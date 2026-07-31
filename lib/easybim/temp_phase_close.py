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
import time

try:
    from pyrevit import script
except Exception:
    script = None

try:
    from easybim import temp_phase_view
except Exception:
    temp_phase_view = None


TITLE = "Temp Phase"
STATE_ENVVAR = "EASYBIM_TEMP_PHASE_CLOSE_STATE"
VIEW_STATE_ENVVAR = "EASYBIM_TEMP_PHASE_VIEW_STATE"
MAX_RESTORE_ATTEMPTS = 8
RETRY_DELAY_SEC = 0.25
REPOST_GUARD_SEC = 12.0
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

    # A close reposted by this module is allowed through once.  This guard is
    # checked before discovery so a stale session cannot create a close loop.
    token = _identity_token(doc_key, doc_runtime_id)
    if _consume_repost_guard(state, token):
        _save_state(state)
        _log("DocClosingAllowedRepost token={0}".format(token))
        return False

    summary = None
    if _is_doc_valid(closing_doc) and not _is_doc_supported(closing_doc):
        _save_state(state)
        _log("DocClosingSkippedUnsupportedDocument token={0}".format(token))
        return False

    if _is_doc_valid(closing_doc):
        summary = collect_tvp_summary(
            uiapp=uiapp,
            doc=closing_doc,
            view_state=_get_view_state(),
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
    )
    _save_state(state)
    _log("DocClosingCancelSucceeded token={0}".format(token))
    _log("DocClosingCancelledQueued token={0}".format(token))
    return True


def handle_idling(uiapp=None, event_args=None):
    """Restore pending documents in a transaction and optionally repost close."""
    del event_args
    uiapp = _get_uiapp(uiapp)
    _log("AppIdlingHookEntry")
    if uiapp is None:
        return False

    state = _get_state()
    if _is_shutdown_context(uiapp):
        if state.get("pending_closes"):
            state["pending_closes"] = {}
            _save_state(state)
        _log("AppIdlingSkippedShutdown")
        return False
    pending = state.get("pending_closes")
    if not isinstance(pending, dict) or not pending:
        _expire_repost_guards(state)
        _save_state(state)
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

        decision = _show_close_decision(summary, record)
        pending.pop(token, None)
        changed = True

        if decision == "close":
            if _post_close_once(uiapp, state, token, record):
                _log("TempPhaseCloseReposted token={0}".format(token))
                _log("IdlingCloseReposted token={0}".format(token))
            else:
                _show_alert(
                    TITLE,
                    "Temporary view properties were restored, but Revit's Close command "
                    "could not be posted. You can close the file again manually.",
                )
                _log("IdlingCloseRepostFailed token={0}".format(token))
        else:
            _log("IdlingCloseCancelledByUser token={0}".format(token))

    _expire_repost_guards(state)
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
    del uiapp
    if event_args is None:
        _log("DocClosedSkippedMissingEventArgs")
        return False

    doc = _resolve_doc_from_event_args(event_args, None)
    doc_runtime_id = _extract_doc_runtime_id(event_args, doc)
    doc_key = _doc_key(doc)
    token = _identity_token(doc_key, doc_runtime_id)
    state = _get_state()
    status = _event_status(event_args)

    # A cancelled/failed close leaves the document open.  Preserve any
    # pending state so a subsequent close can be handled normally.
    if status in ("CANCELLED", "FAILED"):
        state.get("repost_guards", {}).pop(token, None)
        _save_state(state)
        _log("DocClosedNonSuccess status={0} token={1}".format(status, token))
        return False

    pending_record = state.get("pending_closes", {}).pop(token, None)
    repost_guard = state.get("repost_guards", {}).pop(token, None)
    stored_doc_key = ""
    stored_runtime_id = None
    for record in (pending_record, repost_guard):
        if not isinstance(record, dict):
            continue
        stored_doc_key = stored_doc_key or _safe_text(record.get("doc_key")).strip()
        if stored_runtime_id is None:
            stored_runtime_id = _to_int(record.get("doc_runtime_id"))
    _drop_doc_sessions(
        _get_view_state(),
        doc_key=doc_key or stored_doc_key,
        doc_runtime_id=doc_runtime_id if doc_runtime_id is not None else stored_runtime_id,
    )
    _save_state(state)
    _log("DocClosedCleared status={0} token={1}".format(status or "UNKNOWN", token))
    return True


# Explicit delegate-friendly names make direct ``startup.py`` subscriptions
# readable and avoid ambiguity about sender/event-args ordering.
def document_closing_handler(sender, args):
    return handle_doc_closing(uiapp=sender, event_args=args)


def idling_handler(sender, args):
    return handle_idling(uiapp=sender, event_args=args)


