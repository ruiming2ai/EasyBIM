# -*- coding: utf-8 -*-
"""Revit API helpers for the Import Schedules from Excel command."""

# pylint: disable=import-error,invalid-name,broad-except
import os
import sys

from pyrevit import DB
from pyrevit import script

from easybim.compat import eid_to_int
from easybim.compat import exception_text
from easybim.compat import int_to_eid
from easybim.compat import safe_text

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

import import_excel_state as state

try:
    from pyrevit.revit import is_yesno_parameter
except Exception:
    def is_yesno_parameter(definition):
        try:
            return definition.GetDataType() == DB.SpecTypeId.Boolean.YesNo
        except Exception:
            return False


LOGGER = script.get_logger()

DOUBLE_TOLERANCE = 1e-9

# Parameters that swap an element's family/type. Resolving these by name is
# inherently ambiguous (countless families ship a type called "Standard"), and
# Revit raises cannot-be-ignored errors for some categories - a single one
# aborts the whole import transaction. Type changes belong in Revit.
TYPE_CHANGING_BIP_NAMES = (
    "ELEM_FAMILY_AND_TYPE_PARAM",
    "ELEM_TYPE_PARAM",
    "ELEM_FAMILY_PARAM",
)

_TYPE_CHANGING_BIPS = None


def _type_changing_bips():
    global _TYPE_CHANGING_BIPS
    if _TYPE_CHANGING_BIPS is None:
        found = []
        for name in TYPE_CHANGING_BIP_NAMES:
            bip = getattr(DB.BuiltInParameter, name, None)
            if bip is not None:
                found.append(bip)
        _TYPE_CHANGING_BIPS = found
    return _TYPE_CHANGING_BIPS


def _is_type_changing_param(param):
    """True for the Family / Type / Family and Type parameters."""
    bips = _type_changing_bips()
    try:
        definition_bip = getattr(param.Definition, "BuiltInParameter", None)
        if definition_bip is not None:
            for bip in bips:
                if definition_bip == bip:
                    return True
    except Exception:
        pass
    for bip in bips:
        try:
            if param.Id == DB.ElementId(bip):
                return True
        except Exception:
            continue
    return False


class InstanceWrite(object):
    def __init__(self, element, param, spec, new_value, excel_row,
                 is_clear=False):
        self.element = element
        self.param = param
        self.spec = spec
        self.new_value = new_value
        self.excel_row = excel_row
        self.is_clear = bool(is_clear)


class TypeWrite(object):
    def __init__(self, type_element, type_name, param, spec, new_value,
                 source_rows, is_clear=False):
        self.type_element = type_element
        self.type_name = safe_text(type_name)
        self.param = param
        self.spec = spec
        self.new_value = new_value
        self.source_rows = list(source_rows or [])
        self.is_clear = bool(is_clear)


