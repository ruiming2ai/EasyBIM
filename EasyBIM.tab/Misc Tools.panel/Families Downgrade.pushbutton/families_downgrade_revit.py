# -*- coding: utf-8 -*-
"""Revit-facing helpers shared by the export and rebuild halves of Families
Downgrade: version probing, ForgeTypeId reflection, transactions with silent
warnings, family-document sessions, the template finder, the temporary shared
parameter file and the small geometry conversions.

Everything version-dependent is decided at runtime by asking the API what it
has (``hasattr`` on the class, a try on the overload), never by comparing a
version number - a member that is not there cannot be called, which is all a
caller cares about.
"""

# pylint: disable=import-error,invalid-name,broad-except
import math
import os

import clr

clr.AddReference("System")

import System

from pyrevit import DB
from pyrevit import script

from easybim.compat import eid_to_int
from easybim.compat import exception_text
from easybim.compat import safe_text
from easybim.family_selection_revit import close_family_doc
from easybim.family_selection_revit import edit_family
from easybim.family_selection_revit import is_link_family_option
from easybim.family_selection_revit import is_open_rfa_family_option
from easybim.family_selection_revit import is_transferable_family
from easybim.family_selection_revit import resolve_family

import families_downgrade_state as state


LOGGER = script.get_logger()

TEMP_SHARED_FILE_ID = "families_downgrade_shared_parameters"
SHARED_PARAMETER_HEADER = u"\n".join([
    u"# This is a Revit shared parameter file.",
    u"# Do not edit manually.",
    u"*META\tVERSION\tMINVERSION",
    u"META\t2\t1",
    u"",
])
DEFINITION_GROUP_NAME = u"Families Downgrade"


# --------------------------------------------------------------------------
# host
# --------------------------------------------------------------------------


def host_version(app):
    return safe_text(getattr(app, "VersionNumber", "")).strip()


def host_build(app):
    return safe_text(getattr(app, "VersionBuild", "")).strip()


def clr_type(python_type):
    try:
        return clr.GetClrType(python_type)
    except Exception:
        return python_type


def enum_names(enum_type):
    """Every member name of a .NET enum on this host, or ``[]``."""
    try:
        return [safe_text(name) for name in System.Enum.GetNames(clr_type(enum_type))]
    except Exception:
        try:
            return [safe_text(name) for name in System.Enum.GetNames(enum_type)]
        except Exception:
            return []


def enum_member(enum_type, name):
    """``enum_type.name`` or ``None`` when the host lacks the member."""
    name = safe_text(name)
    if not name:
        return None
    try:
        return getattr(enum_type, name)
    except Exception:
        pass
    try:
        return System.Enum.Parse(clr_type(enum_type), name)
    except Exception:
        return None


def enum_name(value):
    """The member name of an enum value (``SupplyAir``), never the integer."""
    if value is None:
        return ""
    try:
        return safe_text(value.ToString())
    except Exception:
        return safe_text(value)


def has_api(owner, member):
    try:
        return hasattr(owner, member)
    except Exception:
        return False


# --------------------------------------------------------------------------
# ForgeTypeId reflection
# --------------------------------------------------------------------------


def _static_forge_properties(static_type, prefix=""):
    """``{TypeId string: property path}`` for a class of static ForgeTypeIds."""
    found = {}
    try:
        clr_static = clr_type(static_type)
        flags = (System.Reflection.BindingFlags.Public
                 | System.Reflection.BindingFlags.Static)
        for prop in clr_static.GetProperties(flags):
            try:
                value = prop.GetValue(None, None)
                type_id = safe_text(getattr(value, "TypeId", ""))
            except Exception:
                continue
            if type_id:
                found.setdefault(type_id, prefix + safe_text(prop.Name))
        for nested in clr_static.GetNestedTypes():
            nested_prefix = prefix + safe_text(nested.Name) + "."
            for prop in nested.GetProperties(flags):
                try:
                    value = prop.GetValue(None, None)
                    type_id = safe_text(getattr(value, "TypeId", ""))
                except Exception:
                    continue
                if type_id:
                    found.setdefault(type_id, nested_prefix + safe_text(prop.Name))
    except Exception as error:
        LOGGER.debug("ForgeTypeId reflection failed: %s", exception_text(error))
    return found