def document_closed_handler(sender, args):
    return handle_doc_closed(uiapp=sender, event_args=args)


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
        "tracked_restore_views": tracked,
        "untracked_tvp_views": untracked,
        "discoverable_tvp_views": discoverable,
        "dialog_view_lines": lines,
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
    """Show the post-restore close decision; return ``close`` or ``cancel``."""
    UI = _get_ui()
    title = _safe_text(record.get("title")) if isinstance(record, dict) else ""
    title = title or _safe_text(summary.get("doc_title")) or "the document"
    lines = list(summary.get("dialog_view_lines") or [])
    main_content = (
        "Temporary phase/view state was restored for {0}.\n\n"
        "The document remains open. What would you like to do?"
    ).format(title)
    expanded = "\n".join(lines)

    if UI is None:
        _show_alert(TITLE, main_content + "\n\nClose the document manually if needed.")
        return "cancel"

    try:
        dialog = UI.TaskDialog(TITLE)
        if hasattr(dialog, "TitleAutoPrefix"):
            dialog.TitleAutoPrefix = False
        if hasattr(dialog, "AllowCancellation"):
            dialog.AllowCancellation = True
        dialog.MainInstruction = "Temporary phase/view state has been restored."
        dialog.MainContent = main_content
        if expanded:
            dialog.ExpandedContent = expanded

        command_link_id = getattr(UI.TaskDialogCommandLinkId, "CommandLink1", None)
        command_link_two = getattr(UI.TaskDialogCommandLinkId, "CommandLink2", None)
        if command_link_id is None:
            _show_alert(TITLE, main_content + "\n\nClose the document manually if needed.")
            return "cancel"

        dialog.AddCommandLink(command_link_id, "Close File Now")
        if command_link_two is not None:
            dialog.AddCommandLink(command_link_two, "Cancel Close")
        try:
            dialog.CommonButtons = UI.TaskDialogCommonButtons.Cancel
        except Exception:
            pass
        result = dialog.Show()
        if result == command_link_id:
            return "close"
        return "cancel"
    except Exception as ex:
        _log("CloseDecisionDialogFailed {0}".format(_exception_text(ex)))
        _show_alert(TITLE, main_content + "\n\nClose the document manually if needed.")
        return "cancel"


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


def _queue_pending_close(state, token, doc_key, doc_runtime_id, title):
    pending = state.setdefault("pending_closes", {})
    existing = pending.get(token)
    if isinstance(existing, dict):
        # Coalesce duplicate DocumentClosing events for the same document.
        existing["last_seen_at"] = time.time()
        return

    generation = (_to_int(state.get("next_generation")) or 0) + 1
    state["next_generation"] = generation
    pending[token] = {
        "doc_key": _safe_text(doc_key),
        "doc_runtime_id": _to_int(doc_runtime_id),
        "title": _safe_text(title),
        "generation": generation,
        "queued_at": time.time(),
        "last_seen_at": time.time(),
        "attempts": 0,
        "next_try_at": time.time() + 0.05,
    }


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
    for token, guard in list((state.get("repost_guards") or {}).items()):
        if not isinstance(guard, dict) or now > float(guard.get("expires_at", 0.0) or 0.0):
            state.get("repost_guards", {}).pop(token, None)


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
    state.setdefault("next_generation", 0)
    state.setdefault("last_idling_at", 0.0)
    if not isinstance(state.get("pending_closes"), dict):
        state["pending_closes"] = {}
    if not isinstance(state.get("repost_guards"), dict):
        state["repost_guards"] = {}
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
    if not isinstance(state.get("last_seen_tvp"), dict):
        state["last_seen_tvp"] = {}
    if not isinstance(state.get("view_sessions"), dict):
        state["view_sessions"] = {}
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
    sessions = view_state.get("view_sessions") or {}
    last_seen = view_state.get("last_seen_tvp") or {}
    for view_key, session in list(sessions.items()):
        if _session_matches_doc(session, doc_key, doc_runtime_id):
            sessions.pop(view_key, None)
            last_seen.pop(view_key, None)
    _save_view_state(view_state)


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
        return DB.ElementId(int(value))
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
    text = "{0} {1}".format(_timestamp(), _safe_text(message))
    try:
        LOGGER.debug(_safe_text(message))
    except Exception:
        pass
    try:
        path = _get_log_path()
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "a") as stream:
            stream.write(text + os.linesep)
    except Exception:
        pass


def _get_log_path():
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "EasyBIM", "Temp Phase", "logs", "events.log")


def _timestamp():
    try:
        import datetime

        return datetime.datetime.now().isoformat()
    except Exception:
        return _safe_text(time.time())


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
