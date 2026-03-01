# -*- coding: utf-8 -*-
"""Rotate selected elements one-by-one around each element's own center."""

# pylint: disable=import-error,invalid-name,broad-except
import math

import clr

clr.AddReference("System.Windows.Forms")
import System.Windows.Forms as WinForms

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script
from pyrevit.compat import get_elementid_value_func

__title__ = "Rotate Multiple"

logger = script.get_logger()
get_elementid_value = get_elementid_value_func()
EPS = 1e-9


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


def _prompt_settings():
    dialog = WinForms.Form()
    dialog.Text = __title__
    dialog.Width = 520
    dialog.Height = 250
    dialog.StartPosition = WinForms.FormStartPosition.CenterScreen
    dialog.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    dialog.MinimizeBox = False
    dialog.MaximizeBox = False

    angle_lbl = WinForms.Label()
    angle_lbl.Text = "Rotation angle (degrees)"
    angle_lbl.Left = 20
    angle_lbl.Top = 24
    angle_lbl.Width = 170

    angle_tb = WinForms.TextBox()
    angle_tb.Left = 200
    angle_tb.Top = 20
    angle_tb.Width = 290
    angle_tb.Text = "90"

    zaxis_cb = WinForms.CheckBox()
    zaxis_cb.Text = "Rotate in Z-axis"
    zaxis_cb.Left = 200
    zaxis_cb.Top = 58
    zaxis_cb.Width = 200
    zaxis_cb.Checked = False

    note_lbl = WinForms.Label()
    note_lbl.Left = 20
    note_lbl.Top = 90
    note_lbl.Width = 470
    note_lbl.Height = 56
    note_lbl.Text = (
        "Off: plan-style rotation around global Z.\n"
        "On: elevation-style rotation around active view direction."
    )

    run_btn = WinForms.Button()
    run_btn.Text = "Run"
    run_btn.DialogResult = WinForms.DialogResult.OK
    run_btn.Left = 325
    run_btn.Top = 155
    run_btn.Width = 80

    cancel_btn = WinForms.Button()
    cancel_btn.Text = "Cancel"
    cancel_btn.DialogResult = WinForms.DialogResult.Cancel
    cancel_btn.Left = 410
    cancel_btn.Top = 155
    cancel_btn.Width = 80

    dialog.Controls.Add(angle_lbl)
    dialog.Controls.Add(angle_tb)
    dialog.Controls.Add(zaxis_cb)
    dialog.Controls.Add(note_lbl)
    dialog.Controls.Add(run_btn)
    dialog.Controls.Add(cancel_btn)
    dialog.AcceptButton = run_btn
    dialog.CancelButton = cancel_btn

    if dialog.ShowDialog() != WinForms.DialogResult.OK:
        return None

    return {
        "angle_text": angle_tb.Text,
        "rotate_in_zaxis": bool(zaxis_cb.Checked),
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


def _axis_direction(rotate_in_zaxis, active_view):
    if rotate_in_zaxis:
        try:
            direction = active_view.ViewDirection
            if direction and direction.GetLength() > EPS:
                return direction.Normalize()
        except Exception:
            pass
    return DB.XYZ.BasisZ


def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if not doc or not uidoc:
        forms.alert("No active project document.", title=__title__)
        return

    selected_ids = list(uidoc.Selection.GetElementIds())
    if not selected_ids:
        forms.alert("Select one or more elements first.", title=__title__)
        return

    settings = _prompt_settings()
    if not settings:
        return

    try:
        angle_deg = _parse_angle_degrees(settings["angle_text"])
    except Exception as ex:
        forms.alert("Invalid angle value: {}".format(ex), title=__title__)
        return

    if abs(angle_deg) < EPS:
        forms.alert("Angle is 0. Nothing to rotate.", title=__title__)
        return

    active_view = uidoc.ActiveView
    axis_dir = _axis_direction(settings["rotate_in_zaxis"], active_view)
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

    mode = "Elevation-style (active view direction)" \
        if settings["rotate_in_zaxis"] else "Plan-style (global Z)"
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
