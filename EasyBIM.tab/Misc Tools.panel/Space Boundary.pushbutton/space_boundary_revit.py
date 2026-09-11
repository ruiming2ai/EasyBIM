# -*- coding: utf-8 -*-
"""Revit API layer for Space Boundary: read the rooms, draw the regions.

Everything that touches the API lives here, and nothing but ints, floats,
booleans and unicode crosses back into ``space_boundary_state`` - which is
what lets every verdict about drift be decided, and tested, without Revit.

Three things in here are worth knowing before reading the code.

**Boundaries are read once per room.**  A room drawn into five views needs
one ``GetBoundarySegments`` call, not five; on a two-thousand room job that
is the difference between two thousand reads and ten thousand.

**Loops are flattened to one Z before they are built.**  Revit's boundary
curves sit at the room's base plane, and a filled region lives in the view's
plane.  Flattening every point to the lowest Z of the loop makes the loop
provably planar in our own code, so a whole class of "not in the plane of the
view" refusals cannot happen, and the question of exactly which Z Revit used
stops mattering.

**The write is one undo step.**  One assimilated ``TransactionGroup`` for the
run, one ``Transaction`` per view, one ``SubTransaction`` per room - so a
room Revit refuses rolls back alone, cheaply, without a regeneration, and
without taking the other nineteen in that view with it.

``db`` is injectable so the desktop tests drive the adapter with fakes.
"""

from __future__ import print_function

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

import space_boundary_state as state
import space_boundary_storage as storage


MM = state.MM_PER_FOOT
#: Level keys are matched at centimetre precision, which is far tighter than
#: any real floor-to-floor and loose enough to survive a link's transform.
LEVEL_KEY_MM = 10.0

BUDGET_SECONDS = 600.0
PROGRESS_EVERY = 25


def _db(db):
    return db if db is not None else DB


def _builtin(db, enum_name, member_name):
    enum = getattr(db, enum_name, None) if db is not None else None
    return getattr(enum, member_name, None) if enum is not None else None


def _member_int(member):
    """A BuiltInCategory member as its integer id, or None."""
    if member is None:
        return None
    try:
        return int(member)
    except Exception:
        return None


def enum_name(value):
    text = safe_text(value)
    return text.rsplit(u".", 1)[1] if u"." in text else text


def _collector(doc, db, view_id=None):
    """A collector over the document - or, given a view id, over what that
    view shows: VG, filters, view range, phase filter and hidden elements
    all applied by Revit itself."""
    factory = getattr(db, "FilteredElementCollector", None)
    if factory is None or doc is None:
        return None
    try:
        if view_id is not None:
            return factory(doc, view_id)
        return factory(doc)
    except Exception:
        return None


def element_name(element, db=None):
    """An element's name, the way IronPython can actually read it.

    ``FilledRegionType`` and ``GraphicsStyle`` expose ``Name`` in a shape
    IronPython 2.7 cannot bind, so ``element.Name`` raises and a ``getattr``
    default quietly hands back an empty string - which is exactly how a
    combo box ends up full of blank rows.  ``Element.Name.GetValue`` is the
    repo's established way round it.
    """
    db = _db(db)
    if element is None:
        return u""
    try:
        name = safe_text(element.Name)
        if name:
            return name
    except Exception:
        pass
    element_class = getattr(db, "Element", None)
    getter = getattr(getattr(element_class, "Name", None), "GetValue", None)
    if getter is None:
        return u""
    try:
        return safe_text(getter(element))
    except Exception:
        return u""


def _param_string(element, db, bip_name):
    builtin = _builtin(db, "BuiltInParameter", bip_name)
    if builtin is None:
        return u""
    try:
        param = element.get_Parameter(builtin)
        return safe_text(param.AsString() or u"").strip() if param is not None else u""
    except Exception:
        return u""


def _param_element_id(element, db, bip_name):
    builtin = _builtin(db, "BuiltInParameter", bip_name)
    if builtin is None:
        return None
    try:
        param = element.get_Parameter(builtin)
        return eid_to_int(param.AsElementId()) if param is not None else None
    except Exception:
        return None


# ----------------------------------------------------------------- levels


def _levels(doc, db, transform=None):
    """``{level id: {"name", "elevation_mm"}}`` in host millimetres."""
    level_class = getattr(db, "Level", None)
    collector = _collector(doc, db)
    if level_class is None or collector is None:
        return {}
    try:
        elements = list(collector.OfClass(level_class).WhereElementIsNotElementType().ToElements())
    except Exception:
        return {}
    rows = {}
    for level in elements:
        elevation = None
        for attr in ("ProjectElevation", "Elevation"):
            try:
                elevation = float(getattr(level, attr))
                break
            except Exception:
                continue
        if elevation is None:
            continue
        if transform is not None:
            elevation = _transform_z(transform, elevation, db)
        rows[eid_to_int(level.Id)] = {"name": element_name(level, db),
                                      "elevation_mm": elevation * MM}
    return rows


def _transform_z(transform, elevation_ft, db):
    try:
        point = transform.OfPoint(db.XYZ(0.0, 0.0, float(elevation_ft)))
        return float(point.Z)
    except Exception:
        return float(elevation_ft)


def elevation_key(elevation_mm):
    return u"el:{0}".format(int(round(float(elevation_mm) / LEVEL_KEY_MM)))


def _id_key(value):
    return u"id:{0}".format(value)


# --------------------------------------------------------------- sources


def count_spatial(doc, kind, db=None):
    """How many rooms or spaces one document holds.

    The setup ticks a source that actually has some, which is what makes an
    MEP job work without being told: the spaces are in this model and the
    rooms are in the architectural link, and each run picks up whichever kind
    was asked for wherever it happens to live.
    """
    db = _db(db)
    member = _builtin(db, "BuiltInCategory",
                      "OST_MEPSpaces" if kind == state.KIND_SPACE else "OST_Rooms")
    collector = _collector(doc, db)
    if member is None or collector is None:
        return 0
    try:
        return len(list(collector.OfCategory(member).WhereElementIsNotElementType().ToElements()))
    except Exception:
        return 0


