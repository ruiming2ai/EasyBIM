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

from easybim.copy_paste import copy_paste_options

from families_transfer_state import FamilyOption
from families_transfer_state import LinkDocumentOption
from families_transfer_state import OpenFamilyDocumentOption
from families_transfer_state import SOURCE_LINK
from families_transfer_state import SOURCE_OPEN_RFA
from families_transfer_state import TargetDocumentOption
from families_transfer_state import TransferResult
from families_transfer_state import TransferSummary
from families_transfer_state import build_unique_export_path
from families_transfer_state import is_link_family_key
from families_transfer_state import is_open_family_document_key
from families_transfer_state import make_link_family_key
from families_transfer_state import make_project_family_key
from families_transfer_state import normalize_category_name
from families_transfer_state import sort_family_options
from families_transfer_state import sort_open_family_documents
from families_transfer_state import sort_target_documents


PICK_PROMPT = "Select family instances to include in Families Transfer"

# Guarded: a declined overwrite is reported as InvalidOperationException, but
# the module must still import where Autodesk.Revit.Exceptions is unreachable.
try:
    from Autodesk.Revit.Exceptions import InvalidOperationException as _INVALID_OPERATION
except Exception:
    _INVALID_OPERATION = None

get_elementid_value = get_elementid_value_func()


def _id_list(element_ids):
    from System.Collections.Generic import List as ClrList
    collection = ClrList[DB.ElementId]()
    for element_id in element_ids:
        collection.Add(element_id)
    return collection


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


def _is_editable_family(family):
    try:
        return bool(family.IsEditable)
    except Exception:
        return True


def is_transferable_family(family, require_editable=True):
    """Eligibility, with the editable gate optional.

    In-place families are always out: they have no definition to extract.
    ``IsEditable`` is different - it is unverified inside a linked document,
    and a hard gate there would empty the whole list, which reads as "this
    link has no families" rather than as a refusal.  Link rows record the
    flag instead and report the real reason per family.
    """
    if family is None:
        return False

    try:
        if bool(family.IsInPlace):
            return False
    except Exception:
        pass

    if require_editable and not _is_editable_family(family):
        return False

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
    """The loadable family behind any element the user can click.

    Exactly two classes in the Revit API declare ``Symbol``: ``FamilyInstance``
    (so model families and generic annotations arrive that way) and
    ``TextElement``, whose ``Symbol`` is a ``TextElementType`` and not a
    family at all - hence the isinstance guard, which is not optional.

    Everything else reaches its type through ``GetTypeId()``. Tags are the
    reason: ``IndependentTag`` derives from ``Element``, has no ``Symbol``,
    and its type is a plain ``FamilySymbol``. Room, area and space tags are
    the same shape.

    Returns ``None`` for system-family elements - text notes, dimensions,
    detail lines, matchlines - which have no owning family to transfer.
    """
    if element is None:
        return None

    if isinstance(element, DB.Family):
        return element

    symbol = None
    try:
        symbol = element.Symbol
    except Exception:
        symbol = None
    if not isinstance(symbol, DB.FamilySymbol):
        symbol = None

    if symbol is None:
        try:
            type_id = element.GetTypeId()
        except Exception:
            type_id = None
        # An element that cannot have a type assigned reports InvalidElementId;
        # a matchline is exactly that case.
        if type_id is not None and type_id != DB.ElementId.InvalidElementId:
            try:
                candidate = element.Document.GetElement(type_id)
            except Exception:
                candidate = None
            if isinstance(candidate, DB.FamilySymbol):
                symbol = candidate

    if symbol is None and isinstance(element, DB.FamilySymbol):
        symbol = element

    if symbol is None:
        return None

    try:
        family = symbol.Family
    except Exception:
        family = None

    if not isinstance(family, DB.Family):
        return None
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
        # _family_option applies the eligibility test itself; testing here as
        # well doubled the CLR property reads over the whole model.
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


def _raw_family_key_from_option(family_option):
    """The bare element id inside a namespaced family key."""
    family_key = _safe_text(getattr(family_option, "family_key", ""))
    if not family_key:
        return ""
    return family_key.rsplit("|", 1)[-1]


