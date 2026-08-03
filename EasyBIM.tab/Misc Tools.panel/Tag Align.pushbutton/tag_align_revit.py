# -*- coding: utf-8 -*-
"""Revit-facing logic for Tag Align.

Everything that touches the API lives here; the geometry is reduced to plain
numbers as early as possible and handed to ``tag_align_state``.

Two API surfaces changed shape over the supported Revit range and are probed
with ``getattr`` rather than pinned to a version:

* the tagged-element accessors (``GetTaggedLocalElementIds`` /
  ``GetTaggedReferences`` on 2022+, the ``TaggedLocalElementId`` /
  ``TaggedElementId`` properties before that), and
* the leader accessors (per-reference ``GetLeaderElbow`` /
  ``SetLeaderElbow`` / ``GetLeaderEnd`` / ``SetLeaderEnd`` on 2022+, the plain
  ``LeaderElbow`` / ``LeaderEnd`` properties before that).
"""

# pylint: disable=import-error,invalid-name,broad-except
import math

import clr

clr.AddReference("RevitAPIUI")

from Autodesk.Revit.UI.Selection import ISelectionFilter

from pyrevit import DB

from easybim.compat import eid_to_int as _eid_int
from easybim.compat import exception_text as _exception_text
from easybim.compat import safe_text as _safe_text

from tag_align_state import ANCHOR_BBOX
from tag_align_state import ANCHOR_CURVE_MID
from tag_align_state import ANCHOR_POINT
from tag_align_state import EPS
from tag_align_state import POSITION_TOL
from tag_align_state import SKIP_ALREADY_ALIGNED
from tag_align_state import SKIP_ALREADY_TAGGED
from tag_align_state import SKIP_ELEMENT_GONE
from tag_align_state import SKIP_EXTRA_TAGS
from tag_align_state import SKIP_MULTI_REF_TARGET
from tag_align_state import SKIP_NO_ANCHOR
from tag_align_state import SKIP_NO_TAG
from tag_align_state import SKIP_NOT_IN_VIEW
from tag_align_state import SKIP_PINNED
from tag_align_state import ProcessSummary
from tag_align_state import ReferenceRule
from tag_align_state import TargetInfo
from tag_align_state import compose_target_offset
from tag_align_state import compose_target_secondary
from tag_align_state import resolve_for_session


try:
    INVALID_EID = DB.ElementId.InvalidElementId
except Exception:
    INVALID_EID = None


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _normalize(vector):
    try:
        if vector is None or vector.GetLength() < EPS:
            return None
        return vector.Normalize()
    except Exception:
        return None


def _is_valid(element):
    return bool(element) and bool(getattr(element, "IsValidObject", True))


def element_type_label(doc, element):
    """``(family, type)`` names for anything that has a type.

    ``ElementType.FamilyName`` is the accessor that works across the board -
    it returns "Basic Wall" for a wall type and the loadable family's name for
    a family symbol - which is what makes the "paired family" scope mean the
    same thing for system and loadable families alike.
    """
    family_name = ""
    type_name = ""

    symbol = getattr(element, "Symbol", None)
    if symbol is not None:
        type_name = _safe_text(getattr(symbol, "Name", ""))
        family = getattr(symbol, "Family", None)
        if family is not None:
            family_name = _safe_text(getattr(family, "Name", ""))
        if not family_name:
            family_name = _safe_text(getattr(symbol, "FamilyName", ""))

    if not type_name or not family_name:
        try:
            element_type = doc.GetElement(element.GetTypeId())
        except Exception:
            element_type = None
        if element_type is not None:
            if not type_name:
                type_name = _safe_text(getattr(element_type, "Name", ""))
            if not family_name:
                family_name = _safe_text(getattr(element_type, "FamilyName", ""))
            if not family_name:
                try:
                    family_param = element_type.get_Parameter(
                        DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME
                    )
                    if family_param is not None:
                        family_name = _safe_text(family_param.AsString())
                except Exception:
                    pass

    if not family_name:
        category = getattr(element, "Category", None)
        family_name = _safe_text(getattr(category, "Name", "")) if category else ""

    return family_name.strip(), type_name.strip()


