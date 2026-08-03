# -*- coding: utf-8 -*-
"""Revit-side engine for Clash Detection Mode.

Lifecycle
---------
Nothing is subscribed until the user presses *Start Ongoing Detection Mode*,
and everything is detached on Stop.  With the mode off this module costs
exactly one dormant import: no event handlers, no timers, no files.

While the mode is on, two events do the work and their responsibilities are
deliberately lopsided:

``Application.DocumentChanged``
    Runs inside Revit's change notification, so it does set unions only -
    no document reads, no collectors, no geometry.  It records which element
    ids a transaction touched and stamps the clock.

``UIApplication.Idling``
    Does the real work, but only after the edit burst settles (debounce) and
    only for a bounded slice of the queue (element count + wall-clock
    budget).  Idling is also a valid API context, which is why deferred
    ``Show`` requests and the alert window are raised from here rather than
    from a WPF click handler or from ``DocumentChanged``.

Nothing is written to disk at any point.  State lives in module globals,
mirrored into pyRevit envvars only so a pyRevit engine reload can still find
and detach a stale delegate.
"""

from __future__ import print_function

import time

try:
    from pyrevit import DB
except Exception:
    DB = None

try:
    from pyrevit import script
except Exception:
    script = None

try:
    from pyrevit import HOST_APP
except Exception:
    HOST_APP = None

from easybim import clash_detection_state as state


HANDLER_ENVVAR = "EASYBIM_CLASH_DETECTION_HANDLERS"

#: Bounding-box padding for the quick filter, in feet (~3 mm).  Only widens
#: the candidate set; the exact filter still decides.
BBOX_PAD = 0.01
#: Ceiling on candidates re-queued when a link instance is moved.
MAX_LINK_SWEEP = 2000

_ACTIVE = False
_SESSION = None
_DOC = None
_UIAPP = None
_CHANGED_HANDLER = None
_IDLING_HANDLER = None
_HANDLER_APP = None
_HANDLER_UIAPP = None
_CONTEXTS = {}
_TYPE_LABEL_CACHE = {}
_SHOW_REQUESTS = []
_PENDING_PANEL_OPEN = [False]
_ID_FACTORY = None


class _NullLogger(object):
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


try:
    LOGGER = script.get_logger() if script is not None else _NullLogger()
except Exception:
    LOGGER = _NullLogger()


def _log(message):
    try:
        LOGGER.debug("[Clash Detection] {0}".format(message))
    except Exception:
        pass


# -- envvar mirror --------------------------------------------------------


def _get_envvar(name, default=None):
    try:
        value = script.get_envvar(name)
    except Exception:
        return default
    return default if value is None else value


def _set_envvar(name, value):
    try:
        script.set_envvar(name, value)
    except Exception:
        pass


# -- element id helpers ---------------------------------------------------


def _eid_int(element_id):
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


def _id_factory():
    """Resolve the working ``ElementId`` constructor once (Revit 2026 drops Int32)."""
    global _ID_FACTORY
    if _ID_FACTORY is None:
        from easybim.compat import element_id_factory

        _ID_FACTORY = element_id_factory(DB.ElementId)
    return _ID_FACTORY


def _to_eid(value):
    try:
        return _id_factory()(value)
    except Exception:
        return None


def _clr_id_list(element_ids):
    from System.Collections.Generic import List as ClrList

    ids = ClrList[DB.ElementId]()
    for element_id in element_ids or []:
        if element_id is not None:
            ids.Add(element_id)
    return ids


# -- event sources --------------------------------------------------------


def _get_uiapp(uiapp=None):
    if uiapp is not None:
        return uiapp
    try:
        return HOST_APP.uiapp
    except Exception:
        return None


def _revit_application(uiapp=None):
    """Find the object that actually exposes ``DocumentChanged``."""
    uiapp = _get_uiapp(uiapp)
    candidates = []
    if uiapp is not None:
        candidates.append(uiapp)
        for attr_name in ("Application", "ControlledApplication"):
            try:
                candidates.append(getattr(uiapp, attr_name))
            except Exception:
                pass
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            getattr(candidate, "DocumentChanged")
            return candidate
        except Exception:
            pass
    return None


