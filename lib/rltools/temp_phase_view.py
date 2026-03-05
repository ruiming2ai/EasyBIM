# -*- coding: utf-8 -*-
"""Temp Phase & View runtime engine."""

from __future__ import print_function

import time

from pyrevit import script


LOGGER = script.get_logger()

STATE_ENVVAR = "RLTOOLS_TEMP_PHASE_VIEW_STATE"

IDLE_THROTTLE_SEC = 0.25
REPOST_GUARD_SEC = 8.0
PENDING_POST_MAX_ATTEMPTS = 16
PENDING_POST_RETRY_SEC = 0.30

CLOSE_KEYWORDS = ("CLOSE",)
FILE_KEYWORDS = ("FILE", "PROJECT")


def run_pushbutton():
    """Pushbutton entrypoint."""
    uiapp = _get_uiapp()
    if uiapp is None:
        _show_alert("Temp Phase & View", "Revit UI context is not available.")
        return

    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    doc = getattr(uidoc, "Document", None) if uidoc else None
    view = getattr(uidoc, "ActiveView", None) if uidoc else None

    if not _is_doc_supported(doc):
        _show_alert("Temp Phase & View", "Open a project document to use this tool.")
        return
    if not _is_view_valid(view):
        _show_alert("Temp Phase & View", "Open a valid project view to use this tool.")
        return

    doc_key = _doc_key(doc)
    view_id_int = _eid_to_int(getattr(view, "Id", None))
    if view_id_int is None:
        _show_alert("Temp Phase & View", "Could not read active view id.")
        return
    view_key = _view_key(doc_key, view_id_int)

    state = _get_state()
    existing = state["view_sessions"].get(view_key)
    original_phase_id = _phase_from_session_or_view(existing, view)
    if original_phase_id is None:
        _show_alert(
            "Temp Phase & View",
            "Active view does not support editable Phase.",
        )
        return

    selected_phase_id = _show_phase_picker(doc, view, original_phase_id)
    if selected_phase_id is None:
        return

    template_id = _template_from_session_or_view(existing, view)
    ok = _apply_selected_phase_transaction(
        doc=doc,
        view=view,
        selected_phase_id=selected_phase_id,
        template_id=template_id,
    )
    if not ok:
        _show_alert(
            "Temp Phase & View",
            "Could not apply selected phase while preserving Temporary View Properties.",
        )
        return

    doc_runtime_id = _get_doc_runtime_id(doc)
    state["view_sessions"][view_key] = {
        "doc_key": doc_key,
        "doc_runtime_id": int(doc_runtime_id) if doc_runtime_id is not None else None,
        "view_id": view_id_int,
        "view_name": _safe_text(getattr(view, "Name", "")),
        "original_phase_id": int(original_phase_id),
        "selected_phase_id": int(selected_phase_id),
        "temp_template_id": int(template_id) if template_id is not None else None,
        "updated_at": time.time(),
    }
    state["last_seen_tvp"][view_key] = bool(_is_tvp_active(view))
    _save_state(state)


def handle_app_idling(uiapp=None, event_args=None):
    """Hook handler for app-idling."""
    del event_args  # not used

    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return

    state = _get_state()
    state_changed = False

    if _process_pending_doc_close(state, uiapp):
        state_changed = True

    if _process_pending_command(state, uiapp):
        state_changed = True

    now = time.time()
    last_idle = float(state.get("last_idling_ts", 0.0) or 0.0)
    if (now - last_idle) < IDLE_THROTTLE_SEC:
        if state_changed:
            _save_state(state)
        return

    state["last_idling_ts"] = now
    state_changed = True

    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    doc = getattr(uidoc, "Document", None) if uidoc else None
    view = getattr(uidoc, "ActiveView", None) if uidoc else None
    if not _is_doc_supported(doc) or not _is_view_valid(view):
        _save_state(state)
        return

    doc_key = _doc_key(doc)
    view_id_int = _eid_to_int(getattr(view, "Id", None))
    if view_id_int is None:
        _save_state(state)
        return

    view_key = _view_key(doc_key, view_id_int)
    tvp_active = bool(_is_tvp_active(view))

    previous = state["last_seen_tvp"].get(view_key)
    state["last_seen_tvp"][view_key] = tvp_active

    if previous is True and not tvp_active:
        if _restore_session_for_view(doc, state, view_key, "tvp-off"):
            state_changed = True

    if state_changed:
        _save_state(state)


