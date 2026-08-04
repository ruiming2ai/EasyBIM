# -*- coding: utf-8 -*-
"""Revit-facing logic for Load Parameters.

Everything that touches the Revit API lives here; the planning, filtering and
report text live in ``load_parameters_state``.

The one piece worth reading before changing anything is
:class:`TempSharedParameterFile`.  ``FamilyManager.AddParameter`` and
``BindingMap.Insert`` both need an ``ExternalDefinition``, and an
``ExternalDefinition`` only exists inside a ``DefinitionFile`` - a shared
parameter read out of a project has none.  So a run builds one temporary
shared parameter file holding every selected parameter, whatever its source,
and holds it current for the whole run.
"""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

import os
import re

import clr

clr.AddReference("RevitAPI")
clr.AddReference("System.Windows.Forms")

import System
from System.Collections.Generic import List as ClrList
from System.Windows.Forms import DialogResult
from System.Windows.Forms import FolderBrowserDialog

from pyrevit import DB
from pyrevit import script

from easybim.compat import exception_text
from easybim.compat import safe_text

import load_parameters_state as state


LOGGER = script.get_logger()

TEMP_FILE_ID = "EasyBIM_LoadParameters"

VERSION_DIGITS = re.compile(r"(\d{4})")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _eid_value(element_id):
    """ElementId -> int across Revit versions (2026 removed IntegerValue)."""
    if element_id is None:
        return None
    for attr in ("IntegerValue", "Value"):
        try:
            return int(getattr(element_id, attr))
        except Exception:
            continue
    return None


def _normalized_path(path):
    """One spelling for a file path, so the already-open check can match."""
    text = safe_text(path).strip()
    if not text:
        return u""
    try:
        return os.path.normcase(os.path.abspath(text))
    except Exception:
        return text.lower()


def _version_number(text):
    match = VERSION_DIGITS.search(safe_text(text))
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


class _WarningSwallower(DB.IFailuresPreprocessor):
    """Delete warnings raised inside the family document.

    Adding a parameter routinely raises benign warnings, and a modal dialog per
    family would make a hundred-family batch unusable.  Errors are left alone -
    they still roll the transaction back.
    """

    def PreprocessFailures(self, failuresAccessor):
        try:
            for failure in failuresAccessor.GetFailureMessages():
                if failure.GetSeverity() == DB.FailureSeverity.Warning:
                    failuresAccessor.DeleteWarning(failure)
        except Exception:
            pass
        return DB.FailureProcessingResult.Continue


def _quiet_transaction(document, name):
    """A transaction that does not stop the batch for warnings."""
    transaction = DB.Transaction(document, name)
    try:
        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(_WarningSwallower())
        options.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(options)
    except Exception:
        pass
    return transaction


# ---------------------------------------------------------------------------
# entry guards
# ---------------------------------------------------------------------------


def document_blocker(doc):
    """Why this document cannot be used, or None.

    ``IsModifiable`` catches sketch and group edit mode, where ``EditFamily``
    throws rather than returning anything useful.
    """
    if doc is None:
        return u"There is no active document."
    if getattr(doc, "IsFamilyDocument", False):
        return u"Load Parameters runs in a project, not in the family editor."
    if getattr(doc, "IsLinked", False):
        return u"The active document is a link."
    if getattr(doc, "IsReadOnly", False):
        return u"The active document is read-only."
    try:
        if doc.IsModifiable:
            return (u"Finish the sketch or group you are editing before "
                    u"running Load Parameters.")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# parameter groups (the "Group" column)
# ---------------------------------------------------------------------------


def group_choices():
    """``[(group_id_text, label)]`` for every built-in parameter group.

    Enumerated at runtime rather than hardcoded: a fixed list is wrong in a
    non-English Revit and drifts with every release.
    """
    choices = []
    try:
        groups = DB.ParameterUtils.GetAllBuiltInGroups()
    except Exception as error:
        LOGGER.debug("GetAllBuiltInGroups failed: %s", exception_text(error))
        return choices

    for group_id in groups:
        type_id = safe_text(getattr(group_id, "TypeId", u""))
        if not type_id:
            continue
        try:
            label = DB.LabelUtils.GetLabelForGroup(group_id)
        except Exception:
            label = type_id.split(":")[-1] or type_id
        choices.append((type_id, safe_text(label)))

    choices.sort(key=lambda item: item[1].lower())
    return choices


