"""The Family Types Excel export, and the workbook it hands back to import.

The interesting claim is the header cell: parameter name on line 1, parameter
ElementId on line 2, in one cell.  These write a real workbook and read it
back through ``easybim.excel_workbook`` - the same reader the command uses -
so the round trip is checked end to end without Revit or Excel.
"""

import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel"
               / "Family Types.pushbutton")
LIB_DIR = REPO_ROOT / "lib" / "easybim"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


st = _load_module("family_types_state", COMMAND_DIR / "family_types_state.py")
ftxlsx = _load_module("family_types_xlsx",
                      COMMAND_DIR / "family_types_xlsx.py")
excel_workbook = _load_module("excel_workbook", LIB_DIR / "excel_workbook.py")


PARAM_INFOS = [
    {"name": "Width", "param_id": 101, "storage_type": "Double",
     "spec_label": "length"},
    {"name": "Comments", "param_id": 102, "storage_type": "String"},
    {"name": "Load Bearing", "param_id": 103, "storage_type": "Integer",
     "is_instance": True, "is_yes_no": True},
    {"name": "Volume", "param_id": 104, "storage_type": "Double",
     "is_determined_by_formula": True, "formula": "Width * 2"},
]


def build_grid():
    columns = st.fixed_columns() + st.build_parameter_columns(PARAM_INFOS)
    rows = []
    for name, width, comment in (("600mm", "600 mm", "narrow"),
                                 ("900mm", "900 mm", "wide")):
        row = st.TypeRowBase(name)
        st.populate_row(row, columns, {
            "p:Width": width,
            "p:Comments": comment,
            "p:Load Bearing": "Yes",
            "p:Volume": "1.2",
        })
        rows.append(row)
    return columns, rows


class FilenameTests(unittest.TestCase):
    def test_the_family_name_becomes_the_workbook_name(self):
        self.assertEqual(u"Family Types - Desk.xlsx",
                         ftxlsx.build_export_filename(u"Desk"))

    def test_an_rfa_suffix_is_dropped(self):
        self.assertEqual(u"Family Types - Desk.xlsx",
                         ftxlsx.build_export_filename(u"Desk.rfa"))

    def test_characters_windows_refuses_are_scrubbed(self):
        name = ftxlsx.build_export_filename(u'Desk/Chair: "big"?')
        for bad in '\\/:*?"<>|':
            self.assertNotIn(bad, name)

    def test_a_nameless_family_still_gets_a_filename(self):
        self.assertEqual(u"Family Types Export.xlsx",
                         ftxlsx.build_export_filename(u"  "))


@unittest.skipUnless(ftxlsx.XLSXWRITER_AVAILABLE,
                     "xlsxwriter is not installed")
class WorkbookRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path = os.path.join(self.folder, "family types.xlsx")
        self.columns, self.rows = build_grid()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def _write(self):
        return ftxlsx.export_table_to_xlsx(
            u"Desk", u"Project", self.columns, self.rows, self.path,
            u"2026-08-19 12:00:00")

    def _read(self):
        return excel_workbook.read_workbook_sheets(
            self.path, [ftxlsx.EXPORT_SHEET_NAME,
                        ftxlsx.METADATA_SHEET_NAME])

    def test_every_type_is_written_as_a_row(self):
        self.assertEqual(2, self._write())
        rows = self._read()[ftxlsx.EXPORT_SHEET_NAME].rows
        self.assertEqual(3, len(rows))          # header + two types
        self.assertEqual(u"600mm", rows[1][1][0])
        self.assertEqual(u"900mm", rows[2][1][0])

    def test_the_header_cell_holds_the_name_over_the_element_id(self):
        self._write()
        header = self._read()[ftxlsx.EXPORT_SHEET_NAME].rows[0][1]
        self.assertEqual(u"Family Type", header[0])
        self.assertIn(u"Comments\n102", header)
        self.assertIn(u"Width\n101", header)

    def test_an_instance_parameter_keeps_its_default_marker(self):
        self._write()
        header = self._read()[ftxlsx.EXPORT_SHEET_NAME].rows[0][1]
        self.assertIn(u"Load Bearing (default)\n103", header)

    def test_the_hidden_metadata_sheet_describes_every_column(self):
        self._write()
        rows = self._read()[ftxlsx.METADATA_SHEET_NAME].rows
        self.assertEqual(ftxlsx.FORMAT_SIGNATURE, rows[0][1][0])
        self.assertEqual(u"Desk", rows[0][1][1])
        self.assertEqual(st.METADATA_HEADERS, rows[1][1])
        self.assertEqual(len(PARAM_INFOS), len(rows) - 2)

    def test_the_metadata_records_which_columns_are_locked(self):
        self._write()
        rows = self._read()[ftxlsx.METADATA_SHEET_NAME].rows
        by_name = dict((cells[2], cells) for _, cells in rows[2:])
        read_only_at = st.METADATA_HEADERS.index("IsReadOnly")
        lock_at = st.METADATA_HEADERS.index("LockReason")
        self.assertEqual(u"Yes", by_name[u"Volume"][read_only_at])
        self.assertEqual(st.LOCK_FORMULA, by_name[u"Volume"][lock_at])
        self.assertEqual(u"No", by_name[u"Comments"][read_only_at])

    def test_staged_edits_are_what_gets_exported(self):
        # The export is WYSIWYG: it writes the table as it looks, not as the
        # family last read, so an export after edits round-trips them.
        width = [c for c in st.param_columns(self.columns)
                 if c.param_name == "Width"][0]
        st.apply_cell_edit(self.rows[0], width, u"650 mm")
        self._write()
        rows = self._read()[ftxlsx.EXPORT_SHEET_NAME].rows
        self.assertIn(u"650 mm", rows[1][1])

    def test_a_deleted_row_is_left_out_of_the_export(self):
        st.toggle_delete(self.rows[1], self.rows)
        self.assertEqual(1, self._write())

    def test_an_untouched_export_reimports_as_entirely_unchanged(self):
        self._write()
        sheets = self._read()
        plan = st.plan_import(
            sheets[ftxlsx.EXPORT_SHEET_NAME].rows,
            sheets[ftxlsx.METADATA_SHEET_NAME].rows,
            self.rows, self.columns)
        self.assertEqual([], plan.unmatched_columns)
        self.assertEqual(0, plan.skipped_locked)
        for entry in plan.entries:
            self.assertEqual(st.IMPORT_UNCHANGED, entry.status)

    def test_an_edited_export_reimports_as_exactly_that_edit(self):
        self._write()
        sheets = self._read()
        export_rows = list(sheets[ftxlsx.EXPORT_SHEET_NAME].rows)
        header = export_rows[0][1]
        comments_at = header.index(u"Comments\n102")
        excel_row, cells = export_rows[1]
        cells = list(cells)
        cells[comments_at] = u"edited in Excel"
        export_rows[1] = (excel_row, cells)

        plan = st.plan_import(export_rows,
                              sheets[ftxlsx.METADATA_SHEET_NAME].rows,
                              self.rows, self.columns)
        changed = [e for e in plan.entries if e.status == st.IMPORT_CHANGED]
        self.assertEqual(1, len(changed))
        self.assertEqual(1, len(changed[0].edits))
        self.assertEqual(u"edited in Excel", changed[0].edits[0][1])

        st.apply_import_selection(plan, [changed[0].key], self.rows,
                                  self.columns, st.TypeRowBase)
        changes = st.compute_staged_changes(self.rows, self.columns)
        self.assertEqual(1, len(changes.value_edits))

    def test_a_family_with_no_types_still_writes_a_readable_workbook(self):
        self.assertEqual(0, ftxlsx.export_table_to_xlsx(
            u"Desk", u"Project", self.columns, [], self.path))
        rows = self._read()[ftxlsx.EXPORT_SHEET_NAME].rows
        self.assertEqual(1, len(rows))

    def test_a_workbook_the_tool_did_not_write_is_recognisable(self):
        # The command refuses a workbook with no export sheet rather than
        # guessing at its shape; this pins the name that check keys off.
        self.assertEqual("FamilyTypes", ftxlsx.EXPORT_SHEET_NAME)
        self.assertEqual("_metadata", ftxlsx.METADATA_SHEET_NAME)


if __name__ == "__main__":
    unittest.main()
