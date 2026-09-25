# -*- coding: utf-8 -*-
"""Verify and update this extension through pyRevit's own Git APIs.

Remote freshness and the revision loaded by this Revit process are separate
facts. Never infer either one from an unchanged local commit after a pull.
"""
from __future__ import print_function

import os
import time


AUTO_UPDATE_GUARD_ENVVAR = "EASYBIM_AUTO_UPDATE_RAN"
AUTO_UPDATE_PENDING_ENVVAR = "EASYBIM_AUTO_UPDATE_PENDING"
AUTO_UPDATE_LOADED_ENVVAR = "EASYBIM_AUTO_UPDATE_LOADED"
AUTO_UPDATE_RUNNING_ENVVAR = "EASYBIM_AUTO_UPDATE_RUNNING"
AUTO_UPDATE_MUTEX_NAME = "Global\\EasyBIMAutoUpdate"
TITLE = "Auto Update"
STATUS_NO_OP = "no_op"
STATUS_SKIPPED_LOCKED = "skipped_locked"
STATUS_UPDATED = "updated"
STATUS_UP_TO_DATE = "up_to_date"
STATUS_RELOADED = "reloaded"
STATUS_SESSION_UNKNOWN = "session_unknown"
STATUS_REPO_NOT_FOUND = "repo_not_found"
STATUS_UPDATE_FAILED = "update_failed"
STATUS_VERIFICATION_FAILED = "verification_failed"
STATUS_LOCAL_CHANGES = "local_changes"
STATUS_RELOAD_FAILED = "reload_failed"
_PENDING_RESOLVED = [False]

try:
    _TEXT_TYPE = unicode
except NameError:
    _TEXT_TYPE = str


class _UpdateError(Exception):
    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status


def _get_envvar(name, default=None):
    try:
        from pyrevit import script
        value = script.get_envvar(name)
        return default if value is None else value
    except Exception:
        return default


def _set_envvar(name, value):
    try:
        from pyrevit import script
        script.set_envvar(name, value)
        return True
    except Exception:
        return False


def should_skip_startup(guard_state):
    return bool((guard_state or {}).get("attempted", False))


def get_startup_guard_state():
    raw = _get_envvar(AUTO_UPDATE_GUARD_ENVVAR, {})
    if not isinstance(raw, dict):
        raw = {}
    try:
        attempted_at = float(raw.get("attempted_at", 0.0))
    except Exception:
        attempted_at = 0.0
    return {"attempted": bool(raw.get("attempted", False)),
            "attempted_at": attempted_at}


def mark_startup_attempted():
    return _set_envvar(AUTO_UPDATE_GUARD_ENVVAR,
                       {"attempted": True, "attempted_at": time.time()})


def record_loaded_revision():
    """Called on every extension load, before queuing any network work.

    This is deliberately separate from the once-per-session attempt guard:
    a pyRevit reload must refresh the baseline even when no update is queued.
    Dirty files cannot be identified by their commit alone.
    """
    state = None
    try:
        info = _find_own_repo()
        if info is not None:
            _require_clean(info)
            state = {"repo_key": _get_repo_key(info), "head": _head_hash(info)}
    except Exception:
        _log("Could not record the loaded EasyBIM revision.")
    return _set_envvar(AUTO_UPDATE_LOADED_ENVVAR, state)


def queue_startup_auto_update():
    """Defer network work until Revit is interactive."""
    if should_skip_startup(get_startup_guard_state()):
        return False
    if not _set_envvar(AUTO_UPDATE_PENDING_ENVVAR, True):
        return False
    _PENDING_RESOLVED[0] = False
    return True


def has_pending_startup_auto_update():
    if _PENDING_RESOLVED[0]:
        return False
    pending = bool(_get_envvar(AUTO_UPDATE_PENDING_ENVVAR, False))
    if not pending:
        _PENDING_RESOLVED[0] = True
    return pending


def _consume_pending_startup():
    _set_envvar(AUTO_UPDATE_PENDING_ENVVAR, None)
    _PENDING_RESOLVED[0] = True


def run_pending_startup_auto_update():
    _consume_pending_startup()
    if should_skip_startup(get_startup_guard_state()):
        return None
    mark_startup_attempted()
    return run_startup_auto_update()


def run_startup_auto_update():
    return _run_easybim_update(trigger="startup")


def run_manual_auto_update():
    return _run_easybim_update(trigger="manual")


def _result(trigger, updated_repos=None, status=STATUS_NO_OP):
    return {"status": status, "trigger": trigger,
            "updated_repos": list(updated_repos or []),
            "verified": False, "reload_status": "not_needed",
            "repo_key": "", "branch": "", "upstream": "",
            "before_head": "", "after_head": "", "upstream_head": "",
            "message": ""}


