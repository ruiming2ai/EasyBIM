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
                "show_label": "Show",
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
    }