def _family_index(source_doc, index_cache):
    """``{raw element id: Family}`` for one document, collected once.

    The fallback lookup used to run a whole ``FilteredElementCollector`` pass
    per family option, which is fine while nothing reaches it and quadratic
    the moment something does.
    """
    if index_cache is None:
        index_cache = {}
    document_key = _doc_key(source_doc)
    index = index_cache.get(document_key)
    if index is None:
        index = {}
        for family in _collect_families(source_doc):
            raw_key = _family_key(family)
            if raw_key:
                index[raw_key] = family
        index_cache[document_key] = index
    return index


def resolve_family(doc, family_option, index_cache=None):
    # A link family belongs to the linked document; asking the active project
    # for it by id would either miss or hand back an unrelated element.
    source_document = getattr(family_option, "source_document", None) or doc

    family = getattr(family_option, "family", None)
    if family is not None:
        return family

    element_id = getattr(family_option, "element_id", None)
    if element_id is not None:
        try:
            resolved = source_document.GetElement(element_id)
        except Exception:
            resolved = None
        if resolved is not None:
            return resolved

    raw_family_key = _raw_family_key_from_option(family_option)
    if not raw_family_key:
        return None
    return _family_index(source_document, index_cache).get(raw_family_key)


# ---------------------------------------------------------------------------
# Revit links
# ---------------------------------------------------------------------------

def _collect_link_instances(doc):
    try:
        return list(
            DB.FilteredElementCollector(doc)
            .OfClass(DB.RevitLinkInstance)
            .WhereElementIsNotElementType()
            .ToElements())
    except Exception:
        return []


def _link_document(instance):
    try:
        return instance.GetLinkDocument()
    except Exception:
        return None


def _link_instance_label(instance):
    name = _safe_text(getattr(instance, "Name", ""))
    if name:
        # Instance names read "Arch.rvt : 1 : location <Not Shared>".
        return name.split(":")[0].strip() or name
    return "(Unnamed Link)"


def get_link_document_options(doc, selected_document_keys=None):
    """One row per linked *document*, not per instance.

    Two instances of one link share a single ``Document``, so listing
    instances would offer the same families twice.  Links nested inside a
    link are not reachable through ``GetLinkDocument`` and are not listed.
    """
    selected_document_keys = set(selected_document_keys or [])
    options = []
    seen = set()

    for instance in _collect_link_instances(doc):
        link_doc = _link_document(instance)

        if link_doc is None:
            label = _link_instance_label(instance)
            document_key = "unloaded|{}".format(label.lower())
            if document_key in seen:
                continue
            seen.add(document_key)
            options.append(
                LinkDocumentOption(
                    label,
                    document_key,
                    is_loaded=False,
                    note="not loaded",
                )
            )
            continue

        document_key = _doc_key(link_doc)
        if document_key in seen:
            continue
        seen.add(document_key)
        options.append(
            LinkDocumentOption(
                _doc_title(link_doc),
                document_key,
                is_selected=document_key in selected_document_keys,
                document=link_doc,
            )
        )

    return sort_target_documents(options)


def _open_project_documents_by_key(uiapp, source_doc):
    app = _application_from(uiapp, source_doc)
    documents = {}
    if app is None:
        return documents
    try:
        open_documents = list(app.Documents)
    except Exception:
        return documents

    for document in open_documents:
        if not _is_project_document(document):
            continue
        documents[_doc_key(document)] = document
    return documents


