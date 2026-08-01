"""ElementId construction compatibility tests for the Temp Phase runtime.

Revit 2026 removed the ``ElementId(Int32)`` constructor (deprecated since
2024), so a plain Python int no longer binds unambiguously and IronPython
raises "Multiple targets could match".  ``_int_to_eid`` must fall back to an
explicit ``System.Int64`` construction on such hosts while remaining
byte-for-byte identical in behavior on Revit 2015-2025 and on test fakes.

These tests simulate the three host generations with small fakes and a fake
``System`` module injected into ``sys.modules``.
"""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_ROOT = ROOT / "lib" / "easybim"


def _load_module(module_name, filename):
    package = types.ModuleType("easybim")
    package.__path__ = [str(LIB_ROOT)]
    sys.modules["easybim"] = package
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, str(LIB_ROOT / filename)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    setattr(package, module_name.rsplit(".", 1)[-1], module)
    return module


class _FakeInt64(object):
    """Stand-in for System.Int64 so the fake DB can detect wrapped values."""

    def __init__(self, value):
        self.value = int(value)


def _make_fake_system():
    fake_system = types.ModuleType("System")
    fake_system.Int64 = _FakeInt64
    return fake_system


def _make_db_2015():
    """Revit 2015-2025 style: the plain-int constructor exists and binds."""

    class ElementId(object):
        def __init__(self, value):
            if not isinstance(value, int):
                raise TypeError("unexpected constructor argument")
            self.Value = int(value)

    db = types.SimpleNamespace()
    db.ElementId = ElementId
    db.BuiltInParameter = types.SimpleNamespace(VIEW_PHASE=object())
    return db


def _make_db_2026():
    """Revit 2026 style: Int32 ctor removed; a plain int is ambiguous."""

    class ElementId(object):
        def __init__(self, value):
            if isinstance(value, _FakeInt64):
                self.Value = value.value
                return
            raise TypeError(
                "Multiple targets could match: ElementId(BuiltInParameter), "
                "ElementId(BuiltInCategory), ElementId(Int64)"
            )

    db = types.SimpleNamespace()
    db.ElementId = ElementId
    db.BuiltInParameter = types.SimpleNamespace(VIEW_PHASE=object())
    return db


def _make_db_no_ctor():
    """Degenerate host: every ElementId construction fails."""

    class ElementId(object):
        def __init__(self, value):
            raise TypeError("no usable constructor")

    db = types.SimpleNamespace()
    db.ElementId = ElementId
    db.BuiltInParameter = types.SimpleNamespace(VIEW_PHASE=object())
    return db


class _BoobyTrappedInt64(object):
    """Fails the test if the Int64 fallback path runs when it must not."""

    def __init__(self, value):
        raise AssertionError("System.Int64 fallback must not be used")


class _FakePhaseParameter(object):
    def __init__(self, element_id_type, expected_value):
        self._element_id_type = element_id_type
        self._expected_value = expected_value
        self.set_calls = []

    def Set(self, element_id):
        self.set_calls.append(element_id)
        if not isinstance(element_id, self._element_id_type):
            raise TypeError("Parameter.Set expects an ElementId")
        return int(element_id.Value) == int(self._expected_value)


class _FakeView(object):
    IsValidObject = True
    IsTemplate = False

    def __init__(self, parameter):
        self._parameter = parameter

    def get_Parameter(self, built_in_parameter):
        del built_in_parameter
        return self._parameter


