# -*- coding: utf-8 -*-
"""Rotate selected elements one-by-one around each element's own center."""

# pylint: disable=import-error,invalid-name,broad-except
import math

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

__title__ = "3D Rotate Multiple"

logger = script.get_logger()
get_elementid_value = get_elementid_value_func()
EPS = 1e-9
PICK_PROMPT = "Select elements to rotate"


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


def _parse_angle_degrees(value_text):
    txt = str(value_text).strip()
    if not txt:
        raise ValueError("Angle is empty.")
    txt = txt.replace(u"\N{DEGREE SIGN}", "").strip()
    if txt.lower().endswith("deg"):
        txt = txt[:-3].strip()
    return float(txt)


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

    angle_lbl = WinForms.Label()
    angle_lbl.Text = "Rotation angle (degrees)"
    angle_lbl.Left = 20
    angle_lbl.Top = 24
    angle_lbl.Width = 240

    angle_tb = WinForms.TextBox()
    angle_tb.Left = 280
    angle_tb.Top = 20
    angle_tb.Width = 590
    angle_tb.Text = "90"

    front_back_cb = WinForms.CheckBox()
    front_back_cb.Text = "Vertical rotate (Front/Back elevation plane)"
    front_back_cb.Left = 280
    front_back_cb.Top = 58
    front_back_cb.Width = 590
    front_back_cb.Checked = False

    left_right_cb = WinForms.CheckBox()
    left_right_cb.Text = "Vertical rotate (Left/Right elevation plane)"
    left_right_cb.Left = 280
    left_right_cb.Top = 84
    left_right_cb.Width = 590
    left_right_cb.Checked = False

    def _on_front_back_changed(sender, args):
        del sender, args
        if front_back_cb.Checked:
            left_right_cb.Checked = False

    def _on_left_right_changed(sender, args):
        del sender, args
        if left_right_cb.Checked:
            front_back_cb.Checked = False

    front_back_cb.CheckedChanged += _on_front_back_changed
    left_right_cb.CheckedChanged += _on_left_right_changed

    note_lbl = WinForms.Label()
    note_lbl.Left = 20
    note_lbl.Top = 178
    note_lbl.Width = 850
    note_lbl.Height = 90
    note_lbl.Text = (
        "Choose one vertical mode for 3D rotation:\n"
        "Front/Back uses global X axis. Left/Right uses global Y axis.\n"
        "Leave both unchecked for normal plan rotation (global Z axis)."
    )

    select_btn = WinForms.Button()
    select_btn.Text = "Select Elements"
    select_btn.Left = 280
    select_btn.Top = 122
    select_btn.Width = 130

    selection_status_lbl = WinForms.Label()
    selection_status_lbl.Left = 430
    selection_status_lbl.Top = 127
    selection_status_lbl.Width = 440
    selection_status_lbl.Height = 42

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
            selection_status_lbl.Text = "{} element(s) selected for rotation.".format(count)
        else:
            selection_status_lbl.Text = "No elements selected. Use Select Elements before running."

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
        dialog.DialogResult = WinForms.DialogResult.OK
        dialog.Close()

    select_btn.Click += _select_elements_click
    run_btn.Click += _run_click
    _set_selection_status()

    dialog.Controls.Add(angle_lbl)
    dialog.Controls.Add(angle_tb)
    dialog.Controls.Add(front_back_cb)
    dialog.Controls.Add(left_right_cb)
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
        "angle_text": angle_tb.Text,
        "rotate_front_back": bool(front_back_cb.Checked),
        "rotate_left_right": bool(left_right_cb.Checked),
        "selected_ids": list(selected_ids),
    }


def _midpoint_xyz(pt_a, pt_b):
    return DB.XYZ(
        (pt_a.X + pt_b.X) * 0.5,
        (pt_a.Y + pt_b.Y) * 0.5,
        (pt_a.Z + pt_b.Z) * 0.5,
    )


