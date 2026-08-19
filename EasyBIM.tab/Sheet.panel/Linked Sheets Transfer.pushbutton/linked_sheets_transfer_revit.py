# -*- coding: utf-8 -*-
"""Every Revit API call Linked Sheets Transfer makes.

Two rules govern this module.

**Measure last.**  A viewport is positioned from a snapshot of the view it
shows - origin, right/up, scale and ``View.Outline``.  Every one of those moves
when the crop, the annotation crop, the scale or a view template changes, so
the snapshot is taken only after the new view has been configured completely.
``View Align`` computes its anchor in a precheck pass and then writes the crop,
which is why its scope-box and crop options can misplace a viewport; nothing
here can hit that, because there is exactly one measurement and it is last.

**The host is never mutated by accident.**  Cross-document copies run with a
duplicate-type handler that answers ``UseDestinationTypes``, so a name clash
always resolves in favour of what this model already has.
"""

# pylint: disable=import-error,invalid-name,broad-except,too-many-lines
from __future__ import print_function

from pyrevit import DB

from easybim import sheet_geometry
from easybim.copy_paste import copy_paste_options
from easybim.compat import eid_to_int
from easybim.compat import element_id_factory
from easybim.compat import exception_text
from easybim.compat import safe_text

import linked_sheets_transfer_state as state


EPS = 1e-9
UNIT_TOLERANCE = 1e-6

# ViewType -> (ViewFamily name, label).  Only plan families are reproduced;
# everything else has either no model coordinates (drafting, legend, schedule)
# or is deferred to a later version (section, elevation, 3D).
_PLAN_VIEW_TYPES = {
    "FloorPlan": (u"FloorPlan", u"Floor Plan"),
    "CeilingPlan": (u"CeilingPlan", u"Ceiling Plan"),
    "EngineeringPlan": (u"StructuralPlan", u"Structural Plan"),
    "AreaPlan": (u"AreaPlan", u"Area Plan"),
}

_VIEW_TYPE_LABELS = {
    "FloorPlan": u"Floor Plan",
    "CeilingPlan": u"Ceiling Plan",
    "EngineeringPlan": u"Structural Plan",
    "AreaPlan": u"Area Plan",
    "Section": u"Section",
    "Elevation": u"Elevation",
    "Detail": u"Detail",
    "ThreeD": u"3D",
    "DraftingView": u"Drafting View",
    "Legend": u"Legend",
    "Schedule": u"Schedule",
}

_new_element_id = None


def _eid(value):
    """``ElementId`` from an int, across the 2015-2027 constructor change."""
    if value is None:
        return DB.ElementId.InvalidElementId
    global _new_element_id
    if _new_element_id is None:
        _new_element_id = element_id_factory(DB.ElementId)
    return _new_element_id(value)


def _id_list(element_ids):
    from System.Collections.Generic import List as ClrList
    collection = ClrList[DB.ElementId]()
    for element_id in element_ids:
        collection.Add(element_id)
    return collection


def _xyz(point):
    return DB.XYZ(float(point[0]), float(point[1]), float(point[2]))


def links_api_available():
    """``RevitLinkGraphicsSettings`` and ``View.SetLinkOverrides`` are 2024+."""
    return hasattr(DB, "RevitLinkGraphicsSettings")


# ---------------------------------------------------------------------------
# entry guards
# ---------------------------------------------------------------------------

def document_blocker(doc):
    if doc is None:
        return u"Open a project first."
    if getattr(doc, "IsFamilyDocument", False):
        return u"Linked Sheets Transfer works on a project, not a family."
    if getattr(doc, "IsLinked", False):
        return u"The active document is a link and cannot be written to."
    if getattr(doc, "IsReadOnly", False):
        return u"This model is read-only."
    if not links_api_available():
        return (u"Linked Sheets Transfer needs Revit 2024 or newer: setting a "
                u"link to 'By Linked View' has no API before that.")
    return u""


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------

class LinkOption(object):
    """One loaded Revit link instance, as offered in the picker."""

    def __init__(self, instance, link_doc, transform, label, blocker=u""):
        self.instance = instance
        self.instance_key = eid_to_int(instance.Id)
        self.doc = link_doc
        self.transform = transform
        self.label = safe_text(label)
        self.blocker = safe_text(blocker)

    @property
    def display(self):
        if self.blocker:
            return u"{0}  -  {1}".format(self.label, self.blocker)
        return self.label

    @property
    def is_usable(self):
        return not self.blocker


def _link_transform(link_instance):
    for method_name in ("GetTotalTransform", "GetTransform"):
        if not hasattr(link_instance, method_name):
            continue
        try:
            transform = getattr(link_instance, method_name)()
            if transform is not None:
                return transform
        except Exception:
            continue
    return DB.Transform.Identity


def link_transform_blocker(transform):
    """Why this link's placement cannot be reproduced as a host plan view.

    A plan can carry a rotation about Z - that is what a rotated crop region
    is - but it cannot be mirrored, tilted or scaled.  Catching these once, up
    front, is much kinder than letting every view come out subtly wrong.
    """
    if transform is None:
        return u""
    try:
        if bool(transform.HasReflection):
            return u"mirrored link: a plan view cannot be mirrored"
    except Exception:
        pass
    try:
        scale = float(transform.Scale)
        if abs(scale - 1.0) > UNIT_TOLERANCE:
            return u"scaled link ({0:.4f}x)".format(scale)
    except Exception:
        pass
    try:
        basis_z = transform.BasisZ
        if abs(float(basis_z.Z)) < 1.0 - 1e-6:
            return u"tilted link: a plan view cannot represent it"
    except Exception:
        pass
    return u""


def collect_link_options(doc):
    """Every loaded RVT link instance, with its own transform and blocker.

    Instances matter, not types: two instances of one link have different
    transforms, and ``SetLinkOverrides`` is keyed on the instance.
    """
    try:
        instances = list(
            DB.FilteredElementCollector(doc)
            .OfClass(DB.RevitLinkInstance)
            .WhereElementIsNotElementType()
            .ToElements())
    except Exception:
        instances = []

    by_title = {}
    rows = []
    for instance in instances:
        try:
            link_doc = instance.GetLinkDocument()
        except Exception:
            link_doc = None
        if link_doc is None:
            continue

        title = safe_text(getattr(link_doc, "Title", u"")) or u"(untitled link)"
        by_title.setdefault(title, []).append((instance, link_doc))

    for title in sorted(by_title, key=lambda text: text.lower()):
        entries = by_title[title]
        for instance, link_doc in entries:
            label = title
            if len(entries) > 1:
                label = u"{0}  ({1})".format(
                    title, safe_text(getattr(instance, "Name", u"")))
            transform = _link_transform(instance)
            rows.append(LinkOption(instance, link_doc, transform, label,
                                   link_transform_blocker(transform)))
    return rows


# ---------------------------------------------------------------------------
# reading the link
# ---------------------------------------------------------------------------

def _view_type_name(view):
    try:
        return safe_text(view.ViewType)
    except Exception:
        return u""


def _view_type_label(view):
    name = _view_type_name(view)
    return _VIEW_TYPE_LABELS.get(name, name or u"View")


def _title_block_label(symbol):
    if symbol is None:
        return u""
    try:
        family = safe_text(symbol.Family.Name)
    except Exception:
        family = u""
    try:
        type_name = safe_text(symbol.Name)
    except Exception:
        type_name = u""
    if family and type_name:
        return u"{0}: {1}".format(family, type_name)
    return family or type_name


def sheet_title_blocks(document, sheet):
    """Title block instances placed on one sheet, in element order."""
    try:
        return list(
            DB.FilteredElementCollector(document, sheet.Id)
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
            .ToElements())
    except Exception:
        return []


