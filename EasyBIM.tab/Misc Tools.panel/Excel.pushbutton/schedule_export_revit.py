# -*- coding: utf-8 -*-
"""Revit API helpers for the Export Schedules to Excel command."""

# pylint: disable=import-error,invalid-name,broad-except
import os
import sys

from pyrevit import DB
from pyrevit import HOST_APP
from pyrevit import framework
from pyrevit import script

from easybim import print_sets

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

import schedule_export_state as state
import schedule_export_xlsx


LOGGER = script.get_logger()

TOKEN_CANDIDATE_BIP_NAMES = (
    "ALL_MODEL_INSTANCE_COMMENTS",
    "ALL_MODEL_MARK",
)

INTERNAL_ORDER_NOTE = "Rows are in Revit internal order ({})."


class _WarningSwallower(DB.IFailuresPreprocessor):
    """Deletes warnings so the temporary token commit never shows dialogs."""

    def PreprocessFailures(self, failures_accessor):
        try:
            for failure in failures_accessor.GetFailureMessages():
                if failure.GetSeverity() == DB.FailureSeverity.Warning:
                    failures_accessor.DeleteWarning(failure)
        except Exception:
            pass
        return DB.FailureProcessingResult.Continue


def _get_sheet_category_id():
    try:
        return DB.ElementId(DB.BuiltInCategory.OST_Sheets)
    except Exception:
        return None


def is_exportable_schedule(schedule):
    """True for schedules worth showing in the selection window."""
    try:
        if getattr(schedule, "IsTemplate", False):
            return False
        if getattr(schedule, "IsTitleblockRevisionSchedule", False):
            return False
        definition = schedule.Definition
        if getattr(definition, "IsInternalKeynoteSchedule", False):
            return False
        if getattr(definition, "IsRevisionSchedule", False):
            return False
        return True
    except Exception:
        return False


def _classify_schedule_kind(schedule):
    try:
        sheet_category_id = _get_sheet_category_id()
        if sheet_category_id is not None and print_sets.is_sheet_list_schedule(
                schedule, sheet_category_id):
            return state.KIND_SHEET_LIST
        if getattr(schedule.Definition, "IsKeySchedule", False):
            return state.KIND_KEY
    except Exception:
        pass
    return state.KIND_REGULAR


def collect_schedule_options(doc):
    schedules = list(
        DB.FilteredElementCollector(doc)
          .OfClass(framework.get_type(DB.ViewSchedule))
          .WhereElementIsNotElementType()
          .ToElements()
    )

    options = []
    for schedule in schedules:
        if not is_exportable_schedule(schedule):
            continue
        options.append(state.ScheduleOption(
            schedule,
            getattr(schedule, "Name", ""),
            kind=_classify_schedule_kind(schedule),
        ))
    options.sort(key=lambda option: option.name.lower())
    return options


def _collect_referenced_param_id_values(definition):
    filter_ids = []
    sort_group_ids = []
    try:
        for schedule_filter in definition.GetFilters():
            try:
                field = definition.GetField(schedule_filter.FieldId)
                filter_ids.append(
                    print_sets.get_element_id_value(field.ParameterId)
                )
            except Exception:
                continue
    except Exception:
        pass
    try:
        for sort_group_field in definition.GetSortGroupFields():
            try:
                field = definition.GetField(sort_group_field.FieldId)
                sort_group_ids.append(
                    print_sets.get_element_id_value(field.ParameterId)
                )
            except Exception:
                continue
    except Exception:
        pass
    return filter_ids, sort_group_ids


def _pick_token_candidate(schedule, elements):
    """Return a writable text BuiltInParameter safe to use for row tokens."""
    filter_ids, sort_group_ids = _collect_referenced_param_id_values(
        schedule.Definition
    )
    for bip_name in TOKEN_CANDIDATE_BIP_NAMES:
        bip = getattr(DB.BuiltInParameter, bip_name, None)
        if bip is None:
            continue
        try:
            bip_id_value = print_sets.get_element_id_value(DB.ElementId(bip))
        except Exception:
            continue
        if state.should_skip_token_candidate(
                bip_id_value, filter_ids, sort_group_ids):
            LOGGER.debug(
                "Token candidate %s is used by schedule filters/sorting.",
                bip_name,
            )
            continue
        for element in elements:
            try:
                param = element.get_Parameter(bip)
                if param is not None and not param.IsReadOnly \
                        and param.StorageType == DB.StorageType.String:
                    return bip
            except Exception:
                continue
    return None


