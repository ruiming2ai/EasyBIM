# -*- coding: utf-8 -*-
"""Pure state and planning for Load Parameters.

Deliberately free of every Revit and WPF import so the test suite can load it
standalone (``Development Space/tests/test_load_parameters_state.py``).  All the
parts that are genuinely easy to get wrong live here rather than in the Revit
layer: the shared-parameter file header, dedupe by name *and* GUID, the
name-collision rules that would otherwise blow up mid-run, backup-file
filtering, and the dry-run plan that the confirmation dialog and the executor
both read.

The plan/execute split is the repo contract (Tag Align): the confirmation must
report exactly what the write will do, so preview and write cannot drift.
"""

from __future__ import print_function


# ---------------------------------------------------------------------------
# row sources
# ---------------------------------------------------------------------------

SOURCE_PROJECT = "project"
SOURCE_FILE = "file"

SOURCE_FOLDER = "folder"

# What to do when a family already carries the parameter.  "Skip" is the safe
# default; "Update" is what a second run usually wants, because the reason to
# run again is normally that a group or an instance/type flag was wrong.
MODE_SKIP = "skip"
MODE_UPDATE = "update"

# Plan cell statuses.  Everything the run will do or refuse to do is one of
# these, and every one of them is shown to the user - nothing is ever silent.
WILL_ADD = "will_add"
WILL_UPDATE = "will_update"
SKIP_EXISTS = "skip_exists"
SKIP_NOT_EDITABLE = "skip_not_editable"
SKIP_NOT_CHECKED_OUT = "skip_not_checked_out"
SKIP_OPEN_IN_SESSION = "skip_open_in_session"
SKIP_READONLY_FILE = "skip_readonly_file"
SKIP_UNSUPPORTED_SPEC = "skip_unsupported_spec"
SKIP_NAME_COLLISION = "skip_name_collision"
SKIP_GUID_CONFLICT = "skip_guid_conflict"

STATUS_LABELS = {
    WILL_ADD: "will be added",
    WILL_UPDATE: "will be updated in place",
    SKIP_EXISTS: "already present",
    SKIP_NOT_EDITABLE: "family cannot be edited",
    SKIP_NOT_CHECKED_OUT: "owned by another user",
    SKIP_OPEN_IN_SESSION: "already open in this Revit session",
    SKIP_READONLY_FILE: "file is read-only",
    SKIP_UNSUPPORTED_SPEC: "data type cannot be recreated",
    SKIP_NAME_COLLISION: "two selected parameters share this name",
    SKIP_GUID_CONFLICT: "name already used by a different shared parameter",
}

SKIP_STATUSES = frozenset([
    SKIP_EXISTS,
    SKIP_NOT_EDITABLE,
    SKIP_NOT_CHECKED_OUT,
    SKIP_OPEN_IN_SESSION,
    SKIP_READONLY_FILE,
    SKIP_UNSUPPORTED_SPEC,
    SKIP_NAME_COLLISION,
    SKIP_GUID_CONFLICT,
])


def safe_text(value):
    """str() that never raises, matching easybim.compat.safe_text.

    Duplicated rather than imported because this module is loaded standalone by
    the tests, without the ``easybim`` package on the path.
    """
    if value is None:
        return u""
    try:
        return u"{0}".format(value)
    except Exception:
        try:
            return value.ToString()
        except Exception:
            return u""


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------


class ParameterRow(object):
    """One shared parameter, and the two settings the user edits for it.

    ``group_id`` and ``is_instance`` feed *both* load actions - a family
    parameter's group and a project binding's group are the same GroupTypeId,
    and Type/Instance means the same thing on both sides.
    """

    def __init__(self, name, guid, spec_id, spec_label,
                 source=SOURCE_PROJECT, source_label=u"Project",
                 definition_group=u"", description=u"", visible=True,
                 user_modifiable=True, hide_when_no_value=False,
                 group_id=u"", group_label=u"", is_instance=False,
                 supported=True, unsupported_reason=u""):
        self.name = safe_text(name)
        self.guid = safe_text(guid)
        self.spec_id = safe_text(spec_id)
        self.spec_label = safe_text(spec_label) or self.spec_id
        self.source = source
        self.source_label = safe_text(source_label)
        self.definition_group = safe_text(definition_group)
        self.description = safe_text(description)
        self.visible = bool(visible)
        self.user_modifiable = bool(user_modifiable)
        self.hide_when_no_value = bool(hide_when_no_value)
        self.group_id = safe_text(group_id)
        self.group_label = safe_text(group_label)
        self.is_instance = bool(is_instance)
        self.supported = bool(supported)
        self.unsupported_reason = safe_text(unsupported_reason)
        self.is_checked = False

    @property
    def key(self):
        """Identity is the GUID; the name is only a label."""
        return self.guid.lower()

    @property
    def binding_label(self):
        return u"Instance" if self.is_instance else u"Type"

    def display(self):
        return u"{0} [{1}]".format(self.name, self.spec_label)


