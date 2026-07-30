# -*- coding: utf-8 -*-
"""Clear EasyBIM per-document startup records during document close."""

try:
    from easybim import coordination_review_passive
except Exception:
    coordination_review_passive = None


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None


if coordination_review_passive is not None:
    try:
        doc = getattr(_EVENT_ARGS, "Document", None) if _EVENT_ARGS else None
        if doc is None and _EVENT_ARGS is not None:
            get_doc = getattr(_EVENT_ARGS, "GetDocument", None)
            doc = get_doc() if callable(get_doc) else None
        coordination_review_passive.clear_document_records(doc)
    except Exception:
        pass