def collect_sources(doc, db=None):
    """This model plus every link instance, as rows the setup can tick.

    Each row carries its own document and transform, so reading rooms out of
    it later needs nothing looked up again, and both counts, so flipping
    Rooms to Spaces re-ticks the list without another pass over the model.
    """
    db = _db(db)
    rows = [{"key": state.HOST_KEY, "title": state.HOST_KEY, "uid": u"", "loaded": True,
             "status": u"", "is_host": True, "count": 0, "category": u"Sources",
             "doc": doc, "transform": None,
             "room_count": count_spatial(doc, state.KIND_ROOM, db=db),
             "space_count": count_spatial(doc, state.KIND_SPACE, db=db)}]
    link_class = getattr(db, "RevitLinkInstance", None)
    collector = _collector(doc, db)
    if link_class is None or collector is None:
        return rows
    try:
        instances = list(collector.OfClass(link_class).WhereElementIsNotElementType().ToElements())
    except Exception:
        instances = []
    # One link file placed twice is two sources, not one: each placement sits
    # at its own transform and gets its own regions.  The second placement is
    # suffixed so the two can be told apart - and so the first keeps the plain
    # title the settings file remembered it by.
    seen = {}
    for instance in instances:
        try:
            link_doc = instance.GetLinkDocument()
        except Exception:
            link_doc = None
        title = _link_title(instance, link_doc)
        seen[title] = seen.get(title, 0) + 1
        if seen[title] > 1:
            title = u"{0} ({1})".format(title, seen[title])
        uid = safe_text(getattr(instance, "UniqueId", u""))
        rows.append({
            "key": title, "title": title, "uid": uid, "loaded": link_doc is not None,
            "status": u"" if link_doc is not None else u"not loaded",
            "is_host": False, "count": 0, "category": u"Sources",
            "instance_id": eid_to_int(getattr(instance, "Id", None)),
            "instance": instance,
            "doc": link_doc, "transform": _link_transform(instance) if link_doc is not None else None,
            "room_count": count_spatial(link_doc, state.KIND_ROOM, db=db) if link_doc is not None else 0,
            "space_count": count_spatial(link_doc, state.KIND_SPACE, db=db) if link_doc is not None else 0,
        })
    return rows


def apply_kind(rows, kind):
    """Point each source row's ``count`` at the kind being converted."""
    field = "space_count" if kind == state.KIND_SPACE else "room_count"
    for row in rows or []:
        row["count"] = int(row.get(field) or 0)
    return rows


def rooms_from_sources(source_rows, kind, db=None, warnings_by_doc=None):
    """Rooms or spaces from every ticked source, in one list, in host terms.

    A room uid is prefixed with the link instance it came through, so two
    placements of one architectural link are two rooms here rather than one
    that overwrites the other.
    """
    db = _db(db)
    rows = []
    skips = []
    warnings_by_doc = warnings_by_doc if warnings_by_doc is not None else {}
    for source in source_rows or []:
        if not source.get("is_checked"):
            continue
        if not source.get("loaded", True) or source.get("doc") is None:
            skips.append({"key": source.get("key"), "title": source.get("title"),
                          "code": "link_not_loaded",
                          "reason": state.CODE_SENTENCES["link_not_loaded"]})
            continue
        link_doc = source["doc"]
        doc_key = id(link_doc)
        if doc_key not in warnings_by_doc:
            warnings_by_doc[doc_key] = redundant_room_ids(link_doc, db=db)
        found = collect_spatial(link_doc, kind, db=db,
                                link_uid=safe_text(source.get("uid")),
                                link_title=u"" if source.get("is_host") else safe_text(
                                    source.get("title")),
                                transform=source.get("transform"),
                                warnings=warnings_by_doc[doc_key])
        for row in found:
            if source.get("uid"):
                row["uid"] = u"{0}|{1}".format(source["uid"], row["uid"])
            row["source_key"] = safe_text(source.get("key"))
            rows.append(row)
    return {"rooms": rows, "skips": skips}


def _link_title(instance, link_doc):
    if link_doc is not None:
        try:
            title = safe_text(link_doc.Title)
            if title:
                return title
        except Exception:
            pass
    name = element_name(instance)
    return name.split(u":")[0].strip() or u"(Unnamed Link)"


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


# ---------------------------------------------------- boundary location


def resolved_boundary_location(doc, kind, db=None):
    """``(name, source, note)`` - what this model computes areas to.

    Read from the document's own Area and Volume Computation setting rather
    than assumed, because that is the rule Revit itself follows when it makes
    a space from a linked room.
    """
    db = _db(db)
    settings_class = getattr(db, "AreaVolumeSettings", None)
    type_enum = getattr(db, "SpatialElementType", None)
    if settings_class is None or type_enum is None:
        return "Finish", "default", u"This Revit build does not expose the setting; wall " \
                                    u"finish was assumed."
    member = getattr(type_enum, "Space" if kind == state.KIND_SPACE else "Room", None)
    if member is None:
        return "Finish", "default", u"This Revit build does not expose that spatial type; " \
                                    u"wall finish was assumed."
    try:
        settings = settings_class.GetAreaVolumeSettings(doc)
        name = enum_name(settings.GetSpatialElementBoundaryLocation(member))
    except Exception as ex:
        return "Finish", "default", u"The model's computation setting could not be read " \
                                    u"({0}); wall finish was assumed.".format(safe_text(ex))
    if name not in state.BOUNDARY_LOCATIONS:
        return "Finish", "default", u"The model reports an unfamiliar setting ({0}); wall " \
                                    u"finish was assumed.".format(name)
    return name, "document", u""


def _boundary_options(db, location_name):
    options_class = getattr(db, "SpatialElementBoundaryOptions", None)
    if options_class is None:
        return None
    try:
        options = options_class()
    except Exception:
        return None
    enum = getattr(db, "SpatialElementBoundaryLocation", None)
    member = getattr(enum, location_name, None) if enum is not None else None
    if member is not None:
        try:
            options.SpatialElementBoundaryLocation = member
        except Exception:
            pass
    return options


# ----------------------------------------------------------------- views


def collect_views(doc, db=None):
    """Every view the tool could draw into, with its level and phase."""
    db = _db(db)
    view_class = getattr(db, "View", None)
    collector = _collector(doc, db)
    if view_class is None or collector is None:
        return []
    try:
        elements = list(collector.OfClass(view_class).WhereElementIsNotElementType().ToElements())
    except Exception:
        return []
    levels = _levels(doc, db)
    rows = []
    for view in elements:
        try:
            is_template = bool(view.IsTemplate)
        except Exception:
            is_template = False
        view_type = enum_name(getattr(view, "ViewType", u""))
        ok, code, sentence = state.view_verdict(view_type, is_template)
        if not ok and code in ("view_is_template",):
            continue
        if view_type not in state.PLAN_VIEW_TYPES:
            continue
        level_id = _view_level_id(view)
        level = levels.get(level_id) or {}
        keys = []
        if level_id is not None:
            keys.append(_id_key(level_id))
        if level.get("elevation_mm") is not None:
            keys.append(elevation_key(level["elevation_mm"]))
        rows.append({
            "key": safe_text(getattr(view, "Name", u"")),
            "uid": safe_text(getattr(view, "UniqueId", u"")),
            "id": eid_to_int(getattr(view, "Id", None)),
            "name": safe_text(getattr(view, "Name", u"")),
            "view_type": view_type,
            "category": _view_type_label(view_type),
            "is_template": is_template,
            "level_id": level_id,
            "level_name": level.get("name", u""),
            "level_keys": keys,
            "phase_name": _view_phase_name(doc, view, db),
            "cut_mm": _view_cut_mm(doc, view, levels, db),
            "count": 1,
            "default_ticked": view_type not in state.UNTICKED_VIEW_TYPES,
        })
    rows.sort(key=lambda row: (row["category"], safe_text(row["name"]).lower()))
    return rows


