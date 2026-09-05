# -*- coding: utf-8 -*-
"""Resolve and select Revit elements for the EasyBIM Coordination Review windows.

``resolve_link_instance`` maps the element id recorded by passive detection
(usually the ``RevitLinkInstance``, sometimes its ``RevitLinkType``) to the
placed link instance.  ``select_element`` makes an element the current
selection and frames it in the *current* view only; it never opens or
searches other views.

The Revit API is imported lazily (and can be injected) so the module loads
standalone under CPython for the unit tests.
"""


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


def _safe_int(value, default=None):
    try:
        return int(value)
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
    return _safe_int(element_id)


def _make_element_id(element_id_type, value):
    if element_id_type is None or value is None:
        return None
    try:
        return element_id_type(int(value))
    except Exception:
        pass
    try:
        # Revit 2026 removed ElementId(Int32); retry with Int64.
        import System

        return element_id_type(System.Int64(int(value)))
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


def _is_link_instance(element, db):
    if element is None:
        return False
    link_instance_type = getattr(db, "RevitLinkInstance", None) if db is not None else None
    if link_instance_type is not None:
        try:
            if isinstance(element, link_instance_type):
                return True
        except Exception:
            pass
    return "revitlinkinstance" in _class_name(element)


def _is_link_type(element, db):
    if element is None:
        return False
    link_type_type = getattr(db, "RevitLinkType", None) if db is not None else None
    if link_type_type is not None:
        try:
            if isinstance(element, link_type_type):
                return True
        except Exception:
            pass
    return "revitlinktype" in _class_name(element)


def _link_instances(doc, db):
    collector_type = getattr(db, "FilteredElementCollector", None) if db is not None else None
    link_instance_type = getattr(db, "RevitLinkInstance", None) if db is not None else None
    if collector_type is None or link_instance_type is None:
        return []
    try:
        return list(collector_type(doc).OfClass(link_instance_type).ToElements())
    except Exception:
        return []


def _instance_for_type(doc, link_type, db):
    """Return the first ``RevitLinkInstance`` placed from ``link_type``."""
    type_id = _element_id_int(getattr(link_type, "Id", None))
    if type_id is None:
        return None
    for instance in _link_instances(doc, db):
        try:
            if _element_id_int(instance.GetTypeId()) == type_id:
                return instance
        except Exception:
            continue
    return None


def element_from_id(doc, element_id_int, db=None):
    """``doc.GetElement`` for a Python int id, across Revit versions."""
    element_id_int = _safe_int(element_id_int)
    if doc is None or element_id_int is None:
        return None
    db = db if db is not None else _import_revit_db()
    element_id = _make_element_id(getattr(db, "ElementId", None), element_id_int)
    if element_id is None:
        element_id = element_id_int
    try:
        return doc.GetElement(element_id)
    except Exception:
        return None


def resolve_link_instance(doc, element_id_int, db=None):
    """Map a recorded element id to a ``RevitLinkInstance`` in ``doc``.

    Passive detection records whichever element Revit attached to the
    warning: usually the link instance, sometimes the ``RevitLinkType``.
    Returns ``(instance, error_message)``.
    """
    element_id_int = _safe_int(element_id_int)
    if element_id_int is None:
        return None, "Could not resolve the selected instance id."
    if doc is None:
        return None, "View Issues is unavailable because there is no active Revit document."

    db = db if db is not None else _import_revit_db()
    element = element_from_id(doc, element_id_int, db)
    if element is None:
        return None, "Element {} is no longer available.".format(element_id_int)

    if _is_link_instance(element, db):
        return element, ""

    if _is_link_type(element, db):
        instance = _instance_for_type(doc, element, db)
        if instance is None:
            return None, "Link type {} has no placed link instance in this document.".format(
                element_id_int
            )
        return instance, ""

    return None, "Element {} is not a Revit link.".format(element_id_int)


def _element_id_list(element_id, db):
    try:
        from System.Collections.Generic import List as ClrList

        ids = ClrList[db.ElementId]()
        ids.Add(element_id)
        return ids
    except Exception:
        return [element_id]


def _bounding_box_in_view(element, view):
    getter = getattr(element, "get_BoundingBox", None)
    if callable(getter):
        try:
            return getter(view)
        except Exception:
            return None
    try:
        return element.BoundingBox[view]
    except Exception:
        return None


def _ui_view_for(uidoc, view):
    view_id = _element_id_int(getattr(view, "Id", None))
    if view_id is None:
        return None
    try:
        ui_views = list(uidoc.GetOpenUIViews())
    except Exception:
        return None
    for ui_view in ui_views:
        try:
            if _element_id_int(ui_view.ViewId) == view_id:
                return ui_view
        except Exception:
            continue
    return None


def frame_element_in_active_view(uidoc, element):
    """Zoom the *current* view to ``element``; never open other views.

    Returns ``{"visible", "framed", "reason"}``.  ``visible`` is False when
    the element has no geometry in the active view (hidden, cropped out, or
    a sheet/schedule is active); ``framed`` is False when the zoom itself was
    unavailable.
    """
    outcome = {"visible": False, "framed": False, "reason": ""}
    if uidoc is None or element is None:
        outcome["reason"] = "no active Revit UI document"
        return outcome

    try:
        view = uidoc.ActiveView
    except Exception:
        view = None
    if view is None:
        outcome["reason"] = "no active view"
        return outcome

    bbox = _bounding_box_in_view(element, view)
    if bbox is None:
        outcome["reason"] = "not visible in the current view"
        return outcome
    outcome["visible"] = True

    ui_view = _ui_view_for(uidoc, view)
    if ui_view is None:
        outcome["reason"] = "current view window not found"
        return outcome
    try:
        ui_view.ZoomAndCenterRectangle(bbox.Min, bbox.Max)
    except Exception as ex:
        outcome["reason"] = "zoom failed: {}".format(_safe_text(ex) or "Unknown error")
        return outcome

    outcome["framed"] = True
    return outcome


def select_element(uidoc, element, db=None):
    """Select ``element`` in Revit and frame it in the current view.

    Returns ``{"selected", "visible", "framed", "message"}``.
    """
    result = {"selected": False, "visible": False, "framed": False, "message": ""}
    if uidoc is None or element is None:
        result["message"] = "Show is unavailable because there is no active Revit UI document."
        return result

    db = db if db is not None else _import_revit_db()
    element_id_int = _element_id_int(getattr(element, "Id", None))
    try:
        uidoc.Selection.SetElementIds(_element_id_list(element.Id, db))
        result["selected"] = True
    except Exception as ex:
        result["message"] = "Could not select element {}: {}".format(
            element_id_int, _safe_text(ex) or "Unknown error"
        )
        return result

    frame = frame_element_in_active_view(uidoc, element)
    result["visible"] = bool(frame.get("visible"))
    result["framed"] = bool(frame.get("framed"))
    if result["framed"]:
        result["message"] = "Element {} selected and framed in the current view.".format(element_id_int)
    elif result["visible"]:
        result["message"] = "Element {} selected.".format(element_id_int)
    else:
        result["message"] = (
            "Element {} selected; it is not visible in the current view, so switch to a view "
            "that shows it."
        ).format(element_id_int)
    return result
