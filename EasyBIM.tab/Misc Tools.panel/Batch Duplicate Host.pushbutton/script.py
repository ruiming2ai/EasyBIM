# -*- coding: utf-8 -*-
"""Port of the C# Batch Duplicate Host Revit add-in."""

# pylint: disable=import-error,invalid-name,broad-except
import os
import sys

import clr

clr.AddReference("RevitAPIUI")

from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.UI.Selection import ObjectType

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit


SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from batch_duplicate_host_revit import BatchDuplicateHostSelectionFilter
from batch_duplicate_host_revit import get_categories
from batch_duplicate_host_revit import get_family_groups
from batch_duplicate_host_revit import get_length_unit_label
from batch_duplicate_host_revit import get_target_documents
from batch_duplicate_host_revit import get_target_instances
from batch_duplicate_host_revit import is_allowed_source
from batch_duplicate_host_revit import place_copies
from batch_duplicate_host_revit import try_parse_length_value
from batch_duplicate_host_state import WizardState
from batch_duplicate_host_state import build_summary_text
from batch_duplicate_host_ui import CategorySelectionWindow
from batch_duplicate_host_ui import FamilyTypeSelectionWindow
from batch_duplicate_host_ui import OffsetWindow
from batch_duplicate_host_ui import SourceSelectionWindow


__title__ = "Batch Duplicate Host"

STEP_SOURCE = "source"
STEP_CATEGORIES = "categories"
STEP_FAMILY_TYPES = "family_types"
STEP_OFFSET = "offset"


def _is_cancelled_pick(ex):
    if isinstance(ex, OperationCanceledException):
        return True
    return "cancel" in str(ex).lower()


def _pick_source_element(uidoc):
    try:
        reference = uidoc.Selection.PickObject(
            ObjectType.Element,
            BatchDuplicateHostSelectionFilter(),
            "Select your element to duplicate and host",
        )
    except Exception as ex:
        if _is_cancelled_pick(ex):
            return None
        raise

    if not reference:
        return None
    return uidoc.Document.GetElement(reference.ElementId)


def _show_summary(summary, target_count):
    TaskDialog.Show(__title__, build_summary_text(summary, target_count))


def _run():
    uidoc = revit.uidoc
    doc = revit.doc
    if uidoc is None or doc is None:
        forms.alert("Open a project document before running Batch Duplicate Host.", title=__title__)
        return

    active_view = doc.ActiveView
    wizard_state = WizardState()
    target_documents = None
    selected_document = None
    source_element = None
    step = STEP_SOURCE

    while True:
        if step == STEP_SOURCE:
            source_window = SourceSelectionWindow("SourceSelectionWindow.xaml")
            source_window.ShowDialog()
            if not source_window.should_select:
                return

            try:
                source_element = _pick_source_element(uidoc)
            except Exception as ex:
                forms.alert("Select failed: {}".format(ex), title=__title__)
                return

            if source_element is None:
                return

            if not is_allowed_source(source_element):
                forms.alert(
                    "Please select one model or annotation element from the current project.",
                    title=__title__,
                )
                return

            step = STEP_CATEGORIES
            continue

        if step == STEP_CATEGORIES:
            if target_documents is None:
                target_documents = get_target_documents(doc)

            category_window = CategorySelectionWindow(
                "CategorySelectionWindow.xaml",
                target_documents,
                get_categories,
                wizard_state.selected_document_key,
                wizard_state.selected_category_ids,
            )
            category_window.ShowDialog()

            if category_window.result == "ok":
                selected_document = category_window.selected_target_document
                wizard_state.selected_document_key = getattr(selected_document, "document_key", None)
                wizard_state.selected_category_ids = set(category_window.selected_category_ids)
                wizard_state.selected_type_ids = set()
                step = STEP_FAMILY_TYPES
                continue

            if category_window.was_back:
                step = STEP_SOURCE
                continue
            return

        if step == STEP_FAMILY_TYPES:
            if selected_document is None or not wizard_state.selected_category_ids:
                step = STEP_CATEGORIES
                continue

            selected_categories = get_categories(selected_document)
            for category in selected_categories:
                category.is_selected = category.category_id in wizard_state.selected_category_ids

            family_groups = get_family_groups(selected_document, selected_categories)
            if not family_groups:
                TaskDialog.Show(__title__, "No family types were found in the selected categories.")
                step = STEP_CATEGORIES
                continue

            family_type_window = FamilyTypeSelectionWindow(
                "FamilyTypeSelectionWindow.xaml",
                family_groups,
                wizard_state.selected_type_ids,
            )
            family_type_window.ShowDialog()

            if family_type_window.result == "ok":
                wizard_state.selected_type_ids = set(family_type_window.selected_type_ids)
                step = STEP_OFFSET
                continue

            if family_type_window.was_back:
                step = STEP_CATEGORIES
                continue
            return

        if step == STEP_OFFSET:
            if source_element is None or selected_document is None or not wizard_state.selected_type_ids:
                step = STEP_FAMILY_TYPES
                continue

            targets = get_target_instances(selected_document, wizard_state.selected_type_ids)
            if not targets:
                TaskDialog.Show(__title__, "No target instances were found for the selected family types.")
                step = STEP_FAMILY_TYPES
                continue

            unit_text = "Offset values ({}) relative to each target family".format(
                get_length_unit_label(doc)
            )
            parse_length = lambda label, text: try_parse_length_value(doc.GetUnits(), label, text)
            offset_window = OffsetWindow(
                "OffsetWindow.xaml",
                unit_text,
                parse_length,
                wizard_state.x_offset_text,
                wizard_state.y_offset_text,
                wizard_state.z_offset_text,
                wizard_state.align_orientation,
            )
            offset_window.ShowDialog()

            wizard_state.x_offset_text = offset_window.x_offset_text
            wizard_state.y_offset_text = offset_window.y_offset_text
            wizard_state.z_offset_text = offset_window.z_offset_text
            wizard_state.align_orientation = bool(offset_window.align_orientation)

            if offset_window.result == "ok":
                offset = DB.XYZ(
                    offset_window.x_offset,
                    offset_window.y_offset,
                    offset_window.z_offset,
                )
                summary = place_copies(
                    doc,
                    active_view,
                    source_element,
                    targets,
                    offset,
                    wizard_state.align_orientation,
                )
                _show_summary(summary, len(targets))
                return

            if offset_window.was_back:
                step = STEP_FAMILY_TYPES
                continue
            return


if __name__ == "__main__":
    _run()
