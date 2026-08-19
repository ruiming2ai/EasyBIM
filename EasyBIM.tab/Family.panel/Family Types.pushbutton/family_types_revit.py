# -*- coding: utf-8 -*-
"""Revit-facing logic for Family Types: read the type table, write it back.

Reading follows ``families_downgrade_export.py`` - ``FamilyManager`` hands out
every family parameter, instance ones included, and each ``FamilyType`` holds
a value for all of them.  That is why instance parameters are columns here
rather than being filtered out: their per-type value is the default a new
instance is placed with, which is what Revit's own dialog marks "(default)".

Writing is the mirror image, in one transaction.  Every version-sensitive API
member is reached through a guarded probe with a documented fallback, the
approach this repo already takes for family code that must span 2021-2026.
"""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

from pyrevit import DB

from easybim.compat import eid_to_int
from easybim.compat import safe_text

import family_types_state as state


def _catch(getter, fallback=None):
    try:
        return getter()
    except Exception:
        return fallback


def _enum_name(value):
    if value is None:
        return u""
    return safe_text(_catch(lambda: value.ToString(), value))


def _has(owner, name):
    return owner is not None and hasattr(owner, name)


# ------------------------------------------------------------------ reading


def family_manager(family_doc):
    return _catch(lambda: family_doc.FamilyManager)


def family_display_name(family_doc):
    """The family's name, from the document title with the .rfa dropped."""
    title = safe_text(_catch(lambda: family_doc.Title))
    if title.lower().endswith(".rfa"):
        title = title[:-4]
    return title


def _all_parameters(manager):
    parameters = _catch(lambda: list(manager.GetParameters()), None)
    if parameters is None:
        parameters = _catch(lambda: list(manager.Parameters), [])
    return parameters


def _spec_label(definition):
    """A readable data-type label across the ForgeTypeId / enum split.

    2022+ answers ``GetDataType`` with a ForgeTypeId; 2021 has
    ``GetSpecTypeId`` for measurable specs and ``ParameterType`` for the rest.
    Only ever used for a tooltip and the metadata sheet, so an empty string is
    an acceptable answer on a host that offers none of them.
    """
    if definition is None:
        return u""
    if _has(definition, "GetDataType"):
        spec = _catch(lambda: definition.GetDataType())
        if spec is not None:
            label = safe_text(_catch(lambda: spec.TypeId, u""))
            if label:
                # "autodesk.spec.aec:length-2.0.0" -> "length"
                tail = label.split(":")[-1]
                return tail.split("-")[0].replace("_", " ") or label
    if _has(definition, "ParameterType"):
        return _enum_name(_catch(lambda: definition.ParameterType))
    return u""


def _is_yes_no(definition, parameter):
    """Whether an Integer parameter is really a checkbox."""
    if _has(definition, "GetDataType"):
        spec = _catch(lambda: definition.GetDataType())
        spec_id = safe_text(_catch(lambda: spec.TypeId, u""))
        if spec_id:
            return "yesno" in spec_id.replace("-", "").replace("_", "").lower()
    name = _enum_name(_catch(lambda: definition.ParameterType))
    if name:
        return name == "YesNo"
    del parameter
    return False


def _is_material(definition, family_doc, family_types, parameter):
    """Whether an ElementId parameter points at a Material.

    The definition answers on a host that exposes the spec; where it does not,
    the value carried by an existing type settles it, which is why the types
    are passed in.
    """
    if _has(definition, "GetDataType"):
        spec_id = safe_text(
            _catch(lambda: definition.GetDataType().TypeId, u""))
        if spec_id:
            return "material" in spec_id.lower()
    name = _enum_name(_catch(lambda: definition.ParameterType))
    if name:
        return name == "Material"
    for family_type in family_types:
        element_id = _catch(lambda: family_type.AsElementId(parameter))
        element = _catch(lambda: family_doc.GetElement(element_id))
        if element is not None:
            return isinstance(element, DB.Material)
    return False