def default_group_id():
    try:
        return safe_text(DB.GroupTypeId.Data.TypeId)
    except Exception:
        return u""


def forge_type_id(type_id_text):
    text = safe_text(type_id_text)
    if not text:
        return None
    try:
        return DB.ForgeTypeId(text)
    except Exception:
        return None


def _spec_label(spec_id):
    try:
        return safe_text(DB.LabelUtils.GetLabelForSpec(spec_id))
    except Exception:
        type_id = safe_text(getattr(spec_id, "TypeId", u""))
        return type_id.split(":")[-1] or type_id


def _family_type_spec_id():
    """The one spec this tool cannot recreate, or None on an older API."""
    reference = getattr(DB.SpecTypeId, "Reference", None)
    if reference is None:
        return u""
    try:
        return safe_text(reference.FamilyType.TypeId)
    except Exception:
        return u""


UNSUPPORTED_SPEC_REASON = (
    u"A Family Type parameter also stores a category, which the API cannot "
    u"put into a shared parameter definition"
)


def _spec_support(spec_id_text):
    text = safe_text(spec_id_text)
    if not text:
        # Without a spec there is nothing to build an ExternalDefinition from,
        # and letting it through would fail the whole run rather than one row.
        return False, u"Revit did not report a data type for this parameter"
    unsupported = _family_type_spec_id()
    if unsupported and text == unsupported:
        return False, UNSUPPORTED_SPEC_REASON
    return True, u""


# ---------------------------------------------------------------------------
# reading shared parameters out of the project
# ---------------------------------------------------------------------------


def _binding_kind(doc, definition):
    """``(is_bound, is_instance, [category names])`` for a definition."""
    try:
        binding = doc.ParameterBindings.get_Item(definition)
    except Exception:
        binding = None
    if binding is None:
        return False, False, []

    is_instance = isinstance(binding, DB.InstanceBinding)
    names = []
    try:
        for category in binding.Categories:
            names.append(safe_text(getattr(category, "Name", u"")))
    except Exception:
        pass
    return True, is_instance, names


def collect_project_parameters(doc):
    """Every shared parameter present in the model.

    ``SharedParameterElement`` covers both the ones bound as project parameters
    and the ones that arrived inside a loaded family, which is what makes this
    the honest answer to "what can I transfer".
    """
    rows = []
    try:
        elements = list(DB.FilteredElementCollector(doc)
                        .OfClass(DB.SharedParameterElement)
                        .ToElements())
    except Exception as error:
        LOGGER.debug("SharedParameterElement scan failed: %s",
                     exception_text(error))
        return rows

    for element in elements:
        try:
            definition = element.GetDefinition()
        except Exception:
            definition = None
        if definition is None:
            continue

        try:
            spec_id = definition.GetDataType()
        except Exception:
            spec_id = None
        spec_text = safe_text(getattr(spec_id, "TypeId", u""))

        group_text = u""
        try:
            group_text = safe_text(definition.GetGroupTypeId().TypeId)
        except Exception:
            group_text = u""

        _, is_instance, _ = _binding_kind(doc, definition)
        supported, reason = _spec_support(spec_text)

        rows.append(state.ParameterRow(
            name=safe_text(getattr(definition, "Name", u"")),
            guid=safe_text(getattr(element, "GuidValue", u"")),
            spec_id=spec_text,
            spec_label=_spec_label(spec_id) if spec_id is not None else u"",
            source=state.SOURCE_PROJECT,
            source_label=u"Project",
            group_id=group_text or default_group_id(),
            group_label=u"",
            is_instance=is_instance,
            supported=supported,
            unsupported_reason=reason,
        ))

    return state.sort_parameter_rows(rows)


def project_guids_by_name(doc):
    """``{lowercase name: guid}`` - the cross-check for a .txt pick.

    A picked definition whose name matches a project parameter with a different
    GUID would leave two identically-named shared parameters in the model, and
    schedules would silently pick whichever they liked.
    """
    mapping = {}
    try:
        elements = list(DB.FilteredElementCollector(doc)
                        .OfClass(DB.SharedParameterElement)
                        .ToElements())
    except Exception:
        return mapping
    for element in elements:
        try:
            name = safe_text(element.GetDefinition().Name).lower()
            mapping[name] = safe_text(element.GuidValue)
        except Exception:
            continue
    return mapping