def _idling_source(uiapp=None):
    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return None
    try:
        getattr(uiapp, "Idling")
        return uiapp
    except Exception:
        return None


def _make_document_changed_handler():
    """Typed CLR delegate, degrading to the bare callable like the repo's
    other passive listener does."""
    try:
        from System import EventHandler

        try:
            from Autodesk.Revit.DB.Events import DocumentChangedEventArgs
        except Exception:
            from Autodesk.Revit.DB import DocumentChangedEventArgs
        return EventHandler[DocumentChangedEventArgs](_on_document_changed)
    except Exception:
        return _on_document_changed


def _make_idling_handler():
    try:
        from System import EventHandler

        try:
            from Autodesk.Revit.UI.Events import IdlingEventArgs
        except Exception:
            from Autodesk.Revit.UI import IdlingEventArgs
        return EventHandler[IdlingEventArgs](_on_idling)
    except Exception:
        return _on_idling


# -- documents ------------------------------------------------------------


def _doc_identity(doc):
    if doc is None:
        return ("", "")
    return (
        state.safe_text(getattr(doc, "PathName", "")).lower(),
        state.safe_text(getattr(doc, "Title", "")).lower(),
    )


def _same_doc(doc_a, doc_b):
    if doc_a is doc_b:
        return True
    if doc_a is None or doc_b is None:
        return False
    return _doc_identity(doc_a) == _doc_identity(doc_b)


# -- side contexts --------------------------------------------------------


def _build_category_filter(category_ids):
    """One ``ElementMulticategoryFilter`` per side, built once at Start.

    Returns ``(filter, fallback_ids)``.  When Revit refuses the filter (an
    id that is not a real category), the caller falls back to checking the
    category in Python instead of losing the whole side.
    """
    numeric_ids = set(cid for cid in (category_ids or []) if cid is not None)
    if not numeric_ids:
        return None, numeric_ids
    try:
        ids = _clr_id_list([_to_eid(cid) for cid in numeric_ids])
        return DB.ElementMulticategoryFilter(ids), numeric_ids
    except Exception:
        _log("ElementMulticategoryFilter unavailable; falling back to a Python check.")
        return None, numeric_ids


def _build_contexts(session, doc, link_instances_by_ref):
    """Cache each side's document, transform and filters once at Start."""
    contexts = {}
    for slot, side in (("a", session.side_a), ("b", session.side_b)):
        side.slot = slot
        category_filter, numeric_ids = _build_category_filter(side.category_ids)
        context = {
            "slot": slot,
            "model_ref": side.model_ref,
            "model_label": side.model_label,
            "doc": doc,
            "link_instance": None,
            "transform": None,
            "category_filter": category_filter,
            "category_ids": numeric_ids,
        }
        if state.is_link_ref(side.model_ref):
            link_instance = link_instances_by_ref.get(side.model_ref)
            if link_instance is None:
                _log("Link instance for {0} is not loaded.".format(side.model_ref))
                return None
            link_doc = None
            try:
                link_doc = link_instance.GetLinkDocument()
            except Exception:
                link_doc = None
            if link_doc is None:
                _log("Link document for {0} is not loaded.".format(side.model_ref))
                return None
            context["doc"] = link_doc
            context["link_instance"] = link_instance
            try:
                context["transform"] = link_instance.GetTotalTransform()
            except Exception:
                context["transform"] = None
        contexts[slot] = context
    return contexts


def _context_for_side(side):
    return _CONTEXTS.get(getattr(side, "slot", ""))


def _context_for_model_ref(model_ref):
    for context in _CONTEXTS.values():
        if context.get("model_ref") == model_ref:
            return context
    return None


