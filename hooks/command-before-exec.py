# -*- coding: utf-8 -*-
"""RL Tools close interception hook."""

from rltools import file_close_guard


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


file_close_guard.handle_command_before_exec(event_args=_EVENT_ARGS)
