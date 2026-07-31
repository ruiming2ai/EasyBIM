# -*- coding: utf-8 -*-
"""Stop Revit saves until Temp Phase state has been restored.

Revit's DocumentSaving and DocumentSavingAs events are the authoritative
pre-save gates.  They are cancellable, but Revit does not allow Save/SaveAs
to be called from inside those events.  This module therefore cancels the
first save, queues the document, restores it during Idling, and posts the
same save command once the document is clean.
"""

from __future__ import print_function

import sys
import time

try:
    from easybim import temp_phase_close
except Exception:
    temp_phase_close = None


TITLE = "Temp Phase"
STATE_ENVVAR = "EASYBIM_TEMP_PHASE_SAVE_STATE"
MAX_RESTORE_ATTEMPTS = 8
RETRY_DELAY_SEC = 0.25
IDLE_THROTTLE_SEC = 0.10
SAVE_GUARD_SEC = 12.0

_MEMORY_STATE = {}

# Keep delegate references outside the startup script module.  pyRevit can
# re-execute startup.py on reload while Revit still owns the old delegates.
_RUNTIME = sys.modules.setdefault("easybim._temp_phase_save_runtime", {})


def install():
    """Install typed Revit save handlers once per Revit process."""
    try:
        from pyrevit import DB, HOST_APP, framework
    except Exception as ex:
        _log("TempPhaseSaveInstallFailed {0}".format(_exception_text(ex)))
        return False

    app = getattr(HOST_APP, "app", None)
    if app is None:
        _log("TempPhaseSaveInstallFailed missingApplication")
        return False

    existing = _RUNTIME.get("registration")
    if isinstance(existing, dict):
        _uninstall_registration(existing)

    try:
        handlers = {
            "DocumentSaving": framework.EventHandler[DB.Events.DocumentSavingEventArgs](
                document_saving_handler
            ),
            "DocumentSavingAs": framework.EventHandler[
                DB.Events.DocumentSavingAsEventArgs
            ](document_saving_as_handler),
            "DocumentSaved": framework.EventHandler[DB.Events.DocumentSavedEventArgs](
                document_saved_handler
            ),
            "DocumentSavedAs": framework.EventHandler[
                DB.Events.DocumentSavedAsEventArgs
            ](document_saved_as_handler),
        }
        for event_name, handler in handlers.items():
            _attach(app, event_name, handler)
    except Exception as ex:
        if "handlers" in locals():
            for event_name, handler in list(handlers.items()):
                _detach(app, event_name, handler)
        _log("TempPhaseSaveInstallFailed {0}".format(_exception_text(ex)))
        return False

    _RUNTIME["registration"] = {"app": app, "handlers": handlers}
    _log("TempPhaseSaveHandlersInstalled events=DocumentSaving,DocumentSavingAs,DocumentSaved,DocumentSavedAs")
    return True


def uninstall():
    registration = _RUNTIME.get("registration")
    if isinstance(registration, dict):
        _uninstall_registration(registration)
        _RUNTIME.pop("registration", None)
        _log("TempPhaseSaveHandlersUninstalled")
    return True


def document_saving_handler(sender, args):
    return handle_document_saving(sender, args, action="save")


def document_saving_as_handler(sender, args):
    return handle_document_saving(sender, args, action="save_as")


def document_saved_handler(sender, args):
    return handle_document_saved(sender, args, action="save")


def document_saved_as_handler(sender, args):
    return handle_document_saved(sender, args, action="save_as")


