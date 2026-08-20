# -*- coding: utf-8 -*-
"""Revit API layer for Circuit Schedule.

One pass over the document, into plain dicts: nothing but ints and unicode
crosses back into ``circuit_schedule_state``, so the hierarchy is decided - and
tested - without a Revit session.

Every reading is taken through ``AsValueString()``, as display text.  The tree
is read-only, so no value ever has to travel back into the model, which keeps
the whole 2021+ ``ForgeTypeId`` / legacy ``DisplayUnitType`` unit split that
``circuit_rating_revit`` has to carry out of this tool entirely.
"""

from __future__ import print_function

from pyrevit import DB

from easybim.compat import eid_to_int
from easybim.compat import element_id_factory
from easybim.compat import safe_text


#: Read off each circuit.  Resolved by name because not every release carries
#: every one of them - the guard ``circuit_rating_revit`` already needs.
CIRCUIT_FIELDS = (
    ("panel_name", "RBS_ELEC_CIRCUIT_PANEL_PARAM"),
    ("circuit_number", "RBS_ELEC_CIRCUIT_NUMBER"),
    ("load_name", "RBS_ELEC_CIRCUIT_NAME"),
    ("rating", "RBS_ELEC_CIRCUIT_RATING_PARAM"),
    ("frame", "RBS_ELEC_CIRCUIT_FRAME_PARAM"),
    ("poles", "RBS_ELEC_NUMBER_OF_POLES"),
    ("voltage", "RBS_ELEC_VOLTAGE"),
    ("apparent_load", "RBS_ELEC_APPARENT_LOAD"),
    ("wire_size", "RBS_ELEC_CIRCUIT_WIRE_SIZE_PARAM"),
)


def _param_text(element, bip_name):
    builtin = getattr(DB.BuiltInParameter, bip_name, None)
    if builtin is None:
        return u""
    try:
        param = element.get_Parameter(builtin)
        if param is None:
            return u""
        return safe_text(param.AsValueString() or param.AsString() or u"")
    except Exception:
        return u""


def _category_name(element):
    try:
        category = element.Category
        return safe_text(category.Name) if category is not None else u""
    except Exception:
        return u""


def _is_electrical_equipment(element):
    try:
        category = element.Category
        if category is None:
            return False
        return eid_to_int(category.Id) == int(
            DB.BuiltInCategory.OST_ElectricalEquipment)
    except Exception:
        return False


def _type_name(element, doc):
    try:
        type_id = element.GetTypeId()
    except Exception:
        return u""
    if type_id is None or eid_to_int(type_id) < 0:
        return u""
    try:
        element_type = doc.GetElement(type_id)
        return safe_text(element_type.Name) if element_type is not None else u""
    except Exception:
        return u""


def _level_name(element, doc):
    try:
        level_id = element.LevelId
    except Exception:
        level_id = None
    if level_id is None or eid_to_int(level_id) < 0:
        return u""
    try:
        level = doc.GetElement(level_id)
        return safe_text(level.Name) if level is not None else u""
    except Exception:
        return u""


def _element_record(element, doc):
    name = u""
    try:
        name = safe_text(element.Name)
    except Exception:
        pass
    type_name = _type_name(element, doc)
    return {
        "id": eid_to_int(element.Id),
        "name": name or type_name or u"Element",
        "type_name": type_name,
        "category": _category_name(element),
        "level": _level_name(element, doc),
        # The split that makes a downstream board look unlike a receptacle.
        "is_panel": _is_electrical_equipment(element),
    }


def collect_circuits(doc):
    """Every electrical circuit in the document.

    The same collector ``circuit_rating_revit.collect_circuits`` uses.  It is
    duplicated rather than imported because that module lives inside a
    ``.pushbutton`` folder, which is only on ``sys.path`` while its own
    ``script.py`` is running - a ``lib`` module can never import it.
    """
    try:
        return list(DB.FilteredElementCollector(doc)
                    .OfCategory(DB.BuiltInCategory.OST_ElectricalCircuit)
                    .WhereElementIsNotElementType())
    except Exception:
        return []


