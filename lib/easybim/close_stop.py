# -*- coding: utf-8 -*-
"""Shared Close Stop runtime for the Close Stop pushbutton and diagnostics."""

from __future__ import print_function

import time

from pyrevit import script
from easybim import temp_phase_view


LOGGER = script.get_logger()

STATE_ENVVAR = "EASYBIM_CLOSE_STOP_STATE"
ALLOW_COMMAND_SEC = 10.0
PROBE_SUPPRESS_SEC = 5.0
MAX_DEBUG_COMMANDS = 12
TITLE_DIALOG = "Close Stop"
TITLE_DEBUG = "Close Stop Debug"
MAX_MAIN_CONTENT_VIEWS = 8

CLOSE_RESTORE_LINK_TEXT = "Restore all Temporary View Properties before closing"
SAVE_RESTORE_LINK_TEXT = "Restore all Temporary View Properties and continue saving"
SYNC_RESTORE_LINK_TEXT = "Restore all Temporary View Properties and continue synchronizing"
CLOSE_FILE_LINK_TEXT = "Close File"

SAVE_COMMAND_NAMES = (
    "ID_REVIT_FILE_SAVE",
    "ID_FILE_SAVE",
)
SYNC_COMMAND_NAMES = (
    "ID_FILE_SAVE_TO_CENTRAL",
    "ID_FILE_SAVE_TO_CENTRAL_SHORTCUT",
    "ID_FILE_SAVE_TO_MASTER",
    "ID_FILE_SAVE_TO_MASTER_SHORTCUT",
)
CLOSE_COMMAND_NAMES = (
    "ID_REVIT_FILE_CLOSE",
    "ID_FILE_CLOSE",
    "ID_REVIT_PROJECT_CLOSE",
    "ID_PROJECT_CLOSE",
)

SAVE_FALLBACK_COMMAND_NAMES = SAVE_COMMAND_NAMES
SYNC_FALLBACK_COMMAND_NAMES = SYNC_COMMAND_NAMES
CLOSE_FALLBACK_COMMAND_NAMES = CLOSE_COMMAND_NAMES


def run_pushbutton(uiapp=None):
    """Run the close-related Close Stop flow manually from the ribbon button."""
    uiapp = _get_uiapp(uiapp)
    doc = _get_active_project_doc(uiapp)
    if not _is_doc_supported(doc):
        _show_alert(TITLE_DIALOG, "Open a project document to use Close Stop.")
        return

    state = _get_state()
    state_changed = _clear_expired_allow_command_once(state)
    if _clear_expired_probe_suppress_intercept(state):
        state_changed = True
    if state_changed:
        _save_state(state)

    mode = _show_button_mode_dialog()
    if mode == "probe":
        arm_save_probe(uiapp=uiapp)
        return
    if mode != "close":
        return

    _save_state(state)
    _run_close_sequence(uiapp=uiapp, doc=doc, state=state)


def capture_action_command(uiapp=None, event_args=None):
    """Capture save, sync, or close command names for later repost/diagnostics."""
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
    _record_action_command(state, target_doc, command_name)
    _save_state(state)


def capture_close_command(uiapp=None, event_args=None):
    """Compatibility wrapper for old close hook imports."""
    return capture_action_command(uiapp=uiapp, event_args=event_args)


def arm_save_probe(uiapp=None):
    """Arm a one-shot generic command probe for the next Revit command."""
    uiapp = _get_uiapp(uiapp)
    doc = _get_active_project_doc(uiapp)
    if not _is_doc_supported(doc):
        _show_alert(TITLE_DEBUG, "Open a project document before testing the save hook.")
        return

    state = _get_state()
    _arm_save_probe_state(state)
    _save_state(state)
    _show_probe_armed_dialog()


