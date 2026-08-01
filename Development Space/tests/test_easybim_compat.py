"""Contract tests for the shared easybim.compat helpers."""

import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "lib" / "easybim" / "compat.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("easybim_compat", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeInt64(object):
    def __init__(self, value):
        self.value = int(value)


class _ElementId2015(object):
    """Revit 2015-2025 style: plain-int constructor binds."""

    def __init__(self, value):
        if not isinstance(value, int):
            raise TypeError("unexpected constructor argument")
        self.IntegerValue = int(value)
        self.Value = int(value)


class _ElementId2026(object):
    """Revit 2026 style: Int32 ctor removed; plain int is ambiguous."""

    def __init__(self, value):
        if isinstance(value, _FakeInt64):
            self.Value = value.value
            return
        raise TypeError("Multiple targets could match")


class EasybimCompatTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self._saved_system = sys.modules.get("System")
        fake_system = types.ModuleType("System")
        fake_system.Int64 = _FakeInt64
        sys.modules["System"] = fake_system

    def tearDown(self):
        if self._saved_system is None:
            sys.modules.pop("System", None)
        else:
            sys.modules["System"] = self._saved_system

    def test_safe_text_handles_none_str_and_hostile_objects(self):
        class Hostile(object):
            def __str__(self):
                raise RuntimeError("no str")

            def ToString(self):
                return "clr-text"

        self.assertEqual("", self.module.safe_text(None))
        self.assertEqual("5", self.module.safe_text(5))
        self.assertEqual("clr-text", self.module.safe_text(Hostile()))

    def test_eid_to_int_prefers_integervalue_then_value(self):
        self.assertEqual(7, self.module.eid_to_int(_ElementId2015(7)))

        class ValueOnly(object):
            Value = 9

        self.assertEqual(9, self.module.eid_to_int(ValueOnly()))
        self.assertIsNone(self.module.eid_to_int(None))
        self.assertIsNone(self.module.eid_to_int(object()))

    def test_int_to_eid_uses_plain_ctor_on_older_hosts(self):
        eid = self.module.int_to_eid(5, _ElementId2015)
        self.assertEqual(5, eid.Value)

    def test_int_to_eid_falls_back_to_int64_on_2026_hosts(self):
        eid = self.module.int_to_eid(5, _ElementId2026)
        self.assertEqual(5, eid.Value)

    def test_int_to_eid_invalid_inputs_return_none(self):
        self.assertIsNone(self.module.int_to_eid(None, _ElementId2015))
        self.assertIsNone(self.module.int_to_eid("nan", _ElementId2015))
        self.assertIsNone(self.module.int_to_eid(5, None))

    def test_element_id_factory_resolves_ctor_once_per_host_style(self):
        factory_2015 = self.module.element_id_factory(_ElementId2015)
        self.assertEqual(5, factory_2015(5).Value)

        factory_2026 = self.module.element_id_factory(_ElementId2026)
        self.assertEqual(5, factory_2026(5).Value)

    def test_exception_text_includes_inner_chain(self):
        inner = RuntimeError("inner")
        outer = RuntimeError("outer")
        outer.InnerException = inner
        text = self.module.exception_text(outer)
        self.assertIn("outer", text)
        self.assertIn("Inner: RuntimeError: inner", text)


if __name__ == "__main__":
    unittest.main()
