# -*- coding: utf-8 -*-
"""Pure-Python state model for the Family Types grid.

Types are rows and parameters are columns - the shape of a Revit type
catalogue, and the shape the Excel export mirrors cell for cell.

Deliberately free of pyrevit/.NET imports so the desktop test suite can load
it standalone (same pattern as ``sheet_manager_state.py``, whose staged-edit
vocabulary this follows: a ``ParamColumn`` carries the generated ``attr`` a
WPF binding uses, every value attr has an ``<attr>_state`` sibling that drives
the cell colour, and ``original`` holds the snapshot the dirty diff is taken
against).
"""

from __future__ import print_function


STATE_NORMAL = "normal"
STATE_DIRTY = "dirty"
STATE_LOCKED = "locked"
STATE_CONFLICT = "conflict"

KIND_TYPE_NAME = "type_name"
KIND_PARAM = "param"

#: Storage types as ``FamilyParameter.StorageType`` names them.
STORAGE_DOUBLE = "Double"
STORAGE_INTEGER = "Integer"
STORAGE_STRING = "String"
STORAGE_ELEMENTID = "ElementId"

#: What a cell shows for a yes/no parameter, and what import accepts back.
YES_TEXT = u"Yes"
NO_TEXT = u"No"
_TRUTHY_CELLS = ("yes", "y", "true", "1", "x")
_FALSY_CELLS = ("no", "n", "false", "0")

#: The row name a family with no named types gets. Revit keeps such a family's
#: values on its current type, which has no name of its own; the row is shown
#: so the values are visible and editable, but it cannot be renamed or deleted.
DEFAULT_TYPE_LABEL = u"(default)"

#: Appended to an instance parameter's column header. Instance parameters do
#: carry a per-type value - it is the default a new instance is placed with -
#: which is exactly what Revit's own Family Types dialog marks this way.
INSTANCE_SUFFIX = u" (default)"

LOCK_FORMULA = "formula"
LOCK_REPORTING = "reporting"
LOCK_ELEMENT_REF = "element reference"

IMPORT_NEW = "New"
IMPORT_CHANGED = "Changed"
IMPORT_UNCHANGED = "Unchanged"
IMPORT_MISSING = "Not in file"


def safe_text(value):
    if value is None:
        return u""
    try:
        return u"{0}".format(value)
    except Exception:
        return u""


def normalize_name(text):
    """Match key for a family type name: collapsed whitespace, lowercased."""
    return u" ".join(safe_text(text).split()).lower()


class ParamColumn(object):
    """One family parameter, rendered as one grid column.

    ``attr`` is the generated binding name (``p0``, ``p1``, ...); ``param_id``
    is the parameter's ElementId value, which is what an Excel round trip
    matches on.  ``lock_reason`` is empty for an editable parameter and
    otherwise names why the column is read-only, so the cell tooltip can say
    it rather than leaving the user guessing.
    """

    def __init__(self, key, header, attr, param_name,
                 param_id=None, storage_type=u"", spec_label=u"",
                 is_instance=False, is_yes_no=False, is_material=False,
                 lock_reason=u"", formula=u"", width=130):
        self.key = key
        self.kind = KIND_PARAM
        self.header = header
        self.attr = attr
        self.param_name = param_name
        self.param_id = param_id
        self.storage_type = storage_type
        self.spec_label = spec_label
        self.is_instance = bool(is_instance)
        self.is_yes_no = bool(is_yes_no)
        self.is_material = bool(is_material)
        self.lock_reason = lock_reason or u""
        self.formula = formula or u""
        self.width = width
        #: Set by the window's Hide Empty Columns toggle.
        self.is_hidden = False

    @property
    def is_read_only(self):
        return bool(self.lock_reason)

    def tooltip(self):
        bits = [self.param_name]
        if self.is_instance:
            bits.append(u"Instance parameter - the value a new instance "
                        u"is placed with.")
        else:
            bits.append(u"Type parameter.")
        if self.spec_label:
            bits.append(u"Type of parameter: {0}".format(self.spec_label))
        if self.formula:
            bits.append(u"Formula: {0}".format(self.formula))
        if self.lock_reason:
            bits.append(u"Read-only ({0}).".format(self.lock_reason))
        return u"\n".join(bits)


