# -*- coding: utf-8 -*-
"""Native Coordination Review report helpers.

The native Revit Coordination Review tree is not exposed through
Document.GetWarnings().  These helpers treat Revit's generated HTML report as
the source of truth and convert it into the existing RL Tools report shape.
"""

import os
import re
import tempfile
import time

try:
    from html.parser import HTMLParser
except Exception:
    from HTMLParser import HTMLParser


STATUS_LABELS = set(["New/Unresolved", "Postponed", "Rejected"])
DEFAULT_INSTANCE_CAP = 200
_NATIVE_STATE_ENVVAR = "RLTOOLS_COORDINATION_REVIEW_NATIVE_STATE"
_NATIVE_JOB_MAX_AGE_SEC = 300.0
_NATIVE_STAGE_TIMEOUT_SEC = 25.0
_ID_RE = re.compile(r"\bid\s+(-?\d+)\b", re.IGNORECASE)


class NativeCoordinationReviewError(Exception):
    def __init__(self, stage, message):
        Exception.__init__(self, message)
        self.stage = _safe_text(stage) or "unknown"
        self.message = _safe_text(message) or "Coordination Review automation failed."


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


def _clean_text(value):
    value = _safe_text(value)
    value = value.replace("\xa0", " ")
    return " ".join(value.split())


def _extract_ids(text):
    ids = []
    seen = set()
    for match in _ID_RE.finditer(_safe_text(text)):
        value = _safe_int(match.group(1))
        if value is None or value in seen:
            continue
        seen.add(value)
        ids.append(value)
    return ids


def _level_from_attrs(attrs):
    attr_map = {}
    for key, value in list(attrs or []):
        attr_map[_safe_text(key).lower()] = _safe_text(value)

    for attr_name in ("data-level", "level", "aria-level"):
        level = _safe_int(attr_map.get(attr_name))
        if level is not None:
            return level

    style = attr_map.get("style", "")
    match = re.search(r"(?:padding|margin)-left\s*:\s*(\d+)", style, re.IGNORECASE)
    if match:
        return int(int(match.group(1)) / 16)

    return None


class _ReportTableParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.rows = []
        self._current_row = None
        self._current_cell = None

    def handle_starttag(self, tag, attrs):
        tag = _safe_text(tag).lower()
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th") and self._current_row is not None:
            self._current_cell = {
                "parts": [],
                "level": _level_from_attrs(attrs),
            }

    def handle_data(self, data):
        if self._current_cell is not None:
            self._current_cell["parts"].append(data)

    def handle_entityref(self, name):
        if self._current_cell is not None:
            if name == "nbsp":
                self._current_cell["parts"].append(" ")
            else:
                self._current_cell["parts"].append("&{};".format(name))

    def handle_endtag(self, tag):
        tag = _safe_text(tag).lower()
        if tag in ("td", "th") and self._current_cell is not None:
            cell = {
                "text": _clean_text(" ".join(self._current_cell.get("parts", []))),
                "level": self._current_cell.get("level"),
            }
            self._current_row.append(cell)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = None


def _is_header_row(cells):
    labels = [_clean_text(cell.get("text", "")).lower() for cell in list(cells or [])]
    return bool(labels and labels[0] == "message" and "action" in labels)


def _append_element_ids(target, text, link_name):
    ids = _extract_ids(text)
    if not ids:
        return

    link_name = _safe_text(link_name).lower()
    text_lower = _safe_text(text).lower()
    key = "linked_element_ids" if link_name and link_name in text_lower else "host_element_ids"
    existing = set(target.get(key, []))
    for value in ids:
        if value not in existing:
            target.setdefault(key, []).append(value)
            existing.add(value)


def _trim_context(context_by_level, level):
    for existing_level in list(context_by_level.keys()):
        if existing_level >= level:
            context_by_level.pop(existing_level, None)


