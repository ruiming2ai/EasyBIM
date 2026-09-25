"""Updater regressions using the RepoInfo/LibGit2Sharp API shared by pyRevit 4.8-6.5."""
import importlib.util
import ntpath
import pathlib
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "lib/easybim/auto_update.py"
COMMAND_DIR = ROOT / "EasyBIM.tab/Misc Tools.panel/Auto Update.pushbutton"
EXT_ROOT = "/extensions/EasyBIM"
A = "a" * 40
B = "b" * 40
C = "c" * 40


def _load_module():
    spec = importlib.util.spec_from_file_location("auto_update_test", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _obj(**kwargs):
    return types.SimpleNamespace(**kwargs)


def _commit(sha):
    return _obj(Id=_obj(Sha=sha))


class RepoInfo:
    """The native wrapper snapshots last_commit_hash; pulls return a NEW wrapper."""
    def __init__(self, disk):
        self.directory = disk.directory
        self.name = "EasyBIM"
        self.last_commit_hash = disk.head
        self.branch = self.head_name = disk.branch
        self.username = self.password = None
        tracked = (_obj(Tip=_commit(disk.upstream_head),
                        CanonicalName=disk.upstream, RemoteName="origin")
                   if disk.upstream else None)
        self.repo = _obj(
            Info=_obj(IsHeadDetached=disk.detached),
            Head=_obj(FriendlyName=disk.branch, TrackedBranch=tracked),
            RetrieveStatus=lambda: _obj(IsDirty=disk.dirty))


class Git:
    def __init__(self, disk):
        self.disk = disk
        self.discovery = disk.directory + "/.git"
        self.opened = []
        self.read_error = False
        self.divergence = None
        self.libgit = _obj(Repository=_obj(Discover=mock.Mock(side_effect=lambda root: self.discovery)))

    def get_repo(self, path):
        self.opened.append(path)
        if self.read_error:
            raise RuntimeError("repository unreadable")
        return RepoInfo(self.disk)

    def compare_branch_heads(self, info):
        if self.divergence is not None:
            return self.divergence
        return _obj(AheadBy=self.disk.ahead,
                    BehindBy=0 if self.disk.head == self.disk.upstream_head else 1)


class Updater:
    def __init__(self, git):
        self.git = git
        self.fetched = []
        self.pulled = []
        self.fetch_ok = True
        self.fetch_error = False
        self.pull_error = False
        self.after_fetch = lambda: None
        self.after_pull = lambda: None
        self.incomplete = False
        self.returned = None

    def get_updates(self, info):
        self.fetched.append(info)
        if self.fetch_error:
            raise RuntimeError("https://user:SECRET@example.com/repository")
        if self.fetch_ok:
            self.git.disk.upstream_head = self.git.disk.remote_head
            self.after_fetch()
        return self.fetch_ok

    def update_repo(self, info):
        self.pulled.append(info)
        if self.pull_error:
            raise RuntimeError("https://user:SECRET@example.com/repository")
        if not self.incomplete:
            self.git.disk.head = self.git.disk.upstream_head
        self.returned = RepoInfo(self.git.disk)
        self.after_pull()
        return self.returned

    def get_all_extension_repos(self):
        raise AssertionError("Unrelated repository enumeration is broken")

    get_thirdparty_ext_repos = get_all_extension_repos
    check_for_updates = get_all_extension_repos

    def update_pyrevit(self):
        raise AssertionError("Must never update other repositories")


class Lock:
    def __init__(self, events):
        self.events = events
        self.released = self.disposed = False

    def ReleaseMutex(self):
        self.released = True
        self.events.append("release")

    def Dispose(self):
        self.disposed = True


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.store = {}
        self.events = []
        self.disk = _obj(directory=EXT_ROOT, head=A, upstream_head=A, remote_head=A,
                         branch="main", upstream="refs/remotes/origin/main",
                         ahead=0, dirty=False, detached=False)
        self.git = Git(self.disk)
        self.updater = Updater(self.git)
        self.lock = Lock(self.events)
        self.core = _obj(directory="/pyRevit")
        self.versionmgr = _obj(get_pyrevit_repo=mock.Mock(side_effect=lambda: self.core))
        self.messages = mock.Mock(side_effect=lambda *a, **k: self.events.append("message"))
        self.reload = mock.Mock(side_effect=self._reload)
        self.logger = mock.Mock()
        patches = {
            "_get_extension_root": lambda: EXT_ROOT,
            "_get_git": lambda: self.git,
            "_get_version_manager": lambda: self.versionmgr,
            "_get_pyrevit_home": lambda: "/pyRevit",
            "_get_native_updater": lambda: self.updater,
            "_get_session_manager": lambda: _obj(reload_pyrevit=self.reload),
            "_get_envvar": lambda key, default=None: self.store.get(key, default),
            "_set_envvar": self._set,
            "_try_acquire_startup_lock": lambda: self.lock,
            "_show_message": self.messages,
            "_log": self.logger,
        }
        for name, replacement in patches.items():
            patcher = mock.patch.object(self.module, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.module.record_loaded_revision()
        self.git.opened[:] = []

    def _set(self, key, value):
        self.store[key] = value
        return True

    def _reload(self):
        self.events.append("reload")
        self.module.record_loaded_revision()
        # startup.py executes again, but the session attempt guard prevents
        # it from queuing a second update under the reload.
        self.assertFalse(self.module.queue_startup_auto_update())

    def _run(self, startup=False):
        result = (self.module.run_startup_auto_update() if startup else
                  self.module.run_manual_auto_update())
        self.assertFalse(self.store.get(self.module.AUTO_UPDATE_RUNNING_ENVVAR))
        return result

    def _assert_failed(self, result, status=None):
        self.assertNotEqual(self.module.STATUS_UP_TO_DATE, result["status"])
        if status:
            self.assertEqual(status, result["status"])
        self.reload.assert_not_called()
        self.assertTrue(self.lock.released)
        self.assertTrue(self.lock.disposed)
        self.messages.assert_called_once()
        self.assertTrue(self.messages.call_args.kwargs["warn"])

    def test_fetches_even_when_cached_upstream_says_current(self):
        self.disk.remote_head = B
        result = self._run()
        self.assertEqual(self.module.STATUS_UPDATED, result["status"])
        self.assertTrue(result["verified"])
        self.assertEqual("reloaded", result["reload_status"])
        self.assertEqual(["EasyBIM"], result["updated_repos"])
        self.assertEqual((A, B, B), (result["before_head"], result["after_head"], result["upstream_head"]))
        self.assertEqual(1, len(self.updater.pulled))
        self.assertEqual(A, self.updater.pulled[0].last_commit_hash)
        self.assertEqual(B, self.updater.returned.last_commit_hash)
        self.assertTrue(all(path.startswith(EXT_ROOT) for path in self.git.opened))
        self.reload.assert_called_once()
        self.assertEqual(["release", "message", "reload"], self.events)

    def test_broken_global_enumerators_cannot_hide_successful_update(self):
        self.disk.remote_head = B
        result = self._run()
        self.assertEqual(self.module.STATUS_UPDATED, result["status"])
        self.reload.assert_called_once()

    def test_stale_global_snapshots_are_not_used_to_decide_reload(self):
        stale = RepoInfo(self.disk)
        self.disk.remote_head = B
        with mock.patch.object(self.updater, "get_all_extension_repos", return_value=[stale]) as enumerate_repos:
            result = self._run()
        enumerate_repos.assert_not_called()
        self.assertEqual(self.module.STATUS_UPDATED, result["status"])
        self.reload.assert_called_once()

    def test_current_files_and_session_give_a_verified_manual_answer(self):
        result = self._run()
        self.assertEqual(self.module.STATUS_UP_TO_DATE, result["status"])
        self.assertTrue(result["verified"])
        self.assertEqual([], result["updated_repos"])
        self.assertEqual([], self.updater.pulled)
        self.assertEqual(1, len(self.updater.fetched))
        self.reload.assert_not_called()
        self.messages.assert_called_once()
        self.assertIn("already up to date", self.messages.call_args.args[0])
        self.assertIn(A[:12], self.messages.call_args.args[0])
        self.assertIn("main", self.messages.call_args.args[0])

    def test_files_updated_by_another_revit_instance_require_reload(self):
        self.disk.head = self.disk.remote_head = self.disk.upstream_head = B
        result = self._run()
        self.assertEqual(self.module.STATUS_RELOADED, result["status"])
        self.assertEqual([], result["updated_repos"])
        self.assertEqual([], self.updater.pulled)
        self.reload.assert_called_once()
        self.assertEqual(B, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]["head"])

    def test_second_click_after_update_does_not_reload_again(self):
        self.disk.remote_head = B
        self._run()
        result = self._run()
        self.assertEqual(self.module.STATUS_UP_TO_DATE, result["status"])
        self.reload.assert_called_once()
        self.assertEqual(1, len(self.updater.pulled))
        self.assertEqual(2, len(self.updater.fetched))
        self.assertEqual(2, self.messages.call_count)

    def test_missing_loaded_marker_does_not_claim_current_session(self):
        self.store.pop(self.module.AUTO_UPDATE_LOADED_ENVVAR)
        result = self._run()
        self._assert_failed(result, self.module.STATUS_SESSION_UNKNOWN)
        self.assertTrue(result["verified"])
        self.assertEqual("unknown", result["reload_status"])

    def test_marker_from_another_repository_does_not_prove_loaded_version(self):
        self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR] = {"repo_key": "/other", "head": A}
        self._assert_failed(self._run(), self.module.STATUS_SESSION_UNKNOWN)

    def test_a_pull_that_installs_changes_can_establish_a_missing_baseline(self):
        self.store.pop(self.module.AUTO_UPDATE_LOADED_ENVVAR)
        self.disk.remote_head = B
        result = self._run()
        self.assertEqual(self.module.STATUS_UPDATED, result["status"])
        self.assertEqual(B, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]["head"])

    def test_failed_fetch_is_not_up_to_date_and_never_pulls(self):
        self.updater.fetch_ok = False
        result = self._run()
        self._assert_failed(result, self.module.STATUS_VERIFICATION_FAILED)
        self.assertFalse(result["verified"])
        self.assertEqual([], self.updater.pulled)

    def test_fetch_auth_exception_is_reported_without_credentials(self):
        self.updater.fetch_error = True
        self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)
        self.assertNotIn("SECRET", str(self.messages.call_args_list))
        self.assertNotIn("SECRET", str(self.logger.call_args_list))

    def test_failed_pull_is_reported_without_credentials(self):
        self.disk.remote_head = B
        self.updater.pull_error = True
        self._assert_failed(self._run(), self.module.STATUS_UPDATE_FAILED)
        self.assertNotIn("SECRET", str(self.messages.call_args_list))

    def test_unchanged_head_after_pull_is_not_success(self):
        self.disk.remote_head = B
        self.updater.incomplete = True
        self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)

    def test_conflicts_after_nonthrowing_pull_are_not_success(self):
        self.disk.remote_head = B
        self.updater.incomplete = True
        self.updater.after_pull = lambda: setattr(self.disk, "dirty", True)
        self._assert_failed(self._run(), self.module.STATUS_LOCAL_CHANGES)

    def test_dirty_worktree_is_not_pulled_or_overwritten(self):
        self.disk.dirty = True
        self._assert_failed(self._run(), self.module.STATUS_LOCAL_CHANGES)
        self.assertEqual([], self.updater.fetched)
        self.assertEqual([], self.updater.pulled)

    def test_local_edits_arriving_during_fetch_prevent_pull(self):
        self.disk.remote_head = B
        self.updater.after_fetch = lambda: setattr(self.disk, "dirty", True)
        self._assert_failed(self._run(), self.module.STATUS_LOCAL_CHANGES)
        self.assertEqual([], self.updater.pulled)

    def test_ahead_or_diverged_branches_are_left_alone(self):
        for behind in (0, 1):
            with self.subTest(behind=behind):
                self.messages.reset_mock()
                self.disk.remote_head = B
                self.git.divergence = _obj(AheadBy=1, BehindBy=behind)
                self._assert_failed(self._run(), self.module.STATUS_LOCAL_CHANGES)
                self.assertEqual([], self.updater.pulled)

    def test_detached_head_is_left_alone(self):
        self.disk.detached = True
        self._assert_failed(self._run(), self.module.STATUS_LOCAL_CHANGES)
        self.assertEqual([], self.updater.fetched)

    def test_missing_upstream_is_not_up_to_date(self):
        self.disk.upstream = None
        self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)
        self.assertEqual([], self.updater.pulled)

    def test_unknown_divergence_is_not_up_to_date(self):
        self.disk.remote_head = B
        self.git.divergence = _obj(AheadBy=None, BehindBy=None)
        self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)

    def test_branch_changes_during_fetch_stop_the_pull(self):
        self.disk.remote_head = B
        self.updater.after_fetch = lambda: setattr(self.disk, "branch", "another")
        self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)
        self.assertEqual([], self.updater.pulled)

    def test_upstream_changes_during_fetch_stop_the_pull(self):
        self.disk.remote_head = B
        self.updater.after_fetch = lambda: setattr(self.disk, "upstream", "refs/remotes/other/main")
        self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)
        self.assertEqual([], self.updater.pulled)

    def test_local_commit_changes_during_fetch_stop_the_pull(self):
        self.disk.remote_head = B
        self.updater.after_fetch = lambda: setattr(self.disk, "head", C)
        self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)

    def test_invalid_native_pull_result_is_not_success(self):
        self.disk.remote_head = B
        with mock.patch.object(self.updater, "update_repo", return_value=None):
            self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)

    def test_native_result_from_another_repo_is_rejected(self):
        self.disk.remote_head = B
        def wrong_repo():
            self.updater.returned.directory = "/other"
        self.updater.after_pull = wrong_repo
        self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)

    def test_disk_commit_disagrees_with_returned_commit(self):
        self.disk.remote_head = B
        self.updater.after_pull = lambda: setattr(self.disk, "head", C)
        self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)

    def test_own_repository_read_failure_after_pull_reports_changed_files(self):
        self.disk.remote_head = B
        self.updater.after_pull = lambda: setattr(self.git, "read_error", True)
        result = self._run()
        self._assert_failed(result, self.module.STATUS_VERIFICATION_FAILED)
        self.assertEqual(["EasyBIM"], result["updated_repos"])
        self.assertIn("Files changed", self.messages.call_args.args[0])

    def test_updater_import_failure_is_visible(self):
        with mock.patch.object(self.module, "_get_native_updater", side_effect=ImportError("unavailable")):
            self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)

    def test_missing_native_apis_fail_closed(self):
        for name in ("get_updates", "update_repo"):
            with self.subTest(name=name), mock.patch.object(self.updater, name, None):
                self.messages.reset_mock()
                self._assert_failed(self._run(), self.module.STATUS_VERIFICATION_FAILED)

    def test_reload_exception_restores_previous_loaded_marker(self):
        self.disk.remote_head = B
        old = dict(self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR])
        def broken_reload():
            self.module.record_loaded_revision()
            raise RuntimeError("reload failed after startup ran")
        self.reload.side_effect = broken_reload
        result = self._run()
        self.assertEqual(self.module.STATUS_RELOAD_FAILED, result["status"])
        self.assertTrue(result["verified"])
        self.assertEqual("failed", result["reload_status"])
        self.assertEqual(old, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR])
        self.assertEqual(2, self.messages.call_count)
        self.assertTrue(self.lock.released)

    def test_reload_without_startup_confirmation_is_not_success(self):
        self.disk.remote_head = B
        self.reload.side_effect = None
        result = self._run()
        self.assertEqual(self.module.STATUS_RELOAD_FAILED, result["status"])
        self.assertEqual(A, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]["head"])

    def test_missing_reload_api_is_reported(self):
        self.disk.remote_head = B
        with mock.patch.object(self.module, "_get_session_manager", side_effect=ImportError("no loader")):
            self._assert_failed(self._run(), self.module.STATUS_RELOAD_FAILED)

    def test_cannot_reload_without_persisting_guard(self):
        self.disk.remote_head = B
        with mock.patch.object(self.module, "mark_startup_attempted", return_value=False):
            self._assert_failed(self._run(), self.module.STATUS_RELOAD_FAILED)

    def test_startup_current_is_quiet_and_performs_only_own_fetch(self):
        result = self._run(startup=True)
        self.assertEqual(self.module.STATUS_UP_TO_DATE, result["status"])
        self.messages.assert_not_called()
        self.reload.assert_not_called()
        self.assertEqual(1, len(self.updater.fetched))

    def test_startup_changed_notifies_then_reloads_once(self):
        self.disk.remote_head = B
        result = self._run(startup=True)
        self.assertEqual(self.module.STATUS_UPDATED, result["status"])
        self.assertEqual(["release", "message", "reload"], self.events)
        self.assertTrue(self.module.should_skip_startup(self.module.get_startup_guard_state()))

    def test_startup_failures_log_without_dialogs(self):
        self.updater.fetch_ok = False
        result = self._run(startup=True)
        self.assertEqual(self.module.STATUS_VERIFICATION_FAILED, result["status"])
        self.messages.assert_not_called()
        self.logger.assert_called()

    def test_startup_can_refresh_files_updated_elsewhere_without_popup(self):
        self.disk.head = self.disk.upstream_head = self.disk.remote_head = B
        result = self._run(startup=True)
        self.assertEqual(self.module.STATUS_RELOADED, result["status"])
        self.reload.assert_called_once()
        self.messages.assert_not_called()

    def test_queue_is_consumed_once_and_guard_survives_reload(self):
        self.assertTrue(self.module.queue_startup_auto_update())
        self.assertTrue(self.module.has_pending_startup_auto_update())
        self.module.run_pending_startup_auto_update()
        self.assertFalse(self.module.has_pending_startup_auto_update())
        self.assertFalse(self.module.queue_startup_auto_update())
        self.assertIsNone(self.module.run_pending_startup_auto_update())
        self.assertEqual(1, len(self.updater.fetched))

    def test_manual_click_consumes_pending_startup_to_avoid_duplicate_check(self):
        self.module.queue_startup_auto_update()
        self._run()
        self.assertFalse(self.module.has_pending_startup_auto_update())
        self.assertIsNone(self.module.run_pending_startup_auto_update())
        self.assertEqual(1, len(self.updater.fetched))

    def test_reload_refreshes_marker_even_when_guard_already_set(self):
        self.module.mark_startup_attempted()
        self.disk.head = B
        self.module.record_loaded_revision()
        self.assertEqual(B, self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR]["head"])
        self.assertFalse(self.module.queue_startup_auto_update())

    def test_record_failure_invalidates_old_marker(self):
        self.git.read_error = True
        self.module.record_loaded_revision()
        self.assertIsNone(self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR])

    def test_dirty_files_cannot_be_recorded_as_a_clean_loaded_commit(self):
        self.disk.dirty = True
        self.module.record_loaded_revision()
        self.assertIsNone(self.store[self.module.AUTO_UPDATE_LOADED_ENVVAR])

    def test_record_loaded_revision_does_no_network_work(self):
        self.module.record_loaded_revision()
        self.assertEqual([], self.updater.fetched)
        self.assertEqual([], self.updater.pulled)

    def test_manual_busy_is_visible_and_startup_busy_is_quiet(self):
        with mock.patch.object(self.module, "_try_acquire_startup_lock", return_value=False):
            result = self._run()
            self.assertEqual(self.module.STATUS_SKIPPED_LOCKED, result["status"])
            self.messages.assert_called_once()
            self.messages.reset_mock()
            self._run(startup=True)
            self.messages.assert_not_called()
        self.assertEqual([], self.updater.fetched)
        self.assertFalse(self.lock.released)

    def test_unavailable_mutex_fails_closed(self):
        with mock.patch.object(self.module, "_try_acquire_startup_lock", return_value=None):
            result = self._run()
        self.assertEqual(self.module.STATUS_VERIFICATION_FAILED, result["status"])
        self.assertEqual([], self.updater.fetched)
        self.messages.assert_called_once()

    def test_modal_reentry_cannot_start_another_update(self):
        nested = []
        def modal(*args, **kwargs):
            if not nested:
                nested.append(None)
                nested[0] = self.module.run_manual_auto_update()
        self.messages.side_effect = modal
        self._run()
        self.assertEqual(self.module.STATUS_SKIPPED_LOCKED, nested[0]["status"])
        self.assertEqual(1, len(self.updater.fetched))

    def test_missing_process_state_prevents_unprotected_updates(self):
        with mock.patch.object(self.module, "_set_envvar", return_value=False):
            result = self.module.run_manual_auto_update()
        self.assertEqual(self.module.STATUS_VERIFICATION_FAILED, result["status"])
        self.assertEqual([], self.updater.fetched)

    def test_core_is_excluded_using_real_versionmgr_namespace(self):
        self.core.directory = self.disk.directory
        self._assert_failed(self._run(), self.module.STATUS_REPO_NOT_FOUND)
        self.versionmgr.get_pyrevit_repo.assert_called()
        self.assertEqual([], self.updater.fetched)

    def test_home_path_still_protects_core_when_repo_getter_returns_none(self):
        self.core = None
        with mock.patch.object(self.module, "_get_pyrevit_home", return_value=EXT_ROOT + "/pyrevitlib"):
            self._assert_failed(self._run(), self.module.STATUS_REPO_NOT_FOUND)

    def test_non_git_install_has_a_manual_explanation(self):
        self.git.discovery = None
        self._assert_failed(self._run(), self.module.STATUS_REPO_NOT_FOUND)
        self.assertIn("ZIP", self.messages.call_args.args[0])

    def test_parent_repo_can_hold_the_extension(self):
        self.disk.directory = "/extensions"
        self.assertEqual("/extensions", self.module._find_own_repo().directory)

    def test_sibling_prefix_cannot_be_selected(self):
        self.disk.directory = "/extensions/Easy"
        self.assertIsNone(self.module._find_own_repo())

    def test_native_discovery_receives_extension_root_not_all_extensions(self):
        self.module._find_own_repo()
        self.git.libgit.Repository.Discover.assert_called_with(EXT_ROOT)

    def test_empty_repo_directory_cannot_match_by_name(self):
        self.disk.directory = ""
        self.assertIsNone(self.module._find_own_repo())

    def test_windows_paths_match_case_slashes_and_trailing_separator(self):
        with mock.patch.object(self.module, "os", _obj(path=ntpath, sep="\\")):
            first = self.module._normalize_dir("C:/Users/Name/EasyBIM/")
            second = self.module._normalize_dir("c:\\users\\NAME\\easybim")
            self.assertEqual(first, second)
            self.assertTrue(self.module._is_same_or_ancestor(first, second + "/nested"))
            self.assertFalse(self.module._is_same_or_ancestor(first, second + "2"))
            self.assertEqual("//server/share/easybim", self.module._normalize_dir("\\\\SERVER\\Share\\EasyBIM\\"))

    def test_unicode_installation_path_is_preserved(self):
        path = "/extensions/用户/EasyBIM"
        self.assertEqual(path, self.module._normalize_dir(path))
        self.assertEqual(path, self.module._safe_text(path))


