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
    def __init__(self, element_id_int, name, view_type_label, element=None):
        self.element_id_int = int(element_id_int)
        self.name = safe_text(name)
        self.view_type_label = safe_text(view_type_label) or "Other"
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


def set_visible_template_selection(options, view_type_filter, is_selected):
    for option in options or []:
        if is_template_visible_for_filter(option, view_type_filter):
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