def handle_generic_command_before_exec(uiapp=None, event_args=None):
    """Probe the next command-before-exec event when armed by the Close Stop button."""
    if event_args is None:
        return

    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return

    state = _get_state()
    if not _is_save_probe_armed(state):
        return

    command_name = _safe_text(_getattr_safe(event_args, "CommandId.Name")).strip()
    if not command_name:
        return

    doc = _resolve_doc_from_event_args(event_args, uiapp)
    if not _is_doc_supported(doc):
        doc = _get_active_project_doc(uiapp)
    normalized_command = _normalize_command_name(command_name)

    state["last_probe_result"] = {
        "captured_at": time.time(),
        "command_name": normalized_command or command_name,
        "action_kind": _classify_action_command(command_name) or "unknown",
        "doc_key": _doc_key(doc),
        "doc_runtime_id": _get_doc_runtime_id(doc),
        "doc_title": _safe_text(getattr(doc, "Title", "")).strip() if doc else "",
        "cancellable": bool(_is_event_cancellable(event_args)),
        "matched_targeted_save_hook": bool(_targeted_save_hook_match(command_name)),
        "captured_by": "generic-before-exec",
    }
    _register_probe_suppress_intercept(state, doc, normalized_command or command_name)
    _clear_save_probe_state(state)
    _save_state(state)
    _show_probe_result_dialog(state.get("last_probe_result"))


def handle_save_related_before_exec(uiapp=None, event_args=None):
    """Compatibility no-op after save automation moved to doc-saving hooks."""
    del uiapp, event_args
    return None


def handle_command_before_exec(uiapp=None, event_args=None):
    """Compatibility no-op for legacy close hook wiring."""
    del uiapp, event_args
    return None


def handle_doc_closing(uiapp=None, event_args=None):
    """Compatibility no-op for legacy close hook wiring."""
    del uiapp, event_args
    return None


def handle_app_idling(uiapp=None, event_args=None):
    """Maintain legacy Close Stop state without owning automatic save behavior."""
    del uiapp, event_args

    state = _get_state()
    state_changed = _clear_expired_allow_command_once(state)
    if _clear_expired_probe_suppress_intercept(state):
        state_changed = True
    if isinstance(state.get("pending_action"), dict):
        state["pending_action"] = None
        state_changed = True

    if state_changed:
        _save_state(state)


def _process_pending_action(state, uiapp):
    pending = state.get("pending_action")
    if not isinstance(pending, dict):
        return False

    doc_key = _safe_text(pending.get("doc_key")).strip()
    doc_runtime_id = _to_int(pending.get("doc_runtime_id"))
    preferred_command_name = _safe_text(pending.get("command_name")).strip()
    action_kind = _safe_text(pending.get("action_kind")).strip().lower()

    doc = _find_doc_by_identity(uiapp, doc_key=doc_key, doc_runtime_id=doc_runtime_id)
    state["pending_action"] = None

    if not _is_doc_supported(doc):
        return True

    summary = _get_tvp_summary(uiapp, doc)
    if bool(summary.get("has_restore_work")):
        if not _show_save_related_restore_dialog(action_kind, summary):
            return True

        restore_result = temp_phase_view.restore_tvp_summary(
            uiapp=uiapp,
            doc=doc,
            summary=summary,
        )
        if not bool(restore_result.get("ok")):
            _show_alert(
                TITLE_DIALOG,
                _safe_text(restore_result.get("message")).strip()
                or "EasyBIM could not restore Temporary View Properties before {}.".format(
                    _action_label(action_kind)
                ),
            )
            return True

    command_name, command_id = _resolve_revit_command(
        uiapp=uiapp,
        doc=doc,
        preferred_command_name=preferred_command_name,
        action_kind=action_kind,
        state=state,
    )
    if command_id is None:
        _show_alert(
            TITLE_DIALOG,
            "EasyBIM could not determine a {} command.\n\nRecent commands:\n{}".format(
                _action_label(action_kind),
                _debug_command_lines(state),
            ),
        )
        return True

    _register_allow_command_once(
        state=state,
        doc=doc,
        command_name=command_name,
    )
    _save_state(state)

    try:
        uiapp.PostCommand(command_id)
        return True
    except Exception as ex:
        state["allow_command_once"] = None
        _save_state(state)
        _show_alert(
            TITLE_DIALOG,
            "EasyBIM could not repost the {} command.\n\n{}".format(
                _action_label(action_kind),
                ex,
            ),
        )
        return True


