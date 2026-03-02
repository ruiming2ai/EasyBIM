# -*- coding: utf-8 -*-
"""TEMP Phase & Views idling hook."""

from rltools import temp_phase_views


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


temp_phase_views.handle_app_idling(event_args=_EVENT_ARGS)