def _run_easybim_update(trigger, updater=None):
    result = _result(trigger)
    # A named Mutex is recursive on its owning thread. This process-local
    # guard also prevents modal dialogs from re-entering through another engine.
    if _get_envvar(AUTO_UPDATE_RUNNING_ENVVAR, False):
        return _failure(result, STATUS_SKIPPED_LOCKED,
                        "An EasyBIM update is already running. Try again when it finishes.")
    if not _set_envvar(AUTO_UPDATE_RUNNING_ENVVAR, True):
        return _failure(result, STATUS_VERIFICATION_FAILED,
                        "Could not initialize EasyBIM update session state. Reload pyRevit and try again.")
    try:
        if trigger == "manual":
            mark_startup_attempted()
            _consume_pending_startup()
        update_lock = _try_acquire_startup_lock()
        if update_lock is False:
            return _failure(result, STATUS_SKIPPED_LOCKED,
                            "Another Revit instance is updating EasyBIM. Try again when it finishes.")
        if update_lock is None:
            return _failure(result, STATUS_VERIFICATION_FAILED,
                            "Could not acquire the EasyBIM update lock. Nothing was updated.")
        try:
            try:
                _verify_and_update(result, updater or _get_native_updater())
            except _UpdateError as error:
                result["status"] = error.status
                result["message"] = _safe_text(error)
            except Exception:
                result["status"] = STATUS_VERIFICATION_FAILED
                result["message"] = (
                    "Could not read EasyBIM repository information using this pyRevit installation. "
                    "The installed version could not be verified.")
        finally:
            _release_startup_lock(update_lock)
        # Do not retain the cross-process lock across dialogs or a reload.
        if not result["verified"]:
            if result["updated_repos"]:
                result["message"] += "\n\nFiles changed during the pull, but the result could not be verified."
            return _failure(result, result["status"], result["message"])
        return _finish_verified_update(result)
    finally:
        _set_envvar(AUTO_UPDATE_RUNNING_ENVVAR, False)


def _verify_and_update(result, updater):
    if not all(callable(getattr(updater, name, None))
               for name in ("get_updates", "update_repo")):
        raise _UpdateError(STATUS_VERIFICATION_FAILED,
                           "This pyRevit build does not provide the required single-repository update APIs.")
    info = _find_own_repo()
    if info is None:
        raise _UpdateError(STATUS_REPO_NOT_FOUND,
                           "EasyBIM could not find its own Git repository, or it shares pyRevit's core repository. "
                           "Nothing was updated. A ZIP installation cannot update itself.")
    git = _get_git()
    before = _inspect_repo(info)
    result.update(before)
    result["before_head"] = before["after_head"]
    # Unlike check_for_updates(), this fetch is confined to our repository,
    # uses pyRevit's configured credentials, and has an explicit success flag.
    try:
        fetched = updater.get_updates(info)
    except Exception:
        fetched = False
    if fetched is not True:
        raise _UpdateError(STATUS_VERIFICATION_FAILED,
                           "Could not fetch EasyBIM's upstream branch. Check the network and pyRevit repository credentials, "
                           "then try again. The latest version could not be verified.")
    info = git.get_repo(info.directory)
    current = _inspect_repo(info)
    _require_same_checkout(before, current)
    if before["after_head"] != current["after_head"]:
        raise _UpdateError(STATUS_VERIFICATION_FAILED,
                           "EasyBIM's local commit changed during the check. Try again.")
    result.update(current)
    if current["after_head"] != current["upstream_head"]:
        divergence = git.compare_branch_heads(info)
        ahead = getattr(divergence, "AheadBy", None)
        behind = getattr(divergence, "BehindBy", None)
        if ahead is None or behind is None or ahead < 0 or behind < 0:
            raise _UpdateError(STATUS_VERIFICATION_FAILED,
                               "Could not compare EasyBIM with its fetched upstream branch. Nothing was pulled.")
        if ahead > 0:
            reason = "has diverged from its upstream" if behind > 0 else "contains local commits"
            raise _UpdateError(STATUS_LOCAL_CHANGES,
                               "EasyBIM {}. Resolve the local commits before updating; nothing was pulled.".format(reason))
        if behind == 0:
            raise _UpdateError(STATUS_VERIFICATION_FAILED,
                               "EasyBIM's commit comparison is inconsistent. Nothing was pulled.")
        try:
            updated = updater.update_repo(info)
        except Exception:
            raise _UpdateError(STATUS_UPDATE_FAILED,
                               "EasyBIM could not pull the latest changes. Check network access, repository credentials, "
                               "and Git conflicts before trying again.")
        # The native updater returns a NEW RepoInfo. Its input is a snapshot
        # and must never be used to decide whether the pull installed changes.
        if updated is None or _get_repo_key(updated) != before["repo_key"]:
            raise _UpdateError(STATUS_VERIFICATION_FAILED,
                               "The updater did not return valid EasyBIM repository information. Files may have changed.")
        pulled_head = _head_hash(updated)
        if pulled_head != before["after_head"]:
            result["updated_repos"] = [_get_repo_name(updated)]
            result["after_head"] = pulled_head
        # Reopen only our repository to verify actual on-disk state, including
        # conflicts (LibGit2Sharp can return from Pull without throwing).
        current = _inspect_repo(git.get_repo(updated.directory))
        _require_same_checkout(before, current)
        if current["after_head"] != pulled_head:
            raise _UpdateError(STATUS_VERIFICATION_FAILED,
                               "EasyBIM's files no longer match the commit returned by the updater. Try again.")
        result.update(current)
    if current["after_head"] != current["upstream_head"]:
        raise _UpdateError(STATUS_VERIFICATION_FAILED,
                           "The pull did not bring EasyBIM to its fetched upstream commit. Check the repository for conflicts.")
    result["verified"] = True


