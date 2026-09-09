# -*- coding: utf-8 -*-
"""Read-only Revit snapshots for Customized Excel comparison.

Called from the existing Load Customized Excel external-event Execute, whose
modal dialog keeps these callbacks inside the same valid Revit API context.
"""

from pyrevit import DB, framework
from easybim import print_sets
from easybim.compat import eid_to_int, element_id_factory, exception_text
from easybim.excel_print_sets import normalize_key

import sheet_manager_revit as smrevit
import sheet_manager_comparison as comparison


_RULES = {
    "Equal": "CreateEqualsRule", "NotEqual": "CreateNotEqualsRule",
    "GreaterThan": "CreateGreaterRule", "GreaterThanOrEqual": "CreateGreaterOrEqualRule",
    "LessThan": "CreateLessRule", "LessThanOrEqual": "CreateLessOrEqualRule",
    "Contains": "CreateContainsRule", "NotContains": "CreateNotContainsRule",
    "BeginsWith": "CreateBeginsWithRule", "NotBeginsWith": "CreateNotBeginsWithRule",
    "EndsWith": "CreateEndsWithRule", "NotEndsWith": "CreateNotEndsWithRule",
    "HasValue": "CreateHasValueParameterRule", "HasNoValue": "CreateHasNoValueParameterRule",
}


def collect_sources(doc):
    """Stable kind/id keys, not ambiguous display names."""
    options = [("schedule", eid_to_int(s.Id), u"Sheet List: {0}".format(s.Name))
               for s in smrevit.collect_sheet_list_schedules(doc)]
    sets = DB.FilteredElementCollector(doc).OfClass(
        framework.get_type(DB.ViewSheetSet)).ToElements()
    options.extend(("printset", eid_to_int(s.Id), u"Print Set: {0}".format(s.Name))
                   for s in sorted(sets, key=lambda s: s.Name.lower()))
    return options


def _value_text(param):
    try:
        display = param.AsValueString()
        if display:
            return display
    except Exception:
        pass
    if param.StorageType == DB.StorageType.String:
        return param.AsString() or u"(blank)"
    if param.StorageType == DB.StorageType.Integer:
        return str(param.AsInteger())
    if param.StorageType == DB.StorageType.ElementId:
        return str(eid_to_int(param.AsElementId()))
    return u"(value unavailable)"


def evaluate_filter(doc, sheet, field, schedule_filter):
    """Return (verified exclusion, unsupported detail), using native rules.

    Do not emulate calculated, global, or double-valued schedule fields: their
    semantics/tolerances cannot be safely inferred from display text.
    """
    label = field.GetName()
    rule = native_filter = None
    try:
        param_id = eid_to_int(field.ParameterId)
        param = next((p for p in sheet.Parameters if eid_to_int(p.Id) == param_id), None)
        if param_id == -1 or param is None:
            return None, u"{0}: no directly readable sheet parameter".format(label)
        kind = str(schedule_filter.FilterType)
        factory_name = _RULES.get(kind)
        if factory_name is None:
            return None, u"{0}: unsupported filter {1}".format(label, kind)
        factory = getattr(DB.ParameterFilterRuleFactory, factory_name)
        if kind in ("HasValue", "HasNoValue"):
            expected = u""
            rule = factory(param.Id)
        else:
            if schedule_filter.IsStringValue and param.StorageType == DB.StorageType.String:
                expected = schedule_filter.GetStringValue()
                try:
                    rule = factory(param.Id, expected)
                except TypeError:
                    # Revit 2023 string overloads accept caseSensitive.
                    rule = factory(param.Id, expected, False)
            elif schedule_filter.IsIntegerValue and param.StorageType == DB.StorageType.Integer:
                expected = schedule_filter.GetIntegerValue()
                rule = factory(param.Id, expected)
            elif schedule_filter.IsElementIdValue and param.StorageType == DB.StorageType.ElementId:
                expected = schedule_filter.GetElementIdValue()
                rule = factory(param.Id, expected)
                referenced = doc.GetElement(expected)
                expected = getattr(referenced, "Name", None) or str(eid_to_int(expected))
            else:
                return None, u"{0}: field value type or numeric tolerance could not be verified".format(label)
        native_filter = DB.ElementParameterFilter(rule)
        if native_filter.PassesFilter(doc, sheet.Id):
            return None, None
        return u"{0} {1} {2}; actual: {3}".format(
            label, kind, expected, _value_text(param)), None
    except Exception as error:
        return None, u"{0}: {1}".format(label, exception_text(error))
    finally:
        if native_filter is not None:
            native_filter.Dispose()
        if rule is not None:
            rule.Dispose()


