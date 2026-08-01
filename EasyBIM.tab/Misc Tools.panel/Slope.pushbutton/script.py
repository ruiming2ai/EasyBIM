# -*- coding: utf-8 -*-
"""Update the visible built-in slope parameter on selected elements."""

# pylint: disable=import-error,invalid-name,broad-except,too-many-lines,too-many-instance-attributes
import math
import re

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
from pyrevit.framework import System


__title__ = "Slope"

logger = script.get_logger()
get_elementid_value = get_elementid_value_func()

EPS = 1e-9
RATIO_COMPARE_EPS = 1e-6
NEAR_VERTICAL_COS_EPS = 1e-6
NUMERIC_TOKEN_RE = re.compile(r"[-+]?(?:\d+(?:[.,]\d+)?|\d*[.,]\d+)(?:[eE][-+]?\d+)?")

DEGREE_UNIT_LABEL = "Degrees"
DESIRED_SLOPE_UNIT_LABELS = [
    "1 : Ratio",
    "Per mille",
    "Percentage",
    "Ratio : 1",
    "Ratio : 10",
    "Ratio : 12",
    "Rise / 1000 millimeters",
    "Rise / 10 feet",
    "Rise / 120 inches",
    "Rise / 12 inches",
    "Rise / 1 foot",
    DEGREE_UNIT_LABEL,
]

PRIMARY_DISPLAY_PARAM_NAMES = (
    "RBS_DUCT_SLOPE",
    "RBS_PIPE_SLOPE",
    "RBS_CURVE_UTSLOPE",
    "RBS_CONDUIT_SLOPE",
    "RBS_CABLETRAY_SLOPE",
)
PERCENT_HELPER_PARAM_NAMES = ("RBS_CURVE_SLOPE",)
PICK_PROMPT = "Select more elements for slope editing"

UNIT_HELPER_TEXTS = {
    "1 : Ratio": "Enter the second number only. Example: 12 means 1 : 12.",
    "Per mille": "Enter the per-mille value. Example: 25 means 25 per mille.",
    "Percentage": "Enter the percent value. Example: 2 means 2 percent.",
    "Ratio : 1": "Enter the first number only. Example: 0.5 means 0.5 : 1.",
    "Ratio : 10": "Enter the first number only. Example: 5 means 5 : 10.",
    "Ratio : 12": "Enter the first number only. Example: 3 means 3 : 12.",
    "Rise / 1000 millimeters": "Enter the rise over 1000 millimeters of run.",
    "Rise / 10 feet": "Enter the rise over 10 feet of run.",
    "Rise / 120 inches": "Enter the rise over 120 inches of run.",
    "Rise / 12 inches": "Enter the rise over 12 inches of run.",
    "Rise / 1 foot": "Enter the rise over 1 foot of run.",
    DEGREE_UNIT_LABEL: "Enter an angle in degrees. Example: 30 means a 30 degree slope.",
}

READY_STATUS_TEXT = (
    "Ready. The command tries direct slope-parameter updates first and then "
    "falls back to center-fixed geometry when Revit blocks the write."
)
EMPTY_SELECTION_STATUS_TEXT = "Click Select to add elements from the model."

FALLBACK_UNIT_ID_ATTRS = {
    "1 : Ratio": ("Ratio", "SlopeRatio"),
    "Per mille": ("PerMille",),
    "Percentage": ("Percentage",),
    "Ratio : 1": ("Ratio1",),
    "Ratio : 10": ("Ratio10",),
    "Ratio : 12": ("Ratio12",),
    "Rise / 1000 millimeters": ("RiseDividedBy1000Millimeters",),
    "Rise / 10 feet": ("RiseDividedBy10Feet",),
    "Rise / 120 inches": ("RiseDividedBy120Inches",),
    "Rise / 12 inches": ("RiseDividedBy12Inches",),
    "Rise / 1 foot": ("RiseDividedBy1Foot",),
    DEGREE_UNIT_LABEL: ("Degrees", "SlopeDegrees"),
}


from easybim.compat import safe_text as _safe_text


def _normalize_text(value):
    return _safe_text(value).strip()


from easybim.compat import eid_to_int as _eid_int


def _parse_number_token(token):
    token = _normalize_text(token)
    if not token:
        return None

    number_styles = (
        System.Globalization.NumberStyles.Float
        | System.Globalization.NumberStyles.AllowThousands
    )
    parse_cultures = (
        System.Globalization.CultureInfo.CurrentCulture,
        System.Globalization.CultureInfo.InvariantCulture,
    )

    for culture in parse_cultures:
        try:
            return float(System.Double.Parse(token, number_styles, culture))
        except Exception:
            continue

    normalized = token.replace(" ", "")
    if normalized.count(",") > 1 and "." not in normalized:
        normalized = normalized.replace(",", "")
    elif normalized.count(".") > 1 and "," not in normalized:
        normalized = normalized.replace(".", "")
    else:
        normalized = normalized.replace(",", ".")

    try:
        return float(normalized)
    except Exception:
        return None


