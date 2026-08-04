# -*- coding: utf-8 -*-
"""Pure state for Linked Sheets Copy: rows, level mapping, plan, report text.

No Revit imports.  Everything Revit-shaped arrives as plain values gathered by
``linked_sheets_copy_revit``, which is what lets the test suite load this
module standalone and check the decisions that actually bite - the level
resolution ladder, sheet-number collisions, and a dry run that cannot drift
from what the executor does, because both read the object built here.
"""

from __future__ import print_function


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

# 1/8" in decimal feet.  Levels either line up or differ by a storey; anything
# in between is worth showing the user rather than silently matching.
LEVEL_TOLERANCE_FT = 0.0104167

# Second-point check tolerance on the printed sheet.  Above this the host view
# is not a faithful reproduction - wrong scale or wrong rotation - and the row
# is flagged even though its anchor matched.
ALIGNMENT_TOLERANCE_MM = 0.2

SOURCE_MONITORED = u"Monitored"
SOURCE_NAME = u"Name"
SOURCE_ELEVATION = u"Elevation"
SOURCE_MANUAL = u"Manual"
SOURCE_UNMAPPED = u"Unmapped"

ACTION_CREATE = u"create"
ACTION_SKIP = u"skip"

# Option keys.  Only the two title-block options start ticked: everything else
# either copies elements into the host model or is a matching nicety, and the
# user asked for those to be opt-in.  Viewport type and view title are the
# exception - "view, view title, view title length should be exactly matching
# the linked sheet" was the stated requirement, so they ship on.
OPT_TITLE_BLOCK = u"title_block"
OPT_TITLE_BLOCK_PARAMS = u"title_block_params"
OPT_SHEET_PARAMS = u"sheet_params"
OPT_SHEET_DETAILING = u"sheet_detailing"
OPT_REVISION_CLOUDS = u"revision_clouds"
OPT_VIEWPORT_TYPE = u"viewport_type"
OPT_VIEW_TITLE = u"view_title"
OPT_VIEW_TEMPLATE = u"view_template"
OPT_SCOPE_BOX = u"scope_box"
OPT_VIEW_ANNOTATIONS = u"view_annotations"
OPT_HIDE_OTHER_LINKS = u"hide_other_links"

OPTION_DEFS = (
    (OPT_TITLE_BLOCK, u"Copy title block", True,
     u"Place the same title block family and type on the new sheet, at the "
     u"same position on the sheet. The family is copied from the link when "
     u"this model has no type of that name."),
    (OPT_TITLE_BLOCK_PARAMS, u"Copy title block parameters", True,
     u"Copy the writable instance parameters of the linked title block "
     u"(drawn by, checked by, and any project or shared parameters this "
     u"model also has)."),
    (OPT_SHEET_PARAMS, u"Copy sheet parameters", False,
     u"Copy the writable sheet parameters other than number and name, which "
     u"always copy. Parameters this model does not have are reported."),
    (OPT_SHEET_DETAILING, u"Copy sheet detailing", False,
     u"Copy what sits on the linked sheet itself - text, detail lines, "
     u"symbols, filled regions, images. This cannot arrive through the link, "
     u"because a sheet is not part of the linked model's geometry."),
    (OPT_REVISION_CLOUDS, u"Copy revision clouds on sheets", False,
     u"Off by default: a revision cloud silently adds its revision to the "
     u"target sheet, and this model's revisions are not the link's."),
    (OPT_VIEWPORT_TYPE, u"Match viewport type", True,
     u"Give the new viewport the same type as the linked one, so the view "
     u"title looks identical. The type is copied from the link when this "
     u"model has no type of that name."),
    (OPT_VIEW_TITLE, u"Match view title position and line length", True,
     u"Copy the linked viewport's label offset and title line length."),
    (OPT_VIEW_TEMPLATE, u"Apply the host view template of the same name",
     False,
     u"When the linked view has a template and this model has one with the "
     u"same name, apply it. Applied before scale and crop; anything the "
     u"template then locks is reported."),
    (OPT_SCOPE_BOX, u"Match scope box by name", False,
     u"When the linked view uses a scope box and this model has one with the "
     u"same name, assign it. A scope box cannot cross documents, so only the "
     u"name is matched."),
    (OPT_VIEW_ANNOTATIONS, u"Copy the linked view's annotations", False,
     u"WARNING: with the link shown By Linked View, the linked view's own "
     u"text, detail lines and filled regions already draw through the link. "
     u"Copying them draws everything twice unless you switch that link off in "
     u"the new view. Tags and dimensions are never copied - their references "
     u"do not survive the document boundary."),
    (OPT_HIDE_OTHER_LINKS, u"Hide the other RVT links in the new views",
     False,
     u"The other links in this model draw By Host View in the new views, "
     u"which the linked sheet never showed. Tick this to hide them."),
)


