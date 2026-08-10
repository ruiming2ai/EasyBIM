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
from families_transfer_revit import get_link_document_options
from families_transfer_revit import get_link_family_options
from families_transfer_revit import get_open_family_documents
from families_transfer_revit import get_open_target_documents
from families_transfer_revit import get_selected_family_options_from_selection
from families_transfer_revit import get_source_family_options
from families_transfer_revit import link_notes
from families_transfer_revit import pick_export_folder
from families_transfer_revit import pick_more_family_options
from families_transfer_revit import prepare_link_documents
from families_transfer_revit import transfer_families
from families_transfer_state import build_transfer_summary_text
from families_transfer_state import get_selected_family_keys
from families_transfer_state import get_selected_source_family_options
from families_transfer_state import merge_source_family_options
from families_transfer_state import merge_transferable_family_options
from families_transfer_state import prune_link_families_to_checked_links
from families_transfer_state import restore_family_selection
from families_transfer_state import split_selected_family_keys
from families_transfer_ui import ActionWindow
from families_transfer_ui import FamilySelectionWindow
from families_transfer_ui import SourceSelectionWindow
from families_transfer_ui import TargetSelectionWindow


__title__ = "Families Transfer"

LOGGER = script.get_logger()

STEP_SOURCE = "source"
STEP_FAMILIES = "families"
STEP_LINK_FAMILIES = "link_families"
STEP_TARGETS = "targets"
STEP_ACTION = "action"


from easybim.compat import safe_text as _safe_text


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


def _with_progress(title, work):
    """Run a batch behind a cancellable progress bar.

    Every family costs an EditFamily plus a LoadFamily per target, so a few
    hundred of them is minutes of a frozen Revit unless the user can both see
    it and stop it.
    """
    with forms.ProgressBar(title=title + ": {value} of {max_value}",
                           cancellable=True) as progress_bar:

        def _tick(done, total):
            progress_bar.update_progress(done + 1, max(total, 1))
            return not progress_bar.cancelled

        return work(_tick)


