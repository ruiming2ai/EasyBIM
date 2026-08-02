# -*- coding: utf-8 -*-
"""Pure-Python state model for the Sheet Manager grid.

Deliberately free of pyrevit/.NET imports so the desktop test suite can load
it standalone (same pattern as schedule_export_state.py). All staged-edit
bookkeeping lives here; the UI and Revit layers stay thin.
"""

from __future__ import print_function


STATE_NORMAL = "normal"
STATE_DIRTY = "dirty"
STATE_LOCKED = "locked"
STATE_DUPLICATED = "duplicated"
STATE_CLOUD = "cloud"

KIND_SELECT = "select"
KIND_INDEX = "index"
KIND_NUMBER = "number"
KIND_NAME = "name"
KIND_SHEET_PARAM = "sheet_param"
KIND_TB_PARAM = "tb_param"
KIND_REVISION = "revision"
KIND_SCHEDULE_TEXT = "schedule_text"

TEXT_VALUE_KINDS = (
    KIND_NUMBER,
    KIND_NAME,
    KIND_SHEET_PARAM,
    KIND_TB_PARAM,
    KIND_SCHEDULE_TEXT,
)
EDITABLE_TEXT_KINDS = (KIND_NUMBER, KIND_NAME, KIND_SHEET_PARAM, KIND_TB_PARAM)

DUPLICATED_TEXT = "duplicated"
TB_HEADER_PREFIX = "TB: "


class ColumnSpec(object):
    """Describes one grid column; ``attr`` is the generated binding name."""

    def __init__(self, key, kind, header, attr,
                 param_name="", param_id_value=None, storage_type="",
                 is_read_only=False, revision_id=None, revision_seq=None,
                 revision_label="", source="default", width=110):
        self.key = key
        self.kind = kind
        self.header = header
        self.attr = attr
        self.param_name = param_name
        self.param_id_value = param_id_value
        self.storage_type = storage_type
        self.is_read_only = bool(is_read_only)
        self.revision_id = revision_id
        self.revision_seq = revision_seq
        self.revision_label = revision_label
        self.source = source
        self.width = width

    @property
    def is_text_value(self):
        return self.kind in TEXT_VALUE_KINDS


def fixed_columns():
    return [
        ColumnSpec("select", KIND_SELECT, "", "is_selected", width=36),
        ColumnSpec("index", KIND_INDEX, "#", "index",
                   is_read_only=True, width=42),
        ColumnSpec("number", KIND_NUMBER, "Sheet Number", "number", width=110),
        ColumnSpec("name", KIND_NAME, "Sheet Name", "name", width=230),
    ]


def build_revision_columns(revision_rows):
    """revision_rows: dicts with id/sequence/number/description
    (the easybim.print_sets.collect_revision_rows shape)."""
    columns = []
    for pos, revision in enumerate(revision_rows):
        label_bits = []
        if revision.get("number"):
            label_bits.append(u"{0}".format(revision.get("number")))
        if revision.get("description"):
            label_bits.append(u"{0}".format(revision.get("description")))
        columns.append(ColumnSpec(
            "rev:{0}".format(revision.get("id")),
            KIND_REVISION,
            "Rev {0}".format(revision.get("sequence")),
            "r{0}".format(pos),
            revision_id=revision.get("id"),
            revision_seq=revision.get("sequence"),
            revision_label=u" - ".join(label_bits),
            width=76,
        ))
    return columns


def next_attr_index(columns, prefix):
    """Next free numeric suffix for generated attr names (p0.., r0.., s0..)."""
    highest = -1
    for column in columns:
        attr = getattr(column, "attr", "") or ""
        if attr.startswith(prefix):
            try:
                highest = max(highest, int(attr[len(prefix):]))
            except (TypeError, ValueError):
                continue
    return highest + 1


class SheetRowBase(object):
    """Plain row model; the UI layer subclasses it with change notification."""

    def __init__(self, sheet_id, number, name,
                 is_placeholder=False, tblock_count=1):
        self.sheet_id = sheet_id
        self.is_pending = sheet_id is None
        self.is_selected = False
        self.index = 0
        self.number = number or u""
        self.name = name or u""
        self.number_state = STATE_NORMAL
        self.name_state = STATE_NORMAL
        self.is_placeholder = bool(is_placeholder)
        self.tblock_count = int(tblock_count or 0)
        self.original = {}
        self.all_revision_ids = set()
        self.revisions_cloud = set()
        self.hidden_cloud_revs = set()

    def notify(self, attr):
        pass

    def get_state(self, attr):
        return getattr(self, attr + "_state", STATE_NORMAL)

    def set_state(self, attr, new_state):
        setattr(self, attr + "_state", new_state)
        self.notify(attr + "_state")

    def set_value(self, attr, value):
        setattr(self, attr, value)
        self.notify(attr)