class TypeNameColumn(object):
    """The one fixed column: the family type's name."""

    def __init__(self, width=200):
        self.key = "type_name"
        self.kind = KIND_TYPE_NAME
        self.header = u"Family Type"
        self.attr = "type_name"
        self.param_name = u""
        self.param_id = None
        self.storage_type = u""
        self.spec_label = u""
        self.is_instance = False
        self.is_yes_no = False
        self.is_material = False
        self.lock_reason = u""
        self.formula = u""
        self.width = width
        self.is_hidden = False

    @property
    def is_read_only(self):
        return False

    def tooltip(self):
        return u"Family type name"


def fixed_columns():
    return [TypeNameColumn()]


def _column_sort_key(info):
    """Type parameters first, then instance parameters, each alphabetical.

    Matches how Revit's own Family Types dialog stacks them, so somebody who
    knows that dialog finds a parameter where they expect it.
    """
    return (1 if info.get("is_instance") else 0,
            safe_text(info.get("name")).lower())


def lock_reason_for(info):
    """Why this parameter cannot be edited, or "" when it can be.

    Only three things lock a cell, and each is something that genuinely cannot
    be written rather than something the API merely calls read-only:

    ``Parameter.IsReadOnly`` and ``Parameter.UserModifiable`` are deliberately
    NOT consulted. ``FamilyParameter`` derives from ``Parameter`` and inherits
    both, but they describe whether the base class's ``Parameter.Set()`` would
    succeed - and a family parameter is never written that way. It is written
    through ``FamilyManager.Set(familyParameter, value)``, a different door
    entirely. Gating on them locked most of the table on a real family, which
    is the bug this replaced. ``families_downgrade_export._parameter_record``
    reads the same two flags, but only to *describe* a parameter in a
    manifest; that meaning does not transfer to "can the user edit this".

    Anything not locked here is offered for editing. If Revit still refuses a
    particular write, ``family_types_revit._set_value`` records the reason and
    the rest of the transaction still commits, so the cost of being wrong in
    this direction is one reported line - not a column the user cannot reach.
    """
    if info.get("is_determined_by_formula"):
        return LOCK_FORMULA
    if info.get("is_reporting"):
        return LOCK_REPORTING
    # A material is the one ElementId parameter a name in a text cell can
    # resolve: materials are unique by name inside a family document. A family
    # type or image reference has no such lookup, so it is shown and locked.
    if info.get("storage_type") == STORAGE_ELEMENTID \
            and not info.get("is_material"):
        return LOCK_ELEMENT_REF
    return u""


def build_parameter_columns(param_infos):
    """One ParamColumn per family parameter.

    ``param_infos``: dicts with name / param_id / storage_type / spec_label /
    is_instance / is_yes_no / is_material / is_read_only / is_reporting /
    user_modifiable / is_determined_by_formula / formula.
    """
    columns = []
    for position, info in enumerate(sorted(param_infos or [],
                                           key=_column_sort_key)):
        name = safe_text(info.get("name"))
        if not name:
            continue
        header = name + (INSTANCE_SUFFIX if info.get("is_instance") else u"")
        columns.append(ParamColumn(
            key=u"p:{0}".format(name),
            header=header,
            attr="p{0}".format(position),
            param_name=name,
            param_id=info.get("param_id"),
            storage_type=safe_text(info.get("storage_type")),
            spec_label=safe_text(info.get("spec_label")),
            is_instance=bool(info.get("is_instance")),
            is_yes_no=bool(info.get("is_yes_no")),
            is_material=bool(info.get("is_material")),
            lock_reason=lock_reason_for(info),
            formula=safe_text(info.get("formula")),
        ))
    return columns