def category_of(element):
    category = getattr(element, "Category", None)
    if category is None:
        return None, ""
    return getattr(category, "Id", None), _safe_text(getattr(category, "Name", ""))


def make_length_formatter(doc):
    """Format a length in internal feet using the project's display units."""
    units = None
    try:
        units = doc.GetUnits()
    except Exception:
        units = None

    spec_id = getattr(getattr(DB, "SpecTypeId", None), "Length", None)
    if spec_id is None:
        spec_id = getattr(getattr(DB, "UnitType", None), "UT_Length", None)

    def _format(value):
        value = float(value or 0.0)
        if units is not None and spec_id is not None:
            for args in (
                (units, spec_id, value, False),
                (units, spec_id, value, False, False),
                (units, spec_id, value),
            ):
                try:
                    text = _safe_text(DB.UnitFormatUtils.Format(*args)).strip()
                    if text:
                        return text
                except Exception:
                    continue
        return "{0:.3f} ft".format(value)

    return _format


# ---------------------------------------------------------------------------
# tag / host reading, with version fallbacks
# ---------------------------------------------------------------------------


def is_independent_tag(element):
    tag_class = getattr(DB, "IndependentTag", None)
    return tag_class is not None and isinstance(element, tag_class)


def is_spatial_tag(element):
    """Room / Area / Space tags - deliberately out of scope for now."""
    for owner, name in (
        (DB, "RoomTag"),
        (DB, "AreaTag"),
        (DB, "SpaceTag"),
        (getattr(DB, "Architecture", None), "RoomTag"),
        (getattr(DB, "Architecture", None), "AreaTag"),
        (getattr(DB, "Mechanical", None), "SpaceTag"),
    ):
        if owner is None:
            continue
        tag_class = getattr(owner, name, None)
        if tag_class is not None and isinstance(element, tag_class):
            return True
    return False


def get_tagged_local_ids(tag):
    getter = getattr(tag, "GetTaggedLocalElementIds", None)
    if getter is not None:
        try:
            ids = [eid for eid in getter()]
            if ids:
                return ids
        except Exception:
            pass

    try:
        element_id = getattr(tag, "TaggedLocalElementId", None)
        if element_id is not None and _eid_int(element_id) not in (None, -1):
            return [element_id]
    except Exception:
        pass
    return []


def tagged_reference_count(tag):
    getter = getattr(tag, "GetTaggedReferences", None)
    if getter is not None:
        try:
            return len([reference for reference in getter()])
        except Exception:
            pass
    return len(get_tagged_local_ids(tag))


def tag_is_linked(tag):
    getter = getattr(tag, "GetTaggedReferences", None)
    if getter is not None:
        try:
            for reference in getter():
                linked = _eid_int(getattr(reference, "LinkedElementId", None))
                if linked is not None and linked > 0:
                    return True
            return False
        except Exception:
            pass

    try:
        link_element_id = getattr(tag, "TaggedElementId", None)
        if link_element_id is not None:
            link_instance = _eid_int(getattr(link_element_id, "LinkInstanceId", None))
            if link_instance is not None and link_instance > 0:
                return True
    except Exception:
        pass
    return False


def first_tagged_reference(tag):
    getter = getattr(tag, "GetTaggedReferences", None)
    if getter is not None:
        try:
            for reference in getter():
                return reference
        except Exception:
            pass
    return None


def _leader_call(tag, method_names, reference, *args):
    """Call the per-reference accessor if present, else the plain property."""
    for name in method_names:
        method = getattr(tag, name, None)
        if method is None:
            continue
        try:
            if reference is not None:
                return True, method(reference, *args)
            return True, method(*args)
        except Exception:
            continue
    return False, None


def get_leader_elbow(tag):
    reference = first_tagged_reference(tag)
    if reference is not None:
        has_elbow, value = _leader_call(tag, ("HasLeaderElbow",), reference)
        if has_elbow and not value:
            return None
        ok, elbow = _leader_call(tag, ("GetLeaderElbow",), reference)
        if ok:
            return elbow
    try:
        return tag.LeaderElbow
    except Exception:
        return None


