# -*- coding: utf-8 -*-
"""The rebuild half of Families Downgrade: package folder -> ``.rfa``.

Runs in the *target* Revit (any release from 2021, including the one that
wrote the package, which is how a round trip is tested). Every package
becomes a new family document from the best-fitting template: category and
flags, subcategories and materials, parameters through the version ladder,
types and values, formulas, then the geometry files imported and turned into
native FreeForm solids with their attributes, MEP connectors placed on the
matching faces, symbolic and model lines, named reference planes, and a
``SaveAs`` into the chosen folder. Each stage is its own transaction and its
own try/except: a failed stage is a note on the family, only a template or
document failure fails the family.
"""

# pylint: disable=import-error,invalid-name,broad-except
import math
import os

from pyrevit import DB

from easybim.compat import exception_text
from easybim.compat import safe_text
from easybim.family_selection_revit import close_family_doc
from easybim.family_selection_revit import keep_going

import families_downgrade_revit as fdr
import families_downgrade_state as state


def _catch(work, default=None):
    try:
        return work()
    except Exception:
        return default


# --------------------------------------------------------------------------
# what the host can do
# --------------------------------------------------------------------------


class HostCapabilities(object):
    """The shape of the parameter API on the running Revit, asked once.

    ``uses_forge`` is the 2022+ ``AddParameter(name, ForgeTypeId group,
    ForgeTypeId spec, bool)`` world - detected by ``Definition.GetDataType``,
    which arrived with it - and the alternative is 2021's
    ``BuiltInParameterGroup`` + ``ParameterType`` overload.
    """

    def __init__(self, app):
        self.version = fdr.host_version(app)
        self.uses_forge = fdr.has_api(DB, "Definition") and fdr.has_api(DB.Definition, "GetDataType")
        self.has_group_type_id = fdr.has_api(DB, "GroupTypeId")
        self.has_parameter_type = fdr.has_api(DB, "ParameterType")
        self.has_builtin_group = fdr.has_api(DB, "BuiltInParameterGroup")
        self.parameter_type_names = (
            fdr.enum_names(DB.ParameterType) if self.has_parameter_type else [])
        self.builtin_group_names = (
            fdr.enum_names(DB.BuiltInParameterGroup) if self.has_builtin_group else [])
        self.category_names = fdr.enum_names(DB.BuiltInCategory)

    def system_type_names(self, enum_type):
        return fdr.enum_names(enum_type)


# --------------------------------------------------------------------------
# per-family context
# --------------------------------------------------------------------------


class RebuildContext(object):
    """Everything one family's stages share: the document, caches, notes."""

    def __init__(self, doc, manifest, caps, scratch, result):
        self.doc = doc
        self.manifest = manifest
        self.caps = caps
        self.scratch = scratch
        self.result = result
        self.family = _catch(lambda: doc.OwnerFamily)
        self.manager = _catch(lambda: doc.FamilyManager)
        self.family_category = _catch(lambda: self.family.FamilyCategory)
        self.parameters = {}          # name -> FamilyParameter
        self.parameter_records = {}   # name -> manifest record
        self.materials = {}           # name -> ElementId
        self.subcategories = {}       # name -> Category
        self.solid_elements = []      # created FreeForm / import instances
        self.view3d = None
        self.plan_view = None
        self.elevation_views = []
        self.sketch_planes = {}
        for record in manifest.get("parameters") or []:
            self.parameter_records[safe_text(record.get("name"))] = record

    def note(self, text):
        self.result.add_note(text)

    def transaction(self, name, work):
        ok, error = fdr.run_transaction(self.doc, name, work)
        if not ok:
            self.note("{}: {}".format(name, error))
        return ok

    # -- lookups ------------------------------------------------------------

    def family_parameter(self, name):
        name = safe_text(name)
        parameter = self.parameters.get(name)
        if parameter is not None:
            return parameter
        parameter = _catch(lambda: self.manager.get_Parameter(name))
        if parameter is not None:
            self.parameters[name] = parameter
        return parameter

    def material_id(self, name, element_class=None):
        """The ElementId of a material by name, created from the manifest if missing."""
        name = safe_text(name)
        if not name:
            return None
        if element_class and element_class != "Material":
            return None
        if name in self.materials:
            return self.materials[name]
        materials = _catch(
            lambda: list(DB.FilteredElementCollector(self.doc).OfClass(DB.Material)), [])
        for material in materials:
            if fdr.element_name(material) == name:
                self.materials[name] = material.Id
                return material.Id
        record = None
        for candidate in self.manifest.get("materials") or []:
            if safe_text(candidate.get("name")) == name:
                record = candidate
                break
        try:
            material_id = DB.Material.Create(self.doc, name)
        except Exception as error:
            self.note("material '{}' could not be created: {}".format(name, exception_text(error)))
            self.materials[name] = None
            return None
        material = self.doc.GetElement(material_id)
        if record and material is not None:
            _apply_material_record(self.doc, material, record)
        self.materials[name] = material_id
        return material_id

    def subcategory(self, name):
        name = safe_text(name)
        if not name:
            return None
        if name in self.subcategories:
            return self.subcategories[name]
        category = self.family_category
        found = None
        for sub in _catch(lambda: list(category.SubCategories), []):
            if safe_text(_catch(lambda: sub.Name)) == name:
                found = sub
                break
        if found is None:
            try:
                found = self.doc.Settings.Categories.NewSubcategory(category, name)
            except Exception as error:
                self.note("subcategory '{}' could not be created: {}".format(
                    name, exception_text(error)))
                found = None
        self.subcategories[name] = found
        return found

    def three_d_view(self):
        if self.view3d is not None:
            return self.view3d
        for view in _catch(lambda: list(DB.FilteredElementCollector(self.doc).OfClass(DB.View3D)), []):
            if not bool(_catch(lambda: view.IsTemplate, False)):
                self.view3d = view
                return view
        holder = {}

        def work():
            for view_type in DB.FilteredElementCollector(self.doc).OfClass(DB.ViewFamilyType):
                if view_type.ViewFamily == DB.ViewFamily.ThreeDimensional:
                    holder["view"] = DB.View3D.CreateIsometric(self.doc, view_type.Id)
                    return
        self.transaction("Families Downgrade 3D view", work)
        self.view3d = holder.get("view")
        return self.view3d

    def views_for_planes(self):
        if self.plan_view is None:
            for view in _catch(lambda: list(DB.FilteredElementCollector(self.doc).OfClass(DB.View)), []):
                if bool(_catch(lambda: view.IsTemplate, False)):
                    continue
                view_type = fdr.enum_name(_catch(lambda: view.ViewType))
                if view_type == "FloorPlan" and self.plan_view is None:
                    self.plan_view = view
                elif view_type == "Elevation":
                    self.elevation_views.append(view)
        return self.plan_view, self.elevation_views