class FamilyRow(object):
    """A family to write into - either one in the project, or an .rfa on disk.

    ``element_id`` is stored rather than the ``Family`` object: LoadFamily
    mutates the project between iterations, so a cached .NET reference goes
    stale halfway through the run.
    """

    def __init__(self, name, source=SOURCE_PROJECT, element_id=None, path=u"",
                 category=u"", editable=True, skip_reason=u"",
                 saved_in_version=u"", is_read_only=False, root=u""):
        self.name = safe_text(name)
        self.source = source
        self.element_id = element_id
        self.path = safe_text(path)
        # The folder the scan started from, so a mirrored output can rebuild
        # the source tree even when several folders were added in one session.
        self.root = safe_text(root)
        self.category = safe_text(category)
        self.editable = bool(editable)
        self.skip_reason = safe_text(skip_reason)
        self.saved_in_version = safe_text(saved_in_version)
        self.is_read_only = bool(is_read_only)
        self.is_checked = False

    @property
    def key(self):
        if self.source == SOURCE_FOLDER:
            return u"folder|" + self.path.lower()
        return u"project|" + safe_text(self.element_id)

    @property
    def source_label(self):
        return u"Project" if self.source == SOURCE_PROJECT else self.path

    @property
    def note(self):
        """The dim text beside the name in the list."""
        if self.skip_reason:
            return u"- {0}".format(self.skip_reason)
        if self.source == SOURCE_FOLDER:
            if self.saved_in_version:
                return u"- {0}, will be upgraded".format(self.saved_in_version)
            return u"- from folder"
        return u"- {0}".format(self.category) if self.category else u""


class GroupChoice(object):
    """One entry in the Group combo: a GroupTypeId and its localized label."""

    def __init__(self, group_id, label):
        self.group_id = safe_text(group_id)
        self.label = safe_text(label)


class CategoryRow(object):
    """A project category a parameter can be bound to."""

    def __init__(self, name, category_id, discipline=u""):
        self.name = safe_text(name)
        self.category_id = category_id
        self.discipline = safe_text(discipline)
        self.is_checked = False

    @property
    def key(self):
        return safe_text(self.category_id)


# ---------------------------------------------------------------------------
# selection helpers - shared by every list in the window
# ---------------------------------------------------------------------------


def select_all(rows):
    for row in rows or []:
        row.is_checked = True
    return rows


def select_none(rows):
    for row in rows or []:
        row.is_checked = False
    return rows


def invert_selection(rows):
    for row in rows or []:
        row.is_checked = not row.is_checked
    return rows


def apply_check_to_rows(rows, is_checked):
    """Bulk-toggle, returning ONLY the rows that actually moved.

    The caller raises PropertyChanged per returned row instead of reassigning
    ItemsSource, which would throw away the shift-selection mid-gesture.
    """
    changed = []
    for row in rows or []:
        if bool(row.is_checked) != bool(is_checked):
            row.is_checked = bool(is_checked)
            changed.append(row)
    return changed


def checked_rows(rows):
    return [row for row in rows or [] if row.is_checked]


def propagate_value(rows, field, value, skip_row=None):
    """Apply one edited cell to every other selected row.

    ``skip_row`` is the row the user actually edited - WPF has already written
    that one, and rewriting it would fight the binding.
    """
    changed = []
    for row in rows or []:
        if row is skip_row:
            continue
        if getattr(row, field, None) == value:
            continue
        setattr(row, field, value)
        changed.append(row)
    return changed


def set_group(rows, group_id, group_label, skip_row=None):
    """Set the family/binding group on several rows at once."""
    changed = []
    for row in rows or []:
        if row is skip_row:
            continue
        if row.group_id == group_id:
            continue
        row.group_id = safe_text(group_id)
        row.group_label = safe_text(group_label)
        changed.append(row)
    return changed


