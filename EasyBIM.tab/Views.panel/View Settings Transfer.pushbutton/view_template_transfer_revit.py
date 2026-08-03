# -*- coding: utf-8 -*-
"""Revit API helpers for view / view template setting transfer."""

from pyrevit import DB
from pyrevit.compat import get_elementid_value_func
from System.Collections.Generic import List

from view_template_transfer_state import GROUP_ANALYTICAL
from view_template_transfer_state import GROUP_ANNOTATION
from view_template_transfer_state import GROUP_FILTERS
from view_template_transfer_state import GROUP_IMPORT
from view_template_transfer_state import GROUP_LINKS
from view_template_transfer_state import GROUP_MODEL
from view_template_transfer_state import GROUP_WORKSETS
from view_template_transfer_state import SOURCE_KIND_VIEW
from view_template_transfer_state import TARGET_KIND_TEMPLATES
from view_template_transfer_state import TARGET_KIND_VIEWS
from view_template_transfer_state import ViewOption
from view_template_transfer_state import ViewTemplateOption
from view_template_transfer_state import VgItemRow
from view_template_transfer_state import adjust_final_ids_for_partial_groups
from view_template_transfer_state import build_parameter_selection_rows
from view_template_transfer_state import calculate_final_non_controlled_ids
from view_template_transfer_state import calculate_temporary_non_controlled_ids
from view_template_transfer_state import element_id_to_int
from view_template_transfer_state import is_eligible_view_type
from view_template_transfer_state import is_view_locked_by_template
from view_template_transfer_state import order_parameter_ids
from view_template_transfer_state import plan_is_empty
from view_template_transfer_state import safe_text


get_elementid_value = get_elementid_value_func()

_CATEGORY_GROUPS = (GROUP_MODEL, GROUP_ANNOTATION, GROUP_ANALYTICAL, GROUP_IMPORT)


class SourceNotSupportedError(Exception):
    """Raised when a view cannot act as a transfer source."""


def _eid_int(element_id):
    if element_id is None:
        return None
    try:
        return int(get_elementid_value(element_id))
    except Exception:
        return element_id_to_int(element_id)


def _int_to_element_id(id_int):
    try:
        return DB.ElementId(int(id_int))
    except Exception:
        # Revit 2026 removed ElementId(Int32); retry with Int64.
        import System
        return DB.ElementId(System.Int64(int(id_int)))


def _to_element_ids(id_ints):
    ids = List[DB.ElementId]()
    for id_int in id_ints or []:
        try:
            ids.Add(_int_to_element_id(id_int))
        except Exception:
            pass
    return ids


def _view_type_label(view):
    value = safe_text(getattr(view, "ViewType", "Other"))
    labels = {
        "FloorPlan": "Floor Plan",
        "CeilingPlan": "Ceiling Plan",
        "EngineeringPlan": "Engineering Plan",
        "AreaPlan": "Area Plan",
        "ThreeD": "3D View",
        "Walkthrough": "Walkthrough",
        "DraftingView": "Drafting View",
        "DrawingSheet": "Sheet",
        "Schedule": "Schedule",
        "PanelSchedule": "Panel Schedule",
        "Section": "Section",
        "Elevation": "Elevation",
        "Detail": "Detail",
        "Legend": "Legend",
    }
    return labels.get(value, value or "Other")


def _option_sort_key(option):
    return (
        safe_text(option.view_type_label).lower(),
        safe_text(option.name).lower(),
        int(option.element_id_int),
    )


def _element_name(doc, element_id):
    try:
        if _eid_int(element_id) in (None, -1):
            return ""
        element = doc.GetElement(element_id)
        return safe_text(getattr(element, "Name", ""))
    except Exception:
        return ""


def collect_view_templates(doc):
    templates = []
    try:
        views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()
    except Exception:
        views = []

    for view in views:
        try:
            if not bool(view.IsTemplate):
                continue
        except Exception:
            continue

        element_id_int = _eid_int(getattr(view, "Id", None))
        if element_id_int is None:
            continue

        templates.append(
            ViewTemplateOption(
                element_id_int,
                safe_text(getattr(view, "Name", "")),
                _view_type_label(view),
                element=view,
                view_type_name=safe_text(getattr(view, "ViewType", "")),
            )
        )

    return sorted(templates, key=_option_sort_key)


