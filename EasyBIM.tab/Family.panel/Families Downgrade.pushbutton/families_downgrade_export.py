# -*- coding: utf-8 -*-
"""The export half of Families Downgrade: family document -> package folder.

Runs in the *source* Revit. For every chosen family the family document is
opened (``EditFamily`` for project and link families, the open document
itself for an opened ``.rfa``), read, and written out as a package: one
``manifest.json`` with parameters, types, formulas, materials, subcategories,
visibility, connectors, curves and reference planes, plus one geometry file
per attribute group - a scratch 3D view per group with everything else
hidden, exported as SAT (or DWG with ACIS solids). The source document is
never saved: everything happens on a document that is closed without saving
or on a transaction group that is rolled back.
"""

# pylint: disable=import-error,invalid-name,broad-except
import os

from pyrevit import DB

from easybim.compat import exception_text
from easybim.compat import safe_text
from easybim.family_selection_revit import keep_going

import families_downgrade_revit as fdr
import families_downgrade_state as state


# Family-level flags read and written by built-in parameter name. Anything the
# target does not have, or refuses, is reported per family.
FAMILY_FLAG_PARAMETERS = (
    "FAMILY_WORK_PLANE_BASED",
    "FAMILY_ALWAYS_VERTICAL",
    "FAMILY_SHARED",
    "FAMILY_ALLOW_CUT_WITH_VOIDS",
    "ROOM_CALCULATION_POINT",
    "FAMILY_KEEP_TEXT_READABLE",
    "FAMILY_ROUNDCONNECTOR_DIMENSION",
    "FAMILY_CONTENT_PART_TYPE",
    "OMNICLASS_CODE",
    "OMNICLASS_DESCRIPTION",
)

# Element parameters that name a solid's treatment.
MATERIAL_PARAM = "MATERIAL_ID_PARAM"
VISIBLE_PARAM = "IS_VISIBLE_PARAM"
GEOM_VISIBILITY_PARAM = "GEOM_VISIBILITY_PARAM"

# Connector parameters that are handled through properties or that make no
# sense to copy; everything else writable is recorded by built-in name.
CONNECTOR_PARAMS_SKIPPED = frozenset([
    "CONNECTOR_INDEX", "CONNECTOR_REFERENCE_INDEX", "RBS_CONNECTOR_ISPRIMARY",
])


def _catch(work, default=None):
    try:
        return work()
    except Exception:
        return default


# --------------------------------------------------------------------------
# reading a family document
# --------------------------------------------------------------------------