# ---------------------------------------------------------------------------
# reading a user's shared parameter .txt
# ---------------------------------------------------------------------------


def read_shared_parameter_file(app, path):
    """Snapshot a .txt into plain rows, grouped as the file groups them.

    Returns ``(groups, error)`` where groups is ``[(group_name, [row])]``.
    The app-wide ``SharedParametersFilename`` is restored before returning,
    and no ``ExternalDefinition`` outlives this call - they are handles onto
    an open ``DefinitionFile`` and must not be cached.
    """
    original = safe_text(getattr(app, "SharedParametersFilename", u"")) or u""
    file_label = os.path.basename(safe_text(path))
    groups = []
    try:
        app.SharedParametersFilename = safe_text(path)
        definition_file = app.OpenSharedParameterFile()
        if definition_file is None:
            # Revit returns None rather than raising for a missing, empty or
            # unparseable file, so this is the only place it can be caught.
            return [], (u"Revit could not read that shared parameter file. "
                        u"Check it is a shared parameter file and not empty.")

        for group in definition_file.Groups:
            rows = []
            group_name = safe_text(getattr(group, "Name", u""))
            for definition in group.Definitions:
                row = _row_from_external_definition(definition, group_name,
                                                    file_label)
                if row is not None:
                    rows.append(row)
            if rows:
                groups.append((group_name, state.sort_parameter_rows(rows)))
    except Exception as error:
        return [], exception_text(error)
    finally:
        _restore_shared_parameters_filename(app, original)

    groups.sort(key=lambda item: item[0].lower())
    return groups, None


def _row_from_external_definition(definition, group_name, file_label):
    try:
        spec_id = definition.GetDataType()
    except Exception:
        spec_id = None
    spec_text = safe_text(getattr(spec_id, "TypeId", u""))
    supported, reason = _spec_support(spec_text)

    try:
        guid = safe_text(definition.GUID)
    except Exception:
        return None

    return state.ParameterRow(
        name=safe_text(getattr(definition, "Name", u"")),
        guid=guid,
        spec_id=spec_text,
        spec_label=_spec_label(spec_id) if spec_id is not None else u"",
        source=state.SOURCE_FILE,
        source_label=u"{0} > {1}".format(file_label, group_name),
        definition_group=group_name,
        description=safe_text(getattr(definition, "Description", u"")),
        visible=bool(getattr(definition, "Visible", True)),
        user_modifiable=bool(getattr(definition, "UserModifiable", True)),
        hide_when_no_value=bool(getattr(definition, "HideWhenNoValue", False)),
        group_id=default_group_id(),
        supported=supported,
        unsupported_reason=reason,
    )


def _restore_shared_parameters_filename(app, original):
    """Put the app-wide setting back.

    Not optional: it persists into Revit's .ini across sessions, so leaving it
    pointed at a deleted temp file would follow the user around forever.
    Assigning None from IronPython throws, hence the empty-string default.
    """
    try:
        app.SharedParametersFilename = original or u""
    except Exception as error:
        LOGGER.debug("Could not restore SharedParametersFilename: %s",
                     exception_text(error))


# ---------------------------------------------------------------------------
# the temporary shared parameter file
# ---------------------------------------------------------------------------


