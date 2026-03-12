# -*- coding: utf-8 -*-
"""Update the built-in Slope parameter on selected elements."""

# pylint: disable=import-error,invalid-name,broad-except,too-many-instance-attributes
import math

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script
from pyrevit.compat import get_elementid_value_func
from pyrevit.framework import System


__title__ = "Slope"

logger = script.get_logger()
get_elementid_value = get_elementid_value_func()

UNIT_MODE_DEGREES = "Degrees"
UNIT_MODE_PROJECT = "Project Default"
EPS = 1e-9
NEAR_VERTICAL_COS_EPS = 1e-6


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _eid_int(element_id):
    if not element_id:
        return None
    try:
        return int(get_elementid_value(element_id))
    except Exception:
        try:
            return int(element_id.IntegerValue)
        except Exception:
            return None


def _normalize_text(value):
    return _safe_text(value).strip()


def _parse_number_token(token):
    token = _normalize_text(token)
    if not token:
        return None

    number_styles = (
        System.Globalization.NumberStyles.Float
        | System.Globalization.NumberStyles.AllowThousands
    )
    cultures = (
        System.Globalization.CultureInfo.CurrentCulture,
        System.Globalization.CultureInfo.InvariantCulture,
    )

    for culture in cultures:
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


def _find_built_in_slope_param(element):
    if not element:
        return None

    fallback = None
    try:
        for param in element.Parameters:
            if not param or not getattr(param, "Definition", None):
                continue

            if _safe_text(param.Definition.Name) != "Slope":
                continue

            pid_int = _eid_int(getattr(param, "Id", None))
            if pid_int is None or pid_int >= 0:
                continue

            if fallback is None:
                fallback = param

            try:
                if not param.IsReadOnly:
                    return param
            except Exception:
                return param
    except Exception:
        return fallback

    return fallback


def _validate_target_param(param):
    if not param:
        return False, "Built-in Slope parameter not found."

    try:
        if param.StorageType != DB.StorageType.Double:
            return False, "Built-in Slope parameter is not a writable numeric value."
    except Exception:
        return False, "Built-in Slope parameter storage type could not be read."

    try:
        if param.IsReadOnly:
            return False, "Built-in Slope parameter is read-only."
    except Exception:
        pass

    if not _param_is_user_modifiable(param):
        return False, "Built-in Slope parameter is not user-modifiable."

    return True, ""


def _build_result_row(element, current_slope, reason):
    return ResultRow(
        element_id=_safe_text(_eid_int(getattr(element, "Id", None))) or "<unknown>",
        category_name=_element_category_name(element),
        element_name=_element_name(element),
        current_slope=_safe_text(current_slope),
        reason=_safe_text(reason),
    )


def _collect_selected_elements():
    doc = revit.doc
    uidoc = revit.uidoc
    if not doc or not uidoc:
        return None, None, "No active project document."

    selected_ids = list(uidoc.Selection.GetElementIds())
    if not selected_ids:
        return doc, [], "Select one or more elements first."

    elements = []
    for element_id in selected_ids:
        element = doc.GetElement(element_id)
        if element:
            elements.append(element)

    if not elements:
        return doc, [], "No elements were found in the current selection."

    return doc, elements, ""


def _parse_degree_target(value_text):
    raw_value = _normalize_text(value_text)
    if not raw_value:
        raise ValueError("Enter a degree value.")

    raw_value = raw_value.replace(u"\N{DEGREE SIGN}", "").strip()
    value = _parse_number_token(raw_value)
    if value is None:
        raise ValueError("Could not parse the degree value.")

    radians = math.radians(value)
    if abs(math.cos(radians)) < NEAR_VERTICAL_COS_EPS:
        raise ValueError("Near-vertical angles are not supported.")

    return value, math.tan(radians)


class ResultRow(object):
    def __init__(self, element_id, category_name, element_name, current_slope, reason):
        self.element_id = _safe_text(element_id)
        self.category_name = _safe_text(category_name)
        self.element_name = _safe_text(element_name)
        self.current_slope = _safe_text(current_slope)
        self.reason = _safe_text(reason)


