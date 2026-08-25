# -*- coding: utf-8 -*-
"""Update the EasyBIM extension's own repository.

pyRevit's own Update pulls every enabled extension and reloads unconditionally.
This tool is scoped to one repository - the one holding this extension - and
reloads only when that repository actually moved.  Everything else on the
machine stays pyRevit >> Update's job.
"""

from __future__ import print_function

import os
import time


AUTO_UPDATE_GUARD_ENVVAR = "EASYBIM_AUTO_UPDATE_RAN"
AUTO_UPDATE_PENDING_ENVVAR = "EASYBIM_AUTO_UPDATE_PENDING"
AUTO_UPDATE_MUTEX_NAME = "Global\\EasyBIMAutoUpdate"
TITLE = "Auto Update"
STATUS_NO_OP = "no_op"
STATUS_SKIPPED_LOCKED = "skipped_locked"
STATUS_UPDATED = "updated"
STATUS_REPO_NOT_FOUND = "repo_not_found"
STATUS_UPDATE_FAILED = "update_failed"

# Once the deferred-update flag has been consumed (or was never set), quiet
# Idling ticks skip the envvar read entirely.
_PENDING_RESOLVED = [False]


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
    default_state = {"attempted": False, "attempted_at": 0.0}

    try:
        from pyrevit import script

        raw_state = script.get_envvar(AUTO_UPDATE_GUARD_ENVVAR)
    except Exception:
        return default_state

    if not isinstance(raw_state, dict):
        return default_state

    state = dict(default_state)
    state["attempted"] = bool(raw_state.get("attempted", False))
    try:
        state["attempted_at"] = float(raw_state.get("attempted_at", 0.0))
    except Exception:
        state["attempted_at"] = 0.0
    return state


def mark_startup_attempted():
    state = {"attempted": True, "attempted_at": time.time()}
    try:
        from pyrevit import script

        script.set_envvar(AUTO_UPDATE_GUARD_ENVVAR, state)
        return True
    except Exception:
        return False


def queue_startup_auto_update():
    """Defer the startup auto-update to the first Idling tick.

    Running git fetch/pull while Revit is starting blocks the UI thread at
    the moment users are least tolerant of it.  The first idle tick runs the
    same guarded update after Revit has become interactive.
    """
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


def run_pending_startup_auto_update():
    """Consume the deferred-update flag and run the guarded startup update."""
    _set_envvar(AUTO_UPDATE_PENDING_ENVVAR, None)
    _PENDING_RESOLVED[0] = True
    if should_skip_startup(get_startup_guard_state()):
        return None
    mark_startup_attempted()
    return run_startup_auto_update()


def run_startup_auto_update():
    startup_lock = _try_acquire_startup_lock()
    if startup_lock is False:
        return _result(
            trigger="startup",
            updated_repos=[],
            status=STATUS_SKIPPED_LOCKED,
        )
    if startup_lock is None:
        return _run_startup_update_after_precheck()

    try:
        return _run_startup_update_after_precheck()
    finally:
        _release_startup_lock(startup_lock)


def run_manual_auto_update():
    return _run_easybim_update(trigger="manual")


def _run_startup_update_after_precheck():
    try:
        updater = _get_native_updater()
    except Exception:
        # Fail closed: without the native updater there is nothing safe to run.
        return _result(trigger="startup", updated_repos=[])

    pending_updates = _try_check_for_pending_updates(updater)
    if pending_updates is not True:
        # Fail closed: an errored pre-check (auth, transient network, API
        # change) must not escalate into the full git-pull-everything path.
        return _result(trigger="startup", updated_repos=[])

    return _run_easybim_update(trigger="startup", updater=updater)


