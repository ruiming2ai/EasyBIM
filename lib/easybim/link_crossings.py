# -*- coding: utf-8 -*-
"""Where host-model curves cross the rated walls and floors of linked models.

The engine the Sleeve Place and Penetration Schedule briefs asked for,
built first for Fire Damper Check.  Read-only, no Transaction, ever.

How it works, and why it is shaped this way:

* One context per linked *document* for reading (types, rating parameters,
  line styles are per document), and one entry per placed *instance* for
  geometry - a copied link has one document and several transforms.  The
  host document is a context too, with the identity transform, because an
  MEP model sometimes carries rated walls of its own.
* Solids are never transformed.  The host curve is mapped into link space
  with the inverse of ``GetTotalTransform()`` (endpoints, then
  ``Line.CreateBound``), intersected there with ``Solid.IntersectWithCurve``,
  and the hits are mapped back.  Every solid of a wall is kept and the
  intervals along the curve are merged: a mitred join splits one wall into
  two solids, and a sweep is a third the merge absorbs.  Hosted doors and
  openings are already cut out of the solid, so a duct through a door is
  not a crossing - the geometry decides, not a heuristic.
* Ten thousand ducts against a few hundred rated walls is the wall: one
  ``BoundingBoxIntersectsFilter`` per link document with the union of the
  host curves (a campus link shrinks to the wing the MEP model covers),
  then a pure-Python spatial hash of barrier boxes in host space, then a
  slab test, and only then a solid call.  Solids are read lazily and
  cached per element; a wall whose geometry throws is named unreadable
  once and dropped from further tests, never counted clean.
* Drawn fire-rating lines have no thickness and no height.  They cross
  back as vertical panels - the curve's points in host space plus a
  z-band from the owner view's level (never the detail line's own Z) up to
  the next level - and the state layer intersects those without Revit.

``db`` is injectable so the desktop tests drive it with fakes shaped like
the API; only ints, floats, booleans and unicode cross back.
"""

from __future__ import print_function

import math
import time

try:
    from pyrevit import DB
except Exception:  # off-Revit (unit tests)
    DB = None

try:
    from easybim.compat import eid_to_int
    from easybim.compat import element_id_factory
    from easybim.compat import safe_text
except Exception:  # standalone load without the easybim package
    def safe_text(value):
        if value is None:
            return ""
        try:
            return str(value)
        except Exception:
            try:
                return value.ToString()
            except Exception:
                return ""

    def eid_to_int(element_id):
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

    def element_id_factory(element_id_type):
        def _construct(value):
            return element_id_type(int(value))
        return _construct


HOST_KEY = u"This model"

MM = 1.0 / 304.8  # feet per millimetre
CELL_FT = 10.0
BBOX_PAD_FT = 1.0
MIN_SEGMENT_FT = 1.0 * MM
SNAP_LEVEL_FT = 100.0 * MM

DEFAULT_BUDGET_SECONDS = 120.0
MAX_SOLID_ELEMENTS = 4000
MAX_SOLIDS_PER_ELEMENT = 16
MAX_CANDIDATE_TESTS = 500000
MAX_CROSSINGS = 20000
MAX_LINE_BARRIERS = 5000
PROGRESS_EVERY = 200

PLAN_VIEW_TYPES = ("FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan")

BARRIER_CATEGORIES = (
    ("OST_Walls", "wall", u"Walls"),
    ("OST_Floors", "floor", u"Floors"),
)


# ----------------------------------------------------------------- probes


def _db(db):
    return db if db is not None else DB


def _builtin(db, enum_name_text, member_name):
    enum = getattr(db, enum_name_text, None) if db is not None else None
    if enum is None:
        return None
    return getattr(enum, member_name, None)


def enum_name(value):
    text = safe_text(value)
    if u"." in text:
        text = text.rsplit(u".", 1)[1]
    return text


def _xyz(point):
    try:
        return [float(point.X), float(point.Y), float(point.Z)]
    except Exception:
        return None


def _make_xyz(db, point):
    return db.XYZ(float(point[0]), float(point[1]), float(point[2]))


def _collector(doc, db):
    collector_type = getattr(db, "FilteredElementCollector", None)
    if collector_type is None:
        return None
    try:
        return collector_type(doc)
    except Exception:
        return None


def _category_int(element):
    try:
        category = element.Category
        if category is None:
            return None
        return eid_to_int(category.Id)
    except Exception:
        return None


def _type_element(element, doc, cache):
    try:
        type_id = eid_to_int(element.GetTypeId())
    except Exception:
        return None, None
    if type_id is None or type_id < 0:
        return None, type_id
    if type_id in cache:
        return cache[type_id], type_id
    try:
        element_type = doc.GetElement(element.GetTypeId())
    except Exception:
        element_type = None
    cache[type_id] = element_type
    return element_type, type_id