class ForgeNames(object):
    """Lazily built ``TypeId -> SpecTypeId/GroupTypeId property`` maps."""

    def __init__(self):
        self._specs = None
        self._groups = None

    @property
    def specs(self):
        if self._specs is None:
            self._specs = (_static_forge_properties(DB.SpecTypeId)
                           if has_api(DB, "SpecTypeId") else {})
        return self._specs

    @property
    def groups(self):
        if self._groups is None:
            self._groups = (_static_forge_properties(DB.GroupTypeId)
                            if has_api(DB, "GroupTypeId") else {})
        return self._groups

    def spec_property(self, forge_id):
        return self.specs.get(safe_text(getattr(forge_id, "TypeId", forge_id)), "")

    def group_property(self, forge_id):
        return self.groups.get(safe_text(getattr(forge_id, "TypeId", forge_id)), "")


FORGE = ForgeNames()


def forge_type_id(type_id):
    """A ``ForgeTypeId`` from its string, or ``None`` off a 2021+ host."""
    type_id = safe_text(type_id)
    if not has_api(DB, "ForgeTypeId"):
        return None
    try:
        return DB.ForgeTypeId(type_id) if type_id else DB.ForgeTypeId()
    except Exception:
        return None


def static_forge_id(static_type, property_path):
    """``SpecTypeId.String.Text`` reached from the string ``"String.Text"``."""
    owner = static_type
    for part in safe_text(property_path).split("."):
        if not part:
            return None
        try:
            owner = getattr(owner, part)
        except Exception:
            return None
    return owner


# --------------------------------------------------------------------------
# transactions
# --------------------------------------------------------------------------


class SilentWarnings(DB.IFailuresPreprocessor):
    """Delete warnings; roll back on errors instead of showing a dialog.

    A batch cannot survive a modal failure dialog, and a rolled-back
    transaction is reported by the caller from its commit status - never a
    silent success.
    """

    def PreprocessFailures(self, failuresAccessor):
        has_error = False
        try:
            for failure in list(failuresAccessor.GetFailureMessages()):
                if failure.GetSeverity() == DB.FailureSeverity.Warning:
                    failuresAccessor.DeleteWarning(failure)
                else:
                    has_error = True
        except Exception:
            pass
        if has_error:
            return DB.FailureProcessingResult.ProceedWithRollBack
        return DB.FailureProcessingResult.Continue


def run_transaction(doc, name, work):
    """Run ``work()`` inside a transaction; ``(ok, error_text)``.

    Warnings are swallowed, an error rolls back, an exception rolls back and
    is returned as text. ``ok`` is only True when the commit really landed.
    """
    transaction = DB.Transaction(doc, safe_text(name) or "Families Downgrade")
    try:
        transaction.Start()
    except Exception as error:
        return False, "could not start a transaction: {}".format(exception_text(error))
    try:
        handling = transaction.GetFailureHandlingOptions()
        handling.SetFailuresPreprocessor(SilentWarnings())
        handling.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(handling)
    except Exception:
        pass
    try:
        work()
    except Exception as error:
        try:
            transaction.RollBack()
        except Exception:
            pass
        return False, exception_text(error)
    try:
        status = transaction.Commit()
    except Exception as error:
        try:
            transaction.RollBack()
        except Exception:
            pass
        return False, "commit failed: {}".format(exception_text(error))
    if not _committed(status):
        return False, "Revit rolled the change back ({})".format(enum_name(status))
    return True, ""


def _committed(status):
    try:
        return status == DB.TransactionStatus.Committed
    except Exception:
        return True


def regenerate(doc):
    try:
        doc.Regenerate()
    except Exception:
        pass