class FamilyReader(object):
    """Reads one family document into a manifest, noting what it drops."""

    def __init__(self, family_doc, manifest):
        self.doc = family_doc
        self.manifest = manifest
        self.family = _catch(lambda: family_doc.OwnerFamily)
        self.manager = _catch(lambda: family_doc.FamilyManager)
        self.family_category = _catch(lambda: self.family.FamilyCategory)
        self._material_names = set()

    def note(self, text):
        state.add_note(self.manifest, text)

    # -- family record ------------------------------------------------------

    def read_family(self):
        family = self.family
        record = self.manifest["family"]
        record["name"] = safe_text(_catch(lambda: family.Name)) or record.get("name", "")
        category = self.family_category
        record["category_builtin"] = fdr.builtin_category_name(category)
        record["category_label"] = safe_text(_catch(lambda: category.Name))
        record["category_type"] = fdr.enum_name(_catch(lambda: category.CategoryType))
        record["placement_type"] = fdr.enum_name(_catch(lambda: family.FamilyPlacementType))
        record["is_in_place"] = bool(_catch(lambda: family.IsInPlace, False))
        hosting = fdr.find_parameter(family, "FAMILY_HOSTING_BEHAVIOR")
        record["hosting_behavior"] = _catch(lambda: int(hosting.AsInteger()), 0) if hosting else 0
        record["hosting_behavior_label"] = safe_text(
            _catch(lambda: hosting.AsValueString())) if hosting else ""
        flags = {}
        for name in FAMILY_FLAG_PARAMETERS:
            parameter = fdr.find_parameter(family, name)
            if parameter is None:
                continue
            flags[name] = fdr.parameter_value_record(parameter)
        record["flags"] = flags
        return record

    def support_reason(self):
        record = self.manifest["family"]
        return state.family_support_reason(
            record.get("category_builtin"), record.get("category_type"),
            record.get("placement_type"), record.get("is_in_place"))

    # -- parameters ---------------------------------------------------------

    def read_parameters(self):
        manager = self.manager
        records = []
        parameters = _catch(lambda: list(manager.GetParameters()), None)
        if parameters is None:
            parameters = _catch(lambda: list(manager.Parameters), [])
        for parameter in parameters:
            record = self._parameter_record(parameter)
            if record is not None:
                records.append(record)
        self.manifest["parameters"] = records
        return records

    def _parameter_record(self, parameter):
        definition = _catch(lambda: parameter.Definition)
        if definition is None:
            return None
        name = safe_text(_catch(lambda: definition.Name))
        record = {
            "name": name,
            "builtin": fdr.builtin_parameter_name(parameter),
            "is_instance": bool(_catch(lambda: parameter.IsInstance, False)),
            "is_shared": bool(_catch(lambda: parameter.IsShared, False)),
            "guid": "",
            "storage_type": fdr.enum_name(_catch(lambda: parameter.StorageType)),
            "spec_type_id": "",
            "spec_property": "",
            "parameter_type": "",
            "group_type_id": "",
            "group_property": "",
            "builtin_group": "",
            "is_reporting": bool(_catch(lambda: parameter.IsReporting, False)),
            "is_read_only": bool(_catch(lambda: parameter.IsReadOnly, False)),
            "user_modifiable": bool(_catch(lambda: parameter.UserModifiable, True)),
            "formula": safe_text(_catch(lambda: parameter.Formula)),
            "is_determined_by_formula": bool(_catch(lambda: parameter.IsDeterminedByFormula, False)),
            "family_type_category": "",
        }
        if record["is_shared"]:
            record["guid"] = safe_text(_catch(lambda: parameter.GUID))

        # Data type: ForgeTypeId on 2022+, the spec id on 2021 (measurable
        # only), the legacy enum name wherever the host still knows it.
        spec = None
        if fdr.has_api(definition, "GetDataType"):
            spec = _catch(lambda: definition.GetDataType())
        elif fdr.has_api(definition, "GetSpecTypeId"):
            spec = _catch(lambda: definition.GetSpecTypeId())
        if spec is not None:
            record["spec_type_id"] = safe_text(_catch(lambda: spec.TypeId))
            record["spec_property"] = fdr.FORGE.spec_property(spec)
        if fdr.has_api(definition, "ParameterType"):
            record["parameter_type"] = fdr.enum_name(_catch(lambda: definition.ParameterType))
            if record["parameter_type"] == "Invalid":
                record["parameter_type"] = ""

        # Group: ForgeTypeId on 2022+, the legacy enum wherever it exists.
        if fdr.has_api(definition, "GetGroupTypeId"):
            group = _catch(lambda: definition.GetGroupTypeId())
            if group is not None:
                record["group_type_id"] = safe_text(_catch(lambda: group.TypeId))
                record["group_property"] = fdr.FORGE.group_property(group)
                if fdr.has_api(DB, "ParameterUtils") and fdr.has_api(
                        DB.ParameterUtils, "GetBuiltInParameterGroup"):
                    legacy = _catch(lambda: DB.ParameterUtils.GetBuiltInParameterGroup(group))
                    if legacy is not None:
                        record["builtin_group"] = fdr.enum_name(legacy)
        if not record["builtin_group"] and fdr.has_api(definition, "ParameterGroup"):
            record["builtin_group"] = fdr.enum_name(_catch(lambda: definition.ParameterGroup))

        # Family type parameters need their category to be recreated at all.
        if (record["spec_property"] == "Reference.FamilyType"
                or record["parameter_type"] == "FamilyType"):
            record["family_type_category"] = self._family_type_category(spec)
        return record

    def _family_type_category(self, spec):
        if spec is None or not fdr.has_api(DB.Category, "GetBuiltInCategory"):
            return ""
        member = _catch(lambda: DB.Category.GetBuiltInCategory(spec))
        text = fdr.enum_name(member)
        return text if text.startswith("OST_") else ""

    # -- types --------------------------------------------------------------

    def read_types(self, only_type=None):
        manager = self.manager
        parameters = _catch(lambda: list(manager.GetParameters()), None)
        if parameters is None:
            parameters = _catch(lambda: list(manager.Parameters), [])
        types = _catch(lambda: list(manager.Types), [])
        if only_type is not None:
            types = [only_type]
        defaults_only = False
        if not types:
            # A family with no named types still has values: they live on the
            # current (default) type and travel under an empty name, which the
            # rebuild writes onto its own default type without creating one.
            current = _catch(lambda: manager.CurrentType)
            types = [current] if current is not None else []
            defaults_only = True
        records = []
        for family_type in types:
            name = "" if defaults_only else safe_text(_catch(lambda: family_type.Name))
            values = {}
            for parameter in parameters:
                param_name = safe_text(_catch(lambda: parameter.Definition.Name))
                if not param_name:
                    continue
                values[param_name] = self._type_value(family_type, parameter)
            records.append({"name": name, "values": values})
        self.manifest["types"] = records
        if defaults_only:
            self.note("the family has no named types; its values travel as the family defaults")
        return records

    def _type_value(self, family_type, parameter):
        try:
            if not family_type.HasValue(parameter):
                return state.value_record("none")
        except Exception:
            pass
        storage = fdr.enum_name(_catch(lambda: parameter.StorageType))
        try:
            if storage == "Double":
                return state.value_record("double", float(family_type.AsDouble(parameter)))
            if storage == "Integer":
                return state.value_record("int", int(family_type.AsInteger(parameter)))
            if storage == "String":
                return state.value_record("string", safe_text(family_type.AsString(parameter)))
            if storage == "ElementId":
                element_id = family_type.AsElementId(parameter)
                if not fdr.is_valid_id(element_id):
                    return state.value_record("none")
                element = self.doc.GetElement(element_id)
                if isinstance(element, DB.Material):
                    self._material_names.add(fdr.element_name(element))
                return state.value_record("element", element_name=fdr.element_name(element),
                                          element_class=fdr.class_name(element))
        except Exception:
            pass
        return state.value_record("none")

    # -- materials and subcategories ---------------------------------------

    def read_materials(self, extra_names=None):
        wanted = set(self._material_names)
        wanted.update(name for name in (extra_names or []) if name)
        records = []
        if not wanted:
            self.manifest["materials"] = records
            return records
        materials = _catch(
            lambda: list(DB.FilteredElementCollector(self.doc).OfClass(DB.Material)), [])
        for material in materials:
            name = fdr.element_name(material)
            if name not in wanted:
                continue
            records.append(self._material_record(material))
        self.manifest["materials"] = records
        return records

    def _material_record(self, material):
        record = {"name": fdr.element_name(material)}
        color = _catch(lambda: material.Color)
        record["color"] = _catch(lambda: [int(color.Red), int(color.Green), int(color.Blue)])
        record["transparency"] = _catch(lambda: int(material.Transparency), 0)
        record["shininess"] = _catch(lambda: int(material.Shininess), 0)
        record["smoothness"] = _catch(lambda: int(material.Smoothness), 0)
        for key, attr in (("surface_foreground_pattern", "SurfaceForegroundPatternId"),
                          ("cut_foreground_pattern", "CutForegroundPatternId")):
            record[key] = self._pattern_name(_catch(lambda: getattr(material, attr)))
        return record

    def _pattern_name(self, pattern_id):
        if not fdr.is_valid_id(pattern_id):
            return ""
        return fdr.element_name(_catch(lambda: self.doc.GetElement(pattern_id)))

    def read_subcategories(self):
        records = []
        category = self.family_category
        subcategories = _catch(lambda: list(category.SubCategories), [])
        for subcategory in subcategories:
            name = safe_text(_catch(lambda: subcategory.Name))
            if not name:
                continue
            color = _catch(lambda: subcategory.LineColor)
            record = {
                "name": name,
                "line_color": _catch(lambda: [int(color.Red), int(color.Green), int(color.Blue)]),
                "projection_weight": _catch(
                    lambda: int(subcategory.GetLineWeight(DB.GraphicsStyleType.Projection))),
                "cut_weight": _catch(
                    lambda: int(subcategory.GetLineWeight(DB.GraphicsStyleType.Cut))),
                "material": fdr.element_name(_catch(lambda: subcategory.Material)),
            }
            if record["material"]:
                self._material_names.add(record["material"])
            records.append(record)
        self.manifest["subcategories"] = records
        return records

    # -- geometry attribute groups ------------------------------------------

    def collect_solid_records(self):
        """Per-element attribute records for every solid the family shows.

        Voids are excluded (their cuts are already baked into the exported
        solids), curves and 2D elements are not solids, and nested family
        instances travel as geometry only.
        """
        records = []
        voids = 0
        nested = 0
        imports = 0
        elements = _catch(
            lambda: list(DB.FilteredElementCollector(self.doc).WhereElementIsNotElementType()), [])
        for element in elements:
            if isinstance(element, DB.GenericForm):
                if not bool(_catch(lambda: element.IsSolid, True)):
                    voids += 1
                    continue
                records.append(self._solid_record(element, "form"))
            elif isinstance(element, DB.FamilyInstance):
                nested += 1
                records.append(self._solid_record(element, "instance"))
            elif isinstance(element, DB.ImportInstance):
                imports += 1
                records.append(self._solid_record(element, "import"))
        if voids:
            self.note("{} void form(s) not carried as forms; their cuts are baked into "
                      "the exported solids".format(voids))
        if nested:
            self.note("{} nested family instance(s) flattened into geometry".format(nested))
        if imports:
            self.note("{} imported CAD instance(s) carried as plain geometry".format(imports))
        return records

    def _solid_record(self, element, kind):
        record = {
            "element_id": fdr.element_id_value(_catch(lambda: element.Id)),
            "kind": kind,
            "material": {"kind": "category"},
            "subcategory": "",
            "visibility": state.visibility_record(),
            "visible": {"kind": "literal", "value": True},
        }
        material = fdr.find_parameter(element, MATERIAL_PARAM)
        if material is not None:
            associated = _catch(lambda: self.manager.GetAssociatedFamilyParameter(material))
            if associated is not None:
                record["material"] = {"kind": "parameter",
                                      "name": safe_text(_catch(lambda: associated.Definition.Name))}
            else:
                value = fdr.parameter_value_record(material)
                if value.get("kind") == "element" and value.get("element_name"):
                    record["material"] = {"kind": "material", "name": value["element_name"]}
                    self._material_names.add(value["element_name"])
        subcategory = _catch(lambda: element.Subcategory)
        if subcategory is not None:
            name = safe_text(_catch(lambda: subcategory.Name))
            family_name = safe_text(_catch(lambda: self.family_category.Name))
            if name and name != family_name:
                record["subcategory"] = name
        if fdr.has_api(element, "GetVisibility"):
            visibility = _catch(lambda: element.GetVisibility())
            if visibility is not None:
                record["visibility"] = state.visibility_record(
                    coarse=_catch(lambda: visibility.IsShownInCoarse, True),
                    medium=_catch(lambda: visibility.IsShownInMedium, True),
                    fine=_catch(lambda: visibility.IsShownInFine, True),
                    plan_rcp_cut=_catch(lambda: visibility.IsShownInPlanRCPCut, True),
                    front_back=_catch(lambda: visibility.IsShownInFrontBack, True),
                    left_right=_catch(lambda: visibility.IsShownInLeftRight, True),
                    top_bottom=_catch(lambda: visibility.IsShownInTopBottom, True),
                    only_when_cut=_catch(lambda: visibility.IsShownOnlyWhenCut, False),
                    raw=self._raw_visibility(element))
        else:
            record["visibility"] = state.visibility_record(raw=self._raw_visibility(element))
        visible = fdr.find_parameter(element, VISIBLE_PARAM)
        if visible is not None:
            associated = _catch(lambda: self.manager.GetAssociatedFamilyParameter(visible))
            if associated is not None:
                record["visible"] = {"kind": "parameter",
                                     "name": safe_text(_catch(lambda: associated.Definition.Name))}
            else:
                record["visible"] = {"kind": "literal",
                                     "value": bool(_catch(lambda: visible.AsInteger(), 1))}
        return record

    def _raw_visibility(self, element):
        parameter = fdr.find_parameter(element, GEOM_VISIBILITY_PARAM)
        if parameter is None:
            return None
        return _catch(lambda: int(parameter.AsInteger()))

    def group_bbox(self, group):
        boxes = []
        for value in group.get("element_ids") or []:
            element = self._element_by_value(value)
            if element is not None:
                boxes.append(fdr.element_bbox_list(element))
        return fdr.union_bbox_lists(boxes)

    def _element_by_value(self, value):
        if value is None:
            return None
        try:
            from easybim.compat import int_to_eid
            return self.doc.GetElement(int_to_eid(int(value), DB.ElementId))
        except Exception:
            return None

    # -- connectors ---------------------------------------------------------

    def read_connectors(self):
        connectors = _catch(
            lambda: list(DB.FilteredElementCollector(self.doc).OfClass(DB.ConnectorElement)), [])
        records = []
        by_id = {}
        for index, connector in enumerate(connectors):
            record = self._connector_record(connector, index)
            records.append(record)
            by_id[record["element_id"]] = index
        for record in records:
            linked = record.pop("_linked_id", None)
            record["linked_to"] = by_id.get(linked) if linked is not None else None
        self.manifest["connectors"] = records
        return records

    def _connector_record(self, connector, index):
        transform = _catch(lambda: connector.CoordinateSystem)
        record = {
            "index": index,
            "element_id": fdr.element_id_value(_catch(lambda: connector.Id)),
            "domain": fdr.enum_name(_catch(lambda: connector.Domain)),
            "system_classification": fdr.enum_name(_catch(lambda: connector.SystemClassification)),
            "shape": fdr.enum_name(_catch(lambda: connector.Shape)),
            "origin": fdr.xyz_to_list(_catch(lambda: connector.Origin)),
            "basis_x": fdr.xyz_to_list(_catch(lambda: transform.BasisX)) if transform else [1, 0, 0],
            "basis_y": fdr.xyz_to_list(_catch(lambda: transform.BasisY)) if transform else [0, 1, 0],
            "basis_z": fdr.xyz_to_list(_catch(lambda: transform.BasisZ)) if transform else [0, 0, 1],
            "radius": _catch(lambda: float(connector.Radius), -1.0),
            "width": _catch(lambda: float(connector.Width), -1.0),
            "height": _catch(lambda: float(connector.Height), -1.0),
            "direction": fdr.enum_name(_catch(lambda: connector.Direction)),
            "is_primary": bool(_catch(lambda: connector.IsPrimary, False)),
            "kind": self._connector_kind(connector),
            "associations": {},
            "values": {},
        }
        linked = _catch(lambda: connector.GetLinkedConnectorElement())
        record["_linked_id"] = fdr.element_id_value(_catch(lambda: linked.Id)) if linked else None
        for parameter in _catch(lambda: list(connector.Parameters), []):
            builtin = fdr.builtin_parameter_name(parameter)
            if not builtin or builtin in CONNECTOR_PARAMS_SKIPPED:
                continue
            associated = _catch(lambda: self.manager.GetAssociatedFamilyParameter(parameter))
            if associated is not None:
                record["associations"][builtin] = safe_text(
                    _catch(lambda: associated.Definition.Name))
                continue
            if bool(_catch(lambda: parameter.IsReadOnly, True)):
                continue
            record["values"][builtin] = fdr.parameter_value_record(parameter)
        return record

    def _connector_kind(self, connector):
        """duct / pipe / electrical / cabletray / conduit.

        Cable tray and conduit connectors share one domain; the family's
        category says which, and the shape decides for the rest.
        """
        domain = fdr.enum_name(_catch(lambda: connector.Domain))
        if domain == state.DOMAIN_HVAC:
            return "duct"
        if domain == state.DOMAIN_PIPING:
            return "pipe"
        if domain == state.DOMAIN_ELECTRICAL:
            return "electrical"
        if domain == state.DOMAIN_CABLE_TRAY_CONDUIT:
            label = safe_text(_catch(lambda: self.family_category.Name)).lower()
            if "cable" in label:
                return "cabletray"
            if "conduit" in label:
                return "conduit"
            shape = fdr.enum_name(_catch(lambda: connector.Shape))
            return "cabletray" if shape == "Rectangular" else "conduit"
        return "unknown"

    # -- curves -------------------------------------------------------------

    def read_curves(self):
        """Symbolic and model lines - never the sketch lines under a form."""
        sketch_curves = self._sketch_curve_keys()
        records = []
        skipped = 0
        curves = _catch(
            lambda: list(DB.FilteredElementCollector(self.doc).OfClass(DB.CurveElement)), [])
        for element in curves:
            if isinstance(element, DB.SymbolicCurve):
                kind = "symbolic"
            elif isinstance(element, DB.ModelCurve):
                kind = "model"
            else:
                continue
            geometry = _catch(lambda: element.GeometryCurve)
            if geometry is None:
                continue
            key = _curve_key(geometry)
            if kind == "model" and key in sketch_curves:
                continue
            curve = curve_record(geometry)
            if curve is None:
                skipped += 1
                continue
            plane = _catch(lambda: element.SketchPlane.GetPlane())
            record = {
                "kind": kind,
                "curve": curve,
                "plane": plane_record(plane),
                "line_style": safe_text(
                    _catch(lambda: element.LineStyle.GraphicsStyleCategory.Name)),
                "visibility_raw": self._raw_visibility(element),
                "visible": {"kind": "literal", "value": True},
            }
            visible = fdr.find_parameter(element, VISIBLE_PARAM)
            if visible is not None:
                associated = _catch(lambda: self.manager.GetAssociatedFamilyParameter(visible))
                if associated is not None:
                    record["visible"] = {"kind": "parameter",
                                         "name": safe_text(_catch(lambda: associated.Definition.Name))}
                else:
                    record["visible"] = {"kind": "literal",
                                         "value": bool(_catch(lambda: visible.AsInteger(), 1))}
            records.append(record)
        if skipped:
            self.note("{} line(s) of a kind that cannot be recreated (helix or "
                      "unbound spline) not carried".format(skipped))
        self.manifest["curves"] = records
        return records

    def _sketch_curve_keys(self):
        """Geometry keys of every curve that belongs to a form's sketch.

        Sketch lines are ModelCurve elements too, and exporting them would
        draw every extrusion profile twice in the rebuilt family.
        """
        keys = set()
        sketches = _catch(lambda: list(DB.FilteredElementCollector(self.doc).OfClass(DB.Sketch)), [])
        for sketch in sketches:
            profile = _catch(lambda: sketch.Profile)
            if profile is None:
                continue
            for curve_array in _catch(lambda: list(profile), []):
                for curve in _catch(lambda: list(curve_array), []):
                    keys.add(_curve_key(curve))
        return keys

    # -- reference planes ---------------------------------------------------

    def read_reference_planes(self):
        records = []
        planes = _catch(
            lambda: list(DB.FilteredElementCollector(self.doc).OfClass(DB.ReferencePlane)), [])
        for plane in planes:
            name = safe_text(_catch(lambda: plane.Name))
            if not name or name.lower().startswith("reference plane"):
                # Unnamed planes read "Reference Plane" and are construction
                # noise; only named references matter to a placed family.
                continue
            geometry = _catch(lambda: plane.GetPlane())
            reference = fdr.find_parameter(plane, "ELEM_REFERENCE_NAME")
            origin = fdr.find_parameter(plane, "DATUM_PLANE_DEFINES_ORIGIN")
            records.append({
                "name": name,
                "plane": plane_record(geometry),
                "bubble_end": fdr.xyz_to_list(_catch(lambda: plane.BubbleEnd)),
                "free_end": fdr.xyz_to_list(_catch(lambda: plane.FreeEnd)),
                "is_reference": _catch(lambda: int(reference.AsInteger())) if reference else None,
                "defines_origin": bool(_catch(lambda: origin.AsInteger(), 0)) if origin else False,
            })
        self.manifest["reference_planes"] = records
        return records


