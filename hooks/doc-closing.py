# -*- coding: utf-8 -*-
"""Temp Phase & View document closing hook."""

from rltools import temp_phase_view


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


temp_phase_view.handle_doc_closing(event_args=_EVENT_ARGS)