def handle_doc_closing(uiapp=None, event_args=None):
    """Hook handler for doc-closing."""
    if event_args is None:
        return

    state = _get_state()
    uiapp = _get_uiapp(uiapp)

    closing_doc = _resolve_doc_from_event_args(event_args, uiapp)
    doc_runtime_id = _extract_doc_runtime_id(event_args, closing_doc)
    doc_key = _doc_key(closing_doc)

    if not _has_doc_sessions(state, doc_key=doc_key, doc_runtime_id=doc_runtime_id):
        _save_state(state)
        return

    if not _cancel_event(event_args):
        _save_state(state)
        return

    _queue_pending_doc_close(
        state=state,
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    )
    _save_state(state)


def handle_command_before_exec(uiapp=None, event_args=None):
    """Hook handler for command-before-exec."""
    if event_args is None:
        return

    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return

    command_name = _safe_text(_getattr_safe(event_args, "CommandId.Name")).strip()
    if not command_name:
        return

    state = _get_state()
    _remember_command_name(state, command_name)

    command_type = _classify_command(command_name)
    if command_type == "close":
        target_doc = _resolve_doc_from_event_args(event_args, uiapp)
        if not _is_doc_supported(target_doc):
            uidoc = getattr(uiapp, "ActiveUIDocument", None)
            target_doc = getattr(uidoc, "Document", None) if uidoc else None

        target_doc_runtime_id = _extract_doc_runtime_id(event_args, target_doc)
        target_doc_key = _doc_key(target_doc)
        if _has_doc_sessions(
            state,
            doc_key=target_doc_key,
            doc_runtime_id=target_doc_runtime_id,
        ):
            if _cancel_event(event_args):
                _queue_pending_doc_close(
                    state=state,
                    doc_key=target_doc_key,
                    doc_runtime_id=target_doc_runtime_id,
                )

    _save_state(state)


def _phase_from_session_or_view(existing, view):
    if isinstance(existing, dict) and existing.get("original_phase_id") is not None:
        try:
            return int(existing.get("original_phase_id"))
        except Exception:
            pass
    return _get_view_phase_id(view)


def _template_from_session_or_view(existing, view):
    if isinstance(existing, dict) and existing.get("temp_template_id") is not None:
        try:
            return int(existing.get("temp_template_id"))
        except Exception:
            pass
    template_id = _get_temp_template_reference_id(view)
    if template_id is not None:
        return int(template_id)
    return _eid_to_int(getattr(view, "Id", None))


def _apply_selected_phase_transaction(doc, view, selected_phase_id, template_id):
    DB = _get_db()
    if DB is None:
        return False

    tx = DB.Transaction(doc, "Temp Phase & View: Apply Phase")
    started = False
    try:
        tx.Start()
        started = True

        _disable_tvp(view)

        if not _set_view_phase_id(view, selected_phase_id):
            raise Exception("Failed setting VIEW_PHASE")

        if not _enable_tvp(view, template_id):
            raise Exception("Failed re-enabling TVP")

        tx.Commit()
        return True
    except Exception as ex:
        LOGGER.debug("Temp Phase & View apply failed: %s", ex)
        if started:
            _rollback_transaction(tx)
        return False


def _restore_session_for_view(doc, state, view_key, reason):
    session = state["view_sessions"].get(view_key)
    if not isinstance(session, dict):
        return False

    view_id = session.get("view_id")
    if view_id is None:
        state["view_sessions"].pop(view_key, None)
        state["last_seen_tvp"].pop(view_key, None)
        return True

    view = doc.GetElement(_int_to_eid(view_id))
    if not _is_view_valid(view):
        state["view_sessions"].pop(view_key, None)
        state["last_seen_tvp"].pop(view_key, None)
        return True

    restored_ok, _ = _restore_views_transaction(
        doc=doc,
        sessions=[(view_key, session, view)],
        reason=reason,
    )
    if restored_ok:
        state["view_sessions"].pop(view_key, None)
        state["last_seen_tvp"].pop(view_key, None)
    return restored_ok


