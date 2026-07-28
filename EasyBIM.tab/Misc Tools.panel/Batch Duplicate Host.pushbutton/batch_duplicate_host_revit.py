# -*- coding: utf-8 -*-
"""Revit-facing logic for Batch Duplicate Host."""

import os

import clr

clr.AddReference("RevitAPIUI")

from Autodesk.Revit.UI.Selection import ISelectionFilter
from System.Collections.Generic import List as ClrList

from pyrevit import DB
from pyrevit.compat import get_elementid_value_func

from batch_duplicate_host_state import CategoryOption
from batch_duplicate_host_state import FamilyGroupOption
from batch_duplicate_host_state import FamilyTypeOption
from batch_duplicate_host_state import PlacementSummary
from batch_duplicate_host_state import SkippedPlacement
from batch_duplicate_host_state import TargetDocumentOption
from batch_duplicate_host_state import TargetInstanceRef
from batch_duplicate_host_state import sort_categories
from batch_duplicate_host_state import sort_family_groups
from batch_duplicate_host_state import sort_target_documents


get_elementid_value = get_elementid_value_func()

try:
    INVALID_EID = DB.ElementId.InvalidElementId
except Exception:
    INVALID_EID = DB.ElementId(-1)


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _eid_text(element_id):
    if not element_id:
        return ""
    try:
        return str(get_elementid_value(element_id))
    except Exception:
        try:
            return str(element_id.IntegerValue)
        except Exception:
            return _safe_text(element_id)


def _doc_path_key(document):
    try:
        path = _safe_text(document.PathName).strip()
        if path:
            return path.lower()
    except Exception:
        pass
    return "<memory>|{}".format(_safe_text(getattr(document, "Title", "")).lower())


def _build_document_key(document, link_instance=None):
    if link_instance:
        return "link|{}|{}".format(_doc_path_key(document), _eid_text(link_instance.Id))
    return "current|{}".format(_doc_path_key(document))


def _normalize_vector(vector):
    if vector is None:
        return None
    try:
        if vector.GetLength() < 1.0e-9:
            return None
        return vector.Normalize()
    except Exception:
        return None


def _get_link_transform(link_instance):
    if link_instance is None:
        return DB.Transform.Identity
    if hasattr(link_instance, "GetTotalTransform"):
        try:
            return link_instance.GetTotalTransform()
        except Exception:
            pass
    if hasattr(link_instance, "GetTransform"):
        try:
            return link_instance.GetTransform()
        except Exception:
            pass
    return DB.Transform.Identity


def _get_document_title(document):
    try:
        if _safe_text(document.PathName).strip():
            return os.path.basename(_safe_text(document.PathName))
    except Exception:
        pass

    title = _safe_text(getattr(document, "Title", ""))
    return title or "(Untitled Link)"


