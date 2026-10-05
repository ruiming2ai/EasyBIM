"""Regression cases missed by the original passive Idling-only observer."""
import unittest
from unittest import mock
import test_auto_update as fixtures
import test_auto_update_sessions as sessions


class RecoveryTests(unittest.TestCase):
    setUp = sessions.SessionUpdateTests.setUp
    _set = sessions.SessionUpdateTests._set
    _reload = sessions.SessionUpdateTests._reload
    _new_dispatcher = sessions.SessionUpdateTests._new_dispatcher
    _finish_startup = sessions.SessionUpdateTests._finish_startup
    _other_session_installs = sessions.SessionUpdateTests._other_session_installs
    def test_unknown_baseline_recovers_after_startup_without_another_click(self):
        self._finish_startup()
        self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR] = None
        self._other_session_installs()
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()
        self.assertEqual(fixtures.B, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]['head'])
        self.assertEqual([], self.updater.fetched)
        self.messages.assert_not_called()

    def test_unknown_baseline_does_not_reload_dirty_or_unpublished_files(self):
        self._finish_startup()
        self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR] = None
        self._other_session_installs()
        self.disk.dirty = True
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_not_called()
        self.disk.dirty = False
        self.disk.upstream_head = fixtures.A
        self.disk.ahead = 1
        self.disk.behind = 0
        self.now += 11
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_not_called()

    def test_transient_reload_failure_retries_the_same_revision_after_backoff(self):
        self._finish_startup()
        self._other_session_installs()
        self.reload.side_effect = RuntimeError('temporarily unavailable')
        self.idling._on_idling(self.uiapp, None)
        self.assertEqual(fixtures.A, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]['head'])
        self.reload.side_effect = self._reload
        self.now += 61
        self.idling._on_idling(self.uiapp, None)
        self.assertEqual(2, self.reload.call_count)
        self.assertEqual(fixtures.B, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]['head'])
        self.assertEqual([], self.updater.fetched)
        self.messages.assert_not_called()

    def test_permanent_reload_failure_has_a_bounded_retry_budget(self):
        self._finish_startup()
        self._other_session_installs()
        self.reload.side_effect = RuntimeError('bad loader')
        for _ in range(8):
            self.now += 301
            self.idling._on_idling(self.uiapp, None)
        self.assertEqual(3, self.reload.call_count)
        self._other_session_installs(fixtures.C)
        self.reload.side_effect = self._reload
        self.now += 301
        self.idling._on_idling(self.uiapp, None)
        self.assertEqual(4, self.reload.call_count)
        self.assertEqual(fixtures.C, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]['head'])

    def test_legacy_failed_marker_does_not_permanently_disable_observer(self):
        self._finish_startup()
        self._other_session_installs()
        self.store[self.module.AUTO_UPDATE_REFRESH_FAILED_ENVVAR] = {
            'repo_key': fixtures.EXT_ROOT, 'head': fixtures.B}
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()

    def test_wakeup_refreshes_without_a_natural_idling_event(self):
        self._finish_startup()
        self._other_session_installs()
        self.idling._on_update_wakeup(self.uiapp)
        self.reload.assert_called_once()
        self.messages.assert_not_called()

    def test_wakeup_does_not_run_other_consumers_or_interrupt_a_transaction(self):
        self._finish_startup()
        self._other_session_installs()
        self.uiapp.ActiveUIDocument = fixtures._obj(Document=fixtures._obj(IsModifiable=True))
        with mock.patch.object(self.idling, '_run_startup_jobs') as jobs:
            self.idling._on_update_wakeup(self.uiapp)
            self.reload.assert_not_called()
            jobs.assert_not_called()
        self.uiapp.ActiveUIDocument.Document.IsModifiable = False
        self.idling._on_update_wakeup(self.uiapp)
        self.reload.assert_called_once()

    def test_manual_update_releases_all_native_repo_handles_even_on_failed_fetch(self):
        for failed_fetch in (False, True):
            self.updater.fetch_error = failed_fetch
            self.disk.remote_head = fixtures.B
            handles = []
            original = fixtures.RepoInfo
            def tracked_info(disk):
                info = original(disk)
                info.repo.Dispose = mock.Mock()
                handles.append(info)
                return info
            with mock.patch.object(fixtures, 'RepoInfo', side_effect=tracked_info):
                self.module.run_manual_auto_update()
            self.assertTrue(handles)
            for info in handles:
                info.repo.Dispose.assert_called_once()

    def test_unknown_partial_marker_recovers_only_its_own_checkout(self):
        self._finish_startup()
        self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR] = {
            'repo_key': fixtures.EXT_ROOT, 'head': None}
        self._other_session_installs()
        self.idling._on_idling(self.uiapp, None)
        self.reload.assert_called_once()


    def test_manual_revalidates_files_changed_while_confirmation_was_open(self):
        self.disk.remote_head = fixtures.B
        self.messages.side_effect = lambda *args, **kw: self._other_session_installs(fixtures.C)
        result = self.module.run_manual_auto_update()
        self.reload.assert_not_called()
        self.assertNotEqual('reloaded', result['reload_status'])
        self.messages.side_effect = None
        self.now += 11
        self.idling._on_update_wakeup(self.uiapp)
        self.reload.assert_called_once()
        self.assertEqual(fixtures.C, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]['head'])

    def test_manual_reload_reacquires_writer_lock_after_dialog(self):
        self.disk.remote_head = fixtures.B
        locks = []
        def acquire():
            lock = fixtures.Lock(self.events)
            locks.append(lock)
            return lock
        def reload_locked():
            self.assertFalse(locks[-1].released)
            self._reload()
        self.reload.side_effect = reload_locked
        with mock.patch.object(self.module, '_try_acquire_startup_lock', side_effect=acquire):
            result = self.module.run_manual_auto_update()
        self.assertEqual('reloaded', result['reload_status'])
        self.assertEqual(2, len(locks))
        self.assertTrue(all(lock.released and lock.disposed for lock in locks))



if __name__ == '__main__':
    unittest.main()
