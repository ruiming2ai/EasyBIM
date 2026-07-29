# -*- coding: utf-8 -*-
"""Pure helpers for the Families Transfer pyRevit command."""

import os
import re


SUMMARY_DISPLAY_LIMIT = 20
INVALID_FILENAME_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


class FamilyOption(object):
    def __init__(self, name, family_key, is_selected=False, family=None, element_id=None):
        self.name = _safe_text(name) or "(Unnamed Family)"
        self.family_key = _safe_text(family_key)
        self.is_selected = bool(is_selected)
        self.family = family
        self.element_id = element_id

    def __str__(self):
        return self.name


class TargetDocumentOption(object):
    def __init__(self, display_name, document_key, is_selected=False, document=None):
        self.display_name = _safe_text(display_name) or "(Untitled Project)"
        self.document_key = _safe_text(document_key)
        self.is_selected = bool(is_selected)
        self.document = document

    def __str__(self):
        return self.display_name


class TransferResult(object):
    def __init__(self, family_name, target_name, status):
        self.family_name = _safe_text(family_name) or "(Unknown Family)"
        self.target_name = _safe_text(target_name) or "(No Target)"
        self.status = _safe_text(status)


class TransferSummary(object):
    def __init__(self, loaded=None, skipped=None, failed=None):
        self.loaded = list(loaded or [])
        self.skipped = list(skipped or [])
        self.failed = list(failed or [])


def restore_family_selection(families, selected_family_keys):
    selected_family_keys = set(selected_family_keys or [])
    for family in families or []:
        family.is_selected = family.family_key in selected_family_keys
    return families


def restore_document_selection(documents, selected_document_keys):
    selected_document_keys = set(selected_document_keys or [])
    for document in documents or []:
        document.is_selected = document.document_key in selected_document_keys
    return documents


def sort_family_options(families):
    return sorted(
        list(families or []),
        key=lambda family: _safe_text(getattr(family, "name", "")).lower(),
    )


def sort_target_documents(documents):
    return sorted(
        list(documents or []),
        key=lambda document: _safe_text(getattr(document, "display_name", "")).lower(),
    )


def sanitize_export_filename(family_name):
    cleaned = INVALID_FILENAME_PATTERN.sub("_", _safe_text(family_name)).strip(" ._")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        cleaned = "Family"
    if not cleaned.lower().endswith(".rfa"):
        cleaned = "{}.rfa".format(cleaned)
    return cleaned


def build_unique_export_path(folder_path, family_name, used_paths):
    used_paths = used_paths if used_paths is not None else set()
    file_name = sanitize_export_filename(family_name)
    base_name, extension = os.path.splitext(file_name)
    candidate = os.path.join(folder_path, file_name)
    index = 2

    while candidate.lower() in used_paths:
        candidate = os.path.join(folder_path, "{}_{}{}".format(base_name, index, extension))
        index += 1

    used_paths.add(candidate.lower())
    return candidate


def get_selected_family_keys(families):
    selected = []
    seen = set()
    for family in families or []:
        if not bool(getattr(family, "is_selected", False)):
            continue
        family_key = _safe_text(getattr(family, "family_key", ""))
        if not family_key or family_key in seen:
            continue
        seen.add(family_key)
        selected.append(family_key)
    return selected


def get_selected_document_keys(documents):
    selected = []
    seen = set()
    for document in documents or []:
        if not bool(getattr(document, "is_selected", False)):
            continue
        document_key = _safe_text(getattr(document, "document_key", ""))
        if not document_key or document_key in seen:
            continue
        seen.add(document_key)
        selected.append(document_key)
    return selected


def _append_result_lines(lines, title, results):
    results = list(results or [])
    if not results:
        return

    lines.append("")
    lines.append(title)
    for result in results[:SUMMARY_DISPLAY_LIMIT]:
        lines.append(
            "- {} -> {}: {}".format(
                _safe_text(getattr(result, "family_name", "")),
                _safe_text(getattr(result, "target_name", "")),
                _safe_text(getattr(result, "status", "")),
            )
        )

    if len(results) > SUMMARY_DISPLAY_LIMIT:
        lines.append("- Plus {} more.".format(len(results) - SUMMARY_DISPLAY_LIMIT))


def build_transfer_summary_text(summary):
    summary = summary or TransferSummary()
    loaded = list(summary.loaded or [])
    skipped = list(summary.skipped or [])
    failed = list(summary.failed or [])

    lines = [
        "Families Transfer completed.",
        "Loaded/overwritten: {}".format(len(loaded)),
        "Skipped: {}".format(len(skipped)),
        "Failed: {}".format(len(failed)),
    ]

    _append_result_lines(lines, "Loaded/overwritten:", loaded)
    _append_result_lines(lines, "Skipped:", skipped)
    _append_result_lines(lines, "Failed:", failed)
    return "\n".join(lines)