class ImportPlan(object):
    """Everything the validation pass decided, before any transaction."""

    def __init__(self, total_rows=0, bad_id_rows=None, ignored_headers=None,
                 out_of_scope_rows=None, scope_name=""):
        self.total_rows = int(total_rows)
        self.matched_rows = 0
        self.conflicts = []
        self.instance_writes = []
        self.type_writes = []
        self.missing_rows = []       # (excel_row, element_id_value)
        self.bad_id_rows = list(bad_id_rows or [])
        self.read_only = []          # (excel_row, param_name)
        self.invalid_values = []     # (excel_row, param_name, text, reason)
        self.unresolved_columns = []
        self.unchanged_count = 0
        self.sentinel_count = 0
        self.blank_count = 0
        self.ignored_headers = list(ignored_headers or [])
        self.out_of_scope_rows = list(out_of_scope_rows or [])
        self.scope_name = safe_text(scope_name)
        self.blocked_columns = []

    def type_write_row_count(self):
        return sum(len(write.source_rows) for write in self.type_writes)

    def instance_clear_count(self):
        return len([w for w in self.instance_writes if w.is_clear])

    def type_clear_count(self):
        return len([w for w in self.type_writes if w.is_clear])

    def skipped_lines(self):
        lines = []
        for param_name in self.blocked_columns:
            lines.append(
                "Column '{}' skipped - family/type changes must be made in "
                "Revit, not imported".format(param_name)
            )
        for excel_row in self.out_of_scope_rows:
            lines.append("Row {}: element not in schedule '{}'".format(
                excel_row, self.scope_name
            ))
        for excel_row, id_value in self.missing_rows:
            lines.append("Row {}: element {} not found in model".format(
                excel_row, id_value
            ))
        for excel_row in self.bad_id_rows:
            lines.append("Row {}: invalid ElementId".format(excel_row))
        for excel_row, param_name in self.read_only:
            lines.append("Row {}: '{}' is read-only".format(
                excel_row, param_name
            ))
        for excel_row, param_name, text, reason in self.invalid_values:
            lines.append("Row {}: '{}' = '{}' ({})".format(
                excel_row, param_name, text, reason
            ))
        for param_name in self.unresolved_columns:
            lines.append(
                "Column '{}' matched no parameter on any element or "
                "type".format(param_name)
            )
        if self.unchanged_count:
            lines.append("{} cell(s) already match the model".format(
                self.unchanged_count
            ))
        if self.sentinel_count:
            lines.append("{} placeholder cell(s) ignored".format(
                self.sentinel_count
            ))
        if self.blank_count:
            lines.append(
                "{} blank cell(s) ignored - only text values can be cleared "
                "from Excel".format(self.blank_count)
            )
        for header in self.ignored_headers:
            lines.append("Column '{}' ignored (internal)".format(header))
        return lines


class ImportResult(object):
    def __init__(self, written_instance, written_type, failed_lines, plan,
                 cleared_count=0):
        self.written_instance = int(written_instance)
        self.written_type = int(written_type)
        self.failed_lines = list(failed_lines or [])
        self.plan = plan
        self.cleared_count = int(cleared_count)


def collect_scope_element_ids(doc, schedule):
    """Id values of every element visible in the schedule."""
    member_ids = set()
    try:
        for element in DB.FilteredElementCollector(doc, schedule.Id):
            id_value = eid_to_int(element.Id)
            if id_value is not None:
                member_ids.add(id_value)
    except Exception:
        LOGGER.exception("Could not collect schedule members.")
    return member_ids


def _get_element(doc, id_value):
    element_id = int_to_eid(id_value, DB.ElementId)
    if element_id is None:
        return None
    try:
        return doc.GetElement(element_id)
    except Exception:
        return None


def _get_type_info(doc, element, type_element_by_key):
    try:
        type_id = element.GetTypeId()
        if type_id is None or type_id == DB.ElementId.InvalidElementId:
            return None, None
        type_key = eid_to_int(type_id)
        if type_key is None:
            return None, None
        if type_key not in type_element_by_key:
            type_element_by_key[type_key] = doc.GetElement(type_id)
        return type_element_by_key[type_key], type_key
    except Exception:
        return None, None


def _type_display_name(type_element):
    name = safe_text(getattr(type_element, "Name", ""))
    try:
        family_name = safe_text(type_element.FamilyName)
        if family_name:
            return "{} : {}".format(family_name, name)
    except Exception:
        pass
    return name or "?"


def _lookup(owner, param_name):
    try:
        return owner.LookupParameter(param_name)
    except Exception:
        return None


def _probe_is_type(spec, records, elements_by_row, type_key_by_row,
                   type_element_by_key):
    """True/False from the first element that resolves the column; else None."""
    for record in records:
        element = elements_by_row.get(record.excel_row)
        if element is None:
            continue
        if _lookup(element, spec.param_name) is not None:
            return False
        type_element = type_element_by_key.get(
            type_key_by_row.get(record.excel_row)
        )
        if type_element is not None:
            if _lookup(type_element, spec.param_name) is not None:
                return True
    return None


