# -*- coding: utf-8 -*-
"""Read .xlsx/.xlsm worksheets by name, including hidden sheets.

Companion to excel_print_sets.py: that module reads only the first visible
sheet's visible cells (its print-set contract), while importers need specific
sheets by name - "Export" and the hidden "_metadata" - with every row and
column. Pure stdlib (zipfile + ElementTree); no Excel installation needed.
"""

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile


SUPPORTED_EXTENSIONS = (".xlsx", ".xlsm")

_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_REL_NS = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
)


class UnsupportedWorkbook(Exception):
    """Raised when the workbook cannot be opened or parsed."""


class SheetData(object):
    """Rows of one worksheet as [(excel_row_number, [cell_text, ...])].

    Each values list is dense from column A up to the row's last used
    column; gaps are empty strings.
    """

    def __init__(self, name, rows):
        self.name = name
        self.rows = list(rows or [])


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except Exception:
        return fallback


def _read_xml(workbook, name):
    try:
        return ET.fromstring(workbook.read(name))
    except KeyError:
        raise UnsupportedWorkbook("Missing workbook part: {}".format(name))


def _read_shared_strings(workbook):
    try:
        root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    strings = []
    for item in root.findall(_MAIN_NS + "si"):
        pieces = []
        for text_node in item.iter(_MAIN_NS + "t"):
            pieces.append(text_node.text or "")
        strings.append("".join(pieces))
    return strings


def _cell_text(cell, shared_strings):
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        pieces = []
        for text_node in cell.iter(_MAIN_NS + "t"):
            pieces.append(text_node.text or "")
        return "".join(pieces)

    value_node = cell.find(_MAIN_NS + "v")
    value = "" if value_node is None else _safe_text(value_node.text)
    if cell_type == "s":
        index = _safe_int(value, -1)
        if 0 <= index < len(shared_strings):
            return shared_strings[index]
        return ""
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def _column_index_from_ref(cell_ref):
    match = re.match(r"([A-Za-z]+)", _safe_text(cell_ref))
    if not match:
        return None
    index = 0
    for char in match.group(1).upper():
        index = (index * 26) + ord(char) - ord("A") + 1
    return index


def _sheet_paths_by_name(workbook):
    """Return ordered [(sheet_name, part_path)] for ALL sheets, hidden too."""
    workbook_root = _read_xml(workbook, "xl/workbook.xml")
    rel_root = _read_xml(workbook, "xl/_rels/workbook.xml.rels")
    relationships = {}
    for rel in rel_root.findall(_REL_NS + "Relationship"):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target", "")
        if rel_id:
            relationships[rel_id] = target

    sheets_root = workbook_root.find(_MAIN_NS + "sheets")
    if sheets_root is None:
        raise UnsupportedWorkbook("No worksheets were found.")

    sheet_paths = []
    for sheet in sheets_root.findall(_MAIN_NS + "sheet"):
        name = sheet.attrib.get("name", "")
        rel_id = sheet.attrib.get(_OFFICE_REL_NS + "id")
        target = relationships.get(rel_id)
        if not name or not target:
            continue
        if target.startswith("/"):
            part_path = target.lstrip("/")
        else:
            part_path = posixpath.normpath(posixpath.join("xl", target))
        sheet_paths.append((name, part_path))
    return sheet_paths


def _read_sheet(workbook, part_path, shared_strings):
    sheet_root = _read_xml(workbook, part_path)
    sheet_data = sheet_root.find(_MAIN_NS + "sheetData")
    if sheet_data is None:
        return []

    rows = []
    for row_node in sheet_data.findall(_MAIN_NS + "row"):
        excel_row = _safe_int(row_node.attrib.get("r"), len(rows) + 1)
        cells = {}
        max_col = 0
        for cell in row_node.findall(_MAIN_NS + "c"):
            col_index = _column_index_from_ref(cell.attrib.get("r", ""))
            if col_index is None:
                col_index = max_col + 1
            cells[col_index] = _cell_text(cell, shared_strings)
            if col_index > max_col:
                max_col = col_index
        values = [cells.get(index, "") for index in range(1, max_col + 1)]
        rows.append((excel_row, values))
    return rows


def read_workbook_sheets(excel_path, sheet_names=None):
    """Return {sheet_name: SheetData} for the requested sheets (all if None).

    Hidden and veryHidden sheets are included. Accepts .xlsx and .xlsm
    (same OOXML zip format). Raises UnsupportedWorkbook on unreadable files.
    """
    try:
        workbook = zipfile.ZipFile(excel_path, "r")
    except Exception as open_error:
        raise UnsupportedWorkbook(_safe_text(open_error))

    try:
        shared_strings = _read_shared_strings(workbook)
        wanted = None
        if sheet_names is not None:
            wanted = set(_safe_text(name) for name in sheet_names)
        sheets = {}
        for name, part_path in _sheet_paths_by_name(workbook):
            if wanted is not None and name not in wanted:
                continue
            sheets[name] = SheetData(
                name, _read_sheet(workbook, part_path, shared_strings)
            )
        return sheets
    finally:
        workbook.close()
