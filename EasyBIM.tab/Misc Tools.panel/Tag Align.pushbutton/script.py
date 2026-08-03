# -*- coding: utf-8 -*-
"""Copy one tag's placement onto other elements."""

# pylint: disable=import-error,invalid-name,broad-except
import os
import sys

import clr

clr.AddReference("RevitAPIUI")

from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List as ClrList

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from easybim.compat import eid_to_int as _eid_int
from easybim.compat import exception_text as _exception_text
from easybim.compat import safe_text as _safe_text

from tag_align_revit import TagOnlyFilter
from tag_align_revit import TargetFilter
from tag_align_revit import ViewContext
from tag_align_revit import build_filter_entries
from tag_align_revit import build_plan
from tag_align_revit import execute_plan
from tag_align_revit import is_independent_tag
from tag_align_revit import make_length_formatter
from tag_align_revit import measure_reference
from tag_align_state import MODE_MULTI
from tag_align_state import MODE_SINGLE
from tag_align_state import SCOPE_EXACT_TYPE
from tag_align_state import TagAlignSession
from tag_align_state import apply_conflict_choices
from tag_align_state import build_fatal_text
from tag_align_state import build_filter_tree
from tag_align_state import build_preview_text
from tag_align_state import filter_entries
from tag_align_state import validate_references
import tag_align_ui as ui


__title__ = "Tag Align"

logger = script.get_logger()

STEP_MAIN = "main"
STEP_PICK_REFERENCE = "pick_reference"
STEP_ORIENTATION = "orientation"
STEP_CONFLICT = "conflict"
STEP_ALIGN_OPTIONS = "align_options"
STEP_MODE = "mode"
STEP_CLICK = "click"
STEP_BATCH = "batch"
STEP_REPORT = "report"


# ---------------------------------------------------------------------------
# Revit plumbing
# ---------------------------------------------------------------------------


def _is_cancelled_pick(ex):
    if isinstance(ex, OperationCanceledException):
        return True
    return "cancel" in _safe_text(ex).lower()


def _clr_ids(element_ids):
    ids = ClrList[DB.ElementId]()
    for element_id in element_ids or []:
        if element_id is not None:
            ids.Add(element_id)
    return ids


def _set_ui_selection(uidoc, element_ids):
    try:
        uidoc.Selection.SetElementIds(_clr_ids(element_ids))
    except Exception:
        pass


def _show_elements(uidoc, element_ids):
    ids = [element_id for element_id in element_ids or [] if element_id is not None]
    if not ids:
        return
    _set_ui_selection(uidoc, ids)
    try:
        uidoc.ShowElements(_clr_ids(ids))
    except Exception:
        pass


def _merge_ids(existing, new_ids):
    merged = []
    seen = set()
    for source in (existing or [], new_ids or []):
        for element_id in source:
            value = _eid_int(element_id)
            if value is None or value in seen:
                continue
            seen.add(value)
            merged.append(element_id)
    return merged


def _subtract_ids(existing, removed):
    drop = set()
    for element_id in removed or []:
        value = _eid_int(element_id)
        if value is not None:
            drop.add(value)
    return [
        element_id
        for element_id in existing or []
        if _eid_int(element_id) not in drop
    ]


def _active_view(uidoc, doc):
    """The graphical view tags would land in, or ``None`` with a reason."""
    view = None
    try:
        view = uidoc.ActiveGraphicalView
    except Exception:
        view = None
    if view is None:
        try:
            view = doc.ActiveView
        except Exception:
            view = None

    if view is None:
        return None, "There is no active view."
    if bool(getattr(view, "IsTemplate", False)):
        return None, "The active view is a view template."
    if isinstance(view, DB.ViewSheet):
        return None, "Open the view itself rather than the sheet."
    if getattr(view, "RightDirection", None) is None:
        return None, "The active view is not a graphical view."
    return view, ""


def _pick_elements(uidoc, selection_filter, prompt, multiple=True):
    """``(ids, cancelled)`` - a cancelled pick is normal, not an error."""
    try:
        if multiple:
            references = uidoc.Selection.PickObjects(
                ObjectType.Element, selection_filter, prompt
            )
            return [ref.ElementId for ref in references or []], False
        reference = uidoc.Selection.PickObject(
            ObjectType.Element, selection_filter, prompt
        )
        return ([reference.ElementId] if reference else []), False
    except Exception as ex:
        if _is_cancelled_pick(ex):
            return [], True
        raise


