# -*- coding: utf-8 -*-
"""Pure-Python Temp Phase close recovery.

The manual Temp Phase command stores its view sessions in
``EASYBIM_TEMP_PHASE_VIEW_STATE``.  This module consumes that state without a
controller assembly.  It is deliberately safe-by-default:

* the DocumentClosing event is cancelled only when it is cancellable and
  cleanup work is actually discoverable;
* model changes are deferred to Idling, where a normal Revit transaction is
  valid;
* a second close is posted only after cleanup succeeds and only once per
  close-attempt generation; and
* missing API members, stale documents, failed transactions, and shutdown
  events leave the document open instead of silently posting a close.

The module can be called from normal pyRevit hook scripts or from a direct
Python event subscription created by ``startup.py``.  It intentionally uses
IronPython-compatible syntax and does not depend on a compiled DLL.
"""

from __future__ import print_function

import os
import sys
import time

try:
    from pyrevit import script
except Exception:
    script = None

try:
    from easybim import temp_phase_view
except Exception:
    temp_phase_view = None

try:
    # The helper imports no WPF assemblies at module import time.  Keeping the
    # import optional lets the close state machine continue to work on hosts
    # where WPF is unavailable, in which case the native Revit TaskDialog
    # fallback below is used.
    from easybim import temp_phase_dialog
except Exception:
    temp_phase_dialog = None


TITLE = "Temp Phase"
STATE_ENVVAR = "EASYBIM_TEMP_PHASE_CLOSE_STATE"
VIEW_STATE_ENVVAR = "EASYBIM_TEMP_PHASE_VIEW_STATE"
MAX_RESTORE_ATTEMPTS = 8
RETRY_DELAY_SEC = 0.25
REPOST_GUARD_SEC = 12.0
COMMIT_GUARD_SEC = 60.0
# A close identity is a short-lived handoff between DocumentClosing and
# DocumentClosed.  Revit normally raises the latter immediately, but a
# modal save/synchronise dialog or a failed close can leave the handoff
# around for a while.  Expiring it prevents an old identity from cleaning a
# later document that happens to reuse an event id.
CLOSING_IDENTITY_GUARD_SEC = 120.0
IDLE_THROTTLE_SEC = 0.10