# --------------------------------------------------------------------------
# family document sessions
# --------------------------------------------------------------------------


class FamilySession(object):
    """A family document to read from, and how to let go of it afterwards.

    Project and link families come from ``EditFamily`` and are closed
    without saving. An already-open ``.rfa`` is the user's document: it is
    wrapped in a ``TransactionGroup`` that is rolled back, so every scratch
    view, hidden element and type switch made while reading it is undone.
    """

    def __init__(self, document, owned, group=None):
        self.document = document
        self.owned = bool(owned)
        self.group = group

    def close(self):
        if self.group is not None:
            try:
                if self.group.HasStarted() and not self.group.HasEnded():
                    self.group.RollBack()
            except Exception:
                pass
            self.group = None
            return
        if self.owned:
            close_family_doc(self.document)
            self.document = None


def open_family_session(family_option, source_doc, index_cache=None):
    """``(FamilySession, reason)`` - one of them is ``None``."""
    if is_open_rfa_family_option(family_option):
        document = getattr(family_option, "family_document", None)
        if document is None:
            return None, "family document is unavailable"
        group = None
        try:
            group = DB.TransactionGroup(document, "Families Downgrade (read only)")
            group.Start()
        except Exception as error:
            return None, "could not start reading the open family: {}".format(
                exception_text(error))
        return FamilySession(document, owned=False, group=group), ""

    is_link = is_link_family_option(family_option)
    edit_doc = getattr(family_option, "source_document", None) or source_doc
    family = resolve_family(source_doc, family_option, index_cache)
    if not is_transferable_family(family, require_editable=not is_link):
        return None, "family is not editable"
    try:
        document = edit_family(edit_doc, family)
    except Exception as error:
        text = exception_text(error)
        if is_link:
            return None, (
                "this link will not hand over a family document, so it cannot "
                "be packaged")
        if "already" in text.lower() and "open" in text.lower():
            return None, (
                "the family is already open in this session; close it, or tick "
                "it in the Opened .rfa card instead ({})".format(text))
        return None, "EditFamily failed: {}".format(text)
    if document is None:
        return None, "EditFamily returned no document"
    return FamilySession(document, owned=True), ""


# --------------------------------------------------------------------------
# geometry conversions
# --------------------------------------------------------------------------


def xyz_to_list(xyz):
    try:
        return [float(xyz.X), float(xyz.Y), float(xyz.Z)]
    except Exception:
        return [0.0, 0.0, 0.0]


def list_to_xyz(values):
    values = list(values or [0.0, 0.0, 0.0])
    while len(values) < 3:
        values.append(0.0)
    return DB.XYZ(float(values[0]), float(values[1]), float(values[2]))


def bbox_to_list(bbox):
    if bbox is None:
        return None
    try:
        return [xyz_to_list(bbox.Min), xyz_to_list(bbox.Max)]
    except Exception:
        return None


def union_bbox_lists(boxes):
    """One ``[[min],[max]]`` around every box in ``boxes`` (``None`` skipped)."""
    low = None
    high = None
    for box in boxes or []:
        if not box:
            continue
        if low is None:
            low = list(box[0])
            high = list(box[1])
            continue
        for i in range(3):
            low[i] = min(low[i], box[0][i])
            high[i] = max(high[i], box[1][i])
    if low is None:
        return None
    return [low, high]


def element_bbox_list(element):
    try:
        return bbox_to_list(element.get_BoundingBox(None))
    except Exception:
        return None


def id_list(element_ids):
    from System.Collections.Generic import List as ClrList
    collection = ClrList[DB.ElementId]()
    for element_id in element_ids or []:
        collection.Add(element_id)
    return collection


def double_list(values):
    from System.Collections.Generic import List as ClrList
    collection = ClrList[System.Double]()
    for value in values or []:
        collection.Add(float(value))
    return collection


def xyz_list(points):
    from System.Collections.Generic import List as ClrList
    collection = ClrList[DB.XYZ]()
    for point in points or []:
        collection.Add(list_to_xyz(point))
    return collection