def _restore_sessions_for_doc(doc, doc_key, reason, state=None, doc_runtime_id=None):
    owns_state = state is None
    if state is None:
        state = _get_state()

    if doc_runtime_id is None:
        doc_runtime_id = _get_doc_runtime_id(doc)

    stale_keys = []
    to_restore = []
    for view_key, session in list((state.get("view_sessions") or {}).items()):
        if not isinstance(session, dict):
            stale_keys.append(view_key)
            continue
        if not _session_matches_doc(
            session=session,
            doc_key=doc_key,
            doc_runtime_id=doc_runtime_id,
        ):
            continue

        view_id = session.get("view_id")
        if view_id is None:
            stale_keys.append(view_key)
            continue

        view = doc.GetElement(_int_to_eid(view_id))
        if _is_view_valid(view):
            to_restore.append((view_key, session, view))
        else:
            stale_keys.append(view_key)

    for key in stale_keys:
        state["view_sessions"].pop(key, None)
        state["last_seen_tvp"].pop(key, None)

    if not to_restore:
        if owns_state:
            _save_state(state)
        return True, 0

    restored_ok, restored_count = _restore_views_transaction(doc, to_restore, reason)
    if restored_ok:
        for view_key, _, _ in to_restore:
            state["view_sessions"].pop(view_key, None)
            state["last_seen_tvp"].pop(view_key, None)

    if owns_state:
        _save_state(state)

    return restored_ok, restored_count


def _restore_views_transaction(doc, sessions, reason):
    DB = _get_db()
    if DB is None:
        return False, 0

    tx = DB.Transaction(doc, "Temp Phase & View: Restore Phase")
    started = False
    restored_count = 0
    try:
        tx.Start()
        started = True

        for _, session, view in sessions:
            if not _is_view_valid(view):
                continue

            _disable_tvp(view)
            original_phase = session.get("original_phase_id")
            if original_phase is None:
                continue

            if not _set_view_phase_id(view, int(original_phase)):
                raise Exception("Failed restoring VIEW_PHASE")

            restored_count += 1

        tx.Commit()
        return True, restored_count
    except Exception as ex:
        LOGGER.debug("Temp Phase & View restore failed (%s): %s", reason, ex)
        if started:
            _rollback_transaction(tx)
        return False, 0


def _process_pending_command(state, uiapp):
    pending = state.get("pending_command")
    if not isinstance(pending, dict):
        return False

    now = time.time()
    next_try = float(pending.get("next_try_at", 0.0) or 0.0)
    if now < next_try:
        return False

    doc_key = _safe_text(pending.get("doc_key")).strip()
    doc_runtime_id = _to_int(pending.get("doc_runtime_id"))
    if not doc_key and doc_runtime_id is None:
        state["pending_command"] = None
        return True

    restored_count = 0
    doc = _find_doc_by_identity(uiapp, doc_key=doc_key, doc_runtime_id=doc_runtime_id)
    if _is_doc_valid(doc):
        restored_ok, restored_count = _restore_sessions_for_doc(
            doc=doc,
            doc_key=doc_key,
            doc_runtime_id=doc_runtime_id,
            reason="pending-close",
            state=state,
        )
        if not restored_ok:
            return _retry_pending(state, pending, now)
    else:
        # Document likely already closed or not discoverable; clear tracked state.
        _drop_sessions_for_doc(state, doc_key=doc_key, doc_runtime_id=doc_runtime_id)
    state["pending_command"] = None
    _show_ok_dialog(
        "Temp Phase & View",
        "Temporary phase settings were restored in {} view(s).\n\n"
        "File close was canceled.\n"
        "Click OK, then close the file again if you still want to close it.".format(restored_count),
    )
    return True


def _process_pending_doc_close(state, uiapp):
    pending = state.get("pending_doc_close")
    if not isinstance(pending, dict):
        return False

    now = time.time()
    next_try = float(pending.get("next_try_at", 0.0) or 0.0)
    if now < next_try:
        return False

    doc_key = _safe_text(pending.get("doc_key")).strip()
    doc_runtime_id = _to_int(pending.get("doc_runtime_id"))
    if not doc_key and doc_runtime_id is None:
        state["pending_doc_close"] = None
        return True

    restored_count = 0
    doc = _find_doc_by_identity(uiapp, doc_key=doc_key, doc_runtime_id=doc_runtime_id)
    if _is_doc_valid(doc):
        restored_ok, restored_count = _restore_sessions_for_doc(
            doc=doc,
            doc_key=doc_key,
            doc_runtime_id=doc_runtime_id,
            reason="pending-doc-close",
            state=state,
        )
        if not restored_ok:
            return _retry_pending_doc_close(state, pending, now)
    else:
        _drop_sessions_for_doc(state, doc_key=doc_key, doc_runtime_id=doc_runtime_id)

    if not bool(pending.get("dialog_shown", False)):
        _show_ok_dialog(
            "Temp Phase & View",
            "All Temporary Phases are clear\n\n"
            "Original phases were restored in {} view(s).\n"
            "Please close the file again if you still want to close it.".format(restored_count),
        )
        pending["dialog_shown"] = True

    state["pending_doc_close"] = None
    return True


