# -*- coding: utf-8 -*-
"""Revit API layer for the Links Loader tool."""

from pyrevit import DB

from easybim.link_reload import collect_manage_links_elements
from easybim.link_reload import get_external_file_reference
from easybim.link_reload import get_linked_cad_type_ids
from easybim.link_reload import is_linked_manage_link_element
from easybim.link_reload import is_unloaded_link_element

from links_loader_state import LinkRecord
from links_loader_state import SUPPORTED_TYPES


def get_link_path(doc, link_elem):
    ext_ref = get_external_file_reference(doc, link_elem)
    if ext_ref is None:
        return ""
    try:
        model_path = ext_ref.GetAbsolutePath()
        return str(
            DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(model_path)
        )
    except Exception:
        return ""


def get_link_path_type(doc, link_elem):
    ext_ref = get_external_file_reference(doc, link_elem)
    if ext_ref is None:
        return ""
    try:
        return str(ext_ref.PathType)
    except Exception:
        return ""


def _link_type_name(link_elem):
    try:
        return type(link_elem).__name__
    except Exception:
        return ""


def _element_name(link_elem):
    name = getattr(link_elem, "Name", None)
    if name:
        return str(name)
    return ""


def collect_link_records(doc):
    all_elems = collect_manage_links_elements(doc)
    linked_cad_ids = get_linked_cad_type_ids(doc)

    records = []
    elements = []
    for elem in all_elems:
        if not is_linked_manage_link_element(doc, elem, linked_cad_ids):
            continue
        type_name = _link_type_name(elem)
        if type_name not in SUPPORTED_TYPES:
            continue
        path = get_link_path(doc, elem)
        path_type = get_link_path_type(doc, elem)
        is_loaded = not is_unloaded_link_element(doc, elem)
        name = _element_name(elem)
        records.append(LinkRecord(name, type_name, path, path_type, is_loaded))
        elements.append(elem)
    return records, elements


def get_project_name(doc):
    try:
        title = doc.Title
        if title:
            return str(title)
    except Exception:
        pass
    try:
        import os
        return os.path.basename(doc.PathName or "")
    except Exception:
        return ""


def get_host_version(doc):
    try:
        return str(doc.Application.VersionNumber)
    except Exception:
        return ""


def build_link_lookup(link_records, link_elements):
    lookup = {}
    for rec, elem in zip(link_records, link_elements):
        lookup[rec.name.lower()] = elem
    return lookup


def apply_revit_link_path(doc, link_elem, new_path):
    try:
        new_model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(
            new_path
        )
    except Exception as ex:
        return (False, "Invalid path: {}".format(ex))

    try:
        workset_config = DB.WorksetConfiguration()
    except Exception:
        workset_config = None

    try:
        if workset_config is not None:
            link_elem.LoadFrom(new_model_path, workset_config)
        else:
            link_elem.LoadFrom(new_model_path, None)
        return (True, "")
    except Exception as ex:
        return (False, str(ex))


def apply_import_plan(doc, plan_items, link_lookup):
    results = []
    for item in plan_items:
        if item.status != "update" or not item.is_selected:
            continue
        elem = link_lookup.get(item.name.lower())
        if elem is None:
            results.append((item.name, False, "Element not found"))
            continue
        ok, err = apply_revit_link_path(doc, elem, item.new_path)
        results.append((item.name, ok, err))
    return results
