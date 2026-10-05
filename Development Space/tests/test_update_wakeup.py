"""Scheduler lifecycle tests; real Revit API calls are intentionally boundaries."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock


PATH = Path(__file__).resolve().parents[2] / 'lib/easybim/update_wakeup.py'


def load_module():
    spec = importlib.util.spec_from_file_location('wakeup_test', str(PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Event:
    def __init__(self):
        self.requests = 0
        self.disposals = 0
        self.answer = 'Accepted'
        self.error = None

    def Raise(self):
        self.requests += 1
        if self.error:
            raise self.error
        return self.answer

    def Dispose(self):
        self.disposals += 1


class WakeupTests(unittest.TestCase):
    def setUp(self):
        self.m = load_module()
        self.store = {}
        self.events = []
        self.timers = []
        self.callback = mock.Mock()

        def event(runtime):
            ev = Event()
            self.events.append(ev)
            return ev

        def timer(tick):
            timer = mock.Mock()
            timer.tick = tick
            self.timers.append(timer)
            return timer

        self.m._create_event = event
        self.m._create_timer = timer
        self.m._get_runtime = lambda: self.store.get('runtime')
        self.m._set_runtime = lambda value: self.store.update(runtime=value) or True
        self.m._subscribe_closing = lambda uiapp, callback: (None, None)
        self.assertTrue(self.m.install(None, self.callback))
        self.runtime = self.store['runtime']

    def test_timer_only_raises_event_and_never_runs_callback(self):
        self.runtime.tick()
        self.assertEqual(1, self.events[0].requests)
        self.callback.assert_not_called()

    def test_repeated_ticks_coalesce_until_revit_executes(self):
        for _ in range(100):
            self.runtime.tick()
        self.assertEqual(1, self.events[0].requests)
        app = object()
        self.runtime.execute(app)
        self.callback.assert_called_once_with(app)
        self.runtime.tick()
        self.assertEqual(2, self.events[0].requests)

    def test_pending_request_is_not_raised_twice(self):
        self.events[0].answer = 'Pending'
        self.runtime.tick()
        self.runtime.tick()
        self.assertEqual(1, self.events[0].requests)

    def test_denied_or_failed_raise_retries_and_does_not_escape_worker(self):
        self.events[0].answer = 'Denied'
        self.runtime.tick()
        self.events[0].error = RuntimeError('SECRET')
        self.runtime.tick()
        self.events[0].error = None
        self.events[0].answer = 'Accepted'
        self.runtime.tick()
        self.assertEqual(3, self.events[0].requests)
        self.assertNotIn('SECRET', str(self.runtime.snapshot()))

    def test_stop_disposes_resources_and_pending_callback_is_inert(self):
        self.runtime.tick()
        self.m.uninstall()
        self.runtime.tick()
        self.runtime.execute(object())
        self.callback.assert_not_called()
        self.assertEqual(1, self.events[0].requests)
        self.assertEqual(1, self.events[0].disposals)
        self.timers[0].Dispose.assert_called_once()
        self.assertIsNone(self.store['runtime'])

    def test_reload_replaces_timer_and_preserves_new_runtime(self):
        new_callback = mock.Mock()
        old = self.runtime
        self.assertTrue(self.m.install(None, new_callback))
        old.stop()
        self.assertIsNot(old, self.store['runtime'])
        self.assertEqual(1, self.events[0].disposals)
        self.assertEqual(0, self.events[1].disposals)
        self.store['runtime'].execute('new app')
        new_callback.assert_called_once_with('new app')

    def test_reload_inside_execute_defers_disposal_until_callback_returns(self):
        def reload(app):
            self.m.install(app, mock.Mock())
            self.assertEqual(0, self.events[0].disposals)
        self.runtime.callback = reload
        self.runtime.execute(object())
        self.assertEqual(1, self.events[0].disposals)
        self.assertEqual(0, self.events[1].disposals)
        self.assertIsNot(self.runtime, self.store['runtime'])

    def test_timer_cannot_reenter_during_execution(self):
        self.runtime.callback = lambda app: self.runtime.tick()
        self.runtime.execute(object())
        self.assertEqual(0, self.events[0].requests)

    def test_consumer_exceptions_and_system_exit_do_not_escape_revit(self):
        for error in (RuntimeError('secret path'), SystemExit(1)):
            self.callback.side_effect = error
            self.runtime.execute(object())
        self.assertEqual(2, self.callback.call_count)
        self.assertNotIn('secret path', str(self.runtime.snapshot()))
        self.runtime.tick()
        self.assertEqual(1, self.events[0].requests)

    def test_failed_install_cleans_up_created_event(self):
        self.m._create_timer = mock.Mock(side_effect=RuntimeError('timer failed'))
        self.assertFalse(self.m.install(None, self.callback))
        self.assertEqual(1, self.events[-1].disposals)
        self.assertIsNone(self.store['runtime'])

    def test_failed_mirror_write_stops_new_timer(self):
        self.m._set_runtime = lambda value: False
        self.assertFalse(self.m.install(None, self.callback))
        self.timers[-1].Dispose.assert_called_once()
        self.assertEqual(1, self.events[-1].disposals)

    def test_ensure_reuses_one_live_timer(self):
        for _ in range(100):
            self.assertTrue(self.m.ensure_installed(None, self.callback))
        self.assertEqual(1, len(self.events))
        self.assertEqual(1, len(self.timers))


if __name__ == '__main__':
    unittest.main()