def _classify_view(view):
    """``(supported, view_family, reason)`` for a view seen in a viewport."""
    if view is None:
        return False, u"", u"The view no longer exists in the link"
    try:
        if bool(view.IsTemplate):
            return False, u"", u"View template"
    except Exception:
        pass

    type_name = _view_type_name(view)
    plan = _PLAN_VIEW_TYPES.get(type_name)
    if plan is None:
        return False, u"", u"{0} is not reproduced in this version".format(
            _VIEW_TYPE_LABELS.get(type_name, type_name or u"This view type"))
    if not isinstance(view, DB.ViewPlan):
        return False, u"", u"Not a plan view"
    return True, plan[0], u""


def _view_scale(view):
    try:
        scale = int(view.Scale)
    except Exception:
        return None
    return scale if scale > 0 else None


def _view_level_key(view):
    for attr in ("GenLevel", "LevelId"):
        try:
            value = getattr(view, attr)
        except Exception:
            continue
        if value is None:
            continue
        if attr == "GenLevel":
            key = eid_to_int(getattr(value, "Id", None))
        else:
            key = eid_to_int(value)
        if key not in (None, -1):
            return key
    return None


def _element_name(element):
    if element is None:
        return u""
    try:
        return safe_text(element.Name)
    except Exception:
        return u""


def _view_template_name(document, view):
    try:
        template_id = view.ViewTemplateId
    except Exception:
        return u""
    if eid_to_int(template_id) in (None, -1):
        return u""
    return _element_name(document.GetElement(template_id))


def _scope_box_name(document, view):
    bip = getattr(DB.BuiltInParameter, "VIEWER_VOLUME_OF_INTEREST_CROP", None)
    if bip is None:
        return u""
    try:
        param = view.get_Parameter(bip)
        if param is None:
            return u""
        box_id = param.AsElementId()
    except Exception:
        return u""
    if eid_to_int(box_id) in (None, -1):
        return u""
    return _element_name(document.GetElement(box_id))


def _area_scheme_name(view):
    try:
        scheme = view.AreaScheme
    except Exception:
        return u""
    return _element_name(scheme)


def collect_linked_sheets(link_doc):
    """Every sheet in the link, with one row per viewport and schedule."""
    try:
        sheets = list(
            DB.FilteredElementCollector(link_doc)
            .OfClass(DB.ViewSheet)
            .WhereElementIsNotElementType()
            .ToElements())
    except Exception:
        sheets = []

    rows = []
    for sheet in sorted(sheets, key=lambda item: (
            safe_text(getattr(item, "SheetNumber", u"")).lower(),
            safe_text(getattr(item, "Name", u"")).lower())):
        try:
            if bool(sheet.IsTemplate):
                continue
        except Exception:
            pass

        view_rows = []
        try:
            viewport_ids = list(sheet.GetAllViewports())
        except Exception:
            viewport_ids = []

        for viewport_id in viewport_ids:
            viewport = link_doc.GetElement(viewport_id)
            if viewport is None:
                continue
            try:
                view = link_doc.GetElement(viewport.ViewId)
            except Exception:
                view = None
            supported, view_family, reason = _classify_view(view)
            view_rows.append(state.ViewRow(
                viewport_key=eid_to_int(viewport.Id),
                view_key=eid_to_int(getattr(view, "Id", None)),
                view_name=_element_name(view),
                view_type_label=_view_type_label(view),
                supported=supported,
                skip_reason=reason,
                level_key=_view_level_key(view) if supported else None,
                scale=_view_scale(view) if supported else None,
                crop_active=bool(getattr(view, "CropBoxActive", False))
                if supported else True,
                template_name=_view_template_name(link_doc, view)
                if supported else u"",
                scope_box_name=_scope_box_name(link_doc, view)
                if supported else u"",
                area_scheme_name=_area_scheme_name(view) if supported else u"",
                view_family=view_family))

        # Schedules are not viewports, so they would otherwise vanish without
        # explanation.  They are listed as skipped instead.
        for instance in _schedule_instances(link_doc, sheet):
            schedule = link_doc.GetElement(instance.ScheduleId)
            view_rows.append(state.ViewRow(
                viewport_key=eid_to_int(instance.Id),
                view_key=eid_to_int(getattr(schedule, "Id", None)),
                view_name=_element_name(schedule) or u"(schedule)",
                view_type_label=u"Schedule",
                supported=False,
                skip_reason=u"A schedule cannot be copied from a link: it "
                            u"would read this model's data, not the link's"))

        title_blocks = sheet_title_blocks(link_doc, sheet)
        label = u""
        if title_blocks:
            try:
                label = _title_block_label(title_blocks[0].Symbol)
            except Exception:
                label = u""

        rows.append(state.SheetRow(
            sheet_key=eid_to_int(sheet.Id),
            number=safe_text(getattr(sheet, "SheetNumber", u"")),
            name=safe_text(getattr(sheet, "Name", u"")),
            view_rows=view_rows,
            is_placeholder=bool(getattr(sheet, "IsPlaceholder", False)),
            title_block_label=label,
            title_block_count=len(title_blocks)))
    return rows


def _schedule_instances(document, sheet):
    try:
        return list(
            DB.FilteredElementCollector(document, sheet.Id)
            .OfClass(DB.ScheduleSheetInstance)
            .ToElements())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# levels
# ---------------------------------------------------------------------------

def _levels(document):
    try:
        return list(
            DB.FilteredElementCollector(document)
            .OfClass(DB.Level)
            .WhereElementIsNotElementType()
            .ToElements())
    except Exception:
        return []


def collect_linked_levels(link_doc, transform):
    """Linked levels, each carrying its elevation in *host* coordinates."""
    rows = []
    for level in _levels(link_doc):
        try:
            elevation = float(level.Elevation)
        except Exception:
            continue
        try:
            host_elevation = float(
                transform.OfPoint(DB.XYZ(0.0, 0.0, elevation)).Z)
        except Exception:
            host_elevation = elevation
        rows.append(state.LinkedLevel(
            eid_to_int(level.Id), _element_name(level), host_elevation))
    rows.sort(key=lambda row: row.elevation_host)
    return rows


def collect_host_levels(doc):
    rows = []
    for level in _levels(doc):
        try:
            elevation = float(level.Elevation)
        except Exception:
            continue
        rows.append(state.HostLevelChoice(
            eid_to_int(level.Id), _element_name(level), elevation))
    rows.sort(key=lambda row: row.elevation)
    return rows


def monitored_level_pairs(doc, link_instance_key):
    """``{linked level key: [host level keys]}`` from Copy/Monitor.

    Filtered to the selected link *instance*: a host level monitoring the same
    level in another instance of the same link says nothing about this one.
    """
    pairs = {}
    for level in _levels(doc):
        try:
            if not level.IsMonitoringLinkElement():
                continue
            monitored = list(level.GetMonitoredLinkElementIds())
        except Exception:
            continue
        for link_element_id in monitored:
            try:
                instance_key = eid_to_int(link_element_id.LinkInstanceId)
                linked_key = eid_to_int(link_element_id.LinkedElementId)
            except Exception:
                continue
            if instance_key != link_instance_key:
                continue
            if linked_key in (None, -1):
                continue
            pairs.setdefault(linked_key, []).append(eid_to_int(level.Id))
    return pairs


# ---------------------------------------------------------------------------
# what the host model already has
# ---------------------------------------------------------------------------

def _names_of(collector):
    names = []
    try:
        for element in collector:
            name = _element_name(element)
            if name:
                names.append(name)
    except Exception:
        pass
    return names


