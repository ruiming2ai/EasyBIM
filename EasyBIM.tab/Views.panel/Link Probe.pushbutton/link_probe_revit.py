# -*- coding: utf-8 -*-
"""Every Revit read Link Probe performs.  Nothing here writes.

No transaction is ever opened, so this module is safe to run against a live
production model.  ``test_link_probe_report.py`` enforces that by scanning
this source for transaction and write-API names.

Each Revit call is wrapped on its own.  A probe that aborts a whole section
because one property was missing on one Revit build would defeat its own
purpose, so failures are recorded as text and the walk carries on.
"""

from __future__ import print_function

import time

from pyrevit import DB

from easybim.compat import element_id_factory
from easybim.compat import eid_to_int
from easybim.compat import exception_text
from easybim.compat import safe_text

import link_probe_report as report


# ---------------------------------------------------------------------------
# capability gates
# ---------------------------------------------------------------------------

def links_api_available():
    """``RevitLinkGraphicsSettings`` and ``View.GetLinkOverrides`` are 2024+."""
    return hasattr(DB, "RevitLinkGraphicsSettings")


def basics_api_available():
    """The nine Basics properties landed in Revit 2025.

    Probed on the type rather than against a version number: a property that
    is not there cannot be read, and that is the only thing the probe cares
    about.  ``ObjectStyles`` stands in for the whole 2025 set.
    """
    settings_type = getattr(DB, "RevitLinkGraphicsSettings", None)
    return settings_type is not None and hasattr(settings_type, "ObjectStyles")


def document_blocker(doc):
    """Why this document cannot be probed, or an empty string."""
    if doc is None:
        return u"Open a project first."
    if getattr(doc, "IsFamilyDocument", False):
        return u"Link Probe works on a project, not a family."
    if not links_api_available():
        return (u"Link Probe needs Revit 2024 or newer: there is no link "
                u"override API before that.")
    return u""


# ---------------------------------------------------------------------------
# host and view facts
# ---------------------------------------------------------------------------

def read_host_facts(doc, app):
    facts = {
        "version": u"",
        "build": u"",
        "title": u"",
        "workshared": None,
        "links_api": links_api_available(),
        "basics_api": basics_api_available(),
    }
    for key, source, attribute in (("version", app, "VersionNumber"),
                                   ("build", app, "VersionBuild"),
                                   ("title", doc, "Title")):
        try:
            facts[key] = safe_text(getattr(source, attribute, u""))
        except Exception:
            pass
    try:
        facts["workshared"] = bool(getattr(doc, "IsWorkshared", False))
    except Exception:
        pass
    return facts


def _view_template(doc, view):
    try:
        template_id = view.ViewTemplateId
    except Exception:
        return None
    if eid_to_int(template_id) in (None, -1):
        return None
    try:
        return doc.GetElement(template_id)
    except Exception:
        return None


def template_controls_links(template):
    """Whether a template owns VIS_GRAPHICS_RVT_LINKS.

    Matched on the BuiltInParameter id, never on its localized display name -
    the same decision View Settings Transfer already made in
    ``_VG_BUILTIN_GROUP_MEMBERS``.  A view whose template
    controls this parameter silently ignores SetLinkOverrides, so this is
    the difference between a write that works and one that reports success
    and does nothing.
    """
    if template is None:
        return False
    try:
        target = int(DB.BuiltInParameter.VIS_GRAPHICS_RVT_LINKS)
    except Exception:
        return False
    try:
        controlled = set(eid_to_int(pid)
                         for pid in template.GetTemplateParameterIds())
    except Exception:
        return False
    try:
        for pid in template.GetNonControlledTemplateParameterIds():
            controlled.discard(eid_to_int(pid))
    except Exception:
        pass
    return target in controlled


def read_view_facts(doc, view):
    facts = {
        "name": u"",
        "type": u"",
        "is_template": None,
        "is_dependent": False,
        "primary_name": u"",
        "template_name": u"",
        "template_controls_links": None,
    }
    try:
        facts["name"] = safe_text(getattr(view, "Name", u""))
    except Exception:
        pass
    try:
        facts["type"] = safe_text(getattr(view, "ViewType", u""))
    except Exception:
        pass
    try:
        facts["is_template"] = bool(getattr(view, "IsTemplate", False))
    except Exception:
        pass
    try:
        primary_id = view.GetPrimaryViewId()
        if eid_to_int(primary_id) not in (None, -1):
            facts["is_dependent"] = True
            primary = doc.GetElement(primary_id)
            facts["primary_name"] = safe_text(getattr(primary, "Name", u""))
    except Exception:
        pass
    template = _view_template(doc, view)
    if template is not None:
        facts["template_name"] = safe_text(getattr(template, "Name", u""))
    facts["template_controls_links"] = template_controls_links(template)
    return facts


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------

