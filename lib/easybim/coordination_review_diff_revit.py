# -*- coding: utf-8 -*-
"""Revit adapter for the EasyBIM Coordination Review comparison.

Builds the plain snapshots that :mod:`coordination_review_diff` compares:
the host elements that monitor a given link instance, and the link's own
elements of the same categories, transformed into host coordinates with
``RevitLinkInstance.GetTotalTransform()``.

The Revit API is imported lazily (and can be injected as ``db``) so the
module loads standalone under CPython for the unit tests.
"""

try:
    from easybim import coordination_review_diff as diff
except Exception:  # standalone test load without the ``easybim`` package
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import coordination_review_diff as diff


# Copy/Monitor categories: the built-in ones plus the MEP categories that
# Coordination Settings can copy.  Members missing on an older Revit are
# skipped at runtime.
CATEGORY_BUILTINS = (
    (diff.CATEGORY_LEVEL, ("OST_Levels",)),
    (diff.CATEGORY_GRID, ("OST_Grids",)),
    ("Column", ("OST_Columns", "OST_StructuralColumns")),
    ("Wall", ("OST_Walls",)),
    ("Floor", ("OST_Floors",)),
    ("Opening", ("OST_ShaftOpening", "OST_FloorOpening", "OST_SWallRectOpening", "OST_RoofOpening")),
    (
        "MEP",
        (
            "OST_MechanicalEquipment",
            "OST_PlumbingFixtures",
            "OST_LightingFixtures",
            "OST_ElectricalFixtures",
            "OST_ElectricalEquipment",
            "OST_AirTerminals",
            "OST_Sprinklers",
            "OST_LightingDevices",
            "OST_CommunicationDevices",
            "OST_DataDevices",
            "OST_FireAlarmDevices",
            "OST_NurseCallDevices",
            "OST_SecurityDevices",
            "OST_TelephoneDevices",
        ),
    ),
)


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        try:
            return value.ToString()
        except Exception:
            return ""


def _safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def _element_id_int(element_id):
    if element_id is None:
        return None
    for attr_name in ("IntegerValue", "Value"):
        try:
            return int(getattr(element_id, attr_name))
        except Exception:
            pass
    try:
        return int(element_id)
    except Exception:
        return None


def _import_revit_db():
    try:
        from Autodesk.Revit import DB

        return DB
    except Exception:
        return None


def _class_name(element):
    try:
        return _safe_text(element.__class__.__name__).lower()
    except Exception:
        return ""


def _builtin_int(member):
    try:
        return int(member)
    except Exception:
        pass
    try:
        return int(getattr(member, "value__"))
    except Exception:
        return None


def available_builtins(db, labels=None):
    """``[(label, BuiltInCategory member, int)]`` for the categories present on this Revit."""
    builtin_enum = getattr(db, "BuiltInCategory", None) if db is not None else None
    rows = []
    if builtin_enum is None:
        return rows
    wanted = set(labels) if labels is not None else None
    for label, member_names in CATEGORY_BUILTINS:
        if wanted is not None and label not in wanted:
            continue
        for member_name in member_names:
            member = getattr(builtin_enum, member_name, None)
            if member is None:
                continue
            value = _builtin_int(member)
            if value is None:
                continue
            rows.append((label, member, value))
    return rows


def _collect_elements(doc, builtins, db):
    """Elements of the given built-in categories (instances only)."""
    collector_type = getattr(db, "FilteredElementCollector", None) if db is not None else None
    if collector_type is None or not builtins:
        return []

    # One multi-category pass when the CLR list is available.
    try:
        from System.Collections.Generic import List as ClrList

        categories = ClrList[db.BuiltInCategory]()
        for _label, member, _value in builtins:
            categories.Add(member)
        collector = collector_type(doc).WherePasses(db.ElementMulticategoryFilter(categories))
        return list(collector.WhereElementIsNotElementType().ToElements())
    except Exception:
        pass

    elements = []
    seen = set()
    for _label, member, _value in builtins:
        try:
            found = list(
                collector_type(doc).OfCategory(member).WhereElementIsNotElementType().ToElements()
            )
        except Exception:
            continue
        for element in found:
            key = _element_id_int(getattr(element, "Id", None))
            if key in seen:
                continue
            seen.add(key)
            elements.append(element)
    return elements


def _category_label(element, builtin_map, db):
    if db is not None:
        for type_name, label in (("Level", diff.CATEGORY_LEVEL), ("Grid", diff.CATEGORY_GRID)):
            element_type = getattr(db, type_name, None)
            if element_type is not None:
                try:
                    if isinstance(element, element_type):
                        return label
                except Exception:
                    pass
    class_name = _class_name(element)
    if class_name == "level":
        return diff.CATEGORY_LEVEL
    if class_name == "grid":
        return diff.CATEGORY_GRID
    try:
        category_id = _element_id_int(element.Category.Id)
    except Exception:
        category_id = None
    return builtin_map.get(category_id)