def _apply_material_record(doc, material, record):
    color = record.get("color")
    if color and len(color) == 3:
        _catch(lambda: setattr(material, "Color", DB.Color(int(color[0]), int(color[1]), int(color[2]))))
    for key, attr in (("transparency", "Transparency"), ("shininess", "Shininess"),
                      ("smoothness", "Smoothness")):
        value = record.get(key)
        if value is not None:
            _catch(lambda: setattr(material, attr, int(value)))
    for key, attr, target in (("surface_foreground_pattern", "SurfaceForegroundPatternId",
                               "Model"),
                              ("cut_foreground_pattern", "CutForegroundPatternId", "Drafting")):
        name = safe_text(record.get(key))
        if not name:
            continue
        pattern = _fill_pattern_by_name(doc, name, target)
        if pattern is not None:
            _catch(lambda: setattr(material, attr, pattern.Id))


def _fill_pattern_by_name(doc, name, target_name):
    for target in (target_name, "Drafting", "Model"):
        try:
            pattern = DB.FillPatternElement.GetFillPatternElementByName(
                doc, getattr(DB.FillPatternTarget, target), name)
        except Exception:
            pattern = None
        if pattern is not None:
            return pattern
    return None


# --------------------------------------------------------------------------
# stage 1: category and flags
# --------------------------------------------------------------------------


def apply_family_record(ctx):
    record = ctx.manifest.get("family") or {}
    caps = ctx.caps

    def work():
        family = ctx.family
        recorded = safe_text(record.get("category_builtin"))
        if not recorded:
            wanted = ""
            ctx.note("the package does not name the family category; the template's category "
                     "({}) is kept".format(safe_text(_catch(lambda: ctx.family_category.Name))))
        else:
            wanted, note = state.category_for(recorded, caps.category_names)
            if note:
                ctx.note(note)
        current = fdr.builtin_category_name(ctx.family_category)
        if wanted and wanted != current:
            category = fdr.category_by_builtin_name(ctx.doc, wanted)
            if category is None:
                ctx.note("category {} is not available in this document".format(wanted))
            else:
                try:
                    family.FamilyCategory = category
                    ctx.family_category = _catch(lambda: family.FamilyCategory)
                except Exception as error:
                    ctx.note("category could not be set to {} (stays {}): {}".format(
                        wanted, safe_text(_catch(lambda: ctx.family_category.Name)),
                        exception_text(error)))
        for name, value in sorted((record.get("flags") or {}).items()):
            parameter = fdr.find_parameter(family, name)
            if parameter is None:
                continue
            ok, reason = fdr.set_parameter_from_record(parameter, value)
            if not ok and reason and reason != "read-only" and reason != "no value":
                ctx.note("family setting {} not applied: {}".format(name, reason))

    ctx.transaction("Families Downgrade category", work)


# --------------------------------------------------------------------------
# stage 2: subcategories and materials
# --------------------------------------------------------------------------


def apply_subcategories(ctx):
    records = ctx.manifest.get("subcategories") or []
    if not records:
        return

    def work():
        for record in records:
            sub = ctx.subcategory(record.get("name"))
            if sub is None:
                continue
            color = record.get("line_color")
            if color and len(color) == 3:
                _catch(lambda: setattr(sub, "LineColor",
                                       DB.Color(int(color[0]), int(color[1]), int(color[2]))))
            for key, style in (("projection_weight", "Projection"), ("cut_weight", "Cut")):
                weight = record.get(key)
                if weight:
                    _catch(lambda: sub.SetLineWeight(int(weight), getattr(DB.GraphicsStyleType, style)))
            material_name = safe_text(record.get("material"))
            if material_name:
                material_id = ctx.material_id(material_name)
                if material_id is not None:
                    _catch(lambda: setattr(sub, "Material", ctx.doc.GetElement(material_id)))

    ctx.transaction("Families Downgrade subcategories", work)


def apply_materials(ctx):
    records = ctx.manifest.get("materials") or []
    if not records:
        return

    def work():
        for record in records:
            ctx.material_id(record.get("name"))

    ctx.transaction("Families Downgrade materials", work)


# --------------------------------------------------------------------------
# stage 3: parameters (the version ladder)
# --------------------------------------------------------------------------


def _spec_is_valid(forge_id):
    if fdr.has_api(DB, "SpecUtils") and fdr.has_api(DB.SpecUtils, "IsValidDataType"):
        return bool(_catch(lambda: DB.SpecUtils.IsValidDataType(forge_id), False))
    if fdr.has_api(DB, "UnitUtils") and fdr.has_api(DB.UnitUtils, "IsSpec"):
        return bool(_catch(lambda: DB.UnitUtils.IsSpec(forge_id), False))
    return True


STORAGE_SPEC_PROPERTIES = {"Double": "Number", "Integer": "Int.Integer", "String": "String.Text"}


def resolve_spec(record, caps, note):
    """``(kind, value)`` for the parameter's data type on this host.

    ``kind`` is ``forge`` (a ForgeTypeId), ``legacy`` (a ParameterType member),
    ``category`` (a family-type parameter; ``value`` is the ``OST_`` name or
    ``""``) or ``None`` when the type cannot be created here. ``note`` is a
    callable that receives the reason for any fallback.
    """
    name = safe_text(record.get("name"))
    spec_property = safe_text(record.get("spec_property"))
    if spec_property == "Reference.FamilyType" or safe_text(record.get("parameter_type")) == "FamilyType":
        return "category", safe_text(record.get("family_type_category"))

    if caps.uses_forge:
        forge_id = fdr.forge_type_id(record.get("spec_type_id"))
        if forge_id is not None and safe_text(record.get("spec_type_id")) and _spec_is_valid(forge_id):
            return "forge", forge_id
        if spec_property and spec_property not in state.UNSUPPORTED_SPEC_PROPERTIES:
            static = fdr.static_forge_id(DB.SpecTypeId, spec_property)
            if static is not None:
                return "forge", static
        legacy = safe_text(record.get("parameter_type"))
        if legacy and legacy not in ("Text", "Integer", "Number", "YesNo", "URL",
                                     "MultilineText", "Material", "Image",
                                     "LoadClassification", "NumberOfPoles"):
            # A 2021 exporter names measurable specs by their legacy enum; the
            # SpecTypeId property usually shares the spelling.
            static = fdr.static_forge_id(DB.SpecTypeId, legacy)
            if static is not None:
                return "forge", static
        table = {"Text": "String.Text", "URL": "String.Url", "MultilineText": "String.MultilineText",
                 "Integer": "Int.Integer", "NumberOfPoles": "Int.NumberOfPoles",
                 "YesNo": "Boolean.YesNo", "Material": "Reference.Material",
                 "Image": "Reference.Image", "LoadClassification": "Reference.LoadClassification",
                 "Number": "Number"}
        if legacy in table:
            static = fdr.static_forge_id(DB.SpecTypeId, table[legacy])
            if static is not None:
                return "forge", static
        storage = safe_text(record.get("storage_type"))
        fallback = STORAGE_SPEC_PROPERTIES.get(storage)
        static = fdr.static_forge_id(DB.SpecTypeId, fallback) if fallback else None
        if static is not None:
            note("parameter '{}': data type '{}' does not exist in this Revit; created as {} "
                 "(its values are unitless here)".format(
                     name, spec_property or safe_text(record.get("spec_type_id")) or legacy or storage,
                     fallback))
            return "forge", static
        note("parameter '{}': data type cannot be created in this Revit; parameter skipped".format(name))
        return None, None

    unit_type_name = ""
    forge_id = fdr.forge_type_id(record.get("spec_type_id"))
    if forge_id is not None and safe_text(record.get("spec_type_id")) and fdr.has_api(DB, "UnitUtils"):
        unit_type_name = fdr.enum_name(_catch(lambda: DB.UnitUtils.GetUnitType(forge_id)))
    legacy_name, reason = state.parameter_type_for(record, caps.parameter_type_names, unit_type_name)
    if reason:
        note(reason)
    if not legacy_name:
        return None, None
    if legacy_name == "FamilyType":
        return "category", safe_text(record.get("family_type_category"))
    member = fdr.enum_member(DB.ParameterType, legacy_name)
    if member is None:
        note("parameter '{}': ParameterType.{} is not available; parameter skipped".format(
            name, legacy_name))
        return None, None
    return "legacy", member


