# -*- coding: utf-8 -*-
"""Revit reads that feed the Clash Detection Mode setup window."""
# pylint: disable=import-error,invalid-name,broad-except

from __future__ import print_function

from pyrevit import DB

from easybim import clash_detection_state as state
from easybim.compat import eid_to_int
from easybim.compat import safe_text


def collect_model_options(doc):
    """`Categories from` entries: the host project plus every loaded link.

    Links whose document is unloaded are skipped - there is nothing to check
    against, and offering them would only produce a dead selection.
    """
    options = [
        state.ModelOption(
            display_name="Current Project",
            model_key=state.HOST_MODEL_KEY,
            model_ref=state.HOST_MODEL_REF,
            is_current_project=True,
            document=doc,
        )
    ]

    try:
        link_instances = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.RevitLinkInstance)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        link_instances = []

    for link_instance in link_instances:
        try:
            link_doc = link_instance.GetLinkDocument()
        except Exception:
            link_doc = None
        if link_doc is None:
            continue
        model_ref = state.link_model_ref(eid_to_int(link_instance.Id))
        if model_ref is None:
            continue
        try:
            display_name = safe_text(link_instance.Name)
        except Exception:
            display_name = ""
        options.append(
            state.ModelOption(
                display_name=display_name or safe_text(link_doc.Title) or "Linked Model",
                model_key=model_ref,
                model_ref=model_ref,
                is_current_project=False,
                document=link_doc,
                link_instance=link_instance,
            )
        )
    return options


def collect_categories(document):
    """Model categories that actually have placed instances.

    Listing every model category would show ~200 rows the project cannot
    clash on.  Probing each category with ``FirstElementId`` keeps this to
    one indexed quick-filter query per category with an early exit, instead
    of walking every element in the model.
    """
    if document is None:
        return []

    categories = []
    try:
        document_categories = list(document.Settings.Categories)
    except Exception:
        return []

    for category in document_categories:
        try:
            if category.CategoryType != DB.CategoryType.Model:
                continue
        except Exception:
            continue

        category_id = eid_to_int(getattr(category, "Id", None))
        if category_id is None:
            continue

        try:
            first_id = (
                DB.FilteredElementCollector(document)
                .OfCategoryId(category.Id)
                .WhereElementIsNotElementType()
                .FirstElementId()
            )
        except Exception:
            continue
        if first_id is None or eid_to_int(first_id) in (None, -1):
            continue

        name = safe_text(getattr(category, "Name", "")).strip()
        if not name:
            continue
        categories.append(state.CategoryOption(name=name, category_id=category_id))

    return state.sort_categories(categories)


def link_instances_by_ref(model_options):
    """Map each link's model ref to its instance for the engine's contexts."""
    mapping = {}
    for option in model_options or []:
        if option.link_instance is not None:
            mapping[option.model_ref] = option.link_instance
    return mapping