def default_options():
    options = {}
    for key, _label, default, _tooltip in OPTION_DEFS:
        options[key] = bool(default)
    return options


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------

def safe_text(value):
    """Best-effort unicode that never raises, CLR objects included."""
    if value is None:
        return u""
    try:
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
    except Exception:
        pass
    try:
        return u"{0}".format(value)
    except Exception:
        pass
    try:
        return value.ToString()
    except Exception:
        return u""


def _norm(value):
    return safe_text(value).strip().lower()


def unique_name(base, taken, separator=u" "):
    """``base``, or ``base 2``, ``base 3``... until it is not in ``taken``.

    ``taken`` is compared case-insensitively because Revit view and sheet
    names are.  The caller is expected to add the result back into ``taken``.
    """
    base = safe_text(base).strip() or u"Unnamed"
    lowered = set(_norm(item) for item in (taken or []))
    if _norm(base) not in lowered:
        return base
    index = 2
    while True:
        candidate = u"{0}{1}{2}".format(base, separator, index)
        if _norm(candidate) not in lowered:
            return candidate
        index += 1


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------

class HostLevelChoice(object):
    """One host level, as offered in the level-mapping dropdown."""

    def __init__(self, key, name, elevation):
        self.key = key
        self.name = safe_text(name)
        self.elevation = float(elevation)

    @property
    def label(self):
        return self.name

    def __repr__(self):
        return "HostLevelChoice({0!r}, {1!r})".format(self.key, self.name)


class LinkedLevel(object):
    def __init__(self, key, name, elevation_host):
        self.key = key
        self.name = safe_text(name)
        # Already converted into host coordinates by the Revit layer, so the
        # elevation comparison here is apples to apples.
        self.elevation_host = float(elevation_host)


class LevelMapRow(object):
    """One linked level and the host level it will be built on."""

    def __init__(self, linked_level, choices):
        self.linked_level = linked_level
        self.choices = list(choices or [])
        self.host_choice = None
        self.source = SOURCE_UNMAPPED
        self.ambiguous = False

    @property
    def linked_name(self):
        return self.linked_level.name

    @property
    def linked_key(self):
        return self.linked_level.key

    @property
    def host_key(self):
        return self.host_choice.key if self.host_choice else None

    @property
    def is_mapped(self):
        return self.host_choice is not None

    @property
    def delta_elevation(self):
        if not self.host_choice:
            return None
        return self.host_choice.elevation - self.linked_level.elevation_host

    @property
    def delta_text(self):
        delta = self.delta_elevation
        if delta is None:
            return u""
        if abs(delta) < LEVEL_TOLERANCE_FT:
            return u"0"
        return u"{0:+.4f} ft".format(delta)

    def set_manual(self, choice):
        """A user pick always wins, and says so in the Source column."""
        self.host_choice = choice
        self.source = SOURCE_MANUAL if choice else SOURCE_UNMAPPED
        self.ambiguous = False


