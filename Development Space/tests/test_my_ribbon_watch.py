"""My Ribbon's watch on its own settings file, for other Revit sessions.

No session can reach into another, so a change made in one is pulled by the
rest: each looks at the file's date while Revit is idle and applies what is
there when it moved.  These pin the two things that make that cheap and
quiet - one stat every few seconds, and never applying what this session
itself just wrote or just applied at startup.
"""

import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_ROOT = ROOT / "lib" / "easybim"


def _load_my_ribbon():
    package = types.ModuleType("easybim")
    package.__path__ = [str(LIB_ROOT)]
    sys.modules["easybim"] = package
    sys.modules.pop("easybim.my_ribbon", None)
    spec = importlib.util.spec_from_file_location(
        "easybim.my_ribbon", str(LIB_ROOT / "my_ribbon.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["easybim.my_ribbon"] = module
    spec.loader.exec_module(module)
    package.my_ribbon = module
    return module


class RegistryWatchTests(unittest.TestCase):
    def setUp(self):
        self.lib = _load_my_ribbon()
        self.store = {}
        self.lib._get_envvar = lambda name, default=None: self.store.get(name, default)
        self.lib._set_envvar = lambda name, value: self.store.__setitem__(name, value)
        self.applied = []
        self.lib.apply_saved = lambda **kwargs: self.applied.append(kwargs) or {"missing": []}
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "my_ribbon.json")
        with open(self.path, "w") as handle:
            handle.write("{}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_changed_file_is_applied_once_and_the_look_is_throttled(self):
        # a file this session has never applied is applied on the first look
        self.assertIsNotNone(self.lib.watch_registry(now=100.0, path=self.path))
        self.assertEqual(len(self.applied), 1)
        # too soon to look again
        self.assertIsNone(self.lib.watch_registry(now=101.0, path=self.path))
        # looked, and nothing moved
        self.assertIsNone(self.lib.watch_registry(now=110.0, path=self.path))
        self.assertEqual(len(self.applied), 1)
        # another session saved
        with open(self.path, "w") as handle:
            handle.write('{"format": 1}')
        os.utime(self.path, (200, 200))
        self.assertIsNotNone(self.lib.watch_registry(now=120.0, path=self.path))
        self.assertEqual(len(self.applied), 2)

    def test_the_startup_apply_records_the_file_so_it_is_not_applied_twice(self):
        self.lib.run_pending_startup_apply(path=self.path)
        self.assertEqual(len(self.applied), 1)
        self.assertIsNone(self.lib.watch_registry(now=100.0, path=self.path))
        self.assertEqual(len(self.applied), 1)

    def test_a_save_made_here_is_not_applied_again_by_the_watch(self):
        ok, error = self.lib.save_registry(self.lib.empty_registry(), path=self.path)
        self.assertTrue(ok, error)
        self.assertIsNone(self.lib.watch_registry(now=100.0, path=self.path))
        self.assertEqual(self.applied, [])

    def test_a_missing_file_is_nothing_to_apply(self):
        os.remove(self.path)
        self.assertIsNone(self.lib.watch_registry(now=100.0, path=self.path))
        self.assertEqual(self.applied, [])


if __name__ == "__main__":
    unittest.main()
