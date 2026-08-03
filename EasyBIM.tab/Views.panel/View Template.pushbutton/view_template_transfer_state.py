# -*- coding: utf-8 -*-
"""Model-independent state helpers for batch view-template transfer."""

ALL_VIEW_TYPES = "All View Types"


def safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def element_id_to_int(element_id):
    if element_id is None:
        return None
    try:
        return int(element_id)
    except Exception:
        pass
    for attr_name in ("IntegerValue", "Value"):
        try:
            return int(getattr(element_id, attr_name))
        except Exception:
            pass
    return None


def _unique_ints(values):
    seen = set()
    result = []
    for value in values or []:
        value_int = element_id_to_int(value)
        if value_int is None or value_int in seen:
            continue
        seen.add(value_int)
        result.append(value_int)
    return result


def _name_sort_key(parameter_id_int, names_by_id):
    name = safe_text((names_by_id or {}).get(parameter_id_int)).lower()
    return (name, int(parameter_id_int))


def order_parameter_ids(template_parameter_ids, ordered_parameter_ids, names_by_id=None):
    """Return template parameter ids in Revit order, with missing ids appended."""
    template_set = set(_unique_ints(template_parameter_ids))
    ordered = []
    used = set()

    for parameter_id_int in _unique_ints(ordered_parameter_ids):
        if parameter_id_int in template_set and parameter_id_int not in used:
            ordered.append(parameter_id_int)
            used.add(parameter_id_int)

    missing = sorted(template_set - used, key=lambda x: _name_sort_key(x, names_by_id))
    ordered.extend(missing)
    return ordered


class TemplateParameterRow(object):
    def __init__(
        self,
        parameter_id_int,
        name,
        value_text="",
        source_is_included=True,
        is_selected=None,
    ):
        self.parameter_id_int = int(parameter_id_int)
        self.name = safe_text(name)
        self.value_text = safe_text(value_text)
        self.source_is_included = bool(source_is_included)
        if is_selected is None:
            self.is_selected = bool(source_is_included)
        else:
            self.is_selected = bool(is_selected)


class ViewTemplateOption(object):
    def __init__(self, element_id_int, name, view_type_label, element=None, view_type_name=""):
        self.element_id_int = int(element_id_int)
        self.name = safe_text(name)
        self.view_type_label = safe_text(view_type_label) or "Other"
        self.view_type_name = safe_text(view_type_name)
        self.element = element
        self.is_selected = False

    @property
    def display(self):
        return self.name


class TargetTemplateGroup(object):
    def __init__(self, view_type_label, templates):
        self.view_type_label = safe_text(view_type_label) or "Other"
        self.display_name = "{} ({})".format(self.view_type_label, len(templates or []))
        self.templates = list(templates or [])
        self.is_expanded = True


def _template_sort_key(option):
    return (
        safe_text(getattr(option, "view_type_label", "")).lower(),
        safe_text(getattr(option, "name", "")).lower(),
        int(getattr(option, "element_id_int", 0) or 0),
    )


def get_view_type_filter_values(options):
    labels = sorted(
        set(
            safe_text(getattr(option, "view_type_label", "")) or "Other"
            for option in options or []
        ),
        key=lambda x: x.lower(),
    )
    return [ALL_VIEW_TYPES] + labels


def is_template_visible_for_filter(option, view_type_filter):
    view_type_filter = safe_text(view_type_filter) or ALL_VIEW_TYPES
    if view_type_filter == ALL_VIEW_TYPES:
        return True
    return safe_text(getattr(option, "view_type_label", "")) == view_type_filter


def group_target_templates_by_view_type(
    options,
    selected_source_id_int=None,
    view_type_filter=ALL_VIEW_TYPES,
):
    groups_by_label = {}
    for option in sorted(list(options or []), key=_template_sort_key):
        if selected_source_id_int is not None:
            if int(getattr(option, "element_id_int", 0)) == int(selected_source_id_int):
                continue
        if not is_template_visible_for_filter(option, view_type_filter):
            continue
        label = safe_text(getattr(option, "view_type_label", "")) or "Other"
        groups_by_label.setdefault(label, []).append(option)

    return [
        TargetTemplateGroup(label, groups_by_label[label])
        for label in sorted(groups_by_label.keys(), key=lambda x: x.lower())
    ]


