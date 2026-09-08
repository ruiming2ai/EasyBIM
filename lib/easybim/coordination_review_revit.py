# -*- coding: utf-8 -*-
"""Check every monitored link and report what changed.

Revit raises the "needs Coordination Review" warning **once**, while a link
loads, and it cannot be re-triggered afterwards.  Anything that depends on
catching that single event will keep missing it - a model already open when
EasyBIM loaded, a listener attached in the wrong engine, a cloud model that
loads its links on a schedule of its own.

So the comparison is the detector: EasyBIM finds the links this model
monitors with Copy/Monitor and compares each of them itself.  A clean result
is then *proved* rather than inferred from a warning that never arrived, and
Revit's warning becomes only a hint (``flagged_by_revit``) on top.

Links that nothing monitors are skipped entirely: without a Copy/Monitor
relationship a link has nothing for Coordination Review to report.

The Revit API is imported lazily by the modules this one calls, so it loads
standalone under CPython for the unit tests.
"""

from easybim import coordination_review_diff_revit as diff_revit
from easybim import coordination_review_show


SOURCE = "computed"


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


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _doc_title(doc):
    try:
        return _safe_text(doc.Title)
    except Exception:
        return ""


def flagged_link_ids(passive_report):
    """Link ids Revit's own warning flagged, when the listener caught it.

    Only a hint now: it decorates a row, it never decides whether a link is
    checked.
    """
    flagged = set()
    link_map = dict((passive_report or {}).get("link_map", {}) or {})
    for entry in link_map.values():
        value = _safe_int((entry or {}).get("element_id"), default=None)
        if value is not None:
            flagged.add(value)
    return flagged


def _link_row(link_id, monitoring_count, flagged):
    return {
        "link_id": link_id,
        "name": "",
        "monitoring_count": _safe_int(monitoring_count),
        "issue_count": 0,
        "estimated_count": 0,
        "error": "",
        "flagged_by_revit": bool(flagged),
        "report": {},
    }


def build_document_review_report(
    doc,
    passive_report=None,
    progress=None,
    db=None,
    collect_link_ids=None,
    build_link_report=None,
    resolve_link=None,
):
    """Compare every monitored link in ``doc`` and summarise the result.

    ``progress`` is an :class:`easybim.progress.ProgressSession` (or anything
    with ``update(done, total)`` and ``cancelled``); it ticks once per link and
    a cancelled run reports what it finished rather than nothing.  The
    injection points exist so the unit tests can drive this without Revit.
    """
    report = {
        "doc_title": _doc_title(doc),
        "source": SOURCE,
        "monitored_link_count": 0,
        "checked_count": 0,
        "problem_count": 0,
        "total_issues": 0,
        "links": [],
        "cancelled": False,
        "capture": dict((passive_report or {}).get("capture", {}) or {}),
        "detection_error": False,
    }
    if doc is None:
        report["detection_error"] = True
        return report

    # Resolved only once there is work: the Revit-facing modules import the
    # API lazily, and nothing should touch them for an absent document.
    collect_link_ids = collect_link_ids or diff_revit.collect_monitored_link_ids
    build_link_report = build_link_report or diff_revit.build_link_issue_report
    resolve_link = resolve_link or coordination_review_show.resolve_link_instance

    counts = dict(collect_link_ids(doc, db=db) or {})
    report["monitored_link_count"] = len(counts)
    if not counts:
        return report

    flagged = flagged_link_ids(passive_report)
    # Busiest links first: the ones with the most monitored elements are the
    # ones a cancelled run most wants to have finished.
    ordered = sorted(counts.items(), key=lambda item: (-_safe_int(item[1]), item[0]))
    total = len(ordered)

    for index, (link_id, monitoring_count) in enumerate(ordered):
        if progress is not None and getattr(progress, "cancelled", False):
            report["cancelled"] = True
            break

        row = _link_row(link_id, monitoring_count, link_id in flagged)
        try:
            link_instance, error = resolve_link(doc, link_id, db=db)
        except Exception as ex:
            link_instance, error = None, _safe_text(ex) or "Could not resolve the link."

        if link_instance is None:
            row["error"] = _safe_text(error) or "Could not resolve the link."
        else:
            row["name"] = _safe_text(getattr(link_instance, "Name", ""))
            try:
                link_report = dict(build_link_report(doc, link_instance, db=db) or {})
            except Exception as ex:
                link_report = {"error": _safe_text(ex) or "Comparison failed."}
            row["report"] = link_report
            row["name"] = _safe_text(link_report.get("link_name")) or row["name"]
            row["error"] = _safe_text(link_report.get("error"))
            row["issue_count"] = _safe_int(link_report.get("issue_count"))
            row["estimated_count"] = _safe_int(link_report.get("estimated_count"))

        if not row["name"]:
            row["name"] = "Link {0}".format(link_id)

        report["links"].append(row)
        report["checked_count"] += 1
        if not row["error"]:
            report["total_issues"] += row["issue_count"]
            if row["issue_count"]:
                report["problem_count"] += 1

        if progress is not None:
            try:
                progress.update(index + 1, total)
            except Exception:
                pass

    return report