def _probe_edit_family(link_doc):
    """Ask the link once whether a family can be taken out of it.

    ``EditFamily`` is refused on a read-only document, and a linked document
    is documented as read-only.  Whether Revit actually refuses is the one
    thing that cannot be settled off Revit, so it is asked here - once per
    link, before the browser opens - rather than discovered per family in the
    middle of a batch.
    """
    read_only = False
    try:
        read_only = bool(link_doc.IsReadOnly)
    except Exception:
        pass

    candidate = None
    for family in _collect_families(link_doc):
        if is_transferable_family(family, require_editable=False):
            candidate = family
            break

    if candidate is None:
        return False, "no loadable families were found in this link"

    family_doc = None
    try:
        family_doc = link_doc.EditFamily(candidate)
    except Exception as ex:
        detail = _safe_text(ex).strip().splitlines()
        detail = detail[0] if detail else "EditFamily was refused"
        if read_only:
            return False, (
                "Revit will not open a family out of this link "
                "(the linked document is read-only: {}). Families are copied "
                "into the target instead, which cannot overwrite one that is "
                "already there.".format(detail)
            )
        return False, (
            "Revit will not open a family out of this link ({}). Families are "
            "copied into the target instead, which cannot overwrite one that "
            "is already there.".format(detail)
        )
    finally:
        _close_family_doc(family_doc)

    return True, ""


def prepare_link_documents(uiapp, source_doc, link_options):
    """Probe every checked link once and cache the verdict on its row."""
    open_by_key = _open_project_documents_by_key(uiapp, source_doc)

    for link_option in list(link_options or []):
        if not bool(getattr(link_option, "is_selected", False)):
            continue
        if not bool(getattr(link_option, "is_loaded", True)):
            continue
        if getattr(link_option, "is_extractable", None) is not None:
            continue

        open_document = open_by_key.get(link_option.document_key)
        if open_document is not None:
            # The same file is open in this session, so there is no read-only
            # question to answer: read it from the real document.
            link_option.read_document = open_document
            link_option.is_extractable = True
            link_option.note = "read from the copy already open in this session"
            continue

        link_option.read_document = link_option.document
        is_extractable, note = _probe_edit_family(link_option.document)
        link_option.is_extractable = is_extractable
        link_option.note = note

    return link_options


def _link_family_rows(link_option):
    read_doc = getattr(link_option, "read_document", None) or link_option.document
    if read_doc is None:
        return []

    label = _safe_text(getattr(link_option, "display_name", ""))
    document_key = _safe_text(getattr(link_option, "document_key", ""))
    rows = []

    for family in _collect_families(read_doc):
        if not is_transferable_family(family, require_editable=False):
            continue
        raw_family_key = _family_key(family)
        if not raw_family_key:
            continue
        rows.append(
            FamilyOption(
                _family_name(family),
                make_link_family_key(document_key, raw_family_key),
                family=family,
                element_id=getattr(family, "Id", None),
                source_kind=SOURCE_LINK,
                source_document=read_doc,
                source_label=label,
                document_key=document_key,
                category_name=_family_category_name(family),
                is_editable=_is_editable_family(family),
            )
        )
    return rows


def get_link_family_options(link_options, selected_family_keys=None, cache=None):
    """Families from the checked links only, collected once per link.

    Scanning every link up front would cost a full collector pass per linked
    document before the user has said which link they care about.
    """
    selected_family_keys = set(selected_family_keys or [])
    cache = cache if cache is not None else {}
    options = []

    for link_option in list(link_options or []):
        if not bool(getattr(link_option, "is_selected", False)):
            continue
        if not bool(getattr(link_option, "is_loaded", True)):
            continue

        document_key = _safe_text(getattr(link_option, "document_key", ""))
        rows = cache.get(document_key)
        if rows is None:
            rows = _link_family_rows(link_option)
            cache[document_key] = rows

        for row in rows:
            row.is_selected = row.family_key in selected_family_keys
            options.append(row)

    return sort_family_options(options)


def link_notes(link_options):
    """The one-sentence reason for every checked link that cannot be read."""
    notes = []
    for link_option in list(link_options or []):
        if not bool(getattr(link_option, "is_selected", False)):
            continue
        if getattr(link_option, "is_extractable", None) is not False:
            continue
        note = _safe_text(getattr(link_option, "note", ""))
        if note:
            notes.append("{}: {}".format(link_option.display_name, note))
    return notes


class FamilyTransferSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        family = _family_from_element(elem)
        return is_transferable_family(family)

    def AllowReference(self, reference, position):
        del reference, position
        return False