# --------------------------------------------------------------------------
# curve and plane records
# --------------------------------------------------------------------------

_TWO_PI = 6.283185307179586


def _curve_key(curve):
    """A cheap identity for a curve: its ends and midpoint, rounded."""
    try:
        points = [curve.GetEndPoint(0), curve.GetEndPoint(1), curve.Evaluate(0.5, True)]
        return tuple(round(v, 5) for point in points for v in (point.X, point.Y, point.Z))
    except Exception:
        try:
            point = curve.Evaluate(0.5, True)
            return (round(point.X, 5), round(point.Y, 5), round(point.Z, 5))
        except Exception:
            return None


def plane_record(plane):
    if plane is None:
        return None
    return {
        "origin": fdr.xyz_to_list(_catch(lambda: plane.Origin)),
        "normal": fdr.xyz_to_list(_catch(lambda: plane.Normal)),
        "x": fdr.xyz_to_list(_catch(lambda: plane.XVec)),
        "y": fdr.xyz_to_list(_catch(lambda: plane.YVec)),
    }


def curve_record(curve):
    """A JSON record for a Line/Arc/Ellipse/NurbSpline/HermiteSpline; else ``None``."""
    bound = bool(_catch(lambda: curve.IsBound, True))
    if isinstance(curve, DB.Line):
        if not bound:
            return None
        return {"type": "Line", "start": fdr.xyz_to_list(curve.GetEndPoint(0)),
                "end": fdr.xyz_to_list(curve.GetEndPoint(1))}
    if isinstance(curve, DB.Arc):
        record = {"type": "Arc", "center": fdr.xyz_to_list(curve.Center),
                  "radius": float(curve.Radius),
                  "x": fdr.xyz_to_list(curve.XDirection), "y": fdr.xyz_to_list(curve.YDirection),
                  "normal": fdr.xyz_to_list(curve.Normal)}
        if bound:
            record["start"] = float(curve.GetEndParameter(0))
            record["end"] = float(curve.GetEndParameter(1))
        else:
            record["start"] = 0.0
            record["end"] = _TWO_PI
        return record
    if isinstance(curve, DB.Ellipse):
        record = {"type": "Ellipse", "center": fdr.xyz_to_list(curve.Center),
                  "radius_x": float(curve.RadiusX), "radius_y": float(curve.RadiusY),
                  "x": fdr.xyz_to_list(curve.XDirection), "y": fdr.xyz_to_list(curve.YDirection)}
        if bound:
            record["start"] = float(curve.GetEndParameter(0))
            record["end"] = float(curve.GetEndParameter(1))
        else:
            record["start"] = 0.0
            record["end"] = _TWO_PI
        return record
    if isinstance(curve, DB.NurbSpline):
        return {"type": "NurbSpline", "degree": int(curve.Degree),
                "knots": [float(k) for k in curve.Knots],
                "control_points": [fdr.xyz_to_list(p) for p in curve.CtrlPoints],
                "weights": [float(w) for w in curve.Weights],
                "rational": bool(_catch(lambda: curve.isRational, False))}
    if isinstance(curve, DB.HermiteSpline):
        return {"type": "HermiteSpline",
                "points": [fdr.xyz_to_list(p) for p in curve.ControlPoints],
                "periodic": bool(_catch(lambda: curve.IsPeriodic, False))}
    return None


