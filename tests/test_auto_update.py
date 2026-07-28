import importlib.util
import pathlib
import unittest
from unittest import mock


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "lib"
    / "easybim"
    / "auto_update.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("auto_update", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeUpdater(object):
    def __init__(self):
        self.call_count = 0

    def update_pyrevit(self):
        self.call_count += 1


class AutoUpdateTests(unittest.TestCase):
    def test_should_skip_startup_returns_true_after_guard_is_set(self):
        module = _load_module()

        self.assertFalse(module.should_skip_startup({"attempted": False}))
        self.assertFalse(module.should_skip_startup(None))
        self.assertTrue(module.should_skip_startup({"attempted": True}))

    def test_startup_auto_update_calls_native_pyrevit_update(self):
        module = _load_module()
        fake_updater = _FakeUpdater()

        with mock.patch.object(module, "_get_native_updater", return_value=fake_updater):
            result = module.run_startup_auto_update()

        self.assertEqual(fake_updater.call_count, 1)
        self.assertEqual(result["status"], module.STATUS_EXECUTED)
        self.assertEqual(result["trigger"], "startup")

    def test_manual_auto_update_calls_native_pyrevit_update(self):
        module = _load_module()
        fake_updater = _FakeUpdater()

        with mock.patch.object(module, "_get_native_updater", return_value=fake_updater):
            result = module.run_manual_auto_update()

        self.assertEqual(fake_updater.call_count, 1)
        self.assertEqual(result["status"], module.STATUS_EXECUTED)
        self.assertEqual(result["trigger"], "manual")

    def test_native_update_errors_are_not_swallowed(self):
        module = _load_module()

        class FailingUpdater(object):
            def update_pyrevit(self):
                raise RuntimeError("native update failed")

        with mock.patch.object(module, "_get_native_updater", return_value=FailingUpdater()):
            with self.assertRaises(RuntimeError):
                module.run_manual_auto_update()

    def test_easybim_auto_update_has_no_local_command_runner_dependencies(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("subprocess", source)
        self.assertNotIn("shutil", source)


if __name__ == "__main__":
    unittest.main()