def _type_name(element_type, element):
    name = u""
    if element_type is not None:
        try:
            name = safe_text(element_type.Name)
        except Exception:
            name = u""
        if not name:
            try:
                name = safe_text(element_type.FamilyName)
            except Exception:
                name = u""
    if not name:
        try:
            name = safe_text(element.Name)
        except Exception:
            name = u""
    return name


def _string_params(element):
    """``{parameter name: non-empty text}`` for an element's string params."""
    found = {}
    try:
        parameters = list(element.Parameters)
    except Exception:
        return found
    for param in parameters:
        try:
            if enum_name(param.StorageType) != u"String":
                continue
            definition = param.Definition
            name = safe_text(definition.Name) if definition is not None else u""
            if not name:
                continue
            text = safe_text(param.AsString() or u"").strip()
            if text:
                found[name] = text
        except Exception:
            continue
    return found


def _lookup_string(element, name):
    try:
        param = element.LookupParameter(name)
    except Exception:
        return u""
    if param is None:
        return u""
    try:
        return safe_text(param.AsString() or u"").strip()
    except Exception:
        return u""


def _bip_string(element, db, bip_name):
    builtin = _builtin(db, "BuiltInParameter", bip_name)
    if builtin is None:
        return u""
    try:
        param = element.get_Parameter(builtin)
        return safe_text(param.AsString() or u"").strip() if param is not None else u""
    except Exception:
        return u""


def _wall_kind(element):
    """``curtain`` / ``stacked`` / ``member`` / ``basic`` / ``inplace``."""
    try:
        if enum_name(element.WallType.Kind) == u"Curtain":
            return "curtain"
    except Exception:
        pass
    try:
        if bool(element.IsStackedWall):
            return "stacked"
    except Exception:
        pass
    try:
        if bool(element.IsStackedWallMember):
            return "member"
    except Exception:
        pass
    try:
        if getattr(element, "Symbol", None) is not None:
            return "inplace"
    except Exception:
        pass
    return "basic"


def _thickness(element, kind, db):
    if kind == "wall":
        try:
            return float(element.Width)
        except Exception:
            return 0.0
    builtin = _builtin(db, "BuiltInParameter", "FLOOR_ATTR_THICKNESS_PARAM")
    if builtin is None:
        return 0.0
    try:
        param = element.get_Parameter(builtin)
        if param is not None:
            return float(param.AsDouble())
    except Exception:
        pass
    return 0.0


def _bbox_corners_host(element, transform, db):
    """The 8 corners of the element's box, in host coordinates."""
    try:
        bbox = element.get_BoundingBox(None)
    except Exception:
        return None
    if bbox is None:
        return None
    try:
        low, high = bbox.Min, bbox.Max
    except Exception:
        return None
    corners = []
    for x_value in (low.X, high.X):
        for y_value in (low.Y, high.Y):
            for z_value in (low.Z, high.Z):
                corners.append((float(x_value), float(y_value), float(z_value)))
    for extra in (getattr(bbox, "Transform", None), transform):
        if extra is None:
            continue
        try:
            if extra.IsIdentity:
                continue
        except Exception:
            pass
        mapped = []
        for corner in corners:
            try:
                point = extra.OfPoint(_make_xyz(db, corner))
                mapped.append((float(point.X), float(point.Y), float(point.Z)))
            except Exception:
                return None
        corners = mapped
    return corners


def _corners_to_box(corners):
    xs = [corner[0] for corner in corners]
    ys = [corner[1] for corner in corners]
    zs = [corner[2] for corner in corners]
    return [[min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]]


def _level_name(element, doc, cache):
    level_id = None
    try:
        level_id = eid_to_int(element.LevelId)
    except Exception:
        level_id = None
    if level_id is None or level_id < 0:
        return u""
    if level_id in cache:
        return cache[level_id]
    name = u""
    try:
        level = doc.GetElement(element.LevelId)
        name = safe_text(level.Name) if level is not None else u""
    except Exception:
        name = u""
    cache[level_id] = name
    return name


# --------------------------------------------------------------- contexts


class LinkContext(object):
    """One document to read, and the instances that place it in the host."""

    def __init__(self, key, title, doc, is_host=False):
        self.key = key
        self.title = title
        self.doc = doc
        self.is_host = is_host
        self.loaded = doc is not None
        self.status = u""
        #: ``[(instance_id, instance, transform)]`` - transform None = identity
        self.instances = []
        self.type_cache = {}
        self.level_cache = {}

    def describe(self):
        return {
            "key": self.key,
            "title": self.title,
            "is_host": self.is_host,
            "loaded": bool(self.loaded),
            "status": self.status,
            "instance_count": len(self.instances),
        }


def _link_transform(instance):
    for method_name in ("GetTotalTransform", "GetTransform"):
        method = getattr(instance, method_name, None)
        if not callable(method):
            continue
        try:
            return method()
        except Exception:
            continue
    return None


