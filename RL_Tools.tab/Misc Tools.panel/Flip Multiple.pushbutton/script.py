# -*- coding: utf-8 -*-
"""Flip selected family instances one-by-one using native Revit flip actions."""

# pylint: disable=import-error,invalid-name,broad-except
import os
import sys

import clr

clr.AddReference("RevitAPIUI")
clr.AddReference("System.Windows.Forms")
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List as ClrList
import System.Windows.Forms as WinForms

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script
from pyrevit.compat import get_elementid_value_func

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from flip_multiple_utils import MODE_FRONT_BACK
from flip_multiple_utils import MODE_LEFT_RIGHT
from flip_multiple_utils import MODE_WORK_PLANE
from flip_multiple_utils import build_incompatibility_message
from flip_multiple_utils import collect_incompatible_type_labels
from flip_multiple_utils import get_mode_label

__title__ = "Flip Multiple"

logger = script.get_logger()
get_elementid_value = get_elementid_value_func()
PICK_PROMPT = "Select elements to flip"


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
    return "cancel" in str(ex).lower()


def _prompt_settings(uidoc, initial_selected_ids):
    selected_ids = _merge_element_ids(initial_selected_ids, [])

    dialog = WinForms.Form()
    dialog.Text = __title__
    dialog.Width = 900
    dialog.Height = 430
    dialog.StartPosition = WinForms.FormStartPosition.CenterScreen
    dialog.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    dialog.MinimizeBox = False
    dialog.MaximizeBox = False

    mode_lbl = WinForms.Label()
    mode_lbl.Text = "Choose one native flip mode"
    mode_lbl.Left = 20
    mode_lbl.Top = 24
    mode_lbl.Width = 240

    mode_group = WinForms.GroupBox()
    mode_group.Left = 280
    mode_group.Top = 14
    mode_group.Width = 590
    mode_group.Height = 108
    mode_group.Text = ""

    work_plane_rb = WinForms.RadioButton()
    work_plane_rb.Text = "Flip Work-plane"
    work_plane_rb.Left = 18
    work_plane_rb.Top = 24
    work_plane_rb.Width = 240

    front_back_rb = WinForms.RadioButton()
    front_back_rb.Text = "Flip Front/Back"
    front_back_rb.Left = 18
    front_back_rb.Top = 48
    front_back_rb.Width = 240

    left_right_rb = WinForms.RadioButton()
    left_right_rb.Text = "Flip Left/Right"
    left_right_rb.Left = 18
    left_right_rb.Top = 72
    left_right_rb.Width = 240

    mode_group.Controls.Add(work_plane_rb)
    mode_group.Controls.Add(front_back_rb)
    mode_group.Controls.Add(left_right_rb)

    select_btn = WinForms.Button()
    select_btn.Text = "Select Elements"
    select_btn.Left = 280
    select_btn.Top = 138
    select_btn.Width = 130

    selection_status_lbl = WinForms.Label()
    selection_status_lbl.Left = 430
    selection_status_lbl.Top = 143
    selection_status_lbl.Width = 440
    selection_status_lbl.Height = 42

    note_lbl = WinForms.Label()
    note_lbl.Left = 20
    note_lbl.Top = 196
    note_lbl.Width = 850
    note_lbl.Height = 95
    note_lbl.Text = (
        "This tool uses native Revit family flip actions only.\n"
        "Work-plane requires native work-plane flip support.\n"
        "Front/Back requires native facing flip support. Left/Right requires native hand flip support.\n"
        "If any selected element is incompatible, nothing will be flipped."
    )

    run_btn = WinForms.Button()
    run_btn.Text = "Run"
    run_btn.Left = 705
    run_btn.Top = 345
    run_btn.Width = 80

    cancel_btn = WinForms.Button()
    cancel_btn.Text = "Cancel"
    cancel_btn.DialogResult = WinForms.DialogResult.Cancel
    cancel_btn.Left = 790
    cancel_btn.Top = 345
    cancel_btn.Width = 80

    def _set_selection_status(message=None):
        if message:
            selection_status_lbl.Text = message
            return
        count = len(selected_ids)
        if count:
            selection_status_lbl.Text = "{} element(s) selected for flipping.".format(count)
        else:
            selection_status_lbl.Text = "No elements selected. Use Select Elements before running."

    def _selected_mode_key():
        if work_plane_rb.Checked:
            return MODE_WORK_PLANE
        if front_back_rb.Checked:
            return MODE_FRONT_BACK
        if left_right_rb.Checked:
            return MODE_LEFT_RIGHT
        return None

    def _select_elements_click(sender, args):
        del sender, args

        try:
            dialog.Hide()
        except Exception:
            pass

        try:
            picked_refs = uidoc.Selection.PickObjects(ObjectType.Element, PICK_PROMPT)
        except Exception as ex:
            picked_refs = None
            if _is_cancelled_pick(ex):
                _set_selection_status("Selection unchanged.")
            else:
                _set_selection_status("Select failed: {}".format(ex))
        finally:
            try:
                dialog.Show()
            except Exception:
                pass
            try:
                dialog.Activate()
            except Exception:
                pass

        if not picked_refs:
            return

        picked_ids = [ref.ElementId for ref in picked_refs if getattr(ref, "ElementId", None)]
        old_count = len(selected_ids)
        selected_ids[:] = _merge_element_ids(selected_ids, picked_ids)

        try:
            _set_ui_selection(uidoc, selected_ids)
        except Exception:
            pass

        if len(selected_ids) != old_count:
            _set_selection_status()
        else:
            _set_selection_status("Selection unchanged.")

    def _run_click(sender, args):
        del sender, args

        if not selected_ids:
            _set_selection_status("Select one or more elements before running.")
            return

        mode_key = _selected_mode_key()
        if not mode_key:
            _set_selection_status("Choose one flip mode before running.")
            return

        dialog.DialogResult = WinForms.DialogResult.OK
        dialog.Close()

    select_btn.Click += _select_elements_click
    run_btn.Click += _run_click
    _set_selection_status()

    dialog.Controls.Add(mode_lbl)
    dialog.Controls.Add(mode_group)
    dialog.Controls.Add(select_btn)
    dialog.Controls.Add(selection_status_lbl)
    dialog.Controls.Add(note_lbl)
    dialog.Controls.Add(run_btn)
    dialog.Controls.Add(cancel_btn)
    dialog.AcceptButton = run_btn
    dialog.CancelButton = cancel_btn

    if dialog.ShowDialog() != WinForms.DialogResult.OK:
        return None

    return {
        "mode_key": _selected_mode_key(),
        "selected_ids": list(selected_ids),
    }


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

    for sid in selected_ids:
        element = doc.GetElement(sid)
        if element is None:
            continue

        if not isinstance(element, DB.FamilyInstance):
            non_family_count += 1
            continue

        if not _supports_mode(element, mode_key):
            invalid_type_entries.append(_family_type_entry(element))
            continue

        valid_instances.append(element)

    return valid_instances, invalid_type_entries, non_family_count


