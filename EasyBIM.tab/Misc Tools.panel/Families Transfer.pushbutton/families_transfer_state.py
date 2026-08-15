# -*- coding: utf-8 -*-
"""Pure helpers that are specific to the Families Transfer command.

The family-selection machinery (option classes, key scheme, sort/filter/
merge and export filename helpers) lives in ``easybim.family_selection_state``
and is shared with Families Downgrade; only the transfer/export result
reporting stays here.
"""

from easybim.family_selection_state import SUMMARY_DISPLAY_LIMIT


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


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
    def __init__(self, loaded=None, skipped=None, failed=None, closed_rfa_count=0,
                 notes=None, cancelled=False):
        self.loaded = list(loaded or [])
        self.skipped = list(skipped or [])
        self.failed = list(failed or [])
        self.closed_rfa_count = int(closed_rfa_count or 0)
        # One line per whole-source reason, so a link that refuses every
        # family reads as one sentence instead of filling the detail list.
        self.notes = list(notes or [])
        self.cancelled = bool(cancelled)

    def add_note(self, note):
        note = _safe_text(note).strip()
        if note and note not in self.notes:
            self.notes.append(note)


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
        "Families Transfer cancelled."
        if bool(getattr(summary, "cancelled", False))
        else "Families Transfer completed.",
        "Loaded/overwritten: {}".format(len(loaded)),
        "Skipped: {}".format(len(skipped)),
        "Failed: {}".format(len(failed)),
    ]
    if closed_rfa_count:
        lines.append("Closed .rfa files: {}".format(closed_rfa_count))

    # Whole-source reasons go above the per-family detail, so a link that
    # refused everything is one sentence rather than twenty identical rows.
    for note in list(getattr(summary, "notes", []) or []):
        lines.append("")
        lines.append(_safe_text(note))

    _append_result_lines(lines, "Loaded/overwritten:", loaded)
    _append_result_lines(lines, "Skipped:", skipped)
    _append_result_lines(lines, "Failed:", failed)
    return "\n".join(lines)