class LinkOption(object):
    """One Revit link instance, loaded or not.

    Unlike the picker in Linked Sheets Copy this keeps unloaded links.  A
    diagnostic whose job is to count must never make a link disappear:
    three links across ninety-six views quietly becoming two is the worst
    failure mode available to it.
    """

    def __init__(self, instance, link_doc, label, loaded, error=u""):
        self.instance = instance
        self.doc = link_doc
        self.label = safe_text(label)
        self.loaded = bool(loaded)
        self.error = safe_text(error)
        self.instance_key = eid_to_int(getattr(instance, "Id", None))
        self.type_key = None
        try:
            self.type_key = eid_to_int(instance.GetTypeId())
        except Exception:
            pass

    @property
    def display(self):
        if self.loaded:
            return self.label
        return u"{0}  -  not loaded".format(self.label)

    def as_payload(self):
        return {
            "label": self.label,
            "loaded": self.loaded,
            "instance_key": self.instance_key,
            "type_key": self.type_key,
            "error": self.error,
        }


def collect_link_options(doc):
    """Every Revit link instance in the document, loaded or not.

    Instances matter rather than types: two instances of one link have
    different transforms, and the override APIs are keyed on the instance.
    Multiple instances of the same file are disambiguated by instance name,
    the way Linked Sheets Copy does it.
    """
    try:
        instances = list(DB.FilteredElementCollector(doc)
                         .OfClass(DB.RevitLinkInstance)
                         .WhereElementIsNotElementType()
                         .ToElements())
    except Exception:
        return []

    titles = {}
    for instance in instances:
        try:
            link_doc = instance.GetLinkDocument()
        except Exception:
            link_doc = None
        title = safe_text(getattr(link_doc, "Title", u"")) if link_doc else u""
        titles[title] = titles.get(title, 0) + 1

    options = []
    for instance in instances:
        link_doc = None
        error = u""
        try:
            link_doc = instance.GetLinkDocument()
        except Exception as ex:
            error = exception_text(ex)
        instance_name = safe_text(getattr(instance, "Name", u""))
        if link_doc is None:
            label = instance_name or u"(unnamed link instance)"
            options.append(LinkOption(instance, None, label, False, error))
            continue
        title = safe_text(getattr(link_doc, "Title", u""))
        label = title
        if titles.get(title, 0) > 1 and instance_name:
            label = u"{0}  [{1}]".format(title, instance_name)
        options.append(LinkOption(instance, link_doc, label, True, error))

    options.sort(key=lambda option: (not option.loaded, option.label.lower()))
    return options


# ---------------------------------------------------------------------------
# link display settings
# ---------------------------------------------------------------------------

_ENUM_PROPERTIES = (
    ("view_range", "ViewRange"),
    ("color_fill", "ColorFill"),
    ("object_styles", "ObjectStyles"),
    ("nested_links", "NestedLinks"),
    ("view_filters", "ViewFilterType"),
)

_ENUM_GETTERS = (
    ("discipline_type", "GetDisciplineType"),
    ("phase_type", "GetPhaseType"),
    ("phase_filter_type", "GetPhaseFilterType"),
    ("detail_level_type", "GetViewDetailLevelType"),
)

_VALUE_GETTERS = (
    (u"Discipline value", "GetDiscipline"),
    (u"Detail level value", "GetViewDetailLevel"),
)


def _settings_for(view, element_id):
    try:
        return view.GetLinkOverrides(element_id), u""
    except Exception as ex:
        return None, exception_text(ex)


