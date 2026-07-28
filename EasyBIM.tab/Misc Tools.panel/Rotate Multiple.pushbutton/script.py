# -*- coding: utf-8 -*-
"""Rotate selected elements one-by-one around each element's own center."""

# pylint: disable=import-error,invalid-name,broad-except
import math

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

__title__ = "3D Rotate Multiple"

logger = script.get_logger()
get_elementid_value = get_elementid_value_func()
EPS = 1e-9
PICK_PROMPT = "Select elements to rotate"

MODE_PLAN = "plan"
MODE_FRONT_BACK = "front_back"
MODE_LEFT_RIGHT = "left_right"

MODE_LABELS = {
    MODE_PLAN: "Plan Rotation",
    MODE_FRONT_BACK: "Vertical Rotation (Front/Back)",
    MODE_LEFT_RIGHT: "Vertical Rotation (Left/Right)",
}

MODE_HELPERS = {
    MODE_PLAN: "Rotate around the global Z axis for normal plan rotation.",
    MODE_FRONT_BACK: "Rotate around the global X axis using the front/back elevation plane.",
    MODE_LEFT_RIGHT: "Rotate around the global Y axis using the left/right elevation plane.",
}

EMPTY_SELECTION_STATUS_TEXT = "Select one or more elements before running."


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


def _parse_angle_degrees(value_text):
    txt = _safe_text(value_text).strip()
    if not txt:
        raise ValueError("Angle is empty.")
    txt = txt.replace(u"\N{DEGREE SIGN}", "").strip()
    if txt.lower().endswith("deg"):
        txt = txt[:-3].strip()
    return float(txt)


def _get_mode_labels():
    return [
        MODE_LABELS[MODE_PLAN],
        MODE_LABELS[MODE_FRONT_BACK],
        MODE_LABELS[MODE_LEFT_RIGHT],
    ]


def _get_mode_key_from_label(label):
    label_text = _safe_text(label)
    for mode_key, mode_label in MODE_LABELS.items():
        if mode_label == label_text:
            return mode_key
    return None


def _get_mode_label(mode_key):
    return MODE_LABELS.get(mode_key, MODE_LABELS[MODE_PLAN])


def _get_mode_helper_text(mode_key):
    return MODE_HELPERS.get(mode_key, MODE_HELPERS[MODE_PLAN])


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
    def __init__(self, selected_ids=None, angle_text="90", mode_key=MODE_PLAN, status_text=""):
        self.selected_ids = list(selected_ids or [])
        self.angle_text = _safe_text(angle_text) or "90"
        self.mode_key = mode_key or MODE_PLAN
        self.status_text = _safe_text(status_text)


class RotateWindow(forms.WPFWindow):
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

        self.mode_cb.ItemsSource = list(_get_mode_labels())
        self.mode_cb.SelectedItem = _get_mode_label(self.dialog_state.mode_key)
        self.angle_tb.Text = self.dialog_state.angle_text
        self._refresh_selection_state()
        self.status_tb.Text = self.dialog_state.status_text
        self.is_ready = True

    def _capture_state(self, status_text=""):
        return DialogState(
            selected_ids=self.selected_ids,
            angle_text=self.angle_tb.Text,
            mode_key=self._selected_mode_key(),
            status_text=status_text,
        )

    def _set_action_controls_enabled(self, is_enabled):
        for control in (self.select_btn, self.run_btn, self.cancel_btn, self.mode_cb, self.angle_tb):
            try:
                control.IsEnabled = bool(is_enabled)
            except Exception:
                continue

    def _selected_mode_key(self):
        return _get_mode_key_from_label(getattr(self.mode_cb, "SelectedItem", None))

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
        self.helper_tb.Text = _get_mode_helper_text(self._selected_mode_key())

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

        try:
            angle_deg = _parse_angle_degrees(self.angle_tb.Text)
        except Exception as ex:
            self.status_tb.Text = "Invalid angle value: {}".format(ex)
            return

        if abs(angle_deg) < EPS:
            self.status_tb.Text = "Angle is 0. Nothing to rotate."
            return

        if not self._selected_mode_key():
            self.status_tb.Text = "Choose one rotation mode before running."
            return

        self._set_action_controls_enabled(False)
        self.status_tb.Text = "Running rotate operation..."
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
            angle_text=dialog_state.angle_text,
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
        angle_text=dialog_state.angle_text,
        mode_key=dialog_state.mode_key,
        status_text=status_text,
    )


def _show_dialog(initial_state):
    dialog_state = initial_state

    while True:
        window = RotateWindow("Script.xaml", dialog_state)
        if not window.is_ready:
            return None

        window.ShowDialog()

        if window.next_action == "select":
            dialog_state = _pick_more_elements(window.uidoc, window.dialog_state)
            continue
        if window.next_action == "run":
            return window.dialog_state
        return None


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


def _axis_direction(mode_key):
    if mode_key == MODE_FRONT_BACK:
        return DB.XYZ.BasisX
    if mode_key == MODE_LEFT_RIGHT:
        return DB.XYZ.BasisY
    return DB.XYZ.BasisZ


def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if not doc or not uidoc:
        forms.alert("No active project document.", title=__title__)
        return

    initial_state = DialogState(selected_ids=list(uidoc.Selection.GetElementIds()))
    settings = _show_dialog(initial_state)
    if not settings:
        return

    selected_ids = list(settings.selected_ids)

    try:
        angle_deg = _parse_angle_degrees(settings.angle_text)
    except Exception as ex:
        forms.alert("Invalid angle value: {}".format(ex), title=__title__)
        return

    if abs(angle_deg) < EPS:
        forms.alert("Angle is 0. Nothing to rotate.", title=__title__)
        return

    active_view = uidoc.ActiveView
    axis_dir = _axis_direction(settings.mode_key)
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

    lines = [
        "Rotate Multiple completed.",
        "Angle: {} deg".format(round(angle_deg, 6)),
        "Mode: {}".format(_get_mode_label(settings.mode_key)),
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
