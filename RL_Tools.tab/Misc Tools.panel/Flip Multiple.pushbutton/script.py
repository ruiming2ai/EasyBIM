# -*- coding: utf-8 -*-
"""Flip selected family instances one-by-one using native Revit flip actions."""

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
from pyrevit.compat import get_elementid_value_func

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from flip_multiple_utils import EMPTY_SELECTION_STATUS_TEXT
from flip_multiple_utils import MODE_FRONT_BACK
from flip_multiple_utils import MODE_LEFT_RIGHT
from flip_multiple_utils import MODE_WORK_PLANE
from flip_multiple_utils import build_completion_message
from flip_multiple_utils import build_incompatibility_message
from flip_multiple_utils import build_status_text
from flip_multiple_utils import collect_incompatible_type_labels
from flip_multiple_utils import get_mode_helper_text
from flip_multiple_utils import get_mode_key_from_label
from flip_multiple_utils import get_mode_label
from flip_multiple_utils import get_mode_labels

__title__ = "Flip Multiple"

logger = script.get_logger()
get_elementid_value = get_elementid_value_func()
PICK_PROMPT = "Select elements to flip"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _eid_int(eid):
    if eid is None:
        return None
    try:
        return int(get_elementid_value(eid))
    except Exception:
        try:
            return int(eid.IntegerValue)
        except Exception:
            return None


def _set_ui_selection(uidoc, element_ids):
    clr_ids = ClrList[DB.ElementId]()
    for element_id in element_ids or []:
        if element_id:
            clr_ids.Add(element_id)
    uidoc.Selection.SetElementIds(clr_ids)


def _merge_element_ids(existing_ids, new_ids):
    merged = []
    seen = set()

    for source in (existing_ids or [], new_ids or []):
        for element_id in source:
            eid_int = _eid_int(element_id)
            if eid_int is None or eid_int in seen:
                continue
            seen.add(eid_int)
            merged.append(element_id)

    return merged


def _is_cancelled_pick(ex):
    if isinstance(ex, OperationCanceledException):
        return True
    return "cancel" in _safe_text(ex).lower()


class DialogState(object):
    def __init__(self, selected_ids=None, mode_key=None, status_text=""):
        self.selected_ids = list(selected_ids or [])
        self.mode_key = mode_key
        self.status_text = _safe_text(status_text)


class ExecuteOutcome(object):
    def __init__(self, reopen_dialog, dialog_state=None):
        self.reopen_dialog = bool(reopen_dialog)
        self.dialog_state = dialog_state


class FlipWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, dialog_state):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.doc = revit.doc
        self.uidoc = revit.uidoc
        self.dialog_state = dialog_state or DialogState()
        self.is_ready = False
        self.next_action = None
        self.selected_ids = list(getattr(self.dialog_state, "selected_ids", []) or [])
        self.selected_elements = []

        if not self.doc or not self.uidoc:
            forms.alert("No active project document.", title=__title__)
            return

        self.mode_cb.ItemsSource = list(get_mode_labels())
        initial_mode_label = get_mode_label(self.dialog_state.mode_key)
        if get_mode_key_from_label(initial_mode_label):
            self.mode_cb.SelectedItem = initial_mode_label

        self._refresh_selection_state()
        self.status_tb.Text = build_status_text(
            len(self.selected_elements),
            self.dialog_state.status_text,
        )
        self.is_ready = True

    def _capture_state(self, status_text=""):
        return DialogState(
            selected_ids=self.selected_ids,
            mode_key=self._selected_mode_key(),
            status_text=status_text,
        )

    def _set_action_controls_enabled(self, is_enabled):
        for control in (self.select_btn, self.run_btn, self.cancel_btn, self.mode_cb):
            try:
                control.IsEnabled = bool(is_enabled)
            except Exception:
                continue

    def _selected_mode_key(self):
        return get_mode_key_from_label(getattr(self.mode_cb, "SelectedItem", None))

    def _refresh_selection_state(self):
        refreshed_ids = []
        refreshed_elements = []
        for element_id in self.selected_ids:
            element = self.doc.GetElement(element_id)
            if element:
                refreshed_ids.append(element_id)
                refreshed_elements.append(element)

        self.selected_ids = refreshed_ids
        self.selected_elements = refreshed_elements
        self.selection_count_tb.Text = "{} element(s) selected.".format(len(self.selected_elements))
        self._sync_mode_state()

    def _sync_mode_state(self):
        self.helper_tb.Text = get_mode_helper_text(self._selected_mode_key())

    def mode_changed(self, sender, args):
        del sender, args
        self._sync_mode_state()

    def select_click(self, sender, args):
        del sender, args
        self._set_action_controls_enabled(False)
        self.status_tb.Text = "Opening Revit element selection..."
        self.dialog_state = self._capture_state(self.status_tb.Text)
        self.next_action = "select"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self._set_action_controls_enabled(False)
        self.dialog_state = self._capture_state("")
        self.next_action = "cancel"
        self.Close()

    def run_click(self, sender, args):
        del sender, args

        if not self.selected_ids:
            self.status_tb.Text = EMPTY_SELECTION_STATUS_TEXT
            return

        mode_key = self._selected_mode_key()
        if not mode_key:
            self.status_tb.Text = "Choose one flip mode before running."
            return

        self._set_action_controls_enabled(False)
        self.status_tb.Text = "Running flip operation..."
        self.dialog_state = self._capture_state(self.status_tb.Text)
        self.next_action = "run"
        self.Close()