def collect_host_facts(doc):
    facts = state.HostFacts(levels=collect_host_levels(doc))

    try:
        facts.sheet_numbers = set(
            safe_text(sheet.SheetNumber).strip().lower()
            for sheet in DB.FilteredElementCollector(doc)
            .OfClass(DB.ViewSheet).WhereElementIsNotElementType())
    except Exception:
        pass

    try:
        facts.view_names = set(
            _element_name(view)
            for view in DB.FilteredElementCollector(doc).OfClass(DB.View)
            if not bool(view.IsTemplate))
    except Exception:
        pass

    try:
        facts.view_templates = set(
            _element_name(view).strip().lower()
            for view in DB.FilteredElementCollector(doc).OfClass(DB.View)
            if bool(view.IsTemplate))
    except Exception:
        pass

    try:
        facts.title_block_types = set(
            _title_block_label(symbol).strip().lower()
            for symbol in DB.FilteredElementCollector(doc)
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsElementType())
    except Exception:
        pass

    try:
        facts.view_family_types = set(
            safe_text(vft.ViewFamily).strip().lower()
            for vft in DB.FilteredElementCollector(doc)
            .OfClass(DB.ViewFamilyType))
    except Exception:
        pass

    try:
        facts.viewport_types = set(
            name.strip().lower()
            for name in _names_of(_host_viewport_types(doc)))
    except Exception:
        pass

    try:
        facts.area_schemes = set(
            name.strip().lower()
            for name in _names_of(DB.FilteredElementCollector(doc)
                                  .OfClass(DB.AreaScheme)))
    except Exception:
        pass

    try:
        facts.scope_boxes = set(
            name.strip().lower()
            for name in _names_of(
                DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_VolumeOfInterest)
                .WhereElementIsNotElementType()))
    except Exception:
        pass

    return facts


def _host_viewport_types(document):
    try:
        return list(
            DB.FilteredElementCollector(document)
            .OfCategory(DB.BuiltInCategory.OST_Viewports)
            .WhereElementIsElementType()
            .ToElements())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# cross-document copying
# ---------------------------------------------------------------------------

class _WarningSwallower(DB.IFailuresPreprocessor):
    """Delete warnings so a batch is not interrupted; errors still surface."""

    def PreprocessFailures(self, failuresAccessor):
        try:
            for failure in list(failuresAccessor.GetFailureMessages()):
                if failure.GetSeverity() == DB.FailureSeverity.Warning:
                    failuresAccessor.DeleteWarning(failure)
        except Exception:
            pass
        return DB.FailureProcessingResult.Continue


def _start_transaction(doc, name):
    transaction = DB.Transaction(doc, name)
    transaction.Start()
    try:
        handling = transaction.GetFailureHandlingOptions()
        handling.SetFailuresPreprocessor(_WarningSwallower())
        handling.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(handling)
    except Exception:
        pass
    return transaction


def _copy_elements(source, element_ids, destination, transform, options):
    """Copy, falling back to one-by-one so one bad element is not fatal.

    Returns ``(copied_count, failures)``.
    """
    if not element_ids:
        return 0, []
    try:
        copied = DB.ElementTransformUtils.CopyElements(
            source, _id_list(element_ids), destination, transform, options)
        return len(list(copied)), []
    except Exception:
        pass

    copied_total = 0
    failures = []
    for element_id in element_ids:
        try:
            copied = DB.ElementTransformUtils.CopyElements(
                source, _id_list([element_id]), destination, transform,
                options)
            copied_total += len(list(copied))
        except Exception as copy_error:
            failures.append((element_id, exception_text(copy_error)))
    return copied_total, failures


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def _crop_center_model(view):
    """Centre of the view's crop box, in that view's model coordinates."""
    try:
        crop = view.CropBox
    except Exception:
        return None
    if crop is None:
        return None
    try:
        local = DB.XYZ((crop.Min.X + crop.Max.X) * 0.5,
                       (crop.Min.Y + crop.Max.Y) * 0.5,
                       (crop.Min.Z + crop.Max.Z) * 0.5)
    except Exception:
        return None
    try:
        transform = crop.Transform
    except Exception:
        transform = None
    if transform is None:
        return local
    try:
        return transform.OfPoint(local)
    except Exception:
        return local


def _crop_corner_model(view):
    """A crop-box corner, used as the second point of the alignment check.

    One matched point proves nothing about scale or rotation; a second one at
    the far end of the drawing proves both.
    """
    try:
        crop = view.CropBox
        local = DB.XYZ(crop.Min.X, crop.Min.Y,
                       (crop.Min.Z + crop.Max.Z) * 0.5)
        transform = crop.Transform
    except Exception:
        return None
    if transform is None:
        return local
    try:
        return transform.OfPoint(local)
    except Exception:
        return local


def _outline_rectangle_model(view):
    """The four model-space corners of what the view actually prints.

    Used when the linked view's crop is switched off: ``View.Outline`` is the
    printed extent whether or not the crop is active, and it is the only
    honest source for the size the new viewport should have.  Reversing the
    projection gives the corners::

        corner = Origin + (U * Scale) * Right + (V * Scale) * Up
    """
    try:
        origin = view.Origin
        right = view.RightDirection
        up = view.UpDirection
        scale = float(view.Scale)
        outline = view.Outline
    except Exception:
        return None
    if abs(scale) < EPS:
        return None
    try:
        bounds = ((outline.Min.U, outline.Min.V), (outline.Max.U, outline.Min.V),
                  (outline.Max.U, outline.Max.V), (outline.Min.U, outline.Max.V))
    except Exception:
        return None

    corners = []
    for u_val, v_val in bounds:
        try:
            corners.append(
                origin
                .Add(right.Multiply(float(u_val) * scale))
                .Add(up.Multiply(float(v_val) * scale)))
        except Exception:
            return None
    return corners


def build_host_crop_box(linked_view, transform):
    """The host crop box that reproduces the linked view's printed extent.

    The transform is composed rather than the extents recomputed, so a link
    rotated about Z produces a rotated crop region in the host - which is what
    keeps the drawing parallel to the linked sheet, not merely coincident at
    one point.
    """
    try:
        linked_crop = linked_view.CropBox
    except Exception:
        return None, u"The linked view has no crop box."
    if linked_crop is None:
        return None, u"The linked view has no crop box."

    try:
        source_transform = linked_crop.Transform or DB.Transform.Identity
        host_transform = transform.Multiply(source_transform)
    except Exception as build_error:
        return None, exception_text(build_error)

    box = DB.BoundingBoxXYZ()
    try:
        box.Transform = host_transform
    except Exception as build_error:
        return None, exception_text(build_error)

    crop_active = bool(getattr(linked_view, "CropBoxActive", False))
    if crop_active:
        try:
            box.Min = DB.XYZ(linked_crop.Min.X, linked_crop.Min.Y,
                             linked_crop.Min.Z)
            box.Max = DB.XYZ(linked_crop.Max.X, linked_crop.Max.Y,
                             linked_crop.Max.Z)
        except Exception as build_error:
            return None, exception_text(build_error)
        return box, u""

    corners = _outline_rectangle_model(linked_view)
    if not corners:
        return None, u"The linked view prints nothing measurable."
    try:
        inverse = host_transform.Inverse
        local = [inverse.OfPoint(transform.OfPoint(corner))
                 for corner in corners]
        min_x = min(point.X for point in local)
        max_x = max(point.X for point in local)
        min_y = min(point.Y for point in local)
        max_y = max(point.Y for point in local)
        box.Min = DB.XYZ(min_x, min_y, linked_crop.Min.Z)
        box.Max = DB.XYZ(max_x, max_y, linked_crop.Max.Z)
    except Exception as build_error:
        return None, exception_text(build_error)
    return box, u""


