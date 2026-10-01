"""Unattended update regressions through the real Idling dispatcher.

Native Git/pyRevit boundaries use the existing API-shaped test fixtures;
queueing, throttling, verification, dispatch and reload decisions are real code.
"""
import sys
import unittest
from unittest import mock

import test_auto_update as updates
from test_idling_dispatcher import FakeUiapp, _load_idling


class SessionUpdateTests(unittest.TestCase):
    _set = updates.UpdateTests._set
    _reload = updates.UpdateTests._reload

    def setUp(self):
        updates.UpdateTests.setUp(self)
        self.now = 1000.0
        clock = mock.patch.object(self.module.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.uiapp = FakeUiapp()
        self.idling = self._new_dispatcher()
        self.idling.install(self.uiapp)

    def _new_dispatcher(self):
        with mock.patch.dict(sys.modules):
            dispatcher = _load_idling()
        dispatcher.auto_update = self.module
        dispatcher.messages = dispatcher.temp_phase_close = None
        dispatcher.my_ribbon = dispatcher.etransmit_worker = None
        dispatcher._get_envvar = self.module._get_envvar
        dispatcher._set_envvar = self.module._set_envvar
        dispatcher._log = self.logger
        return dispatcher

    def _finish_startup(self):
        self.assertTrue(self.module.queue_startup_auto_update())
        self.idling._on_idling(self.uiapp, None)
        self.assertFalse(self.module.has_pending_startup_auto_update())
        self.updater.fetched[:] = []
        self.git.opened[:] = []
        self.events[:] = []
        self.lock = updates.Lock(self.events)

    def _other_session_installs(self, head=updates.B):
        self.disk.head = self.disk.upstream_head = self.disk.remote_head = head

    def test_idle_refreshes_shared_installation_after_startup_has_finished(self):
        self._finish_startup()
        self._other_session_installs()
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()
        self.assertEqual(updates.B, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]["head"])
        self.assertEqual([], self.updater.fetched)
        self.assertEqual([], self.updater.pulled)
        self.messages.assert_not_called()

    def test_busy_startup_retries_without_another_button_click(self):
        self.module.queue_startup_auto_update()
        with mock.patch.object(self.module, "_try_acquire_startup_lock", return_value=False):
            self.idling._on_idling(self.uiapp, None)
        self.assertEqual([], self.updater.fetched)
        # Do not spin against the lock on every Idling event.
        self.assertFalse(self.module.has_pending_startup_auto_update())
        self.now += 11
        self.assertTrue(self.module.has_pending_startup_auto_update())
        self.disk.remote_head = updates.B
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()
        self.assertEqual(updates.B, self.disk.head)
        self.assertFalse(self.module.has_pending_startup_auto_update())

    def test_receiving_session_refreshes_offline_without_fetch_or_popup(self):
        self._finish_startup()
        self._other_session_installs()
        self.updater.fetch_error = True
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()
        self.assertEqual([], self.updater.fetched)
        self.messages.assert_not_called()

    def test_receiving_session_keeps_new_engine_delegate_after_reload(self):
        self._finish_startup()
        self._other_session_installs()
        new_dispatcher = self._new_dispatcher()
        old_handler = self.idling._HANDLER

        def reload_in_new_engine():
            self._reload()
            new_dispatcher.install(self.uiapp)

        self.reload.side_effect = reload_in_new_engine
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()
        self.assertIs(new_dispatcher._HANDLER,
                      self.store[self.idling.HANDLER_ENVVAR]["handler"])
        self.assertIsNot(old_handler, new_dispatcher._HANDLER)


    def test_startup_recovers_baseline_missing_during_other_session_pull(self):
        self.disk.dirty = True
        self.module.record_loaded_revision()
        self.assertIsNone(self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR])
        self.module.queue_startup_auto_update()
        with mock.patch.object(self.module, "_try_acquire_startup_lock", return_value=False):
            self.idling._on_idling(self.uiapp, None)
        self.disk.dirty = False
        self._other_session_installs()
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()
        self.assertEqual(updates.B, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]["head"])
        self.assertFalse(self.module.has_pending_startup_auto_update())

    def test_current_idle_ticks_are_throttled_and_do_not_scan_or_detach(self):
        self._finish_startup()
        with mock.patch.object(self.module, "_require_clean") as status, \
                mock.patch.object(self.idling, "uninstall") as detach:
            for _ in range(100):
                self.idling._on_idling(self.uiapp, None)
            self.assertEqual(1, len(self.git.opened))
            status.assert_not_called()
            detach.assert_not_called()
        self.assertEqual([], self.updater.fetched)
        self.reload.assert_not_called()

    def test_each_later_installed_revision_reloads_exactly_once(self):
        self._finish_startup()
        for expected, head in enumerate((updates.B, updates.C), 1):
            self._other_session_installs(head)
            self.now += 11
            self.idling._on_idling(self.uiapp, None)
            self.assertEqual(expected, self.reload.call_count)
            self.now += 11
            self.idling._on_idling(self.uiapp, None)
            self.assertEqual(expected, self.reload.call_count)
        self.assertEqual([], self.updater.fetched)
        self.messages.assert_not_called()

    def test_dirty_receiver_defers_then_refreshes_when_files_are_clean(self):
        self._finish_startup()
        self._other_session_installs()
        self.disk.dirty = True
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_not_called()
        self.assertEqual(updates.B, self.disk.head)
        self.disk.dirty = False
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()

    def test_receiver_rejects_committed_edits_and_incomplete_history(self):
        self._finish_startup()
        for ahead, behind, tree in ((1, 0, updates.C), (0, 1, updates.B)):
            with self.subTest(ahead=ahead, behind=behind, tree=tree):
                self.disk.head = updates.B
                self.disk.upstream_head = updates.C
                self.disk.upstream_tree = updates.B
                self.disk.tree = tree
                self.disk.ahead, self.disk.behind = ahead, behind
                self.now += 11
                self.idling._on_idling(self.uiapp, None)
                self.reload.assert_not_called()
                self.assertEqual(updates.B, self.disk.head)
        self.assertEqual([], self.updater.pulled)
        self.messages.assert_not_called()

    def test_receiver_accepts_file_neutral_native_merge(self):
        self._finish_startup()
        self.disk.head = updates.C
        self.disk.upstream_head = updates.B
        self.disk.tree = self.disk.upstream_tree = updates.B
        self.disk.ahead, self.disk.behind = 1, 0
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()
        self.assertEqual(updates.C, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]["head"])

    def test_receiver_retries_writer_lock_without_fetching(self):
        self._finish_startup()
        self._other_session_installs()
        with mock.patch.object(self.module, "_try_acquire_startup_lock", return_value=False):
            self.idling._on_idling(self.uiapp, None)
        self.reload.assert_not_called()
        self.assertTrue(self.idling.is_installed())
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()
        self.assertEqual([], self.updater.fetched)

    def test_receiver_holds_writer_lock_through_reload_and_releases_after(self):
        self._finish_startup()
        self._other_session_installs()
        def reload_with_lock():
            self.assertFalse(self.lock.released)
            self.assertTrue(self.store[self.module.AUTO_UPDATE_RUNNING_ENVVAR])
            self._reload()
        self.reload.side_effect = reload_with_lock
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()
        self.assertTrue(self.lock.released)
        self.assertTrue(self.lock.disposed)
        self.assertFalse(self.store[self.module.AUTO_UPDATE_RUNNING_ENVVAR])

    def test_reload_failure_is_not_repeated_on_every_idle_tick(self):
        self._finish_startup()
        self._other_session_installs()
        self.reload.side_effect = RuntimeError("reload failed")
        self.idling._on_idling(self.uiapp, None)
        for _ in range(5):
            self.now += 11
            self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()
        self.assertTrue(self.lock.released)
        self.assertEqual(updates.A, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]["head"])
        self.assertTrue(self.idling.is_installed())
        self.messages.assert_not_called()
        # An explicit manual retry is still possible.
        self.reload.side_effect = self._reload
        result = self.module.run_manual_auto_update()
        self.assertEqual("reloaded", result["reload_status"])

    def test_successful_load_clears_failed_revision_suppression(self):
        self.store[self.module.AUTO_UPDATE_REFRESH_FAILED_ENVVAR] = {
            "repo_key": updates.EXT_ROOT, "head": updates.B}
        self.module.record_loaded_revision()
        self.assertIsNone(self.store.get(self.module.AUTO_UPDATE_REFRESH_FAILED_ENVVAR))

    def test_unchanged_poll_disposes_native_handle(self):
        info = updates.RepoInfo(self.disk)
        info.repo.Dispose = mock.Mock()
        with mock.patch.object(self.git, "get_repo", return_value=info):
            self.assertFalse(self.module.has_pending_session_refresh())
        info.repo.Dispose.assert_called_once()

    def test_failed_status_inspection_disposes_native_handle(self):
        self._other_session_installs()
        self.disk.status_error = TypeError("https://user:SECRET@example.com")
        info = updates.RepoInfo(self.disk)
        info.repo.Dispose = mock.Mock()
        with mock.patch.object(self.git, "get_repo", return_value=info):
            result = self.module.run_pending_session_refresh()
        info.repo.Dispose.assert_called_once()
        self.assertEqual(self.module.STATUS_VERIFICATION_FAILED, result["status"])
        self.assertNotIn("SECRET", result["message"])
        self.reload.assert_not_called()

    def test_receiver_does_not_import_remote_updater_or_claim_remote_freshness(self):
        self._finish_startup()
        self._other_session_installs()
        with mock.patch.object(self.module, "_get_native_updater", side_effect=AssertionError("no network")):
            result = self.module.run_pending_session_refresh()
        self.assertEqual("local_checkout", result["verification_source"])
        self.assertEqual(self.module.STATUS_RELOADED, result["status"])
        self.assertNotIn("latest", result["message"])
        self.messages.assert_not_called()

    def test_running_update_prevents_observer_reads(self):
        self._finish_startup()
        self.store[self.module.AUTO_UPDATE_RUNNING_ENVVAR] = True
        self.idling._on_idling(self.uiapp, None)
        self.assertEqual([], self.git.opened)
        self.reload.assert_not_called()

    def test_unknown_baseline_cannot_authorize_periodic_reload(self):
        self._finish_startup()
        self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR] = None
        self._other_session_installs()
        self.idling._on_idling(self.uiapp, None)
        self.assertEqual([], self.git.opened)
        self.reload.assert_not_called()

    def test_receiver_rejects_detached_or_untracked_checkout(self):
        self._finish_startup()
        self._other_session_installs()
        for detached, upstream in ((True, self.disk.upstream), (False, None)):
            self.disk.detached, self.disk.upstream = detached, upstream
            self.now += 11
            self.idling._on_idling(self.uiapp, None)
            self.reload.assert_not_called()
        self.messages.assert_not_called()

    def test_manual_click_consumes_delayed_startup_retry(self):
        self.module.queue_startup_auto_update()
        with mock.patch.object(self.module, "_try_acquire_startup_lock", return_value=False):
            self.idling._on_idling(self.uiapp, None)
        self.module.run_manual_auto_update()
        self.now += 11
        self.assertFalse(self.module.has_pending_startup_auto_update())
        self.assertIsNone(self.store[self.module.AUTO_UPDATE_RETRY_ENVVAR])

    def test_clock_rollback_and_bad_retry_state_do_not_stall_startup(self):
        self.module.queue_startup_auto_update()
        self.store[self.module.AUTO_UPDATE_RETRY_ENVVAR] = self.now + 3600
        self.assertTrue(self.module.has_pending_startup_auto_update())
        self.store[self.module.AUTO_UPDATE_RETRY_ENVVAR] = "corrupt"
        self.assertTrue(self.module.has_pending_startup_auto_update())


if __name__ == "__main__":
    unittest.main()
