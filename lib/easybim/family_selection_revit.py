# -*- coding: utf-8 -*-
"""Revit-facing family selection - shared by Families Transfer and Families
Downgrade.

Collectors, the element-to-family walk, the Revit-link cascade, the model
pick filter, the folder picker and the family-document open/close helpers.
Nothing here loads, copies or writes a family: that is each command's own.
"""

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

from easybim.family_selection_state import FamilyOption
from easybim.family_selection_state import LinkDocumentOption
from easybim.family_selection_state import OpenFamilyDocumentOption
from easybim.family_selection_state import SOURCE_LINK
from easybim.family_selection_state import SOURCE_OPEN_RFA
from easybim.family_selection_state import is_link_family_key
from easybim.family_selection_state import is_open_family_document_key
from easybim.family_selection_state import make_link_family_key
from easybim.family_selection_state import make_project_family_key
from easybim.family_selection_state import normalize_category_name
from easybim.family_selection_state import sort_family_options
from easybim.family_selection_state import sort_open_family_documents
from easybim.family_selection_state import sort_target_documents


PICK_PROMPT = "Select family instances to include"

get_elementid_value = get_elementid_value_func()


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def eid_key(element_id):
    if not element_id:
        return ""
    try:
        return str(get_elementid_value(element_id))
    except Exception:
        try:
            return str(element_id.IntegerValue)
        except Exception:
            return _safe_text(element_id)


def doc_path(document):
    try:
        return _safe_text(getattr(document, "PathName", "")).strip()
    except Exception:
        return ""


def doc_title(document):
    try:
        path = doc_path(document)
        if path:
            return os.path.basename(path)
    except Exception:
        pass
    return _safe_text(getattr(document, "Title", "")) or "(Untitled Project)"


def category_name_of(category):
    try:
        return normalize_category_name(getattr(category, "Name", ""))
    except Exception:
        return normalize_category_name("")


def family_category_name(family):
    try:
        return category_name_of(getattr(family, "FamilyCategory", None))
    except Exception:
        return normalize_category_name("")


def family_document_category_name(document):
    try:
        return family_category_name(getattr(document, "OwnerFamily", None))
    except Exception:
        return normalize_category_name("")


def doc_key(document):
    path = doc_path(document)
    if path:
        return "path|{}".format(path.lower())

    title = _safe_text(getattr(document, "Title", "")).lower()
    try:
        return "memory|{}|{}".format(title, document.GetHashCode())
    except Exception:
        return "memory|{}".format(title)


def same_document(doc_a, doc_b):
    if doc_a is doc_b:
        return True
    return doc_key(doc_a) == doc_key(doc_b)


def is_project_document(document):
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


def is_family_document(document):
    if document is None:
        return False
    try:
        return bool(document.IsFamilyDocument)
    except Exception:
        return False


def application_from(uiapp, source_doc):
    app = getattr(uiapp, "Application", None)
    if app is not None:
        return app
    try:
        return source_doc.Application
    except Exception:
        return None

def get_open_family_documents(uiapp, source_doc=None, selected_document_keys=None):
    selected_document_keys = set(selected_document_keys or [])
    app = application_from(uiapp, source_doc)
    documents = []
    if app is not None:
        try:
            documents = list(app.Documents)
        except Exception:
            documents = []

    options = []
    for document in documents:
        if not is_family_document(document):
            continue
        if source_doc is not None and same_document(document, source_doc):
            continue

        document_key = doc_key(document)
        options.append(
            OpenFamilyDocumentOption(
                doc_title(document),
                document_key,
                is_selected=document_key in selected_document_keys,
                document=document,
                category_name=family_document_category_name(document),
            )
        )

    return sort_open_family_documents(options)

def is_editable_family(family):
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

    if require_editable and not is_editable_family(family):
        return False

    return True


def family_key_of(family):
    return eid_key(getattr(family, "Id", None))


def family_name_of(family):
    return _safe_text(getattr(family, "Name", "")) or "(Unnamed Family)"


