# -*- coding: utf-8 -*-
"""Pure-Python state core for Clash Detection Mode.

This module deliberately imports nothing from pyRevit, Autodesk, or the
``easybim`` package: the desktop test suite loads it standalone with
``importlib`` (the same trick ``test_coordination_review_passive.py`` uses),
and every decision that can be made without touching the Revit API is made
here so it can be unit-tested off-Revit.

The engine owns Revit; this module owns the bookkeeping:

* which categories each side of the check watches,
* the bounded queue of element ids that changed since the mode started,
* pair identity/dedupe so a clash is reported once and only once,
* when an Idling tick is allowed to do work (debounce) and how much work it
  may do (element count + wall-clock budget),
* resolution of recorded clashes when their elements are deleted or moved.

Nothing here writes to disk.  The whole feature is memory-only by design, so
there is no cache file that can grow and none to delete on Stop.
"""

from __future__ import print_function


# -- Tuning ---------------------------------------------------------------
#
# These bounds exist to keep the mode's cost flat no matter what the user
# does in Revit.  A 5000-element paste must not stall the UI, and a session
# left running all day must not grow without bound.

#: Elements waiting to be geometry-checked.  Oldest are dropped past this.
MAX_QUEUE = 5000
#: Recorded live clash pairs.  Recording stops (with a note) past this.
MAX_PAIRS = 2000
#: Quiet period after the last document change before a pass may run.  This
#: coalesces a burst of edits (a drag, a mirror, an array) into one pass.
DEBOUNCE_SEC = 0.35
#: Wall-clock ceiling for geometry work inside a single Idling tick.
BUDGET_SEC = 0.04
#: Hard element ceiling for a single Idling tick, whichever hits first.
MAX_ELEMENTS_PER_TICK = 25

HOST_MODEL_REF = "host"
HOST_MODEL_KEY = "host"

STATUS_ACTIVE = "active"


def safe_text(value):
    """Best-effort str() that never raises (CLR objects included)."""
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        try:
            return value.ToString()
        except Exception:
            return ""


def safe_int(value):
    try:
        return int(value)
    except Exception:
        return None


# -- Identity -------------------------------------------------------------


def link_model_ref(link_instance_id):
    """Stable reference string for elements living inside a linked model."""
    numeric = safe_int(link_instance_id)
    if numeric is None:
        return None
    return "link:{0}".format(numeric)


def is_link_ref(model_ref):
    return safe_text(model_ref).startswith("link:")


def link_instance_id_from_ref(model_ref):
    if not is_link_ref(model_ref):
        return None
    return safe_int(safe_text(model_ref).split(":", 1)[1])


def element_key(model_ref, element_id):
    """Identity of one element, unique across the host and every link."""
    numeric = safe_int(element_id)
    if numeric is None:
        return None
    return "{0}#{1}".format(safe_text(model_ref) or HOST_MODEL_REF, numeric)


def pair_key(model_ref_a, element_id_a, model_ref_b, element_id_b):
    """Order-independent identity of a clash pair.

    Sorting the two element keys is what makes ``A vs B`` and ``B vs A`` the
    same record - without it the same clash would be reported twice, once
    from each side of the check.
    """
    key_a = element_key(model_ref_a, element_id_a)
    key_b = element_key(model_ref_b, element_id_b)
    if key_a is None or key_b is None or key_a == key_b:
        return None
    if key_a <= key_b:
        return "{0}|{1}".format(key_a, key_b)
    return "{0}|{1}".format(key_b, key_a)


def element_keys_in_pair(key):
    """Split a pair key back into its two element keys."""
    text = safe_text(key)
    if "|" not in text:
        return []
    return text.split("|", 1)


# -- Records --------------------------------------------------------------