class _NullLogger(object):
    def debug(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


try:
    LOGGER = script.get_logger() if script is not None else _NullLogger()
except Exception:
    LOGGER = _NullLogger()


_MEMORY_STATE = {}

# Completion-event delegates are kept outside ``startup.py``.  pyRevit may
# execute startup more than once during a reload while Revit still owns the
# previous delegates; retaining the delegates here lets us detach them before
# attaching a fresh copy and avoids duplicate completion handling.
_RUNTIME = sys.modules.setdefault("easybim._temp_phase_close_runtime", {})


def handle_doc_closing(uiapp=None, event_args=None):
    """Inspect and synchronously cancel a document close when cleanup is needed.

    pyRevit hook scripts normally call this as
    ``handle_doc_closing(event_args=EXEC_PARAMS.event_args)``.  Direct Revit
    delegates may call it as ``handle_doc_closing(sender, args)``; both forms
    are accepted.
    """
    uiapp, event_args = _normalize_event_call(uiapp, event_args)
    _log(
        "DocClosingHookEntry argsType={0} Cancellable={1}".format(
            _safe_text(type(event_args)),
            _event_is_cancellable(event_args),
        )
    )
    if event_args is None:
        _log("DocClosingSkippedMissingEventArgs")
        return False

    uiapp = _get_uiapp(uiapp)
    closing_doc = _resolve_doc_from_event_args(event_args, uiapp)
    doc_runtime_id = _extract_doc_runtime_id(event_args, closing_doc)
    doc_key = _doc_key(closing_doc)
    state = _get_state()
    _expire_closing_identities(state)

    # DocumentClosed may expose a different identity (or no Document object at
    # all) after Revit has released the document.  Keep the stable path and
    # both identities from this event as a handoff so the close-completion
    # hook can still clear only this document's state.
    _remember_closing_identity(
        state=state,
        token=_identity_token(doc_key, doc_runtime_id),
        doc=closing_doc,
        event_args=event_args,
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
        title=_doc_title(closing_doc),
    )

    # A close reposted by this module is allowed through once.  This guard is
    # checked before discovery so a stale session cannot create a close loop.
    token = _identity_token(doc_key, doc_runtime_id)
    if _consume_repost_guard(state, token):
        _save_state(state)
        _log("DocClosingAllowedRepost token={0}".format(token))
        return False

    # A Save/Sync operation selected by the user owns the close attempt until
    # its post-operation event arrives.  If another close command is issued in
    # that window, cancel it and coalesce it rather than allowing the restored
    # document to close before the commit is complete.
    if token in (state.get("commit_operations") or {}) or token in (
        state.get("pending_commit_closes") or {}
    ):
        if _event_is_cancellable(event_args) and _try_cancel_event(event_args):
            _save_state(state)
            _log("DocClosingCancelledCommitInProgress token={0}".format(token))
            return True
        _log("DocClosingCommitInProgressNotCancellable token={0}".format(token))
        return False

    summary = None
    if _is_doc_valid(closing_doc) and not _is_doc_supported(closing_doc):
        _save_state(state)
        _log("DocClosingSkippedUnsupportedDocument token={0}".format(token))
        return False

    view_state = _get_view_state()
    if not _is_document_armed(
        view_state,
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    ):
        _save_state(state)
        _log(
            "DocClosingSkippedUnarmedDocument token={0} title={1}".format(
                token,
                _doc_title(closing_doc),
            )
        )
        return False

    _log("DocClosingArmedDocument token={0}".format(token))

    if _is_doc_valid(closing_doc):
        summary = collect_tvp_summary(
            uiapp=uiapp,
            doc=closing_doc,
            view_state=view_state,
        )
        has_work = bool(summary.get("has_restore_work"))
    else:
        # If Revit has already released the Document object, tracked sessions
        # can still be matched by DocumentId.  Untracked TVP cannot be safely
        # inspected without the document, so do not cancel for that case.
        has_work = _has_tracked_sessions_for_identity(
            _get_view_state(),
            doc_key=doc_key,
            doc_runtime_id=doc_runtime_id,
        )

    _log(
        "DocClosingInspect token={0} title={1} hasWork={2} cancellable={3}".format(
            token,
            _doc_title(closing_doc),
            bool(has_work),
            _event_is_cancellable(event_args),
        )
    )

    if not has_work:
        _save_state(state)
        _log("DocClosingAllowedNoTempState token={0}".format(token))
        return False

    if not _event_is_cancellable(event_args):
        _save_state(state)
        _log("DocClosingSkippedNonCancellable token={0}".format(token))
        return False

    if not _try_cancel_event(event_args):
        _save_state(state)
        _log("DocClosingCancelFailed token={0}".format(token))
        return False

    _queue_pending_close(
        state=state,
        token=token,
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
        title=_doc_title(closing_doc),
        summary=summary,
    )
    _save_state(state)
    _log("DocClosingCancelSucceeded token={0}".format(token))
    _log("DocClosingCancelledQueued token={0}".format(token))
    return True


def handle_idling(uiapp=None, event_args=None):
    """Restore pending documents and finish Save/Sync-before-close commits.

    Revit's save and synchronize commands are asynchronous.  The command is
    posted from this module only after restoration; the corresponding
    ``DocumentSaved``/``DocumentSavedAs``/``DocumentSynchronizedWithCentral``
    event then moves the operation into ``pending_commit_closes``.  The next
    Idling callback posts the guarded Close command.
    """
    del event_args
    uiapp = _get_uiapp(uiapp)
    _log("AppIdlingHookEntry")
    if uiapp is None:
        return False

    state = _get_state()
    if _is_shutdown_context(uiapp):
        if (
            state.get("pending_closes")
            or state.get("commit_operations")
            or state.get("pending_commit_closes")
            or state.get("closing_identities")
        ):
            state["pending_closes"] = {}
            state["commit_operations"] = {}
            state["pending_commit_closes"] = {}
            state["commit_failures"] = {}
            state["closing_identities"] = {}
            _save_state(state)
        _log("AppIdlingSkippedShutdown")
        return False
    pending = state.get("pending_closes")
    if not isinstance(pending, dict):
        pending = {}
        state["pending_closes"] = pending

    commit_pending = state.get("pending_commit_closes")
    if not isinstance(commit_pending, dict):
        commit_pending = {}
        state["pending_commit_closes"] = commit_pending
    commit_operations = state.get("commit_operations")
    if not isinstance(commit_operations, dict):
        commit_operations = {}
        state["commit_operations"] = commit_operations
    commit_failures = state.get("commit_failures")
    if not isinstance(commit_failures, dict):
        commit_failures = {}
        state["commit_failures"] = commit_failures

    if not pending and not commit_operations and not commit_pending and not commit_failures:
        _expire_repost_guards(state)
        _save_state(state)
        return False

    now = time.time()
    last_idle = float(state.get("last_idling_at", 0.0) or 0.0)
    if now - last_idle < IDLE_THROTTLE_SEC:
        return False
    state["last_idling_at"] = now
    changed = False

    # Completion handlers run on Revit's application event thread, so failures
    # are queued and the user-facing TaskDialog is shown from Idling instead.
    for token, failure in list(commit_failures.items()):
        if not isinstance(failure, dict):
            commit_failures.pop(token, None)
            changed = True
            continue
        commit_failures.pop(token, None)
        changed = True
        action = _safe_text(failure.get("action")) or "save"
        operation_name = "synchronization" if action == "sync" else "save"
        message = _safe_text(failure.get("message"))
        if not message:
            message = (
                "The {0} was cancelled or failed. The restored document remains open; "
                "the temporary state was not committed.".format(operation_name)
            )
        _show_alert(TITLE, message)
        _log(
            "TempPhaseCommitFailureShown action={0} token={1}".format(
                action,
                token,
            )
        )

    # A successful Save/Sync completion is converted into one guarded Close
    # request.  Keep retrying briefly when the target document is inactive;
    # command availability failures eventually leave the document open.
    for token, record in list(commit_pending.items()):
        if not isinstance(record, dict):
            commit_pending.pop(token, None)
            changed = True
            continue
        expires_at = float(record.get("expires_at", 0.0) or 0.0)
        if expires_at and now > expires_at:
            commit_pending.pop(token, None)
            changed = True
            _show_alert(
                TITLE,
                "The restored file was committed, but Revit did not allow the "
                "document to close. The file remains open.",
            )
            _log("TempPhaseCommitCloseExpired token={0}".format(token))
            continue
        next_try = float(record.get("next_try_at", 0.0) or 0.0)
        if now < next_try:
            continue
        if _post_close_once(uiapp, state, token, record):
            commit_pending.pop(token, None)
            changed = True
            _log("TempPhaseCommitCloseReposted token={0}".format(token))
            _log("TempPhaseCloseReposted token={0}".format(token))
            continue
        attempts = (_to_int(record.get("attempts")) or 0) + 1
        record["attempts"] = attempts
        target_doc = _find_doc_by_identity(
            uiapp,
            doc_key=record.get("doc_key"),
            doc_runtime_id=record.get("doc_runtime_id"),
        )
        active_doc = _get_active_document(uiapp)
        if (
            attempts < MAX_RESTORE_ATTEMPTS
            and target_doc is not None
            and active_doc is not None
            and not _same_document(target_doc, active_doc)
        ):
            record["next_try_at"] = now + RETRY_DELAY_SEC
            changed = True
            _log("TempPhaseCommitCloseWaitingForActiveDocument token={0}".format(token))
            continue
        commit_pending.pop(token, None)
        changed = True
        _show_alert(
            TITLE,
            "The restored file was committed, but Revit could not post its Close "
            "command. The document remains open.",
        )
        _log("TempPhaseCommitClosePostFailed token={0}".format(token))

    for token, record in list(pending.items()):
        if not isinstance(record, dict):
            pending.pop(token, None)
            changed = True
            continue

        next_try = float(record.get("next_try_at", 0.0) or 0.0)
        if now < next_try:
            continue

        doc = _find_doc_by_identity(
            uiapp,
            doc_key=record.get("doc_key"),
            doc_runtime_id=record.get("doc_runtime_id"),
        )
        if not _is_doc_valid(doc):
            pending.pop(token, None)
            changed = True
            _log("IdlingPendingDocumentUnavailable token={0}".format(token))
            continue

        summary = collect_tvp_summary(
            uiapp=uiapp,
            doc=doc,
            view_state=_get_view_state(),
        )
        if summary.get("has_restore_work"):
            result = restore_tvp_summary(
                uiapp=uiapp,
                doc=doc,
                summary=summary,
            )
            if not result.get("ok"):
                if _retry_pending_close(record, now):
                    changed = True
                    _log(
                        "IdlingRestoreRetry token={0} attempt={1}".format(
                            token,
                            record.get("attempts"),
                        )
                    )
                    continue

                pending.pop(token, None)
                changed = True
                _show_alert(
                    TITLE,
                    "Temporary view properties could not be restored.\n\n"
                    "The close was cancelled. Please correct the issue and try again.",
                )
                _log("IdlingRestoreFailedGivingUp token={0}".format(token))
                continue

            _log(
                "IdlingRestored token={0} phaseViews={1} untrackedViews={2}".format(
                    token,
                    result.get("restored_phase_views", 0),
                    result.get("disabled_untracked_tvp_views", 0),
                )
            )

        # Preserve the counts discovered at DocumentClosing when another hook
        # has already disabled TVP by the time Idling runs.
        if not summary.get("tracked_restore_views") and not summary.get("untracked_tvp_views"):
            summary["tracked_restore_count"] = _to_int(record.get("tracked_restore_count")) or 0
            summary["untracked_tvp_count"] = _to_int(record.get("untracked_tvp_count")) or 0
        if "doc_is_workshared" not in summary:
            summary["doc_is_workshared"] = bool(record.get("doc_is_workshared"))
        decision = _show_close_decision(summary, record)
        pending.pop(token, None)
        changed = True

        if decision == "save_close":
            _log("TempPhaseSaveCloseSelected token={0}".format(token))
            if not _begin_commit_close(uiapp, state, token, record, "save"):
                _show_alert(
                    TITLE,
                    "The restored file remains open because Revit could not start its "
                    "Save command.",
                )
        elif decision == "sync_close":
            _log("TempPhaseSyncCloseSelected token={0}".format(token))
            if not _begin_commit_close(uiapp, state, token, record, "sync"):
                _show_alert(
                    TITLE,
                    "The restored file remains open because Revit could not start "
                    "synchronization with central.",
                )
        else:
            _log("TempPhaseCloseKeptOpen token={0}".format(token))
            _log("IdlingCloseCancelledByUser token={0}".format(token))

    before_expiry = (
        len(state.get("repost_guards") or {}),
        len(state.get("commit_operations") or {}),
        len(state.get("pending_commit_closes") or {}),
        len(state.get("commit_failures") or {}),
        len(state.get("closing_identities") or {}),
    )
    _expire_repost_guards(state)
    after_expiry = (
        len(state.get("repost_guards") or {}),
        len(state.get("commit_operations") or {}),
        len(state.get("pending_commit_closes") or {}),
        len(state.get("commit_failures") or {}),
        len(state.get("closing_identities") or {}),
    )
    if before_expiry != after_expiry:
        changed = True
    if changed:
        _save_state(state)
    return changed


def handle_app_idling(uiapp=None, event_args=None):
    """Alias matching the pyRevit hook name used by existing EasyBIM code."""
    return handle_idling(uiapp=uiapp, event_args=event_args)


def handle_doc_closed(uiapp=None, event_args=None):
    """Clear per-document pending/guard/session state after close completion."""
    uiapp, event_args = _normalize_event_call(uiapp, event_args)
    _log("DocClosedHookEntry")
    if event_args is None:
        _log("DocClosedSkippedMissingEventArgs")
        return False

    doc = _resolve_doc_from_event_args(event_args, uiapp)
    event_document_id = _event_document_id(event_args)
    doc_runtime_identity = _get_doc_runtime_id(doc)
    doc_runtime_id = event_document_id
    if doc_runtime_id is None:
        doc_runtime_id = doc_runtime_identity
    doc_key = _doc_key(doc)
    token = _identity_token(doc_key, doc_runtime_id)
    state = _get_state()
    _expire_closing_identities(state)
    status = _event_status(event_args)

    # Resolve the handoff before mutating any close state.  The event token is
    # not sufficient on all Revit versions: DocumentClosed can provide a
    # different DocumentId, or only a released Document wrapper.  Matching by
    # stable path and both recorded identities handles all of those forms.
    closing_records = _find_closing_identity_records(
        state,
        token=token,
        doc_key=doc_key,
        event_document_id=event_document_id,
        doc_runtime_id=doc_runtime_identity,
    )
    identity_pairs = []
    if doc_key or doc_runtime_id is not None:
        identity_pairs.append((doc_key, doc_runtime_id))
    if doc_key or doc_runtime_identity is not None:
        identity_pairs.append((doc_key, doc_runtime_identity))
    close_tokens = {token} if token else set()
    for identity_key, record in closing_records:
        close_tokens.add(identity_key)
        if isinstance(record, dict):
            close_tokens.add(_safe_text(record.get("token")))
            identity_pairs.append(
                (
                    _safe_text(record.get("doc_key")),
                    _to_int(record.get("doc_runtime_id")),
                )
            )
            identity_pairs.append(
                (
                    _safe_text(record.get("doc_key")),
                    _to_int(record.get("event_document_id")),
                )
            )
    identity_pairs = _unique_identity_pairs(identity_pairs)

    # A cancelled/failed close leaves the document open.  Preserve any
    # pending state so a subsequent close can be handled normally.  Only the
    # short-lived identity handoff is discarded; the armed document and view
    # sessions remain available for the next attempt.
    if status in ("CANCELLED", "FAILED"):
        _remove_closing_identity_records(state, closing_records)
        for guard_token in close_tokens:
            state.get("repost_guards", {}).pop(guard_token, None)
        _save_state(state)
        _log(
            "DocClosedNonSuccess status={0} token={1} identityHandoffDiscarded={2}".format(
                status,
                token,
                len(closing_records),
            )
        )
        return False

    _remove_closing_identity_records(state, closing_records)
    # Clear all close-operation records that refer to the resolved identity,
    # not merely the token exposed by this DocumentClosed event.
    operation_records = []
    for map_name in (
        "pending_closes",
        "repost_guards",
        "commit_operations",
        "pending_commit_closes",
        "commit_failures",
    ):
        operation_records.extend(
            _pop_close_records_for_identity(
                state.get(map_name) or {},
                close_tokens=close_tokens,
                identity_pairs=identity_pairs,
                map_name=map_name,
            )
        )

    stored_doc_key = ""
    stored_runtime_id = None
    for record in operation_records:
        if not isinstance(record, dict):
            continue
        stored_doc_key = stored_doc_key or _safe_text(record.get("doc_key")).strip()
        if stored_runtime_id is None:
            stored_runtime_id = _to_int(
                record.get("doc_runtime_id")
                if record.get("doc_runtime_id") is not None
                else record.get("event_document_id")
            )
        identity_pairs.append(
            (
                _safe_text(record.get("doc_key")),
                _to_int(record.get("doc_runtime_id")),
            )
        )

    resolved_doc_key = doc_key or stored_doc_key
    resolved_runtime_id = (
        doc_runtime_identity
        if doc_runtime_identity is not None
        else doc_runtime_id
        if doc_runtime_id is not None
        else stored_runtime_id
    )
    identity_pairs = _unique_identity_pairs(
        identity_pairs + [(resolved_doc_key, resolved_runtime_id)]
    )
    _drop_doc_sessions_for_identities(_get_view_state(), identity_pairs)
    _save_state(state)
    _log(
        "DocClosedIdentityResolved token={0} matched={1} docKey={2} "
        "eventDocumentId={3} runtimeId={4}".format(
            token,
            len(closing_records),
            resolved_doc_key,
            "" if event_document_id is None else event_document_id,
            "" if resolved_runtime_id is None else resolved_runtime_id,
        )
    )
    _log(
        "DocClosedCleared status={0} token={1} identityHandoffConsumed={2}".format(
            status or "UNKNOWN",
            token,
            len(closing_records),
        )
    )
    return True


# Explicit delegate-friendly names make direct ``startup.py`` subscriptions
# readable and avoid ambiguity about sender/event-args ordering.
def document_closing_handler(sender, args):
    return handle_doc_closing(uiapp=sender, event_args=args)


def idling_handler(sender, args):
    return handle_idling(uiapp=sender, event_args=args)


def document_closed_handler(sender, args):
    return handle_doc_closed(uiapp=sender, event_args=args)


def document_saved_handler(sender, args):
    """Handle the post-Save completion event for a pending close."""
    return handle_commit_completed(sender, args, action="save")


def document_saved_as_handler(sender, args):
    """DocumentSavedAs is also completion for a Save that opened Save As."""
    return handle_commit_completed(sender, args, action="save")


def document_synchronized_with_central_handler(sender, args):
    """Handle the post-SynchronizeWithCentral completion event."""
    return handle_commit_completed(sender, args, action="sync")


def handle_document_saved(sender, event_args):
    """Compatibility-friendly name for the post-Save completion delegate."""
    return document_saved_handler(sender, event_args)


def handle_document_saved_as(sender, event_args):
    """Compatibility-friendly name for the post-SaveAs completion delegate."""
    return document_saved_as_handler(sender, event_args)


def handle_document_synchronized_with_central(sender, event_args):
    """Compatibility-friendly name for the post-sync completion delegate."""
    return document_synchronized_with_central_handler(sender, event_args)


def install_completion_handlers():
    """Install only post-operation completion handlers.

    This deliberately subscribes only to post-operation completion events.
    Save and synchronization are initiated by an explicit command link after
    restoration; these handlers merely observe completion before the guarded
    Close command is posted.
    """
    try:
        from pyrevit import DB, HOST_APP, framework
    except Exception as ex:
        _log("TempPhaseCommitHandlersInstallFailed {0}".format(_exception_text(ex)))
        return False

    app = getattr(HOST_APP, "app", None)
    if app is None:
        _log("TempPhaseCommitHandlersInstallFailed missingApplication")
        return False

    existing = _RUNTIME.get("completion_registration")
    if isinstance(existing, dict):
        _uninstall_completion_registration(existing)

    events = (
        ("DocumentSaved", "DocumentSavedEventArgs", document_saved_handler),
        ("DocumentSavedAs", "DocumentSavedAsEventArgs", document_saved_as_handler),
        (
            "DocumentSynchronizedWithCentral",
            "DocumentSynchronizedWithCentralEventArgs",
            document_synchronized_with_central_handler,
        ),
    )
    handlers = {}
    try:
        db_events = getattr(DB, "Events", None)
        for event_name, args_name, callback in events:
            args_type = getattr(db_events, args_name, None) if db_events is not None else None
            if args_type is None:
                raise AttributeError("Missing {0}".format(args_name))
            event_handler = framework.EventHandler[args_type](callback)
            _attach_completion(app, event_name, event_handler)
            handlers[event_name] = event_handler
    except Exception as ex:
        for event_name, handler in list(handlers.items()):
            _detach_completion(app, event_name, handler)
        _log("TempPhaseCommitHandlersInstallFailed {0}".format(_exception_text(ex)))
        return False

    _RUNTIME["completion_registration"] = {"app": app, "handlers": handlers}
    _log(
        "TempPhaseCommitHandlersInstalled events=DocumentSaved,DocumentSavedAs,"
        "DocumentSynchronizedWithCentral"
    )
    return True


def uninstall_completion_handlers():
    registration = _RUNTIME.get("completion_registration")
    if isinstance(registration, dict):
        _uninstall_completion_registration(registration)
        _RUNTIME.pop("completion_registration", None)
        _log("TempPhaseCommitHandlersUninstalled")
    return True


def handle_commit_completed(sender, event_args, action):
    """Move a completed Save/Sync operation to the guarded Close queue."""
    doc = _resolve_doc_from_event_args(event_args, sender)
    doc_runtime_id = _extract_doc_runtime_id(event_args, doc)
    doc_key = _doc_key(doc)
    token = _identity_token(doc_key, doc_runtime_id)
    status = _event_status(event_args)
    state = _get_state()
    operations = state.get("commit_operations") or {}
    operation = operations.get(token)
    if not isinstance(operation, dict):
        # Event DocumentId and the runtime identity used by a lightweight
        # host/test wrapper can differ.  Fall back to the stable document key.
        for candidate_token, candidate in list(operations.items()):
            if not isinstance(candidate, dict):
                continue
            if doc_key and doc_key == _safe_text(candidate.get("doc_key")):
                token = candidate_token
                operation = candidate
                break
    if not isinstance(operation, dict):
        _log(
            "TempPhaseCommitCompletionIgnored action={0} token={1} status={2}".format(
                action,
                token,
                status or "UNKNOWN",
            )
        )
        return False

    expected_action = _safe_text(operation.get("action")) or "save"
    if expected_action != action:
        _log(
            "TempPhaseCommitCompletionIgnoredWrongAction expected={0} actual={1} token={2}".format(
                expected_action,
                action,
                token,
            )
        )
        return False
    operations.pop(token, None)
    if _completion_succeeded(status):
        pending = state.setdefault("pending_commit_closes", {})
        if token not in pending:
            record = dict(operation)
            record["completed_at"] = time.time()
            record["next_try_at"] = 0.0
            record["expires_at"] = time.time() + COMMIT_GUARD_SEC
            pending[token] = record
        _save_state(state)
        _log(
            "TempPhaseCommitCompleted action={0} token={1} status={2}".format(
                action,
                token,
                status or "UNKNOWN",
            )
        )
        return True

    failure = state.setdefault("commit_failures", {})
    failure[token] = {
        "action": action,
        "doc_key": _safe_text(operation.get("doc_key")),
        "doc_runtime_id": _to_int(operation.get("doc_runtime_id")),
        "status": status,
        "message": _commit_failure_message(action, status),
        "failed_at": time.time(),
    }
    _save_state(state)
    _log(
        "TempPhaseCommitFailed action={0} token={1} status={2}".format(
            action,
            token,
            status or "UNKNOWN",
        )
    )
    return False


def collect_tvp_summary(uiapp, doc, view_state=None, include_all_discoverable=True):
    """Return tracked and untracked Temporary View Properties work for *doc*."""
    del uiapp
    if view_state is None:
        view_state = _get_view_state()

    doc_key = _doc_key(doc)
    doc_runtime_id = _get_doc_runtime_id(doc)
    tracked = []
    tracked_ids = {}
    stale_keys = []
    sessions = view_state.get("view_sessions") or {}

    for view_key, session in list(sessions.items()):
        if not isinstance(session, dict):
            stale_keys.append(view_key)
            continue
        if not _session_matches_doc(session, doc_key, doc_runtime_id):
            continue

        view_id = _to_int(session.get("view_id"))
        if view_id is None:
            stale_keys.append(view_key)
            continue
        view = _get_doc_element(doc, view_id)
        if not _is_view_valid(view):
            stale_keys.append(view_key)
            continue

        tracked_ids[view_id] = True
        tracked.append(
            {
                "key": view_key,
                "session": session,
                "view": view,
                "view_id": view_id,
                "view_name": _view_name(view, session),
            }
        )

    for key in stale_keys:
        sessions.pop(key, None)
        (view_state.get("last_seen_tvp") or {}).pop(key, None)
    if stale_keys:
        _save_view_state(view_state)

    discoverable = []
    untracked = []
    if include_all_discoverable:
        for view in _collect_discoverable_tvp_views(doc):
            view_id = _eid_to_int(getattr(view, "Id", None))
            if view_id is None:
                continue
            item = {
                "view": view,
                "view_id": view_id,
                "view_name": _view_name(view, None),
            }
            discoverable.append(item)
            if view_id not in tracked_ids:
                untracked.append(item)

    lines = []
    for item in tracked:
        lines.append("Tracked: {0}".format(item.get("view_name") or item.get("view_id")))
    for item in untracked:
        lines.append("Untracked TVP: {0}".format(item.get("view_name") or item.get("view_id")))

    return {
        "doc_key": doc_key,
        "doc_runtime_id": doc_runtime_id,
        "doc_title": _doc_title(doc),
        "doc_is_workshared": _doc_is_workshared(doc),
        "tracked_restore_views": tracked,
        "untracked_tvp_views": untracked,
        "discoverable_tvp_views": discoverable,
        "dialog_view_lines": lines,
        "tracked_restore_count": len(tracked),
        "untracked_tvp_count": len(untracked),
        "has_restore_work": bool(tracked or untracked),
    }


def restore_tvp_summary(uiapp, doc, summary):
    """Restore tracked phases and disable tracked/untracked TVP in one transaction."""
    del uiapp
    if not _is_doc_valid(doc):
        return _restore_failure("Document is no longer available.")

    tracked = list(summary.get("tracked_restore_views") or [])
    untracked = list(summary.get("untracked_tvp_views") or [])
    if not tracked and not untracked:
        return {
            "ok": True,
            "restored_phase_views": 0,
            "disabled_untracked_tvp_views": 0,
            "failed_views": [],
            "message": "",
        }

    DB = _get_db()
    if DB is None:
        return _restore_failure("The Revit database API is unavailable.")

    transaction = None
    started = False
    restored_phase_views = 0
    disabled_untracked = 0
    failed_views = []
    try:
        transaction = DB.Transaction(doc, "Temp Phase: Restore Temporary View Properties")
        transaction.Start()
        started = True

        for item in tracked:
            view = item.get("view")
            session = item.get("session") or {}
            if not _is_view_valid(view):
                continue

            if _is_tvp_active(view) and not _disable_tvp(view):
                failed_views.append(item.get("view_name") or item.get("view_id"))
                raise Exception(
                    "Failed disabling Temporary View Properties for view {0}".format(
                        item.get("view_id")
                    )
                )
            original_phase_id = _to_int(session.get("original_phase_id"))
            if original_phase_id is not None:
                if not _set_view_phase_id(view, original_phase_id):
                    failed_views.append(item.get("view_name") or item.get("view_id"))
                    raise Exception("Failed restoring the phase for view {0}".format(item.get("view_id")))
                restored_phase_views += 1

        for item in untracked:
            view = item.get("view")
            if not _is_view_valid(view):
                continue
            if _is_tvp_active(view):
                if not _disable_tvp(view):
                    failed_views.append(item.get("view_name") or item.get("view_id"))
                    raise Exception(
                        "Failed disabling Temporary View Properties for view {0}".format(
                            item.get("view_id")
                        )
                    )
                disabled_untracked += 1

        transaction.Commit()
        _log(
            "TempPhaseRestoreCommitted phaseViews={0} untrackedViews={1}".format(
                restored_phase_views,
                disabled_untracked,
            )
        )

        view_state = _get_view_state()
        for item in tracked:
            key = item.get("key")
            if key:
                (view_state.get("view_sessions") or {}).pop(key, None)
                (view_state.get("last_seen_tvp") or {}).pop(key, None)
        _save_view_state(view_state)

        return {
            "ok": True,
            "restored_phase_views": restored_phase_views,
            "disabled_untracked_tvp_views": disabled_untracked,
            "failed_views": failed_views,
            "message": "",
        }
    except Exception as ex:
        if started:
            _rollback_transaction(transaction)
        _log("RestoreTransactionFailed {0}".format(_exception_text(ex)))
        return _restore_failure(
            "Temporary view properties could not be restored.",
            failed_views=failed_views,
        )


def _restore_failure(message, failed_views=None):
    return {
        "ok": False,
        "restored_phase_views": 0,
        "disabled_untracked_tvp_views": 0,
        "failed_views": list(failed_views or []),
        "message": message,
    }


def _show_close_decision(summary, record):
    """Show the commit-aware post-restore decision.

    Return ``save_close``, ``sync_close`` or ``cancel``.  There is intentionally
    no immediate close option: the restored state must be committed before the
    document is allowed to close.
    """
    UI = _get_ui()
    title = _safe_text(record.get("title")) if isinstance(record, dict) else ""
    title = title or _safe_text(summary.get("doc_title")) or "the document"
    tracked_count = _to_int(summary.get("tracked_restore_count"))
    if tracked_count is None:
        tracked_count = len(summary.get("tracked_restore_views") or [])
    untracked_count = _to_int(summary.get("untracked_tvp_count"))
    if untracked_count is None:
        untracked_count = len(summary.get("untracked_tvp_views") or [])
    workshared = bool(summary.get("doc_is_workshared"))

    # Keep the useful details in the diagnostic stream while keeping the
    # decision window short enough to be read at a glance.
    compact_message = (
        getattr(temp_phase_dialog, "WARNING_MESSAGE", None)
        if temp_phase_dialog is not None
        else None
    ) or (
        "Please Sync or Save the model again to remove all the Temporary Phases "
        "and Views Settings before closing!!"
    )
    restored_message = (
        getattr(temp_phase_dialog, "RESTORED_MESSAGE", None)
        if temp_phase_dialog is not None
        else None
    ) or "Temporary phase/view state has been restored."
    _log(
        "TempPhaseDialogSummary title={0} tracked={1} untracked={2} "
        "workshared={3}".format(
            title,
            tracked_count,
            untracked_count,
            workshared,
        )
    )

    # WPF is the primary presentation so Sync/Save can be emphasized in red
    # and Temporary Phases can be bold.  The helper returns None when its
    # assemblies or window cannot be loaded, which deliberately falls through
    # to the native Revit warning dialog.
    if temp_phase_dialog is not None:
        try:
            choice = temp_phase_dialog.show_close_decision(
                workshared=workshared,
                title="Temp Phase Warning",
            )
        except Exception as ex:
            _log("TempPhaseDialogWpfException {0}".format(_exception_text(ex)))
            choice = None
        if choice in ("save_close", "sync_close", "cancel"):
            _log(
                "TempPhaseDialogWpfShown choice={0} workshared={1} "
                "tracked={2} untracked={3}".format(
                    choice,
                    workshared,
                    tracked_count,
                    untracked_count,
                )
            )
            return choice
        _log("TempPhaseDialogWpfUnavailable")

    if UI is None:
        _show_alert(
            "Temp Phase Warning",
            "{0}\n\n{1}".format(compact_message, restored_message),
        )
        _log("TempPhaseDialogFallbackNoUi choice=cancel")
        return "cancel"

    try:
        dialog = UI.TaskDialog("Temp Phase Warning")
        if hasattr(dialog, "TitleAutoPrefix"):
            dialog.TitleAutoPrefix = False
        if hasattr(dialog, "AllowCancellation"):
            dialog.AllowCancellation = True
        # Keep the warning as the prominent instruction and put the short
        # restoration status beneath it.  The WPF path adds the requested
        # per-word red styling; this native path remains intentionally plain.
        dialog.MainInstruction = compact_message
        dialog.MainContent = restored_message
        task_dialog_icon = getattr(UI, "TaskDialogIcon", None)
        # Revit's enum member is ``TaskDialogIconWarning``; the short
        # ``Warning`` name is kept as a fallback for wrapper hosts.
        warning_icon = None
        for icon_member in ("TaskDialogIconWarning", "Warning"):
            warning_icon = getattr(task_dialog_icon, icon_member, None)
            if warning_icon is not None:
                break
        if warning_icon is not None:
            try:
                dialog.MainIcon = warning_icon
            except Exception:
                # A very old Python.NET wrapper may expose the icon enum but
                # not the TaskDialog.MainIcon setter.  The dialog remains a
                # valid compact fallback in that case.
                pass

        command_ids = getattr(UI, "TaskDialogCommandLinkId", None)
        link_one = getattr(command_ids, "CommandLink1", None)
        link_two = getattr(command_ids, "CommandLink2", None)
        link_three = getattr(command_ids, "CommandLink3", None)
        if link_one is None or link_two is None:
            _show_alert(
                "Temp Phase Warning",
                "{0}\n\n{1}".format(compact_message, restored_message),
            )
            _log("TempPhaseDialogFallbackMissingCommandLinks choice=cancel")
            return "cancel"

        dialog.AddCommandLink(link_one, "Save and Close")
        if workshared:
            dialog.AddCommandLink(link_two, "Synchronize and Close")
            if link_three is not None:
                dialog.AddCommandLink(link_three, "Keep File Open")
            result_map = (
                ("save_close", "CommandLink1"),
                ("sync_close", "CommandLink2"),
                ("cancel", "CommandLink3"),
            )
        else:
            dialog.AddCommandLink(link_two, "Keep File Open")
            result_map = (("save_close", "CommandLink1"), ("cancel", "CommandLink2"))
        # Keep File Open is deliberately the safe default in the native
        # fallback too.  Revit otherwise focuses the first command link,
        # which would make Enter choose Save.
        try:
            result_enum = getattr(UI, "TaskDialogResult", None)
            default_member = "CommandLink3" if workshared else "CommandLink2"
            default_button = getattr(result_enum, default_member, None)
            if default_button is not None:
                dialog.DefaultButton = default_button
        except Exception:
            pass
        try:
            dialog.CommonButtons = UI.TaskDialogCommonButtons.Cancel
        except Exception:
            pass
        result = dialog.Show()

        choice = _task_dialog_choice(result, UI, result_map)
        _log(
            "TempPhaseDialogShown mode=fallback result={0} choice={1} workshared={2} "
            "tracked={3} untracked={4}".format(
                _safe_text(result),
                choice,
                workshared,
                tracked_count,
                untracked_count,
            )
        )
        return choice
    except Exception as ex:
        _log("TempPhaseDialogFallbackFailed {0}".format(_exception_text(ex)))
        _show_alert(
            "Temp Phase Warning",
            "{0}\n\n{1}".format(compact_message, restored_message),
        )
        return "cancel"


def _task_dialog_choice(result, UI, result_map):
    """Map TaskDialogResult enums to semantic command choices.

    The command-link ids passed to ``AddCommandLink`` are a different enum
    from the result returned by ``Show``.  Compare against TaskDialogResult
    first, then retain a textual fallback for Python.NET wrappers.
    """
    result_enum = getattr(UI, "TaskDialogResult", None)
    for choice, member_name in result_map:
        expected = getattr(result_enum, member_name, None) if result_enum is not None else None
        if expected is not None:
            try:
                if result == expected:
                    return choice
            except Exception:
                pass
    result_text = _safe_text(result).strip().lower()
    if result_text:
        if "commandlink1" in result_text:
            return result_map[0][0]
        for choice, member_name in result_map[1:]:
            if member_name.lower() in result_text:
                return choice
        if "cancel" in result_text:
            return "cancel"
    return "cancel"


def _is_close_command_result(result, expected_result=None, command_link_id=None):
    """Return True only for the first TaskDialog command link.

    Revit returns ``TaskDialogResult.CommandLink1`` from ``Show`` while the
    command link was registered with ``TaskDialogCommandLinkId.CommandLink1``.
    Keep a textual fallback for older Python.NET enum wrappers, but never
    treat CommandLink2 or the common Cancel button as a close request.
    """
    for expected in (expected_result,):
        if expected is None:
            continue
        try:
            if result == expected:
                return True
        except Exception:
            pass

    result_text = _safe_text(result).strip().lower()
    if not result_text or "commandlink2" in result_text:
        return False
    if "cancel" in result_text:
        return False
    if "commandlink1" in result_text:
        return True

    # Some test/runtime wrappers expose only a comparable command-link id.
    if command_link_id is not None:
        try:
            return result == command_link_id
        except Exception:
            return False
    return False


def _post_close_once(uiapp, state, token, record):
    if not token:
        return False

    guards = state.setdefault("repost_guards", {})
    current = guards.get(token)
    if isinstance(current, dict) and time.time() <= float(current.get("expires_at", 0.0) or 0.0):
        _log("CloseRepostSkippedDuplicate token={0}".format(token))
        return False

    target_doc = _find_doc_by_identity(
        uiapp,
        doc_key=(record or {}).get("doc_key") if isinstance(record, dict) else "",
        doc_runtime_id=(record or {}).get("doc_runtime_id") if isinstance(record, dict) else None,
    )
    active_doc = _get_active_document(uiapp)
    target_key = _safe_text((record or {}).get("doc_key")) if isinstance(record, dict) else ""
    target_runtime = _to_int((record or {}).get("doc_runtime_id")) if isinstance(record, dict) else None
    if target_doc is None and (target_key or target_runtime is not None):
        _log("CloseRepostSkippedUnavailableTarget token={0}".format(token))
        return False
    if target_doc is not None:
        if active_doc is None or not _same_document(target_doc, active_doc):
            _log("CloseRepostSkippedInactiveTarget token={0}".format(token))
            return False

    UI = _get_ui()
    if UI is None:
        _log("CloseRepostSkippedNoUiApi token={0}".format(token))
        return False

    command_id = None
    command_name = "PostableCommand.Close"
    try:
        try:
            from Autodesk.Revit.UI import PostableCommand

            close_member = PostableCommand.Close
        except Exception:
            postable = getattr(UI, "PostableCommand", None)
            close_member = getattr(postable, "Close", None) if postable is not None else None
        lookup = getattr(UI.RevitCommandId, "LookupPostableCommandId", None)
        if close_member is None or not callable(lookup):
            _log("CloseRepostUnavailableNoPostableClose token={0}".format(token))
            return False
        command_id = lookup(close_member)
        if command_id is None:
            _log("CloseRepostUnavailableNoCommandId token={0}".format(token))
            return False
    except Exception as ex:
        _log("CloseRepostLookupFailed token={0} {1}".format(token, _exception_text(ex)))
        return False

    try:
        can_post = getattr(uiapp, "CanPostCommand", None)
        if not callable(can_post) or not bool(can_post(command_id)):
            _log("CloseRepostUnavailableCannotPost token={0}".format(token))
            return False
    except Exception as ex:
        _log("CloseRepostCanPostFailed token={0} {1}".format(token, _exception_text(ex)))
        return False

    generation = 0
    if isinstance(record, dict):
        generation = _to_int(record.get("generation")) or 0
    guards[token] = {
        "doc_key": _safe_text((record or {}).get("doc_key")) if isinstance(record, dict) else "",
        "doc_runtime_id": _to_int((record or {}).get("doc_runtime_id")) if isinstance(record, dict) else None,
        "generation": generation,
        "command_name": command_name,
        "posted_at": time.time(),
        "expires_at": time.time() + REPOST_GUARD_SEC,
    }
    try:
        uiapp.PostCommand(command_id)
        return True
    except Exception as ex:
        guards.pop(token, None)
        _log("CloseRepostPostCommandFailed token={0} {1}".format(token, _exception_text(ex)))
        return False


def _begin_commit_close(uiapp, state, token, record, action):
    """Post Save or Synchronize and wait for its completion event."""
    if action not in ("save", "sync") or not token:
        return False
    record = record if isinstance(record, dict) else {}
    if action == "sync" and not bool(record.get("doc_is_workshared")):
        _log("TempPhaseCommitUnavailableNotWorkshared token={0}".format(token))
        return False

    existing = (state.setdefault("commit_operations", {})).get(token)
    if isinstance(existing, dict):
        _log("TempPhaseCommitSkippedDuplicate action={0} token={1}".format(action, token))
        return True

    target_doc = _find_doc_by_identity(
        uiapp,
        doc_key=record.get("doc_key"),
        doc_runtime_id=record.get("doc_runtime_id"),
    )
    active_doc = _get_active_document(uiapp)
    if target_doc is None and (record.get("doc_key") or _to_int(record.get("doc_runtime_id")) is not None):
        _log("TempPhaseCommitSkippedUnavailableTarget action={0} token={1}".format(action, token))
        return False
    if target_doc is not None and (active_doc is None or not _same_document(target_doc, active_doc)):
        _log("TempPhaseCommitSkippedInactiveTarget action={0} token={1}".format(action, token))
        return False

    UI = _get_ui()
    if UI is None:
        _log("TempPhaseCommitUnavailableNoUi action={0} token={1}".format(action, token))
        return False
    try:
        try:
            from Autodesk.Revit.UI import PostableCommand
        except Exception:
            PostableCommand = getattr(UI, "PostableCommand", None)
        if PostableCommand is None:
            _log("TempPhaseCommitUnavailableNoPostableCommand action={0} token={1}".format(action, token))
            return False
        member_name = "SynchronizeAndModifySettings" if action == "sync" else "Save"
        command = getattr(PostableCommand, member_name, None)
        command_id_lookup = getattr(getattr(UI, "RevitCommandId", None), "LookupPostableCommandId", None)
        if command is None or not callable(command_id_lookup):
            _log("TempPhaseCommitUnavailableNoCommand action={0} token={1}".format(action, token))
            return False
        command_id = command_id_lookup(command)
        if command_id is None:
            _log("TempPhaseCommitUnavailableNoCommandId action={0} token={1}".format(action, token))
            return False
        can_post = getattr(uiapp, "CanPostCommand", None)
        if not callable(can_post) or not bool(can_post(command_id)):
            _log("TempPhaseCommitUnavailableCannotPost action={0} token={1}".format(action, token))
            return False
    except Exception as ex:
        _log("TempPhaseCommitLookupFailed action={0} token={1} {2}".format(action, token, _exception_text(ex)))
        return False

    operation = {
        "action": action,
        "doc_key": _safe_text(record.get("doc_key")),
        "doc_runtime_id": _to_int(record.get("doc_runtime_id")),
        "title": _safe_text(record.get("title")),
        "generation": _to_int(record.get("generation")) or 0,
        "requested_at": time.time(),
        "expires_at": time.time() + COMMIT_GUARD_SEC,
        "command_name": "PostableCommand.{0}".format(member_name),
    }
    state["commit_operations"][token] = operation
    try:
        uiapp.PostCommand(command_id)
    except Exception as ex:
        state["commit_operations"].pop(token, None)
        _log("TempPhaseCommitPostFailed action={0} token={1} {2}".format(action, token, _exception_text(ex)))
        return False
    _save_state(state)
    _log(
        "TempPhaseCommitPostRequested action={0} command={1} token={2}".format(
            action,
            member_name,
            token,
        )
    )
    return True


def _completion_succeeded(status):
    value = _safe_text(status).upper()
    return value in ("SUCCEEDED", "SUCCESS", "SUCCEED", "COMPLETED", "COMPLETE", "OK")


def _commit_failure_message(action, status):
    status_text = _safe_text(status).strip()
    suffix = " ({0})".format(status_text) if status_text else ""
    if action == "sync":
        return (
            "Synchronization with central was cancelled or failed{0}. The restored "
            "document remains open and central was not updated.".format(suffix)
        )
    return (
        "The restored file could not be saved{0}. The restored document remains open; "
        "the temporary state was not committed.".format(suffix)
    )


def _attach_completion(app, event_name, handler):
    if event_name == "DocumentSaved":
        app.DocumentSaved += handler
    elif event_name == "DocumentSavedAs":
        app.DocumentSavedAs += handler
    elif event_name == "DocumentSynchronizedWithCentral":
        app.DocumentSynchronizedWithCentral += handler


def _detach_completion(app, event_name, handler):
    try:
        if event_name == "DocumentSaved":
            app.DocumentSaved -= handler
        elif event_name == "DocumentSavedAs":
            app.DocumentSavedAs -= handler
        elif event_name == "DocumentSynchronizedWithCentral":
            app.DocumentSynchronizedWithCentral -= handler
    except Exception:
        pass


def _uninstall_completion_registration(registration):
    app = registration.get("app") if isinstance(registration, dict) else None
    for event_name, handler in list((registration.get("handlers") or {}).items() if isinstance(registration, dict) else []):
        _detach_completion(app, event_name, handler)


def _queue_pending_close(state, token, doc_key, doc_runtime_id, title, summary=None):
    pending = state.setdefault("pending_closes", {})
    existing = pending.get(token)
    if isinstance(existing, dict):
        # Coalesce duplicate DocumentClosing events for the same document.
        existing["last_seen_at"] = time.time()
        return

    generation = (_to_int(state.get("next_generation")) or 0) + 1
    state["next_generation"] = generation
    summary = summary if isinstance(summary, dict) else {}
    pending[token] = {
        "doc_key": _safe_text(doc_key),
        "doc_runtime_id": _to_int(doc_runtime_id),
        "title": _safe_text(title),
        "generation": generation,
        "queued_at": time.time(),
        "last_seen_at": time.time(),
        "attempts": 0,
        "next_try_at": time.time() + 0.05,
        "doc_is_workshared": bool(summary.get("doc_is_workshared")),
        "tracked_restore_count": _to_int(summary.get("tracked_restore_count")) or len(summary.get("tracked_restore_views") or []),
        "untracked_tvp_count": _to_int(summary.get("untracked_tvp_count")) or len(summary.get("untracked_tvp_views") or []),
    }


def _remember_closing_identity(
    state,
    token,
    doc,
    event_args,
    doc_key,
    doc_runtime_id,
    title,
):
    """Store the identities needed to match the later DocumentClosed event."""
    if not isinstance(state, dict) or not token or token == "unknown":
        return None
    records = state.setdefault("closing_identities", {})
    if not isinstance(records, dict):
        records = {}
        state["closing_identities"] = records
    event_document_id = _event_document_id(event_args)
    runtime_identity = _get_doc_runtime_id(doc)
    if runtime_identity is None:
        runtime_identity = _to_int(doc_runtime_id)
    stable_key = _safe_text(doc_key).strip()
    stable_path = _doc_path(doc)
    current = records.get(token)
    if isinstance(current, dict):
        # Preserve the first stable identity but refresh the observation time
        # and fill in fields that were unavailable on an earlier hook call.
        if stable_key and not current.get("doc_key"):
            current["doc_key"] = stable_key
        if stable_path and not current.get("doc_path"):
            current["doc_path"] = stable_path
        if event_document_id is not None:
            current["event_document_id"] = event_document_id
        if runtime_identity is not None:
            current["doc_runtime_id"] = runtime_identity
        if title and not current.get("title"):
            current["title"] = _safe_text(title)
        current["last_seen_at"] = time.time()
        current["expires_at"] = time.time() + CLOSING_IDENTITY_GUARD_SEC
        return current

    records[token] = {
        "token": token,
        "doc_key": stable_key,
        "doc_path": stable_path,
        "event_document_id": event_document_id,
        "doc_runtime_id": runtime_identity,
        "title": _safe_text(title),
        "recorded_at": time.time(),
        "last_seen_at": time.time(),
        "expires_at": time.time() + CLOSING_IDENTITY_GUARD_SEC,
    }
    _log(
        "DocClosingIdentityRecorded token={0} docKey={1} eventDocumentId={2} "
        "runtimeId={3}".format(
            token,
            stable_key,
            "" if event_document_id is None else event_document_id,
            "" if runtime_identity is None else runtime_identity,
        )
    )
    return records[token]


def _find_closing_identity_records(
    state,
    token=None,
    doc_key="",
    event_document_id=None,
    doc_runtime_id=None,
):
    records = state.get("closing_identities") if isinstance(state, dict) else None
    if not isinstance(records, dict):
        return []
    matches = []
    for identity_key, record in list(records.items()):
        if not isinstance(record, dict):
            continue
        if _closing_identity_matches(
            identity_key,
            record,
            token=token,
            doc_key=doc_key,
            event_document_id=event_document_id,
            doc_runtime_id=doc_runtime_id,
        ):
            matches.append((identity_key, record))
    return matches


def _closing_identity_matches(
    identity_key,
    record,
    token=None,
    doc_key="",
    event_document_id=None,
    doc_runtime_id=None,
):
    if token and (identity_key == token or _safe_text(record.get("token")) == token):
        return True
    if doc_key and _safe_text(record.get("doc_key")).strip() == _safe_text(doc_key).strip():
        return True
    event_document_id = _to_int(event_document_id)
    if event_document_id is not None:
        if event_document_id == _to_int(record.get("event_document_id")):
            return True
        if event_document_id == _to_int(record.get("doc_runtime_id")):
            return True
    doc_runtime_id = _to_int(doc_runtime_id)
    if doc_runtime_id is not None:
        if doc_runtime_id == _to_int(record.get("doc_runtime_id")):
            return True
        if doc_runtime_id == _to_int(record.get("event_document_id")):
            return True
    return False


def _remove_closing_identity_records(state, matches):
    records = state.get("closing_identities") if isinstance(state, dict) else None
    if not isinstance(records, dict):
        return 0
    removed = 0
    for identity_key, unused_record in list(matches or []):
        if identity_key in records:
            records.pop(identity_key, None)
            removed += 1
    if removed:
        _log("DocClosingIdentityHandoffConsumed count={0}".format(removed))
    return removed


def _pop_close_records_for_identity(mapping, close_tokens, identity_pairs, map_name=""):
    """Pop close-operation records matching any resolved identity."""
    if not isinstance(mapping, dict):
        return []
    popped = []
    for key, record in list(mapping.items()):
        if key in (close_tokens or set()) or _close_record_matches_identities(
            record,
            identity_pairs,
        ):
            mapping.pop(key, None)
            if isinstance(record, dict):
                popped.append(record)
    if popped and map_name:
        _log("DocClosedOperationRecordsCleared map={0} count={1}".format(map_name, len(popped)))
    return popped


def _close_record_matches_identities(record, identity_pairs):
    if not isinstance(record, dict):
        return False
    stored_key = _safe_text(record.get("doc_key")).strip()
    stored_runtime = _to_int(record.get("doc_runtime_id"))
    stored_event = _to_int(record.get("event_document_id"))
    for identity_key, identity_runtime in identity_pairs or []:
        identity_key = _safe_text(identity_key).strip()
        identity_runtime = _to_int(identity_runtime)
        if identity_key and stored_key and identity_key == stored_key:
            return True
        if identity_runtime is not None:
            if stored_runtime is not None and identity_runtime == stored_runtime:
                return True
            if stored_event is not None and identity_runtime == stored_event:
                return True
    return False


def _unique_identity_pairs(pairs):
    result = []
    seen = set()
    for pair in pairs or []:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        key = _safe_text(pair[0]).strip()
        runtime = _to_int(pair[1])
        marker = (key, runtime)
        if not key and runtime is None:
            continue
        if marker in seen:
            continue
        seen.add(marker)
        result.append((key, runtime))
    return result


def _retry_pending_close(record, now):
    attempts = (_to_int(record.get("attempts")) or 0) + 1
    record["attempts"] = attempts
    if attempts >= MAX_RESTORE_ATTEMPTS:
        return False
    record["next_try_at"] = now + RETRY_DELAY_SEC
    return True


def _consume_repost_guard(state, token):
    guard = (state.get("repost_guards") or {}).get(token)
    if not isinstance(guard, dict):
        return False
    expires_at = float(guard.get("expires_at", 0.0) or 0.0)
    if time.time() > expires_at:
        state.get("repost_guards", {}).pop(token, None)
        return False
    state.get("repost_guards", {}).pop(token, None)
    return True


def _expire_repost_guards(state):
    now = time.time()
    _expire_closing_identities(state, now=now)
    for token, guard in list((state.get("repost_guards") or {}).items()):
        if not isinstance(guard, dict) or now > float(guard.get("expires_at", 0.0) or 0.0):
            state.get("repost_guards", {}).pop(token, None)
    for token, operation in list((state.get("commit_operations") or {}).items()):
        if not isinstance(operation, dict) or now > float(operation.get("expires_at", 0.0) or 0.0):
            state.get("commit_operations", {}).pop(token, None)
            action = _safe_text(operation.get("action")) if isinstance(operation, dict) else "save"
            state.setdefault("commit_failures", {})[token] = {
                "action": action,
                "status": "TIMEOUT",
                "message": _commit_failure_message(
                    action,
                    "TIMEOUT",
                ),
            }
            _log("TempPhaseCommitFailed action={0} token={1} status=TIMEOUT".format(action, token))
    for token, pending in list((state.get("pending_commit_closes") or {}).items()):
        if not isinstance(pending, dict) or now > float(pending.get("expires_at", 0.0) or 0.0):
            state.get("pending_commit_closes", {}).pop(token, None)


def _expire_closing_identities(state, now=None):
    records = state.get("closing_identities") if isinstance(state, dict) else None
    if not isinstance(records, dict):
        return 0
    now = time.time() if now is None else now
    removed = 0
    for token, record in list(records.items()):
        expires_at = _to_float(record.get("expires_at")) if isinstance(record, dict) else None
        if (
            not isinstance(record, dict)
            or expires_at is None
            or now > expires_at
        ):
            records.pop(token, None)
            removed += 1
            _log("TempPhaseClosingIdentityExpired token={0}".format(token))
    return removed


def _get_state():
    if script is not None:
        try:
            state = script.get_envvar(STATE_ENVVAR)
        except Exception:
            state = None
    else:
        state = _MEMORY_STATE.get(STATE_ENVVAR)

    if not isinstance(state, dict):
        state = {}
    state.setdefault("pending_closes", {})
    state.setdefault("repost_guards", {})
    state.setdefault("commit_operations", {})
    state.setdefault("pending_commit_closes", {})
    state.setdefault("commit_failures", {})
    state.setdefault("closing_identities", {})
    state.setdefault("next_generation", 0)
    state.setdefault("last_idling_at", 0.0)
    if not isinstance(state.get("pending_closes"), dict):
        state["pending_closes"] = {}
    if not isinstance(state.get("repost_guards"), dict):
        state["repost_guards"] = {}
    for key in (
        "commit_operations",
        "pending_commit_closes",
        "commit_failures",
        "closing_identities",
    ):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def _save_state(state):
    if script is not None:
        try:
            script.set_envvar(STATE_ENVVAR, state)
            return
        except Exception as ex:
            _log("CloseStateSaveFailed {0}".format(_exception_text(ex)))
    _MEMORY_STATE[STATE_ENVVAR] = state


def _get_view_state():
    if temp_phase_view is not None:
        try:
            state = temp_phase_view._get_state()
            if isinstance(state, dict):
                return state
        except Exception:
            pass

    if script is not None:
        try:
            state = script.get_envvar(VIEW_STATE_ENVVAR)
        except Exception:
            state = None
    else:
        state = _MEMORY_STATE.get(VIEW_STATE_ENVVAR)
    if not isinstance(state, dict):
        state = {}
    state.setdefault("last_seen_tvp", {})
    state.setdefault("view_sessions", {})
    state.setdefault("armed_documents", {})
    if not isinstance(state.get("last_seen_tvp"), dict):
        state["last_seen_tvp"] = {}
    if not isinstance(state.get("view_sessions"), dict):
        state["view_sessions"] = {}
    if not isinstance(state.get("armed_documents"), dict):
        state["armed_documents"] = {}
    return state


def _save_view_state(state):
    if temp_phase_view is not None:
        try:
            temp_phase_view._save_state(state)
            return
        except Exception:
            pass
    if script is not None:
        try:
            script.set_envvar(VIEW_STATE_ENVVAR, state)
            return
        except Exception as ex:
            _log("ViewStateSaveFailed {0}".format(_exception_text(ex)))
    _MEMORY_STATE[VIEW_STATE_ENVVAR] = state


def _drop_doc_sessions(view_state, doc_key, doc_runtime_id):
    return _drop_doc_sessions_for_identities(
        view_state,
        [(doc_key, doc_runtime_id)],
    )


def _drop_doc_sessions_for_identities(view_state, identity_pairs):
    sessions = view_state.get("view_sessions") or {}
    last_seen = view_state.get("last_seen_tvp") or {}
    armed_documents = view_state.get("armed_documents") or {}
    removed_armed = 0
    removed_sessions = 0
    identity_pairs = _unique_identity_pairs(identity_pairs)
    for view_key, session in list(sessions.items()):
        if any(
            _session_matches_doc(session, identity_key, identity_runtime)
            for identity_key, identity_runtime in identity_pairs
        ):
            sessions.pop(view_key, None)
            last_seen.pop(view_key, None)
            removed_sessions += 1
    # ``last_seen_tvp`` can contain an observation without a corresponding
    # session (for example, after an earlier restore).  Stable document keys
    # let us clear those entries as well without touching another document.
    stable_keys = set(
        _safe_text(identity_key).strip()
        for identity_key, unused_runtime in identity_pairs
        if _safe_text(identity_key).strip()
    )
    for view_key in list(last_seen):
        if any(
            view_key == stable_key or view_key.startswith(stable_key + "|")
            for stable_key in stable_keys
        ):
            last_seen.pop(view_key, None)
    for identity, record in list(armed_documents.items()):
        if any(
            _session_matches_doc(record, identity_key, identity_runtime)
            for identity_key, identity_runtime in identity_pairs
        ):
            armed_documents.pop(identity, None)
            removed_armed += 1
    _save_view_state(view_state)
    if removed_armed or removed_sessions:
        _log(
            "PythonTempPhaseDocumentTriggerCleared docKey={0} runtimeId={1} "
            "sessions={2} armed={3}".format(
                identity_pairs[0][0] if identity_pairs else "",
                "" if not identity_pairs or identity_pairs[0][1] is None else identity_pairs[0][1],
                removed_sessions,
                removed_armed,
            )
        )
    return removed_sessions + removed_armed


def _is_document_armed(view_state, doc_key, doc_runtime_id):
    """Return whether close recovery is enabled for this document.

    Existing tracked sessions are treated as an implicit arm so upgrades do
    not strand a Temp Phase session created before ``armed_documents`` was
    introduced.  New untracked TVP discovery is gated by the explicit arm.
    """
    if not isinstance(view_state, dict):
        return False

    tracked = _has_tracked_sessions_for_identity(
        view_state,
        doc_key=doc_key,
        doc_runtime_id=doc_runtime_id,
    )
    changed = False
    expected_schema = _arm_schema_version()
    current_process_id = _get_revit_process_id()
    armed_documents = view_state.get("armed_documents") or {}
    stale_target_arm = False
    for identity, record in list(armed_documents.items()):
        if not isinstance(record, dict):
            armed_documents.pop(identity, None)
            changed = True
            _log("TempPhaseArmStaleRemoved identity={0} reason=invalid_record".format(identity))
            continue

        schema = _to_int(record.get("arm_schema_version"))
        process_id = _to_int(record.get("revit_process_id"))
        record_matches = _session_matches_doc(record, doc_key, doc_runtime_id)

        # Records written before schema/process metadata was introduced are
        # safe only when a live tracked view session still proves that the
        # user used Temp Phase in this document.
        if schema is None or process_id is None:
            legacy_session = _has_tracked_sessions_for_identity(
                view_state,
                doc_key=_safe_text(record.get("doc_key")),
                doc_runtime_id=_to_int(record.get("doc_runtime_id")),
            )
            if record_matches and legacy_session:
                _log("TempPhaseArmLegacyAccepted docKey={0}".format(doc_key))
                return True
            if not legacy_session:
                armed_documents.pop(identity, None)
                changed = True
                _log(
                    "TempPhaseArmStaleRemoved identity={0} reason=legacy_without_session".format(
                        identity
                    )
                )
            continue

        if schema != expected_schema:
            if record_matches:
                stale_target_arm = True
            armed_documents.pop(identity, None)
            changed = True
            _log(
                "TempPhaseArmStaleRemoved identity={0} reason=schema expected={1} actual={2}".format(
                    identity,
                    expected_schema,
                    schema,
                )
            )
            continue
        if current_process_id is not None and process_id != current_process_id:
            if record_matches:
                stale_target_arm = True
            armed_documents.pop(identity, None)
            changed = True
            _log(
                "TempPhaseArmStaleRemoved identity={0} reason=process expected={1} actual={2}".format(
                    identity,
                    current_process_id,
                    process_id,
                )
            )
            continue
        if record_matches:
            if changed:
                _save_view_state(view_state)
            return True

    if changed:
        _save_view_state(view_state)

    # Existing tracked sessions remain a backward-compatible arm.  This is
    # independent of a stale explicit arm record and ensures an in-progress
    # session is not stranded by a pyRevit reload.
    if tracked and not stale_target_arm:
        _log("TempPhaseArmTrackedSessionFallback docKey={0}".format(doc_key))
        return True
    return False


def _has_tracked_sessions_for_identity(view_state, doc_key, doc_runtime_id):
    for session in (view_state.get("view_sessions") or {}).values():
        if _session_matches_doc(session, doc_key, doc_runtime_id):
            return True
    return False


def _session_matches_doc(session, doc_key, doc_runtime_id):
    if not isinstance(session, dict):
        return False
    stored_runtime = _to_int(session.get("doc_runtime_id"))
    target_runtime = _to_int(doc_runtime_id)
    if stored_runtime is not None and target_runtime is not None:
        if stored_runtime == target_runtime:
            return True
        # Revit's DocumentClosingEventArgs.DocumentId and a Document's
        # runtime hash are not guaranteed to be the same identity.  Fall
        # through to the stable path/memory key when both are available.
    stored_key = _safe_text(session.get("doc_key")).strip()
    target_key = _safe_text(doc_key).strip()
    return bool(stored_key and target_key and stored_key == target_key)


def _find_doc_by_identity(uiapp, doc_key, doc_runtime_id):
    if uiapp is None:
        return None
    app = getattr(uiapp, "Application", None)
    if app is None and hasattr(uiapp, "Documents"):
        app = uiapp
    docs = getattr(app, "Documents", None) if app is not None else None
    if docs is None:
        return None
    target_runtime = _to_int(doc_runtime_id)
    target_key = _safe_text(doc_key).strip()
    try:
        for doc in docs:
            if target_runtime is not None and _get_doc_runtime_id(doc) == target_runtime:
                return doc
            if target_key and _doc_key(doc) == target_key:
                return doc
    except Exception:
        return None
    return None


def _collect_document_views(doc):
    DB = _get_db()
    if DB is None or not _is_doc_valid(doc):
        return []
    result = []
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.View)
        try:
            collector = collector.WhereElementIsNotElementType()
        except Exception:
            pass
        for view in collector:
            if _is_view_valid(view):
                result.append(view)
    except Exception as ex:
        _log("TvpDiscoveryFailed {0}".format(_exception_text(ex)))
    return result