def handle_document_saving(sender, event_args, action="save"):
    """Cancel a save that would persist temporary state."""
    doc = _resolve_document(sender, event_args)
    token, doc_key, runtime_id = _identity(doc, event_args)
    _log(
        "DocumentSavingEntry action={0} token={1} cancellable={2}".format(
            action,
            token,
            _is_cancellable(event_args),
        )
    )
    if event_args is None or doc is None or not _is_supported_document(doc):
        _log("DocumentSavingAllowedUnsupportedOrMissing token={0}".format(token))
        return False

    state = _get_state()
    if _consume_save_guard(state, token, action):
        _save_state(state)
        _log("DocumentSavingAllowedRepost action={0} token={1}".format(action, token))
        return False

    if _is_shutdown(sender):
        _save_state(state)
        _log("DocumentSavingAllowedShutdown token={0}".format(token))
        return False

    summary = _collect_summary(doc)
    has_work = bool(summary.get("has_restore_work"))
    _log(
        "DocumentSavingInspect action={0} token={1} hasWork={2} cancellable={3}".format(
            action,
            token,
            has_work,
            _is_cancellable(event_args),
        )
    )
    if not has_work or not _is_cancellable(event_args):
        _save_state(state)
        return False

    if not _try_cancel(event_args):
        _save_state(state)
        _log("DocumentSavingCancelFailed action={0} token={1}".format(action, token))
        return False

    _queue_save(state, token, doc_key, runtime_id, action)
    _save_state(state)
    _log("DocumentSavingCancelledQueued action={0} token={1}".format(action, token))
    return True


def handle_document_saved(sender, event_args, action="save"):
    """Record final save status and clear one-shot state."""
    del sender
    doc = _resolve_document(None, event_args)
    token, _doc_key, _runtime_id = _identity(doc, event_args)
    status = _event_status(event_args)
    state = _get_state()
    # Revit raises DocumentSaved even when the preceding DocumentSaving event
    # was cancelled.  Keep the queued request alive so Idling can restore and
    # repost it; only the reposted save has already removed its pending record.
    if token in (state.get("pending_saves") or {}):
        _save_state(state)
        _log(
            "DocumentSavedPreservedPending action={0} token={1} status={2}".format(
                action,
                token,
                status or "UNKNOWN",
            )
        )
        return False
    state.get("pending_saves", {}).pop(token, None)
    state.get("save_guards", {}).pop(token, None)
    _save_state(state)
    _log(
        "DocumentSavedCompleted action={0} token={1} status={2}".format(
            action,
            token,
            status or "UNKNOWN",
        )
    )
    return True


def handle_doc_closed(uiapp=None, event_args=None):
    """Clear save state after a document really closes."""
    del uiapp
    doc = _resolve_document(None, event_args)
    token, _doc_key, _runtime_id = _identity(doc, event_args)
    status = _event_status(event_args)
    if status in ("CANCELLED", "FAILED"):
        _log("TempPhaseSaveDocClosedNonSuccess status={0} token={1}".format(status, token))
        return False
    state = _get_state()
    state.get("pending_saves", {}).pop(token, None)
    state.get("save_guards", {}).pop(token, None)
    _save_state(state)
    _log("TempPhaseSaveDocClosedCleared status={0} token={1}".format(status or "UNKNOWN", token))
    return True


