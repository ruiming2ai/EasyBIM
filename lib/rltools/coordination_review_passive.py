# -*- coding: utf-8 -*-
"""Passive Coordination Review warning capture.

This module does not open Revit's native Coordination Review UI.  It watches
Revit failure processing and records only the built-in warning that a linked
model needs Coordination Review.
"""

import json
import os
import tempfile
import time


STATE_ENVVAR = "RLTOOLS_COORDINATION_REVIEW_PASSIVE_STATE"
HANDLER_ENVVAR = "RLTOOLS_COORDINATION_REVIEW_PASSIVE_HANDLER"
DEBUG_ENVVAR = "RLTOOLS_COORDINATION_REVIEW_PASSIVE_DEBUG"
DIALOG_HANDLER_ENVVAR = "RLTOOLS_COORDINATION_REVIEW_PASSIVE_DIALOG_HANDLER"
DEBUG_EVENT_LIMIT = 200
SOURCE = "passive_coordination_review_warning"
ISSUE_TEXT = "Needs Coordination Review"
GENERIC_LINK_KEY = "__COORDINATION_REVIEW_LINK__"
GENERIC_LINK_NAME = "Linked model needs Coordination Review"
STATUS = "needs_coordination_review"

_FALLBACK_STATE = {"documents": {}}
_FALLBACK_DEBUG_STATE = {"events": []}
_HANDLER_REF = None
_HANDLER_APP = None
_DIALOG_HANDLER_REF = None
_DIALOG_HANDLER_APP = None


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value, default=None):
    try:
        return int(value)
    except Exception:
        return default


def _element_id_int(element_id):
    if element_id is None:
        return None
    for attr in ("IntegerValue", "Value"):
        try:
            value = getattr(element_id, attr)
            return int(value)
        except Exception:
            pass
    try:
        return int(element_id)
    except Exception:
        return None


def _call_no_args(obj, method_name, default=None):
    try:
        method = getattr(obj, method_name)
    except Exception:
        return default
    try:
        return method()
    except Exception:
        return default


def _get_envvar(name, default=None):
    try:
        from pyrevit import script
        value = script.get_envvar(name)
        return default if value is None else value
    except Exception:
        return default


def _set_envvar(name, value):
    try:
        from pyrevit import script
        script.set_envvar(name, value)
        return True
    except Exception:
        return False


def _get_logger():
    try:
        from pyrevit import script
        return script.get_logger()
    except Exception:
        return None


def _log_debug(message):
    logger = _get_logger()
    if logger is None:
        return
    try:
        logger.debug("[Coordination Review Passive] {}".format(message))
    except Exception:
        pass


def _normalize_state(state):
    if not isinstance(state, dict):
        return {"documents": {}}
    documents = state.get("documents", {})
    if not isinstance(documents, dict):
        documents = {}
    return {"documents": documents}


def _load_state():
    state = _get_envvar(STATE_ENVVAR, None)
    if state is None:
        state = _FALLBACK_STATE
    return _normalize_state(state)


def _save_state(state):
    global _FALLBACK_STATE
    state = _normalize_state(state)
    _FALLBACK_STATE = state
    _set_envvar(STATE_ENVVAR, state)
    return state


def _normalize_debug_state(state):
    if not isinstance(state, dict):
        return {"events": []}
    events = state.get("events", [])
    if not isinstance(events, list):
        events = []
    return {"events": list(events[-int(DEBUG_EVENT_LIMIT):])}


def get_debug_state():
    state = _get_envvar(DEBUG_ENVVAR, None)
    if state is None:
        state = _FALLBACK_DEBUG_STATE
    return _normalize_debug_state(state)


def _save_debug_state(state):
    global _FALLBACK_DEBUG_STATE
    state = _normalize_debug_state(state)
    _FALLBACK_DEBUG_STATE = state
    _set_envvar(DEBUG_ENVVAR, state)
    return state


