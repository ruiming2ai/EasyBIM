# -*- coding: utf-8 -*-
"""Prompt to remove Temporary View Properties before Save As continues."""

from rltools import save_tvp_prompt


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


save_tvp_prompt.handle_doc_saving_as(event_args=_EVENT_ARGS)