def document_key(doc):
    """What the tree was read from, so a stale pane can say so."""
    if doc is None:
        return u""
    try:
        return safe_text(doc.PathName or doc.Title)
    except Exception:
        return u""


def document_title(doc):
    if doc is None:
        return u""
    try:
        return safe_text(doc.Title)
    except Exception:
        return u""


def scan_model(doc):
    """``{doc_key, doc_title, elements, circuits}`` - the whole snapshot.

    ``BaseEquipment`` gives the upstream end of a circuit and ``Elements`` the
    downstream end, so one walk over the circuits yields every edge of the
    distribution graph.  Nothing else is asked of the model: deriving the
    hierarchy from a single source is what stops a parent and a child ever
    disagreeing about who feeds whom, and it steers clear of ``MEPModel``,
    whose electrical-system accessors changed shape across releases.
    """
    circuits = []
    elements = {}

    def remember(element):
        if element is None:
            return None
        try:
            element_id = eid_to_int(element.Id)
        except Exception:
            return None
        if element_id not in elements:
            try:
                elements[element_id] = _element_record(element, doc)
            except Exception:
                return None
        return element_id

    for circuit in collect_circuits(doc):
        try:
            entry = {"id": eid_to_int(circuit.Id)}
            for key, bip_name in CIRCUIT_FIELDS:
                entry[key] = _param_text(circuit, bip_name)
            entry["system_type"] = _system_type(circuit)
            entry["panel_id"] = remember(_base_equipment(circuit))
            entry["element_ids"] = [
                element_id for element_id in
                (remember(element) for element in _circuit_elements(circuit))
                if element_id is not None]
        except Exception:
            continue
        circuits.append(entry)

    return {
        "doc_key": document_key(doc),
        "doc_title": document_title(doc),
        "elements": elements,
        "circuits": circuits,
    }


def _base_equipment(circuit):
    try:
        return circuit.BaseEquipment
    except Exception:
        return None


def _circuit_elements(circuit):
    try:
        return [element for element in circuit.Elements]
    except Exception:
        return []


def _system_type(circuit):
    try:
        return safe_text(circuit.SystemType)
    except Exception:
        return u""


# ---------------------------------------------------------------- selection


def _clr_id_list(element_ids):
    from System.Collections.Generic import List as ClrList

    factory = element_id_factory(DB.ElementId)
    id_list = ClrList[DB.ElementId]()
    for element_id in element_ids:
        id_list.Add(factory(element_id))
    return id_list


def show_elements(uidoc, element_ids):
    """Select the elements and frame them in the active view.

    The shape of ``clash_detection_engine._show_elements`` minus its link
    branch: a circuit never reaches into a linked model, so there are no
    ``Reference`` gymnastics and no Revit-2023 fallback to carry.
    """
    ids = [element_id for element_id in element_ids or [] if element_id is not None]
    if not uidoc or not ids:
        return False

    doc = getattr(uidoc, "Document", None)
    # Resolved once: element_id_factory probes a construction, and on Revit 2026
    # the plain-int overload throws - not something to repeat per element.
    to_eid = element_id_factory(DB.ElementId)
    points = []
    for element_id in ids:
        try:
            element = doc.GetElement(to_eid(element_id))
        except Exception:
            element = None
        if element is None:
            continue
        try:
            points.extend(_bbox_corners(element.get_BoundingBox(None)))
        except Exception:
            pass

    selected = False
    try:
        id_list = _clr_id_list(ids)
        uidoc.Selection.SetElementIds(id_list)
        uidoc.ShowElements(id_list)
        selected = True
    except Exception:
        selected = False

    if points:
        _zoom_to(uidoc, points)
    return selected or bool(points)


def _bbox_corners(bbox):
    if bbox is None:
        return []
    return [bbox.Min, bbox.Max]


def _zoom_to(uidoc, points):
    """Frame the run rather than leaving ShowElements' own framing to chance."""
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