class OperationContext(object):
    def __init__(self, element, param, current_text, current_internal):
        self.element = element
        self.param = param
        self.current_text = _safe_text(current_text)
        self.current_internal = float(current_internal)


class RunStats(object):
    def __init__(self, selected_count):
        self.selected_count = int(selected_count)
        self.eligible_count = 0
        self.updated_count = 0
        self.no_change_count = 0
        self.skipped_types = 0
        self.skipped_pinned = 0
        self.skipped_missing_slope = 0
        self.skipped_not_writable = 0
        self.error_count = 0

    def build_summary(self, unit_mode, input_value):
        lines = [
            "Slope completed.",
            "Unit: {}".format(_safe_text(unit_mode)),
            "Input: {}".format(_safe_text(input_value)),
            "Selected elements: {}".format(self.selected_count),
            "Eligible targets: {}".format(self.eligible_count),
            "Updated: {}".format(self.updated_count),
            "No change: {}".format(self.no_change_count),
            "Skipped element types: {}".format(self.skipped_types),
            "Skipped pinned: {}".format(self.skipped_pinned),
            "Skipped missing built-in Slope: {}".format(self.skipped_missing_slope),
            "Skipped not writable: {}".format(self.skipped_not_writable),
            "Apply errors: {}".format(self.error_count),
        ]
        return "\n".join(lines)


class SlopeResultsWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, summary_text, result_rows):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.summary_tb.Text = _safe_text(summary_text)
        self.result_lv.ItemsSource = list(result_rows or [])

    def close_click(self, sender, args):
        del sender, args
        self.Close()


class SlopeWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.doc, self.selected_elements, self.init_error = _collect_selected_elements()
        self.is_ready = False

        if self.init_error:
            forms.alert(self.init_error, title=__title__)
            return

        self.unit_cb.ItemsSource = [UNIT_MODE_DEGREES, UNIT_MODE_PROJECT]
        self.unit_cb.SelectedIndex = 0
        self.selection_count_tb.Text = "{} element(s) selected.".format(len(self.selected_elements))
        self.status_tb.Text = "Ready. Run will skip unsupported or read-only elements and report them."
        self._sync_unit_state()
        self.is_ready = True

    def _sync_unit_state(self):
        selected_mode = _safe_text(self.unit_cb.SelectedItem) or UNIT_MODE_DEGREES
        if selected_mode == UNIT_MODE_PROJECT:
            self.unit_symbol_tb.Text = ""
            self.helper_tb.Text = (
                "Enter the slope exactly as Revit accepts it in the current project units. "
                "Example formats depend on the project slope display settings."
            )
            self.slope_value_tb.ToolTip = "Enter the same slope text Revit accepts in Properties."
        else:
            self.unit_symbol_tb.Text = u"\N{DEGREE SIGN}"
            self.helper_tb.Text = (
                "Enter a degree value. The command converts degrees to the built-in slope ratio "
                "before writing the parameter."
            )
            self.slope_value_tb.ToolTip = "Enter a numeric angle in degrees."

    def unit_changed(self, sender, args):
        del sender, args
        self._sync_unit_state()

    def cancel_click(self, sender, args):
        del sender, args
        self.Close()

    def _collect_operations(self):
        result_rows = []
        operations = []
        stats = RunStats(len(self.selected_elements))

        for element in self.selected_elements:
            if isinstance(element, DB.ElementType):
                stats.skipped_types += 1
                result_rows.append(
                    _build_result_row(element, "", "Element type selection is not supported.")
                )
                continue

            try:
                if getattr(element, "Pinned", False):
                    stats.skipped_pinned += 1
                    result_rows.append(
                        _build_result_row(element, "", "Pinned elements are skipped.")
                    )
                    continue
            except Exception:
                pass

            param = _find_built_in_slope_param(element)
            if not param:
                stats.skipped_missing_slope += 1
                result_rows.append(
                    _build_result_row(
                        element,
                        "",
                        "Built-in Slope parameter was not found on the element.",
                    )
                )
                continue

            current_text = _current_slope_text(param)
            is_valid, reason = _validate_target_param(param)
            if not is_valid:
                stats.skipped_not_writable += 1
                result_rows.append(_build_result_row(element, current_text, reason))
                continue

            try:
                current_internal = float(param.AsDouble())
            except Exception:
                stats.skipped_not_writable += 1
                result_rows.append(
                    _build_result_row(
                        element,
                        current_text,
                        "Current built-in Slope value could not be read.",
                    )
                )
                continue

            operations.append(
                OperationContext(
                    element=element,
                    param=param,
                    current_text=current_text,
                    current_internal=current_internal,
                )
            )

        stats.eligible_count = len(operations)
        return stats, operations, result_rows

    def _show_results(self, summary_text, result_rows):
        ordered_rows = sorted(
            list(result_rows or []),
            key=lambda row: (_parse_number_token(row.element_id) or 0.0, row.reason.lower()),
        )
        if ordered_rows:
            results_window = SlopeResultsWindow("Results.xaml", summary_text, ordered_rows)
            results_window.ShowDialog()
            return

        forms.alert(summary_text, title=__title__, warn_icon=False)

    def run_click(self, sender, args):
        del sender, args

        raw_value = _normalize_text(self.slope_value_tb.Text)
        if not raw_value:
            forms.alert("Enter a slope value first.", title=__title__)
            return

        unit_mode = _safe_text(self.unit_cb.SelectedItem) or UNIT_MODE_DEGREES
        if unit_mode == UNIT_MODE_DEGREES:
            try:
                _, target_internal = _parse_degree_target(raw_value)
            except Exception as ex:
                forms.alert("Invalid degree value: {}".format(ex), title=__title__)
                return
        else:
            target_internal = None

        stats, operations, result_rows = self._collect_operations()
        if not operations:
            summary_text = stats.build_summary(unit_mode, raw_value)
            self.status_tb.Text = "No eligible built-in Slope targets were found."
            self._show_results(summary_text, result_rows)
            self.Close()
            return

        with revit.Transaction(__title__):
            for op in operations:
                element = op.element
                param = op.param
                try:
                    if unit_mode == UNIT_MODE_DEGREES:
                        if abs(op.current_internal - target_internal) < EPS:
                            stats.no_change_count += 1
                            result_rows.append(
                                _build_result_row(
                                    element,
                                    op.current_text,
                                    "Slope already matches the requested degree target.",
                                )
                            )
                            continue

                        try:
                            did_set = param.Set(float(target_internal))
                        except Exception:
                            did_set = None
                            raise

                        if did_set is False:
                            raise Exception("Revit rejected the built-in Slope update.")
                    else:
                        if _normalize_text(op.current_text) == raw_value:
                            stats.no_change_count += 1
                            result_rows.append(
                                _build_result_row(
                                    element,
                                    op.current_text,
                                    "Slope already matches the requested project-default text.",
                                )
                            )
                            continue

                        try:
                            did_set = param.SetValueString(raw_value)
                        except Exception:
                            did_set = None
                            raise

                        if did_set is False:
                            raise Exception(
                                "Revit rejected the project-default slope text '{}'.".format(raw_value)
                            )

                    new_internal = float(param.AsDouble())
                    if abs(new_internal - op.current_internal) < EPS:
                        stats.no_change_count += 1
                        result_rows.append(
                            _build_result_row(
                                element,
                                op.current_text,
                                "Slope write completed with no effective change.",
                            )
                        )
                        continue

                    stats.updated_count += 1
                except Exception as ex:
                    stats.error_count += 1
                    logger.debug(
                        "Slope apply failed | id=%s | %s",
                        _eid_int(getattr(element, "Id", None)),
                        ex,
                    )
                    result_rows.append(_build_result_row(element, op.current_text, ex))

        summary_text = stats.build_summary(unit_mode, raw_value)
        self.status_tb.Text = "Done. Updated {} element(s).".format(stats.updated_count)
        self._show_results(summary_text, result_rows)
        self.Close()


def main():
    if not revit.doc or not revit.uidoc:
        forms.alert("No active project document.", title=__title__)
        return

    window = SlopeWindow("Script.xaml")
    if not window.is_ready:
        return
    window.ShowDialog()


if __name__ == "__main__":
    main()
