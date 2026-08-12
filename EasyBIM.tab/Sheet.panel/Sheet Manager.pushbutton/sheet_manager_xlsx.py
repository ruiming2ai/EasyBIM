# -*- coding: utf-8 -*-
"""Excel export/import for the Sheet Manager table.

Export mirrors the Excel.pushbutton schedule-export conventions (locked
grey cells, red read-only headers, sheet protection, hidden ``_metadata``
round-trip sheet). Import reads workbooks with the shared
``easybim.excel_workbook`` OOXML reader - no Excel installation needed.

This module stays free of pyrevit imports so the desktop test suite can
exercise the planning logic (xlsxwriter is optional and guarded).
"""

from __future__ import print_function

import re

try:
    import xlsxwriter
    XLSXWRITER_AVAILABLE = True
except Exception:
    xlsxwriter = None
    XLSXWRITER_AVAILABLE = False

import sheet_manager_state as state


EXPORT_SHEET_NAME = "SheetManager"
METADATA_SHEET_NAME = "_metadata"
FORMAT_SIGNATURE = "EasyBIM Sheet Manager"
FORMAT_VERSION = 1
METADATA_HEADERS = ["ColumnKey", "Kind", "Header", "ParamName",
                    "ParamIdValue", "RevisionId", "RevisionSeq", "ReadOnly"]
MAX_COLUMN_WIDTH = 50

_TRUTHY_CELLS = ("yes", "y", "true", "1", "x")


def build_export_filename(doc_title):
    title = u"{0}".format(doc_title or u"").strip()
    if title.lower().endswith(u".rvt"):
        title = title[:-4]
    if title:
        name = u"Sheet Manager - {0}.xlsx".format(title)
    else:
        name = u"Sheet Manager Export.xlsx"
    return re.sub(r'[\\/:*?"<>|]+', u"_", name)


def export_table_to_xlsx(doc_title, columns, rows, file_path,
                         timestamp_text=u""):
    """Write the staged table WYSIWYG; returns the exported row count."""
    if not XLSXWRITER_AVAILABLE:
        raise Exception("The 'xlsxwriter' module is not available.")
    header_cells, metadata_rows, data_rows, lock_rows = \
        state.build_export_matrix(columns, rows)
    cols = state.export_columns(columns)

    workbook = xlsxwriter.Workbook(file_path)
    body_error = None
    try:
        worksheet = workbook.add_worksheet(EXPORT_SHEET_NAME)
        metadata_sheet = workbook.add_worksheet(METADATA_SHEET_NAME)
        metadata_sheet.hide()

        bold = workbook.add_format({"bold": True})
        unlocked = workbook.add_format({"locked": False})
        locked = workbook.add_format(
            {"locked": True, "bg_color": "#D9D9D9"})
        readonly_header = workbook.add_format(
            {"bold": True, "bg_color": "#FF3131"})
        revision_header = workbook.add_format(
            {"bold": True, "bg_color": "#D9E1F2"})

        worksheet.freeze_panes(1, 0)
        widths = [len(header_cells[0])]
        worksheet.write(0, 0, header_cells[0], bold)
        for pos, column in enumerate(cols):
            header_format = bold
            if column.is_read_only \
                    or column.kind == state.KIND_SCHEDULE_TEXT:
                header_format = readonly_header
            elif column.kind == state.KIND_REVISION:
                header_format = revision_header
            worksheet.write(0, pos + 1, header_cells[pos + 1],
                            header_format)
            widths.append(len(header_cells[pos + 1] or u""))

        for row_pos, cells in enumerate(data_rows):
            locks = lock_rows[row_pos]
            for col_pos, text in enumerate(cells):
                cell_format = locked if locks[col_pos] else unlocked
                worksheet.write(row_pos + 1, col_pos, text, cell_format)
                if col_pos < len(widths):
                    widths[col_pos] = max(widths[col_pos],
                                          len(text or u""))

        for col_pos, width in enumerate(widths):
            worksheet.set_column(col_pos, col_pos,
                                 min(width + 3, MAX_COLUMN_WIDTH))
        worksheet.autofilter(0, 0, len(data_rows),
                             len(header_cells) - 1)
        worksheet.protect(
            "",
            {
                "autofilter": True,
                "sort": True,
                "format_cells": True,
                "format_columns": True,
                "format_rows": True,
                "delete_columns": True,
                "delete_rows": True,
                "select_locked_cells": True,
                "select_unlocked_cells": True,
            },
        )

        metadata_sheet.write_row(
            0, 0, [FORMAT_SIGNATURE, u"{0}".format(doc_title or u""),
                   u"{0}".format(timestamp_text or u""),
                   FORMAT_VERSION])
        metadata_sheet.write_row(1, 0, METADATA_HEADERS)
        for pos, meta in enumerate(metadata_rows):
            metadata_sheet.write_row(pos + 2, 0, meta)
    except Exception as write_error:
        body_error = write_error
    try:
        workbook.close()
    except Exception as close_error:
        if body_error is None:
            raise Exception(
                "Could not write '{0}'. Close it in Excel and retry. "
                "({1})".format(file_path, close_error))
    if body_error is not None:
        raise body_error
    return len(data_rows)


def parse_bool_cell(text):
    return u"{0}".format(text or u"").strip().lower() in _TRUTHY_CELLS


def _default_normalize(text):
    return u" ".join(u"{0}".format(text or u"").split()).lower()