def read_link_basics(doc, view, option):
    """Everything ``GetLinkOverrides`` exposes for one (view, link) pair.

    Values come back as plain strings and ints - enum member names, not CLR
    objects - which is what lets the classification live in a module that
    never imports Revit.
    """
    payload = {
        "link_label": option.label,
        "visibility": u"",
        "linked_view_name": u"",
        "linked_view_resolved": False,
        "had_linked_view_id": False,
        "basics": {},
        "extra": {},
        "type_slot_differs": None,
        "classification": u"",
        "note": u"",
    }

    if not option.loaded:
        payload["classification"], payload["note"] = report.classify_binding(
            u"", link_loaded=False)
        return payload

    settings, error = _settings_for(view, option.instance.Id)
    if settings is None:
        payload["classification"], payload["note"] = report.classify_binding(
            u"", read_error=error or u"GetLinkOverrides returned nothing.")
        return payload

    try:
        payload["visibility"] = safe_text(settings.LinkVisibilityType)
    except Exception as ex:
        payload["extra"][u"visibility error"] = exception_text(ex)

    try:
        linked_view_id = settings.LinkedViewId
        if eid_to_int(linked_view_id) not in (None, -1):
            payload["had_linked_view_id"] = True
            linked_view = option.doc.GetElement(linked_view_id)
            if linked_view is not None:
                payload["linked_view_resolved"] = True
                payload["linked_view_name"] = safe_text(
                    getattr(linked_view, "Name", u""))
    except Exception as ex:
        payload["extra"][u"linked view error"] = exception_text(ex)

    basics = {}
    for field in report.BASICS_TYPE_FIELDS:
        basics[field] = None
    for field, attribute in _ENUM_PROPERTIES:
        try:
            basics[field] = safe_text(getattr(settings, attribute))
        except Exception:
            basics[field] = None
    for field, method in _ENUM_GETTERS:
        try:
            basics[field] = safe_text(getattr(settings, method)())
        except Exception:
            basics[field] = None
    payload["basics"] = basics

    for label, method in _VALUE_GETTERS:
        try:
            payload["extra"][label] = safe_text(getattr(settings, method)())
        except Exception:
            pass
    for label, method in ((u"Phase id", "GetPhaseId"),
                          (u"Phase filter id", "GetPhaseFilterId")):
        try:
            payload["extra"][label] = eid_to_int(getattr(settings, method)())
        except Exception:
            pass
    try:
        payload["extra"][u"View range supported"] = safe_text(
            settings.IsViewRangeSupported(view))
    except Exception:
        pass

    # Revit accepts either a RevitLinkInstance id or a RevitLinkType id here.
    # If those are independent slots, a type-keyed override could be what is
    # actually driving the view while the instance read reports Unmanaged.
    if option.type_key is not None:
        make_id = element_id_factory(DB.ElementId)
        type_settings, _ = _settings_for(view, make_id(option.type_key))
        if type_settings is None:
            payload["type_slot_differs"] = False
        else:
            try:
                payload["type_slot_differs"] = (
                    safe_text(type_settings.LinkVisibilityType)
                    != payload["visibility"])
            except Exception:
                payload["type_slot_differs"] = None

    payload["classification"], payload["note"] = report.classify_binding(
        payload["visibility"],
        basics=basics,
        link_loaded=True,
        linked_view_resolved=payload["linked_view_resolved"],
        had_linked_view_id=payload["had_linked_view_id"],
        basics_api=basics_api_available(),
    )
    return payload


# ---------------------------------------------------------------------------
# the geometry experiment
# ---------------------------------------------------------------------------

def _category_name(geometry_object, docs):
    """Top-level category name for one geometry object, or an empty string.

    Subcategories are walked up to their parent so that a solid drawn on
    "Furniture : Hidden Lines" still answers "Furniture" - which is the level
    the display-settings dialog works at.
    """
    try:
        style_id = geometry_object.GraphicsStyleId
    except Exception:
        return u""
    if eid_to_int(style_id) in (None, -1):
        return u""
    for doc in docs:
        if doc is None:
            continue
        try:
            style = doc.GetElement(style_id)
        except Exception:
            continue
        if style is None:
            continue
        try:
            category = style.GraphicsStyleCategory
        except Exception:
            continue
        if category is None:
            continue
        try:
            parent = category.Parent
            if parent is not None:
                category = parent
        except Exception:
            pass
        try:
            return safe_text(category.Name)
        except Exception:
            continue
    return u""


class _Budget(object):
    """Cap on a geometry walk, and the record of whether it was hit.

    The truncation flag is the whole reason this class exists: a walk that
    stopped early can prove a category is present but never that it is
    absent, and the verdict logic downgrades every absence-based conclusion
    when this is set.
    """

    def __init__(self, max_objects, max_seconds):
        self.max_objects = int(max_objects)
        self.deadline = time.time() + float(max_seconds)
        self.count = 0
        self.truncated = False

    def spend(self):
        self.count += 1
        if self.count >= self.max_objects:
            self.truncated = True
            return False
        if time.time() > self.deadline:
            self.truncated = True
            return False
        return True


def _walk(geometry, docs, found, budget, depth=0):
    if geometry is None or depth > 8:
        return
    try:
        iterator = iter(geometry)
    except Exception:
        return
    for item in iterator:
        if not budget.spend():
            return
        name = _category_name(item, docs)
        if name:
            found.add(name)
        nested = None
        if isinstance(item, DB.GeometryInstance):
            for method in ("GetInstanceGeometry", "GetSymbolGeometry"):
                try:
                    nested = getattr(item, method)()
                except Exception:
                    nested = None
                if nested is not None:
                    break
        elif isinstance(item, DB.GeometryElement):
            nested = item
        if nested is not None:
            _walk(nested, docs, found, budget, depth + 1)


