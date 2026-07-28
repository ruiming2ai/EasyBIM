# -*- coding: utf-8 -*-
"""Auto-run the native pyRevit Update command."""

from __future__ import print_function

import time


AUTO_UPDATE_GUARD_ENVVAR = "EASYBIM_AUTO_UPDATE_RAN"
STATUS_EXECUTED = "executed"


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


def run_startup_auto_update():
    return _run_native_update(trigger="startup")


def run_manual_auto_update():
    return _run_native_update(trigger="manual")


def _run_native_update(trigger):
    updater = _get_native_updater()
    updater.update_pyrevit()
    return {
        "status": STATUS_EXECUTED,
        "trigger": trigger,
    }


def _get_native_updater():
    from pyrevit.versionmgr import updater

    return updater