def parameter_infos(family_doc, manager, family_types):
    """One dict per family parameter, in the shape build_parameter_columns wants."""
    infos = []
    for parameter in _all_parameters(manager):
        definition = _catch(lambda: parameter.Definition)
        if definition is None:
            continue
        name = safe_text(_catch(lambda: definition.Name))
        if not name:
            continue
        storage = _enum_name(_catch(lambda: parameter.StorageType))
        is_material = False
        if storage == state.STORAGE_ELEMENTID:
            is_material = _is_material(definition, family_doc, family_types,
                                       parameter)
        infos.append({
            "name": name,
            # FamilyParameter.Id is the round-trip key the Excel header
            # carries. It is the one member here that this repo has not used
            # before, so a host without it simply exports name-only headers
            # and the import falls back to matching by name.
            "param_id": eid_to_int(_catch(lambda: parameter.Id)),
            "storage_type": storage,
            "spec_label": _spec_label(definition),
            "is_instance": bool(_catch(lambda: parameter.IsInstance, False)),
            "is_yes_no": (storage == state.STORAGE_INTEGER
                          and _is_yes_no(definition, parameter)),
            "is_material": is_material,
            # IsReadOnly and UserModifiable are deliberately not read here.
            # FamilyParameter inherits them from Parameter, where they mean
            # "would Parameter.Set() work" - and a family parameter is written
            # through FamilyManager.Set instead. Reading them locked most of
            # the table on a real family. See state.lock_reason_for.
            "is_reporting": bool(_catch(lambda: parameter.IsReporting, False)),
            "is_determined_by_formula": bool(
                _catch(lambda: parameter.IsDeterminedByFormula, False)),
            "formula": safe_text(_catch(lambda: parameter.Formula)),
            "parameter": parameter,
        })
    return infos


def _cell_text(family_doc, family_type, parameter, column):
    """What one type shows for one parameter."""
    if not _catch(lambda: family_type.HasValue(parameter), True):
        return u""
    storage = column.storage_type
    if storage == state.STORAGE_DOUBLE:
        # AsValueString honours the user's display units, so the grid reads
        # the way the same value reads everywhere else in Revit.
        text = safe_text(_catch(lambda: family_type.AsValueString(parameter)))
        if text:
            return text
        value = _catch(lambda: family_type.AsDouble(parameter))
        return u"" if value is None else safe_text(value)
    if storage == state.STORAGE_INTEGER:
        value = _catch(lambda: family_type.AsInteger(parameter))
        if value is None:
            return u""
        if column.is_yes_no:
            # A bool, not text: the grid binds a CheckBox straight to it.
            return bool(int(value))
        return safe_text(int(value))
    if storage == state.STORAGE_STRING:
        return safe_text(_catch(lambda: family_type.AsString(parameter)))
    if storage == state.STORAGE_ELEMENTID:
        element_id = _catch(lambda: family_type.AsElementId(parameter))
        if element_id is None or eid_to_int(element_id) in (None, -1):
            return u""
        element = _catch(lambda: family_doc.GetElement(element_id))
        return safe_text(_catch(lambda: element.Name)) if element else u""
    return u""


def read_family_table(family_doc, column_factory=None, row_factory=None):
    """Read the whole type table.

    -> ``(columns, rows, notes)``.  ``column_factory(param_infos)`` and
    ``row_factory(name, is_default_row)`` let the UI supply its own
    notifying classes; both default to the plain state ones.
    """
    column_factory = column_factory or state.build_parameter_columns
    row_factory = row_factory or (
        lambda name, is_default_row=False:
        state.TypeRowBase(name, is_default_row=is_default_row))

    notes = []
    manager = family_manager(family_doc)
    if manager is None:
        return [], [], [u"This document has no FamilyManager."]

    family_types = _catch(lambda: list(manager.Types), [])
    defaults_only = False
    if not family_types:
        # A family with no named types still has values - they live on the
        # current type, which has no name. The row is shown so those values
        # are visible and editable; it just cannot be renamed or deleted.
        current = _catch(lambda: manager.CurrentType)
        family_types = [current] if current is not None else []
        defaults_only = True
        if family_types:
            notes.append(u"This family has no named types; its values are "
                         u"shown as a single {0} row.".format(
                             state.DEFAULT_TYPE_LABEL))

    infos = parameter_infos(family_doc, manager, family_types)
    columns = state.fixed_columns() + column_factory(infos)
    param_by_name = dict((info["name"], info["parameter"]) for info in infos)

    rows = []
    for family_type in family_types:
        name = state.DEFAULT_TYPE_LABEL if defaults_only \
            else safe_text(_catch(lambda: family_type.Name))
        row = row_factory(name, defaults_only)
        row.family_type = family_type
        values = {}
        for column in state.param_columns(columns):
            parameter = param_by_name.get(column.param_name)
            if parameter is None:
                continue
            values[column.key] = _cell_text(family_doc, family_type,
                                            parameter, column)
        state.populate_row(row, columns, values)
        rows.append(row)

    for column in state.param_columns(columns):
        column.parameter = param_by_name.get(column.param_name)
    return columns, rows, notes


