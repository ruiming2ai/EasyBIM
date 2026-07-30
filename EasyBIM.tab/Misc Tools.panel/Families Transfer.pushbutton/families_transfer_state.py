# -*- coding: utf-8 -*-
"""Pure helpers for the Families Transfer pyRevit command."""

import os
import re


SUMMARY_DISPLAY_LIMIT = 20
INVALID_FILENAME_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
SOURCE_PROJECT = "project"
SOURCE_OPEN_RFA = "open_rfa"
PROJECT_FAMILY_KEY_PREFIX = "project|"
OPEN_RFA_FAMILY_KEY_PREFIX = "open-rfa|"
UNKNOWN_CATEGORY = "Unknown Category"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def normalize_category_name(category_name):
    category_name = _safe_text(category_name).strip()
    return category_name or UNKNOWN_CATEGORY


class FamilyOption(object):
    def __init__(
        self,
        name,
        family_key,
        is_selected=False,
        family=None,
        element_id=None,
        source_kind=SOURCE_PROJECT,
        family_document=None,
        document_key=None,
        category_name=None,
    ):
        self.name = _safe_text(name) or "(Unnamed Family)"
        self.family_key = _safe_text(family_key)
        self.is_selected = bool(is_selected)
        self.family = family
        self.element_id = element_id
        self.source_kind = source_kind or SOURCE_PROJECT
        self.family_document = family_document
        self.document_key = _safe_text(document_key)
        self.category_name = normalize_category_name(category_name)

    def __str__(self):
        return self.name


class OpenFamilyDocumentOption(object):
    def __init__(self, display_name, document_key, is_selected=False, document=None, category_name=None):
        self.display_name = _safe_text(display_name) or "(Untitled Family)"
        self.document_key = _safe_text(document_key)
        self.is_selected = bool(is_selected)
        self.document = document
        self.category_name = normalize_category_name(category_name)

    def __str__(self):
        return self.display_name


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
    def __init__(self, loaded=None, skipped=None, failed=None, closed_rfa_count=0):
        self.loaded = list(loaded or [])
        self.skipped = list(skipped or [])
        self.failed = list(failed or [])
        self.closed_rfa_count = int(closed_rfa_count or 0)


class FamilyCategoryGroup(object):
    def __init__(self, category_name, families=None):
        self.category_name = normalize_category_name(category_name)
        self.families = list(families or [])


def make_project_family_key(raw_family_key):
    raw_family_key = _safe_text(raw_family_key)
    if raw_family_key.startswith(PROJECT_FAMILY_KEY_PREFIX):
        return raw_family_key
    return "{}{}".format(PROJECT_FAMILY_KEY_PREFIX, raw_family_key)


def make_open_family_document_key(document_key):
    document_key = _safe_text(document_key)
    if document_key.startswith(OPEN_RFA_FAMILY_KEY_PREFIX):
        return document_key
    return "{}{}".format(OPEN_RFA_FAMILY_KEY_PREFIX, document_key)


def is_project_family_key(family_key):
    return _safe_text(family_key).startswith(PROJECT_FAMILY_KEY_PREFIX)


def is_open_family_document_key(family_key):
    return _safe_text(family_key).startswith(OPEN_RFA_FAMILY_KEY_PREFIX)


def open_family_document_key_from_family_key(family_key):
    family_key = _safe_text(family_key)
    if not is_open_family_document_key(family_key):
        return ""
    return family_key[len(OPEN_RFA_FAMILY_KEY_PREFIX):]


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


def restore_open_family_document_selection(documents, selected_document_keys):
    selected_document_keys = set(selected_document_keys or [])
    for document in documents or []:
        document.is_selected = document.document_key in selected_document_keys
    return documents


def _category_sort_key(category_name):
    category_name = normalize_category_name(category_name)
    return (category_name == UNKNOWN_CATEGORY, category_name.lower())


def sort_family_options(families):
    return sorted(
        list(families or []),
        key=lambda family: (
            _category_sort_key(getattr(family, "category_name", "")),
            _safe_text(getattr(family, "name", "")).lower(),
        ),
    )


def sort_target_documents(documents):
    return sorted(
        list(documents or []),
        key=lambda document: _safe_text(getattr(document, "display_name", "")).lower(),
    )


def sort_open_family_documents(documents):
    return sorted(
        list(documents or []),
        key=lambda document: (
            _category_sort_key(getattr(document, "category_name", "")),
            _safe_text(getattr(document, "display_name", "")).lower(),
        ),
    )


def get_selected_source_family_options(families, selected_family_keys):
    selected_family_keys = set(selected_family_keys or [])
    selected = []
    for family in families or []:
        family_key = _safe_text(getattr(family, "family_key", ""))
        if family_key in selected_family_keys:
            selected.append(family)
    return sort_family_options(selected)


def group_family_options_by_category(families):
    grouped = {}
    for family in sort_family_options(families):
        category_name = normalize_category_name(getattr(family, "category_name", ""))
        grouped.setdefault(category_name, []).append(family)

    groups = []
    for category_name in sorted(grouped.keys(), key=_category_sort_key):
        groups.append(FamilyCategoryGroup(category_name, grouped[category_name]))
    return groups


def _matches_family_search(name, category_name, search_text):
    search_text = _safe_text(search_text).strip().lower()
    if not search_text:
        return True
    return search_text in _safe_text(name).lower() or search_text in _safe_text(category_name).lower()


def filter_family_options(families, search_text):
    return [
        family
        for family in list(families or [])
        if _matches_family_search(
            getattr(family, "name", ""),
            getattr(family, "category_name", ""),
            search_text,
        )
    ]


def filter_open_family_document_options(documents, search_text):
    return [
        document
        for document in list(documents or [])
        if _matches_family_search(
            getattr(document, "display_name", ""),
            getattr(document, "category_name", ""),
            search_text,
        )
    ]


def merge_transferable_family_options(project_families, open_family_documents):
    merged = list(project_families or [])
    for document in list(open_family_documents or []):
        if not bool(getattr(document, "is_selected", False)):
            continue
        document_key = _safe_text(getattr(document, "document_key", ""))
        if not document_key:
            continue
        merged.append(
            FamilyOption(
                getattr(document, "display_name", ""),
                make_open_family_document_key(document_key),
                is_selected=True,
                source_kind=SOURCE_OPEN_RFA,
                family_document=getattr(document, "document", None),
                document_key=document_key,
                category_name=getattr(document, "category_name", None),
            )
        )
    return sort_family_options(merged)


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


def get_selected_open_family_document_keys(documents):
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

    closed_rfa_count = int(getattr(summary, "closed_rfa_count", 0) or 0)
    lines = [
        "Families Transfer completed.",
        "Loaded/overwritten: {}".format(len(loaded)),
        "Skipped: {}".format(len(skipped)),
        "Failed: {}".format(len(failed)),
    ]
    if closed_rfa_count:
        lines.append("Closed .rfa files: {}".format(closed_rfa_count))

    _append_result_lines(lines, "Loaded/overwritten:", loaded)
    _append_result_lines(lines, "Skipped:", skipped)
    _append_result_lines(lines, "Failed:", failed)
    return "\n".join(lines)
