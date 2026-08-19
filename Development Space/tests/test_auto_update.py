import importlib.util
import pathlib
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "lib" / "easybim" / "auto_update.py"
COMMAND_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Auto Update.pushbutton"


def _load_module():
    spec = importlib.util.spec_from_file_location("auto_update", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeRepoInfo(object):
    def __init__(self, name, last_commit_hash):
        self.name = name
        self.last_commit_hash = last_commit_hash


class _FakeSessionManager(object):
    def __init__(self):
        self.reload_count = 0
        self.reload_pyrevit = self._reload_pyrevit

    def _reload_pyrevit(self):
        self.reload_count += 1


class _FakeUpdater(object):
    def __init__(
        self,
        before_repos=None,
        after_repos=None,
        sessionmgr=None,
        request_reload=False,
        pending_updates=True,
        check_exception=None,
    ):
        self.check_count = 0
        self.check_exception = check_exception
        self.call_count = 0
        self.pending_updates = pending_updates
        self.repos = list(before_repos or [])
        self.after_repos = list(after_repos or self.repos)
        self.sessionmgr = sessionmgr
        self.request_reload = request_reload

    def get_all_extension_repos(self):
        return list(self.repos)

    def check_for_updates(self):
        self.check_count += 1
        if self.check_exception is not None:
            raise self.check_exception
        return self.pending_updates

    def update_pyrevit(self):
        self.call_count += 1
        self.repos = list(self.after_repos)
        if self.request_reload:
            self.sessionmgr.reload_pyrevit()


class _FakeStartupLock(object):
    def __init__(self):
        self.released = False
        self.disposed = False

    def ReleaseMutex(self):
        self.released = True

    def Dispose(self):
        self.disposed = True


class AutoUpdateTests(unittest.TestCase):
    def test_should_skip_startup_returns_true_after_guard_is_set(self):
        module = _load_module()

        self.assertFalse(module.should_skip_startup({"attempted": False}))
        self.assertFalse(module.should_skip_startup(None))
        self.assertTrue(module.should_skip_startup({"attempted": True}))

    def test_startup_auto_update_calls_native_pyrevit_update(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            sessionmgr=sessionmgr,
        )

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                result = module.run_startup_auto_update()

        self.assertEqual(updater.call_count, 1)
        self.assertEqual(result["status"], module.STATUS_NO_OP)
        self.assertEqual(result["trigger"], "startup")

    def test_startup_lock_acquired_runs_native_update_and_releases(self):
        module = _load_module()
        startup_lock = _FakeStartupLock()
        expected_result = {
            "status": module.STATUS_NO_OP,
            "trigger": "startup",
            "updated_repos": [],
        }

        with mock.patch.object(module, "_try_acquire_startup_lock", return_value=startup_lock):
            with mock.patch.object(
                module, "_run_startup_update_after_precheck", return_value=expected_result
            ) as run_update:
                result = module.run_startup_auto_update()

        run_update.assert_called_once_with()
        self.assertEqual(result, expected_result)
        self.assertTrue(startup_lock.released)
        self.assertTrue(startup_lock.disposed)

    def test_startup_lock_unavailable_skips_native_update(self):
        module = _load_module()

        with mock.patch.object(module, "_try_acquire_startup_lock", return_value=False):
            with mock.patch.object(module, "_run_native_update") as run_update:
                result = module.run_startup_auto_update()

        run_update.assert_not_called()
        self.assertEqual(result["status"], module.STATUS_SKIPPED_LOCKED)
        self.assertEqual(result["trigger"], "startup")
        self.assertEqual(result["updated_repos"], [])

    def test_startup_lock_releases_when_native_update_raises(self):
        module = _load_module()
        startup_lock = _FakeStartupLock()

        with mock.patch.object(module, "_try_acquire_startup_lock", return_value=startup_lock):
            with mock.patch.object(
                module, "_run_startup_update_after_precheck", side_effect=RuntimeError("failed")
            ):
                with self.assertRaises(RuntimeError):
                    module.run_startup_auto_update()

        self.assertTrue(startup_lock.released)
        self.assertTrue(startup_lock.disposed)

    def test_manual_auto_update_calls_native_pyrevit_update(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            sessionmgr=sessionmgr,
        )

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                result = module.run_manual_auto_update()

        self.assertEqual(updater.call_count, 1)
        self.assertEqual(result["status"], module.STATUS_NO_OP)
        self.assertEqual(result["trigger"], "manual")

    def test_manual_auto_update_does_not_require_startup_lock(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            sessionmgr=sessionmgr,
        )

        with mock.patch.object(module, "_try_acquire_startup_lock") as acquire_lock:
            with mock.patch.object(module, "_get_native_updater", return_value=updater):
                with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                    result = module.run_manual_auto_update()

        acquire_lock.assert_not_called()
        self.assertEqual(updater.call_count, 1)
        self.assertEqual(result["trigger"], "manual")

    def test_startup_no_pending_updates_skips_full_native_update(self):
        module = _load_module()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            pending_updates=False,
        )

        with mock.patch.object(module, "_try_acquire_startup_lock", return_value=None):
            with mock.patch.object(module, "_get_native_updater", return_value=updater):
                result = module.run_startup_auto_update()

        self.assertEqual(updater.check_count, 1)
        self.assertEqual(updater.call_count, 0)
        self.assertEqual(result["status"], module.STATUS_NO_OP)
        self.assertEqual(result["trigger"], "startup")
        self.assertEqual(result["updated_repos"], [])

    def test_startup_pending_updates_calls_full_native_update(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            sessionmgr=sessionmgr,
            pending_updates=True,
        )

        with mock.patch.object(module, "_try_acquire_startup_lock", return_value=None):
            with mock.patch.object(module, "_get_native_updater", return_value=updater):
                with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                    result = module.run_startup_auto_update()

        self.assertEqual(updater.check_count, 1)
        self.assertEqual(updater.call_count, 1)
        self.assertEqual(result["status"], module.STATUS_NO_OP)

    def test_startup_precheck_exception_fails_closed_and_skips_update(self):
        """A broken pre-check (auth/network) must not escalate into the full
        git-pull-everything path."""
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            sessionmgr=sessionmgr,
            check_exception=RuntimeError("precheck failed"),
        )

        with mock.patch.object(module, "_try_acquire_startup_lock", return_value=None):
            with mock.patch.object(module, "_get_native_updater", return_value=updater):
                with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                    result = module.run_startup_auto_update()

        self.assertEqual(updater.check_count, 1)
        self.assertEqual(updater.call_count, 0)
        self.assertEqual(result["status"], module.STATUS_NO_OP)

    def test_queue_defers_and_pending_flag_is_consumed(self):
        module = _load_module()
        store = {}

        def fake_set(name, value):
            store[name] = value
            return True

        with mock.patch.object(module, "_set_envvar", side_effect=fake_set), mock.patch.object(
            module, "_get_envvar", side_effect=lambda name, default=None: store.get(name, default)
        ), mock.patch.object(
            module, "get_startup_guard_state", return_value={"attempted": False}
        ), mock.patch.object(
            module, "mark_startup_attempted"
        ) as mark_attempted, mock.patch.object(
            module, "run_startup_auto_update", return_value={"status": module.STATUS_NO_OP}
        ) as run_update:
            self.assertTrue(module.queue_startup_auto_update())
            self.assertTrue(module.has_pending_startup_auto_update())

            module.run_pending_startup_auto_update()
            mark_attempted.assert_called_once()
            run_update.assert_called_once()
            self.assertFalse(module.has_pending_startup_auto_update())

    def test_queue_skips_when_startup_already_attempted(self):
        module = _load_module()

        with mock.patch.object(
            module, "get_startup_guard_state", return_value={"attempted": True}
        ), mock.patch.object(module, "_set_envvar") as set_envvar:
            self.assertFalse(module.queue_startup_auto_update())
        set_envvar.assert_not_called()

    def test_manual_auto_update_bypasses_pending_update_precheck(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            sessionmgr=sessionmgr,
            check_exception=RuntimeError("manual should not precheck"),
        )

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                result = module.run_manual_auto_update()

        self.assertEqual(updater.check_count, 0)
        self.assertEqual(updater.call_count, 1)
        self.assertEqual(result["trigger"], "manual")

    def test_no_popup_when_repo_heads_match(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            sessionmgr=sessionmgr,
        )

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with mock.patch.object(module, "_show_message") as show_message:
                    result = module.run_manual_auto_update()

        show_message.assert_not_called()
        self.assertEqual(result["status"], module.STATUS_NO_OP)
        self.assertEqual(result["updated_repos"], [])
        self.assertEqual(sessionmgr.reload_count, 0)

    def test_popup_lists_single_changed_repo_before_native_reload(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "def456")],
            sessionmgr=sessionmgr,
            request_reload=True,
        )

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with mock.patch.object(module, "_show_message") as show_message:
                    result = module.run_manual_auto_update()

        show_message.assert_called_once()
        message = show_message.call_args[0][0]
        self.assertIn("pyRevit Update installed changes:", message)
        self.assertIn("- EasyBIM", message)
        self.assertIn("pyRevit is reloading.", message)
        self.assertEqual(result["status"], module.STATUS_UPDATED)
        self.assertEqual(result["updated_repos"], ["EasyBIM"])
        self.assertEqual(sessionmgr.reload_count, 1)

    def test_popup_lists_multiple_changed_repos(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[
                _FakeRepoInfo("EasyBIM", "abc123"),
                _FakeRepoInfo("Other.extension", "111111"),
            ],
            after_repos=[
                _FakeRepoInfo("EasyBIM", "def456"),
                _FakeRepoInfo("Other.extension", "222222"),
            ],
            sessionmgr=sessionmgr,
            request_reload=True,
        )

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with mock.patch.object(module, "_show_message") as show_message:
                    result = module.run_startup_auto_update()

        message = show_message.call_args[0][0]
        self.assertIn("- EasyBIM", message)
        self.assertIn("- Other.extension", message)
        self.assertEqual(result["updated_repos"], ["EasyBIM", "Other.extension"])
        self.assertEqual(sessionmgr.reload_count, 1)

    def test_native_reload_still_runs_when_no_hash_change_is_detected(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            sessionmgr=sessionmgr,
            request_reload=True,
        )

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with mock.patch.object(module, "_show_message") as show_message:
                    result = module.run_manual_auto_update()

        show_message.assert_not_called()
        self.assertEqual(result["status"], module.STATUS_NO_OP)
        self.assertEqual(result["updated_repos"], [])
        self.assertEqual(sessionmgr.reload_count, 1)

    def test_native_reload_is_restored_when_update_raises(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        original_reload = sessionmgr.reload_pyrevit

        class FailingUpdater(object):
            def get_all_extension_repos(self):
                return [_FakeRepoInfo("EasyBIM", "abc123")]

            def update_pyrevit(self):
                raise RuntimeError("native update failed")

        with mock.patch.object(module, "_get_native_updater", return_value=FailingUpdater()):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with self.assertRaises(RuntimeError):
                    module.run_manual_auto_update()

        self.assertIs(sessionmgr.reload_pyrevit, original_reload)

    def test_snapshot_failure_runs_native_update_without_popup_interception(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()

        class SnapshotFailingUpdater(object):
            def __init__(self):
                self.call_count = 0

            def get_all_extension_repos(self):
                raise RuntimeError("snapshot failed")

            def update_pyrevit(self):
                self.call_count += 1
                sessionmgr.reload_pyrevit()

        updater = SnapshotFailingUpdater()

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with mock.patch.object(module, "_show_message") as show_message:
                    result = module.run_manual_auto_update()

        show_message.assert_not_called()
        self.assertEqual(updater.call_count, 1)
        self.assertEqual(sessionmgr.reload_count, 1)
        self.assertEqual(result["status"], module.STATUS_NO_OP)
        self.assertEqual(result["updated_repos"], [])

    def test_easybim_auto_update_has_no_local_command_runner_dependencies(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("subprocess", source)
        self.assertNotIn("shutil", source)


class AutoUpdateBundleTests(unittest.TestCase):
    def test_command_folder_sits_in_the_misc_tools_panel(self):
        self.assertTrue(COMMAND_DIR.is_dir())

    def test_the_button_stays_live_with_no_document_open(self):
        # Updating the extensions touches no document, and the natural moment
        # to run it is Revit's start page before any model is open.
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("context: zero-doc", bundle)


if __name__ == "__main__":
    unittest.main()
