# -*- coding: utf-8 -*-
"""Families out of `.rvt` files that are not open in Revit.

This module is separate from ``families_transfer_revit`` on purpose. That one
carries a contract test asserting ``OpenDocumentFile`` never appears in it -
the Revit Links source promises the linked file is never opened, and that
promise must stay exact. This source legitimately opens files, so it lives
here with its own contract tests.

Three things about it are not obvious and are load-bearing.

**"Without opening" means without a tab.**  There is no API that reads a
closed Revit file: ``BasicFileInfo`` returns header metadata and no content,
and ``TransmissionData`` returns external references, which loaded families
are not. ``Application.OpenDocumentFile`` is documented as *"opens the
document into memory but does not make it visible to the user in any way"* -
no tab, no view, no change of active document. The file really is fully
loaded, parsed and, if older, upgraded in memory. Only the visibility differs.

**``Document.Close()`` saves.**  Its own summary: *"Closes the document, save
the changes if there are."* Opening an older file upgrades it, which counts as
changes, so closing without the argument would silently overwrite the user's
2019 file with a current-version one. Every close here passes ``False``, and a
contract test fails the build if a bare ``Close()`` ever appears.

**A newer file is a hard stop.**  No API can read a file saved in a later
Revit than the one running. It is refused up front from the header rather than
failing halfway through a batch.
"""

# pylint: disable=import-error,invalid-name,broad-except
import os
import re

import clr

clr.AddReference("System.Windows.Forms")

from System.Windows.Forms import DialogResult
from System.Windows.Forms import OpenFileDialog

from pyrevit import DB

from families_transfer_state import FamilyOption
from families_transfer_state import ProjectFileOption
from families_transfer_state import SOURCE_FILE
from families_transfer_state import make_file_family_key
from families_transfer_state import sort_family_options
from families_transfer_state import sort_target_documents

from families_transfer_revit import family_category_name
from families_transfer_revit import family_display_name
from families_transfer_revit import family_element_key
from families_transfer_revit import is_editable_family
from families_transfer_revit import is_transferable_family


VERSION_DIGITS = re.compile(r"(\d{4})")


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _version_number(text):
    match = VERSION_DIGITS.search(_safe_text(text))
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


def host_version_text(app):
    return _safe_text(getattr(app, "VersionNumber", ""))


def _file_key(path):
    return "path|{}".format(_safe_text(path).lower())


# ---------------------------------------------------------------------------
# picking files
# ---------------------------------------------------------------------------

def pick_project_files():
    """A plain Windows file dialog - no Revit API context needed."""
    dialog = OpenFileDialog()
    dialog.Title = "Select Revit project files to read families from"
    dialog.Filter = "Revit projects (*.rvt)|*.rvt"
    dialog.Multiselect = True
    if dialog.ShowDialog() == DialogResult.OK:
        return [_safe_text(path) for path in dialog.FileNames]
    return []


# ---------------------------------------------------------------------------
# header pre-flight, before anything is opened
# ---------------------------------------------------------------------------

def _read_header(path):
    """``(file_version, is_readable, note)`` from the header alone.

    ``BasicFileInfo.Extract`` throws for a file whose header structure changed
    in a newer release *and* for one saved before headers existed, so a throw
    means "cannot process" rather than "too new" - the message says so.
    """
    try:
        info = DB.BasicFileInfo.Extract(path)
    except Exception:
        return "", False, "the file header could not be read"

    if info is None:
        return "", False, "the file header could not be read"

    try:
        if bool(info.IsSavedInLaterVersion):
            return "", False, "saved in a newer Revit and cannot be read"
    except Exception:
        pass

    # SavedInVersion was removed in Revit 2020; Format replaced it.
    file_version = _safe_text(getattr(info, "Format", "")) or _safe_text(
        getattr(info, "SavedInVersion", ""))
    return file_version, True, ""


def inspect_project_files(paths, existing_options=None):
    """Turn paths into rows, refusing what cannot be opened, without opening.

    Called before any document is touched, so a whole batch is vetted while
    Revit is still responsive.
    """
    options = list(existing_options or [])
    seen = set(option.document_key for option in options)

    for path in paths or []:
        path = _safe_text(path)
        if not path:
            continue
        document_key = _file_key(path)
        if document_key in seen:
            continue
        seen.add(document_key)

        if not os.path.isfile(path):
            file_version, is_readable, note = "", False, "the file is missing"
        else:
            file_version, is_readable, note = _read_header(path)

        options.append(
            ProjectFileOption(
                os.path.basename(path) or path,
                document_key,
                path=path,
                is_selected=bool(is_readable),
                file_version=file_version,
                is_readable=is_readable,
                note=note,
            )
        )

    return sort_target_documents(options)


# ---------------------------------------------------------------------------
# opening, reading, closing
# ---------------------------------------------------------------------------

def _swallow_open_warnings(sender, args):
    """Delete warnings raised while a file is opened and upgraded.

    Opening an older model raises real failures - FamilyCannotBeUpgraded,
    UpgradedMassingWarning and friends. Left alone each becomes a modal
    dialog, which stops a batch dead waiting for a click, and is the most
    likely way this source would ship broken.

    An open is not a transaction, so ``IFailuresPreprocessor`` never sees
    these; ``Application.FailuresProcessing`` is the one that does. Only
    warnings are deleted - errors still surface.
    """
    del sender
    try:
        accessor = args.GetFailuresAccessor()
    except Exception:
        return
    try:
        for failure in list(accessor.GetFailureMessages()):
            if failure.GetSeverity() == DB.FailureSeverity.Warning:
                accessor.DeleteWarning(failure)
    except Exception:
        pass
    try:
        args.SetProcessingResult(DB.FailureProcessingResult.Continue)
    except Exception:
        pass