class ViewRow(object):
    """One viewport on a linked sheet."""

    def __init__(self, viewport_key, view_key, view_name, view_type_label,
                 supported=True, skip_reason=u"", level_key=None, scale=None,
                 crop_active=True, template_name=u"", scope_box_name=u"",
                 area_scheme_name=u"", view_family=u""):
        self.viewport_key = viewport_key
        self.view_key = view_key
        self.view_name = safe_text(view_name)
        self.view_type_label = safe_text(view_type_label)
        self.supported = bool(supported)
        self.skip_reason = safe_text(skip_reason)
        self.level_key = level_key
        self.scale = scale
        self.crop_active = bool(crop_active)
        self.template_name = safe_text(template_name)
        self.scope_box_name = safe_text(scope_box_name)
        self.area_scheme_name = safe_text(area_scheme_name)
        self.view_family = safe_text(view_family)


class SheetRow(object):
    """One sheet in the link, as listed in the window."""

    def __init__(self, sheet_key, number, name, view_rows=None,
                 is_placeholder=False, title_block_label=u"",
                 title_block_count=0):
        self.sheet_key = sheet_key
        self.source_number = safe_text(number)
        self.source_name = safe_text(name)
        self.new_number = self.source_number
        self.new_name = self.source_name
        self.is_checked = False
        self.is_placeholder = bool(is_placeholder)
        self.title_block_label = safe_text(title_block_label)
        self.title_block_count = int(title_block_count)
        self.view_rows = list(view_rows or [])
        self.collision = u""

    @property
    def supported_views(self):
        return [row for row in self.view_rows if row.supported]

    @property
    def skipped_views(self):
        return [row for row in self.view_rows if not row.supported]

    @property
    def views_text(self):
        parts = []
        supported = len(self.supported_views)
        if supported:
            parts.append(u"{0} plan{1}".format(
                supported, u"" if supported == 1 else u"s"))
        skipped = len(self.skipped_views)
        if skipped:
            parts.append(u"{0} skipped".format(skipped))
        if not parts:
            parts.append(u"no views")
        return u", ".join(parts)

    @property
    def status_text(self):
        if self.collision:
            return self.collision
        if self.is_placeholder:
            return u"Placeholder sheet - created without a title block"
        return u""

    @property
    def has_problem(self):
        return bool(self.collision)

    @property
    def search_blob(self):
        return u" ".join([
            self.source_number, self.source_name,
            self.new_number, self.new_name,
            u" ".join(row.view_name for row in self.view_rows),
        ]).lower()


# ---------------------------------------------------------------------------
# selection, filtering, numbering
# ---------------------------------------------------------------------------

def filter_rows(rows, text):
    """Case-insensitive substring match over a row's user-visible fields."""
    needle = safe_text(text).strip().lower()
    if not needle:
        return list(rows or [])
    return [row for row in (rows or []) if needle in row.search_blob]


def apply_check_to_rows(rows, checked):
    for row in rows or []:
        row.is_checked = bool(checked)


def checked_rows(rows):
    return [row for row in (rows or []) if row.is_checked]


def apply_number_affixes(rows, prefix, suffix):
    """Rebuild every row's new number from its source number plus affixes.

    Rebuilt rather than appended so that clearing the boxes undoes it and
    re-typing does not stack a second prefix on the first.
    """
    prefix = safe_text(prefix)
    suffix = safe_text(suffix)
    for row in rows or []:
        row.new_number = u"{0}{1}{2}".format(prefix, row.source_number, suffix)


