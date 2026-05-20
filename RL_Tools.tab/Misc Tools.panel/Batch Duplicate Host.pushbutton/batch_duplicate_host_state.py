# -*- coding: utf-8 -*-
"""Plain-Python state and summary helpers for Batch Duplicate Host."""

from dataclasses import dataclass, field


SKIPPED_DISPLAY_LIMIT = 20
NOTES_DISPLAY_LIMIT = 10


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


@dataclass
class WizardState:
    selected_document_key: str = None
    selected_category_ids: set = field(default_factory=set)
    selected_type_ids: set = field(default_factory=set)
    x_offset_text: str = "0"
    y_offset_text: str = "0"
    z_offset_text: str = "0"
    align_orientation: bool = True


@dataclass
class TargetDocumentOption:
    display_name: str
    document_key: str
    is_current_project: bool = False
    document: object = None
    link_instance: object = None
    to_host_transform: object = None

    def __str__(self):
        return self.display_name


@dataclass
class CategoryOption:
    name: str
    category_id: str
    is_selected: bool = False


@dataclass
class FamilyTypeOption:
    family_name: str
    type_name: str
    type_id: str
    is_selected: bool = False


@dataclass
class FamilyGroupOption:
    name: str
    types: list = field(default_factory=list)


@dataclass
class TargetInstanceRef:
    source_option: object
    instance_id: object
    type_id: object
    display_label: str
    host_point: object
    target_local_x_axis: object = None
    target_local_y_axis: object = None
    target_local_z_axis: object = None
    local_coordinate_frame_error: str = ""


@dataclass
class SkippedPlacement:
    target_label: str
    reason: str


@dataclass
class PlacementSummary:
    created_element_ids: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    notes: list = field(default_factory=list)


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