def _require_clean(info):
    if info.repo.RetrieveStatus().IsDirty:
        raise _UpdateError(STATUS_LOCAL_CHANGES,
                           "EasyBIM contains local file changes or conflicts. Save or resolve them before updating.")


def _head_hash(info):
    head = _safe_text(info.last_commit_hash).strip()
    if not head:
        raise _UpdateError(STATUS_VERIFICATION_FAILED, "EasyBIM's commit could not be read.")
    return head


def _inspect_repo(info):
    _require_clean(info)
    repo = info.repo
    if repo.Info.IsHeadDetached:
        raise _UpdateError(STATUS_LOCAL_CHANGES,
                           "EasyBIM is on a detached commit. Select its intended tracking branch before updating.")
    branch = repo.Head
    upstream = branch.TrackedBranch
    if upstream is None or upstream.Tip is None:
        raise _UpdateError(STATUS_VERIFICATION_FAILED,
                           "EasyBIM's current branch has no usable upstream branch. Configure its upstream before updating.")
    upstream_head = _safe_text(upstream.Tip.Id.Sha).strip()
    if not upstream_head:
        raise _UpdateError(STATUS_VERIFICATION_FAILED, "EasyBIM's upstream commit could not be read.")
    return {"repo_key": _get_repo_key(info), "branch": _safe_text(branch.FriendlyName),
            "upstream": _safe_text(upstream.CanonicalName),
            "after_head": _head_hash(info), "upstream_head": upstream_head}


def _require_same_checkout(before, after):
    if any(before[key] != after[key] for key in ("repo_key", "branch", "upstream")):
        raise _UpdateError(STATUS_VERIFICATION_FAILED,
                           "EasyBIM's repository, branch, or upstream changed during the update. Try again.")


def _finish_verified_update(result):
    loaded = _get_envvar(AUTO_UPDATE_LOADED_ENVVAR, None)
    known = (isinstance(loaded, dict)
             and loaded.get("repo_key") == result["repo_key"] and bool(loaded.get("head")))
    installed = bool(result["updated_repos"])
    # A pull that changed files also proves a reload is required, even if this
    # is the first update from a session started before tracking was introduced.
    needs_reload = installed or (known and loaded["head"] != result["after_head"])
    if not needs_reload:
        if not known:
            result["reload_status"] = "unknown"
            return _failure(result, STATUS_SESSION_UNKNOWN,
                            "EasyBIM's files match the fetched upstream, but this session's loaded version is unknown. "
                            "Reload pyRevit or restart Revit once to establish the loaded version.")
        result["status"] = STATUS_UP_TO_DATE
        result["message"] = "EasyBIM is already up to date, and this Revit session has that version loaded."
        _report(result)
        return result
    try:
        reload_pyrevit = _get_session_manager().reload_pyrevit
        if not callable(reload_pyrevit):
            raise RuntimeError("reload unavailable")
    except Exception:
        result["reload_status"] = "failed"
        return _failure(result, STATUS_RELOAD_FAILED,
                        "EasyBIM's files are verified, but pyRevit's reload command is unavailable. Restart Revit to load them.")
    if not mark_startup_attempted():
        result["reload_status"] = "failed"
        return _failure(result, STATUS_RELOAD_FAILED,
                        "EasyBIM's files are verified, but the reload guard could not be saved. Restart Revit to load them.")
    _consume_pending_startup()
    result["reload_status"] = "pending"
    result["status"] = STATUS_UPDATED if installed else STATUS_RELOADED
    result["message"] = ("EasyBIM installed changes. pyRevit is reloading." if installed else
                         "EasyBIM's latest files are already installed, but this Revit session has an older version loaded. "
                         "pyRevit is reloading.")
    _report(result)
    try:
        reload_pyrevit()
        refreshed = _get_envvar(AUTO_UPDATE_LOADED_ENVVAR, None)
        if not isinstance(refreshed, dict) or refreshed != {
                "repo_key": result["repo_key"], "head": result["after_head"]}:
            raise RuntimeError("loaded revision was not confirmed during reload")
    except Exception:
        _set_envvar(AUTO_UPDATE_LOADED_ENVVAR, loaded)
        result["reload_status"] = "failed"
        return _failure(result, STATUS_RELOAD_FAILED,
                        "EasyBIM's files are verified, but the reload did not confirm that version was loaded. "
                        "Restart Revit to finish applying the update.")
    result["reload_status"] = "reloaded"
    return result