def _group_property_from_legacy(legacy_name):
    """The GroupTypeId property name for a ``PG_`` name, via the quirks and normalisation."""
    for prop, legacy in state.GROUP_NAME_QUIRKS.items():
        if legacy == legacy_name:
            return prop
    wanted = state.normalize_enum_name(legacy_name)
    for prop in fdr.FORGE.groups.values():
        if state.normalize_enum_name(prop) == wanted:
            return prop
    return ""


def resolve_group(record, caps, note):
    """The group argument for ``AddParameter`` on this host (ForgeTypeId or enum)."""
    name = safe_text(record.get("name"))
    if caps.uses_forge:
        recorded_id = safe_text(record.get("group_type_id"))
        forge_id = fdr.forge_type_id(recorded_id) if recorded_id else None
        if forge_id is not None:
            valid = True
            if fdr.has_api(DB, "ParameterUtils") and fdr.has_api(DB.ParameterUtils, "IsBuiltInGroup"):
                valid = bool(_catch(lambda: DB.ParameterUtils.IsBuiltInGroup(forge_id), True))
            if valid:
                return forge_id
        prop = safe_text(record.get("group_property"))
        legacy = safe_text(record.get("builtin_group"))
        if not prop and legacy:
            prop = _group_property_from_legacy(legacy)
        if prop and caps.has_group_type_id:
            static = fdr.static_forge_id(DB.GroupTypeId, prop)
            if static is not None:
                return static
        if legacy and caps.has_builtin_group and fdr.has_api(DB, "ParameterUtils") and fdr.has_api(
                DB.ParameterUtils, "GetParameterGroupTypeId"):
            member = fdr.enum_member(DB.BuiltInParameterGroup, legacy)
            if member is not None:
                converted = _catch(lambda: DB.ParameterUtils.GetParameterGroupTypeId(member))
                if converted is not None:
                    return converted
        if prop or legacy or recorded_id:
            note("parameter '{}': group '{}' does not exist in this Revit; placed under "
                 "Other".format(name, prop or legacy or recorded_id))
        other = fdr.forge_type_id("")
        if other is not None:
            return other
        return fdr.static_forge_id(DB.GroupTypeId, "General") if caps.has_group_type_id else None

    legacy_name, reason = state.builtin_group_for(record, caps.builtin_group_names)
    if reason:
        note(reason)
    return fdr.enum_member(DB.BuiltInParameterGroup, legacy_name)


def _instance_matches(parameter, wanted):
    try:
        return bool(parameter.IsInstance) == bool(wanted)
    except Exception:
        return True


def add_family_parameter(ctx, record):
    """Create (or reuse) one family parameter; the FamilyParameter or ``None``."""
    manager = ctx.manager
    name = safe_text(record.get("name"))
    if not name:
        return None
    existing = ctx.family_parameter(name)
    if existing is not None:
        if not record.get("builtin") and not _instance_matches(existing, record.get("is_instance")):
            try:
                if record.get("is_instance"):
                    manager.MakeInstance(existing)
                else:
                    manager.MakeType(existing)
            except Exception:
                ctx.note("parameter '{}' exists in the template as a {} parameter and stays one".format(
                    name, "type" if record.get("is_instance") else "instance"))
        return existing
    if record.get("builtin"):
        return None

    spec_kind, spec = resolve_spec(record, ctx.caps, ctx.note)
    if spec_kind is None:
        return None
    group = resolve_group(record, ctx.caps, ctx.note)
    is_instance = bool(record.get("is_instance"))
    if record.get("is_reporting"):
        ctx.note("parameter '{}' was a reporting parameter; rebuilt as a plain one".format(name))

    if spec_kind == "category":
        category = fdr.category_by_builtin_name(ctx.doc, spec) if spec else None
        if category is None:
            ctx.note("parameter '{}' (family type) skipped: its category is unknown here".format(name))
            return None
        if record.get("is_shared") and record.get("guid"):
            ctx.note("parameter '{}' (shared family type) rebuilt as a family parameter".format(name))
        parameter = manager.AddParameter(name, group, category, is_instance)
        ctx.parameters[name] = parameter
        ctx.note("parameter '{}' (family type) rebuilt empty: nested families are not carried".format(name))
        return parameter

    if record.get("is_shared") and record.get("guid") and ctx.scratch is not None:
        definition = None
        try:
            definition = ctx.scratch.definition(name, record.get("guid"), spec)
        except Exception as error:
            ctx.note("shared parameter '{}' definition refused ({}); rebuilt as a family "
                     "parameter".format(name, exception_text(error)))
        if definition is not None:
            parameter = manager.AddParameter(definition, group, is_instance)
            ctx.parameters[name] = parameter
            return parameter

    parameter = manager.AddParameter(name, group, spec, is_instance)
    ctx.parameters[name] = parameter
    return parameter