# ------------------------------------------------------------------ writing


class ApplyResult(object):
    def __init__(self):
        self.created = 0
        self.renamed = 0
        self.deleted = 0
        self.values = 0
        self.problems = []      # human-readable, one per refused write
        self.loaded = u""       # "", "loaded", "overwritten" or "declined"

    def note(self, type_name, param_name, reason):
        if param_name:
            self.problems.append(u"{0} / {1}: {2}".format(
                type_name, param_name, reason))
        else:
            self.problems.append(u"{0}: {1}".format(type_name, reason))

    def text(self):
        bits = []
        for count, label in ((self.created, u"type(s) created"),
                             (self.renamed, u"type(s) renamed"),
                             (self.deleted, u"type(s) deleted"),
                             (self.values, u"value(s) set")):
            if count:
                bits.append(u"{0} {1}".format(count, label))
        return u", ".join(bits) if bits else u"nothing changed"


def _find_material(family_doc, name):
    wanted = state.normalize_name(name)
    if not wanted:
        return None
    materials = _catch(
        lambda: list(DB.FilteredElementCollector(family_doc)
                     .OfClass(DB.Material)), [])
    for material in materials:
        if state.normalize_name(_catch(lambda: material.Name)) == wanted:
            return material
    return None


def _set_value(manager, family_doc, parameter, column, text, result,
               type_name):
    """Write one cell. Returns True when the value actually changed.

    ``text`` is whatever the column stores - text for most columns, a bool for
    a yes/no one; only the Integer branch below is ever handed the bool.
    """
    storage = column.storage_type
    if storage == state.STORAGE_DOUBLE:
        if not text.strip():
            result.note(type_name, column.param_name,
                        u"cleared cells are ignored - Revit has no way to "
                        u"unset a numeric family parameter")
            return False
        # SetValueString parses in the user's display units, which is what
        # the cell was written in by AsValueString.
        if _has(manager, "SetValueString"):
            if _catch(lambda: manager.SetValueString(parameter, text)) \
                    is not False:
                return True
        try:
            manager.Set(parameter, float(text))
            return True
        except Exception as error:
            result.note(type_name, column.param_name,
                        u"could not read '{0}' as a number ({1})".format(
                            text, error))
            return False
    if storage == state.STORAGE_INTEGER:
        if column.is_yes_no:
            # The grid stores a bool; only an import can still hand over text.
            if isinstance(text, bool):
                value = 1 if text else 0
            else:
                parsed = state.parse_bool_cell(text)
                if parsed is None:
                    result.note(type_name, column.param_name,
                                u"'{0}' is not Yes or No".format(text))
                    return False
                value = 1 if parsed else 0
        else:
            if not text.strip():
                result.note(type_name, column.param_name,
                            u"cleared cells are ignored")
                return False
            try:
                value = int(float(text))
            except (TypeError, ValueError):
                result.note(type_name, column.param_name,
                            u"'{0}' is not a whole number".format(text))
                return False
        try:
            manager.Set(parameter, value)
            return True
        except Exception as error:
            result.note(type_name, column.param_name, safe_text(error))
            return False
    if storage == state.STORAGE_STRING:
        try:
            manager.Set(parameter, text)
            return True
        except Exception as error:
            result.note(type_name, column.param_name, safe_text(error))
            return False
    if storage == state.STORAGE_ELEMENTID and column.is_material:
        if not text.strip():
            result.note(type_name, column.param_name,
                        u"cleared cells are ignored")
            return False
        material = _find_material(family_doc, text)
        if material is None:
            result.note(type_name, column.param_name,
                        u"this family has no material called '{0}'".format(
                            text))
            return False
        try:
            manager.Set(parameter, material.Id)
            return True
        except Exception as error:
            result.note(type_name, column.param_name, safe_text(error))
            return False
    result.note(type_name, column.param_name, u"this parameter cannot be set "
                u"from the grid")
    return False


