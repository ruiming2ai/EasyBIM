# -*- coding: utf-8 -*-
"""Pure helpers that shape a computed Coordination Review report for display.

One report model, used by every surface: the WPF summary window and the
console fallbacks in ``messages.py`` all render these records, so they cannot
drift apart and tell the user different things about the same model.
"""


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
