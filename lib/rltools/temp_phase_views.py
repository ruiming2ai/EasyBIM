# -*- coding: utf-8 -*-
"""TEMP Phase & Views runtime engine.

This module provides hook handlers and pushbutton entrypoints for managing
temporary phase switching while Temporary View Properties mode is active.
"""

from __future__ import print_function

import time

from pyrevit import script


LOGGER = script.get_logger()

STATE_ENVVAR = "RLTOOLS_TEMP_PHASE_VIEWS_STATE"

IDLE_THROTTLE_SEC = 0.25
SUPPRESS_TRANSITION_SEC = 1.2
REPOST_GUARD_SEC = 8.0
PENDING_POST_MAX_ATTEMPTS = 16
PENDING_POST_RETRY_SEC = 0.30

CLOSE_KEYWORDS = ("CLOSE",)
FILE_KEYWORDS = ("FILE", "PROJECT")


def run_pushbutton():
    """Pushbutton entrypoint."""
    uiapp = _get_uiapp()
    if uiapp is None:
        _show_alert("TEMP Phase & Views", "Revit UI context is not available.")
        return

    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    doc = getattr(uidoc, "Document", None) if uidoc else None
    if not _is_doc_supported(doc):
        _show_alert(
            "TEMP Phase & Views",
            "Open a project document to use this tool.",
        )
        return

    while True:
        state = _get_state()
        action = _show_control_dialog(state, doc)
        if action == "close":
            return

        if action == "toggle":
            state["enabled"] = not bool(state.get("enabled", True))
            _save_state(state)
            continue

        if action == "apply":
            view = _get_active_view(uiapp)
            if not _is_view_valid(view):
                _show_alert("TEMP Phase & Views", "Active view is not available.")
                continue
            _manual_apply_for_active_view(state, doc, view)
            continue

        if action == "restore":
            doc_key = _doc_key(doc)
            restored = _restore_sessions_for_doc(doc, doc_key, "manual", state=state)
            _save_state(state)
            _show_alert(
                "TEMP Phase & Views",
                "Restored {} tracked view(s).".format(restored),
            )
            continue


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
    tvp_active = _is_tvp_active(view)

    previous = state["last_seen_tvp"].get(view_key)
    state["last_seen_tvp"][view_key] = bool(tvp_active)

    if previous is None:
        _save_state(state)
        return

    if bool(previous) is False and bool(tvp_active) is True and bool(state.get("enabled", True)):
        if _prompt_and_apply_phase(state, doc, view, doc_key, view_key, force=False):
            state_changed = True

    if bool(previous) is True and bool(tvp_active) is False:
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
    if command_kind is None:
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

    if _consume_repost_guard(state, doc_key, command_name):
        _save_state(state)
        return

    if not _cancel_event(event_args):
        _save_state(state)
        return

    state["pending_command"] = {
        "doc_key": doc_key,
        "command_name": command_name,
        "kind": command_kind,
        "attempts": 0,
        "next_try_at": time.time() + 0.05,
        "queued_at": time.time(),
    }
    _save_state(state)


def _manual_apply_for_active_view(state, doc, view):
    doc_key = _doc_key(doc)
    view_id_int = _eid_to_int(getattr(view, "Id", None))
    if view_id_int is None:
        _show_alert("TEMP Phase & Views", "Active view id is not available.")
        return
    view_key = _view_key(doc_key, view_id_int)
    if not _is_tvp_active(view):
        template_id = _get_temp_template_reference_id(view) or _eid_to_int(view.Id)
        if not _enable_tvp(view, template_id):
            _show_alert(
                "TEMP Phase & Views",
                "Could not enable Temporary View Properties mode in this view.",
            )
            return

        # Force state map so the next idling cycle does not treat this as external toggle.
        state["last_seen_tvp"][view_key] = True

    if _prompt_and_apply_phase(state, doc, view, doc_key, view_key, force=True):
        _save_state(state)