def _collect_discoverable_tvp_views(doc):
    """Compatibility helper returning only currently active TVP views."""
    result = []
    for view in _collect_document_views(doc):
        if _is_tvp_active(view):
            result.append(view)
    return result


def _get_doc_element(doc, view_id):
    element_id = _int_to_eid(view_id)
    try:
        element = doc.GetElement(element_id)
        if element is not None:
            return element
    except Exception:
        pass
    # Keep the helper usable in lightweight tests and in hosts exposing an
    # integer overload; Revit itself normally takes an ElementId above.
    try:
        return doc.GetElement(view_id)
    except Exception:
        return None


def _is_tvp_active(view):
    if temp_phase_view is not None:
        try:
            return bool(temp_phase_view._is_tvp_active(view))
        except Exception:
            pass
    DB = _get_db()
    if DB is None or not _is_view_valid(view):
        return False
    try:
        return bool(view.IsTemporaryViewModeEnabled(DB.TemporaryViewMode.TemporaryViewProperties))
    except Exception:
        pass
    try:
        legacy = getattr(view, "IsTemporaryViewPropertiesModeEnabled", None)
        return bool(legacy()) if callable(legacy) else False
    except Exception:
        return False


def _disable_tvp(view):
    if temp_phase_view is not None:
        try:
            return bool(temp_phase_view._disable_tvp(view))
        except Exception:
            pass
    DB = _get_db()
    if DB is None or not _is_view_valid(view):
        return False
    try:
        view.DisableTemporaryViewMode(DB.TemporaryViewMode.TemporaryViewProperties)
        return True
    except Exception:
        return False


