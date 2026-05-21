# -*- coding: utf-8 -*-
"""Auto-update helper for RL Tools startup and manual reload."""

from __future__ import print_function

import os
import shutil
import subprocess
import time


AUTO_UPDATE_GUARD_ENVVAR = "RLTOOLS_AUTO_UPDATE_RAN"
TITLE = "Auto Update"
STATUS_NO_OP = "no_op"
STATUS_UPDATED = "updated"
STATUS_REPAIRED = "repaired"
STATUS_FAILURE = "failure"
STATUS_SKIPPED = "skipped"
FETCH_NETWORK = "network"
FETCH_ERROR = "error"


def should_skip_startup(guard_state):
    return bool((guard_state or {}).get("attempted", False))


def is_repo_compliant(repo_state):
    state = repo_state or {}
    return (
        state.get("branch") == "main"
        and bool(state.get("head"))
        and state.get("head") == state.get("remote_head")
        and not bool(state.get("tracked_dirty", False))
        and not bool(state.get("has_untracked", False))
    )


def classify_sync_result(before_state, after_state):
    before_state = before_state or {}
    after_state = after_state or {}

    if not is_repo_compliant(after_state):
        return {
            "status": STATUS_FAILURE,
            "message": "RL Tools auto update did not end in a clean main state.",
        }

    if is_repo_compliant(before_state) and before_state.get("head") == after_state.get("head"):
        return {"status": STATUS_NO_OP, "message": "RL Tools is already current."}

    if before_state.get("head") != after_state.get("head"):
        return {
            "status": STATUS_UPDATED,
            "message": "RL Tools updated. pyRevit is reloading.",
        }

    if (
        before_state.get("branch") != after_state.get("branch")
        or bool(before_state.get("tracked_dirty", False)) != bool(after_state.get("tracked_dirty", False))
        or bool(before_state.get("has_untracked", False)) != bool(after_state.get("has_untracked", False))
        or not is_repo_compliant(before_state)
    ):
        return {
            "status": STATUS_REPAIRED,
            "message": "RL Tools updated. pyRevit is reloading.",
        }

    return {"status": STATUS_NO_OP, "message": "RL Tools is already current."}


def classify_fetch_failure(returncode, stderr_text):
    del returncode
    lowered = _safe_text(stderr_text).lower()
    network_markers = (
        "could not resolve host",
        "failed to connect",
        "connection timed out",
        "network is unreachable",
        "connection refused",
        "couldn't connect",
        "unable to access",
        "temporary failure in name resolution",
    )

    if any(marker in lowered for marker in network_markers):
        if (
            "resolve host" in lowered
            or "failed to connect" in lowered
            or "timed out" in lowered
            or "unreachable" in lowered
            or "refused" in lowered
        ):
            return FETCH_NETWORK

    return FETCH_ERROR


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


def run_startup_auto_update():
    result = _run_auto_update(trigger="startup")
    status = result.get("status")

    if status in (STATUS_UPDATED, STATUS_REPAIRED):
        _show_message(result.get("message"), warn=False)
        _reload_pyrevit(result)
        return result

    if status == STATUS_FAILURE and result.get("visible", False):
        _show_message(result.get("message"), warn=True)

    return result


def run_manual_auto_update():
    result = _run_auto_update(trigger="manual")
    status = result.get("status")

    if status == STATUS_NO_OP:
        _show_message(result.get("message"), warn=False)
        return result

    if status in (STATUS_UPDATED, STATUS_REPAIRED):
        _reload_pyrevit(result)
        return result

    if status == STATUS_FAILURE:
        _show_message(result.get("message"), warn=True)

    return result