def _parse_single_numeric_value(value_text):
    raw_text = _normalize_text(value_text)
    if not raw_text:
        raise ValueError("Enter a slope value.")

    cleaned = (
        raw_text.replace(u"\N{DEGREE SIGN}", " ")
        .replace("%", " ")
        .replace("per mille", " ")
        .replace("permille", " ")
        .replace("per-mille", " ")
    )

    tokens = NUMERIC_TOKEN_RE.findall(cleaned)
    if len(tokens) != 1:
        raise ValueError("Enter a single numeric value for the selected unit.")

    parsed_value = _parse_number_token(tokens[0])
    if parsed_value is None:
        raise ValueError("Could not parse the slope value.")
    return parsed_value


def _element_category_name(element):
    try:
        category = getattr(element, "Category", None)
        if category:
            return _safe_text(category.Name) or "<No Category>"
    except Exception:
        pass
    return "<No Category>"


def _element_name(element):
    for attr_name in ("Name",):
        try:
            value = getattr(element, attr_name, None)
            txt = _safe_text(value).strip()
            if txt:
                return txt
        except Exception:
            continue

    try:
        return _safe_text(element.GetType().Name) or "<Unnamed>"
    except Exception:
        return "<Unnamed>"


def _current_slope_text(param):
    if not param:
        return ""

    try:
        value_string = param.AsValueString()
        if value_string:
            return _safe_text(value_string)
    except Exception:
        pass

    try:
        return str(round(float(param.AsDouble()), 8))
    except Exception:
        return ""


def _param_is_user_modifiable(param):
    try:
        return bool(param.UserModifiable)
    except Exception:
        return True


def _get_param_by_builtin_name(element, bip_name):
    if not element or not bip_name:
        return None

    bip = getattr(DB.BuiltInParameter, bip_name, None)
    if bip is None:
        return None

    try:
        return element.get_Parameter(bip)
    except Exception:
        return None


def _category_matches(element, *category_names):
    category = getattr(element, "Category", None)
    category_id_int = _eid_int(getattr(category, "Id", None)) if category else None
    if category_id_int is None:
        return False

    for cat_name in category_names:
        bic = getattr(DB.BuiltInCategory, cat_name, None)
        if bic is None:
            continue
        try:
            if int(bic) == category_id_int:
                return True
        except Exception:
            try:
                if _eid_int(DB.ElementId(bic)) == category_id_int:
                    return True
            except Exception:
                continue

    return False


def _display_param_name_priority(element):
    prioritized = []
    if _category_matches(element, "OST_DuctCurves"):
        prioritized.extend(("RBS_DUCT_SLOPE", "RBS_CURVE_UTSLOPE"))
    elif _category_matches(element, "OST_PipeCurves"):
        prioritized.extend(("RBS_PIPE_SLOPE", "RBS_CURVE_UTSLOPE"))
    elif _category_matches(element, "OST_Conduit", "OST_CableTray"):
        prioritized.append("RBS_CURVE_UTSLOPE")

    for name in PRIMARY_DISPLAY_PARAM_NAMES:
        if name not in prioritized:
            prioritized.append(name)
    return prioritized


def _dedupe_params(params):
    unique = []
    seen = set()
    for param in params:
        pid_int = _eid_int(getattr(param, "Id", None))
        if pid_int in seen:
            continue
        seen.add(pid_int)
        unique.append(param)
    return unique


def _get_param_spec_id(param):
    definition = getattr(param, "Definition", None)
    try:
        spec_id = definition.GetDataType() if definition else None
        if _safe_text(getattr(spec_id, "TypeId", "")):
            return spec_id
    except Exception:
        pass
    return None


def _get_unit_label(unit_id):
    if unit_id is None:
        return ""

    try:
        return _safe_text(DB.LabelUtils.GetLabelForUnit(unit_id)).strip()
    except Exception:
        pass

    try:
        return _safe_text(DB.LabelUtils.GetLabelFor(unit_id)).strip()
    except Exception:
        pass

    return _safe_text(unit_id).strip()


def _get_valid_unit_ids(spec_id):
    if spec_id is None:
        return []
    try:
        return list(DB.UnitUtils.GetValidUnits(spec_id))
    except Exception:
        return []


def _resolve_unit_id_for_label(spec_id, unit_label):
    for unit_id in _get_valid_unit_ids(spec_id):
        if _get_unit_label(unit_id) == unit_label:
            return unit_id

    unit_type_id = getattr(DB, "UnitTypeId", None)
    if unit_type_id:
        for attr_name in FALLBACK_UNIT_ID_ATTRS.get(unit_label, ()):
            resolved = getattr(unit_type_id, attr_name, None)
            if resolved is not None:
                return resolved

    return None


def _copy_format_options(current_format_options, unit_id):
    format_options = None

    if current_format_options is not None:
        try:
            format_options = DB.FormatOptions(current_format_options)
        except Exception:
            format_options = None

    if format_options is None:
        try:
            format_options = DB.FormatOptions(unit_id)
        except Exception:
            format_options = current_format_options

    if format_options is None:
        return None

    try:
        format_options.UseDefault = False
    except Exception:
        pass

    try:
        format_options.SetUnitTypeId(unit_id)
    except Exception:
        try:
            format_options.DisplayUnits = unit_id
        except Exception:
            pass

    return format_options