def _view_cut_mm(doc, view, levels, db):
    """The height of the plan's cut plane, in host millimetres, or None.

    A room shows in a plan when the cut plane passes through its height;
    for a linked room displayed by the host view that is the only rule left
    to apply, so the plane is read here once per view.
    """
    plane_enum = getattr(db, "PlanViewPlane", None)
    cut = getattr(plane_enum, "CutPlane", None) if plane_enum is not None else None
    try:
        view_range = view.GetViewRange()
    except Exception:
        view_range = None
    if view_range is None or cut is None:
        return None
    try:
        level_id = eid_to_int(view_range.GetLevelId(cut))
        offset = float(view_range.GetOffset(cut))
    except Exception:
        return None
    level = levels.get(level_id)
    if level is None:
        # A sentinel (Level Above/Below, Unlimited) or an unknown level: the
        # view's own level is the only honest fallback.
        level = levels.get(_view_level_id(view))
    if level is None or level.get("elevation_mm") is None:
        return None
    return float(level["elevation_mm"]) + offset * MM


def _view_type_label(view_type):
    return {"FloorPlan": u"Floor plans", "CeilingPlan": u"Ceiling plans",
            "EngineeringPlan": u"Engineering plans",
            "AreaPlan": u"Area plans"}.get(view_type, view_type)


def _view_level_id(view):
    for attr in ("GenLevel", "LevelId"):
        try:
            value = getattr(view, attr)
        except Exception:
            continue
        if value is None:
            continue
        key = eid_to_int(getattr(value, "Id", None)) if attr == "GenLevel" else eid_to_int(value)
        if key not in (None, -1):
            return key
    return None


def _view_phase_name(doc, view, db):
    phase_id = _param_element_id(view, db, "VIEW_PHASE")
    if phase_id is None or phase_id < 0:
        return u""
    try:
        phase = doc.GetElement(element_id_factory(db.ElementId)(phase_id))
        return safe_text(getattr(phase, "Name", u""))
    except Exception:
        return u""


def active_view_row(doc, views, db=None):
    """The row for the active view, when it is one the tool can draw into."""
    try:
        active_uid = safe_text(doc.ActiveView.UniqueId)
    except Exception:
        return None
    for row in views or []:
        if row.get("uid") == active_uid:
            return row
    return None


# ------------------------------------------------------- rooms and spaces


def collect_spatial(doc, kind, db=None, link_uid=u"", link_title=u"", transform=None,
                    warnings=None):
    """Rooms or spaces from one document, as plain dicts in host terms."""
    db = _db(db)
    member = _builtin(db, "BuiltInCategory",
                      "OST_MEPSpaces" if kind == state.KIND_SPACE else "OST_Rooms")
    collector = _collector(doc, db)
    if member is None or collector is None:
        return []
    try:
        elements = list(collector.OfCategory(member).WhereElementIsNotElementType().ToElements())
    except Exception:
        return []
    levels = _levels(doc, db, transform=transform)
    warnings = warnings or set()
    rows = []
    for element in elements:
        element_id = eid_to_int(getattr(element, "Id", None))
        level_id = None
        try:
            level_id = eid_to_int(element.LevelId)
        except Exception:
            level_id = None
        level = levels.get(level_id) or {}
        if link_uid:
            level_key = elevation_key(level.get("elevation_mm", 0.0))
        else:
            level_key = _id_key(level_id) if level_id is not None else None
        placed = True
        try:
            placed = element.Location is not None
        except Exception:
            placed = False
        base_mm, top_mm = _vertical_extent(element, level, levels)
        rows.append({
            "uid": safe_text(getattr(element, "UniqueId", u"")),
            "id": element_id,
            "element": element,
            "number": _param_string(element, db, "ROOM_NUMBER"),
            "name": _param_string(element, db, "ROOM_NAME"),
            "placed": placed,
            "area": _area(element),
            "level_key": level_key,
            "level_name": level.get("name", u""),
            "base_mm": base_mm,
            "top_mm": top_mm,
            "phase_name": _phase_name(doc, element, db),
            "design_option": _design_option(element),
            "in_group": _in_group(element),
            "link_uid": safe_text(link_uid),
            "link_title": safe_text(link_title),
            # Carried on the row so the boundary read can put a linked room's
            # loops into host coordinates without being told twice.
            "transform": transform,
            "redundant": element_id in warnings,
        })
    return rows


def _vertical_extent(element, level, levels):
    """``(base_mm, top_mm)`` in host millimetres, or ``(None, None)``.

    Base level plus base offset up to the upper limit plus its offset - or
    the unbounded height when no upper limit is set - which is the height a
    plan's cut plane has to pass through for the room to show.
    """
    elevation = (level or {}).get("elevation_mm")
    if elevation is None:
        return None, None
    try:
        base = float(elevation) + float(element.BaseOffset) * MM
    except Exception:
        return None, None
    top = None
    try:
        upper = element.UpperLimit
    except Exception:
        upper = None
    if upper is not None:
        upper_level = levels.get(eid_to_int(getattr(upper, "Id", None))) or {}
        if upper_level.get("elevation_mm") is not None:
            try:
                top = float(upper_level["elevation_mm"]) + float(element.LimitOffset) * MM
            except Exception:
                top = None
    if top is None:
        try:
            top = base + float(element.UnboundedHeight) * MM
        except Exception:
            return base, None
    return base, top


def _area(element):
    try:
        return float(element.Area)
    except Exception:
        return 0.0


def _phase_name(doc, element, db):
    for bip in ("ROOM_PHASE", "PHASE_CREATED"):
        phase_id = _param_element_id(element, db, bip)
        if phase_id is None or phase_id < 0:
            continue
        try:
            phase = doc.GetElement(element_id_factory(db.ElementId)(phase_id))
            name = safe_text(getattr(phase, "Name", u""))
            if name:
                return name
        except Exception:
            continue
    return u""


def _design_option(element):
    try:
        option = element.DesignOption
        return safe_text(getattr(option, "Name", u"")) if option is not None else u""
    except Exception:
        return u""


def _in_group(element):
    try:
        return eid_to_int(element.GroupId) not in (None, -1)
    except Exception:
        return False


