# -*- coding: utf-8 -*-
"""Process RL Tools startup queue, temp-phase runtime, and close guard."""

from rltools.messages import process_startup_jobs

try:
    from rltools import temp_phase_view
except Exception:
    temp_phase_view = None

try:
    from rltools import file_close_guard
except Exception:
    file_close_guard = None

try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None

try:
    process_startup_jobs()
except Exception:
    # Never hard-fail Revit idling because of startup automation.
    pass

if temp_phase_view is not None:
    try:
        temp_phase_view.handle_app_idling(event_args=_EVENT_ARGS)
    except Exception:
        # Never hard-fail Revit idling because of temp phase automation.
        pass

if file_close_guard is not None:
    try:
        file_close_guard.handle_app_idling(event_args=_EVENT_ARGS)
    except Exception:
        # Never hard-fail Revit idling because of close guard automation.
        pass