def param_columns(columns):
    return [column for column in columns if column.kind == KIND_PARAM]


def columns_by_key(columns):
    return dict((column.key, column) for column in columns)


class TypeRowBase(object):
    """One family type. The UI layer subclasses it with change notification.

    ``is_new`` marks a row added in the grid or by an import - it has no
    family type behind it yet.  ``is_marked_deleted`` is a staged delete: the
    row stays visible, struck through, until Apply.
    """

    def __init__(self, type_name, is_new=False, is_default_row=False):
        self.type_name = safe_text(type_name)
        self.type_name_state = STATE_NORMAL
        self.is_new = bool(is_new)
        self.is_default_row = bool(is_default_row)
        self.is_marked_deleted = False
        self.original = {}

    # -- hooks the WPF subclass overrides to raise PropertyChanged ---------

    def notify(self, attr):
        del attr

    def set_value(self, attr, value):
        setattr(self, attr, value)
        self.notify(attr)

    def set_state(self, attr, state):
        setattr(self, attr + "_state", state)
        self.notify(attr + "_state")

    def get_state(self, attr):
        return getattr(self, attr + "_state", STATE_NORMAL)

    @property
    def can_rename(self):
        return not self.is_default_row

    @property
    def can_delete(self):
        return not self.is_default_row and not self.is_new


def base_state(row, column):
    """Cell state ignoring staged edits (what a clean cell should show)."""
    if column.kind == KIND_TYPE_NAME:
        return STATE_NORMAL if row.can_rename else STATE_LOCKED
    if column.is_read_only:
        return STATE_LOCKED
    return STATE_NORMAL


def can_edit_cell(row, column):
    if row.is_marked_deleted:
        return False
    return base_state(row, column) != STATE_LOCKED


def refresh_cell_state(row, column):
    base = base_state(row, column)
    if base == STATE_LOCKED:
        row.set_state(column.attr, base)
        return
    if row.get_state(column.attr) == STATE_CONFLICT:
        return
    current = getattr(row, column.attr, None)
    original = row.original.get(column.attr)
    row.set_state(column.attr,
                  STATE_DIRTY if current != original else base)


def cell_value(column, value):
    """The value a column stores for ``value``, whatever form it arrives in.

    A yes/no column stores a **bool** so a CheckBox can bind straight to the
    attribute; every other column stores text. Import hands us text, Revit
    hands us a bool, and the grid hands us either - this is the one place that
    decides, so the three never disagree.
    """
    if column.kind == KIND_TYPE_NAME:
        return safe_text(value)
    if getattr(column, "is_yes_no", False):
        if isinstance(value, bool):
            return value
        return bool(parse_bool_cell(value, fallback=False))
    return safe_text(value)


def display_text(column, value):
    """What a cell's value reads as - for search, export and messages."""
    if getattr(column, "is_yes_no", False):
        return YES_TEXT if value else NO_TEXT
    return safe_text(value)


def populate_row(row, columns, values=None):
    """Set every value attr, its ``original`` snapshot and its ``_state``."""
    values = values or {}
    for column in columns:
        if column.kind == KIND_TYPE_NAME:
            row.original[column.attr] = row.type_name
            row.set_state(column.attr, base_state(row, column))
            continue
        has_value = column.key in values
        value = cell_value(column, values.get(column.key, u""))
        if not has_value and getattr(column, "is_yes_no", False):
            value = False
        setattr(row, column.attr, value)
        row.original[column.attr] = value
        setattr(row, column.attr + "_state", base_state(row, column))
    return row


def apply_cell_edit(row, column, new_value):
    """Stage one cell edit. Returns True when the edit was accepted."""
    if not can_edit_cell(row, column):
        return False
    if column.kind == KIND_TYPE_NAME:
        row.set_value("type_name", safe_text(new_value))
    else:
        row.set_value(column.attr, cell_value(column, new_value))
    refresh_cell_state(row, column)
    return True


