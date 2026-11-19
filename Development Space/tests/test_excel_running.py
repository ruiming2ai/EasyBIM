import importlib.util
import os
import pathlib
import shutil
import tempfile
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "excel_running.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "excel_running", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExcelRunningTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)

    def test_owner_lock_paths_cover_short_and_long_names(self):
        path = os.path.join(self.tempdir, "Sheet Manager - Tower.xlsx")
        self.assertEqual(
            [os.path.basename(p) for p in self.module.owner_lock_paths(path)],
            ["~$Sheet Manager - Tower.xlsx", "~$eet Manager - Tower.xlsx"])
        self.assertEqual(self.module.owner_lock_paths(""), [])

    def test_is_open_in_excel_follows_owner_file(self):
        path = os.path.join(self.tempdir, "Sheet Manager - Tower.xlsx")
        self.assertFalse(self.module.is_open_in_excel(path))
        with open(os.path.join(self.tempdir, "~$eet Manager - Tower.xlsx"),
                  "w") as handle:
            handle.write("owner")
        self.assertTrue(self.module.is_open_in_excel(path))

    @unittest.skipIf(os.name == "nt", "checks the off-Windows fallback")
    def test_unsaved_changes_unknown_off_windows(self):
        self.assertIsNone(self.module.workbook_has_unsaved_changes(
            os.path.join(self.tempdir, "any.xlsx")))


if __name__ == "__main__":
    unittest.main()