def family_option_for(family, is_selected=False):
    if not is_transferable_family(family):
        return None

    family_key = family_key_of(family)
    if not family_key:
        return None

    return FamilyOption(
        family_name_of(family),
        make_project_family_key(family_key),
        is_selected=is_selected,
        family=family,
        element_id=getattr(family, "Id", None),
        category_name=family_category_name(family),
    )


def family_from_element(element):
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
        family = family_from_element(element)
        option = family_option_for(family, is_selected=True)
        if option is None or option.family_key in seen:
            continue
        seen.add(option.family_key)
        options.append(option)

    return sort_family_options(options)


def get_selected_family_keys_from_selection(doc, uidoc):
    return set(option.family_key for option in get_selected_family_options_from_selection(doc, uidoc))


def collect_families(doc):
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

    for family in collect_families(doc):
        # family_option_for applies the eligibility test itself; testing here as
        # well doubled the CLR property reads over the whole model.
        family_key = family_key_of(family)
        if not family_key:
            continue
        option = family_option_for(
            family,
            is_selected=make_project_family_key(family_key) in selected_family_keys,
        )
        if option is not None:
            options.append(option)

    return sort_family_options(options)


def raw_family_key_from_option(family_option):
    """The bare element id inside a namespaced family key."""
    family_key = _safe_text(getattr(family_option, "family_key", ""))
    if not family_key:
        return ""
    return family_key.rsplit("|", 1)[-1]


def family_index(source_doc, index_cache):
    """``{raw element id: Family}`` for one document, collected once.

    The fallback lookup used to run a whole ``FilteredElementCollector`` pass
    per family option, which is fine while nothing reaches it and quadratic
    the moment something does.
    """
    if index_cache is None:
        index_cache = {}
    document_key = doc_key(source_doc)
    index = index_cache.get(document_key)
    if index is None:
        index = {}
        for family in collect_families(source_doc):
            raw_key = family_key_of(family)
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

    raw_family_key = raw_family_key_from_option(family_option)
    if not raw_family_key:
        return None
    return family_index(source_document, index_cache).get(raw_family_key)


# ---------------------------------------------------------------------------
# Revit links
# ---------------------------------------------------------------------------

def collect_link_instances(doc):
    try:
        return list(
            DB.FilteredElementCollector(doc)
            .OfClass(DB.RevitLinkInstance)
            .WhereElementIsNotElementType()
            .ToElements())
    except Exception:
        return []


def link_document_of(instance):
    try:
        return instance.GetLinkDocument()
    except Exception:
        return None


def link_instance_label(instance):
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

    for instance in collect_link_instances(doc):
        link_doc = link_document_of(instance)

        if link_doc is None:
            label = link_instance_label(instance)
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

        document_key = doc_key(link_doc)
        if document_key in seen:
            continue
        seen.add(document_key)
        options.append(
            LinkDocumentOption(
                doc_title(link_doc),
                document_key,
                is_selected=document_key in selected_document_keys,
                document=link_doc,
            )
        )

    return sort_target_documents(options)


def open_project_documents_by_key(uiapp, source_doc):
    app = application_from(uiapp, source_doc)
    documents = {}
    if app is None:
        return documents
    try:
        open_documents = list(app.Documents)
    except Exception:
        return documents

    for document in open_documents:
        if not is_project_document(document):
            continue
        documents[doc_key(document)] = document
    return documents


# What Families Transfer does about a link that refuses EditFamily. Every
# consumer passes its own sentence; this one is the default because the
# transfer was the first caller.
REFUSED_LINK_HINT = (
    "Families are copied into the target instead, which cannot overwrite one "
    "that is already there."
)


