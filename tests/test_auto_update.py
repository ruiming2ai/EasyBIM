import importlib.util
import pathlib
import unittest
from unittest import mock


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "lib"
    / "easybim"
    / "auto_update.py"
)


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
    def __init__(self, before_repos=None, after_repos=None, sessionmgr=None, request_reload=False):
        self.call_count = 0
        self.repos = list(before_repos or [])
        self.after_repos = list(after_repos or self.repos)
        self.sessionmgr = sessionmgr
        self.request_reload = request_reload

    def get_all_extension_repos(self):
        return list(self.repos)

    def update_pyrevit(self):
        self.call_count += 1
        self.repos = list(self.after_repos)
        if self.request_reload:
            self.sessionmgr.reload_pyrevit()


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


if __name__ == "__main__":
    unittest.main()
