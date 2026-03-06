# -*- coding: utf-8 -*-
"""Two-step file close guard for RL Tools project documents."""

from __future__ import print_function

import time

from pyrevit import script

from rltools import temp_phase_view


LOGGER = script.get_logger()

TITLE = "RL Tools"
STATE_ENVVAR = "RLTOOLS_FILE_CLOSE_GUARD_STATE"
REPOST_GUARD_SEC = 10.0
MAX_DEBUG_COMMANDS = 12

CLOSE_KEYWORDS = ("CLOSE",)
FILE_KEYWORDS = ("FILE", "PROJECT")


def handle_command_before_exec(uiapp=None, event_args=None):
    """Intercept close commands before Revit executes them."""
    if event_args is None:
        return

    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return

    command_name = _safe_text(_getattr_safe(event_args, "CommandId.Name")).strip()
    if not command_name:
        return

    state = _get_state()
    state_changed = _clear_expired_repost_guard(state)

    if _classify_command(command_name) != "close":
        if state_changed:
            _save_state(state)
        return

    _remember_command_name(state, command_name)
    state_changed = True

    target_doc = _resolve_doc_from_event_args(event_args, uiapp)
    if not _is_doc_supported(target_doc):
        uidoc = getattr(uiapp, "ActiveUIDocument", None)
        target_doc = getattr(uidoc, "Document", None) if uidoc else None
    if not _is_doc_supported(target_doc):
        if state_changed:
            _save_state(state)
        return

    doc_key = _doc_key(target_doc)
    doc_runtime_id = _extract_doc_runtime_id(event_args, target_doc)

    if _allow_reposted_command(state, doc_key, doc_runtime_id, command_name):
        _save_state(state)
        return

    if _has_pending_close(state):
        if _cancel_event(event_args):
            state_changed = True
        if state_changed:
            _save_state(state)
        return

    if not _cancel_event(event_args):
        if state_changed:
            _save_state(state)
        return

    _queue_pending_close(
        state=state,
        doc=target_doc,
        command_name=command_name,
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    )
    _save_state(state)


def handle_doc_closing(uiapp=None, event_args=None):
    """Allow the reposted close to pass through once."""
    if event_args is None:
        return

    state = _get_state()
    state_changed = _clear_expired_repost_guard(state)

    uiapp = _get_uiapp(uiapp)
    closing_doc = _resolve_doc_from_event_args(event_args, uiapp)
    doc_key = _doc_key(closing_doc)
    doc_runtime_id = _extract_doc_runtime_id(event_args, closing_doc)

    if _consume_repost_guard(state, doc_key, doc_runtime_id):
        state_changed = True

    if state_changed:
        _save_state(state)


def handle_app_idling(uiapp=None, event_args=None):
    """Process any queued two-step file close flow on idling."""
    del event_args  # not used

    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return

    state = _get_state()
    state_changed = _clear_expired_repost_guard(state)

    if _process_pending_close(state, uiapp):
        state_changed = True

    if state_changed:
        _save_state(state)


def _process_pending_close(state, uiapp):
    pending = state.get("pending_close")
    if not isinstance(pending, dict):
        return False

    doc_key = _safe_text(pending.get("doc_key")).strip()
    doc_runtime_id = _to_int(pending.get("doc_runtime_id"))
    command_name = _safe_text(pending.get("command_name")).strip()
    doc_title = _safe_text(pending.get("doc_title")).strip()

    doc = _find_doc_by_identity(uiapp, doc_key=doc_key, doc_runtime_id=doc_runtime_id)
    if not _is_doc_valid(doc):
        state["pending_close"] = None
        return True

    cleanup_result = temp_phase_view.prepare_doc_for_close(
        uiapp=uiapp,
        doc=doc,
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    )
    if not cleanup_result.get("restored_ok", False):
        state["pending_close"] = None
        _show_alert(
            TITLE,
            cleanup_result.get("message")
            or "Could not restore temporary phase settings before close. The file will remain open.",
        )
        return True

    _show_ok_dialog(TITLE, _build_first_message(doc_title, cleanup_result))

    should_close = _show_close_file_dialog(
        TITLE,
        _build_second_message(doc_title),
    )

    state["pending_close"] = None
    if not should_close:
        return True

    if not command_name:
        _show_alert(
            TITLE,
            "RL Tools could not determine the Revit close command.\n\nPlease try closing the file again.",
        )
        return True

    command_id = _lookup_command_id(command_name)
    if command_id is None:
        _show_alert(
            TITLE,
            "RL Tools could not resolve the Revit close command.\n\nPlease try closing the file again.",
        )
        return True

    _register_repost_guard(
        state=state,
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
        command_name=command_name,
    )
    _save_state(state)

    try:
        uiapp.PostCommand(command_id)
    except Exception as ex:
        state["repost_guard"] = None
        _show_alert(
            TITLE,
            "RL Tools could not repost the close command.\n\n{}".format(ex),
        )
    return True


