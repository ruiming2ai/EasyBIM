# -*- coding: utf-8 -*-
"""Open Revit's built-in Coordination Review for one linked model.

Backs the ``View Issues`` button in the EasyBIM Coordination Review window.
The button resolves the recorded element to a ``RevitLinkInstance`` in the
active document, selects it, frames it in the *current* view, and posts the
native ``Collaborate > Coordinate > Coordination Review > Select Link``
command.  That Revit command always waits for one click on the linked model
in the drawing area (it ignores the current selection, and the API cannot
perform a pick), so the caller warns the user through ``before_post`` and the
link is framed where that click has to land.  Nothing here opens or searches
other views.

Revit runs posted commands only after control returns from the API context,
so the window closes itself first and the caller invokes
:func:`show_link_coordination_review` once ``ShowDialog`` has returned.

The Revit API is imported lazily (and can be injected) so the module loads
standalone under CPython for the unit tests.
"""

# ``PostableCommand`` members for "Coordination Review > Select Link", most
# recent first.  Revit 2022 renamed ``SelectLink`` to ``CoordinationSelectLink``
# (plain ``SelectLink`` no longer exists there); Revit 2021 and earlier only
# know ``SelectLink``.
COORDINATION_REVIEW_COMMANDS = ("CoordinationSelectLink", "SelectLink")


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


def _import_revit_ui():
    try:
        from Autodesk.Revit import UI

        return UI
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
    element_id = _make_element_id(getattr(db, "ElementId", None), element_id_int)
    if element_id is None:
        element_id = element_id_int

    try:
        element = doc.GetElement(element_id)
    except Exception:
        element = None
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


def select_link(uidoc, link_instance, db=None):
    """Make ``link_instance`` the current Revit selection."""
    if uidoc is None or link_instance is None:
        return False
    db = db if db is not None else _import_revit_db()
    try:
        uidoc.Selection.SetElementIds(_element_id_list(link_instance.Id, db))
        return True
    except Exception:
        return False


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


def frame_link_in_active_view(uidoc, link_instance):
    """Zoom the *current* view to ``link_instance``; never open other views.

    Returns ``{"visible", "framed", "reason"}``.  ``visible`` is False when
    the link has no geometry in the active view (hidden, cropped out, or a
    sheet/schedule is active) - Revit's pick cannot land there.  ``framed``
    is False when the zoom itself was unavailable; the pick still works if
    the user can see the link.
    """
    outcome = {"visible": False, "framed": False, "reason": ""}
    if uidoc is None or link_instance is None:
        outcome["reason"] = "no active Revit UI document"
        return outcome

    try:
        view = uidoc.ActiveView
    except Exception:
        view = None
    if view is None:
        outcome["reason"] = "no active view"
        return outcome

    bbox = _bounding_box_in_view(link_instance, view)
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


def resolve_coordination_review_command(ui=None):
    """Return ``(command_id, member_name)`` for the native Select Link command."""
    ui = ui if ui is not None else _import_revit_ui()
    if ui is None:
        return None, ""
    postable = getattr(ui, "PostableCommand", None)
    lookup = getattr(getattr(ui, "RevitCommandId", None), "LookupPostableCommandId", None)
    if postable is None or not callable(lookup):
        return None, ""
    for member_name in COORDINATION_REVIEW_COMMANDS:
        member = getattr(postable, member_name, None)
        if member is None:
            continue
        try:
            command_id = lookup(member)
        except Exception:
            command_id = None
        if command_id is not None:
            return command_id, member_name
    return None, ""


def _can_post(uiapp, command_id):
    can_post = getattr(uiapp, "CanPostCommand", None)
    if not callable(can_post):
        return True
    try:
        return bool(can_post(command_id))
    except Exception:
        return True


def post_coordination_review_command(uiapp, ui=None):
    """Post the native Coordination Review > Select Link command.

    The post is always attempted; ``CanPostCommand`` only sharpens the
    message when Revit refuses it.  Returns ``(posted, error_message)``.
    """
    if uiapp is None:
        return False, "View Issues is unavailable because there is no Revit application."
    command_id, member_name = resolve_coordination_review_command(ui)
    if command_id is None:
        return False, "Revit's Coordination Review command is not available in this version."
    try:
        uiapp.PostCommand(command_id)
    except Exception as ex:
        reason = _safe_text(ex) or "Unknown error"
        if not _can_post(uiapp, command_id):
            return False, "Revit cannot open Coordination Review right now ({}): {}".format(
                member_name, reason
            )
        return False, "Could not open Coordination Review ({}): {}".format(member_name, reason)
    return True, ""


def show_link_coordination_review(
    uiapp, uidoc, doc, element_id_int, db=None, ui=None, before_post=None
):
    """Select the recorded link, frame it, and start Revit's Coordination Review.

    Returns a dict with ``ok``, ``selected``, ``visible``, ``framed``,
    ``posted``, ``link_name`` and ``message``.  The command is posted only
    when the link is visible in the current view, because Revit's Select
    Link command then asks for one click on the link in the drawing area.
    ``before_post(result)`` runs just before posting so the caller can warn
    the user about that click; ``posted`` means the native command is
    queued and runs once the calling API context returns control.
    """
    result = {
        "ok": False,
        "selected": False,
        "visible": False,
        "framed": False,
        "posted": False,
        "link_name": "",
        "message": "",
    }

    link_instance, error = resolve_link_instance(doc, element_id_int, db=db)
    if link_instance is None:
        result["message"] = error
        return result
    result["link_name"] = _safe_text(getattr(link_instance, "Name", ""))
    link_label = result["link_name"] or "the selected link"

    result["selected"] = select_link(uidoc, link_instance, db=db)
    if not result["selected"]:
        result["message"] = "Could not select link {} in Revit.".format(
            result["link_name"] or _safe_text(element_id_int)
        )
        return result

    frame = frame_link_in_active_view(uidoc, link_instance)
    result["visible"] = bool(frame.get("visible"))
    result["framed"] = bool(frame.get("framed"))
    if not result["visible"]:
        result["message"] = (
            "Link {} is not visible in the current view, so Revit cannot pick it "
            "there. Open a view where the link is visible and click View Issues "
            "again. The link is selected in Revit."
        ).format(link_label)
        return result

    if callable(before_post):
        try:
            before_post(result)
        except Exception:
            pass

    posted, error = post_coordination_review_command(uiapp, ui=ui)
    result["posted"] = posted
    if not posted:
        result["message"] = "{} The link is selected in Revit.".format(error)
        return result

    result["ok"] = True
    result["message"] = (
        "Revit is asking you to click the highlighted link {} to open "
        "Coordination Review for it."
    ).format(link_label)
    return result