def set_leader_elbow(tag, point):
    reference = first_tagged_reference(tag)
    if reference is not None:
        ok, _ = _leader_call(tag, ("SetLeaderElbow",), reference, point)
        if ok:
            return True
    try:
        tag.LeaderElbow = point
        return True
    except Exception:
        return False


def get_leader_end(tag):
    reference = first_tagged_reference(tag)
    if reference is not None:
        ok, end = _leader_call(tag, ("GetLeaderEnd",), reference)
        if ok:
            return end
    try:
        return tag.LeaderEnd
    except Exception:
        return None


def set_leader_end(tag, point):
    reference = first_tagged_reference(tag)
    if reference is not None:
        ok, _ = _leader_call(tag, ("SetLeaderEnd",), reference, point)
        if ok:
            return True
    try:
        tag.LeaderEnd = point
        return True
    except Exception:
        return False


def leader_end_is_free(tag):
    free = getattr(getattr(DB, "LeaderEndCondition", None), "Free", None)
    try:
        return tag.LeaderEndCondition == free
    except Exception:
        return False


# ---------------------------------------------------------------------------
# view frame, anchors and directions
# ---------------------------------------------------------------------------


class ViewFrame(object):
    def __init__(self, view):
        self.view = view
        self.view_id = getattr(view, "Id", None)
        self.right = _normalize(getattr(view, "RightDirection", None)) or DB.XYZ.BasisX
        self.up = _normalize(getattr(view, "UpDirection", None)) or DB.XYZ.BasisY
        self.normal = _normalize(getattr(view, "ViewDirection", None)) or DB.XYZ.BasisZ
        try:
            self.scale = int(view.Scale) or 1
        except Exception:
            self.scale = 1
        self.name = _safe_text(getattr(view, "Name", ""))

    def decompose(self, vector):
        """``(u, v, w)`` along Right / Up / ViewDirection."""
        return (
            vector.DotProduct(self.right),
            vector.DotProduct(self.up),
            vector.DotProduct(self.normal),
        )

    def compose(self, origin, u, v, w=0.0):
        return (
            origin
            + self.right.Multiply(float(u))
            + self.up.Multiply(float(v))
            + self.normal.Multiply(float(w))
        )


def get_anchor(element, view=None):
    """``(XYZ, anchor_kind)`` - the point every offset is measured from."""
    location = getattr(element, "Location", None)

    if isinstance(location, DB.LocationPoint):
        try:
            point = location.Point
            if point is not None:
                return point, ANCHOR_POINT
        except Exception:
            pass

    if isinstance(location, DB.LocationCurve):
        try:
            curve = location.Curve
            if curve is not None:
                return curve.Evaluate(0.5, True), ANCHOR_CURVE_MID
        except Exception:
            pass

    for target_view in (view, None):
        try:
            bbox = element.get_BoundingBox(target_view)
        except Exception:
            bbox = None
        if bbox is not None:
            try:
                return (
                    DB.XYZ(
                        (bbox.Min.X + bbox.Max.X) / 2.0,
                        (bbox.Min.Y + bbox.Max.Y) / 2.0,
                        (bbox.Min.Z + bbox.Max.Z) / 2.0,
                    ),
                    ANCHOR_BBOX,
                )
            except Exception:
                continue

    return None, None


def get_direction(element):
    """``(XYZ or None, axis_symmetric)`` - the element's reference direction.

    A location curve wins over a family instance's hand orientation because its
    tangent is what the eye reads as "the direction of the wall / pipe / beam",
    and because it is an undirected axis: a wall drawn right to left is the
    same wall, which is what ``axis_symmetric`` records.
    """
    location = getattr(element, "Location", None)
    if isinstance(location, DB.LocationCurve):
        try:
            curve = location.Curve
            derivatives = curve.ComputeDerivatives(0.5, True)
            tangent = _normalize(derivatives.BasisX)
            if tangent is not None:
                return tangent, True
        except Exception:
            pass
        try:
            curve = location.Curve
            tangent = _normalize(curve.GetEndPoint(1) - curve.GetEndPoint(0))
            if tangent is not None:
                return tangent, True
        except Exception:
            pass

    for attribute in ("HandOrientation", "FacingOrientation"):
        try:
            direction = _normalize(getattr(element, attribute, None))
            if direction is not None:
                return direction, False
        except Exception:
            continue

    for method_name in ("GetTransform", "GetTotalTransform"):
        method = getattr(element, method_name, None)
        if method is None:
            continue
        try:
            transform = method()
            direction = _normalize(getattr(transform, "BasisX", None))
            if direction is not None:
                return direction, False
        except Exception:
            continue

    return None, False