def parse_native_coordination_review_html(html_text, link_name="", link_key=None):
    """Parse a native Coordination Review HTML report into flat issue rows."""
    parser = _ReportTableParser()
    parser.feed(_safe_text(html_text))

    rows = []
    current_status = ""
    context_by_level = {}
    current_issue = None

    for cells in parser.rows:
        if _is_header_row(cells):
            continue

        message = _clean_text(cells[0].get("text", "")) if cells else ""
        action = _clean_text(cells[1].get("text", "")) if len(cells) > 1 else ""
        comment = _clean_text(cells[2].get("text", "")) if len(cells) > 2 else ""
        level = cells[0].get("level") if cells else None
        if level is None:
            level = max(context_by_level.keys() or [0]) + 1 if (action or comment) else 0

        if not message:
            continue

        if message in STATUS_LABELS:
            current_status = message
            context_by_level = {0: message}
            current_issue = None
            continue

        if action or comment:
            category_path = [
                context_by_level[key]
                for key in sorted(context_by_level.keys())
                if key > 0 and key < level and context_by_level.get(key)
            ]
            issue = {
                "link_key": _safe_text(link_key) or _safe_text(link_name) or "native-report",
                "link_name": _safe_text(link_name) or "Unknown Link",
                "status": current_status or "Unknown",
                "category_path": category_path,
                "message": message,
                "action": action,
                "comment": comment,
                "host_element_ids": [],
                "linked_element_ids": [],
            }
            _append_element_ids(issue, message, link_name)
            rows.append(issue)
            current_issue = issue
            continue

        if _extract_ids(message) and current_issue is not None:
            _append_element_ids(current_issue, message, link_name)
            continue

        _trim_context(context_by_level, level)
        context_by_level[level] = message
        current_issue = None

    return rows


def _native_issue_label(row):
    parts = []
    status = _safe_text(row.get("status"))
    if status:
        parts.append(status)
    parts.extend([_safe_text(item) for item in list(row.get("category_path", []) or []) if _safe_text(item)])
    message = _safe_text(row.get("message")) or "(No description)"
    parts.append(message)
    label = " > ".join(parts)
    if _safe_text(row.get("action")):
        label = "{} [Action: {}]".format(label, _safe_text(row.get("action")))
    if _safe_text(row.get("comment")):
        label = "{} [Comment: {}]".format(label, _safe_text(row.get("comment")))
    return label


def build_report_from_native_rows(doc_title, native_rows):
    """Convert native rows to the generic report consumed by the WPF window."""
    link_map = {}
    grouped = {}
    link_totals = {}
    total = 0

    for row in list(native_rows or []):
        link_key = _safe_text(row.get("link_key")) or _safe_text(row.get("link_name")) or "native-report"
        link_name = _safe_text(row.get("link_name")) or "Unknown Link"
        link_map.setdefault(link_key, {"name": link_name, "element_id": None})

        issue_label = _native_issue_label(row)
        bucket = grouped.setdefault(link_key, {})
        issue = bucket.setdefault(
            issue_label,
            {
                "count": 0,
                "instance_ids": set(),
                "native_rows": [],
            },
        )
        issue["count"] += 1
        host_ids = list(row.get("host_element_ids", []) or [])
        linked_ids = list(row.get("linked_element_ids", []) or [])
        issue["instance_ids"].update(host_ids or linked_ids)
        issue["native_rows"].append(dict(row))
        link_totals[link_key] = int(link_totals.get(link_key, 0)) + 1
        total += 1

    return {
        "doc_title": _safe_text(doc_title) or "(Unknown)",
        "link_map": link_map,
        "grouped": grouped,
        "link_totals": link_totals,
        "total_matching_warnings": total,
        "total_link_assignments": total,
        "source": "native_coordination_review_report",
    }


def build_error_report(doc_title, stage, message):
    return {
        "doc_title": _safe_text(doc_title) or "(Unknown)",
        "link_map": {},
        "grouped": {},
        "link_totals": {},
        "total_matching_warnings": 0,
        "total_link_assignments": 0,
        "source": "native_coordination_review_report",
        "automation_error": {
            "stage": _safe_text(stage) or "unknown",
            "message": _safe_text(message) or "Coordination Review automation failed.",
        },
    }