def redundant_room_ids(doc, db=None):
    """Ids Revit itself is complaining about, so a skip can say which."""
    db = _db(db)
    found = set()
    try:
        warnings = list(doc.GetWarnings())
    except Exception:
        return found
    for warning in warnings:
        try:
            text = safe_text(warning.GetDescriptionText()).lower()
        except Exception:
            continue
        if u"redundant" not in text and u"not enclosed" not in text:
            continue
        try:
            for element_id in warning.GetFailingElements():
                value = eid_to_int(element_id)
                if value is not None:
                    found.add(value)
        except Exception:
            continue
    return found


# ------------------------------------------------------------ boundaries


def short_curve_mm(document):
    """The shortest curve this Revit will build, in millimetres, with margin.

    Read from the session rather than assumed: a segment under it is dropped
    by the loop repair, and a bridge under it is never inserted, because
    ``Line.CreateBound`` refuses both and the whole room with them.
    """
    try:
        value = float(document.Application.ShortCurveTolerance) * MM
    except Exception:
        return state.SHORT_MM
    if value <= 0.0:
        return state.SHORT_MM
    return value * 1.05


def read_boundaries(room, location_name, db=None, transform=None, grid_mm=state.GRID_MM):
    """One room's boundary as loops of curve records, in host millimetres."""
    db = _db(db)
    element = room.get("element")
    short_mm = short_curve_mm(getattr(element, "Document", None))
    gap_mm = max(state.GAP_BUDGET_MM, 2.0 * short_mm)
    options = _boundary_options(db, location_name)
    if element is None or options is None:
        return {"loops": [], "code": "boundary_unreadable",
                "sentence": state.sentence_for("boundary_unreadable", u"no boundary options")}
    try:
        raw_loops = list(element.GetBoundarySegments(options))
    except Exception as ex:
        return {"loops": [], "code": "boundary_unreadable",
                "sentence": state.sentence_for("boundary_unreadable", safe_text(ex))}
    if not raw_loops:
        return {"loops": [], "code": "not_enclosed", "sentence": state.CODE_SENTENCES["not_enclosed"]}
    if len(raw_loops) > state.MAX_LOOPS_PER_ROOM:
        return {"loops": [], "code": "too_many_loops",
                "sentence": state.CODE_SENTENCES["too_many_loops"]}

    loops = []
    notes = []
    for raw in raw_loops:
        records = []
        for segment in raw:
            record = _segment_record(segment, db, transform)
            if record is not None:
                records.append(record)
        repaired, code, sentence, loop_notes = state.repair_loop(records, gap_budget_mm=gap_mm,
                                                                 short_mm=short_mm)
        notes.extend(loop_notes)
        if code:
            return {"loops": [], "code": code, "sentence": sentence}
        points = state.tessellate_loop(repaired)
        if len(points) > state.MAX_VERTICES_PER_LOOP:
            return {"loops": [], "code": "too_many_vertices",
                    "sentence": state.CODE_SENTENCES["too_many_vertices"]}
        canonical = state.canonical_loop(points, grid_mm)
        if len(canonical) < 3:
            continue
        intersecting, _tested = state.is_self_intersecting(canonical)
        if intersecting:
            return {"loops": [], "code": "self_intersecting",
                    "sentence": state.CODE_SENTENCES["self_intersecting"]}
        if abs(state.signed_area2(canonical)) / 2.0 * grid_mm * grid_mm < state.MIN_LOOP_AREA_MM2:
            notes.append("sliver_dropped")
            continue
        loops.append({"records": repaired, "points": points, "short_mm": short_mm})

    if not loops:
        return {"loops": [], "code": "sliver", "sentence": state.CODE_SENTENCES["sliver"]}
    point_loops = [loop["points"] for loop in loops]
    return {"loops": loops, "code": "", "sentence": u"", "notes": notes,
            "fingerprints": state.fingerprints(point_loops, grid_mm)}


def read_boundary_map(rooms, location_name, db=None, grid_mm=state.GRID_MM, progress=None,
                      clock=None):
    """``{room uid: boundary}`` for a whole run, read once per room.

    ``build_plan`` looks boundaries up by room uid, so a room drawn into five
    views costs one ``GetBoundarySegments`` call rather than five.  On a two
    thousand room job across five plans that is two thousand reads instead of
    ten thousand, which is the whole difference in how long a run takes.

    Each room carries its own link transform, so a linked room's boundary
    lands in host millimetres here and every comparison downstream is done in
    one coordinate system.
    """
    db = _db(db)
    clock = clock or time.time
    started = clock()
    boundaries = {}
    cancelled = False
    total = len(rooms or [])
    for index, room in enumerate(rooms or []):
        uid = room.get("uid")
        if uid in boundaries:
            continue
        if index and index % PROGRESS_EVERY == 0:
            if clock() - started > BUDGET_SECONDS:
                cancelled = True
            elif progress is not None:
                try:
                    if progress(index, total) is False:
                        cancelled = True
                except Exception:
                    pass
        if cancelled:
            boundaries[uid] = {"loops": [], "code": "cancelled",
                               "sentence": state.CODE_SENTENCES["cancelled"]}
            continue
        boundaries[uid] = read_boundaries(room, location_name, db=db,
                                          transform=room.get("transform"), grid_mm=grid_mm)
    return {"boundaries": boundaries, "cancelled": cancelled, "seconds": clock() - started}


def _segment_record(segment, db, transform):
    """One boundary segment as the record the state layer understands."""
    try:
        curve = segment.GetCurve()
    except Exception:
        return None
    if curve is None:
        return None
    try:
        start = _point_mm(curve.GetEndPoint(0), db, transform)
        end = _point_mm(curve.GetEndPoint(1), db, transform)
    except Exception:
        return None
    if start is None or end is None:
        return None
    arc_class = getattr(db, "Arc", None)
    try:
        is_arc = arc_class is not None and isinstance(curve, arc_class)
    except Exception:
        is_arc = False
    if is_arc:
        try:
            middle = _point_mm(curve.Evaluate(0.5, True), db, transform)
            if middle is not None:
                return ("A", start[0], start[1], end[0], end[1], middle[0], middle[1])
        except Exception:
            pass
    line_class = getattr(db, "Line", None)
    try:
        is_line = line_class is not None and isinstance(curve, line_class)
    except Exception:
        is_line = False
    if not is_line:
        try:
            points = [_point_mm(point, db, transform) for point in curve.Tessellate()]
            points = [point for point in points if point is not None]
            if len(points) >= 2:
                return ("T", points)
        except Exception:
            pass
    return ("L", start[0], start[1], end[0], end[1])


def _point_mm(point, db, transform):
    try:
        if transform is not None:
            point = transform.OfPoint(point)
        return (float(point.X) * MM, float(point.Y) * MM)
    except Exception:
        return None


# ------------------------------------------------------- region geometry