def _relative_transform(source_context, target_context):
    """Map source-model coordinates into target-model coordinates."""
    source = source_context.get("transform")
    target = target_context.get("transform")
    if source is None and target is None:
        return None
    if target is None:
        return source
    inverse = target.Inverse
    if source is None:
        return inverse
    return inverse.Multiply(source)


# -- geometry -------------------------------------------------------------


def _geometry_options():
    options = DB.Options()
    options.ComputeReferences = False
    options.IncludeNonVisibleObjects = False
    try:
        options.DetailLevel = DB.ViewDetailLevel.Medium
    except Exception:
        pass
    return options


def _collect_solids(geometry_element, results, depth=0):
    """Flatten a GeometryElement to solids, recursing into instances.

    The recursion matters: family instances hand back a ``GeometryInstance``
    wrapper, and a non-recursive walk finds no solids at all for them.
    """
    if geometry_element is None or depth > 4:
        return results
    try:
        iterator = iter(geometry_element)
    except Exception:
        return results
    for geometry_object in iterator:
        try:
            if isinstance(geometry_object, DB.Solid):
                if geometry_object.Volume > 1e-9:
                    results.append(geometry_object)
            elif isinstance(geometry_object, DB.GeometryInstance):
                _collect_solids(geometry_object.GetInstanceGeometry(), results, depth + 1)
        except Exception:
            continue
    return results


def _element_solids(element):
    try:
        geometry_element = element.get_Geometry(_geometry_options())
    except Exception:
        return []
    return _collect_solids(geometry_element, [])


def _outline_from_corners(points, pad=BBOX_PAD):
    if not points:
        return None
    min_x = min(point.X for point in points) - pad
    min_y = min(point.Y for point in points) - pad
    min_z = min(point.Z for point in points) - pad
    max_x = max(point.X for point in points) + pad
    max_y = max(point.Y for point in points) + pad
    max_z = max(point.Z for point in points) + pad
    try:
        return DB.Outline(DB.XYZ(min_x, min_y, min_z), DB.XYZ(max_x, max_y, max_z))
    except Exception:
        return None


def _bbox_corners(bbox, extra_transform=None):
    """The 8 corners of a bounding box, in model coordinates.

    ``BoundingBoxXYZ.Min``/``Max`` are expressed in the box's own
    ``Transform``, which is usually but not always the identity - ignoring
    it silently misplaces the quick filter for the cases where it is not.
    """
    if bbox is None:
        return []
    try:
        low, high = bbox.Min, bbox.Max
    except Exception:
        return []
    corners = []
    for x_value in (low.X, high.X):
        for y_value in (low.Y, high.Y):
            for z_value in (low.Z, high.Z):
                corners.append(DB.XYZ(x_value, y_value, z_value))
    for transform in (getattr(bbox, "Transform", None), extra_transform):
        if transform is None:
            continue
        try:
            if transform.IsIdentity:
                continue
        except Exception:
            pass
        try:
            corners = [transform.OfPoint(point) for point in corners]
        except Exception:
            return []
    return corners


def _outline_for_element(element, extra_transform=None):
    try:
        bbox = element.get_BoundingBox(None)
    except Exception:
        bbox = None
    return _outline_from_corners(_bbox_corners(bbox, extra_transform))


# -- clash queries --------------------------------------------------------


def _base_collector(context, outline):
    collector = DB.FilteredElementCollector(context["doc"])
    collector = collector.WhereElementIsNotElementType()
    category_filter = context.get("category_filter")
    if category_filter is not None:
        collector = collector.WherePasses(category_filter)
    if outline is not None:
        collector = collector.WherePasses(DB.BoundingBoxIntersectsFilter(outline))
    return collector


def _passes_category_fallback(context, element):
    """Only used when Revit refused the multicategory quick filter."""
    if context.get("category_filter") is not None:
        return True
    try:
        category = element.Category
        return category is not None and _eid_int(category.Id) in context["category_ids"]
    except Exception:
        return False


