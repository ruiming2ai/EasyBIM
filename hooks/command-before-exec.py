# -*- coding: utf-8 -*-
"""Temp Phase & View close interception hook."""

from rltools import temp_phase_view


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


temp_phase_view.handle_command_before_exec(event_args=_EVENT_ARGS)
