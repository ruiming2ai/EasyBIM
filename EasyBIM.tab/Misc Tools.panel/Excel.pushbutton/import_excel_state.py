# -*- coding: utf-8 -*-
"""Pure state helpers for the Import Schedules from Excel command."""

# pylint: disable=import-error,invalid-name,broad-except
import re


UNIT_POSTFIX_PATTERN = re.compile(r"\s*\[.*\]$")
SENTINEL_VALUES = ("<does not exist>", "<unsupported>", "<Error>")
HELPER_HEADERS = ("_EBIM_TypeId", "_EBIM_RowId", "TypeId")
ELEMENT_ID_HEADER = "ElementId"
SUMMARY_DISPLAY_LIMIT = 20

_TRUE_TEXTS = ("yes", "1", "true")
_FALSE_TEXTS = ("no", "0", "false")


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


class MetaEntry(object):
    """One _metadata row describing an exported column."""

    def __init__(self, forge_type_id="", unit_type_id="", symbol_type_id="",
                 is_schedule_override=False, is_type=None):
        self.forge_type_id = _safe_text(forge_type_id)
        self.unit_type_id = _safe_text(unit_type_id)
        self.symbol_type_id = _safe_text(symbol_type_id)
        self.is_schedule_override = bool(is_schedule_override)
        # True/False from the IsTypeParameter column; None = unknown
        # (legacy export) -> the Revit layer probes at runtime.
        self.is_type = is_type


class ColumnSpec(object):
    """One importable parameter column of the Export sheet."""

    def __init__(self, col_index, header, param_name, meta=None):
        self.col_index = int(col_index)
        self.header = _safe_text(header)
        self.param_name = _safe_text(param_name)
        self.meta = meta


class RowRecord(object):
    """One data row of the Export sheet."""

    def __init__(self, excel_row, element_id_value, values):
        self.excel_row = int(excel_row)
        self.element_id_value = element_id_value
        self.values = dict(values or {})


class TypeConflict(object):
    """Divergent values for one type parameter within one element type."""

    def __init__(self, type_key, type_name, param_name, values):
        self.type_key = type_key
        self.type_name = _safe_text(type_name)
        self.param_name = _safe_text(param_name)
        # [(display_value, [excel_rows])], sorted by display value
        self.values = list(values or [])


def strip_unit_postfix(header):
    return UNIT_POSTFIX_PATTERN.sub("", _safe_text(header)).strip()


def is_sentinel(value):
    return _safe_text(value).strip() in SENTINEL_VALUES


def parse_yesno(text):
    """Strict yes/no parsing: unrecognized text returns None (invalid)."""
    value = _safe_text(text).strip().lower()
    if value in _TRUE_TEXTS:
        return True
    if value in _FALSE_TEXTS:
        return False
    return None


def parse_number(text):
    try:
        return float(_safe_text(text).strip())
    except Exception:
        return None


def canonical_value(text, numeric):
    """Comparison key for a cell value.

    Numeric groups compare as floats so "2.5" and "2.50" never count as a
    conflict; everything else compares as stripped text (so text values
    like Mark "010" vs "10" stay distinct).
    """
    raw = _safe_text(text).strip()
    if numeric:
        number = parse_number(raw)
        if number is not None:
            return repr(number)
    return raw


def parse_metadata_rows(sheet_rows):
    """Parse _metadata rows [(excel_row, values)] -> {param_name: MetaEntry}."""
    entries = {}
    for excel_row, values in sheet_rows or []:
        del excel_row
        values = list(values or [])
        name = _safe_text(values[0] if values else "").strip()
        if not name or name == "Parameter Name":
            continue

        def _cell(index):
            if index < len(values):
                return _safe_text(values[index]).strip()
            return ""

        is_type = None
        is_type_text = _cell(5).lower()
        if is_type_text:
            is_type = is_type_text == "yes"

        entries[name] = MetaEntry(
            forge_type_id=_cell(1),
            unit_type_id=_cell(2),
            symbol_type_id=_cell(3),
            is_schedule_override=_cell(4).lower() == "yes",
            is_type=is_type,
        )
    return entries


def classify_columns(header_values, metadata):
    """Return ([ColumnSpec], [ignored_headers]) from the Export header row."""
    header_values = list(header_values or [])
    first = _safe_text(header_values[0] if header_values else "").strip()
    if first != ELEMENT_ID_HEADER:
        raise ValueError(
            "First column must be 'ElementId'. This file does not look "
            "like an EasyBIM/pyRevit parameter export."
        )

    columns = []
    ignored = []
    for col_index in range(1, len(header_values)):
        header = _safe_text(header_values[col_index]).strip()
        if not header:
            continue
        if header in HELPER_HEADERS:
            ignored.append(header)
            continue
        param_name = strip_unit_postfix(header)
        if not param_name:
            ignored.append(header)
            continue
        columns.append(ColumnSpec(
            col_index, header, param_name, (metadata or {}).get(param_name)
        ))
    return columns, ignored


