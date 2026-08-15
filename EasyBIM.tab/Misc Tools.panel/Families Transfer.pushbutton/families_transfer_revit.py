# -*- coding: utf-8 -*-
"""Revit-facing logic for the Families Transfer command.

Family selection (collectors, links, model pick, folder picker, family
document open/close) is shared with Families Downgrade and lives in
``easybim.family_selection_revit``; loading, copying and exporting stay here.
"""

# pylint: disable=import-error,invalid-name,broad-except
from pyrevit import DB

from easybim.copy_paste import copy_paste_options
from easybim.family_selection_revit import application_from
from easybim.family_selection_revit import close_family_doc
from easybim.family_selection_revit import collect_families
from easybim.family_selection_revit import doc_key
from easybim.family_selection_revit import doc_title
from easybim.family_selection_revit import edit_family
from easybim.family_selection_revit import family_name_of as _family_name
from easybim.family_selection_revit import is_link_family_option
from easybim.family_selection_revit import is_open_rfa_family_option
from easybim.family_selection_revit import is_project_document
from easybim.family_selection_revit import is_transferable_family
from easybim.family_selection_revit import keep_going
from easybim.family_selection_revit import pick_folder
from easybim.family_selection_revit import resolve_family
from easybim.family_selection_revit import same_document
from easybim.family_selection_state import build_unique_export_path
from easybim.family_selection_state import sort_target_documents

from families_transfer_state import TargetDocumentOption
from families_transfer_state import TransferResult
from families_transfer_state import TransferSummary


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

def get_open_target_documents(uiapp, source_doc, selected_document_keys=None):
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
        if not is_project_document(document):
            continue
        if same_document(document, source_doc):
            continue

        document_key = doc_key(document)
        options.append(
            TargetDocumentOption(
                doc_title(document),
                document_key,
                is_selected=document_key in selected_document_keys,
                document=document,
            )
        )

    return sort_target_documents(options)

OVERWRITE = "overwrite"
OVERWRITE_WITH_VALUES = "overwrite_values"
DECLINE = "decline"


class FamilyTransferLoadOptions(DB.IFamilyLoadOptions):
    """Answers Revit's "this family already exists" question.

    Revit raises this only when the family is both already in the target and
    actually *different*, so a byte-identical family loads with no prompt at
    all - the same as loading it by hand.

    ``ask`` is a callable taking the family name and returning
    ``(answer, apply_to_all)``. Left as ``None`` the answer is a silent
    overwrite including parameter values: that is what a UI-less session gets,
    and what a Revit too old to show the prompt falls back to.
    """

    def __init__(self, ask=None):
        self._ask = ask
        #: Set once the user ticks "do this for all"; suppresses later prompts.
        self._remembered = None
        self.family_name = ""
        self.declined = False
        #: How many times the user was actually asked - the apply-to-all is
        #: only meaningful if this stays at 1 for a batch.
        self.prompts = 0

    def begin(self, family_name):
        """Name the family about to load, and clear the last decline.

        ``OnFamilyFound`` is handed only ``familyInUse`` and an out-parameter;
        there is no family name anywhere in the callback, so the caller has to
        leave one here for the prompt to use.
        """
        self.family_name = _safe_text(family_name)
        self.declined = False

    def _decide(self, family_name):
        if self._remembered is not None:
            return self._remembered
        if self._ask is None:
            return OVERWRITE_WITH_VALUES

        self.prompts += 1
        try:
            answer, apply_to_all = self._ask(family_name)
        except Exception:
            # A prompt that cannot be shown must not abandon the family.
            return OVERWRITE_WITH_VALUES

        if answer not in (OVERWRITE, OVERWRITE_WITH_VALUES, DECLINE):
            answer = OVERWRITE_WITH_VALUES
        # A decline is never remembered: Cancel means "not this one", so the
        # next family that clashes asks again.
        if apply_to_all and answer != DECLINE:
            self._remembered = answer
        return answer

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        del familyInUse
        answer = self._decide(self.family_name)
        if answer == DECLINE:
            self.declined = True
            return False
        try:
            overwriteParameterValues.Value = (answer == OVERWRITE_WITH_VALUES)
        except Exception:
            pass
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        del familyInUse
        # Nested and shared families can name themselves, and they run through
        # the same remembered answer so one tick covers them too.
        name = _safe_text(getattr(sharedFamily, "Name", "")) or self.family_name
        answer = self._decide(name)
        if answer == DECLINE:
            self.declined = True
            return False
        try:
            source.Value = DB.FamilySource.Family
        except Exception:
            pass
        try:
            overwriteParameterValues.Value = (answer == OVERWRITE_WITH_VALUES)
        except Exception:
            pass
        return True


