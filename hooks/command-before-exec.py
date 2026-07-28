# -*- coding: utf-8 -*-
"""Generic command-before-exec probe hook for button-armed diagnostics."""

from easybim import close_stop


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


close_stop.handle_generic_command_before_exec(event_args=_EVENT_ARGS)