class ImportPlan(object):
    def __init__(self):
        self.cell_edits = []        # (row, column, new_value)
        self.creatable = []         # {"number","name","values","revisions"}
        self.skipped_rows = []      # (label, reason) - red-flag list
        self.skipped_readonly = 0
        self.unmatched_columns = []
        self.matched_count = 0

    def is_empty(self):
        return not (self.cell_edits or self.creatable)


def _metadata_column_keys(metadata_rows):
    keys = []
    for _, cells in metadata_rows or []:
        if not cells:
            continue
        first = u"{0}".format(cells[0] or u"")
        if first in (FORMAT_SIGNATURE, METADATA_HEADERS[0]):
            continue
        keys.append(first)
    return keys


def plan_import(export_rows, metadata_rows, all_rows, columns,
                normalize=None):
    """Match workbook rows to grid rows and plan staged edits/creations.

    ``export_rows``/``metadata_rows``: SheetData.rows from
    easybim.excel_workbook. Rows match by ElementId; a normalized
    sheet-number fallback applies only when the id is present but stale.
    Rows with a MISSING ElementId (and unmatched rows) become creatable
    entries unless their number is missing or already taken - those are
    red-flagged in skipped_rows and never created.
    """
    normalize = normalize or _default_normalize
    plan = ImportPlan()
    if not export_rows:
        return plan
    header_cells = export_rows[0][1]
    data_rows = export_rows[1:]
    column_map = state.columns_by_key(columns)

    file_keys = [None]
    meta_keys = _metadata_column_keys(metadata_rows)
    if meta_keys and len(meta_keys) == len(header_cells) - 1:
        file_keys += meta_keys
    else:
        by_header = {}
        for column in state.export_columns(columns):
            by_header.setdefault(column.header, column.key)
        for header in header_cells[1:]:
            file_keys.append(by_header.get(u"{0}".format(header or u"")))

    for key in file_keys[1:]:
        if key is not None and key not in column_map \
                and key not in plan.unmatched_columns:
            plan.unmatched_columns.append(key)

    rows_by_id = {}
    rows_by_number = {}
    for row in all_rows:
        if row.sheet_id is not None:
            rows_by_id[row.sheet_id] = row
        number_key = normalize(row.number)
        if number_key:
            rows_by_number.setdefault(number_key, row)

    number_pos = file_keys.index("number") if "number" in file_keys \
        else None
    name_pos = file_keys.index("name") if "name" in file_keys else None

    # Pass 1: stage edits for matched rows; keep unmatched rows for pass 2
    # so the create-check can run against PROJECTED numbers (a staged
    # rename frees its old number for a new row).
    unmatched_rows = []
    imported_numbers = {}
    for excel_row, cells in data_rows:
        if not cells or not any(u"{0}".format(c or u"").strip()
                                for c in cells):
            continue
        id_text = u"{0}".format(cells[0] or u"").strip() if cells else u""
        target = None
        if id_text:
            sheet_id = None
            try:
                sheet_id = int(float(id_text))
            except (TypeError, ValueError):
                sheet_id = None
            if sheet_id is not None:
                target = rows_by_id.get(sheet_id)
            if target is None and number_pos is not None \
                    and number_pos < len(cells):
                # stale id from another model: rescue by sheet number
                target = rows_by_number.get(
                    normalize(cells[number_pos]))

        if target is not None:
            plan.matched_count += 1
            for pos, key in enumerate(file_keys):
                if pos == 0 or key is None or pos >= len(cells):
                    continue
                column = column_map.get(key)
                if column is None:
                    continue
                text = u"{0}".format(cells[pos] or u"")
                if column.kind == state.KIND_REVISION:
                    desired = parse_bool_cell(text)
                    if desired != bool(getattr(target, column.attr,
                                               False)):
                        plan.cell_edits.append((target, column, desired))
                else:
                    current = getattr(target, column.attr, None)
                    current_text = u"" if current is None \
                        else u"{0}".format(current)
                    if text == current_text:
                        continue
                    if not state.can_edit_cell(target, column):
                        plan.skipped_readonly += 1
                        continue
                    plan.cell_edits.append((target, column, text))
                    if column.kind == state.KIND_NUMBER:
                        imported_numbers[target] = text
            continue

        unmatched_rows.append((excel_row, cells))

    # Pass 2: creation candidates checked against projected numbers.
    taken_numbers = set()
    for row in all_rows:
        projected = imported_numbers.get(row, row.number)
        number_key = normalize(projected)
        if number_key:
            taken_numbers.add(number_key)

    for excel_row, cells in unmatched_rows:
        number = u""
        if number_pos is not None and number_pos < len(cells):
            number = u"{0}".format(cells[number_pos] or u"").strip()
        label = number or u"(Excel row {0})".format(excel_row)
        if not number:
            plan.skipped_rows.append((label, "No sheet number"))
            continue
        number_key = normalize(number)
        if number_key in taken_numbers:
            plan.skipped_rows.append(
                (number, "Duplicate sheet number - not created"))
            continue
        taken_numbers.add(number_key)
        name = u""
        if name_pos is not None and name_pos < len(cells):
            name = u"{0}".format(cells[name_pos] or u"")
        values = {}
        revisions = set()
        for pos, key in enumerate(file_keys):
            if pos == 0 or key is None or pos >= len(cells):
                continue
            column = column_map.get(key)
            if column is None:
                continue
            if column.kind == state.KIND_REVISION:
                if parse_bool_cell(cells[pos]):
                    revisions.add(column.revision_id)
            elif column.kind == state.KIND_SHEET_PARAM:
                values[key] = u"{0}".format(cells[pos] or u"")
        plan.creatable.append({
            "number": number,
            "name": name,
            "values": values,
            "revisions": revisions,
        })
    return plan