def probe_edit_family(link_doc, refusal_hint=None):
    """Ask the link once whether a family can be taken out of it.

    ``EditFamily`` is refused on a read-only document, and a linked document
    is documented as read-only.  Whether Revit actually refuses is the one
    thing that cannot be settled off Revit, so it is asked here - once per
    link, before the browser opens - rather than discovered per family in the
    middle of a batch.  ``refusal_hint`` is the consumer's own sentence about
    what happens next, appended to the reason.
    """
    if refusal_hint is None:
        refusal_hint = REFUSED_LINK_HINT
    read_only = False
    try:
        read_only = bool(link_doc.IsReadOnly)
    except Exception:
        pass

    candidate = None
    for family in collect_families(link_doc):
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
                "(the linked document is read-only: {}). {}".format(
                    detail, refusal_hint).strip()
            )
        return False, (
            "Revit will not open a family out of this link ({}). {}".format(
                detail, refusal_hint).strip()
        )
    finally:
        close_family_doc(family_doc)

    return True, ""


def prepare_link_documents(uiapp, source_doc, link_options, refusal_hint=None):
    """Probe every checked link once and cache the verdict on its row."""
    open_by_key = open_project_documents_by_key(uiapp, source_doc)

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
        is_extractable, note = probe_edit_family(link_option.document, refusal_hint)
        link_option.is_extractable = is_extractable
        link_option.note = note

    return link_options


def link_family_rows(link_option):
    read_doc = getattr(link_option, "read_document", None) or link_option.document
    if read_doc is None:
        return []

    label = _safe_text(getattr(link_option, "display_name", ""))
    document_key = _safe_text(getattr(link_option, "document_key", ""))
    rows = []

    for family in collect_families(read_doc):
        if not is_transferable_family(family, require_editable=False):
            continue
        raw_family_key = family_key_of(family)
        if not raw_family_key:
            continue
        rows.append(
            FamilyOption(
                family_name_of(family),
                make_link_family_key(document_key, raw_family_key),
                family=family,
                element_id=getattr(family, "Id", None),
                source_kind=SOURCE_LINK,
                source_document=read_doc,
                source_label=label,
                document_key=document_key,
                category_name=family_category_name(family),
                is_editable=is_editable_family(family),
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
            rows = link_family_rows(link_option)
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

class FamilySelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        family = family_from_element(elem)
        return is_transferable_family(family)

    def AllowReference(self, reference, position):
        del reference, position
        return False

def pick_more_family_options(uidoc, prompt=None):
    picked_options = []
    seen = set()
    references = uidoc.Selection.PickObjects(
        ObjectType.Element,
        FamilySelectionFilter(),
        prompt or PICK_PROMPT,
    )

    doc = uidoc.Document
    for reference in references or []:
        try:
            element = doc.GetElement(reference.ElementId)
        except Exception:
            element = None
        family = family_from_element(element)
        option = family_option_for(family, is_selected=True)
        if option is None or option.family_key in seen:
            continue
        seen.add(option.family_key)
        picked_options.append(option)

    return sort_family_options(picked_options)


def pick_more_family_keys(uidoc, prompt=None):
    return set(option.family_key for option in pick_more_family_options(uidoc, prompt))


def pick_folder(description, allow_new_folder=True):
    """WinForms folder picker; ``None`` when the user cancels."""
    dialog = FolderBrowserDialog()
    dialog.Description = _safe_text(description)
    dialog.ShowNewFolderButton = bool(allow_new_folder)
    if dialog.ShowDialog() == DialogResult.OK:
        return _safe_text(dialog.SelectedPath)
    return None


def edit_family(source_doc, family):
    return source_doc.EditFamily(family)


def close_family_doc(family_doc):
    if family_doc is None:
        return
    try:
        family_doc.Close(False)
    except Exception:
        pass


def is_open_rfa_family_option(family_option):
    family_key = _safe_text(getattr(family_option, "family_key", ""))
    return (
        getattr(family_option, "source_kind", None) == SOURCE_OPEN_RFA
        or is_open_family_document_key(family_key)
    )


def is_link_family_option(family_option):
    family_key = _safe_text(getattr(family_option, "family_key", ""))
    return (
        getattr(family_option, "source_kind", None) == SOURCE_LINK
        or is_link_family_key(family_key)
    )


def keep_going(progress, done, total):
    """``False`` once the user has cancelled the progress bar."""
    if progress is None:
        return True
    try:
        return bool(progress(done, total))
    except Exception:
        return True
