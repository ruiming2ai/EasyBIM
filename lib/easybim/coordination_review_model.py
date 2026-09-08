# -*- coding: utf-8 -*-
"""Pure helpers for the Coordination Review WPF window."""

ALL_LINKS_FILTER_VALUE = "__ALL_LINKS__"
ALL_LINKS_FILTER_LABEL = "All problem links"
ALL_ISSUES_FILTER_VALUE = "__ALL_ISSUES__"
ALL_ISSUES_FILTER_LABEL = "All issue types"
UNMAPPED_LINK_KEY = "__UNMAPPED__"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        if default is None:
            return None
        return int(default)


def _normalize_instance_ids(instance_ids):
    normalized = []
    seen = set()

    for raw_value in list(instance_ids or []):
        value = _safe_int(raw_value, default=None)
        if value is None or value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    normalized.sort()
    return normalized


def _normalize_link_name(link_key, link_map):
    if link_key == UNMAPPED_LINK_KEY:
        return "Unmapped"
    link_record = dict((link_map or {}).get(link_key, {}) or {})
    name = _safe_text(link_record.get("name"))
    if name:
        return name
    return "Unknown Link"


def _build_issue_record(issue_text, row, instance_cap):
    count = _safe_int((row or {}).get("count", 0))
    if count <= 0:
        return None

    normalized_ids = _normalize_instance_ids((row or {}).get("instance_ids", []))
    visible_ids = list(normalized_ids[:max(_safe_int(instance_cap, 0), 0)])
    overflow_count = max(len(normalized_ids) - len(visible_ids), 0)

    return {
        "text": _safe_text(issue_text) or "(No description)",
        "count": count,
        "total_instances": len(normalized_ids),
        "instance_rows": [
            {
                "instance_id": instance_id,
                "label": str(instance_id),
                "show_label": "View Issues",
            }
            for instance_id in visible_ids
        ],
        "overflow_count": overflow_count,
        "overflow_label": "+{} more".format(overflow_count) if overflow_count else "",
        "is_expanded": True,
    }


def build_problem_link_records(report, instance_cap=200):
    link_map = dict((report or {}).get("link_map", {}) or {})
    grouped = dict((report or {}).get("grouped", {}) or {})
    link_totals = dict((report or {}).get("link_totals", {}) or {})

    problem_links = []
    for link_key, warning_bucket in grouped.items():
        warning_bucket = dict(warning_bucket or {})
        issue_records = []
        for issue_text, row in warning_bucket.items():
            issue_record = _build_issue_record(issue_text, row, instance_cap)
            if issue_record is not None:
                issue_records.append(issue_record)

        if not issue_records:
            continue

        issue_records.sort(key=lambda item: (-item["count"], item["text"].lower()))
        total = _safe_int(link_totals.get(link_key, 0))
        if total <= 0:
            total = sum(item["count"] for item in issue_records)

        problem_links.append(
            {
                "key": link_key,
                "filter_value": str(link_key),
                "name": _normalize_link_name(link_key, link_map),
                "total": total,
                "visible_total": total,
                "is_expanded": True,
                "issues": issue_records,
            }
        )

    problem_links.sort(key=lambda item: (-item["total"], item["name"].lower()))
    return problem_links


KIND_PROBLEM = "problem"
KIND_ERROR = "error"
KIND_CLEAN = "clean"

#: Problems first, then links that could not be checked, then clean ones.
_KIND_ORDER = {KIND_PROBLEM: 0, KIND_ERROR: 1, KIND_CLEAN: 2}


def _link_kind(row):
    if _safe_text((row or {}).get("error")):
        return KIND_ERROR
    if _safe_int((row or {}).get("issue_count", 0)) > 0:
        return KIND_PROBLEM
    return KIND_CLEAN


def _link_status_text(row, kind):
    monitoring = _safe_int((row or {}).get("monitoring_count", 0))
    monitored = "{0} monitored element{1}".format(monitoring, "" if monitoring == 1 else "s")

    if kind == KIND_ERROR:
        return _safe_text(row.get("error")) or "This link could not be checked."
    if kind == KIND_CLEAN:
        return "No differences found ({0}).".format(monitored)

    issues = _safe_int(row.get("issue_count", 0))
    estimated = _safe_int(row.get("estimated_count", 0))
    text = "{0} difference{1} found ({2}).".format(
        issues, "" if issues == 1 else "s", monitored
    )
    if estimated:
        text += " {0} estimated by nearest-element matching.".format(estimated)
    return text


def build_checked_link_records(report):
    """UI records for the links EasyBIM compared, worst first.

    Every link something monitors gets a row, whether or not it has
    differences: a clean row is a *proved* result, which is the point of
    comparing instead of waiting for Revit's one-shot warning.
    """
    records = []
    for row in list((report or {}).get("links", []) or []):
        row = dict(row or {})
        kind = _link_kind(row)
        issues = _safe_int(row.get("issue_count", 0))
        records.append(
            {
                "key": _safe_text(row.get("link_id")),
                "filter_value": _safe_text(row.get("link_id")),
                "link_id": row.get("link_id"),
                "name": _safe_text(row.get("name")) or "Unknown Link",
                "kind": kind,
                "issue_count": issues,
                "estimated_count": _safe_int(row.get("estimated_count", 0)),
                "monitoring_count": _safe_int(row.get("monitoring_count", 0)),
                "flagged_by_revit": bool(row.get("flagged_by_revit")),
                "error": _safe_text(row.get("error")),
                "status_text": _link_status_text(row, kind),
                "badge_text": (
                    "{0} difference(s)".format(issues)
                    if kind == KIND_PROBLEM
                    else ("not checked" if kind == KIND_ERROR else "no differences")
                ),
                "is_expanded": kind == KIND_PROBLEM,
                "groups": list((row.get("report") or {}).get("groups", []) or []),
                "report": dict(row.get("report") or {}),
            }
        )

    records.sort(
        key=lambda item: (
            _KIND_ORDER.get(item["kind"], 3),
            -item["issue_count"],
            item["name"].lower(),
        )
    )
    return records


