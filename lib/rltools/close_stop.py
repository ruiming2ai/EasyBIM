# -*- coding: utf-8 -*-
"""Shared close-stop runtime for hooks and the Close Stop pushbutton."""

from __future__ import print_function

import time

from pyrevit import script


LOGGER = script.get_logger()

STATE_ENVVAR = "RLTOOLS_CLOSE_STOP_STATE"
ALLOW_CLOSE_SEC = 10.0
MAX_DEBUG_COMMANDS = 12
TITLE_STOP = "Stop Close File"
TITLE_CLOSE = "Close File Now"
FALLBACK_COMMAND_NAMES = (
    "ID_REVIT_FILE_CLOSE",
    "ID_FILE_CLOSE",
    "ID_REVIT_PROJECT_CLOSE",
    "ID_PROJECT_CLOSE",
)


def run_pushbutton(uiapp=None):
    """Run the close-stop flow manually from the ribbon button."""
    uiapp = _get_uiapp(uiapp)
    doc = _get_active_project_doc(uiapp)
    if not _is_doc_supported(doc):
        _show_alert("Close Stop", "Open a project document to use Close Stop.")
        return

    state = _get_state()
    _clear_expired_allow_close_once(state)
    _save_state(state)
    _run_close_sequence(
        uiapp=uiapp,
        doc=doc,
        preferred_command_name=_get_preferred_command_name(
            state,
            _doc_key(doc),
            _get_doc_runtime_id(doc),
        ),
        state=state,
        source="pushbutton",
    )


def capture_close_command(uiapp=None, event_args=None):
    """Capture the Revit close command name for later repost."""
    if event_args is None:
        return

    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return

    command_name = _safe_text(_getattr_safe(event_args, "CommandId.Name")).strip()
    if not command_name:
        return

    target_doc = _resolve_doc_from_event_args(event_args, uiapp)
    if not _is_doc_supported(target_doc):
        target_doc = _get_active_project_doc(uiapp)
    if not _is_doc_supported(target_doc):
        return

    state = _get_state()
    _remember_debug_command(state, command_name)
    state["last_close_command"] = {
        "doc_key": _doc_key(target_doc),
        "doc_runtime_id": _get_doc_runtime_id(target_doc),
        "command_name": command_name,
        "captured_at": time.time(),
    }
    _save_state(state)


def handle_command_before_exec(uiapp=None, event_args=None):
    """Cancel supported close commands before execution and queue Close Stop."""
    if event_args is None:
        return

    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return

    closing_doc = _resolve_doc_from_event_args(event_args, uiapp)
    if not _is_doc_supported(closing_doc):
        closing_doc = _get_active_project_doc(uiapp)
    if not _is_doc_supported(closing_doc):
        return

    state = _get_state()
    _clear_expired_allow_close_once(state)

    command_name = _safe_text(_getattr_safe(event_args, "CommandId.Name")).strip()
    doc_key = _doc_key(closing_doc)
    doc_runtime_id = _get_doc_runtime_id(closing_doc)

    if _consume_allow_close_once(
        state,
        doc_key,
        doc_runtime_id,
        command_name=command_name,
    ):
        _save_state(state)
        return

    cancellable = _is_event_cancellable(event_args)
    pending = state.get("pending_close")
    if isinstance(pending, dict):
        cancel_succeeded = _cancel_event(event_args) if cancellable else False
        _set_debug_last_intercept(
            state=state,
            stage="before-exec",
            doc=closing_doc,
            cancellable=cancellable,
            cancel_succeeded=cancel_succeeded,
            reason="pending-close-exists",
        )
        _save_state(state)
        return

    if not cancellable:
        _set_debug_last_intercept(
            state=state,
            stage="before-exec",
            doc=closing_doc,
            cancellable=False,
            cancel_succeeded=False,
            reason="event-not-cancellable",
        )
        _save_state(state)
        return

    cancel_succeeded = _cancel_event(event_args)
    _set_debug_last_intercept(
        state=state,
        stage="before-exec",
        doc=closing_doc,
        cancellable=True,
        cancel_succeeded=cancel_succeeded,
        reason="queued-close-stop" if cancel_succeeded else "cancel-failed",
    )
    if not cancel_succeeded:
        _save_state(state)
        return

    _queue_pending_close(
        state=state,
        doc=closing_doc,
        preferred_command_name=command_name
        or _get_preferred_command_name(state, doc_key, doc_runtime_id),
        source="hook",
        intercept_stage="before-exec",
    )
    _save_state(state)