def element_id_value(element_id):
    try:
        return int(eid_to_int(element_id))
    except Exception:
        return None


def is_valid_id(element_id):
    if element_id is None:
        return False
    try:
        return element_id != DB.ElementId.InvalidElementId
    except Exception:
        return False


def element_name(element):
    if element is None:
        return ""
    try:
        return safe_text(element.Name)
    except Exception:
        return ""


def class_name(element):
    try:
        return safe_text(element.GetType().Name)
    except Exception:
        return safe_text(type(element).__name__)


def builtin_category_name(category):
    """``OST_MechanicalEquipment`` for a Category, or ``""``."""
    if category is None:
        return ""
    try:
        member = category.BuiltInCategory
        text = enum_name(member)
        if text.startswith("OST_"):
            return text
    except Exception:
        pass
    value = element_id_value(getattr(category, "Id", None))
    if value is None:
        return ""
    try:
        text = safe_text(System.Enum.GetName(clr_type(DB.BuiltInCategory), value))
        return text if text.startswith("OST_") else ""
    except Exception:
        return ""


def category_by_builtin_name(doc, builtin_name):
    """The document's Category for an ``OST_`` name, or ``None``."""
    member = enum_member(DB.BuiltInCategory, builtin_name)
    if member is None:
        return None
    try:
        return doc.Settings.Categories.get_Item(member)
    except Exception:
        try:
            return DB.Category.GetCategory(doc, member)
        except Exception:
            return None


def parameter_value_record(parameter):
    """A ``value_record`` for a Parameter, by its storage type."""
    try:
        if not parameter.HasValue:
            return state.value_record("none")
    except Exception:
        pass
    storage = enum_name(getattr(parameter, "StorageType", None))
    try:
        if storage == "Double":
            return state.value_record("double", float(parameter.AsDouble()))
        if storage == "Integer":
            return state.value_record("int", int(parameter.AsInteger()))
        if storage == "String":
            return state.value_record("string", safe_text(parameter.AsString()))
        if storage == "ElementId":
            element_id = parameter.AsElementId()
            if not is_valid_id(element_id):
                return state.value_record("none")
            element = parameter.Element.Document.GetElement(element_id)
            return state.value_record("element", element_name=element_name(element),
                                      element_class=class_name(element))
    except Exception:
        pass
    return state.value_record("none")


def builtin_parameter_name(parameter):
    """``ALL_MODEL_MANUFACTURER`` for a built-in parameter, else ``""``."""
    try:
        definition = parameter.Definition
        builtin = definition.BuiltInParameter
    except Exception:
        return ""
    text = enum_name(builtin)
    if not text or text == "INVALID":
        return ""
    return text


def find_parameter(element, builtin_name):
    """An element parameter by built-in name, or ``None`` on this host."""
    member = enum_member(DB.BuiltInParameter, builtin_name)
    if member is None:
        return None
    try:
        return element.get_Parameter(member)
    except Exception:
        return None


def set_parameter_from_record(parameter, record, doc=None, material_lookup=None):
    """Write a value record onto a Parameter; ``(ok, reason)``."""
    if parameter is None:
        return False, "parameter is missing"
    try:
        if parameter.IsReadOnly:
            return False, "read-only"
    except Exception:
        pass
    kind = safe_text((record or {}).get("kind"))
    value = (record or {}).get("value")
    try:
        if kind == "double":
            return bool(parameter.Set(float(value))), ""
        if kind == "int":
            return bool(parameter.Set(int(value))), ""
        if kind == "string":
            return bool(parameter.Set(safe_text(value))), ""
        if kind == "element":
            if material_lookup is None:
                return False, "element values are not carried"
            element_id = material_lookup(record.get("element_name"), record.get("element_class"))
            if element_id is None:
                return False, "'{}' is not available here".format(
                    safe_text(record.get("element_name")))
            return bool(parameter.Set(element_id)), ""
    except Exception as error:
        return False, exception_text(error)
    return False, "no value"