def _clone_units(doc):
    units = doc.GetUnits()
    try:
        return DB.Units(units)
    except Exception:
        return units


def _build_custom_units(doc, spec_id, unit_label):
    if doc is None or spec_id is None:
        return None

    unit_id = _resolve_unit_id_for_label(spec_id, unit_label)
    if unit_id is None:
        return None

    custom_units = _clone_units(doc)

    try:
        current_format_options = custom_units.GetFormatOptions(spec_id)
    except Exception:
        current_format_options = None

    format_options = _copy_format_options(current_format_options, unit_id)
    if format_options is None:
        return None

    try:
        custom_units.SetFormatOptions(spec_id, format_options)
        return custom_units
    except Exception:
        return None


def _coerce_tryparse_result(parse_result):
    if isinstance(parse_result, (tuple, list)):
        if len(parse_result) >= 2:
            return bool(parse_result[0]), parse_result[1]
        if len(parse_result) == 1:
            return bool(parse_result[0]), None
    return False, None


def _parse_ratio_manually(unit_label, value_text):
    numeric_value = _parse_single_numeric_value(value_text)

    if unit_label == DEGREE_UNIT_LABEL:
        radians = math.radians(numeric_value)
        if abs(math.cos(radians)) < NEAR_VERTICAL_COS_EPS:
            raise ValueError("Near-vertical angles are not supported.")
        return math.tan(radians)

    if unit_label == "1 : Ratio":
        if abs(numeric_value) < EPS:
            raise ValueError("1 : Ratio cannot use 0.")
        return 1.0 / numeric_value
    if unit_label == "Per mille":
        return numeric_value / 1000.0
    if unit_label == "Percentage":
        return numeric_value / 100.0
    if unit_label == "Ratio : 1":
        return numeric_value
    if unit_label == "Ratio : 10":
        return numeric_value / 10.0
    if unit_label == "Ratio : 12":
        return numeric_value / 12.0
    if unit_label == "Rise / 1000 millimeters":
        return numeric_value / 1000.0
    if unit_label == "Rise / 10 feet":
        return numeric_value / 10.0
    if unit_label == "Rise / 120 inches":
        return numeric_value / 120.0
    if unit_label == "Rise / 12 inches":
        return numeric_value / 12.0
    if unit_label == "Rise / 1 foot":
        return numeric_value

    raise ValueError("Unsupported slope unit '{}'.".format(unit_label))


def _parse_target_ratio(doc, spec_id, unit_label, value_text):
    custom_units = _build_custom_units(doc, spec_id, unit_label)
    if custom_units is not None and spec_id is not None:
        try:
            parse_result = DB.UnitFormatUtils.TryParse(custom_units, spec_id, value_text)
            success, parsed_value = _coerce_tryparse_result(parse_result)
            if success and parsed_value is not None:
                return float(parsed_value)
        except Exception:
            pass

    return float(_parse_ratio_manually(unit_label, value_text))


def _format_ratio_for_project(doc, spec_id, ratio_value):
    if doc is None or spec_id is None:
        return None

    doc_units = doc.GetUnits()
    for args in (
        (doc_units, spec_id, float(ratio_value), False, True),
        (doc_units, spec_id, float(ratio_value), False),
        (doc_units, spec_id, float(ratio_value)),
    ):
        try:
            formatted = DB.UnitFormatUtils.Format(*args)
            formatted_text = _normalize_text(formatted)
            if formatted_text:
                return formatted_text
        except Exception:
            continue

    return None


def _get_project_unit_label(doc, spec_id):
    if doc is None or spec_id is None:
        return ""

    try:
        format_options = doc.GetUnits().GetFormatOptions(spec_id)
    except Exception:
        format_options = None

    if not format_options:
        return ""

    try:
        unit_id = format_options.GetUnitTypeId()
        label = _get_unit_label(unit_id)
        if label:
            return label
    except Exception:
        pass

    try:
        display_unit = format_options.DisplayUnits
        label = _get_unit_label(display_unit)
        if label:
            return label
    except Exception:
        pass

    return ""


def _read_param_ratio(param):
    if not param:
        return None

    try:
        if param.StorageType != DB.StorageType.Double:
            return None
    except Exception:
        return None

    try:
        return float(param.AsDouble())
    except Exception:
        return None


def _get_location_line(element):
    loc = getattr(element, "Location", None)
    if not isinstance(loc, DB.LocationCurve):
        return None, None, "Element does not have a LocationCurve."

    curve = getattr(loc, "Curve", None)
    if curve is None:
        return None, None, "LocationCurve is empty."

    try:
        if isinstance(curve, DB.Line):
            return loc, curve, ""
    except Exception:
        pass

    try:
        if _safe_text(curve.GetType().Name).lower() == "line":
            return loc, curve, ""
    except Exception:
        pass

    return None, None, "Only straight line-based curves support geometry fallback."


def _curve_horizontal_run(line):
    if line is None:
        return None
    try:
        start_pt = line.GetEndPoint(0)
        end_pt = line.GetEndPoint(1)
        delta_x = end_pt.X - start_pt.X
        delta_y = end_pt.Y - start_pt.Y
        return math.sqrt(delta_x * delta_x + delta_y * delta_y)
    except Exception:
        return None