def _run_close_sequence(uiapp, doc, state):
    if uiapp is None or not _is_doc_supported(doc):
        return False

    summary = _get_tvp_summary(uiapp, doc)
    needs_restore = bool(summary.get("has_restore_work"))
    restore_result = {
        "ok": True,
        "restored_phase_views": 0,
        "disabled_untracked_tvp_views": 0,
        "failed_views": [],
        "message": "",
    }

    if needs_restore:
        if not _show_close_restore_dialog(summary):
            return False

        restore_result = temp_phase_view.restore_tvp_summary(
            uiapp=uiapp,
            doc=doc,
            summary=summary,
        )
        if not bool(restore_result.get("ok")):
            _show_alert(
                TITLE_DIALOG,
                _safe_text(restore_result.get("message")).strip()
                or "EasyBIM could not restore Temporary View Properties before close.",
            )
            return False

    close_instruction, close_message = _build_close_prompt_message(
        needs_restore,
        restore_result,
    )
    if not _show_close_file_prompt(close_instruction, close_message):
        return False

    command_name, command_id = _resolve_revit_command(
        uiapp=uiapp,
        doc=doc,
        preferred_command_name=_get_preferred_command_name(
            state,
            _doc_key(doc),
            _get_doc_runtime_id(doc),
            action_kind="close",
        ),
        action_kind="close",
        state=state,
    )
    if command_id is None:
        _show_alert(
            TITLE_DIALOG,
            "EasyBIM could not determine a close command.\n\nRecent commands:\n{}".format(
                _debug_command_lines(state)
            ),
        )
        return False

    _register_allow_command_once(
        state=state,
        doc=doc,
        command_name=command_name,
    )
    _save_state(state)

    try:
        uiapp.PostCommand(command_id)
        return True
    except Exception as ex:
        state["allow_command_once"] = None
        _save_state(state)
        _show_alert(
            TITLE_DIALOG,
            "EasyBIM could not repost the close command.\n\n{}".format(ex),
        )
        return False


def _show_button_mode_dialog():
    UI = _get_ui()
    if UI is None:
        return "close"

    try:
        dialog = UI.TaskDialog(TITLE_DIALOG)
        if hasattr(dialog, "TitleAutoPrefix"):
            dialog.TitleAutoPrefix = False
        if hasattr(dialog, "AllowCancellation"):
            dialog.AllowCancellation = True

        dialog.MainInstruction = "Choose a Close Stop action."
        dialog.MainContent = (
            "Run the normal close workflow, or arm a diagnostic probe for the next Save-related command."
        )
        dialog.CommonButtons = UI.TaskDialogCommonButtons.Cancel
        dialog.AddCommandLink(
            UI.TaskDialogCommandLinkId.CommandLink1,
            "Run Close Stop",
        )
        dialog.AddCommandLink(
            UI.TaskDialogCommandLinkId.CommandLink2,
            "Test Save Hook",
        )
        result = dialog.Show()
        if result == UI.TaskDialogResult.CommandLink1:
            return "close"
        if result == UI.TaskDialogResult.CommandLink2:
            return "probe"
        return "cancel"
    except Exception as ex:
        LOGGER.warning("Close Stop mode dialog failed: %s", ex)
        return "close"


def _show_probe_armed_dialog():
    _show_alert(
        TITLE_DEBUG,
        (
            "Save hook probe is armed.\n\n"
            "Click Save now.\n"
            "EasyBIM will report the next command-before-exec event it sees.\n"
            "This test does not block or cancel the next command."
        ),
    )


