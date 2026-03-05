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

    original_phase_filter_id = _phase_filter_from_session_or_view(existing, view)
    if original_phase_filter_id is None:
        _show_alert(
            "Temp Phase & View",
            "Active view does not support editable Phase Filter.",
        )
        return

    selection = _show_phase_picker(doc, view, original_phase_id, original_phase_filter_id)
    if selection is None:
        return

    template_id = _template_from_session_or_view(existing, view)
    ok, fail_reason = _apply_selected_phase_transaction(
        doc=doc,
        view=view,
        selected_phase_id=selection["selected_phase_id"],
        selected_phase_filter_id=selection["selected_phase_filter_id"],
        template_id=template_id,
    )
    if not ok:
        if fail_reason == "tvp-not-available":
            _show_alert(
                "Temp Phase & View",
                "Temporary View Properties cannot be enabled for this view in its current state.\n"
                "No phase changes were applied.",
            )
        else:
            _show_alert(
                "Temp Phase & View",
                "Could not apply selected phase and phase filter while preserving Temporary View Properties.",
            )
        return

    state["view_sessions"][view_key] = {
        "doc_key": doc_key,
        "view_id": view_id_int,
        "view_name": _safe_text(getattr(view, "Name", "")),
        "original_phase_id": int(original_phase_id),
        "original_phase_filter_id": int(original_phase_filter_id),
        "selected_phase_id": int(selection["selected_phase_id"]),
        "selected_phase_filter_id": int(selection["selected_phase_filter_id"]),
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

    command_kind = _classify_command(command_name)
    if command_kind != "close":
        _save_state(state)
        return

    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    doc = getattr(uidoc, "Document", None) if uidoc else None
    if not _is_doc_supported(doc):
        _save_state(state)
        return

    doc_key = _doc_key(doc)
    if not _has_doc_sessions(state, doc_key):
        _save_state(state)
        return

    if not _cancel_event(event_args):
        _save_state(state)
        return

    pending = state.get("pending_command")
    if not isinstance(pending, dict) or _safe_text(pending.get("doc_key")) != doc_key:
        state["pending_command"] = {
            "doc_key": doc_key,
            "command_name": command_name,
            "kind": command_kind,
            "attempts": 0,
            "next_try_at": time.time() + 0.05,
            "queued_at": time.time(),
        }
    _save_state(state)


def _phase_from_session_or_view(existing, view):
    if isinstance(existing, dict) and existing.get("original_phase_id") is not None:
        try:
            return int(existing.get("original_phase_id"))
        except Exception:
            pass
    return _get_view_phase_id(view)


def _phase_filter_from_session_or_view(existing, view):
    if isinstance(existing, dict) and existing.get("original_phase_filter_id") is not None:
        try:
            return int(existing.get("original_phase_filter_id"))
        except Exception:
            pass
    return _get_view_phase_filter_id(view)


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


def _apply_selected_phase_transaction(doc, view, selected_phase_id, selected_phase_filter_id, template_id):
    DB = _get_db()
    if DB is None:
        return False, "api-unavailable"

    tx = DB.Transaction(doc, "Temp Phase & View: Apply Phase")
    started = False
    try:
        tx.Start()
        started = True

        tvp_ready, tvp_reason = _ensure_tvp_active_for_apply(view, template_id)
        if not tvp_ready:
            raise Exception("TVP_NOT_AVAILABLE:{}".format(tvp_reason))

        if not _set_view_phase_id(view, selected_phase_id):
            raise Exception("Failed setting VIEW_PHASE")

        if not _set_view_phase_filter_id(view, selected_phase_filter_id):
            raise Exception("Failed setting VIEW_PHASE_FILTER")

        tx.Commit()
        return True, ""
    except Exception as ex:
        LOGGER.debug("Temp Phase & View apply failed: %s", ex)
        if started:
            _rollback_transaction(tx)
        if _safe_text(ex).startswith("TVP_NOT_AVAILABLE:"):
            return False, "tvp-not-available"
        return False, "apply-failed"


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


def _restore_sessions_for_doc(doc, doc_key, reason, state=None):
    owns_state = state is None
    if state is None:
        state = _get_state()

    stale_keys = []
    to_restore = []
    for view_key, session in list((state.get("view_sessions") or {}).items()):
        if not isinstance(session, dict):
            stale_keys.append(view_key)
            continue
        if _safe_text(session.get("doc_key")) != doc_key:
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

            original_filter = session.get("original_phase_filter_id")
            if original_filter is not None:
                if not _set_view_phase_filter_id(view, int(original_filter)):
                    raise Exception("Failed restoring VIEW_PHASE_FILTER")

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
    if not doc_key:
        state["pending_command"] = None
        return True

    restored_count = 0
    doc = _find_doc_by_key(uiapp, doc_key)
    if _is_doc_valid(doc):
        restored_ok, restored_count = _restore_sessions_for_doc(
            doc=doc,
            doc_key=doc_key,
            reason="pending-close",
            state=state,
        )
        if not restored_ok:
            return _retry_pending(state, pending, now)
    else:
        # Document likely already closed or not discoverable; clear tracked state.
        _drop_sessions_for_doc(state, doc_key)
    state["pending_command"] = None
    _show_ok_dialog(
        "Temp Phase & View",
        "Temporary phase settings were restored in {} view(s).\n\n"
        "File close was canceled.\n"
        "Click OK, then close the file again if you still want to close it.".format(restored_count),
    )
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


def _drop_sessions_for_doc(state, doc_key):
    for view_key, session in list((state.get("view_sessions") or {}).items()):
        if not isinstance(session, dict):
            state["view_sessions"].pop(view_key, None)
            state["last_seen_tvp"].pop(view_key, None)
            continue
        if _safe_text(session.get("doc_key")) == doc_key:
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
    cancellable = bool(getattr(event_args, "Cancellable", False))
    if not cancellable:
        return False

    try:
        event_args.Cancel()
        return True
    except Exception:
        pass

    try:
        event_args.Cancel = True
        return True
    except Exception:
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


def _has_doc_sessions(state, doc_key):
    for session in (state.get("view_sessions") or {}).values():
        if not isinstance(session, dict):
            continue
        if _safe_text(session.get("doc_key")) == doc_key:
            return True
    return False


def _show_phase_picker(doc, view, current_phase_id, current_phase_filter_id):
    phases = _collect_document_phases(doc)
    if not phases:
        _show_alert("Temp Phase & View", "No phases are available in this document.")
        return None

    phase_filters = _collect_document_phase_filters(doc)
    if not phase_filters:
        _show_alert("Temp Phase & View", "No phase filters are available in this document.")
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
    form.Height = 286
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

    filter_lbl = WinForms.Label()
    filter_lbl.Left = 14
    filter_lbl.Top = 110
    filter_lbl.Width = 430
    filter_lbl.Height = 20
    filter_lbl.Text = "Temporary Phase Filter:"
    form.Controls.Add(filter_lbl)

    filter_combo = WinForms.ComboBox()
    filter_combo.Left = 14
    filter_combo.Top = 132
    filter_combo.Width = 430
    filter_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList

    phase_filter_ids = []
    filter_selected_index = 0
    for idx, filter_data in enumerate(phase_filters):
        filter_combo.Items.Add("{} ({})".format(filter_data["name"], filter_data["id"]))
        phase_filter_ids.append(int(filter_data["id"]))
        if int(filter_data["id"]) == int(current_phase_filter_id):
            filter_selected_index = idx
    if phase_filter_ids:
        filter_combo.SelectedIndex = filter_selected_index
    form.Controls.Add(filter_combo)

    ok_btn = WinForms.Button()
    ok_btn.Text = "OK"
    ok_btn.DialogResult = WinForms.DialogResult.OK
    ok_btn.Left = 270
    ok_btn.Top = 188
    ok_btn.Width = 84
    form.Controls.Add(ok_btn)

    cancel_btn = WinForms.Button()
    cancel_btn.Text = "Cancel"
    cancel_btn.DialogResult = WinForms.DialogResult.Cancel
    cancel_btn.Left = 360
    cancel_btn.Top = 188
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

    filter_index = int(filter_combo.SelectedIndex)
    if filter_index < 0 or filter_index >= len(phase_filter_ids):
        return None

    return {
        "selected_phase_id": int(phase_ids[index]),
        "selected_phase_filter_id": int(phase_filter_ids[filter_index]),
    }


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


def _collect_document_phase_filters(doc):
    result = []
    DB = _get_db()
    if DB is None or not _is_doc_valid(doc):
        return result

    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.PhaseFilter)
        for phase_filter in collector:
            filter_id = _eid_to_int(getattr(phase_filter, "Id", None))
            if filter_id is None:
                continue
            result.append(
                {
                    "id": int(filter_id),
                    "name": _safe_text(getattr(phase_filter, "Name", "")) or "Phase Filter {}".format(filter_id),
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


def _get_view_phase_filter_param(view):
    DB = _get_db()
    if DB is None or not _is_view_valid(view):
        return None
    try:
        return view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE_FILTER)
    except Exception:
        return None


def _get_view_phase_filter_id(view):
    param = _get_view_phase_filter_param(view)
    if param is None:
        return None
    try:
        return _eid_to_int(param.AsElementId())
    except Exception:
        return None


def _set_view_phase_filter_id(view, phase_filter_id_int):
    if phase_filter_id_int is None:
        return False
    param = _get_view_phase_filter_param(view)
    if param is None:
        return False

    try:
        return bool(param.Set(_int_to_eid(phase_filter_id_int)))
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


def _ensure_tvp_active_for_apply(view, template_id):
    if _is_tvp_active(view):
        return True, ""

    can_enable = True
    checker = getattr(view, "CanEnableTemporaryViewPropertiesMode", None)
    if callable(checker):
        try:
            can_enable = bool(checker())
        except Exception:
            can_enable = True

    if not can_enable:
        return False, "can-enable-false"

    if _enable_tvp(view, template_id):
        return True, ""
    return False, "enable-failed"


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