# ---------------------------------------------------------------------------
# reference capture
# ---------------------------------------------------------------------------


def _measure_references(doc, active_view, tag_ids):
    """Measure picked tags and note the ones taken from another view."""
    rules = []
    for tag_id in tag_ids:
        tag = doc.GetElement(tag_id)
        if tag is None:
            continue
        rule = measure_reference(doc, tag)
        if rule.is_valid and _eid_int(rule.view_id) != _eid_int(active_view.Id):
            rule.notes.append(
                "Measured in '{0}' at 1:{1}, not the active view.".format(
                    rule.view_name, rule.view_scale
                )
            )
        rules.append(rule)
    return rules


def _report_dropped(rules):
    dropped = [rule for rule in rules if not rule.is_valid]
    if not dropped:
        return
    lines = ["Some picked tags could not be used as a reference:", ""]
    for rule in dropped:
        lines.append("- {0}: {1}".format(rule.tag_type_label or "tag", rule.invalid_reason))
    forms.alert("\n".join(lines), title=__title__)


def _accept_references(session, rules):
    """Store the usable rules and say whether conflicts need resolving."""
    _report_dropped(rules)
    usable = [rule for rule in rules if rule.is_valid]
    if not usable:
        session.reset_references()
        return STEP_MAIN

    session.references = usable
    return STEP_CONFLICT if _validate(session).is_blocked else STEP_MAIN


def _validate(session):
    return validate_references(
        session.references,
        scope=session.scope,
        scale_with_view=session.scale_with_view,
        collapse_flip=session.collapse_flip,
    )


# ---------------------------------------------------------------------------
# processing
# ---------------------------------------------------------------------------


def _run_transaction(doc, context, session, items, plan_summary, progress=None):
    """Write a plan in one undo step.

    A raw ``DB.Transaction`` rather than ``revit.Transaction`` because a
    cancelled progress bar has to roll the whole batch back without raising -
    a half-applied batch would be worse than none at all.
    """
    transaction = DB.Transaction(doc, __title__)
    transaction.Start()
    try:
        summary = execute_plan(context, session, items, plan_summary, progress)
    except Exception:
        transaction.RollBack()
        raise

    if summary.cancelled:
        transaction.RollBack()
        # The writes that had already run are gone with the rollback, so the
        # counters must not claim them.
        summary.aligned = 0
        summary.tagged = 0
    else:
        transaction.Commit()
    return summary


def _run_transaction_with_progress(doc, context, session, items, plan_summary):
    with forms.ProgressBar(title="Tag Align: {value} of {max_value}",
                           cancellable=True) as progress_bar:

        def _tick(index, total):
            progress_bar.update_progress(index + 1, max(int(total), 1))
            return not progress_bar.cancelled

        return _run_transaction(doc, context, session, items, plan_summary, _tick)


def _run_click_mode(doc, uidoc, session, view_holder):
    """Pick one target at a time and process it immediately; Esc ends the loop.

    Each click is its own transaction so undo walks back click by click, which
    is what "process immediately" implies.
    """
    selection_filter = TargetFilter(session.host_category_id(), session.tag_type_id())
    prompt = "Pick a tag or element to align - press Esc when you are done"
    context = None
    context_view_value = None

    while True:
        view, reason = _active_view(uidoc, doc)
        if view is None:
            forms.alert(reason, title=__title__)
            return

        view_value = _eid_int(view.Id)
        if context is None or view_value != context_view_value:
            context = ViewContext(doc, view)
            context_view_value = view_value
            view_holder[0] = view

        try:
            picked, cancelled = _pick_elements(
                uidoc, selection_filter, prompt, multiple=False
            )
        except Exception as ex:
            forms.alert("Select failed: {0}".format(_exception_text(ex)), title=__title__)
            return

        if cancelled or not picked:
            return

        try:
            items, plan_summary = build_plan(context, session, picked)
            summary = (
                _run_transaction(doc, context, session, items, plan_summary)
                if items
                else plan_summary
            )
        except Exception as ex:
            logger.debug("Tag Align click-mode write failed | %s", ex)
            forms.alert(
                "Nothing was changed.\n\n{0}".format(_exception_text(ex)),
                title=__title__,
            )
            return

        session.running.merge(summary)


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------