def coordination_review_cache_dir(doc_title=None):
    safe_title = re.sub(r"[^A-Za-z0-9_.-]+", "_", _safe_text(doc_title) or "document").strip("_")
    if not safe_title:
        safe_title = "document"
    return os.path.join(tempfile.gettempdir(), "RLTools", "CoordinationReview", safe_title)


def make_report_path(cache_dir, link_name, index=0):
    safe_link = re.sub(r"[^A-Za-z0-9_.-]+", "_", _safe_text(link_name) or "link").strip("_")
    if not safe_link:
        safe_link = "link"
    filename = "{}_{}_{}.html".format(int(time.time() * 1000), int(index), safe_link)
    return os.path.join(cache_dir, filename)


def delete_html_cache(paths):
    removed = []
    for path in list(paths or []):
        path = _safe_text(path)
        if not path or not path.lower().endswith((".html", ".htm")):
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
                removed.append(path)
        except Exception:
            pass
    return removed


def start_native_coordination_review_extraction(doc, uiapp=None):
    """Queue native Coordination Review extraction for app-idling processing."""
    uiapp = uiapp or _get_uiapp()
    doc_title = _get_doc_title(doc)

    try:
        link_targets = _collect_link_targets(doc)
        if not link_targets:
            report = build_report_from_native_rows(doc_title, [])
            _show_native_report_window(report, doc=doc, uiapp=uiapp)
            return True

        state = _build_native_job_state(doc, link_targets)
        if not _save_native_state(state):
            report = build_error_report(
                doc_title,
                "prepare_link",
                "Could not queue native Coordination Review extraction.",
            )
            _show_native_report_window(report, doc=doc, uiapp=uiapp)
            return True
        return True
    except NativeCoordinationReviewError as ex:
        report = build_error_report(doc_title, ex.stage, ex.message)
        _show_native_report_window(report, doc=doc, uiapp=uiapp)
        return True
    except Exception as ex:
        report = build_error_report(doc_title, "native_automation", _safe_text(ex))
        _show_native_report_window(report, doc=doc, uiapp=uiapp)
        return True


def process_native_coordination_review_jobs(uiapp=None):
    """Advance one queued native Coordination Review extraction step."""
    uiapp = uiapp or _get_uiapp()
    if uiapp is None:
        return

    state = _load_native_state()
    if not state.get("active"):
        return

    doc = _resolve_doc_for_state(uiapp, state)
    try:
        if doc is None:
            raise NativeCoordinationReviewError(
                "prepare_link",
                "The source Revit document is no longer active or open.",
            )

        age = time.time() - float(state.get("created_at", time.time()) or time.time())
        if age > _NATIVE_JOB_MAX_AGE_SEC:
            raise NativeCoordinationReviewError(
                _safe_text(state.get("stage")) or "native_automation",
                "Native Coordination Review extraction timed out.",
            )

        done = _advance_native_job_state(uiapp, doc, state)
        if done:
            _clear_native_state()
        else:
            _save_native_state(state)
    except NativeCoordinationReviewError as ex:
        _fail_native_job(state, doc, uiapp, ex.stage, ex.message)
    except Exception as ex:
        _fail_native_job(state, doc, uiapp, "native_automation", _safe_text(ex))


def _build_native_job_state(doc, link_targets):
    doc_title = _get_doc_title(doc)
    cache_dir = coordination_review_cache_dir(doc_title)
    _ensure_dir(cache_dir)

    links = []
    cache_paths = []
    for index, target in enumerate(list(link_targets or [])):
        report_path = make_report_path(cache_dir, target.get("name"), index=index)
        cache_paths.append(report_path)
        links.append(
            {
                "key": _safe_text(target.get("key")),
                "name": _safe_text(target.get("name")),
                "element_id_int": _element_id_int(target.get("element_id")) or _safe_int(target.get("element_id_int")),
                "report_path": report_path,
            }
        )

    now = time.time()
    return {
        "active": True,
        "created_at": now,
        "stage_started_at": now,
        "stage": "prepare_link",
        "doc": _doc_identity(doc),
        "doc_title": doc_title,
        "links": links,
        "index": 0,
        "native_rows": [],
        "cache_paths": cache_paths,
    }