class ElementInfo(object):
    """What the panel shows for one side of a clash."""

    def __init__(
        self,
        model_ref=HOST_MODEL_REF,
        model_label="Current Project",
        element_id=None,
        category_name="",
        family_name="",
        type_name="",
    ):
        self.model_ref = model_ref or HOST_MODEL_REF
        self.model_label = model_label or ""
        self.element_id = safe_int(element_id)
        self.category_name = category_name or ""
        self.family_name = family_name or ""
        self.type_name = type_name or ""

    @property
    def key(self):
        return element_key(self.model_ref, self.element_id)

    @property
    def is_linked(self):
        return is_link_ref(self.model_ref)

    @property
    def family_type_label(self):
        """``Family : Type`` with graceful fallbacks for either half."""
        family = safe_text(self.family_name).strip()
        type_name = safe_text(self.type_name).strip()
        if family and type_name:
            return "{0} : {1}".format(family, type_name)
        return family or type_name or "(Unknown Type)"

    @property
    def display_label(self):
        category = safe_text(self.category_name).strip()
        if category:
            return "{0} - {1}".format(category, self.family_type_label)
        return self.family_type_label

    @property
    def detail_label(self):
        """Second line: element id, plus the link name when not in the host."""
        detail = "ID: {0}".format(
            self.element_id if self.element_id is not None else "?"
        )
        if self.is_linked and self.model_label:
            detail = "{0}  |  {1}".format(detail, self.model_label)
        return detail


class ClashRecord(object):
    def __init__(self, key, element_a, element_b, sequence=0):
        self.key = key
        self.element_a = element_a
        self.element_b = element_b
        self.sequence = sequence
        self.status = STATUS_ACTIVE

    @property
    def element_keys(self):
        return [self.element_a.key, self.element_b.key]

    @property
    def model_refs(self):
        return [self.element_a.model_ref, self.element_b.model_ref]

    @property
    def summary_text(self):
        return "{0}  <->  {1}".format(
            self.element_a.display_label,
            self.element_b.display_label,
        )


# -- Category picker ------------------------------------------------------


class CategoryOption(object):
    """One row in either category list of the setup window."""

    def __init__(self, name, category_id, is_checked=False):
        self.name = name
        self.category_id = category_id
        self.is_checked = bool(is_checked)


class ModelOption(object):
    """One entry in a `Categories from` dropdown."""

    def __init__(
        self,
        display_name,
        model_key,
        model_ref=HOST_MODEL_REF,
        is_current_project=False,
        document=None,
        link_instance=None,
    ):
        self.display_name = display_name
        self.model_key = model_key
        self.model_ref = model_ref
        self.is_current_project = bool(is_current_project)
        self.document = document
        self.link_instance = link_instance

    def __str__(self):
        return safe_text(self.display_name)


class SideSelection(object):
    """The frozen result of one side of the setup window."""

    def __init__(
        self,
        model_key=HOST_MODEL_KEY,
        model_ref=HOST_MODEL_REF,
        model_label="Current Project",
        category_ids=None,
        slot="",
    ):
        self.model_key = model_key
        self.model_ref = model_ref
        self.model_label = model_label
        self.category_ids = set(category_ids or [])
        #: "a" or "b" - lets the engine look up this side's cached Revit
        #: filters without keying on ``model_ref``, which both sides share
        #: whenever the user checks the host model on both lists.
        self.slot = slot

    @property
    def is_host(self):
        return not is_link_ref(self.model_ref)

    def watches(self, category_id):
        numeric = safe_int(category_id)
        return numeric is not None and numeric in self.category_ids

    def owns(self, model_ref):
        return safe_text(self.model_ref) == safe_text(model_ref)


def sort_categories(categories):
    return sorted(categories or [], key=lambda item: safe_text(item.name).lower())


def checked_category_ids(categories):
    return set(
        item.category_id for item in (categories or []) if item.is_checked
    )


def restore_category_selection(categories, checked_ids):
    checked_ids = set(checked_ids or [])
    for item in categories or []:
        item.is_checked = item.category_id in checked_ids
    return categories


def select_all(categories):
    for item in categories or []:
        item.is_checked = True
    return categories


def select_none(categories):
    for item in categories or []:
        item.is_checked = False
    return categories


def invert_selection(categories):
    for item in categories or []:
        item.is_checked = not item.is_checked
    return categories


def apply_check_to_rows(rows, is_checked):
    """Bulk-toggle: what a shift/drag selection plus one checkbox click does.

    Native Interference Check has no equivalent - every category must be
    ticked individually.  Selecting a range and toggling once is the whole
    point of the Extended-selection lists.
    """
    changed = []
    for row in rows or []:
        if bool(row.is_checked) != bool(is_checked):
            row.is_checked = bool(is_checked)
            changed.append(row)
    return changed


def can_start(side_a_ids, side_b_ids):
    """Both sides need at least one category or there is nothing to check."""
    return bool(side_a_ids) and bool(side_b_ids)


# -- Session --------------------------------------------------------------