def _find_clashes(element, source_context, target_context):
    """Element ids in the target model whose solids overlap ``element``.

    Ordering is the whole performance story: two quick filters (category and
    bounding box, both served by Revit's spatial index) cut the candidate
    set down to a handful before the expensive exact filter ever runs.
    """
    same_model = source_context["model_ref"] == target_context["model_ref"]

    if same_model:
        outline = _outline_for_element(element)
        if outline is None:
            return []
        collector = _base_collector(target_context, outline)
        try:
            collector = collector.Excluding(_clr_id_list([element.Id]))
            collector = collector.WherePasses(DB.ElementIntersectsElementFilter(element))
            found = list(collector.ToElements())
        except Exception:
            # Elements without solid geometry make the exact filter throw.
            return []
        return [
            _eid_int(item.Id)
            for item in found
            if _passes_category_fallback(target_context, item)
        ]

    solids = _element_solids(element)
    if not solids:
        return []
    transform = _relative_transform(source_context, target_context)
    found_ids = set()
    for solid in solids:
        try:
            target_solid = (
                DB.SolidUtils.CreateTransformed(solid, transform)
                if transform is not None
                else solid
            )
        except Exception:
            continue
        outline = _outline_from_corners(_bbox_corners(target_solid.GetBoundingBox()))
        if outline is None:
            continue
        try:
            collector = _base_collector(target_context, outline)
            collector = collector.WherePasses(DB.ElementIntersectsSolidFilter(target_solid))
            for item in collector.ToElements():
                if _passes_category_fallback(target_context, item):
                    found_ids.add(_eid_int(item.Id))
        except Exception:
            continue
    return [element_id for element_id in found_ids if element_id is not None]


# -- element description --------------------------------------------------


def _type_labels(doc, element):
    """``(family_name, type_name)`` for an element, cached per type id."""
    try:
        type_id = element.GetTypeId()
    except Exception:
        type_id = None
    numeric = _eid_int(type_id)
    cache_key = (id(doc), numeric)
    if cache_key in _TYPE_LABEL_CACHE:
        return _TYPE_LABEL_CACHE[cache_key]

    family_name = ""
    type_name = ""
    if numeric is not None and numeric > 0:
        try:
            element_type = doc.GetElement(type_id)
        except Exception:
            element_type = None
        if element_type is not None:
            family_name = state.safe_text(getattr(element_type, "FamilyName", ""))
            try:
                type_name = state.safe_text(element_type.Name)
            except Exception:
                type_name = ""
    if not type_name:
        try:
            type_name = state.safe_text(element.Name)
        except Exception:
            type_name = ""

    labels = (family_name, type_name)
    if numeric is not None:
        _TYPE_LABEL_CACHE[cache_key] = labels
    return labels


def _element_info(context, element):
    doc = context["doc"]
    family_name, type_name = _type_labels(doc, element)
    category_name = ""
    try:
        category = element.Category
        if category is not None:
            category_name = state.safe_text(category.Name)
    except Exception:
        category_name = ""
    return state.ElementInfo(
        model_ref=context["model_ref"],
        model_label=context["model_label"],
        element_id=_eid_int(element.Id),
        category_name=category_name,
        family_name=family_name,
        type_name=type_name,
    )


# -- work items -----------------------------------------------------------

def _resolve_missing(model_ref, element_id):
    key = state.element_key(model_ref, element_id)
    return bool(_SESSION.remove_pairs_for_element_keys([key]))