def handle_app_idling(uiapp=None, event_args=None):
    """Restore queued saves and repost Save/Save As from a safe API context."""
    del event_args
    if uiapp is None:
        return False
    state = _get_state()
    pending = state.get("pending_saves")
    if not isinstance(pending, dict) or not pending:
        _expire_guards(state)
        _save_state(state)
        return False
    if _is_shutdown(uiapp):
        state["pending_saves"] = {}
        state["save_guards"] = {}
        _save_state(state)
        _log("TempPhaseSaveIdlingSkippedShutdown")
        return False

    now = time.time()
    last_idle = float(state.get("last_idling_at", 0.0) or 0.0)
    if now - last_idle < IDLE_THROTTLE_SEC:
        return False
    state["last_idling_at"] = now
    changed = False

    for token, record in list(pending.items()):
        if not isinstance(record, dict):
            pending.pop(token, None)
            changed = True
            continue
        if now < float(record.get("next_try_at", 0.0) or 0.0):
            continue

        doc = _find_document(uiapp, record.get("doc_key"), record.get("doc_runtime_id"))
        if doc is None or not _is_supported_document(doc):
            if _retry(record, now):
                changed = True
                continue
            pending.pop(token, None)
            changed = True
            _show_alert(TITLE, "The document is no longer available for the clean save.")
            _log("TempPhaseSaveDocumentUnavailable token={0}".format(token))
            continue

        active = _active_document(uiapp)
        if active is None or not _same_document(active, doc):
            if _retry(record, now):
                changed = True
                _log("TempPhaseSaveWaitingForActiveDocument token={0}".format(token))
                continue
            pending.pop(token, None)
            changed = True
            _show_alert(TITLE, "Activate the document and save again; Temp Phase was not saved.")
            _log("TempPhaseSaveInactiveDocumentGivingUp token={0}".format(token))
            continue

        summary = _collect_summary(doc)
        if summary.get("has_restore_work"):
            result = _restore_summary(uiapp, doc, summary)
            if not result.get("ok"):
                if _retry(record, now):
                    changed = True
                    _log("TempPhaseSaveRestoreRetry token={0} attempt={1}".format(token, record.get("attempts")))
                    continue
                pending.pop(token, None)
                changed = True
                _show_alert(TITLE, "Temporary phase/view state could not be restored. The save was cancelled.")
                _log("TempPhaseSaveRestoreFailedGivingUp token={0}".format(token))
                continue
            _log(
                "TempPhaseSaveRestored token={0} phaseViews={1} untrackedViews={2}".format(
                    token,
                    result.get("restored_phase_views", 0),
                    result.get("disabled_untracked_tvp_views", 0),
                )
            )

        action = _safe_text(record.get("action")) or "save"
        if _post_save_once(uiapp, state, token, record, action):
            pending.pop(token, None)
            changed = True
            _save_state(state)
            _log("TempPhaseSaveReposted action={0} token={1}".format(action, token))
        else:
            pending.pop(token, None)
            changed = True
            _show_alert(TITLE, "Temporary state was restored, but Revit could not restart the save.")
            _log("TempPhaseSaveRepostFailed action={0} token={1}".format(action, token))

    _expire_guards(state)
    if changed:
        _save_state(state)
    return changed


def _restore_summary(uiapp, doc, summary):
    if temp_phase_close is None:
        return {"ok": False, "message": "Temp Phase close runtime is unavailable."}
    return temp_phase_close.restore_tvp_summary(uiapp=uiapp, doc=doc, summary=summary)


def _post_save_once(uiapp, state, token, record, action):
    UI = _get_ui()
    if UI is None:
        _log("TempPhaseSaveRepostUnavailableNoUi action={0} token={1}".format(action, token))
        return False
    try:
        try:
            from Autodesk.Revit.UI import PostableCommand
        except Exception:
            PostableCommand = getattr(UI, "PostableCommand", None)
        if PostableCommand is None:
            _log("TempPhaseSaveRepostUnavailableNoPostableCommand action={0} token={1}".format(action, token))
            return False
        command = PostableCommand.SaveAs if action == "save_as" else PostableCommand.Save
        command_id = UI.RevitCommandId.LookupPostableCommandId(command)
        if command_id is None:
            _log("TempPhaseSaveRepostUnavailableNoCommandId action={0} token={1}".format(action, token))
            return False
        can_post = getattr(uiapp, "CanPostCommand", None)
        if not callable(can_post) or not bool(can_post(command_id)):
            _log("TempPhaseSaveRepostUnavailableCannotPost action={0} token={1}".format(action, token))
            return False
    except Exception as ex:
        _log("TempPhaseSaveRepostLookupFailed action={0} token={1} {2}".format(action, token, _exception_text(ex)))
        return False

    state.setdefault("save_guards", {})[token] = {
        "action": action,
        "doc_key": _safe_text(record.get("doc_key")),
        "doc_runtime_id": _to_int(record.get("doc_runtime_id")),
        "expires_at": time.time() + SAVE_GUARD_SEC,
    }
    try:
        uiapp.PostCommand(command_id)
        return True
    except Exception as ex:
        state.get("save_guards", {}).pop(token, None)
        _log("TempPhaseSavePostCommandFailed action={0} token={1} {2}".format(action, token, _exception_text(ex)))
        return False