def _linked_crop_loops(linked_view):
    try:
        manager = linked_view.GetCropRegionShapeManager()
        return list(manager.GetCropShape())
    except Exception:
        return []


def _loop_is_rectangle(loop):
    try:
        curves = list(loop)
    except Exception:
        return True
    if len(curves) != 4:
        return False
    for curve in curves:
        if not isinstance(curve, DB.Line):
            return False
    return True


def _project_onto_view_plane(view, point):
    try:
        origin = view.Origin
        normal = view.ViewDirection
        offset = point.Subtract(origin)
        distance = offset.DotProduct(normal)
        return point.Subtract(normal.Multiply(distance))
    except Exception:
        return point


def apply_shape_edited_crop(host_view, linked_view, transform):
    """Reproduce a shape-edited crop, or say why it could not be.

    Only polyline loops are attempted: an arc would have to be rebuilt in the
    host view's plane and the failure mode is a silently wrong boundary, so it
    falls back to the rectangle instead.
    """
    loops = _linked_crop_loops(linked_view)
    if not loops:
        return False, u""
    loop = loops[0]
    if _loop_is_rectangle(loop):
        return False, u""

    try:
        curves = list(loop)
    except Exception:
        return False, u""

    points = []
    for curve in curves:
        if not isinstance(curve, DB.Line):
            return False, (u"The linked crop is shape-edited with curved "
                           u"edges; the rectangular crop was used instead.")
        try:
            points.append(_project_onto_view_plane(
                host_view, transform.OfPoint(curve.GetEndPoint(0))))
        except Exception:
            return False, (u"The shape-edited crop could not be transformed; "
                           u"the rectangular crop was used instead.")

    if len(points) < 3:
        return False, u""

    try:
        new_loop = DB.CurveLoop()
        for index in range(len(points)):
            start = points[index]
            end = points[(index + 1) % len(points)]
            if start.DistanceTo(end) < EPS:
                continue
            new_loop.Append(DB.Line.CreateBound(start, end))
        host_view.GetCropRegionShapeManager().SetCropShape(new_loop)
        return True, u""
    except Exception as shape_error:
        return False, (u"The shape-edited crop was refused ({0}); the "
                       u"rectangular crop was used instead.".format(
                           exception_text(shape_error)))


def _copy_annotation_crop(host_view, linked_view):
    try:
        source = linked_view.GetCropRegionShapeManager()
        target = host_view.GetCropRegionShapeManager()
    except Exception:
        return
    if source is None or target is None:
        return
    try:
        if not bool(target.CanHaveAnnotationCrop):
            return
        target.AnnotationCropActive = bool(source.AnnotationCropActive)
    except Exception:
        return
    for name in ("TopAnnotationCropOffset", "BottomAnnotationCropOffset",
                 "LeftAnnotationCropOffset", "RightAnnotationCropOffset"):
        try:
            setattr(target, name, float(getattr(source, name)))
        except Exception:
            continue


# ---------------------------------------------------------------------------
# creating the host view
# ---------------------------------------------------------------------------

def _view_family_type_id(doc, view_family_name, preferred_name=u""):
    best = None
    fallback = None
    wanted = safe_text(view_family_name).strip().lower()
    preferred = safe_text(preferred_name).strip().lower()
    try:
        types = list(DB.FilteredElementCollector(doc)
                     .OfClass(DB.ViewFamilyType).ToElements())
    except Exception:
        types = []
    for view_family_type in types:
        try:
            if safe_text(view_family_type.ViewFamily).strip().lower() != wanted:
                continue
        except Exception:
            continue
        if fallback is None:
            fallback = view_family_type
        if preferred and _element_name(
                view_family_type).strip().lower() == preferred:
            best = view_family_type
            break
    chosen = best or fallback
    return chosen.Id if chosen is not None else None


def _area_scheme_id(doc, name):
    wanted = safe_text(name).strip().lower()
    if not wanted:
        return None
    try:
        schemes = list(DB.FilteredElementCollector(doc)
                       .OfClass(DB.AreaScheme).ToElements())
    except Exception:
        schemes = []
    for scheme in schemes:
        if _element_name(scheme).strip().lower() == wanted:
            return scheme.Id
    return None


def _element_by_name(collector, name):
    wanted = safe_text(name).strip().lower()
    if not wanted:
        return None
    try:
        for element in collector:
            if _element_name(element).strip().lower() == wanted:
                return element
    except Exception:
        return None
    return None


def _copy_view_appearance(host_view, linked_view, notes):
    """Scale first, then the graphic settings that do not move the outline."""
    scale = _view_scale(linked_view)
    if scale:
        try:
            host_view.Scale = scale
        except Exception:
            notes.append(u"Scale {0} could not be set (a view template may "
                         u"lock it).".format(scale))

    for attribute in ("DetailLevel", "Discipline", "DisplayStyle",
                      "PartsVisibility"):
        try:
            setattr(host_view, attribute, getattr(linked_view, attribute))
        except Exception:
            continue


_PLAN_VIEW_RANGE_SENTINELS = ("Unlimited", "LevelBelow", "LevelAbove",
                              "Current")


def _sentinel_ids():
    ids = []
    for name in _PLAN_VIEW_RANGE_SENTINELS:
        value = getattr(DB.PlanViewRange, name, None)
        key = eid_to_int(value) if value is not None else None
        if key is not None:
            ids.append(key)
    return set(ids)


def _copy_view_range(host_view, linked_view, level_lookup, notes):
    """Remap the linked view range onto host levels, offsets adjusted.

    The offset is measured from the level it names, so substituting a host
    level at a different elevation has to move the offset by the same amount
    or the plane lands somewhere else.
    """
    try:
        source = linked_view.GetViewRange()
        target = host_view.GetViewRange()
    except Exception:
        return
    if source is None or target is None:
        return

    sentinels = _sentinel_ids()
    planes = []
    for name in ("TopClipPlane", "CutPlane", "BottomClipPlane",
                 "ViewDepthPlane"):
        plane = getattr(DB.PlanViewPlane, name, None)
        if plane is not None:
            planes.append((name, plane))

    changed = False
    for name, plane in planes:
        try:
            level_id = source.GetLevelId(plane)
            offset = float(source.GetOffset(plane))
        except Exception:
            continue
        key = eid_to_int(level_id)
        if key in sentinels:
            try:
                target.SetLevelId(plane, level_id)
                target.SetOffset(plane, offset)
                changed = True
            except Exception:
                pass
            continue

        mapped = level_lookup.get(key)
        if mapped is None:
            notes.append(u"View range plane {0} keeps this model's default: "
                         u"its level is not mapped.".format(name))
            continue
        try:
            target.SetLevelId(plane, _eid(mapped[0]))
            target.SetOffset(plane, offset + mapped[1])
            changed = True
        except Exception:
            notes.append(u"View range plane {0} could not be set.".format(name))

    if not changed:
        return
    try:
        host_view.SetViewRange(target)
    except Exception as range_error:
        notes.append(u"The view range was refused: {0}".format(
            exception_text(range_error)))


