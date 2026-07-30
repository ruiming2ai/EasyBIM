# -*- coding: utf-8 -*-
"""Revit API helpers for batch view-template setting transfer."""

from pyrevit import DB
from pyrevit.compat import get_elementid_value_func
from System.Collections.Generic import List

from view_template_transfer_state import TemplateParameterRow
from view_template_transfer_state import ViewTemplateOption
from view_template_transfer_state import calculate_final_non_controlled_ids
from view_template_transfer_state import calculate_temporary_non_controlled_ids
from view_template_transfer_state import element_id_to_int
from view_template_transfer_state import order_parameter_ids
from view_template_transfer_state import safe_text


get_elementid_value = get_elementid_value_func()


EDIT_VALUE_NAMES = set(
    [
        "v/g overrides model",
        "v/g overrides annotation",
        "v/g overrides analytical model",
        "v/g overrides import",
        "v/g overrides filters",
        "v/g overrides rvt links",
        "model display",
        "shadows",
        "sketchy lines",
        "lighting",
        "photographic exposure",
        "view range",
        "system color schemes",
    ]
)


def _eid_int(element_id):
    if element_id is None:
        return None
    try:
        return int(get_elementid_value(element_id))
    except Exception:
        return element_id_to_int(element_id)


def _to_element_ids(id_ints):
    ids = List[DB.ElementId]()
    for id_int in id_ints or []:
        try:
            ids.Add(DB.ElementId(int(id_int)))
        except Exception:
            pass
    return ids


def _view_type_label(view):
    value = safe_text(getattr(view, "ViewType", "Other"))
    labels = {
        "FloorPlan": "Floor Plan",
        "CeilingPlan": "Ceiling Plan",
        "EngineeringPlan": "Engineering Plan",
        "AreaPlan": "Area Plan",
        "ThreeD": "3D View",
        "DraftingView": "Drafting View",
        "DrawingSheet": "Sheet",
        "Schedule": "Schedule",
        "PanelSchedule": "Panel Schedule",
        "Section": "Section",
        "Elevation": "Elevation",
        "Detail": "Detail",
        "Legend": "Legend",
    }
    return labels.get(value, value or "Other")


def _template_sort_key(option):
    return (
        safe_text(option.view_type_label).lower(),
        safe_text(option.name).lower(),
        int(option.element_id_int),
    )


def collect_view_templates(doc):
    templates = []
    try:
        views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()
    except Exception:
        views = []

    for view in views:
        try:
            if not bool(view.IsTemplate):
                continue
        except Exception:
            continue

        element_id_int = _eid_int(getattr(view, "Id", None))
        if element_id_int is None:
            continue

        templates.append(
            ViewTemplateOption(
                element_id_int,
                safe_text(getattr(view, "Name", "")),
                _view_type_label(view),
                element=view,
            )
        )

    return sorted(templates, key=_template_sort_key)


def count_views_assigned_to_template(doc, template):
    template_id_int = _eid_int(getattr(template, "Id", None))
    if template_id_int is None:
        return 0

    count = 0
    try:
        views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()
    except Exception:
        views = []

    for view in views:
        try:
            if bool(view.IsTemplate):
                continue
        except Exception:
            pass

        try:
            view_template_id_int = _eid_int(view.ViewTemplateId)
        except Exception:
            view_template_id_int = None
        if view_template_id_int == template_id_int:
            count += 1

    return count


def _template_parameter_ids(template):
    try:
        return [_eid_int(element_id) for element_id in template.GetTemplateParameterIds()]
    except Exception:
        return []


def _non_controlled_parameter_ids(template):
    try:
        return [_eid_int(element_id) for element_id in template.GetNonControlledTemplateParameterIds()]
    except Exception:
        return []


def _parameter_name(parameter):
    try:
        return safe_text(parameter.Definition.Name)
    except Exception:
        return ""


def _ordered_parameters(template):
    try:
        parameters = list(template.GetOrderedParameters())
    except Exception:
        try:
            parameters = list(template.Parameters)
        except Exception:
            parameters = []
    return parameters


def _parameter_value_text(parameter):
    text = ""
    try:
        text = safe_text(parameter.AsValueString())
    except Exception:
        text = ""
    if text:
        return text

    try:
        text = safe_text(parameter.AsString())
    except Exception:
        text = ""
    if text:
        return text

    try:
        storage_type = parameter.StorageType
    except Exception:
        storage_type = None

    try:
        if storage_type == DB.StorageType.Integer:
            return safe_text(parameter.AsInteger())
    except Exception:
        pass

    try:
        if storage_type == DB.StorageType.Double:
            return safe_text(parameter.AsDouble())
    except Exception:
        pass

    try:
        if storage_type == DB.StorageType.ElementId:
            return safe_text(_eid_int(parameter.AsElementId()))
    except Exception:
        pass

    name = _parameter_name(parameter).lower()
    if name in EDIT_VALUE_NAMES:
        return "Edit..."
    return ""


