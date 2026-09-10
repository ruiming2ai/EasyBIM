# -*- coding: utf-8 -*-
"""Plain review rows; no Revit or WPF dependency."""
from __future__ import unicode_literals
import json
try:
    text_type = unicode
except NameError:
    text_type = str

STATUS_LABELS = {
    "unchanged":"Unchanged", "accepted":"Accepted", "source_changed":"Source changed",
    "local_changed":"Local changed", "conflict":"Both changed", "postponed":"Postponed",
    "placement_difference":"Placement differs", "conversion_required":"Conversion required",
    "parameter_exceptions":"Parameter exceptions", "link_unloaded":"Link unloaded",
    "link_missing":"Link missing", "link_replaced":"Link replaced", "source_missing":"Source missing",
    "destination_missing":"Destination missing", "nested_link_unavailable":"Nested link unavailable",
    "scan_error":"Check failed", "stopped":"Stopped",
}


def display(value):
    if value is None: return "(none)"
    if isinstance(value, dict):
        return value.get("name") or json.dumps(value, ensure_ascii=True, sort_keys=True)
    return text_type(value)


def details(report):
    record = report["record"]
    source = report.get("source") or {}
    destination = report.get("destination") or {}
    lines = [STATUS_LABELS.get(report["status"], report["status"])]
    if report.get("error"):
        lines.append(report["error"])
    if destination.get("independence"):
        lines.append(destination["independence"])
    for label, keys in (("Source changes",report.get("source_changes",[])),
                        ("Local changes",report.get("destination_changes",[]))):
        if keys: lines.append(label + ": " + ", ".join(keys))
    old = record.get("baseline_source") or {}
    old_local = record.get("baseline_destination") or {}
    keys = sorted(set(report.get("source_changes",[])) | set(report.get("destination_changes",[])))
    for key in keys:
        if key in source.get("params",{}) or key in destination.get("params",{}):
            label = source.get("parameter_labels",{}).get(key,key)
            def value(snap):
                return snap.get("parameter_display",{}).get(key,display(snap.get("params",{}).get(key)))
            lines.append("{}: prior source={} | current source={} | prior local={} | current local={}".format(
                label,value(old),value(source),value(old_local),value(destination)))
    for issue in record.get("parameter_issues",[]):
        lines.append("{}: {}".format(issue.get("name",issue.get("key","Parameter")),issue["reason"]))
    if report.get("expected"):
        lines.append("Expected position (internal feet): " + display(report["expected"]["origin"]))
    if destination.get("frame"):
        lines.append("Actual position (internal feet): " + display(destination["frame"]["origin"]))
    return "\n".join(lines)


class ReviewRow(object):
    def __init__(self, report):
        self.report = report
        self.Checked = False
        self.StatusKey = report["status"]
        self.Status = STATUS_LABELS.get(self.StatusKey,self.StatusKey)
        record = report["record"]
        source = report.get("source") or record.get("baseline_source") or {}
        self.Link = record.get("link_label",record["link_uid"])
        self.Family = source.get("family","Unavailable")
        self.Type = source.get("type_label","")
        self.Source = str(source.get("element_id",record["source_uid"]))
        dest = report.get("destination") or record.get("baseline_destination") or {}
        self.Destination = str(dest.get("element_id",record["destination_uid"]))
        self.Changes = ", ".join(report.get("source_changes",[]) + report.get("destination_changes",[]))
        self.Details = details(report)


def build_rows(reports):
    return sorted([ReviewRow(r) for r in reports],
                  key=lambda r:(r.Status,r.Link,r.Family,r.Type,r.Source))


def filter_rows(rows, filter_name):
    if filter_name == "Pending":
        return [r for r in rows if r.StatusKey not in ("unchanged","accepted","stopped")]
    if filter_name == "All":
        return list(rows)
    return [r for r in rows if r.Status == filter_name]


def scan_summary(scan):
    prefix = "Cancelled: checked" if scan.get("cancelled") else "Checked"
    return "{} {} of {} relationships. {}".format(prefix,scan["completed"],scan["total"],
                                                   scan.get("checked",""))
