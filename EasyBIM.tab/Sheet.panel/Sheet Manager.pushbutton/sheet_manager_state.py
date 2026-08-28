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
STATE_CONFLICT = "conflict"

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
        self.is_missing = False   # sheet deleted in the model since load
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
    if getattr(row, "is_missing", False):
        return STATE_LOCKED
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
    if column.kind == KIND_REVISION:
        changed = bool(current) != bool(original)
    else:
        changed = u"{0}".format(current or u"") \
            != u"{0}".format(original or u"")
    if changed:
        row.set_state(column.attr, STATE_DIRTY)
    else:
        row.set_state(column.attr, base)


def _normalized_cell_value(row, column, value):
    """The value a clean cell shows for a model value (shared by populate,
    late-added columns and model merges)."""
    if column.kind == KIND_REVISION:
        return bool(value)
    if value is None:
        value = u""
    cell_state = base_state(row, column)
    if cell_state == STATE_DUPLICATED:
        return DUPLICATED_TEXT
    if column.kind == KIND_TB_PARAM and cell_state == STATE_LOCKED \
            and (row.is_placeholder or row.tblock_count == 0):
        return u""
    return value


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
        value = _normalized_cell_value(
            row, column, values.get(column.key, u""))
        setattr(row, column.attr, value)
        row.original[column.attr] = value
        setattr(row, column.attr + "_state", base_state(row, column))


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


def conflicted_number_rows(rows):
    """Rows whose staged number is empty or duplicated by another row.

    Collisions are allowed while staging (a swap is a transient collision);
    they only block Apply Changes.
    """
    empty_rows, duplicate_groups = find_number_problems(rows)
    conflicted = list(empty_rows)
    for _, group in duplicate_groups:
        conflicted.extend(group)
    return conflicted


def refresh_number_conflicts(rows, number_column):
    """Paint number cells: conflict overrides, else normal dirty logic.

    Returns the conflict count (for status display).
    """
    conflicted = set(conflicted_number_rows(rows))
    for row in rows:
        if row in conflicted:
            row.set_state(number_column.attr, STATE_CONFLICT)
        else:
            refresh_cell_state(row, number_column)
    return len(conflicted)


# Characters Revit rejects in sheet numbers (ViewSheet.SheetNumber throws
# "sheetNumber cannot include prohibited characters, such as { } [ ] ..."):
PROHIBITED_NUMBER_CHARS = u"\\:{}[]|;<>?`~\n\r"


def invalid_number_chars(number):
    """Prohibited characters present in a proposed sheet number."""
    text = u"{0}".format(number or u"")
    return sorted(set(ch for ch in text
                      if ch in PROHIBITED_NUMBER_CHARS))


