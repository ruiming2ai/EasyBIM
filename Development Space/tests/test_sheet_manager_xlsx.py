import importlib.util
import pathlib
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (
    REPO_ROOT / "EasyBIM.tab" / "Sheet.panel" / "Sheet Manager.pushbutton"
)
LIB_DIR = REPO_ROOT / "lib"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


st = _load_module(
    "sheet_manager_state", COMMAND_DIR / "sheet_manager_state.py")
sx = _load_module(
    "sheet_manager_xlsx", COMMAND_DIR / "sheet_manager_xlsx.py")
excel_workbook = _load_module(
    "excel_workbook_under_test",
    LIB_DIR / "easybim" / "excel_workbook.py")


REVISIONS = [
    {"id": 11, "sequence": 1, "number": "1", "description": "R1"},
    {"id": 22, "sequence": 2, "number": "2", "description": "R2"},
]


def build_columns():
    columns = st.fixed_columns()
    columns.append(st.ColumnSpec(
        "p:Drawn By", st.KIND_SHEET_PARAM, "Drawn By", "p0",
        param_name="Drawn By"))
    columns += st.build_revision_columns(REVISIONS)
    return columns


def build_rows(columns):
    row_a = st.SheetRowBase(101, "A101", "Plan L1")
    row_b = st.SheetRowBase(102, "A102", "Plan L2")
    for row, drawn in ((row_a, "AB"), (row_b, "CD")):
        st.populate_row(row, columns,
                        {"rev:11": row is row_a})
        st.populate_new_column(
            row, [c for c in columns if c.key == "p:Drawn By"][0], drawn)
    return [row_a, row_b]


def export_rows_fixture(columns, rows):
    headers, metadata, data, _ = st.build_export_matrix(columns, rows)
    export_rows = [(1, headers)]
    for pos, cells in enumerate(data):
        export_rows.append((pos + 2, list(cells)))
    metadata_rows = [
        (1, [sx.FORMAT_SIGNATURE, "Model", "ts", sx.FORMAT_VERSION]),
        (2, list(sx.METADATA_HEADERS)),
    ]
    for pos, meta in enumerate(metadata):
        metadata_rows.append((pos + 3, list(meta)))
    return export_rows, metadata_rows


class HelperTests(unittest.TestCase):
    def test_parse_bool_cell(self):
        for text in ("Yes", "y", "TRUE", "1", "x"):
            self.assertTrue(sx.parse_bool_cell(text))
        for text in ("No", "", None, "0", "false"):
            self.assertFalse(sx.parse_bool_cell(text))

    def test_build_export_filename(self):
        self.assertEqual(sx.build_export_filename("Tower.rvt"),
                         "Sheet Manager - Tower.xlsx")
        self.assertEqual(sx.build_export_filename(""),
                         "Sheet Manager Export.xlsx")
        self.assertNotIn(":", sx.build_export_filename("A:B.rvt"))