def _xyz_tuple(xyz):
    try:
        return (float(xyz.X), float(xyz.Y), float(xyz.Z))
    except Exception:
        return None


def _transform_point(transform, point, db):
    if point is None:
        return None
    if transform is None:
        return point
    xyz_type = getattr(db, "XYZ", None) if db is not None else None
    if xyz_type is None:
        return point
    try:
        return _xyz_tuple(transform.OfPoint(xyz_type(point[0], point[1], point[2])))
    except Exception:
        return point


def _element_name(element):
    try:
        return _safe_text(element.Name)
    except Exception:
        return ""


def _type_name(doc, element):
    try:
        element_type = doc.GetElement(element.GetTypeId())
    except Exception:
        element_type = None
    if element_type is None:
        return ""
    name = _element_name(element_type)
    family = ""
    try:
        family = _safe_text(element_type.FamilyName)
    except Exception:
        family = ""
    if family and name:
        return "{0}: {1}".format(family, name)
    return name or family


def _curve_geometry(curve, transform, db):
    """``(curve_endpoints, arc)`` tuples from a Revit curve."""
    if curve is None:
        return None, None
    center = getattr(curve, "Center", None)
    radius = getattr(curve, "Radius", None)
    if center is not None and radius is not None:
        center_point = _transform_point(transform, _xyz_tuple(center), db)
        if center_point is not None:
            return None, (center_point, _safe_float(radius, 0.0))
    try:
        start = _transform_point(transform, _xyz_tuple(curve.GetEndPoint(0)), db)
        end = _transform_point(transform, _xyz_tuple(curve.GetEndPoint(1)), db)
    except Exception:
        return None, None
    if start is None or end is None:
        return None, None
    return (start, end), None


