# -*- coding: utf-8 -*-
"""Excel export for the Family Types table.

Follows the conventions the Excel and Sheet Manager exports already set -
locked grey cells for anything read-only, a red header over them, sheet
protection, and a hidden ``_metadata`` round-trip sheet.  What is new here is
the header cell itself: it carries the parameter name on the first line and
the parameter's **ElementId** on the second, in the same cell.  That id is
what an import matches on, and putting it on the visible sheet rather than
only in ``_metadata`` means somebody reading the file can see the key.

Import reads workbooks back through ``easybim.excel_workbook`` (pure stdlib
OOXML), so no Excel installation is needed at either end.

Free of pyrevit imports so the desktop test suite can exercise it;
``xlsxwriter`` is optional and guarded the same way.
"""

from __future__ import print_function

import re

try:
    import xlsxwriter
    XLSXWRITER_AVAILABLE = True
except Exception:
    xlsxwriter = None
    XLSXWRITER_AVAILABLE = False

import family_types_state as state


EXPORT_SHEET_NAME = "FamilyTypes"
METADATA_SHEET_NAME = "_metadata"
FORMAT_SIGNATURE = "EasyBIM Family Types"
FORMAT_VERSION = 1
MAX_COLUMN_WIDTH = 50

#: Header fills, matching the palette the other EasyBIM exports use.
READONLY_HEADER_COLOR = "#FF3131"
INSTANCE_HEADER_COLOR = "#D9E1F2"
LOCKED_CELL_COLOR = "#D9D9D9"


def build_export_filename(family_name):
    name = u"{0}".format(family_name or u"").strip()
    if name.lower().endswith(u".rfa"):
        name = name[:-4]
    if name:
        filename = u"Family Types - {0}.xlsx".format(name)
    else:
        filename = u"Family Types Export.xlsx"
    return re.sub(r'[\\/:*?"<>|]+', u"_", filename)


def _header_width(header_cell):
    """Widest line of a two-line header cell."""
    return max(len(line) for line in
               u"{0}".format(header_cell or u"").split(u"\n"))


def export_table_to_xlsx(family_name, doc_title, columns, rows, file_path,
                         timestamp_text=u""):
    """Write the staged table WYSIWYG; returns the exported row count."""
    if not XLSXWRITER_AVAILABLE:
        raise Exception("The 'xlsxwriter' module is not available.")
    header_cells, metadata_rows, data_rows, lock_flags = \
        state.build_export_matrix(columns, rows)
    cols = state.export_columns(columns)

    workbook = xlsxwriter.Workbook(file_path)
    body_error = None
    try:
        worksheet = workbook.add_worksheet(EXPORT_SHEET_NAME)
        metadata_sheet = workbook.add_worksheet(METADATA_SHEET_NAME)
        metadata_sheet.hide()

        # text_wrap is what makes the second header line visible; without it
        # Excel shows the newline as a single tall-but-clipped line.
        bold_wrap = workbook.add_format({"bold": True, "text_wrap": True,
                                         "valign": "top"})
        instance_header = workbook.add_format(
            {"bold": True, "text_wrap": True, "valign": "top",
             "bg_color": INSTANCE_HEADER_COLOR})
        readonly_header = workbook.add_format(
            {"bold": True, "text_wrap": True, "valign": "top",
             "bg_color": READONLY_HEADER_COLOR})
        unlocked = workbook.add_format({"locked": False})
        locked = workbook.add_format(
            {"locked": True, "bg_color": LOCKED_CELL_COLOR})

        worksheet.freeze_panes(1, 1)
        widths = [_header_width(header_cells[0])]
        worksheet.write(0, 0, header_cells[0], bold_wrap)
        for position, column in enumerate(cols):
            if column.is_read_only:
                header_format = readonly_header
            elif column.is_instance:
                header_format = instance_header
            else:
                header_format = bold_wrap
            cell = header_cells[position + 1]
            worksheet.write(0, position + 1, cell, header_format)
            widths.append(_header_width(cell))

        for row_position, cells in enumerate(data_rows):
            for col_position, text in enumerate(cells):
                cell_format = locked if lock_flags[col_position] else unlocked
                worksheet.write(row_position + 1, col_position, text,
                                cell_format)
                if col_position < len(widths):
                    widths[col_position] = max(widths[col_position],
                                               len(text or u""))

        for col_position, width in enumerate(widths):
            worksheet.set_column(col_position, col_position,
                                 min(width + 3, MAX_COLUMN_WIDTH))
        if data_rows:
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
                "insert_rows": True,
                "delete_rows": True,
                "select_locked_cells": True,
                "select_unlocked_cells": True,
            },
        )

        metadata_sheet.write_row(
            0, 0, [FORMAT_SIGNATURE,
                   u"{0}".format(family_name or u""),
                   u"{0}".format(doc_title or u""),
                   u"{0}".format(timestamp_text or u""),
                   FORMAT_VERSION])
        metadata_sheet.write_row(1, 0, state.METADATA_HEADERS)
        for position, meta in enumerate(metadata_rows):
            metadata_sheet.write_row(position + 2, 0, meta)
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
