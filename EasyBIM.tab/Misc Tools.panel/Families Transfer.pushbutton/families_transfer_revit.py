# -*- coding: utf-8 -*-
"""Revit-facing logic for the Families Transfer command."""

# pylint: disable=import-error,invalid-name,broad-except
import os

import clr

clr.AddReference("RevitAPIUI")
clr.AddReference("System.Windows.Forms")

from Autodesk.Revit.UI.Selection import ISelectionFilter
from Autodesk.Revit.UI.Selection import ObjectType
from System.Windows.Forms import DialogResult
from System.Windows.Forms import FolderBrowserDialog

from pyrevit import DB
from pyrevit.compat import get_elementid_value_func

from families_transfer_state import FamilyOption
from families_transfer_state import TargetDocumentOption
from families_transfer_state import TransferResult
from families_transfer_state import TransferSummary
from families_transfer_state import build_unique_export_path
from families_transfer_state import sort_family_options
from families_transfer_state import sort_target_documents


PICK_PROMPT = "Select family instances to include in Families Transfer"

get_elementid_value = get_elementid_value_func()


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _eid_key(element_id):
    if not element_id:
        return ""
    try:
        return str(get_elementid_value(element_id))
    except Exception:
        try:
            return str(element_id.IntegerValue)
        except Exception:
            return _safe_text(element_id)


def _doc_path(document):
    try:
        return _safe_text(getattr(document, "PathName", "")).strip()
    except Exception:
        return ""


def _doc_title(document):
    try:
        path = _doc_path(document)
        if path:
            return os.path.basename(path)
    except Exception:
        pass
    return _safe_text(getattr(document, "Title", "")) or "(Untitled Project)"


def _doc_key(document):
    path = _doc_path(document)
    if path:
        return "path|{}".format(path.lower())

    title = _safe_text(getattr(document, "Title", "")).lower()
    try:
        return "memory|{}|{}".format(title, document.GetHashCode())
    except Exception:
        return "memory|{}".format(title)


def _same_document(doc_a, doc_b):
    if doc_a is doc_b:
        return True
    return _doc_key(doc_a) == _doc_key(doc_b)


def _is_project_document(document):
    if document is None:
        return False
    try:
        if bool(document.IsFamilyDocument):
            return False
    except Exception:
        pass
    return True


def _application_from(uiapp, source_doc):
    app = getattr(uiapp, "Application", None)
    if app is not None:
        return app
    try:
        return source_doc.Application
    except Exception:
        return None


def get_open_target_documents(uiapp, source_doc, selected_document_keys=None):
    selected_document_keys = set(selected_document_keys or [])
    app = _application_from(uiapp, source_doc)
    documents = []
    if app is not None:
        try:
            documents = list(app.Documents)
        except Exception:
            documents = []

    options = []
    for document in documents:
        if not _is_project_document(document):
            continue
        if _same_document(document, source_doc):
            continue

        document_key = _doc_key(document)
        options.append(
            TargetDocumentOption(
                _doc_title(document),
                document_key,
                is_selected=document_key in selected_document_keys,
                document=document,
            )
        )

    return sort_target_documents(options)


def is_transferable_family(family):
    if family is None:
        return False

    try:
        if bool(family.IsInPlace):
            return False
    except Exception:
        pass

    try:
        if not bool(family.IsEditable):
            return False
    except Exception:
        pass

    return True


def _family_key(family):
    return _eid_key(getattr(family, "Id", None))


def _family_name(family):
    return _safe_text(getattr(family, "Name", "")) or "(Unnamed Family)"


def _family_from_element(element):
    if element is None:
        return None

    if isinstance(element, DB.Family):
        return element

    try:
        symbol = element.Symbol
    except Exception:
        symbol = None

    try:
        family = symbol.Family
    except Exception:
        family = None

    return family


def get_selected_family_keys_from_selection(doc, uidoc):
    keys = set()
    if doc is None or uidoc is None:
        return keys

    try:
        selected_ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        selected_ids = []

    for element_id in selected_ids:
        element = doc.GetElement(element_id)
        family = _family_from_element(element)
        if not is_transferable_family(family):
            continue
        family_key = _family_key(family)
        if family_key:
            keys.add(family_key)

    return keys


def _collect_families(doc):
    try:
        return list(DB.FilteredElementCollector(doc).OfClass(DB.Family).ToElements())
    except Exception:
        try:
            return list(DB.FilteredElementCollector(doc).OfClass(DB.Family))
        except Exception:
            return []


def get_source_family_options(doc, selected_family_keys=None):
    selected_family_keys = set(selected_family_keys or [])
    options = []

    for family in _collect_families(doc):
        if not is_transferable_family(family):
            continue
        family_key = _family_key(family)
        if not family_key:
            continue
        options.append(
            FamilyOption(
                _family_name(family),
                family_key,
                is_selected=family_key in selected_family_keys,
                family=family,
                element_id=getattr(family, "Id", None),
            )
        )

    return sort_family_options(options)