def _collect_summary(doc):
    if temp_phase_close is None:
        return {"has_restore_work": False}
    try:
        return temp_phase_close.collect_tvp_summary(
            uiapp=None,
            doc=doc,
            view_state=temp_phase_close._get_view_state(),
            include_all_discoverable=True,
        )
    except Exception as ex:
        _log("TempPhaseSaveSummaryFailed {0}".format(_exception_text(ex)))
        return {"has_restore_work": False}


def _queue_save(state, token, doc_key, runtime_id, action):
    pending = state.setdefault("pending_saves", {})
    existing = pending.get(token)
    if isinstance(existing, dict):
        existing["action"] = action
        existing["last_seen_at"] = time.time()
        return
    generation = (_to_int(state.get("next_generation")) or 0) + 1
    state["next_generation"] = generation
    pending[token] = {
        "doc_key": _safe_text(doc_key),
        "doc_runtime_id": _to_int(runtime_id),
        "action": action,
        "generation": generation,
        "attempts": 0,
        "next_try_at": 0.0,
        "requested_at": time.time(),
    }


def _consume_save_guard(state, token, action):
    guard = state.get("save_guards", {}).get(token)
    if not isinstance(guard, dict):
        return False
    if time.time() > float(guard.get("expires_at", 0.0) or 0.0):
        state.get("save_guards", {}).pop(token, None)
        return False
    expected = _safe_text(guard.get("action")) or "save"
    if expected != action:
        return False
    state.get("save_guards", {}).pop(token, None)
    return True


def _expire_guards(state):
    now = time.time()
    for token, guard in list((state.get("save_guards") or {}).items()):
        if not isinstance(guard, dict) or now > float(guard.get("expires_at", 0.0) or 0.0):
            state.get("save_guards", {}).pop(token, None)


def _retry(record, now):
    attempts = (_to_int(record.get("attempts")) or 0) + 1
    record["attempts"] = attempts
    if attempts > MAX_RESTORE_ATTEMPTS:
        return False
    record["next_try_at"] = now + RETRY_DELAY_SEC
    return True


def _resolve_document(sender, event_args):
    if event_args is not None:
        try:
            doc = event_args.Document
            if _is_valid_document(doc):
                return doc
        except Exception:
            pass
        getter = getattr(event_args, "GetDocument", None)
        if callable(getter):
            try:
                doc = getter()
                if _is_valid_document(doc):
                    return doc
            except Exception:
                pass
    runtime_id = _event_runtime_id(event_args)
    return _find_document(sender, "", runtime_id) if runtime_id is not None else None


def _identity(doc, event_args):
    doc_key = _doc_key(doc)
    runtime_id = _event_runtime_id(event_args)
    if runtime_id is None:
        runtime_id = _doc_runtime_id(doc)
    return _identity_token(doc_key, runtime_id), doc_key, runtime_id


def _find_document(uiapp_or_app, doc_key, runtime_id):
    if temp_phase_close is not None:
        try:
            return temp_phase_close._find_doc_by_identity(uiapp_or_app, doc_key, runtime_id)
        except Exception:
            pass
    return None


def _active_document(uiapp):
    if temp_phase_close is not None:
        return temp_phase_close._get_active_document(uiapp)
    return None


def _same_document(left, right):
    if temp_phase_close is not None:
        return temp_phase_close._same_document(left, right)
    return left is right


def _is_supported_document(doc):
    if temp_phase_close is not None:
        return temp_phase_close._is_doc_supported(doc)
    return _is_valid_document(doc)


def _is_valid_document(doc):
    if temp_phase_close is not None:
        return temp_phase_close._is_doc_valid(doc)
    return doc is not None


def _is_cancellable(event_args):
    if event_args is None:
        return False
    value = getattr(event_args, "Cancellable", True)
    try:
        return bool(value)
    except Exception:
        return False


def _try_cancel(event_args):
    try:
        method = getattr(event_args, "Cancel", None)
        if callable(method):
            method()
            return True
    except Exception as ex:
        _log("DocumentSavingCancelMethodFailed {0}".format(_exception_text(ex)))
    try:
        event_args.Cancel = True
        return True
    except Exception as ex:
        _log("DocumentSavingCancelPropertyFailed {0}".format(_exception_text(ex)))
        return False