class TempSharedParameterFile(object):
    """Materialise every selected parameter as an ``ExternalDefinition``.

    Used as a context manager wrapping the *whole* run, not just the
    AddParameter calls: an ExternalDefinition is a live handle onto an open
    DefinitionFile, and LoadFamily and the binding insert may both re-read it.

    Only the file header is written by hand; every PARAM line is written by
    Revit through ``Definitions.Create``.  That avoids hand-mapping a
    ForgeTypeId onto the legacy DATATYPE tokens, and keeps our own bytes pure
    ASCII so text encoding stays Revit's problem.
    """

    def __init__(self, app, rows):
        self.app = app
        self.rows = list(rows or [])
        self.path = u""
        self.error = None
        self.definitions = {}
        self._original = u""

    def __enter__(self):
        self._original = safe_text(
            getattr(self.app, "SharedParametersFilename", u"")) or u""
        try:
            self._build()
        except Exception as error:
            # Never raise out of __enter__: the caller checks ``error`` and
            # __exit__ still runs the restore either way.
            self.error = exception_text(error)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._cleanup()
        return False

    def _build(self):
        self.path = script.get_universal_data_file(TEMP_FILE_ID, "txt")
        # Binary mode, because the header is deliberately pure ASCII bytes and
        # nothing here should go near a platform text encoding.
        with open(self.path, "wb") as handle:
            handle.write(state.SHARED_PARAMETER_HEADER.encode("ascii"))

        self.app.SharedParametersFilename = self.path
        definition_file = self.app.OpenSharedParameterFile()
        if definition_file is None:
            self.error = (
                u"Revit could not open the temporary shared parameter file "
                u"at {0}.".format(self.path))
            return

        groups = {}
        for row in self.rows:
            try:
                group_name = state.definition_group_name(row)
                group = groups.get(group_name)
                if group is None:
                    group = definition_file.Groups.Create(group_name)
                    groups[group_name] = group

                options = DB.ExternalDefinitionCreationOptions(
                    row.name, DB.ForgeTypeId(row.spec_id))
                # The GUID is the whole point: it is what makes the parameter
                # in the family the same one the project already knows.
                options.GUID = System.Guid(row.guid)
                options.Description = row.description
                options.Visible = row.visible
                options.UserModifiable = row.user_modifiable
                options.HideWhenNoValue = row.hide_when_no_value
                self.definitions[row.key] = group.Definitions.Create(options)
            except Exception as error:
                # One definition Revit will not create must not cost the run.
                # The row simply has no definition, and every writer already
                # reports that per target.
                LOGGER.debug("Could not create definition %s: %s", row.name,
                             exception_text(error))

    def _cleanup(self):
        # Restore first, delete second - a temp file Revit still points at is a
        # much softer failure than a pointer to a file that is gone.
        _restore_shared_parameters_filename(self.app, self._original)
        if not self.path:
            return
        try:
            if os.path.isfile(self.path):
                os.remove(self.path)
        except Exception as error:
            LOGGER.debug("Could not remove %s: %s", self.path,
                         exception_text(error))

    def get(self, row):
        return self.definitions.get(row.key)


# ---------------------------------------------------------------------------
# families
# ---------------------------------------------------------------------------


def is_editable_family(family):
    """Loadable, not in-place - the same test Families Transfer applies."""
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


def open_family_titles(app):
    """Lowercase names of family documents already open in this session.

    ``EditFamily`` on one of these hands back the document the user is editing,
    and ``Close(False)`` would end their edit session.
    """
    titles = set()
    paths = set()
    try:
        documents = list(app.Documents)
    except Exception:
        return titles, paths
    for document in documents:
        try:
            if not bool(document.IsFamilyDocument):
                continue
        except Exception:
            continue
        try:
            title = safe_text(document.Title)
            if title.lower().endswith(u".rfa"):
                title = title[:-4]
            titles.add(title.lower())
        except Exception:
            pass
        path = _normalized_path(getattr(document, "PathName", u""))
        if path:
            paths.add(path)
    return titles, paths


def collect_project_families(doc, app):
    """Every editable family in the project, as rows."""
    rows = []
    open_titles, _ = open_family_titles(app)
    try:
        families = list(DB.FilteredElementCollector(doc)
                        .OfClass(DB.Family)
                        .ToElements())
    except Exception as error:
        LOGGER.debug("Family scan failed: %s", exception_text(error))
        return rows

    for family in families:
        if not is_editable_family(family):
            continue
        name = safe_text(getattr(family, "Name", u"")) or u"(Unnamed Family)"
        category = u""
        try:
            category = safe_text(family.FamilyCategory.Name)
        except Exception:
            category = u""
        skip_reason = u""
        if name.lower() in open_titles:
            skip_reason = u"already open in this Revit session"
        rows.append(state.FamilyRow(
            name=name,
            source=state.SOURCE_PROJECT,
            element_id=_eid_value(getattr(family, "Id", None)),
            category=category,
            editable=not skip_reason,
            skip_reason=skip_reason,
        ))

    return state.sort_family_rows(rows)