def detect_collisions(rows, existing_numbers):
    """Flag every checked row whose number is already taken.

    Two sources of collision, and both are fatal because Revit refuses a
    duplicate sheet number: a sheet that already exists in this model, and
    another row in the same batch.  Returns the flagged rows.
    """
    existing = set(_norm(number) for number in (existing_numbers or []))
    seen = {}
    flagged = []

    for row in rows or []:
        row.collision = u""

    for row in checked_rows(rows):
        number = _norm(row.new_number)
        if not number:
            row.collision = u"Sheet number is empty"
            flagged.append(row)
            continue
        if number in existing:
            row.collision = u"This model already has sheet {0}".format(
                row.new_number)
            flagged.append(row)
            continue
        if number in seen:
            row.collision = u"Duplicated by {0} in this batch".format(
                seen[number].source_number)
            flagged.append(row)
            continue
        seen[number] = row
    return flagged


# ---------------------------------------------------------------------------
# level mapping
# ---------------------------------------------------------------------------

def resolve_level_map(linked_levels, host_levels, monitored_pairs):
    """Match each linked level to a host level, best evidence first.

    The ladder is Copy/Monitor, then an exact name match, then an elevation
    match within :data:`LEVEL_TOLERANCE_FT`.  Copy/Monitor comes first because
    it is the only one that records a human decision - the other two are
    guesses, however good, and the window lets the user overrule either.

    ``monitored_pairs`` maps linked level key -> host level key, already
    filtered by the Revit layer to the *selected* link instance: a host level
    monitoring the same level in a different instance of the same link says
    nothing about this one.  A linked level monitored by two host levels
    arrives as a list and is marked ambiguous rather than resolved.
    """
    choices = list(host_levels or [])
    by_key = dict((choice.key, choice) for choice in choices)
    by_name = {}
    for choice in choices:
        by_name.setdefault(_norm(choice.name), []).append(choice)

    rows = []
    for level in linked_levels or []:
        row = LevelMapRow(level, choices)

        monitored = (monitored_pairs or {}).get(level.key)
        if monitored is not None and not isinstance(monitored, (list, tuple)):
            monitored = [monitored]
        monitored = [key for key in (monitored or []) if key in by_key]

        if len(monitored) == 1:
            row.host_choice = by_key[monitored[0]]
            row.source = SOURCE_MONITORED
            rows.append(row)
            continue
        if len(monitored) > 1:
            # Two host levels monitoring one linked level: pick the first so
            # the run is not blocked, but say so - the user has to confirm it.
            row.host_choice = by_key[monitored[0]]
            row.source = SOURCE_MONITORED
            row.ambiguous = True
            rows.append(row)
            continue

        named = by_name.get(_norm(level.name)) or []
        if len(named) == 1:
            row.host_choice = named[0]
            row.source = SOURCE_NAME
            rows.append(row)
            continue

        nearest = None
        nearest_delta = None
        for choice in choices:
            delta = abs(choice.elevation - level.elevation_host)
            if nearest_delta is None or delta < nearest_delta:
                nearest = choice
                nearest_delta = delta
        if nearest is not None and nearest_delta <= LEVEL_TOLERANCE_FT:
            row.host_choice = nearest
            row.source = SOURCE_ELEVATION
        rows.append(row)
    return rows


def level_map_by_key(level_rows):
    """``{linked level key: host level key}`` for every mapped row."""
    mapping = {}
    for row in level_rows or []:
        if row.is_mapped:
            mapping[row.linked_key] = row.host_key
    return mapping


def all_level_keys(sheet_rows):
    """Linked level keys any listed sheet could need.

    The mapping grid is built from this rather than from the ticked sheets, so
    it does not reshuffle every time a checkbox moves.
    """
    keys = []
    seen = set()
    for sheet in sheet_rows or []:
        for view in sheet.supported_views:
            if view.level_key is None or view.level_key in seen:
                continue
            seen.add(view.level_key)
            keys.append(view.level_key)
    return keys


def required_level_keys(sheet_rows):
    """Linked level keys the checked sheets actually need."""
    keys = []
    seen = set()
    for sheet in checked_rows(sheet_rows):
        for view in sheet.supported_views:
            if view.level_key is None or view.level_key in seen:
                continue
            seen.add(view.level_key)
            keys.append(view.level_key)
    return keys


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------

