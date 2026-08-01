# -*- coding: utf-8 -*-
"""Auto-run the native pyRevit Update command."""

from __future__ import print_function

import time


AUTO_UPDATE_GUARD_ENVVAR = "EASYBIM_AUTO_UPDATE_RAN"
AUTO_UPDATE_PENDING_ENVVAR = "EASYBIM_AUTO_UPDATE_PENDING"
AUTO_UPDATE_MUTEX_NAME = "Global\\EasyBIMAutoUpdate"
TITLE = "Auto Update"
STATUS_NO_OP = "no_op"
STATUS_SKIPPED_LOCKED = "skipped_locked"
STATUS_UPDATED = "updated"

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
    return _run_native_update(trigger="manual")


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

    return _run_native_update(trigger="startup", updater=updater)


def _run_native_update(trigger, updater=None):
    if updater is None:
        updater = _get_native_updater()
    sessionmgr = _get_session_manager()
    before_heads = _try_snapshot_repo_heads(updater)

    if before_heads is None:
        updater.update_pyrevit()
        return _result(trigger=trigger, updated_repos=[])

    reload_requested = {"value": False}
    original_reload = sessionmgr.reload_pyrevit

    def _capture_reload_request():
        reload_requested["value"] = True

    try:
        sessionmgr.reload_pyrevit = _capture_reload_request
        updater.update_pyrevit()
    finally:
        sessionmgr.reload_pyrevit = original_reload

    after_heads = _try_snapshot_repo_heads(updater)
    updated_repos = []
    if after_heads is not None:
        updated_repos = _get_changed_repo_names(before_heads, after_heads)
        if updated_repos:
            _show_message(_format_updated_message(updated_repos), warn=False)

    if reload_requested["value"]:
        original_reload()

    return _result(trigger=trigger, updated_repos=updated_repos)


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
    lines = ["pyRevit Update installed changes:", ""]
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

    print("[{}] {}".format(TITLE, message))


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""