def checkout_families(doc, family_rows):
    """Claim the family elements up front on a workshared model.

    Without this a mid-loop ownership failure surfaces as a modal
    request-permission dialog, once per family.  Must run outside a
    transaction.
    """
    if not getattr(doc, "IsWorkshared", False):
        return
    ids = ClrList[DB.ElementId]()
    lookup = {}
    for row in family_rows:
        if row.source != state.SOURCE_PROJECT or row.element_id is None:
            continue
        element_id = _element_id(row.element_id)
        if element_id is None:
            continue
        ids.Add(element_id)
        lookup[row.key] = element_id
    if ids.Count == 0:
        return

    try:
        DB.WorksharingUtils.CheckoutElements(doc, ids)
    except Exception as error:
        LOGGER.debug("CheckoutElements failed: %s", exception_text(error))

    for row in family_rows:
        element_id = lookup.get(row.key)
        if element_id is None:
            continue
        try:
            status = DB.WorksharingUtils.GetCheckoutStatus(doc, element_id)
        except Exception:
            continue
        if status == DB.CheckoutStatus.OwnedByOtherUser:
            row.editable = False
            row.skip_reason = u"another user has this family checked out"


def _element_id(value):
    if value is None:
        return None
    try:
        return DB.ElementId(int(value))
    except Exception:
        pass
    try:
        return DB.ElementId(System.Int64(int(value)))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# scanning a folder of .rfa files
# ---------------------------------------------------------------------------


def pick_folder(description):
    dialog = FolderBrowserDialog()
    dialog.Description = safe_text(description)
    dialog.ShowNewFolderButton = False
    if dialog.ShowDialog() == DialogResult.OK:
        return safe_text(dialog.SelectedPath)
    return None


def scan_family_folder(app, folder, recursive=True):
    """List .rfa files as rows, without opening a single one.

    ``BasicFileInfo.Extract`` reads the header only, which is what makes it
    affordable to check the authoring version of a whole library - and that
    check is the difference between a batch that quietly upgrades someone's
    family library and one that warns first.
    """
    rows = []
    host_version = _version_number(getattr(app, "VersionNumber", u""))
    open_titles, open_paths = open_family_titles(app)

    root = safe_text(folder)
    for current, _dirs, files in os.walk(root):
        for _, name in state.family_files_from_names(current, files):
            path = os.path.join(current, name)
            row = _folder_family_row(path, name, host_version,
                                     open_titles, open_paths, root)
            if row is not None:
                rows.append(row)
        if not recursive:
            break

    return state.sort_family_rows(rows)


def _folder_family_row(path, file_name, host_version, open_titles, open_paths,
                       root):
    stem = file_name[:-4]
    saved_in = u""
    try:
        info = DB.BasicFileInfo.Extract(path)
        if info is not None:
            if bool(getattr(info, "IsWorkshared", False)):
                return None
            saved_version = safe_text(getattr(info, "SavedInVersion", u""))
            if host_version and _version_number(saved_version) < host_version:
                saved_in = saved_version
    except Exception:
        # Not a readable Revit file - leave it in the list and let the run
        # report the real failure rather than guessing here.
        saved_in = u""

    skip_reason = u""
    if _normalized_path(path) in open_paths or stem.lower() in open_titles:
        skip_reason = u"already open in this Revit session"

    read_only = False
    try:
        read_only = not os.access(path, os.W_OK)
    except Exception:
        read_only = False

    return state.FamilyRow(
        name=stem,
        source=state.SOURCE_FOLDER,
        path=path,
        editable=not skip_reason,
        skip_reason=skip_reason,
        saved_in_version=saved_in,
        is_read_only=read_only,
        root=root,
    )


# ---------------------------------------------------------------------------
# categories
# ---------------------------------------------------------------------------


def collect_categories(doc):
    """Categories a parameter can actually be bound to."""
    rows = []
    try:
        categories = list(doc.Settings.Categories)
    except Exception as error:
        LOGGER.debug("Category scan failed: %s", exception_text(error))
        return rows

    for category in categories:
        try:
            if not bool(category.AllowsBoundParameters):
                continue
        except Exception:
            continue
        name = safe_text(getattr(category, "Name", u""))
        if not name:
            continue
        rows.append(state.CategoryRow(
            name=name,
            category_id=_eid_value(getattr(category, "Id", None)),
            discipline=safe_text(getattr(category, "CategoryType", u"")),
        ))

    rows.sort(key=lambda row: row.name.lower())
    return rows


def category_by_id(doc, category_id):
    try:
        for category in doc.Settings.Categories:
            if _eid_value(category.Id) == category_id:
                return category
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# writing into families
# ---------------------------------------------------------------------------