def _advance_native_job_state(uiapp, doc, state):
    stage = _safe_text(state.get("stage")) or "prepare_link"

    if _stage_timed_out(state):
        raise NativeCoordinationReviewError(
            stage,
            "Native Coordination Review extraction timed out while running '{}'.".format(stage),
        )

    if stage == "prepare_link":
        if int(state.get("index", 0) or 0) >= len(list(state.get("links", []) or [])):
            _set_native_stage(state, "delete_html_cache")
            return False

        link_target = _resolve_link_target_for_state(doc, state)
        _select_link_instance(getattr(uiapp, "ActiveUIDocument", None), link_target.get("element_id"))
        _set_native_stage(state, "post_coordination_select_link")
        return False

    if stage == "post_coordination_select_link":
        _post_coordination_select_link(uiapp)
        _set_native_stage(state, "wait_for_native_dialog")
        return False

    if stage == "wait_for_native_dialog":
        automation = _load_ui_automation()
        if _find_coordination_review_window(automation) is None:
            return False
        _set_native_stage(state, "create_report")
        return False

    if stage == "create_report":
        automation = _load_ui_automation()
        review_window = _find_coordination_review_window(automation)
        if review_window is None:
            return False
        if not _invoke_named_button(automation, review_window, "Create Report"):
            raise NativeCoordinationReviewError(
                "create_report",
                "The native Coordination Review dialog did not expose a Create Report button.",
            )
        _set_native_stage(state, "save_report")
        return False

    if stage == "save_report":
        automation = _load_ui_automation()
        save_window = _find_save_report_window(automation)
        if save_window is None:
            return False

        link_state = _current_link_state(state)
        report_path = _safe_text(link_state.get("report_path"))
        if not _set_first_edit_value(automation, save_window, report_path):
            raise NativeCoordinationReviewError("save_report", "Could not set the native report path.")
        if not (_invoke_named_button(automation, save_window, "Save") or _invoke_named_button(automation, save_window, "OK")):
            raise NativeCoordinationReviewError("save_report", "Could not confirm the native report save dialog.")
        _set_native_stage(state, "parse_report")
        return False

    if stage == "parse_report":
        link_state = _current_link_state(state)
        report_path = _safe_text(link_state.get("report_path"))
        if not (os.path.exists(report_path) and os.path.getsize(report_path) > 0):
            return False

        try:
            with open(report_path, "r") as handle:
                html_text = handle.read()
        except Exception as ex:
            raise NativeCoordinationReviewError("parse_report", "Could not read native report HTML: {}".format(ex))

        parsed_rows = parse_native_coordination_review_html(
            html_text,
            link_name=link_state.get("name"),
            link_key=link_state.get("key"),
        )
        state.setdefault("native_rows", []).extend(parsed_rows)
        _close_coordination_review_window()
        _set_native_stage(state, "next_link")
        return False

    if stage == "next_link":
        state["index"] = int(state.get("index", 0) or 0) + 1
        _set_native_stage(state, "prepare_link")
        return False

    if stage == "delete_html_cache":
        report = build_report_from_native_rows(
            state.get("doc_title"),
            list(state.get("native_rows", []) or []),
        )
        state["report"] = _make_report_state_safe(report)
        delete_html_cache(state.get("cache_paths", []))
        _set_native_stage(state, "show_rltools_window")
        return False

    if stage == "show_rltools_window":
        report = state.get("report")
        if not isinstance(report, dict):
            report = build_report_from_native_rows(
                state.get("doc_title"),
                list(state.get("native_rows", []) or []),
            )
        _show_native_report_window(report, doc=doc, uiapp=uiapp)
        return True

    raise NativeCoordinationReviewError(stage, "Unknown native Coordination Review extraction stage.")