def propagate_cell_edit(rows, column, new_value, skip_row=None):
    """Bulk-apply one value down a column. Returns the rows it changed.

    The multi-cell gesture: select cells down a column, set one, and every
    other selected cell in that column follows. Locked cells refuse
    individually rather than failing the batch (same shape as
    ``sheet_manager_state.propagate_edit``).
    """
    changed = []
    for row in rows:
        if row is skip_row:
            continue
        if apply_cell_edit(row, column, new_value):
            changed.append(row)
    return changed


def mark_row_new(row, columns):
    """Render a row added in the grid or by import entirely as staged."""
    row.is_new = True
    for column in columns:
        row.original[column.attr] = u""
        refresh_cell_state(row, column)


def toggle_delete(row, rows, marked=None):
    """Stage or clear a delete. Returns (ok, message).

    Refuses the delete that would leave the family with no type at all -
    ``FamilyManager.DeleteCurrentType`` has nothing to fall back on, and a
    family with no types is not a thing Revit will hold.
    """
    if not row.can_delete:
        if row.is_new:
            return False, u"Use Remove Type to drop a row you just added."
        return False, u"This family has no named types, so there is " \
                      u"nothing to delete."
    wanted = (not row.is_marked_deleted) if marked is None else bool(marked)
    if wanted and surviving_type_count(rows, extra_deleted=row) < 1:
        return False, u"A family must keep at least one type."
    row.is_marked_deleted = wanted
    row.notify("is_marked_deleted")
    return True, u""


def surviving_type_count(rows, extra_deleted=None):
    """How many types would remain after the staged deletes are applied."""
    count = 0
    for row in rows:
        if row.is_marked_deleted or row is extra_deleted:
            continue
        count += 1
    return count


def find_name_problems(rows):
    """Flag blank and duplicate type names. Returns a list of messages.

    Runs over every row on each edit, because a rename can resolve somebody
    else's conflict as easily as it can create one.
    """
    problems = []
    seen = {}
    for row in rows:
        if row.is_marked_deleted:
            row.set_state("type_name", base_state(row, TypeNameColumn()))
            continue
        key = normalize_name(row.type_name)
        if not key:
            row.set_state("type_name", STATE_CONFLICT)
            problems.append(u"A family type name cannot be blank.")
            continue
        seen.setdefault(key, []).append(row)

    for key, group in seen.items():
        del key
        if len(group) > 1:
            for row in group:
                row.set_state("type_name", STATE_CONFLICT)
            problems.append(
                u"More than one type is called '{0}'.".format(
                    group[0].type_name))
            continue
        row = group[0]
        if row.get_state("type_name") == STATE_CONFLICT:
            row.set_state("type_name", STATE_NORMAL)
        refresh_cell_state(row, TypeNameColumn())
    return problems


def count_staged_cells(rows, columns):
    count = 0
    for row in rows:
        if row.is_marked_deleted or row.is_new:
            continue
        for column in columns:
            if row.get_state(column.attr) == STATE_DIRTY:
                count += 1
    return count


def row_matches_search(row, columns, text):
    needle = safe_text(text).strip().lower()
    if not needle:
        return True
    if needle in safe_text(row.type_name).lower():
        return True
    for column in param_columns(columns):
        value = display_text(column, getattr(row, column.attr, u""))
        if needle in value.lower():
            return True
        if needle in safe_text(column.header).lower():
            return True
    return False


def search_rows(rows, columns, text):
    return [row for row in rows if row_matches_search(row, columns, text)]