def _ensure_token_field_visible(definition, bip):
    """Make sure the token parameter is a visible schedule column."""
    target_id_value = print_sets.get_element_id_value(DB.ElementId(bip))
    for field_id in definition.GetFieldOrder():
        try:
            field = definition.GetField(field_id)
            field_param_value = print_sets.get_element_id_value(
                field.ParameterId
            )
        except Exception:
            continue
        if field_param_value == target_id_value:
            if getattr(field, "IsHidden", False):
                field.IsHidden = False
            return
    definition.AddField(DB.ScheduleFieldType.Instance, DB.ElementId(bip))


def _write_tokens_and_export(doc, schedule, elements, bip):
    """Write row tokens, export the schedule text, then roll everything back."""
    lines = []
    tokened_count = 0
    field_ids = []
    tgroup = DB.TransactionGroup(doc, "EasyBIM Temp Schedule Ordering")
    tgroup.Start()
    try:
        txn = DB.Transaction(doc, "Write ordering tokens")
        txn.Start()
        try:
            failure_options = txn.GetFailureHandlingOptions()
            failure_options.SetFailuresPreprocessor(_WarningSwallower())
            txn.SetFailureHandlingOptions(failure_options)
        except Exception:
            pass

        try:
            schedule.Definition.IsItemized = True
        except Exception:
            pass
        _ensure_token_field_visible(schedule.Definition, bip)

        for element in elements:
            try:
                param = element.get_Parameter(bip)
                if param is None or param.IsReadOnly \
                        or param.StorageType != DB.StorageType.String:
                    continue
                id_value = print_sets.get_element_id_value(element.Id)
                if id_value is None:
                    continue
                param.Set(state.build_token(id_value))
                tokened_count += 1
            except Exception:
                # Workshared ownership and similar per-element errors: the
                # element simply stays untokened and is appended last later.
                continue

        try:
            doc.Regenerate()
        except Exception:
            pass

        if txn.Commit() != DB.TransactionStatus.Committed:
            raise Exception("Token transaction was not committed.")

        # Column layout of THIS export (the token field may have been
        # added or unhidden), so row text maps back to the right fields.
        field_ids = visible_field_ids(schedule)
        lines = print_sets.get_schedule_text_data(
            schedule, script, logger=LOGGER, host_app=HOST_APP
        )
    finally:
        try:
            if tgroup.HasStarted():
                tgroup.RollBack()
        except Exception:
            LOGGER.exception("Failed to roll back temporary ordering edits.")
    return lines, tokened_count, field_ids


def _ordered_sheet_list_elements(doc, schedule, elements):
    try:
        ordered_sheets = print_sets.get_ordered_schedule_sheets(
            doc, schedule, DB, framework, script,
            logger=LOGGER, host_app=HOST_APP,
        )
        if ordered_sheets:
            return list(ordered_sheets), ""
    except Exception:
        LOGGER.exception("Sheet list ordering failed.")
    return elements, INTERNAL_ORDER_NOTE.format(
        "the sheet list export could not be read"
    )


def field_id_key(field_or_id):
    """Stable int key for a ScheduleField / ScheduleFieldId."""
    try:
        field_id = getattr(field_or_id, "FieldId", field_or_id)
        return int(field_id.IntegerValue)
    except Exception:
        return None


def visible_field_ids(schedule):
    return [field_id_key(field) for field in _visible_fields(schedule)]


def _visible_fields(schedule):
    fields = []
    definition = schedule.Definition
    for field_id in definition.GetFieldOrder():
        try:
            field = definition.GetField(field_id)
            if not field.IsHidden:
                fields.append(field)
        except Exception:
            continue
    return fields