def _is_workset_param(param):
    try:
        if param.Definition.BuiltInParameter == \
                DB.BuiltInParameter.ELEM_PARTITION_PARAM:
            return True
    except Exception:
        pass
    try:
        return eid_to_int(param.Id) == \
            int(DB.BuiltInParameter.ELEM_PARTITION_PARAM)
    except Exception:
        return False


def _find_workset_id(doc, name):
    try:
        collector = DB.FilteredWorksetCollector(doc).OfKind(
            DB.WorksetKind.UserWorkset
        )
        for workset in collector:
            if safe_text(workset.Name) == name:
                return eid_to_int(workset.Id)
    except Exception:
        pass
    return None


def _resolve_unit_type_id(doc, param, spec):
    meta = spec.meta
    if meta is not None and meta.unit_type_id:
        try:
            return DB.ForgeTypeId(meta.unit_type_id)
        except Exception:
            pass
    try:
        forge_type_id = param.Definition.GetDataType()
        if forge_type_id and DB.UnitUtils.IsMeasurableSpec(forge_type_id):
            return doc.GetUnits().GetFormatOptions(
                forge_type_id
            ).GetUnitTypeId()
    except Exception:
        pass
    return None


def _candidate_names(candidate):
    """Names a candidate can be referred to by, including "Family: Type"."""
    names = []
    try:
        name = safe_text(candidate.Name).strip()
    except Exception:
        return names
    if not name:
        return names
    names.append(name)
    try:
        family_name = safe_text(candidate.FamilyName).strip()
        if family_name:
            names.append("{}: {}".format(family_name, name))
    except Exception:
        pass
    return names


def elementid_text_unchanged(param, text):
    """True when an ElementId cell still holds its exported display text.

    The export writes AsValueString() for these columns, so comparing display
    text is the only way to recognise an untouched cell BEFORE resolving the
    text back to an element. Without this, a cell the user never edited can
    resolve to a different same-named element and be written as a "change".
    """
    try:
        current = safe_text(param.AsValueString()).strip()
    except Exception:
        return False
    return bool(current) and current == safe_text(text).strip()


def _find_element_id_by_name(doc, param, text):
    try:
        current_id = param.AsElementId()
        if current_id is None or current_id == DB.ElementId.InvalidElementId:
            return None, "cannot infer category (no current value)"
        current_element = doc.GetElement(current_id)
        if current_element is None or current_element.Category is None:
            return None, "cannot infer category"
        category_id = current_element.Category.Id
        collector = DB.FilteredElementCollector(doc).OfCategoryId(category_id)
        # Match like with like: a type-valued parameter must never resolve to
        # an instance, which reports its type's name from .Name.
        try:
            if isinstance(current_element, DB.ElementType):
                collector = collector.WhereElementIsElementType()
            else:
                collector = collector.WhereElementIsNotElementType()
        except Exception:
            pass

        matches = []
        matched_ids = set()
        for candidate in collector:
            try:
                if text not in _candidate_names(candidate):
                    continue
                id_value = eid_to_int(candidate.Id)
                if id_value in matched_ids:
                    continue
                matched_ids.add(id_value)
                matches.append(candidate.Id)
            except Exception:
                continue

        if not matches:
            return None, "no element named '{}' in that category".format(text)
        if len(matches) > 1:
            return None, (
                "'{}' is ambiguous - {} elements share that name; "
                "set this parameter in Revit"
            ).format(text, len(matches))
        return matches[0], None
    except Exception as find_error:
        return None, exception_text(find_error)