def _collect_categories(instance, docs, view, max_objects, max_seconds):
    """Categories present in a link instance's geometry.

    Passing ``view=None`` is the control pass.  Only ``Options.View`` differs
    between the two passes, so any difference in the result is attributable
    to it alone.
    """
    budget = _Budget(max_objects, max_seconds)
    found = set()
    try:
        options = DB.Options()
    except Exception as ex:
        return found, budget, exception_text(ex)
    if view is not None:
        try:
            options.View = view
        except Exception as ex:
            return found, budget, exception_text(ex)
    try:
        geometry = instance.get_Geometry(options)
    except Exception as ex:
        return found, budget, exception_text(ex)
    try:
        _walk(geometry, docs, found, budget)
    except Exception as ex:
        return found, budget, exception_text(ex)
    return found, budget, u""


def run_geometry_experiment(doc, view, option, target_category,
                            max_objects=250000, max_seconds=90):
    """The question the whole probe exists to answer.

    Two identical geometry walks over the same link instance, differing only
    in whether ``Options.View`` is set.  If Revit honours the link's display
    settings when a view is supplied, the category the user hid inside the
    link will be missing from the second pass and present in the first.
    """
    payload = {
        "link_label": option.label,
        "target_category": safe_text(target_category),
        "control": [],
        "test": [],
        "control_count": 0,
        "test_count": 0,
        "control_truncated": False,
        "test_truncated": False,
        "verdict": u"",
        "note": u"",
    }
    docs = [option.doc, doc]

    control, control_budget, control_error = _collect_categories(
        option.instance, docs, None, max_objects, max_seconds)
    payload["control"] = sorted(control)
    payload["control_count"] = control_budget.count
    payload["control_truncated"] = control_budget.truncated

    test, test_budget, test_error = _collect_categories(
        option.instance, docs, view, max_objects, max_seconds)
    payload["test"] = sorted(test)
    payload["test_count"] = test_budget.count
    payload["test_truncated"] = test_budget.truncated

    payload["verdict"], payload["note"] = report.geometry_verdict(
        control,
        test,
        target_category,
        control_truncated=control_budget.truncated,
        test_truncated=test_budget.truncated,
        error=control_error or test_error,
    )
    return payload


# ---------------------------------------------------------------------------
# the linked view, read directly
# ---------------------------------------------------------------------------

def scan_linked_view(option, linked_view_name):
    """Read the architect's own view through ``GetLinkDocument()``.

    This is the drift baseline in miniature: if these reads work, a tool can
    tell an engineer exactly what the architect changed between two issues.
    Read-only on a linked document - a transaction on one is never legal.
    """
    payload = {
        "view_name": safe_text(linked_view_name),
        "category_count": 0,
        "hidden": [],
        "overridden": [],
        "filters": [],
        "view_range_ok": False,
        "error": u"",
    }
    if option.doc is None:
        payload["error"] = u"The link is not loaded."
        return payload

    linked_view = None
    try:
        for candidate in (DB.FilteredElementCollector(option.doc)
                          .OfClass(DB.View)
                          .ToElements()):
            if safe_text(getattr(candidate, "Name", u"")) == payload["view_name"]:
                linked_view = candidate
                break
    except Exception as ex:
        payload["error"] = exception_text(ex)
        return payload
    if linked_view is None:
        payload["error"] = u"Could not find that view in the linked document."
        return payload

    hidden = []
    overridden = []
    count = 0
    try:
        categories = list(option.doc.Settings.Categories)
    except Exception as ex:
        payload["error"] = exception_text(ex)
        categories = []

    for category in categories:
        try:
            if not category.get_AllowsVisibilityControl(linked_view):
                continue
        except Exception:
            pass
        name = u""
        try:
            name = safe_text(category.Name)
        except Exception:
            continue
        count += 1
        try:
            if linked_view.GetCategoryHidden(category.Id):
                hidden.append(name)
        except Exception:
            pass
        try:
            overrides = linked_view.GetCategoryOverrides(category.Id)
            if overrides is not None and bool(overrides.Halftone):
                overridden.append(name)
        except Exception:
            pass

    payload["category_count"] = count
    payload["hidden"] = hidden
    payload["overridden"] = overridden

    try:
        for filter_id in linked_view.GetFilters():
            element = option.doc.GetElement(filter_id)
            payload["filters"].append(safe_text(getattr(element, "Name", u"")))
    except Exception:
        pass
    try:
        payload["view_range_ok"] = linked_view.GetViewRange() is not None
    except Exception:
        pass
    return payload


def link_category_names(option):
    """Category names offered as "which one did you hide?"."""
    names = []
    if option.doc is None:
        return names
    try:
        for category in option.doc.Settings.Categories:
            try:
                if category.CategoryType != DB.CategoryType.Model:
                    continue
            except Exception:
                pass
            try:
                names.append(safe_text(category.Name))
            except Exception:
                continue
    except Exception:
        return names
    return sorted(set(name for name in names if name))