# --------------------------------------------------------------------------
# geometry export
# --------------------------------------------------------------------------


def _three_d_view_type_id(doc):
    types = _catch(lambda: list(DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)), [])
    for view_type in types:
        if _catch(lambda: view_type.ViewFamily) == DB.ViewFamily.ThreeDimensional:
            return view_type.Id
    return None


def _visible_element_ids(doc, view):
    """What the view would export: everything visible in it, by id."""
    try:
        collector = DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
        return [element.Id for element in collector]
    except Exception:
        return []


def _hideable(element, view):
    try:
        return bool(element.CanBeHidden(view))
    except Exception:
        return False


def _prepare_scratch_view(doc, view_type_id, group_ids, detail_level_name):
    """Create the scratch 3D view showing only ``group_ids``; the view or ``None``."""
    holder = {"view": None}

    def work():
        view = DB.View3D.CreateIsometric(doc, view_type_id)
        try:
            view.ViewTemplateId = DB.ElementId.InvalidElementId
        except Exception:
            pass
        try:
            view.DetailLevel = getattr(DB.ViewDetailLevel, detail_level_name)
        except Exception:
            pass
        fdr.regenerate(doc)
        wanted = set(group_ids)
        to_hide = []
        for element_id in _visible_element_ids(doc, view):
            if fdr.element_id_value(element_id) in wanted or element_id == view.Id:
                continue
            element = doc.GetElement(element_id)
            if element is not None and _hideable(element, view):
                to_hide.append(element_id)
        if to_hide:
            try:
                view.HideElements(fdr.id_list(to_hide))
            except Exception:
                # CanBeHidden is not the whole story (group members, for
                # one); hide what can be hidden one at a time.
                for element_id in to_hide:
                    try:
                        view.HideElements(fdr.id_list([element_id]))
                    except Exception:
                        pass
        holder["view"] = view

    ok, error = fdr.run_transaction(doc, "Families Downgrade scratch view", work)
    if not ok:
        return None, error
    return holder["view"], ""