class HostFacts(object):
    """What the host model already has, so the plan can be built off Revit."""

    def __init__(self, sheet_numbers=None, view_names=None,
                 title_block_types=None, view_family_types=None,
                 viewport_types=None, area_schemes=None, view_templates=None,
                 scope_boxes=None, levels=None):
        self.sheet_numbers = set(_norm(item) for item in (sheet_numbers or []))
        self.view_names = set(safe_text(item) for item in (view_names or []))
        self.title_block_types = set(
            _norm(item) for item in (title_block_types or []))
        self.view_family_types = set(
            _norm(item) for item in (view_family_types or []))
        self.viewport_types = set(
            _norm(item) for item in (viewport_types or []))
        self.area_schemes = set(_norm(item) for item in (area_schemes or []))
        self.view_templates = set(
            _norm(item) for item in (view_templates or []))
        self.scope_boxes = set(_norm(item) for item in (scope_boxes or []))
        self.levels = list(levels or [])

    def has_view_family(self, view_family):
        return _norm(view_family) in self.view_family_types


class ViewPlanItem(object):
    def __init__(self, view_row, action, reason=u"", target_name=u"",
                 level_row=None, notes=None):
        self.view_row = view_row
        self.action = action
        self.reason = safe_text(reason)
        self.target_name = safe_text(target_name)
        self.level_row = level_row
        self.notes = list(notes or [])

    @property
    def source_name(self):
        return self.view_row.view_name


class SheetPlanItem(object):
    def __init__(self, sheet_row, action, reason=u"", number=u"", name=u"",
                 views=None, notes=None):
        self.sheet_row = sheet_row
        self.action = action
        self.reason = safe_text(reason)
        self.number = safe_text(number)
        self.name = safe_text(name)
        self.views = list(views or [])
        self.notes = list(notes or [])

    @property
    def views_to_create(self):
        return [view for view in self.views if view.action == ACTION_CREATE]


class CopyPlan(object):
    def __init__(self, options, link_label=u""):
        self.options = dict(options or {})
        self.link_label = safe_text(link_label)
        self.sheets = []
        self.level_rows = []
        self.model_changes = []
        self.warnings = []

    @property
    def sheets_to_create(self):
        return [sheet for sheet in self.sheets if sheet.action == ACTION_CREATE]

    @property
    def views_to_create(self):
        total = []
        for sheet in self.sheets_to_create:
            total.extend(sheet.views_to_create)
        return total

    def has_work(self):
        return bool(self.sheets_to_create)


def build_plan(sheet_rows, level_rows, options, host_facts, link_label=u""):
    """The complete dry run.  The confirmation and the executor both read it.

    Everything the run refuses to do is decided here, once, so the preview
    cannot promise work the writer will not attempt.
    """
    options = dict(options or {})
    facts = host_facts or HostFacts()
    plan = CopyPlan(options, link_label)
    plan.level_rows = list(level_rows or [])

    level_by_key = {}
    for row in plan.level_rows:
        level_by_key[row.linked_key] = row

    taken_numbers = set(facts.sheet_numbers)
    taken_view_names = set(facts.view_names)
    missing_title_blocks = set()
    missing_viewport_types = set()

    for sheet_row in checked_rows(sheet_rows):
        number = safe_text(sheet_row.new_number).strip()
        name = safe_text(sheet_row.new_name).strip()
        notes = []

        if not number:
            plan.sheets.append(SheetPlanItem(
                sheet_row, ACTION_SKIP, u"Sheet number is empty"))
            continue
        if _norm(number) in taken_numbers:
            plan.sheets.append(SheetPlanItem(
                sheet_row, ACTION_SKIP,
                u"Sheet number {0} is already taken".format(number)))
            continue
        taken_numbers.add(_norm(number))

        if sheet_row.is_placeholder:
            notes.append(u"Placeholder sheet: no title block and no views.")
        elif options.get(OPT_TITLE_BLOCK):
            label = sheet_row.title_block_label
            if not label:
                notes.append(u"The linked sheet has no title block.")
            elif _norm(label) not in facts.title_block_types:
                missing_title_blocks.add(label)
                notes.append(
                    u"Title block '{0}' will be copied from the "
                    u"link.".format(label))
            if sheet_row.title_block_count > 1:
                notes.append(
                    u"{0} title blocks on the linked sheet; the first sets "
                    u"the sheet origin.".format(sheet_row.title_block_count))

        views = []
        for view_row in sheet_row.view_rows:
            views.append(_plan_one_view(
                view_row, level_by_key, options, facts, taken_view_names,
                missing_viewport_types))

        plan.sheets.append(SheetPlanItem(
            sheet_row, ACTION_CREATE, u"", number, name, views, notes))

    _collect_model_changes(plan, options, missing_title_blocks,
                           missing_viewport_types)
    _collect_warnings(plan, options)
    return plan