def _link_status(instance, doc, db):
    try:
        link_type = doc.GetElement(instance.GetTypeId())
        status = enum_name(link_type.GetLinkedFileStatus())
        return status or u"not loaded"
    except Exception:
        return u"not loaded"


def _link_title(instance, link_doc):
    if link_doc is not None:
        try:
            title = safe_text(link_doc.Title)
            if title:
                return title
        except Exception:
            pass
    name = safe_text(getattr(instance, "Name", u""))
    return name.split(u":")[0].strip() or u"(Unnamed Link)"


def build_contexts(doc, db=None):
    """The host plus one context per linked document, instances attached."""
    db = _db(db)
    contexts = [LinkContext(HOST_KEY, HOST_KEY, doc, is_host=True)]
    contexts[0].instances.append((None, None, None))
    by_key = {}
    link_type = getattr(db, "RevitLinkInstance", None)
    instances = []
    if link_type is not None:
        try:
            instances = list(_collector(doc, db).OfClass(link_type)
                             .WhereElementIsNotElementType().ToElements())
        except Exception:
            instances = []
    for instance in instances:
        try:
            link_doc = instance.GetLinkDocument()
        except Exception:
            link_doc = None
        title = _link_title(instance, link_doc)
        context = by_key.get(title)
        if context is None:
            context = LinkContext(title, title, link_doc)
            if link_doc is None:
                context.status = _link_status(instance, doc, db)
            by_key[title] = context
            contexts.append(context)
        elif context.doc is None and link_doc is not None:
            context.doc = link_doc
            context.loaded = True
            context.status = u""
        transform = _link_transform(instance)
        conformal = True
        try:
            conformal = bool(transform.IsConformal) if transform is not None else True
        except Exception:
            conformal = True
        if not conformal:
            context.status = (context.status + u"; " if context.status else u"") + \
                u"an instance is scaled (not conformal) and was skipped"
            continue
        context.instances.append((eid_to_int(instance.Id), instance, transform))
    return contexts


# ---------------------------------------------------------------- catalog


def collect_catalog(context, db=None, include_floors=True):
    """What one document offers: wall/floor types with their string
    parameters, the line styles in use, and the levels."""
    db = _db(db)
    result = {"types": [], "line_styles": [], "levels": [], "rating_params": []}
    doc = context.doc
    if doc is None:
        return result

    type_rows = {}
    param_counts = {}
    for member_name, kind, category_label in BARRIER_CATEGORIES:
        if kind == "floor" and not include_floors:
            continue
        member = _builtin(db, "BuiltInCategory", member_name)
        if member is None:
            continue
        try:
            elements = list(_collector(doc, db).OfCategory(member)
                            .WhereElementIsNotElementType().ToElements())
        except Exception:
            continue
        for element in elements:
            element_type, type_id = _type_element(element, doc, context.type_cache)
            name = _type_name(element_type, element)
            wall_kind = _wall_kind(element) if kind == "wall" else "basic"
            key = u"{0}|{1}".format(kind, name)
            row = type_rows.get(key)
            if row is None:
                ratings = _string_params(element_type) if element_type is not None else {}
                row = {"key": key, "type_key": name, "kind": kind, "category": category_label,
                       "count": 0, "type_id": type_id if type_id is not None else -1,
                       "ratings": ratings, "curtain": wall_kind == "curtain",
                       "stacked": wall_kind == "stacked"}
                type_rows[key] = row
                for param_name in ratings:
                    param_counts[param_name] = param_counts.get(param_name, 0) + 1
            row["count"] += 1
    result["types"] = sorted(type_rows.values(), key=lambda row: (row["kind"], row["type_key"].lower()))
    result["rating_params"] = sorted(
        [{"name": name, "count": count} for name, count in param_counts.items()],
        key=lambda row: (-row["count"], row["name"].lower()))

    style_counts = {}
    member = _builtin(db, "BuiltInCategory", "OST_Lines")
    if member is not None:
        try:
            curves = list(_collector(doc, db).OfCategory(member)
                          .WhereElementIsNotElementType().ToElements())
        except Exception:
            curves = []
        for curve in curves:
            try:
                name = safe_text(curve.LineStyle.Name)
            except Exception:
                continue
            if name:
                style_counts[name] = style_counts.get(name, 0) + 1
    result["line_styles"] = sorted(
        [{"key": name, "count": count, "category": u"Line styles"} for name, count in style_counts.items()],
        key=lambda row: row["key"].lower())

    result["levels"] = _levels(doc, db)
    return result