def _handle_link_instance_change(link_instance):
    """A watched link moved: re-evaluate everything that touches it.

    Its own elements did not change, so instead of walking the link, drop
    every pair that references it and re-queue the opposite side's elements
    inside the link's new footprint.  Bounded by that footprint, never by
    project size.
    """
    model_ref = state.link_model_ref(_eid_int(link_instance.Id))
    if model_ref is None or model_ref not in _SESSION.watched_model_refs():
        return False

    context = _context_for_model_ref(model_ref)
    if context is not None:
        try:
            context["transform"] = link_instance.GetTotalTransform()
        except Exception:
            pass

    changed = False
    for record in _SESSION.pairs_for_model_ref(model_ref):
        _SESSION.remove_pair(record.key, count_resolved=False)
        changed = True

    try:
        link_bbox = link_instance.get_BoundingBox(None)
    except Exception:
        link_bbox = None
    corners = _bbox_corners(link_bbox)
    if not corners:
        return changed

    for side, other in (
        (_SESSION.side_a, _SESSION.side_b),
        (_SESSION.side_b, _SESSION.side_a),
    ):
        if not side.owns(model_ref):
            continue
        other_context = _context_for_side(other)
        if other_context is None:
            continue
        local_corners = corners
        other_transform = other_context.get("transform")
        if other_transform is not None:
            try:
                inverse = other_transform.Inverse
                local_corners = [inverse.OfPoint(point) for point in corners]
            except Exception:
                continue
        outline = _outline_from_corners(local_corners)
        if outline is None:
            continue
        try:
            candidates = [
                _eid_int(element_id)
                for element_id in _base_collector(other_context, outline).ToElementIds()
            ]
        except Exception:
            continue
        if len(candidates) > MAX_LINK_SWEEP:
            candidates = candidates[:MAX_LINK_SWEEP]
        _SESSION.enqueue_elements(other.model_ref, candidates)
    return changed


def _process_work_item(model_ref, element_id):
    """Check one element against every side it must be compared with."""
    context = _context_for_model_ref(model_ref)
    if context is None:
        return False

    element_id_obj = _to_eid(element_id)
    try:
        element = context["doc"].GetElement(element_id_obj)
    except Exception:
        element = None
    if element is None:
        return _resolve_missing(model_ref, element_id)

    if model_ref == state.HOST_MODEL_REF:
        try:
            if isinstance(element, DB.RevitLinkInstance):
                return _handle_link_instance_change(element)
        except Exception:
            pass

    try:
        category = element.Category
    except Exception:
        category = None
    if category is None:
        return False
    category_id = _eid_int(category.Id)

    target_sides = _SESSION.target_sides_for(model_ref, category_id)
    if not target_sides:
        return False

    source_key = state.element_key(model_ref, element_id)
    source_info = None
    current_partner_keys = set()
    changed = False

    for target_side in target_sides:
        target_context = _context_for_side(target_side)
        if target_context is None:
            continue
        for other_id in _find_clashes(element, context, target_context):
            other_key = state.element_key(target_context["model_ref"], other_id)
            if other_key is None or other_key == source_key:
                continue
            current_partner_keys.add(other_key)
            key = state.pair_key(
                model_ref, element_id, target_context["model_ref"], other_id
            )
            if key is None or _SESSION.has_pair(key):
                continue
            try:
                other_element = target_context["doc"].GetElement(_to_eid(other_id))
            except Exception:
                other_element = None
            if other_element is None:
                continue
            if source_info is None:
                source_info = _element_info(context, element)
            other_info = _element_info(target_context, other_element)
            if _SESSION.record_clash(key, source_info, other_info) is not None:
                changed = True

    # Anything previously recorded against this element that is no longer in
    # the fresh result set has been resolved by the user's edit.  This is
    # exact, and it costs nothing extra: the queries above already told us
    # the current truth.
    for record in _SESSION.pairs_for_element_key(source_key):
        partners = [key for key in record.element_keys if key != source_key]
        if partners and partners[0] not in current_partner_keys:
            _SESSION.remove_pair(record.key)
            changed = True

    return changed


# -- passes ---------------------------------------------------------------