def collect_region_types(doc, db=None):
    """The filled region types this model can draw with."""
    db = _db(db)
    type_class = getattr(db, "FilledRegionType", None)
    collector = _collector(doc, db)
    if type_class is None or collector is None:
        return []
    try:
        elements = list(collector.OfClass(type_class).ToElements())
    except Exception:
        return []
    rows = []
    for element in elements:
        rows.append({"id": eid_to_int(getattr(element, "Id", None)),
                     "name": element_name(element, db)})
    rows.sort(key=lambda row: safe_text(row["name"]).lower())
    return rows


def collect_line_styles(doc, db=None):
    """The line styles a filled region's boundary may take.

    The invisible one is found by its built-in category rather than by name,
    because ``<Invisible lines>`` is a different string in every language
    Revit ships in; the user's own choice is remembered by name, which only
    has to survive within one office.
    """
    db = _db(db)
    region_class = getattr(db, "FilledRegion", None)
    getter = getattr(region_class, "GetValidLineStyleIdsForFilledRegion", None)
    if getter is None or doc is None:
        return []
    try:
        ids = list(getter(doc))
    except Exception:
        return []
    invisible_key = _member_int(_builtin(db, "BuiltInCategory", "OST_InvisibleLines"))
    rows = []
    for style_id in ids:
        try:
            style = doc.GetElement(style_id)
        except Exception:
            style = None
        if style is None:
            continue
        category = None
        try:
            category = style.GraphicsStyleCategory
        except Exception:
            category = None
        name = element_name(category, db) if category is not None else u""
        if not name:
            name = element_name(style, db)
        category_key = eid_to_int(getattr(category, "Id", None)) if category is not None else None
        rows.append({"id": eid_to_int(style_id), "name": name,
                     "is_invisible": invisible_key is not None and category_key == invisible_key})
    rows.sort(key=lambda row: (not row["is_invisible"], safe_text(row["name"]).lower()))
    return rows


# ------------------------------------------------------------- visibility


def _visible_ids(doc, view_id, member, db):
    """Ids of one category the view shows, or None when it cannot be read."""
    if member is None:
        return None
    collector = _collector(doc, db, element_id_factory(db.ElementId)(view_id))
    if collector is None:
        return None
    try:
        ids = collector.OfCategory(member).WhereElementIsNotElementType().ToElementIds()
    except Exception:
        return None
    found = set()
    for element_id in ids:
        value = eid_to_int(element_id)
        if value is not None:
            found.add(value)
    return found


def _category_hidden(view, member, db):
    key = _member_int(member)
    if key is None:
        return False
    try:
        return bool(view.GetCategoryHidden(element_id_factory(db.ElementId)(key)))
    except Exception:
        return False


def _link_mode(view, instance):
    """``(mode name, linked view id)`` - how the view displays the link."""
    try:
        overrides = view.GetLinkOverrides(instance.Id)
    except Exception:
        overrides = None
    if overrides is None:
        return "ByHostView", None
    mode = enum_name(getattr(overrides, "LinkVisibilityType", u"")) or "ByHostView"
    linked_view_id = None
    try:
        linked_view_id = eid_to_int(overrides.LinkedViewId)
    except Exception:
        linked_view_id = None
    if linked_view_id is not None and linked_view_id < 0:
        linked_view_id = None
    return mode, linked_view_id


def _link_hidden(view, instance, db):
    try:
        if bool(instance.CanBeHidden(view)) and bool(instance.IsHidden(view)):
            return True
    except Exception:
        pass
    links_category = _builtin(db, "BuiltInCategory", "OST_RvtLinks")
    return _category_hidden(view, links_category, db) if links_category is not None else False


def _linked_visible_uids(doc, view, view_row, source, rooms, member, db,
                         include_design_options=False):
    """``(set of room uids or None, note)`` for one link in one view.

    Revit cannot be asked what a host view shows of a link, so the link's
    display mode decides which rule applies.  By linked view: the linked
    view itself is asked, exactly.  By host view: the host view's rules are
    applied to the linked rooms one by one - category, phase, design option
    and whether the cut plane passes through the room.
    """
    instance = source.get("instance")
    link_doc = source.get("doc")
    if instance is None or link_doc is None:
        return set(), u""
    if _link_hidden(view, instance, db):
        return set(), state.sentence_for("link_hidden", source.get("title"), view_row.get("name"))
    mode, linked_view_id = _link_mode(view, instance)
    if mode in ("ByLinkView", "Custom") and linked_view_id is not None:
        ids = _visible_ids(link_doc, linked_view_id, member, db)
        if ids is None:
            return None, state.sentence_for("visibility_unknown",
                                            u"{0} through {1}".format(view_row.get("name"),
                                                                      source.get("title")))
        return set(room["uid"] for room in rooms if room.get("id") in ids), u""

    if _category_hidden(view, member, db):
        return set(), state.sentence_for(
            "rooms_hidden_in_view",
            u"Spaces" if enum_name(member) == "OST_MEPSpaces" else u"Rooms",
            view_row.get("name"), source.get("title"))
    cut_mm = view_row.get("cut_mm")
    if cut_mm is None:
        return None, state.sentence_for("visibility_unknown", view_row.get("name"))
    view_phase = safe_text(view_row.get("phase_name"))
    visible = set()
    for room in rooms:
        room_phase = safe_text(room.get("phase_name"))
        if view_phase and room_phase and view_phase != room_phase:
            continue
        if safe_text(room.get("design_option")) and not include_design_options:
            continue
        if room.get("base_mm") is None or room.get("top_mm") is None:
            continue
        if state.cut_plane_visible(room["base_mm"], room["top_mm"], cut_mm):
            visible.add(room["uid"])
    return visible, u""


def visibility_map(doc, views, rooms, sources, kind, db=None, include_design_options=False):
    """``{view uid: {"visible": set of room uids or None, "note"}}``.

    One pass per view.  This model's rooms come from a view-filtered
    collector, which is Revit's own answer to "what does this view show";
    each link's rooms come from ``_linked_visible_uids``.  ``None`` means the
    view could not be read at all, and the plan then falls back to the level
    rule and says so - a room is never dropped by an unanswered question.
    """
    db = _db(db)
    member = _builtin(db, "BuiltInCategory",
                      "OST_MEPSpaces" if kind == state.KIND_SPACE else "OST_Rooms")
    to_eid = element_id_factory(db.ElementId) if getattr(db, "ElementId", None) else None
    by_source = {}
    for room in rooms or []:
        by_source.setdefault(safe_text(room.get("link_uid")), []).append(room)
    link_sources = dict((safe_text(row.get("uid")), row) for row in sources or []
                        if row.get("uid") and row.get("is_checked", True))

    result = {}
    for view_row in views or []:
        view_uid = safe_text(view_row.get("uid"))
        notes = []
        visible = set()
        unknown = False
        host_rooms = by_source.get(u"") or []
        if host_rooms:
            ids = _visible_ids(doc, view_row.get("id"), member, db)
            if ids is None:
                unknown = True
                notes.append(state.sentence_for("visibility_unknown", view_row.get("name")))
            else:
                visible.update(room["uid"] for room in host_rooms if room.get("id") in ids)
        view = None
        if any(uid for uid in by_source if uid):
            try:
                view = doc.GetElement(to_eid(view_row.get("id"))) if to_eid else None
            except Exception:
                view = None
        for link_uid, link_rooms in by_source.items():
            if not link_uid:
                continue
            source = link_sources.get(link_uid)
            if source is None or view is None:
                unknown = True
                notes.append(state.sentence_for("visibility_unknown", view_row.get("name")))
                continue
            found, note = _linked_visible_uids(doc, view, view_row, source, link_rooms, member,
                                               db, include_design_options)
            if note:
                notes.append(note)
            if found is None:
                unknown = True
                continue
            visible.update(found)
        result[view_uid] = {"visible": None if unknown else visible,
                            "note": u" ".join(notes)}
    return result