def handle_doc_closing(uiapp=None, event_args=None):
    """Fallback close interception when command-before-exec did not stop close."""
    if event_args is None:
        return

    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return

    closing_doc = _resolve_doc_from_event_args(event_args, uiapp)
    if not _is_doc_supported(closing_doc):
        closing_doc = _get_active_project_doc(uiapp)
    if not _is_doc_supported(closing_doc):
        return

    state = _get_state()
    _clear_expired_allow_close_once(state)

    doc_key = _doc_key(closing_doc)
    doc_runtime_id = _extract_doc_runtime_id(event_args, closing_doc)

    if _consume_allow_close_once(state, doc_key, doc_runtime_id):
        _save_state(state)
        return

    cancellable = _is_event_cancellable(event_args)
    pending = state.get("pending_close")
    if isinstance(pending, dict):
        cancel_succeeded = _cancel_event(event_args) if cancellable else False
        _set_debug_last_intercept(
            state=state,
            stage="doc-closing",
            doc=closing_doc,
            cancellable=cancellable,
            cancel_succeeded=cancel_succeeded,
            reason="pending-close-exists",
        )
        _save_state(state)
        return

    if not cancellable:
        _set_debug_last_intercept(
            state=state,
            stage="doc-closing",
            doc=closing_doc,
            cancellable=False,
            cancel_succeeded=False,
            reason="event-not-cancellable",
        )
        _save_state(state)
        return

    cancel_succeeded = _cancel_event(event_args)
    _set_debug_last_intercept(
        state=state,
        stage="doc-closing",
        doc=closing_doc,
        cancellable=True,
        cancel_succeeded=cancel_succeeded,
        reason="queued-close-stop" if cancel_succeeded else "cancel-failed",
    )
    if not cancel_succeeded:
        _save_state(state)
        return

    _queue_pending_close(
        state=state,
        doc=closing_doc,
        preferred_command_name=_get_preferred_command_name(
            state,
            doc_key,
            doc_runtime_id,
        ),
        source="hook",
        intercept_stage="doc-closing",
    )
    _save_state(state)


def handle_app_idling(uiapp=None, event_args=None):
    """Process queued close-stop work on idling."""
    del event_args  # not used

    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return

    state = _get_state()
    state_changed = _clear_expired_allow_close_once(state)

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
    preferred_command_name = _safe_text(pending.get("preferred_command_name")).strip()

    doc = _find_doc_by_identity(uiapp, doc_key=doc_key, doc_runtime_id=doc_runtime_id)
    state["pending_close"] = None

    if not _is_doc_supported(doc):
        return True

    _run_close_sequence(
        uiapp=uiapp,
        doc=doc,
        preferred_command_name=preferred_command_name,
        state=state,
        source=_safe_text(pending.get("source")).strip() or "hook",
    )
    return True