def _is_shutdown(sender):
    app = getattr(sender, "Application", None) or sender
    for name in ("IsClosing", "IsShuttingDown", "IsQuitting", "IsShutdown"):
        try:
            if bool(getattr(app, name)):
                return True
        except Exception:
            pass
    return False


def _event_runtime_id(event_args):
    if event_args is None:
        return None
    value = getattr(event_args, "DocumentId", None)
    if temp_phase_close is not None:
        try:
            return temp_phase_close._eid_to_int(value)
        except Exception:
            pass
    return _to_int(value)


def _event_status(event_args):
    value = getattr(event_args, "Status", None)
    text = _safe_text(value).upper()
    if "CANCEL" in text:
        return "CANCELLED"
    if "FAIL" in text:
        return "FAILED"
    if "SUCCESS" in text or "SUCCEED" in text:
        return "SUCCEEDED"
    return text


def _get_state():
    state = None
    try:
        from pyrevit import script

        state = script.get_envvar(STATE_ENVVAR)
    except Exception:
        state = _MEMORY_STATE.get(STATE_ENVVAR)
    if not isinstance(state, dict):
        state = {}
    state.setdefault("pending_saves", {})
    state.setdefault("save_guards", {})
    state.setdefault("next_generation", 0)
    state.setdefault("last_idling_at", 0.0)
    if not isinstance(state.get("pending_saves"), dict):
        state["pending_saves"] = {}
    if not isinstance(state.get("save_guards"), dict):
        state["save_guards"] = {}
    return state


def _save_state(state):
    try:
        from pyrevit import script

        script.set_envvar(STATE_ENVVAR, state)
        return
    except Exception as ex:
        _log("TempPhaseSaveStateFailed {0}".format(_exception_text(ex)))
    _MEMORY_STATE[STATE_ENVVAR] = state


def _doc_key(doc):
    if temp_phase_close is not None:
        return temp_phase_close._doc_key(doc)
    return ""


def _doc_runtime_id(doc):
    if temp_phase_close is not None:
        return temp_phase_close._get_doc_runtime_id(doc)
    return None


def _identity_token(doc_key, runtime_id):
    if temp_phase_close is not None:
        return temp_phase_close._identity_token(doc_key, runtime_id)
    return "id|{0}".format(runtime_id) if runtime_id is not None else "key|{0}".format(doc_key)


def _get_ui():
    if temp_phase_close is not None:
        return temp_phase_close._get_ui()
    return None


def _show_alert(title, message):
    if temp_phase_close is not None:
        return temp_phase_close._show_alert(title, message)


def _log(message):
    if temp_phase_close is not None:
        return temp_phase_close._log(message)


def _exception_text(ex):
    if temp_phase_close is not None:
        return temp_phase_close._exception_text(ex)
    return str(ex)


def _safe_text(value):
    if temp_phase_close is not None:
        return temp_phase_close._safe_text(value)
    return "" if value is None else str(value)


def _to_int(value):
    if temp_phase_close is not None:
        return temp_phase_close._to_int(value)
    try:
        return int(value)
    except Exception:
        return None


def _attach(app, event_name, handler):
    if event_name == "DocumentSaving":
        app.DocumentSaving += handler
    elif event_name == "DocumentSavingAs":
        app.DocumentSavingAs += handler
    elif event_name == "DocumentSaved":
        app.DocumentSaved += handler
    elif event_name == "DocumentSavedAs":
        app.DocumentSavedAs += handler


def _detach(app, event_name, handler):
    try:
        if event_name == "DocumentSaving":
            app.DocumentSaving -= handler
        elif event_name == "DocumentSavingAs":
            app.DocumentSavingAs -= handler
        elif event_name == "DocumentSaved":
            app.DocumentSaved -= handler
        elif event_name == "DocumentSavedAs":
            app.DocumentSavedAs -= handler
    except Exception:
        pass


def _uninstall_registration(registration):
    app = registration.get("app")
    for event_name, handler in list((registration.get("handlers") or {}).items()):
        _detach(app, event_name, handler)