def _set_view_phase_id(view, phase_id):
    if temp_phase_view is not None:
        try:
            return bool(temp_phase_view._set_view_phase_id(view, phase_id))
        except Exception:
            pass
    DB = _get_db()
    if DB is None or phase_id is None or not _is_view_valid(view):
        return False
    try:
        parameter = view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
        return bool(parameter and parameter.Set(_int_to_eid(phase_id)))
    except Exception:
        return False


def _event_is_cancellable(event_args):
    value = _getattr_safe(event_args, "Cancellable")
    if value is None:
        return True
    try:
        return bool(value)
    except Exception:
        return False


def _try_cancel_event(event_args):
    try:
        cancel_method = getattr(event_args, "Cancel", None)
        if callable(cancel_method):
            cancel_method()
            return True
    except Exception as ex:
        _log("DocClosingCancelMethodFailed {0}".format(_exception_text(ex)))
    try:
        event_args.Cancel = True
        return True
    except Exception as ex:
        _log("DocClosingCancelPropertyFailed {0}".format(_exception_text(ex)))
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
    runtime_id = _eid_to_int(_getattr_safe(event_args, "DocumentId"))
    if runtime_id is not None:
        return _find_doc_by_identity(uiapp, "", runtime_id)
    return None


def _extract_doc_runtime_id(event_args, doc):
    # Prefer the event's DocumentId.  It is the identity that DocumentClosed
    # exposes later and therefore lets pending/guard state be cleared even
    # when Revit has released the Document object.
    event_runtime_id = _eid_to_int(_getattr_safe(event_args, "DocumentId"))
    if event_runtime_id is not None:
        return event_runtime_id
    if _is_doc_valid(doc):
        value = _get_doc_runtime_id(doc)
        if value is not None:
            return value
    return None