class PlanImportTests(unittest.TestCase):
    def setUp(self):
        self.columns = build_columns()
        self.rows = build_rows(self.columns)

    def test_unchanged_roundtrip_plans_nothing(self):
        export_rows, metadata_rows = export_rows_fixture(
            self.columns, self.rows)
        plan = sx.plan_import(export_rows, metadata_rows, self.rows,
                              self.columns)
        self.assertTrue(plan.is_empty())
        self.assertEqual(plan.matched_count, 2)
        self.assertEqual(plan.skipped_rows, [])

    def test_id_match_stages_differences(self):
        export_rows, metadata_rows = export_rows_fixture(
            self.columns, self.rows)
        export_rows[1] = (2, ["101", "A101", "Plan EDITED", "ZZ",
                              "No", "Yes"])
        plan = sx.plan_import(export_rows, metadata_rows, self.rows,
                              self.columns)
        edits = sorted((column.key, value)
                       for _, column, value in plan.cell_edits)
        self.assertEqual(edits, [
            ("name", "Plan EDITED"),
            ("p:Drawn By", "ZZ"),
            ("rev:11", False),
            ("rev:22", True),
        ])

    def test_stale_id_rescued_by_number(self):
        export_rows, metadata_rows = export_rows_fixture(
            self.columns, self.rows)
        export_rows[2] = (3, ["999999", "A102", "Renamed", "CD",
                              "No", "No"])
        plan = sx.plan_import(export_rows, metadata_rows, self.rows,
                              self.columns)
        self.assertEqual(plan.matched_count, 2)
        self.assertEqual(
            [(c.key, v) for _, c, v in plan.cell_edits],
            [("name", "Renamed")])

    def test_missing_id_creates_and_duplicates_are_flagged(self):
        export_rows, metadata_rows = export_rows_fixture(
            self.columns, self.rows)
        export_rows.append((4, ["", "A200", "New Sheet", "ZZ",
                                "Yes", "No"]))
        export_rows.append((5, ["", "A101", "Dup", "", "", ""]))
        export_rows.append((6, ["", "A200", "Dup of new", "", "", ""]))
        export_rows.append((7, ["", "", "No number", "", "", ""]))
        plan = sx.plan_import(export_rows, metadata_rows, self.rows,
                              self.columns)
        self.assertEqual(len(plan.creatable), 1)
        created = plan.creatable[0]
        self.assertEqual(created["number"], "A200")
        self.assertEqual(created["name"], "New Sheet")
        self.assertEqual(created["revisions"], set([11]))
        self.assertEqual(created["values"].get("p:Drawn By"), "ZZ")
        reasons = [reason for _, reason in plan.skipped_rows]
        self.assertEqual(len(plan.skipped_rows), 3)
        self.assertTrue(
            any("Duplicate" in reason for reason in reasons))
        self.assertTrue(
            any("No sheet number" in reason for reason in reasons))

    def test_readonly_cells_counted_not_staged(self):
        readonly_col = st.ColumnSpec(
            "p:Locked", st.KIND_SHEET_PARAM, "Locked", "p1",
            param_name="Locked", is_read_only=True)
        columns = self.columns[:5] + [readonly_col] + self.columns[5:]
        rows = build_rows(columns)
        for row in rows:
            st.populate_new_column(row, readonly_col, "orig")
        export_rows, metadata_rows = export_rows_fixture(columns, rows)
        cells = list(export_rows[1][1])
        cells[cells.index("orig")] = "changed"
        export_rows[1] = (2, cells)
        plan = sx.plan_import(export_rows, metadata_rows, rows, columns)
        self.assertEqual(plan.skipped_readonly, 1)
        self.assertEqual(plan.cell_edits, [])

    def test_header_fallback_without_metadata(self):
        export_rows, _ = export_rows_fixture(self.columns, self.rows)
        export_rows[1] = (2, ["101", "A101", "Plan EDITED", "AB",
                              "Yes", "No"])
        plan = sx.plan_import(export_rows, [], self.rows, self.columns)
        self.assertEqual(
            [(c.key, v) for _, c, v in plan.cell_edits],
            [("name", "Plan EDITED")])


@unittest.skipUnless(sx.XLSXWRITER_AVAILABLE,
                     "xlsxwriter not installed")
class RoundTripTests(unittest.TestCase):
    def test_export_then_read_back_plans_nothing(self):
        columns = build_columns()
        rows = build_rows(columns)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(pathlib.Path(temp_dir) / "roundtrip.xlsx")
            count = sx.export_table_to_xlsx(
                "Model.rvt", columns, rows, path, "2026-08-02")
            self.assertEqual(count, 2)
            sheets = excel_workbook.read_workbook_sheets(
                path, [sx.EXPORT_SHEET_NAME, sx.METADATA_SHEET_NAME])
            export_data = sheets[sx.EXPORT_SHEET_NAME]
            metadata = sheets[sx.METADATA_SHEET_NAME]
            plan = sx.plan_import(export_data.rows, metadata.rows,
                                  rows, columns)
            self.assertTrue(plan.is_empty())
            self.assertEqual(plan.matched_count, 2)


if __name__ == "__main__":
    unittest.main()