class StagedChanges(object):
    """Diff of the grid against the family it was read from."""

    def __init__(self):
        self.deletes = []       # rows
        self.new_types = []     # rows
        self.renames = []       # (row, old_name, new_name)
        self.value_edits = []   # (row, column, old, new)

    def is_empty(self):
        return not (self.deletes or self.new_types or self.renames
                    or self.value_edits)

    def summary_text(self):
        bits = []
        if self.new_types:
            bits.append(u"{0} new type(s)".format(len(self.new_types)))
        if self.renames:
            bits.append(u"{0} rename(s)".format(len(self.renames)))
        if self.deletes:
            bits.append(u"{0} delete(s)".format(len(self.deletes)))
        if self.value_edits:
            bits.append(u"{0} value edit(s)".format(len(self.value_edits)))
        return u", ".join(bits) if bits else u"no changes"


def compute_staged_changes(rows, columns):
    """What Apply would do, in the order it has to happen.

    A delete frees its name and a rename frees the old one, so both run before
    any new type is created - otherwise a swap ("A" renamed to "B" while a new
    "A" appears) would collide inside Revit.
    """
    changes = StagedChanges()
    params = param_columns(columns)
    for row in rows:
        if row.is_marked_deleted:
            if not row.is_new:
                changes.deletes.append(row)
            continue
        if row.is_new:
            changes.new_types.append(row)
        else:
            old_name = row.original.get("type_name", row.type_name)
            if row.type_name != old_name:
                changes.renames.append((row, old_name, row.type_name))
        for column in params:
            if row.get_state(column.attr) != STATE_DIRTY:
                continue
            current = getattr(row, column.attr, u"")
            original = row.original.get(column.attr, u"")
            changes.value_edits.append((row, column, original, current))
    return changes


def parse_bool_cell(text, fallback=None):
    """Read a yes/no cell. Returns True, False or ``fallback``."""
    lowered = safe_text(text).strip().lower()
    if lowered in _TRUTHY_CELLS:
        return True
    if lowered in _FALSY_CELLS:
        return False
    return fallback


# ---------------------------------------------------------------- export


def export_columns(columns):
    """Parameter columns, in grid order; the type name is column A."""
    return param_columns(columns)


def export_header_cell(column):
    """The two-line header cell: parameter name over its ElementId.

    The id is the round-trip key - it survives a rename in Revit, which the
    name does not - and it is on the visible sheet rather than only in
    ``_metadata`` so somebody reading the file can see what it matched on.
    """
    if column.param_id is None:
        return column.header
    return u"{0}\n{1}".format(column.header, column.param_id)


def parse_header_cell(text):
    """-> (header_text, param_id or None) for a cell written by the export."""
    lines = safe_text(text).split(u"\n")
    header = lines[0].strip() if lines else u""
    param_id = None
    if len(lines) > 1:
        try:
            param_id = int(float(lines[1].strip()))
        except (TypeError, ValueError):
            param_id = None
    return header, param_id


def header_to_param_name(header):
    """Undo the "(default)" an instance parameter's header carries."""
    text = safe_text(header)
    if text.endswith(INSTANCE_SUFFIX):
        return text[:-len(INSTANCE_SUFFIX)]
    return text


METADATA_HEADERS = ["ColumnKey", "Header", "ParamName", "ParamId",
                    "StorageType", "SpecLabel", "IsInstance", "IsYesNo",
                    "IsMaterial", "IsReadOnly", "LockReason", "Formula"]