def _run_close_sequence(uiapp, doc, preferred_command_name, state, source):
    del source  # reserved for future diagnostics

    if uiapp is None or not _is_doc_supported(doc):
        return False

    if not _show_action_dialog(
        title=TITLE_STOP,
        button_text="OK",
        instruction_text="Click OK to continue.",
    ):
        return False

    if not _show_action_dialog(
        title=TITLE_CLOSE,
        button_text="Close",
        instruction_text="Click Close to close the file.",
    ):
        return False

    command_name, command_id = _resolve_close_command(
        uiapp=uiapp,
        doc=doc,
        preferred_command_name=preferred_command_name,
        state=state,
    )
    if command_id is None:
        _show_alert(
            TITLE_CLOSE,
            "RL Tools could not determine a close command.\n\nRecent close commands:\n{}".format(
                _debug_command_lines(state)
            ),
        )
        return False

    _register_allow_close_once(
        state=state,
        doc=doc,
        command_name=command_name,
    )
    _save_state(state)

    try:
        uiapp.PostCommand(command_id)
        return True
    except Exception as ex:
        state["allow_close_once"] = None
        _save_state(state)
        _show_alert(
            TITLE_CLOSE,
            "RL Tools could not repost the close command.\n\n{}".format(ex),
        )
        return False


def _resolve_close_command(uiapp, doc, preferred_command_name, state):
    doc_key = _doc_key(doc)
    doc_runtime_id = _get_doc_runtime_id(doc)

    candidates = []
    for name in (
        preferred_command_name,
        _get_preferred_command_name(state, doc_key, doc_runtime_id),
    ) + FALLBACK_COMMAND_NAMES:
        normalized = _safe_text(name).strip()
        if not normalized or normalized in candidates:
            continue
        candidates.append(normalized)

    for command_name in candidates:
        command_id = _lookup_command_id(command_name)
        if command_id is None:
            continue
        if not _can_post_command(uiapp, command_id):
            continue
        return command_name, command_id

    return "", None


def _show_action_dialog(title, button_text, instruction_text=""):
    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        import System.Drawing as Drawing
        import System.Windows.Forms as WinForms
    except Exception:
        if button_text == "OK":
            return _show_ok_messagebox(title, instruction_text or title)
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
    form.ClientSize = Drawing.Size(400, 155)

    title_lbl = WinForms.Label()
    title_lbl.Left = 14
    title_lbl.Top = 16
    title_lbl.Width = 372
    title_lbl.Height = 32
    title_lbl.Text = title
    title_lbl.Font = Drawing.Font(title_lbl.Font.FontFamily, 12, Drawing.FontStyle.Bold)
    form.Controls.Add(title_lbl)

    info_lbl = WinForms.Label()
    info_lbl.Left = 14
    info_lbl.Top = 58
    info_lbl.Width = 372
    info_lbl.Height = 34
    info_lbl.Text = instruction_text or ""
    form.Controls.Add(info_lbl)

    action_btn = WinForms.Button()
    action_btn.Text = button_text
    action_btn.Width = 96
    action_btn.Height = 30
    action_btn.Left = 290
    action_btn.Top = 108

    def _confirm(sender, args):
        del sender, args
        result["confirmed"] = True
        form.Close()

    action_btn.Click += _confirm
    form.AcceptButton = action_btn
    form.Controls.Add(action_btn)

    try:
        form.ShowDialog()
    except Exception as ex:
        LOGGER.warning("Close Stop dialog failed to render: %s", ex)
        return False

    return bool(result.get("confirmed", False))


def _show_ok_messagebox(title, message):
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


def _register_allow_close_once(state, doc, command_name):
    state["allow_close_once"] = {
        "doc_key": _doc_key(doc),
        "doc_runtime_id": _get_doc_runtime_id(doc),
        "command_name": _safe_text(command_name).strip(),
        "expires_at": time.time() + ALLOW_CLOSE_SEC,
    }


def _consume_allow_close_once(state, doc_key, doc_runtime_id, command_name=""):
    guard = state.get("allow_close_once")
    if not isinstance(guard, dict):
        return False

    expires_at = float(guard.get("expires_at", 0.0) or 0.0)
    if time.time() > expires_at:
        state["allow_close_once"] = None
        return False

    if not _identity_matches(
        stored_doc_key=guard.get("doc_key"),
        stored_doc_runtime_id=guard.get("doc_runtime_id"),
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    ):
        return False

    expected_command_name = _safe_text(guard.get("command_name")).strip()
    command_name = _safe_text(command_name).strip()
    if command_name and expected_command_name and command_name != expected_command_name:
        return False

    state["allow_close_once"] = None
    return True