def _event_document_id(event_args):
    """Return the Revit DocumentId carried by an event, when available."""
    return _eid_to_int(_getattr_safe(event_args, "DocumentId"))


def _event_status(event_args):
    value = _getattr_safe(event_args, "Status")
    text = _safe_text(value).upper()
    if "CANCEL" in text:
        return "CANCELLED"
    if "FAIL" in text:
        return "FAILED"
    if "SUCCESS" in text or "SUCCEED" in text:
        return "SUCCEEDED"
    return text


def _normalize_event_call(uiapp, event_args):
    if event_args is None and uiapp is not None:
        # This form is useful for tests that pass only event args.  Revit
        # senders expose Application/Documents, while event args expose
        # Document/DocumentId/Cancel.
        if hasattr(uiapp, "Document") or hasattr(uiapp, "DocumentId") or hasattr(uiapp, "Cancel"):
            return None, uiapp
    return uiapp, event_args


def _get_uiapp(uiapp=None):
    if uiapp is not None and hasattr(uiapp, "ActiveUIDocument"):
        return uiapp
    try:
        return __revit__
    except Exception:
        return uiapp if uiapp is not None and hasattr(uiapp, "Application") else None


def _is_shutdown_context(uiapp):
    app = getattr(uiapp, "Application", None) if uiapp is not None else None
    for name in ("IsClosing", "IsShuttingDown", "IsQuitting", "IsShutdown"):
        try:
            if bool(getattr(app, name)):
                return True
        except Exception:
            pass
    return False


