# -*- coding: utf-8 -*-
"""Process RL Tools startup queue, temp-phase runtime, and close stop."""

from rltools.messages import process_startup_jobs

try:
    from rltools import temp_phase_view
except Exception:
    temp_phase_view = None

try:
    from rltools import close_stop
except Exception:
    close_stop = None

try:
    from rltools import coordination_review_native
except Exception:
    coordination_review_native = None

try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None

try:
    process_startup_jobs()
except Exception:
    # Never hard-fail Revit idling because of startup automation.
    pass

if coordination_review_native is not None:
    try:
        coordination_review_native.process_native_coordination_review_jobs()
    except Exception:
        # Never hard-fail Revit idling because of native report automation.
        pass

if temp_phase_view is not None:
    try:
        temp_phase_view.handle_app_idling(event_args=_EVENT_ARGS)
    except Exception:
        # Never hard-fail Revit idling because of temp phase automation.
        pass

if close_stop is not None:
    try:
        close_stop.handle_app_idling(event_args=_EVENT_ARGS)
    except Exception:
        # Never hard-fail Revit idling because of close stop automation.
        pass