def filter_rows(rows, text):
    """Case-insensitive substring match over a row's user-visible fields."""
    needle = safe_text(text).strip().lower()
    if not needle:
        return list(rows or [])
    matched = []
    for row in rows or []:
        haystack = u" ".join([
            safe_text(getattr(row, "name", u"")),
            safe_text(getattr(row, "spec_label", u"")),
            safe_text(getattr(row, "source_label", u"")),
            safe_text(getattr(row, "definition_group", u"")),
            safe_text(getattr(row, "category", u"")),
        ]).lower()
        if needle in haystack:
            matched.append(row)
    return matched


def sort_parameter_rows(rows):
    return sorted(rows or [], key=lambda row: (row.name.lower(), row.guid))


def sort_family_rows(rows):
    return sorted(
        rows or [],
        key=lambda row: (row.source != SOURCE_PROJECT,
                         row.name.lower(),
                         row.path.lower()),
    )


def merge_rows(existing, incoming):
    """Add rows that are not already present, keyed by ``row.key``.

    Existing rows keep their current checkbox and edited values - re-running
    "Add All Project Families" must not silently reset what the user set up.
    """
    seen = set(row.key for row in existing or [])
    merged = list(existing or [])
    for row in incoming or []:
        if row.key in seen:
            continue
        seen.add(row.key)
        merged.append(row)
    return merged


def categories_for_families(family_rows, category_rows):
    """Category rows matching the categories of the checked families.

    Used to pre-tick the Categories tab.  Only ever *adds*: an unticked
    category stays unticked, because re-applying it on every family click
    would make the tab impossible to correct.
    """
    wanted = set()
    for family in family_rows or []:
        if family.is_checked and family.category:
            wanted.add(family.category.lower())
    return [row for row in category_rows or []
            if row.name.lower() in wanted]


# ---------------------------------------------------------------------------
# .rfa scanning
# ---------------------------------------------------------------------------

BACKUP_SUFFIX_DIGITS = 4


def is_backup_family_file(file_name):
    """True for Revit's own backups, ``<name>.0001.rfa``.

    They are byte-identical siblings of the real family, so processing them
    doubles the run and writes files nobody loads.
    """
    name = safe_text(file_name)
    if not name.lower().endswith(u".rfa"):
        return False
    stem = name[:-4]
    dot = stem.rfind(u".")
    if dot < 0:
        return False
    suffix = stem[dot + 1:]
    if len(suffix) != BACKUP_SUFFIX_DIGITS:
        return False
    return suffix.isdigit()


def family_files_from_names(folder, file_names):
    """Filter one directory listing down to real family files."""
    keep = []
    for name in sorted(file_names or []):
        lowered = safe_text(name).lower()
        if not lowered.endswith(u".rfa"):
            continue
        if is_backup_family_file(name):
            continue
        keep.append((safe_text(folder), safe_text(name)))
    return keep


# ---------------------------------------------------------------------------
# the temporary shared parameter file
# ---------------------------------------------------------------------------

# Only the header is written by hand.  Every *GROUP and *PARAM line is written
# by Revit itself through Definitions.Create, which avoids hand-mapping a
# ForgeTypeId onto the legacy DATATYPE tokens (LENGTH, HVAC_PRESSURE, #OTHER#)
# - a version-sensitive mapping with no public helper - and keeps our own bytes
# pure ASCII, so encoding stays Revit's problem rather than ours.
SHARED_PARAMETER_HEADER = u"\n".join([
    u"# This is a Revit shared parameter file.",
    u"# Do not edit manually.",
    u"*META\tVERSION\tMINVERSION",
    u"META\t2\t1",
    u"",
])

DEFAULT_DEFINITION_GROUP = u"EasyBIM"


def definition_group_name(row):
    """Which group the definition is recreated under in the temp file.

    Mirroring the source group keeps a round-trip through this tool readable
    when the file is inspected; project-sourced parameters have no group of
    their own, so they land in one named group rather than an empty string,
    which Revit rejects.
    """
    return row.definition_group or DEFAULT_DEFINITION_GROUP