class LoadOptions(DB.IFamilyLoadOptions):
    """Overwrite the family, but never its parameter *values*.

    Deliberately not shared with Families Transfer, which sets
    ``overwriteParameterValues = True``.  That is right for a transfer and
    wrong here: adding a parameter must leave the project's existing type
    values exactly as they were.
    """

    def __init__(self):
        self.loaded_existing = False

    def reset(self):
        self.loaded_existing = False

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        del familyInUse
        self.loaded_existing = True
        try:
            overwriteParameterValues.Value = False
        except Exception:
            pass
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source,
                            overwriteParameterValues):
        del sharedFamily, familyInUse
        self.loaded_existing = True
        try:
            source.Value = DB.FamilySource.Family
        except Exception:
            pass
        try:
            overwriteParameterValues.Value = False
        except Exception:
            pass
        return True


def existing_parameter_names(family_doc):
    """``(names, guids)`` already on a family, both lowercase.

    ``FamilyParameter.GUID`` throws on a non-shared parameter, so the GUID read
    is gated on ``IsShared`` - without that gate the first plain family
    parameter kills the whole scan.
    """
    names = set()
    guids = set()
    try:
        manager = family_doc.FamilyManager
    except Exception:
        return names, guids
    if manager is None:
        return names, guids

    try:
        parameters = list(manager.Parameters)
    except Exception:
        return names, guids

    for parameter in parameters:
        try:
            names.add(safe_text(parameter.Definition.Name).lower())
        except Exception:
            pass
        try:
            if bool(parameter.IsShared):
                guids.add(safe_text(parameter.GUID).lower())
        except Exception:
            pass
    return names, guids


def _find_family_parameter(manager, name):
    try:
        for parameter in manager.Parameters:
            if safe_text(parameter.Definition.Name).lower() == name.lower():
                return parameter
    except Exception:
        pass
    return None


def _apply_rows_to_family_doc(family_doc, rows, definitions, mode, summary,
                              target_name):
    """Add or update every selected parameter inside one open family document.

    Returns True when at least one write succeeded, so the caller knows whether
    reloading or saving the family is worth doing at all.
    """
    manager = getattr(family_doc, "FamilyManager", None)
    if manager is None:
        summary.note_failed(target_name, u"(all)",
                            u"the family has no FamilyManager")
        return False

    names, guids = existing_parameter_names(family_doc)
    wrote = False

    transaction = _quiet_transaction(family_doc, "Load Parameters")
    transaction.Start()
    try:
        for row in rows:
            definition = definitions.get(row.key)
            if definition is None:
                summary.note_skipped(target_name, row.name,
                                     u"no shared parameter definition")
                continue

            already = (row.name.lower() in names) or (row.key in guids)
            group_id = forge_type_id(row.group_id) or DB.GroupTypeId.Data

            if already and mode != state.MODE_UPDATE:
                summary.note_skipped(
                    target_name, row.name,
                    state.STATUS_LABELS[state.SKIP_EXISTS])
                continue

            try:
                if already:
                    if _update_existing(manager, row, group_id):
                        summary.note_applied(target_name, row.name, u"updated")
                        wrote = True
                    else:
                        summary.note_skipped(
                            target_name, row.name,
                            u"already present and could not be updated")
                    continue

                manager.AddParameter(definition, group_id, row.is_instance)
                summary.note_applied(target_name, row.name, u"added")
                names.add(row.name.lower())
                guids.add(row.key)
                wrote = True
            except Exception as error:
                # One bad pair - a name that collides with a built-in, say -
                # must not cost the whole family.
                summary.note_failed(target_name, row.name,
                                    exception_text(error))

        transaction.Commit()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        raise

    return wrote


def _update_existing(manager, row, group_id):
    parameter = _find_family_parameter(manager, row.name)
    if parameter is None:
        return False
    changed = False
    try:
        parameter.Definition.SetGroupTypeId(group_id)
        changed = True
    except Exception:
        pass
    try:
        if bool(parameter.IsInstance) != bool(row.is_instance):
            if row.is_instance:
                manager.MakeInstance(parameter)
            else:
                # MakeType fails when the parameter drives a label or a
                # formula, which is a refusal rather than an error.
                manager.MakeType(parameter)
            changed = True
    except Exception:
        pass
    return changed