def _plan_one_view(view_row, level_by_key, options, facts, taken_view_names,
                   missing_viewport_types):
    if not view_row.supported:
        return ViewPlanItem(view_row, ACTION_SKIP,
                            view_row.skip_reason or u"Unsupported view type")

    if view_row.scale is None or view_row.scale <= 0:
        return ViewPlanItem(view_row, ACTION_SKIP,
                            u"The linked view has no usable scale")

    if not facts.has_view_family(view_row.view_family):
        return ViewPlanItem(
            view_row, ACTION_SKIP,
            u"This model has no view type for {0}".format(
                view_row.view_family or u"that view family"))

    if view_row.area_scheme_name and (
            _norm(view_row.area_scheme_name) not in facts.area_schemes):
        return ViewPlanItem(
            view_row, ACTION_SKIP,
            u"This model has no area scheme named '{0}'".format(
                view_row.area_scheme_name))

    if view_row.level_key is None:
        return ViewPlanItem(view_row, ACTION_SKIP,
                            u"The linked view is not associated with a level")

    level_row = level_by_key.get(view_row.level_key)
    if level_row is None or not level_row.is_mapped:
        linked_name = level_row.linked_name if level_row else u"?"
        return ViewPlanItem(
            view_row, ACTION_SKIP,
            u"Level '{0}' is not mapped to a level in this model".format(
                linked_name))

    notes = []
    if not view_row.crop_active:
        notes.append(u"Crop is off in the linked view; it is switched on in "
                     u"the new view so the viewport keeps the printed size.")
    if options.get(OPT_VIEW_TEMPLATE) and view_row.template_name:
        if _norm(view_row.template_name) in facts.view_templates:
            notes.append(u"View template '{0}' applied.".format(
                view_row.template_name))
        else:
            notes.append(u"No view template named '{0}' in this model.".format(
                view_row.template_name))
    if options.get(OPT_SCOPE_BOX) and view_row.scope_box_name:
        if _norm(view_row.scope_box_name) in facts.scope_boxes:
            notes.append(u"Scope box '{0}' assigned.".format(
                view_row.scope_box_name))
        else:
            notes.append(u"No scope box named '{0}' in this model.".format(
                view_row.scope_box_name))
    if level_row.ambiguous:
        notes.append(u"Two levels in this model monitor '{0}'.".format(
            level_row.linked_name))

    target_name = unique_name(view_row.view_name, taken_view_names)
    taken_view_names.add(target_name)
    if target_name != view_row.view_name:
        notes.append(u"Renamed to '{0}'; this model already has a view called "
                     u"'{1}'.".format(target_name, view_row.view_name))

    return ViewPlanItem(view_row, ACTION_CREATE, u"", target_name, level_row,
                        notes)