def _make_report_state_safe(report):
    report = dict(report or {})
    grouped = {}
    for link_key, bucket in dict(report.get("grouped", {}) or {}).items():
        safe_bucket = {}
        for issue_text, row in dict(bucket or {}).items():
            safe_row = dict(row or {})
            instance_ids = safe_row.get("instance_ids", [])
            if isinstance(instance_ids, set):
                instance_ids = sorted(list(instance_ids))
            else:
                instance_ids = list(instance_ids or [])
            safe_row["instance_ids"] = instance_ids
            safe_bucket[issue_text] = safe_row
        grouped[link_key] = safe_bucket
    report["grouped"] = grouped
    return report


def _fail_native_job(state, doc, uiapp, stage, message):
    delete_html_cache((state or {}).get("cache_paths", []))
    _close_coordination_review_window()
    report = build_error_report((state or {}).get("doc_title") or _get_doc_title(doc), stage, message)
    _show_native_report_window(report, doc=doc, uiapp=uiapp)
    _clear_native_state()


def _current_link_state(state):
    links = list(state.get("links", []) or [])
    index = int(state.get("index", 0) or 0)
    if index < 0 or index >= len(links):
        raise NativeCoordinationReviewError("prepare_link", "The native Coordination Review link queue is invalid.")
    return links[index]


def _resolve_link_target_for_state(doc, state):
    link_state = _current_link_state(state)
    element_id_int = _safe_int(link_state.get("element_id_int"))
    if element_id_int is None:
        raise NativeCoordinationReviewError(
            "prepare_link",
            "The Revit link '{}' did not have a valid element id.".format(link_state.get("name")),
        )

    try:
        from Autodesk.Revit.DB import ElementId
        element = doc.GetElement(ElementId(element_id_int))
    except Exception as ex:
        raise NativeCoordinationReviewError(
            "prepare_link",
            "Could not resolve Revit link '{}': {}".format(link_state.get("name"), ex),
        )

    if element is None:
        raise NativeCoordinationReviewError(
            "prepare_link",
            "The Revit link '{}' is no longer available.".format(link_state.get("name")),
        )

    try:
        if element.GetLinkDocument() is None:
            raise NativeCoordinationReviewError(
                "prepare_link",
                "The Revit link '{}' is not loaded.".format(link_state.get("name")),
            )
    except NativeCoordinationReviewError:
        raise
    except Exception:
        pass

    return {
        "key": _safe_text(link_state.get("key")),
        "name": _safe_text(link_state.get("name")),
        "element_id": element.Id,
    }


def _set_native_stage(state, stage):
    state["stage"] = stage
    state["stage_started_at"] = time.time()


def _stage_timed_out(state):
    started = float(state.get("stage_started_at", time.time()) or time.time())
    return time.time() - started > _NATIVE_STAGE_TIMEOUT_SEC


def _doc_identity(doc):
    return {
        "path": _normalize_path(_get_doc_path(doc)),
        "title": _normalize_title(_get_doc_title(doc)),
    }


def _resolve_doc_for_state(uiapp, state):
    active_doc = _get_active_doc_from_uiapp(uiapp)
    identity = dict(state.get("doc", {}) or {})
    if _doc_matches_identity(active_doc, identity):
        return active_doc

    return active_doc if _is_doc_valid(active_doc) and not identity.get("path") and not identity.get("title") else None


def _doc_matches_identity(doc, identity):
    if not _is_doc_valid(doc):
        return False
    path = _normalize_path(_get_doc_path(doc))
    title = _normalize_title(_get_doc_title(doc))
    wanted_path = _safe_text(identity.get("path"))
    wanted_title = _safe_text(identity.get("title"))
    if wanted_path and path:
        return wanted_path == path
    if wanted_title and title:
        return wanted_title == title
    return False


def _get_active_doc_from_uiapp(uiapp):
    if uiapp is None:
        return None
    try:
        uidoc = uiapp.ActiveUIDocument
    except Exception:
        uidoc = None
    if uidoc is None:
        return None
    try:
        return uidoc.Document
    except Exception:
        return None


def _get_doc_path(doc):
    if not _is_doc_valid(doc):
        return ""
    try:
        return _safe_text(getattr(doc, "PathName", "") or "").strip()
    except Exception:
        return ""