def load_into_project_families(doc, family_rows, param_rows, definitions,
                               mode, summary, progress=None):
    """Edit and reload every selected project family, in one undo step.

    ``EditFamily`` and ``LoadFamily`` both need the project to be
    non-modifiable, i.e. no open Transaction.  An open TransactionGroup does
    not make a document modifiable, so this nesting is legal, and Assimilate
    collapses the whole batch into a single undo item.
    """
    rows = [row for row in family_rows
            if row.source == state.SOURCE_PROJECT and row.editable]
    if not rows:
        return False

    load_options = LoadOptions()
    total = len(rows)
    cancelled = False

    group = DB.TransactionGroup(doc, "Load Parameters to Families")
    group.Start()
    try:
        for index, row in enumerate(rows):
            if progress is not None and not progress(index, total):
                cancelled = True
                break
            _process_project_family(doc, row, param_rows, definitions, mode,
                                    summary, load_options)
    except Exception:
        if group.HasStarted():
            group.RollBack()
        raise

    if cancelled:
        if group.HasStarted():
            group.RollBack()
        summary.cancelled = True
        summary.clear_applied()
        return True

    if group.HasStarted():
        group.Assimilate()
    return False


def _process_project_family(doc, row, param_rows, definitions, mode, summary,
                            load_options):
    element_id = _element_id(row.element_id)
    family = doc.GetElement(element_id) if element_id is not None else None
    if family is None:
        summary.note_failed(row.name, u"(all)", u"the family is no longer in "
                            u"the model")
        return

    try:
        family_doc = doc.EditFamily(family)
    except Exception as error:
        summary.note_failed(row.name, u"(all)",
                            u"EditFamily failed: {0}".format(
                                exception_text(error)))
        return

    try:
        wrote = _apply_rows_to_family_doc(family_doc, param_rows, definitions,
                                          mode, summary, row.name)
        if not wrote:
            return
        try:
            load_options.reset()
            family_doc.LoadFamily(doc, load_options)
        except Exception as error:
            summary.note_failed(row.name, u"(all)",
                                u"LoadFamily failed: {0}".format(
                                    exception_text(error)))
    finally:
        try:
            family_doc.Close(False)
        except Exception as error:
            LOGGER.debug("Could not close %s: %s", row.name,
                         exception_text(error))


def load_into_folder_families(app, family_rows, param_rows, definitions, mode,
                              summary, output_folder=None, progress=None):
    """Write .rfa files on disk.  Runs last, and has no undo.

    A saved file cannot be rolled back, which is why the project pass goes
    first: cancelling before this phase costs nothing, and cancelling during it
    leaves a result the report names file by file.
    """
    rows = [row for row in family_rows
            if row.source == state.SOURCE_FOLDER and row.editable]
    if not rows:
        return

    total = len(rows)
    for index, row in enumerate(rows):
        if progress is not None and not progress(index, total):
            summary.cancelled = True
            return

        if row.is_read_only and not output_folder:
            summary.note_skipped(row.name, u"(all)",
                                 state.STATUS_LABELS[state.SKIP_READONLY_FILE])
            continue

        if not _process_folder_family(app, row, param_rows, definitions, mode,
                                      summary, output_folder):
            # A document that would not close leaks for the rest of the
            # session; several hundred of them exhaust it, so the phase stops.
            summary.note_failed(
                row.name, u"(all)",
                u"stopped: the family document could not be closed")
            return


def _process_folder_family(app, row, param_rows, definitions, mode, summary,
                           output_folder):
    try:
        family_doc = app.OpenDocumentFile(row.path)
    except Exception as error:
        summary.note_failed(row.name, u"(all)",
                            u"could not open: {0}".format(
                                exception_text(error)))
        return True
    if family_doc is None:
        summary.note_failed(row.name, u"(all)", u"could not open the file")
        return True

    closed = True
    try:
        wrote = _apply_rows_to_family_doc(family_doc, param_rows, definitions,
                                          mode, summary, row.name)
        if wrote:
            destination = _destination_path(row.path, output_folder, row.root)
            try:
                _save_family(family_doc, row.path, destination)
                summary.files_written.append(destination)
            except Exception as error:
                summary.note_failed(row.name, u"(all)",
                                    u"could not save: {0}".format(
                                        exception_text(error)))
    finally:
        try:
            family_doc.Close(False)
        except Exception as error:
            closed = False
            LOGGER.debug("Could not close %s: %s", row.path,
                         exception_text(error))
    return closed