def dedupe_parameter_rows(rows):
    """Collapse rows that are the same parameter, keyed by GUID.

    Returns ``(unique_rows, dropped_rows)``.  The same shared parameter easily
    arrives twice - once from the project and once from a .txt - and
    Definitions.Create would throw on the second.
    """
    unique = []
    dropped = []
    seen = set()
    for row in rows or []:
        if row.key in seen:
            dropped.append(row)
            continue
        seen.add(row.key)
        unique.append(row)
    return unique, dropped


def name_collisions(rows):
    """Selected rows that share a name but not a GUID.

    ``Definitions.Create`` throws ArgumentException on a duplicate *name*
    whatever the GUID, and a project that has consumed more than one shared
    parameter file over its life routinely holds two.  Catching it here turns a
    mid-run crash into a dialog.
    """
    by_name = {}
    for row in rows or []:
        by_name.setdefault(row.name.lower(), []).append(row)
    clashing = []
    for _, group in sorted(by_name.items()):
        if len(group) > 1:
            clashing.append(list(group))
    return clashing


def guid_conflicts(rows, project_guids_by_name):
    """Picked rows whose name is already a *different* project parameter.

    Loading one of these would leave the project holding two shared parameters
    with identical names and different GUIDs.  Schedules and filters then pick
    whichever they please, and the result is miserable to diagnose - so it is
    blocked rather than warned about.
    """
    conflicts = []
    for row in rows or []:
        existing = project_guids_by_name.get(row.name.lower())
        if not existing:
            continue
        if safe_text(existing).lower() != row.key:
            conflicts.append((row, safe_text(existing)))
    return conflicts


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------


class PlanCell(object):
    """One (target, parameter) pair and what will happen to it."""

    def __init__(self, target_name, parameter_name, status, detail=u""):
        self.target_name = safe_text(target_name)
        self.parameter_name = safe_text(parameter_name)
        self.status = status
        self.detail = safe_text(detail)

    @property
    def is_work(self):
        return self.status in (WILL_ADD, WILL_UPDATE)

    def line(self):
        text = u"{0} - {1}: {2}".format(
            self.target_name, self.parameter_name, STATUS_LABELS.get(
                self.status, self.status))
        if self.detail:
            text += u" ({0})".format(self.detail)
        return text


class Plan(object):
    """A complete dry run.  ``execute`` reads this, never the rows."""

    def __init__(self, action):
        self.action = action
        self.cells = []
        self.blocked = []          # (row, reason) - never written at all
        self.upgrade_files = []    # .rfa authored in an older Revit
        self.binding_flips = []    # (name, from_label, to_label)
        self.nested_note = False
        self.overlap_names = []    # a folder .rfa that is also a project family

    def add(self, cell):
        self.cells.append(cell)

    @property
    def work_cells(self):
        return [cell for cell in self.cells if cell.is_work]

    @property
    def skipped_cells(self):
        return [cell for cell in self.cells if not cell.is_work]

    @property
    def targets_with_work(self):
        return sorted(set(cell.target_name for cell in self.work_cells))

    def has_work(self):
        return bool(self.work_cells)


ACTION_FAMILIES = "families"
ACTION_PROJECT = "project"


def build_family_plan(parameter_rows, family_rows, mode=MODE_SKIP,
                      existing_lookup=None, project_family_names=None):
    """Dry-run the family write.

    ``existing_lookup(family_row) -> set of lowercase parameter names`` lets the
    Revit layer report "already present" without this module importing Revit.
    When it is None (no cheap way to know yet) every pair is planned as an add
    and the executor reports the real outcome.
    """
    plan = Plan(ACTION_FAMILIES)
    params = checked_rows(parameter_rows)
    families = checked_rows(family_rows)

    for row in params:
        if not row.supported:
            plan.blocked.append(
                (row, row.unsupported_reason
                 or STATUS_LABELS[SKIP_UNSUPPORTED_SPEC]))

    for group in name_collisions(params):
        for row in group:
            plan.blocked.append(
                (row, STATUS_LABELS[SKIP_NAME_COLLISION]))

    blocked_keys = set(row.key for row, _ in plan.blocked)
    usable = [row for row in params if row.key not in blocked_keys]

    project_names = set(
        safe_text(name).lower() for name in project_family_names or [])

    for family in families:
        if family.source == SOURCE_FOLDER:
            stem = family.name.lower()
            if stem in project_names and stem not in plan.overlap_names:
                plan.overlap_names.append(stem)
            if family.saved_in_version:
                plan.upgrade_files.append(family)

        for row in usable:
            status, detail = _family_cell_status(family, row, mode,
                                                 existing_lookup)
            plan.add(PlanCell(family.name, row.name, status, detail))

    plan.nested_note = bool(plan.work_cells)
    return plan