def _ordered_by_row_values(doc, schedule, elements):
    """Read-only ordering: match exported schedule rows by cell values.

    Nothing is written to the model, so this works in workshared models
    without borrowing elements or needing Reload Latest.
    Returns (elements, note, row_text) or None when no row matched;
    row_text is {element id value: {field id key: displayed text}}.
    """
    fields = _visible_fields(schedule)
    if not fields:
        return None
    lines = print_sets.get_schedule_text_data(
        schedule, script, logger=LOGGER, host_app=HOST_APP
    )
    if not lines:
        return None

    headings = []
    for field in fields:
        try:
            headings.append(field.ColumnHeading)
        except Exception:
            headings.append("")

    type_cache = {}
    related_cache = schedule_export_xlsx.RelatedElementCache(doc, schedule)
    element_cells = []
    for element in elements:
        cells = []
        for field in fields:
            try:
                cells.append(schedule_export_xlsx.field_display_text(
                    doc, element, field, type_cache, related_cache
                ))
            except Exception:
                cells.append(None)
        element_cells.append((element, cells))

    ordered, unmatched, row_cells = state.order_elements_by_row_values(
        lines, element_cells, len(fields), headings=headings
    )
    if not ordered:
        return None
    field_ids = [field_id_key(field) for field in fields]
    row_text = {}
    for element, cells in zip(ordered, row_cells):
        row_text[print_sets.get_element_id_value(element.Id)] = \
            state.cells_by_field_id(cells, field_ids)
    note = ""
    if unmatched:
        note = ("{} element(s) could not be matched to schedule rows "
                "and were appended last.").format(len(unmatched))
    return ordered + unmatched, note, row_text


def _try_value_match(doc, schedule, elements):
    try:
        return _ordered_by_row_values(doc, schedule, elements)
    except Exception as match_error:
        LOGGER.warning("Row value matching failed: %s", match_error)
        return None


def _value_order_or_internal(doc, schedule, elements, reason):
    result = _try_value_match(doc, schedule, elements)
    if result is not None:
        return result
    return elements, INTERNAL_ORDER_NOTE.format(reason), {}


def _with_row_text(doc, schedule, ordered_result):
    """Add value-matched row text to an (elements, note) ordering result."""
    elements, note = ordered_result
    matched = _try_value_match(doc, schedule, elements)
    return elements, note, (matched[2] if matched is not None else {})


def get_display_ordered_elements(doc, schedule):
    """Return (ordered elements, ordering note, row_text).

    row_text maps element id value -> {field id key: text the schedule
    displays}; it fills columns that have no readable parameter
    (calculated, count, material, related-element fields, ...).
    """
    elements = list(DB.FilteredElementCollector(doc, schedule.Id))
    if not elements:
        return [], "", {}

    kind = _classify_schedule_kind(schedule)
    if kind == state.KIND_SHEET_LIST:
        return _with_row_text(doc, schedule, _ordered_sheet_list_elements(
            doc, schedule, elements
        ))
    if kind == state.KIND_KEY:
        return _with_row_text(doc, schedule, (
            elements,
            INTERNAL_ORDER_NOTE.format("key schedules cannot be reordered"),
        ))
    if getattr(doc, "IsReadOnly", False):
        return _value_order_or_internal(
            doc, schedule, elements, "the document is read-only"
        )
    if getattr(doc, "IsWorkshared", False):
        # Writing temporary tokens would borrow elements from central and
        # fail on anything not current ("Reload Latest"); stay read-only.
        return _value_order_or_internal(
            doc, schedule, elements,
            "no schedule rows could be matched to elements"
        )

    candidate_bip = _pick_token_candidate(schedule, elements)
    if candidate_bip is None:
        return _value_order_or_internal(
            doc, schedule, elements,
            "no usable text parameter was found for ordering"
        )

    try:
        lines, tokened_count, token_field_ids = _write_tokens_and_export(
            doc, schedule, elements, candidate_bip
        )
    except Exception as ordering_error:
        LOGGER.warning("Schedule ordering failed: %s", ordering_error)
        return _value_order_or_internal(
            doc, schedule, elements,
            "ordering failed: {}".format(ordering_error)
        )

    if not lines or not tokened_count:
        return _value_order_or_internal(
            doc, schedule, elements,
            "the schedule export produced no ordering data"
        )

    ordered_ids = state.parse_ordered_ids(lines)
    pairs = [
        (print_sets.get_element_id_value(element.Id), element)
        for element in elements
    ]
    ordered_elements, unmatched_elements = state.order_elements_by_ids(
        pairs, ordered_ids
    )
    if not ordered_elements:
        return _value_order_or_internal(
            doc, schedule, elements, "no rows could be matched to elements"
        )

    note = ""
    if unmatched_elements:
        note = ("{} element(s) could not be matched to schedule rows "
                "and were appended last.").format(len(unmatched_elements))
    row_text = state.map_token_rows(lines, token_field_ids)
    return ordered_elements + unmatched_elements, note, row_text