def _convert_value(doc, param, spec, text):
    """Return (value ready for param.Set, error_text)."""
    try:
        storage = param.StorageType
        if storage == DB.StorageType.String:
            return text, None
        if storage == DB.StorageType.Integer:
            if _is_workset_param(param):
                workset_id = _find_workset_id(doc, text)
                if workset_id is None:
                    return None, "workset '{}' not found".format(text)
                return workset_id, None
            if is_yesno_parameter(param.Definition):
                yesno = state.parse_yesno(text)
                if yesno is None:
                    return None, "not a Yes/No value"
                return 1 if yesno else 0, None
            number = state.parse_number(text)
            if number is None:
                return None, "not a number"
            return int(number), None
        if storage == DB.StorageType.Double:
            number = state.parse_number(text)
            if number is None:
                return None, "not a number"
            unit_type_id = _resolve_unit_type_id(doc, param, spec)
            if unit_type_id is not None:
                return DB.UnitUtils.ConvertToInternalUnits(
                    number, unit_type_id
                ), None
            return number, None
        if storage == DB.StorageType.ElementId:
            element_id, error = _find_element_id_by_name(doc, param, text)
            if element_id is None:
                return None, error or "no matching element"
            return element_id, None
        return None, "unsupported storage type"
    except Exception as convert_error:
        return None, exception_text(convert_error)


def _values_equal(param, new_value):
    try:
        storage = param.StorageType
        if storage == DB.StorageType.String:
            return safe_text(param.AsString()) == safe_text(new_value)
        if not param.HasValue:
            return False
        if storage == DB.StorageType.Integer:
            return int(param.AsInteger()) == int(new_value)
        if storage == DB.StorageType.Double:
            return abs(
                float(param.AsDouble()) - float(new_value)
            ) <= DOUBLE_TOLERANCE
        if storage == DB.StorageType.ElementId:
            return eid_to_int(param.AsElementId()) == eid_to_int(new_value)
    except Exception:
        return False
    return False


def _queue_write(doc, plan, owner, spec, text, excel_row, on_valid):
    param = _lookup(owner, spec.param_name)
    if param is None:
        plan.invalid_values.append(
            (excel_row, spec.param_name, text, "parameter not found")
        )
        return
    if _is_type_changing_param(param):
        if spec.param_name not in plan.blocked_columns:
            plan.blocked_columns.append(spec.param_name)
        return
    if param.IsReadOnly:
        plan.read_only.append((excel_row, spec.param_name))
        return

    # A blank cell means "clear this value". Only text parameters have a
    # meaningful empty state - a number or Yes/No has no "no value", and
    # writing 0/No would be a change the user never asked for. Blanks in
    # those columns keep the old "leave unchanged" behaviour, counted in
    # aggregate so a mostly-empty column cannot flood the report.
    is_clear = not text
    if is_clear and param.StorageType != DB.StorageType.String:
        plan.blank_count += 1
        return

    if param.StorageType == DB.StorageType.ElementId \
            and elementid_text_unchanged(param, text):
        plan.unchanged_count += 1
        return
    converted, error = _convert_value(doc, param, spec, text)
    if error is not None:
        plan.invalid_values.append((excel_row, spec.param_name, text, error))
        return
    if _values_equal(param, converted):
        plan.unchanged_count += 1
        return
    on_valid(param, converted, is_clear)