def _note_link_refusals(summary, link_document_options):
    """Carry each link's one-sentence refusal into the report.

    Without this a link that would not hand over any family reports the same
    line once per family, which buries the reason under its own repetition.
    """
    for note in link_notes(link_document_options):
        summary.add_note(note)
    return summary


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

    selected_source_families = get_selected_family_options_from_selection(doc, uidoc)
    selected_project_family_keys = set(get_selected_family_keys(selected_source_families))
    selected_open_family_document_keys = set()
    selected_family_keys = set(selected_project_family_keys)
    selected_document_keys = set()
    source_status = ""
    open_family_documents = []
    families = []
    targets = []
    step = STEP_SOURCE
    target_back_step = STEP_SOURCE

    # Collected once and reused: the family scan is the expensive part, and
    # every Back, Add and pick returns to a step that would otherwise redo it.
    project_families_cache = [None]
    link_documents = [None]
    link_family_cache = {}
    selected_link_document_keys = set()
    selected_link_family_keys = set()
    selected_link_families = []

    def _project_families():
        if project_families_cache[0] is None:
            project_families_cache[0] = get_source_family_options(
                doc,
                selected_project_family_keys,
            )
        return project_families_cache[0]

    def _link_documents():
        if link_documents[0] is None:
            # Enumerating link instances is cheap; reading their families is
            # not, and that waits until a link is actually checked.
            link_documents[0] = get_link_document_options(doc, selected_link_document_keys)
        return link_documents[0]

    def _read_link_selection(window):
        checked = set(window.selected_link_document_keys)
        return checked, prune_link_families_to_checked_links(
            selected_link_families,
            checked,
        )

    while True:
        if step == STEP_SOURCE:
            selected_source_families = get_selected_source_family_options(
                selected_source_families,
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
                link_documents=_link_documents(),
                selected_link_document_keys=selected_link_document_keys,
                link_family_count=len(selected_link_families),
            )
            source_window.ShowDialog()

            if source_window.result == "load_more":
                selected_project_family_keys = set(source_window.selected_family_keys)
                selected_family_keys = set(selected_project_family_keys)
                selected_open_family_document_keys = set(source_window.selected_document_keys)
                selected_link_document_keys, selected_link_families = _read_link_selection(source_window)
                selected_link_family_keys = set(get_selected_family_keys(selected_link_families))
                step = STEP_FAMILIES
                continue

            if source_window.result == "load_links":
                selected_project_family_keys = set(source_window.selected_family_keys)
                selected_open_family_document_keys = set(source_window.selected_document_keys)
                selected_link_document_keys, selected_link_families = _read_link_selection(source_window)
                selected_link_family_keys = set(get_selected_family_keys(selected_link_families))
                step = STEP_LINK_FAMILIES
                continue

            if source_window.result == "next":
                selected_project_family_keys = set(source_window.selected_family_keys)
                selected_source_families = get_selected_source_family_options(
                    selected_source_families,
                    selected_project_family_keys,
                )
                selected_open_family_document_keys = set(source_window.selected_document_keys)
                selected_link_document_keys, selected_link_families = _read_link_selection(source_window)
                selected_link_family_keys = set(get_selected_family_keys(selected_link_families))
                open_family_documents = get_open_family_documents(
                    uiapp,
                    doc,
                    selected_open_family_document_keys,
                )
                families = merge_transferable_family_options(
                    selected_source_families,
                    open_family_documents,
                    selected_link_families,
                )
                selected_family_keys = set(get_selected_family_keys(families))
                if not selected_family_keys:
                    forms.alert(
                        "Select at least one family, opened .rfa file or link family before continuing.",
                        title=__title__,
                    )
                    source_status = "Select at least one family, opened .rfa file or link family before continuing."
                    continue
                target_back_step = STEP_SOURCE
                step = STEP_TARGETS
                continue
            return

        if step == STEP_LINK_FAMILIES:
            prepare_link_documents(uiapp, doc, _link_documents())
            link_families = get_link_family_options(
                _link_documents(),
                selected_link_family_keys,
                link_family_cache,
            )
            refusals = link_notes(_link_documents())

            if not link_families:
                forms.alert(
                    "\n\n".join(
                        ["No families could be read from the checked Revit links."] + refusals
                    ),
                    title=__title__,
                )
                step = STEP_SOURCE
                continue

            links_window = FamilySelectionWindow(
                "FamilySelectionWindow.xaml",
                link_families,
                selected_link_family_keys,
                header_text="Load More from Revit Links",
                allow_model_pick=False,
                require_selection=False,
            )
            links_window.ShowDialog()

            if links_window.result == "add":
                selected_link_family_keys = set(links_window.selected_family_keys)
                selected_link_families = get_selected_source_family_options(
                    link_families,
                    selected_link_family_keys,
                )
                source_status = "{} link family/families selected.".format(
                    len(selected_link_families)
                )
                step = STEP_SOURCE
                continue

            if links_window.result == "back":
                step = STEP_SOURCE
                continue
            return

        if step == STEP_FAMILIES:
            project_families = _project_families()
            restore_family_selection(project_families, selected_project_family_keys)
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

            selected_family_keys = set(get_selected_family_keys(families))

            family_window = FamilySelectionWindow(
                "FamilySelectionWindow.xaml",
                families,
                selected_family_keys,
            )
            family_window.ShowDialog()

            if family_window.result == "pick":
                # The page's own ticks have to survive the pick: this step is
                # now re-entered rather than left, so dropping them would wipe
                # whatever the user had checked before reaching for the model.
                pick_project_keys, pick_open_keys, _ = split_selected_family_keys(
                    set(family_window.selected_family_keys)
                )
                selected_project_family_keys = pick_project_keys
                selected_open_family_document_keys = pick_open_keys

                try:
                    picked_families = pick_more_family_options(uidoc)
                except Exception as ex:
                    if _is_cancelled_pick(ex):
                        continue
                    forms.alert("Select failed: {}".format(ex), title=__title__)
                    return

                selected_source_families = merge_source_family_options(
                    get_selected_source_family_options(
                        selected_source_families,
                        selected_project_family_keys,
                    ),
                    picked_families,
                )
                selected_project_family_keys.update(
                    get_selected_family_keys(picked_families)
                )
                selected_family_keys = set(selected_project_family_keys)
                continue

            if family_window.result == "add":
                add_project_family_keys, add_open_family_document_keys, _ = split_selected_family_keys(
                    set(family_window.selected_family_keys)
                )
                selected_existing_source_families = get_selected_source_family_options(
                    selected_source_families,
                    selected_project_family_keys,
                )
                added_source_families = get_selected_source_family_options(
                    project_families,
                    add_project_family_keys,
                )
                selected_source_families = merge_source_family_options(
                    selected_existing_source_families,
                    added_source_families,
                )
                selected_project_family_keys = set(get_selected_family_keys(selected_source_families))
                selected_open_family_document_keys.update(add_open_family_document_keys)
                selected_family_keys = set(selected_project_family_keys)
                source_status = "{} active-project families selected.".format(
                    len(selected_project_family_keys)
                )
                step = STEP_SOURCE
                continue

            if family_window.result == "back":
                selected_source_families = get_selected_source_family_options(
                    selected_source_families,
                    selected_project_family_keys,
                )
                selected_family_keys = set(selected_project_family_keys)
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
                step = target_back_step
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
                summary = _with_progress(
                    "Exporting families",
                    lambda tick: export_families(
                        doc, selected_families, folder_path, progress=tick),
                )
                _show_summary(_note_link_refusals(summary, _link_documents()))
                return

            if action_window.result == "transfer":
                if not selected_targets:
                    forms.alert("Select at least one open target file before transferring.", title=__title__)
                    step = STEP_TARGETS
                    continue
                summary = _with_progress(
                    "Transferring families",
                    lambda tick: transfer_families(
                        doc, selected_families, selected_targets, progress=tick),
                )
                _show_summary(_note_link_refusals(summary, _link_documents()))
                return

            if action_window.result == "transfer_close_all_rfa":
                if not selected_targets:
                    forms.alert("Select at least one open target file before transferring.", title=__title__)
                    step = STEP_TARGETS
                    continue

                summary = _with_progress(
                    "Transferring families",
                    lambda tick: transfer_families(
                        doc, selected_families, selected_targets, progress=tick),
                )
                close_summary = close_open_family_documents(get_open_family_documents(uiapp, doc))
                _show_summary(
                    _note_link_refusals(
                        _merge_close_summary(summary, close_summary),
                        _link_documents(),
                    )
                )
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
