# -*- coding: utf-8 -*-
"""Helpers for loading ordered native print sets from visible Excel rows."""

import os
import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile


SUPPORTED_EXTENSIONS = (".xlsx", ".xlsm")
FALLBACK_WARNING = (
    "Excel was not available, so the saved workbook data was read directly. "
    "Filtered rows are skipped when they are saved as hidden in the workbook."
)

_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_REL_NS = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
)


try:
    _TEXT_TYPES = (basestring,)
except NameError:
    _TEXT_TYPES = (str,)


class UnsupportedExcelFile(Exception):
    """Raised when the import file cannot be read by this command."""


class ExcelImportRow(object):
    """Raw visible row imported from Excel."""

    def __init__(self, excel_row, sheet_number, sheet_name):
        self.excel_row = int(excel_row)
        self.row_id = int(excel_row)
        self.sheet_number = _safe_text(sheet_number)
        self.sheet_name = _safe_text(sheet_name)
        self.number = self.sheet_number
        self.name = self.sheet_name


class ExcelReadResult(object):
    def __init__(self, rows, warning=None, reader="ooxml"):
        self.rows = list(rows or [])
        self.warning = warning
        self.reader = reader


class DiscrepancyRow(object):
    """Validation row shown in the missing-number/name tables."""

    def __init__(self, import_row, reason, revit_sheet=None):
        self.source_row = import_row
        self.excel_row = getattr(import_row, "excel_row", 0)
        self.number = getattr(import_row, "sheet_number", "")
        self.name = getattr(import_row, "sheet_name", "")
        self.reason = reason
        self.revit_sheet = revit_sheet


class ExcelPrintSetRow(object):
    """Validated sheet row ready for native print-set saving."""

    def __init__(self, import_row, revit_sheet, index):
        self.source_row = import_row
        self.revit_sheet = revit_sheet
        self.excel_row = getattr(import_row, "excel_row", 0)
        self.index = index
        self.number = _safe_text(getattr(revit_sheet, "SheetNumber", ""))
        self.name = _safe_text(getattr(revit_sheet, "Name", ""))
        self.printable = bool(getattr(revit_sheet, "CanBePrinted", False))
        self.status = "Printable" if self.printable else "Skipped"


class ExcelValidationResult(object):
    def __init__(self, number_discrepancies, name_discrepancies, final_rows,
                 visible_rows, skipped_row_ids):
        self.number_discrepancies = list(number_discrepancies or [])
        self.name_discrepancies = list(name_discrepancies or [])
        self.final_rows = list(final_rows or [])
        self.visible_rows = list(visible_rows or [])
        self.skipped_row_ids = set(skipped_row_ids or [])
        self.can_continue = (
            not self.number_discrepancies and not self.name_discrepancies
        )


class ExcelPrintSetSession(object):
    """Mutable import session that tracks user-skipped discrepancy rows."""

    def __init__(self, rows, model_sheets):
        self.rows = list(rows or [])
        self.model_sheets = list(model_sheets or [])
        self.skipped_row_ids = set()
        self._sheet_by_number = _build_sheet_index(self.model_sheets)

    def validate(self, selected_revision_ids=None):
        selected_revision_ids = set([
            int(x) for x in selected_revision_ids or []
        ])
        visible_rows = []
        for import_row in self.rows:
            if import_row.row_id in self.skipped_row_ids:
                continue
            if not _row_matches_revision_filter(
                    import_row,
                    self._sheet_by_number,
                    selected_revision_ids
            ):
                continue
            visible_rows.append(import_row)

        number_counts = {}
        for import_row in visible_rows:
            number_key = normalize_key(import_row.sheet_number)
            if not number_key:
                continue
            number_counts[number_key] = number_counts.get(number_key, 0) + 1

        number_discrepancies = []
        name_discrepancies = []
        matched_pairs = []

        for import_row in visible_rows:
            number_key = normalize_key(import_row.sheet_number)
            if not number_key:
                number_discrepancies.append(
                    DiscrepancyRow(import_row, "Sheet number is blank.")
                )
                continue
            if number_counts.get(number_key, 0) > 1:
                number_discrepancies.append(
                    DiscrepancyRow(
                        import_row,
                        "Duplicate imported sheet number."
                    )
                )
                continue

            revit_sheet = self._sheet_by_number.get(number_key)
            if revit_sheet is None:
                number_discrepancies.append(
                    DiscrepancyRow(
                        import_row,
                        "Sheet number was not found in the model."
                    )
                )
                continue

            name_key = normalize_key(import_row.sheet_name)
            model_name_key = normalize_key(getattr(revit_sheet, "Name", ""))
            if not name_key:
                name_discrepancies.append(
                    DiscrepancyRow(
                        import_row,
                        "Sheet name is blank.",
                        revit_sheet=revit_sheet
                    )
                )
                continue
            if name_key != model_name_key:
                name_discrepancies.append(
                    DiscrepancyRow(
                        import_row,
                        "Sheet name does not match the model sheet name.",
                        revit_sheet=revit_sheet
                    )
                )
                continue

            matched_pairs.append((import_row, revit_sheet))

        final_rows = [
            ExcelPrintSetRow(import_row, revit_sheet, index + 1)
            for index, (import_row, revit_sheet) in enumerate(matched_pairs)
        ]
        return ExcelValidationResult(
            number_discrepancies,
            name_discrepancies,
            final_rows,
            visible_rows,
            self.skipped_row_ids
        )

    def skip_number_discrepancies(self, discrepancy_rows):
        self._skip_discrepancy_rows(discrepancy_rows)

    def skip_name_discrepancies(self, discrepancy_rows):
        self._skip_discrepancy_rows(discrepancy_rows)

    def _skip_discrepancy_rows(self, discrepancy_rows):
        for discrepancy in discrepancy_rows or []:
            try:
                self.skipped_row_ids.add(int(discrepancy.source_row.row_id))
            except Exception:
                continue


