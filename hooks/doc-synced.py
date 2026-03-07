# -*- coding: utf-8 -*-
"""Show a post-save RL Tools notice after synchronize with central completes."""

from rltools import file_saved_notice


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


file_saved_notice.handle_doc_synced(event_args=_EVENT_ARGS)