def summarize_checked_links(report):
    """Headline and empty-state text for a computed report."""
    report = dict(report or {})
    monitored = _safe_int(report.get("monitored_link_count", 0))
    checked = _safe_int(report.get("checked_count", 0))
    problems = _safe_int(report.get("problem_count", 0))
    issues = _safe_int(report.get("total_issues", 0))
    cancelled = bool(report.get("cancelled"))

    if monitored == 0:
        return {
            "is_empty": True,
            "headline": "Nothing in this model uses Copy/Monitor.",
            "detail": (
                "Coordination Review only reports changes to elements copied or monitored "
                "from a link, and this model monitors none, so there is nothing to review."
            ),
            "status": "Nothing in this model uses Copy/Monitor.",
        }

    links_text = "{0} monitored link{1}".format(checked, "" if checked == 1 else "s")
    if problems == 0 and not cancelled:
        return {
            "is_empty": True,
            "headline": "No differences found across {0}.".format(links_text),
            "detail": (
                "Every element this model monitors was compared with its counterpart in "
                "the link. Nothing needs Coordination Review."
            ),
            "status": "No differences found across {0}.".format(links_text),
        }

    status = "{0} difference{1} across {2} of {3}.".format(
        issues,
        "" if issues == 1 else "s",
        "{0} link{1}".format(problems, "" if problems == 1 else "s"),
        links_text,
    )
    if cancelled:
        status += " Checking was cancelled, so later links were not compared."
    return {"is_empty": False, "headline": status, "detail": "", "status": status}


def build_link_filter_options(problem_links):
    options = [
        {
            "value": ALL_LINKS_FILTER_VALUE,
            "label": ALL_LINKS_FILTER_LABEL,
        }
    ]

    ordered_links = sorted(
        list(problem_links or []),
        key=lambda item: item.get("name", "").lower(),
    )
    for item in ordered_links:
        options.append(
            {
                "value": item.get("filter_value"),
                "label": item.get("name", ""),
            }
        )
    return options


def build_issue_filter_options(problem_links):
    labels = set()
    for link_record in list(problem_links or []):
        for issue_record in list(link_record.get("issues", []) or []):
            label = _safe_text(issue_record.get("text"))
            if label:
                labels.add(label)

    ordered_labels = sorted(labels, key=lambda item: item.lower())
    options = [
        {
            "value": ALL_ISSUES_FILTER_VALUE,
            "label": ALL_ISSUES_FILTER_LABEL,
        }
    ]
    for label in ordered_labels:
        options.append({"value": label, "label": label})
    return options


def apply_coordination_review_filters(
    problem_links,
    selected_link_value=None,
    selected_issue_value=None,
):
    selected_link_value = _safe_text(selected_link_value) or ALL_LINKS_FILTER_VALUE
    selected_issue_value = _safe_text(selected_issue_value) or ALL_ISSUES_FILTER_VALUE

    filtered_links = []
    for link_record in list(problem_links or []):
        if (
            selected_link_value != ALL_LINKS_FILTER_VALUE
            and _safe_text(link_record.get("filter_value")) != selected_link_value
        ):
            continue

        visible_issues = []
        for issue_record in list(link_record.get("issues", []) or []):
            if (
                selected_issue_value != ALL_ISSUES_FILTER_VALUE
                and _safe_text(issue_record.get("text")) != selected_issue_value
            ):
                continue
            visible_issues.append(dict(issue_record))

        if not visible_issues:
            continue

        visible_total = sum(_safe_int(item.get("count", 0)) for item in visible_issues)
        filtered_link = dict(link_record)
        filtered_link["issues"] = visible_issues
        filtered_link["visible_total"] = visible_total
        filtered_links.append(filtered_link)

    return filtered_links


def build_coordination_review_view_model(
    report,
    selected_link_value=None,
    selected_issue_value=None,
    instance_cap=200,
):
    problem_links = build_problem_link_records(report, instance_cap=instance_cap)
    filtered_links = apply_coordination_review_filters(
        problem_links,
        selected_link_value=selected_link_value,
        selected_issue_value=selected_issue_value,
    )

    if not filtered_links:
        filtered_links = []

    return {
        "doc_title": _safe_text((report or {}).get("doc_title")) or "(Unknown)",
        "matching_warnings": _safe_int((report or {}).get("total_matching_warnings", 0)),
        "link_assignments": _safe_int((report or {}).get("total_link_assignments", 0)),
        "problem_link_count": len(problem_links),
        "visible_link_count": len(filtered_links),
        "selected_link_value": _safe_text(selected_link_value) or ALL_LINKS_FILTER_VALUE,
        "selected_issue_value": _safe_text(selected_issue_value) or ALL_ISSUES_FILTER_VALUE,
        "link_filter_options": build_link_filter_options(problem_links),
        "issue_filter_options": build_issue_filter_options(problem_links),
        "links": filtered_links,
        "is_empty": len(problem_links) == 0,
        "detection_error": bool((report or {}).get("detection_error", False)),
    }
