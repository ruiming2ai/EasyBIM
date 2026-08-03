import ast
import importlib.util
import pathlib
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Excel.pushbutton"
)
STATE_MODULE_PATH = COMMAND_DIR / "import_excel_state.py"
REVIT_MODULE_PATH = COMMAND_DIR / "import_excel_revit.py"
UI_MODULE_PATH = COMMAND_DIR / "import_excel_ui.py"
SCRIPT_MODULE_PATH = COMMAND_DIR / "script.py"
PREVIEW_XAML_PATH = COMMAND_DIR / "ImportPreviewWindow.xaml"
CONFLICT_XAML_PATH = COMMAND_DIR / "ImportConflictWindow.xaml"


def _load_state_module():
    spec = importlib.util.spec_from_file_location(
        "import_excel_state",
        str(STATE_MODULE_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImportExcelStateTests(unittest.TestCase):
    def test_strip_unit_postfix(self):
        module = _load_state_module()

        self.assertEqual(module.strip_unit_postfix("Width [mm]"), "Width")
        self.assertEqual(module.strip_unit_postfix("Area [m^2]"), "Area")
        self.assertEqual(module.strip_unit_postfix("Type Mark"), "Type Mark")
        self.assertEqual(module.strip_unit_postfix("  Comments  "), "Comments")

    def test_is_sentinel(self):
        module = _load_state_module()

        self.assertTrue(module.is_sentinel("<does not exist>"))
        self.assertTrue(module.is_sentinel(" <unsupported> "))
        self.assertTrue(module.is_sentinel("<Error>"))
        self.assertFalse(module.is_sentinel("<other>"))
        self.assertFalse(module.is_sentinel(""))

    def test_parse_yesno_is_strict(self):
        module = _load_state_module()

        self.assertIs(module.parse_yesno("Yes"), True)
        self.assertIs(module.parse_yesno("1"), True)
        self.assertIs(module.parse_yesno("TRUE"), True)
        self.assertIs(module.parse_yesno("no"), False)
        self.assertIs(module.parse_yesno("0"), False)
        self.assertIs(module.parse_yesno("False"), False)
        self.assertIsNone(module.parse_yesno("maybe"))
        self.assertIsNone(module.parse_yesno(""))

    def test_canonical_value_merges_numeric_only_when_numeric(self):
        module = _load_state_module()

        self.assertEqual(
            module.canonical_value("2.50", True),
            module.canonical_value("2.5", True),
        )
        self.assertNotEqual(
            module.canonical_value("010", False),
            module.canonical_value("10", False),
        )

    def test_parse_metadata_rows_reads_new_and_legacy_shapes(self):
        module = _load_state_module()

        rows = [
            (1, ["Parameter Name", "ForgeTypeId", "UnitTypeId",
                 "SymbolTypeId", "IsScheduleOverride", "IsTypeParameter"]),
            (2, ["Width", "spec:length", "unit:mm", "sym:mm", "Yes", "Yes"]),
            (3, ["Comments", "spec:text", "", "", "No", "No"]),
            (4, ["Mark", "spec:text", "", "", "No"]),
        ]

        metadata = module.parse_metadata_rows(rows)

        self.assertIs(metadata["Width"].is_type, True)
        self.assertEqual(metadata["Width"].unit_type_id, "unit:mm")
        self.assertTrue(metadata["Width"].is_schedule_override)
        self.assertIs(metadata["Comments"].is_type, False)
        self.assertIsNone(metadata["Mark"].is_type)

    def test_classify_columns_requires_elementid_and_skips_helpers(self):
        module = _load_state_module()
        metadata = {"Width": module.MetaEntry(is_type=True)}

        id_col, columns, ignored = module.classify_columns(
            ["ElementId", "Width [mm]", "Comments", "_EBIM_TypeId"],
            metadata,
        )

        self.assertEqual(id_col, 0)
        self.assertEqual(
            [(spec.col_index, spec.param_name) for spec in columns],
            [(1, "Width"), (2, "Comments")],
        )
        self.assertIs(columns[0].meta.is_type, True)
        self.assertIsNone(columns[1].meta)
        self.assertEqual(ignored, ["_EBIM_TypeId"])

        with self.assertRaises(ValueError):
            module.classify_columns(["Id", "Width"], {})

    def test_element_id_column_is_found_wherever_it_sits(self):
        module = _load_state_module()

        # Hiding a column keeps it in the file, but a user can also insert a
        # column before it; either way the id column must still be found.
        self.assertEqual(
            module.find_element_id_column(["Notes", "ElementId", "Width"]), 1
        )
        self.assertIsNone(module.find_element_id_column(["Notes", "Width"]))

        id_col, columns, _ = module.classify_columns(
            ["Notes", "ElementId", "Width"], {}
        )

        self.assertEqual(id_col, 1)
        self.assertEqual(
            [(spec.col_index, spec.param_name) for spec in columns],
            [(0, "Notes"), (2, "Width")],
        )

    def test_parse_export_rows_honours_a_moved_id_column(self):
        module = _load_state_module()
        columns = [module.ColumnSpec(0, "Notes", "Notes")]
        rows = [
            (1, ["Notes", "ElementId"]),
            (2, ["hello", "1001"]),
        ]

        records, bad_rows = module.parse_export_rows(
            rows, columns, id_col_index=1
        )

        self.assertEqual(bad_rows, [])
        self.assertEqual(records[0].element_id_value, 1001)
        self.assertEqual(records[0].values, {"Notes": "hello"})

    def test_parse_export_rows_matches_columns_and_flags_bad_ids(self):
        module = _load_state_module()
        columns = [
            module.ColumnSpec(1, "Width [mm]", "Width"),
            module.ColumnSpec(2, "Comments", "Comments"),
        ]
        rows = [
            (1, ["ElementId", "Width [mm]", "Comments"]),
            (2, ["1001", "2.5", "note"]),
            (3, ["1002.0", "3.0"]),
            (5, ["abc", "4.0", "x"]),
            (6, ["", "5.0", "y"]),
            (7, ["", "", ""]),
        ]

        records, bad_rows = module.parse_export_rows(rows, columns)

        self.assertEqual(
            [(r.excel_row, r.element_id_value) for r in records],
            [(2, 1001), (3, 1002)],
        )
        self.assertEqual(records[0].values, {"Width": "2.5", "Comments": "note"})
        self.assertEqual(records[1].values, {"Width": "3.0", "Comments": ""})
        self.assertEqual(bad_rows, [5, 6])

    def test_detect_type_conflicts_reports_divergent_values(self):
        module = _load_state_module()
        records = [
            module.RowRecord(2, 1, {"Width": "2.5"}),
            module.RowRecord(3, 2, {"Width": "2.50"}),
            module.RowRecord(4, 3, {"Width": "3.0"}),
            module.RowRecord(5, 4, {"Width": "9.9"}),
        ]
        type_key_by_row = {2: 100, 3: 100, 4: 100, 5: 200}
        type_names = {100: "Door 900", 200: "Door 750"}

        conflicts, agreed = module.detect_type_conflicts(
            records, ["Width"], type_key_by_row, type_names
        )

        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict.type_name, "Door 900")
        self.assertEqual(conflict.param_name, "Width")
        self.assertEqual(
            conflict.values, [("2.5", [2, 3]), ("3.0", [4])]
        )
        self.assertEqual(agreed, {(200, "Width"): ("9.9", [5])})

    def test_detect_type_conflicts_ignores_sentinel_and_typeless(self):
        module = _load_state_module()
        records = [
            module.RowRecord(2, 1, {"Width": "5"}),
            module.RowRecord(3, 2, {"Width": "<does not exist>"}),
            module.RowRecord(4, 3, {"Width": "7"}),
        ]
        type_key_by_row = {2: 100, 3: 100}

        conflicts, agreed = module.detect_type_conflicts(
            records, ["Width"], type_key_by_row, {100: "T"}
        )

        self.assertEqual(conflicts, [])
        self.assertEqual(agreed, {(100, "Width"): ("5", [2])})

    def test_blank_now_conflicts_with_a_value_on_the_same_type(self):
        module = _load_state_module()
        records = [
            module.RowRecord(2, 1, {"Width": "5"}),
            module.RowRecord(3, 2, {"Width": ""}),
        ]

        conflicts, agreed = module.detect_type_conflicts(
            records, ["Width"], {2: 100, 3: 100}, {100: "Door 900"}
        )

        # One row says "set 5", the other says "clear it" - ambiguous.
        self.assertEqual(agreed, {})
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(
            conflicts[0].values,
            [("(blank)", [3]), ("5", [2])],
        )

    def test_all_blank_type_group_agrees_on_clearing(self):
        module = _load_state_module()
        records = [
            module.RowRecord(2, 1, {"Width": ""}),
            module.RowRecord(3, 2, {"Width": ""}),
        ]

        conflicts, agreed = module.detect_type_conflicts(
            records, ["Width"], {2: 100, 3: 100}, {100: "T"}
        )

        self.assertEqual(conflicts, [])
        self.assertEqual(agreed, {(100, "Width"): ("", [2, 3])})

    def test_blank_does_not_break_numeric_canonicalisation(self):
        module = _load_state_module()
        records = [
            module.RowRecord(2, 1, {"Width": "2.5"}),
            module.RowRecord(3, 2, {"Width": "2.50"}),
        ]

        conflicts, agreed = module.detect_type_conflicts(
            records, ["Width"], {2: 100, 3: 100}, {100: "T"}
        )

        self.assertEqual(conflicts, [])
        self.assertEqual(agreed, {(100, "Width"): ("2.5", [2, 3])})

    def test_rows_are_matched_by_element_id_not_by_position(self):
        module = _load_state_module()
        columns = [
            module.ColumnSpec(1, "Mark", "Mark"),
            module.ColumnSpec(2, "Comments", "Comments"),
        ]
        header = (1, ["ElementId", "Mark", "Comments"])
        in_order = [
            header,
            (2, ["1001", "A", "first"]),
            (3, ["1002", "B", "second"]),
            (4, ["1003", "C", "third"]),
        ]
        # Same workbook after the user sorted the sheet: the rows travel
        # with their ids, so every id must keep its own values.
        shuffled = [header, in_order[3], in_order[1], in_order[2]]

        def by_id(rows):
            records, bad = module.parse_export_rows(rows, columns)
            self.assertEqual(bad, [])
            return dict(
                (r.element_id_value, r.values) for r in records
            )

        self.assertEqual(by_id(in_order), by_id(shuffled))
        self.assertEqual(
            by_id(shuffled)[1003], {"Mark": "C", "Comments": "third"}
        )

    def test_columns_are_matched_by_header_not_by_position(self):
        module = _load_state_module()

        _, columns, _ = module.classify_columns(
            ["Comments", "_EBIM_TypeId", "ElementId", "Mark"], {}
        )

        self.assertEqual(
            sorted((s.param_name, s.col_index) for s in columns),
            [("Comments", 0), ("Mark", 3)],
        )

    def test_type_conflict_results_do_not_depend_on_row_order(self):
        module = _load_state_module()

        def analyse(records):
            return module.detect_type_conflicts(
                records, ["Width"],
                {2: 100, 3: 100, 4: 200, 5: 200},
                {100: "A", 200: "B"},
            )

        rows = [
            module.RowRecord(2, 1, {"Width": "2.5"}),
            module.RowRecord(3, 2, {"Width": "2.50"}),
            module.RowRecord(4, 3, {"Width": "9"}),
            module.RowRecord(5, 4, {"Width": "8"}),
        ]
        reversed_rows = list(reversed(rows))

        conflicts_a, agreed_a = analyse(rows)
        conflicts_b, agreed_b = analyse(reversed_rows)

        # "2.5" and "2.50" agree; the literal written is the one from the
        # lowest Excel row either way, not whichever row came first.
        self.assertEqual(agreed_a, agreed_b)
        self.assertEqual(agreed_a[(100, "Width")], ("2.5", [2, 3]))
        self.assertEqual(
            [(c.type_name, c.param_name, c.values) for c in conflicts_a],
            [(c.type_name, c.param_name, c.values) for c in conflicts_b],
        )

    def test_detect_type_conflicts_multiple_params_same_type(self):
        module = _load_state_module()
        records = [
            module.RowRecord(2, 1, {"Width": "1", "Height": "a"}),
            module.RowRecord(3, 2, {"Width": "2", "Height": "b"}),
        ]
        type_key_by_row = {2: 100, 3: 100}

        conflicts, _ = module.detect_type_conflicts(
            records, ["Width", "Height"], type_key_by_row, {100: "T"}
        )

        self.assertEqual(
            [(c.param_name,) for c in conflicts],
            [("Height",), ("Width",)],
        )

    def test_split_records_by_scope_partitions_by_membership(self):
        module = _load_state_module()
        records = [
            module.RowRecord(2, 1001, {}),
            module.RowRecord(3, 1002, {}),
            module.RowRecord(4, 9999, {}),
        ]

        in_scope, out_rows = module.split_records_by_scope(
            records, {1001, 1002}
        )

        self.assertEqual([r.excel_row for r in in_scope], [2, 3])
        self.assertEqual(out_rows, [4])

    def test_split_records_by_scope_empty_membership_excludes_all(self):
        module = _load_state_module()
        records = [module.RowRecord(2, 1001, {})]

        in_scope, out_rows = module.split_records_by_scope(records, set())

        self.assertEqual(in_scope, [])
        self.assertEqual(out_rows, [2])
        self.assertEqual(module.split_records_by_scope([], {1}), ([], []))

    def test_conflict_report_lines_format(self):
        module = _load_state_module()
        conflict = module.TypeConflict(
            100, "Door 900", "Width", [("2.5", [4, 9]), ("3.0", [12])]
        )

        lines = module.build_conflict_report_lines([conflict])

        self.assertEqual(
            lines,
            ["Type 'Door 900' - parameter 'Width': "
             '"2.5" (rows 4, 9), "3.0" (row 12)'],
        )

    def test_preview_and_summary_builders_cap_lists(self):
        module = _load_state_module()
        many = ["line {}".format(i) for i in range(module.SUMMARY_DISPLAY_LIMIT + 3)]

        preview = module.build_preview_lines(8, 10, 5, 2, 6, many)
        summary = module.build_import_summary_text(5, 2, many, ["skip"], "C:/f.xlsx")

        self.assertIn("Rows matched to elements: 8 of 10", preview)
        self.assertIn("Type parameters to write: 2 (covering 6 row(s))", preview)
        self.assertIn("...and 3 more.", preview)
        self.assertIn("Instance cells written: 5", summary)
        self.assertIn("...and 3 more.", summary)
        self.assertIn("C:/f.xlsx", summary)

    def test_preview_and_summary_report_cleared_values(self):
        module = _load_state_module()

        preview = "\n".join(module.build_preview_lines(
            4, 4, 5, 1, 2, [], instance_clear_count=3, type_clear_count=1
        ))
        summary = module.build_import_summary_text(
            5, 1, [], [], "C:/f.xlsx", cleared_count=4
        )

        self.assertIn("Instance cells to write: 5 - 3 clearing a value", preview)
        self.assertIn(
            "Type parameters to write: 1 (covering 2 row(s)) - 1 clearing a "
            "value",
            preview,
        )
        self.assertIn("Values cleared: 4", summary)
        # Nothing extra when there is nothing to clear.
        self.assertNotIn(
            "clearing a value",
            "\n".join(module.build_preview_lines(1, 1, 1, 0, 0, [])),
        )
        self.assertNotIn(
            "Values cleared",
            module.build_import_summary_text(1, 0, [], [], "f"),
        )


class ImportExcelBundleTests(unittest.TestCase):
    def test_xaml_windows_expose_required_buttons(self):
        self.assertTrue(PREVIEW_XAML_PATH.exists(), "ImportPreviewWindow.xaml is missing")
        self.assertTrue(CONFLICT_XAML_PATH.exists(), "ImportConflictWindow.xaml is missing")
        preview = PREVIEW_XAML_PATH.read_text()
        conflict = CONFLICT_XAML_PATH.read_text()

        self.assertIn('Content="Import"', preview)
        self.assertIn('Click="import_click"', preview)
        self.assertIn('Content="Cancel"', preview)
        self.assertIn('Content="Close"', conflict)
        self.assertNotIn('Content="Import"', conflict)

    def test_revit_module_validates_before_single_transaction(self):
        self.assertTrue(REVIT_MODULE_PATH.exists(), "import_excel_revit.py is missing")
        source = REVIT_MODULE_PATH.read_text()

        self.assertIn("def build_import_plan", source)
        self.assertIn("def apply_import_plan", source)
        self.assertIn("def collect_scope_element_ids", source)
        self.assertIn("detect_type_conflicts", source)
        self.assertIn("out_of_scope_rows", source)

    def test_import_never_rewrites_family_or_type_parameters(self):
        self.assertTrue(REVIT_MODULE_PATH.exists(), "import_excel_revit.py is missing")
        source = REVIT_MODULE_PATH.read_text()

        # Writing these by name retypes elements and can raise Revit errors
        # that abort the whole import transaction.
        self.assertIn("ELEM_FAMILY_AND_TYPE_PARAM", source)
        self.assertIn("ELEM_TYPE_PARAM", source)
        self.assertIn("ELEM_FAMILY_PARAM", source)
        self.assertIn("def _is_type_changing_param", source)
        self.assertIn("blocked_columns", source)

    def test_elementid_cells_compare_as_display_text_before_resolving(self):
        self.assertTrue(REVIT_MODULE_PATH.exists(), "import_excel_revit.py is missing")
        source = REVIT_MODULE_PATH.read_text()

        # An untouched ElementId cell must never be resolved by name (and so
        # never retargeted at a different same-named element).
        self.assertIn("def elementid_text_unchanged", source)
        self.assertIn("elementid_text_unchanged(param, text)", source)
        # Name resolution must not silently pick the first of several matches.
        self.assertIn("is ambiguous", source)
        self.assertIn("WhereElementIsElementType", source)
        self.assertIn("WhereElementIsNotElementType", source)

    def test_blank_cells_clear_any_parameter_via_clearvalue(self):
        self.assertTrue(REVIT_MODULE_PATH.exists(), "import_excel_revit.py is missing")
        source = REVIT_MODULE_PATH.read_text()

        # Revit 2022+ exposes Parameter.ClearValue, so clearing is not
        # limited to text and a blank number is never written as 0.
        self.assertIn("def clear_param", source)
        self.assertIn("ClearValue", source)
        self.assertIn("def _already_clear", source)
        # ClearValue is refused for many built-in parameters, so each
        # storage type needs the fallback Revit does accept.
        self.assertIn("is_yesno_parameter(param.Definition)", source)
        self.assertIn("param.Set(DB.ElementId.InvalidElementId)", source)
        # HasValue alone must not decide that a clear is a no-op.
        self.assertIn("param.AsValueString()", source)
        self.assertIn("def instance_clear_count", source)
        self.assertIn("def type_clear_count", source)
        self.assertIn("cleared_count", source)

    def test_write_pass_reports_uncommitted_transactions(self):
        self.assertTrue(REVIT_MODULE_PATH.exists(), "import_excel_revit.py is missing")
        source = REVIT_MODULE_PATH.read_text()

        self.assertIn("TransactionStatus.Committed", source)
        self.assertIn("Nothing was written.", source)

    def test_bundle_modules_stay_ironpython_compatible(self):
        module_paths = [
            STATE_MODULE_PATH,
            REVIT_MODULE_PATH,
            UI_MODULE_PATH,
            SCRIPT_MODULE_PATH,
        ]
        for module_path in module_paths:
            self.assertTrue(
                module_path.exists(), "{} is missing".format(module_path.name)
            )
            tree = ast.parse(module_path.read_text(), filename=str(module_path))
            for node in ast.walk(tree):
                self.assertNotIsInstance(
                    node,
                    ast.JoinedStr,
                    "f-string found in {}".format(module_path.name),
                )


if __name__ == "__main__":
    unittest.main()