def _attach_failure_handler(app):
    """Returns the handler to detach, or None if it could not be attached."""
    if app is None:
        return None
    try:
        app.FailuresProcessing += _swallow_open_warnings
        return _swallow_open_warnings
    except Exception:
        return None


def _detach_failure_handler(app, handler):
    if app is None or handler is None:
        return
    try:
        app.FailuresProcessing -= handler
    except Exception:
        pass


def _open_options():
    options = DB.OpenOptions()
    try:
        # The single biggest saving: without it Revit drags in the whole
        # federated model behind a project that happens to carry links.
        options.DoNotLoadLinks = True
    except Exception:
        pass
    try:
        # Severs the central link, so no ownership warnings and no chance of
        # touching somebody's local. Nothing here ever writes.
        options.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndDiscardWorksets
    except Exception:
        pass
    # Audit stays at its default of False - it is documented to increase both
    # the time and the memory an open costs.
    #
    # WorksetConfigurationOption.CloseAllWorksets is deliberately NOT set.
    # It looks like free speed, but a closed workset's elements are not
    # reliably enumerable, so families would go silently missing from the
    # scan - a wrong answer is worse than a slow one.
    return options


def _model_path(path):
    try:
        return DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    except Exception:
        return None


def open_project_file(app, file_option):
    """Open one file into memory. Returns ``(document, note)``."""
    if app is None:
        return None, "no Revit application to open with"
    if file_option.is_readable is False:
        return None, _safe_text(file_option.note) or "cannot be read"
    if file_option.document is not None:
        return file_option.document, ""

    model_path = _model_path(file_option.path)
    try:
        if model_path is not None:
            document = app.OpenDocumentFile(model_path, _open_options())
        else:
            document = app.OpenDocumentFile(file_option.path)
    except Exception as ex:
        detail = _safe_text(ex).strip().splitlines()
        return None, detail[0] if detail else "the file could not be opened"

    file_option.document = document
    return document, ""


def close_project_documents(file_options):
    """Close every document this source opened, without saving.

    ``Close(False)`` is not a style choice: the bare ``Close()`` saves, and an
    older file upgraded on open counts as modified, so a bare close would
    write the user's file back in the running Revit's format.
    """
    closed = 0
    for file_option in list(file_options or []):
        document = getattr(file_option, "document", None)
        if document is None:
            continue
        try:
            if document.Close(False):
                closed += 1
        except Exception:
            pass
        file_option.document = None
    return closed


def _purge(app):
    """Revit cannot finalize API objects from the garbage collector's thread."""
    try:
        app.PurgeReleasedAPIObjects()
    except Exception:
        pass


def _file_family_rows(file_option):
    document = getattr(file_option, "document", None)
    if document is None:
        return []

    label = _safe_text(getattr(file_option, "display_name", ""))
    document_key = _safe_text(getattr(file_option, "document_key", ""))
    rows = []

    try:
        families = list(
            DB.FilteredElementCollector(document).OfClass(DB.Family).ToElements())
    except Exception:
        families = []

    for family in families:
        if not is_transferable_family(family, require_editable=False):
            continue
        raw_family_key = family_element_key(family)
        if not raw_family_key:
            continue
        rows.append(
            FamilyOption(
                family_display_name(family),
                make_file_family_key(document_key, raw_family_key),
                family=family,
                element_id=getattr(family, "Id", None),
                source_kind=SOURCE_FILE,
                source_document=document,
                source_label=label,
                document_key=document_key,
                category_name=family_category_name(family),
                is_editable=is_editable_family(family),
            )
        )
    return rows


def scan_project_files(app, file_options, selected_family_keys=None, progress=None):
    """Open each checked file, collect its families, and leave it open.

    One file per call to ``progress`` so a cancel lands between files - an
    ``OpenDocumentFile`` is atomic and cannot be interrupted once started.

    The documents stay open because ``EditFamily`` needs the document that
    owns the family; closing here would invalidate every handle collected.
    ``close_project_documents`` is the matching call.
    """
    selected_family_keys = set(selected_family_keys or [])
    wanted = [option for option in list(file_options or [])
              if bool(getattr(option, "is_selected", False))
              and getattr(option, "is_readable", None) is not False]
    total = len(wanted)
    rows = []
    notes = []
    cancelled = False

    handler = _attach_failure_handler(app)
    try:
        for done, file_option in enumerate(wanted):
            if progress is not None:
                try:
                    if not progress(done, total):
                        cancelled = True
                        break
                except Exception:
                    pass

            document, note = open_project_file(app, file_option)
            if document is None:
                file_option.is_readable = False
                file_option.note = note
                notes.append("{}: {}".format(file_option.display_name, note))
                continue

            rows.extend(_file_family_rows(file_option))
            _purge(app)
    finally:
        _detach_failure_handler(app, handler)

    for row in rows:
        row.is_selected = row.family_key in selected_family_keys

    return sort_family_options(rows), notes, cancelled


def file_notes(file_options):
    """One sentence per file that could not be read."""
    notes = []
    for file_option in list(file_options or []):
        if getattr(file_option, "is_readable", None) is not False:
            continue
        note = _safe_text(getattr(file_option, "note", ""))
        if note:
            notes.append("{}: {}".format(file_option.display_name, note))
    return notes