def _safe_text(value):
    if value is None:
        return ""
    if isinstance(value, _TEXT_TYPES):
        return value
    try:
        return str(value)
    except Exception:
        try:
            return unicode(value)
        except Exception:
            return ""


def default_print_set_name_from_path(excel_path):
    return os.path.splitext(os.path.basename(_safe_text(excel_path)))[0]


def normalize_key(value):
    text = _safe_text(value)
    try:
        text = unicodedata.normalize("NFKC", text)
    except Exception:
        pass

    chars = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Cf":
            continue
        if char in (
                u"\u2010", u"\u2011", u"\u2012", u"\u2013",
                u"\u2014", u"\u2015", u"\u2212", u"\ufe58",
                u"\ufe63", u"\uff0d"):
            chars.append("-")
            continue
        if category.startswith("Z") or char in ("\t", "\r", "\n", "\v", "\f"):
            chars.append(" ")
            continue
        chars.append(char)

    collapsed = re.sub(r"\s+", " ", "".join(chars)).strip()
    try:
        return collapsed.casefold()
    except AttributeError:
        return collapsed.lower()


def read_visible_excel_rows(excel_path, prefer_com=True):
    excel_path = _safe_text(excel_path)
    extension = os.path.splitext(excel_path)[1].lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedExcelFile(
            "Only .xlsx and .xlsm files are supported."
        )

    if prefer_com:
        try:
            return _read_visible_rows_with_excel_com(excel_path)
        except Exception:
            pass

    return _read_visible_rows_from_ooxml(excel_path)


def _read_visible_rows_with_excel_com(excel_path):
    if os.name != "nt":
        raise UnsupportedExcelFile("Excel COM is available only on Windows.")

    try:
        import System
        import clr
        clr.AddReference("Microsoft.Office.Interop.Excel")
    except Exception as import_err:
        raise UnsupportedExcelFile(str(import_err))

    excel_app = None
    workbook = None
    try:
        excel_type = System.Type.GetTypeFromProgID("Excel.Application")
        if excel_type is None:
            raise UnsupportedExcelFile("Microsoft Excel is not installed.")
        excel_app = System.Activator.CreateInstance(excel_type)
        excel_app.DisplayAlerts = False
        excel_app.Visible = False
        workbook = excel_app.Workbooks.Open(excel_path, False, True)
        worksheet = _first_visible_com_worksheet(workbook)
        if worksheet is None:
            raise UnsupportedExcelFile("No visible worksheets were found.")

        used_range = worksheet.UsedRange
        first_row = int(used_range.Row)
        first_col = int(used_range.Column)
        last_row = first_row + int(used_range.Rows.Count) - 1
        last_col = first_col + int(used_range.Columns.Count) - 1
        visible_cols = []
        for col_index in range(first_col, last_col + 1):
            try:
                if bool(worksheet.Columns(col_index).Hidden):
                    continue
            except Exception:
                pass
            visible_cols.append(col_index)
            if len(visible_cols) == 2:
                break
        if len(visible_cols) < 2:
            raise UnsupportedExcelFile(
                "The first visible worksheet must have two visible columns."
            )

        rows = []
        for row_index in range(first_row, last_row + 1):
            try:
                if bool(worksheet.Rows(row_index).Hidden):
                    continue
            except Exception:
                pass
            sheet_number = _safe_text(worksheet.Cells(row_index, visible_cols[0]).Text)
            sheet_name = _safe_text(worksheet.Cells(row_index, visible_cols[1]).Text)
            if not normalize_key(sheet_number) and not normalize_key(sheet_name):
                continue
            rows.append(ExcelImportRow(row_index, sheet_number, sheet_name))
        return ExcelReadResult(rows, warning=None, reader="excel")
    finally:
        if workbook is not None:
            try:
                workbook.Close(False)
            except Exception:
                pass
        if excel_app is not None:
            try:
                excel_app.Quit()
            except Exception:
                pass