def build_template_parameter_rows(source_template):
    template_parameter_ids = [
        x for x in _template_parameter_ids(source_template) if x is not None
    ]
    non_controlled_ids = set(
        x for x in _non_controlled_parameter_ids(source_template) if x is not None
    )

    parameters = _ordered_parameters(source_template)
    parameters_by_id = {}
    names_by_id = {}
    ordered_ids = []
    for parameter in parameters:
        parameter_id_int = _eid_int(getattr(parameter, "Id", None))
        if parameter_id_int is None:
            continue
        ordered_ids.append(parameter_id_int)
        parameters_by_id[parameter_id_int] = parameter
        names_by_id[parameter_id_int] = _parameter_name(parameter)

    ordered_template_ids = order_parameter_ids(
        template_parameter_ids,
        ordered_ids,
        names_by_id=names_by_id,
    )

    rows = []
    for parameter_id_int in ordered_template_ids:
        parameter = parameters_by_id.get(parameter_id_int)
        name = names_by_id.get(parameter_id_int, "")
        value_text = ""
        if parameter is not None:
            value_text = _parameter_value_text(parameter)
        rows.append(
            TemplateParameterRow(
                parameter_id_int,
                name,
                value_text,
                source_is_included=parameter_id_int not in non_controlled_ids,
            )
        )
    return rows


class TransferSummary(object):
    def __init__(self, requested_target_count=0, selected_setting_count=0):
        self.requested_target_count = int(requested_target_count or 0)
        self.selected_setting_count = int(selected_setting_count or 0)
        self.transferred_count = 0
        self.skipped = []
        self.errors = []

    @property
    def skipped_count(self):
        return len(self.skipped) + len(self.errors)

    def add_skip(self, target_name, reason):
        self.skipped.append("{}: {}".format(safe_text(target_name), safe_text(reason)))

    def add_error(self, target_name, error):
        self.errors.append("{}: {}".format(safe_text(target_name), safe_text(error)))

    def message(self):
        lines = [
            "Transferred {} setting(s) to {} target view template(s).".format(
                self.selected_setting_count,
                self.transferred_count,
            )
        ]
        if self.skipped_count:
            lines.append("{} target template(s) skipped.".format(self.skipped_count))
            for detail in (self.errors + self.skipped)[:8]:
                lines.append("- {}".format(detail))
            if self.skipped_count > 8:
                lines.append("- ...")
        return "\n".join(lines)


def _template_name(template):
    return safe_text(getattr(template, "Name", "View Template"))


def transfer_template_settings(doc, source_template, target_templates, selected_parameter_ids):
    selected_parameter_ids = set(int(x) for x in selected_parameter_ids or [])
    target_templates = list(target_templates or [])
    summary = TransferSummary(len(target_templates), len(selected_parameter_ids))

    if not selected_parameter_ids:
        summary.add_skip("Transfer", "No settings selected.")
        return summary

    source_template_ids = set(
        x for x in _template_parameter_ids(source_template) if x is not None
    )
    source_non_controlled_ids = _non_controlled_parameter_ids(source_template)
    selected_parameter_ids = selected_parameter_ids.intersection(source_template_ids)
    if not selected_parameter_ids:
        summary.add_skip("Transfer", "Selected settings are not available on the source template.")
        return summary

    transaction_group = DB.TransactionGroup(doc, "Batch Transfer View Template Settings")
    transaction_group.Start()
    changed_any = False

    try:
        for target_template in target_templates:
            target_name = _template_name(target_template)
            source_id_int = _eid_int(getattr(source_template, "Id", None))
            target_id_int = _eid_int(getattr(target_template, "Id", None))
            if source_id_int is not None and source_id_int == target_id_int:
                summary.add_skip(target_name, "Source template cannot also be a target.")
                continue

            target_template_ids = _template_parameter_ids(target_template)
            compatible_selected_ids = sorted(
                selected_parameter_ids.intersection(set(target_template_ids))
            )
            if not compatible_selected_ids:
                summary.add_skip(
                    target_name,
                    "No selected settings are available on this target template.",
                )
                continue

            original_non_controlled_ids = _non_controlled_parameter_ids(target_template)
            temporary_non_controlled_ids = calculate_temporary_non_controlled_ids(
                target_template_ids,
                compatible_selected_ids,
            )
            final_non_controlled_ids = calculate_final_non_controlled_ids(
                target_template_ids,
                original_non_controlled_ids,
                compatible_selected_ids,
                source_non_controlled_ids,
            )

            transaction = DB.Transaction(doc, "Transfer settings to {}".format(target_name))
            try:
                transaction.Start()
                target_template.SetNonControlledTemplateParameterIds(
                    _to_element_ids(temporary_non_controlled_ids)
                )
                target_template.ApplyViewTemplateParameters(source_template)
                target_template.SetNonControlledTemplateParameterIds(
                    _to_element_ids(final_non_controlled_ids)
                )
                transaction.Commit()
                summary.transferred_count += 1
                changed_any = True
            except Exception as ex:
                try:
                    if transaction.HasStarted():
                        transaction.RollBack()
                except Exception:
                    pass
                summary.add_error(target_name, ex)

        if changed_any:
            transaction_group.Assimilate()
        else:
            transaction_group.RollBack()
    except Exception:
        try:
            transaction_group.RollBack()
        except Exception:
            pass
        raise

    return summary
