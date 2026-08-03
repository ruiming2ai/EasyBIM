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
STATE_MODULE_PATH = COMMAND_DIR / "schedule_export_state.py"
REVIT_MODULE_PATH = COMMAND_DIR / "schedule_export_revit.py"
XLSX_MODULE_PATH = COMMAND_DIR / "schedule_export_xlsx.py"
UI_MODULE_PATH = COMMAND_DIR / "schedule_export_ui.py"
SCRIPT_MODULE_PATH = COMMAND_DIR / "script.py"
XAML_PATH = COMMAND_DIR / "ScheduleSelectionWindow.xaml"


def _load_state_module():
    spec = importlib.util.spec_from_file_location(
        "schedule_export_state",
        str(STATE_MODULE_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScheduleExportStateTests(unittest.TestCase):
    def test_build_token_uses_prefix_and_id(self):
        module = _load_state_module()

        self.assertEqual(module.build_token(123456), "EBIMORD:123456")
        self.assertEqual(module.build_token("789"), "EBIMORD:789")

    def test_parse_ordered_ids_follows_line_order_not_id_order(self):
        module = _load_state_module()

        lines = [
            "Schedule Title",
            "Mark\tComments",
            "A-10\tEBIMORD:300",
            "A-2\tEBIMORD:100",
            "",
            "A-5\tEBIMORD:200",
        ]

        self.assertEqual(module.parse_ordered_ids(lines), [300, 100, 200])

    def test_parse_ordered_ids_skips_tokenless_lines_and_duplicates(self):
        module = _load_state_module()

        lines = [
            "Header row without token",
            "EBIMORD:5\tfirst occurrence",
            "continuation line from multiline value",
            "EBIMORD:5\tduplicate ignored",
            "prefix text EBIMORD:9 suffix text",
        ]

        self.assertEqual(module.parse_ordered_ids(lines), [5, 9])
        self.assertEqual(module.parse_ordered_ids([]), [])
        self.assertEqual(module.parse_ordered_ids(None), [])

    def test_order_elements_by_ids_appends_unmatched_in_collector_order(self):
        module = _load_state_module()

        pairs = [(1, "wall"), (2, "door"), (3, "window"), (None, "odd")]

        ordered, unmatched = module.order_elements_by_ids(pairs, [3, 1])

        self.assertEqual(ordered, ["window", "wall"])
        self.assertEqual(unmatched, ["door", "odd"])

    def test_order_elements_by_ids_ignores_unknown_exported_ids(self):
        module = _load_state_module()

        pairs = [(1, "wall"), (2, "door")]

        ordered, unmatched = module.order_elements_by_ids(pairs, [9, 2, 1, 2])

        self.assertEqual(ordered, ["door", "wall"])
        self.assertEqual(unmatched, [])

    def test_strip_rvt_suffix(self):
        module = _load_state_module()

        self.assertEqual(module.strip_rvt_suffix("Tower.rvt"), "Tower")
        self.assertEqual(module.strip_rvt_suffix("Tower.RVT"), "Tower")
        self.assertEqual(module.strip_rvt_suffix("  Tower  "), "Tower")
        self.assertEqual(module.strip_rvt_suffix(""), "")

    def test_build_export_filename_combines_schedule_and_model_names(self):
        module = _load_state_module()

        self.assertEqual(
            module.build_export_filename("Door Schedule", "Tower"),
            "Door Schedule - Tower.xlsx",
        )

    def test_build_export_filename_strips_invalid_characters(self):
        module = _load_state_module()

        self.assertEqual(
            module.build_export_filename('Door:/*?"<>|Schedule', "Tower\\Model"),
            "DoorSchedule - TowerModel.xlsx",
        )
        self.assertEqual(
            module.build_export_filename("", ""), "Schedule Export.xlsx"
        )
        self.assertEqual(
            module.build_export_filename("***", '"""'),
            "Schedule Export.xlsx",
        )

    def test_build_export_filename_prefers_injected_cleaner(self):
        module = _load_state_module()

        self.assertEqual(
            module.build_export_filename(
                "Door Schedule",
                "Tower",
                cleaner=lambda text: text.replace(" ", "_"),
            ),
            "Door_Schedule_-_Tower.xlsx",
        )

    def test_build_export_filename_falls_back_when_cleaner_fails(self):
        module = _load_state_module()

        def broken_cleaner(text):
            raise ValueError(text)

        self.assertEqual(
            module.build_export_filename(
                "Door*Schedule", "Tower", cleaner=broken_cleaner
            ),
            "DoorSchedule - Tower.xlsx",
        )

    def test_build_export_filename_supports_custom_extension(self):
        module = _load_state_module()

        self.assertEqual(
            module.build_export_filename(
                "Door Schedule", "Tower", extension=".xlsm"
            ),
            "Door Schedule - Tower.xlsm",
        )
        self.assertEqual(
            module.build_export_filename("Door Schedule", "Tower"),
            "Door Schedule - Tower.xlsx",
        )

    def test_should_skip_token_candidate(self):
        module = _load_state_module()

        self.assertTrue(module.should_skip_token_candidate(10, [10], []))
        self.assertTrue(module.should_skip_token_candidate(10, [], [10]))
        self.assertFalse(module.should_skip_token_candidate(10, [11], [12]))
        self.assertTrue(module.should_skip_token_candidate(None, [], []))

    def test_filter_schedule_options_matches_name_case_insensitively(self):
        module = _load_state_module()

        options = [
            module.ScheduleOption(None, "Door Schedule"),
            module.ScheduleOption(None, "Wall Schedule", is_selected=True),
        ]

        filtered = module.filter_schedule_options(options, "wall")

        self.assertEqual([option.name for option in filtered], ["Wall Schedule"])
        self.assertEqual(len(module.filter_schedule_options(options, "")), 2)
        self.assertTrue(options[1].is_selected)

    def test_get_selected_options(self):
        module = _load_state_module()

        options = [
            module.ScheduleOption(None, "A", is_selected=True),
            module.ScheduleOption(None, "B"),
            module.ScheduleOption(None, "C", is_selected=True),
        ]

        self.assertEqual(
            [option.name for option in module.get_selected_options(options)],
            ["A", "C"],
        )

    def test_schedule_display_text_labels_special_kinds(self):
        module = _load_state_module()

        regular = module.ScheduleOption(None, "Doors", kind=module.KIND_REGULAR)
        key = module.ScheduleOption(None, "Finishes", kind=module.KIND_KEY)
        sheets = module.ScheduleOption(None, "Index", kind=module.KIND_SHEET_LIST)

        self.assertEqual(module.schedule_display_text(regular), "Doors")
        self.assertEqual(
            module.schedule_display_text(key), "Finishes  (key schedule)"
        )
        self.assertEqual(
            module.schedule_display_text(sheets), "Index  (sheet list)"
        )

    def test_build_export_summary_text_reports_all_sections(self):
        module = _load_state_module()

        results = [
            module.ScheduleExportResult(
                "Doors", ok=True, path="C:/x/Doors - Tower.xlsx", row_count=12
            ),
            module.ScheduleExportResult(
                "Walls",
                ok=True,
                row_count=3,
                ordering_note="Rows are in Revit internal order.",
            ),
            module.ScheduleExportResult(
                "Windows", ok=False, message="File is open in Excel."
            ),
        ]

        message = module.build_export_summary_text(results, "C:/x")

        self.assertIn("Exported 2 of 3 schedule(s) to:", message)
        self.assertIn("C:/x", message)
        self.assertIn("- Doors (12 row(s))", message)
        self.assertIn("- Walls (3 row(s))", message)
        self.assertIn("Ordering warnings:", message)
        self.assertIn("- Walls: Rows are in Revit internal order.", message)
        self.assertIn("Failed:", message)
        self.assertIn("- Windows: File is open in Excel.", message)
        self.assertIn("overwritten", message)

    def test_build_export_summary_text_caps_long_lists(self):
        module = _load_state_module()

        results = [
            module.ScheduleExportResult("Schedule {}".format(index), ok=True)
            for index in range(module.SUMMARY_DISPLAY_LIMIT + 5)
        ]

        message = module.build_export_summary_text(results, "C:/x")

        self.assertIn("...and 5 more.", message)


class ScheduleExportBundleTests(unittest.TestCase):
    def test_xaml_exposes_required_buttons(self):
        self.assertTrue(XAML_PATH.exists(), "ScheduleSelectionWindow.xaml is missing")
        source = XAML_PATH.read_text()

        self.assertIn('Content="Select All"', source)
        self.assertIn('Content="Select None"', source)
        self.assertIn('Content="Export to Excel"', source)
        self.assertIn('Content="Import to Schedule"', source)
        self.assertIn('x:Name="ImportButton"', source)
        self.assertIn('IsEnabled="False"', source)
        self.assertIn('Click="select_all_click"', source)
        self.assertIn('Click="select_none_click"', source)
        self.assertIn('Click="export_click"', source)
        self.assertIn('Click="import_click"', source)
        self.assertNotIn('Content="Select"' + ' ', source)

    def test_ordering_pipeline_always_rolls_back_transaction_group(self):
        self.assertTrue(REVIT_MODULE_PATH.exists(), "schedule_export_revit.py is missing")
        source = REVIT_MODULE_PATH.read_text()

        self.assertIn("TransactionGroup", source)
        self.assertIn("RollBack", source)
        self.assertNotIn("tgroup.Assimilate", source)
        self.assertNotIn("group.Commit", source)

    def test_xlsx_writer_resolves_type_parameters(self):
        self.assertTrue(XLSX_MODULE_PATH.exists(), "schedule_export_xlsx.py is missing")
        source = XLSX_MODULE_PATH.read_text()

        self.assertIn("GetTypeId", source)
        self.assertIn("_lookup_row_param", source)
        self.assertIn("istype=istype", source)
        self.assertNotIn("istype=False", source)

    def test_xlsx_writer_has_type_conflict_interlock(self):
        self.assertTrue(XLSX_MODULE_PATH.exists(), "schedule_export_xlsx.py is missing")
        source = XLSX_MODULE_PATH.read_text()

        self.assertIn('"IsTypeParameter"', source)
        self.assertIn('"_EBIM_TypeId"', source)
        self.assertIn("conditional_format", source)
        self.assertIn("COUNTIFS", source)
        self.assertIn("add_vba_project", source)
        self.assertIn("write_string", source)
        # The autofilter must span every trailing helper column, otherwise a
        # sort reorders the data while the helper columns stay put.
        self.assertIn(
            "autofilter(0, 0, len(src_elements), rowid_col)", source
        )
        self.assertNotIn(
            "autofilter(0, 0, len(src_elements), len(valid_params))", source
        )
        self.assertIn("COLOR_LOCKED = \"#F2F2F2\"", source)
        self.assertIn("COLOR_TYPE = \"#FFF2CC\"", source)
        self.assertIn("COLOR_CONFLICT = \"#FFC7CE\"", source)
        self.assertIn("COLOR_WARNING = ", source)
        # Type-parameter cells stay yellow; conditional rules paint over it.
        self.assertIn("bool(param_def.istype)", source)
        self.assertIn("cell_format = _editable_format(param)", source)

    def test_export_writes_a_legend_sheet_second(self):
        self.assertTrue(XLSX_MODULE_PATH.exists(), "schedule_export_xlsx.py is missing")
        source = XLSX_MODULE_PATH.read_text()

        self.assertIn('add_worksheet("Legend")', source)
        self.assertIn("def _write_legend_sheet", source)
        self.assertIn("LEGEND_CELL_ROWS", source)
        self.assertIn("LEGEND_HEADER_ROWS", source)
        self.assertIn("LEGEND_RULES", source)
        # Legend must come before the hidden bookkeeping sheet.
        self.assertLess(
            source.index('add_worksheet("Legend")'),
            source.index('add_worksheet("_metadata")'),
        )

    def test_export_flags_values_that_will_not_import(self):
        self.assertTrue(XLSX_MODULE_PATH.exists(), "schedule_export_xlsx.py is missing")
        source = XLSX_MODULE_PATH.read_text()

        self.assertIn("def _add_validation_formatting", source)
        self.assertIn("ISNUMBER", source)
        self.assertIn("COUNTIF(", source)
        # Text columns get a Text number format so Excel stops eating values.
        self.assertIn('"num_format": "@"', source)
        # Placeholder cells must never be flagged.
        self.assertIn('<does not exist>', source)

    def test_export_tracks_edited_element_ids(self):
        self.assertTrue(XLSX_MODULE_PATH.exists(), "schedule_export_xlsx.py is missing")
        source = XLSX_MODULE_PATH.read_text()

        self.assertIn("_EBIM_RowId", source)
        self.assertIn("def _add_edited_id_formatting", source)
        self.assertIn("autofilter(0, 0, len(src_elements), rowid_col)", source)

    def test_family_and_type_columns_are_reference_only(self):
        self.assertTrue(XLSX_MODULE_PATH.exists(), "schedule_export_xlsx.py is missing")
        source = XLSX_MODULE_PATH.read_text()

        self.assertIn("def _is_reference_only_param", source)
        self.assertIn("ELEM_FAMILY_AND_TYPE_PARAM", source)
        self.assertIn("COLOR_HEADER_REFERENCE", source)

    def test_helper_columns_are_ignored_by_the_importer(self):
        import_state_path = COMMAND_DIR / "import_excel_state.py"
        self.assertTrue(import_state_path.exists(), "import_excel_state.py is missing")
        source = import_state_path.read_text()

        self.assertIn('"_EBIM_TypeId"', source)
        self.assertIn('"_EBIM_RowId"', source)

    def test_vba_macro_tracks_the_trailing_helper_columns(self):
        vba_path = COMMAND_DIR / "vba" / "ThisWorkbook_TypeSync.vba.txt"
        self.assertTrue(vba_path.exists(), "type sync macro source is missing")
        source = vba_path.read_text()

        # _EBIM_RowId is now the last column; the type key sits one left.
        self.assertIn('<> "_EBIM_RowId" Then Exit Sub', source)
        self.assertIn('<> "_EBIM_TypeId" Then Exit Sub', source)
        self.assertIn("typeCol = lastCol - 1", source)
        self.assertNotIn("Sh.Cells(r, lastCol).Value) = typeKey", source)

    def test_bundle_modules_stay_ironpython_compatible(self):
        module_paths = [
            STATE_MODULE_PATH,
            REVIT_MODULE_PATH,
            XLSX_MODULE_PATH,
            UI_MODULE_PATH,
            SCRIPT_MODULE_PATH,
        ]
        for module_path in module_paths:
            self.assertTrue(
                module_path.exists(), "{} is missing".format(module_path.name)
            )
            tree = ast.parse(
                module_path.read_text(), filename=str(module_path)
            )
            for node in ast.walk(tree):
                self.assertNotIsInstance(
                    node,
                    ast.JoinedStr,
                    "f-string found in {}".format(module_path.name),
                )


if __name__ == "__main__":
    unittest.main()