def _retry_pending_doc_close(state, pending, now):
    attempts = int(pending.get("attempts", 0)) + 1
    pending["attempts"] = attempts
    if attempts >= PENDING_POST_MAX_ATTEMPTS:
        state["pending_doc_close"] = None
        _show_alert(
            "Temp Phase & View",
            "Could not clear all temporary phases before close. "
            "Close remains canceled; please try closing again.",
        )
    else:
        pending["next_try_at"] = now + PENDING_POST_RETRY_SEC
    return True


def _retry_pending(state, pending, now):
    attempts = int(pending.get("attempts", 0)) + 1
    pending["attempts"] = attempts
    if attempts >= PENDING_POST_MAX_ATTEMPTS:
        state["pending_command"] = None
        _show_alert(
            "Temp Phase & View",
            "Could not complete temporary phase restore before close. "
            "Close remains canceled; try closing again.",
        )
    else:
        pending["next_try_at"] = now + PENDING_POST_RETRY_SEC
    return True


def _lookup_command_id(command_name):
    UI = _get_ui()
    if UI is None:
        return None
    try:
        return UI.RevitCommandId.LookupCommandId(command_name)
    except Exception:
        return None


def _drop_sessions_for_doc(state, doc_key="", doc_runtime_id=None):
    for view_key, session in list((state.get("view_sessions") or {}).items()):
        if not isinstance(session, dict):
            state["view_sessions"].pop(view_key, None)
            state["last_seen_tvp"].pop(view_key, None)
            continue
        if _session_matches_doc(
            session=session,
            doc_key=doc_key,
            doc_runtime_id=doc_runtime_id,
        ):
            state["view_sessions"].pop(view_key, None)
            state["last_seen_tvp"].pop(view_key, None)


def _find_doc_by_key(uiapp, doc_key):
    app = getattr(uiapp, "Application", None)
    docs = getattr(app, "Documents", None)
    if docs is None:
        return None

    try:
        for doc in docs:
            if _doc_key(doc) == doc_key:
                return doc
    except Exception:
        return None
    return None


def _find_doc_by_runtime_id(uiapp, doc_runtime_id):
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


def _find_doc_by_identity(uiapp, doc_key="", doc_runtime_id=None):
    doc = _find_doc_by_runtime_id(uiapp, doc_runtime_id)
    if doc is not None:
        return doc
    if doc_key:
        return _find_doc_by_key(uiapp, doc_key)
    return None


def _consume_repost_guard(state, doc_key, command_name):
    guard = state.get("repost_guard")
    if not isinstance(guard, dict):
        return False

    if time.time() > float(guard.get("expires_at", 0.0) or 0.0):
        state["repost_guard"] = None
        return False

    same_doc = (_safe_text(guard.get("doc_key")) == _safe_text(doc_key))
    same_cmd = (_safe_text(guard.get("command_name")) == _safe_text(command_name))
    if same_doc and same_cmd:
        state["repost_guard"] = None
        return True
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
            "Temp Phase & View could not cancel event. args_type=%s",
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


def _remember_command_name(state, command_name):
    recent = list(state.get("debug_last_commands") or [])
    recent.append(_safe_text(command_name))
    if len(recent) > 12:
        recent = recent[-12:]
    state["debug_last_commands"] = recent


def _has_doc_sessions(state, doc_key="", doc_runtime_id=None):
    for session in (state.get("view_sessions") or {}).values():
        if not isinstance(session, dict):
            continue
        if _session_matches_doc(
            session=session,
            doc_key=doc_key,
            doc_runtime_id=doc_runtime_id,
        ):
            return True
    return False