class FamilyTransferLoadOptions(DB.IFamilyLoadOptions):
    """The UI-less fallback: overwrite without asking.

    Only used when Revit's own prompt is unreachable - `native_family_load_options`
    returns None in UI-less mode. When it is reachable the user answers the
    real dialog instead, so nothing here runs.
    """

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        del familyInUse
        try:
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        del sharedFamily, familyInUse
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


def _is_link_family_option(family_option):
    family_key = _safe_text(getattr(family_option, "family_key", ""))
    return (
        getattr(family_option, "source_kind", None) == SOURCE_LINK
        or is_link_family_key(family_key)
    )


def _keep_going(progress, done, total):
    """``False`` once the user has cancelled the progress bar."""
    if progress is None:
        return True
    try:
        return bool(progress(done, total))
    except Exception:
        return True


def native_family_load_options():
    """Revit's own "Family Already Exists" prompt, or ``None``.

    ``UIDocument.GetRevitUIFamilyLoadOptions`` is a public *static* method
    returning an ``IFamilyLoadOptions`` that puts Revit's real dialog in front
    of the user - the same one an interactive load shows. It throws in UI-less
    mode, and the import itself can fail where ``Autodesk.Revit.UI`` is not
    reachable, so both are guarded and the caller falls back.

    Note ``RevitUIFamilyLoadOptions``, which the LoadFamily docs name, is not
    an instantiable type - it is absent from the assembly metadata in every
    shipped version, and the only implementer of ``IFamilyLoadOptions`` there
    is a non-public proxy. This accessor is the only way in.
    """
    try:
        from Autodesk.Revit.UI import UIDocument
    except Exception:
        return None
    try:
        return UIDocument.GetRevitUIFamilyLoadOptions()
    except Exception:
        return None


def _is_declined_overwrite(ex, family_existed):
    """Did the user answer "no" to the overwrite prompt?

    A declined load arrives as an exception, not a ``False`` return:
    ``LoadFamily`` documents ``InvalidOperationException`` for "the load was
    cancelled due to a conflict and a False return from one of the interface
    methods".

    The prompt only appears when the family was already in the target, so that
    is the signal used. The message text is undocumented and is only a
    secondary check - keying on it alone would misfile real failures.
    """
    if not family_existed:
        return False
    if _INVALID_OPERATION is not None and isinstance(ex, _INVALID_OPERATION):
        return True
    return "cancel" in _safe_text(ex).lower()


def _load_family_document_into_targets(source_doc, family_doc, family_name, targets, summary,
                                       load_options, name_cache):
    for target_option in targets:
        target_doc = getattr(target_option, "document", None)
        target_name = _safe_text(getattr(target_option, "display_name", ""))
        if target_doc is None:
            summary.skipped.append(TransferResult(family_name, target_name, "target document is unavailable"))
            continue
        if _same_document(source_doc, target_doc):
            summary.skipped.append(TransferResult(family_name, target_name, "target is the source document"))
            continue

        # Asked before the load, for two reasons: it decides whether the
        # result is "loaded" or "overwritten" now that Revit's own options
        # object answers the prompt and cannot report back, and it is what
        # tells a declined overwrite apart from a real failure.
        target_names = _target_family_names(target_doc, name_cache)
        family_existed = family_name.lower() in target_names

        try:
            loaded = family_doc.LoadFamily(target_doc, load_options)
        except Exception as ex:
            if _is_declined_overwrite(ex, family_existed):
                summary.skipped.append(
                    TransferResult(family_name, target_name,
                                   "already in the target; overwrite declined")
                )
            else:
                summary.failed.append(TransferResult(family_name, target_name, "LoadFamily failed: {}".format(ex)))
            continue

        if loaded:
            summary.loaded.append(
                TransferResult(family_name, target_name,
                               "overwritten" if family_existed else "loaded")
            )
            target_names.add(family_name.lower())
        else:
            summary.failed.append(TransferResult(family_name, target_name, "LoadFamily returned false"))


