# -*- coding: utf-8 -*-
"""Prompt to remove Temporary View Properties during Save and Save As."""

from __future__ import print_function

from pyrevit import script
from rltools import temp_phase_view


LOGGER = script.get_logger()

TITLE_DIALOG = "RL Tools"
TITLE_ERROR = "RL Tools"


def handle_doc_saving(uiapp=None, event_args=None):
    """Prompt on Save and optionally remove TVP before save continues."""
    _handle_save_event(uiapp=uiapp, event_args=event_args, action_label="saving")


def handle_doc_saving_as(uiapp=None, event_args=None):
    """Prompt on Save As and optionally remove TVP before save continues."""
    _handle_save_event(uiapp=uiapp, event_args=event_args, action_label="saving")


def _handle_save_event(uiapp=None, event_args=None, action_label="saving"):
    uiapp = _get_uiapp(uiapp)
    doc = _resolve_doc_from_event_args(event_args, uiapp)
    if not _is_doc_supported(doc):
        doc = _get_active_project_doc(uiapp)
    if not _is_doc_supported(doc):
        return

    summary = _collect_summary(uiapp, doc)
    if not _show_save_prompt(summary):
        return

    if not bool(summary.get("has_restore_work")):
        return

    try:
        restore_result = temp_phase_view.restore_tvp_summary(
            uiapp=uiapp,
            doc=doc,
            summary=summary,
        )
    except Exception as ex:
        LOGGER.warning("RL Tools save TVP cleanup raised during %s: %s", action_label, ex)
        _show_alert(
            TITLE_ERROR,
            "RL Tools could not remove Temporary View Properties before {}.\n\n{}".format(
                action_label,
                ex,
            ),
        )
        return

    if bool(restore_result.get("ok")):
        return

    _show_alert(
        TITLE_ERROR,
        _safe_text(restore_result.get("message")).strip()
        or "RL Tools could not remove Temporary View Properties before {}.".format(
            action_label
        ),
    )


def _show_save_prompt(summary):
    UI = _get_ui()
    if UI is None:
        _show_alert(
            TITLE_DIALOG,
            _fallback_prompt_message(summary),
        )
        return True

    has_restore_work = bool(summary.get("has_restore_work"))
    try:
        dialog = UI.TaskDialog(TITLE_DIALOG)
        if hasattr(dialog, "TitleAutoPrefix"):
            dialog.TitleAutoPrefix = False
        if hasattr(dialog, "AllowCancellation"):
            dialog.AllowCancellation = True

        if has_restore_work:
            tracked_count = len(list(summary.get("tracked_restore_views") or []))
            untracked_count = len(list(summary.get("untracked_tvp_views") or []))
            total_count = len(list(summary.get("discoverable_tvp_views") or []))
            dialog.MainInstruction = "Remove all Temporary View Properties before saving?"
            dialog.MainContent = (
                "RL Tools found Temporary View Properties work in this project.\n"
                "Click OK to remove all Temporary View Properties before the save continues."
            )
            dialog.ExpandedContent = (
                "Affected views with Temporary View Properties on: {total}\n"
                "Tracked RL Tools temp-phase views to restore: {tracked}\n"
                "Additional discoverable TVP-enabled views to turn off: {untracked}"
            ).format(
                total=total_count,
                tracked=tracked_count,
                untracked=untracked_count,
            )
        else:
            dialog.MainInstruction = "No Temporary View Properties were found in this project."
            dialog.MainContent = "Click OK to continue."

        dialog.CommonButtons = (
            UI.TaskDialogCommonButtons.Ok | UI.TaskDialogCommonButtons.Cancel
        )
        result = dialog.Show()
        return result == UI.TaskDialogResult.Ok
    except Exception as ex:
        LOGGER.warning("RL Tools save prompt TaskDialog failed: %s", ex)
        _show_alert(TITLE_DIALOG, _fallback_prompt_message(summary))
        return True


def _fallback_prompt_message(summary):
    if bool(summary.get("has_restore_work")):
        tracked_count = len(list(summary.get("tracked_restore_views") or []))
        untracked_count = len(list(summary.get("untracked_tvp_views") or []))
        total_count = len(list(summary.get("discoverable_tvp_views") or []))
        return (
            "Remove all Temporary View Properties before saving?\n\n"
            "RL Tools found Temporary View Properties work in this project.\n"
            "Affected views with Temporary View Properties on: {total}\n"
            "Tracked RL Tools temp-phase views to restore: {tracked}\n"
            "Additional discoverable TVP-enabled views to turn off: {untracked}\n\n"
            "Press OK to remove all Temporary View Properties before the save continues."
        ).format(
            total=total_count,
            tracked=tracked_count,
            untracked=untracked_count,
        )

    return "No Temporary View Properties were found in this project.\n\nClick OK to continue."


def _collect_summary(uiapp, doc):
    try:
        summary = temp_phase_view.collect_tvp_summary(
            uiapp=uiapp,
            doc=doc,
            include_all_discoverable=True,
        )
    except Exception as ex:
        LOGGER.warning("RL Tools save TVP prompt could not collect summary: %s", ex)
        summary = None

    if isinstance(summary, dict):
        return summary

    return {
        "doc_key": _doc_key(doc),
        "doc_runtime_id": _get_doc_runtime_id(doc),
        "doc_title": _safe_text(getattr(doc, "Title", "")).strip() if doc else "",
        "discoverable_tvp_views": [],
        "tracked_restore_views": [],
        "untracked_tvp_views": [],
        "dialog_view_lines": [],
        "has_blocking_views": False,
        "has_restore_work": False,
    }


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


def _get_active_project_doc(uiapp):
    if uiapp is None:
        return None
    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    doc = getattr(uidoc, "Document", None) if uidoc else None
    return doc if _is_doc_supported(doc) else None


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


def _doc_key(doc):
    if not _is_doc_valid(doc):
        return ""
    try:
        path = _safe_text(doc.PathName).strip()
    except Exception:
        path = ""
    if path:
        return "path|{}".format(path.lower())
    try:
        hash_code = _safe_text(doc.GetHashCode())
    except Exception:
        hash_code = _safe_text(id(doc))
    title = _safe_text(getattr(doc, "Title", "")).strip().lower()
    return "mem|{}|{}".format(title, hash_code)


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


def _is_doc_supported(doc):
    if not _is_doc_valid(doc):
        return False
    try:
        if bool(doc.IsFamilyDocument):
            return False
    except Exception:
        pass
    try:
        if bool(doc.IsLinked):
            return False
    except Exception:
        pass
    return True


def _is_doc_valid(doc):
    if doc is None:
        return False
    try:
        return bool(doc.IsValidObject)
    except Exception:
        return True


def _show_alert(title, message):
    UI = _get_ui()
    if UI is not None:
        try:
            UI.TaskDialog.Show(title, message)
            return
        except Exception:
            pass

    print("[RL Tools] {}: {}".format(title, message))


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


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""