def _delete_view(doc, view):
    if view is None:
        return

    def work():
        doc.Delete(view.Id)

    fdr.run_transaction(doc, "Families Downgrade drop scratch view", work)


def _export_options(geometry_format):
    if geometry_format == state.GEOMETRY_DWG:
        options = DB.DWGExportOptions()
        try:
            options.ExportOfSolids = DB.SolidGeometry.ACIS
        except Exception:
            pass
        try:
            options.FileVersion = DB.ACADVersion.R2007
        except Exception:
            pass
    else:
        options = DB.SATExportOptions()
    for name in ("HideReferencePlane", "HideScopeBox", "HideUnreferenceViewTags"):
        try:
            setattr(options, name, True)
        except Exception:
            pass
    return options


def _snapshot(folder):
    try:
        return set(os.listdir(folder))
    except Exception:
        return set()


def _new_files(folder, before, extension):
    found = []
    for name in _snapshot(folder) - before:
        if name.lower().endswith("." + extension):
            found.append(os.path.join(folder, name))
    return sorted(found)


def sat_has_bodies(path):
    """True when a SAT file carries at least one body record.

    Revit writes text SAT; an export of a view with nothing visible still
    produces a header. Only the SAT format can be checked this way - a DWG is
    binary and is left to the rebuild to count.
    """
    try:
        with open(path, "rb") as handle:
            content = handle.read()
    except Exception:
        return False
    for line in content.splitlines():
        if line.startswith(b"body "):
            return True
    return False