def _family_cell_status(family, row, mode, existing_lookup):
    if not family.editable:
        return SKIP_NOT_EDITABLE, family.skip_reason
    if family.skip_reason:
        return _reason_status(family.skip_reason), family.skip_reason
    if family.source == SOURCE_FOLDER and family.is_read_only:
        return SKIP_READONLY_FILE, u""

    existing = None
    if existing_lookup is not None:
        try:
            existing = existing_lookup(family)
        except Exception:
            existing = None
    if existing and row.name.lower() in existing:
        if mode == MODE_UPDATE:
            return WILL_UPDATE, u""
        return SKIP_EXISTS, u""
    return WILL_ADD, u""


def _reason_status(reason):
    lowered = safe_text(reason).lower()
    if u"another user" in lowered or u"checked out" in lowered:
        return SKIP_NOT_CHECKED_OUT
    if u"open" in lowered:
        return SKIP_OPEN_IN_SESSION
    if u"read-only" in lowered:
        return SKIP_READONLY_FILE
    return SKIP_NOT_EDITABLE


def build_project_plan(parameter_rows, category_rows, bound_lookup=None):
    """Dry-run the project binding.

    ``bound_lookup(row) -> (is_bound, is_instance, category_names)`` reports the
    parameter's current binding so the plan can flag an Instance/Type flip,
    which silently discards the parameter's values.
    """
    plan = Plan(ACTION_PROJECT)
    params = checked_rows(parameter_rows)
    categories = checked_rows(category_rows)

    for row in params:
        if not row.supported:
            plan.blocked.append(
                (row, row.unsupported_reason
                 or STATUS_LABELS[SKIP_UNSUPPORTED_SPEC]))

    for group in name_collisions(params):
        for row in group:
            plan.blocked.append((row, STATUS_LABELS[SKIP_NAME_COLLISION]))

    blocked_keys = set(row.key for row, _ in plan.blocked)
    usable = [row for row in params if row.key not in blocked_keys]

    for row in usable:
        bound, bound_instance, bound_categories = False, False, []
        if bound_lookup is not None:
            try:
                bound, bound_instance, bound_categories = bound_lookup(row)
            except Exception:
                bound, bound_instance, bound_categories = False, False, []

        if bound and bool(bound_instance) != bool(row.is_instance):
            plan.binding_flips.append((
                row.name,
                u"Instance" if bound_instance else u"Type",
                row.binding_label,
            ))

        existing = set(safe_text(name).lower() for name in bound_categories)
        for category in categories:
            if category.name.lower() in existing:
                status = WILL_UPDATE
            else:
                status = WILL_ADD
            plan.add(PlanCell(category.name, row.name, status, u""))

    return plan


# ---------------------------------------------------------------------------
# summaries
# ---------------------------------------------------------------------------


class RunSummary(object):
    """Two buckets, because cancel means two different things.

    Project families roll back to nothing when a run is cancelled; an .rfa that
    has already been saved stays saved, because a file write has no
    transaction.  One combined counter would let the report claim work that is
    gone, or hide work that is not.
    """

    def __init__(self):
        self.applied = []          # (target, parameter, note)
        self.skipped = []          # (target, parameter, reason)
        self.failed = []           # (target, parameter, error)
        self.files_written = []    # paths already on disk, cancel or not
        self.rolled_back = 0
        self.cancelled = False

    def note_applied(self, target, parameter, note=u""):
        self.applied.append((safe_text(target), safe_text(parameter),
                             safe_text(note)))

    def note_skipped(self, target, parameter, reason):
        self.skipped.append((safe_text(target), safe_text(parameter),
                             safe_text(reason)))

    def note_failed(self, target, parameter, error):
        self.failed.append((safe_text(target), safe_text(parameter),
                            safe_text(error)))

    def clear_applied(self):
        """Zero what a rollback undid, so the report cannot claim it."""
        self.rolled_back = len(self.applied)
        self.applied = []


