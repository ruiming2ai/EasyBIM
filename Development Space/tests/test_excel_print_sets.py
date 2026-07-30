import importlib.util
import os
import pathlib
import shutil
import tempfile
import unittest
import zipfile


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "excel_print_sets.py"
)

PRINT_SETS_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "print_sets.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "excel_print_sets",
        str(MODULE_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_print_sets_module():
    spec = importlib.util.spec_from_file_location(
        "print_sets",
        str(PRINT_SETS_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeSheet(object):
    def __init__(self, number, name, revision_ids=None, printable=True):
        self.SheetNumber = number
        self.Name = name
        self._revision_ids = list(revision_ids or [])
        self.CanBePrinted = printable

    def GetAllRevisionIds(self):
        return list(self._revision_ids)


class _FakeElementId(object):
    def __init__(self, value):
        self.IntegerValue = value


def _write_test_xlsx(path):
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Visible" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/sharedStrings.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="8" uniqueCount="8">'
            "<si><t>A001</t></si><si><t>ignored</t></si><si><t>First</t></si>"
            "<si><t>A002</t></si><si><t>hidden</t></si><si><t>Hidden Row</t></si>"
            "<si><t>A003</t></si><si><t>Third</t></si>"
            "</sst>"
        ),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<cols><col min="2" max="2" hidden="1"/></cols>'
            "<sheetData>"
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>'
            '<row r="2" hidden="1"><c r="A2" t="s"><v>3</v></c><c r="B2" t="s"><v>4</v></c><c r="C2" t="s"><v>5</v></c></row>'
            '<row r="3"><c r="A3" t="s"><v>6</v></c><c r="C3" t="s"><v>7</v></c></row>'
            "</sheetData>"
            "</worksheet>"
        ),
    }
    with zipfile.ZipFile(path, "w") as workbook:
        for name, data in files.items():
            workbook.writestr(name, data)