def export_group_file(doc, folder, group, geometry_format, view_type_id):
    """Write one geometry file for a group; ``(file_name, note)``.

    Tries the group's detail levels in order and stops at the first export
    that carries bodies (SAT) or that Revit reports as written (DWG). Returns
    ``("", reason)`` when nothing could be exported.
    """
    extension = state.GEOMETRY_DWG if geometry_format == state.GEOMETRY_DWG else state.GEOMETRY_SAT
    target_name = state.geometry_file_name(group["index"], geometry_format)
    target_path = os.path.join(folder, target_name)
    last_error = "nothing visible to export"
    for level in group.get("detail_levels") or state.DETAIL_LEVELS:
        view, error = _prepare_scratch_view(doc, view_type_id, group.get("element_ids") or [],
                                            level)
        if view is None:
            last_error = error or "the scratch view could not be created"
            continue
        before = _snapshot(folder)
        try:
            fdr.regenerate(doc)
            exported = doc.Export(folder, "fdg_g{:02d}".format(int(group["index"])),
                                  fdr.id_list([view.Id]), _export_options(geometry_format))
        except Exception as error:
            exported = False
            last_error = "Export failed: {}".format(exception_text(error))
        finally:
            _delete_view(doc, view)
        files = _new_files(folder, before, extension)
        if not files:
            if exported is not False and last_error == "nothing visible to export":
                last_error = "Revit wrote no file at {} detail".format(level.lower())
            continue
        source = files[0]
        for extra in files[1:]:
            _catch(lambda: os.remove(extra))
        if extension == state.GEOMETRY_SAT and not sat_has_bodies(source):
            _catch(lambda: os.remove(source))
            last_error = "nothing visible at {} detail".format(level.lower())
            continue
        try:
            if os.path.isfile(target_path):
                os.remove(target_path)
            os.rename(source, target_path)
        except Exception as error:
            return "", "could not place the geometry file: {}".format(exception_text(error))
        return target_name, ""
    return "", last_error