def _preselected_tag_ids(uidoc, doc):
    ids = []
    try:
        for element_id in uidoc.Selection.GetElementIds():
            element = doc.GetElement(element_id)
            if element is not None and is_independent_tag(element):
                ids.append(element_id)
    except Exception:
        pass
    return ids


def main():
    doc = revit.doc
    uidoc = revit.uidoc
    if doc is None or uidoc is None:
        forms.alert("Open a project document before running Tag Align.", title=__title__)
        return

    active_view, reason = _active_view(uidoc, doc)
    if active_view is None:
        forms.alert(reason, title=__title__)
        return

    format_length = make_length_formatter(doc)
    session = TagAlignSession()
    view_holder = [active_view]

    def _show(element_ids):
        _show_elements(uidoc, element_ids)

    def _select_skipped(summary):
        _set_ui_selection(uidoc, summary.skipped_element_ids())

    preselected = _preselected_tag_ids(uidoc, doc)
    pending_action = None
    pick_mode = MODE_SINGLE
    pending_rules = []
    last_any_orientation = True
    step = STEP_MAIN

    while True:
        # ------------------------------------------------------------- main
        if step == STEP_MAIN:
            window = ui.TagAlignWindow(
                "TagAlignWindow.xaml",
                session,
                format_length=format_length,
                show_callback=_show,
                preselected_count=len(preselected),
            )
            window.ShowDialog()
            action = window.next_action

            if action == ui.ACTION_CANCEL:
                return

            if action == ui.ACTION_USE_SELECTION:
                pick_mode = MODE_SINGLE if len(preselected) == 1 else MODE_MULTI
                session.reference_mode = pick_mode
                pending_rules = _measure_references(doc, view_holder[0], preselected)
                preselected = []
                step = (
                    STEP_ORIENTATION
                    if pick_mode == MODE_SINGLE
                    else _accept_references(session, pending_rules)
                )
                continue

            if action in (ui.ACTION_SELECT_ONE, ui.ACTION_SELECT_MULTIPLE):
                pick_mode = MODE_SINGLE if action == ui.ACTION_SELECT_ONE else MODE_MULTI
                step = STEP_PICK_REFERENCE
                continue

            if action in (ui.ACTION_ALIGN, ui.ACTION_ALIGN_AND_TAG):
                session.create_tags = action == ui.ACTION_ALIGN_AND_TAG
                validation = _validate(session)
                if validation.is_blocked:
                    pending_action = action
                    step = STEP_CONFLICT
                    continue
                session.references = validation.accepted
                step = STEP_ALIGN_OPTIONS if session.create_tags else STEP_MODE
                continue

            continue

        # --------------------------------------------------- pick reference
        if step == STEP_PICK_REFERENCE:
            prompt = (
                "Select the reference tag"
                if pick_mode == MODE_SINGLE
                else "Select the reference tags, then click Finish"
            )
            try:
                picked, cancelled = _pick_elements(
                    uidoc, TagOnlyFilter(), prompt, multiple=pick_mode == MODE_MULTI
                )
            except Exception as ex:
                forms.alert("Select failed: {0}".format(_exception_text(ex)), title=__title__)
                step = STEP_MAIN
                continue

            if cancelled or not picked:
                step = STEP_MAIN
                continue

            session.reference_mode = pick_mode
            pending_rules = _measure_references(doc, view_holder[0], picked)

            if pick_mode == MODE_SINGLE:
                step = STEP_ORIENTATION
                continue

            step = _accept_references(session, pending_rules)
            continue

        # ------------------------------------------------------ orientation
        if step == STEP_ORIENTATION:
            usable = [rule for rule in pending_rules if rule.is_valid]
            if not usable:
                _report_dropped(pending_rules)
                step = STEP_MAIN
                continue

            if len(usable) > 1:
                # "Use current selection" can hand a single-reference flow more
                # than one tag; treat that as the multiple-reference path.
                session.reference_mode = MODE_MULTI
                step = _accept_references(session, pending_rules)
                continue

            orientation_window = ui.ReferenceOrientationWindow(
                "ReferenceOrientationWindow.xaml",
                usable[0],
                format_length=format_length,
                initial_any=last_any_orientation,
            )
            orientation_window.ShowDialog()

            if orientation_window.result == ui.ACTION_OK:
                last_any_orientation = bool(orientation_window.any_orientation)
                session.any_orientation = last_any_orientation
                step = _accept_references(session, pending_rules)
                continue
            if orientation_window.result == ui.ACTION_BACK:
                step = STEP_PICK_REFERENCE
                continue
            step = STEP_MAIN
            continue

        # --------------------------------------------------------- conflict
        if step == STEP_CONFLICT:
            validation = _validate(session)

            if validation.notes:
                for note in validation.notes:
                    logger.debug("Tag Align reference note | %s", note)

            if validation.fatals:
                forms.alert(build_fatal_text(validation), title=__title__)
                session.reset_references()
                pending_action = None
                step = STEP_MAIN
                continue

            if not validation.conflicts:
                session.references = validation.accepted
                step = _resume_after_conflict(session, pending_action)
                pending_action = None
                continue

            conflict_window = ui.ReferenceConflictWindow(
                "ReferenceConflictWindow.xaml",
                validation,
                format_length=format_length,
                scope=session.scope,
                show_callback=_show,
            )
            conflict_window.ShowDialog()
            result = conflict_window.result

            if result == ui.ACTION_OK:
                session.references = apply_conflict_choices(validation)
                step = _resume_after_conflict(session, pending_action)
                pending_action = None
                continue

            if result == ui.ACTION_SWITCH_SCOPE:
                session.scope = SCOPE_EXACT_TYPE
                continue

            if result == ui.ACTION_RESELECT:
                session.reset_references()
                pending_action = None
                step = STEP_PICK_REFERENCE
                continue

            pending_action = None
            step = STEP_MAIN
            continue

        # ---------------------------------------------------- align options
        if step == STEP_ALIGN_OPTIONS:
            options_window = ui.AlignTagOptionsWindow(
                "AlignTagOptionsWindow.xaml",
                initial_align_existing=session.align_existing,
            )
            options_window.ShowDialog()

            if options_window.result == ui.ACTION_OK:
                session.align_existing = bool(options_window.align_existing)
                step = STEP_MODE
                continue
            if options_window.result == ui.ACTION_BACK:
                step = STEP_MAIN
                continue
            return

        # ------------------------------------------------------------- mode
        if step == STEP_MODE:
            mode_window = ui.ModeWindow("ModeWindow.xaml", session)
            mode_window.ShowDialog()
            result = mode_window.result

            if result == ui.ACTION_CLICK_MODE:
                step = STEP_CLICK
                continue
            if result == ui.ACTION_BATCH_MODE:
                step = STEP_BATCH
                continue
            if result == "report":
                step = STEP_REPORT
                continue
            if result == ui.ACTION_BACK:
                step = STEP_ALIGN_OPTIONS if session.create_tags else STEP_MAIN
                continue
            return

        # ------------------------------------------------------------ click
        if step == STEP_CLICK:
            _run_click_mode(doc, uidoc, session, view_holder)
            step = STEP_MODE
            continue

        # ------------------------------------------------------------ batch
        if step == STEP_BATCH:
            step = _run_batch_mode(
                doc, uidoc, session, view_holder, format_length, _select_skipped
            )
            continue

        # ----------------------------------------------------------- report
        if step == STEP_REPORT:
            report_window = ui.ReportWindow(
                "ReportWindow.xaml",
                session.running,
                create_tags=session.create_tags,
                select_callback=_select_skipped,
            )
            report_window.ShowDialog()
            step = STEP_MODE
            continue

        return