def _show_probe_result_dialog(result):
    if not isinstance(result, dict):
        _show_alert(TITLE_DEBUG, "No save hook probe result is available.")
        return

    command_name = _safe_text(result.get("command_name")).strip() or "(blank)"
    action_kind = _safe_text(result.get("action_kind")).strip() or "unknown"
    doc_title = _safe_text(result.get("doc_title")).strip() or "(no supported document)"
    captured_by = _safe_text(result.get("captured_by")).strip() or "unknown"
    matched_targeted = bool(result.get("matched_targeted_save_hook"))
    match_text = "Yes" if matched_targeted else "No"
    explanation = (
        "This command matches the current targeted save/sync hook files."
        if matched_targeted
        else "This explains why the current save proof dialog did not appear."
    )

    _show_alert(
        TITLE_DEBUG,
        (
            "Save hook probe captured a command.\n\n"
            "Hook source: {captured_by}\n"
            "Command: {command}\n"
            "Classified as: {action}\n"
            "Document: {document}\n"
            "Cancellable: {cancellable}\n"
            "Matches current targeted save/sync hook files: {matched}\n\n"
            "{explanation}"
        ).format(
            captured_by=captured_by,
            command=command_name,
            action=action_kind,
            document=doc_title,
            cancellable=bool(result.get("cancellable")),
            matched=match_text,
            explanation=explanation,
        ),
    )


def _resolve_revit_command(uiapp, doc, preferred_command_name, action_kind, state):
    doc_key = _doc_key(doc)
    doc_runtime_id = _get_doc_runtime_id(doc)

    candidates = []
    for name in (
        preferred_command_name,
        _get_preferred_command_name(state, doc_key, doc_runtime_id, action_kind=action_kind),
    ) + _fallback_command_names(action_kind):
        normalized = _normalize_command_name(name)
        if not normalized or normalized in candidates:
            continue
        if action_kind and _classify_action_command(normalized) != action_kind:
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


def _fallback_command_names(action_kind):
    if action_kind == "save":
        return SAVE_FALLBACK_COMMAND_NAMES
    if action_kind == "sync":
        return SYNC_FALLBACK_COMMAND_NAMES
    if action_kind == "close":
        return CLOSE_FALLBACK_COMMAND_NAMES
    return ()


def _show_save_related_restore_dialog(action_kind, summary):
    lines = list(summary.get("dialog_view_lines") or [])
    main_content = "\n".join(lines[:MAX_MAIN_CONTENT_VIEWS])
    if not main_content:
        main_content = "Temporary View Properties cleanup is still required in this file."

    expanded_content = ""
    if len(lines) > MAX_MAIN_CONTENT_VIEWS:
        expanded_content = "\n".join(lines)

    details = (
        "EasyBIM tracked temp-phase views will be restored to their original phase.\n"
        "Other discoverable TVP-enabled views will only have TVP turned off.\n"
        "{} will continue automatically after restore.".format(
            _action_prompt_label(action_kind)
        )
    )
    if expanded_content:
        details = "{}\n\nAll affected views:\n{}".format(details, expanded_content)

    return _show_task_dialog_with_command_link(
        title=TITLE_DIALOG,
        main_instruction="Temporary View Properties must be restored before {}.".format(
            _action_ing_label(action_kind)
        ),
        main_content=main_content,
        command_text=_action_restore_link_text(action_kind),
        expanded_content=details,
    )


def _show_close_restore_dialog(summary):
    lines = list(summary.get("dialog_view_lines") or [])
    main_content = "\n".join(lines[:MAX_MAIN_CONTENT_VIEWS])
    if not main_content:
        main_content = "Temporary View Properties cleanup is still required in this file."

    expanded_content = ""
    if len(lines) > MAX_MAIN_CONTENT_VIEWS:
        expanded_content = "\n".join(lines)

    details = (
        "EasyBIM tracked temp-phase views will be restored to their original phase.\n"
        "Other discoverable TVP-enabled views will only have TVP turned off.\n"
        "The file will remain open until you click Close File."
    )
    if expanded_content:
        details = "{}\n\nAll affected views:\n{}".format(details, expanded_content)

    return _show_task_dialog_with_command_link(
        title=TITLE_DIALOG,
        main_instruction="Temporary View Properties must be restored before closing.",
        main_content=main_content,
        command_text=CLOSE_RESTORE_LINK_TEXT,
        expanded_content=details,
    )