def build_export_matrix(columns, rows):
    """WYSIWYG export payload from the staged table.

    -> (header_cells, metadata_rows, data_rows, lock_flags)
    header_cells: ["Family Type", "<name>\\n<id>", ...]
    data_rows:    [type name, cell text, ...] per row
    lock_flags:   one bool per export column (True = write the cells locked)
    """
    cols = export_columns(columns)
    header_cells = [u"Family Type"]
    metadata_rows = []
    lock_flags = [False]
    for column in cols:
        header_cells.append(export_header_cell(column))
        lock_flags.append(bool(column.is_read_only))
        metadata_rows.append([
            column.key,
            column.header,
            column.param_name,
            u"" if column.param_id is None
            else u"{0}".format(column.param_id),
            column.storage_type,
            column.spec_label,
            u"Yes" if column.is_instance else u"No",
            u"Yes" if column.is_yes_no else u"No",
            u"Yes" if column.is_material else u"No",
            u"Yes" if column.is_read_only else u"No",
            column.lock_reason,
            column.formula,
        ])
    data_rows = []
    for row in rows:
        if row.is_marked_deleted:
            continue
        cells = [safe_text(row.type_name)]
        for column in cols:
            cells.append(display_text(column,
                                      getattr(row, column.attr, u"")))
        data_rows.append(cells)
    return header_cells, metadata_rows, data_rows, lock_flags


# ---------------------------------------------------------------- import


class ImportEntry(object):
    """One row of the workbook, matched against the grid.

    ``edits`` is [(column, new_text)] for a matched row, or the full value set
    for a new type. ``status`` drives both the dialog's tick and its label.
    """

    def __init__(self, key, type_name, status, row=None, edits=None,
                 skipped_locked=0):
        self.key = key
        self.type_name = type_name
        self.status = status
        self.row = row
        self.edits = edits or []
        self.skipped_locked = skipped_locked
        self.is_selected = status in (IMPORT_NEW, IMPORT_CHANGED)

    @property
    def change_text(self):
        if self.status == IMPORT_NEW:
            return u"{0} value(s)".format(len(self.edits))
        if self.status == IMPORT_CHANGED:
            return u"{0} cell(s) differ".format(len(self.edits))
        if self.status == IMPORT_MISSING:
            return u"only deleted if the box below is ticked"
        return u"nothing to do"


class ImportPlan(object):
    def __init__(self):
        self.entries = []
        self.unmatched_columns = []   # header text we could not place
        self.skipped_locked = 0       # read-only cells the file tried to set
        self.matched_columns = 0

    def selectable(self):
        return [entry for entry in self.entries
                if entry.status != IMPORT_MISSING]

    def missing(self):
        return [entry for entry in self.entries
                if entry.status == IMPORT_MISSING]

    def is_empty(self):
        return not any(entry.edits for entry in self.entries) \
            and not self.missing()


def _metadata_column_keys(metadata_rows):
    keys = []
    for _, cells in metadata_rows or []:
        if not cells:
            continue
        first = safe_text(cells[0])
        if first in (METADATA_HEADERS[0],) or first.startswith(u"EasyBIM"):
            continue
        keys.append(first)
    return keys


def map_file_columns(header_cells, metadata_rows, columns):
    """Resolve each workbook column onto a grid column.

    Order of preference: the ElementId on header line 2, then the parameter
    name on line 1, then the ``_metadata`` ColumnKey by position. A column
    that resolves to nothing is reported rather than guessed at.
    """
    by_id = {}
    by_name = {}
    for column in param_columns(columns):
        if column.param_id is not None:
            by_id.setdefault(column.param_id, column)
        by_name.setdefault(normalize_name(column.param_name), column)
    by_key = columns_by_key(columns)

    meta_keys = _metadata_column_keys(metadata_rows)
    file_columns = [None]        # position 0 is the type name
    unmatched = []
    for position, cell in enumerate(list(header_cells)[1:]):
        header, param_id = parse_header_cell(cell)
        column = None
        if param_id is not None:
            column = by_id.get(param_id)
        if column is None:
            column = by_name.get(normalize_name(header_to_param_name(header)))
        if column is None and position < len(meta_keys):
            column = by_key.get(meta_keys[position])
        if column is None and header:
            unmatched.append(header)
        file_columns.append(column)
    return file_columns, unmatched


