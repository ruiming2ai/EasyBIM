# -*- coding: utf-8 -*-
"""Cancel Temp Phase closes before Revit disposes the document."""

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None

try:
    from easybim import coordination_review_passive
except Exception:
    coordination_review_passive = None

try:
    from easybim import temp_phase_close
except Exception:
    temp_phase_close = None


try:
    _EVENT_ARGS = EXEC_PARAMS.event_args if EXEC_PARAMS is not None else None
except Exception:
    _EVENT_ARGS = None

try:
    _UIAPP = EXEC_PARAMS.event_sender if EXEC_PARAMS is not None else None
except Exception:
    try:
        _UIAPP = __revit__
    except Exception:
        _UIAPP = None


if temp_phase_close is not None:
    try:
        temp_phase_close.log_hook_context("DocClosing", __file__)
        temp_phase_close.handle_doc_closing(uiapp=_UIAPP, event_args=_EVENT_ARGS)
    except Exception as ex:
        try:
            temp_phase_close.log_hook_exception("DocClosingHookException", ex)
        except Exception:
            pass

if coordination_review_passive is not None:
    try:
        doc = getattr(_EVENT_ARGS, "Document", None) if _EVENT_ARGS else None
        if doc is None and _EVENT_ARGS is not None:
            get_doc = getattr(_EVENT_ARGS, "GetDocument", None)
            doc = get_doc() if callable(get_doc) else None
        coordination_review_passive.clear_document_records(doc)
    except Exception:
        pass
