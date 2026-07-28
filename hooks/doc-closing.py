# -*- coding: utf-8 -*-
"""EasyBIM close-stop document closing hook."""

from easybim import close_stop

try:
    from easybim import coordination_review_passive
except Exception:
    coordination_review_passive = None


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


close_stop.handle_doc_closing(event_args=_EVENT_ARGS)