def _build_close_prompt_message(needs_restore, restore_result):
    restored_phase_views = int(restore_result.get("restored_phase_views", 0) or 0)
    disabled_untracked = int(restore_result.get("disabled_untracked_tvp_views", 0) or 0)

    if needs_restore:
        instruction = "Temporary View Properties were restored."
        message = (
            "Original phases were restored in {} view(s).\n"
            "Temporary View Properties were turned off in {} additional view(s).\n\n"
            "The file is still open.\n"
            "Click Close File to continue with Revit's normal close process."
        ).format(restored_phase_views, disabled_untracked)
        return instruction, message

    return (
        "No Temporary View Properties cleanup is needed.",
        "Click Close File to continue with Revit's normal close process.",
    )


def _show_close_file_prompt(main_instruction, main_content):
    return _show_task_dialog_with_command_link(
        title=TITLE_DIALOG,
        main_instruction=main_instruction,
        main_content=main_content,
        command_text=CLOSE_FILE_LINK_TEXT,
    )


def _show_task_dialog_with_command_link(
    title,
    main_instruction,
    main_content,
    command_text,
    expanded_content="",
):
    UI = _get_ui()
    if UI is None:
        _show_alert(title, "{}\n\n{}".format(main_instruction, main_content).strip())
        return False

    try:
        dialog = UI.TaskDialog(title)
        if hasattr(dialog, "TitleAutoPrefix"):
            dialog.TitleAutoPrefix = False
        if hasattr(dialog, "AllowCancellation"):
            dialog.AllowCancellation = True

        dialog.MainInstruction = _safe_text(main_instruction)
        dialog.MainContent = _safe_text(main_content)
        if expanded_content:
            dialog.ExpandedContent = _safe_text(expanded_content)
        dialog.CommonButtons = UI.TaskDialogCommonButtons.Cancel
        dialog.AddCommandLink(
            UI.TaskDialogCommandLinkId.CommandLink1,
            _safe_text(command_text),
        )
        result = dialog.Show()
        return result == UI.TaskDialogResult.CommandLink1
    except Exception as ex:
        LOGGER.warning("Close Stop TaskDialog failed: %s", ex)
        _show_alert(title, "{}\n\n{}".format(main_instruction, main_content).strip())
        return False


def _action_restore_link_text(action_kind):
    if action_kind == "save":
        return SAVE_RESTORE_LINK_TEXT
    if action_kind == "sync":
        return SYNC_RESTORE_LINK_TEXT
    return CLOSE_RESTORE_LINK_TEXT


def _action_ing_label(action_kind):
    if action_kind == "save":
        return "saving"
    if action_kind == "sync":
        return "synchronizing"
    return "closing"


def _action_label(action_kind):
    if action_kind == "save":
        return "save"
    if action_kind == "sync":
        return "synchronize"
    return "close"


def _action_prompt_label(action_kind):
    if action_kind == "save":
        return "Saving"
    if action_kind == "sync":
        return "Synchronizing"
    return "Closing"


def _get_tvp_summary(uiapp, doc):
    try:
        summary = temp_phase_view.collect_tvp_summary(
            uiapp=uiapp,
            doc=doc,
            include_all_discoverable=True,
        )
    except Exception as ex:
        LOGGER.warning("Close Stop could not collect TVP summary: %s", ex)
        summary = None

    if isinstance(summary, dict):
        return summary

    return {
        "doc_key": _doc_key(doc),
        "doc_runtime_id": _get_doc_runtime_id(doc),
        "doc_title": _safe_text(getattr(doc, "Title", "")).strip(),
        "discoverable_tvp_views": [],
        "tracked_restore_views": [],
        "untracked_tvp_views": [],
        "dialog_view_lines": [],
        "has_blocking_views": False,
        "has_restore_work": False,
    }


