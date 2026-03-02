# -*- coding: utf-8 -*-
"""TEMP Phase & Views command interception hook."""

from rltools import temp_phase_views


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


temp_phase_views.handle_command_before_exec(event_args=_EVENT_ARGS)