def set_visible_template_selection(options, view_type_filter, is_selected, skip_locked=True):
    for option in options or []:
        if not is_template_visible_for_filter(option, view_type_filter):
            continue
        if is_selected and skip_locked and not bool(getattr(option, "is_checkable", True)):
            continue
        option.is_selected = bool(is_selected)


def get_selected_template_options(options):
    return [option for option in options or [] if bool(getattr(option, "is_selected", False))]


def get_selected_parameter_ids(rows):
    return [
        int(row.parameter_id_int)
        for row in rows or []
        if bool(getattr(row, "is_selected", False))
    ]


def calculate_temporary_non_controlled_ids(
    target_template_parameter_ids,
    selected_parameter_ids,
):
    target_set = set(_unique_ints(target_template_parameter_ids))
    selected_set = set(_unique_ints(selected_parameter_ids))
    return sorted(target_set.intersection(selected_set))


def calculate_final_non_controlled_ids(
    target_template_parameter_ids,
    original_target_non_controlled_ids,
    selected_parameter_ids,
    source_non_controlled_ids,
):
    target_set = set(_unique_ints(target_template_parameter_ids))
    selected_set = set(_unique_ints(selected_parameter_ids)).intersection(target_set)
    source_non_controlled_set = set(_unique_ints(source_non_controlled_ids))

    final_set = set(_unique_ints(original_target_non_controlled_ids)).intersection(target_set)
    for parameter_id_int in selected_set:
        if parameter_id_int in source_non_controlled_set:
            final_set.add(parameter_id_int)
        else:
            final_set.discard(parameter_id_int)

    return sorted(final_set)


# ---------------------------------------------------------------------------
# Source / target modes
# ---------------------------------------------------------------------------

SOURCE_KIND_VIEW = "view"
SOURCE_KIND_TEMPLATE = "template"
TARGET_KIND_VIEWS = "views"
TARGET_KIND_TEMPLATES = "templates"

LOCK_STATE_NORMAL = "normal"
LOCK_STATE_LOCKED = "locked"
LOCK_STATE_INCOMPATIBLE = "incompatible"

VIEW_TYPE_FAMILIES = {
    "FloorPlan": "plan",
    "EngineeringPlan": "plan",
    "AreaPlan": "plan",
    "CeilingPlan": "ceiling",
    "Section": "section",
    "Elevation": "section",
    "Detail": "section",
    "ThreeD": "three_d",
    "Walkthrough": "three_d",
    "DraftingView": "drafting",
    "Legend": "legend",
    "Schedule": "schedule",
    "PanelSchedule": "schedule",
    "ColumnSchedule": "schedule",
}

EXCLUDED_VIEW_TYPES = set(
    [
        "DrawingSheet",
        "ProjectBrowser",
        "SystemBrowser",
        "Internal",
        "Undefined",
        "Legend",
        "Schedule",
        "PanelSchedule",
        "ColumnSchedule",
        "Rendering",
        "Report",
        "CostReport",
        "LoadsReport",
        "PressureLossReport",
        "SystemsAnalysisReport",
    ]
)


def view_type_family(view_type_name):
    return VIEW_TYPE_FAMILIES.get(safe_text(view_type_name))


def is_compatible_view_types(source_view_type_name, target_view_type_name):
    source_family = view_type_family(source_view_type_name)
    target_family = view_type_family(target_view_type_name)
    if source_family is None or target_family is None:
        return True
    return source_family == target_family


def is_eligible_view_type(view_type_name):
    return safe_text(view_type_name) not in EXCLUDED_VIEW_TYPES


def is_view_locked_by_template(template_id_int):
    template_id_int = element_id_to_int(template_id_int)
    return template_id_int is not None and template_id_int > 0


def has_transfer_candidates(view_count, template_count):
    return (int(view_count or 0) + int(template_count or 0)) >= 2


class ViewOption(object):
    def __init__(
        self,
        element_id_int,
        name,
        view_type_name,
        view_type_label,
        element=None,
        template_id_int=None,
        template_name="",
        is_dependent=False,
    ):
        self.element_id_int = int(element_id_int)
        self.name = safe_text(name)
        self.view_type_name = safe_text(view_type_name)
        self.view_type_label = safe_text(view_type_label) or "Other"
        self.element = element
        normalized = element_id_to_int(template_id_int)
        if normalized is not None and normalized <= 0:
            normalized = None
        self.template_id_int = normalized
        self.template_name = safe_text(template_name)
        self.is_dependent = bool(is_dependent)
        self.is_selected = False

    @property
    def display(self):
        if self.is_dependent:
            return u"{} (dependent)".format(self.name)
        return self.name


