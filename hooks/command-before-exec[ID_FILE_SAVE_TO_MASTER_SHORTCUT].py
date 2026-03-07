# -*- coding: utf-8 -*-
"""Capture legacy Revit synchronize commands for compatibility diagnostics only."""

from rltools import close_stop


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


close_stop.capture_action_command(event_args=_EVENT_ARGS)