def _levels(doc, db):
    level_type = getattr(db, "Level", None)
    if level_type is None:
        return []
    try:
        levels = list(_collector(doc, db).OfClass(level_type).WhereElementIsNotElementType().ToElements())
    except Exception:
        return []
    rows = []
    for level in levels:
        elevation = None
        for attr in ("ProjectElevation", "Elevation"):
            try:
                elevation = float(getattr(level, attr))
                break
            except Exception:
                continue
        if elevation is None:
            continue
        try:
            name = safe_text(level.Name)
        except Exception:
            name = u""
        rows.append({"id": eid_to_int(level.Id), "name": name, "elevation": elevation})
    rows.sort(key=lambda row: row["elevation"])
    return rows


# --------------------------------------------------------------- barriers


class BarrierIndex(object):
    """The rated barriers of every context, boxed in host space, with a
    spatial hash for candidate lookup and a lazy solid cache."""

    def __init__(self, db):
        self.db = db
        #: plain records, one per (instance, element)
        self.barriers = []
        #: ``key -> (context, instance transform, element)`` - never crosses back
        self._live = {}
        self._solids = {}
        self._unreadable = set()
        self._cells = {}
        self.skips = {"curtain_walls": 0, "stacked_parents": 0, "unloaded_links": [],
                      "unreadable": [], "solid_budget": 0}
        self.line_barriers = []
        self.line_skips = {"not_in_plan_view": 0, "no_level": 0, "capped": 0}

    # -- building ------------------------------------------------------------

    def add_context(self, context, choices, options):
        if context.doc is None:
            self.skips["unloaded_links"].append(u"{0} ({1})".format(context.title, context.status or u"not loaded"))
            return
        wall_types = set(choices.get("wall_types") or ())
        floor_types = set(choices.get("floor_types") or ())
        rating_param = safe_text(choices.get("rating_param")).strip()
        include_floors = bool(options.get("include_floors", True))
        outline = self._trim_outline(context, options)
        for member_name, kind, _label in BARRIER_CATEGORIES:
            if kind == "floor" and not include_floors:
                continue
            member = _builtin(self.db, "BuiltInCategory", member_name)
            if member is None:
                continue
            try:
                collector = _collector(context.doc, self.db).OfCategory(member).WhereElementIsNotElementType()
                if outline is not None:
                    collector = collector.WherePasses(self.db.BoundingBoxIntersectsFilter(outline))
                elements = list(collector.ToElements())
            except Exception:
                continue
            for element in elements:
                self._add_element(context, element, kind, wall_types if kind == "wall" else floor_types,
                                  rating_param)
        self._add_lines(context, set(choices.get("line_styles") or ()), options)

    def _trim_outline(self, context, options):
        """One indexed query per link: the host curves' union box in link space."""
        box = options.get("host_extent")
        if not box or context.is_host:
            return None
        transform = None
        for _instance_id, _instance, candidate in context.instances:
            transform = candidate
            break
        if len(context.instances) != 1 or transform is None:
            return None  # several placements: no single link-space box
        try:
            inverse = transform.Inverse
            corners = []
            for x_value in (box[0][0], box[1][0]):
                for y_value in (box[0][1], box[1][1]):
                    for z_value in (box[0][2], box[1][2]):
                        point = inverse.OfPoint(_make_xyz(self.db, (x_value, y_value, z_value)))
                        corners.append((point.X, point.Y, point.Z))
            low, high = _corners_to_box(corners)
            pad = BBOX_PAD_FT
            return self.db.Outline(_make_xyz(self.db, (low[0] - pad, low[1] - pad, low[2] - pad)),
                                   _make_xyz(self.db, (high[0] + pad, high[1] + pad, high[2] + pad)))
        except Exception:
            return None

    def _add_element(self, context, element, kind, ticked_types, rating_param):
        element_type, _type_id = _type_element(element, context.doc, context.type_cache)
        type_name = _type_name(element_type, element)
        wall_kind = _wall_kind(element) if kind == "wall" else "basic"
        if wall_kind == "curtain":
            self.skips["curtain_walls"] += 1
            return
        if wall_kind == "stacked":
            self.skips["stacked_parents"] += 1
            return
        rated_by_type = type_name in ticked_types
        owner_type = u""
        if wall_kind == "member" and not rated_by_type:
            try:
                owner = context.doc.GetElement(element.StackedWallOwnerId)
                owner_element_type, _oid = _type_element(owner, context.doc, context.type_cache)
                owner_type = _type_name(owner_element_type, owner)
                rated_by_type = owner_type in ticked_types
            except Exception:
                pass
        rating = u""
        if rating_param:
            rating = _lookup_string(element, rating_param)
            if not rating and element_type is not None:
                rating = _lookup_string(element_type, rating_param)
        if not rating and not rated_by_type:
            return
        element_id = eid_to_int(element.Id)
        thickness = _thickness(element, kind, self.db)
        level = _level_name(element, context.doc, context.level_cache)
        for instance_id, _instance, transform in context.instances:
            corners = _bbox_corners_host(element, transform, self.db)
            if corners is None:
                continue
            key = u"{0}|{1}|{2}|{3}".format(context.key, instance_id if instance_id is not None else u"host",
                                             kind, element_id)
            record = {
                "key": key,
                "context_key": context.key,
                "link_title": context.title,
                "instance_id": instance_id,
                "element_id": element_id,
                "kind": kind,
                "type_name": type_name,
                "owner_type": owner_type,
                "rating": rating,
                "rated_by": u"parameter" if rating else u"type",
                "thickness": float(thickness),
                "level": level,
                "box": _corners_to_box(corners),
                "label": u"{0} · {1}{2}".format(context.title, type_name,
                                                 u" (rated {0})".format(rating) if rating else u""),
            }
            self.barriers.append(record)
            self._live[key] = (context, transform, element)
            self._hash(len(self.barriers) - 1, record["box"])

    def _add_lines(self, context, styles, options):
        if not styles:
            return
        member = _builtin(self.db, "BuiltInCategory", "OST_Lines")
        if member is None:
            return
        try:
            curves = list(_collector(context.doc, self.db).OfCategory(member)
                          .WhereElementIsNotElementType().ToElements())
        except Exception:
            return
        levels = _levels(context.doc, self.db)
        band_mode = options.get("band_mode") or "next_level"
        band_height = float(options.get("band_height_ft") or 3000.0 * MM)
        min_storey = float(options.get("min_storey_ft") or 1500.0 * MM)
        thickness = float(options.get("line_thickness_ft") or 200.0 * MM)
        for curve in curves:
            if len(self.line_barriers) >= MAX_LINE_BARRIERS:
                self.line_skips["capped"] += 1
                continue
            try:
                style = safe_text(curve.LineStyle.Name)
            except Exception:
                continue
            if style not in styles:
                continue
            points = self._curve_points(curve)
            if len(points) < 2:
                continue
            base = None
            level_name = u""
            view_specific = False
            try:
                view_specific = bool(curve.ViewSpecific)
            except Exception:
                view_specific = False
            if view_specific:
                base, level_name, reason = self._detail_base(curve, context, levels)
                if base is None:
                    self.line_skips[reason] += 1
                    continue
            else:
                base = min(point[2] for point in points)
                for level in levels:
                    if abs(level["elevation"] - base) <= SNAP_LEVEL_FT:
                        base = level["elevation"]
                        level_name = level["name"]
                        break
            top = self._band_top(base, levels, band_mode, band_height, min_storey)
            element_id = eid_to_int(curve.Id)
            for instance_id, _instance, transform in context.instances:
                host_points = []
                for point in points:
                    host_points.append(self._to_host(transform, point))
                if any(point is None for point in host_points):
                    continue
                z0 = self._to_host(transform, (0.0, 0.0, base))
                z1 = self._to_host(transform, (0.0, 0.0, top))
                if z0 is None or z1 is None:
                    continue
                key = u"{0}|{1}|line|{2}".format(context.key, instance_id if instance_id is not None else u"host",
                                                  element_id)
                self.line_barriers.append({
                    "key": key,
                    "context_key": context.key,
                    "link_title": context.title,
                    "instance_id": instance_id,
                    "element_id": element_id,
                    "kind": "line",
                    "type_name": style,
                    "rating": u"",
                    "rated_by": u"line",
                    "thickness": thickness,
                    "level": level_name,
                    "points": host_points,
                    "z0": min(z0[2], z1[2]),
                    "z1": max(z0[2], z1[2]),
                    "label": u"{0} · line '{1}'".format(context.title, style),
                })

    def _curve_points(self, curve, cap=64):
        try:
            geometry = curve.GeometryCurve
        except Exception:
            return []
        if geometry is None:
            return []
        try:
            if enum_name(type(geometry).__name__) != u"Line":
                points = [_xyz(point) for point in geometry.Tessellate()]
                points = [point for point in points if point is not None]
                if len(points) >= 2:
                    return points[:cap]
        except Exception:
            pass
        try:
            ends = [_xyz(geometry.GetEndPoint(0)), _xyz(geometry.GetEndPoint(1))]
        except Exception:
            return []
        return [] if None in ends else ends

    def _detail_base(self, curve, context, levels):
        """``(base elevation, level name, skip reason)`` for a detail line."""
        try:
            view = context.doc.GetElement(curve.OwnerViewId)
        except Exception:
            view = None
        if view is None:
            return None, u"", "no_level"
        try:
            if enum_name(view.ViewType) not in PLAN_VIEW_TYPES:
                return None, u"", "not_in_plan_view"
        except Exception:
            pass
        level = None
        try:
            level = view.GenLevel
        except Exception:
            level = None
        if level is None:
            try:
                level = context.doc.GetElement(view.LevelId)
            except Exception:
                level = None
        if level is None:
            return None, u"", "no_level"
        level_id = eid_to_int(getattr(level, "Id", None))
        for row in levels:
            if row["id"] == level_id:
                return row["elevation"], row["name"], ""
        for attr in ("ProjectElevation", "Elevation"):
            try:
                return float(getattr(level, attr)), safe_text(level.Name), ""
            except Exception:
                continue
        return None, u"", "no_level"

    @staticmethod
    def _band_top(base, levels, band_mode, band_height, min_storey):
        if band_mode == "next_level":
            for level in levels:
                if level["elevation"] >= base + min_storey:
                    return level["elevation"]
        return base + band_height

    def _to_host(self, transform, point):
        if transform is None:
            return [float(point[0]), float(point[1]), float(point[2])]
        try:
            mapped = transform.OfPoint(_make_xyz(self.db, point))
            return [float(mapped.X), float(mapped.Y), float(mapped.Z)]
        except Exception:
            return None

    # -- spatial hash ----------------------------------------------------------

    def _hash(self, index, box):
        pad = BBOX_PAD_FT
        for cell in _cells(box[0][0] - pad, box[0][1] - pad, box[1][0] + pad, box[1][1] + pad):
            self._cells.setdefault(cell, []).append(index)

    def candidates(self, p0, p1):
        found = set()
        for cell in _cells(min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1])):
            for index in self._cells.get(cell, ()):
                found.add(index)
        return sorted(found)

    # -- solids ----------------------------------------------------------------

    def solids_for(self, key):
        """The element's solids in its own document's coordinates, cached."""
        if key in self._solids:
            return self._solids[key]
        if key in self._unreadable:
            return None
        if len(self._solids) >= MAX_SOLID_ELEMENTS:
            self.skips["solid_budget"] += 1
            return None
        live = self._live.get(key)
        if live is None:
            return None
        _context, _transform, element = live
        solids = _element_solids(element, self.db)
        if solids is None or not solids:
            self._unreadable.add(key)
            self.skips["unreadable"].append(key)
            return None
        self._solids[key] = solids
        return solids

    def transform_for(self, key):
        live = self._live.get(key)
        return live[1] if live is not None else None

    def mark_unreadable(self, key):
        self._solids.pop(key, None)
        if key not in self._unreadable:
            self._unreadable.add(key)
            self.skips["unreadable"].append(key)


