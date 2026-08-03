"""Contract tests for the single Revit Idling subscription.

The delegate replaces a pyRevit hook script, which means this module takes on
two obligations pyRevit used to handle: detaching across a reload (pyRevit
detaches its own hooks but never a raw delegate), and absorbing anything a
consumer throws before it reaches Revit's event dispatcher.
"""

import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_ROOT = ROOT / "lib" / "easybim"


def _load_idling():
    package = types.ModuleType("easybim")
    package.__path__ = [str(LIB_ROOT)]
    sys.modules["easybim"] = package
    sys.modules.pop("easybim.idling", None)
    spec = importlib.util.spec_from_file_location(
        "easybim.idling", str(LIB_ROOT / "idling.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["easybim.idling"] = module
    spec.loader.exec_module(module)
    package.idling = module
    return module


class FakeUiapp(object):
    """Records += / -= so subscription bookkeeping can be asserted."""

    def __init__(self, fail_on_add=False):
        self.Idling = self
        self.added = []
        self.removed = []
        self._fail_on_add = fail_on_add

    # `uiapp.Idling += handler` compiles to __iadd__ on the Idling attribute.
    def __iadd__(self, handler):
        if self._fail_on_add:
            raise RuntimeError("host refused the subscription")
        self.added.append(handler)
        return self

    def __isub__(self, handler):
        self.removed.append(handler)
        return self


class _NoIdling(object):
    pass


class IdlingDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.idling = _load_idling()
        self.store = {}
        self.idling._get_envvar = lambda name, default=None: self.store.get(name, default)

        def _set(name, value):
            self.store[name] = value

        self.idling._set_envvar = _set
        self.logs = []
        self.idling._log = lambda message: self.logs.append(message)

    # -- subscription -----------------------------------------------------

    def test_install_subscribes_and_mirrors_the_delegate(self):
        uiapp = FakeUiapp()

        self.assertTrue(self.idling.install(uiapp))

        self.assertEqual(1, len(uiapp.added))
        stored = self.store[self.idling.HANDLER_ENVVAR]
        # The mirror must carry the event source too, or a recycled engine
        # would have nothing to detach from.
        self.assertIs(uiapp, stored["uiapp"])
        self.assertIsNotNone(stored["handler"])
        self.assertTrue(self.idling.is_installed())

    def test_install_detaches_a_previous_engines_delegate_first(self):
        old_uiapp = FakeUiapp()
        old_handler = object()
        self.store[self.idling.HANDLER_ENVVAR] = {
            "handler": old_handler,
            "uiapp": old_uiapp,
        }
        new_uiapp = FakeUiapp()

        self.assertTrue(self.idling.install(new_uiapp))

        self.assertEqual([old_handler], old_uiapp.removed)
        self.assertEqual(1, len(new_uiapp.added))

    def test_install_is_not_fatal_when_the_host_has_no_idling_event(self):
        self.assertFalse(self.idling.install(_NoIdling()))
        self.assertFalse(self.idling.is_installed())
        self.assertTrue(any("Install skipped" in item for item in self.logs))

    def test_install_is_not_fatal_when_subscription_raises(self):
        self.assertFalse(self.idling.install(FakeUiapp(fail_on_add=True)))
        self.assertFalse(self.idling.is_installed())
        self.assertTrue(any("subscription failed" in item for item in self.logs))

    def test_uninstall_clears_both_anchors(self):
        uiapp = FakeUiapp()
        self.idling.install(uiapp)

        handler = self.store[self.idling.HANDLER_ENVVAR]["handler"]

        self.assertTrue(self.idling.uninstall())

        # Detach runs through both anchors on purpose - a Stop must work
        # whether or not this engine is the one that subscribed. A repeated
        # `-=` is a no-op in .NET, so the duplicate costs nothing.
        self.assertIn(handler, uiapp.removed)
        self.assertIsNone(self.store[self.idling.HANDLER_ENVVAR])
        self.assertFalse(self.idling.is_installed())

    def test_uninstall_works_from_an_engine_that_never_subscribed(self):
        """Reload case: no module globals, only the envvar mirror."""
        uiapp = FakeUiapp()
        handler = object()
        self.store[self.idling.HANDLER_ENVVAR] = {"handler": handler, "uiapp": uiapp}

        self.idling.uninstall()

        self.assertEqual([handler], uiapp.removed)

    # -- dispatch safety --------------------------------------------------

    def test_one_failing_consumer_does_not_stop_the_others(self):
        calls = []

        def _boom():
            raise RuntimeError("consumer exploded")

        self.idling._guarded("first", _boom)
        self.idling._guarded("second", lambda: calls.append("ran"))

        self.assertEqual(["ran"], calls)
        self.assertTrue(any("first failed" in item for item in self.logs))

    def test_systemexit_from_a_consumer_is_absorbed(self):
        """forms.alert(exitscript=True) raises SystemExit, which is not an
        Exception subclass and would otherwise reach Revit's dispatcher."""

        def _exits():
            raise SystemExit(0)

        self.idling._guarded("exiting", _exits)

        self.assertTrue(any("interpreter exit" in item for item in self.logs))

    def test_reentrant_tick_is_skipped_while_a_pass_is_open(self):
        """A modal dialog can let Revit pump Idling again mid-pass."""
        passes = []

        def _consumer_that_reenters(sender):
            passes.append(sender)
            # Simulates a modal dialog: Revit raises Idling again underneath.
            self.idling._on_idling("nested-sender", None)

        self.idling._run_consumers = _consumer_that_reenters
        self.idling._on_idling("outer-sender", None)

        self.assertEqual(["outer-sender"], passes)
        self.assertFalse(self.idling._IN_PASS[0])

    def test_pass_flag_is_released_even_when_a_pass_raises(self):
        def _raises(sender):
            del sender
            raise RuntimeError("unguarded")

        self.idling._run_consumers = _raises
        with self.assertRaises(RuntimeError):
            self.idling._on_idling("sender", None)

        self.assertFalse(self.idling._IN_PASS[0])

    def test_sender_is_passed_through_to_consumers(self):
        """EXEC_PARAMS returns stale data outside a script run, so the
        delegate's own sender is the only trustworthy uiapp."""
        seen = []

        class FakeTempPhaseClose(object):
            @staticmethod
            def handle_app_idling(uiapp=None, event_args=None):
                seen.append((uiapp, event_args))

        self.idling.temp_phase_close = FakeTempPhaseClose
        self.idling._on_idling("real-uiapp", "idling-args")

        self.assertEqual([("real-uiapp", None)], seen)

    def test_missing_temp_phase_module_is_tolerated(self):
        self.idling.temp_phase_close = None
        self.idling.messages = None
        self.idling._on_idling("sender", None)
        self.assertEqual([], self.logs)

    # -- consumers --------------------------------------------------------

    def test_startup_jobs_consumer_receives_the_sender(self):
        calls = []

        class FakeMessages(object):
            @staticmethod
            def has_pending_startup_jobs():
                return True

            @staticmethod
            def process_startup_jobs(uiapp=None):
                calls.append(uiapp)

        self.idling.messages = FakeMessages
        self.idling.temp_phase_close = None
        self.idling._on_idling("real-uiapp", None)

        self.assertEqual(["real-uiapp"], calls)

    def test_startup_jobs_are_not_processed_when_nothing_is_pending(self):
        calls = []

        class FakeMessages(object):
            @staticmethod
            def has_pending_startup_jobs():
                return False

            @staticmethod
            def process_startup_jobs(uiapp=None):
                calls.append(uiapp)

        self.idling.messages = FakeMessages
        self.idling.temp_phase_close = None
        self.idling._on_idling("sender", None)

        self.assertEqual([], calls)

    def test_a_failing_startup_job_pass_still_runs_close_recovery(self):
        ran = []

        class ExplodingMessages(object):
            @staticmethod
            def has_pending_startup_jobs():
                raise RuntimeError("startup jobs exploded")

        class FakeTempPhaseClose(object):
            @staticmethod
            def handle_app_idling(uiapp=None, event_args=None):
                ran.append(uiapp)

        self.idling.messages = ExplodingMessages
        self.idling.temp_phase_close = FakeTempPhaseClose
        self.idling._on_idling("sender", None)

        self.assertEqual(["sender"], ran)
        self.assertTrue(any("StartupJobs failed" in item for item in self.logs))


if __name__ == "__main__":
    unittest.main()