def _transfer_open_rfa_family(source_doc, family_option, targets, summary, load_options, name_cache):
    family_name = _safe_text(getattr(family_option, "name", ""))
    family_doc = getattr(family_option, "family_document", None)
    if family_doc is None:
        summary.skipped.append(TransferResult(family_name, "Opened .rfa files", "family document is unavailable"))
        return
    _load_family_document_into_targets(source_doc, family_doc, family_name, targets, summary,
                                       load_options, name_cache)


def _target_family_names(target_doc, name_cache):
    """Lowercased family names already in a target, collected once."""
    document_key = _doc_key(target_doc)
    names = name_cache.get(document_key)
    if names is None:
        names = set()
        for family in _collect_families(target_doc):
            names.add(_family_name(family).lower())
        name_cache[document_key] = names
    return names


def _copy_link_family_into_targets(link_doc, family, family_name, targets, summary, name_cache):
    """Fallback when a link will not hand over an editable family document.

    ``CopyElements`` reaches into the linked document without opening the
    file, but it is not a family *load* and there is no overwrite anywhere on
    this path: ``DuplicateTypeAction`` offers exactly ``UseDestinationTypes``
    and ``Abort``, so a family already in the target either wins silently or
    the whole paste is cancelled.  A name that is already there is therefore
    refused, with the routes that do work named in the message.
    """
    for target_option in targets:
        target_doc = getattr(target_option, "document", None)
        target_name = _safe_text(getattr(target_option, "display_name", ""))
        if target_doc is None:
            summary.skipped.append(TransferResult(family_name, target_name, "target document is unavailable"))
            continue

        if family_name.lower() in _target_family_names(target_doc, name_cache):
            summary.skipped.append(
                TransferResult(
                    family_name,
                    target_name,
                    "already in the target. Revit offers no overwrite on the copy "
                    "path - open the linked file as a target, or use Load More "
                    "from Recent Project.",
                )
            )
            continue

        transaction = DB.Transaction(target_doc, "Copy family from Revit link")
        try:
            transaction.Start()
        except Exception as ex:
            summary.failed.append(TransferResult(family_name, target_name, "could not start a transaction: {}".format(ex)))
            continue

        try:
            DB.ElementTransformUtils.CopyElements(
                link_doc,
                _id_list([family.Id]),
                target_doc,
                DB.Transform.Identity,
                copy_paste_options(),
            )
            transaction.Commit()
            summary.loaded.append(TransferResult(family_name, target_name, "copied from link"))
            # A copied family lands in the target; it is not overwritten
            # there again, so the cached name set has to know about it.
            _target_family_names(target_doc, name_cache).add(family_name.lower())
        except Exception as ex:
            try:
                transaction.RollBack()
            except Exception:
                pass
            summary.failed.append(TransferResult(family_name, target_name, "copy from link failed: {}".format(ex)))


def _transfer_editable_family(source_doc, family_option, targets, summary, load_options,
                              index_cache, name_cache):
    """One path for project families and link families alike.

    The only difference is which document is asked to open the family, and
    ``resolve_family`` has already worked that out.
    """
    family_name = _safe_text(getattr(family_option, "name", ""))
    is_link = _is_link_family_option(family_option)
    edit_doc = getattr(family_option, "source_document", None) or source_doc
    family = resolve_family(source_doc, family_option, index_cache)

    if not is_transferable_family(family, require_editable=not is_link):
        summary.skipped.append(TransferResult(family_name, "Source", "family is not editable"))
        return

    family_doc = None
    try:
        family_doc = _edit_family(edit_doc, family)
    except Exception as ex:
        if is_link:
            _copy_link_family_into_targets(edit_doc, family, family_name, targets, summary, name_cache)
            return
        summary.failed.append(TransferResult(family_name, "Source", "EditFamily failed: {}".format(ex)))
        return

    try:
        _load_family_document_into_targets(source_doc, family_doc, family_name, targets, summary,
                                           load_options, name_cache)
    finally:
        _close_family_doc(family_doc)