def apply_parameters(ctx):
    records = ctx.manifest.get("parameters") or []
    if not records:
        return

    def work():
        for record in records:
            try:
                add_family_parameter(ctx, record)
            except Exception as error:
                ctx.note("parameter '{}' could not be created: {}".format(
                    safe_text(record.get("name")), exception_text(error)))
        # Display order, best effort: the template's own parameters stay
        # where Revit puts them.
        try:
            ordered = []
            for record in records:
                parameter = ctx.parameters.get(safe_text(record.get("name")))
                if parameter is not None and not record.get("builtin"):
                    ordered.append(parameter)
            if ordered:
                ctx.manager.ReorderParameters(_family_parameter_list(ordered))
        except Exception:
            pass

    ctx.transaction("Families Downgrade parameters", work)


def _family_parameter_list(parameters):
    from System.Collections.Generic import List as ClrList
    collection = ClrList[DB.FamilyParameter]()
    for parameter in parameters:
        collection.Add(parameter)
    return collection


# --------------------------------------------------------------------------
# stage 4: types, values, formulas
# --------------------------------------------------------------------------


def _skip_value_reason(record):
    if record is None:
        return None
    if record.get("formula") or record.get("is_determined_by_formula"):
        return "formula"
    if record.get("is_read_only"):
        return "read-only"
    return None


def set_family_value(ctx, parameter, value_record, parameter_name):
    """Write one type value; a reason string when it could not be."""
    manager = ctx.manager
    kind = safe_text((value_record or {}).get("kind"))
    value = (value_record or {}).get("value")
    try:
        if kind == "double":
            manager.Set(parameter, float(value))
            return None
        if kind == "int":
            manager.Set(parameter, int(value))
            return None
        if kind == "string":
            manager.Set(parameter, safe_text(value))
            return None
        if kind == "element":
            element_class = safe_text(value_record.get("element_class"))
            element_name = safe_text(value_record.get("element_name"))
            if element_class == "Material":
                material_id = ctx.material_id(element_name)
                if material_id is None:
                    return "material '{}' unavailable".format(element_name)
                manager.Set(parameter, material_id)
                return None
            if element_class == "ElectricalLoadClassification":
                classification = _load_classification(ctx, element_name)
                if classification is None:
                    return "load classification '{}' unavailable".format(element_name)
                manager.Set(parameter, classification.Id)
                return None
            return "'{}' ({}) is not carried".format(element_name, element_class or "element")
    except Exception as error:
        return exception_text(error)
    return None


def _load_classification(ctx, name):
    if not name:
        return None
    for element in _catch(lambda: list(DB.FilteredElementCollector(ctx.doc).OfClass(
            DB.Electrical.ElectricalLoadClassification)), []):
        if fdr.element_name(element) == name:
            return element
    return _catch(lambda: DB.Electrical.ElectricalLoadClassification.Create(ctx.doc, name))


def apply_types(ctx):
    manager = ctx.manager
    types = ctx.manifest.get("types") or []
    if not types:
        return
    refused = {}

    def work():
        existing = {}
        for family_type in _catch(lambda: list(manager.Types), []):
            existing[safe_text(_catch(lambda: family_type.Name))] = family_type
        for type_record in types:
            type_name = safe_text(type_record.get("name"))
            family_type = existing.get(type_name)
            if family_type is None and type_name:
                family_type = manager.NewType(type_name)
                existing[type_name] = family_type
            if family_type is not None:
                manager.CurrentType = family_type
            for param_name, value_record in sorted((type_record.get("values") or {}).items()):
                record = ctx.parameter_records.get(param_name)
                if _skip_value_reason(record):
                    continue
                if not value_record or value_record.get("kind") in ("none", None):
                    continue
                parameter = ctx.family_parameter(param_name)
                if parameter is None:
                    continue
                reason = set_family_value(ctx, parameter, value_record, param_name)
                if reason and param_name not in refused:
                    refused[param_name] = reason

    ctx.transaction("Families Downgrade types", work)
    for param_name in sorted(refused):
        ctx.note("value of '{}' not set: {}".format(param_name, refused[param_name]))


def apply_formulas(ctx):
    manager = ctx.manager
    pending = []
    for record in ctx.manifest.get("parameters") or []:
        formula = safe_text(record.get("formula"))
        if not formula:
            continue
        parameter = ctx.family_parameter(record.get("name"))
        if parameter is None:
            continue
        pending.append((safe_text(record.get("name")), parameter, formula))
    if not pending:
        return
    failures = {}

    def work():
        remaining = list(pending)
        # Formulas reference each other; keep going while a pass lands one.
        while remaining:
            progressed = False
            still = []
            for name, parameter, formula in remaining:
                try:
                    manager.SetFormula(parameter, formula)
                    progressed = True
                    failures.pop(name, None)
                except Exception as error:
                    failures[name] = exception_text(error)
                    still.append((name, parameter, formula))
            remaining = still
            if not progressed:
                break

    ctx.transaction("Families Downgrade formulas", work)
    for name in sorted(failures):
        ctx.note("formula on '{}' refused: {}".format(name, failures[name]))


# --------------------------------------------------------------------------
# stage 5: geometry
# --------------------------------------------------------------------------


def _import_options(geometry_format, unit_name):
    if geometry_format == state.GEOMETRY_DWG:
        options = DB.DWGImportOptions()
    else:
        options = DB.SATImportOptions()
    _catch(lambda: setattr(options, "Placement", DB.ImportPlacement.Origin))
    _catch(lambda: setattr(options, "ThisViewOnly", False))
    _catch(lambda: setattr(options, "VisibleLayersOnly", False))
    unit = _catch(lambda: getattr(DB.ImportUnit, unit_name or "Default"))
    if unit is not None:
        _catch(lambda: setattr(options, "Unit", unit))
    return options


def _import_file(doc, path, view, geometry_format, unit_name):
    """Import one geometry file; ``(ElementId, error)``. Runs its own transaction."""
    holder = {}

    def work():
        options = _import_options(geometry_format, unit_name)
        result = doc.Import(path, options, view)
        if isinstance(result, tuple):
            # DWG import has an out parameter: (bool, ElementId).
            holder["id"] = result[-1]
        else:
            holder["id"] = result
        doc.Regenerate()

    ok, error = fdr.run_transaction(doc, "Families Downgrade import", work)
    if not ok:
        return None, error
    element_id = holder.get("id")
    if not fdr.is_valid_id(element_id):
        return None, "import produced no instance"
    return element_id, ""


def _delete_elements(doc, element_ids):
    def work():
        for element_id in element_ids:
            try:
                doc.Delete(element_id)
            except Exception:
                pass
    fdr.run_transaction(doc, "Families Downgrade delete", work)


def _instance_solids(element):
    """``(solids, sheet_count)``: every solid with volume in an element's
    geometry (world coordinates), and how many zero-volume bodies were left out."""
    solids = []
    counter = {"sheets": 0}
    geometry = _catch(lambda: element.get_Geometry(DB.Options()))
    if geometry is None:
        return solids, 0
    _collect_solids(geometry, solids, counter)
    return solids, counter["sheets"]