def _run_pass():
    changed = False

    deleted = _SESSION.take_deleted()
    if deleted:
        keys = [
            state.element_key(state.HOST_MODEL_REF, element_id)
            for element_id in deleted
        ]
        if _SESSION.remove_pairs_for_element_keys(keys):
            changed = True

    started_at = time.time()
    processed = 0
    while processed < state.MAX_ELEMENTS_PER_TICK:
        if processed and _SESSION.budget_exceeded(started_at, time.time()):
            break
        item = _SESSION.take_one()
        if item is None:
            break
        processed += 1
        try:
            if _process_work_item(item[0], item[1]):
                changed = True
        except Exception as ex:
            _log("Work item {0} failed: {1}".format(item, ex))

    new_pairs = _SESSION.drain_new_pairs()
    if new_pairs and not _SESSION.silent_mode:
        _raise_alert(new_pairs)
    if processed or changed or new_pairs:
        # Refresh on any processed item, not just on a change, so the
        # "checking N..." counter clears when the queue finally drains.
        _refresh_panel()
    return changed


def _refresh_panel():
    try:
        from easybim import clash_detection_panel

        clash_detection_panel.refresh(_SESSION)
    except Exception as ex:
        _log("Panel refresh failed: {0}".format(ex))


def _raise_alert(new_pairs):
    try:
        from easybim import clash_detection_alert

        clash_detection_alert.show(list(new_pairs), _SESSION)
    except Exception as ex:
        _log("Alert failed: {0}".format(ex))


# -- show requests --------------------------------------------------------


def request_show(pair_key):
    """Queue a Show request; Idling runs it in a valid API context."""
    if pair_key and pair_key not in _SHOW_REQUESTS:
        _SHOW_REQUESTS.append(pair_key)


def _link_instance_for_ref(model_ref):
    context = _context_for_model_ref(model_ref)
    return context.get("link_instance") if context else None


def _show_record(uidoc, record):
    host_doc = getattr(uidoc, "Document", None) or _DOC
    host_ids = []
    link_references = []
    zoom_points = []

    for info in (record.element_a, record.element_b):
        context = _context_for_model_ref(info.model_ref)
        if context is None:
            continue
        element_id_obj = _to_eid(info.element_id)
        try:
            element = context["doc"].GetElement(element_id_obj)
        except Exception:
            element = None
        if element is None:
            continue

        try:
            zoom_points.extend(
                _bbox_corners(element.get_BoundingBox(None), context.get("transform"))
            )
        except Exception:
            pass

        if not info.is_linked:
            host_ids.append(element_id_obj)
            continue

        link_instance = context.get("link_instance")
        try:
            link_references.append(
                DB.Reference(element).CreateLinkReference(link_instance)
            )
        except Exception:
            # No link reference available: fall back to selecting the link
            # instance itself, and let the zoom below frame the element.
            if link_instance is not None:
                host_ids.append(link_instance.Id)

    selected = False
    if link_references:
        try:
            from System.Collections.Generic import List as ClrList

            reference_list = ClrList[DB.Reference]()
            for reference in link_references:
                reference_list.Add(reference)
            for host_id in host_ids:
                host_element = host_doc.GetElement(host_id)
                if host_element is not None:
                    reference_list.Add(DB.Reference(host_element))
            uidoc.Selection.SetReferences(reference_list)
            selected = True
        except Exception:
            # Revit < 2023 has no Selection.SetReferences; fall through to
            # host-id selection plus an explicit zoom.
            selected = False

    if not selected and host_ids:
        try:
            id_list = _clr_id_list(host_ids)
            uidoc.Selection.SetElementIds(id_list)
            uidoc.ShowElements(id_list)
            selected = True
        except Exception:
            selected = False

    if zoom_points:
        _zoom_to(uidoc, zoom_points)
    return selected or bool(zoom_points)


def _zoom_to(uidoc, points):
    """Frame the clash even when the element lives inside a link."""
    try:
        active_view_id = uidoc.ActiveView.Id
        ui_view = None
        for candidate in uidoc.GetOpenUIViews():
            if candidate.ViewId == active_view_id:
                ui_view = candidate
                break
        if ui_view is None:
            return
        pad = 2.0
        corner_min = DB.XYZ(
            min(point.X for point in points) - pad,
            min(point.Y for point in points) - pad,
            min(point.Z for point in points) - pad,
        )
        corner_max = DB.XYZ(
            max(point.X for point in points) + pad,
            max(point.Y for point in points) + pad,
            max(point.Z for point in points) + pad,
        )
        ui_view.ZoomAndCenterRectangle(corner_min, corner_max)
    except Exception:
        pass