def _pick_more_elements(uidoc, dialog_state):
    selected_ids = list(getattr(dialog_state, "selected_ids", []) or [])

    try:
        picked_refs = uidoc.Selection.PickObjects(ObjectType.Element, PICK_PROMPT)
    except Exception as ex:
        status_text = "Selection unchanged." if _is_cancelled_pick(ex) else "Select failed: {}".format(ex)
        return DialogState(
            selected_ids=selected_ids,
            mode_key=dialog_state.mode_key,
            status_text=status_text,
        )

    picked_ids = [ref.ElementId for ref in picked_refs or [] if getattr(ref, "ElementId", None)]
    merged_ids = _merge_element_ids(selected_ids, picked_ids)

    try:
        _set_ui_selection(uidoc, merged_ids)
    except Exception:
        pass

    status_text = "Selection unchanged."
    if len(merged_ids) != len(selected_ids):
        status_text = "Selection updated to {} element(s).".format(len(merged_ids))

    return DialogState(
        selected_ids=merged_ids,
        mode_key=dialog_state.mode_key,
        status_text=status_text,
    )


def _family_type_entry(element):
    symbol = getattr(element, "Symbol", None)
    family_name = ""
    type_name = ""

    try:
        if symbol is not None:
            type_name = getattr(symbol, "Name", "") or ""
            family = getattr(symbol, "Family", None)
            if family is not None:
                family_name = getattr(family, "Name", "") or ""
    except Exception:
        pass

    if not family_name:
        family_name = getattr(element, "Name", "") or "Unknown"
    if not type_name:
        type_name = getattr(symbol, "Name", "") or family_name

    return {
        "family_name": str(family_name).strip() or "Unknown",
        "type_name": str(type_name).strip() or "Unknown",
    }


def _supports_mode(element, mode_key):
    try:
        if mode_key == MODE_WORK_PLANE:
            return bool(element.CanFlipWorkPlane)
        if mode_key == MODE_FRONT_BACK:
            return bool(element.CanFlipFacing)
        if mode_key == MODE_LEFT_RIGHT:
            return bool(element.CanFlipHand)
    except Exception:
        return False
    return False


def _apply_flip(element, mode_key):
    if mode_key == MODE_WORK_PLANE:
        current_state = bool(element.IsWorkPlaneFlipped)
        element.IsWorkPlaneFlipped = not current_state
        return True
    if mode_key == MODE_FRONT_BACK:
        return bool(element.flipFacing())
    if mode_key == MODE_LEFT_RIGHT:
        return bool(element.flipHand())
    raise ValueError("Unknown flip mode: {}".format(mode_key))