def _record_action_command(state, doc, command_name):
    _remember_debug_command(state, command_name)
    state["last_action_command"] = {
        "doc_key": _doc_key(doc),
        "doc_runtime_id": _get_doc_runtime_id(doc),
        "command_name": _normalize_command_name(command_name),
        "captured_at": time.time(),
        "action_kind": _classify_action_command(command_name),
    }


def _register_allow_command_once(state, doc, command_name):
    state["allow_command_once"] = {
        "doc_key": _doc_key(doc),
        "doc_runtime_id": _get_doc_runtime_id(doc),
        "command_name": _normalize_command_name(command_name),
        "expires_at": time.time() + ALLOW_COMMAND_SEC,
    }


def _arm_save_probe_state(state):
    state["save_probe"] = {
        "armed": True,
        "armed_at": time.time(),
        "source": "close-stop-button",
        "mode": "next-command",
    }
    state["last_probe_result"] = None
    state["probe_suppress_intercept"] = None


def _clear_save_probe_state(state):
    state["save_probe"] = None


def _is_save_probe_armed(state):
    probe = state.get("save_probe")
    return isinstance(probe, dict) and bool(probe.get("armed"))


def _register_probe_suppress_intercept(state, doc, command_name):
    normalized_command = _normalize_command_name(command_name)
    if not normalized_command:
        state["probe_suppress_intercept"] = None
        return

    state["probe_suppress_intercept"] = {
        "doc_key": _doc_key(doc),
        "doc_runtime_id": _get_doc_runtime_id(doc),
        "command_name": normalized_command,
        "expires_at": time.time() + PROBE_SUPPRESS_SEC,
    }


def _should_skip_save_intercept_for_probe(state, doc, command_name):
    if _is_save_probe_armed(state):
        return True, "probe-armed"

    guard = state.get("probe_suppress_intercept")
    if not isinstance(guard, dict):
        return False, ""

    if time.time() > float(guard.get("expires_at", 0.0) or 0.0):
        state["probe_suppress_intercept"] = None
        return False, ""

    if not _identity_matches(
        stored_doc_key=guard.get("doc_key"),
        stored_doc_runtime_id=guard.get("doc_runtime_id"),
        doc_key=_doc_key(doc),
        doc_runtime_id=_get_doc_runtime_id(doc),
    ):
        return False, ""

    normalized_command = _normalize_command_name(command_name)
    expected_command = _normalize_command_name(guard.get("command_name"))
    if normalized_command and expected_command and normalized_command != expected_command:
        return False, ""

    state["probe_suppress_intercept"] = None
    return True, "probe-suppress-intercept"


def _consume_allow_command_once(state, doc_key, doc_runtime_id, command_name=""):
    guard = state.get("allow_command_once")
    if not isinstance(guard, dict):
        return False

    expires_at = float(guard.get("expires_at", 0.0) or 0.0)
    if time.time() > expires_at:
        state["allow_command_once"] = None
        return False

    if not _identity_matches(
        stored_doc_key=guard.get("doc_key"),
        stored_doc_runtime_id=guard.get("doc_runtime_id"),
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    ):
        return False

    expected_command_name = _normalize_command_name(guard.get("command_name"))
    command_name = _normalize_command_name(command_name)
    if command_name and expected_command_name and command_name != expected_command_name:
        return False

    state["allow_command_once"] = None
    return True


def _clear_expired_allow_command_once(state):
    guard = state.get("allow_command_once")
    if not isinstance(guard, dict):
        return False

    expires_at = float(guard.get("expires_at", 0.0) or 0.0)
    if time.time() <= expires_at:
        return False

    state["allow_command_once"] = None
    return True


