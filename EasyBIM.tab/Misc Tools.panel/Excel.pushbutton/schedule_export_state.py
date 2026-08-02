# -*- coding: utf-8 -*-
"""Pure state helpers for the Export Schedules to Excel command."""

# pylint: disable=import-error,invalid-name,broad-except
import re


TOKEN_PREFIX = "EBIMORD:"
TOKEN_PATTERN = re.compile(r"EBIMORD:(\d+)")
INVALID_FILENAME_PATTERN = re.compile(r"[<>:\"/\\|?*\x00-\x1f]+")

KIND_REGULAR = "regular"
KIND_KEY = "key"
KIND_SHEET_LIST = "sheet_list"

DEFAULT_EXPORT_BASENAME = "Schedule Export"
SUMMARY_DISPLAY_LIMIT = 20


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except Exception:
        return fallback


class ScheduleOption(object):
    """Schedule shown in the selection window."""

    def __init__(self, schedule, name, kind=KIND_REGULAR, is_selected=False):
        self.schedule = schedule
        self.name = _safe_text(name)
        self.kind = _safe_text(kind) or KIND_REGULAR
        self.is_selected = bool(is_selected)


class ScheduleExportResult(object):
    """Outcome of exporting one schedule."""

    def __init__(self, name, ok=False, path="", row_count=0,
                 ordering_note="", message=""):
        self.name = _safe_text(name)
        self.ok = bool(ok)
        self.path = _safe_text(path)
        self.row_count = _safe_int(row_count)
        self.ordering_note = _safe_text(ordering_note)
        self.message = _safe_text(message)


def schedule_display_text(option):
    name = _safe_text(getattr(option, "name", ""))
    kind = _safe_text(getattr(option, "kind", ""))
    if kind == KIND_KEY:
        return "{}  (key schedule)".format(name)
    if kind == KIND_SHEET_LIST:
        return "{}  (sheet list)".format(name)
    return name


def filter_schedule_options(options, search_text):
    search = _safe_text(search_text).strip().lower()
    if not search:
        return list(options or [])
    return [
        option for option in options or []
        if search in _safe_text(getattr(option, "name", "")).lower()
    ]


def get_selected_options(options):
    return [
        option for option in options or []
        if bool(getattr(option, "is_selected", False))
    ]


def build_token(element_id_value):
    return "{}{}".format(TOKEN_PREFIX, _safe_int(element_id_value))


def parse_ordered_ids(lines):
    """Return element id values in the order their tokens appear in the export."""
    ordered_ids = []
    seen = set()
    for line in lines or []:
        for match in TOKEN_PATTERN.finditer(_safe_text(line)):
            id_value = _safe_int(match.group(1), fallback=None)
            if id_value is None or id_value in seen:
                continue
            seen.add(id_value)
            ordered_ids.append(id_value)
    return ordered_ids


def order_elements_by_ids(element_id_pairs, ordered_ids):
    """Reorder (id_value, element) pairs by ordered_ids.

    Returns (ordered_elements, unmatched_elements). Unmatched elements keep
    their original (collector) order.
    """
    element_by_id = {}
    for id_value, element in element_id_pairs or []:
        if id_value is not None and id_value not in element_by_id:
            element_by_id[id_value] = element

    ordered_elements = []
    matched_ids = set()
    for id_value in ordered_ids or []:
        if id_value in element_by_id and id_value not in matched_ids:
            ordered_elements.append(element_by_id[id_value])
            matched_ids.add(id_value)

    unmatched_elements = [
        element for id_value, element in element_id_pairs or []
        if id_value not in matched_ids
    ]
    return ordered_elements, unmatched_elements


def strip_rvt_suffix(title):
    text = _safe_text(title).strip()
    if text.lower().endswith(".rvt"):
        text = text[:-4]
    return text.strip()


def _fallback_clean_filename(value):
    cleaned = INVALID_FILENAME_PATTERN.sub("", _safe_text(value))
    return cleaned.strip(" .")


def build_export_filename(schedule_name, model_name, cleaner=None,
                          extension=".xlsx"):
    """Build "<Schedule Name> - <Model Name>{ext}" with a safe basename."""
    base = "{} - {}".format(
        _safe_text(schedule_name).strip(),
        _safe_text(model_name).strip(),
    )
    cleaned = ""
    if cleaner is not None:
        try:
            cleaned = _safe_text(cleaner(base))
        except Exception:
            cleaned = ""
    if not cleaned:
        cleaned = base
    cleaned = _fallback_clean_filename(cleaned)
    if not cleaned.strip(" -."):
        cleaned = DEFAULT_EXPORT_BASENAME
    return "{}{}".format(cleaned, extension)


def should_skip_token_candidate(candidate_id_value, filter_param_id_values,
                                sort_group_param_id_values):
    """True when writing the candidate parameter could change the schedule."""
    candidate = _safe_int(candidate_id_value, fallback=None)
    if candidate is None:
        return True
    referenced = set()
    for id_value in list(filter_param_id_values or []) + \
            list(sort_group_param_id_values or []):
        parsed = _safe_int(id_value, fallback=None)
        if parsed is not None:
            referenced.add(parsed)
    return candidate in referenced


def build_export_summary_text(results, folder):
    results = list(results or [])
    exported = [result for result in results if result.ok]
    failed = [result for result in results if not result.ok]

    lines = [
        "Exported {} of {} schedule(s) to:".format(len(exported), len(results)),
        _safe_text(folder),
    ]

    if exported:
        lines.append("")
        lines.append("Exported:")
        for result in exported[:SUMMARY_DISPLAY_LIMIT]:
            lines.append("- {} ({} row(s))".format(result.name, result.row_count))
        if len(exported) > SUMMARY_DISPLAY_LIMIT:
            lines.append("...and {} more.".format(
                len(exported) - SUMMARY_DISPLAY_LIMIT
            ))

    warned = [result for result in exported if result.ordering_note]
    if warned:
        lines.append("")
        lines.append("Ordering warnings:")
        for result in warned[:SUMMARY_DISPLAY_LIMIT]:
            lines.append("- {}: {}".format(result.name, result.ordering_note))
        if len(warned) > SUMMARY_DISPLAY_LIMIT:
            lines.append("...and {} more.".format(
                len(warned) - SUMMARY_DISPLAY_LIMIT
            ))

    if failed:
        lines.append("")
        lines.append("Failed:")
        for result in failed[:SUMMARY_DISPLAY_LIMIT]:
            lines.append("- {}: {}".format(result.name, result.message))
        if len(failed) > SUMMARY_DISPLAY_LIMIT:
            lines.append("...and {} more.".format(
                len(failed) - SUMMARY_DISPLAY_LIMIT
            ))

    lines.append("")
    lines.append("Existing files with the same name were overwritten.")
    return "\n".join(lines)
