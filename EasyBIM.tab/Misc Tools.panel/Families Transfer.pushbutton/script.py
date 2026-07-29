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

from families_transfer_revit import export_families
from families_transfer_revit import get_open_target_documents
from families_transfer_revit import get_selected_family_keys_from_selection
from families_transfer_revit import get_source_family_options
from families_transfer_revit import pick_export_folder
from families_transfer_revit import pick_more_family_keys
from families_transfer_revit import transfer_families
from families_transfer_state import build_transfer_summary_text
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

    selected_family_keys = set(get_selected_family_keys_from_selection(doc, uidoc))
    selected_document_keys = set()
    source_status = ""
    families = []
    targets = []
    step = STEP_SOURCE

    while True:
        if step == STEP_SOURCE:
            source_window = SourceSelectionWindow(
                "SourceSelectionWindow.xaml",
                len(selected_family_keys),
                source_status,
            )
            source_window.ShowDialog()

            if source_window.result == "select":
                try:
                    picked_keys = pick_more_family_keys(uidoc)
                except Exception as ex:
                    if _is_cancelled_pick(ex):
                        source_status = "Selection canceled."
                        continue
                    forms.alert("Select failed: {}".format(ex), title=__title__)
                    return

                selected_family_keys.update(picked_keys)
                source_status = "{} family/families selected.".format(len(selected_family_keys))
                continue

            if source_window.result == "next":
                step = STEP_FAMILIES
                continue
            return

        if step == STEP_FAMILIES:
            families = get_source_family_options(doc, selected_family_keys)
            if not families:
                forms.alert("No transferable loadable families were found in the active project.", title=__title__)
                return

            family_window = FamilySelectionWindow(
                "FamilySelectionWindow.xaml",
                families,
                selected_family_keys,
            )
            family_window.ShowDialog()

            if family_window.result == "next":
                selected_family_keys = set(family_window.selected_family_keys)
                step = STEP_TARGETS
                continue

            if family_window.result == "back":
                selected_family_keys = set(family_window.selected_family_keys)
                source_status = "{} family/families selected.".format(len(selected_family_keys))
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

            return


try:
    _run()
except Exception as run_error:
    LOGGER.exception("Families Transfer failed.")
    forms.alert("Families Transfer failed:\n{}".format(run_error), title=__title__)