def _materialise_loops(db, loops, z_ft):
    """Curve records in millimetres -> ``CurveLoop`` objects at one height.

    Arcs are kept as arcs, so the drawn region matches the room rather than a
    polyline approximation of it; the fingerprint tessellates both sides the
    same way, so comparison is unaffected.
    """
    from System.Collections.Generic import List as ClrList

    result = ClrList[db.CurveLoop]()
    for loop in loops or []:
        curve_loop = db.CurveLoop()
        for record in loop.get("records") or []:
            for curve in _curves_for(db, record, z_ft):
                curve_loop.Append(curve)
        result.Add(curve_loop)
    return result


def _materialise_simplified(db, loops, z_ft):
    """The fallback: straight edges through the cleaned outline of each loop."""
    from System.Collections.Generic import List as ClrList

    result = ClrList[db.CurveLoop]()
    for loop in loops or []:
        points = state.simplified_outline(loop.get("points") or [],
                                          loop.get("short_mm") or state.SHORT_MM)
        if len(points) < 3:
            return None
        curve_loop = db.CurveLoop()
        for index in range(len(points)):
            a = points[index]
            b = points[(index + 1) % len(points)]
            curve_loop.Append(db.Line.CreateBound(_xyz(db, a[0], a[1], z_ft),
                                                  _xyz(db, b[0], b[1], z_ft)))
        result.Add(curve_loop)
    return result


def _curves_for(db, record, z_ft):
    kind = record[0]
    if kind == "A":
        start = _xyz(db, record[1], record[2], z_ft)
        end = _xyz(db, record[3], record[4], z_ft)
        middle = _xyz(db, record[5], record[6], z_ft)
        try:
            return [db.Arc.Create(start, middle, end)]
        except Exception:
            return [db.Line.CreateBound(start, end)]
    if kind == "T":
        points = record[1] or []
        curves = []
        for index in range(len(points) - 1):
            a = _xyz(db, points[index][0], points[index][1], z_ft)
            b = _xyz(db, points[index + 1][0], points[index + 1][1], z_ft)
            curves.append(db.Line.CreateBound(a, b))
        return curves
    return [db.Line.CreateBound(_xyz(db, record[1], record[2], z_ft),
                                _xyz(db, record[3], record[4], z_ft))]


def _xyz(db, x_mm, y_mm, z_ft):
    return db.XYZ(float(x_mm) / MM, float(y_mm) / MM, float(z_ft))


def read_region_loops(region, db=None, transform=None, grid_mm=state.GRID_MM):
    """A filled region's own outline, as point loops in millimetres.

    ``GetBoundaries`` is Revit 2022 and later, which is why the bundle asks
    for 2022: reading a region's own outline is the only way to notice that
    somebody reshaped it by hand.
    """
    db = _db(db)
    getter = getattr(region, "GetBoundaries", None)
    if not callable(getter):
        return None
    try:
        loops = list(getter())
    except Exception:
        return None
    points_loops = []
    for loop in loops:
        points = []
        try:
            curves = list(loop)
        except Exception:
            continue
        for curve in curves:
            record = _curve_record(curve, db, transform)
            if record is not None:
                points.extend(state.tessellate_loop([record]))
        if len(points) >= 3:
            points_loops.append(points)
    return points_loops


def _curve_record(curve, db, transform):
    try:
        start = _point_mm(curve.GetEndPoint(0), db, transform)
        end = _point_mm(curve.GetEndPoint(1), db, transform)
    except Exception:
        return None
    if start is None or end is None:
        return None
    arc_class = getattr(db, "Arc", None)
    try:
        if arc_class is not None and isinstance(curve, arc_class):
            middle = _point_mm(curve.Evaluate(0.5, True), db, transform)
            if middle is not None:
                return ("A", start[0], start[1], end[0], end[1], middle[0], middle[1])
    except Exception:
        pass
    return ("L", start[0], start[1], end[0], end[1])


def read_relationships(doc, db=None, grid_mm=state.GRID_MM):
    """Every tracked region, with its record and its outline as it is now."""
    db = _db(db)
    rows = []
    for entry in storage.read_all(doc, db=db):
        region = entry["element"]
        record = entry["record"]
        loops = read_region_loops(region, db=db, grid_mm=record.get("grid_mm") or grid_mm)
        owner_view_id = None
        try:
            owner_view_id = eid_to_int(region.OwnerViewId)
        except Exception:
            owner_view_id = None
        owner_view_uid = u""
        if owner_view_id is not None:
            try:
                owner = doc.GetElement(element_id_factory(db.ElementId)(owner_view_id))
                owner_view_uid = safe_text(getattr(owner, "UniqueId", u""))
            except Exception:
                owner_view_uid = u""
        rows.append({
            "region": region,
            "region_id": eid_to_int(getattr(region, "Id", None)),
            "region_uid": safe_text(getattr(region, "UniqueId", u"")),
            "owner_view_id": owner_view_id,
            "owner_view_uid": owner_view_uid,
            "record": record,
            "readable": loops is not None,
            "fingerprints": state.fingerprints(loops or [], record.get("grid_mm") or grid_mm),
        })
    return rows


