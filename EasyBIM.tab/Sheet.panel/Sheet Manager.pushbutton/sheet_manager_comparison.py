# -*- coding: utf-8 -*-
"""Read-only, two-way drawing-list comparison over plain sheet snapshots."""

import re

from easybim.excel_print_sets import normalize_key


REASONS = (
    ("missing_excel", "Missing from Excel"),
    ("missing_model", "Missing from Revit model"),
    ("appears_off", "Appears in Sheet List is off"),
    ("filtered", "Excluded by schedule parameter filters"),
    ("not_in_source", "Not included in selected sheet list / print set"),
    ("ambiguous", "Duplicate or ambiguous sheet numbers"),
    ("name", "Sheet-name differences"),
    ("order", "Sheet-order differences"),
    ("warning", "Other warnings"),
)
_PRIORITY = dict((key, i) for i, (key, label) in enumerate(REASONS))
_LABELS = dict(REASONS)


def _natural_key(value):
    return tuple((1, int(part)) if part.isdigit() else (0, part)
                 for part in re.split(r"(\d+)", normalize_key(value)))


def _joined(values):
    unique = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return u" | ".join(unique)


def _index(rows):
    result = {}
    for row in rows:
        result.setdefault(normalize_key(row["number"]), []).append(row)
    return result


class Finding(object):
    def __init__(self, excel, source, model, reasons, order_known):
        reasons.sort(key=lambda pair: _PRIORITY[pair[0]])
        self.reason_key = reasons[0][0]
        self.group_label = _LABELS[self.reason_key]
        self.number = (excel or source or model)[0]["number"] or u"(blank)"
        self.excel_name = _joined([r["name"] for r in excel])
        self.revit_name = _joined([r["name"] for r in (source or model)])
        self.excel_rows = u", ".join(str(r["position"]) for r in excel)
        self.source_positions = u", ".join(str(r["position"]) for r in source) \
            if order_known else u"Not verified"
        self.details = u"\n".join(detail for key, detail in reasons)


class ComparisonResult(object):
    def __init__(self, findings, matched_count, warnings, excel_count, source_count):
        self.findings = sorted(findings, key=lambda row: (
            _PRIORITY[row.reason_key], _natural_key(row.number), row.number))
        self.matched_count = matched_count
        self.warnings = warnings
        self.excel_count = excel_count
        self.source_count = source_count


def compare_lists(excel_rows, source_sheets, model_sheets, source_kind,
                  source_order_known=True):
    """Compare original workbook rows; never consume import staging/ignore state.

    Sheet snapshots are dictionaries with id, number, name, appears, printable,
    exclusions (verified filter descriptions), and unknown_filters. Missing
    sheets are diagnosed against the full model, not just the selected source.
    """
    excel = [dict(number=r.sheet_number, name=r.sheet_name, position=r.excel_row)
             for r in excel_rows]
    source = [dict(row, position=i + 1) for i, row in enumerate(source_sheets)]
    ei, si, mi = _index(excel), _index(source), _index(model_sheets)
    common = set(key for key in ei if key and len(ei[key]) == 1
                 and len(si.get(key, [])) == 1)
    excel_order = [normalize_key(r["number"]) for r in excel
                   if normalize_key(r["number"]) in common]
    source_order = [normalize_key(r["number"]) for r in source
                    if normalize_key(r["number"]) in common]
    ranks = dict((key, i) for i, key in enumerate(source_order))
    out_of_order = set(key for i, key in enumerate(excel_order)
                       if source_order_known and ranks[key] != i)
    findings = []
    for key in set(ei) | set(si):
        e, s, m = ei.get(key, []), si.get(key, []), mi.get(key, [])
        reasons = []
        if not key:
            reasons.append(("warning", "Sheet number is blank; this row cannot be matched."))
        elif not e:
            reasons.append(("missing_excel", "In the selected Revit source, but missing from Excel."))
        elif not s:
            if not m:
                reasons.append(("missing_model", "Excel sheet number was not found in the Revit model."))
            else:
                label = "sheet list" if source_kind == "schedule" else "print set"
                reasons.append(("not_in_source", u"Exists in the model, but is not included in the selected {0}.".format(label)))
                if source_kind == "schedule" and len(m) == 1:
                    candidate = m[0]
                    if candidate.get("appears") is False:
                        reasons.append(("appears_off", "Appears in Sheet List is off."))
                    for detail in candidate.get("exclusions", []):
                        reasons.append(("filtered", u"Excluded by filter: {0}".format(detail)))
                    unknown = candidate.get("unknown_filters", [])
                    if unknown:
                        reasons.append(("not_in_source", u"Filter evaluation could not be verified: {0}".format(u"; ".join(unknown))))
                    if candidate.get("appears") is not False and not candidate.get("exclusions"):
                        reasons.append(("not_in_source", "The exclusion reason could not be determined."))
        if key and (len(e) > 1 or len(s) > 1 or len(m) > 1):
            notes = []
            if len(e) > 1:
                notes.append("Duplicate Excel sheet number (including normalized spelling).")
            if len(s) > 1 or len(m) > 1:
                candidates = m if len(m) > 1 else s
                notes.append(u"Revit sheet number is ambiguous; element IDs: {0}.".format(
                    u", ".join(str(r["id"]) for r in candidates)))
                if len(s) == 1:
                    notes.append("The selected source contains one of these model sheets.")
            reasons.append(("ambiguous", u" ".join(notes)))
        name_match = s if s else m
        if key and len(e) == 1 and len(name_match) == 1:
            if normalize_key(e[0]["name"]) != normalize_key(name_match[0]["name"]):
                detail = "Excel and Revit sheet names differ (a blank name also counts)."
                if not s:
                    detail += " Compared with the existing model sheet outside the selected source."
                reasons.append(("name", detail))
        if key in out_of_order:
            reasons.append(("order", "Relative order of common sheets differs; see Excel row and Revit position."))
        for candidate in (s or m):
            if candidate.get("printable") is False:
                reasons.append(("warning", u"Revit sheet {0} is a placeholder or is not printable.".format(candidate["id"])))
        if reasons:
            findings.append(Finding(e, s, m, reasons, source_order_known))
    warnings = []
    if not source_order_known:
        warnings.append("Revit source order could not be verified; sheet-order comparison was skipped.")
    return ComparisonResult(findings, len(common), warnings, len(excel), len(source))