def _curve_ratio_from_line(line):
    if line is None:
        return None

    try:
        start_pt = line.GetEndPoint(0)
        end_pt = line.GetEndPoint(1)
    except Exception:
        return None

    horizontal_run = _curve_horizontal_run(line)
    if horizontal_run is None or horizontal_run < EPS:
        return None

    return float((end_pt.Z - start_pt.Z) / horizontal_run)


def _build_center_fixed_line(line, target_ratio):
    start_pt = line.GetEndPoint(0)
    end_pt = line.GetEndPoint(1)

    horizontal_run = _curve_horizontal_run(line)
    if horizontal_run is None or horizontal_run < EPS:
        raise ValueError("Geometry fallback requires a non-zero horizontal run.")

    midpoint_z = (start_pt.Z + end_pt.Z) * 0.5
    delta_z = float(target_ratio) * horizontal_run
    half_delta = delta_z * 0.5

    new_start = DB.XYZ(start_pt.X, start_pt.Y, midpoint_z - half_delta)
    new_end = DB.XYZ(end_pt.X, end_pt.Y, midpoint_z + half_delta)

    try:
        return DB.Line.CreateBound(new_start, new_end)
    except Exception as ex:
        raise ValueError("Could not create fallback line: {}".format(ex))


def _is_param_writable(param):
    if not param:
        return False
    try:
        if param.StorageType != DB.StorageType.Double:
            return False
    except Exception:
        return False
    try:
        if param.IsReadOnly:
            return False
    except Exception:
        pass
    return _param_is_user_modifiable(param)


class ResultRow(object):
    def __init__(self, stage, element_id, category_name, element_name, current_slope, reason):
        self.stage = _safe_text(stage)
        self.element_id = _safe_text(element_id)
        self.category_name = _safe_text(category_name)
        self.element_name = _safe_text(element_name)
        self.current_slope = _safe_text(current_slope)
        self.reason = _safe_text(reason)


class SlopeParamBundle(object):
    def __init__(self, display_params, helper_params, spec_id, project_unit_label):
        self.display_params = list(display_params or [])
        self.helper_params = list(helper_params or [])
        self.primary_param = self.display_params[0] if self.display_params else None
        self.readback_param = self.primary_param or (self.helper_params[0] if self.helper_params else None)
        self.spec_id = spec_id
        self.project_unit_label = _safe_text(project_unit_label)

    @property
    def has_any(self):
        return bool(self.display_params or self.helper_params)


def _build_result_row(element, current_slope, reason, stage):
    return ResultRow(
        stage=stage,
        element_id=_safe_text(_eid_int(getattr(element, "Id", None))) or "<unknown>",
        category_name=_element_category_name(element),
        element_name=_element_name(element),
        current_slope=_safe_text(current_slope),
        reason=_safe_text(reason),
    )


def _resolve_slope_bundle(element):
    display_params = []
    helper_params = []

    for bip_name in _display_param_name_priority(element):
        param = _get_param_by_builtin_name(element, bip_name)
        if param:
            display_params.append(param)

    for bip_name in PERCENT_HELPER_PARAM_NAMES:
        param = _get_param_by_builtin_name(element, bip_name)
        if param:
            helper_params.append(param)

    display_params = _dedupe_params(display_params)
    helper_params = _dedupe_params(helper_params)

    spec_param = display_params[0] if display_params else (helper_params[0] if helper_params else None)
    spec_id = _get_param_spec_id(spec_param)
    project_unit_label = _get_project_unit_label(revit.doc, spec_id)
    return SlopeParamBundle(display_params, helper_params, spec_id, project_unit_label)


def _resolve_first_spec_id(doc, elements):
    del doc
    for element in elements or []:
        bundle = _resolve_slope_bundle(element)
        if bundle.spec_id is not None:
            return bundle.spec_id, bundle.project_unit_label
    return None, ""


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
            if eid_int in seen:
                continue
            seen.add(eid_int)
            merged.append(element_id)

    return merged


def _is_cancelled_pick(ex):
    if isinstance(ex, OperationCanceledException):
        return True
    return "cancel" in _safe_text(ex).lower()


class SlopeResultsWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, summary_text, result_rows):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.summary_tb.Text = _safe_text(summary_text)
        self.result_lv.ItemsSource = list(result_rows or [])

    def close_click(self, sender, args):
        del sender, args
        self.Close()


class DialogState(object):
    def __init__(self, selected_ids=None, slope_value="", unit_mode=DEGREE_UNIT_LABEL, status_text=""):
        self.selected_ids = list(selected_ids or [])
        self.slope_value = _safe_text(slope_value)
        self.unit_mode = _safe_text(unit_mode) or DEGREE_UNIT_LABEL
        self.status_text = _safe_text(status_text)


class OperationContext(object):
    def __init__(self, element, bundle, location_curve=None, line=None, geometry_precheck_reason=""):
        self.element = element
        self.bundle = bundle
        self.location_curve = location_curve
        self.line = line
        self.geometry_precheck_reason = _safe_text(geometry_precheck_reason)
        self.current_ratio = _read_param_ratio(bundle.readback_param)
        self.current_text = _current_slope_text(bundle.readback_param)
        self.restore_text = self.current_text
        self.original_line = line