def _get_active_document(uiapp):
    uidoc = getattr(uiapp, "ActiveUIDocument", None) if uiapp is not None else None
    return getattr(uidoc, "Document", None) if uidoc is not None else None


def _same_document(left, right):
    if left is right:
        return True
    left_runtime = _get_doc_runtime_id(left)
    right_runtime = _get_doc_runtime_id(right)
    if left_runtime is not None and right_runtime is not None:
        return left_runtime == right_runtime
    left_key = _doc_key(left)
    return bool(left_key and left_key == _doc_key(right))


def _is_doc_supported(doc):
    if temp_phase_view is not None:
        try:
            return bool(temp_phase_view._is_doc_supported(doc))
        except Exception:
            pass
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


def _doc_is_workshared(doc):
    if not _is_doc_valid(doc):
        return False
    try:
        return bool(doc.IsWorkshared)
    except Exception:
        return False


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


def _doc_key(doc):
    if temp_phase_view is not None:
        try:
            return _safe_text(temp_phase_view._doc_key(doc))
        except Exception:
            pass
    if not _is_doc_valid(doc):
        return ""
    try:
        path = _safe_text(doc.PathName).strip()
    except Exception:
        path = ""
    if path:
        return "path|{0}".format(path.lower())
    try:
        hash_code = _safe_text(doc.GetHashCode())
    except Exception:
        hash_code = _safe_text(id(doc))
    return "mem|{0}|{1}".format(_safe_text(getattr(doc, "Title", "")).lower(), hash_code)


