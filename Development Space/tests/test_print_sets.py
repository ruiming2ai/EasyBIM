import importlib.util
import pathlib
import sys
import types
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "print_sets.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("print_sets", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeSheet(object):
    def __init__(self, revision_ids=None):
        self._revision_ids = list(revision_ids or [])

    def GetAllRevisionIds(self):
        return list(self._revision_ids)


class _FakeRow(object):
    def __init__(self, key, revision_ids=None, printable=True):
        self.key = key
        self.revit_sheet = _FakeSheet(revision_ids)
        self.printable = printable


class _FakeOrderedList(list):
    def Add(self, value):
        self.append(value)


class _FakeView(object):
    pass


class _FakeCurrentViewSheetSet(object):
    def __init__(self):
        self.IsAutomatic = True
        self.OrderedViewList = None

    def SaveAs(self, name):
        raise AssertionError("In-session sheet sets must not be saved")


class _FakeViewSheetSetting(object):
    def __init__(self):
        self.saved_set = _FakeCurrentViewSheetSet()
        self.InSession = _FakeCurrentViewSheetSet()
        self.CurrentViewSheetSet = self.saved_set


class _FakePrintManager(object):
    def __init__(self):
        self.PrintRange = None
        self.ViewSheetSetting = _FakeViewSheetSetting()
        self.apply_count = 0

    def Apply(self):
        self.apply_count += 1


class _FakeDoc(object):
    def __init__(self):
        self.PrintManager = _FakePrintManager()


class _FakeDB(object):
    View = _FakeView

    class PrintRange(object):
        Select = "select"


class _NewHost(object):
    def is_newer_than(self, version):
        return version == 2022


class PrintSetsTests(unittest.TestCase):
    def test_build_print_set_name_uses_sheet_list_name_without_revision_filter(self):
        module = _load_module()

        name = module.build_print_set_name("A0 Sheet List", [], [])

        self.assertEqual(name, "A0 Sheet List")

    def test_build_print_set_name_appends_selected_revision_numbers_in_sequence_order(self):
        module = _load_module()
        revisions = [
            {"id": 20, "sequence": 2, "number": "02"},
            {"id": 10, "sequence": 1, "number": "01"},
            {"id": 30, "sequence": 3, "number": "03"},
        ]

        name = module.build_print_set_name("A0 Sheet List", [20, 10], revisions)

        self.assertEqual(name, "A0 Sheet List - Rev 01, 02")

    def test_filter_rows_by_revision_preserves_existing_schedule_order(self):
        module = _load_module()
        rows = [
            _FakeRow("A", [1]),
            _FakeRow("B", [2, 3]),
            _FakeRow("C", [3]),
            _FakeRow("D", []),
        ]

        filtered = module.filter_rows_by_revision(rows, [3])

        self.assertEqual([row.key for row in filtered], ["B", "C"])

    def test_split_printable_rows_keeps_order_and_counts_skipped_rows(self):
        module = _load_module()
        rows = [
            _FakeRow("A", printable=True),
            _FakeRow("B", printable=False),
            _FakeRow("C", printable=True),
        ]

        printable_rows, skipped_count = module.split_printable_rows(rows)

        self.assertEqual([row.key for row in printable_rows], ["A", "C"])
        self.assertEqual(skipped_count, 1)

    def test_in_session_print_set_becomes_current_and_uses_input_order(self):
        module = _load_module()
        system = types.ModuleType("System")
        collections = types.ModuleType("System.Collections")
        generic = types.ModuleType("System.Collections.Generic")

        class _List(object):
            def __class_getitem__(cls, item_type):
                return _FakeOrderedList

        generic.List = _List
        old_modules = {name: sys.modules.get(name) for name in (
            "System", "System.Collections", "System.Collections.Generic")}
        sys.modules["System"] = system
        sys.modules["System.Collections"] = collections
        sys.modules["System.Collections.Generic"] = generic
        try:
            first = _FakeView()
            second = _FakeView()
            doc = _FakeDoc()

            result = module.set_in_session_print_set(
                doc, [second, first], _FakeDB, _NewHost())
        finally:
            for name, previous in old_modules.items():
                if previous is None:
                    del sys.modules[name]
                else:
                    sys.modules[name] = previous

        setting = doc.PrintManager.ViewSheetSetting
        current = setting.CurrentViewSheetSet
        self.assertIs(result, current)
        self.assertIs(current, setting.InSession)
        self.assertIsNot(current, setting.saved_set)
        self.assertEqual(doc.PrintManager.PrintRange, "select")
        self.assertFalse(current.IsAutomatic)
        self.assertEqual(current.OrderedViewList, [second, first])
        self.assertIsNone(setting.saved_set.OrderedViewList)
        self.assertEqual(doc.PrintManager.apply_count, 1)


if __name__ == "__main__":
    unittest.main()