class ApplyResult(object):
    def __init__(self, status, stage, reason, updated_bundle=None):
        self.status = _safe_text(status)
        self.stage = _safe_text(stage)
        self.reason = _safe_text(reason)
        self.updated_bundle = updated_bundle


class ExecuteOutcome(object):
    def __init__(self, reopen_dialog, dialog_state=None):
        self.reopen_dialog = bool(reopen_dialog)
        self.dialog_state = dialog_state


class RunStats(object):
    def __init__(self, selected_count):
        self.selected_count = int(selected_count)
        self.eligible_count = 0
        self.direct_parameter_updates = 0
        self.geometry_fallback_updates = 0
        self.no_change_count = 0
        self.skipped_count = 0
        self.failed_count = 0

    def build_summary(self, unit_mode, input_value):
        lines = [
            "Slope completed.",
            "Unit: {}".format(_safe_text(unit_mode)),
            "Input: {}".format(_safe_text(input_value)),
            "Selected elements: {}".format(self.selected_count),
            "Eligible elements: {}".format(self.eligible_count),
            "Direct parameter updates: {}".format(self.direct_parameter_updates),
            "Geometry fallback updates: {}".format(self.geometry_fallback_updates),
            "No-op: {}".format(self.no_change_count),
            "Skipped: {}".format(self.skipped_count),
            "Failed: {}".format(self.failed_count),
        ]
        return "\n".join(lines)


def _build_status_text(selected_count, custom_text=""):
    custom_text = _normalize_text(custom_text)
    if custom_text:
        return custom_text
    if int(selected_count or 0) <= 0:
        return EMPTY_SELECTION_STATUS_TEXT
    return READY_STATUS_TEXT


def _ratio_matches(left_value, right_value):
    if left_value is None or right_value is None:
        return False
    return abs(float(left_value) - float(right_value)) <= RATIO_COMPARE_EPS


def _build_missing_result_row(element_id, reason, stage):
    element_id_int = _eid_int(element_id)
    return ResultRow(
        stage=stage,
        element_id=_safe_text(element_id_int) or _safe_text(element_id) or "<unknown>",
        category_name="<Missing Element>",
        element_name="<Missing Element>",
        current_slope="",
        reason=_safe_text(reason),
    )


def _refresh_operation_state(op):
    op.current_ratio = _read_param_ratio(op.bundle.readback_param)
    op.current_text = _current_slope_text(op.bundle.readback_param)
    if op.location_curve is not None:
        try:
            op.line = op.location_curve.Curve
        except Exception:
            pass


def _operation_matches_target(op, target_ratio):
    if _ratio_matches(op.current_ratio, target_ratio):
        return True
    return _ratio_matches(_curve_ratio_from_line(op.line), target_ratio)


def _combine_apply_reasons(direct_result, geometry_result):
    direct_reason = _safe_text(getattr(direct_result, "reason", ""))
    geometry_reason = _safe_text(getattr(geometry_result, "reason", ""))

    if direct_reason and geometry_reason:
        return "Parameter write: {} | Geometry fallback: {}".format(
            direct_reason,
            geometry_reason,
        )
    return geometry_reason or direct_reason


def _collect_operations(doc, selected_ids):
    stats = RunStats(len(selected_ids or []))
    operations = []
    result_rows = []
    refreshed_ids = []

    for element_id in selected_ids or []:
        element = doc.GetElement(element_id)
        if element is None:
            stats.skipped_count += 1
            result_rows.append(
                _build_missing_result_row(
                    element_id,
                    "Element no longer exists in the document.",
                    "precheck",
                )
            )
            continue

        refreshed_ids.append(element_id)

        if isinstance(element, DB.ElementType):
            stats.skipped_count += 1
            result_rows.append(
                _build_result_row(
                    element,
                    "",
                    "Element types are not supported.",
                    "precheck",
                )
            )
            continue

        bundle = _resolve_slope_bundle(element)
        if not bundle.has_any:
            stats.skipped_count += 1
            result_rows.append(
                _build_result_row(
                    element,
                    "",
                    "No supported built-in slope parameter was found on the element.",
                    "precheck",
                )
            )
            continue

        location_curve, line, geometry_reason = _get_location_line(element)
        operations.append(
            OperationContext(
                element,
                bundle,
                location_curve=location_curve,
                line=line,
                geometry_precheck_reason=geometry_reason,
            )
        )

    stats.eligible_count = len(operations)
    return stats, operations, result_rows, refreshed_ids


