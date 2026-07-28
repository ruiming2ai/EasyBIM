# -*- coding: utf-8 -*-
"""Show a post-save EasyBIM notice after a document save-as completes."""

from easybim import file_saved_notice


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


file_saved_notice.handle_doc_saved_as(event_args=_EVENT_ARGS)
