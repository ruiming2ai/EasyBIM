# -*- coding: utf-8 -*-
"""RL Tools document closing hook."""

from rltools import file_close_guard


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


file_close_guard.handle_doc_closing(event_args=_EVENT_ARGS)