def build_import_plan(doc, records, columns, bad_id_rows=None,
                      ignored_headers=None, out_of_scope_rows=None,
                      scope_name=""):
    """Validation pass: resolve, classify, detect conflicts, queue writes.

    Runs zero transactions. A non-empty plan.conflicts means HARD STOP -
    no write queues are built in that case.
    """
    plan = ImportPlan(
        total_rows=len(records or []) + len(out_of_scope_rows or []),
        bad_id_rows=bad_id_rows,
        ignored_headers=ignored_headers,
        out_of_scope_rows=out_of_scope_rows,
        scope_name=scope_name,
    )

    elements_by_row = {}
    type_key_by_row = {}
    type_name_by_key = {}
    type_element_by_key = {}
    for record in records or []:
        element = _get_element(doc, record.element_id_value)
        if element is None:
            plan.missing_rows.append(
                (record.excel_row, record.element_id_value)
            )
            continue
        elements_by_row[record.excel_row] = element
        type_element, type_key = _get_type_info(
            doc, element, type_element_by_key
        )
        if type_key is not None and type_element is not None:
            type_key_by_row[record.excel_row] = type_key
            if type_key not in type_name_by_key:
                type_name_by_key[type_key] = _type_display_name(type_element)
    plan.matched_rows = len(elements_by_row)

    instance_specs = []
    type_specs = []
    for spec in columns or []:
        is_type = spec.meta.is_type if spec.meta is not None else None
        if is_type is None:
            is_type = _probe_is_type(
                spec, records or [], elements_by_row,
                type_key_by_row, type_element_by_key,
            )
        if is_type is None:
            plan.unresolved_columns.append(spec.param_name)
        elif is_type:
            type_specs.append(spec)
        else:
            instance_specs.append(spec)

    conflicts, agreed = state.detect_type_conflicts(
        records or [],
        [spec.param_name for spec in type_specs],
        type_key_by_row,
        type_name_by_key,
    )
    if conflicts:
        plan.conflicts = conflicts
        return plan

    for record in records or []:
        element = elements_by_row.get(record.excel_row)
        if element is None:
            continue
        for spec in instance_specs:
            text = safe_text(record.values.get(spec.param_name, "")).strip()
            if state.is_sentinel(text):
                plan.sentinel_count += 1
                continue

            def _append_instance(param, value, is_clear, _element=element,
                                 _spec=spec, _excel_row=record.excel_row):
                plan.instance_writes.append(
                    InstanceWrite(_element, param, _spec, value, _excel_row,
                                  is_clear=is_clear)
                )

            _queue_write(
                doc, plan, element, spec, text, record.excel_row,
                _append_instance,
            )

    spec_by_name = dict(
        (spec.param_name, spec) for spec in type_specs
    )
    for (type_key, param_name) in sorted(agreed.keys()):
        value_text, source_rows = agreed[(type_key, param_name)]
        type_element = type_element_by_key.get(type_key)
        spec = spec_by_name.get(param_name)
        if type_element is None or spec is None:
            continue
        type_name = type_name_by_key.get(type_key, "")

        def _append_type(param, value, is_clear, _type_element=type_element,
                         _type_name=type_name, _spec=spec,
                         _rows=source_rows):
            plan.type_writes.append(
                TypeWrite(_type_element, _type_name, param, _spec, value,
                          _rows, is_clear=is_clear)
            )

        _queue_write(
            doc, plan, type_element, spec, value_text, source_rows[0],
            _append_type,
        )

    return plan


def apply_import_plan(doc, plan):
    """Write pass: one transaction, per-write try/except."""
    written_instance = 0
    written_type = 0
    cleared_count = 0
    failed_lines = []

    transaction = DB.Transaction(doc, "Import Schedules from Excel")
    transaction.Start()
    try:
        for write in plan.instance_writes:
            try:
                write.param.Set(write.new_value)
                written_instance += 1
                if write.is_clear:
                    cleared_count += 1
            except Exception as set_error:
                failed_lines.append("Row {} - '{}': {}".format(
                    write.excel_row,
                    write.spec.param_name,
                    exception_text(set_error),
                ))
        for write in plan.type_writes:
            try:
                write.param.Set(write.new_value)
                written_type += 1
                if write.is_clear:
                    cleared_count += 1
            except Exception as set_error:
                failed_lines.append("Type '{}' - '{}': {}".format(
                    write.type_name,
                    write.spec.param_name,
                    exception_text(set_error),
                ))
        status = transaction.Commit()
    except Exception:
        try:
            if transaction.HasStarted():
                transaction.RollBack()
        except Exception:
            LOGGER.exception("Could not roll back the import transaction.")
        raise

    # Revit can veto the commit (errors that cannot be ignored, ownership
    # conflicts). Report that instead of counts nothing kept.
    if status != DB.TransactionStatus.Committed:
        failed_lines.insert(0, (
            "Revit did not commit the changes (transaction status: {}). "
            "Nothing was written."
        ).format(status))
        written_instance = 0
        written_type = 0
        cleared_count = 0

    return ImportResult(written_instance, written_type, failed_lines, plan,
                        cleared_count=cleared_count)