class TargetRow(object):
    def __init__(
        self,
        element_id_int,
        name,
        view_type_label,
        element=None,
        lock_state=LOCK_STATE_NORMAL,
        tooltip_text="",
    ):
        self.element_id_int = int(element_id_int)
        self.name = safe_text(name)
        self.view_type_label = safe_text(view_type_label) or "Other"
        self.element = element
        self.lock_state = safe_text(lock_state) or LOCK_STATE_NORMAL
        self.tooltip_text = safe_text(tooltip_text)
        self.is_selected = False

    @property
    def is_checkable(self):
        return self.lock_state == LOCK_STATE_NORMAL


def build_target_rows(options, target_kind, source_view_type_name=None, source_id_int=None):
    """Wrap view/template options as target rows with lock/compatibility state."""
    rows = []
    for option in options or []:
        element_id_int = element_id_to_int(getattr(option, "element_id_int", None))
        if element_id_int is None:
            continue
        if source_id_int is not None and element_id_int == int(source_id_int):
            continue

        lock_state = LOCK_STATE_NORMAL
        tooltip_text = ""
        if target_kind == TARGET_KIND_VIEWS and is_view_locked_by_template(
            getattr(option, "template_id_int", None)
        ):
            lock_state = LOCK_STATE_LOCKED
            template_name = safe_text(getattr(option, "template_name", "")) or "a view template"
            tooltip_text = u"Controlled by view template '{}'. This tool does not modify template-controlled views.".format(
                template_name
            )
        elif source_view_type_name and not is_compatible_view_types(
            source_view_type_name, getattr(option, "view_type_name", "")
        ):
            lock_state = LOCK_STATE_INCOMPATIBLE
            tooltip_text = u"View type '{}' is not compatible with the source view type.".format(
                safe_text(getattr(option, "view_type_label", "")) or "Other"
            )

        rows.append(
            TargetRow(
                element_id_int,
                safe_text(getattr(option, "display", "")) or safe_text(getattr(option, "name", "")),
                getattr(option, "view_type_label", ""),
                element=getattr(option, "element", None),
                lock_state=lock_state,
                tooltip_text=tooltip_text,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# V/G Overrides groups (Selective Transfer)
# ---------------------------------------------------------------------------

GROUP_MODEL = "model"
GROUP_ANNOTATION = "annotation"
GROUP_ANALYTICAL = "analytical"
GROUP_IMPORT = "import"
GROUP_FILTERS = "filters"
GROUP_WORKSETS = "worksets"
GROUP_LINKS = "links"

VG_GROUP_KEYS = (
    GROUP_MODEL,
    GROUP_ANNOTATION,
    GROUP_ANALYTICAL,
    GROUP_IMPORT,
    GROUP_FILTERS,
    GROUP_WORKSETS,
    GROUP_LINKS,
)

VG_GROUP_TITLES = {
    GROUP_MODEL: "V/G Overrides Model",
    GROUP_ANNOTATION: "V/G Overrides Annotation",
    GROUP_ANALYTICAL: "V/G Overrides Analytical Model",
    GROUP_IMPORT: "V/G Overrides Import",
    GROUP_FILTERS: "V/G Overrides Filters",
    GROUP_WORKSETS: "V/G Overrides Worksets",
    GROUP_LINKS: "V/G Overrides RVT Links",
}

VG_PARAM_NAME_TO_GROUP = {
    "v/g overrides model": GROUP_MODEL,
    "v/g overrides annotation": GROUP_ANNOTATION,
    "v/g overrides analytical model": GROUP_ANALYTICAL,
    "v/g overrides import": GROUP_IMPORT,
    "v/g overrides filters": GROUP_FILTERS,
    "v/g overrides worksets": GROUP_WORKSETS,
    "v/g overrides rvt links": GROUP_LINKS,
}

# Column specs per group: (header, width, kind, row attribute).
# kind "check" renders a frozen checkbox; "text" renders greyed read-only text.
_CATEGORY_COLUMNS = (
    ("Visibility", 70, "check", "check0"),
    ("Projection Lines", 130, "text", "text0"),
    ("Projection Patterns", 150, "text", "text1"),
    ("Transparency", 95, "text", "text2"),
    ("Cut Lines", 120, "text", "text3"),
    ("Cut Patterns", 130, "text", "text4"),
    ("Halftone", 70, "check", "check1"),
    ("Detail Level", 95, "text", "text5"),
)

VG_GROUP_COLUMNS = {
    GROUP_MODEL: _CATEGORY_COLUMNS,
    GROUP_ANNOTATION: (
        ("Visibility", 70, "check", "check0"),
        ("Projection Lines", 130, "text", "text0"),
        ("Halftone", 70, "check", "check1"),
    ),
    GROUP_ANALYTICAL: _CATEGORY_COLUMNS,
    GROUP_IMPORT: (
        ("Visibility", 70, "check", "check0"),
        ("Projection Lines", 130, "text", "text0"),
        ("Projection Patterns", 150, "text", "text1"),
        ("Halftone", 70, "check", "check1"),
    ),
    GROUP_FILTERS: (
        ("Enable Filter", 85, "check", "check0"),
        ("Visibility", 70, "check", "check1"),
        ("Projection", 170, "text", "text0"),
        ("Cut", 170, "text", "text1"),
        ("Halftone", 70, "check", "check2"),
    ),
    GROUP_WORKSETS: (("Visibility Setting", 170, "text", "text0"),),
    GROUP_LINKS: (
        ("Display Setting", 130, "text", "text0"),
        ("Linked View", 180, "text", "text1"),
    ),
}

VG_GROUP_EMPTY_HINTS = {
    GROUP_FILTERS: "The source has no filters applied.",
    GROUP_WORKSETS: "This project is not workshared or has no user worksets.",
    GROUP_LINKS: "No Revit links in this project.",
}

VG_DEFAULT_EMPTY_HINT = "No items available for this group."

VG_LINKS_UNSUPPORTED_HINT = "RVT link overrides require Revit 2024 or newer."


def vg_group_for_parameter_name(name):
    return VG_PARAM_NAME_TO_GROUP.get(safe_text(name).strip().lower())


def vg_group_empty_hint(group_key, links_api_available=True):
    if group_key == GROUP_LINKS and not links_api_available:
        return VG_LINKS_UNSUPPORTED_HINT
    return VG_GROUP_EMPTY_HINTS.get(group_key, VG_DEFAULT_EMPTY_HINT)


class VgItemRow(object):
    """One row of a V/G edit dialog: frozen source values + transfer checkbox."""

    def __init__(self, item_key, name, values=None, parent_key=None, indent=0):
        self.item_key = int(item_key)
        self.parent_key = None if parent_key is None else int(parent_key)
        prefix = u" " * int(indent or 0)
        self.name = prefix + safe_text(name)
        self.is_checked = False
        self.check0 = False
        self.check1 = False
        self.check2 = False
        self.text0 = ""
        self.text1 = ""
        self.text2 = ""
        self.text3 = ""
        self.text4 = ""
        self.text5 = ""
        for attr, value in (values or {}).items():
            if attr.startswith("check"):
                setattr(self, attr, bool(value))
            elif attr.startswith("text"):
                setattr(self, attr, safe_text(value))


class ParameterSelectionRow(object):
    """One row of the Selective Transfer dialog. All rows start unchecked."""

    def __init__(self, parameter_id_int, name, group_key=None):
        self.parameter_id_int = int(parameter_id_int)
        self.name = safe_text(name)
        self.group_key = group_key
        self.is_selected = False
        self.items = None  # populated lazily for V/G group rows

    @property
    def has_edit(self):
        return self.group_key is not None

    @property
    def include_checked(self):
        if self.group_key is None:
            return bool(self.is_selected)
        return compute_group_include_state(self)


def build_parameter_selection_rows(id_name_pairs, group_key_by_id=None):
    group_key_by_id = dict(group_key_by_id or {})
    rows = []
    for parameter_id_int, name in id_name_pairs or []:
        group_key = vg_group_for_parameter_name(name)
        if group_key is None:
            # Display names are localized; BuiltInParameter ids are not.
            group_key = group_key_by_id.get(int(parameter_id_int))
        rows.append(
            ParameterSelectionRow(
                parameter_id_int,
                name,
                group_key=group_key,
            )
        )
    return rows


def compute_group_include_state(group_row):
    """True = all items checked, False = none (or no items), None = partial."""
    items = list(getattr(group_row, "items", None) or [])
    if not items:
        return False
    checked = 0
    for item in items:
        if bool(getattr(item, "is_checked", False)):
            checked += 1
    if checked == 0:
        return False
    if checked == len(items):
        return True
    return None


def apply_group_master_toggle(group_row, checked):
    for item in getattr(group_row, "items", None) or []:
        item.is_checked = bool(checked)


def apply_include_click(row, new_value):
    checked = bool(new_value)
    if getattr(row, "group_key", None) is None:
        row.is_selected = checked
    else:
        apply_group_master_toggle(row, checked)
    return checked


def resolve_include_click(row, checkbox_checked):
    """Effective value of an Include-column click.

    WPF collapses an indeterminate checkbox to unchecked before Click fires,
    so a partially-checked V/G group must resolve to select-all instead of
    silently clearing the user's per-item choices.
    """
    if getattr(row, "group_key", None) is not None:
        if compute_group_include_state(row) is None:
            return True
    return bool(checkbox_checked)


def snapshot_checked_keys(items):
    return set(
        int(item.item_key)
        for item in items or []
        if bool(getattr(item, "is_checked", False))
    )


def restore_checked_keys(items, checked_keys):
    checked_keys = set(int(x) for x in checked_keys or [])
    for item in items or []:
        item.is_checked = int(item.item_key) in checked_keys


class SelectiveState(object):
    """Selective Transfer selections, valid for exactly one source."""

    def __init__(self, source_kind, source_id_int, rows):
        self.source_kind = safe_text(source_kind)
        self.source_id_int = element_id_to_int(source_id_int)
        self.rows = list(rows or [])
        self.loaded_groups = set()

    def matches_source(self, source_kind, source_id_int):
        return (
            self.source_kind == safe_text(source_kind)
            and self.source_id_int == element_id_to_int(source_id_int)
        )


class TransferPlan(object):
    def __init__(
        self,
        transfer_all=False,
        full_parameter_ids=None,
        partial_groups=None,
        partial_group_parameter_ids=None,
    ):
        self.transfer_all = bool(transfer_all)
        self.full_parameter_ids = [int(x) for x in (full_parameter_ids or [])]
        self.partial_groups = dict(partial_groups or {})
        self.partial_group_parameter_ids = [
            int(x) for x in (partial_group_parameter_ids or [])
        ]

    @property
    def setting_count(self):
        count = len(self.full_parameter_ids)
        for item_keys in self.partial_groups.values():
            count += len(item_keys or [])
        return count


def build_transfer_plan(rows):
    """Turn Selective Transfer rows into a plan.

    Fully-checked V/G groups ride the native parameter mechanism; partial
    groups are excluded from it and transferred item-by-item instead.
    """
    full_ids = []
    partial_groups = {}
    partial_param_ids = []
    for row in rows or []:
        group_key = getattr(row, "group_key", None)
        if group_key is None:
            if bool(getattr(row, "is_selected", False)):
                full_ids.append(int(row.parameter_id_int))
            continue
        group_state = compute_group_include_state(row)
        if group_state is True:
            full_ids.append(int(row.parameter_id_int))
        elif group_state is None:
            partial_groups[group_key] = [
                int(item.item_key)
                for item in row.items
                if bool(getattr(item, "is_checked", False))
            ]
            partial_param_ids.append(int(row.parameter_id_int))
    return TransferPlan(
        transfer_all=False,
        full_parameter_ids=full_ids,
        partial_groups=partial_groups,
        partial_group_parameter_ids=partial_param_ids,
    )


def plan_is_empty(plan):
    if plan is None:
        return True
    if bool(getattr(plan, "transfer_all", False)):
        return False
    return not plan.full_parameter_ids and not plan.partial_groups


def adjust_final_ids_for_partial_groups(final_non_controlled_ids, partial_group_parameter_ids):
    """Force partially-transferred V/G groups to be controlled on the target.

    Their transferred items would otherwise be inert on views assigned to the
    target template.
    """
    return sorted(
        set(_unique_ints(final_non_controlled_ids))
        - set(_unique_ints(partial_group_parameter_ids))
    )
