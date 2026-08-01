# -*- coding: utf-8 -*-
"""Passive Coordination Review warning capture.

This module does not open Revit's native Coordination Review UI.  It watches
Revit failure processing and records only the built-in warning that a linked
model needs Coordination Review.
"""

import time


STATE_ENVVAR = "EASYBIM_COORDINATION_REVIEW_PASSIVE_STATE"
HANDLER_ENVVAR = "EASYBIM_COORDINATION_REVIEW_PASSIVE_HANDLER"
SOURCE = "passive_coordination_review_warning"
ISSUE_TEXT = "Needs Coordination Review"
GENERIC_LINK_KEY = "__COORDINATION_REVIEW_LINK__"
GENERIC_LINK_NAME = "Linked model needs Coordination Review"
STATUS = "needs_coordination_review"

_FALLBACK_STATE = {"documents": {}}
_HANDLER_REF = None
_HANDLER_APP = None


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

    if not records:
        _log_debug(
            "Detection Error. doc_key={} stored_doc_keys={}".format(
                _doc_key(doc),
                sorted(list((state.get("documents", {}) or {}).keys())),
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
    for failure in list(failures):
        try:
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


def register_passive_detector(uiapp=None):
    """Register the session-level Revit failure listener."""
    global _HANDLER_REF, _HANDLER_APP

    if _HANDLER_REF is not None:
        return True

    app = _revit_application(uiapp)
    if app is None:
        _log_debug("FailuresProcessing registration failed: event source not found.")
        return False

    # A previous pyRevit engine may have left its delegate attached; detach it
    # before adding a fresh one so subscriptions can never stack.  A legacy
    # ``True`` marker (written by an older version when the envvar store
    # failed) carries no delegate to detach and is simply ignored.
    existing_handler = _get_envvar(HANDLER_ENVVAR, None)
    if existing_handler is not None and existing_handler is not True:
        try:
            app.FailuresProcessing -= existing_handler
        except Exception:
            pass

    handler = _make_failures_processing_handler()
    try:
        app.FailuresProcessing += handler
    except Exception:
        _log_debug("FailuresProcessing registration failed while adding handler.")
        return False

    _HANDLER_REF = handler
    _HANDLER_APP = app
    # Never store a bare ``True`` marker: it would block the detach path above
    # on the next registration.  If the envvar store fails, the module globals
    # still guard against re-registration within this engine.
    _set_envvar(HANDLER_ENVVAR, handler)
    _log_debug("FailuresProcessing registered.")
    return True


def unregister_passive_detector(uiapp=None):
    """Unregister the passive detector when the stored handler is available."""
    global _HANDLER_REF, _HANDLER_APP

    handler = _HANDLER_REF or _get_envvar(HANDLER_ENVVAR, None)
    app = _HANDLER_APP or _revit_application(uiapp)
    if handler is not None and handler is not True and app is not None:
        try:
            app.FailuresProcessing -= handler
        except Exception:
            pass

    _HANDLER_REF = None
    _HANDLER_APP = None
    _set_envvar(HANDLER_ENVVAR, None)
