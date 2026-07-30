# -*- coding: utf-8 -*-
"""Export or transfer selected loadable families to open Revit files."""

# pylint: disable=import-error,invalid-name,broad-except
import os
import sys

import clr

clr.AddReference("RevitAPIUI")

from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI import TaskDialog

from pyrevit import forms
from pyrevit import revit
from pyrevit import script


SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from families_transfer_revit import close_open_family_documents
from families_transfer_revit import export_families
from families_transfer_revit import get_open_family_documents
from families_transfer_revit import get_open_target_documents
from families_transfer_revit import get_selected_family_keys_from_selection
from families_transfer_revit import get_source_family_options
from families_transfer_revit import pick_export_folder
from families_transfer_revit import pick_more_family_keys
from families_transfer_revit import transfer_families
from families_transfer_state import build_transfer_summary_text
from families_transfer_state import get_selected_source_family_options
from families_transfer_state import is_open_family_document_key
from families_transfer_state import is_project_family_key
from families_transfer_state import merge_transferable_family_options
from families_transfer_state import open_family_document_key_from_family_key
from families_transfer_ui import ActionWindow
from families_transfer_ui import FamilySelectionWindow
from families_transfer_ui import SourceSelectionWindow
from families_transfer_ui import TargetSelectionWindow


__title__ = "Families Transfer"

LOGGER = script.get_logger()

STEP_SOURCE = "source"
STEP_FAMILIES = "families"
STEP_TARGETS = "targets"
STEP_ACTION = "action"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _is_cancelled_pick(ex):
    if isinstance(ex, OperationCanceledException):
        return True
    return "cancel" in _safe_text(ex).lower()


def _get_uiapp():
    try:
        return __revit__
    except Exception:
        return None


def _selected_options(options, selected_keys, key_name):
    selected_keys = set(selected_keys or [])
    result = []
    for option in options or []:
        if getattr(option, key_name, None) in selected_keys:
            result.append(option)
    return result


def _show_summary(summary):
    TaskDialog.Show(__title__, build_transfer_summary_text(summary))


def _split_selected_family_keys(selected_family_keys):
    project_family_keys = set()
    open_family_document_keys = set()

    for family_key in selected_family_keys or []:
        if is_open_family_document_key(family_key):
            document_key = open_family_document_key_from_family_key(family_key)
            if document_key:
                open_family_document_keys.add(document_key)
            continue

        if is_project_family_key(family_key):
            project_family_keys.add(family_key)

    return project_family_keys, open_family_document_keys


def _merge_close_summary(transfer_summary, close_summary):
    if close_summary is None:
        return transfer_summary

    transfer_summary.closed_rfa_count = int(getattr(close_summary, "closed_rfa_count", 0) or 0)
    transfer_summary.skipped.extend(list(getattr(close_summary, "skipped", []) or []))
    transfer_summary.failed.extend(list(getattr(close_summary, "failed", []) or []))
    return transfer_summary