def _collect_solids(geometry, solids, counter):
    for item in geometry:
        if isinstance(item, DB.Solid):
            if _catch(lambda: item.Volume, 0.0) > 1e-9:
                solids.append(item)
            else:
                counter["sheets"] += 1
        elif isinstance(item, DB.GeometryInstance):
            instance_geometry = _catch(lambda: item.GetInstanceGeometry())
            if instance_geometry is not None:
                _collect_solids(instance_geometry, solids, counter)


def import_group(ctx, folder, group):
    """Import one group's file, self-check it, and turn it into FreeForms.

    Returns the list of solid elements now standing for the group (FreeForms,
    or the import instance itself when a body would not become one).
    """
    file_name = safe_text(group.get("file"))
    if not file_name:
        return []
    path = os.path.join(folder, file_name)
    if not os.path.isfile(path):
        ctx.note("geometry group {}: file {} is missing".format(group.get("index"), file_name))
        return []
    view = ctx.three_d_view()
    if view is None:
        ctx.note("geometry group {}: no 3D view to import into".format(group.get("index")))
        return []
    geometry_format = safe_text((ctx.manifest.get("geometry") or {}).get("format")) or state.GEOMETRY_SAT
    recorded_bbox = group.get("bbox")

    element_id, error = _import_file(ctx.doc, path, view, geometry_format, "Default")
    if element_id is None:
        ctx.note("geometry group {}: import failed: {}".format(group.get("index"), error))
        return []
    instance = ctx.doc.GetElement(element_id)
    imported_bbox = fdr.element_bbox_list(instance)
    if recorded_bbox and imported_bbox and not state.size_matches(recorded_bbox, imported_bbox):
        # The file's own unit header gave the wrong size: try the units in
        # turn until the extents match what the exporter measured.
        matched = False
        for unit_name in state.IMPORT_UNIT_CANDIDATES:
            _delete_elements(ctx.doc, [element_id])
            element_id, error = _import_file(ctx.doc, path, view, geometry_format, unit_name)
            if element_id is None:
                ctx.note("geometry group {}: import failed: {}".format(group.get("index"), error))
                return []
            instance = ctx.doc.GetElement(element_id)
            imported_bbox = fdr.element_bbox_list(instance)
            if state.size_matches(recorded_bbox, imported_bbox):
                ctx.note("geometry group {}: the file's unit header was wrong; imported as {}".format(
                    group.get("index"), unit_name.lower()))
                matched = True
                break
        if not matched:
            ctx.note("geometry group {}: imported size does not match the exported one "
                     "(ratio {:.4f}); kept as imported".format(
                         group.get("index"), state.size_ratio(recorded_bbox, imported_bbox) or 0.0))
    if recorded_bbox and imported_bbox:
        offset = state.bbox_offset(recorded_bbox, imported_bbox)
        if state.offset_is_significant(offset):
            def move():
                DB.ElementTransformUtils.MoveElement(ctx.doc, element_id, fdr.list_to_xyz(offset))
                ctx.doc.Regenerate()
            if fdr.run_transaction(ctx.doc, "Families Downgrade place import", move)[0]:
                ctx.note("geometry group {}: import moved by {} to its exported position".format(
                    group.get("index"), state.format_mm(math.sqrt(sum(c * c for c in offset)))))
                instance = ctx.doc.GetElement(element_id)

    return _free_forms_from_import(ctx, group, instance, element_id)


def _free_forms_from_import(ctx, group, instance, instance_id):
    solids, dropped = _instance_solids(instance)
    if not solids:
        ctx.note("geometry group {}: the imported file holds no solid bodies; kept as an import "
                 "instance".format(group.get("index")))
        return [instance]
    created = []
    problems = []

    def work():
        for solid in solids:
            element = _catch(lambda: DB.FreeFormElement.Create(ctx.doc, solid))
            if element is not None:
                created.append(element)
                continue
            pieces = _catch(lambda: list(DB.SolidUtils.SplitVolumes(solid)), [])
            if len(pieces) > 1:
                piece_elements = []
                for piece in pieces:
                    element = _catch(lambda: DB.FreeFormElement.Create(ctx.doc, piece))
                    if element is None:
                        piece_elements = None
                        break
                    piece_elements.append(element)
                if piece_elements:
                    created.extend(piece_elements)
                    continue
            problems.append(solid)
        if problems:
            raise RuntimeError("{} body(ies) would not become native solids".format(len(problems)))

    ok, error = fdr.run_transaction(ctx.doc, "Families Downgrade native solids", work)
    if not ok:
        del created[:]
        ctx.note("geometry group {}: {}; kept as an import instance without per-solid "
                 "attributes".format(group.get("index"), error))
        return [instance]

    def cleanup():
        type_id = _catch(lambda: instance.GetTypeId())
        ctx.doc.Delete(instance_id)
        if fdr.is_valid_id(type_id):
            _catch(lambda: ctx.doc.Delete(type_id))
    fdr.run_transaction(ctx.doc, "Families Downgrade drop import", cleanup)
    if dropped:
        ctx.note("geometry group {}: {} sheet body(ies) dropped".format(group.get("index"), dropped))
    return created


def apply_group_attributes(ctx, group, elements):
    """Subcategory, material, visibility and Visible on the group's solids."""
    if not elements:
        return
    manager = ctx.manager
    material = group.get("material") or {}
    visible = group.get("visible") or {}
    visibility = group.get("visibility") or {}
    subcategory_name = safe_text(group.get("subcategory"))

    def work():
        subcategory = ctx.subcategory(subcategory_name) if subcategory_name else None
        for element in elements:
            if subcategory is not None:
                _catch(lambda: setattr(element, "Subcategory", subcategory))
            material_param = fdr.find_parameter(element, "MATERIAL_ID_PARAM")
            if material_param is not None:
                if material.get("kind") == "parameter":
                    family_parameter = ctx.family_parameter(material.get("name"))
                    if family_parameter is not None:
                        _catch(lambda: manager.AssociateElementParameterToFamilyParameter(
                            material_param, family_parameter))
                elif material.get("kind") == "material":
                    material_id = ctx.material_id(material.get("name"))
                    if material_id is not None:
                        _catch(lambda: material_param.Set(material_id))
            if fdr.has_api(element, "GetVisibility"):
                settings = _catch(lambda: element.GetVisibility())
                if settings is not None:
                    for key, attr in (("coarse", "IsShownInCoarse"), ("medium", "IsShownInMedium"),
                                      ("fine", "IsShownInFine"),
                                      ("plan_rcp_cut", "IsShownInPlanRCPCut"),
                                      ("front_back", "IsShownInFrontBack"),
                                      ("left_right", "IsShownInLeftRight"),
                                      ("top_bottom", "IsShownInTopBottom"),
                                      ("only_when_cut", "IsShownOnlyWhenCut")):
                        if key in visibility:
                            _catch(lambda: setattr(settings, attr, bool(visibility[key])))
                    _catch(lambda: element.SetVisibility(settings))
            elif visibility.get("raw") is not None:
                raw = fdr.find_parameter(element, "GEOM_VISIBILITY_PARAM")
                if raw is not None:
                    _catch(lambda: raw.Set(int(visibility["raw"])))
            visible_param = fdr.find_parameter(element, "IS_VISIBLE_PARAM")
            if visible_param is not None:
                if visible.get("kind") == "parameter":
                    family_parameter = ctx.family_parameter(visible.get("name"))
                    if family_parameter is not None:
                        _catch(lambda: manager.AssociateElementParameterToFamilyParameter(
                            visible_param, family_parameter))
                elif visible.get("kind") == "literal" and not visible.get("value", True):
                    _catch(lambda: visible_param.Set(0))

    ctx.transaction("Families Downgrade solid attributes", work)


