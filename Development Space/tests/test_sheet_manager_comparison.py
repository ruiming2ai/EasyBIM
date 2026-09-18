import importlib.util
import ast
import pathlib
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND = ROOT / "EasyBIM.tab" / "Sheet.panel" / "Sheet Manager.pushbutton"
_excel_spec = importlib.util.spec_from_file_location(
    "comparison_excel_dependency", ROOT / "lib" / "easybim" / "excel_print_sets.py")
_excel = importlib.util.module_from_spec(_excel_spec)
_excel_spec.loader.exec_module(_excel)
ExcelImportRow, ExcelPrintSetSession = _excel.ExcelImportRow, _excel.ExcelPrintSetSession


def sheet(number, name="Details", sheet_id=None, **extra):
    values = dict(id=sheet_id or number, number=number, name=name,
                  appears=True, printable=True, exclusions=[], unknown_filters=[])
    values.update(extra)
    return values


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "sheet_manager_comparison", COMMAND / "sheet_manager_comparison.py")
        cls.api = importlib.util.module_from_spec(spec)
        package = types.ModuleType("easybim")
        package.__path__ = [str(ROOT / "lib" / "easybim")]
        with mock.patch.dict(sys.modules, {"easybim": package, "easybim.excel_print_sets": _excel}):
            spec.loader.exec_module(cls.api)

    def compare(self, excel, source, model=None, kind="schedule", known=True):
        rows = [ExcelImportRow(i + 1, number, name)
                for i, (number, name) in enumerate(excel)]
        return self.api.compare_lists(rows, source, model if model is not None else source,
                                      kind, source_order_known=known)

    def test_two_way_missing_and_priority_then_natural_number_sort(self):
        model = [sheet("E10"), sheet("E2"),
                 sheet("A1", appears=False, exclusions=["Discipline = Electrical; actual: Mechanical"]),
                 sheet("A2", exclusions=["Phase = New; actual: Existing"]), sheet("A3")]
        result = self.compare([(n, "Details") for n in ("Z1", "A3", "A2", "A1")],
                              model[:2], model)
        self.assertEqual([(r.number, r.reason_key) for r in result.findings],
                         [("E2", "missing_excel"), ("E10", "missing_excel"),
                          ("Z1", "missing_model"), ("A1", "appears_off"),
                          ("A2", "filtered"), ("A3", "not_in_source")])
        self.assertIn("Discipline", result.findings[3].details)
        self.assertIn("Mechanical", result.findings[3].details)

    def test_missing_from_source_is_not_missing_from_model(self):
        result = self.compare([("E1", "Details")], [], [sheet("E1")], kind="printset")
        self.assertEqual(result.findings[0].reason_key, "not_in_source")
        self.assertIn("print set", result.findings[0].details)

    def test_appears_off_is_not_a_print_set_exclusion(self):
        result = self.compare([("E1", "Details")], [],
                              [sheet("E1", appears=False)], kind="printset")
        self.assertEqual(result.findings[0].reason_key, "not_in_source")

    def test_repeated_names_and_normalized_names_are_valid(self):
        result = self.compare([("E1", " Detail – A "), ("E2", "DETAIL - A")],
                              [sheet("E1", "detail - a"), sheet("E2", "Detail - A")])
        self.assertEqual(result.findings, [])
        self.assertEqual(result.matched_count, 2)

    def test_name_mismatch_shows_both_names_and_blank_name(self):
        result = self.compare([("E1", "Excel Name"), ("E2", "")],
                              [sheet("E1", "Revit Name"), sheet("E2", "Details")])
        self.assertEqual([r.reason_key for r in result.findings], ["name", "name"])
        self.assertEqual((result.findings[0].excel_name, result.findings[0].revit_name),
                         ("Excel Name", "Revit Name"))

    def test_duplicates_are_not_guessed_as_name_or_order_matches(self):
        result = self.compare([("E1", "A"), (" e1 ", "B"), ("E2", "C")],
                              [sheet("E2", "C", 1), sheet("E2", "D", 2), sheet("E1", "Z")])
        self.assertEqual([r.reason_key for r in result.findings], ["ambiguous", "ambiguous"])
        self.assertIn("1, 2", result.findings[0].excel_rows)
        self.assertIn("A", result.findings[0].excel_name)
        self.assertIn("B", result.findings[0].excel_name)

    def test_ambiguous_model_match_does_not_invent_filter_reason(self):
        model = [sheet("E1", "A", 1, appears=False), sheet("E1", "B", 2)]
        result = self.compare([("E1", "Details")], [], model)
        self.assertEqual(result.findings[0].reason_key, "not_in_source")
        self.assertIn("ambiguous", result.findings[0].details.lower())
        self.assertNotIn("is off", result.findings[0].details)

    def test_unknown_exclusion_is_explicit_not_inferred(self):
        result = self.compare([("E1", "Details")], [],
                              [sheet("E1", unknown_filters=["Calculated field X"])])
        finding = result.findings[0]
        self.assertEqual(finding.reason_key, "not_in_source")
        self.assertIn("could not", finding.details)
        self.assertIn("Calculated field X", finding.details)

    def test_excluded_sheet_retains_name_difference_as_secondary_reason(self):
        result = self.compare([("E1", "Excel Name")], [],
                              [sheet("E1", "Model Name", appears=False)])
        self.assertEqual(result.findings[0].reason_key, "appears_off")
        self.assertIn("names differ", result.findings[0].details)
        self.assertIn("outside the selected source", result.findings[0].details)

    def test_selected_source_does_not_hide_full_model_normalized_collisions(self):
        selected = sheet("E-1", "Details", 1)
        result = self.compare([("E-1", "Details")], [selected],
                              [selected, sheet("E–1", "Other", 2)])
        self.assertEqual(result.findings[0].reason_key, "ambiguous")
        self.assertIn("1, 2", result.findings[0].details)
        self.assertIn("selected source", result.findings[0].details)

    def test_missing_rows_do_not_cause_false_order_differences(self):
        result = self.compare([("E1", "Details"), ("E2", "Details"), ("E3", "Details")],
                              [sheet("E2"), sheet("E3")])
        self.assertEqual([r.reason_key for r in result.findings], ["missing_model"])

    def test_relative_order_differences_include_source_positions(self):
        result = self.compare([("E1", "Details"), ("E2", "Details")],
                              [sheet("E2"), sheet("E1")])
        self.assertEqual([r.reason_key for r in result.findings], ["order", "order"])
        self.assertEqual(result.findings[0].source_positions, "2")

    def test_unknown_order_is_not_reported_as_order_mismatch(self):
        result = self.compare([("E1", "Details"), ("E2", "Details")],
                              [sheet("E2"), sheet("E1")], known=False)
        self.assertEqual(result.findings, [])
        self.assertTrue(result.warnings)

    def test_blank_number_and_placeholder_are_explained_without_creation(self):
        result = self.compare([("", "Unnamed"), ("E1", "Details")],
                              [sheet("E1", printable=False)])
        self.assertTrue(all(r.reason_key == "warning" for r in result.findings))
        self.assertTrue(any("blank" in r.details for r in result.findings))
        self.assertTrue(any("printable" in r.details for r in result.findings))

    def test_empty_sides_compare_without_false_success_or_crashing(self):
        self.assertEqual(self.compare([], []).findings, [])
        self.assertEqual(self.compare([], [sheet("E1")]).findings[0].reason_key, "missing_excel")
        self.assertEqual(self.compare([("E1", "Details")], []).findings[0].reason_key, "missing_model")

    def test_comparison_uses_raw_workbook_even_after_ignore_and_staging(self):
        rows = [ExcelImportRow(1, "E1", "Excel"), ExcelImportRow(2, "E2", "Details")]
        revit_sheet = types.SimpleNamespace(Id=1, SheetNumber="E1", Name="Model")
        session = ExcelPrintSetSession(rows, [revit_sheet])
        validation = session.validate()
        validation.number_discrepancies[0].is_selected = True
        session.ignore_number_rows(validation.number_discrepancies)
        session.stage_renames(validation.name_discrepancies)
        before = session.validate()
        result = self.api.compare_lists(session.rows, [sheet("E1", "Model")],
                                        [sheet("E1", "Model")], "schedule")
        self.assertEqual([r.reason_key for r in result.findings], ["missing_model", "name"])
        self.assertEqual(len(session.validate().final_rows), len(before.final_rows))
        self.assertEqual(revit_sheet.Name, "Model")


