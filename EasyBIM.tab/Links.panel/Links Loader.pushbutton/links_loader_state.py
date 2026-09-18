# -*- coding: utf-8 -*-
"""Pure-Python logic for the Links Loader tool.

No Revit imports -- desktop-testable.
"""

import json
import os
import time


FORMAT_NAME = "easybim-links-loader"
SCHEMA_VERSION = 1

SUPPORTED_TYPES = ("RevitLinkType", "CADLinkType")
UPDATABLE_TYPES = ("RevitLinkType",)


class LinkRecord(object):
    __slots__ = ("name", "element_type", "path", "path_type", "is_loaded")

    def __init__(self, name, element_type, path, path_type="", is_loaded=True):
        self.name = name
        self.element_type = element_type
        self.path = path
        self.path_type = path_type
        self.is_loaded = is_loaded

    def to_dict(self):
        return {
            "name": self.name,
            "element_type": self.element_type,
            "path": self.path,
            "path_type": self.path_type,
            "is_loaded": self.is_loaded,
        }


class ImportPlanItem(object):
    __slots__ = (
        "name", "element_type", "old_path", "new_path",
        "status", "is_selected", "file_missing",
    )

    def __init__(self, name, element_type, old_path, new_path, status):
        self.name = name
        self.element_type = element_type
        self.old_path = old_path
        self.new_path = new_path
        self.status = status
        self.is_selected = status == "update"
        self.file_missing = False


def build_export_data(link_records, revit_version, project_name):
    return {
        "format": FORMAT_NAME,
        "schema_version": SCHEMA_VERSION,
        "exported": {
            "revit_version": str(revit_version or ""),
            "project_name": str(project_name or ""),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "links": [r.to_dict() for r in link_records],
    }


def _validate_import_data(raw):
    if not isinstance(raw, dict):
        return "The file does not contain a valid JSON object."
    fmt = raw.get("format", "")
    if fmt != FORMAT_NAME:
        return (
            "Unrecognised file format: '{}'. "
            "Expected a Links Loader export ('{}')."
        ).format(fmt, FORMAT_NAME)
    ver = raw.get("schema_version")
    if not isinstance(ver, int):
        return "Missing or invalid 'schema_version'."
    if ver > SCHEMA_VERSION:
        return (
            "This file was written by a newer Links Loader "
            "(schema version {}). Please update EasyBIM."
        ).format(ver)
    links = raw.get("links")
    if not isinstance(links, list):
        return "Missing or invalid 'links' array."
    for i, entry in enumerate(links):
        if not isinstance(entry, dict):
            return "Link entry {} is not an object.".format(i)
        if "name" not in entry or "path" not in entry:
            return "Link entry {} is missing 'name' or 'path'.".format(i)
    return ""


def parse_import_data(raw):
    error = _validate_import_data(raw)
    if error:
        return ([], error)
    records = []
    for entry in raw.get("links", []):
        records.append(LinkRecord(
            name=str(entry.get("name", "")),
            element_type=str(entry.get("element_type", "")),
            path=str(entry.get("path", "")),
            path_type=str(entry.get("path_type", "")),
            is_loaded=bool(entry.get("is_loaded", True)),
        ))
    return (records, "")


def build_import_plan(current_links, imported_links):
    current_by_name = {}
    for rec in current_links:
        current_by_name[rec.name.lower()] = rec

    plan = []
    for imp in imported_links:
        key = imp.name.lower()
        cur = current_by_name.get(key)
        if cur is None:
            plan.append(ImportPlanItem(
                imp.name, imp.element_type, "", imp.path,
                "not_found_in_document",
            ))
            continue
        if cur.element_type not in UPDATABLE_TYPES:
            plan.append(ImportPlanItem(
                cur.name, cur.element_type, cur.path, imp.path,
                "unsupported_type",
            ))
            continue
        if _paths_equal(cur.path, imp.path):
            plan.append(ImportPlanItem(
                cur.name, cur.element_type, cur.path, imp.path,
                "unchanged",
            ))
            continue
        plan.append(ImportPlanItem(
            cur.name, cur.element_type, cur.path, imp.path,
            "update",
        ))
    return plan


def _paths_equal(a, b):
    if not a or not b:
        return a == b
    return os.path.normcase(os.path.normpath(a)) == \
        os.path.normcase(os.path.normpath(b))


def check_file_exists(path):
    if not path:
        return False
    return os.path.isfile(path)


def build_result_summary(results):
    succeeded = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    lines = []
    if succeeded:
        lines.append("{} link(s) updated successfully.".format(succeeded))
    if failed:
        lines.append("{} link(s) failed:".format(failed))
        for name, ok, err in results:
            if not ok:
                lines.append("  - {}: {}".format(name, err or "Unknown error"))
    if not lines:
        lines.append("No links were updated.")
    return "\n".join(lines)


STATUS_LABELS = {
    "update": "Will update",
    "unchanged": "Unchanged",
    "not_found_in_document": "Not in document",
    "unsupported_type": "Not supported",
}


def status_label(status):
    return STATUS_LABELS.get(status, status)


def count_updatable(plan_items):
    return sum(1 for item in plan_items
               if item.status == "update" and item.is_selected)


def dump_json(data):
    return json.dumps(data, indent=2, ensure_ascii=True)
