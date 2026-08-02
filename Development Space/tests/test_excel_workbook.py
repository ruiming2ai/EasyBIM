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
    / "excel_workbook.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "excel_workbook",
        str(MODULE_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_test_workbook(path):
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
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
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/>'
            '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            '<sheet name="Export" sheetId="1" r:id="rId1"/>'
            '<sheet name="_metadata" sheetId="2" state="hidden" r:id="rId2"/>'
            '<sheet name="_worksets" sheetId="3" state="veryHidden" r:id="rId3"/>'
            "</sheets>"
            "</workbook>"
        ),
        "xl/sharedStrings.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3" uniqueCount="3">'
            "<si><t>ElementId</t></si>"
            "<si><t>Type </t><t>Mark</t></si>"
            "<si><t>Width [mm]</t></si>"
            "</sst>"
        ),
        # Export sheet: header row, one full row, one sparse row (gap in B),
        # one row with inline string and boolean.
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            '<row r="1">'
            '<c r="A1" t="s"><v>0</v></c>'
            '<c r="B1" t="s"><v>1</v></c>'
            '<c r="C1" t="s"><v>2</v></c>'
            "</row>"
            '<row r="2">'
            '<c r="A2"><v>1001</v></c>'
            '<c r="B2" t="inlineStr"><is><t>TM-01</t></is></c>'
            '<c r="C2"><v>2.5</v></c>'
            "</row>"
            '<row r="4">'
            '<c r="A4"><v>1002</v></c>'
            '<c r="C4" t="b"><v>1</v></c>'
            "</row>"
            "</sheetData>"
            "</worksheet>"
        ),
        # Hidden metadata sheet.
        "xl/worksheets/sheet2.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            '<row r="1"><c r="A1" t="inlineStr"><is><t>Parameter Name</t></is></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>Width</t></is>'
            "</c></row>"
            "</sheetData>"
            "</worksheet>"
        ),
        # veryHidden sheet.
        "xl/worksheets/sheet3.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            '<row r="1"><c r="A1" t="inlineStr"><is><t>Workset 1</t></is></c></row>'
            "</sheetData>"
            "</worksheet>"
        ),
    }
    with zipfile.ZipFile(path, "w") as workbook:
        for name, content in files.items():
            workbook.writestr(name, content)


class ExcelWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.workbook_path = os.path.join(self.tempdir, "export.xlsx")
        _write_test_workbook(self.workbook_path)

    def test_reads_requested_sheets_including_hidden_by_name(self):
        sheets = self.module.read_workbook_sheets(
            self.workbook_path, ["Export", "_metadata"]
        )

        self.assertEqual(sorted(sheets.keys()), ["Export", "_metadata"])
        self.assertEqual(
            sheets["_metadata"].rows,
            [(1, ["Parameter Name"]), (2, ["Width"])],
        )

    def test_reads_all_sheets_when_names_omitted(self):
        sheets = self.module.read_workbook_sheets(self.workbook_path)

        self.assertEqual(
            sorted(sheets.keys()), ["Export", "_metadata", "_worksets"]
        )
        self.assertEqual(sheets["_worksets"].rows, [(1, ["Workset 1"])])

    def test_export_rows_are_dense_and_keep_excel_row_numbers(self):
        sheets = self.module.read_workbook_sheets(self.workbook_path, ["Export"])
        rows = sheets["Export"].rows

        self.assertEqual(
            rows[0], (1, ["ElementId", "Type Mark", "Width [mm]"])
        )
        self.assertEqual(rows[1], (2, ["1001", "TM-01", "2.5"]))
        self.assertEqual(rows[2], (4, ["1002", "", "TRUE"]))

    def test_xlsm_extension_is_accepted(self):
        xlsm_path = os.path.join(self.tempdir, "export.xlsm")
        shutil.copyfile(self.workbook_path, xlsm_path)

        sheets = self.module.read_workbook_sheets(xlsm_path, ["Export"])

        self.assertIn("Export", sheets)

    def test_missing_requested_sheet_is_absent_not_error(self):
        sheets = self.module.read_workbook_sheets(
            self.workbook_path, ["Export", "NoSuchSheet"]
        )

        self.assertEqual(sorted(sheets.keys()), ["Export"])

    def test_non_zip_file_raises_unsupported(self):
        bad_path = os.path.join(self.tempdir, "not_excel.xlsx")
        with open(bad_path, "w") as handle:
            handle.write("not a workbook")

        with self.assertRaises(self.module.UnsupportedWorkbook):
            self.module.read_workbook_sheets(bad_path)

    def test_missing_file_raises_unsupported(self):
        with self.assertRaises(self.module.UnsupportedWorkbook):
            self.module.read_workbook_sheets(
                os.path.join(self.tempdir, "absent.xlsx")
            )


if __name__ == "__main__":
    unittest.main()
