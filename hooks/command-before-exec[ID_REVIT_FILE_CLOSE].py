# -*- coding: utf-8 -*-
"""Capture Revit close command names for Close Stop."""

from rltools import close_stop


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


close_stop.capture_close_command(event_args=_EVENT_ARGS)
close_stop.handle_command_before_exec(event_args=_EVENT_ARGS)