def rooms_now_map(rooms, locations, db=None, grid_mm=state.GRID_MM, progress=None,
                  clock=None):
    """``{room_key: room + by_location fingerprints}`` for the drift pass.

    A record remembers the boundary location its region was drawn at, so the
    room is recomputed at *that* location.  Changing the model's Area and
    Volume setting after a run must not turn every region in the job into a
    false drift, and the only way to promise that is to measure the room the
    same way twice.
    """
    db = _db(db)
    wanted = [name for name in (locations or ["Finish"]) if name in state.BOUNDARY_LOCATIONS]
    if not wanted:
        wanted = ["Finish"]
    result = {}
    for name in wanted:
        read = read_boundary_map(rooms, name, db=db, grid_mm=grid_mm, progress=progress,
                                 clock=clock)
        for room in rooms or []:
            key = state.room_key(room.get("link_uid"), room.get("uid"))
            entry = result.get(key)
            if entry is None:
                entry = dict(room)
                entry["by_location"] = {}
                result[key] = entry
            boundary = read["boundaries"].get(room.get("uid")) or {}
            entry["by_location"][name] = boundary.get("fingerprints") or {}
            if entry.get("fingerprints") is None:
                entry["fingerprints"] = boundary.get("fingerprints") or {}
    return result


def views_now_map(views):
    """``{view uid: view}`` - what the drift pass matches a record against."""
    return dict((row.get("uid"), row) for row in views or [] if row.get("uid"))


# ------------------------------------------------------------- the write


def _view_z_ft(doc, view, db):
    """The height the loops are flattened to: the view's own level."""
    for attr in ("GenLevel",):
        try:
            level = getattr(view, attr)
        except Exception:
            level = None
        if level is None:
            continue
        for name in ("ProjectElevation", "Elevation"):
            try:
                return float(getattr(level, name))
            except Exception:
                continue
    return 0.0


def create_regions(doc, plan, checked_keys=None, db=None, progress=None, clock=None,
                   created_utc=u""):
    """Draw the planned regions and write each one's record beside it.

    One assimilated ``TransactionGroup`` for the run, one ``Transaction`` per
    view, one ``SubTransaction`` per room: a room Revit refuses rolls back on
    its own without costing the rest of the view, and the whole run is still
    a single undo step.
    """
    db = _db(db)
    clock = clock or time.time
    started = clock()
    checked = set(checked_keys) if checked_keys is not None else None
    items = [item for item in (plan or {}).get("items") or []
             if checked is None or item["key"] in checked]

    result = {"created": 0, "replaced": 0, "failed": [], "skipped": [],
              "views_done": 0, "cancelled": False, "seconds": 0.0, "error": u""}
    if not items:
        return result

    by_view = {}
    for item in items:
        by_view.setdefault(item["view_id"], []).append(item)

    to_eid = element_id_factory(db.ElementId)
    region_type_id = to_eid((plan.get("region_type") or {}).get("id"))
    line_style_id = None
    if (plan.get("line_style") or {}).get("id") is not None:
        line_style_id = to_eid(plan["line_style"]["id"])
    result["notes"] = []

    group = db.TransactionGroup(doc, u"Space Boundary")
    try:
        group.Start()
    except Exception as ex:
        result["error"] = safe_text(ex)
        return result

    done = 0
    try:
        for view_id, view_items in by_view.items():
            view = doc.GetElement(to_eid(view_id))
            if view is None:
                for item in view_items:
                    result["skipped"].append({"key": item["key"], "title": item["title"],
                                              "code": "view_no_detail_items",
                                              "reason": state.CODE_SENTENCES["view_no_detail_items"]})
                continue
            reason = storage.checkout_reason(doc, getattr(view, "Id", None), db=db)
            if reason:
                for item in view_items:
                    result["skipped"].append({"key": item["key"], "title": item["title"],
                                              "code": "owned_by_other", "reason": reason})
                continue

            before = (result["created"], result["replaced"])
            z_ft = _view_z_ft(doc, view, db)
            transaction = db.Transaction(doc, u"Space Boundary: {0}".format(
                safe_text(getattr(view, "Name", u"view"))))
            transaction.Start()
            try:
                drawn = []
                for item in view_items:
                    done += 1
                    if done % PROGRESS_EVERY == 0:
                        if clock() - started > BUDGET_SECONDS:
                            result["cancelled"] = True
                        elif progress is not None:
                            try:
                                if progress(done, len(items)) is False:
                                    result["cancelled"] = True
                            except Exception:
                                pass
                    if result["cancelled"]:
                        break
                    self_result = _one_region(doc, db, plan, item, view, view_id, z_ft,
                                              region_type_id, line_style_id, to_eid, created_utc)
                    if self_result["ok"]:
                        if item["action"] == "replace":
                            result["replaced"] += 1
                        else:
                            result["created"] += 1
                        drawn.append((self_result["region"], self_result["record"]))
                        if self_result.get("note"):
                            result["notes"].append(self_result["note"])
                    else:
                        result["failed"].append({"key": item["key"], "title": item["title"],
                                                 "code": self_result["code"],
                                                 "reason": self_result["reason"]})
                # The sketch a region reports before Revit has regenerated
                # can be empty or stale, and a record written from it would
                # hide every later hand edit behind "nothing to compare".
                # One regeneration per view, then the records are brought up
                # to what the regions actually are.
                _regenerate(doc)
                _refresh_records(doc, db, drawn, plan, result)
                transaction.Commit()
                result["views_done"] += 1
            except Exception as ex:
                try:
                    transaction.RollBack()
                except Exception:
                    pass
                # The view's work is gone from the model, so it goes from the
                # counters too: a report must never claim work that no longer
                # exists.
                result["created"], result["replaced"] = before
                result["failed"].append({"key": u"", "title": safe_text(getattr(view, "Name", u"")),
                                         "code": "revit_refused",
                                         "reason": state.sentence_for("revit_refused", safe_text(ex))})
            if result["cancelled"]:
                break
        group.Assimilate()
    except Exception as ex:
        try:
            group.RollBack()
        except Exception:
            pass
        result["created"] = 0
        result["replaced"] = 0
        result["error"] = safe_text(ex)

    result["seconds"] = clock() - started
    return result


def _regenerate(doc):
    """Regenerate inside the open transaction; a refusal is not a rollback."""
    method = getattr(doc, "Regenerate", None)
    if not callable(method):
        return False
    try:
        method()
        return True
    except Exception:
        return False


def _refresh_records(doc, db, drawn, plan, result):
    """Re-read each new region's outline and fix the record where it moved."""
    grid_mm = plan.get("grid_mm") or state.GRID_MM
    for region, record in drawn:
        loops = read_region_loops(region, db=db)
        now = state.fingerprints(loops or [], grid_mm)
        if now.get("abs") == (record.get("region") or {}).get("abs"):
            continue
        record = dict(record)
        record["region"] = state.digests_only(now)
        ok, reason = storage.write_record(doc, region, record, db=db)
        if not ok:
            result["notes"].append(u"The record on one region could not be refreshed: "
                                   u"{0}".format(reason))