def sheet_snapshot(sheet, definition=None):
    appears = None
    try:
        param = sheet.get_Parameter(DB.BuiltInParameter.SHEET_SCHEDULED)
        if param is not None:
            appears = bool(param.AsInteger())
    except Exception:
        pass
    snapshot = dict(id=eid_to_int(sheet.Id), number=sheet.SheetNumber, name=sheet.Name,
                    appears=appears, printable=bool(sheet.CanBePrinted),
                    exclusions=[], unknown_filters=[])
    if definition is not None:
        try:
            for filt in definition.GetFilters():
                field = definition.GetField(filt.FieldId)
                detail, unknown = evaluate_filter(getattr(sheet, "Document", None), sheet, field, filt)
                if detail:
                    snapshot["exclusions"].append(detail)
                if unknown:
                    snapshot["unknown_filters"].append(unknown)
        except Exception as error:
            snapshot["unknown_filters"].append(u"Schedule filters: {0}".format(exception_text(error)))
    return snapshot


def schedule_order(schedule, sheets):
    """Keep collector membership; trust order only with a complete number column."""
    if not sheets:
        return sheets, True
    try:
        definition = schedule.Definition
        if not definition.IsItemized:
            return sheets, False
        fields = [definition.GetField(fid) for fid in definition.GetFieldOrder()]
        visible = [f for f in fields if not f.IsHidden]
        number_column = next(i for i, field in enumerate(visible)
                             if eid_to_int(field.ParameterId) == int(DB.BuiltInParameter.SHEET_NUMBER))
        by_number = {}
        for sheet in sheets:
            if sheet.SheetNumber in by_number:
                return sheets, False
            by_number[sheet.SheetNumber] = sheet
        data = print_sets.get_schedule_text_data(
            schedule, smrevit.script, logger=smrevit.LOGGER, host_app=smrevit.HOST_APP)
        ordered = []
        for line in data:
            cells = line.split("\t")
            if len(cells) > number_column and cells[number_column] in by_number:
                ordered.append(by_number[cells[number_column]])
        if len(ordered) == len(sheets) and len(set(eid_to_int(s.Id) for s in ordered)) == len(sheets):
            return ordered, True
    except Exception:
        pass
    return sheets, False


def read_comparison(doc, excel_rows, source_key):
    """Fresh read of the chosen source and the full model; no transactions."""
    kind, source_id = source_key
    source = doc.GetElement(element_id_factory(DB.ElementId)(source_id))
    if source is None:
        raise ValueError("The selected Revit source no longer exists. Reopen the Excel dialog.")
    warnings = []
    if kind == "schedule":
        members = list(DB.FilteredElementCollector(doc, source.Id).OfClass(
            framework.get_type(DB.ViewSheet)).WhereElementIsNotElementType().ToElements())
        sheets, order_known = schedule_order(source, members)
        definition = source.Definition
        if getattr(definition, "IncludeLinkedFiles", False):
            warnings.append("This schedule includes linked files. Comparison covers host-model sheets only.")
    elif kind == "printset":
        views = list(source.Views)
        sheets = [v for v in views if isinstance(v, DB.ViewSheet)]
        definition = None
        order_known = False
        try:
            ordered = [v for v in source.OrderedViewList if isinstance(v, DB.ViewSheet)]
            if (not source.IsAutomatic and len(ordered) == len(sheets)
                    and set(eid_to_int(s.Id) for s in ordered) == set(eid_to_int(s.Id) for s in sheets)):
                sheets, order_known = ordered, True
        except Exception:
            pass
        if len(views) != len(sheets):
            warnings.append(u"{0} non-sheet view(s) in the print set were excluded from comparison.".format(len(views) - len(sheets)))
    else:
        raise ValueError("Choose a Sheet List or Print Set.")
    members = set(eid_to_int(s.Id) for s in sheets)
    excel_keys = set(normalize_key(row.sheet_number) for row in excel_rows)
    model = []
    for sheet in smrevit.collect_sheets(doc):
        diagnose = definition if (eid_to_int(sheet.Id) not in members
                                  and normalize_key(sheet.SheetNumber) in excel_keys) else None
        model.append(sheet_snapshot(sheet, diagnose))
    result = comparison.compare_lists(excel_rows, [sheet_snapshot(s) for s in sheets],
                                      model, kind, source_order_known=order_known)
    result.warnings.extend(warnings)
    return result