# --------------------------------------------------------------------------
# the batch
# --------------------------------------------------------------------------


def _clear_owned_files(folder):
    for path in state.owned_package_files(folder):
        _catch(lambda: os.remove(path))


def _discard_partial(folder):
    """Undo a half-written package: our files go, the folder only if empty.

    Never ``rmtree``: an existing package folder may hold the user's own
    files next to ours, and a failed export must not take those with it.
    """
    _clear_owned_files(folder)
    try:
        if os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
    except Exception:
        pass


def _switch_type(family_doc, manager, family_type):
    def work():
        manager.CurrentType = family_type
    ok, error = fdr.run_transaction(family_doc, "Families Downgrade type switch", work)
    if ok:
        fdr.regenerate(family_doc)
    return ok, error


def _write_package(family_doc, family_option, folder, options, exported, only_type=None):
    """Read one family (at its current type) into ``folder``; the manifest and its notes."""
    manifest = state.new_manifest(safe_text(getattr(family_option, "name", "")), exported)
    reader = FamilyReader(family_doc, manifest)
    reader.read_family()
    reason = reader.support_reason()
    if reason:
        return None, reason

    if only_type is not None:
        state.add_note(manifest, "one family per type: this package holds type '{}'".format(
            safe_text(_catch(lambda: only_type.Name))))
    manifest["geometry"]["source_type"] = safe_text(_catch(lambda: reader.manager.CurrentType.Name))
    manifest["geometry"]["format"] = options.geometry_format

    reader.read_parameters()
    reader.read_types(only_type=only_type)
    reader.read_subcategories()
    solid_records = reader.collect_solid_records()
    groups = state.group_elements(solid_records)
    reader.read_materials()
    reader.read_connectors()
    reader.read_curves()
    reader.read_reference_planes()

    if not os.path.isdir(folder):
        os.makedirs(folder)
    _clear_owned_files(folder)

    view_type_id = _three_d_view_type_id(family_doc)
    if groups and view_type_id is None:
        state.add_note(manifest, "no 3D view type in the family: geometry not exported")
        groups = []
    for group in groups:
        group["bbox"] = reader.group_bbox(group)
        group["solid_count"] = len(group.get("element_ids") or [])
        file_name, note = export_group_file(family_doc, folder, group, options.geometry_format,
                                            view_type_id)
        group["file"] = file_name
        if not file_name:
            state.add_note(manifest, "geometry group {}: {}".format(group["index"], note))
    manifest["geometry"]["groups"] = [
        {key: value for key, value in group.items() if key != "key"} for group in groups]
    if not groups:
        state.add_note(manifest, "the family has no solid geometry to carry")

    state.write_manifest(folder, manifest)
    return manifest, ""