# --------------------------------------------------------------------------
# templates
# --------------------------------------------------------------------------


def list_templates(folder):
    """``{file name: full path}`` for every ``.rft`` under ``folder``."""
    templates = {}
    folder = safe_text(folder)
    if not folder or not os.path.isdir(folder):
        return templates
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if name.lower().endswith(".rft") and name not in templates:
                templates[name] = os.path.join(root, name)
    return templates


def choose_template(templates, family_record, fallback_path=""):
    """``(path, note)`` - the best ``.rft`` for a family record.

    ``templates`` is ``list_templates`` output; ``fallback_path`` a user-picked
    file used when nothing in the folder fits. The note names the template so
    the report can say which one a family was built on.
    """
    ranked = state.rank_templates(list(templates.keys()), family_record)
    if ranked:
        name = ranked[0]
        return templates[name], "template: {}".format(name)
    if fallback_path and os.path.isfile(fallback_path):
        return fallback_path, "template: {} (fallback)".format(os.path.basename(fallback_path))
    return "", "no family template was found"


# --------------------------------------------------------------------------
# the temporary shared parameter file
# --------------------------------------------------------------------------


def _restore_shared_parameters_filename(app, original):
    try:
        app.SharedParametersFilename = original or u""
    except Exception as error:
        LOGGER.debug("Could not restore SharedParametersFilename: %s", exception_text(error))


class SharedParameterScratch(object):
    """A throwaway shared parameter file that hands out ``ExternalDefinition``s.

    Same shape as Load Parameters' file: only the header is ours, every PARAM
    line is written by Revit through ``Definitions.Create`` (with the recorded
    GUID, which is what makes the rebuilt parameter the same shared parameter
    the project knows). ``SharedParametersFilename`` is restored in
    ``close()`` before the file is deleted, whatever happened in between.
    """

    def __init__(self, app):
        self.app = app
        self.path = u""
        self.error = ""
        self._original = u""
        self._file = None
        self._group = None
        self.definitions = {}

    def open(self):
        self._original = safe_text(getattr(self.app, "SharedParametersFilename", u"")) or u""
        try:
            self.path = script.get_universal_data_file(TEMP_SHARED_FILE_ID, "txt")
            with open(self.path, "wb") as handle:
                handle.write(SHARED_PARAMETER_HEADER.encode("ascii"))
            self.app.SharedParametersFilename = self.path
            self._file = self.app.OpenSharedParameterFile()
            if self._file is None:
                self.error = "Revit could not open the temporary shared parameter file"
                return self
            self._group = self._file.Groups.Create(DEFINITION_GROUP_NAME)
        except Exception as error:
            self.error = exception_text(error)
        return self

    def definition(self, name, guid, spec_or_type):
        """The ExternalDefinition for a shared parameter, created on demand."""
        key = safe_text(guid).lower()
        if key in self.definitions:
            return self.definitions[key]
        if self._group is None:
            return None
        options = DB.ExternalDefinitionCreationOptions(safe_text(name), spec_or_type)
        options.GUID = System.Guid(safe_text(guid))
        definition = self._group.Definitions.Create(options)
        self.definitions[key] = definition
        return definition

    def close(self):
        _restore_shared_parameters_filename(self.app, self._original)
        if not self.path:
            return
        try:
            if os.path.isfile(self.path):
                os.remove(self.path)
        except Exception as error:
            LOGGER.debug("Could not remove %s: %s", self.path, exception_text(error))


# --------------------------------------------------------------------------
# small maths
# --------------------------------------------------------------------------


def unit_vector(values):
    values = [float(v) for v in (values or [0.0, 0.0, 1.0])]
    size = math.sqrt(sum(v * v for v in values))
    if size <= 1e-12:
        return [0.0, 0.0, 1.0]
    return [v / size for v in values]


def cross(a, b):
    return [
        float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
        float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
        float(a[0]) * float(b[1]) - float(a[1]) * float(b[0]),
    ]