class ClashSession(object):
    """Bounded bookkeeping for one run of the detection mode.

    Only elements that change *after* ``start`` ever enter the queue, which
    is what makes the mode forward-only: a clash that already existed is
    never surfaced because neither of its elements is ever tested.
    """

    def __init__(self, side_a=None, side_b=None, silent_mode=True):
        self.side_a = side_a or SideSelection()
        self.side_b = side_b or SideSelection()
        self.silent_mode = bool(silent_mode)

        self._pending = []
        self._pending_set = set()
        self._deleted = []
        self._pairs = {}
        self._new_pairs = []
        self._sequence = 0

        self.last_change_at = 0.0
        self.dropped_change_count = 0
        self.resolved_count = 0
        self.pair_limit_reached = False
        self.scanned_count = 0

    # -- category interest ------------------------------------------------

    def watched_model_refs(self):
        return set([self.side_a.model_ref, self.side_b.model_ref])

    def category_ids_for_model(self, model_ref):
        ids = set()
        if self.side_a.owns(model_ref):
            ids |= set(self.side_a.category_ids)
        if self.side_b.owns(model_ref):
            ids |= set(self.side_b.category_ids)
        return ids

    def watches(self, model_ref, category_id):
        """Cheap pre-filter: skip a changed element before any geometry."""
        return bool(self.target_sides_for(model_ref, category_id))

    def target_sides_for(self, model_ref, category_id):
        """Which side(s) this element must be checked *against*.

        An element belonging to a category that both sides watch is checked
        against both, which is how a same-category self-check (Pipes vs
        Pipes) falls out without a special case.
        """
        sides = []
        if self.side_a.owns(model_ref) and self.side_a.watches(category_id):
            sides.append(self.side_b)
        if self.side_b.owns(model_ref) and self.side_b.watches(category_id):
            sides.append(self.side_a)
        return sides

    # -- change queue -----------------------------------------------------

    @property
    def queue_size(self):
        return len(self._pending)

    @property
    def pending_deleted_count(self):
        return len(self._deleted)

    def _push(self, model_ref, element_id):
        numeric = safe_int(element_id)
        if numeric is None:
            return False
        item = (safe_text(model_ref) or HOST_MODEL_REF, numeric)
        if item in self._pending_set:
            return False
        self._pending.append(item)
        self._pending_set.add(item)
        return True

    def _trim_queue(self):
        overflow = len(self._pending) - MAX_QUEUE
        if overflow <= 0:
            return
        # Drop oldest: the newest edits are the ones the user is looking at,
        # and an unbounded queue is exactly the runaway this mode must not
        # have.
        for item in self._pending[:overflow]:
            self._pending_set.discard(item)
        del self._pending[:overflow]
        self.dropped_change_count += overflow

    def enqueue_changes(self, added_ids=None, modified_ids=None, deleted_ids=None, now=0.0):
        """Record what a host transaction touched.  Called from DocumentChanged.

        Kept to set operations only - no document reads, no geometry - so
        the cost paid inside Revit's change event stays negligible.
        """
        for element_id in list(deleted_ids or []):
            numeric = safe_int(element_id)
            if numeric is None:
                continue
            self._deleted.append(numeric)
            item = (HOST_MODEL_REF, numeric)
            if item in self._pending_set:
                self._pending_set.discard(item)
                try:
                    self._pending.remove(item)
                except ValueError:
                    pass

        for element_id in list(added_ids or []) + list(modified_ids or []):
            self._push(HOST_MODEL_REF, element_id)

        self._trim_queue()
        self.last_change_at = now
        return len(self._pending)

    def enqueue_elements(self, model_ref, element_ids, now=None):
        """Queue extra candidates (used when a link instance is moved)."""
        for element_id in list(element_ids or []):
            self._push(model_ref, element_id)
        self._trim_queue()
        if now is not None:
            self.last_change_at = now
        return len(self._pending)

    def take_deleted(self):
        deleted = list(self._deleted)
        del self._deleted[:]
        return deleted

    def take_batch(self, limit=None):
        """Pop up to ``limit`` ``(model_ref, element_id)`` work items."""
        limit = MAX_ELEMENTS_PER_TICK if limit is None else limit
        batch = self._pending[:limit]
        del self._pending[:limit]
        for item in batch:
            self._pending_set.discard(item)
        self.scanned_count += len(batch)
        return batch

    def take_one(self):
        """Pop a single work item so the caller can re-check its time budget."""
        if not self._pending:
            return None
        item = self._pending.pop(0)
        self._pending_set.discard(item)
        self.scanned_count += 1
        return item

    def has_work(self):
        return bool(self._pending) or bool(self._deleted)

    def should_run(self, now):
        """Debounce gate: wait for the edit burst to settle before checking."""
        if not self.has_work():
            return False
        return (now - self.last_change_at) >= DEBOUNCE_SEC

    def budget_exceeded(self, started_at, now):
        return (now - started_at) >= BUDGET_SEC

    # -- clash records ----------------------------------------------------

    @property
    def pair_count(self):
        return len(self._pairs)

    def has_pair(self, key):
        return key in self._pairs

    def record_clash(self, key, element_a, element_b):
        """Register a clash.  Returns the record when it is new, else None.

        Returning ``None`` for a known pair is what stops the popup from
        firing again every time the user nudges an element that is still
        clashing.
        """
        if not key or key in self._pairs:
            return None
        if len(self._pairs) >= MAX_PAIRS:
            self.pair_limit_reached = True
            return None
        self._sequence += 1
        record = ClashRecord(key, element_a, element_b, sequence=self._sequence)
        self._pairs[key] = record
        self._new_pairs.append(record)
        return record

    def records(self):
        return sorted(self._pairs.values(), key=lambda item: item.sequence)

    def drain_new_pairs(self):
        """Hand the not-yet-announced clashes to the panel/alert, once."""
        new_pairs = list(self._new_pairs)
        del self._new_pairs[:]
        return new_pairs

    def pairs_for_element_key(self, key):
        if not key:
            return []
        return [
            record
            for record in self._pairs.values()
            if key in record.element_keys
        ]

    def pairs_for_model_ref(self, model_ref):
        if not model_ref:
            return []
        return [
            record
            for record in self._pairs.values()
            if model_ref in record.model_refs
        ]

    def remove_pair(self, key, count_resolved=True):
        """Drop a pair.  ``count_resolved=False`` for re-evaluation, not a fix.

        Once dropped, the same pair can be detected again later - which is
        what makes "move it away, move it back" report a second time.
        """
        record = self._pairs.pop(key, None)
        if record is None:
            return None
        try:
            self._new_pairs.remove(record)
        except ValueError:
            pass
        if count_resolved:
            self.resolved_count += 1
        return record

    def remove_pairs_for_element_keys(self, keys):
        """Resolve every recorded clash touching these elements.

        Driven off the change set, so the cost is proportional to what the
        user just edited - never to the size of the recorded list.
        """
        wanted = set(key for key in (keys or []) if key)
        if not wanted:
            return []
        removed = []
        for key in list(self._pairs.keys()):
            record = self._pairs.get(key)
            if record is None:
                continue
            if wanted.intersection(record.element_keys):
                removed.append(self.remove_pair(key))
        return [record for record in removed if record is not None]

    # -- reporting --------------------------------------------------------

    def status_notes(self):
        notes = []
        if self.pair_limit_reached:
            notes.append(
                "Clash list is capped at {0}; new clashes are not being "
                "recorded until some are resolved.".format(MAX_PAIRS)
            )
        if self.dropped_change_count:
            notes.append(
                "{0} change(s) were dropped to keep Revit responsive.".format(
                    self.dropped_change_count
                )
            )
        return notes

    def summary(self):
        return {
            "active": self.pair_count,
            "resolved": self.resolved_count,
            "queued": self.queue_size,
            "notes": self.status_notes(),
        }

    def status_text(self):
        text = "{0} live clash(es)".format(self.pair_count)
        if self.resolved_count:
            text = "{0}  |  {1} resolved".format(text, self.resolved_count)
        if self.queue_size:
            text = "{0}  |  checking {1}...".format(text, self.queue_size)
        notes = self.status_notes()
        if notes:
            text = "{0}\n{1}".format(text, "\n".join(notes))
        return text

    def clear(self):
        """Wipe every byte this session held.  Called by Stop Detection."""
        del self._pending[:]
        del self._deleted[:]
        del self._new_pairs[:]
        self._pending_set.clear()
        self._pairs.clear()
        self._sequence = 0
        self.last_change_at = 0.0
        self.dropped_change_count = 0
        self.resolved_count = 0
        self.pair_limit_reached = False
        self.scanned_count = 0