def collect_eligible_views(doc):
    options = []
    try:
        views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()
    except Exception:
        views = []

    for view in views:
        try:
            if bool(view.IsTemplate):
                continue
        except Exception:
            continue

        view_type_name = safe_text(getattr(view, "ViewType", ""))
        if not is_eligible_view_type(view_type_name):
            continue

        element_id_int = _eid_int(getattr(view, "Id", None))
        if element_id_int is None:
            continue

        template_id_int = None
        template_name = ""
        try:
            template_id = view.ViewTemplateId
            template_id_int = _eid_int(template_id)
            if template_id_int is not None and template_id_int > 0:
                template_name = _element_name(doc, template_id)
            else:
                template_id_int = None
        except Exception:
            template_id_int = None

        is_dependent = False
        try:
            primary_id_int = _eid_int(view.GetPrimaryViewId())
            is_dependent = primary_id_int is not None and primary_id_int > 0
        except Exception:
            pass

        options.append(
            ViewOption(
                element_id_int,
                safe_text(getattr(view, "Name", "")),
                view_type_name,
                _view_type_label(view),
                element=view,
                template_id_int=template_id_int,
                template_name=template_name,
                is_dependent=is_dependent,
            )
        )

    return sorted(options, key=_option_sort_key)


def get_active_view_id_int(doc):
    try:
        return _eid_int(getattr(doc.ActiveView, "Id", None))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Template parameter listing
# ---------------------------------------------------------------------------

def _template_parameter_ids(template):
    try:
        return [_eid_int(element_id) for element_id in template.GetTemplateParameterIds()]
    except Exception:
        return []


def _non_controlled_parameter_ids(template):
    try:
        return [
            _eid_int(element_id)
            for element_id in template.GetNonControlledTemplateParameterIds()
        ]
    except Exception:
        return []


def _parameter_name(parameter):
    try:
        return safe_text(parameter.Definition.Name)
    except Exception:
        return ""


def _ordered_parameters(template):
    try:
        parameters = list(template.GetOrderedParameters())
    except Exception:
        try:
            parameters = list(template.Parameters)
        except Exception:
            parameters = []
    return parameters


def _ordered_id_name_pairs(template):
    template_parameter_ids = [
        x for x in _template_parameter_ids(template) if x is not None
    ]

    names_by_id = {}
    ordered_ids = []
    for parameter in _ordered_parameters(template):
        parameter_id_int = _eid_int(getattr(parameter, "Id", None))
        if parameter_id_int is None:
            continue
        ordered_ids.append(parameter_id_int)
        names_by_id[parameter_id_int] = _parameter_name(parameter)

    ordered_template_ids = order_parameter_ids(
        template_parameter_ids,
        ordered_ids,
        names_by_id=names_by_id,
    )
    return [(pid, names_by_id.get(pid, "")) for pid in ordered_template_ids]


# BuiltInParameter members identify the V/G groups regardless of the Revit
# UI language; display-name matching alone fails on localized installs.
_VG_BUILTIN_GROUP_MEMBERS = (
    ("VIS_GRAPHICS_MODEL", GROUP_MODEL),
    ("VIS_GRAPHICS_ANNOTATION", GROUP_ANNOTATION),
    ("VIS_GRAPHICS_ANALYTICAL_MODEL", GROUP_ANALYTICAL),
    ("VIS_GRAPHICS_IMPORT", GROUP_IMPORT),
    ("VIS_GRAPHICS_FILTERS", GROUP_FILTERS),
    ("VIS_GRAPHICS_WORKSETS", GROUP_WORKSETS),
    ("VIS_GRAPHICS_RVT_LINKS", GROUP_LINKS),
)


def _vg_builtin_group_ids():
    mapping = {}
    for member_name, group_key in _VG_BUILTIN_GROUP_MEMBERS:
        member = getattr(DB.BuiltInParameter, member_name, None)
        if member is None:
            continue
        try:
            mapping[int(member)] = group_key
        except Exception:
            pass
    return mapping