def direction_in_view(frame, element):
    """``(has_direction, angle_deg, axis_symmetric, (dx, dy))`` in view axes.

    An element whose axis runs straight into the screen (a riser in plan)
    projects to a point; that is reported as "no direction" so the caller falls
    back to the view axes instead of dividing by a vanishing length.
    """
    direction, axis_symmetric = get_direction(element)
    if direction is None:
        return False, 0.0, False, (1.0, 0.0)

    dx = direction.DotProduct(frame.right)
    dy = direction.DotProduct(frame.up)
    length = math.sqrt(dx * dx + dy * dy)
    if length < 1.0e-6:
        return False, 0.0, False, (1.0, 0.0)

    dx /= length
    dy /= length
    angle = math.degrees(math.atan2(dy, dx))
    return True, angle, axis_symmetric, (dx, dy)


def to_local(offset_view, basis):
    """View-axis ``(u, v)`` expressed in the element's own in-plane frame."""
    u, v = float(offset_view[0]), float(offset_view[1])
    dx, dy = basis
    return (u * dx + v * dy, -u * dy + v * dx)


# ---------------------------------------------------------------------------
# measuring a reference
# ---------------------------------------------------------------------------


def read_target_info(doc, frame, element):
    family_name, type_name = element_type_label(doc, element)
    category_id, _ = category_of(element)
    has_direction, angle, axis_symmetric, _ = direction_in_view(frame, element)
    try:
        type_id = element.GetTypeId()
    except Exception:
        type_id = None

    return TargetInfo(
        element_id=element.Id,
        category_id=category_id,
        family_name=family_name,
        type_id=type_id,
        type_label=type_name,
        has_direction=has_direction,
        angle_deg=angle,
        axis_symmetric=axis_symmetric,
        label="{0} : {1} [{2}]".format(family_name, type_name, _eid_int(element.Id)),
    )