def resolve_family(doc, family_option):
    family = getattr(family_option, "family", None)
    if family is not None:
        return family

    element_id = getattr(family_option, "element_id", None)
    if element_id is not None:
        try:
            return doc.GetElement(element_id)
        except Exception:
            pass

    family_key = _safe_text(getattr(family_option, "family_key", ""))
    for family in _collect_families(doc):
        if _family_key(family) == family_key:
            return family
    return None


class FamilyTransferSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        family = _family_from_element(elem)
        return is_transferable_family(family)

    def AllowReference(self, reference, position):
        del reference, position
        return False


class FamilyTransferLoadOptions(DB.IFamilyLoadOptions):
    def __init__(self):
        self.reset()

    def reset(self):
        self.loaded_existing = False

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        del familyInUse
        self.loaded_existing = True
        try:
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        del sharedFamily, familyInUse
        self.loaded_existing = True
        try:
            source.Value = DB.FamilySource.Family
        except Exception:
            pass
        try:
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True


def pick_more_family_keys(uidoc):
    picked_keys = set()
    references = uidoc.Selection.PickObjects(
        ObjectType.Element,
        FamilyTransferSelectionFilter(),
        PICK_PROMPT,
    )

    doc = uidoc.Document
    for reference in references or []:
        try:
            element = doc.GetElement(reference.ElementId)
        except Exception:
            element = None
        family = _family_from_element(element)
        if not is_transferable_family(family):
            continue
        family_key = _family_key(family)
        if family_key:
            picked_keys.add(family_key)

    return picked_keys


def pick_export_folder():
    dialog = FolderBrowserDialog()
    dialog.Description = "Select a folder for exported family files."
    dialog.ShowNewFolderButton = True
    if dialog.ShowDialog() == DialogResult.OK:
        return _safe_text(dialog.SelectedPath)
    return None


def _edit_family(source_doc, family):
    return source_doc.EditFamily(family)


def _close_family_doc(family_doc):
    if family_doc is None:
        return
    try:
        family_doc.Close(False)
    except Exception:
        pass


def transfer_families(source_doc, family_options, target_options):
    summary = TransferSummary()
    targets = list(target_options or [])
    load_options = FamilyTransferLoadOptions()

    for family_option in list(family_options or []):
        family_name = _safe_text(getattr(family_option, "name", ""))
        family = resolve_family(source_doc, family_option)
        if not is_transferable_family(family):
            summary.skipped.append(TransferResult(family_name, "Source", "family is not editable"))
            continue

        family_doc = None
        try:
            family_doc = _edit_family(source_doc, family)
        except Exception as ex:
            summary.failed.append(TransferResult(family_name, "Source", "EditFamily failed: {}".format(ex)))
            continue

        try:
            for target_option in targets:
                target_doc = getattr(target_option, "document", None)
                target_name = _safe_text(getattr(target_option, "display_name", ""))
                if target_doc is None:
                    summary.skipped.append(TransferResult(family_name, target_name, "target document is unavailable"))
                    continue
                if _same_document(source_doc, target_doc):
                    summary.skipped.append(TransferResult(family_name, target_name, "target is the source document"))
                    continue

                try:
                    load_options.reset()
                    loaded = family_doc.LoadFamily(target_doc, load_options)
                except Exception as ex:
                    summary.failed.append(TransferResult(family_name, target_name, "LoadFamily failed: {}".format(ex)))
                    continue

                if loaded:
                    status = "overwritten" if load_options.loaded_existing else "loaded"
                    summary.loaded.append(TransferResult(family_name, target_name, status))
                else:
                    summary.failed.append(TransferResult(family_name, target_name, "LoadFamily returned false"))
        finally:
            _close_family_doc(family_doc)

    return summary


def export_families(source_doc, family_options, folder_path):
    summary = TransferSummary()
    used_paths = set()

    for family_option in list(family_options or []):
        family_name = _safe_text(getattr(family_option, "name", ""))
        family = resolve_family(source_doc, family_option)
        if not is_transferable_family(family):
            summary.skipped.append(TransferResult(family_name, folder_path, "family is not editable"))
            continue

        export_path = build_unique_export_path(folder_path, family_name, used_paths)
        family_doc = None
        try:
            family_doc = _edit_family(source_doc, family)
            save_options = DB.SaveAsOptions()
            save_options.OverwriteExistingFile = True
            family_doc.SaveAs(export_path, save_options)
            summary.loaded.append(TransferResult(family_name, export_path, "exported"))
        except Exception as ex:
            summary.failed.append(TransferResult(family_name, export_path, "Export failed: {}".format(ex)))
        finally:
            _close_family_doc(family_doc)

    return summary