def apply_geometry(ctx, folder):
    groups = (ctx.manifest.get("geometry") or {}).get("groups") or []
    for group in groups:
        elements = import_group(ctx, folder, group)
        apply_group_attributes(ctx, group, elements)
        ctx.solid_elements.extend(elements)
    fdr.regenerate(ctx.doc)


# --------------------------------------------------------------------------
# stage 6: connectors
# --------------------------------------------------------------------------


def _planar_faces(ctx):
    """``[(face, element)]`` for every planar face on the rebuilt solids."""
    faces = []
    options = DB.Options()
    options.ComputeReferences = True
    for element in ctx.solid_elements:
        geometry = _catch(lambda: element.get_Geometry(options))
        if geometry is None:
            continue
        for solid in _solids_with_references(geometry):
            for face in _catch(lambda: list(solid.Faces), []):
                if isinstance(face, DB.PlanarFace):
                    faces.append((face, element))
    return faces


def _solids_with_references(geometry):
    solids = []
    for item in geometry:
        if isinstance(item, DB.Solid):
            solids.append(item)
        elif isinstance(item, DB.GeometryInstance):
            # Instance geometry keeps its references; the symbol's does not.
            nested = _catch(lambda: item.GetInstanceGeometry())
            if nested is not None:
                solids.extend(_solids_with_references(nested))
    return solids


def find_connector_face(faces, connector):
    """``(face, score)`` for the best home of a recorded connector, or ``(None, None)``."""
    origin = connector.get("origin") or [0, 0, 0]
    normal = fdr.unit_vector(connector.get("basis_z") or [0, 0, 1])
    origin_xyz = fdr.list_to_xyz(origin)
    best = None
    best_score = None
    for face, _element in faces:
        face_origin = fdr.xyz_to_list(_catch(lambda: face.Origin))
        face_normal = fdr.xyz_to_list(_catch(lambda: face.FaceNormal))
        projection = _catch(lambda: face.Project(origin_xyz))
        inside = projection is not None and _catch(lambda: float(projection.Distance), 1.0) < 1e-4
        score = state.face_match_score(origin, normal, face_origin, face_normal, inside)
        if score is None:
            continue
        if not inside and projection is not None:
            score += _catch(lambda: float(projection.Distance), 0.0)
        if best_score is None or score < best_score:
            best = face
            best_score = score
    return best, best_score


def _create_connector(ctx, connector, reference):
    kind = safe_text(connector.get("kind"))
    classification = safe_text(connector.get("system_classification"))
    doc = ctx.doc
    if kind == "duct":
        names = fdr.enum_names(DB.Mechanical.DuctSystemType)
        system_name, note = state.system_type_name_for(classification, names)
        if note:
            ctx.note(note)
        system_type = fdr.enum_member(DB.Mechanical.DuctSystemType, system_name)
        shape = fdr.enum_member(DB.ConnectorProfileType, connector.get("shape")) or DB.ConnectorProfileType.Round
        return DB.ConnectorElement.CreateDuctConnector(doc, system_type, shape, reference)
    if kind == "pipe":
        names = fdr.enum_names(DB.Plumbing.PipeSystemType)
        system_name, note = state.system_type_name_for(classification, names)
        if note:
            ctx.note(note)
        system_type = fdr.enum_member(DB.Plumbing.PipeSystemType, system_name)
        return DB.ConnectorElement.CreatePipeConnector(doc, system_type, reference)
    if kind == "electrical":
        names = fdr.enum_names(DB.Electrical.ElectricalSystemType)
        system_name, note = state.system_type_name_for(classification, names)
        if note:
            ctx.note(note)
        system_type = fdr.enum_member(DB.Electrical.ElectricalSystemType, system_name)
        return DB.ConnectorElement.CreateElectricalConnector(doc, system_type, reference)
    if kind == "cabletray":
        return DB.ConnectorElement.CreateCableTrayConnector(doc, reference)
    if kind == "conduit":
        return DB.ConnectorElement.CreateConduitConnector(doc, reference)
    raise ValueError("connector domain '{}' is not supported".format(kind or "unknown"))


def _apply_connector_properties(ctx, connector, element):
    manager = ctx.manager
    for key, attr in (("radius", "Radius"), ("width", "Width"), ("height", "Height")):
        value = connector.get(key)
        if value is not None and float(value) > 0:
            _catch(lambda: setattr(element, attr, float(value)))
    direction = fdr.enum_member(DB.FlowDirectionType, connector.get("direction"))
    if direction is not None:
        _catch(lambda: setattr(element, "Direction", direction))
    classification = fdr.enum_member(DB.MEPSystemClassification,
                                     connector.get("system_classification"))
    if classification is not None:
        _catch(lambda: setattr(element, "SystemClassification", classification))
    for builtin, value_record in sorted((connector.get("values") or {}).items()):
        parameter = fdr.find_parameter(element, builtin)
        if parameter is None:
            continue
        fdr.set_parameter_from_record(
            parameter, value_record,
            material_lookup=lambda name, cls: _connector_element_lookup(ctx, name, cls))
    for builtin, family_name in sorted((connector.get("associations") or {}).items()):
        parameter = fdr.find_parameter(element, builtin)
        family_parameter = ctx.family_parameter(family_name)
        if parameter is None or family_parameter is None:
            continue
        _catch(lambda: manager.AssociateElementParameterToFamilyParameter(parameter, family_parameter))
    # Rectangular and oval connectors: rotate the created X axis onto the
    # recorded one through CONNECTOR_ANGLE.
    created = _catch(lambda: element.CoordinateSystem)
    if created is not None and connector.get("basis_x"):
        created_x = fdr.xyz_to_list(created.BasisX)
        normal = fdr.xyz_to_list(created.BasisZ)
        angle = state.connector_angle(connector.get("basis_x"), created_x, normal)
        if abs(angle) > 1e-3:
            angle_param = fdr.find_parameter(element, "CONNECTOR_ANGLE")
            if angle_param is not None:
                current = _catch(lambda: float(angle_param.AsDouble()), 0.0)
                _catch(lambda: angle_param.Set(current + angle))


