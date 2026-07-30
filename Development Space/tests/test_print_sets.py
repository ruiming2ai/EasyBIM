import importlib.util
import pathlib
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


if __name__ == "__main__":
    unittest.main()