def _build_first_message(doc_title, cleanup_result):
    lines = ["File close was stopped."]

    doc_title = _safe_text(doc_title).strip()
    if doc_title:
        lines.append("")
        lines.append("File: {}".format(doc_title))

    cleanup_message = _safe_text(cleanup_result.get("message")).strip()
    if cleanup_message:
        lines.append("")
        lines.append(cleanup_message)

    lines.append("")
    lines.append("Click OK to continue.")
    return "\n".join(lines)


def _build_second_message(doc_title):
    lines = ["Click Close File to continue closing this file."]
    doc_title = _safe_text(doc_title).strip()
    if doc_title:
        lines.append("")
        lines.append("File: {}".format(doc_title))
    return "\n".join(lines)


def _show_ok_dialog(title, message):
    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        import System.Windows.Forms as WinForms

        WinForms.MessageBox.Show(
            message,
            title,
            WinForms.MessageBoxButtons.OK,
            WinForms.MessageBoxIcon.Information,
        )
        return True
    except Exception:
        _show_alert(title, message)
        return True


def _show_close_file_dialog(title, message):
    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        import System.Drawing as Drawing
        import System.Windows.Forms as WinForms
    except Exception:
        _show_alert(
            title,
            "{}\n\nThe file remains open because RL Tools could not render the Close File dialog.".format(
                message
            ),
        )
        return False

    result = {"confirmed": False}

    form = WinForms.Form()
    form.Text = title
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    form.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    form.MaximizeBox = False
    form.MinimizeBox = False
    form.ShowInTaskbar = False
    form.TopMost = True
    form.ClientSize = Drawing.Size(420, 170)

    info = WinForms.Label()
    info.Left = 14
    info.Top = 14
    info.Width = 392
    info.Height = 78
    info.Text = message
    form.Controls.Add(info)

    close_btn = WinForms.Button()
    close_btn.Text = "Close File"
    close_btn.Width = 104
    close_btn.Height = 32
    close_btn.Left = 302
    close_btn.Top = 110

    def _confirm_close(sender, args):
        del sender, args
        result["confirmed"] = True
        form.Close()

    close_btn.Click += _confirm_close
    form.AcceptButton = close_btn
    form.Controls.Add(close_btn)

    try:
        form.ShowDialog()
    except Exception as ex:
        LOGGER.warning("Close File dialog failed to render: %s", ex)
        return False

    return bool(result.get("confirmed", False))


def _show_alert(title, message):
    UI = _get_ui()
    if UI is not None:
        try:
            UI.TaskDialog.Show(title, message)
            return
        except Exception:
            pass

    print("[RL Tools] {}: {}".format(title, message))


def _queue_pending_close(state, doc, command_name, doc_key="", doc_runtime_id=None):
    state["pending_close"] = {
        "doc_key": _safe_text(doc_key).strip() or _doc_key(doc),
        "doc_runtime_id": _to_int(doc_runtime_id if doc_runtime_id is not None else _get_doc_runtime_id(doc)),
        "doc_title": _safe_text(getattr(doc, "Title", "")).strip() if _is_doc_valid(doc) else "",
        "command_name": _safe_text(command_name).strip(),
        "queued_at": time.time(),
    }


def _register_repost_guard(state, doc_key="", doc_runtime_id=None, command_name=""):
    state["repost_guard"] = {
        "doc_key": _safe_text(doc_key).strip(),
        "doc_runtime_id": _to_int(doc_runtime_id),
        "command_name": _safe_text(command_name).strip(),
        "expires_at": time.time() + REPOST_GUARD_SEC,
        "command_seen": False,
    }


def _allow_reposted_command(state, doc_key="", doc_runtime_id=None, command_name=""):
    guard = state.get("repost_guard")
    if not isinstance(guard, dict):
        return False

    expires_at = float(guard.get("expires_at", 0.0) or 0.0)
    if time.time() > expires_at:
        state["repost_guard"] = None
        return False

    same_doc = _identity_matches(
        stored_doc_key=guard.get("doc_key"),
        stored_doc_runtime_id=guard.get("doc_runtime_id"),
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    )
    same_command = _safe_text(guard.get("command_name")).strip() == _safe_text(command_name).strip()
    if not (same_doc and same_command):
        return False

    guard["command_seen"] = True
    return True