def _queue_parameter_write(op, target_ratio, project_text):
    if _operation_matches_target(op, target_ratio):
        return ApplyResult(
            status="no_change",
            stage="parameter write",
            reason="Slope already matches the requested target.",
            updated_bundle=op.bundle,
        )

    if not op.bundle.display_params:
        return ApplyResult(
            status="skipped",
            stage="parameter write",
            reason="No visible built-in slope parameter was found for direct writing.",
            updated_bundle=op.bundle,
        )

    attempted_write = False
    reasons = []

    for param in op.bundle.display_params:
        param_label = _safe_text(getattr(getattr(param, "Definition", None), "Name", "")) or "Slope"

        try:
            if param.StorageType != DB.StorageType.Double:
                reasons.append("{} is not a writable numeric slope field.".format(param_label))
                continue
        except Exception:
            reasons.append("{} storage type could not be read.".format(param_label))
            continue

        if not _is_param_writable(param):
            reasons.append("{} is read-only.".format(param_label))
            continue

        attempted_write = True
        write_error = ""

        if project_text:
            try:
                did_write = param.SetValueString(project_text)
                if did_write is not False:
                    return ApplyResult(
                        status="pending",
                        stage="parameter write",
                        reason="Direct parameter write queued.",
                        updated_bundle=op.bundle,
                    )
            except Exception as ex:
                write_error = _safe_text(ex)

        try:
            did_write = param.Set(float(target_ratio))
            if did_write is not False:
                return ApplyResult(
                    status="pending",
                    stage="parameter write",
                    reason="Direct parameter write queued.",
                    updated_bundle=op.bundle,
                )
        except Exception as ex:
            fallback_error = _safe_text(ex)
            write_error = "{} | {}".format(write_error, fallback_error) if write_error else fallback_error

        reasons.append(write_error or "{} rejected the requested value.".format(param_label))

    default_reason = "Direct parameter write failed." if attempted_write else (
        "No writable built-in slope parameter was found for direct writing."
    )
    return ApplyResult(
        status="failed" if attempted_write else "skipped",
        stage="parameter write",
        reason="; ".join(reasons) or default_reason,
        updated_bundle=op.bundle,
    )


def _prepare_geometry_fallback(op, target_ratio):
    try:
        if getattr(op.element, "Pinned", False):
            return ApplyResult(
                status="skipped",
                stage="geometry fallback",
                reason="Pinned elements cannot use geometry fallback.",
                updated_bundle=op.bundle,
            )
    except Exception:
        pass

    if op.location_curve is None or op.line is None:
        return ApplyResult(
            status="skipped",
            stage="geometry fallback",
            reason=op.geometry_precheck_reason or "Geometry fallback is not available for this element.",
            updated_bundle=op.bundle,
        )

    if _ratio_matches(_curve_ratio_from_line(op.line), target_ratio):
        return ApplyResult(
            status="no_change",
            stage="geometry fallback",
            reason="Geometry already matches the requested target.",
            updated_bundle=op.bundle,
        )

    try:
        new_line = _build_center_fixed_line(op.line, target_ratio)
    except Exception as ex:
        return ApplyResult(
            status="skipped",
            stage="geometry fallback",
            reason=_safe_text(ex),
            updated_bundle=op.bundle,
        )

    op.restore_text = op.current_text
    op.original_line = op.line

    try:
        op.location_curve.Curve = new_line
    except Exception as ex:
        return ApplyResult(
            status="failed",
            stage="geometry fallback",
            reason="Center-fixed geometry fallback failed: {}".format(ex),
            updated_bundle=op.bundle,
        )

    return ApplyResult(
        status="pending",
        stage="geometry fallback",
        reason="Center-fixed geometry fallback queued.",
        updated_bundle=op.bundle,
    )


def _show_results(summary_text, result_rows):
    ordered_rows = sorted(
        list(result_rows or []),
        key=lambda row: (
            _parse_number_token(row.element_id) or 0.0,
            row.stage.lower(),
            row.reason.lower(),
        ),
    )
    if ordered_rows:
        SlopeResultsWindow("Results.xaml", summary_text, ordered_rows).ShowDialog()
        return

    forms.alert(summary_text, title=__title__, warn_icon=False)


def _pick_more_elements(uidoc, dialog_state):
    selected_ids = list(getattr(dialog_state, "selected_ids", []) or [])

    try:
        picked_refs = uidoc.Selection.PickObjects(ObjectType.Element, PICK_PROMPT)
    except Exception as ex:
        status_text = "Selection unchanged." if _is_cancelled_pick(ex) else "Select failed: {}".format(ex)
        return DialogState(
            selected_ids=selected_ids,
            slope_value=dialog_state.slope_value,
            unit_mode=dialog_state.unit_mode,
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
        slope_value=dialog_state.slope_value,
        unit_mode=dialog_state.unit_mode,
        status_text=status_text,
    )