def _connector_element_lookup(ctx, name, element_class):
    if element_class == "ElectricalLoadClassification":
        element = _load_classification(ctx, name)
        return element.Id if element is not None else None
    if element_class == "Material":
        return ctx.material_id(name)
    return None


def apply_connectors(ctx):
    connectors = ctx.manifest.get("connectors") or []
    if not connectors:
        return
    fdr.regenerate(ctx.doc)
    faces = _planar_faces(ctx)
    created = {}
    misses = []

    def work():
        for connector in connectors:
            index = connector.get("index")
            face, score = find_connector_face(faces, connector)
            if face is None:
                ctx.note("connector {} ({}) has no matching planar face and was not created "
                         "(a work-plane connector cannot be placed by the API)".format(
                             index, safe_text(connector.get("system_classification"))))
                continue
            try:
                element = _create_connector(ctx, connector, face.Reference)
            except Exception as error:
                ctx.note("connector {} could not be created: {}".format(index, exception_text(error)))
                continue
            created[index] = element
            _apply_connector_properties(ctx, connector, element)
            recorded = connector.get("origin") or [0, 0, 0]
            actual = fdr.xyz_to_list(_catch(lambda: element.Origin))
            distance = math.sqrt(sum((float(recorded[i]) - actual[i]) ** 2 for i in range(3)))
            if distance > 1e-3:
                misses.append((index, distance))
        for connector in connectors:
            element = created.get(connector.get("index"))
            if element is None:
                continue
            if connector.get("is_primary"):
                _catch(lambda: element.AssignAsPrimary())
            linked = connector.get("linked_to")
            if linked is not None and created.get(linked) is not None:
                _catch(lambda: element.SetLinkedConnectorElement(created[linked]))

    ctx.transaction("Families Downgrade connectors", work)
    for index, distance in misses:
        ctx.note("connector {} sits {} from its exported position (the API places a connector "
                 "at the centre of its face)".format(index, state.format_mm(distance)))


# --------------------------------------------------------------------------
# stage 7: curves
# --------------------------------------------------------------------------


def _plane_key(plane):
    values = []
    for key in ("origin", "normal", "x"):
        values.extend(round(float(v), 4) for v in (plane.get(key) or [0, 0, 0]))
    return tuple(values)


def sketch_plane_for(ctx, plane_record):
    if not plane_record:
        return None
    key = _plane_key(plane_record)
    if key in ctx.sketch_planes:
        return ctx.sketch_planes[key]
    origin = fdr.list_to_xyz(plane_record.get("origin"))
    x_axis = fdr.list_to_xyz(fdr.unit_vector(plane_record.get("x") or [1, 0, 0]))
    y_axis = plane_record.get("y")
    if not y_axis:
        y_axis = fdr.cross(plane_record.get("normal") or [0, 0, 1], plane_record.get("x") or [1, 0, 0])
    y_axis = fdr.list_to_xyz(fdr.unit_vector(y_axis))
    try:
        plane = DB.Plane.CreateByOriginAndBasis(origin, x_axis, y_axis)
        sketch_plane = DB.SketchPlane.Create(ctx.doc, plane)
    except Exception as error:
        ctx.note("a sketch plane could not be created: {}".format(exception_text(error)))
        sketch_plane = None
    ctx.sketch_planes[key] = sketch_plane
    return sketch_plane


def build_curve(record):
    """A Revit Curve from a curve record; ``None`` when the record cannot be built."""
    kind = safe_text((record or {}).get("type"))
    xyz = fdr.list_to_xyz
    if kind == "Line":
        return DB.Line.CreateBound(xyz(record["start"]), xyz(record["end"]))
    if kind == "Arc":
        start = float(record.get("start", 0.0))
        end = float(record.get("end", 6.283185307179586))
        return DB.Arc.Create(xyz(record["center"]), float(record["radius"]), start, end,
                             xyz(fdr.unit_vector(record["x"])), xyz(fdr.unit_vector(record["y"])))
    if kind == "Ellipse":
        return DB.Ellipse.CreateCurve(
            xyz(record["center"]), float(record["radius_x"]), float(record["radius_y"]),
            xyz(fdr.unit_vector(record["x"])), xyz(fdr.unit_vector(record["y"])),
            float(record.get("start", 0.0)), float(record.get("end", 6.283185307179586)))
    if kind == "NurbSpline":
        return DB.NurbSpline.CreateCurve(
            int(record["degree"]), fdr.double_list(record["knots"]),
            fdr.xyz_list(record["control_points"]), fdr.double_list(record["weights"]))
    if kind == "HermiteSpline":
        return DB.HermiteSpline.Create(fdr.xyz_list(record["points"]),
                                       bool(record.get("periodic", False)))
    return None


def _line_style(curve_element, style_name):
    if not style_name:
        return None
    for style_id in _catch(lambda: list(curve_element.GetLineStyleIds()), []):
        style = curve_element.Document.GetElement(style_id)
        name = safe_text(_catch(lambda: style.GraphicsStyleCategory.Name))
        if name == style_name:
            return style
    return None


def apply_curves(ctx):
    records = ctx.manifest.get("curves") or []
    if not records:
        return
    manager = ctx.manager
    failures = {"count": 0}
    missing_styles = set()

    def work():
        factory = ctx.doc.FamilyCreate
        for record in records:
            sketch_plane = sketch_plane_for(ctx, record.get("plane"))
            curve = _catch(lambda: build_curve(record.get("curve")))
            if sketch_plane is None or curve is None:
                failures["count"] += 1
                continue
            try:
                if record.get("kind") == "symbolic":
                    element = factory.NewSymbolicCurve(curve, sketch_plane)
                else:
                    element = factory.NewModelCurve(curve, sketch_plane)
            except Exception:
                failures["count"] += 1
                continue
            style_name = safe_text(record.get("line_style"))
            if style_name:
                style = _line_style(element, style_name)
                if style is None:
                    ctx.subcategory(style_name)
                    style = _line_style(element, style_name)
                if style is not None:
                    _catch(lambda: setattr(element, "LineStyle", style))
                else:
                    missing_styles.add(style_name)
            raw = record.get("visibility_raw")
            if raw is not None:
                parameter = fdr.find_parameter(element, "GEOM_VISIBILITY_PARAM")
                if parameter is not None:
                    _catch(lambda: parameter.Set(int(raw)))
            visible = record.get("visible") or {}
            visible_param = fdr.find_parameter(element, "IS_VISIBLE_PARAM")
            if visible_param is not None:
                if visible.get("kind") == "parameter":
                    family_parameter = ctx.family_parameter(visible.get("name"))
                    if family_parameter is not None:
                        _catch(lambda: manager.AssociateElementParameterToFamilyParameter(
                            visible_param, family_parameter))
                elif visible.get("kind") == "literal" and not visible.get("value", True):
                    _catch(lambda: visible_param.Set(0))

    ctx.transaction("Families Downgrade lines", work)
    if failures["count"]:
        ctx.note("{} line(s) could not be recreated".format(failures["count"]))
    for style_name in sorted(missing_styles):
        ctx.note("line style '{}' is not available; default style used".format(style_name))


