import importlib.util
import pathlib
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "lib" / "easybim" / "auto_update.py"
COMMAND_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Auto Update.pushbutton"


#: Where the fakes pretend this extension lives.  ``_find_own_repo`` matches
#: repositories against this module's own folder, so the loader pins the seam
#: to this path and every fake repo carries a matching ``directory``.
FAKE_EXTENSION_ROOT = "/ext/EasyBIM"


def _load_module(extension_root=FAKE_EXTENSION_ROOT):
    spec = importlib.util.spec_from_file_location("auto_update", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if extension_root is not None:
        module._get_extension_root = lambda: extension_root
    return module


class _FakeRepoInfo(object):
    def __init__(self, name, last_commit_hash, directory=None):
        self.name = name
        self.last_commit_hash = last_commit_hash
        # Default: our own repo is the one whose folder is the extension root.
        self.directory = (
            directory if directory is not None else "/ext/{}".format(name)
        )


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
        self.updated_repos = []

    def get_all_extension_repos(self):
        return list(self.repos)

    def check_for_updates(self):
        self.check_count += 1
        if self.check_exception is not None:
            raise self.check_exception
        return self.pending_updates

    def update_repo(self, repo_info):
        self.call_count += 1
        self.updated_repos.append(repo_info)
        self.repos = list(self.after_repos)
        if self.request_reload:
            self.sessionmgr.reload_pyrevit()

    def update_pyrevit(self):
        # The whole point of the change: never pull every extension again.
        raise AssertionError(
            "update_pyrevit() must not be called - Auto Update is scoped to "
            "the EasyBIM repository"
        )


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
            with mock.patch.object(module, "_run_easybim_update") as run_update:
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

    def test_popup_and_reload_when_the_easybim_repo_moves(self):
        # No request_reload here on purpose: the reload must come from our own
        # head diff, not from the updater asking for one.
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "def456")],
            sessionmgr=sessionmgr,
        )

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with mock.patch.object(module, "_show_message") as show_message:
                    result = module.run_manual_auto_update()

        show_message.assert_called_once()
        message = show_message.call_args[0][0]
        self.assertIn("EasyBIM Auto Update installed changes:", message)
        self.assertIn("- EasyBIM", message)
        self.assertIn("pyRevit is reloading.", message)
        self.assertEqual(result["status"], module.STATUS_UPDATED)
        self.assertEqual(result["updated_repos"], ["EasyBIM"])
        self.assertEqual(sessionmgr.reload_count, 1)

    def test_another_extension_moving_is_neither_updated_nor_reported(self):
        # The whole point: a stranger's repo advancing must not pull us into a
        # popup or a reload, and must never be handed to update_repo.
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[
                _FakeRepoInfo("EasyBIM", "abc123"),
                _FakeRepoInfo("Other.extension", "111111"),
            ],
            after_repos=[
                _FakeRepoInfo("EasyBIM", "abc123"),
                _FakeRepoInfo("Other.extension", "222222"),
            ],
            sessionmgr=sessionmgr,
        )

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with mock.patch.object(module, "_show_message") as show_message:
                    result = module.run_startup_auto_update()

        show_message.assert_not_called()
        self.assertEqual(result["updated_repos"], [])
        self.assertEqual(result["status"], module.STATUS_NO_OP)
        self.assertEqual(sessionmgr.reload_count, 0)
        self.assertEqual(
            [repo.directory for repo in updater.updated_repos],
            [FAKE_EXTENSION_ROOT],
        )

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

    def test_reload_is_restored_and_reported_when_the_pull_raises(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        original_reload = sessionmgr.reload_pyrevit

        class FailingUpdater(object):
            def get_all_extension_repos(self):
                return [_FakeRepoInfo("EasyBIM", "abc123")]

            def update_repo(self, repo_info):
                raise RuntimeError("pull failed")

        with mock.patch.object(module, "_get_native_updater", return_value=FailingUpdater()):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with mock.patch.object(module, "_show_message") as show_message:
                    result = module.run_manual_auto_update()

        # Restored on the way out of the failure, not just the happy path.
        self.assertIs(sessionmgr.reload_pyrevit, original_reload)
        self.assertEqual(result["status"], module.STATUS_UPDATE_FAILED)
        self.assertEqual(sessionmgr.reload_count, 0)
        show_message.assert_called_once()
        self.assertIn("pull failed", show_message.call_args[0][0])

    def test_repo_enumeration_failure_fails_closed(self):
        # Previously this fell through to updating every extension.  Now it
        # must do nothing at all: we cannot tell which repo is ours.
        module = _load_module()
        sessionmgr = _FakeSessionManager()

        class EnumerationFailingUpdater(object):
            def __init__(self):
                self.call_count = 0

            def get_all_extension_repos(self):
                raise RuntimeError("enumeration failed")

            def update_repo(self, repo_info):
                self.call_count += 1

        updater = EnumerationFailingUpdater()

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with mock.patch.object(module, "_show_message") as show_message:
                    result = module.run_manual_auto_update()

        self.assertEqual(updater.call_count, 0)
        self.assertEqual(sessionmgr.reload_count, 0)
        self.assertEqual(result["status"], module.STATUS_REPO_NOT_FOUND)
        self.assertEqual(result["updated_repos"], [])
        show_message.assert_called_once()

    def test_easybim_auto_update_has_no_local_command_runner_dependencies(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("subprocess", source)
        self.assertNotIn("shutil", source)

    def test_auto_update_never_calls_the_update_everything_routine(self):
        # The regression that matters: one stray update_pyrevit() and the tool
        # is back to pulling every extension on the machine.
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("update_pyrevit", source)


class OwnRepoOnlyTests(unittest.TestCase):
    """The scoping itself: one repository is pulled, and it is ours."""

    def _run_manual(self, module, updater, sessionmgr):
        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                with mock.patch.object(module, "_show_message") as show_message:
                    result = module.run_manual_auto_update()
        return result, show_message

    def test_only_the_easybim_repo_is_handed_to_update_repo(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[
                _FakeRepoInfo("Other.extension", "111111"),
                _FakeRepoInfo("EasyBIM", "abc123"),
                _FakeRepoInfo("Third.extension", "333333"),
            ],
            sessionmgr=sessionmgr,
        )

        self._run_manual(module, updater, sessionmgr)

        self.assertEqual(
            [repo.directory for repo in updater.updated_repos],
            [FAKE_EXTENSION_ROOT],
        )

    def test_no_reload_when_our_repo_did_not_move(self):
        # The other half of the complaint: the old tool reloaded regardless.
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            sessionmgr=sessionmgr,
        )

        result, show_message = self._run_manual(module, updater, sessionmgr)

        self.assertEqual(sessionmgr.reload_count, 0)
        self.assertEqual(result["status"], module.STATUS_NO_OP)
        show_message.assert_not_called()

    def test_fails_closed_when_our_repo_is_not_among_the_extensions(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("Other.extension", "111111")],
            sessionmgr=sessionmgr,
        )

        result, show_message = self._run_manual(module, updater, sessionmgr)

        self.assertEqual(updater.updated_repos, [])
        self.assertEqual(sessionmgr.reload_count, 0)
        self.assertEqual(result["status"], module.STATUS_REPO_NOT_FOUND)
        show_message.assert_called_once()

    def test_fails_closed_when_the_updater_has_no_per_repo_entry_point(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()

        class OldUpdater(object):
            def __init__(self):
                self.enumerated = 0

            def get_all_extension_repos(self):
                self.enumerated += 1
                return [_FakeRepoInfo("EasyBIM", "abc123")]

        updater = OldUpdater()
        result, show_message = self._run_manual(module, updater, sessionmgr)

        self.assertEqual(result["status"], module.STATUS_REPO_NOT_FOUND)
        self.assertEqual(sessionmgr.reload_count, 0)
        self.assertEqual(updater.enumerated, 0, "must bail before doing any work")
        show_message.assert_called_once()

    def test_startup_failures_stay_silent(self):
        # A machine that can never self-update must not nag every session.
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("Other.extension", "111111")],
            sessionmgr=sessionmgr,
        )

        with mock.patch.object(module, "_try_acquire_startup_lock", return_value=None):
            with mock.patch.object(module, "_get_native_updater", return_value=updater):
                with mock.patch.object(module, "_get_session_manager", return_value=sessionmgr):
                    with mock.patch.object(module, "_show_message") as show_message:
                        result = module.run_startup_auto_update()

        self.assertEqual(result["status"], module.STATUS_REPO_NOT_FOUND)
        show_message.assert_not_called()

    def test_a_reload_request_from_pyrevit_is_still_honoured(self):
        module = _load_module()
        sessionmgr = _FakeSessionManager()
        updater = _FakeUpdater(
            before_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            after_repos=[_FakeRepoInfo("EasyBIM", "abc123")],
            sessionmgr=sessionmgr,
            request_reload=True,
        )

        result, show_message = self._run_manual(module, updater, sessionmgr)

        self.assertEqual(sessionmgr.reload_count, 1)
        self.assertEqual(result["status"], module.STATUS_NO_OP)
        show_message.assert_not_called()

    def test_the_update_still_runs_without_a_session_manager(self):
        module = _load_module()
        updater = _FakeUpdater(before_repos=[_FakeRepoInfo("EasyBIM", "abc123")])

        with mock.patch.object(module, "_get_native_updater", return_value=updater):
            with mock.patch.object(
                module, "_get_session_manager", side_effect=RuntimeError("no loader")
            ):
                with mock.patch.object(module, "_show_message"):
                    result = module.run_manual_auto_update()

        self.assertEqual(
            [repo.directory for repo in updater.updated_repos],
            [FAKE_EXTENSION_ROOT],
        )
        self.assertEqual(result["status"], module.STATUS_NO_OP)