def plan_import(export_rows, metadata_rows, rows, columns):
    """Match workbook rows to grid rows and classify each one.

    Rows match by type name - the type-catalogue contract. A name the family
    does not have is a new type, which is why a rename belongs in the grid and
    not in the spreadsheet: through Excel the two are indistinguishable.
    """
    plan = ImportPlan()
    if not export_rows:
        return plan
    header_cells = export_rows[0][1]
    data_rows = export_rows[1:]

    file_columns, plan.unmatched_columns = map_file_columns(
        header_cells, metadata_rows, columns)
    plan.matched_columns = len([c for c in file_columns if c is not None])

    rows_by_name = {}
    for row in rows:
        if row.is_marked_deleted:
            continue
        rows_by_name.setdefault(normalize_name(row.type_name), row)

    seen_names = set()
    for excel_row, cells in data_rows:
        if not cells or not any(safe_text(cell).strip() for cell in cells):
            continue
        type_name = safe_text(cells[0]).strip()
        if not type_name:
            continue
        key = normalize_name(type_name)
        if key in seen_names:
            continue
        seen_names.add(key)
        del excel_row

        target = rows_by_name.get(key)
        edits = []
        skipped = 0
        for position, column in enumerate(file_columns):
            if position == 0 or column is None or position >= len(cells):
                continue
            text = safe_text(cells[position])
            wanted = cell_value(column, text)
            if column.is_read_only:
                current = u"" if target is None \
                    else display_text(column,
                                      getattr(target, column.attr, u""))
                if text != current:
                    skipped += 1
                continue
            if target is None:
                if text:
                    edits.append((column, wanted))
                continue
            if wanted != cell_value(
                    column, getattr(target, column.attr, u"")):
                edits.append((column, wanted))
        plan.skipped_locked += skipped

        if target is None:
            status = IMPORT_NEW
        elif edits:
            status = IMPORT_CHANGED
        else:
            status = IMPORT_UNCHANGED
        plan.entries.append(ImportEntry(key, type_name, status, target,
                                        edits, skipped))

    for row in rows:
        if row.is_marked_deleted or row.is_new:
            continue
        key = normalize_name(row.type_name)
        if key in seen_names:
            continue
        plan.entries.append(
            ImportEntry(key, row.type_name, IMPORT_MISSING, row))
    return plan


class ImportResult(object):
    def __init__(self):
        self.updated = 0
        self.created = 0
        self.deleted = 0
        self.cells = 0
        self.refused_deletes = []

    def text(self):
        bits = []
        if self.cells:
            bits.append(u"{0} cell(s) staged on {1} existing type(s)".format(
                self.cells, self.updated))
        if self.created:
            bits.append(u"{0} new type(s)".format(self.created))
        if self.deleted:
            bits.append(u"{0} type(s) marked for deletion".format(
                self.deleted))
        return u", ".join(bits) if bits else u"nothing to stage"


def apply_import_selection(plan, chosen_keys, rows, columns,
                           row_factory, delete_missing=False):
    """Stage the ticked entries into the grid. Nothing reaches Revit here.

    ``row_factory(type_name)`` builds a row of whatever class the UI binds.
    """
    result = ImportResult()
    chosen = set(chosen_keys or [])
    for entry in plan.entries:
        if entry.status == IMPORT_MISSING:
            if not delete_missing or entry.row is None:
                continue
            ok, _ = toggle_delete(entry.row, rows, marked=True)
            if ok:
                result.deleted += 1
            else:
                result.refused_deletes.append(entry.type_name)
            continue
        if entry.key not in chosen or not entry.edits:
            continue
        if entry.row is None:
            row = row_factory(entry.type_name)
            populate_row(row, columns,
                         dict((column.key, text)
                              for column, text in entry.edits))
            mark_row_new(row, columns)
            rows.append(row)
            result.created += 1
            continue
        staged = 0
        for column, text in entry.edits:
            if apply_cell_edit(entry.row, column, text):
                staged += 1
        if staged:
            result.updated += 1
            result.cells += staged
    return result