def _drain_show_requests(uiapp):
    requests = list(_SHOW_REQUESTS)
    del _SHOW_REQUESTS[:]
    if not requests:
        return
    uidoc = getattr(uiapp, "ActiveUIDocument", None) or getattr(
        _UIAPP, "ActiveUIDocument", None
    )
    if uidoc is None:
        return
    if not _same_doc(getattr(uidoc, "Document", None), _DOC):
        return
    records = dict((record.key, record) for record in _SESSION.records())
    for pair_key in requests:
        record = records.get(pair_key)
        if record is None:
            continue
        try:
            _show_record(uidoc, record)
        except Exception as ex:
            _log("Show failed for {0}: {1}".format(pair_key, ex))


# -- event callbacks ------------------------------------------------------


def _on_document_changed(sender, args):
    """Hot path.  Set unions only - no reads, no geometry, no allocation churn."""
    if not _ACTIVE or _SESSION is None:
        return
    try:
        doc = args.GetDocument()
    except Exception:
        return
    if not _same_doc(doc, _DOC):
        return
    try:
        _SESSION.enqueue_changes(
            added_ids=[_eid_int(item) for item in args.GetAddedElementIds()],
            modified_ids=[_eid_int(item) for item in args.GetModifiedElementIds()],
            deleted_ids=[_eid_int(item) for item in args.GetDeletedElementIds()],
            now=time.time(),
        )
    except Exception:
        pass


def _on_idling(sender, args):
    if not _ACTIVE or _SESSION is None:
        return
    try:
        if _PENDING_PANEL_OPEN[0]:
            # Showing a dockable pane is a Revit UI call, so it is deferred
            # here from the alert window's click rather than made inline.
            _PENDING_PANEL_OPEN[0] = False
            from easybim import clash_detection_panel

            clash_detection_panel.open_panel(_SESSION)
        if _SHOW_REQUESTS:
            _drain_show_requests(sender)
        if not _SESSION.should_run(time.time()):
            return
        _run_pass()
    except Exception as ex:
        _log("Idling pass failed: {0}".format(ex))


# -- subscription ---------------------------------------------------------


def _detach(app, uiapp, handlers):
    changed_handler = handlers.get("changed")
    if app is not None and changed_handler is not None:
        try:
            app.DocumentChanged -= changed_handler
        except Exception:
            pass
    idling_handler = handlers.get("idling")
    if uiapp is not None and idling_handler is not None:
        try:
            uiapp.Idling -= idling_handler
        except Exception:
            pass


def _detach_stale():
    """Detach a delegate left behind by a previous pyRevit engine.

    ``sys.modules`` does not survive a pyRevit reload, so without the envvar
    mirror a reload would silently stack a second subscription on every
    Start.
    """
    stored = _get_envvar(HANDLER_ENVVAR, None)
    if not isinstance(stored, dict):
        return
    _detach(stored.get("app"), stored.get("uiapp"), stored)
    _set_envvar(HANDLER_ENVVAR, None)


def _subscribe(uiapp):
    global _CHANGED_HANDLER, _IDLING_HANDLER, _HANDLER_APP, _HANDLER_UIAPP

    app = _revit_application(uiapp)
    idling_source = _idling_source(uiapp)
    if app is None or idling_source is None:
        _log("Could not find the DocumentChanged/Idling event sources.")
        return False

    _detach_stale()

    changed_handler = _make_document_changed_handler()
    idling_handler = _make_idling_handler()

    try:
        app.DocumentChanged += changed_handler
    except Exception:
        _log("DocumentChanged subscription failed.")
        return False
    try:
        idling_source.Idling += idling_handler
    except Exception:
        try:
            app.DocumentChanged -= changed_handler
        except Exception:
            pass
        _log("Idling subscription failed.")
        return False

    _CHANGED_HANDLER = changed_handler
    _IDLING_HANDLER = idling_handler
    _HANDLER_APP = app
    _HANDLER_UIAPP = idling_source
    _set_envvar(
        HANDLER_ENVVAR,
        {
            "changed": changed_handler,
            "idling": idling_handler,
            "app": app,
            "uiapp": idling_source,
        },
    )
    return True


