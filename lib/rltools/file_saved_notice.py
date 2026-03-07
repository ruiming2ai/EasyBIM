# -*- coding: utf-8 -*-
"""Post-save notice runtime for save, save as, and synchronize events."""

from __future__ import print_function


TITLE = "RL Tools"
MESSAGE = "File Saved"


def run_pushbutton(uiapp=None):
    """Manual test entrypoint for the File Saved pushbutton."""
    del uiapp
    _show_file_saved_dialog()


def handle_doc_saved(uiapp=None, event_args=None):
    """Show the File Saved popup after a DocumentSaved hook fires."""
    _handle_post_save_event(uiapp=uiapp, event_args=event_args)


def handle_doc_saved_as(uiapp=None, event_args=None):
    """Show the File Saved popup after a DocumentSavedAs hook fires."""
    _handle_post_save_event(uiapp=uiapp, event_args=event_args)


def handle_doc_synchronized_with_central(uiapp=None, event_args=None):
    """Show the File Saved popup after a synchronize-with-central hook fires."""
    _handle_post_save_event(uiapp=uiapp, event_args=event_args)


def _handle_post_save_event(uiapp=None, event_args=None):
    uiapp = _get_uiapp(uiapp)
    doc = _resolve_doc_from_event_args(event_args, uiapp)
    if not _should_show_for_event(event_args=event_args, doc=doc):
        return
    _show_file_saved_dialog()


def _show_file_saved_dialog():
    UI = _get_ui()
    if UI is not None:
        try:
            UI.TaskDialog.Show(TITLE, MESSAGE)
            return
        except Exception:
            pass

    print("[RL Tools] {}: {}".format(TITLE, MESSAGE))


def _should_show_for_event(event_args=None, doc=None):
    if _is_doc_valid(doc):
        return True
    return event_args is not None


def _resolve_doc_from_event_args(event_args, uiapp):
    if event_args is None:
        return None

    try:
        doc = event_args.Document
        if _is_doc_valid(doc):
            return doc
    except Exception:
        pass

    get_doc = getattr(event_args, "GetDocument", None)
    if callable(get_doc):
        try:
            doc = get_doc()
            if _is_doc_valid(doc):
                return doc
        except Exception:
            pass

    doc_runtime_id = _to_int(_getattr_safe(event_args, "DocumentId"))
    if doc_runtime_id is not None:
        return _find_doc_by_runtime_id(uiapp, doc_runtime_id)

    return None


def _find_doc_by_runtime_id(uiapp, doc_runtime_id):
    doc_runtime_id = _to_int(doc_runtime_id)
    if uiapp is None or doc_runtime_id is None:
        return None

    app = getattr(uiapp, "Application", None)
    docs = getattr(app, "Documents", None)
    if docs is None:
        return None

    try:
        for doc in docs:
            if _get_doc_runtime_id(doc) == doc_runtime_id:
                return doc
    except Exception:
        return None
    return None


def _get_doc_runtime_id(doc):
    if not _is_doc_valid(doc):
        return None

    for attr_name in ("DocumentId", "Id"):
        try:
            value = getattr(doc, attr_name)
        except Exception:
            value = None
        value_int = _to_int(value)
        if value_int is not None:
            return value_int

    try:
        return _to_int(doc.GetHashCode())
    except Exception:
        return None


def _is_doc_valid(doc):
    if doc is None:
        return False
    try:
        return bool(doc.IsValidObject)
    except Exception:
        return True


def _get_uiapp(uiapp=None):
    if uiapp is not None:
        return uiapp
    try:
        return __revit__
    except Exception:
        return None


def _get_ui():
    try:
        from Autodesk.Revit import UI

        return UI
    except Exception:
        return None


def _getattr_safe(obj, attr_name):
    try:
        return getattr(obj, attr_name)
    except Exception:
        return None


def _to_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None