def _first_visible_com_worksheet(workbook):
    for index in range(1, int(workbook.Worksheets.Count) + 1):
        worksheet = workbook.Worksheets(index)
        try:
            if int(worksheet.Visible) == -1:
                return worksheet
        except Exception:
            continue
    return None


def _read_visible_rows_from_ooxml(excel_path):
    try:
        workbook = zipfile.ZipFile(excel_path, "r")
    except Exception as open_err:
        raise UnsupportedExcelFile(str(open_err))

    try:
        shared_strings = _read_shared_strings(workbook)
        sheet_path = _get_first_visible_sheet_path(workbook)
        rows = _read_sheet_rows_from_ooxml(workbook, sheet_path, shared_strings)
    finally:
        workbook.close()

    return ExcelReadResult(rows, warning=FALLBACK_WARNING, reader="ooxml")


def _read_xml(workbook, name):
    try:
        return ET.fromstring(workbook.read(name))
    except KeyError:
        raise UnsupportedExcelFile("Missing workbook part: {}".format(name))


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


def _get_first_visible_sheet_path(workbook):
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
        raise UnsupportedExcelFile("No worksheets were found.")

    for sheet in sheets_root.findall(_MAIN_NS + "sheet"):
        state = sheet.attrib.get("state", "visible").lower()
        if state in ("hidden", "veryhidden"):
            continue
        rel_id = sheet.attrib.get(_OFFICE_REL_NS + "id")
        target = relationships.get(rel_id)
        if not target:
            continue
        if target.startswith("/"):
            return target.lstrip("/")
        return posixpath.normpath(posixpath.join("xl", target))

    raise UnsupportedExcelFile("No visible worksheets were found.")


def _read_sheet_rows_from_ooxml(workbook, sheet_path, shared_strings):
    sheet_root = _read_xml(workbook, sheet_path)
    hidden_cols = _hidden_column_indexes(sheet_root)
    sheet_data = sheet_root.find(_MAIN_NS + "sheetData")
    if sheet_data is None:
        return []

    row_data = []
    visible_cols = []
    for row_node in sheet_data.findall(_MAIN_NS + "row"):
        if row_node.attrib.get("hidden") == "1":
            continue
        excel_row = _safe_int(row_node.attrib.get("r"), len(row_data) + 1)
        cells = {}
        for cell in row_node.findall(_MAIN_NS + "c"):
            col_index = _column_index_from_ref(cell.attrib.get("r", ""))
            if col_index is None or col_index in hidden_cols:
                continue
            value = _cell_text(cell, shared_strings)
            cells[col_index] = value
            if col_index not in visible_cols:
                visible_cols.append(col_index)
        row_data.append((excel_row, cells))

    visible_cols = sorted(visible_cols)[:2]
    if len(visible_cols) < 2:
        return []

    rows = []
    for excel_row, cells in row_data:
        sheet_number = _safe_text(cells.get(visible_cols[0], ""))
        sheet_name = _safe_text(cells.get(visible_cols[1], ""))
        if not normalize_key(sheet_number) and not normalize_key(sheet_name):
            continue
        rows.append(ExcelImportRow(excel_row, sheet_number, sheet_name))
    return rows


def _hidden_column_indexes(sheet_root):
    hidden_cols = set()
    cols_root = sheet_root.find(_MAIN_NS + "cols")
    if cols_root is None:
        return hidden_cols
    for col in cols_root.findall(_MAIN_NS + "col"):
        if col.attrib.get("hidden") != "1":
            continue
        min_col = _safe_int(col.attrib.get("min"), 0)
        max_col = _safe_int(col.attrib.get("max"), min_col)
        for col_index in range(min_col, max_col + 1):
            hidden_cols.add(col_index)
    return hidden_cols


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


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except Exception:
        return fallback


def _build_sheet_index(sheets):
    sheet_by_number = {}
    for sheet in sheets or []:
        number_key = normalize_key(getattr(sheet, "SheetNumber", ""))
        if number_key and number_key not in sheet_by_number:
            sheet_by_number[number_key] = sheet
    return sheet_by_number


def _row_matches_revision_filter(import_row, sheet_by_number,
                                 selected_revision_ids):
    if not selected_revision_ids:
        return True
    sheet = sheet_by_number.get(normalize_key(import_row.sheet_number))
    if sheet is None:
        return False
    return _sheet_has_revision(sheet, selected_revision_ids)


def _sheet_has_revision(sheet, selected_revision_ids):
    if not selected_revision_ids:
        return True
    try:
        for rev_id in sheet.GetAllRevisionIds():
            rev_int = _get_element_id_value(rev_id)
            if rev_int is not None and int(rev_int) in selected_revision_ids:
                return True
    except Exception:
        return False
    return False


def _get_element_id_value(element_id):
    if element_id is None:
        return None
    for attr in ("IntegerValue", "Value"):
        try:
            value = getattr(element_id, attr)
            if value is not None:
                return int(value)
        except Exception:
            continue
    try:
        return int(element_id)
    except Exception:
        return None
