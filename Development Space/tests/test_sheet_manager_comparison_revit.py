import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND = ROOT / "EasyBIM.tab" / "Sheet.panel" / "Sheet Manager.pushbutton"


class FilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = types.SimpleNamespace(
            StorageType=types.SimpleNamespace(String="String", Integer="Integer", ElementId="ElementId"),
            ElementId=types.SimpleNamespace(InvalidElementId=-1),
            BuiltInParameter=types.SimpleNamespace(SHEET_NUMBER=-10, SHEET_SCHEDULED=-11))
        pyrevit = types.ModuleType("pyrevit")
        pyrevit.DB = cls.db
        pyrevit.framework = types.SimpleNamespace(get_type=lambda t: t)
        cls.sm = types.ModuleType("sheet_manager_revit")
        cls.sm.HOST_APP = None
        cls.sm.LOGGER = None
        cls.sm.script = None
        package = types.ModuleType("easybim")
        package.__path__ = [str(ROOT / "lib" / "easybim")]
        dependencies = {"pyrevit": pyrevit, "sheet_manager_revit": cls.sm, "easybim": package}
        for name in ("print_sets", "compat", "excel_print_sets"):
            spec = importlib.util.spec_from_file_location(
                "easybim." + name, ROOT / "lib" / "easybim" / (name + ".py"))
            dependency = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(dependency)
            dependencies["easybim." + name] = dependency
        with mock.patch.dict(sys.modules, dependencies), \
                mock.patch.object(sys, "path", [str(COMMAND), str(ROOT / "lib")] + sys.path):
            spec = importlib.util.spec_from_file_location(
                "comparison_revit_under_test", COMMAND / "sheet_manager_comparison_revit.py")
            cls.api = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.api)

    def test_native_filter_verifies_hidden_parameter_and_shows_actual_value(self):
        param = types.SimpleNamespace(Id=42, StorageType="String", AsString=lambda: "Mechanical",
                                      AsValueString=lambda: None)
        sheet = types.SimpleNamespace(Id=9, Parameters=[param])
        field = types.SimpleNamespace(ParameterId=42, GetName=lambda: "Discipline", IsHidden=True)
        filt = types.SimpleNamespace(FilterType="Equal", IsStringValue=True,
                                     IsIntegerValue=False, IsElementIdValue=False,
                                     IsDoubleValue=False, GetStringValue=lambda: "Electrical")
        calls = []

        def factory(pid, value):
            calls.append((pid, value))
            return types.SimpleNamespace(Dispose=lambda: None)

        self.db.ParameterFilterRuleFactory = types.SimpleNamespace(CreateEqualsRule=factory)
        self.db.ElementParameterFilter = lambda rule: types.SimpleNamespace(
            PassesFilter=lambda doc, sid: False, Dispose=lambda: None)
        detail, unknown = self.api.evaluate_filter(None, sheet, field, filt)
        self.assertEqual(calls, [(42, "Electrical")])
        self.assertIn("Discipline", detail)
        self.assertIn("Electrical", detail)
        self.assertIn("Mechanical", detail)
        self.assertIsNone(unknown)

    def test_passing_filter_is_not_an_exclusion(self):
        param = types.SimpleNamespace(Id=42, StorageType="Integer", AsInteger=lambda: 1,
                                      AsValueString=lambda: "Yes")
        sheet = types.SimpleNamespace(Id=9, Parameters=[param])
        field = types.SimpleNamespace(ParameterId=42, GetName=lambda: "Issued")
        filt = types.SimpleNamespace(FilterType="Equal", IsStringValue=False,
                                     IsIntegerValue=True, IsElementIdValue=False,
                                     IsDoubleValue=False, GetIntegerValue=lambda: 1)
        self.db.ParameterFilterRuleFactory = types.SimpleNamespace(
            CreateEqualsRule=lambda pid, value: types.SimpleNamespace(Dispose=lambda: None))
        self.db.ElementParameterFilter = lambda rule: types.SimpleNamespace(
            PassesFilter=lambda doc, sid: True, Dispose=lambda: None)
        self.assertEqual(self.api.evaluate_filter(None, sheet, field, filt), (None, None))

    def test_unsupported_calculated_filter_is_unknown_not_false_exclusion(self):
        field = types.SimpleNamespace(ParameterId=-1, GetName=lambda: "Calculated X")
        detail, unknown = self.api.evaluate_filter(None, types.SimpleNamespace(Parameters=[]),
                                                   field, types.SimpleNamespace(FilterType="Equal"))
        self.assertIsNone(detail)
        self.assertIn("Calculated X", unknown)

    def test_sheet_flag_and_multiple_failed_filters_are_all_preserved(self):
        sheet = types.SimpleNamespace(Id=9, SheetNumber="E1", Name="Details", CanBePrinted=True,
                                     get_Parameter=lambda key: types.SimpleNamespace(AsInteger=lambda: 0))
        fields = [types.SimpleNamespace(GetName=lambda: "Discipline"),
                  types.SimpleNamespace(GetName=lambda: "Phase")]
        definition = types.SimpleNamespace(
            GetFilters=lambda: [types.SimpleNamespace(FieldId=0), types.SimpleNamespace(FieldId=1)],
            GetField=lambda fid: fields[fid])
        with mock.patch.object(self.api, "evaluate_filter", side_effect=[("discipline", None), ("phase", None)]):
            result = self.api.sheet_snapshot(sheet, definition)
        self.assertFalse(result["appears"])
        self.assertEqual(result["exclusions"], ["discipline", "phase"])

    def test_schedule_order_requires_complete_unique_number_column(self):
        field = types.SimpleNamespace(IsHidden=False, ParameterId=-10)
        schedule = types.SimpleNamespace(Definition=types.SimpleNamespace(
            IsItemized=True, GetFieldOrder=lambda: [0], GetField=lambda fid: field))
        sheets = [types.SimpleNamespace(Id=1, SheetNumber="E1"), types.SimpleNamespace(Id=2, SheetNumber="E2")]
        with mock.patch.object(self.api.print_sets, "get_schedule_text_data", return_value=["Sheet Number", "E2", "E1"]):
            ordered, known = self.api.schedule_order(schedule, sheets)
        self.assertTrue(known)
        self.assertEqual([s.Id for s in ordered], [2, 1])
        with mock.patch.object(self.api.print_sets, "get_schedule_text_data", return_value=["E2"]):
            ordered, known = self.api.schedule_order(schedule, sheets)
        self.assertFalse(known)
        self.assertEqual({s.Id for s in ordered}, {1, 2})

    def test_unitemized_schedule_never_claims_individual_sheet_order(self):
        schedule = types.SimpleNamespace(Definition=types.SimpleNamespace(IsItemized=False))
        sheets = [types.SimpleNamespace(Id=1, SheetNumber="E1")]
        ordered, known = self.api.schedule_order(schedule, sheets)
        self.assertFalse(known)
        self.assertEqual(ordered, sheets)

    def test_print_set_snapshot_uses_membership_and_order_without_model_writes(self):
        class Sheet:
            def __init__(self, number, sheet_id):
                self.Id, self.SheetNumber, self.Name = sheet_id, number, "Details"
                self.CanBePrinted = True
            def get_Parameter(self, key):
                return types.SimpleNamespace(AsInteger=lambda: 0)

        a, b, outside = Sheet("E1", 1), Sheet("E2", 2), Sheet("E3", 3)
        source = types.SimpleNamespace(Views=[a, b], OrderedViewList=[b, a], IsAutomatic=False)
        doc = types.SimpleNamespace(GetElement=lambda key: source)
        rows = [types.SimpleNamespace(sheet_number=n, sheet_name="Details", excel_row=i + 1)
                for i, n in enumerate(("E2", "E1", "E3", "E4"))]
        with mock.patch.object(self.db, "ElementId", int), \
                mock.patch.object(self.db, "ViewSheet", Sheet, create=True), \
                mock.patch.object(self.sm, "collect_sheets", return_value=[a, b, outside], create=True):
            result = self.api.read_comparison(doc, rows, ("printset", 50))
        self.assertEqual([(r.number, r.reason_key) for r in result.findings],
                         [("E4", "missing_model"), ("E3", "not_in_source")])
        self.assertEqual(result.warnings, [])
        self.assertEqual([s.SheetNumber for s in source.Views], ["E1", "E2"])

    def test_deleted_source_fails_instead_of_reporting_every_sheet_missing(self):
        doc = types.SimpleNamespace(GetElement=lambda key: None)
        with mock.patch.object(self.db, "ElementId", int):
            with self.assertRaisesRegex(ValueError, "no longer exists"):
                self.api.read_comparison(doc, [], ("schedule", 50))


if __name__ == "__main__":
    unittest.main()