def _validate_selection(doc, selected_ids, mode_key):
    valid_instances = []
    invalid_type_entries = []
    non_family_count = 0
    refreshed_ids = []

    for sid in selected_ids:
        element = doc.GetElement(sid)
        if element is None:
            continue

        refreshed_ids.append(sid)

        if not isinstance(element, DB.FamilyInstance):
            non_family_count += 1
            continue

        if not _supports_mode(element, mode_key):
            invalid_type_entries.append(_family_type_entry(element))
            continue

        valid_instances.append(element)

    return valid_instances, invalid_type_entries, non_family_count, refreshed_ids


def _execute_flip_run(doc, uidoc, dialog_state):
    selected_ids = list(getattr(dialog_state, "selected_ids", []) or [])
    mode_key = getattr(dialog_state, "mode_key", None)

    if not selected_ids:
        return ExecuteOutcome(
            True,
            DialogState(
                selected_ids=[],
                mode_key=mode_key,
                status_text=EMPTY_SELECTION_STATUS_TEXT,
            ),
        )

    if not mode_key:
        return ExecuteOutcome(
            True,
            DialogState(
                selected_ids=selected_ids,
                mode_key=mode_key,
                status_text="Choose one flip mode before running.",
            ),
        )

    mode_label = get_mode_label(mode_key)
    valid_instances, invalid_type_entries, non_family_count, refreshed_ids = _validate_selection(
        doc,
        selected_ids,
        mode_key,
    )

    try:
        _set_ui_selection(uidoc, refreshed_ids)
    except Exception:
        pass

    incompatible_type_labels = collect_incompatible_type_labels(invalid_type_entries)
    if invalid_type_entries or non_family_count:
        forms.alert(
            build_incompatibility_message(
                mode_label,
                len(refreshed_ids),
                incompatible_type_labels,
                non_family_count,
            ),
            title=__title__,
        )
        return ExecuteOutcome(
            True,
            DialogState(
                selected_ids=refreshed_ids,
                mode_key=mode_key,
                status_text="Selection contains incompatible elements for {}.".format(mode_label),
            ),
        )

    if not valid_instances:
        return ExecuteOutcome(
            True,
            DialogState(
                selected_ids=refreshed_ids,
                mode_key=mode_key,
                status_text=EMPTY_SELECTION_STATUS_TEXT,
            ),
        )

    flipped = 0

    try:
        with revit.Transaction(__title__):
            for element in valid_instances:
                result = _apply_flip(element, mode_key)
                if not result:
                    raise RuntimeError(
                        "Native flip returned False for id {}.".format(_eid_int(element.Id))
                    )
                flipped += 1
    except Exception as ex:
        logger.debug("Flip failed | mode=%s | %s", mode_key, ex)
        forms.alert(
            "Flip failed and no changes were kept.\nMode: {}\n{}".format(mode_label, ex),
            title=__title__,
        )
        return ExecuteOutcome(
            True,
            DialogState(
                selected_ids=refreshed_ids,
                mode_key=mode_key,
                status_text="Flip failed: {}".format(_safe_text(ex)),
            ),
        )

    forms.alert(
        build_completion_message(mode_label, len(refreshed_ids), flipped),
        title=__title__,
        warn_icon=False,
    )
    return ExecuteOutcome(False)


def main():
    if not revit.doc or not revit.uidoc:
        forms.alert("No active project document.", title=__title__)
        return

    dialog_state = DialogState(selected_ids=list(revit.uidoc.Selection.GetElementIds()))

    while True:
        window = FlipWindow("Script.xaml", dialog_state)
        if not window.is_ready:
            return

        window.ShowDialog()
        dialog_state = window.dialog_state or dialog_state

        if window.next_action == "select":
            dialog_state = _pick_more_elements(revit.uidoc, dialog_state)
            continue

        if window.next_action == "run":
            outcome = _execute_flip_run(revit.doc, revit.uidoc, dialog_state)
            if outcome.reopen_dialog:
                dialog_state = outcome.dialog_state or dialog_state
                continue
            return

        return


if __name__ == "__main__":
    main()