class ComparisonDialogTests(unittest.TestCase):
    def setUp(self):
        source = (COMMAND / "sheet_manager_dialogs.py").read_text(encoding="utf-8")
        nodes = [n for n in ast.parse(source).body
                 if getattr(n, "name", None) in ("LoadCustomizedExcelWindow", "_combo_key")]

        class ArrayList(list):
            Add = list.append

        self.alerts = []
        namespace = dict(forms=types.SimpleNamespace(WPFWindow=object,
                         alert=lambda *args, **kw: self.alerts.append(args)),
                         ArrayList=ArrayList,
                         ListCollectionView=lambda rows: types.SimpleNamespace(
                             rows=rows, GroupDescriptions=ArrayList()),
                         PropertyGroupDescription=lambda name: name)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "dialogs", "exec"), namespace)
        self.window = object.__new__(namespace["LoadCustomizedExcelWindow"])
        self.window._is_ready = True
        self.window._comparison_view = None
        self.window.result = None
        self.window.comparison_source_cb = types.SimpleNamespace(
            SelectedItem=types.SimpleNamespace(Tag=("schedule", 19), Content="Sheet List: Issued"))
        self.window.comparison_tab = types.SimpleNamespace(IsSelected=False)
        self.window.comparison_dg = types.SimpleNamespace(ItemsSource=None)
        self.window.comparison_summary_tb = types.SimpleNamespace(Text="")
        self.window.comparison_warning_tb = types.SimpleNamespace(Text="")
        self.window.load_b = types.SimpleNamespace(IsEnabled=False)
        self.window._session = object()  # Comparison must not access staging APIs.

    def test_compare_routes_selected_source_without_enabling_load_or_accepting_import(self):
        calls = []
        row = types.SimpleNamespace(group_label="Missing from Excel")

        def reader(key):
            calls.append(key)
            return types.SimpleNamespace(findings=[row], excel_count=1, source_count=2,
                                         matched_count=1, warnings=["Order not verified"])
        self.window._comparison_reader = reader
        self.window.compare_clicked(None, None)
        self.assertEqual(calls, [("schedule", 19)])
        self.assertEqual(self.window.comparison_dg.ItemsSource.rows, [row])
        self.assertIn("Sheet List: Issued", self.window.comparison_summary_tb.Text)
        self.assertEqual(self.window.comparison_warning_tb.Text, "Order not verified")
        self.assertFalse(self.window.load_b.IsEnabled)
        self.assertIsNone(self.window.result)

    def test_source_change_clears_old_findings_without_changing_import(self):
        self.window.comparison_dg.ItemsSource = [object()]
        self.window.comparison_source_changed(None, None)
        self.assertIsNone(self.window.comparison_dg.ItemsSource)
        self.assertFalse(self.window.load_b.IsEnabled)
        self.assertIsNone(self.window.result)

    def test_failed_compare_clears_old_findings_and_does_not_accept_import(self):
        def reader(key):
            raise ValueError("Deleted source")
        self.window._comparison_reader = reader
        self.window.comparison_dg.ItemsSource = [object()]
        self.window.compare_clicked(None, None)
        self.assertIsNone(self.window.comparison_dg.ItemsSource)
        self.assertIn("failed", self.window.comparison_summary_tb.Text)
        self.assertTrue(self.alerts)
        self.assertIsNone(self.window.result)


if __name__ == "__main__":
    unittest.main()