def build_selection_rows_for_template(source_template):
    return build_parameter_selection_rows(
        _ordered_id_name_pairs(source_template),
        group_key_by_id=_vg_builtin_group_ids(),
    )


def build_selection_rows_for_view(doc, source_view):
    """List the template parameters a view would define.

    Creates a temporary view template inside a transaction that is rolled
    back after reading, so the document is never modified.
    """
    transaction = DB.Transaction(doc, "View Template - Read View Parameters")
    transaction.Start()
    try:
        created = source_view.CreateViewTemplate()
        if isinstance(created, DB.ElementId):
            created = doc.GetElement(created)
        if created is None:
            raise Exception("temporary view template unavailable")
        pairs = _ordered_id_name_pairs(created)
    except Exception as ex:
        try:
            if transaction.HasStarted():
                transaction.RollBack()
        except Exception:
            pass
        raise SourceNotSupportedError(safe_text(ex))
    transaction.RollBack()
    return build_parameter_selection_rows(
        pairs, group_key_by_id=_vg_builtin_group_ids()
    )


# ---------------------------------------------------------------------------
# V/G group snapshot readers
# ---------------------------------------------------------------------------

def _color_text(color):
    try:
        if color is not None and color.IsValid:
            return "RGB {}-{}-{}".format(color.Red, color.Green, color.Blue)
    except Exception:
        pass
    return ""


def _line_summary(doc, weight, color, pattern_id):
    parts = []
    try:
        if weight is not None and int(weight) > 0:
            parts.append("Weight {}".format(int(weight)))
    except Exception:
        pass
    color_text = _color_text(color)
    if color_text:
        parts.append(color_text)
    pattern_name = _element_name(doc, pattern_id)
    if pattern_name:
        parts.append(pattern_name)
    return ", ".join(parts)


def _pattern_summary(doc, pattern_id, color, visible=True):
    parts = []
    if not visible:
        parts.append("Hidden")
    pattern_name = _element_name(doc, pattern_id)
    if pattern_name:
        parts.append(pattern_name)
    color_text = _color_text(color)
    if color_text:
        parts.append(color_text)
    return ", ".join(parts)


def _ogs_attr(ogs, attr_name, default=None):
    try:
        return getattr(ogs, attr_name)
    except Exception:
        return default


def _detail_level_text(ogs):
    value = safe_text(_ogs_attr(ogs, "DetailLevel"))
    if not value or value == "Undefined":
        return "By View"
    return value


def _transparency_text(ogs):
    try:
        transparency = int(_ogs_attr(ogs, "Transparency", 0) or 0)
        if transparency > 0:
            return "{}%".format(transparency)
    except Exception:
        pass
    return ""


def _projection_lines_text(doc, ogs):
    return _line_summary(
        doc,
        _ogs_attr(ogs, "ProjectionLineWeight"),
        _ogs_attr(ogs, "ProjectionLineColor"),
        _ogs_attr(ogs, "ProjectionLinePatternId"),
    )


def _projection_patterns_text(doc, ogs):
    visible = _ogs_attr(ogs, "IsSurfaceForegroundPatternVisible", True)
    return _pattern_summary(
        doc,
        _ogs_attr(ogs, "SurfaceForegroundPatternId"),
        _ogs_attr(ogs, "SurfaceForegroundPatternColor"),
        visible=visible is not False,
    )


def _cut_lines_text(doc, ogs):
    return _line_summary(
        doc,
        _ogs_attr(ogs, "CutLineWeight"),
        _ogs_attr(ogs, "CutLineColor"),
        _ogs_attr(ogs, "CutLinePatternId"),
    )


def _cut_patterns_text(doc, ogs):
    visible = _ogs_attr(ogs, "IsCutForegroundPatternVisible", True)
    return _pattern_summary(
        doc,
        _ogs_attr(ogs, "CutForegroundPatternId"),
        _ogs_attr(ogs, "CutForegroundPatternColor"),
        visible=visible is not False,
    )


