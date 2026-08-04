# -*- coding: utf-8 -*-
"""Pure logic for Update Circuit Rating.

Nothing in this module touches the Revit API, so the whole decision layer -
which circuit is listed, which element wins, what gets written - is unit
tested off Revit.  ``circuit_rating_revit`` produces the records this module
consumes and executes the plan it returns.

A circuit record is a plain dict::

    {
        "circuit_id": 123456,                # int
        "panel": u"HP-1",
        "circuit_number": u"3",
        "load_name": u"Panelboard LP-2",
        "samples": [                         # one per circuited element that
            {                                # actually carries the parameter
                "value": 100.0,              # amps, display units
                "label": u"Panelboard : 225 A",
                "is_equipment": True,        # Electrical Equipment category
            },
        ],
        "existing": {-1006968: 20.0},        # target param id -> current value
    }

Elements without the parameter simply contribute no sample.  A circuit with
no sample at all cannot be updated and is dropped rather than listed.
"""

from __future__ import print_function


# Two readings of the same amp value never land on the exact same float once
# unit conversion is involved, so "did this actually change?" needs a window.
VALUE_TOLERANCE = 1e-6

STATUS_UPDATE = u""
STATUS_NO_CHANGE = u"no change"


def values_match(left, right):
    """True when two amp readings are the same number for our purposes."""
    if left is None or right is None:
        return False
    try:
        return abs(float(left) - float(right)) <= VALUE_TOLERANCE
    except (TypeError, ValueError):
        return False


def format_amps(value):
    """``100.0`` -> ``100``, ``12.5`` -> ``12.5``, ``None`` -> ``u""``."""
    if value is None:
        return u""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return u"{0}".format(value)
    text = u"{0:.4f}".format(number)
    if u"." in text:
        text = text.rstrip(u"0").rstrip(u".")
    return text or u"0"


def pick_highest(samples):
    """The element whose rating defines the circuit.

    Highest value wins.  A tie goes to an Electrical Equipment element - a
    panelboard and a receptacle both reading 20 A should credit the panel -
    and then to the label, so the pick never depends on element order.
    """
    usable = [sample for sample in samples or []
              if sample.get("value") is not None]
    if not usable:
        return None
    usable.sort(key=_sample_order)
    return usable[0]


def _sample_order(sample):
    return (
        -float(sample.get("value") or 0.0),
        0 if sample.get("is_equipment") else 1,
        sample.get("label") or u"",
    )


def circuit_sort_key(row):
    """Panel first, then circuit number read as a number when it is one."""
    panel = (row.panel or u"").lower()
    number = row.circuit_number or u""
    numeric = _leading_number(number)
    return (panel, 0 if numeric is not None else 1, numeric or 0.0, number)


def _leading_number(text):
    digits = u""
    for char in text or u"":
        if char.isdigit():
            digits += char
        elif digits:
            break
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


class CircuitRowBase(object):
    """One line of the circuit grid.

    ``is_selected`` starts checked: the user asked for every listed circuit to
    be ticked by default.
    """

    def __init__(self, circuit_id, panel, circuit_number, load_name,
                 donor_label, source_value):
        self.circuit_id = circuit_id
        self.panel = panel or u""
        self.circuit_number = circuit_number or u""
        self.load_name = load_name or u""
        self.donor_label = donor_label or u""
        self.source_value = source_value
        self.new_text = format_amps(source_value)
        self.existing_text = u""
        self.status = STATUS_UPDATE
        self.is_selected = True

    @property
    def circuit_label(self):
        if self.panel and self.circuit_number:
            return u"{0} - {1}".format(self.panel, self.circuit_number)
        return self.panel or self.circuit_number

    def describe_existing(self, targets, existing):
        """``Rating 20 A; Frame 100 A`` for the ticked targets only."""
        parts = []
        for target in targets:
            value = (existing or {}).get(target.get("key"))
            parts.append(u"{0} {1}".format(
                target.get("label") or u"",
                format_amps(value) if value is not None else u"-"))
        return u"; ".join(parts)

    def unchanged_against(self, targets, existing):
        """True when every ticked target already holds the new value."""
        if not targets:
            return False
        for target in targets:
            if not values_match(
                    (existing or {}).get(target.get("key")), self.source_value):
                return False
        return True


def build_records(circuits_data, source_key):
    """Per-circuit records for :func:`build_rows`.

    ``circuits_data`` is the one-pass model scan; picking a different source
    parameter is just a different key into the readings already collected,
    which is why switching the combo never re-reads the document.
    """
    records = []
    for entry in circuits_data or []:
        readings = entry.get("readings") or {}
        records.append({
            "circuit_id": entry.get("circuit_id"),
            "panel": entry.get("panel"),
            "circuit_number": entry.get("circuit_number"),
            "load_name": entry.get("load_name"),
            "existing": entry.get("existing"),
            "samples": list(readings.get(source_key, [])),
        })
    return records


def build_rows(records, targets, row_factory=None, selected_ids=None):
    """Rows for the grid, plus the count of circuits that cannot be updated.

    ``targets`` are the ticked target parameters (``{"key", "label"}``).
    ``selected_ids`` carries the user's tick state across a source/target
    change so re-running the build does not silently re-check rows they
    unchecked.
    """
    factory = row_factory or CircuitRowBase
    rows = []
    skipped = 0
    for record in records or []:
        best = pick_highest(record.get("samples"))
        if best is None:
            skipped += 1
            continue
        row = factory(
            record.get("circuit_id"),
            record.get("panel"),
            record.get("circuit_number"),
            record.get("load_name"),
            best.get("label"),
            best.get("value"),
        )
        existing = record.get("existing") or {}
        row.existing_text = row.describe_existing(targets, existing)
        row.status = (STATUS_NO_CHANGE
                      if row.unchanged_against(targets, existing)
                      else STATUS_UPDATE)
        if selected_ids is not None:
            row.is_selected = row.circuit_id in selected_ids
        rows.append(row)
    rows.sort(key=circuit_sort_key)
    return rows, skipped


def build_plan(rows, targets):
    """What Update will write: one entry per ticked row."""
    keys = [target.get("key") for target in targets or []]
    if not keys:
        return []
    plan = []
    for row in rows or []:
        if not row.is_selected:
            continue
        plan.append({
            "circuit_id": row.circuit_id,
            "circuit_label": row.circuit_label,
            "value": row.source_value,
            "target_keys": list(keys),
        })
    return plan


def summarize(plan, results):
    """Counts and detail lines for the completion dialog.

    ``results`` is the list ``circuit_rating_revit.apply_ratings`` returns:
    ``{"circuit_id", "circuit_label", "written", "error"}``.
    """
    written = [item for item in results or [] if not item.get("error")]
    failed = [item for item in results or [] if item.get("error")]
    lines = []
    for item in written:
        lines.append(u"{0}: {1}".format(
            item.get("circuit_label") or u"",
            u", ".join(item.get("written") or [])))
    if failed:
        lines.append(u"")
        lines.append(u"Failed:")
        for item in failed:
            lines.append(u"{0}: {1}".format(
                item.get("circuit_label") or u"", item.get("error")))
    return {
        "attempted": len(plan or []),
        "updated": len(written),
        "failed": len(failed),
        "detail": u"\n".join(lines),
    }