def _get_rotation_center(element, active_view):
    loc = getattr(element, "Location", None)

    try:
        if isinstance(loc, DB.LocationPoint) and loc.Point:
            return loc.Point
    except Exception:
        pass

    try:
        if isinstance(loc, DB.LocationCurve) and loc.Curve:
            curve = loc.Curve
            try:
                return curve.Evaluate(0.5, True)
            except Exception:
                return _midpoint_xyz(curve.GetEndPoint(0), curve.GetEndPoint(1))
    except Exception:
        pass

    for method_name in ("GetTransform", "GetTotalTransform"):
        try:
            method = getattr(element, method_name, None)
            if not method:
                continue
            transform = method()
            if transform and transform.Origin:
                return transform.Origin
        except Exception:
            continue

    for bbox_view in (active_view, None):
        try:
            bbox = element.get_BoundingBox(bbox_view)
            if bbox and bbox.Min and bbox.Max:
                return _midpoint_xyz(bbox.Min, bbox.Max)
        except Exception:
            continue

    return None


def _axis_direction(rotate_front_back, rotate_left_right):
    # Front/Back elevation-like rotation plane: YZ plane => axis X
    if rotate_front_back:
        return DB.XYZ.BasisX
    # Left/Right elevation-like rotation plane: XZ plane => axis Y
    if rotate_left_right:
        return DB.XYZ.BasisY
    return DB.XYZ.BasisZ


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

    selected_ids = list(settings["selected_ids"])

    try:
        angle_deg = _parse_angle_degrees(settings["angle_text"])
    except Exception as ex:
        forms.alert("Invalid angle value: {}".format(ex), title=__title__)
        return

    if abs(angle_deg) < EPS:
        forms.alert("Angle is 0. Nothing to rotate.", title=__title__)
        return

    active_view = uidoc.ActiveView
    axis_dir = _axis_direction(
        settings["rotate_front_back"],
        settings["rotate_left_right"],
    )
    if not axis_dir or axis_dir.GetLength() < EPS:
        forms.alert("Could not determine rotation axis.", title=__title__)
        return
    axis_dir = axis_dir.Normalize()
    angle_rad = math.radians(angle_deg)

    elements = []
    skipped_types = 0
    for sid in selected_ids:
        elem = doc.GetElement(sid)
        if not elem:
            continue
        if isinstance(elem, DB.ElementType):
            skipped_types += 1
            continue
        elements.append(elem)

    if not elements:
        forms.alert("No model elements found in the current selection.", title=__title__)
        return

    rotated = 0
    skipped_pinned = 0
    skipped_no_center = 0
    errors = 0
    sample_errors = []

    with revit.Transaction(__title__):
        for elem in elements:
            elem_id = _eid_int(elem.Id)

            try:
                if getattr(elem, "Pinned", False):
                    skipped_pinned += 1
                    continue
            except Exception:
                pass

            center = _get_rotation_center(elem, active_view)
            if center is None:
                skipped_no_center += 1
                continue

            try:
                axis = DB.Line.CreateBound(center, center + axis_dir)
                DB.ElementTransformUtils.RotateElement(doc, elem.Id, axis, angle_rad)
                rotated += 1
            except Exception as ex:
                errors += 1
                logger.debug("Rotate failed | id=%s | %s", elem_id, ex)
                if len(sample_errors) < 20:
                    sample_errors.append("id {} -> {}".format(elem_id, ex))

    if settings["rotate_front_back"]:
        mode = "Vertical (Front/Back elevation plane, global X axis)"
    elif settings["rotate_left_right"]:
        mode = "Vertical (Left/Right elevation plane, global Y axis)"
    else:
        mode = "Plan (global Z axis)"
    lines = [
        "Rotate Multiple completed.",
        "Angle: {} deg".format(round(angle_deg, 6)),
        "Mode: {}".format(mode),
        "Selected elements: {}".format(len(selected_ids)),
        "Rotated: {}".format(rotated),
        "Skipped pinned: {}".format(skipped_pinned),
        "Skipped no center: {}".format(skipped_no_center),
        "Skipped element types: {}".format(skipped_types),
        "Errors: {}".format(errors),
    ]

    if sample_errors:
        lines.append("")
        lines.append("Error samples:")
        lines.extend(sample_errors)

    forms.alert("\n".join(lines), title=__title__, warn_icon=False)


if __name__ == "__main__":
    run()