def pick_export_folder():
    return pick_folder("Select a folder for exported family files.")


def _load_family_document_into_targets(source_doc, family_doc, family_name, targets, summary,
                                       load_options, name_cache):
    for target_option in targets:
        target_doc = getattr(target_option, "document", None)
        target_name = _safe_text(getattr(target_option, "display_name", ""))
        if target_doc is None:
            summary.skipped.append(TransferResult(family_name, target_name, "target document is unavailable"))
            continue
        if same_document(source_doc, target_doc):
            summary.skipped.append(TransferResult(family_name, target_name, "target is the source document"))
            continue

        # Asked before the load: it decides whether the result reads "loaded"
        # or "overwritten". Revit's LoadFamily returns only a handle, and the
        # prompt fires only when the family is *changed* as well as present,
        # so the name check is the honest answer to "was it already there".
        target_names = _target_family_names(target_doc, name_cache)
        family_existed = family_name.lower() in target_names

        # The prompt gets no family name of its own - leave one for it.
        load_options.begin(family_name)
        try:
            loaded = family_doc.LoadFamily(target_doc, load_options)
        except Exception as ex:
            # A declined overwrite surfaces as an exception rather than a
            # False return, but we no longer have to infer it from the message:
            # our own options object returned the False, so it knows.
            if load_options.declined:
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
    document_key = doc_key(target_doc)
    names = name_cache.get(document_key)
    if names is None:
        names = set()
        for family in collect_families(target_doc):
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
    is_link = is_link_family_option(family_option)
    edit_doc = getattr(family_option, "source_document", None) or source_doc
    family = resolve_family(source_doc, family_option, index_cache)

    if not is_transferable_family(family, require_editable=not is_link):
        summary.skipped.append(TransferResult(family_name, "Source", "family is not editable"))
        return

    family_doc = None
    try:
        family_doc = edit_family(edit_doc, family)
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
        close_family_doc(family_doc)


def transfer_families(source_doc, family_options, target_options, progress=None,
                      overwrite_prompt=None):
    summary = TransferSummary()
    targets = list(target_options or [])
    # One options object for the whole batch, and that is what makes "do this
    # for all loading families" mean anything: the remembered answer lives on
    # it, so a tick on the first clash covers every later one.
    load_options = FamilyTransferLoadOptions(ask=overwrite_prompt)
    families = list(family_options or [])
    total = len(families)
    index_cache = {}
    name_cache = {}

    for done, family_option in enumerate(families):
        if not keep_going(progress, done, total):
            summary.cancelled = True
            summary.add_note(
                "Cancelled after {} of {} families. Families already loaded stay "
                "loaded.".format(done, total)
            )
            break

        if is_open_rfa_family_option(family_option):
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
    is_link = is_link_family_option(family_option)
    edit_doc = getattr(family_option, "source_document", None) or source_doc
    family = resolve_family(source_doc, family_option, index_cache)

    if not is_transferable_family(family, require_editable=not is_link):
        summary.skipped.append(TransferResult(family_name, folder_path, "family is not editable"))
        return

    export_path = build_unique_export_path(folder_path, family_name, used_paths)
    family_doc = None
    try:
        family_doc = edit_family(edit_doc, family)
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
        close_family_doc(family_doc)


def export_families(source_doc, family_options, folder_path, progress=None):
    summary = TransferSummary()
    used_paths = set()
    families = list(family_options or [])
    total = len(families)
    index_cache = {}

    for done, family_option in enumerate(families):
        if not keep_going(progress, done, total):
            summary.cancelled = True
            summary.add_note(
                "Cancelled after {} of {} families. Files already written stay "
                "on disk.".format(done, total)
            )
            break

        if is_open_rfa_family_option(family_option):
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