def _create_region(doc, db, boundary, z_ft, region_type_id, view_eid, item):
    """``(region, note)`` - the true loops first, the simplified outline second.

    Revit's sketch validator refuses things the boundary read cannot always
    foresee - a spur two walls wide, an arc it will not build, loops that
    touch at a vertex.  When it does, the same room is drawn again from its
    cleaned outline with straight edges, and the note says so; only if that
    is refused too does the room fail, with Revit's own words.
    """
    loops = _materialise_loops(db, boundary.get("loops") or [], z_ft)
    try:
        return db.FilledRegion.Create(doc, region_type_id, view_eid, loops), u""
    except Exception as first:
        fallback = _materialise_simplified(db, boundary.get("loops") or [], z_ft)
        if fallback is None:
            raise first
        try:
            region = db.FilledRegion.Create(doc, region_type_id, view_eid, fallback)
        except Exception:
            raise first
        return region, (u"{0} was drawn from its simplified outline (straight edges, within "
                        u"a millimetre) because Revit refused the exact one: {1}".format(
                            item.get("title"), safe_text(first)))


def _one_region(doc, db, plan, item, view, view_id, z_ft, region_type_id, line_style_id,
                to_eid, created_utc):
    """One room in one view, in its own SubTransaction."""
    sub = db.SubTransaction(doc)
    sub.Start()
    note = u""
    try:
        if item.get("replaces_region_id") is not None:
            old_id = to_eid(item["replaces_region_id"])
            reason = storage.checkout_reason(doc, old_id, db=db)
            if reason:
                sub.RollBack()
                return {"ok": False, "code": "owned_by_other", "reason": reason}
            doc.Delete(old_id)

        boundary = (plan.get("boundaries") or {}).get(item["boundary_key"]) or {}
        region, note = _create_region(doc, db, boundary, z_ft, region_type_id, to_eid(view_id),
                                      item)
        if line_style_id is not None:
            setter = getattr(region, "SetLineStyleId", None)
            try:
                if callable(setter):
                    setter(line_style_id)
                else:
                    note = (note + u" " if note else u"") + \
                        u"This Revit cannot set a filled region's line style; the type's " \
                        u"default was kept."
            except Exception as ex:
                note = (note + u" " if note else u"") + \
                    u"The boundary line style could not be set on {0}: {1}".format(
                        item.get("title"), safe_text(ex))
        region_loops = read_region_loops(region, db=db)
        record = state.make_record(item, boundary.get("fingerprints") or {},
                                   state.fingerprints(region_loops or [],
                                                      plan.get("grid_mm") or state.GRID_MM),
                                   plan, created_utc=created_utc)
        ok, reason = storage.write_record(doc, region, record, db=db)
        if not ok:
            sub.RollBack()
            return {"ok": False, "code": "revit_refused", "reason": reason}
        sub.Commit()
        return {"ok": True, "code": "", "reason": u"", "region": region, "record": record,
                "note": note}
    except Exception as ex:
        try:
            sub.RollBack()
        except Exception:
            pass
        return {"ok": False, "code": "revit_refused",
                "reason": state.sentence_for("revit_refused", safe_text(ex))}


def delete_regions(doc, region_ids, db=None):
    """Delete tracked regions, in one undo step, naming what could not go."""
    db = _db(db)
    result = {"deleted": 0, "failed": []}
    ids = [value for value in region_ids or [] if value is not None]
    if not ids:
        return result
    to_eid = element_id_factory(db.ElementId)
    transaction = db.Transaction(doc, u"Space Boundary: delete regions")
    transaction.Start()
    try:
        for value in ids:
            element_id = to_eid(value)
            reason = storage.checkout_reason(doc, element_id, db=db)
            if reason:
                result["failed"].append({"region_id": value, "reason": reason})
                continue
            sub = db.SubTransaction(doc)
            sub.Start()
            try:
                doc.Delete(element_id)
                sub.Commit()
                result["deleted"] += 1
            except Exception as ex:
                sub.RollBack()
                result["failed"].append({"region_id": value, "reason": safe_text(ex)})
        transaction.Commit()
    except Exception as ex:
        try:
            transaction.RollBack()
        except Exception:
            pass
        result["deleted"] = 0
        result["failed"].append({"region_id": None, "reason": safe_text(ex)})
    return result


# -------------------------------------------------------------- pick/show


def _selection_module():
    try:
        from Autodesk.Revit.UI import Selection
        return Selection
    except Exception:
        return None


def pick_spatial(uidoc, kind, db=None):
    """Rooms or spaces picked in the model, as many as wanted.  ``[]`` when
    cancelled.

    The filter keeps the pick to the one category, so a wall or a tag under
    the cursor does not end the pick.  Rooms in a link cannot be picked this
    way - a pick on a link lands on the link - so they are converted by
    ticking the link and choosing the views instead.
    """
    db = _db(db)
    selection = _selection_module()
    if selection is None or uidoc is None:
        return []
    wanted = _member_int(_builtin(db, "BuiltInCategory",
                                  "OST_MEPSpaces" if kind == state.KIND_SPACE else "OST_Rooms"))

    def _is_wanted(element):
        if wanted is None:
            return True
        try:
            return eid_to_int(element.Category.Id) == wanted
        except Exception:
            return False

    class _OnlySpatial(selection.ISelectionFilter):
        def AllowElement(self, element):
            return _is_wanted(element)

        def AllowReference(self, reference, point):
            return False

    try:
        references = list(uidoc.Selection.PickObjects(
            selection.ObjectType.Element, _OnlySpatial(),
            u"Pick the {0}s to convert, then Finish".format(
                u"space" if kind == state.KIND_SPACE else u"room")))
    except Exception:
        return []
    picked = []
    seen = set()
    for reference in references:
        try:
            element = uidoc.Document.GetElement(reference.ElementId)
        except Exception:
            element = None
        if element is None or not _is_wanted(element):
            continue
        uid = safe_text(getattr(element, "UniqueId", u""))
        if uid in seen:
            continue
        seen.add(uid)
        picked.append({"uid": uid, "id": eid_to_int(getattr(element, "Id", None)),
                       "label": u"{0} {1}".format(_param_string(element, db, "ROOM_NUMBER"),
                                                  _param_string(element, db, "ROOM_NAME")).strip()})
    return picked


def show(uidoc, row, db=None):
    """Select the region (and open its view) and frame it."""
    db = _db(db)
    if not uidoc or not row:
        return False
    to_eid = element_id_factory(db.ElementId)
    ids = [value for value in (row.get("region_id"), row.get("view_id")) if value is not None]
    if not ids:
        return False
    try:
        from System.Collections.Generic import List as ClrList

        id_list = ClrList[db.ElementId]()
        if row.get("region_id") is not None:
            id_list.Add(to_eid(row["region_id"]))
        if id_list.Count == 0:
            return False
        uidoc.Selection.SetElementIds(id_list)
        uidoc.ShowElements(id_list)
        return True
    except Exception:
        return False