def _run_easybim_update(trigger, updater=None):
    """Pull the repository holding this extension, and nothing else.

    Fails closed in both directions: without our repository, or without a
    per-repository entry point on the updater, this does nothing at all rather
    than falling back to pyRevit's update-everything routine.
    """
    if updater is None:
        updater = _get_native_updater()

    if not callable(getattr(updater, "update_repo", None)):
        # Never widen the scope to compensate: updating everything is exactly
        # what this tool exists to avoid.
        return _fail_closed(
            trigger,
            STATUS_REPO_NOT_FOUND,
            "This pyRevit build has no single-repository update entry point, "
            "so EasyBIM cannot update only itself.\n\n"
            "Use pyRevit >> Update instead.",
        )

    repo_info = _find_own_repo(updater)
    if repo_info is None:
        return _fail_closed(
            trigger,
            STATUS_REPO_NOT_FOUND,
            "EasyBIM could not find its own repository among pyRevit's "
            "extensions - it may have been installed without git.\n\n"
            "Nothing was updated.",
        )

    repo_key = _get_repo_key(repo_info)
    sessionmgr = _try_get_session_manager()
    before_heads = _try_snapshot_repo_heads(updater)

    # The per-repo pull is not expected to ask for a reload the way pyRevit's
    # update-everything routine does, but capturing the request costs nothing
    # and keeps us correct - and in charge of the ordering - if it ever does.
    reload_requested = {"value": False}
    original_reload = getattr(sessionmgr, "reload_pyrevit", None)

    def _capture_reload_request(*args, **kwargs):
        reload_requested["value"] = True

    try:
        if original_reload is not None:
            sessionmgr.reload_pyrevit = _capture_reload_request
        try:
            updater.update_repo(repo_info)
        except Exception as pull_error:
            # A failed pull must not reach the user as pyRevit's traceback
            # dialog, nor vanish into the Idling guard at startup.
            return _fail_closed(
                trigger,
                STATUS_UPDATE_FAILED,
                "EasyBIM Auto Update could not pull the latest EasyBIM "
                "changes.\n\n{}".format(_safe_text(pull_error)),
            )
    finally:
        if original_reload is not None:
            sessionmgr.reload_pyrevit = original_reload

    after_heads = _try_snapshot_repo_heads(updater)
    updated_repos = []
    if before_heads is not None and after_heads is not None:
        updated_repos = _get_changed_repo_names(
            _only_key(before_heads, repo_key),
            _only_key(after_heads, repo_key),
        )
        if updated_repos:
            _show_message(_format_updated_message(updated_repos), warn=False)

    # The reload is the point of the update, so it follows a real change; an
    # unchanged repository leaves the session alone.
    if original_reload is not None and (updated_repos or reload_requested["value"]):
        original_reload()

    return _result(trigger=trigger, updated_repos=updated_repos)


def _fail_closed(trigger, status, message):
    """Do nothing, and say so only to someone who asked for it.

    A click deserves an answer; startup runs on every session, so a machine
    that can never self-update must not nag about it every time.
    """
    if trigger == "manual":
        _show_message(message, warn=True)
    return _result(trigger=trigger, updated_repos=[], status=status)


def _try_get_session_manager():
    try:
        return _get_session_manager()
    except Exception:
        return None


def _only_key(heads, key):
    """The one-entry view of a head snapshot that the diff should consider."""
    if not key or key not in heads:
        return {}
    return {key: heads[key]}


def _result(trigger, updated_repos, status=None):
    updated_repos = list(updated_repos or [])
    if status is None:
        status = STATUS_UPDATED if updated_repos else STATUS_NO_OP
    return {
        "status": status,
        "trigger": trigger,
        "updated_repos": updated_repos,
    }


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


def _get_native_updater():
    from pyrevit.versionmgr import updater

    return updater


def _get_session_manager():
    from pyrevit.loader import sessionmgr

    return sessionmgr


def _get_extension_root():
    """The extension folder: this module is <EXT_ROOT>/lib/easybim/<this>.py."""
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _normalize_dir(path):
    """Comparable form of a directory path: absolute, cased, no trailing sep."""
    text = _safe_text(path).strip()
    if not text:
        return ""
    # Separators first: abspath on a foreign-separator path would resolve it
    # against the working directory instead of normalising it.
    text = text.replace("/", os.sep).replace("\\", os.sep)
    try:
        text = os.path.abspath(text)
    except Exception:
        pass
    text = text.rstrip(os.sep)
    try:
        return os.path.normcase(text)
    except Exception:
        return text