def _json_safe(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return dict((_safe_text(key), _json_safe(item)) for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in list(value)]
    return _safe_text(value)


def passive_debug_file_path():
    return os.path.join(
        tempfile.gettempdir(),
        "RLTools",
        "CoordinationReview",
        "passive_detection_debug.json",
    )


def _ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path)


def write_debug_file():
    path = passive_debug_file_path()
    _ensure_dir(os.path.dirname(path))
    with open(path, "w") as handle:
        json.dump(_json_safe(get_debug_state()), handle, indent=2, sort_keys=True)
    return path


def _write_debug_file_silent():
    try:
        return write_debug_file()
    except Exception:
        return ""


def append_debug_event(event_type, payload=None):
    state = get_debug_state()
    events = state.setdefault("events", [])
    events.append(
        {
            "timestamp": time.time(),
            "type": _safe_text(event_type) or "unknown",
            "payload": _json_safe(payload or {}),
        }
    )
    state = _save_debug_state(state)
    _write_debug_file_silent()
    return state


def _coordination_failure_id():
    try:
        from Autodesk.Revit.DB import BuiltInFailures
        return BuiltInFailures.LinkFailures.LinkInstanceNeedsReconcile
    except Exception:
        return None


def _failure_ids_equal(left, right):
    if left is None or right is None:
        return False
    try:
        if left == right:
            return True
    except Exception:
        pass

    for attr in ("Guid", "TypeId"):
        try:
            if getattr(left, attr) == getattr(right, attr):
                return True
        except Exception:
            pass

    for method_name in ("GetGuid", "GetTypeId"):
        try:
            if getattr(left, method_name)() == getattr(right, method_name)():
                return True
        except Exception:
            pass

    return _safe_text(left).lower() == _safe_text(right).lower()


def is_coordination_review_failure(failure, expected_failure_id=None):
    failure_id = _call_no_args(failure, "GetFailureDefinitionId")
    expected_failure_id = expected_failure_id or _coordination_failure_id()

    if expected_failure_id is not None and _failure_ids_equal(failure_id, expected_failure_id):
        return True

    failure_id_text = _safe_text(failure_id).lower()
    if "linkinstanceneedsreconcile" in failure_id_text:
        return True

    description = _safe_text(_call_no_args(failure, "GetDescriptionText")).lower()
    if "needs coordination review" in description and ("linked" in description or ".rvt" in description):
        return True

    return False


def _get_doc_title(doc):
    try:
        title = getattr(doc, "Title", "")
        if title:
            return _safe_text(title)
    except Exception:
        pass
    return "(Unknown)"


def _get_doc_path(doc):
    try:
        return _safe_text(getattr(doc, "PathName", "") or "").strip()
    except Exception:
        return ""


def _normalize_path(value):
    return _safe_text(value).replace("\\", "/").strip().lower()


def _normalize_title(value):
    return _safe_text(value).strip().lower()