class FindOwnRepoTests(unittest.TestCase):
    """Matching this extension to its repository, by directory only."""

    def setUp(self):
        self.module = _load_module()

    def _find(self, repos, core_repo=None):
        class _Updater(object):
            def get_all_extension_repos(self):
                return list(repos)

            if core_repo is not None:
                def get_pyrevit_repo(self):
                    return core_repo

        return self.module._find_own_repo(_Updater())

    def test_matches_when_the_extension_folder_is_the_repo_root(self):
        repo = _FakeRepoInfo("EasyBIM", "abc", directory=FAKE_EXTENSION_ROOT)
        self.assertIs(self._find([repo]), repo)

    def test_matches_a_repo_that_contains_the_extension(self):
        # pyRevit discovers repos by walking up, so in a checkout holding
        # several *.extension folders the repo is a parent of ours.
        repo = _FakeRepoInfo("Monorepo", "abc", directory="/ext")
        self.assertIs(self._find([repo]), repo)

    def test_the_deepest_matching_repo_wins(self):
        outer = _FakeRepoInfo("Monorepo", "abc", directory="/ext")
        inner = _FakeRepoInfo("EasyBIM", "abc", directory=FAKE_EXTENSION_ROOT)
        self.assertIs(self._find([outer, inner]), inner)
        self.assertIs(self._find([inner, outer]), inner)

    def test_a_trailing_separator_still_matches(self):
        # LibGit2Sharp reports a working directory with a trailing separator.
        repo = _FakeRepoInfo("EasyBIM", "abc", directory=FAKE_EXTENSION_ROOT + "/")
        self.assertIs(self._find([repo]), repo)

    def test_a_sibling_sharing_a_prefix_is_not_matched(self):
        repo = _FakeRepoInfo("Easy", "abc", directory="/ext/Easy")
        self.assertIsNone(self._find([repo]))

    def test_a_repo_without_a_directory_is_never_matched_by_name(self):
        repo = _FakeRepoInfo("EasyBIM", "abc", directory="")
        self.assertIsNone(self._find([repo]))

    def test_the_pyrevit_core_clone_is_never_selected(self):
        # If EasyBIM sits inside pyRevit's own clone, the ancestor rule would
        # otherwise pick the core repo and we would pull pyRevit itself.
        core = _FakeRepoInfo("pyRevit", "abc", directory="/ext")
        self.assertIsNone(self._find([core], core_repo=core))

    def test_third_party_repos_are_preferred_over_the_full_list(self):
        ours = _FakeRepoInfo("EasyBIM", "abc", directory=FAKE_EXTENSION_ROOT)

        class _Updater(object):
            def __init__(self):
                self.used = []

            def get_thirdparty_ext_repos(self):
                self.used.append("thirdparty")
                return [ours]

            def get_all_extension_repos(self):
                self.used.append("all")
                return [ours]

        updater = _Updater()
        self.assertIs(self.module._find_own_repo(updater), ours)
        self.assertEqual(updater.used, ["thirdparty"])


class ExtensionRootTests(unittest.TestCase):
    def test_the_real_root_is_the_folder_holding_extension_yaml(self):
        # Loaded without the fake seam, so this exercises the real derivation.
        module = _load_module(extension_root=None)
        root = pathlib.Path(module._get_extension_root())

        self.assertEqual(root, REPO_ROOT)
        self.assertTrue((root / "Extension.yaml").is_file())


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