def _queue_pending_doc_close(state, doc_key="", doc_runtime_id=None):
    pending = state.get("pending_doc_close")
    pending_doc_key = _safe_text(doc_key).strip()
    pending_doc_runtime_id = _to_int(doc_runtime_id)
    if isinstance(pending, dict):
        same_runtime = _to_int(pending.get("doc_runtime_id")) == pending_doc_runtime_id
        same_key = _safe_text(pending.get("doc_key")).strip() == pending_doc_key
        if same_runtime or (pending_doc_runtime_id is None and same_key):
            return

    state["pending_doc_close"] = {
        "doc_key": pending_doc_key,
        "doc_runtime_id": pending_doc_runtime_id,
        "queued_at": time.time(),
        "attempts": 0,
        "next_try_at": time.time() + 0.05,
        "dialog_shown": False,
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


def _session_matches_doc(session, doc_key="", doc_runtime_id=None):
    if not isinstance(session, dict):
        return False

    session_runtime_id = _to_int(session.get("doc_runtime_id"))
    match_runtime = (doc_runtime_id is not None and session_runtime_id == doc_runtime_id)
    if match_runtime:
        return True

    session_key = _safe_text(session.get("doc_key")).strip()
    target_key = _safe_text(doc_key).strip()
    if target_key and session_key == target_key:
        return True

    return False


def _show_phase_picker(doc, view, current_phase_id):
    phases = _collect_document_phases(doc)
    if not phases:
        _show_alert("Temp Phase & View", "No phases are available in this document.")
        return None

    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        import System.Windows.Forms as WinForms
    except Exception:
        _show_alert(
            "Temp Phase & View",
            "Could not load Windows Forms UI. Phase selection cancelled.",
        )
        return None

    form = WinForms.Form()
    form.Text = "Temp Phase & View"
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    form.Width = 470
    form.Height = 230
    form.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    form.MaximizeBox = False
    form.MinimizeBox = False

    info = WinForms.Label()
    info.Left = 14
    info.Top = 14
    info.Width = 430
    info.Height = 44
    info.Text = "Select Temporary Phase for view:\n{}".format(_safe_text(getattr(view, "Name", "")))
    form.Controls.Add(info)

    combo = WinForms.ComboBox()
    combo.Left = 14
    combo.Top = 72
    combo.Width = 430
    combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList

    phase_ids = []
    selected_index = 0
    for idx, phase_data in enumerate(phases):
        combo.Items.Add("{} ({})".format(phase_data["name"], phase_data["id"]))
        phase_ids.append(int(phase_data["id"]))
        if int(phase_data["id"]) == int(current_phase_id):
            selected_index = idx
    if phase_ids:
        combo.SelectedIndex = selected_index
    form.Controls.Add(combo)

    ok_btn = WinForms.Button()
    ok_btn.Text = "OK"
    ok_btn.DialogResult = WinForms.DialogResult.OK
    ok_btn.Left = 270
    ok_btn.Top = 132
    ok_btn.Width = 84
    form.Controls.Add(ok_btn)

    cancel_btn = WinForms.Button()
    cancel_btn.Text = "Cancel"
    cancel_btn.DialogResult = WinForms.DialogResult.Cancel
    cancel_btn.Left = 360
    cancel_btn.Top = 132
    cancel_btn.Width = 84
    form.Controls.Add(cancel_btn)

    form.AcceptButton = ok_btn
    form.CancelButton = cancel_btn

    result = form.ShowDialog()
    if result != WinForms.DialogResult.OK:
        return None

    index = int(combo.SelectedIndex)
    if index < 0 or index >= len(phase_ids):
        return None
    return int(phase_ids[index])


def _collect_document_phases(doc):
    result = []
    if not _is_doc_valid(doc):
        return result
    try:
        for phase in doc.Phases:
            phase_id = _eid_to_int(getattr(phase, "Id", None))
            if phase_id is None:
                continue
            result.append(
                {
                    "id": int(phase_id),
                    "name": _safe_text(getattr(phase, "Name", "")) or "Phase {}".format(phase_id),
                }
            )
    except Exception:
        return []
    return result


def _get_view_phase_param(view):
    DB = _get_db()
    if DB is None or not _is_view_valid(view):
        return None
    try:
        return view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
    except Exception:
        return None


def _get_view_phase_id(view):
    param = _get_view_phase_param(view)
    if param is None:
        return None
    try:
        return _eid_to_int(param.AsElementId())
    except Exception:
        return None


def _set_view_phase_id(view, phase_id_int):
    if phase_id_int is None:
        return False
    param = _get_view_phase_param(view)
    if param is None:
        return False

    try:
        return bool(param.Set(_int_to_eid(phase_id_int)))
    except Exception:
        return False


def _get_temp_template_reference_id(view):
    if not _is_view_valid(view):
        return None

    getter_names = (
        "GetTemporaryViewPropertiesId",
        "GetTemporaryViewPropertiesViewId",
    )
    prop_names = (
        "TemporaryViewPropertiesId",
        "TemporaryViewPropertiesViewId",
    )

    for getter_name in getter_names:
        getter = getattr(view, getter_name, None)
        if callable(getter):
            try:
                value = getter()
                value_int = _eid_to_int(value)
                if _is_valid_id(value_int):
                    return int(value_int)
            except Exception:
                continue

    for prop_name in prop_names:
        try:
            value = getattr(view, prop_name)
        except Exception:
            value = None
        value_int = _eid_to_int(value)
        if _is_valid_id(value_int):
            return int(value_int)

    return None


def _is_tvp_active(view):
    DB = _get_db()
    if DB is None or not _is_view_valid(view):
        return False
    try:
        if hasattr(view, "IsTemporaryViewModeEnabled"):
            return bool(
                view.IsTemporaryViewModeEnabled(DB.TemporaryViewMode.TemporaryViewProperties)
            )
    except Exception:
        pass

    try:
        fn = getattr(view, "IsTemporaryViewPropertiesModeEnabled", None)
        if callable(fn):
            return bool(fn())
    except Exception:
        pass
    return False


def _disable_tvp(view):
    DB = _get_db()
    if DB is None or not _is_view_valid(view):
        return False
    try:
        view.DisableTemporaryViewMode(DB.TemporaryViewMode.TemporaryViewProperties)
        return True
    except Exception:
        return False


def _enable_tvp(view, template_id):
    if not _is_view_valid(view):
        return False

    target_ids = []
    if template_id is not None:
        target_ids.append(_int_to_eid(template_id))
    target_ids.append(getattr(view, "Id", None))

    for target_id in target_ids:
        if target_id is None:
            continue
        try:
            view.EnableTemporaryViewPropertiesMode(target_id)
            return True
        except Exception:
            continue
    return False


def _show_alert(title, message):
    UI = _get_ui()
    if UI is not None:
        try:
            UI.TaskDialog.Show(title, message)
            return
        except Exception:
            pass
    print("[Temp Phase & View] {}: {}".format(title, message))


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
        return
    except Exception:
        pass

    _show_alert(title, message)


def _view_key(doc_key, view_id_int):
    return "{}|{}".format(doc_key, int(view_id_int))


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


def _is_view_valid(view):
    if view is None:
        return False
    try:
        if not bool(view.IsValidObject):
            return False
    except Exception:
        pass
    try:
        if bool(view.IsTemplate):
            return False
    except Exception:
        pass
    return True


def _rollback_transaction(tx):
    try:
        tx.RollBack()
        return
    except Exception:
        pass
    try:
        tx.Rollback()
    except Exception:
        pass


def _get_state():
    state = script.get_envvar(STATE_ENVVAR)
    if not isinstance(state, dict):
        state = {}

    state.setdefault("last_idling_ts", 0.0)
    state.setdefault("last_seen_tvp", {})
    state.setdefault("view_sessions", {})
    state.setdefault("pending_doc_close", None)
    state.setdefault("pending_command", None)
    state.setdefault("repost_guard", None)
    state.setdefault("debug_last_commands", [])

    if not isinstance(state.get("last_seen_tvp"), dict):
        state["last_seen_tvp"] = {}
    if not isinstance(state.get("view_sessions"), dict):
        state["view_sessions"] = {}
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


def _get_db():
    try:
        from Autodesk.Revit import DB

        return DB
    except Exception:
        return None


def _get_ui():
    try:
        from Autodesk.Revit import UI

        return UI
    except Exception:
        return None


def _eid_to_int(element_id):
    if element_id is None:
        return None
    try:
        return int(element_id.IntegerValue)
    except Exception:
        pass
    try:
        return int(element_id.Value)
    except Exception:
        pass
    try:
        return int(element_id)
    except Exception:
        return None


def _to_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _int_to_eid(value):
    DB = _get_db()
    if DB is None:
        return None
    try:
        return DB.ElementId(int(value))
    except Exception:
        return None


def _is_valid_id(value):
    if value is None:
        return False
    try:
        return int(value) > 0
    except Exception:
        return False


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