def _category_values(doc, group_key, hidden, ogs):
    values = {"check0": not hidden}
    if ogs is None:
        return values
    values["text0"] = _projection_lines_text(doc, ogs)
    halftone = bool(_ogs_attr(ogs, "Halftone", False))
    if group_key == GROUP_ANNOTATION:
        values["check1"] = halftone
        return values
    values["text1"] = _projection_patterns_text(doc, ogs)
    values["check1"] = halftone
    if group_key == GROUP_IMPORT:
        return values
    values["text2"] = _transparency_text(ogs)
    values["text3"] = _cut_lines_text(doc, ogs)
    values["text4"] = _cut_patterns_text(doc, ogs)
    values["text5"] = _detail_level_text(ogs)
    return values


def _category_item(doc, source_view, category, group_key, parent_key=None, indent=0):
    category_id = getattr(category, "Id", None)
    item_key = _eid_int(category_id)
    if item_key is None:
        return None, None

    try:
        if not bool(source_view.CanCategoryBeHidden(category_id)):
            return None, None
    except Exception:
        pass

    try:
        hidden = bool(source_view.GetCategoryHidden(category_id))
    except Exception:
        return None, None

    try:
        ogs = source_view.GetCategoryOverrides(category_id)
    except Exception:
        ogs = None

    item = VgItemRow(
        item_key,
        safe_text(getattr(category, "Name", "")),
        values=_category_values(doc, group_key, hidden, ogs),
        parent_key=parent_key,
        indent=indent,
    )
    return item, {"hidden": hidden, "ogs": ogs}


def _subcategories(category):
    try:
        return sorted(
            list(category.SubCategories),
            key=lambda sub: safe_text(getattr(sub, "Name", "")).lower(),
        )
    except Exception:
        return []


def _import_parent_categories(doc):
    parents = []
    try:
        import_parent = DB.Category.GetCategory(
            doc, DB.BuiltInCategory.OST_ImportObjectStyles
        )
        if import_parent is not None:
            parents.append(import_parent)
    except Exception:
        pass
    return parents


def read_category_group(doc, source_view, group_key):
    items = []
    payloads = {}

    if group_key == GROUP_IMPORT:
        parents = _import_parent_categories(doc)
    else:
        category_types = {
            GROUP_MODEL: DB.CategoryType.Model,
            GROUP_ANNOTATION: DB.CategoryType.Annotation,
            GROUP_ANALYTICAL: DB.CategoryType.AnalyticalModel,
        }
        wanted_type = category_types.get(group_key)
        parents = []
        try:
            for category in doc.Settings.Categories:
                try:
                    if category.CategoryType == wanted_type:
                        parents.append(category)
                except Exception:
                    continue
        except Exception:
            parents = []
        parents.sort(key=lambda cat: safe_text(getattr(cat, "Name", "")).lower())

    for parent in parents:
        parent_item, parent_payload = _category_item(
            doc, source_view, parent, group_key
        )
        if parent_item is None:
            continue
        items.append(parent_item)
        payloads[parent_item.item_key] = parent_payload
        for subcategory in _subcategories(parent):
            sub_item, sub_payload = _category_item(
                doc,
                source_view,
                subcategory,
                group_key,
                parent_key=parent_item.item_key,
                indent=1,
            )
            if sub_item is None:
                continue
            items.append(sub_item)
            payloads[sub_item.item_key] = sub_payload

    return items, payloads


def read_filters_group(doc, source_view):
    items = []
    payloads = {}
    try:
        filter_ids = list(source_view.GetFilters())
    except Exception:
        filter_ids = []

    for filter_id in filter_ids:
        item_key = _eid_int(filter_id)
        if item_key is None:
            continue

        enabled = True
        try:
            enabled = bool(source_view.GetIsFilterEnabled(filter_id))
        except Exception:
            pass
        visible = True
        try:
            visible = bool(source_view.GetFilterVisibility(filter_id))
        except Exception:
            pass
        try:
            overrides = source_view.GetFilterOverrides(filter_id)
        except Exception:
            overrides = None

        projection_parts = []
        cut_parts = []
        halftone = False
        if overrides is not None:
            projection_parts = [
                _projection_lines_text(doc, overrides),
                _projection_patterns_text(doc, overrides),
            ]
            cut_parts = [
                _cut_lines_text(doc, overrides),
                _cut_patterns_text(doc, overrides),
            ]
            halftone = bool(_ogs_attr(overrides, "Halftone", False))

        items.append(
            VgItemRow(
                item_key,
                _element_name(doc, filter_id) or "Filter {}".format(item_key),
                values={
                    "check0": enabled,
                    "check1": visible,
                    "text0": "; ".join(x for x in projection_parts if x),
                    "text1": "; ".join(x for x in cut_parts if x),
                    "check2": halftone,
                },
            )
        )
        payloads[item_key] = {
            "overrides": overrides,
            "visible": visible,
            "enabled": enabled,
        }

    return items, payloads