def _destination_path(source_path, output_folder, source_root):
    if not output_folder:
        return source_path
    try:
        if source_root:
            relative = os.path.relpath(source_path, source_root)
        else:
            relative = os.path.basename(source_path)
    except Exception:
        relative = os.path.basename(source_path)
    destination = os.path.join(output_folder, relative)
    folder = os.path.dirname(destination)
    if folder and not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except Exception:
            pass
    return destination


def _save_family(family_doc, source_path, destination):
    if _normalized_path(destination) == _normalized_path(source_path):
        family_doc.Save()
        return
    options = DB.SaveAsOptions()
    options.OverwriteExistingFile = True
    family_doc.SaveAs(destination, options)


# ---------------------------------------------------------------------------
# writing into the project
# ---------------------------------------------------------------------------


def binding_lookup(doc):
    """A ``bound_lookup`` for ``state.build_project_plan``."""

    def _lookup(row):
        definition = _internal_definition(doc, row.guid)
        if definition is None:
            return False, False, []
        return _binding_kind(doc, definition)

    return _lookup


def _internal_definition(doc, guid_text):
    try:
        element = DB.SharedParameterElement.Lookup(
            doc, System.Guid(safe_text(guid_text)))
    except Exception:
        return None
    if element is None:
        return None
    try:
        return element.GetDefinition()
    except Exception:
        return None


def load_into_project(doc, app, param_rows, category_rows, definitions,
                      summary, progress=None):
    """Bind every selected parameter to every selected category.

    An existing binding is *merged*, never replaced: ReInsert overwrites the
    whole category set, so rebinding without unioning the current categories
    would quietly unbind everything the user did not happen to tick this time.
    """
    categories = [row for row in category_rows if row.is_checked]
    if not categories:
        return

    total = len(param_rows)
    transaction = DB.Transaction(doc, "Load Parameters to Project")
    transaction.Start()
    try:
        for index, row in enumerate(param_rows):
            if progress is not None and not progress(index, total):
                transaction.RollBack()
                summary.cancelled = True
                summary.clear_applied()
                return
            _bind_one(doc, app, row, categories, definitions, summary)
        transaction.Commit()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        raise


def _bind_one(doc, app, row, categories, definitions, summary):
    definition = definitions.get(row.key)
    if definition is None:
        summary.note_skipped(u"(project)", row.name,
                             u"no shared parameter definition")
        return

    existing_definition = _internal_definition(doc, row.guid)
    bound, _, existing_names = (False, False, [])
    if existing_definition is not None:
        bound, _, existing_names = _binding_kind(doc, existing_definition)

    category_set = app.Create.NewCategorySet()
    wanted = []
    for category_row in categories:
        category = category_by_id(doc, category_row.category_id)
        if category is None:
            continue
        try:
            category_set.Insert(category)
            wanted.append(category_row.name)
        except Exception:
            continue

    # Union with what is already bound, or ReInsert would drop it.
    kept = []
    if bound:
        for name in existing_names:
            category = _category_by_name(doc, name)
            if category is None:
                continue
            try:
                if category_set.Insert(category):
                    kept.append(name)
            except Exception:
                continue

    if category_set.IsEmpty:
        summary.note_skipped(u"(project)", row.name,
                             u"no usable categories were selected")
        return

    try:
        if row.is_instance:
            binding = app.Create.NewInstanceBinding(category_set)
        else:
            binding = app.Create.NewTypeBinding(category_set)
        group_id = forge_type_id(row.group_id) or DB.GroupTypeId.Data

        if bound:
            ok = doc.ParameterBindings.ReInsert(definition, binding, group_id)
        else:
            ok = doc.ParameterBindings.Insert(definition, binding, group_id)
    except Exception as error:
        summary.note_failed(u"(project)", row.name, exception_text(error))
        return

    if not ok:
        summary.note_failed(u"(project)", row.name,
                            u"Revit refused the binding")
        return

    note = u"{0}, {1} categor(ies)".format(row.binding_label, len(wanted))
    if kept:
        note += u", {0} kept".format(len(kept))
    summary.note_applied(u"(project)", row.name, note)


def _category_by_name(doc, name):
    try:
        for category in doc.Settings.Categories:
            if safe_text(category.Name).lower() == safe_text(name).lower():
                return category
    except Exception:
        pass
    return None