class ElementIdCompatTests(unittest.TestCase):
    def setUp(self):
        self.view_module = _load_module(
            "easybim.temp_phase_view", "temp_phase_view.py"
        )
        self.close_module = _load_module(
            "easybim.temp_phase_close", "temp_phase_close.py"
        )
        self._saved_system = sys.modules.get("System")

    def tearDown(self):
        if self._saved_system is None:
            sys.modules.pop("System", None)
        else:
            sys.modules["System"] = self._saved_system

    def _modules(self):
        return (self.view_module, self.close_module)

    def test_plain_int_constructor_is_used_first_on_older_hosts(self):
        """Revit 2015-2025: identical behavior, Int64 fallback never touched."""
        fake_system = _make_fake_system()
        fake_system.Int64 = _BoobyTrappedInt64
        sys.modules["System"] = fake_system

        for module in self._modules():
            with mock.patch.object(module, "_get_db", return_value=_make_db_2015()):
                element_id = module._int_to_eid(5)
            self.assertIsNotNone(element_id, module.__name__)
            self.assertEqual(5, element_id.Value, module.__name__)

    def test_int64_fallback_rescues_revit_2026_hosts(self):
        """Revit 2026: plain int raises; System.Int64 construction succeeds."""
        sys.modules["System"] = _make_fake_system()

        for module in self._modules():
            with mock.patch.object(module, "_get_db", return_value=_make_db_2026()):
                element_id = module._int_to_eid(5)
            self.assertIsNotNone(element_id, module.__name__)
            self.assertEqual(5, element_id.Value, module.__name__)

    def test_set_view_phase_succeeds_end_to_end_on_revit_2026_host(self):
        """The failing user flow: setting VIEW_PHASE must work on 2026."""
        sys.modules["System"] = _make_fake_system()
        db = _make_db_2026()
        parameter = _FakePhaseParameter(db.ElementId, expected_value=5)
        view = _FakeView(parameter)

        module = self.view_module
        with mock.patch.object(module, "_get_db", return_value=db):
            self.assertTrue(module._set_view_phase_id(view, 5))
        self.assertEqual(1, len(parameter.set_calls))
        self.assertEqual(5, parameter.set_calls[0].Value)

    def test_restore_phase_set_succeeds_on_revit_2026_host(self):
        """Close-recovery phase restore uses the close module's own helper."""
        sys.modules["System"] = _make_fake_system()
        db = _make_db_2026()
        parameter = _FakePhaseParameter(db.ElementId, expected_value=4)
        view = _FakeView(parameter)

        module = self.close_module
        # The close module defers to temp_phase_view when importable; patch
        # both layers so the fake DB is used regardless of the delegation.
        with mock.patch.object(module, "_get_db", return_value=db), mock.patch.object(
            self.view_module, "_get_db", return_value=db
        ):
            self.assertTrue(module._set_view_phase_id(view, 4))

    def test_unconstructible_host_returns_none_gracefully(self):
        sys.modules.pop("System", None)

        for module in self._modules():
            with mock.patch.object(
                module, "_get_db", return_value=_make_db_no_ctor()
            ):
                self.assertIsNone(module._int_to_eid(5), module.__name__)

    def test_invalid_inputs_return_none(self):
        for module in self._modules():
            with mock.patch.object(module, "_get_db", return_value=_make_db_2015()):
                self.assertIsNone(module._int_to_eid(None), module.__name__)
                self.assertIsNone(
                    module._int_to_eid("not-a-number"), module.__name__
                )
            with mock.patch.object(module, "_get_db", return_value=None):
                self.assertIsNone(module._int_to_eid(5), module.__name__)

    def test_enable_tvp_retries_next_candidate_on_false_return(self):
        """An explicit False from Revit must not be reported as success."""
        module = self.view_module

        class _RecordingView(object):
            IsValidObject = True
            IsTemplate = False
            Id = object()

            def __init__(self):
                self.calls = []

            def EnableTemporaryViewPropertiesMode(self, target_id):
                self.calls.append(target_id)
                # First candidate (the template id) is refused with False;
                # the view's own id is then accepted with True.
                return len(self.calls) > 1

        view = _RecordingView()
        with mock.patch.object(module, "_get_db", return_value=_make_db_2015()):
            self.assertTrue(module._enable_tvp(view, 77))
        self.assertEqual(2, len(view.calls))

    def test_enable_tvp_accepts_none_return_from_fakes(self):
        """Fakes and wrappers without a return value keep counting as success."""
        module = self.view_module

        class _SilentView(object):
            IsValidObject = True
            IsTemplate = False
            Id = object()

            def __init__(self):
                self.calls = []

            def EnableTemporaryViewPropertiesMode(self, target_id):
                self.calls.append(target_id)

        view = _SilentView()
        with mock.patch.object(module, "_get_db", return_value=_make_db_2015()):
            self.assertTrue(module._enable_tvp(view, 77))
        self.assertEqual(1, len(view.calls))


if __name__ == "__main__":
    unittest.main()