_WORKSET_VISIBILITY_LABELS = {
    "Visible": "Show",
    "Hidden": "Hide",
    "UseGlobalSetting": "Use Global Setting",
}


def read_worksets_group(doc, source_view):
    items = []
    payloads = {}
    try:
        if not bool(doc.IsWorkshared):
            return items, payloads
    except Exception:
        return items, payloads

    try:
        worksets = list(
            DB.FilteredWorksetCollector(doc).OfKind(DB.WorksetKind.UserWorkset)
        )
    except Exception:
        worksets = []
    worksets.sort(key=lambda ws: safe_text(getattr(ws, "Name", "")).lower())

    for workset in worksets:
        workset_id = getattr(workset, "Id", None)
        try:
            item_key = int(workset_id.IntegerValue)
        except Exception:
            continue

        visibility = None
        try:
            visibility = source_view.GetWorksetVisibility(workset_id)
        except Exception:
            pass
        visibility_name = safe_text(visibility)
        label = _WORKSET_VISIBILITY_LABELS.get(visibility_name, visibility_name)

        items.append(
            VgItemRow(
                item_key,
                safe_text(getattr(workset, "Name", "")),
                values={"text0": label},
            )
        )
        payloads[item_key] = {"visibility": visibility}

    return items, payloads


_LINK_VISIBILITY_LABELS = {
    "ByHostView": "By Host View",
    "ByLinkView": "By Linked View",
    "Custom": "Custom",
}


def links_api_available():
    """View link overrides (RevitLinkGraphicsSettings) exist in Revit 2024+."""
    return hasattr(DB, "RevitLinkGraphicsSettings")


def read_links_group(doc, source_view):
    items = []
    payloads = {}
    if not links_api_available():
        # Revit 2023: no per-view link override API. Returning no rows makes
        # the edit dialog show the unsupported hint instead of fake defaults.
        return items, payloads
    try:
        instances = list(
            DB.FilteredElementCollector(doc)
            .OfClass(DB.RevitLinkInstance)
            .ToElements()
        )
    except Exception:
        instances = []
    instances.sort(key=lambda inst: safe_text(getattr(inst, "Name", "")).lower())

    for instance in instances:
        item_key = _eid_int(getattr(instance, "Id", None))
        if item_key is None:
            continue

        settings = None
        try:
            settings = source_view.GetLinkOverrides(instance.Id)
        except Exception:
            settings = None

        display = "By Host View"
        linked_view = ""
        if settings is not None:
            visibility_name = safe_text(_ogs_attr(settings, "LinkVisibilityType"))
            display = _LINK_VISIBILITY_LABELS.get(visibility_name, visibility_name)
            try:
                linked_view_id = settings.LinkedViewId
                if _eid_int(linked_view_id) not in (None, -1):
                    link_doc = instance.GetLinkDocument()
                    if link_doc is not None:
                        linked_view = safe_text(
                            getattr(link_doc.GetElement(linked_view_id), "Name", "")
                        )
            except Exception:
                pass

        items.append(
            VgItemRow(
                item_key,
                safe_text(getattr(instance, "Name", "")),
                values={"text0": display, "text1": linked_view},
            )
        )
        payloads[item_key] = {"settings": settings}

    return items, payloads


def read_vg_group(doc, source_view, group_key):
    """Snapshot one V/G overrides group from the source view or template."""
    if group_key in _CATEGORY_GROUPS:
        return read_category_group(doc, source_view, group_key)
    if group_key == GROUP_FILTERS:
        return read_filters_group(doc, source_view)
    if group_key == GROUP_WORKSETS:
        return read_worksets_group(doc, source_view)
    if group_key == GROUP_LINKS:
        return read_links_group(doc, source_view)
    return [], {}


