# -*- coding: utf-8 -*-
"""The by-name checklist every checker confirms its choices in, and the
report search that goes with it - shared by Damper Check and Fire Damper
Check.

Rows are plain dicts with a ``type_key`` (or ``key``), a ``category`` for
grouping and a ``count``.  A ``rule(row) -> (ticked, reason)`` decides what
the keywords alone would do; saved choices win over the rule in both
directions, so a damper the user unticked once does not come back ticked
next time, and names from other models survive a fold so one settings file
serves a whole content library.

No Revit imports.  Hoisted out of Damper Check when Fire Damper Check
became the second consumer.
"""

from __future__ import print_function

import re


ROW_CAP = 300

_TOKEN_SPLIT = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9]+")


def safe_text(value):
    if value is None:
        return u""
    try:
        return u"{0}".format(value)
    except Exception:
        return u""


def row_key(row):
    return safe_text(row.get("type_key") if row.get("type_key") is not None else row.get("key"))


# --------------------------------------------------------------- keywords


def keyword_match(text, include, exclude, whole_word=()):
    """``(ticked, reason)`` - exclusions win over inclusions.

    Keywords in ``whole_word`` must match a whole token ("hr" must not tick
    "Standard" through its letters), everything else matches as a
    substring.
    """
    lowered = safe_text(text).lower()
    words = set(_WORD.findall(lowered))
    whole = set(keyword.lower() for keyword in whole_word or ())

    def _hit(keyword):
        keyword = keyword.lower()
        if keyword in whole:
            return keyword in words
        return keyword in lowered

    for keyword in exclude or ():
        if keyword and _hit(keyword):
            return False, u"not pre-ticked: '{0}'".format(keyword)
    for keyword in include or ():
        if keyword and _hit(keyword):
            return True, u"matches '{0}'".format(keyword)
    return False, u""


# --------------------------------------------------------------- choices


def preselect_rows(rows, saved, unticked, rule):
    """Rows -> copies with ``is_checked`` and a ``reason``."""
    saved = set(safe_text(key) for key in saved or [])
    unticked = set(safe_text(key) for key in unticked or [])
    result = []
    for raw in rows or []:
        row = dict(raw)
        key = row_key(row)
        if "type_key" in row:
            row["type_key"] = key
        if key in saved:
            row["is_checked"], row["reason"] = True, u"ticked before"
        elif key in unticked:
            row["is_checked"], row["reason"] = False, u"unticked before"
        else:
            ticked, reason = rule(row) if rule is not None else (False, u"")
            row["is_checked"], row["reason"] = bool(ticked), safe_text(reason)
        result.append(row)
    return result


def fold_choices(saved, unticked, rows, rule):
    """``(saved, unticked)`` after the user's ticks on ``rows``.

    A key absent from ``rows`` keeps whatever was saved for it; an unticked
    row is remembered only when the rule would have ticked it, so the file
    never grows with names that need no note.
    """
    present = set(row_key(row) for row in rows or [])
    old_saved = set(safe_text(key) for key in saved or [])
    old_unticked = set(safe_text(key) for key in unticked or [])
    checked = set(row_key(row) for row in rows or [] if row.get("is_checked"))
    suppressed = set()
    for row in rows or []:
        if row.get("is_checked"):
            continue
        would_tick, _reason = rule(row) if rule is not None else (False, u"")
        if would_tick:
            suppressed.add(row_key(row))
    return (sorted((old_saved - present) | checked),
            sorted((old_unticked - present) | suppressed))


def category_rank(category, order):
    name = safe_text(category)
    for index, known in enumerate(order or ()):
        if name == known:
            return index
    return len(order or ())


def sort_rows(rows, order):
    return sorted(rows or [], key=lambda item: (category_rank(item.get("category"), order),
                                                safe_text(item.get("category")).lower(),
                                                row_key(item).lower()))


def group_rows(rows, order=(), expanded=()):
    """Rows -> ``[{"category", "rows", "is_expanded", "type_count",
    "instance_count"}]`` in ``order`` first, then alphabetically."""
    groups = {}
    names = []
    for row in rows or []:
        category = safe_text(row.get("category")) or u"Other"
        if category not in groups:
            groups[category] = {
                "category": category,
                "rows": [],
                "is_expanded": category in (expanded or ()),
                "type_count": 0,
                "instance_count": 0,
            }
            names.append(category)
        group = groups[category]
        group["rows"].append(row)
        group["type_count"] += 1
        try:
            group["instance_count"] += int(row.get("count") or 0)
        except Exception:
            pass
    names.sort(key=lambda name: (category_rank(name, order), name.lower()))
    return [groups[name] for name in names]


def count_text(rows, noun):
    total = len(rows or [])
    checked = len([row for row in rows or [] if row.get("is_checked")])
    return u"{0} of {1} {2}.".format(checked, total, noun)


# ---------------------------------------------------------------- search


def tokens(text):
    return [part for part in _TOKEN_SPLIT.split(safe_text(text).strip().lower()) if part]


def matches(query, item, text_fields, id_fields):
    """Every query token must hit: numbers as whole id tokens ("12" does
    not find 112), words as substrings of the named text fields."""
    wanted = tokens(query)
    if not wanted:
        return True
    haystack = u" ".join(safe_text(item.get(field)) for field in text_fields).lower()
    id_tokens = set()
    for field in id_fields:
        value = item.get(field)
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            for entry in value:
                if entry is not None:
                    id_tokens.add(safe_text(entry))
        else:
            id_tokens.add(safe_text(value))
    for token in wanted:
        if token.isdigit():
            if token not in id_tokens:
                return False
        elif token not in haystack:
            return False
    return True


def filter_report(report, query, problem_keys, text_fields, id_fields):
    """The same report with only matching rows; counts follow the rows."""
    report = report or {}
    buckets = []
    counts = {}
    for bucket in report.get("buckets") or []:
        items = [item for item in bucket.get("items") or []
                 if matches(query, item, text_fields, id_fields)]
        counts[bucket["key"]] = len(items)
        buckets.append(dict(bucket, items=items))
    filtered = dict(report)
    filtered["buckets"] = buckets
    filtered["counts"] = counts
    filtered["problem_count"] = sum(counts.get(key, 0) for key in problem_keys)
    return filtered


def cap_items(items, cap=ROW_CAP):
    """``(shown, hidden_count)`` - branches never grow past the cap."""
    items = list(items or [])
    if len(items) <= cap:
        return items, 0
    return items[:cap], len(items) - cap