def _consume_repost_guard(state, doc_key="", doc_runtime_id=None):
    guard = state.get("repost_guard")
    if not isinstance(guard, dict):
        return False

    expires_at = float(guard.get("expires_at", 0.0) or 0.0)
    if time.time() > expires_at:
        state["repost_guard"] = None
        return False

    if not _identity_matches(
        stored_doc_key=guard.get("doc_key"),
        stored_doc_runtime_id=guard.get("doc_runtime_id"),
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    ):
        return False

    state["repost_guard"] = None
    return True


def _clear_expired_repost_guard(state):
    guard = state.get("repost_guard")
    if not isinstance(guard, dict):
        return False

    expires_at = float(guard.get("expires_at", 0.0) or 0.0)
    if time.time() <= expires_at:
        return False

    state["repost_guard"] = None
    return True


def _has_pending_close(state):
    return isinstance(state.get("pending_close"), dict)


def _remember_command_name(state, command_name):
    recent = list(state.get("debug_last_commands") or [])
    recent.append(_safe_text(command_name))
    if len(recent) > MAX_DEBUG_COMMANDS:
        recent = recent[-MAX_DEBUG_COMMANDS:]
    state["debug_last_commands"] = recent


def _lookup_command_id(command_name):
    UI = _get_ui()
    if UI is None:
        return None
    try:
        return UI.RevitCommandId.LookupCommandId(command_name)
    except Exception:
        return None


def _find_doc_by_identity(uiapp, doc_key="", doc_runtime_id=None):
    doc = _find_doc_by_runtime_id(uiapp, doc_runtime_id)
    if doc is not None:
        return doc
    if _safe_text(doc_key).strip():
        return _find_doc_by_key(uiapp, doc_key)
    return None


def _find_doc_by_key(uiapp, doc_key):
    app = getattr(uiapp, "Application", None)
    docs = getattr(app, "Documents", None)
    if docs is None:
        return None

    try:
        for doc in docs:
            if _doc_key(doc) == _safe_text(doc_key).strip():
                return doc
    except Exception:
        return None
    return None


def _find_doc_by_runtime_id(uiapp, doc_runtime_id):
    doc_runtime_id = _to_int(doc_runtime_id)
    if doc_runtime_id is None:
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


def _identity_matches(stored_doc_key, stored_doc_runtime_id, doc_key="", doc_runtime_id=None):
    stored_runtime_id = _to_int(stored_doc_runtime_id)
    doc_runtime_id = _to_int(doc_runtime_id)
    if stored_runtime_id is not None and doc_runtime_id is not None:
        return stored_runtime_id == doc_runtime_id

    stored_doc_key = _safe_text(stored_doc_key).strip()
    doc_key = _safe_text(doc_key).strip()
    if stored_doc_key and doc_key:
        return stored_doc_key == doc_key

    return False


def _cancel_event(event_args):
    try:
        event_args.Cancel = True
        return True
    except Exception:
        pass

    try:
        event_args.Cancel()
        return True
    except Exception:
        LOGGER.debug(
            "RL Tools close guard could not cancel event. args_type=%s",
            _safe_text(type(event_args)),
        )
        return False


def _classify_command(command_name):
    normalized = _normalize_command_name(command_name)
    if not normalized:
        return None

    if any(token in normalized for token in CLOSE_KEYWORDS) and any(
        token in normalized for token in FILE_KEYWORDS
    ):
        return "close"

    return None


def _normalize_command_name(name):
    text = _safe_text(name).strip().upper()
    if not text:
        return ""
    return text.replace("-", "_").replace(" ", "_")


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

    doc_runtime_id = _extract_doc_runtime_id(event_args, None)
    if doc_runtime_id is not None:
        return _find_doc_by_runtime_id(uiapp, doc_runtime_id)

    return None


def _extract_doc_runtime_id(event_args, doc):
    if _is_doc_valid(doc):
        doc_runtime_id = _get_doc_runtime_id(doc)
        if doc_runtime_id is not None:
            return doc_runtime_id

    if event_args is None:
        return None

    return _to_int(_getattr_safe(event_args, "DocumentId"))


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


def _get_state():
    state = script.get_envvar(STATE_ENVVAR)
    if not isinstance(state, dict):
        state = {}

    state.setdefault("pending_close", None)
    state.setdefault("repost_guard", None)
    state.setdefault("debug_last_commands", [])

    if not isinstance(state.get("debug_last_commands"), list):
        state["debug_last_commands"] = []

    return state


def _save_state(state):
    script.set_envvar(STATE_ENVVAR, state)


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


def _getattr_safe(obj, path):
    parts = _safe_text(path).split(".")
    cur = obj
    for part in parts:
        if not part:
            continue
        try:
            cur = getattr(cur, part)
        except Exception:
            return None
    return cur