# ---------------------------------------------------------------------------
# Partial group writers
# ---------------------------------------------------------------------------

def _apply_category_item(target_view, item_key, payload):
    category_id = _int_to_element_id(item_key)
    target_view.SetCategoryHidden(category_id, bool(payload.get("hidden")))
    ogs = payload.get("ogs")
    if ogs is not None:
        target_view.SetCategoryOverrides(category_id, ogs)


def _apply_filter_item(target_view, item_key, payload):
    filter_id = _int_to_element_id(item_key)
    try:
        existing = set(
            _eid_int(existing_id) for existing_id in target_view.GetFilters()
        )
    except Exception:
        existing = set()
    if int(item_key) not in existing:
        target_view.AddFilter(filter_id)
    overrides = payload.get("overrides")
    if overrides is not None:
        target_view.SetFilterOverrides(filter_id, overrides)
    if payload.get("visible") is not None:
        target_view.SetFilterVisibility(filter_id, bool(payload.get("visible")))
    if payload.get("enabled") is not None:
        try:
            target_view.SetIsFilterEnabled(filter_id, bool(payload.get("enabled")))
        except Exception:
            pass


def _apply_workset_item(target_view, item_key, payload):
    visibility = payload.get("visibility")
    if visibility is None:
        return
    target_view.SetWorksetVisibility(DB.WorksetId(int(item_key)), visibility)


def _apply_link_item(target_view, item_key, payload):
    if not links_api_available():
        raise Exception("RVT link overrides require Revit 2024 or newer.")
    settings = payload.get("settings")
    if settings is None:
        settings = DB.RevitLinkGraphicsSettings()
        try:
            settings.LinkVisibilityType = DB.LinkVisibility.ByHostView
        except Exception:
            pass
    target_view.SetLinkOverrides(_int_to_element_id(item_key), settings)


def apply_partial_groups(target_view, partial_groups, payload_cache):
    """Write checked V/G items directly onto one target. Returns note strings."""
    notes = []
    for group_key in sorted((partial_groups or {}).keys()):
        item_keys = partial_groups.get(group_key) or []
        payloads = (payload_cache or {}).get(group_key) or {}
        for item_key in item_keys:
            payload = payloads.get(int(item_key))
            if payload is None:
                notes.append(
                    "{}: item {} is no longer available.".format(group_key, item_key)
                )
                continue
            try:
                if group_key in _CATEGORY_GROUPS:
                    _apply_category_item(target_view, item_key, payload)
                elif group_key == GROUP_FILTERS:
                    _apply_filter_item(target_view, item_key, payload)
                elif group_key == GROUP_WORKSETS:
                    _apply_workset_item(target_view, item_key, payload)
                elif group_key == GROUP_LINKS:
                    _apply_link_item(target_view, item_key, payload)
            except Exception as ex:
                notes.append(
                    "{} item {} failed: {}".format(group_key, item_key, safe_text(ex))
                )
    return notes


# ---------------------------------------------------------------------------
# Transfer engine
# ---------------------------------------------------------------------------

class TransferSummary(object):
    def __init__(self, requested_target_count=0, selected_setting_count=0,
                 target_noun="target view template(s)"):
        self.requested_target_count = int(requested_target_count or 0)
        self.selected_setting_count = int(selected_setting_count or 0)
        self.target_noun = safe_text(target_noun) or "target(s)"
        self.transferred_count = 0
        self.skipped = []
        self.errors = []
        self.notes = []

    @property
    def skipped_count(self):
        return len(self.skipped) + len(self.errors)

    def add_skip(self, target_name, reason):
        self.skipped.append("{}: {}".format(safe_text(target_name), safe_text(reason)))

    def add_error(self, target_name, error):
        self.errors.append("{}: {}".format(safe_text(target_name), safe_text(error)))

    def add_note(self, target_name, note):
        # Item-level detail on a target that still transferred; not a skip.
        self.notes.append("{}: {}".format(safe_text(target_name), safe_text(note)))

    def message(self):
        lines = [
            "Transferred {} setting(s) to {} {}.".format(
                self.selected_setting_count,
                self.transferred_count,
                self.target_noun,
            )
        ]
        if self.skipped_count:
            lines.append("{} {} skipped.".format(self.skipped_count, self.target_noun))
            for detail in (self.errors + self.skipped)[:8]:
                lines.append("- {}".format(detail))
            if self.skipped_count > 8:
                lines.append("- ...")
        if self.notes:
            lines.append("{} setting item note(s):".format(len(self.notes)))
            for detail in self.notes[:8]:
                lines.append("- {}".format(detail))
            if len(self.notes) > 8:
                lines.append("- ...")
        return "\n".join(lines)