def build_confirm_text(plan, mode=MODE_SKIP):
    """What the user reads before anything is written."""
    lines = []
    work = plan.work_cells
    targets = plan.targets_with_work

    if plan.action == ACTION_FAMILIES:
        lines.append(u"{0} parameter change(s) across {1} family(ies).".format(
            len(work), len(targets)))
    else:
        lines.append(u"{0} binding(s) across {1} category(ies).".format(
            len(work), len(targets)))

    skipped = plan.skipped_cells
    if skipped:
        lines.append(u"{0} pair(s) will be skipped.".format(len(skipped)))
        lines.append(u"")
        for cell in skipped[:40]:
            lines.append(u"  " + cell.line())
        if len(skipped) > 40:
            lines.append(u"  ... and {0} more".format(len(skipped) - 40))

    if plan.blocked:
        lines.append(u"")
        lines.append(u"Blocked - these are not written at all:")
        for row, reason in plan.blocked:
            lines.append(u"  {0}: {1}".format(row.name, reason))

    if plan.overlap_names:
        lines.append(u"")
        lines.append(
            u"These families are selected both from the project and from a "
            u"folder, so the two copies are written separately and can end up "
            u"out of step:")
        for name in plan.overlap_names:
            lines.append(u"  " + name)

    if plan.binding_flips:
        lines.append(u"")
        lines.append(
            u"Changing a bound parameter between Instance and Type discards "
            u"its existing values:")
        for name, was, now in plan.binding_flips:
            lines.append(u"  {0}: {1} -> {2}".format(name, was, now))

    if plan.upgrade_files:
        lines.append(u"")
        lines.append(
            u"{0} family file(s) were saved in an older Revit and will be "
            u"upgraded when written. Anyone still on that release will not be "
            u"able to open them again:".format(len(plan.upgrade_files)))
        for family in plan.upgrade_files[:20]:
            lines.append(u"  {0} ({1})".format(
                family.name, family.saved_in_version))
        if len(plan.upgrade_files) > 20:
            lines.append(u"  ... and {0} more".format(
                len(plan.upgrade_files) - 20))

    if plan.nested_note:
        lines.append(u"")
        lines.append(
            u"Families nested inside another family are not updated; only the "
            u"families listed above are.")

    if mode == MODE_UPDATE:
        lines.append(u"")
        lines.append(
            u"Existing parameters will have their group and Type/Instance "
            u"updated in place.")

    return u"\n".join(lines)


def build_report_text(summary, action=ACTION_FAMILIES):
    """The result report, with the two cancel buckets kept apart."""
    lines = []

    if summary.cancelled:
        lines.append(u"Cancelled.")
        if summary.rolled_back:
            lines.append(
                u"{0} change(s) to project families were rolled back - the "
                u"model is exactly as it was.".format(summary.rolled_back))
        if summary.files_written:
            lines.append(
                u"{0} family file(s) had already been written to disk and "
                u"were kept - a saved file has no undo:".format(
                    len(summary.files_written)))
            for path in summary.files_written:
                lines.append(u"  " + path)
        if not summary.rolled_back and not summary.files_written:
            lines.append(u"Nothing had been written yet.")
    else:
        if action == ACTION_FAMILIES:
            lines.append(u"{0} parameter(s) added or updated.".format(
                len(summary.applied)))
        else:
            lines.append(u"{0} binding(s) written.".format(
                len(summary.applied)))
        if summary.files_written:
            lines.append(u"{0} family file(s) written to disk.".format(
                len(summary.files_written)))

    if summary.applied:
        lines.append(u"")
        lines.append(u"Applied:")
        for target, parameter, note in summary.applied:
            text = u"  {0} - {1}".format(target, parameter)
            if note:
                text += u" ({0})".format(note)
            lines.append(text)

    if summary.skipped:
        lines.append(u"")
        lines.append(u"Skipped:")
        for target, parameter, reason in summary.skipped:
            lines.append(u"  {0} - {1}: {2}".format(target, parameter, reason))

    if summary.failed:
        lines.append(u"")
        lines.append(u"Failed:")
        for target, parameter, error in summary.failed:
            lines.append(u"  {0} - {1}: {2}".format(target, parameter, error))

    return u"\n".join(lines)


def build_headline(summary, action=ACTION_FAMILIES):
    if summary.cancelled:
        return u"Cancelled."
    count = len(summary.applied)
    if not count:
        return u"Nothing to change."
    if action == ACTION_FAMILIES:
        return u"{0} parameter(s) written into families.".format(count)
    return u"{0} project binding(s) written.".format(count)