def _execute_slope_run(doc, uidoc, dialog_state):
    selected_ids = list(getattr(dialog_state, "selected_ids", []) or [])
    raw_value = _normalize_text(getattr(dialog_state, "slope_value", ""))
    unit_mode = _safe_text(getattr(dialog_state, "unit_mode", "")) or DEGREE_UNIT_LABEL

    if not selected_ids:
        return ExecuteOutcome(
            True,
            DialogState(
                selected_ids=[],
                slope_value=raw_value,
                unit_mode=unit_mode,
                status_text="No elements selected. Click Select to add elements from the model.",
            ),
        )

    if not raw_value:
        return ExecuteOutcome(
            True,
            DialogState(
                selected_ids=selected_ids,
                slope_value=raw_value,
                unit_mode=unit_mode,
                status_text="Enter a slope value first.",
            ),
        )

    selected_elements = []
    for element_id in selected_ids:
        element = doc.GetElement(element_id)
        if element is not None:
            selected_elements.append(element)

    parse_spec_id, _ = _resolve_first_spec_id(doc, selected_elements)

    try:
        target_ratio = _parse_target_ratio(doc, parse_spec_id, unit_mode, raw_value)
    except Exception as ex:
        return ExecuteOutcome(
            True,
            DialogState(
                selected_ids=selected_ids,
                slope_value=raw_value,
                unit_mode=unit_mode,
                status_text="Invalid slope value: {}".format(ex),
            ),
        )

    stats, operations, result_rows, refreshed_ids = _collect_operations(doc, selected_ids)

    if not refreshed_ids:
        return ExecuteOutcome(
            True,
            DialogState(
                selected_ids=[],
                slope_value=raw_value,
                unit_mode=unit_mode,
                status_text="No elements selected. Click Select to add elements from the model.",
            ),
        )

    try:
        _set_ui_selection(uidoc, refreshed_ids)
    except Exception:
        pass

    if not operations:
        _show_results(stats.build_summary(unit_mode, raw_value), result_rows)
        return ExecuteOutcome(False)

    work_items = []
    for op in operations:
        if _operation_matches_target(op, target_ratio):
            stats.no_change_count += 1
            result_rows.append(
                _build_result_row(
                    op.element,
                    op.current_text,
                    "Slope already matches the requested target.",
                    "precheck",
                )
            )
            continue
        work_items.append(op)

    if not work_items:
        _show_results(stats.build_summary(unit_mode, raw_value), result_rows)
        return ExecuteOutcome(False)

    project_text = _format_ratio_for_project(doc, parse_spec_id, target_ratio)

    with revit.Transaction(__title__):
        direct_pending = []
        geometry_candidates = []

        for op in work_items:
            direct_result = _queue_parameter_write(op, target_ratio, project_text)

            if direct_result.status == "pending":
                direct_pending.append((op, direct_result))
                continue

            if direct_result.status == "no_change":
                stats.no_change_count += 1
                result_rows.append(
                    _build_result_row(
                        op.element,
                        op.current_text,
                        direct_result.reason,
                        direct_result.stage,
                    )
                )
                continue

            geometry_candidates.append((op, direct_result))

        direct_regen_error = ""
        if direct_pending:
            try:
                doc.Regenerate()
            except Exception as ex:
                direct_regen_error = _safe_text(ex)

        if direct_regen_error:
            for op, direct_result in direct_pending:
                del direct_result
                stats.failed_count += 1
                result_rows.append(
                    _build_result_row(
                        op.element,
                        op.current_text,
                        "Direct parameter batch regenerate failed: {}".format(direct_regen_error),
                        "parameter write",
                    )
                )
        else:
            for op, direct_result in direct_pending:
                _refresh_operation_state(op)
                if _operation_matches_target(op, target_ratio):
                    stats.direct_parameter_updates += 1
                else:
                    geometry_candidates.append(
                        (
                            op,
                            ApplyResult(
                                status="failed",
                                stage="parameter write",
                                reason=(
                                    "Direct parameter write completed but Revit did not "
                                    "report the requested slope."
                                ),
                                updated_bundle=op.bundle,
                            ),
                        )
                    )

        geometry_pending = []

        for op, direct_result in geometry_candidates:
            _refresh_operation_state(op)
            if _operation_matches_target(op, target_ratio):
                stats.direct_parameter_updates += 1
                continue

            geometry_result = _prepare_geometry_fallback(op, target_ratio)

            if geometry_result.status == "pending":
                geometry_pending.append((op, direct_result))
                continue

            if geometry_result.status == "no_change":
                stats.no_change_count += 1
                result_rows.append(
                    _build_result_row(
                        op.element,
                        op.current_text,
                        _combine_apply_reasons(direct_result, geometry_result),
                        geometry_result.stage,
                    )
                )
                continue

            if geometry_result.status == "skipped":
                stats.skipped_count += 1
            else:
                stats.failed_count += 1

            result_rows.append(
                _build_result_row(
                    op.element,
                    op.current_text,
                    _combine_apply_reasons(direct_result, geometry_result),
                    geometry_result.stage,
                )
            )

        geometry_regen_error = ""
        if geometry_pending:
            try:
                doc.Regenerate()
            except Exception as ex:
                geometry_regen_error = _safe_text(ex)

        if geometry_regen_error:
            for op, direct_result in geometry_pending:
                restore_error = ""
                if op.location_curve is not None and op.original_line is not None:
                    try:
                        op.location_curve.Curve = op.original_line
                    except Exception as restore_ex:
                        restore_error = " Restore also failed: {}".format(restore_ex)

                stats.failed_count += 1
                geometry_result = ApplyResult(
                    status="failed",
                    stage="geometry fallback",
                    reason=(
                        "Geometry batch regenerate failed: {}. Original geometry restored "
                        "if possible.{}"
                    ).format(geometry_regen_error, restore_error),
                    updated_bundle=op.bundle,
                )
                result_rows.append(
                    _build_result_row(
                        op.element,
                        op.restore_text,
                        _combine_apply_reasons(direct_result, geometry_result),
                        geometry_result.stage,
                    )
                )
        else:
            for op, direct_result in geometry_pending:
                _refresh_operation_state(op)
                if _operation_matches_target(op, target_ratio):
                    stats.geometry_fallback_updates += 1
                    continue

                restore_error = ""
                if op.location_curve is not None and op.original_line is not None:
                    try:
                        op.location_curve.Curve = op.original_line
                    except Exception as restore_ex:
                        restore_error = " Restore also failed: {}".format(restore_ex)

                failure_reason = "Geometry fallback did not produce the requested slope."
                if restore_error:
                    failure_reason = "{}{}".format(failure_reason, restore_error)
                else:
                    failure_reason = "{} Original geometry restored.".format(failure_reason)

                stats.failed_count += 1
                geometry_result = ApplyResult(
                    status="failed",
                    stage="geometry fallback",
                    reason=failure_reason,
                    updated_bundle=op.bundle,
                )
                result_rows.append(
                    _build_result_row(
                        op.element,
                        op.restore_text,
                        _combine_apply_reasons(direct_result, geometry_result),
                        geometry_result.stage,
                    )
                )

    _show_results(stats.build_summary(unit_mode, raw_value), result_rows)
    return ExecuteOutcome(False)


class SlopeWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, dialog_state):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.doc = revit.doc
        self.uidoc = revit.uidoc
        self.dialog_state = dialog_state or DialogState()
        self.is_ready = False
        self.next_action = None
        self.selected_ids = list(getattr(self.dialog_state, "selected_ids", []) or [])
        self.selected_elements = []
        self.active_spec_id = None
        self.project_unit_label = ""

        if not self.doc or not self.uidoc:
            forms.alert("No active project document.", title=__title__)
            return

        self.unit_cb.ItemsSource = list(DESIRED_SLOPE_UNIT_LABELS)
        initial_unit_mode = self.dialog_state.unit_mode
        if initial_unit_mode not in DESIRED_SLOPE_UNIT_LABELS:
            initial_unit_mode = DEGREE_UNIT_LABEL
        self.unit_cb.SelectedItem = initial_unit_mode
        self.slope_value_tb.Text = self.dialog_state.slope_value

        self._refresh_selection_state()
        self.status_tb.Text = _build_status_text(len(self.selected_elements), self.dialog_state.status_text)
        self.is_ready = True

    def _capture_state(self, status_text=""):
        return DialogState(
            selected_ids=self.selected_ids,
            slope_value=self.slope_value_tb.Text,
            unit_mode=_safe_text(self.unit_cb.SelectedItem) or DEGREE_UNIT_LABEL,
            status_text=status_text,
        )

    def _set_action_controls_enabled(self, is_enabled):
        for control in (self.select_btn, self.run_btn, self.cancel_btn):
            try:
                control.IsEnabled = bool(is_enabled)
            except Exception:
                continue

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
        self.active_spec_id, self.project_unit_label = _resolve_first_spec_id(self.doc, self.selected_elements)
        self.selection_count_tb.Text = "{} element(s) selected.".format(len(self.selected_elements))
        self._sync_unit_state()

    def _sync_unit_state(self):
        selected_mode = _safe_text(self.unit_cb.SelectedItem) or DEGREE_UNIT_LABEL
        helper_text = UNIT_HELPER_TEXTS.get(
            selected_mode,
            "Enter a slope value using the selected format.",
        )

        if self.project_unit_label:
            helper_text = "{} Project display: {}.".format(helper_text, self.project_unit_label)

        self.unit_symbol_tb.Text = u"\N{DEGREE SIGN}" if selected_mode == DEGREE_UNIT_LABEL else ""
        self.helper_tb.Text = helper_text
        self.slope_value_tb.ToolTip = helper_text

    def unit_changed(self, sender, args):
        del sender, args
        self._sync_unit_state()

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

        raw_value = _normalize_text(self.slope_value_tb.Text)
        if not raw_value:
            self.status_tb.Text = "Enter a slope value first."
            return

        if not self.selected_ids:
            self.status_tb.Text = "No elements selected. Click Select to add elements from the model."
            return

        self._set_action_controls_enabled(False)
        self.status_tb.Text = "Running slope update..."
        self.dialog_state = self._capture_state(self.status_tb.Text)
        self.next_action = "run"
        self.Close()


def main():
    if not revit.doc or not revit.uidoc:
        forms.alert("No active project document.", title=__title__)
        return

    dialog_state = DialogState(selected_ids=list(revit.uidoc.Selection.GetElementIds()))

    while True:
        window = SlopeWindow("Script.xaml", dialog_state)
        if not window.is_ready:
            return

        window.ShowDialog()
        dialog_state = window.dialog_state or dialog_state

        if window.next_action == "select":
            dialog_state = _pick_more_elements(revit.uidoc, dialog_state)
            continue

        if window.next_action == "run":
            outcome = _execute_slope_run(revit.doc, revit.uidoc, dialog_state)
            if outcome.reopen_dialog:
                dialog_state = outcome.dialog_state or dialog_state
                continue
            return

        return


if __name__ == "__main__":
    main()