def _prompt_and_apply_phase(state, doc, view, doc_key, view_key, force=False):
    now = time.time()
    existing = state["view_sessions"].get(view_key)
    suppress_until = float((existing or {}).get("suppress_until", 0.0) or 0.0)
    if not force and suppress_until and now < suppress_until:
        return False

    original_phase_id = _phase_from_session_or_view(existing, view)
    if original_phase_id is None:
        _show_alert(
            "TEMP Phase & Views",
            "Active view does not support editable Phase.",
        )
        return False

    selected_phase_id = _show_phase_picker(doc, view, original_phase_id)
    if selected_phase_id is None:
        return False

    if selected_phase_id == original_phase_id and existing is None:
        return False

    template_id = _template_from_session_or_view(existing, view)
    ok = _apply_selected_phase_transaction(
        doc=doc,
        view=view,
        selected_phase_id=selected_phase_id,
        template_id=template_id,
    )
    if not ok:
        _show_alert(
            "TEMP Phase & Views",
            "Could not apply selected phase while preserving Temporary View Properties.",
        )
        return False

    state["view_sessions"][view_key] = {
        "doc_key": doc_key,
        "view_id": _eid_to_int(getattr(view, "Id", None)),
        "view_name": _safe_text(getattr(view, "Name", "")),
        "original_phase_id": int(original_phase_id),
        "selected_phase_id": int(selected_phase_id),
        "temp_template_id": int(template_id) if template_id is not None else None,
        "suppress_until": time.time() + SUPPRESS_TRANSITION_SEC,
        "updated_at": time.time(),
    }
    state["last_seen_tvp"][view_key] = True
    return True


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

    tx = DB.Transaction(doc, "TEMP Phase & Views: Apply Phase")
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
        LOGGER.debug("TEMP Phase & Views apply failed: %s", ex)
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

    restored = _restore_views_transaction(
        doc=doc,
        sessions=[(view_key, session, view)],
        reason=reason,
    )
    if restored:
        state["view_sessions"].pop(view_key, None)
        state["last_seen_tvp"].pop(view_key, None)
    return restored


def _restore_sessions_for_doc(doc, doc_key, reason, state=None):
    owns_state = state is None
    if state is None:
        state = _get_state()
    to_restore = []
    for view_key, session in list(state["view_sessions"].items()):
        if not isinstance(session, dict):
            continue
        if _safe_text(session.get("doc_key")) != doc_key:
            continue
        view_id = session.get("view_id")
        if view_id is None:
            continue
        view = doc.GetElement(_int_to_eid(view_id))
        if _is_view_valid(view):
            to_restore.append((view_key, session, view))

    if not to_restore:
        return 0

    restored_count = _restore_views_transaction(doc, to_restore, reason)
    for view_key, session, view in to_restore:
        del session, view  # silence lint in CPython and IronPython
        state["view_sessions"].pop(view_key, None)
        state["last_seen_tvp"].pop(view_key, None)
    if owns_state:
        _save_state(state)
    return restored_count


def _restore_views_transaction(doc, sessions, reason):
    DB = _get_db()
    if DB is None:
        return 0

    tx = DB.Transaction(doc, "TEMP Phase & Views: Restore Phase")
    started = False
    restored = 0
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
            if _set_view_phase_id(view, int(original_phase)):
                restored += 1

        tx.Commit()
    except Exception as ex:
        LOGGER.debug("TEMP Phase & Views restore failed (%s): %s", reason, ex)
        if started:
            _rollback_transaction(tx)
        return 0
    return restored


def _process_pending_command(state, uiapp):
    pending = state.get("pending_command")
    if not isinstance(pending, dict):
        return False

    now = time.time()
    next_try = float(pending.get("next_try_at", 0.0) or 0.0)
    if now < next_try:
        return False

    command_name = _safe_text(pending.get("command_name")).strip()
    doc_key = _safe_text(pending.get("doc_key")).strip()
    if not command_name or not doc_key:
        state["pending_command"] = None
        return True

    doc = _find_doc_by_key(uiapp, doc_key)
    if _is_doc_valid(doc):
        _restore_sessions_for_doc(
            doc,
            doc_key,
            "pending-{}".format(pending.get("kind", "")),
            state=state,
        )

    cmd_id = _lookup_command_id(command_name)
    if cmd_id is None:
        pending["attempts"] = int(pending.get("attempts", 0)) + 1
        if pending["attempts"] >= PENDING_POST_MAX_ATTEMPTS:
            state["pending_command"] = None
        else:
            pending["next_try_at"] = now + PENDING_POST_RETRY_SEC
        return True

    can_post = True
    if hasattr(uiapp, "CanPostCommand"):
        try:
            can_post = bool(uiapp.CanPostCommand(cmd_id))
        except Exception:
            can_post = True

    if not can_post:
        pending["attempts"] = int(pending.get("attempts", 0)) + 1
        if pending["attempts"] >= PENDING_POST_MAX_ATTEMPTS:
            state["pending_command"] = None
        else:
            pending["next_try_at"] = now + PENDING_POST_RETRY_SEC
        return True

    state["repost_guard"] = {
        "doc_key": doc_key,
        "command_name": command_name,
        "expires_at": now + REPOST_GUARD_SEC,
    }
    try:
        uiapp.PostCommand(cmd_id)
    except Exception as ex:
        LOGGER.debug("TEMP Phase & Views repost failed: %s", ex)
        pending["attempts"] = int(pending.get("attempts", 0)) + 1
        pending["next_try_at"] = now + PENDING_POST_RETRY_SEC
        return True

    state["pending_command"] = None
    return True