def _run():
    uidoc = revit.uidoc
    doc = revit.doc
    uiapp = _get_uiapp()

    if uidoc is None or doc is None:
        forms.alert("Open a project document before running Families Transfer.", title=__title__)
        return

    try:
        if bool(doc.IsFamilyDocument):
            forms.alert("Run Families Transfer from a project document, not a family document.", title=__title__)
            return
    except Exception:
        pass

    selected_project_family_keys = set(get_selected_family_keys_from_selection(doc, uidoc))
    selected_open_family_document_keys = set()
    selected_family_keys = set(selected_project_family_keys)
    selected_document_keys = set()
    source_status = ""
    open_family_documents = []
    families = []
    targets = []
    step = STEP_SOURCE

    while True:
        if step == STEP_SOURCE:
            project_families = get_source_family_options(doc, selected_project_family_keys)
            selected_source_families = get_selected_source_family_options(
                project_families,
                selected_project_family_keys,
            )
            open_family_documents = get_open_family_documents(
                uiapp,
                doc,
                selected_open_family_document_keys,
            )
            source_window = SourceSelectionWindow(
                "SourceSelectionWindow.xaml",
                selected_source_families,
                open_family_documents,
                selected_open_family_document_keys,
                source_status,
            )
            source_window.ShowDialog()

            if source_window.result == "select":
                selected_project_family_keys = set(source_window.selected_family_keys)
                selected_family_keys = set(selected_project_family_keys)
                selected_open_family_document_keys = set(source_window.selected_document_keys)
                try:
                    picked_keys = pick_more_family_keys(uidoc)
                except Exception as ex:
                    if _is_cancelled_pick(ex):
                        source_status = "Selection canceled."
                        continue
                    forms.alert("Select failed: {}".format(ex), title=__title__)
                    return

                selected_family_keys.update(picked_keys)
                selected_project_family_keys.update(picked_keys)
                source_status = "{} active-project families selected.".format(
                    len(selected_project_family_keys)
                )
                continue

            if source_window.result == "next":
                selected_project_family_keys = set(source_window.selected_family_keys)
                selected_family_keys = set(selected_project_family_keys)
                selected_open_family_document_keys = set(source_window.selected_document_keys)
                step = STEP_FAMILIES
                continue
            return

        if step == STEP_FAMILIES:
            project_families = get_source_family_options(doc, selected_project_family_keys)
            open_family_documents = get_open_family_documents(
                uiapp,
                doc,
                selected_open_family_document_keys,
            )
            families = merge_transferable_family_options(project_families, open_family_documents)
            if not families:
                forms.alert(
                    "No transferable active-project families or selected opened .rfa files were found.",
                    title=__title__,
                )
                return

            selected_family_keys = set(
                getattr(family, "family_key", "")
                for family in families
                if bool(getattr(family, "is_selected", False))
            )

            family_window = FamilySelectionWindow(
                "FamilySelectionWindow.xaml",
                families,
                selected_family_keys,
            )
            family_window.ShowDialog()

            if family_window.result == "next":
                selected_family_keys = set(family_window.selected_family_keys)
                selected_project_family_keys, selected_open_family_document_keys = _split_selected_family_keys(
                    selected_family_keys
                )
                step = STEP_TARGETS
                continue

            if family_window.result == "back":
                selected_family_keys = set(family_window.selected_family_keys)
                selected_project_family_keys, selected_open_family_document_keys = _split_selected_family_keys(
                    selected_family_keys
                )
                source_status = "{} active-project families selected.".format(
                    len(selected_project_family_keys)
                )
                step = STEP_SOURCE
                continue
            return

        if step == STEP_TARGETS:
            targets = get_open_target_documents(uiapp, doc, selected_document_keys)
            target_window = TargetSelectionWindow(
                "TargetSelectionWindow.xaml",
                targets,
                selected_document_keys,
            )
            target_window.ShowDialog()

            if target_window.result == "next":
                selected_document_keys = set(target_window.selected_document_keys)
                step = STEP_ACTION
                continue

            if target_window.result == "back":
                selected_document_keys = set(target_window.selected_document_keys)
                step = STEP_FAMILIES
                continue
            return

        if step == STEP_ACTION:
            selected_families = _selected_options(families, selected_family_keys, "family_key")
            selected_targets = _selected_options(targets, selected_document_keys, "document_key")
            action_window = ActionWindow(
                "ActionWindow.xaml",
                len(selected_families),
                len(selected_targets),
            )
            action_window.ShowDialog()

            if action_window.result == "export":
                folder_path = pick_export_folder()
                if not folder_path:
                    return
                _show_summary(export_families(doc, selected_families, folder_path))
                return

            if action_window.result == "transfer":
                if not selected_targets:
                    forms.alert("Select at least one open target file before transferring.", title=__title__)
                    step = STEP_TARGETS
                    continue
                _show_summary(transfer_families(doc, selected_families, selected_targets))
                return

            if action_window.result == "transfer_close_all_rfa":
                if not selected_targets:
                    forms.alert("Select at least one open target file before transferring.", title=__title__)
                    step = STEP_TARGETS
                    continue

                summary = transfer_families(doc, selected_families, selected_targets)
                close_summary = close_open_family_documents(get_open_family_documents(uiapp, doc))
                _show_summary(_merge_close_summary(summary, close_summary))
                return

            if action_window.result == "back":
                step = STEP_TARGETS
                continue

            return


try:
    _run()
except Exception as run_error:
    LOGGER.exception("Families Transfer failed.")
    forms.alert("Families Transfer failed:\n{}".format(run_error), title=__title__)