def _unsubscribe():
    global _CHANGED_HANDLER, _IDLING_HANDLER, _HANDLER_APP, _HANDLER_UIAPP

    _detach(
        _HANDLER_APP,
        _HANDLER_UIAPP,
        {"changed": _CHANGED_HANDLER, "idling": _IDLING_HANDLER},
    )
    _detach_stale()
    _CHANGED_HANDLER = None
    _IDLING_HANDLER = None
    _HANDLER_APP = None
    _HANDLER_UIAPP = None


# -- public API -----------------------------------------------------------


def is_active():
    return bool(_ACTIVE)


def get_session():
    return _SESSION


def get_document():
    return _DOC


def start(uiapp, doc, side_a, side_b, silent_mode=True, link_instances_by_ref=None):
    """Arm the mode.  Returns ``(ok, message)``.

    Deliberately does no baseline scan: only elements that change from this
    moment on are ever tested, which is both why the mode starts instantly
    and why pre-existing clashes are never reported.
    """
    global _ACTIVE, _SESSION, _DOC, _UIAPP, _CONTEXTS

    if _ACTIVE:
        stop()

    session = state.ClashSession(side_a=side_a, side_b=side_b, silent_mode=silent_mode)
    contexts = _build_contexts(session, doc, link_instances_by_ref or {})
    if contexts is None:
        return False, "A selected linked model is not loaded. Reload it and try again."

    _SESSION = session
    _DOC = doc
    _UIAPP = _get_uiapp(uiapp)
    _CONTEXTS = contexts
    _TYPE_LABEL_CACHE.clear()
    del _SHOW_REQUESTS[:]
    _PENDING_PANEL_OPEN[0] = False

    if not _subscribe(_UIAPP):
        _SESSION = None
        _DOC = None
        _CONTEXTS = {}
        return False, "Revit did not expose the events this mode needs."

    _ACTIVE = True
    try:
        from easybim import clash_detection_panel

        clash_detection_panel.open_panel(session)
    except Exception as ex:
        _log("Panel open failed: {0}".format(ex))
    _log("Started.")
    return True, "Clash Detection Mode is on."


def stop(reason=None):
    """Disarm and wipe.  Nothing was written to disk, so nothing to delete."""
    global _ACTIVE, _SESSION, _DOC, _UIAPP, _CONTEXTS

    was_active = _ACTIVE
    _ACTIVE = False
    _unsubscribe()

    if _SESSION is not None:
        _SESSION.clear()
    _SESSION = None
    _DOC = None
    _UIAPP = None
    _CONTEXTS = {}
    _TYPE_LABEL_CACHE.clear()
    del _SHOW_REQUESTS[:]
    _PENDING_PANEL_OPEN[0] = False

    try:
        from easybim import clash_detection_panel

        clash_detection_panel.close_panel()
    except Exception:
        pass
    try:
        from easybim import clash_detection_alert

        clash_detection_alert.close()
    except Exception:
        pass

    if was_active:
        _log("Stopped ({0}).".format(reason or "user request"))
    return was_active


def set_silent_mode(silent_mode):
    if _SESSION is not None:
        _SESSION.silent_mode = bool(silent_mode)
        if silent_mode:
            try:
                from easybim import clash_detection_alert

                clash_detection_alert.close()
            except Exception:
                pass
            _PENDING_PANEL_OPEN[0] = True
    return bool(silent_mode)


def handle_doc_closing(doc):
    """Auto-stop when the monitored document goes away."""
    if not _ACTIVE:
        return False
    if doc is not None and not _same_doc(doc, _DOC):
        return False
    return stop(reason="document closing")