def create_host_view(doc, linked_view, view_family, host_level_id, target_name,
                     transform, options, level_lookup, notes):
    """Build and fully configure one host view.  Nothing is measured here."""
    preferred_type = u""
    try:
        preferred_type = _element_name(
            linked_view.Document.GetElement(linked_view.GetTypeId()))
    except Exception:
        preferred_type = u""

    if view_family == u"AreaPlan":
        scheme_id = _area_scheme_id(doc, _area_scheme_name(linked_view))
        if scheme_id is None:
            return None, u"This model has no matching area scheme."
        try:
            host_view = DB.ViewPlan.CreateAreaPlan(doc, scheme_id,
                                                   host_level_id)
        except Exception as create_error:
            return None, exception_text(create_error)
    else:
        view_family_type_id = _view_family_type_id(doc, view_family,
                                                   preferred_type)
        if view_family_type_id is None:
            return None, u"This model has no view type for {0}.".format(
                view_family)
        try:
            host_view = DB.ViewPlan.Create(doc, view_family_type_id,
                                           host_level_id)
        except Exception as create_error:
            return None, exception_text(create_error)

    try:
        host_view.Name = target_name
    except Exception:
        notes.append(u"The view could not be named '{0}'.".format(target_name))

    # A template can lock scale and crop, so it goes on before either.
    if options.get(state.OPT_VIEW_TEMPLATE):
        template = _element_by_name(
            [view for view in DB.FilteredElementCollector(doc).OfClass(DB.View)
             if bool(view.IsTemplate)],
            _view_template_name(linked_view.Document, linked_view))
        if template is not None:
            try:
                host_view.ViewTemplateId = template.Id
            except Exception:
                notes.append(u"View template '{0}' could not be applied.".format(
                    _element_name(template)))

    _copy_view_appearance(host_view, linked_view, notes)
    _copy_view_range(host_view, linked_view, level_lookup, notes)

    if options.get(state.OPT_SCOPE_BOX):
        _apply_scope_box(doc, host_view, linked_view, notes)

    reason = _apply_crop(host_view, linked_view, transform, notes)
    if reason:
        notes.append(reason)

    return host_view, u""


def _apply_scope_box(doc, host_view, linked_view, notes):
    name = _scope_box_name(linked_view.Document, linked_view)
    if not name:
        return
    bip = getattr(DB.BuiltInParameter, "VIEWER_VOLUME_OF_INTEREST_CROP", None)
    if bip is None:
        return
    box = _element_by_name(
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_VolumeOfInterest)
        .WhereElementIsNotElementType(),
        name)
    if box is None:
        notes.append(u"No scope box named '{0}' in this model.".format(name))
        return
    try:
        parameter = host_view.get_Parameter(bip)
        if parameter is not None and not parameter.IsReadOnly:
            parameter.Set(box.Id)
    except Exception:
        notes.append(u"Scope box '{0}' could not be assigned.".format(name))


def _apply_crop(host_view, linked_view, transform, notes):
    box, reason = build_host_crop_box(linked_view, transform)
    if box is None:
        return reason

    try:
        host_view.CropBoxActive = True
    except Exception:
        return u"The crop could not be switched on."

    try:
        host_view.CropBox = box
    except Exception as crop_error:
        return u"The crop box was refused: {0}".format(
            exception_text(crop_error))

    if not bool(getattr(linked_view, "CropBoxActive", False)):
        notes.append(u"Crop is off in the linked view; it was switched on "
                     u"here so the viewport keeps the printed size.")

    try:
        host_view.CropBoxVisible = bool(linked_view.CropBoxVisible)
    except Exception:
        pass

    _, shape_note = apply_shape_edited_crop(host_view, linked_view, transform)
    if shape_note:
        notes.append(shape_note)

    _copy_annotation_crop(host_view, linked_view)
    return u""


def set_by_linked_view(host_view, link_instance_id, linked_view_id):
    """Point the link's V/G at the linked view.  This is the whole point."""
    if not links_api_available():
        return u"RVT link overrides need Revit 2024 or newer."
    try:
        settings = DB.RevitLinkGraphicsSettings()
    except Exception as settings_error:
        return exception_text(settings_error)

    # LinkedViewId first: Revit validates the pair, and ByLinkView without a
    # view is not a legal state to pass through.
    last_error = None
    for order in (("LinkedViewId", "LinkVisibilityType"),
                  ("LinkVisibilityType", "LinkedViewId")):
        try:
            for attribute in order:
                if attribute == "LinkedViewId":
                    settings.LinkedViewId = linked_view_id
                else:
                    settings.LinkVisibilityType = DB.LinkVisibility.ByLinkView
            host_view.SetLinkOverrides(link_instance_id, settings)
            return u""
        except Exception as apply_error:
            last_error = apply_error
    return u"'By Linked View' was refused: {0}".format(
        exception_text(last_error))


def hide_other_links(doc, host_view, keep_instance_key):
    ids = []
    try:
        for instance in (DB.FilteredElementCollector(doc)
                         .OfClass(DB.RevitLinkInstance)
                         .WhereElementIsNotElementType()):
            if eid_to_int(instance.Id) == keep_instance_key:
                continue
            if bool(instance.CanBeHidden(host_view)):
                ids.append(instance.Id)
    except Exception:
        return
    if not ids:
        return
    try:
        host_view.HideElements(_id_list(ids))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# placing and aligning the viewport
# ---------------------------------------------------------------------------

def _viewport_type_id(doc, linked_viewport, link_doc, options, notes):
    """A host viewport type with the linked one's name, copied if missing."""
    try:
        linked_type = link_doc.GetElement(linked_viewport.GetTypeId())
    except Exception:
        linked_type = None
    name = _element_name(linked_type)
    if not name:
        return None

    existing = _element_by_name(_host_viewport_types(doc), name)
    if existing is not None:
        return existing.Id

    if not options.get(state.OPT_VIEWPORT_TYPE):
        return None

    try:
        copied = DB.ElementTransformUtils.CopyElements(
            link_doc, _id_list([linked_type.Id]), doc, DB.Transform.Identity,
            copy_paste_options())
        for element_id in list(copied):
            element = doc.GetElement(element_id)
            if element is not None and _element_name(element) == name:
                return element.Id
    except Exception:
        pass

    notes.append(u"Viewport type '{0}' is not in this model and could not be "
                 u"copied; the default was used.".format(name))
    return None


def _box_size(viewport):
    try:
        outline = viewport.GetBoxOutline()
        low = outline.MinimumPoint
        high = outline.MaximumPoint
        return (float(high.X - low.X), float(high.Y - low.Y))
    except Exception:
        return None


class Placement(object):
    def __init__(self, viewport, deviation_mm=None, size_note=u""):
        self.viewport = viewport
        self.deviation_mm = deviation_mm
        self.size_note = size_note