def _cells(min_x, min_y, max_x, max_y):
    x0 = int(math.floor(min_x / CELL_FT))
    x1 = int(math.floor(max_x / CELL_FT))
    y0 = int(math.floor(min_y / CELL_FT))
    y1 = int(math.floor(max_y / CELL_FT))
    if (x1 - x0 + 1) * (y1 - y0 + 1) > 40000:
        # A degenerate box (a link placed kilometres away) must not
        # allocate the world; clamp to the corner cell and let the slab
        # test refuse it.
        return [(x0, y0)]
    cells = []
    for x_value in range(x0, x1 + 1):
        for y_value in range(y0, y1 + 1):
            cells.append((x_value, y_value))
    return cells


def _geometry_options(db):
    try:
        options = db.Options()
    except Exception:
        return None
    for attr, value in (("ComputeReferences", False), ("IncludeNonVisibleObjects", False)):
        try:
            setattr(options, attr, value)
        except Exception:
            pass
    try:
        options.DetailLevel = db.ViewDetailLevel.Fine
    except Exception:
        pass
    return options


def _collect_solids(db, geometry_element, results, depth=0):
    if geometry_element is None or depth > 4 or len(results) >= MAX_SOLIDS_PER_ELEMENT:
        return results
    try:
        iterator = iter(geometry_element)
    except Exception:
        return results
    solid_type = getattr(db, "Solid", None)
    instance_type = getattr(db, "GeometryInstance", None)
    for geometry_object in iterator:
        if len(results) >= MAX_SOLIDS_PER_ELEMENT:
            break
        try:
            if solid_type is not None and isinstance(geometry_object, solid_type):
                if float(geometry_object.Volume) > 1e-6:
                    results.append(geometry_object)
            elif instance_type is not None and isinstance(geometry_object, instance_type):
                _collect_solids(db, geometry_object.GetInstanceGeometry(), results, depth + 1)
        except Exception:
            continue
    return results


