"""Temp Phase must never let a sync or save write temporary phases out."""

import pathlib
import unittest
from unittest import mock

from test_temp_phase_commit_runtime import (
    FakeClosingArgs,
    FakeDocument,
    FakeUiapp,
    _load_runtime,
)


DOC_KEY = "path|c:\\models\\sample.rvt"
VIEW_KEY = DOC_KEY + "|11"


class FakeSyncArgs(FakeClosingArgs):
    """DocumentSynchronizingWithCentralEventArgs: same Document/Cancel surface."""


class TempPhaseCommitGuardTests(unittest.TestCase):
    def setUp(self):
        self.runtime = _load_runtime()
        self.doc = FakeDocument(workshared=True)
        self.uiapp = FakeUiapp(self.doc)
        self.view_state = {
            "last_seen_tvp": {VIEW_KEY: True},
            "view_sessions": {
                VIEW_KEY: {
                    "doc_key": DOC_KEY,
                    "doc_runtime_id": 42,
                    "view_id": 11,
                    "view_name": "Plan",
                    "original_phase_id": 3,
                }
            },
            "armed_documents": {
                "id|42": {"doc_key": DOC_KEY, "doc_runtime_id": 42},
            },
        }
        self.runtime._MEMORY_STATE.clear()
        self.runtime._get_view_state = lambda: self.view_state
        self.runtime._save_view_state = lambda state: None
        self.runtime._is_document_armed = lambda view_state, doc_key, doc_runtime_id: bool(
            view_state.get("armed_documents")
        )
        # A temporary phase is applied with TVP on; the fakes carry no DB.
        self.runtime._is_tvp_active = lambda view: view.tvp_active
        self.alerts = []
        self.runtime._show_alert = lambda title, message: self.alerts.append(message)
        self.restore_calls = 0

    def _summary(self, has_work=True):
        return {"has_restore_work": has_work, "doc_title": "sample.rvt"}

    def _restore(self, ok):
        def restore(uiapp, doc, summary):
            self.restore_calls += 1
            if ok:
                # The real restore retires the tracked session it just undid.
                self.view_state["view_sessions"].clear()
                return {"ok": True, "restored_phase_views": 1, "disabled_untracked_tvp_views": 2}
            return {"ok": False, "message": "Modification forbidden"}

        return restore

    def _sync(self, restore_ok, has_work=True, cancellable=True, action="sync"):
        args = FakeSyncArgs(self.doc)
        args.Cancellable = cancellable
        with mock.patch.object(
            self.runtime, "collect_tvp_summary", return_value=self._summary(has_work)
        ), mock.patch.object(self.runtime, "restore_tvp_summary", side_effect=self._restore(restore_ok)):
            result = self.runtime.handle_commit_requested(self.uiapp, args, action=action)
        return args, result

    def _idle(self, has_work=True, restore_ok=True):
        self.runtime._LAST_IDLE_AT[0] = 0.0
        with mock.patch.object(
            self.runtime, "collect_tvp_summary", return_value=self._summary(has_work)
        ), mock.patch.object(self.runtime, "restore_tvp_summary", side_effect=self._restore(restore_ok)):
            self.runtime.handle_app_idling(self.uiapp)

    def test_sync_restores_temporary_phases_in_place_and_lets_the_sync_run(self):
        args, result = self._sync(restore_ok=True)

        self.assertTrue(result)
        self.assertEqual(self.restore_calls, 1)
        self.assertEqual(args.cancel_calls, 0, "the restore rides in the user's own sync")
        self.assertEqual(self.view_state["armed_documents"], {})

        self._idle()
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("restored before synchronizing", self.alerts[0])
        self.assertIn("original phase: 1", self.alerts[0])

        self._idle()
        self.assertEqual(len(self.alerts), 1, "the notice is shown once")

    def test_unarmed_document_syncs_untouched(self):
        self.view_state["armed_documents"] = {}
        args, result = self._sync(restore_ok=True)

        self.assertFalse(result)
        self.assertEqual(self.restore_calls, 0)
        self.assertEqual(args.cancel_calls, 0)

    def test_armed_document_with_nothing_to_restore_syncs_untouched(self):
        args, result = self._sync(restore_ok=True, has_work=False)

        self.assertFalse(result)
        self.assertEqual(self.restore_calls, 0)
        self.assertEqual(args.cancel_calls, 0)

    def test_refused_restore_cancels_the_sync_and_restores_on_idle(self):
        args, result = self._sync(restore_ok=False)

        self.assertTrue(result)
        self.assertEqual(args.cancel_calls, 1)
        self.assertEqual(self.alerts, [])

        self._idle(restore_ok=True)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("was stopped", self.alerts[0])
        self.assertIn("Synchronize again", self.alerts[0])
        self.assertEqual(self.runtime._get_state()["guard_restores"], {})
        self.assertEqual(self.view_state["armed_documents"], {})

    def test_uncancellable_sync_is_still_followed_by_the_restore(self):
        args, result = self._sync(restore_ok=False, cancellable=False)

        self.assertFalse(result)
        self.assertEqual(args.cancel_calls, 0)

        self._idle(restore_ok=True)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("may have sent temporary phases", self.alerts[0])
        self.assertIn("Synchronize again", self.alerts[0])

    def test_idle_restore_that_keeps_failing_gives_up_with_manual_steps(self):
        self._sync(restore_ok=False)
        for _ in range(self.runtime.MAX_RESTORE_ATTEMPTS):
            record = self.runtime._get_state()["guard_restores"].get("id|42")
            if record is not None:
                record["next_try_at"] = 0.0
            self._idle(restore_ok=False)

        self.assertEqual(self.runtime._get_state()["guard_restores"], {})
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("Restore All Views", self.alerts[0])

    def test_save_restores_temporary_phases_in_place(self):
        args, result = self._sync(restore_ok=True, action="save")

        self.assertTrue(result)
        self.assertEqual(self.restore_calls, 1)
        self.assertEqual(args.cancel_calls, 0, "the restore rides in the user's own save")

        self._idle()
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("restored before saving", self.alerts[0])
        self.assertIn("the saved file keeps the original phases", self.alerts[0])

    def test_refused_restore_cancels_the_save_and_asks_to_save_again(self):
        args, result = self._sync(restore_ok=False, action="save")

        self.assertTrue(result)
        self.assertEqual(args.cancel_calls, 1)

        self._idle(restore_ok=True)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("Saving sample.rvt was stopped", self.alerts[0])
        self.assertIn("Save again", self.alerts[0])

    def test_save_and_save_as_delegates_route_to_the_save_guard(self):
        calls = []
        with mock.patch.object(
            self.runtime,
            "handle_commit_requested",
            side_effect=lambda uiapp, event_args, action: calls.append(action),
        ):
            self.runtime.document_saving_handler(self.uiapp, FakeSyncArgs(self.doc))
            self.runtime.document_saving_as_handler(self.uiapp, FakeSyncArgs(self.doc))
            self.runtime.document_synchronizing_with_central_handler(self.uiapp, FakeSyncArgs(self.doc))
        self.assertEqual(calls, ["save", "save", "sync"])

    def test_guard_exception_never_escapes_the_delegate(self):
        with mock.patch.object(
            self.runtime, "handle_commit_requested", side_effect=RuntimeError("boom")
        ):
            self.assertFalse(
                self.runtime.document_synchronizing_with_central_handler(
                    self.uiapp, FakeSyncArgs(self.doc)
                )
            )

    def test_the_pre_commit_events_are_attached_and_detached(self):
        class FakeEvent(object):
            def __init__(self):
                self.handlers = []

            def __iadd__(self, handler):
                self.handlers.append(handler)
                return self

            def __isub__(self, handler):
                self.handlers.remove(handler)
                return self

        names = ("DocumentSynchronizingWithCentral", "DocumentSaving", "DocumentSavingAs")

        class FakeApp(object):
            pass

        app = FakeApp()
        for name in names:
            setattr(app, name, FakeEvent())
            self.runtime._attach_completion(app, name, "handler")
            self.assertEqual(getattr(app, name).handlers, ["handler"], name)
            self.runtime._detach_completion(app, name, "handler")
            self.assertEqual(getattr(app, name).handlers, [], name)

    def test_install_registers_the_pre_commit_guard(self):
        source = pathlib.Path(self.runtime.__file__).read_text(encoding="utf-8")
        for args_name in (
            "DocumentSynchronizingWithCentralEventArgs",
            "DocumentSavingEventArgs",
            "DocumentSavingAsEventArgs",
        ):
            self.assertIn('"{0}"'.format(args_name), source)


if __name__ == "__main__":
    unittest.main()