def _collect_model_changes(plan, options, title_blocks, viewport_types):
    """Everything the run adds to the host model, named before it happens."""
    changes = plan.model_changes
    for label in sorted(title_blocks):
        changes.append(u"Title block family/type copied from the link: "
                       u"{0}".format(label))
    if options.get(OPT_VIEWPORT_TYPE):
        for label in sorted(viewport_types):
            changes.append(u"Viewport type copied from the link: "
                           u"{0}".format(label))
    if options.get(OPT_SHEET_DETAILING):
        changes.append(u"Sheet detailing is copied: text, line and filled "
                       u"region types the link uses will be created here if "
                       u"they are missing.")
    if options.get(OPT_VIEW_ANNOTATIONS):
        changes.append(u"View annotations are copied: the same applies to "
                       u"their types.")
    if options.get(OPT_REVISION_CLOUDS):
        changes.append(u"Revision clouds are copied, which adds their "
                       u"revisions to the new sheets.")


def _collect_warnings(plan, options):
    warnings = plan.warnings
    if options.get(OPT_VIEW_ANNOTATIONS):
        warnings.append(
            u"The link is shown By Linked View, so the linked view's text, "
            u"detail lines and filled regions already draw through it. "
            u"Copying them draws everything twice until you switch that link "
            u"off in the new view.")
    if not options.get(OPT_HIDE_OTHER_LINKS):
        warnings.append(
            u"Other RVT links in this model draw By Host View in the new "
            u"views, which the linked sheet never showed.")
    warnings.append(
        u"The new views also show this model's own geometry, which the "
        u"linked sheet did not. That is the point of reusing the sheet.")
    warnings.append(
        u"Crops are written in this model's coordinates. If the link is "
        u"moved afterwards the new views do not follow it.")
    warnings.append(
        u"Sheets inside a nested link are not reachable and are not listed.")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

class RunSummary(object):
    def __init__(self):
        self.sheets_created = 0
        self.views_created = 0
        self.viewports_placed = 0
        self.elements_copied = 0
        self.cancelled = False
        self.first_sheet_key = None
        self.rows = []
        self.errors = []

    def add_row(self, sheet_number, item, status, detail=u"",
                deviation_mm=None):
        self.rows.append({
            "sheet": safe_text(sheet_number),
            "item": safe_text(item),
            "status": safe_text(status),
            "detail": safe_text(detail),
            "deviation": deviation_mm,
        })

    def add_error(self, sheet_number, item, detail):
        self.errors.append({
            "sheet": safe_text(sheet_number),
            "item": safe_text(item),
            "detail": safe_text(detail),
        })

    def snapshot(self):
        """Freeze the counters so one sheet's rollback can be undone here too.

        A sheet is written inside its own transaction group; when that group
        rolls back, everything counted while building it is gone from the
        model and must go from the report as well.
        """
        return (self.sheets_created, self.views_created,
                self.viewports_placed, self.elements_copied, len(self.rows),
                self.first_sheet_key)

    def restore(self, snapshot):
        (self.sheets_created, self.views_created, self.viewports_placed,
         self.elements_copied, row_count, self.first_sheet_key) = snapshot
        del self.rows[row_count:]

    def clear_applied(self):
        """Zero the counters after a rollback.

        A report that claims work which no longer exists in the model is
        worse than no report at all.
        """
        self.sheets_created = 0
        self.views_created = 0
        self.viewports_placed = 0
        self.elements_copied = 0
        self.first_sheet_key = None

    @property
    def worst_deviation(self):
        values = [row["deviation"] for row in self.rows
                  if row["deviation"] is not None]
        return max(values) if values else None


def build_confirm_headline(plan):
    sheets = len(plan.sheets_to_create)
    views = len(plan.views_to_create)
    return u"Create {0} sheet{1} and {2} view{3} from {4}?".format(
        sheets, u"" if sheets == 1 else u"s",
        views, u"" if views == 1 else u"s",
        plan.link_label or u"the link")