def _element_solids(element, db):
    """The solids, or None when the read failed (never "no solids" for that)."""
    try:
        geometry = element.get_Geometry(_geometry_options(db))
    except Exception:
        return None
    if geometry is None:
        return []
    return _collect_solids(db, geometry, [])


# ------------------------------------------------------------------ build


def build_index(contexts, choices_by_key, options, db=None):
    """Every rated barrier of every enabled context, boxed and hashed."""
    db = _db(db)
    index = BarrierIndex(db)
    for context in contexts:
        choices = choices_by_key.get(context.key)
        if not choices or not choices.get("enabled", True):
            continue
        index.add_context(context, choices, options)
    return index


def describe_index(index):
    """The plain half of the index: what the state layer and the notes read."""
    return {
        "barriers": [dict(record) for record in index.barriers],
        "line_barriers": [dict(record) for record in index.line_barriers],
        "skips": {
            "curtain_walls": index.skips["curtain_walls"],
            "stacked_parents": index.skips["stacked_parents"],
            "unloaded_links": list(index.skips["unloaded_links"]),
            "unreadable": list(index.skips["unreadable"]),
            "solid_budget": index.skips["solid_budget"],
            "lines_not_in_plan_view": index.line_skips["not_in_plan_view"],
            "lines_no_level": index.line_skips["no_level"],
            "lines_capped": index.line_skips["capped"],
        },
    }