def base_state(row, column):
    """Cell state ignoring staged edits (what a clean cell should show)."""
    if column.kind == KIND_REVISION:
        if column.revision_id in row.revisions_cloud:
            return STATE_CLOUD
        return STATE_NORMAL
    if column.kind == KIND_TB_PARAM:
        if row.tblock_count > 1:
            return STATE_DUPLICATED
        if row.is_placeholder or row.tblock_count == 0:
            return STATE_LOCKED
    if column.is_read_only or column.kind == KIND_SCHEDULE_TEXT:
        return STATE_LOCKED
    return STATE_NORMAL


def can_edit_cell(row, column):
    if column.kind == KIND_REVISION:
        return True
    if column.kind not in EDITABLE_TEXT_KINDS:
        return False
    return base_state(row, column) not in (STATE_LOCKED, STATE_DUPLICATED)


def refresh_cell_state(row, column):
    base = base_state(row, column)
    if base in (STATE_LOCKED, STATE_DUPLICATED):
        row.set_state(column.attr, base)
        return
    current = getattr(row, column.attr, None)
    original = row.original.get(column.attr)
    if current != original:
        row.set_state(column.attr, STATE_DIRTY)
    else:
        row.set_state(column.attr, base)


def populate_row(row, columns, values=None):
    """Set every value attr and its ``_state`` sibling before binding.

    ``values`` maps column.key -> initial value (bool for revision columns,
    text for the rest). Missing keys default to unchecked/empty.
    """
    values = values or {}
    for column in columns:
        if column.kind in (KIND_SELECT, KIND_INDEX):
            continue
        if column.kind in (KIND_NUMBER, KIND_NAME):
            row.original[column.attr] = getattr(row, column.attr, u"")
            row.set_state(column.attr, STATE_NORMAL)
            continue
        if column.kind == KIND_REVISION:
            checked = bool(values.get(column.key, False))
            setattr(row, column.attr, checked)
            row.original[column.attr] = checked
            setattr(row, column.attr + "_state", base_state(row, column))
            continue
        value = values.get(column.key, u"")
        if value is None:
            value = u""
        state = base_state(row, column)
        if state == STATE_DUPLICATED:
            value = DUPLICATED_TEXT
        elif column.kind == KIND_TB_PARAM and state == STATE_LOCKED \
                and (row.is_placeholder or row.tblock_count == 0):
            value = u""
        setattr(row, column.attr, value)
        row.original[column.attr] = value
        setattr(row, column.attr + "_state", state)


def apply_cell_edit(row, column, new_value):
    """Stage a text-cell edit. Returns True when the edit was accepted."""
    if column.kind == KIND_REVISION or not can_edit_cell(row, column):
        return False
    if new_value is None:
        new_value = u""
    row.set_value(column.attr, new_value)
    refresh_cell_state(row, column)
    return True


def apply_revision_toggle(row, column, checked):
    """Stage a revision checkbox change. Returns True when accepted."""
    if column.kind != KIND_REVISION:
        return False
    if row.get_state(column.attr) == STATE_LOCKED:
        return False
    row.set_value(column.attr, bool(checked))
    refresh_cell_state(row, column)
    return True


def count_staged_cells(rows, columns):
    count = 0
    for row in rows:
        for column in columns:
            if column.kind in (KIND_SELECT, KIND_INDEX):
                continue
            if row.get_state(column.attr) == STATE_DIRTY:
                count += 1
    return count


def row_matches_search(row, columns, text):
    if not text:
        return True
    needle = u"{0}".format(text).strip().lower()
    if not needle:
        return True
    for column in columns:
        if not column.is_text_value:
            continue
        value = getattr(row, column.attr, None)
        if value is None:
            continue
        try:
            haystack = u"{0}".format(value).lower()
        except Exception:
            continue
        if needle in haystack:
            return True
    return False


def search_rows(rows, columns, text):
    return [row for row in rows if row_matches_search(row, columns, text)]


def renumber(rows):
    for pos, row in enumerate(rows):
        row.set_value("index", pos + 1)
    return rows


def multi_titleblock_rows(rows):
    return [row for row in rows if row.tblock_count > 1]


def propagate_edit(rows, column, new_value, skip_row=None):
    """Bulk-apply a text edit across ``rows`` (multi-select propagation).

    Sheet Number and Sheet Name never propagate. Returns the changed rows.
    """
    if column.kind in (KIND_NUMBER, KIND_NAME):
        return []
    changed = []
    for row in rows:
        if row is skip_row:
            continue
        if apply_cell_edit(row, column, new_value):
            changed.append(row)
    return changed


def propagate_revision_toggle(rows, column, checked, skip_row=None):
    changed = []
    for row in rows:
        if row is skip_row:
            continue
        if apply_revision_toggle(row, column, checked):
            changed.append(row)
    return changed