def transfer_families(source_doc, family_options, target_options, progress=None):
    summary = TransferSummary()
    targets = list(target_options or [])
    # Revit's own dialog when it is reachable, so an overwrite is the user's
    # decision and they see it happen; our silent-overwrite answers only where
    # there is no UI to ask through.
    #
    # Fetched ONCE, outside the loop, and that placement is load-bearing: the
    # dialog carries a "Do this for all loading families" checkbox, and if
    # Revit stores that answer on the options object then one instance for the
    # whole batch is the only thing that could let it span more than a single
    # family. Moving this inside the loop would silently bring back a dialog
    # per family. Pinned by test_the_load_options_are_fetched_once_per_batch.
    load_options = native_family_load_options() or FamilyTransferLoadOptions()
    families = list(family_options or [])
    total = len(families)
    index_cache = {}
    name_cache = {}

    for done, family_option in enumerate(families):
        if not _keep_going(progress, done, total):
            summary.cancelled = True
            summary.add_note(
                "Cancelled after {} of {} families. Families already loaded stay "
                "loaded.".format(done, total)
            )
            break

        if _is_open_rfa_family_option(family_option):
            _transfer_open_rfa_family(source_doc, family_option, targets, summary, load_options,
                                      name_cache)
        else:
            _transfer_editable_family(
                source_doc, family_option, targets, summary, load_options,
                index_cache, name_cache,
            )

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


def _export_editable_family(source_doc, family_option, folder_path, used_paths, summary,
                            index_cache):
    family_name = _safe_text(getattr(family_option, "name", ""))
    is_link = _is_link_family_option(family_option)
    edit_doc = getattr(family_option, "source_document", None) or source_doc
    family = resolve_family(source_doc, family_option, index_cache)

    if not is_transferable_family(family, require_editable=not is_link):
        summary.skipped.append(TransferResult(family_name, folder_path, "family is not editable"))
        return

    export_path = build_unique_export_path(folder_path, family_name, used_paths)
    family_doc = None
    try:
        family_doc = _edit_family(edit_doc, family)
        save_options = DB.SaveAsOptions()
        save_options.OverwriteExistingFile = True
        family_doc.SaveAs(export_path, save_options)
        summary.loaded.append(TransferResult(family_name, export_path, "exported"))
    except Exception as ex:
        if is_link:
            # There is no copy-based route to a file: writing an .rfa needs a
            # family document, and that is the thing the link refused.
            summary.skipped.append(
                TransferResult(
                    family_name,
                    export_path,
                    "this link will not hand over a family document, so it cannot be exported",
                )
            )
        else:
            summary.failed.append(TransferResult(family_name, export_path, "Export failed: {}".format(ex)))
    finally:
        _close_family_doc(family_doc)


def export_families(source_doc, family_options, folder_path, progress=None):
    summary = TransferSummary()
    used_paths = set()
    families = list(family_options or [])
    total = len(families)
    index_cache = {}

    for done, family_option in enumerate(families):
        if not _keep_going(progress, done, total):
            summary.cancelled = True
            summary.add_note(
                "Cancelled after {} of {} families. Files already written stay "
                "on disk.".format(done, total)
            )
            break

        if _is_open_rfa_family_option(family_option):
            _export_open_rfa_family(family_option, folder_path, used_paths, summary)
        else:
            _export_editable_family(
                source_doc, family_option, folder_path, used_paths, summary, index_cache,
            )

    return summary


def close_open_family_documents(open_family_documents):
    summary = TransferSummary()
    for document_option in list(open_family_documents or []):
        display_name = _safe_text(getattr(document_option, "display_name", ""))
        document = getattr(document_option, "document", None)
        if document is None:
            summary.skipped.append(TransferResult(display_name, "Opened .rfa files", "document is unavailable"))
            continue

        # A linked document is Revit's, not ours: closing it is forbidden and
        # this list must never be able to reach one.
        try:
            if bool(document.IsLinked):
                summary.skipped.append(
                    TransferResult(display_name, "Opened .rfa files", "linked documents are never closed")
                )
                continue
        except Exception:
            pass

        try:
            document.Close(False)
            summary.closed_rfa_count += 1
        except Exception as ex:
            summary.failed.append(TransferResult(display_name, "Opened .rfa files", "Close failed: {}".format(ex)))

    return summary