def export_family_packages(source_doc, family_options, options, progress=None, app=None):
    """Write one package per family (or per type) into ``options.folder``."""
    summary = state.DowngradeSummary(state.MODE_EXPORT)
    families = list(family_options or [])
    total = len(families)
    used_paths = set()
    index_cache = {}
    exported = {
        "revit_version": fdr.host_version(app) if app else "",
        "revit_build": fdr.host_build(app) if app else "",
        "source_document": safe_text(_catch(lambda: source_doc.Title)),
        "geometry_format": options.geometry_format,
    }

    for done, family_option in enumerate(families):
        if not keep_going(progress, done, total):
            summary.cancelled = True
            summary.add_note("Cancelled after {} of {} families. Packages already written "
                             "stay on disk.".format(done, total))
            break
        family_name = safe_text(getattr(family_option, "name", ""))
        session, reason = fdr.open_family_session(family_option, source_doc, index_cache)
        if session is None:
            summary.skipped.append(state.DowngradeResult(family_name, "", reason))
            continue
        try:
            _export_one(session.document, family_option, options, exported, used_paths,
                        summary, progress, done, total)
        except Exception as error:
            summary.failed.append(state.DowngradeResult(
                family_name, "", "export failed: {}".format(exception_text(error))))
        finally:
            session.close()
    return summary


def _export_one(family_doc, family_option, options, exported, used_paths, summary,
                progress, done, total):
    family_name = safe_text(getattr(family_option, "name", ""))
    exported = dict(exported)
    exported["source_kind"] = safe_text(getattr(family_option, "source_kind", ""))
    manager = _catch(lambda: family_doc.FamilyManager)
    types = []
    if options.split_types and manager is not None:
        types = sorted(_catch(lambda: list(manager.Types), []),
                       key=lambda family_type: safe_text(_catch(lambda: family_type.Name)).lower())
    if not types:
        types = [None]

    for family_type in types:
        if family_type is not None and not keep_going(progress, done, total):
            summary.cancelled = True
            summary.add_note("Cancelled inside '{}'.".format(family_name))
            return
        type_name = safe_text(_catch(lambda: family_type.Name)) if family_type is not None else None
        folder = state.build_unique_package_folder(options.folder, family_name, used_paths,
                                                   type_name)
        label = family_name if type_name is None else "{} - {}".format(family_name, type_name)
        if family_type is not None:
            ok, error = _switch_type(family_doc, manager, family_type)
            if not ok:
                summary.failed.append(state.DowngradeResult(
                    label, folder, "type could not be made current: {}".format(error)))
                continue
        try:
            manifest, reason = _write_package(family_doc, family_option, folder, options,
                                              exported, only_type=family_type)
        except Exception as error:
            _discard_partial(folder)
            summary.failed.append(state.DowngradeResult(
                label, folder, "export failed: {}".format(exception_text(error))))
            continue
        if manifest is None:
            _discard_partial(folder)
            summary.skipped.append(state.DowngradeResult(label, "", reason))
            continue
        summary.written.append(state.DowngradeResult(
            label, folder, "package written", notes=manifest.get("notes")))
