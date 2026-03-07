# -*- coding: utf-8 -*-
"""Capture Revit save commands and run Close Stop before saving."""

from rltools import close_stop


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


close_stop.capture_action_command(event_args=_EVENT_ARGS)
close_stop.handle_save_related_before_exec(event_args=_EVENT_ARGS)