# -------------------------------------------------------------- crossings


def _slab_overlaps(p0, p1, box):
    for axis in (0, 1, 2):
        low = min(p0[axis], p1[axis]) - BBOX_PAD_FT
        high = max(p0[axis], p1[axis]) + BBOX_PAD_FT
        if high < box[0][axis] or low > box[1][axis]:
            return False
    return True


def _param_along(p0, p1, point):
    dx, dy, dz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    length2 = dx * dx + dy * dy + dz * dz
    if length2 <= 0.0:
        return 0.0
    return ((point[0] - p0[0]) * dx + (point[1] - p0[1]) * dy + (point[2] - p0[2]) * dz) / length2


def _merge_intervals(intervals):
    intervals = sorted(intervals)
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _segment_length(p0, p1):
    return math.sqrt(sum((p1[axis] - p0[axis]) ** 2 for axis in (0, 1, 2)))


def _intersect_solid(db, solid, line):
    """``[(start point, end point)]`` inside the solid, in its own
    coordinates.  Raises when the solid refuses the call, so the caller can
    name that barrier unreadable and drop it."""
    options = db.SolidCurveIntersectionOptions()
    try:
        options.ResultType = db.SolidCurveIntersectionMode.CurveSegmentsInside
    except Exception:
        pass
    result = solid.IntersectWithCurve(line, options)
    pieces = []
    try:
        count = int(result.SegmentCount)
    except Exception:
        return pieces
    for index in range(count):
        try:
            segment = result.GetCurveSegment(index)
            pieces.append((_xyz(segment.GetEndPoint(0)), _xyz(segment.GetEndPoint(1))))
        except Exception:
            continue
    return [piece for piece in pieces if None not in piece]


def find_crossings(index, segments, options=None, db=None, progress=None, clock=None):
    """Every place a host segment passes through a rated solid barrier.

    ``segments``: ``[{"owner_id", "kind", "points": [p0, p1]}]`` in host
    coordinates.  Returns plain crossing dicts plus a ``meta`` block that
    says exactly how far the pass got.
    """
    db = _db(db)
    options = options or {}
    clock = clock or time.time
    started = clock()
    budget = float(options.get("budget_seconds") or DEFAULT_BUDGET_SECONDS)
    max_tests = int(options.get("max_candidates") or MAX_CANDIDATE_TESTS)
    max_crossings = int(options.get("max_crossings") or MAX_CROSSINGS)
    min_penetration = float(options.get("min_penetration_ft") or 10.0 * MM)

    items = []
    meta = {"segments": len(segments or []), "candidate_tests": 0, "solid_calls": 0,
            "seconds": 0.0, "truncated": False, "truncated_reason": u""}

    for number, segment in enumerate(segments or []):
        if number % PROGRESS_EVERY == 0 and number:
            if clock() - started > budget:
                meta["truncated"] = True
                meta["truncated_reason"] = u"{0:.0f} s budget reached".format(budget)
                break
            if progress is not None:
                try:
                    keep_going = progress(number, len(segments))
                except Exception:
                    keep_going = True
                if keep_going is False:
                    meta["truncated"] = True
                    meta["truncated_reason"] = u"cancelled"
                    break
        if len(items) >= max_crossings:
            meta["truncated"] = True
            meta["truncated_reason"] = u"{0:,} crossings cap reached".format(max_crossings)
            break
        points = segment.get("points") or []
        if len(points) != 2:
            continue
        p0, p1 = points
        if _segment_length(p0, p1) < MIN_SEGMENT_FT:
            continue
        for barrier_index in index.candidates(p0, p1):
            record = index.barriers[barrier_index]
            if not _slab_overlaps(p0, p1, record["box"]):
                continue
            meta["candidate_tests"] += 1
            if meta["candidate_tests"] > max_tests:
                meta["truncated"] = True
                meta["truncated_reason"] = u"{0:,} candidate tests cap reached".format(max_tests)
                break
            key = record["key"]
            solids = index.solids_for(key)
            if not solids:
                continue
            transform = index.transform_for(key)
            try:
                if transform is None:
                    q0, q1 = _make_xyz(db, p0), _make_xyz(db, p1)
                else:
                    inverse = transform.Inverse
                    q0 = inverse.OfPoint(_make_xyz(db, p0))
                    q1 = inverse.OfPoint(_make_xyz(db, p1))
                line = db.Line.CreateBound(q0, q1)
            except Exception:
                continue
            intervals = []
            failed = False
            for solid in solids:
                meta["solid_calls"] += 1
                try:
                    pieces = _intersect_solid(db, solid, line)
                except Exception:
                    failed = True
                    break
                for start, end in pieces:
                    if transform is not None:
                        try:
                            start = _xyz(transform.OfPoint(_make_xyz(db, start)))
                            end = _xyz(transform.OfPoint(_make_xyz(db, end)))
                        except Exception:
                            continue
                    if start is None or end is None:
                        continue
                    t_start = _param_along(p0, p1, start)
                    t_end = _param_along(p0, p1, end)
                    intervals.append((min(t_start, t_end), max(t_start, t_end)))
            if failed:
                index.mark_unreadable(key)
                continue
            total = _segment_length(p0, p1)
            for start, end in _merge_intervals(intervals):
                length = (end - start) * total
                if length < min_penetration:
                    continue
                mid = (start + end) / 2.0
                point = [p0[axis] + (p1[axis] - p0[axis]) * mid for axis in (0, 1, 2)]
                thickness = float(record["thickness"])
                touches_end = start <= 1e-6 or end >= 1.0 - 1e-6
                items.append({
                    "element_id": segment.get("owner_id"),
                    "element_kind": segment.get("kind"),
                    "barrier_key": key,
                    "point": point,
                    "length": length,
                    "thickness": thickness,
                    "partial": bool(touches_end and thickness > 0 and length < thickness - 10.0 * MM),
                    "along": bool(length > 3.0 * max(thickness, 100.0 * MM)),
                })
        if meta["truncated"]:
            break

    meta["seconds"] = clock() - started
    return {"items": items, "meta": meta}