def _normalize_path(value):
    return _safe_text(value).replace("\\", "/").strip().lower()


def _normalize_title(value):
    return _safe_text(value).strip().lower()


def _is_doc_valid(doc):
    if doc is None:
        return False
    try:
        return bool(doc.IsValidObject)
    except Exception:
        return True


def _load_native_state():
    try:
        from pyrevit import script
        state = script.get_envvar(_NATIVE_STATE_ENVVAR)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def _save_native_state(state):
    try:
        from pyrevit import script
        script.set_envvar(_NATIVE_STATE_ENVVAR, state)
        return True
    except Exception:
        return False


def _clear_native_state():
    try:
        from pyrevit import script
        script.set_envvar(_NATIVE_STATE_ENVVAR, {})
    except Exception:
        pass


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


def _show_native_report_window(report, doc=None, uiapp=None):
    try:
        from rltools.coordination_review_window import show_coordination_review_dialog
        return bool(show_coordination_review_dialog(report, doc=doc, uiapp=uiapp))
    except Exception:
        try:
            print("[RL Tools] Coordination Review report could not be displayed.")
        except Exception:
            pass
        return False


def build_native_coordination_report(doc, uiapp=None, timeout_sec=12.0):
    """Create and parse native Coordination Review reports for loaded links.

    This function is intentionally defensive: native Coordination Review data is
    only available through Revit UI/report behavior, so failures are returned as
    error-only report data for the WPF window.
    """
    doc_title = _get_doc_title(doc)
    cache_paths = []

    try:
        link_targets = _collect_link_targets(doc)
        if not link_targets:
            return build_report_from_native_rows(doc_title, [])

        cache_dir = coordination_review_cache_dir(doc_title)
        _ensure_dir(cache_dir)

        native_rows = []
        for index, target in enumerate(link_targets):
            report_path = make_report_path(cache_dir, target.get("name"), index=index)
            cache_paths.append(report_path)
            html_text = _create_native_report_for_link(
                doc=doc,
                uiapp=uiapp,
                link_target=target,
                report_path=report_path,
                timeout_sec=timeout_sec,
            )
            native_rows.extend(
                parse_native_coordination_review_html(
                    html_text,
                    link_name=target.get("name"),
                    link_key=target.get("key"),
                )
            )

        report = build_report_from_native_rows(doc_title, native_rows)
        delete_html_cache(cache_paths)
        return report
    except NativeCoordinationReviewError as ex:
        delete_html_cache(cache_paths)
        return build_error_report(doc_title, ex.stage, ex.message)
    except Exception as ex:
        delete_html_cache(cache_paths)
        return build_error_report(doc_title, "native_automation", _safe_text(ex))


def _get_doc_title(doc):
    try:
        title = getattr(doc, "Title", "")
        if title:
            return _safe_text(title)
    except Exception:
        pass
    return "(Unknown)"


def _ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def _element_id_int(element_id):
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