def _rollback_started(transaction):
    try:
        if transaction.HasStarted():
            transaction.RollBack()
    except Exception:
        pass


def execute_transfer(doc, source_kind, source_element, target_kind, target_rows,
                     plan, payload_cache=None):
    """Transfer settings from the source view/template to the target rows.

    Template targets use the non-controlled-parameter sandwich so only the
    carried parameters change. View targets receive the source's included
    parameters via ApplyViewTemplateParameters; selective mode temporarily
    restricts the working template's controlled set first. Partial V/G
    groups are written item-by-item afterwards.
    """
    target_rows = [
        row for row in list(target_rows or [])
        if getattr(row, "element", None) is not None
    ]
    if target_kind == TARGET_KIND_VIEWS:
        target_noun = "target view(s)"
    else:
        target_noun = "target view template(s)"
    summary = TransferSummary(len(target_rows), 0, target_noun=target_noun)

    if plan is None or plan_is_empty(plan):
        summary.add_skip("Transfer", "No settings selected.")
        return summary
    if not target_rows:
        summary.add_skip("Transfer", "No targets selected.")
        return summary

    source_name = safe_text(getattr(source_element, "Name", "Source"))

    transaction_group = DB.TransactionGroup(doc, "View Template - Transfer Settings")
    transaction_group.Start()
    changed_any = False

    try:
        # 1. Materialize the working template.
        temp_template = None
        if source_kind == SOURCE_KIND_VIEW:
            transaction = DB.Transaction(doc, "Create temporary view template")
            transaction.Start()
            try:
                created = source_element.CreateViewTemplate()
                if isinstance(created, DB.ElementId):
                    created = doc.GetElement(created)
                if created is None:
                    raise Exception("temporary view template unavailable")
                temp_template = created
                transaction.Commit()
            except Exception as ex:
                _rollback_started(transaction)
                transaction_group.RollBack()
                summary.add_error(
                    source_name,
                    "Cannot create a temporary view template from this view: {}".format(
                        safe_text(ex)
                    ),
                )
                return summary
            work_template = temp_template
        else:
            work_template = source_element

        work_template_id_int = _eid_int(getattr(work_template, "Id", None))
        source_id_int = _eid_int(getattr(source_element, "Id", None))

        all_source_ids = set(
            x for x in _template_parameter_ids(work_template) if x is not None
        )
        original_source_non_controlled = [
            x for x in _non_controlled_parameter_ids(work_template) if x is not None
        ]

        if plan.transfer_all:
            carried_ids = all_source_ids - set(original_source_non_controlled)
            partial_groups = {}
            partial_param_ids = []
        else:
            carried_ids = set(
                int(x) for x in plan.full_parameter_ids
            ).intersection(all_source_ids)
            partial_groups = dict(plan.partial_groups)
            partial_param_ids = list(plan.partial_group_parameter_ids)

        summary.selected_setting_count = len(carried_ids) + sum(
            len(item_keys or []) for item_keys in partial_groups.values()
        )

        if not carried_ids and not partial_groups:
            transaction_group.RollBack()
            summary.add_skip(
                "Transfer", "None of the selected settings are available on the source."
            )
            return summary

        # 2. Selective mode toward views: restrict the working template so
        # ApplyViewTemplateParameters only carries the selected parameters.
        source_restricted = False
        if target_kind == TARGET_KIND_VIEWS and not plan.transfer_all and carried_ids:
            restricted = sorted(all_source_ids - carried_ids)
            transaction = DB.Transaction(doc, "Restrict source template parameters")
            transaction.Start()
            try:
                work_template.SetNonControlledTemplateParameterIds(
                    _to_element_ids(restricted)
                )
                transaction.Commit()
                source_restricted = True
            except Exception as ex:
                _rollback_started(transaction)
                transaction_group.RollBack()
                summary.add_error(source_name, ex)
                return summary

        # 3. Per-target isolated transactions.
        for row in target_rows:
            target = row.element
            target_name = safe_text(getattr(row, "name", "")) or "Target"
            target_id_int = _eid_int(getattr(target, "Id", None))

            if target_id_int is not None and target_id_int in (
                source_id_int,
                work_template_id_int,
            ):
                summary.add_skip(target_name, "Source cannot also be a target.")
                continue
            if not bool(getattr(row, "is_checkable", True)):
                summary.add_skip(
                    target_name,
                    safe_text(getattr(row, "tooltip_text", "")) or "Target is locked.",
                )
                continue
            if target_kind == TARGET_KIND_VIEWS:
                try:
                    if is_view_locked_by_template(_eid_int(target.ViewTemplateId)):
                        summary.add_skip(
                            target_name, "View is controlled by a view template."
                        )
                        continue
                except Exception:
                    pass

            transaction = DB.Transaction(
                doc, "Transfer settings to {}".format(target_name)
            )
            try:
                transaction.Start()
                if target_kind == TARGET_KIND_TEMPLATES:
                    target_ids = _template_parameter_ids(target)
                    compatible_ids = sorted(
                        carried_ids.intersection(set(target_ids))
                    )
                    if not compatible_ids and not partial_groups:
                        transaction.RollBack()
                        summary.add_skip(
                            target_name,
                            "No selected settings are available on this target template.",
                        )
                        continue
                    original_non_controlled = _non_controlled_parameter_ids(target)
                    temporary_non_controlled = calculate_temporary_non_controlled_ids(
                        target_ids,
                        compatible_ids,
                    )
                    final_non_controlled = calculate_final_non_controlled_ids(
                        target_ids,
                        original_non_controlled,
                        compatible_ids,
                        original_source_non_controlled,
                    )
                    final_non_controlled = adjust_final_ids_for_partial_groups(
                        final_non_controlled,
                        partial_param_ids,
                    )
                    if compatible_ids:
                        target.SetNonControlledTemplateParameterIds(
                            _to_element_ids(temporary_non_controlled)
                        )
                        target.ApplyViewTemplateParameters(work_template)
                    target.SetNonControlledTemplateParameterIds(
                        _to_element_ids(final_non_controlled)
                    )
                else:
                    if carried_ids:
                        target.ApplyViewTemplateParameters(work_template)

                if partial_groups:
                    for note in apply_partial_groups(
                        target, partial_groups, payload_cache
                    ):
                        summary.add_note(target_name, note)

                transaction.Commit()
                summary.transferred_count += 1
                changed_any = True
            except Exception as ex:
                _rollback_started(transaction)
                summary.add_error(target_name, ex)

        # 4. Restore the source template / drop the temporary one.
        if source_restricted and temp_template is None:
            transaction = DB.Transaction(doc, "Restore source template parameters")
            transaction.Start()
            try:
                work_template.SetNonControlledTemplateParameterIds(
                    _to_element_ids(original_source_non_controlled)
                )
                transaction.Commit()
            except Exception:
                _rollback_started(transaction)
                transaction_group.RollBack()
                # The group rollback undid every committed target.
                summary.transferred_count = 0
                summary.add_error(
                    source_name,
                    "Failed to restore the source template; the transfer was rolled back.",
                )
                return summary
        if temp_template is not None:
            transaction = DB.Transaction(doc, "Delete temporary view template")
            transaction.Start()
            try:
                doc.Delete(temp_template.Id)
                transaction.Commit()
            except Exception:
                _rollback_started(transaction)
                transaction_group.RollBack()
                summary.transferred_count = 0
                summary.add_error(
                    source_name,
                    "Failed to remove the temporary view template; the transfer was rolled back.",
                )
                return summary

        if changed_any:
            transaction_group.Assimilate()
        else:
            transaction_group.RollBack()
    except Exception:
        try:
            transaction_group.RollBack()
        except Exception:
            pass
        raise

    return summary