def split_records_by_scope(records, member_id_values):
    """Partition records by schedule membership.

    Returns (in_scope_records, out_of_scope_excel_rows); membership is
    checked on element_id_value against the schedule's member ids.
    """
    members = set(member_id_values or [])
    in_scope = []
    out_rows = []
    for record in records or []:
        if record.element_id_value in members:
            in_scope.append(record)
        else:
            out_rows.append(record.excel_row)
    return in_scope, out_rows


def parse_export_rows(sheet_rows, columns):
    """Parse Export data rows -> ([RowRecord], [bad_id_excel_rows])."""
    records = []
    bad_rows = []
    for excel_row, values in sheet_rows or []:
        if excel_row == 1:
            continue
        values = list(values or [])
        id_text = _safe_text(values[0] if values else "").strip()
        has_data = any(_safe_text(value).strip() for value in values[1:])
        if not id_text:
            if has_data:
                bad_rows.append(excel_row)
            continue
        try:
            element_id_value = int(float(id_text))
        except Exception:
            bad_rows.append(excel_row)
            continue

        row_values = {}
        for spec in columns:
            if spec.col_index < len(values):
                row_values[spec.param_name] = _safe_text(
                    values[spec.col_index]
                )
            else:
                row_values[spec.param_name] = ""
        records.append(RowRecord(excel_row, element_id_value, row_values))
    return records, bad_rows


def detect_type_conflicts(records, type_param_names, type_key_by_row,
                          type_name_by_key):
    """Detect divergent type-parameter values within each element type.

    type_key_by_row maps excel_row -> type key (from the MODEL, not the
    workbook). Blank and sentinel cells and typeless rows are excluded, so
    one value plus blanks is not a conflict.

    Returns (conflicts, agreed) where agreed maps
    (type_key, param_name) -> (value_text, [excel_rows]).
    """
    raw_groups = {}
    for record in records or []:
        type_key = (type_key_by_row or {}).get(record.excel_row)
        if type_key is None:
            continue
        for param_name in type_param_names or []:
            text = _safe_text(record.values.get(param_name, "")).strip()
            if not text or is_sentinel(text):
                continue
            raw_groups.setdefault((type_key, param_name), []).append(
                (text, record.excel_row)
            )

    conflicts = []
    agreed = {}
    for (type_key, param_name), pairs in raw_groups.items():
        numeric = all(parse_number(text) is not None for text, _ in pairs)
        buckets = {}
        for text, excel_row in pairs:
            canon = canonical_value(text, numeric)
            bucket = buckets.setdefault(canon, [text, []])
            bucket[1].append(excel_row)

        if len(buckets) > 1:
            values = sorted(
                (bucket[0], sorted(bucket[1])) for bucket in buckets.values()
            )
            conflicts.append(TypeConflict(
                type_key,
                (type_name_by_key or {}).get(type_key, _safe_text(type_key)),
                param_name,
                values,
            ))
        else:
            bucket = list(buckets.values())[0]
            agreed[(type_key, param_name)] = (bucket[0], sorted(bucket[1]))

    conflicts.sort(key=lambda c: (c.type_name.lower(), c.param_name.lower()))
    return conflicts, agreed


def _rows_label(excel_rows):
    row_texts = [str(row) for row in excel_rows]
    if len(row_texts) == 1:
        return "row {}".format(row_texts[0])
    return "rows {}".format(", ".join(row_texts))


def build_conflict_report_lines(conflicts):
    lines = []
    for conflict in conflicts or []:
        values_text = ", ".join(
            '"{}" ({})'.format(display, _rows_label(rows))
            for display, rows in conflict.values
        )
        lines.append("Type '{}' - parameter '{}': {}".format(
            conflict.type_name, conflict.param_name, values_text
        ))
    return lines


def _capped(lines, limit=SUMMARY_DISPLAY_LIMIT):
    lines = list(lines or [])
    if len(lines) <= limit:
        return lines
    shown = lines[:limit]
    shown.append("...and {} more.".format(len(lines) - limit))
    return shown


def build_preview_lines(matched_rows, total_rows, instance_write_count,
                        type_write_count, type_write_row_count,
                        skipped_lines):
    lines = [
        "Rows matched to elements: {} of {}".format(
            int(matched_rows), int(total_rows)
        ),
        "Instance cells to write: {}".format(int(instance_write_count)),
        "Type parameters to write: {} (covering {} row(s))".format(
            int(type_write_count), int(type_write_row_count)
        ),
    ]
    if skipped_lines:
        lines.append("")
        lines.append("Skipped:")
        lines.extend(_capped(skipped_lines))
    return lines


def build_import_summary_text(written_instance, written_type, failed_lines,
                              skipped_lines, file_path):
    lines = [
        "Imported from:",
        _safe_text(file_path),
        "",
        "Instance cells written: {}".format(int(written_instance)),
        "Type parameters written: {}".format(int(written_type)),
    ]
    if skipped_lines:
        lines.append("")
        lines.append("Skipped:")
        lines.extend(_capped(skipped_lines))
    if failed_lines:
        lines.append("")
        lines.append("Failed:")
        lines.extend(_capped(failed_lines))
    return "\n".join(lines)