def measure_reference(doc, tag, view=None):
    """Turn a picked tag into a :class:`ReferenceRule`.

    An unusable tag still comes back as a rule, carrying ``invalid_reason`` so
    the caller can report exactly which pick was dropped and why.
    """
    tag_type_label = ""
    tag_family_name = ""
    try:
        tag_family_name, tag_type_name = element_type_label(doc, tag)
        tag_type_label = "{0} : {1}".format(tag_family_name, tag_type_name).strip(" :")
        if not tag_type_label:
            tag_type = doc.GetElement(tag.GetTypeId())
            tag_type_label = _safe_text(getattr(tag_type, "Name", ""))
    except Exception:
        pass

    def _invalid(reason):
        return ReferenceRule(
            tag_id=getattr(tag, "Id", None),
            tag_type_label=tag_type_label,
            tag_family_name=tag_family_name,
            invalid_reason=reason,
        )

    if is_spatial_tag(tag):
        return _invalid("room, area and space tags are not supported yet")
    if not is_independent_tag(tag):
        return _invalid("not a taggable annotation this tool can read")
    if tag_is_linked(tag):
        return _invalid("labels an element in a linked model")

    reference_count = tagged_reference_count(tag)
    if reference_count > 1:
        return _invalid("labels {0} elements, so its anchor is ambiguous".format(reference_count))

    host_ids = get_tagged_local_ids(tag)
    if not host_ids:
        return _invalid("has no host element (orphaned tag)")

    host = doc.GetElement(host_ids[0])
    if not _is_valid(host):
        return _invalid("its host element no longer exists")

    if view is None:
        try:
            view = doc.GetElement(tag.OwnerViewId)
        except Exception:
            view = None
    if view is None:
        return _invalid("its owner view could not be read")

    frame = ViewFrame(view)

    anchor, anchor_kind = get_anchor(host, view)
    if anchor is None:
        return _invalid("its host element has no usable location")

    try:
        head = tag.TagHeadPosition
    except Exception:
        head = None
    if head is None:
        return _invalid("its head position could not be read")

    notes = []
    u, v, w = frame.decompose(head - anchor)
    has_direction, angle, axis_symmetric, basis = direction_in_view(frame, host)
    if not has_direction:
        notes.append("Host has no direction in this view; offset uses the view axes.")

    offset_view = (u, v)
    offset_local = to_local(offset_view, basis) if has_direction else (u, v)

    elbow_view = None
    elbow_local = None
    leader_end_view = None
    leader_end_local = None
    has_leader = False
    try:
        has_leader = bool(tag.HasLeader)
    except Exception:
        has_leader = False

    if has_leader:
        elbow = get_leader_elbow(tag)
        if elbow is not None:
            eu, ev, _ = frame.decompose(elbow - anchor)
            elbow_view = (eu, ev)
            elbow_local = to_local(elbow_view, basis) if has_direction else elbow_view
        if leader_end_is_free(tag):
            end = get_leader_end(tag)
            if end is not None:
                lu, lv, _ = frame.decompose(end - anchor)
                leader_end_view = (lu, lv)
                leader_end_local = to_local(leader_end_view, basis) if has_direction else leader_end_view

    family_name, type_name = element_type_label(doc, host)
    category_id, category_name = category_of(host)

    try:
        host_type_id = host.GetTypeId()
    except Exception:
        host_type_id = None

    try:
        tag_orientation = tag.TagOrientation
    except Exception:
        tag_orientation = None

    try:
        leader_end_condition = tag.LeaderEndCondition
    except Exception:
        leader_end_condition = None

    return ReferenceRule(
        tag_id=tag.Id,
        tag_type_id=tag.GetTypeId(),
        tag_type_label=tag_type_label,
        tag_family_name=tag_family_name,
        host_id=host.Id,
        host_category_id=category_id,
        host_category_name=category_name,
        host_family_name=family_name,
        host_type_id=host_type_id,
        host_type_label=type_name,
        view_id=frame.view_id,
        view_name=frame.name,
        view_scale=frame.scale,
        anchor_kind=anchor_kind,
        has_direction=has_direction,
        angle_deg=angle,
        axis_symmetric=axis_symmetric,
        offset_view=offset_view,
        offset_local=offset_local,
        offset_normal=w,
        tag_orientation=tag_orientation,
        has_leader=has_leader,
        leader_end_condition=leader_end_condition,
        elbow_view=elbow_view,
        elbow_local=elbow_local,
        leader_end_view=leader_end_view,
        leader_end_local=leader_end_local,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# view context - built once per processing pass
# ---------------------------------------------------------------------------


class ViewContext(object):
    """Per-view lookups the processing pass needs repeatedly.

    Building the visible-element set and the host-to-tags index once turns
    every per-element check into a dictionary hit instead of a fresh collector.
    """

    def __init__(self, doc, view):
        self.doc = doc
        self.view = view
        self.frame = ViewFrame(view)
        self._visible_ids = None
        self._tags_by_host = None

    @property
    def visible_ids(self):
        if self._visible_ids is None:
            ids = set()
            try:
                collector = DB.FilteredElementCollector(self.doc, self.view.Id)
                for element_id in collector.WhereElementIsNotElementType().ToElementIds():
                    value = _eid_int(element_id)
                    if value is not None:
                        ids.add(value)
            except Exception:
                pass
            self._visible_ids = ids
        return self._visible_ids

    @property
    def tags_by_host(self):
        if self._tags_by_host is None:
            index = {}
            tag_class = getattr(DB, "IndependentTag", None)
            if tag_class is not None:
                try:
                    collector = DB.FilteredElementCollector(self.doc, self.view.Id)
                    for tag in collector.OfClass(tag_class).WhereElementIsNotElementType():
                        for host_id in get_tagged_local_ids(tag):
                            key = _eid_int(host_id)
                            if key is None:
                                continue
                            index.setdefault(key, []).append(tag)
                except Exception:
                    pass
            self._tags_by_host = index
        return self._tags_by_host

    def invalidate_tags(self):
        self._tags_by_host = None

    def is_visible(self, element_id):
        value = _eid_int(element_id)
        return value is not None and value in self.visible_ids

    def tags_for(self, element_id):
        return list(self.tags_by_host.get(_eid_int(element_id), []))

    def register_tag(self, tag, host_id):
        key = _eid_int(host_id)
        if key is None:
            return
        self.tags_by_host.setdefault(key, []).append(tag)


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def _target_points(rule, target, match, context, session, anchor, existing_head):
    """``(head, elbow, leader_end)`` in model coordinates for one target."""
    frame = context.frame
    offset = compose_target_offset(
        rule, target, match.use_local_frame, frame.scale, session.scale_with_view
    )

    if existing_head is not None:
        _, _, depth = frame.decompose(existing_head - anchor)
    else:
        depth = rule.offset_normal

    head = frame.compose(anchor, offset[0], offset[1], depth)

    elbow = None
    leader_end = None
    if session.copy_leader:
        elbow_offset = compose_target_secondary(
            rule, target, match.use_local_frame, frame.scale, session.scale_with_view,
            "elbow_view", "elbow_local",
        )
        if elbow_offset is not None:
            elbow = frame.compose(anchor, elbow_offset[0], elbow_offset[1], depth)

        end_offset = compose_target_secondary(
            rule, target, match.use_local_frame, frame.scale, session.scale_with_view,
            "leader_end_view", "leader_end_local",
        )
        if end_offset is not None:
            leader_end = frame.compose(anchor, end_offset[0], end_offset[1], depth)

    return head, elbow, leader_end


def _apply_leader(tag, rule, elbow, leader_end):
    try:
        if bool(tag.HasLeader) != bool(rule.has_leader):
            tag.HasLeader = bool(rule.has_leader)
    except Exception:
        pass

    if not rule.has_leader:
        return

    if rule.leader_end_condition is not None:
        try:
            tag.LeaderEndCondition = rule.leader_end_condition
        except Exception:
            pass

    if leader_end is not None and leader_end_is_free(tag):
        set_leader_end(tag, leader_end)

    if elbow is not None:
        set_leader_elbow(tag, elbow)


def create_tag(context, session, host, rule, head, elbow, leader_end):
    """Create a tag on ``host`` at the planned head position."""
    doc = context.doc
    orientation = rule.tag_orientation
    if orientation is None:
        orientation = getattr(getattr(DB, "TagOrientation", None), "Horizontal", None)

    reference = DB.Reference(host)
    add_leader = bool(rule.has_leader)
    tag_mode = getattr(getattr(DB, "TagMode", None), "TM_ADDBY_CATEGORY", None)

    tag = None
    attempts = [
        lambda: DB.IndependentTag.Create(
            doc, rule.tag_type_id, context.view.Id, reference, add_leader, orientation, head
        ),
        lambda: DB.IndependentTag.Create(
            doc, context.view.Id, reference, add_leader, tag_mode, orientation, head
        ),
        lambda: doc.Create.NewTag(
            context.view, host, add_leader, tag_mode, orientation, head
        ),
    ]
    last_error = None
    for attempt in attempts:
        try:
            tag = attempt()
            if tag is not None:
                break
        except Exception as ex:
            last_error = ex
            continue

    if tag is None:
        raise RuntimeError(
            "Tag creation failed: {0}".format(_exception_text(last_error) or "unknown error")
        )

    if rule.tag_type_id is not None:
        try:
            if _eid_int(tag.GetTypeId()) != _eid_int(rule.tag_type_id):
                tag.ChangeTypeId(rule.tag_type_id)
        except Exception:
            pass

    try:
        tag.TagHeadPosition = head
    except Exception:
        pass

    if session.copy_leader:
        _apply_leader(tag, rule, elbow, leader_end)

    return tag


# ---------------------------------------------------------------------------
# plan then execute
# ---------------------------------------------------------------------------

ACTION_ALIGN = "align"
ACTION_CREATE = "create"


class PlannedItem(object):
    def __init__(self, action, host, tag, rule, label, head, elbow, leader_end,
                 retype=False):
        self.action = action
        self.host = host
        self.tag = tag
        self.rule = rule
        self.label = label
        self.head = head
        self.elbow = elbow
        self.leader_end = leader_end
        self.retype = bool(retype)


def _tag_family_name(doc, tag):
    family_name, _ = element_type_label(doc, tag)
    return family_name.strip().lower()


def _relevant_tags(doc, context, host, rule):
    """The host's tags that belong to the reference tag's family.

    Sorted so a tag of the exact reference type comes first - that is the one
    the user means when an element carries two sizes of the same tag family.
    """
    wanted_family = _safe_text(rule.tag_family_name).strip().lower()
    wanted_type = _eid_int(rule.tag_type_id)

    matches = []
    for tag in context.tags_for(host.Id):
        if not _is_valid(tag):
            continue
        if wanted_family and _tag_family_name(doc, tag) != wanted_family:
            continue
        matches.append(tag)

    matches.sort(key=lambda tag: 0 if _eid_int(tag.GetTypeId()) == wanted_type else 1)
    return matches


def build_plan(context, session, element_ids, progress=None):
    """Dry run: resolve every target and record what would happen.

    Returns ``(items, summary)``.  ``summary`` already carries the skips and the
    planned ``aligned`` / ``tagged`` counts, so the confirmation dialog shows
    exactly what the write will do.
    """
    doc = context.doc
    summary = ProcessSummary()
    items = []
    seen_hosts = set()

    element_ids = list(element_ids or [])
    total = len(element_ids)

    for index, element_id in enumerate(element_ids):
        if progress is not None and not progress(index, total):
            summary.cancelled = True
            return items, summary

        element = doc.GetElement(element_id)
        if not _is_valid(element):
            summary.considered += 1
            summary.skip(_eid_int(element_id), SKIP_ELEMENT_GONE)
            continue

        host, reason = resolve_picked_host(doc, element)
        if host is None:
            summary.considered += 1
            summary.skip(_eid_int(element_id), reason or SKIP_ELEMENT_GONE,
                         element_id=element_id)
            continue

        host_key = _eid_int(host.Id)
        if host_key in seen_hosts:
            continue
        seen_hosts.add(host_key)
        summary.considered += 1

        if not context.is_visible(host.Id):
            summary.skip(host_key, SKIP_NOT_IN_VIEW, element_id=host.Id)
            continue

        target = read_target_info(doc, context.frame, host)
        label = target.display_label()
        match = resolve_for_session(session, target)
        if not match.matched:
            summary.skip(label, match.skip_reason, element_id=host.Id)
            continue

        rule = match.rule
        anchor, _ = get_anchor(host, context.view)
        if anchor is None:
            summary.skip(label, SKIP_NO_ANCHOR, element_id=host.Id)
            continue

        tags = _relevant_tags(doc, context, host, rule)

        if not tags:
            if not session.create_tags:
                summary.skip(label, SKIP_NO_TAG, element_id=host.Id)
                continue
            head, elbow, leader_end = _target_points(
                rule, target, match, context, session, anchor, None
            )
            items.append(
                PlannedItem(
                    ACTION_CREATE, host, None, rule, label, head, elbow, leader_end,
                )
            )
            summary.tagged += 1
            continue

        if session.create_tags and not session.align_existing:
            summary.skip(label, SKIP_ALREADY_TAGGED, element_id=host.Id)
            continue

        tag = tags[0]
        if len(tags) > 1:
            summary.skip(label, SKIP_EXTRA_TAGS, element_id=host.Id)

        if tagged_reference_count(tag) > 1:
            summary.skip(label, SKIP_MULTI_REF_TARGET, element_id=host.Id)
            continue

        if not session.include_pinned and bool(getattr(tag, "Pinned", False)):
            summary.skip(label, SKIP_PINNED, element_id=tag.Id)
            continue

        try:
            existing_head = tag.TagHeadPosition
        except Exception:
            existing_head = None

        head, elbow, leader_end = _target_points(
            rule, target, match, context, session, anchor, existing_head
        )

        retype = False
        if session.change_tag_type and rule.tag_type_id is not None:
            try:
                retype = _eid_int(tag.GetTypeId()) != _eid_int(rule.tag_type_id)
            except Exception:
                retype = False

        in_place = (
            existing_head is not None
            and (head - existing_head).GetLength() <= POSITION_TOL
        )
        if in_place and not retype:
            summary.already_aligned += 1
            summary.skip(label, SKIP_ALREADY_ALIGNED, element_id=host.Id)
            continue

        items.append(
            PlannedItem(
                ACTION_ALIGN, host, tag, rule, label,
                head, elbow, leader_end, retype=retype,
            )
        )
        summary.aligned += 1

    return items, summary


def execute_plan(context, session, items, plan_summary, progress=None):
    """Apply a plan. Must run inside a transaction opened by the caller."""
    summary = ProcessSummary()
    summary.considered = plan_summary.considered
    summary.already_aligned = plan_summary.already_aligned
    summary.skipped = list(plan_summary.skipped)
    summary.notes = list(plan_summary.notes)

    items = list(items or [])
    total = len(items)

    for index, item in enumerate(items):
        if progress is not None and not progress(index, total):
            summary.cancelled = True
            return summary

        try:
            if item.action == ACTION_CREATE:
                tag = create_tag(
                    context, session, item.host, item.rule,
                    item.head, item.elbow, item.leader_end,
                )
                if tag is None:
                    raise RuntimeError("Revit returned no tag.")
                context.register_tag(tag, item.host.Id)
                summary.tagged += 1
                continue

            if item.retype and item.rule.tag_type_id is not None:
                item.tag.ChangeTypeId(item.rule.tag_type_id)
            if session.copy_leader:
                _apply_leader(item.tag, item.rule, item.elbow, item.leader_end)
            item.tag.TagHeadPosition = item.head
            summary.aligned += 1
        except Exception as ex:
            summary.fail(item.label, _exception_text(ex), element_id=item.host.Id)

    return summary


# ---------------------------------------------------------------------------
# selection filters
# ---------------------------------------------------------------------------


class TagOnlyFilter(ISelectionFilter):
    """Reference picking - only tags this tool can actually read."""

    def AllowElement(self, elem):
        try:
            return is_independent_tag(elem) or is_spatial_tag(elem)
        except Exception:
            return False

    def AllowReference(self, reference, position):
        del reference, position
        return False


class TargetFilter(ISelectionFilter):
    """Target picking - a tag of the reference type, or an element of its category."""

    def __init__(self, category_id, tag_type_id=None):
        self.category_value = _eid_int(category_id)
        self.tag_type_value = _eid_int(tag_type_id)

    def AllowElement(self, elem):
        try:
            if is_independent_tag(elem):
                if self.tag_type_value is None:
                    return True
                return _eid_int(elem.GetTypeId()) == self.tag_type_value
            category_id, _ = category_of(elem)
            return _eid_int(category_id) == self.category_value
        except Exception:
            return False

    def AllowReference(self, reference, position):
        del reference, position
        return False


def build_filter_entries(doc, element_ids):
    """Feed for the category -> family -> type filter tree."""
    entries = []
    for element_id in element_ids or []:
        element = doc.GetElement(element_id)
        if not _is_valid(element):
            continue
        _, category_name = category_of(element)
        family_name, type_name = element_type_label(doc, element)
        entries.append(
            {
                "element_id": element_id,
                "category": category_name,
                "family": family_name,
                "type": type_name,
            }
        )
    return entries


def resolve_picked_host(doc, element):
    """A picked tag resolves to its host; a picked element is its own host."""
    if element is None:
        return None, ""
    if is_independent_tag(element):
        if tag_is_linked(element):
            return None, "linked_tag"
        if tagged_reference_count(element) > 1:
            return None, "multi_ref_target"
        host_ids = get_tagged_local_ids(element)
        if not host_ids:
            return None, "element_gone"
        host = doc.GetElement(host_ids[0])
        if not _is_valid(host):
            return None, "element_gone"
        return host, ""
    if is_spatial_tag(element):
        return None, "spatial_tag"
    return element, ""