def _run_auto_update(trigger):
    repo_root = _get_repo_root()
    logger = _get_logger()

    if not os.path.isdir(repo_root):
        return _failure_result("RL Tools auto update could not find the extension folder.", visible=True)

    git_command = resolve_git_command()
    if not git_command:
        return _failure_result("RL Tools auto update could not find git on this machine.", visible=True)

    if not os.path.exists(os.path.join(repo_root, ".git")):
        return _failure_result("RL Tools auto update requires a valid git checkout.", visible=True)

    worktree_check = _run_git(repo_root, ["rev-parse", "--is-inside-work-tree"], git_command=git_command)
    if worktree_check["returncode"] != 0 or worktree_check["stdout"].strip().lower() != "true":
        return _failure_result("RL Tools auto update requires a valid git checkout.", visible=True, details=worktree_check)

    remote_check = _run_git(repo_root, ["remote", "get-url", "origin"], git_command=git_command)
    if remote_check["returncode"] != 0 or not remote_check["stdout"].strip():
        return _failure_result("RL Tools auto update requires an origin remote.", visible=True, details=remote_check)

    before_state = _read_repo_state(repo_root, git_command=git_command)
    if before_state["error"]:
        return _failure_result("RL Tools auto update could not read the current repo state.", visible=True, details=before_state)

    fetch_result = _run_git(repo_root, ["fetch", "origin", "main", "--prune"], git_command=git_command)
    if fetch_result["returncode"] != 0:
        fetch_kind = classify_fetch_failure(fetch_result["returncode"], fetch_result["stderr"])
        if fetch_kind == FETCH_NETWORK and trigger == "startup":
            _log_debug(logger, "Auto update skipped at startup due to network fetch failure: {}".format(fetch_result["stderr"]))
            return {
                "status": STATUS_SKIPPED,
                "message": "",
                "visible": False,
                "details": fetch_result,
            }
        return _failure_result(
            "RL Tools auto update could not fetch origin/main.",
            visible=True,
            details=fetch_result,
        )

    remote_head_result = _run_git(repo_root, ["rev-parse", "origin/main"], git_command=git_command)
    if remote_head_result["returncode"] != 0:
        return _failure_result(
            "RL Tools auto update could not resolve origin/main.",
            visible=True,
            details=remote_head_result,
        )

    remote_head = remote_head_result["stdout"].strip()
    before_state["remote_head"] = remote_head

    if is_repo_compliant(before_state):
        return {"status": STATUS_NO_OP, "message": "RL Tools is already current.", "visible": trigger == "manual"}

    if before_state.get("has_untracked", False):
        preclean = _run_git(repo_root, ["clean", "-fd"], git_command=git_command)
        if preclean["returncode"] != 0:
            return _failure_result(
                "RL Tools auto update could not remove untracked files.",
                visible=True,
                details=preclean,
            )

    checkout_result = _run_git(repo_root, ["checkout", "-B", "main", "origin/main"], git_command=git_command)
    if checkout_result["returncode"] != 0:
        return _failure_result(
            "RL Tools auto update could not switch the extension repo to main.",
            visible=True,
            details=checkout_result,
        )

    reset_result = _run_git(repo_root, ["reset", "--hard", "origin/main"], git_command=git_command)
    if reset_result["returncode"] != 0:
        return _failure_result(
            "RL Tools auto update could not reset the extension repo to origin/main.",
            visible=True,
            details=reset_result,
        )

    clean_result = _run_git(repo_root, ["clean", "-fd"], git_command=git_command)
    if clean_result["returncode"] != 0:
        return _failure_result(
            "RL Tools auto update could not clean untracked files.",
            visible=True,
            details=clean_result,
        )

    after_state = _read_repo_state(repo_root, remote_head=remote_head, git_command=git_command)
    if after_state["error"]:
        return _failure_result("RL Tools auto update could not read the final repo state.", visible=True, details=after_state)

    result = classify_sync_result(before_state, after_state)
    result["visible"] = bool(trigger == "manual" and result["status"] == STATUS_NO_OP)
    result["details"] = {
        "before": before_state,
        "after": after_state,
        "fetch": fetch_result,
    }
    return result


def _get_repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_git_command(shutil_module=None):
    shutil_module = shutil if shutil_module is None else shutil_module
    which_func = getattr(shutil_module, "which", None)
    if callable(which_func):
        git_path = which_func("git")
        if git_path:
            return [git_path]

    fallback = ["git"]
    if _can_run_git_command(fallback):
        return fallback
    return None


def _read_repo_state(repo_root, remote_head=None, git_command=None):
    branch_result = _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"], git_command=git_command)
    head_result = _run_git(repo_root, ["rev-parse", "HEAD"], git_command=git_command)
    status_result = _run_git(repo_root, ["status", "--short", "--untracked-files=all"], git_command=git_command)

    if branch_result["returncode"] != 0 or head_result["returncode"] != 0 or status_result["returncode"] != 0:
        return {
            "error": True,
            "branch_result": branch_result,
            "head_result": head_result,
            "status_result": status_result,
        }

    tracked_dirty = False
    has_untracked = False
    for line in status_result["stdout"].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("??"):
            has_untracked = True
        else:
            tracked_dirty = True

    return {
        "error": False,
        "branch": branch_result["stdout"].strip(),
        "head": head_result["stdout"].strip(),
        "remote_head": remote_head,
        "tracked_dirty": tracked_dirty,
        "has_untracked": has_untracked,
    }


def _can_run_git_command(git_command):
    try:
        completed = subprocess.Popen(
            list(git_command) + ["--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        completed.communicate()
        return completed.returncode == 0
    except Exception:
        return False


def _run_git(repo_root, args, git_command=None):
    git_command = list(git_command or ["git"])
    try:
        completed = subprocess.Popen(
            git_command + list(args),
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        stdout_text, stderr_text = completed.communicate()
        return {
            "returncode": completed.returncode,
            "stdout": _safe_text(stdout_text),
            "stderr": _safe_text(stderr_text),
            "args": list(args),
        }
    except Exception as ex:
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": _safe_text(ex),
            "args": list(args),
        }


def _reload_pyrevit(result):
    try:
        from pyrevit.loader import sessionmgr

        sessionmgr.reload_pyrevit()
        return True
    except Exception as ex:
        details = result.setdefault("details", {})
        details["reload_error"] = _safe_text(ex)
        result["status"] = STATUS_FAILURE
        result["message"] = "RL Tools updated, but pyRevit reload failed."
        _show_message(
            "RL Tools updated, but pyRevit reload failed.\n\n{}".format(_safe_text(ex)),
            warn=True,
        )
        return False


def _failure_result(message, visible, details=None):
    logger = _get_logger()
    if details is not None:
        _log_debug(logger, "Auto update failure details: {}".format(details))
    return {
        "status": STATUS_FAILURE,
        "message": message,
        "visible": bool(visible),
        "details": details,
    }


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


def _get_logger():
    try:
        from pyrevit import script

        return script.get_logger()
    except Exception:
        return None


def _log_debug(logger, message):
    if logger is not None:
        try:
            logger.debug(message)
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
