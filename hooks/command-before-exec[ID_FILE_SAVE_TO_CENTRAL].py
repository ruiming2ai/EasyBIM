# -*- coding: utf-8 -*-
"""Capture Revit synchronize commands and run Close Stop before syncing."""

from easybim import close_stop


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


close_stop.show_save_hook_probe(event_args=_EVENT_ARGS, hook_name="ID_FILE_SAVE_TO_CENTRAL")
close_stop.capture_action_command(event_args=_EVENT_ARGS)
close_stop.handle_save_related_before_exec(event_args=_EVENT_ARGS)