def _is_same_or_ancestor(candidate, target):
    """True when `candidate` is `target` or a directory above it."""
    if not candidate or not target:
        return False
    if candidate == target:
        return True
    return target.startswith(candidate + os.sep)


def _find_own_repo(updater):
    """The repository holding this extension, or None.

    pyRevit discovers a repository by walking *up* from the extension folder,
    so in a checkout that holds several ``*.extension`` folders the repository
    directory is a parent of ours rather than equal to it.  The deepest
    matching directory wins, which keeps nested checkouts honest.
    """
    root = _normalize_dir(_get_extension_root())
    if not root:
        return None

    try:
        repos = _list_extension_repos(updater)
    except Exception:
        return None

    core_dirs = _get_core_repo_dirs(updater)

    best_repo = None
    best_length = -1
    for repo_info in repos or []:
        directory = _normalize_dir(getattr(repo_info, "directory", ""))
        if not directory or directory in core_dirs:
            continue
        if not _is_same_or_ancestor(directory, root):
            continue
        if len(directory) > best_length:
            best_repo = repo_info
            best_length = len(directory)
    return best_repo


def _list_extension_repos(updater):
    """Candidate repositories, third-party ones for preference.

    ``get_all_extension_repos`` includes pyRevit's own clone.  If someone has
    dropped this extension inside that clone, the ancestor match below would
    otherwise select it and we would pull pyRevit itself - precisely what this
    tool exists to stop doing.
    """
    thirdparty = getattr(updater, "get_thirdparty_ext_repos", None)
    if callable(thirdparty):
        return thirdparty()
    return updater.get_all_extension_repos()


def _get_core_repo_dirs(updater):
    """Normalized directories of pyRevit's own clone; empty when unknown."""
    dirs = set()
    getter = getattr(updater, "get_pyrevit_repo", None)
    if not callable(getter):
        return dirs
    try:
        core_repo = getter()
    except Exception:
        return dirs
    directory = _normalize_dir(getattr(core_repo, "directory", ""))
    if directory:
        dirs.add(directory)
    return dirs


def _try_snapshot_repo_heads(updater):
    try:
        return _snapshot_repo_heads(updater)
    except Exception:
        return None


def _try_check_for_pending_updates(updater):
    try:
        return bool(updater.check_for_updates())
    except Exception:
        return None


def _snapshot_repo_heads(updater):
    heads = {}
    for repo_info in updater.get_all_extension_repos():
        key = _get_repo_key(repo_info)
        if not key:
            continue
        heads[key] = {
            "name": _get_repo_name(repo_info),
            "head": _safe_text(getattr(repo_info, "last_commit_hash", "")),
        }
    return heads


def _get_changed_repo_names(before_heads, after_heads):
    changed = []
    for key, after_info in after_heads.items():
        before_info = before_heads.get(key)
        if before_info is None:
            continue
        if before_info.get("head") != after_info.get("head"):
            changed.append(after_info.get("name") or key)
    return sorted(changed)


def _get_repo_key(repo_info):
    directory = _safe_text(getattr(repo_info, "directory", "")).strip()
    if directory:
        return directory
    return _safe_text(getattr(repo_info, "name", "")).strip()


def _get_repo_name(repo_info):
    name = _safe_text(getattr(repo_info, "name", "")).strip()
    if name:
        return name
    return _get_repo_key(repo_info)


def _format_updated_message(updated_repos):
    lines = ["EasyBIM Auto Update installed changes:", ""]
    for repo_name in updated_repos:
        lines.append("- {}".format(repo_name))
    lines.extend(["", "pyRevit is reloading."])
    return "\n".join(lines)


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


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""