def _doc_path(doc):
    if not _is_doc_valid(doc):
        return ""
    try:
        return _safe_text(doc.PathName).strip()
    except Exception:
        return ""


def _get_doc_runtime_id(doc):
    if not _is_doc_valid(doc):
        return None
    for attr_name in ("DocumentId", "Id"):
        try:
            value = _eid_to_int(getattr(doc, attr_name))
            if value is not None:
                return value
        except Exception:
            pass
    try:
        return _to_int(doc.GetHashCode())
    except Exception:
        return None


def _get_revit_process_id():
    try:
        return int(os.getpid())
    except Exception:
        return None


def _arm_schema_version():
    try:
        value = _to_int(getattr(temp_phase_view, "ARM_SCHEMA_VERSION", None))
        if value is not None:
            return value
    except Exception:
        pass
    # Version 2 is the first format that includes process and schema
    # metadata.  Keeping the fallback here lets the close hook run while the
    # command module is being reloaded, without accepting older records.
    return 2


def _identity_token(doc_key, doc_runtime_id):
    runtime = _to_int(doc_runtime_id)
    if runtime is not None:
        return "id|{0}".format(runtime)
    key = _safe_text(doc_key).strip()
    return "key|{0}".format(key) if key else "unknown"


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


def _view_name(view, session):
    try:
        name = _safe_text(view.Name).strip()
    except Exception:
        name = ""
    if name:
        return name
    return _safe_text((session or {}).get("view_name"))