def _failure(result, status, message):
    result["status"] = status
    result["message"] = message
    _report(result, warn=True)
    return result


def _report(result, warn=False):
    message = result["message"]
    if result["branch"]:
        message += "\n\nBranch: {}\nLocal: {}\nUpstream: {} ({})".format(
            result["branch"], result["after_head"][:12],
            result["upstream"], result["upstream_head"][:12])
    # Do not echo native exceptions: they can contain authenticated URLs.
    _log("{}: {}".format(result["status"], message))
    if result["trigger"] == "manual" or (result["updated_repos"] and not warn):
        _show_message(message, warn=warn)


def _log(message):
    try:
        from pyrevit.coreutils.logger import get_logger
        get_logger(__name__).debug(message)
    except Exception:
        pass


def _get_native_updater():
    from pyrevit.versionmgr import updater
    return updater


def _get_git():
    from pyrevit.coreutils import git
    return git


def _get_version_manager():
    from pyrevit import versionmgr
    return versionmgr


def _get_pyrevit_home():
    from pyrevit import HOME_DIR
    return HOME_DIR


def _get_session_manager():
    from pyrevit.loader import sessionmgr
    return sessionmgr


def _get_extension_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _normalize_dir(path):
    text = _safe_text(path).strip()
    if not text:
        return ""
    text = text.replace("/", os.sep).replace("\\", os.sep)
    return os.path.normcase(os.path.realpath(text)).replace("\\", "/").rstrip("/")


def _is_same_or_ancestor(candidate, target):
    return bool(candidate and target and
                (candidate == target or target.startswith(candidate + "/")))


def _find_own_repo():
    """Discover only the nearest Git repository containing this extension."""
    root = _get_extension_root()
    git = _get_git()
    repo_path = git.libgit.Repository.Discover(root)
    if not repo_path:
        return None
    info = git.get_repo(repo_path)
    directory = _get_repo_key(info)
    if not _is_same_or_ancestor(directory, _normalize_dir(root)):
        return None
    # The getter lives on versionmgr, not versionmgr.updater. HOME_DIR also
    # protects core installations for which get_pyrevit_repo() returns None.
    core = _get_version_manager().get_pyrevit_repo()
    if directory == _get_repo_key(core) or _is_same_or_ancestor(
            directory, _normalize_dir(_get_pyrevit_home())):
        return None
    return info


def _get_repo_key(info):
    return _normalize_dir(getattr(info, "directory", ""))


def _get_repo_name(info):
    return _safe_text(getattr(info, "name", "")).strip() or _get_repo_key(info)


def _safe_text(value):
    if value is None:
        return ""
    try:
        return _TEXT_TYPE(value)
    except Exception:
        return ""


def _try_acquire_startup_lock():
    startup_lock = None
    try:
        from System.Threading import AbandonedMutexException
        from System.Threading import Mutex

        startup_lock = Mutex(False, AUTO_UPDATE_MUTEX_NAME)
        try:
            acquired = bool(startup_lock.WaitOne(0, False))
        except AbandonedMutexException:
            acquired = True
    except Exception:
        _dispose_startup_lock(startup_lock)
        return None

    if acquired:
        return startup_lock

    _dispose_startup_lock(startup_lock)
    return False


def _release_startup_lock(startup_lock):
    try:
        startup_lock.ReleaseMutex()
    except Exception:
        pass
    _dispose_startup_lock(startup_lock)


def _dispose_startup_lock(startup_lock):
    if startup_lock is None:
        return

    dispose = getattr(startup_lock, "Dispose", None)
    if dispose is None:
        return

    try:
        dispose()
    except Exception:
        pass


def _show_message(message, warn=False):
    message = _safe_text(message).strip()
    if not message:
        return

    try:
        from pyrevit import forms

        forms.alert(message, title=TITLE, warn_icon=bool(warn))
        return
    except Exception:
        pass

    try:
        from Autodesk.Revit.UI import TaskDialog

        TaskDialog.Show(TITLE, message)
        return
    except Exception:
        pass

    # Last resort. Running from the Idling delegate there may be no script
    # output stream behind sys.stdout, so even this must not raise.
    try:
        print("[{}] {}".format(TITLE, message))
    except Exception:
        pass