def build_confirm_text(plan):
    lines = []
    lines.append(u"LINK")
    lines.append(u"    {0}".format(plan.link_label or u"(unnamed)"))
    lines.append(u"")

    lines.append(u"LEVELS")
    used = set()
    for sheet in plan.sheets_to_create:
        for view in sheet.views_to_create:
            if view.level_row is not None:
                used.add(view.level_row.linked_key)
    shown = [row for row in plan.level_rows if row.linked_key in used]
    if not shown:
        lines.append(u"    (none needed)")
    for row in shown:
        lines.append(u"    {0}  ->  {1}   [{2}]{3}".format(
            row.linked_name,
            row.host_choice.name if row.host_choice else u"(unmapped)",
            row.source,
            u"  delta {0}".format(row.delta_text) if row.delta_text else u""))
    lines.append(u"")

    lines.append(u"SHEETS")
    for sheet in plan.sheets:
        if sheet.action == ACTION_SKIP:
            lines.append(u"    SKIP  {0} - {1}   ({2})".format(
                sheet.sheet_row.source_number, sheet.sheet_row.source_name,
                sheet.reason))
            continue
        lines.append(u"    {0} - {1}".format(sheet.number, sheet.name))
        for note in sheet.notes:
            lines.append(u"        note: {0}".format(note))
        for view in sheet.views:
            if view.action == ACTION_CREATE:
                lines.append(u"        create  {0}   on {1}".format(
                    view.target_name,
                    view.level_row.host_choice.name
                    if view.level_row and view.level_row.host_choice
                    else u"?"))
                for note in view.notes:
                    lines.append(u"            note: {0}".format(note))
            else:
                lines.append(u"        skip    {0}   ({1})".format(
                    view.source_name, view.reason))
    lines.append(u"")

    if plan.model_changes:
        lines.append(u"CHANGES TO THIS MODEL")
        for change in plan.model_changes:
            lines.append(u"    - {0}".format(change))
        lines.append(u"")

    if plan.warnings:
        lines.append(u"WORTH KNOWING")
        for warning in plan.warnings:
            lines.append(u"    - {0}".format(warning))
    return u"\n".join(lines)


def build_report_headline(summary):
    if summary.cancelled:
        return u"Cancelled - nothing was written."
    return u"Created {0} sheet(s), {1} view(s), {2} viewport(s).".format(
        summary.sheets_created, summary.views_created,
        summary.viewports_placed)


def build_report_text(summary):
    lines = []
    lines.append(u"Sheets created      {0}".format(summary.sheets_created))
    lines.append(u"Views created       {0}".format(summary.views_created))
    lines.append(u"Viewports placed    {0}".format(summary.viewports_placed))
    lines.append(u"Elements copied     {0}".format(summary.elements_copied))

    worst = summary.worst_deviation
    if worst is not None:
        lines.append(u"Worst alignment     {0:.3f} mm on the printed "
                     u"sheet".format(worst))
        if worst > ALIGNMENT_TOLERANCE_MM:
            lines.append(u"    Above {0} mm means a view did not reproduce "
                         u"the linked scale or rotation - see the rows "
                         u"below.".format(ALIGNMENT_TOLERANCE_MM))
    lines.append(u"")

    if summary.rows:
        lines.append(u"DETAIL")
        for row in summary.rows:
            deviation = u""
            if row["deviation"] is not None:
                deviation = u"   [{0:.3f} mm]".format(row["deviation"])
            lines.append(u"    {0:<10} {1:<40} {2}{3}".format(
                row["sheet"], row["item"], row["status"], deviation))
            if row["detail"]:
                lines.append(u"        {0}".format(row["detail"]))
        lines.append(u"")

    if summary.errors:
        lines.append(u"FAILED")
        for row in summary.errors:
            lines.append(u"    {0:<10} {1}".format(row["sheet"], row["item"]))
            lines.append(u"        {0}".format(row["detail"]))
    elif not summary.cancelled:
        lines.append(u"No failures.")
    return u"\n".join(lines)