def _model_identity(value):
    text = _normalize_path(value)
    if not text:
        return ""

    text = text.rsplit("/", 1)[-1]
    if text.endswith(".rvt"):
        text = text[:-4]

    for suffix in ("_detached", " detached", "-detached"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break

    return text.strip(" _-")


def _doc_aliases(doc):
    aliases = set()

    path = _normalize_path(_get_doc_path(doc))
    if path:
        aliases.add("path:{}".format(path))
        aliases.add("file:{}".format(path.rsplit("/", 1)[-1]))
        identity = _model_identity(path)
        if identity:
            aliases.add("model:{}".format(identity))

    title = _normalize_title(_get_doc_title(doc))
    if title:
        aliases.add("title:{}".format(title))
        identity = _model_identity(title)
        if identity:
            aliases.add("model:{}".format(identity))

    return sorted(aliases or set(["unknown"]))


def _doc_key(doc):
    aliases = _doc_aliases(doc)
    for prefix in ("path:", "title:", "model:", "file:"):
        for alias in aliases:
            if alias.startswith(prefix):
                return alias
    return aliases[0] if aliases else "unknown"


def _doc_entry(state, doc, create=False):
    documents = state.setdefault("documents", {})
    key = _doc_key(doc)
    aliases = set(_doc_aliases(doc))
    if key in documents:
        entry = documents.get(key)
        entry["aliases"] = sorted(set(entry.get("aliases", []) or []) | aliases | set([key]))
        return entry

    for stored_key, entry in documents.items():
        stored_aliases = set(entry.get("aliases", []) or [])
        stored_aliases.add(stored_key)
        if aliases.intersection(stored_aliases):
            entry["aliases"] = sorted(stored_aliases | aliases | set([key]))
            return entry

    if create:
        documents[key] = {
            "doc_title": _get_doc_title(doc),
            "aliases": sorted(aliases | set([key])),
            "records": [],
        }
    return documents.get(key)


def _failure_description(failure):
    return _safe_text(_call_no_args(failure, "GetDescriptionText"))


def _failure_element_ids(failure):
    ids = []
    seen = set()
    for method_name in ("GetFailingElementIds", "GetAdditionalElementIds"):
        for raw_id in list(_call_no_args(failure, method_name, []) or []):
            value = _element_id_int(raw_id)
            if value is None or value in seen:
                continue
            seen.add(value)
            ids.append(raw_id)
    return ids


def _element_name(element):
    try:
        name = getattr(element, "Name", "")
        if name:
            return _safe_text(name)
    except Exception:
        pass
    return ""


def _is_link_like(element):
    if element is None:
        return False
    try:
        element.GetLinkDocument()
        return True
    except Exception:
        pass

    class_name = _safe_text(element.__class__.__name__).lower()
    if "revitlinkinstance" in class_name or "revitlinktype" in class_name:
        return True

    name = _element_name(element).lower()
    return ".rvt" in name


def _record_for_element(doc, element_id, failure):
    try:
        element = doc.GetElement(element_id)
    except Exception:
        element = None

    if not _is_link_like(element):
        return None

    element_id_int = _element_id_int(getattr(element, "Id", element_id))
    link_key = _safe_text(element_id_int) if element_id_int is not None else _safe_text(element_id)
    link_name = _element_name(element) or "Revit Link {}".format(link_key)
    return {
        "doc_key": _doc_key(doc),
        "link_key": link_key,
        "link_name": link_name,
        "element_id": element_id_int,
        "fallback_text": _failure_description(failure),
        "status": STATUS,
    }


def _generic_record(doc, failure):
    return {
        "doc_key": _doc_key(doc),
        "link_key": GENERIC_LINK_KEY,
        "link_name": GENERIC_LINK_NAME,
        "element_id": None,
        "fallback_text": _failure_description(failure),
        "status": STATUS,
    }


def _records_from_failure(doc, failure):
    records = []
    seen = set()
    for element_id in _failure_element_ids(failure):
        record = _record_for_element(doc, element_id, failure)
        if not record:
            continue
        key = record.get("link_key")
        if key in seen:
            continue
        seen.add(key)
        records.append(record)

    if not records:
        records.append(_generic_record(doc, failure))
    return records


def record_coordination_review_failure(doc, failure, timestamp=None):
    """Record a passive Coordination Review warning and return doc records."""
    if not is_coordination_review_failure(failure):
        return []

    append_debug_event(
        "coordination_failure_matched",
        {
            "doc_key": _doc_key(doc),
            "failure_id": _safe_text(_call_no_args(failure, "GetFailureDefinitionId")),
            "description": _failure_description(failure),
        },
    )

    timestamp = time.time() if timestamp is None else float(timestamp)
    state = _load_state()
    entry = _doc_entry(state, doc, create=True)
    records = entry.setdefault("records", [])
    existing = set([_safe_text(record.get("link_key")) for record in records])

    for record in _records_from_failure(doc, failure):
        link_key = _safe_text(record.get("link_key"))
        if link_key in existing:
            continue
        record["timestamp"] = timestamp
        records.append(record)
        existing.add(link_key)
        append_debug_event(
            "record_saved",
            {
                "doc_key": _doc_key(doc),
                "link_key": link_key,
                "link_name": record.get("link_name"),
                "element_id": record.get("element_id"),
            },
        )

    _save_state(state)
    return list(records)


def _empty_report(doc, detection_error=False):
    return {
        "doc_title": _get_doc_title(doc),
        "link_map": {},
        "grouped": {},
        "link_totals": {},
        "total_matching_warnings": 0,
        "total_link_assignments": 0,
        "source": SOURCE,
        "detection_error": bool(detection_error),
    }


def build_passive_coordination_report(doc, consume=True):
    state = _load_state()
    entry = _doc_entry(state, doc, create=False)
    records = list((entry or {}).get("records", []) or [])
    stored_doc_keys = sorted(list((state.get("documents", {}) or {}).keys()))

    report_payload = {
        "doc_key": _doc_key(doc),
        "doc_title": _get_doc_title(doc),
        "doc_path": _get_doc_path(doc),
        "consume": bool(consume),
        "record_count": len(records),
        "stored_doc_keys": stored_doc_keys,
    }
    append_debug_event("report_build", report_payload)

    if not records:
        append_debug_event("report_detection_error", report_payload)
        _log_debug(
            "Detection Error. doc_key={} stored_doc_keys={} debug_file={}".format(
                report_payload.get("doc_key"),
                stored_doc_keys,
                passive_debug_file_path(),
            )
        )
        return _empty_report(doc, detection_error=True)

    link_map = {}
    grouped = {}
    link_totals = {}

    for record in records:
        link_key = _safe_text(record.get("link_key")) or GENERIC_LINK_KEY
        link_name = _safe_text(record.get("link_name")) or GENERIC_LINK_NAME
        element_id = _safe_int(record.get("element_id"), default=None)

        link_map.setdefault(link_key, {"name": link_name, "element_id": element_id})
        warning_bucket = grouped.setdefault(link_key, {})
        issue = warning_bucket.setdefault(ISSUE_TEXT, {"count": 0, "instance_ids": set()})
        issue["count"] += 1
        if element_id is not None:
            issue["instance_ids"].add(element_id)
        link_totals[link_key] = int(link_totals.get(link_key, 0)) + 1

    total = sum(link_totals.values())
    report = {
        "doc_title": _get_doc_title(doc),
        "link_map": link_map,
        "grouped": grouped,
        "link_totals": link_totals,
        "total_matching_warnings": total,
        "total_link_assignments": total,
        "source": SOURCE,
    }

    if consume:
        clear_document_records(doc)

    return report


def clear_document_records(doc):
    state = _load_state()
    documents = state.setdefault("documents", {})
    aliases = set(_doc_aliases(doc))
    keys_to_remove = []
    for stored_key, entry in documents.items():
        stored_aliases = set((entry or {}).get("aliases", []) or [])
        stored_aliases.add(stored_key)
        if stored_key == _doc_key(doc) or aliases.intersection(stored_aliases):
            keys_to_remove.append(stored_key)

    for key in keys_to_remove:
        documents.pop(key, None)
    _save_state(state)


def clear_all_records():
    _save_state({"documents": {}})


def _get_uiapp():
    try:
        return __revit__
    except Exception:
        pass

    try:
        from pyrevit import HOST_APP
        return HOST_APP.uiapp
    except Exception:
        return None


def _revit_application(uiapp):
    uiapp = uiapp or _get_uiapp()
    candidates = []
    if uiapp is not None:
        candidates.append(uiapp)
        try:
            candidates.append(uiapp.Application)
        except Exception:
            pass
        try:
            candidates.append(uiapp.ControlledApplication)
        except Exception:
            pass

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            getattr(candidate, "FailuresProcessing")
            return candidate
        except Exception:
            pass
    return None


def _event_source(uiapp, event_name):
    uiapp = uiapp or _get_uiapp()
    candidates = []
    if uiapp is not None:
        candidates.append(uiapp)
        try:
            candidates.append(uiapp.Application)
        except Exception:
            pass
        try:
            candidates.append(uiapp.ControlledApplication)
        except Exception:
            pass

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            getattr(candidate, event_name)
            return candidate
        except Exception:
            pass
    return None


def _active_doc_from_sender(sender):
    try:
        uidoc = sender.ActiveUIDocument
        return uidoc.Document if uidoc else None
    except Exception:
        pass
    return None


def _handle_failures_processing(sender, args):
    try:
        accessor = args.GetFailuresAccessor()
    except Exception:
        return

    try:
        doc = accessor.GetDocument()
    except Exception:
        doc = _active_doc_from_sender(sender)

    failures = _call_no_args(accessor, "GetFailureMessages", []) or []
    append_debug_event(
        "failures_processing_event",
        {
            "doc_key": _doc_key(doc),
            "failure_count": len(list(failures or [])),
        },
    )
    for failure in list(failures):
        try:
            append_debug_event(
                "failure_seen",
                {
                    "doc_key": _doc_key(doc),
                    "failure_id": _safe_text(_call_no_args(failure, "GetFailureDefinitionId")),
                    "description": _failure_description(failure),
                    "is_coordination_review": is_coordination_review_failure(failure),
                },
            )
            record_coordination_review_failure(doc, failure)
        except Exception:
            pass


def _make_failures_processing_handler():
    try:
        from System import EventHandler
        try:
            from Autodesk.Revit.DB.Events import FailuresProcessingEventArgs
        except Exception:
            from Autodesk.Revit.DB import FailuresProcessingEventArgs
        return EventHandler[FailuresProcessingEventArgs](_handle_failures_processing)
    except Exception:
        return _handle_failures_processing


def _dialog_message(args):
    for method_name in ("GetMessage", "GetDialogMessage"):
        value = _call_no_args(args, method_name, "")
        if value:
            return _safe_text(value)
    for attr in ("Message", "DialogMessage"):
        try:
            value = getattr(args, attr)
            if value:
                return _safe_text(value)
        except Exception:
            pass
    return ""


def _dialog_id(args):
    for method_name in ("GetDialogId",):
        value = _call_no_args(args, method_name, "")
        if value:
            return _safe_text(value)
    for attr in ("DialogId", "Id"):
        try:
            value = getattr(args, attr)
            if value:
                return _safe_text(value)
        except Exception:
            pass
    return ""


def _handle_dialog_box_showing(sender, args):
    append_debug_event(
        "dialog_box_showing_event",
        {
            "dialog_id": _dialog_id(args),
            "message": _dialog_message(args),
            "args_type": _safe_text(type(args)),
        },
    )


def _make_dialog_box_showing_handler():
    try:
        from System import EventHandler
        try:
            from Autodesk.Revit.UI.Events import DialogBoxShowingEventArgs
        except Exception:
            from Autodesk.Revit.UI import DialogBoxShowingEventArgs
        return EventHandler[DialogBoxShowingEventArgs](_handle_dialog_box_showing)
    except Exception:
        return _handle_dialog_box_showing


def register_passive_detector(uiapp=None):
    """Register the session-level Revit failure listener."""
    global _HANDLER_REF, _HANDLER_APP, _DIALOG_HANDLER_REF, _DIALOG_HANDLER_APP

    append_debug_event(
        "startup_register_attempt",
        {
            "uiapp_type": _safe_text(type(uiapp or _get_uiapp())),
        },
    )

    if _HANDLER_REF is not None:
        append_debug_event("startup_register_success", {"reason": "existing_module_handler"})
        return True

    existing_handler = _get_envvar(HANDLER_ENVVAR, None)
    if existing_handler is not None:
        append_debug_event(
            "startup_existing_handler_found",
            {
                "handler_type": _safe_text(type(existing_handler)),
                "handler_is_marker": existing_handler is True,
            },
        )

    app = _revit_application(uiapp)
    if app is None:
        append_debug_event("startup_register_failed", {"reason": "failures_processing_source_not_found"})
        _log_debug("FailuresProcessing registration failed: event source not found.")
        return False

    if existing_handler is not None and existing_handler is not True:
        try:
            app.FailuresProcessing -= existing_handler
            append_debug_event("startup_existing_handler_removed", {})
        except Exception:
            append_debug_event("startup_existing_handler_remove_failed", {})

    handler = _make_failures_processing_handler()
    try:
        app.FailuresProcessing += handler
    except Exception:
        append_debug_event("startup_register_failed", {"reason": "failures_processing_add_failed"})
        _log_debug("FailuresProcessing registration failed while adding handler.")
        return False

    _HANDLER_REF = handler
    _HANDLER_APP = app
    if not _set_envvar(HANDLER_ENVVAR, handler):
        _set_envvar(HANDLER_ENVVAR, True)

    dialog_registered = False
    existing_dialog_handler = _get_envvar(DIALOG_HANDLER_ENVVAR, None)
    dialog_source = _event_source(uiapp, "DialogBoxShowing")
    if dialog_source is not None:
        if existing_dialog_handler is not None and existing_dialog_handler is not True:
            try:
                dialog_source.DialogBoxShowing -= existing_dialog_handler
                append_debug_event("dialog_existing_handler_removed", {})
            except Exception:
                append_debug_event("dialog_existing_handler_remove_failed", {})
        dialog_handler = _make_dialog_box_showing_handler()
        try:
            dialog_source.DialogBoxShowing += dialog_handler
            _DIALOG_HANDLER_REF = dialog_handler
            _DIALOG_HANDLER_APP = dialog_source
            if not _set_envvar(DIALOG_HANDLER_ENVVAR, dialog_handler):
                _set_envvar(DIALOG_HANDLER_ENVVAR, True)
            dialog_registered = True
        except Exception:
            append_debug_event("dialog_register_failed", {"reason": "dialog_box_showing_add_failed"})
    else:
        append_debug_event("dialog_register_failed", {"reason": "dialog_box_showing_source_not_found"})

    append_debug_event(
        "startup_register_success",
        {
            "failures_processing_source": _safe_text(type(app)),
            "dialog_registered": dialog_registered,
            "debug_file": passive_debug_file_path(),
        },
    )
    _log_debug(
        "FailuresProcessing registered. dialog_registered={} debug_file={}".format(
            dialog_registered,
            passive_debug_file_path(),
        )
    )
    return True


def unregister_passive_detector(uiapp=None):
    """Unregister the passive detector when the stored handler is available."""
    global _HANDLER_REF, _HANDLER_APP, _DIALOG_HANDLER_REF, _DIALOG_HANDLER_APP

    handler = _HANDLER_REF or _get_envvar(HANDLER_ENVVAR, None)
    app = _HANDLER_APP or _revit_application(uiapp)
    if handler is not None and app is not None:
        try:
            app.FailuresProcessing -= handler
        except Exception:
            pass

    dialog_handler = _DIALOG_HANDLER_REF or _get_envvar(DIALOG_HANDLER_ENVVAR, None)
    dialog_app = _DIALOG_HANDLER_APP or _event_source(uiapp, "DialogBoxShowing")
    if dialog_handler is not None and dialog_app is not None:
        try:
            dialog_app.DialogBoxShowing -= dialog_handler
        except Exception:
            pass

    _HANDLER_REF = None
    _HANDLER_APP = None
    _DIALOG_HANDLER_REF = None
    _DIALOG_HANDLER_APP = None
    _set_envvar(HANDLER_ENVVAR, None)
    _set_envvar(DIALOG_HANDLER_ENVVAR, None)