def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if not doc or not uidoc:
        forms.alert("No active project document.", title=__title__)
        return

    selected_ids = list(uidoc.Selection.GetElementIds())

    settings = _prompt_settings(uidoc, selected_ids)
    if not settings:
        return

    mode_key = settings["mode_key"]
    mode_label = get_mode_label(mode_key)
    selected_ids = list(settings["selected_ids"])

    valid_instances, invalid_type_entries, non_family_count = _validate_selection(
        doc,
        selected_ids,
        mode_key,
    )

    incompatible_type_labels = collect_incompatible_type_labels(invalid_type_entries)
    if invalid_type_entries or non_family_count:
        forms.alert(
            build_incompatibility_message(
                mode_label,
                len(selected_ids),
                incompatible_type_labels,
                non_family_count,
            ),
            title=__title__,
        )
        return

    if not valid_instances:
        forms.alert("No compatible family instances found in the current selection.", title=__title__)
        return

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
        return

    forms.alert(
        "\n".join(
            [
                "Flip Multiple completed.",
                "Mode: {}".format(mode_label),
                "Selected elements: {}".format(len(selected_ids)),
                "Flipped: {}".format(flipped),
            ]
        ),
        title=__title__,
        warn_icon=False,
    )


if __name__ == "__main__":
    run()