def _doc_title(doc):
    try:
        return _safe_text(doc.Title).strip()
    except Exception:
        return ""


def _eid_to_int(element_id):
    if element_id is None:
        return None
    for attr_name in ("IntegerValue", "Value"):
        try:
            value = int(getattr(element_id, attr_name))
            return value
        except Exception:
            pass
    return _to_int(element_id)


def _int_to_eid(value):
    DB = _get_db()
    if DB is None or value is None:
        return None
    try:
        value = int(value)
    except Exception:
        return None
    # Keep the plain-int constructor first: it is the exact call this module
    # has always made and binds ElementId(Int32) on Revit 2015-2025 unchanged.
    try:
        return DB.ElementId(value)
    except Exception:
        pass
    # Revit 2026 removed ElementId(Int32), so the plain call above raises
    # "Multiple targets could match".  Retry with an explicit Int64, which
    # binds the ElementId(Int64) constructor introduced in Revit 2024.
    try:
        import System

        return DB.ElementId(System.Int64(value))
    except Exception:
        return None


def _rollback_transaction(transaction):
    try:
        transaction.RollBack()
    except Exception:
        pass


def _show_alert(title, message):
    UI = _get_ui()
    if UI is not None:
        try:
            UI.TaskDialog.Show(title, message)
            return
        except Exception:
            pass
    try:
        print("[{0}] {1}".format(title, message))
    except Exception:
        pass


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        try:
            return value.ToString()
        except Exception:
            return ""


def _to_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _getattr_safe(obj, path):
    current = obj
    for part in _safe_text(path).split("."):
        if not part:
            continue
        try:
            current = getattr(current, part)
        except Exception:
            return None
    return current


def _exception_text(exception):
    if exception is None:
        return ""
    text = "{0}: {1}".format(exception.__class__.__name__, _safe_text(exception))
    inner = getattr(exception, "InnerException", None)
    if inner is not None:
        text += " | Inner: {0}".format(_exception_text(inner))
    return text


def _log(message):
    """Forward a diagnostic marker to pyRevit's logger.

    The legacy per-event file log under ``%APPDATA%\\EasyBIM`` grew without
    bound and paid a file open/append on every hook event — including every
    Idling tick — so it was removed.  Markers stay visible through pyRevit's
    debug logging.
    """
    try:
        LOGGER.debug(_safe_text(message))
    except Exception:
        pass


def log_hook_exception(stage, exception):
    """Public safe logger for thin pyRevit hook wrappers."""
    _log("{0} {1}".format(_safe_text(stage), _exception_text(exception)))


def log_hook_context(hook_name, script_path):
    """Record the physical hook path to identify stale extension copies."""
    _log(
        "{0}HookContext scriptPath={1} modulePath={2}".format(
            _safe_text(hook_name),
            _safe_text(script_path),
            _safe_text(__file__),
        )
    )