def place_and_align(doc, host_sheet, host_view, linked_viewport, linked_view,
                    transform, options, link_doc, notes):
    """Create the viewport and move it until the model matches the link.

    Every configuration write is already done by the time this runs, so the
    single projection taken here is measured against the view Revit will
    actually print.
    """
    anchor_link = _crop_center_model(linked_view)
    if anchor_link is None:
        return None, u"The linked view has no crop box to anchor on."

    reference, reason = sheet_geometry.build_projection(linked_view,
                                                        linked_viewport)
    if reference is None:
        return None, u"The linked viewport cannot be measured: {0}".format(
            reason)

    anchor_link_xyz = sheet_geometry.to_xyz(anchor_link)
    target_sheet_point = reference.project(anchor_link_xyz)

    # View.Outline is derived, so it is stale until the crop, scale and
    # template writes have been regenerated.  Measuring a stale outline is
    # exactly how a viewport ends up confidently misplaced.
    try:
        doc.Regenerate()
    except Exception:
        pass

    try:
        if not DB.Viewport.CanAddViewToSheet(doc, host_sheet.Id, host_view.Id):
            return None, u"Revit will not put this view on the sheet."
        viewport = DB.Viewport.Create(doc, host_sheet.Id, host_view.Id,
                                      DB.XYZ.Zero)
    except Exception as create_error:
        return None, exception_text(create_error)
    if viewport is None:
        return None, u"The viewport could not be created."

    try:
        viewport.Rotation = linked_viewport.Rotation
    except Exception:
        notes.append(u"The viewport rotation could not be matched.")

    type_id = _viewport_type_id(doc, linked_viewport, link_doc, options, notes)
    if type_id is not None:
        try:
            viewport.ChangeTypeId(type_id)
        except Exception:
            notes.append(u"The viewport type could not be matched.")

    # Rotation and the viewport type both reshape the box, so the second
    # regeneration is what makes this measurement the final one.
    try:
        doc.Regenerate()
    except Exception:
        pass

    projection, reason = sheet_geometry.build_projection(host_view, viewport)
    if projection is None:
        return None, u"The new viewport cannot be measured: {0}".format(reason)

    # Taken now rather than after the move: moving a box does not resize it,
    # and this way it comes from the same regenerated state as the projection.
    size_note = _size_mismatch_note(viewport, linked_viewport)

    try:
        anchor_host = sheet_geometry.to_xyz(transform.OfPoint(anchor_link))
    except Exception:
        return None, u"The anchor point could not be converted to this model."

    new_center = projection.box_center_for(anchor_host, target_sheet_point)
    try:
        viewport.SetBoxCenter(_xyz(new_center))
    except Exception as move_error:
        return None, exception_text(move_error)

    if options.get(state.OPT_VIEW_TITLE):
        _match_view_title(viewport, linked_viewport, notes)

    deviation = _second_point_deviation(
        reference, projection, transform, linked_view, target_sheet_point,
        anchor_host)
    return Placement(viewport, deviation, size_note), u""


def _second_point_deviation(reference, projection, transform, linked_view,
                            target_sheet_point, anchor_host):
    """How far a second model point misses, in printed millimetres.

    Matching one point proves the translation; it says nothing about scale or
    rotation.  A corner of the crop box is as far from the anchor as the
    drawing goes, so it is where any mismatch shows up largest.
    """
    corner_link = _crop_corner_model(linked_view)
    if corner_link is None:
        return None
    try:
        corner_link_xyz = sheet_geometry.to_xyz(corner_link)
        corner_host = sheet_geometry.to_xyz(transform.OfPoint(corner_link))
    except Exception:
        return None
    if corner_link_xyz is None or corner_host is None:
        return None

    expected = reference.project(corner_link_xyz)

    # The viewport has already moved, so shift the pre-move projection by the
    # same delta rather than re-reading a box centre mid-transaction.
    before = projection.project(anchor_host)
    delta_x = target_sheet_point[0] - before[0]
    delta_y = target_sheet_point[1] - before[1]
    actual_raw = projection.project(corner_host)
    actual = (actual_raw[0] + delta_x, actual_raw[1] + delta_y, actual_raw[2])
    return sheet_geometry.deviation_mm(expected, actual)


def _size_mismatch_note(viewport, linked_viewport):
    host_size = _box_size(viewport)
    linked_size = _box_size(linked_viewport)
    if not host_size or not linked_size:
        return u""
    if (abs(host_size[0] - linked_size[0]) < 1.0 / 64.0
            and abs(host_size[1] - linked_size[1]) < 1.0 / 64.0):
        return u""
    return (u"The viewport is {0:.1f} x {1:.1f} mm where the linked one is "
            u"{2:.1f} x {3:.1f} mm - the crop did not reproduce exactly."
            .format(sheet_geometry.feet_to_mm(host_size[0]),
                    sheet_geometry.feet_to_mm(host_size[1]),
                    sheet_geometry.feet_to_mm(linked_size[0]),
                    sheet_geometry.feet_to_mm(linked_size[1])))


def _match_view_title(viewport, linked_viewport, notes):
    try:
        offset = linked_viewport.LabelOffset
        viewport.LabelOffset = DB.XYZ(offset.X, offset.Y, offset.Z)
    except Exception:
        notes.append(u"The view title position could not be matched (the "
                     u"viewport type may have no title).")
    try:
        viewport.LabelLineLength = float(linked_viewport.LabelLineLength)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# sheet content
# ---------------------------------------------------------------------------

def _host_title_block_type_id(doc, link_doc, label, options, notes):
    """The host type matching the linked title block, copied if missing."""
    if not label:
        return DB.ElementId.InvalidElementId

    existing = None
    try:
        for symbol in (DB.FilteredElementCollector(doc)
                       .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                       .WhereElementIsElementType()):
            if _title_block_label(symbol).strip().lower() == label.strip().lower():
                existing = symbol
                break
    except Exception:
        existing = None
    if existing is not None:
        return existing.Id

    if not options.get(state.OPT_TITLE_BLOCK):
        return DB.ElementId.InvalidElementId

    source_symbol = None
    try:
        for symbol in (DB.FilteredElementCollector(link_doc)
                       .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                       .WhereElementIsElementType()):
            if _title_block_label(symbol).strip().lower() == label.strip().lower():
                source_symbol = symbol
                break
    except Exception:
        source_symbol = None
    if source_symbol is None:
        notes.append(u"Title block '{0}' was not found in the link.".format(
            label))
        return DB.ElementId.InvalidElementId

    try:
        copied = DB.ElementTransformUtils.CopyElements(
            link_doc, _id_list([source_symbol.Id]), doc,
            DB.Transform.Identity, copy_paste_options())
        for element_id in list(copied):
            element = doc.GetElement(element_id)
            if element is None:
                continue
            if _title_block_label(element).strip().lower() == label.strip().lower():
                return element.Id
    except Exception as copy_error:
        notes.append(u"Title block '{0}' could not be copied: {1}".format(
            label, exception_text(copy_error)))
        return DB.ElementId.InvalidElementId

    # The family landed but the type name did not round-trip; take any type of
    # that family rather than leaving the sheet bare.
    try:
        family_name = label.split(u":")[0].strip().lower()
        for symbol in (DB.FilteredElementCollector(doc)
                       .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                       .WhereElementIsElementType()):
            if _title_block_label(symbol).split(
                    u":")[0].strip().lower() == family_name:
                return symbol.Id
    except Exception:
        pass
    return DB.ElementId.InvalidElementId


def _position_title_block(doc, host_sheet, linked_sheet, link_doc, notes):
    """Put the host title block where the linked one sits on its sheet.

    Sheet coordinates are absolute, and the viewports are aligned to the
    linked sheet's absolute coordinates.  If the two title blocks do not sit
    at the same place, every drawing lands at the right *sheet* coordinate and
    the wrong *title-block* coordinate - which is precisely the failure this
    tool exists to avoid.
    """
    linked_blocks = sheet_title_blocks(link_doc, linked_sheet)
    host_blocks = sheet_title_blocks(doc, host_sheet)
    if not linked_blocks or not host_blocks:
        return
    try:
        source_point = linked_blocks[0].Location.Point
        target_point = host_blocks[0].Location.Point
    except Exception:
        return
    try:
        shift = source_point.Subtract(target_point)
        if shift.GetLength() < EPS:
            return
        DB.ElementTransformUtils.MoveElement(doc, host_blocks[0].Id, shift)
    except Exception as move_error:
        notes.append(u"The title block could not be moved to the linked "
                     u"position: {0}".format(exception_text(move_error)))