def _bounding_box_center(element, transform, db):
    try:
        bbox = element.get_BoundingBox(None)
    except Exception:
        bbox = None
    if bbox is None:
        return None
    low, high = _xyz_tuple(bbox.Min), _xyz_tuple(bbox.Max)
    if low is None or high is None:
        return None
    center = ((low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0, (low[2] + high[2]) / 2.0)
    return _transform_point(transform, center, db)


def snapshot(doc, element, category, transform=None, db=None):
    """Plain snapshot of ``element`` in host coordinates."""
    item = {
        "id": _element_id_int(getattr(element, "Id", None)),
        "category": category,
        "name": _element_name(element),
        "type_name": _type_name(doc, element),
        "elevation": None,
        "point": None,
        "curve": None,
        "arc": None,
    }

    if category == diff.CATEGORY_LEVEL:
        # ProjectElevation is always relative to the internal origin, which
        # is what the link transform maps; Elevation follows the level type's
        # Elevation Base setting.  Older APIs only offer Elevation.
        elevation = _safe_float(getattr(element, "ProjectElevation", None))
        if elevation is None:
            elevation = _safe_float(getattr(element, "Elevation", None))
        if elevation is not None:
            point = _transform_point(transform, (0.0, 0.0, elevation), db)
            item["elevation"] = point[2] if point is not None else elevation
        return item

    if category == diff.CATEGORY_GRID:
        curve, arc = _curve_geometry(getattr(element, "Curve", None), transform, db)
        item["curve"], item["arc"] = curve, arc
        return item

    location = getattr(element, "Location", None)
    location_curve = getattr(location, "Curve", None) if location is not None else None
    if location_curve is not None:
        curve, arc = _curve_geometry(location_curve, transform, db)
        item["curve"], item["arc"] = curve, arc
        if curve is not None or arc is not None:
            return item
    location_point = getattr(location, "Point", None) if location is not None else None
    if location_point is not None:
        item["point"] = _transform_point(transform, _xyz_tuple(location_point), db)
        if item["point"] is not None:
            return item

    item["point"] = _bounding_box_center(element, transform, db)
    return item


def _monitors_link(element, link_id):
    try:
        if not element.IsMonitoringLinkElement():
            return False
    except Exception:
        return False
    try:
        monitored = list(element.GetMonitoredLinkElementIds() or [])
    except Exception:
        return False
    for monitored_id in monitored:
        if _element_id_int(monitored_id) == link_id:
            return True
    return False


def collect_host_monitoring_items(doc, link_instance_id_int, db=None):
    """Snapshots of the host elements that monitor the given link instance."""
    db = db if db is not None else _import_revit_db()
    link_id = _element_id_int(link_instance_id_int)
    if doc is None or link_id is None:
        return []
    builtins = available_builtins(db)
    builtin_map = dict((value, label) for label, _member, value in builtins)
    items = []
    for element in _collect_elements(doc, builtins, db):
        if not _monitors_link(element, link_id):
            continue
        label = _category_label(element, builtin_map, db)
        if not label:
            continue
        items.append(snapshot(doc, element, label, None, db))
    return items


def count_monitoring_elements(doc, db=None, limit=1):
    """How many host elements use Copy/Monitor against any link.

    Answers "does Coordination Review apply to this model at all", so the
    default stops at the first hit.  Pass a larger ``limit`` for a count.
    """
    db = db if db is not None else _import_revit_db()
    if doc is None:
        return 0
    found = 0
    for element in _collect_elements(doc, available_builtins(db), db):
        try:
            if not element.IsMonitoringLinkElement():
                continue
        except Exception:
            continue
        try:
            if not list(element.GetMonitoredLinkElementIds() or []):
                continue
        except Exception:
            continue
        found += 1
        if limit and found >= int(limit):
            break
    return found


def collect_link_items(link_doc, transform, labels, db=None):
    """Snapshots of the link's elements for ``labels``, in host coordinates."""
    db = db if db is not None else _import_revit_db()
    if link_doc is None or not labels:
        return []
    builtins = available_builtins(db, labels)
    builtin_map = dict((value, label) for label, _member, value in builtins)
    items = []
    for element in _collect_elements(link_doc, builtins, db):
        label = _category_label(element, builtin_map, db)
        if not label or label not in labels:
            continue
        items.append(snapshot(link_doc, element, label, transform, db))
    return items


def make_length_formatter(doc, db=None):
    """Format internal feet in the project's length display units."""
    db = db if db is not None else _import_revit_db()
    units = None
    try:
        units = doc.GetUnits()
    except Exception:
        units = None
    spec_id = getattr(getattr(db, "SpecTypeId", None), "Length", None)
    if spec_id is None:
        spec_id = getattr(getattr(db, "UnitType", None), "UT_Length", None)
    format_utils = getattr(db, "UnitFormatUtils", None)

    def _format(value):
        value = float(value or 0.0)
        if units is not None and spec_id is not None and format_utils is not None:
            for args in (
                (units, spec_id, value, False),
                (units, spec_id, value, False, False),
                (units, spec_id, value),
            ):
                try:
                    text = _safe_text(format_utils.Format(*args)).strip()
                    if text:
                        return text
                except Exception:
                    continue
        return "{0:.4f} ft".format(value)

    return _format


def _link_document(link_instance):
    try:
        return link_instance.GetLinkDocument()
    except Exception:
        return None


def _link_transform(link_instance):
    for method_name in ("GetTotalTransform", "GetTransform"):
        method = getattr(link_instance, method_name, None)
        if not callable(method):
            continue
        try:
            return method()
        except Exception:
            continue
    return None


def _doc_title(doc):
    try:
        return _safe_text(doc.Title)
    except Exception:
        return ""


def build_link_issue_report(doc, link_instance, db=None, format_length=None):
    """Compute the Coordination Review report for one link instance."""
    db = db if db is not None else _import_revit_db()
    report = {
        "doc_title": _doc_title(doc),
        "link_name": _safe_text(getattr(link_instance, "Name", "")),
        "link_id": _element_id_int(getattr(link_instance, "Id", None)),
        "link_loaded": False,
        "monitored_count": 0,
        "issue_count": 0,
        "ok_count": 0,
        "estimated_count": 0,
        "level_offset_ft": 0.0,
        "level_offset_text": "",
        "issues": [],
        "groups": [],
        "error": "",
    }
    if doc is None or link_instance is None:
        report["error"] = "No link instance to review."
        return report

    link_doc = _link_document(link_instance)
    if link_doc is None:
        report["error"] = "Link {} is not loaded. Reload it (Manage Links) and try again.".format(
            report["link_name"] or report["link_id"]
        )
        return report
    report["link_loaded"] = True

    fmt = format_length if callable(format_length) else make_length_formatter(doc, db)
    host_items = collect_host_monitoring_items(doc, report["link_id"], db)
    labels = set(item["category"] for item in host_items)
    link_items = collect_link_items(link_doc, _link_transform(link_instance), labels, db)

    result = diff.compare(host_items, link_items, format_length=fmt)
    report["monitored_count"] = result["monitored_count"]
    report["ok_count"] = result["ok_count"]
    report["estimated_count"] = result["estimated_count"]
    report["issues"] = result["issues"]
    report["issue_count"] = len(result["issues"])
    report["groups"] = diff.group_issues(result["issues"])
    report["level_offset_ft"] = result["level_offset_ft"]
    if abs(result["level_offset_ft"]) > 0:
        report["level_offset_text"] = (
            "Every matched level differs by the same {0}; this is usually a "
            "Copy/Monitor level offset rather than a move."
        ).format(diff._signed(result["level_offset_ft"], fmt))
    return report