def _resume_after_conflict(session, pending_action):
    if pending_action == ui.ACTION_ALIGN:
        return STEP_MODE
    if pending_action == ui.ACTION_ALIGN_AND_TAG:
        return STEP_ALIGN_OPTIONS
    return STEP_MAIN


def _run_batch_mode(doc, uidoc, session, view_holder, format_length, select_skipped):
    """The Filter / Select / Deselect / Process bar. Returns the next step."""
    del format_length

    selection_filter = TargetFilter(session.host_category_id(), session.tag_type_id())
    status = (
        "Press Select and cross-select in Revit. Only {0} and their tags can be "
        "picked; Filter narrows that down by family and type.".format(
            session.host_category_name() or "matching elements"
        )
    )

    while True:
        view, reason = _active_view(uidoc, doc)
        if view is None:
            forms.alert(reason, title=__title__)
            return STEP_MODE
        view_holder[0] = view

        entries = build_filter_entries(doc, session.batch_ids)
        session.batch_ids = [entry["element_id"] for entry in entries]
        _set_ui_selection(uidoc, session.batch_ids)

        window = ui.BatchWindow("BatchWindow.xaml", session, entries, status)
        window.ShowDialog()
        result = window.result

        if result == ui.ACTION_SELECT:
            try:
                picked, cancelled = _pick_elements(
                    uidoc, selection_filter, "Select elements to align", multiple=True
                )
            except Exception as ex:
                status = "Select failed: {0}".format(_exception_text(ex))
                continue
            if cancelled:
                status = "Selection unchanged."
                continue
            before = len(session.batch_ids)
            session.batch_ids = _merge_ids(session.batch_ids, picked)
            status = "Added {0} element(s).".format(len(session.batch_ids) - before)
            continue

        if result == ui.ACTION_DESELECT:
            try:
                picked, cancelled = _pick_elements(
                    uidoc, selection_filter, "Select elements to remove", multiple=True
                )
            except Exception as ex:
                status = "Deselect failed: {0}".format(_exception_text(ex))
                continue
            if cancelled:
                status = "Selection unchanged."
                continue
            before = len(session.batch_ids)
            session.batch_ids = _subtract_ids(session.batch_ids, picked)
            status = "Removed {0} element(s).".format(before - len(session.batch_ids))
            continue

        if result == "filter":
            tree = build_filter_tree(entries)
            filter_window = ui.SelectionFilterWindow("SelectionFilterWindow.xaml", tree)
            filter_window.ShowDialog()
            if filter_window.result != ui.ACTION_OK:
                continue
            kept = filter_entries(entries, filter_window.allowed_type_keys)
            removed = len(entries) - len(kept)
            session.batch_ids = [entry["element_id"] for entry in kept]
            status = "Filter removed {0} element(s).".format(removed)
            continue

        if result == "clear":
            session.batch_ids = []
            status = "Selection cleared."
            continue

        if result == ui.ACTION_PROCESS:
            context = ViewContext(doc, view)
            try:
                items, plan_summary = build_plan(context, session, session.batch_ids)
            except Exception as ex:
                forms.alert(
                    "Could not work out what to do:\n\n{0}".format(_exception_text(ex)),
                    title=__title__,
                )
                continue

            if session.confirm_before_process:
                proceed = forms.alert(
                    build_preview_text(plan_summary, session.create_tags),
                    title=__title__,
                    yes=True,
                    no=True,
                )
                if not proceed:
                    status = "Nothing was changed."
                    continue

            if not items:
                session.running.merge(plan_summary)
                status = "Nothing to change - see the report."
                continue

            try:
                summary = _run_transaction_with_progress(
                    doc, context, session, items, plan_summary
                )
            except Exception as ex:
                logger.debug("Tag Align batch write failed | %s", ex)
                forms.alert(
                    "Nothing was changed.\n\n{0}".format(_exception_text(ex)),
                    title=__title__,
                )
                continue

            session.running.merge(summary)
            report_window = ui.ReportWindow(
                "ReportWindow.xaml",
                summary,
                create_tags=session.create_tags,
                select_callback=select_skipped,
            )
            report_window.ShowDialog()
            status = "Processed. Selection kept so you can adjust and run again."
            continue

        if result == "report":
            report_window = ui.ReportWindow(
                "ReportWindow.xaml",
                session.running,
                create_tags=session.create_tags,
                select_callback=select_skipped,
            )
            report_window.ShowDialog()
            continue

        if result == ui.ACTION_BACK:
            return STEP_MODE

        return None


if __name__ == "__main__":
    main()
