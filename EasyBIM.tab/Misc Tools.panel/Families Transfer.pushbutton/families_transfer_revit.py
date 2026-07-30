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
from families_transfer_state import OpenFamilyDocumentOption
from families_transfer_state import SOURCE_OPEN_RFA
from families_transfer_state import TargetDocumentOption
from families_transfer_state import TransferResult
from families_transfer_state import TransferSummary
from families_transfer_state import build_unique_export_path
from families_transfer_state import is_open_family_document_key
from families_transfer_state import make_project_family_key
from families_transfer_state import normalize_category_name
from families_transfer_state import sort_family_options
from families_transfer_state import sort_open_family_documents
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


def _category_name(category):
    try:
        return normalize_category_name(getattr(category, "Name", ""))
    except Exception:
        return normalize_category_name("")


def _family_category_name(family):
    try:
        return _category_name(getattr(family, "FamilyCategory", None))
    except Exception:
        return normalize_category_name("")


def _family_document_category_name(document):
    try:
        return _family_category_name(getattr(document, "OwnerFamily", None))
    except Exception:
        return normalize_category_name("")


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
    try:
        if bool(document.IsLinked):
            return False
    except Exception:
        pass
    return True


def _is_family_document(document):
    if document is None:
        return False
    try:
        return bool(document.IsFamilyDocument)
    except Exception:
        return False


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


def get_open_family_documents(uiapp, source_doc=None, selected_document_keys=None):
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
        if not _is_family_document(document):
            continue
        if source_doc is not None and _same_document(document, source_doc):
            continue

        document_key = _doc_key(document)
        options.append(
            OpenFamilyDocumentOption(
                _doc_title(document),
                document_key,
                is_selected=document_key in selected_document_keys,
                document=document,
                category_name=_family_document_category_name(document),
            )
        )

    return sort_open_family_documents(options)


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


def _family_option(family, is_selected=False):
    if not is_transferable_family(family):
        return None

    family_key = _family_key(family)
    if not family_key:
        return None

    return FamilyOption(
        _family_name(family),
        make_project_family_key(family_key),
        is_selected=is_selected,
        family=family,
        element_id=getattr(family, "Id", None),
        category_name=_family_category_name(family),
    )


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


def get_selected_family_options_from_selection(doc, uidoc):
    options = []
    seen = set()
    if doc is None or uidoc is None:
        return options

    try:
        selected_ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        selected_ids = []

    for element_id in selected_ids:
        element = doc.GetElement(element_id)
        family = _family_from_element(element)
        option = _family_option(family, is_selected=True)
        if option is None or option.family_key in seen:
            continue
        seen.add(option.family_key)
        options.append(option)

    return sort_family_options(options)


def get_selected_family_keys_from_selection(doc, uidoc):
    return set(option.family_key for option in get_selected_family_options_from_selection(doc, uidoc))


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
        option = _family_option(
            family,
            is_selected=make_project_family_key(family_key) in selected_family_keys,
        )
        if option is not None:
            options.append(option)

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
        if make_project_family_key(_family_key(family)) == family_key:
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


def pick_more_family_options(uidoc):
    picked_options = []
    seen = set()
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
        option = _family_option(family, is_selected=True)
        if option is None or option.family_key in seen:
            continue
        seen.add(option.family_key)
        picked_options.append(option)

    return sort_family_options(picked_options)


def pick_more_family_keys(uidoc):
    return set(option.family_key for option in pick_more_family_options(uidoc))


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


def _is_open_rfa_family_option(family_option):
    family_key = _safe_text(getattr(family_option, "family_key", ""))
    return (
        getattr(family_option, "source_kind", None) == SOURCE_OPEN_RFA
        or is_open_family_document_key(family_key)
    )


def _load_family_document_into_targets(source_doc, family_doc, family_name, targets, summary, load_options):
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


def _transfer_open_rfa_family(source_doc, family_option, targets, summary, load_options):
    family_name = _safe_text(getattr(family_option, "name", ""))
    family_doc = getattr(family_option, "family_document", None)
    if family_doc is None:
        summary.skipped.append(TransferResult(family_name, "Opened .rfa files", "family document is unavailable"))
        return
    _load_family_document_into_targets(source_doc, family_doc, family_name, targets, summary, load_options)


def _transfer_project_family(source_doc, family_option, targets, summary, load_options):
    family_name = _safe_text(getattr(family_option, "name", ""))
    family = resolve_family(source_doc, family_option)
    if not is_transferable_family(family):
        summary.skipped.append(TransferResult(family_name, "Source", "family is not editable"))
        return

    family_doc = None
    try:
        family_doc = _edit_family(source_doc, family)
    except Exception as ex:
        summary.failed.append(TransferResult(family_name, "Source", "EditFamily failed: {}".format(ex)))
        return

    try:
        _load_family_document_into_targets(source_doc, family_doc, family_name, targets, summary, load_options)
    finally:
        _close_family_doc(family_doc)


def transfer_families(source_doc, family_options, target_options):
    summary = TransferSummary()
    targets = list(target_options or [])
    load_options = FamilyTransferLoadOptions()

    for family_option in list(family_options or []):
        if _is_open_rfa_family_option(family_option):
            _transfer_open_rfa_family(source_doc, family_option, targets, summary, load_options)
        else:
            _transfer_project_family(source_doc, family_option, targets, summary, load_options)

    return summary


def _export_open_rfa_family(family_option, folder_path, used_paths, summary):
    family_name = _safe_text(getattr(family_option, "name", ""))
    family_doc = getattr(family_option, "family_document", None)
    export_path = build_unique_export_path(folder_path, family_name, used_paths)
    if family_doc is None:
        summary.skipped.append(TransferResult(family_name, export_path, "family document is unavailable"))
        return

    try:
        save_options = DB.SaveAsOptions()
        save_options.OverwriteExistingFile = True
        family_doc.SaveAs(export_path, save_options)
        summary.loaded.append(TransferResult(family_name, export_path, "exported"))
    except Exception as ex:
        summary.failed.append(TransferResult(family_name, export_path, "Export failed: {}".format(ex)))


def _export_project_family(source_doc, family_option, folder_path, used_paths, summary):
    family_name = _safe_text(getattr(family_option, "name", ""))
    family = resolve_family(source_doc, family_option)
    if not is_transferable_family(family):
        summary.skipped.append(TransferResult(family_name, folder_path, "family is not editable"))
        return

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


def export_families(source_doc, family_options, folder_path):
    summary = TransferSummary()
    used_paths = set()

    for family_option in list(family_options or []):
        if _is_open_rfa_family_option(family_option):
            _export_open_rfa_family(family_option, folder_path, used_paths, summary)
        else:
            _export_project_family(source_doc, family_option, folder_path, used_paths, summary)

    return summary


def close_open_family_documents(open_family_documents):
    summary = TransferSummary()
    for document_option in list(open_family_documents or []):
        display_name = _safe_text(getattr(document_option, "display_name", ""))
        document = getattr(document_option, "document", None)
        if document is None:
            summary.skipped.append(TransferResult(display_name, "Opened .rfa files", "document is unavailable"))
            continue

        try:
            document.Close(False)
            summary.closed_rfa_count += 1
        except Exception as ex:
            summary.failed.append(TransferResult(display_name, "Opened .rfa files", "Close failed: {}".format(ex)))

    return summary