class BundleAndMutexTests(unittest.TestCase):
    def test_real_extension_root(self):
        self.assertEqual(ROOT, pathlib.Path(_load_module()._get_extension_root()))
        self.assertTrue((ROOT / "Extension.yaml").is_file())

    def test_button_stays_available_without_a_document(self):
        self.assertIn("context: zero-doc", (COMMAND_DIR / "bundle.yaml").read_text())

    def test_startup_records_revision_before_the_guarded_queue(self):
        source = (ROOT / "startup.py").read_text()
        self.assertLess(source.index("auto_update.record_loaded_revision()"),
                        source.index("auto_update.queue_startup_auto_update()"))

    def test_no_shell_git_or_update_everything_dependency(self):
        source = MODULE_PATH.read_text()
        for forbidden in ("subprocess", "shutil", "update_pyrevit", "get_all_extension_repos("):
            self.assertNotIn(forbidden, source)

    def test_startup_guard_tolerates_malformed_storage(self):
        module = _load_module()
        for raw in (None, "invalid", {"attempted": True, "attempted_at": "bad"}):
            with mock.patch.object(module, "_get_envvar", return_value=raw):
                state = module.get_startup_guard_state()
                self.assertEqual(0.0, state["attempted_at"])
                self.assertEqual(isinstance(raw, dict), module.should_skip_startup(state))

    def test_native_mutex_busy_abandoned_and_acquired_paths(self):
        module = _load_module()
        class Abandoned(Exception):
            pass
        for outcome in (False, True, Abandoned()):
            with self.subTest(outcome=outcome):
                lock = mock.Mock()
                lock.WaitOne.side_effect = outcome if isinstance(outcome, Exception) else None
                lock.WaitOne.return_value = outcome
                threading = types.ModuleType("System.Threading")
                threading.AbandonedMutexException = Abandoned
                threading.Mutex = mock.Mock(return_value=lock)
                with mock.patch.dict("sys.modules", {"System.Threading": threading}):
                    acquired = module._try_acquire_startup_lock()
                lock.WaitOne.assert_called_once_with(0, False)
                if outcome is False:
                    self.assertIs(False, acquired)
                    lock.Dispose.assert_called_once()
                    lock.ReleaseMutex.assert_not_called()
                else:
                    self.assertIs(lock, acquired)
                    module._release_startup_lock(acquired)
                    lock.ReleaseMutex.assert_called_once()
                    lock.Dispose.assert_called_once()


if __name__ == "__main__":
    unittest.main()
