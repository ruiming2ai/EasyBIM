# -*- coding: utf-8 -*-
"""Process startup work and deferred Temp Phase close recovery."""

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None

from easybim.messages import has_pending_startup_jobs
from easybim.messages import process_startup_jobs

try:
    from easybim import temp_phase_close
except Exception:
    temp_phase_close = None

try:
    from easybim import auto_update
except Exception:
    auto_update = None

try:
    _EVENT_ARGS = EXEC_PARAMS.event_args if EXEC_PARAMS is not None else None
except Exception:
    _EVENT_ARGS = None

try:
    _UIAPP = EXEC_PARAMS.event_sender if EXEC_PARAMS is not None else None
except Exception:
    try:
        _UIAPP = __revit__
    except Exception:
        _UIAPP = None


try:
    if has_pending_startup_jobs():
        process_startup_jobs()
except Exception:
    # Never hard-fail Revit idling because of startup automation.
    pass

try:
    # The startup auto-update is deferred here so its git/network work runs
    # after Revit is interactive instead of blocking the startup thread.
    if auto_update is not None and auto_update.has_pending_startup_auto_update():
        auto_update.run_pending_startup_auto_update()
except Exception:
    pass

# Note: no per-tick log_hook_context call here - Idling fires continuously
# and the hook path never changes within a session.

if temp_phase_close is not None:
    try:
        temp_phase_close.handle_app_idling(uiapp=_UIAPP, event_args=_EVENT_ARGS)
    except Exception as ex:
        try:
            temp_phase_close.log_hook_exception("AppIdlingHookException", ex)
        except Exception:
            pass