def _lookup_command_id(command_name):
    UI = _get_ui()
    if UI is None:
        return None
    try:
        return UI.RevitCommandId.LookupCommandId(command_name)
    except Exception:
        return None


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

    if "SYNC" in normalized or "SYNCHRONIZE" in normalized:
        return "sync"
    if "SAVE" in normalized and "CENTRAL" in normalized:
        return "sync"

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


def _show_phase_picker(doc, view, current_phase_id):
    phases = _collect_document_phases(doc)
    if not phases:
        _show_alert(
            "TEMP Phase & Views",
            "No phases available in this document.",
        )
        return None

    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        import System.Windows.Forms as WinForms
    except Exception:
        _show_alert(
            "TEMP Phase & Views",
            "Could not load Windows Forms UI. Phase selection was cancelled.",
        )
        return None

    form = WinForms.Form()
    form.Text = "TEMP Phase & Views"
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    form.Width = 430
    form.Height = 205
    form.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    form.MaximizeBox = False
    form.MinimizeBox = False

    info = WinForms.Label()
    info.Left = 14
    info.Top = 14
    info.Width = 390
    info.Height = 40
    info.Text = "Select temporary Phase for view:\n{}".format(_safe_text(getattr(view, "Name", "")))
    form.Controls.Add(info)

    combo = WinForms.ComboBox()
    combo.Left = 14
    combo.Top = 66
    combo.Width = 390
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
    ok_btn.Left = 230
    ok_btn.Top = 112
    ok_btn.Width = 84
    form.Controls.Add(ok_btn)

    cancel_btn = WinForms.Button()
    cancel_btn.Text = "Cancel"
    cancel_btn.DialogResult = WinForms.DialogResult.Cancel
    cancel_btn.Left = 320
    cancel_btn.Top = 112
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
        if bool(param.IsReadOnly):
            return None
    except Exception:
        pass
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
        if bool(param.IsReadOnly):
            return False
    except Exception:
        pass

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


def _show_control_dialog(state, doc):
    UI = _get_ui()
    if UI is None:
        return "close"

    doc_key = _doc_key(doc)
    tracked = 0
    for session in (state.get("view_sessions") or {}).values():
        if not isinstance(session, dict):
            continue
        if _safe_text(session.get("doc_key")) == doc_key:
            tracked += 1

    enabled = bool(state.get("enabled", True))
    status = "ON" if enabled else "OFF"

    td = UI.TaskDialog("TEMP Phase & Views")
    td.MainInstruction = "TEMP Phase & Views"
    td.MainContent = (
        "Automation is currently {}.\n"
        "Tracked views in this document: {}.\n\n"
        "Choose an action."
    ).format(status, tracked)
    td.AddCommandLink(
        UI.TaskDialogCommandLinkId.CommandLink1,
        "Toggle automation ({})".format("turn OFF" if enabled else "turn ON"),
    )
    td.AddCommandLink(
        UI.TaskDialogCommandLinkId.CommandLink2,
        "Apply temporary phase to active view",
    )
    td.AddCommandLink(
        UI.TaskDialogCommandLinkId.CommandLink3,
        "Restore tracked views in active document",
    )
    td.CommonButtons = UI.TaskDialogCommonButtons.Close
    td.DefaultButton = UI.TaskDialogResult.Close
    result = td.Show()

    if result == UI.TaskDialogResult.CommandLink1:
        return "toggle"
    if result == UI.TaskDialogResult.CommandLink2:
        return "apply"
    if result == UI.TaskDialogResult.CommandLink3:
        return "restore"
    return "close"


def _show_alert(title, message):
    UI = _get_ui()
    if UI is not None:
        try:
            UI.TaskDialog.Show(title, message)
            return
        except Exception:
            pass
    print("[TEMP Phase & Views] {}: {}".format(title, message))


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


def _get_active_view(uiapp):
    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    return getattr(uidoc, "ActiveView", None) if uidoc else None


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
    state.setdefault("enabled", True)
    state.setdefault("last_idling_ts", 0.0)
    state.setdefault("last_seen_tvp", {})
    state.setdefault("view_sessions", {})
    state.setdefault("pending_command", None)
    state.setdefault("repost_guard", None)
    state.setdefault("debug_last_commands", [])
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