def _clear_expired_allow_close_once(state):
    guard = state.get("allow_close_once")
    if not isinstance(guard, dict):
        return False

    expires_at = float(guard.get("expires_at", 0.0) or 0.0)
    if time.time() <= expires_at:
        return False

    state["allow_close_once"] = None
    return True


def _queue_pending_close(state, doc, preferred_command_name, source, intercept_stage=""):
    state["pending_close"] = {
        "doc_key": _doc_key(doc),
        "doc_runtime_id": _get_doc_runtime_id(doc),
        "doc_title": _safe_text(getattr(doc, "Title", "")).strip(),
        "preferred_command_name": _safe_text(preferred_command_name).strip(),
        "queued_at": time.time(),
        "source": _safe_text(source).strip(),
        "intercept_stage": _safe_text(intercept_stage).strip(),
    }


def _get_preferred_command_name(state, doc_key, doc_runtime_id):
    record = state.get("last_close_command")
    if not isinstance(record, dict):
        return ""

    if not _identity_matches(
        stored_doc_key=record.get("doc_key"),
        stored_doc_runtime_id=record.get("doc_runtime_id"),
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    ):
        return ""

    return _safe_text(record.get("command_name")).strip()


def _remember_debug_command(state, command_name):
    recent = list(state.get("debug_seen_close_commands") or [])
    recent.append(_safe_text(command_name))
    if len(recent) > MAX_DEBUG_COMMANDS:
        recent = recent[-MAX_DEBUG_COMMANDS:]
    state["debug_seen_close_commands"] = recent


def _debug_command_lines(state):
    recent = list(state.get("debug_seen_close_commands") or [])
    if not recent:
        return "(none captured)"
    return "\n".join(recent)


def _set_debug_last_intercept(
    state,
    stage,
    doc,
    cancellable,
    cancel_succeeded,
    reason,
):
    state["debug_last_intercept"] = {
        "stage": _safe_text(stage).strip(),
        "doc_title": _safe_text(getattr(doc, "Title", "")).strip() if doc else "",
        "cancellable": bool(cancellable),
        "cancel_succeeded": bool(cancel_succeeded),
        "timestamp": time.time(),
        "reason": _safe_text(reason).strip(),
    }


def _lookup_command_id(command_name):
    UI = _get_ui()
    if UI is None:
        return None
    try:
        return UI.RevitCommandId.LookupCommandId(command_name)
    except Exception:
        return None


def _can_post_command(uiapp, command_id):
    if uiapp is None or command_id is None:
        return False

    can_post = getattr(uiapp, "CanPostCommand", None)
    if callable(can_post):
        try:
            return bool(can_post(command_id))
        except Exception:
            pass
    return True


def _is_event_cancellable(event_args):
    if event_args is None:
        return False

    cancellable = _getattr_safe(event_args, "Cancellable")
    if cancellable is None:
        return True

    try:
        return bool(cancellable)
    except Exception:
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
            "Close Stop could not cancel closing event. args_type=%s",
            _safe_text(type(event_args)),
        )
        return False


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


def _get_active_project_doc(uiapp):
    if uiapp is None:
        return None
    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    doc = getattr(uidoc, "Document", None) if uidoc else None
    return doc if _is_doc_supported(doc) else None


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

    print("[Close Stop] {}: {}".format(title, message))


def _get_state():
    state = script.get_envvar(STATE_ENVVAR)
    if not isinstance(state, dict):
        state = {}

    state.setdefault("pending_close", None)
    state.setdefault("last_close_command", None)
    state.setdefault("allow_close_once", None)
    state.setdefault("debug_seen_close_commands", [])
    state.setdefault("debug_last_intercept", None)

    if not isinstance(state.get("debug_seen_close_commands"), list):
        state["debug_seen_close_commands"] = []

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