def _collect_link_targets(doc):
    if doc is None:
        raise NativeCoordinationReviewError("prepare_link", "No active Revit document was available.")

    try:
        from Autodesk.Revit.DB import FilteredElementCollector, RevitLinkInstance
        instances = (
            FilteredElementCollector(doc)
            .OfClass(RevitLinkInstance)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception as ex:
        raise NativeCoordinationReviewError("prepare_link", "Could not collect Revit links: {}".format(ex))

    targets = []
    for instance in list(instances or []):
        try:
            link_doc = instance.GetLinkDocument()
        except Exception:
            link_doc = None
        if link_doc is None:
            continue

        link_id_int = _element_id_int(getattr(instance, "Id", None))
        name = _safe_text(getattr(instance, "Name", "")) or "Link {}".format(link_id_int)
        targets.append(
            {
                "key": str(link_id_int) if link_id_int is not None else name,
                "name": name,
                "element_id": instance.Id,
                "element_id_int": link_id_int,
            }
        )

    return targets


def _create_native_report_for_link(doc, uiapp, link_target, report_path, timeout_sec):
    if uiapp is None:
        raise NativeCoordinationReviewError("post_coordination_select_link", "No Revit UI application was available.")

    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    if uidoc is None:
        raise NativeCoordinationReviewError("post_coordination_select_link", "No active Revit UI document was available.")

    _select_link_instance(uidoc, link_target.get("element_id"))
    _post_coordination_select_link(uiapp)

    automation = _load_ui_automation()
    review_window = _wait_for_window(automation, "Coordination Review", timeout_sec)
    if review_window is None:
        raise NativeCoordinationReviewError(
            "wait_for_native_dialog",
            "The native Coordination Review dialog did not open for {}.".format(link_target.get("name")),
        )

    if not _invoke_named_button(automation, review_window, "Create Report"):
        raise NativeCoordinationReviewError(
            "create_report",
            "The native Coordination Review dialog did not expose a Create Report button.",
        )

    save_window = _wait_for_any_window(automation, ["Save", "Report"], timeout_sec)
    if save_window is None:
        raise NativeCoordinationReviewError(
            "save_report",
            "The native report save dialog did not open.",
        )

    if not _set_first_edit_value(automation, save_window, report_path):
        raise NativeCoordinationReviewError(
            "save_report",
            "Could not set the native report path.",
        )

    if not (_invoke_named_button(automation, save_window, "Save") or _invoke_named_button(automation, save_window, "OK")):
        raise NativeCoordinationReviewError(
            "save_report",
            "Could not confirm the native report save dialog.",
        )

    if not _wait_for_file(report_path, timeout_sec):
        raise NativeCoordinationReviewError(
            "parse_report",
            "The native report file was not created at {}.".format(report_path),
        )

    try:
        with open(report_path, "r") as handle:
            html_text = handle.read()
    except Exception as ex:
        raise NativeCoordinationReviewError("parse_report", "Could not read native report HTML: {}".format(ex))

    _invoke_named_button(automation, review_window, "OK") or _invoke_named_button(automation, review_window, "Cancel")
    return html_text


def _select_link_instance(uidoc, element_id):
    if element_id is None:
        raise NativeCoordinationReviewError("prepare_link", "The Revit link instance did not have an element id.")

    try:
        from System.Collections.Generic import List as ClrList
        from Autodesk.Revit.DB import ElementId
        ids = ClrList[ElementId]()
        ids.Add(element_id)
        uidoc.Selection.SetElementIds(ids)
    except Exception as ex:
        raise NativeCoordinationReviewError("prepare_link", "Could not select the Revit link: {}".format(ex))


def _post_coordination_select_link(uiapp):
    try:
        from Autodesk.Revit.UI import PostableCommand, RevitCommandId
        command_id = RevitCommandId.LookupPostableCommandId(PostableCommand.CoordinationSelectLink)
    except Exception as ex:
        raise NativeCoordinationReviewError(
            "post_coordination_select_link",
            "Could not resolve Revit's CoordinationSelectLink command: {}".format(ex),
        )

    try:
        can_post = getattr(uiapp, "CanPostCommand", None)
        if can_post is not None and not can_post(command_id):
            raise NativeCoordinationReviewError(
                "post_coordination_select_link",
                "Revit would not allow CoordinationSelectLink to be posted in the current state.",
            )
        uiapp.PostCommand(command_id)
    except NativeCoordinationReviewError:
        raise
    except Exception as ex:
        raise NativeCoordinationReviewError("post_coordination_select_link", "Could not post native command: {}".format(ex))


def _load_ui_automation():
    try:
        import clr
        clr.AddReference("UIAutomationClient")
        clr.AddReference("UIAutomationTypes")
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Automation import (
            AutomationElement,
            Condition,
            ControlType,
            InvokePattern,
            PropertyCondition,
            TreeScope,
            ValuePattern,
        )
        import System.Windows.Forms as WinForms
        return {
            "AutomationElement": AutomationElement,
            "Condition": Condition,
            "ControlType": ControlType,
            "InvokePattern": InvokePattern,
            "PropertyCondition": PropertyCondition,
            "TreeScope": TreeScope,
            "ValuePattern": ValuePattern,
            "WinForms": WinForms,
        }
    except Exception as ex:
        raise NativeCoordinationReviewError("wait_for_native_dialog", "Could not load UI Automation: {}".format(ex))


def _pump_ui(automation):
    try:
        automation["WinForms"].Application.DoEvents()
    except Exception:
        pass
    time.sleep(0.1)


def _window_name(element):
    try:
        return _safe_text(element.Current.Name)
    except Exception:
        return ""


def _all_top_windows(automation):
    try:
        root = automation["AutomationElement"].RootElement
        condition = automation["Condition"].TrueCondition
        return list(root.FindAll(automation["TreeScope"].Children, condition))
    except Exception:
        return []


def _wait_for_window(automation, title_contains, timeout_sec):
    deadline = time.time() + float(timeout_sec)
    title_contains = _safe_text(title_contains).lower()
    while time.time() < deadline:
        for window in _all_top_windows(automation):
            if title_contains in _window_name(window).lower():
                return window
        _pump_ui(automation)
    return None


def _find_coordination_review_window(automation):
    for window in _all_top_windows(automation):
        if "coordination review" in _window_name(window).lower():
            return window
    return None


def _wait_for_any_window(automation, title_parts, timeout_sec):
    deadline = time.time() + float(timeout_sec)
    parts = [_safe_text(item).lower() for item in list(title_parts or []) if _safe_text(item)]
    while time.time() < deadline:
        for window in _all_top_windows(automation):
            name = _window_name(window).lower()
            if all(part in name for part in parts):
                return window
        _pump_ui(automation)
    return None


def _find_save_report_window(automation):
    for window in _all_top_windows(automation):
        name = _window_name(window).lower()
        if "coordination review" in name:
            continue
        edits = _find_controls_by_type(automation, window, automation["ControlType"].Edit)
        if not edits:
            continue
        if not (_has_named_button(automation, window, "Save") or _has_named_button(automation, window, "OK")):
            continue
        if "save" in name or "report" in name or "export" in name or "html" in name:
            return window
        return window
    return None


def _find_controls_by_type(automation, root, control_type):
    try:
        condition = automation["PropertyCondition"](
            automation["AutomationElement"].ControlTypeProperty,
            control_type,
        )
        return list(root.FindAll(automation["TreeScope"].Descendants, condition))
    except Exception:
        return []


def _has_named_button(automation, root, button_name):
    button_name = _safe_text(button_name).lower()
    buttons = _find_controls_by_type(automation, root, automation["ControlType"].Button)
    for button in buttons:
        if _window_name(button).lower() == button_name:
            return True
    return False


def _invoke_named_button(automation, root, button_name):
    button_name = _safe_text(button_name).lower()
    buttons = _find_controls_by_type(automation, root, automation["ControlType"].Button)
    for button in buttons:
        if _window_name(button).lower() != button_name:
            continue
        try:
            pattern = button.GetCurrentPattern(automation["InvokePattern"].Pattern)
            pattern.Invoke()
            _pump_ui(automation)
            return True
        except Exception:
            return False
    return False


def _close_coordination_review_window():
    try:
        automation = _load_ui_automation()
        review_window = _find_coordination_review_window(automation)
        if review_window is None:
            return
        (
            _invoke_named_button(automation, review_window, "OK")
            or _invoke_named_button(automation, review_window, "Close")
            or _invoke_named_button(automation, review_window, "Cancel")
        )
    except Exception:
        pass


def _set_first_edit_value(automation, root, value):
    edits = _find_controls_by_type(automation, root, automation["ControlType"].Edit)
    for edit in edits:
        try:
            pattern = edit.GetCurrentPattern(automation["ValuePattern"].Pattern)
            pattern.SetValue(_safe_text(value))
            _pump_ui(automation)
            return True
        except Exception:
            continue
    return False


def _wait_for_file(path, timeout_sec):
    deadline = time.time() + float(timeout_sec)
    while time.time() < deadline:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return True
        time.sleep(0.1)
    return False