# --------------------------------------------------------------------------
# stage 8: reference planes
# --------------------------------------------------------------------------


def _existing_reference_plane_names(ctx):
    names = set()
    for plane in _catch(lambda: list(DB.FilteredElementCollector(ctx.doc).OfClass(DB.ReferencePlane)), []):
        names.add(safe_text(_catch(lambda: plane.Name)))
    return names


def reference_plane_view(ctx, normal):
    """The view to draw a reference plane in: plan for vertical planes,
    an elevation for horizontal ones, ``None`` for tilted planes."""
    plan_view, elevations = ctx.views_for_planes()
    normal = fdr.unit_vector(normal)
    if abs(normal[2]) < 0.05:
        return plan_view
    if abs(normal[2]) > 0.95:
        return elevations[0] if elevations else None
    return None


def apply_reference_planes(ctx):
    records = ctx.manifest.get("reference_planes") or []
    if not records:
        return
    existing = _existing_reference_plane_names(ctx)
    skipped = []

    def work():
        factory = ctx.doc.FamilyCreate
        for record in records:
            name = safe_text(record.get("name"))
            if not name or name in existing:
                continue
            plane = record.get("plane") or {}
            normal = fdr.unit_vector(plane.get("normal") or [0, 0, 1])
            view = reference_plane_view(ctx, normal)
            if view is None:
                skipped.append(name)
                continue
            bubble = list(record.get("bubble_end") or plane.get("origin") or [0, 0, 0])
            free = list(record.get("free_end") or plane.get("origin") or [0, 0, 0])
            if abs(normal[2]) < 0.05:
                bubble[2] = 0.0
                free[2] = 0.0
            direction = fdr.unit_vector([free[i] - bubble[i] for i in range(3)])
            cut = fdr.unit_vector(fdr.cross(normal, direction))
            try:
                element = factory.NewReferencePlane(fdr.list_to_xyz(bubble), fdr.list_to_xyz(free),
                                                    fdr.list_to_xyz(cut), view)
                element.Name = name
                existing.add(name)
            except Exception:
                skipped.append(name)
                continue
            is_reference = record.get("is_reference")
            if is_reference is not None:
                parameter = fdr.find_parameter(element, "ELEM_REFERENCE_NAME")
                if parameter is not None:
                    _catch(lambda: parameter.Set(int(is_reference)))

    ctx.transaction("Families Downgrade reference planes", work)
    if skipped:
        ctx.note("reference plane(s) not recreated: {}".format(", ".join(skipped)))


# --------------------------------------------------------------------------
# the batch
# --------------------------------------------------------------------------


def _save_family(doc, path):
    options = DB.SaveAsOptions()
    options.OverwriteExistingFile = True
    doc.SaveAs(path, options)


def rebuild_one(app, package, output_path, caps, scratch, templates, options, result):
    """Rebuild one package into ``output_path``; ``(ok, error)``."""
    manifest = package.manifest
    family_record = manifest.get("family") or {}
    template_path, note = fdr.choose_template(templates, family_record, options.template_path)
    result.add_note(note)
    if not template_path:
        return False, "no family template was found in {}".format(
            safe_text(_catch(lambda: app.FamilyTemplatePath)) or "the template folder")
    try:
        doc = app.NewFamilyDocument(template_path)
    except Exception as error:
        return False, "the template could not be opened: {}".format(exception_text(error))
    if doc is None:
        return False, "the template produced no document"
    ctx = RebuildContext(doc, manifest, caps, scratch, result)
    for note in manifest.get("notes") or []:
        result.add_note(note)
    try:
        apply_family_record(ctx)
        apply_subcategories(ctx)
        apply_materials(ctx)
        apply_parameters(ctx)
        apply_types(ctx)
        apply_formulas(ctx)
        apply_geometry(ctx, package.folder)
        apply_connectors(ctx)
        apply_curves(ctx)
        apply_reference_planes(ctx)
        fdr.regenerate(doc)
        _save_family(doc, output_path)
    except Exception as error:
        return False, exception_text(error)
    finally:
        close_family_doc(doc)
    return True, ""


def rebuild_family_packages(app, packages, options, progress=None):
    """Rebuild every usable package into ``options.output_folder``."""
    summary = state.DowngradeSummary(state.MODE_REBUILD)
    packages = [package for package in list(packages or []) if package.is_usable]
    total = len(packages)
    caps = HostCapabilities(app)
    templates = fdr.list_templates(_catch(lambda: app.FamilyTemplatePath))
    if options.template_path:
        templates.setdefault(os.path.basename(options.template_path), options.template_path)
    if not templates:
        summary.add_note("No family templates were found (Revit's family template folder is "
                         "empty or unset); nothing could be rebuilt.")
        for package in packages:
            summary.failed.append(state.DowngradeResult(package.family_name, "", "no family template"))
        return summary
    file_names = state.planned_rfa_filenames(packages)
    scratch = fdr.SharedParameterScratch(app).open()
    if scratch.error:
        summary.add_note("Shared parameters are rebuilt as family parameters: {}".format(scratch.error))
        scratch_for_run = None
    else:
        scratch_for_run = scratch
    try:
        for done, package in enumerate(packages):
            if not keep_going(progress, done, total):
                summary.cancelled = True
                summary.add_note("Cancelled after {} of {} families. Files already written stay "
                                 "on disk.".format(done, total))
                break
            output_path = os.path.join(options.output_folder, file_names[done])
            result = state.DowngradeResult(package.family_name, output_path, "rebuilt")
            try:
                ok, error = rebuild_one(app, package, output_path, caps, scratch_for_run,
                                        templates, options, result)
            except Exception as error:
                ok, error = False, exception_text(error)
            if ok:
                summary.written.append(result)
            else:
                summary.failed.append(state.DowngradeResult(
                    package.family_name, output_path, error, notes=result.notes))
    finally:
        scratch.close()
    return summary