class ExcelPrintSetsTests(unittest.TestCase):
    def test_default_print_set_name_uses_excel_file_basename(self):
        module = _load_module()

        name = module.default_print_set_name_from_path(
            os.path.join("/tmp", "A0 Sheet Issue Set.xlsm")
        )

        self.assertEqual(name, "A0 Sheet Issue Set")

    def test_normalize_key_handles_excel_spacing_and_dash_issues(self):
        module = _load_module()

        left = module.normalize_key(
            " A101\u00a0  Mechanical\u200b\u2013Plan "
        )
        right = module.normalize_key("a101 mechanical-plan")

        self.assertEqual(left, right)

    def test_validate_rows_keeps_final_rows_in_excel_order(self):
        module = _load_module()
        rows = [
            module.ExcelImportRow(1, "A003", "Third Floor"),
            module.ExcelImportRow(2, " A001\u00a0", "First  Floor"),
            module.ExcelImportRow(3, "A002", "Second \u2013 Floor"),
        ]
        sheets = [
            _FakeSheet("A001", "First Floor"),
            _FakeSheet("A002", "Second - Floor"),
            _FakeSheet("A003", "Third Floor"),
        ]

        result = module.ExcelPrintSetSession(rows, sheets).validate()

        self.assertEqual(result.number_discrepancies, [])
        self.assertEqual(result.name_discrepancies, [])
        self.assertEqual(
            [row.number for row in result.final_rows],
            ["A003", "A001", "A002"]
        )
        self.assertEqual([row.index for row in result.final_rows], [1, 2, 3])

    def test_discrepancies_block_next_until_skipped(self):
        module = _load_module()
        rows = [
            module.ExcelImportRow(1, "A001", "First"),
            module.ExcelImportRow(2, "A999", "Missing"),
            module.ExcelImportRow(3, "A002", "Wrong Name"),
        ]
        sheets = [
            _FakeSheet("A001", "First"),
            _FakeSheet("A002", "Second"),
        ]
        session = module.ExcelPrintSetSession(rows, sheets)

        result = session.validate()
        self.assertFalse(result.can_continue)
        self.assertEqual(
            [row.reason for row in result.number_discrepancies],
            ["Sheet number was not found in the model."]
        )
        self.assertEqual(
            [row.reason for row in result.name_discrepancies],
            ["Sheet name does not match the model sheet name."]
        )

        session.skip_number_discrepancies(result.number_discrepancies)
        result = session.validate()
        self.assertFalse(result.can_continue)
        session.skip_name_discrepancies(result.name_discrepancies)
        result = session.validate()

        self.assertTrue(result.can_continue)
        self.assertEqual([row.number for row in result.final_rows], ["A001"])

    def test_duplicate_excel_sheet_numbers_are_number_discrepancies(self):
        module = _load_module()
        rows = [
            module.ExcelImportRow(1, "A001", "First"),
            module.ExcelImportRow(2, "A001 ", "First"),
            module.ExcelImportRow(3, "A002", "Second"),
        ]
        sheets = [
            _FakeSheet("A001", "First"),
            _FakeSheet("A002", "Second"),
        ]

        result = module.ExcelPrintSetSession(rows, sheets).validate()

        self.assertFalse(result.can_continue)
        self.assertEqual(len(result.number_discrepancies), 2)
        self.assertEqual(
            set(row.excel_row for row in result.number_discrepancies),
            set([1, 2])
        )
        self.assertEqual(
            set(row.reason for row in result.number_discrepancies),
            set(["Duplicate imported sheet number."])
        )

    def test_revision_filter_hides_unresolved_rows_and_preserves_matching_order(self):
        module = _load_module()
        rows = [
            module.ExcelImportRow(1, "A001", "First"),
            module.ExcelImportRow(2, "A999", "Missing"),
            module.ExcelImportRow(3, "A002", "Second"),
        ]
        sheets = [
            _FakeSheet("A001", "First", revision_ids=[_FakeElementId(10)]),
            _FakeSheet("A002", "Second", revision_ids=[_FakeElementId(20)]),
        ]

        result = module.ExcelPrintSetSession(rows, sheets).validate([20])

        self.assertTrue(result.can_continue)
        self.assertEqual(result.number_discrepancies, [])
        self.assertEqual([row.number for row in result.final_rows], ["A002"])

    def test_xlsx_reader_uses_first_two_visible_columns_and_keeps_first_row(self):
        module = _load_module()
        temp_dir = tempfile.mkdtemp()
        try:
            workbook_path = os.path.join(temp_dir, "Visible Rows.xlsx")
            _write_test_xlsx(workbook_path)

            result = module.read_visible_excel_rows(workbook_path)

            self.assertEqual(result.reader, "ooxml")
            self.assertEqual(
                [(row.excel_row, row.sheet_number, row.sheet_name)
                 for row in result.rows],
                [(1, "A001", "First"), (3, "A003", "Third")]
            )
            self.assertIsNotNone(result.warning)
        finally:
            shutil.rmtree(temp_dir)

    def test_collect_print_set_names_sorts_existing_native_sets(self):
        module = _load_print_sets_module()

        class _FakeFramework(object):
            @staticmethod
            def get_type(value):
                return value

        class _FakeViewSheetSet(object):
            pass

        class _Collector(object):
            def __init__(self, doc):
                self._doc = doc

            def OfClass(self, cls):
                return self

            def WhereElementIsNotElementType(self):
                return self

            def ToElements(self):
                return list(self._doc.elements)

        class _FakeDB(object):
            ViewSheetSet = _FakeViewSheetSet
            FilteredElementCollector = _Collector

        class _Set(object):
            def __init__(self, name):
                self.Name = name

        class _Doc(object):
            elements = [_Set("Z Set"), _Set("A Set"), _Set("")]

        names = module.collect_print_set_names(
            _Doc(),
            _FakeDB,
            _FakeFramework
        )

        self.assertEqual(names, ["A Set", "Z Set"])


if __name__ == "__main__":
    unittest.main()