_SKIP_PARAMETERS = frozenset([
    "SHEET_NUMBER", "SHEET_NAME", "VIEW_NAME", "VIEWER_SHEET_NUMBER",
    "SHEET_CURRENT_REVISION", "SHEET_CURRENT_REVISION_DATE",
    "SHEET_CURRENT_REVISION_DESCRIPTION", "SHEET_CURRENT_REVISION_ISSUED_BY",
    "SHEET_CURRENT_REVISION_ISSUED_TO", "SHEET_ISSUE_DATE",
    "ELEM_FAMILY_PARAM", "ELEM_TYPE_PARAM", "ELEM_FAMILY_AND_TYPE_PARAM",
])


def _parameter_key(parameter):
    try:
        definition = parameter.Definition
    except Exception:
        return None
    try:
        builtin = definition.BuiltInParameter
        name = safe_text(builtin)
        if name and name != "INVALID":
            return name
    except Exception:
        pass
    return safe_text(getattr(definition, "Name", u""))


def copy_parameters(source_element, target_element):
    """Copy writable same-name parameters.  Returns the count and the misses.

    ``ElementId`` parameters are never copied: an id means nothing in another
    document, and writing one would point at whatever happens to share that
    number here.
    """
    copied = 0
    missing = []
    try:
        parameters = list(source_element.Parameters)
    except Exception:
        return 0, []

    for parameter in parameters:
        key = _parameter_key(parameter)
        if not key or key in _SKIP_PARAMETERS:
            continue
        try:
            if parameter.IsReadOnly or not parameter.HasValue:
                continue
            storage = parameter.StorageType
        except Exception:
            continue
        # An ElementId means nothing in another document, so it is never
        # copied: writing one would point at whatever shares that number here.
        if storage == DB.StorageType.ElementId:
            continue
        if storage == getattr(DB.StorageType, "None", None):
            continue

        name = safe_text(getattr(parameter.Definition, "Name", u""))
        try:
            target = target_element.LookupParameter(name)
        except Exception:
            target = None
        if target is None:
            missing.append(name)
            continue
        try:
            if target.IsReadOnly or target.StorageType != storage:
                continue
            if storage == DB.StorageType.String:
                target.Set(parameter.AsString() or u"")
            elif storage == DB.StorageType.Double:
                target.Set(parameter.AsDouble())
            elif storage == DB.StorageType.Integer:
                target.Set(parameter.AsInteger())
            copied += 1
        except Exception:
            continue
    return copied, missing


_SHEET_CONTENT_EXCLUDED_CLASSES = ("Viewport", "ScheduleSheetInstance",
                                   "RevisionCloud")


def sheet_detailing_ids(link_doc, linked_sheet, include_revision_clouds):
    """What can be copied off a linked sheet: not views, not the title block.

    Revision clouds are excluded unless asked for, because a cloud drags its
    revision onto the target sheet and this model's revisions are not the
    link's.
    """
    try:
        elements = list(
            DB.FilteredElementCollector(link_doc, linked_sheet.Id)
            .WhereElementIsNotElementType().ToElements())
    except Exception:
        return []

    title_block_category = None
    try:
        title_block_category = eid_to_int(
            DB.Category.GetCategory(
                link_doc, DB.BuiltInCategory.OST_TitleBlocks).Id)
    except Exception:
        title_block_category = None

    sheet_key = eid_to_int(linked_sheet.Id)
    ids = []
    for element in elements:
        if isinstance(element, DB.Viewport):
            continue
        if isinstance(element, DB.ScheduleSheetInstance):
            continue
        if isinstance(element, DB.RevisionCloud) and not include_revision_clouds:
            continue
        category = getattr(element, "Category", None)
        if category is None:
            continue
        if title_block_category is not None and eid_to_int(
                category.Id) == title_block_category:
            continue
        try:
            if eid_to_int(element.OwnerViewId) != sheet_key:
                continue
        except Exception:
            continue
        ids.append(element.Id)
    return ids


_ANNOTATION_EXCLUDED = ("IndependentTag", "Dimension", "SpotDimension",
                        "RoomTag", "SpaceTag", "AreaTag", "ReferenceViewer")


def view_annotation_ids(link_doc, linked_view):
    """View-specific elements worth copying out of a linked view.

    Tags and dimensions are excluded on purpose: both are references to model
    elements that do not exist in this document, so a copy is either refused
    or lands as something meaningless.
    """
    try:
        elements = list(
            DB.FilteredElementCollector(link_doc, linked_view.Id)
            .WhereElementIsNotElementType().ToElements())
    except Exception:
        return []

    view_key = eid_to_int(linked_view.Id)
    ids = []
    for element in elements:
        if not bool(getattr(element, "ViewSpecific", False)):
            continue
        try:
            if eid_to_int(element.OwnerViewId) != view_key:
                continue
        except Exception:
            continue
        if type(element).__name__ in _ANNOTATION_EXCLUDED:
            continue
        excluded = False
        for name in _ANNOTATION_EXCLUDED:
            api_type = getattr(DB, name, None)
            if api_type is not None and isinstance(element, api_type):
                excluded = True
                break
        if excluded:
            continue
        ids.append(element.Id)
    return ids


def in_plane_transform(transform):
    """The link transform with its vertical component dropped.

    ``CopyElements`` between two views wants a transform inside the view
    plane; a plan's plane is horizontal, so the Z translation has to go.
    """
    try:
        flat = DB.Transform.CreateTranslation(DB.XYZ.Zero)
        flat.BasisX = transform.BasisX
        flat.BasisY = transform.BasisY
        flat.BasisZ = transform.BasisZ
        origin = transform.Origin
        flat.Origin = DB.XYZ(origin.X, origin.Y, 0.0)
        return flat
    except Exception:
        return DB.Transform.Identity


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def build_level_lookup(level_rows, linked_levels, host_levels):
    """``{linked key: (host key, elevation shift)}`` for the view range.

    The shift is what an offset measured from the linked level has to move by
    to stay at the same absolute height once the host level replaces it.
    """
    linked_by_key = dict((level.key, level) for level in linked_levels or [])
    host_by_key = dict((level.key, level) for level in host_levels or [])
    lookup = {}
    for row in level_rows or []:
        if not row.is_mapped:
            continue
        linked = linked_by_key.get(row.linked_key)
        host = host_by_key.get(row.host_key)
        if linked is None or host is None:
            continue
        lookup[row.linked_key] = (row.host_key,
                                  linked.elevation_host - host.elevation)
    return lookup


def run_copy(doc, link_option, plan, level_lookup, progress=None):
    """Write the plan.  One undo step, one rolled-back group per bad sheet."""
    summary = state.RunSummary()
    options = plan.options
    link_doc = link_option.doc
    transform = link_option.transform

    group = DB.TransactionGroup(doc, u"Linked Sheets Transfer")
    group.Start()
    try:
        total = max(len(plan.sheets_to_create), 1)
        for index, sheet_item in enumerate(plan.sheets_to_create):
            if progress is not None and not progress(index, total):
                summary.cancelled = True
                break
            _run_one_sheet(doc, link_doc, link_option, sheet_item, plan,
                           options, transform, level_lookup, summary)
    except Exception as run_error:
        summary.add_error(u"", u"run", exception_text(run_error))
        summary.cancelled = True

    if summary.cancelled:
        try:
            group.RollBack()
        except Exception:
            pass
        summary.clear_applied()
        return summary

    try:
        group.Assimilate()
    except Exception:
        try:
            group.RollBack()
        except Exception:
            pass
        summary.cancelled = True
        summary.clear_applied()
    return summary


