# -*- coding: utf-8 -*-
"""Plain-Python state and summary helpers for Batch Duplicate Host."""


SKIPPED_DISPLAY_LIMIT = 20
NOTES_DISPLAY_LIMIT = 10


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


class WizardState:
    def __init__(
        self,
        selected_document_key=None,
        selected_category_ids=None,
        selected_type_ids=None,
        x_offset_text="0",
        y_offset_text="0",
        z_offset_text="0",
        align_orientation=True,
    ):
        self.selected_document_key = selected_document_key
        self.selected_category_ids = set(selected_category_ids or [])
        self.selected_type_ids = set(selected_type_ids or [])
        self.x_offset_text = x_offset_text
        self.y_offset_text = y_offset_text
        self.z_offset_text = z_offset_text
        self.align_orientation = align_orientation


class TargetDocumentOption:
    def __init__(
        self,
        display_name,
        document_key,
        is_current_project=False,
        document=None,
        link_instance=None,
        to_host_transform=None,
    ):
        self.display_name = display_name
        self.document_key = document_key
        self.is_current_project = is_current_project
        self.document = document
        self.link_instance = link_instance
        self.to_host_transform = to_host_transform

    def __str__(self):
        return self.display_name


class CategoryOption:
    def __init__(self, name, category_id, is_selected=False):
        self.name = name
        self.category_id = category_id
        self.is_selected = is_selected


class FamilyTypeOption:
    def __init__(self, family_name, type_name, type_id, is_selected=False):
        self.family_name = family_name
        self.type_name = type_name
        self.type_id = type_id
        self.is_selected = is_selected


class FamilyGroupOption:
    def __init__(self, name, types=None):
        self.name = name
        self.types = list(types or [])


class TargetInstanceRef:
    def __init__(
        self,
        source_option,
        instance_id,
        type_id,
        display_label,
        host_point,
        target_local_x_axis=None,
        target_local_y_axis=None,
        target_local_z_axis=None,
        local_coordinate_frame_error="",
    ):
        self.source_option = source_option
        self.instance_id = instance_id
        self.type_id = type_id
        self.display_label = display_label
        self.host_point = host_point
        self.target_local_x_axis = target_local_x_axis
        self.target_local_y_axis = target_local_y_axis
        self.target_local_z_axis = target_local_z_axis
        self.local_coordinate_frame_error = local_coordinate_frame_error


class SkippedPlacement:
    def __init__(self, target_label, reason):
        self.target_label = target_label
        self.reason = reason


class PlacementSummary:
    def __init__(self, created_element_ids=None, skipped=None, notes=None):
        self.created_element_ids = list(created_element_ids or [])
        self.skipped = list(skipped or [])
        self.notes = list(notes or [])


def restore_category_selection(categories, selected_ids):
    selected_ids = set(selected_ids or [])
    for category in categories or []:
        category.is_selected = category.category_id in selected_ids
    return categories


def restore_type_selection(groups, selected_ids):
    selected_ids = set(selected_ids or [])
    for group in groups or []:
        for family_type in group.types:
            family_type.is_selected = family_type.type_id in selected_ids
    return groups


def sort_target_documents(documents):
    return sorted(
        list(documents or []),
        key=lambda option: (
            0 if getattr(option, "is_current_project", False) else 1,
            _safe_text(getattr(option, "display_name", "")).lower(),
        ),
    )


def sort_categories(categories):
    return sorted(
        list(categories or []),
        key=lambda option: _safe_text(getattr(option, "name", "")).lower(),
    )


def sort_family_groups(groups):
    sorted_groups = []
    for group in list(groups or []):
        sorted_types = sorted(
            list(group.types or []),
            key=lambda family_type: _safe_text(getattr(family_type, "type_name", "")).lower(),
        )
        sorted_groups.append(FamilyGroupOption(group.name, sorted_types))

    return sorted(
        sorted_groups,
        key=lambda group: _safe_text(getattr(group, "name", "")).lower(),
    )


def build_summary_text(summary, target_count):
    summary = summary or PlacementSummary()
    builder = [
        "Targets processed: {}".format(int(target_count or 0)),
        "Elements created: {}".format(len(summary.created_element_ids or [])),
        "Skipped: {}".format(len(summary.skipped or [])),
    ]

    skipped_items = list(summary.skipped or [])
    if skipped_items:
        builder.append("")
        builder.append("Skipped items:")
        for skipped in skipped_items[:SKIPPED_DISPLAY_LIMIT]:
            builder.append(
                "- {}: {}".format(
                    _safe_text(getattr(skipped, "target_label", "")),
                    _safe_text(getattr(skipped, "reason", "")),
                )
            )

        if len(skipped_items) > SKIPPED_DISPLAY_LIMIT:
            builder.append("- Plus {} more.".format(len(skipped_items) - SKIPPED_DISPLAY_LIMIT))

    notes = list(summary.notes or [])
    if notes:
        builder.append("")
        builder.append("Notes:")
        for note in notes[:NOTES_DISPLAY_LIMIT]:
            builder.append("- {}".format(_safe_text(note)))

        if len(notes) > NOTES_DISPLAY_LIMIT:
            builder.append("- Plus {} more.".format(len(notes) - NOTES_DISPLAY_LIMIT))

    return "\n".join(builder)