def plan_number_assignments(renames, existing_numbers):
    """Two-phase renumber plan safe for swaps and cycles.

    ``renames``: [(key, old_number, new_number)]. ``existing_numbers``: every
    current sheet number in the model. Every renamed sheet first gets a
    unique temporary number, then its final number. Temporary numbers use
    only hyphen/alphanumeric characters - Revit rejects sheet numbers
    containing { } [ ] | ; < > ? ` ~ backslash or colon.
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
            temp = u"{0}-EBIMTMP{1}".format(new_number, counter)
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
        if getattr(row, "is_missing", False):
            continue
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


FILTER_OPS = [
    ("equals", "equals"),
    ("not_equals", "does not equal"),
    ("contains", "contains"),
    ("not_contains", "does not contain"),
    ("begins", "begins with"),
    ("ends", "ends with"),
    ("greater", "is greater than"),
    ("less", "is less than"),
    ("has_value", "has a value"),
    ("no_value", "has no value"),
]
FILTER_OPS_NO_VALUE = ("has_value", "no_value")


def evaluate_filter_rule(value_text, op, arg):
    text = u"" if value_text is None else u"{0}".format(value_text)
    lowered = text.strip().lower()
    arg_text = u"" if arg is None else u"{0}".format(arg)
    arg_lower = arg_text.strip().lower()
    if op == "has_value":
        return bool(text.strip())
    if op == "no_value":
        return not text.strip()
    if op == "equals":
        return lowered == arg_lower
    if op == "not_equals":
        return lowered != arg_lower
    if op == "contains":
        return arg_lower in lowered
    if op == "not_contains":
        return arg_lower not in lowered
    if op == "begins":
        return lowered.startswith(arg_lower)
    if op == "ends":
        return lowered.endswith(arg_lower)
    if op in ("greater", "less"):
        try:
            left = float(text.strip())
            right = float(arg_text.strip())
        except (TypeError, ValueError):
            left = lowered
            right = arg_lower
        if op == "greater":
            return left > right
        return left < right
    return True


def columns_by_key(columns):
    result = {}
    for column in columns:
        result[column.key] = column
    return result


def filter_rows_by_rules(rows, column_map, rules, extra_lookup=None):
    """AND of rules; rules = [(column_key, op, arg)].

    ``extra_lookup(row, column_key)`` supplies values for rule keys that are
    not table columns (params filtered without being shown); returning None
    skips that rule for the row.
    """
    if not rules:
        return list(rows)
    result = []
    for row in rows:
        keep = True
        for column_key, op, arg in rules:
            column = column_map.get(column_key)
            if column is not None:
                value = getattr(row, column.attr, None)
            elif extra_lookup is not None:
                value = extra_lookup(row, column_key)
                if value is None:
                    continue
            else:
                continue
            if not evaluate_filter_rule(value, op, arg):
                keep = False
                break
        if keep:
            result.append(row)
    return result


def checked_revision_ids(row, columns):
    """Revision ids currently checked on the row (staged state)."""
    result = set()
    for column in columns:
        if column.kind == KIND_REVISION and getattr(row, column.attr, False):
            result.add(column.revision_id)
    return result


def filter_rows_by_revisions(rows, columns, selected_ids, all_revision_ids):
    """Keep rows carrying any selected revision. All-selected = inactive
    (otherwise sheets without revisions would always vanish)."""
    if not selected_ids:
        return list(rows)
    if set(selected_ids) >= set(all_revision_ids):
        return list(rows)
    selected = set(selected_ids)
    return [row for row in rows
            if checked_revision_ids(row, columns) & selected]


def sort_rows(rows, column_map, levels):
    """Stable multi-level sort; levels = [(column_key, ascending)] applied
    right-to-left. Numeric-aware: numbers sort before text."""
    result = list(rows)

    def make_key(column):
        def _key(row):
            value = getattr(row, column.attr, None)
            text = u"" if value is None else u"{0}".format(value)
            stripped = text.strip()
            try:
                return (0, float(stripped), u"")
            except (TypeError, ValueError):
                return (1, 0.0, stripped.lower())
        return _key

    for column_key, ascending in reversed(list(levels)):
        column = column_map.get(column_key)
        if column is None:
            continue
        result.sort(key=make_key(column), reverse=not ascending)
    return result


def plan_search_replace(rows, columns, find_text, replace_text,
                        match_case=True):
    """-> [(row, column, old_text, new_text)] over editable text cells."""
    if not find_text:
        return []
    replace_text = u"" if replace_text is None else u"{0}".format(replace_text)
    find_text = u"{0}".format(find_text)
    plan = []
    if not match_case:
        import re
        pattern = re.compile(re.escape(find_text), re.IGNORECASE)
    for row in rows:
        for column in columns:
            if column.kind not in EDITABLE_TEXT_KINDS:
                continue
            if not can_edit_cell(row, column):
                continue
            value = getattr(row, column.attr, None)
            text = u"" if value is None else u"{0}".format(value)
            if match_case:
                if find_text not in text:
                    continue
                new_text = text.replace(find_text, replace_text)
            else:
                if not pattern.search(text):
                    continue
                new_text = pattern.sub(
                    replace_text.replace(u"\\", u"\\\\"), text)
            if new_text != text:
                plan.append((row, column, text, new_text))
    return plan


def populate_new_column(row, column, value):
    """Initialize one late-added text column on an existing row."""
    value = _normalized_cell_value(row, column, value)
    setattr(row, column.attr, value)
    row.original[column.attr] = value
    setattr(row, column.attr + "_state", base_state(row, column))


def merge_row_values(row, columns, values):
    """Fold fresh model values into ``row`` without discarding staged edits.

    ``values``: column.key -> current model value (bool for revision
    columns, text otherwise); columns absent from ``values`` are untouched.
    Clean cell  -> value and original both take the model value.
    Staged cell (dirty/conflict) -> keeps its value; original moves to the
    model value; state recomputed, so an edit that now matches the model
    reverts to normal. Returns (updated_attrs, reverted_attrs, kept_dirty).
    """
    updated, reverted, kept = [], [], []
    for column in columns:
        if column.kind in (KIND_SELECT, KIND_INDEX):
            continue
        if column.key not in values:
            continue
        model_value = _normalized_cell_value(row, column, values[column.key])
        attr = column.attr
        if row.get_state(attr) in (STATE_DIRTY, STATE_CONFLICT):
            row.original[attr] = model_value
            refresh_cell_state(row, column)
            if row.get_state(attr) == STATE_DIRTY:
                kept.append(attr)
            else:
                reverted.append(attr)
        else:
            row.original[attr] = model_value
            if getattr(row, attr, None) != model_value:
                row.set_value(attr, model_value)
                updated.append(attr)
            refresh_cell_state(row, column)
    return updated, reverted, kept


def mark_row_missing(row, columns):
    """Sheet deleted in the model: lock every cell, keep values so a Revit
    undo that brings the sheet back restores the staged edits."""
    row.is_missing = True
    for column in columns:
        if column.kind in (KIND_SELECT, KIND_INDEX):
            continue
        row.set_state(column.attr, STATE_LOCKED)
    row.notify("is_missing")


def unmark_row_missing(row, columns):
    row.is_missing = False
    for column in columns:
        if column.kind in (KIND_SELECT, KIND_INDEX):
            continue
        refresh_cell_state(row, column)
    row.notify("is_missing")


MISSING = object()   # sentinel: the sheet no longer exists in the model


def partition_stale_changes(changes, current_values):
    """Split staged changes into (clean, stale) against fresh model values.

    ``current_values``: {(sheet_id, attr): model_value | MISSING} for every
    staged cell of existing sheets. A cell is stale when the model value
    differs from the snapshot the edit was made against (row.original), or
    when the sheet is gone. Pending sheets and copy-content ops are always
    clean. Returns (clean StagedChanges, stale [(row, column, attr,
    model_value)]).
    """
    clean = StagedChanges()
    stale = []
    clean.pending_sheets = list(changes.pending_sheets)
    clean.copy_content_ops = list(changes.copy_content_ops)

    def is_stale(row, attr):
        if row.is_pending:
            return False, None
        model_value = current_values.get((row.sheet_id, attr), None)
        if model_value is MISSING:
            return True, MISSING
        if (row.sheet_id, attr) not in current_values:
            return False, None
        if model_value != row.original.get(attr):
            return True, model_value
        return False, None

    for row, old, new in changes.renames:
        stale_flag, model_value = is_stale(row, "number")
        if stale_flag:
            stale.append((row, None, "number", model_value))
        else:
            clean.renames.append((row, old, new))
    for row, old, new in changes.name_edits:
        stale_flag, model_value = is_stale(row, "name")
        if stale_flag:
            stale.append((row, None, "name", model_value))
        else:
            clean.name_edits.append((row, old, new))
    for row, column, old, new in changes.param_edits:
        stale_flag, model_value = is_stale(row, column.attr)
        if stale_flag:
            stale.append((row, column, column.attr, model_value))
        else:
            clean.param_edits.append((row, column, old, new))
    for bucket_name in ("revision_adds", "revision_removes",
                        "cloud_hide_requests", "cloud_unhide_candidates"):
        for row, column, revision_id in getattr(changes, bucket_name):
            stale_flag, model_value = is_stale(row, column.attr)
            if stale_flag:
                stale.append((row, column, column.attr, model_value))
            else:
                getattr(clean, bucket_name).append((row, column, revision_id))
    return clean, stale


def record_stale_changes(results, stale, columns_by_attr=None):
    """Add one skipped Errors/Warnings line per stale cell."""
    columns_by_attr = columns_by_attr or {}
    for row, column, attr, model_value in stale:
        if column is not None:
            label = column.header
        elif attr == "number":
            label = "Sheet Number"
        elif attr == "name":
            label = "Sheet Name"
        else:
            label = attr
        if model_value is MISSING:
            message = ("Skipped: sheet no longer exists in the model.")
        else:
            if isinstance(model_value, bool):
                shown = u"On" if model_value else u"Off"
            else:
                shown = u"{0}".format(model_value)
            message = (u"Skipped: changed in the model since load "
                       u"(now '{0}') - review and re-apply.".format(shown))
        results.add_error(row.number, label, message, status="skipped")


def export_columns(columns):
    """Columns that appear in an Excel export (everything but select/#)."""
    return [column for column in columns
            if column.kind not in (KIND_SELECT, KIND_INDEX)]


def build_export_matrix(columns, rows):
    """WYSIWYG export payload from the staged table.

    -> (header_cells, metadata_rows, data_rows, lock_rows)
    header_cells: ["ElementId", header, ...]
    metadata_rows: per export column [key, kind, header, param_name,
        param_id_value, revision_id, revision_seq, read_only]
    data_rows: per row ["<sheet id>", cell text..., revisions as Yes/No]
    lock_rows: booleans mirroring data_rows (True = write cell locked)
    """
    cols = export_columns(columns)
    header_cells = [u"ElementId"]
    metadata_rows = []
    for column in cols:
        header_cells.append(column.header)
        metadata_rows.append([
            column.key,
            column.kind,
            column.header,
            column.param_name or u"",
            u"" if column.param_id_value is None
            else u"{0}".format(column.param_id_value),
            u"" if column.revision_id is None
            else u"{0}".format(column.revision_id),
            u"" if column.revision_seq is None
            else u"{0}".format(column.revision_seq),
            u"Yes" if column.is_read_only else u"No",
        ])
    data_rows = []
    lock_rows = []
    for row in rows:
        cells = [u"" if row.sheet_id is None
                 else u"{0}".format(row.sheet_id)]
        locks = [True]
        for column in cols:
            value = getattr(row, column.attr, None)
            if column.kind == KIND_REVISION:
                cells.append(u"Yes" if value else u"No")
                locks.append(row.get_state(column.attr) == STATE_LOCKED)
            else:
                cells.append(u"" if value is None
                             else u"{0}".format(value))
                locks.append(base_state(row, column)
                             in (STATE_LOCKED, STATE_DUPLICATED))
        data_rows.append(cells)
        lock_rows.append(locks)
    return header_cells, metadata_rows, data_rows, lock_rows


def mark_pending_row_dirty(row, columns):
    """Render an import-created row entirely as staged (red) content."""
    for column in columns:
        if column.kind in (KIND_SELECT, KIND_INDEX):
            continue
        if column.kind == KIND_REVISION:
            if getattr(row, column.attr, False):
                row.original[column.attr] = False
                refresh_cell_state(row, column)
        else:
            value = getattr(row, column.attr, u"")
            if value:
                row.original[column.attr] = u""
                refresh_cell_state(row, column)


class CopySheetRequest(object):
    """Copy Sheet Info payload: source sheet info onto target sheets."""

    def __init__(self, source_sheet_id, source_number,
                 dup_sheet_info, dup_tb_info, dup_detailing, dup_with_views,
                 target_sheet_ids):
        self.source_sheet_id = source_sheet_id
        self.source_number = source_number
        self.dup_sheet_info = bool(dup_sheet_info)
        self.dup_tb_info = bool(dup_tb_info)
        self.dup_detailing = bool(dup_detailing)
        self.dup_with_views = bool(dup_with_views)
        self.target_sheet_ids = list(target_sheet_ids)


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