def _collect_family_instances(document):
    try:
        collector = (
            DB.FilteredElementCollector(document)
            .OfClass(DB.FamilyInstance)
            .WhereElementIsNotElementType()
        )
        return list(collector)
    except Exception:
        try:
            return list(
                DB.FilteredElementCollector(document)
                .OfClass(DB.FamilyInstance)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        except Exception:
            return []


class BatchDuplicateHostSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return is_allowed_source(elem)

    def AllowReference(self, reference, position):
        del reference, position
        return False


def is_allowed_source(element):
    if element is None or isinstance(element, DB.ElementType):
        return False

    category = getattr(element, "Category", None)
    if category is None:
        return False

    category_type = getattr(category, "CategoryType", None)
    return category_type in (DB.CategoryType.Model, DB.CategoryType.Annotation)


def get_target_documents(host_document):
    options = [
        TargetDocumentOption(
            display_name="Current Project",
            document_key=_build_document_key(host_document),
            is_current_project=True,
            document=host_document,
            link_instance=None,
            to_host_transform=DB.Transform.Identity,
        )
    ]

    link_instances = []
    try:
        link_instances = list(
            DB.FilteredElementCollector(host_document)
            .OfClass(DB.RevitLinkInstance)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        try:
            link_instances = list(DB.FilteredElementCollector(host_document).OfClass(DB.RevitLinkInstance))
        except Exception:
            link_instances = []

    link_instances = sorted(
        link_instances,
        key=lambda link: _safe_text(getattr(link, "Name", "")).lower(),
    )

    link_type_counts = {}
    for link_instance in link_instances:
        type_key = _eid_text(link_instance.GetTypeId())
        link_type_counts[type_key] = link_type_counts.get(type_key, 0) + 1

    for link_instance in link_instances:
        try:
            link_document = link_instance.GetLinkDocument()
        except Exception:
            link_document = None
        if not link_document:
            continue

        type_key = _eid_text(link_instance.GetTypeId())
        display_name = _get_document_title(link_document)
        if link_type_counts.get(type_key, 0) > 1:
            display_name = "{} ({})".format(display_name, _safe_text(getattr(link_instance, "Name", "")))

        options.append(
            TargetDocumentOption(
                display_name=display_name,
                document_key=_build_document_key(link_document, link_instance),
                is_current_project=False,
                document=link_document,
                link_instance=link_instance,
                to_host_transform=_get_link_transform(link_instance),
            )
        )

    return sort_target_documents(options)


def get_categories(target_document_option):
    categories_by_id = {}

    for instance in _collect_family_instances(target_document_option.document):
        category = getattr(instance, "Category", None)
        if category is None or getattr(category, "CategoryType", None) != DB.CategoryType.Model:
            continue

        category_id = _eid_text(category.Id)
        if category_id not in categories_by_id:
            categories_by_id[category_id] = CategoryOption(
                name=_safe_text(getattr(category, "Name", "")),
                category_id=category_id,
            )

    return sort_categories(categories_by_id.values())


def get_family_groups(target_document_option, selected_categories):
    selected_category_ids = set()
    for category in list(selected_categories or []):
        if getattr(category, "is_selected", False):
            selected_category_ids.add(category.category_id)

    groups_by_name = {}
    seen_type_ids = set()

    for instance in _collect_family_instances(target_document_option.document):
        category = getattr(instance, "Category", None)
        if category is None or _eid_text(category.Id) not in selected_category_ids:
            continue

        symbol = None
        try:
            symbol = target_document_option.document.GetElement(instance.GetTypeId())
        except Exception:
            symbol = None
        if symbol is None:
            continue

        family_name = _safe_text(getattr(symbol, "FamilyName", "")) or "(Unnamed Family)"
        type_name = _safe_text(getattr(symbol, "Name", "")) or "(Unnamed Type)"
        type_id = _eid_text(getattr(symbol, "Id", None))

        if type_id in seen_type_ids:
            continue
        seen_type_ids.add(type_id)

        groups_by_name.setdefault(family_name, []).append(
            FamilyTypeOption(
                family_name=family_name,
                type_name=type_name,
                type_id=type_id,
            )
        )

    groups = [
        FamilyGroupOption(name=family_name, types=family_types)
        for family_name, family_types in groups_by_name.items()
    ]
    return sort_family_groups(groups)


def get_target_instances(target_document_option, selected_type_ids):
    selected_type_ids = set(selected_type_ids or [])
    targets = []

    for instance in _collect_family_instances(target_document_option.document):
        type_id = _eid_text(instance.GetTypeId())
        if type_id not in selected_type_ids:
            continue

        host_point = _get_instance_point_in_host_coordinates(
            instance,
            target_document_option.to_host_transform,
        )
        if host_point is None:
            continue

        target_x_axis, target_y_axis, target_z_axis, error = _get_target_coordinate_frame_in_host_coordinates(
            instance,
            target_document_option.to_host_transform,
        )

        targets.append(
            TargetInstanceRef(
                source_option=target_document_option,
                instance_id=instance.Id,
                type_id=instance.GetTypeId(),
                display_label=_build_instance_label(target_document_option, instance),
                host_point=host_point,
                target_local_x_axis=target_x_axis,
                target_local_y_axis=target_y_axis,
                target_local_z_axis=target_z_axis,
                local_coordinate_frame_error=error or "",
            )
        )

    return sorted(
        targets,
        key=lambda target: _safe_text(target.display_label).lower(),
    )


def _build_instance_label(target_document_option, instance):
    family_name = _safe_text(getattr(getattr(instance, "Symbol", None), "FamilyName", "")) or "Family"
    type_name = _safe_text(getattr(instance, "Name", "")) or "Type"
    return "{} / {} : {} / {}".format(
        target_document_option.display_name,
        family_name,
        type_name,
        _eid_text(instance.Id),
    )


def _get_instance_point_in_host_coordinates(instance, to_host_transform):
    source_point = None

    location = getattr(instance, "Location", None)
    if isinstance(location, DB.LocationPoint):
        source_point = location.Point
    elif isinstance(location, DB.LocationCurve):
        try:
            source_point = location.Curve.Evaluate(0.5, True)
        except Exception:
            source_point = None

    if source_point is None:
        try:
            box = instance.get_BoundingBox(None)
        except Exception:
            box = None
        if box is not None:
            source_point = box.Min.Add(box.Max).Multiply(0.5)

    if source_point is None:
        return None

    try:
        return to_host_transform.OfPoint(source_point)
    except Exception:
        return source_point


def _get_target_coordinate_frame_in_host_coordinates(instance, to_host_transform):
    try:
        target_x_axis = _normalize_vector(to_host_transform.OfVector(instance.HandOrientation))
        target_y_axis = _normalize_vector(to_host_transform.OfVector(instance.FacingOrientation))
    except Exception as ex:
        return None, None, None, "Could not resolve the target family's local coordinate frame: {}".format(ex)

    if target_x_axis is None or target_y_axis is None:
        return None, None, None, "The target family instance has an invalid local coordinate frame."

    target_z_axis = _normalize_vector(target_x_axis.CrossProduct(target_y_axis))
    if target_z_axis is None:
        return None, None, None, "The target family instance has parallel hand and facing directions."

    try:
        transform_z_axis = _normalize_vector(to_host_transform.OfVector(instance.GetTransform().BasisZ))
    except Exception:
        transform_z_axis = None

    if transform_z_axis is not None and target_z_axis.DotProduct(transform_z_axis) < 0:
        target_z_axis = target_z_axis.Negate()

    return target_x_axis, target_y_axis, target_z_axis, ""


def _coerce_tryparse_result(parse_result):
    if isinstance(parse_result, (tuple, list)):
        if len(parse_result) >= 2:
            return bool(parse_result[0]), parse_result[1]
        if len(parse_result) == 1:
            return bool(parse_result[0]), None
    return False, None


def get_length_unit_label(document):
    try:
        units = document.GetUnits()
        format_options = units.GetFormatOptions(DB.SpecTypeId.Length)
        unit_type_id = format_options.GetUnitTypeId()
        return DB.LabelUtils.GetLabelForUnit(unit_type_id)
    except Exception:
        return "project units"


def try_parse_length_value(units, label, value_text):
    try:
        parse_result = DB.UnitFormatUtils.TryParse(units, DB.SpecTypeId.Length, value_text)
        success, parsed_value = _coerce_tryparse_result(parse_result)
        if success and parsed_value is not None:
            return True, float(parsed_value), ""
    except Exception:
        pass

    return False, None, "{}: enter a valid length, for example 6\", 2', or 1' 6\".".format(label)


def place_copies(host_document, active_view, source_element, targets, offset, align_orientation):
    summary = PlacementSummary()
    source_point = _get_element_point(source_element, active_view)
    source_coordinate_frame = _get_source_coordinate_frame(source_element)

    if source_point is None:
        summary.skipped.append(
            SkippedPlacement(
                _eid_text(source_element.Id),
                "The selected source element does not expose a usable point, curve midpoint, or bounding-box center.",
            )
        )
        return summary

    if align_orientation and not _is_annotation_or_view_specific(source_element) and source_coordinate_frame is None:
        summary.notes.append(
            "Align Orientation was requested, but the source element does not expose a usable orientation frame. Translation-only copy was used."
        )

    transaction = DB.Transaction(host_document, "Batch Duplicate Host")
    transaction.Start()

    try:
        for target in list(targets or []):
            try:
                target_offset = _get_target_offset_in_host_coordinates(target, offset)
                if target_offset is None:
                    summary.skipped.append(
                        SkippedPlacement(
                            target.display_label,
                            target.local_coordinate_frame_error or "Could not resolve the target family's local coordinate frame.",
                        )
                    )
                    continue

                desired_point = target.host_point.Add(target_offset)

                if _is_annotation_or_view_specific(source_element):
                    _copy_annotation_in_active_view(
                        host_document,
                        active_view,
                        source_element,
                        source_point,
                        desired_point,
                        target,
                        summary,
                    )
                else:
                    _copy_model_element(
                        host_document,
                        source_element,
                        source_point,
                        desired_point,
                        target,
                        source_coordinate_frame,
                        summary,
                        align_orientation,
                    )
            except Exception as ex:
                summary.skipped.append(SkippedPlacement(target.display_label, _safe_text(ex)))

        transaction.Commit()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        raise

    return summary


def _get_target_offset_in_host_coordinates(target, offset):
    if target.target_local_x_axis is None or target.target_local_y_axis is None or target.target_local_z_axis is None:
        return None

    return target.target_local_x_axis.Multiply(offset.X).Add(
        target.target_local_y_axis.Multiply(offset.Y)
    ).Add(
        target.target_local_z_axis.Multiply(offset.Z)
    )


def _copy_model_element(
    document,
    source_element,
    source_point,
    desired_point,
    target,
    source_coordinate_frame,
    summary,
    align_orientation,
):
    copy_transform = _build_copy_transform(
        source_point,
        desired_point,
        target,
        source_coordinate_frame,
        align_orientation,
    )
    copied_ids = DB.ElementTransformUtils.CopyElements(
        document,
        _build_element_id_list([source_element.Id]),
        document,
        copy_transform,
        DB.CopyPasteOptions(),
    )
    for copied_id in copied_ids:
        summary.created_element_ids.append(copied_id)


def _build_copy_transform(
    source_point,
    desired_point,
    target,
    source_coordinate_frame,
    align_orientation,
):
    if (
        not align_orientation
        or source_coordinate_frame is None
        or target.target_local_x_axis is None
        or target.target_local_y_axis is None
        or target.target_local_z_axis is None
    ):
        return DB.Transform.CreateTranslation(desired_point.Subtract(source_point))

    source_frame_transform = _create_frame_transform(
        source_point,
        source_coordinate_frame[0],
        source_coordinate_frame[1],
        source_coordinate_frame[2],
    )
    target_frame_transform = _create_frame_transform(
        desired_point,
        target.target_local_x_axis,
        target.target_local_y_axis,
        target.target_local_z_axis,
    )
    return target_frame_transform.Multiply(source_frame_transform.Inverse)


def _create_frame_transform(origin, x_axis, y_axis, z_axis):
    transform = DB.Transform.Identity
    transform.Origin = origin
    transform.BasisX = x_axis
    transform.BasisY = y_axis
    transform.BasisZ = z_axis
    return transform


def _get_source_coordinate_frame(source_element):
    if not isinstance(source_element, DB.FamilyInstance):
        return None

    source_x_axis = _normalize_vector(source_element.HandOrientation)
    source_y_axis = _normalize_vector(source_element.FacingOrientation)
    if source_x_axis is None or source_y_axis is None:
        return None

    source_z_axis = _normalize_vector(source_x_axis.CrossProduct(source_y_axis))
    if source_z_axis is None:
        return None

    try:
        transform_z_axis = _normalize_vector(source_element.GetTransform().BasisZ)
    except Exception:
        transform_z_axis = None

    if transform_z_axis is not None and source_z_axis.DotProduct(transform_z_axis) < 0:
        source_z_axis = source_z_axis.Negate()

    return source_x_axis, source_y_axis, source_z_axis


def _copy_annotation_in_active_view(
    document,
    active_view,
    source_element,
    source_point,
    desired_point,
    target,
    summary,
):
    if bool(getattr(source_element, "ViewSpecific", False)) and source_element.OwnerViewId != active_view.Id:
        summary.skipped.append(
            SkippedPlacement(
                target.display_label,
                "The selected annotation belongs to a different owner view than the active view.",
            )
        )
        return

    if not bool(getattr(source_element, "ViewSpecific", False)):
        _copy_model_element(
            document,
            source_element,
            source_point,
            desired_point,
            target,
            None,
            summary,
            False,
        )
        return

    translation = DB.Transform.CreateTranslation(desired_point.Subtract(source_point))
    copied_ids = DB.ElementTransformUtils.CopyElements(
        active_view,
        _build_element_id_list([source_element.Id]),
        active_view,
        translation,
        DB.CopyPasteOptions(),
    )
    for copied_id in copied_ids:
        summary.created_element_ids.append(copied_id)


def _is_annotation_or_view_specific(element):
    category = getattr(element, "Category", None)
    category_type = getattr(category, "CategoryType", None) if category else None
    return bool(getattr(element, "ViewSpecific", False)) or category_type == DB.CategoryType.Annotation


def _get_element_point(element, active_view):
    location = getattr(element, "Location", None)
    if isinstance(location, DB.LocationPoint):
        return location.Point

    if isinstance(location, DB.LocationCurve):
        try:
            return location.Curve.Evaluate(0.5, True)
        except Exception:
            pass

    try:
        box = element.get_BoundingBox(active_view)
    except Exception:
        box = None

    if box is None:
        try:
            box = element.get_BoundingBox(None)
        except Exception:
            box = None

    if box is None:
        return None

    return box.Min.Add(box.Max).Multiply(0.5)


def _build_element_id_list(element_ids):
    clr_ids = ClrList[DB.ElementId]()
    for element_id in list(element_ids or []):
        if element_id is None or element_id == INVALID_EID:
            continue
        clr_ids.Add(element_id)
    return clr_ids
