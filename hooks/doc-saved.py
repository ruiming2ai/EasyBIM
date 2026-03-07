# -*- coding: utf-8 -*-
"""Show a post-save RL Tools notice after a document save completes."""

from rltools import file_saved_notice


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


file_saved_notice.handle_doc_saved(event_args=_EVENT_ARGS)