def _clear_expired_probe_suppress_intercept(state):
    guard = state.get("probe_suppress_intercept")
    if not isinstance(guard, dict):
        return False

    expires_at = float(guard.get("expires_at", 0.0) or 0.0)
    if time.time() <= expires_at:
        return False

    state["probe_suppress_intercept"] = None
    return True


def _queue_pending_action(state, doc, command_name, action_kind, source):
    state["pending_action"] = {
        "doc_key": _doc_key(doc),
        "doc_runtime_id": _get_doc_runtime_id(doc),
        "doc_title": _safe_text(getattr(doc, "Title", "")).strip(),
        "command_name": _normalize_command_name(command_name),
        "action_kind": _safe_text(action_kind).strip().lower(),
        "queued_at": time.time(),
        "source": _safe_text(source).strip(),
    }


def _get_preferred_command_name(state, doc_key, doc_runtime_id, action_kind=None):
    record = state.get("last_action_command")
    if not isinstance(record, dict):
        return ""

    if not _identity_matches(
        stored_doc_key=record.get("doc_key"),
        stored_doc_runtime_id=record.get("doc_runtime_id"),
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    ):
        return ""

    recorded_kind = _safe_text(record.get("action_kind")).strip().lower()
    if action_kind and recorded_kind and recorded_kind != action_kind:
        return ""

    command_name = _normalize_command_name(record.get("command_name"))
    if action_kind and _classify_action_command(command_name) != action_kind:
        return ""
    return command_name


def _remember_debug_command(state, command_name):
    recent = list(state.get("debug_seen_commands") or [])
    recent.append(_normalize_command_name(command_name))
    if len(recent) > MAX_DEBUG_COMMANDS:
        recent = recent[-MAX_DEBUG_COMMANDS:]
    state["debug_seen_commands"] = recent


def _debug_command_lines(state):
    recent = list(state.get("debug_seen_commands") or [])
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


def _classify_action_command(command_name):
    normalized = _normalize_command_name(command_name)
    if not normalized:
        return None
    if normalized in SAVE_COMMAND_NAMES:
        return "save"
    if normalized in SYNC_COMMAND_NAMES:
        return "sync"
    if normalized in CLOSE_COMMAND_NAMES:
        return "close"
    if "SAVE_TO_CENTRAL" in normalized or "SAVE_TO_MASTER" in normalized:
        return "sync"
    if normalized.endswith("FILE_SAVE"):
        return "save"
    if "CLOSE" in normalized and ("FILE" in normalized or "PROJECT" in normalized):
        return "close"
    return None


def _targeted_save_hook_match(command_name):
    normalized = _normalize_command_name(command_name)
    return normalized in SAVE_COMMAND_NAMES or normalized in SYNC_COMMAND_NAMES


def _lookup_command_id(command_name):
    UI = _get_ui()
    if UI is None:
        return None
    try:
        return UI.RevitCommandId.LookupCommandId(_normalize_command_name(command_name))
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
            "Close Stop could not cancel command event. args_type=%s",
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

    state.setdefault("pending_action", None)
    state.setdefault("last_action_command", None)
    state.setdefault("allow_command_once", None)
    state.setdefault("save_probe", None)
    state.setdefault("last_probe_result", None)
    state.setdefault("probe_suppress_intercept", None)

    legacy_debug = state.get("debug_seen_close_commands")
    if not isinstance(state.get("debug_seen_commands"), list):
        state["debug_seen_commands"] = list(legacy_debug) if isinstance(legacy_debug, list) else []

    state.setdefault("debug_last_intercept", None)

    if not isinstance(state.get("debug_seen_commands"), list):
        state["debug_seen_commands"] = []

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


def _normalize_command_name(name):
    text = _safe_text(name).strip().upper()
    if not text:
        return ""
    return text.replace("-", "_").replace(" ", "_")


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