def find_number_problems(rows):
    """-> (empty_rows, duplicate_groups) over projected (staged) numbers.

    duplicate_groups: list of (number, [rows]) with more than one row.
    """
    empty_rows = []
    by_number = {}
    for row in rows:
        number = u"{0}".format(row.number or u"").strip()
        if not number:
            empty_rows.append(row)
            continue
        by_number.setdefault(number, []).append(row)
    duplicate_groups = []
    for number in sorted(by_number.keys()):
        group = by_number[number]
        if len(group) > 1:
            duplicate_groups.append((number, group))
    return empty_rows, duplicate_groups


def plan_number_assignments(renames, existing_numbers):
    """Two-phase renumber plan safe for swaps and cycles.

    ``renames``: [(key, old_number, new_number)]. ``existing_numbers``: every
    current sheet number in the model. Every renamed sheet first gets a
    unique temporary number, then its final number.
    Returns (phase_temp, phase_final): lists of (key, number).
    """
    taken = set(u"{0}".format(n) for n in existing_numbers)
    for _, old_number, new_number in renames:
        taken.add(u"{0}".format(old_number))
        taken.add(u"{0}".format(new_number))
    phase_temp = []
    phase_final = []
    counter = 0
    for key, _, new_number in renames:
        while True:
            temp = u"{0}~EBIMTMP{1}".format(new_number, counter)
            counter += 1
            if temp not in taken:
                taken.add(temp)
                break
        phase_temp.append((key, temp))
        phase_final.append((key, new_number))
    return phase_temp, phase_final


class StagedChanges(object):
    """Diff of the grid against its original snapshot."""

    def __init__(self):
        self.renames = []                 # (row, old, new)
        self.name_edits = []              # (row, old, new)
        self.param_edits = []             # (row, column, old, new)
        self.revision_adds = []           # (row, column, revision_id)
        self.revision_removes = []        # (row, column, revision_id)
        self.cloud_hide_requests = []     # (row, column, revision_id)
        self.cloud_unhide_candidates = [] # (row, column, revision_id)
        self.pending_sheets = []          # rows created by import
        self.copy_content_ops = []        # CopySheetRequest payloads

    def is_empty(self):
        return not (self.renames or self.name_edits or self.param_edits
                    or self.revision_adds or self.revision_removes
                    or self.cloud_hide_requests
                    or self.cloud_unhide_candidates
                    or self.pending_sheets or self.copy_content_ops)


def compute_staged_changes(rows, columns):
    changes = StagedChanges()
    for row in rows:
        if row.is_pending:
            changes.pending_sheets.append(row)
        for column in columns:
            if column.kind in (KIND_SELECT, KIND_INDEX):
                continue
            if row.get_state(column.attr) != STATE_DIRTY:
                continue
            current = getattr(row, column.attr, None)
            original = row.original.get(column.attr)
            if column.kind == KIND_NUMBER:
                if not row.is_pending:
                    changes.renames.append((row, original, current))
            elif column.kind == KIND_NAME:
                if not row.is_pending:
                    changes.name_edits.append((row, original, current))
            elif column.kind == KIND_REVISION:
                if current and not original:
                    if column.revision_id in row.hidden_cloud_revs:
                        changes.cloud_unhide_candidates.append(
                            (row, column, column.revision_id))
                    else:
                        changes.revision_adds.append(
                            (row, column, column.revision_id))
                elif original and not current:
                    if column.revision_id in row.revisions_cloud:
                        changes.cloud_hide_requests.append(
                            (row, column, column.revision_id))
                    else:
                        changes.revision_removes.append(
                            (row, column, column.revision_id))
            elif column.kind in (KIND_SHEET_PARAM, KIND_TB_PARAM):
                changes.param_edits.append((row, column, original, current))
    return changes


class ResultItem(object):
    """One line in the Apply Changes results dialog."""

    def __init__(self, sheet, item, old_value, new_value, status):
        self.sheet = sheet
        self.item = item
        self.old_value = old_value
        self.new_value = new_value
        self.status = status


class ApplyResults(object):
    def __init__(self):
        self.parameter_changes = []   # ResultItem
        self.revision_changes = []    # ResultItem
        self.sheet_changes = []       # ResultItem
        self.errors = []              # ResultItem (status = error/skipped)
        self.applied_cells = []       # (row, attr) successfully written
        self.created_count = 0
        self.updated_parameter_count = 0
        self.revisions_added = 0
        self.revisions_removed = 0
        self.modified_sheet_ids = set()

    def modified_count(self):
        return len(self.modified_sheet_ids)

    def add_error(self, sheet, item, message, status="error"):
        self.errors.append(ResultItem(sheet, item, u"", message, status))