def apply_changes(family_doc, changes, transaction_name="Family Types"):
    """Write every staged change in one transaction. -> ApplyResult.

    Order is deliberate: deletes and renames both free a name, so they run
    before any new type is created - otherwise a swap (A renamed to B while a
    new A appears) would collide inside Revit.

    A single refused cell is reported and the rest still commits, the way
    Sheet Manager reports a partial apply; only a failure to open the
    transaction aborts everything.
    """
    result = ApplyResult()
    manager = family_manager(family_doc)
    if manager is None:
        result.problems.append(u"This document has no FamilyManager.")
        return result
    if changes.is_empty():
        return result

    previous_type = _catch(lambda: manager.CurrentType)
    transaction = DB.Transaction(family_doc, transaction_name)
    transaction.Start()
    try:
        for row in changes.deletes:
            family_type = getattr(row, "family_type", None)
            if family_type is None:
                continue
            try:
                manager.CurrentType = family_type
                manager.DeleteCurrentType()
                result.deleted += 1
            except Exception as error:
                result.note(row.type_name, u"", safe_text(error))

        for row, old_name, new_name in changes.renames:
            family_type = getattr(row, "family_type", None)
            if family_type is None:
                continue
            try:
                manager.CurrentType = family_type
                manager.RenameCurrentType(new_name)
                result.renamed += 1
            except Exception as error:
                result.note(old_name, u"",
                            u"could not rename to '{0}' ({1})".format(
                                new_name, safe_text(error)))

        for row in changes.new_types:
            # NewType copies the current type's values into the new one, so
            # every value the grid shows for this row is written below - the
            # window seeds a new row from an existing type for exactly this
            # reason. A parameter the row leaves blank keeps whatever it
            # inherited; there is no way to unset a family parameter.
            try:
                row.family_type = manager.NewType(row.type_name)
                result.created += 1
            except Exception as error:
                row.family_type = None
                result.note(row.type_name, u"",
                            u"could not create ({0})".format(safe_text(error)))

        # Group by type so CurrentType is set once per type, not per cell.
        # A plain list keeps the grid's row order; rows are not hashable in a
        # way worth relying on and there are never many of them.
        by_row = []
        for row, column, old, new in changes.value_edits:
            del old
            for known_row, edits in by_row:
                if known_row is row:
                    edits.append((column, new))
                    break
            else:
                by_row.append((row, [(column, new)]))

        for row, edits in by_row:
            family_type = getattr(row, "family_type", None)
            if family_type is None:
                result.note(row.type_name, u"",
                            u"skipped - the type was not created")
                continue
            try:
                manager.CurrentType = family_type
            except Exception as error:
                result.note(row.type_name, u"", safe_text(error))
                continue
            for column, text in edits:
                parameter = getattr(column, "parameter", None)
                if parameter is None:
                    result.note(row.type_name, column.param_name,
                                u"this parameter is no longer in the family")
                    continue
                if _set_value(manager, family_doc, parameter, column, text,
                              result, row.type_name):
                    result.values += 1

        if previous_type is not None:
            _catch(lambda: setattr(manager, "CurrentType", previous_type))
        transaction.Commit()
    except Exception as error:
        _catch(lambda: transaction.RollBack())
        result.problems.append(
            u"Nothing was written: {0}".format(safe_text(error)))
    return result


def load_back_into_project(family_doc, project_doc, family_name, result,
                           load_options):
    """Reload the edited family into the project it came from.

    Revit raises the "Family Already Exists" prompt only when the family is
    both present and actually changed, so the user sees exactly the question a
    manual load asks, and answers it once.  A decline is a decision, not a
    failure.
    """
    if load_options is None:
        result.loaded = u"declined"
        result.problems.append(
            u"Could not build the family load options; nothing was loaded.")
        return result
    _catch(lambda: load_options.begin(family_name))
    try:
        loaded = family_doc.LoadFamily(project_doc, load_options)
    except Exception as error:
        if getattr(load_options, "declined", False):
            result.loaded = u"declined"
        else:
            result.loaded = u"failed"
            result.problems.append(
                u"LoadFamily failed: {0}".format(safe_text(error)))
        return result
    if getattr(load_options, "declined", False):
        result.loaded = u"declined"
    elif loaded:
        result.loaded = u"overwritten"
    else:
        result.loaded = u"failed"
        result.problems.append(u"LoadFamily returned false.")
    return result