def _run_one_sheet(doc, link_doc, link_option, sheet_item, plan, options,
                   transform, level_lookup, summary):
    """One sheet, in its own rolled-back-on-failure group.

    Isolation matters here: a sheet whose title block will not copy must not
    take twenty good sheets down with it.
    """
    sheet_group = DB.TransactionGroup(
        doc, u"Linked Sheets Transfer - {0}".format(sheet_item.number))
    sheet_group.Start()
    transaction = None
    snapshot = summary.snapshot()
    try:
        transaction = _start_transaction(
            doc, u"Linked Sheets Transfer - {0}".format(sheet_item.number))
        _build_sheet(doc, link_doc, link_option, sheet_item, plan, options,
                     transform, level_lookup, summary)
        transaction.Commit()
        sheet_group.Assimilate()
    except Exception as sheet_error:
        if transaction is not None and transaction.HasStarted():
            try:
                transaction.RollBack()
            except Exception:
                pass
        try:
            if sheet_group.HasStarted():
                sheet_group.RollBack()
        except Exception:
            pass
        # The group is gone from the model, so it goes from the report too.
        summary.restore(snapshot)
        summary.add_error(sheet_item.number, u"sheet",
                          exception_text(sheet_error))


def _build_sheet(doc, link_doc, link_option, sheet_item, plan, options,
                 transform, level_lookup, summary):
    linked_sheet = link_doc.GetElement(_eid(sheet_item.sheet_row.sheet_key))
    if linked_sheet is None:
        raise Exception(u"The sheet no longer exists in the link.")

    notes = []
    title_block_type_id = DB.ElementId.InvalidElementId
    if options.get(state.OPT_TITLE_BLOCK) and not sheet_item.sheet_row.is_placeholder:
        title_block_type_id = _host_title_block_type_id(
            doc, link_doc, sheet_item.sheet_row.title_block_label, options,
            notes)

    host_sheet = DB.ViewSheet.Create(doc, title_block_type_id)
    host_sheet.SheetNumber = sheet_item.number
    # The title block instance is not collectable until the sheet has been
    # regenerated, and everything below reads it back.
    try:
        doc.Regenerate()
    except Exception:
        pass
    if summary.first_sheet_key is None:
        summary.first_sheet_key = eid_to_int(host_sheet.Id)
    if sheet_item.name:
        try:
            host_sheet.Name = sheet_item.name
        except Exception:
            notes.append(u"The sheet name was refused.")
    summary.sheets_created += 1

    if eid_to_int(title_block_type_id) not in (None, -1):
        _position_title_block(doc, host_sheet, linked_sheet, link_doc, notes)
        if options.get(state.OPT_TITLE_BLOCK_PARAMS):
            _copy_title_block_parameters(doc, host_sheet, linked_sheet,
                                         link_doc, notes)

    if options.get(state.OPT_SHEET_PARAMS):
        copied, missing = copy_parameters(linked_sheet, host_sheet)
        if missing:
            notes.append(u"{0} sheet parameter(s) are not in this model: "
                         u"{1}".format(len(missing),
                                       u", ".join(sorted(set(missing))[:8])))
        del copied

    if options.get(state.OPT_SHEET_DETAILING) or options.get(
            state.OPT_REVISION_CLOUDS):
        ids = sheet_detailing_ids(
            link_doc, linked_sheet,
            bool(options.get(state.OPT_REVISION_CLOUDS)))
        if not options.get(state.OPT_SHEET_DETAILING):
            ids = [element_id for element_id in ids
                   if isinstance(link_doc.GetElement(element_id),
                                 DB.RevisionCloud)]
        copied, failures = _copy_elements(
            linked_sheet, ids, host_sheet, DB.Transform.Identity,
            copy_paste_options())
        summary.elements_copied += copied
        if failures:
            notes.append(u"{0} sheet element(s) could not be "
                         u"copied.".format(len(failures)))

    summary.add_row(sheet_item.number, u"sheet", u"created",
                    u"  ".join(notes) if notes else u"")

    for view_item in sheet_item.views:
        if view_item.action != state.ACTION_CREATE:
            summary.add_row(sheet_item.number,
                            u"view {0}".format(view_item.source_name),
                            u"skipped", view_item.reason)
            continue
        _build_view(doc, link_doc, link_option, host_sheet, linked_sheet,
                    sheet_item, view_item, plan, options, transform,
                    level_lookup, summary)


def _copy_title_block_parameters(doc, host_sheet, linked_sheet, link_doc,
                                 notes):
    linked_blocks = sheet_title_blocks(link_doc, linked_sheet)
    host_blocks = sheet_title_blocks(doc, host_sheet)
    if not linked_blocks or not host_blocks:
        return
    _, missing = copy_parameters(linked_blocks[0], host_blocks[0])
    if missing:
        notes.append(u"{0} title block parameter(s) are not in this model: "
                     u"{1}".format(len(missing),
                                   u", ".join(sorted(set(missing))[:8])))


def _build_view(doc, link_doc, link_option, host_sheet, linked_sheet,
                sheet_item, view_item, plan, options, transform, level_lookup,
                summary):
    del linked_sheet, plan
    view_row = view_item.view_row
    label = u"view {0}".format(view_item.target_name or view_row.view_name)

    linked_view = link_doc.GetElement(_eid(view_row.view_key))
    linked_viewport = link_doc.GetElement(_eid(view_row.viewport_key))
    if linked_view is None or linked_viewport is None:
        summary.add_row(sheet_item.number, label, u"skipped",
                        u"The view or viewport no longer exists in the link.")
        return

    notes = list(view_item.notes)
    host_level_key = view_item.level_row.host_key if view_item.level_row else None
    if host_level_key is None:
        summary.add_row(sheet_item.number, label, u"skipped",
                        u"The level is not mapped.")
        return

    host_view, reason = create_host_view(
        doc, linked_view, view_row.view_family, _eid(host_level_key),
        view_item.target_name, transform, options, level_lookup, notes)
    if host_view is None:
        summary.add_row(sheet_item.number, label, u"failed", reason)
        return
    summary.views_created += 1

    link_reason = set_by_linked_view(host_view, link_option.instance.Id,
                                     linked_view.Id)
    if link_reason:
        notes.append(link_reason)

    if options.get(state.OPT_HIDE_OTHER_LINKS):
        hide_other_links(doc, host_view, link_option.instance_key)

    if options.get(state.OPT_VIEW_ANNOTATIONS):
        ids = view_annotation_ids(link_doc, linked_view)
        copied, failures = _copy_elements(
            linked_view, ids, host_view, in_plane_transform(transform),
            copy_paste_options())
        summary.elements_copied += copied
        if failures:
            notes.append(u"{0} annotation(s) could not be copied.".format(
                len(failures)))

    placement, reason = place_and_align(
        doc, host_sheet, host_view, linked_viewport, linked_view, transform,
        options, link_doc, notes)
    if placement is None:
        summary.add_row(sheet_item.number, label, u"created, not placed",
                        u"  ".join([reason] + notes))
        return

    summary.viewports_placed += 1
    if placement.size_note:
        notes.append(placement.size_note)
    if (placement.deviation_mm is not None
            and placement.deviation_mm > state.ALIGNMENT_TOLERANCE_MM):
        notes.append(u"The second alignment point misses by {0:.3f} mm: this "
                     u"view did not reproduce the linked scale or "
                     u"rotation.".format(placement.deviation_mm))

    summary.add_row(sheet_item.number, label, u"placed",
                    u"  ".join(notes) if notes else u"",
                    placement.deviation_mm)


def open_sheet_by_key(uidoc, sheet_key):
    """Make a created sheet active.  Only legal once no dialog is open."""
    if uidoc is None or sheet_key is None:
        return False
    try:
        sheet = uidoc.Document.GetElement(_eid(sheet_key))
        if sheet is None:
            return False
        uidoc.ActiveView = sheet
        return True
    except Exception:
        return False