def host_extent(segments):
    """The union box of the host segments, for the per-link trim."""
    xs, ys, zs = [], [], []
    for segment in segments or []:
        for point in segment.get("points") or []:
            xs.append(point[0])
            ys.append(point[1])
            zs.append(point[2])
    if not xs:
        return None
    return [[min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]]


# ------------------------------------------------------------------- show


def show_crossing(uidoc, show, contexts=None, db=None):
    """Select the host elements (and the linked barrier where Revit lets us)
    and frame the crossing point.  Returns True when anything was selected."""
    db = _db(db)
    if not uidoc or not show:
        return False
    doc = getattr(uidoc, "Document", None)
    to_eid = element_id_factory(db.ElementId)
    host_ids = [value for value in show.get("host_ids") or [] if value is not None]
    references = []
    host_elements = []
    for value in host_ids:
        try:
            element = doc.GetElement(to_eid(value))
        except Exception:
            element = None
        if element is not None:
            host_elements.append(element)

    link_instance_id = show.get("link_instance_id")
    link_element_id = show.get("link_element_id")
    if link_instance_id is not None and link_element_id is not None:
        try:
            instance = doc.GetElement(to_eid(link_instance_id))
            link_doc = instance.GetLinkDocument()
            element = link_doc.GetElement(to_eid(link_element_id))
            references.append(db.Reference(element).CreateLinkReference(instance))
        except Exception:
            pass

    selected = False
    if references:
        try:
            from System.Collections.Generic import List as ClrList

            reference_list = ClrList[db.Reference]()
            for reference in references:
                reference_list.Add(reference)
            for element in host_elements:
                reference_list.Add(db.Reference(element))
            uidoc.Selection.SetReferences(reference_list)
            selected = True
        except Exception:
            selected = False

    if not selected and host_elements:
        try:
            from System.Collections.Generic import List as ClrList

            id_list = ClrList[db.ElementId]()
            for element in host_elements:
                id_list.Add(element.Id)
            uidoc.Selection.SetElementIds(id_list)
            uidoc.ShowElements(id_list)
            selected = True
        except Exception:
            selected = False

    point = show.get("point")
    if point:
        _zoom_to_point(uidoc, point, db)
    return selected


def _zoom_to_point(uidoc, point, db, pad=5.0):
    try:
        active_view_id = uidoc.ActiveView.Id
        ui_view = None
        for candidate in uidoc.GetOpenUIViews():
            if candidate.ViewId == active_view_id:
                ui_view = candidate
                break
        if ui_view is None:
            return
        ui_view.ZoomAndCenterRectangle(
            _make_xyz(db, (point[0] - pad, point[1] - pad, point[2] - pad)),
            _make_xyz(db, (point[0] + pad, point[1] + pad, point[2] + pad)))
    except Exception:
        pass
