# -*- coding: utf-8 -*-
"""RL Tools app-idling hook."""

from rltools.messages import process_startup_jobs

try:
    from rltools import temp_phase_views
except Exception:
    temp_phase_views = None

try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None

try:
    process_startup_jobs()
except Exception:
    # Never hard-fail Revit idling because of startup automation.
    pass

if temp_phase_views is not None:
    try:
        temp_phase_views.handle_app_idling(event_args=_EVENT_ARGS)
    except Exception:
        # Never hard-fail Revit idling because of temp phase automation.
        pass
