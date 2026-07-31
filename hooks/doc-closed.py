# -*- coding: utf-8 -*-
"""Clear Temp Phase close state after Revit finishes closing a document."""

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None

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
        temp_phase_close.log_hook_context("DocClosed", __file__)
    except Exception as ex:
        try:
            temp_phase_close.log_hook_exception("DocClosedHookException", ex)
        except Exception:
            pass

if temp_phase_close is not None:
    try:
        temp_phase_close.handle_doc_closed(uiapp=_UIAPP, event_args=_EVENT_ARGS)
    except Exception as ex:
        try:
            temp_phase_close.log_hook_exception("DocClosedHookException", ex)
        except Exception:
            pass
