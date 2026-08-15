import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_ROOT = ROOT / "lib" / "easybim"


def _load_module():
    package = types.ModuleType("easybim")
    package.__path__ = [str(LIB_ROOT)]
    sys.modules["easybim"] = package
    sys.modules.pop("easybim.external_events", None)
    spec = importlib.util.spec_from_file_location(
        "easybim.external_events", str(LIB_ROOT / "external_events.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["easybim.external_events"] = module
    spec.loader.exec_module(module)
    package.external_events = module
    return module


ee = _load_module()


class FakeEvent(object):
    """Stands in for Autodesk.Revit.UI.ExternalEvent."""

    def __init__(self, handler, raise_result="Accepted", raise_error=None):
        self.handler = handler
        self.raise_result = raise_result
        self.raise_error = raise_error
        self.raise_calls = 0
        self.disposed = False

    def Raise(self):
        self.raise_calls += 1
        if self.raise_error is not None:
            raise self.raise_error
        return self.raise_result

    def Dispose(self):
        self.disposed = True

    def fire(self, uiapp="UIAPP"):
        """What Revit does later: call the handler on the API thread."""
        self.handler.Execute(uiapp)


class Harness(object):
    def __init__(self, raise_result="Accepted", raise_error=None):
        self.event = None
        self.errors = []
        self.idle_calls = 0

        def create_event(handler):
            self.event = FakeEvent(handler, raise_result, raise_error)
            return self.event

        self.bridge = ee.ExternalEventBridge(
            "Test Bridge",
            create_event=create_event,
            on_error=lambda label, ex: self.errors.append((label, ex)),
            on_idle=self._on_idle)

    def _on_idle(self):
        self.idle_calls += 1


class CreateTests(unittest.TestCase):
    def test_create_registers_handler_with_both_members(self):
        harness = Harness()
        self.assertTrue(harness.bridge.create())
        self.assertTrue(harness.bridge.is_ready())
        handler = harness.event.handler
        self.assertTrue(callable(getattr(handler, "Execute", None)))
        self.assertEqual(handler.GetName(), "Test Bridge")
        # idempotent
        self.assertTrue(harness.bridge.create())

    def test_create_failure_reports_and_stays_not_ready(self):
        def broken(handler):
            raise RuntimeError("no revit")
        errors = []
        bridge = ee.ExternalEventBridge(
            "B", create_event=broken,
            on_error=lambda label, ex: errors.append(label))
        self.assertFalse(bridge.create())
        self.assertFalse(bridge.is_ready())
        self.assertEqual(errors, ["create"])
        self.assertFalse(bridge.run(lambda uiapp: None))


class RunTests(unittest.TestCase):
    def test_fifo_drain_passes_uiapp_and_results(self):
        harness = Harness()
        harness.bridge.create()
        seen = []
        done = []
        self.assertTrue(harness.bridge.run(
            lambda uiapp: seen.append(("a", uiapp)) or "ra",
            on_done=lambda result: done.append(result), label="a"))
        self.assertTrue(harness.bridge.run(
            lambda uiapp: seen.append(("b", uiapp)) or "rb",
            on_done=lambda result: done.append(result), label="b"))
        self.assertTrue(harness.bridge.is_busy())
        self.assertEqual(harness.bridge.pending(), 2)
        harness.event.fire("UI")
        self.assertEqual(seen, [("a", "UI"), ("b", "UI")])
        self.assertEqual(done, ["ra", "rb"])
        self.assertFalse(harness.bridge.is_busy())
        self.assertEqual(harness.idle_calls, 1)

    def test_error_in_work_skips_on_done_and_continues(self):
        harness = Harness()
        harness.bridge.create()
        done = []

        def failing(uiapp):
            raise ValueError("boom")
        harness.bridge.run(failing, on_done=lambda r: done.append("bad"),
                           label="first")
        harness.bridge.run(lambda uiapp: "ok",
                           on_done=lambda r: done.append(r), label="second")
        harness.event.fire()
        self.assertEqual(done, ["ok"])
        self.assertEqual([label for label, _ in harness.errors], ["first"])
        self.assertIsInstance(harness.errors[0][1], ValueError)
        self.assertEqual(harness.idle_calls, 1)

    def test_error_in_on_done_is_reported_with_finish_label(self):
        harness = Harness()
        harness.bridge.create()

        def bad_done(result):
            raise RuntimeError("ui broke")
        harness.bridge.run(lambda uiapp: 1, on_done=bad_done, label="apply")
        harness.event.fire()
        self.assertEqual([label for label, _ in harness.errors],
                         ["apply (finish)"])

    def test_system_exit_is_swallowed(self):
        harness = Harness()
        harness.bridge.create()
        ran = []

        def exiting(uiapp):
            raise SystemExit()
        harness.bridge.run(exiting, label="exit")
        harness.bridge.run(lambda uiapp: ran.append(True))
        harness.event.fire()
        self.assertEqual(ran, [True])
        self.assertEqual(harness.errors, [])

    def test_denied_raise_unqueues_and_returns_false(self):
        harness = Harness(raise_result="Denied")
        harness.bridge.create()
        self.assertFalse(harness.bridge.run(lambda uiapp: None))
        self.assertEqual(harness.bridge.pending(), 0)
        self.assertFalse(harness.bridge.is_busy())

    def test_raise_exception_unqueues_and_reports(self):
        harness = Harness(raise_error=RuntimeError("closed"))
        harness.bridge.create()
        self.assertFalse(harness.bridge.run(lambda uiapp: None, label="x"))
        self.assertEqual(harness.bridge.pending(), 0)
        self.assertEqual([label for label, _ in harness.errors], ["x"])

    def test_run_from_on_done_drains_in_same_pass(self):
        harness = Harness()
        harness.bridge.create()
        order = []

        def second(uiapp):
            order.append("second")

        def first_done(result):
            order.append("first-done")
            harness.bridge.run(second)
        harness.bridge.run(lambda uiapp: order.append("first"),
                           on_done=first_done)
        harness.event.fire()
        self.assertEqual(order, ["first", "first-done", "second"])
        self.assertEqual(harness.idle_calls, 1)

    def test_reentrant_execute_is_ignored(self):
        harness = Harness()
        harness.bridge.create()
        depth = []

        def nested(uiapp):
            depth.append("outer")
            harness.event.fire()  # Revit would never do this; guard anyway
        harness.bridge.run(nested)
        harness.event.fire()
        self.assertEqual(depth, ["outer"])


class DisposeTests(unittest.TestCase):
    def test_dispose_clears_queue_and_event(self):
        harness = Harness()
        harness.bridge.create()
        harness.bridge.run(lambda uiapp: None)
        harness.bridge.dispose()
        self.assertTrue(harness.event.disposed)
        self.assertFalse(harness.bridge.is_ready())
        self.assertEqual(harness.bridge.pending(), 0)
        self.assertFalse(harness.bridge.run(lambda uiapp: None))
        harness.event.fire()  # a late Execute after dispose is a no-op
        self.assertEqual(harness.errors, [])


if __name__ == "__main__":
    unittest.main()
