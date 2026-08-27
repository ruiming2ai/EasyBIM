# -*- coding: utf-8 -*-
"""Pure helpers for the Families Downgrade command.

No Revit imports here. This module owns the package format (the manifest a
newer Revit writes and an older one reads back), the cross-version mapping
tables for parameter data types, parameter groups, categories and MEP system
types, the attribute-group signature that decides how many geometry files a
family needs, package discovery, planned file names, and every piece of
report text - so all of it can be driven by the test suite without an engine.

The one rule behind every mapping helper: the ForgeTypeId *string* is the
stable key across Revit releases (2021 already has ``SpecTypeId`` and
``UnitUtils.GetUnitType``); legacy enum names are reached by normalising
names, with the handful of spelling quirks Autodesk left behind spelled out
in a table. Nothing is guessed silently - every fallback returns a note.
"""

import io
import json
import os
import re

from easybim.family_selection_state import INVALID_FILENAME_PATTERN
from easybim.family_selection_state import SUMMARY_DISPLAY_LIMIT
from easybim.family_selection_state import sanitize_export_filename


PACKAGE_FORMAT = "easybim-families-downgrade"
SCHEMA_VERSION = 1
PACKAGE_SUFFIX = ".downgrade"
MANIFEST_NAME = "manifest.json"
REPORT_FILENAME = "families_downgrade_report.txt"

GEOMETRY_SAT = "sat"
GEOMETRY_DWG = "dwg"
GEOMETRY_FORMATS = (GEOMETRY_SAT, GEOMETRY_DWG)

MODE_EXPORT = "export"
MODE_REBUILD = "rebuild"
# One run that packages families here and rebuilds them in another Revit.
MODE_DOWNGRADE = "downgrade"

FEET_TO_MM = 304.8

# The order the rebuild tries import units in when the file's own unit header
# gave the wrong size. Foot first because that is what Revit's own exporters
# write; the rest cover a header another tool may have rewritten.
IMPORT_UNIT_CANDIDATES = ("Foot", "Inch", "Millimeter", "Meter", "Centimeter",
                          "Decimeter")
SIZE_MATCH_TOLERANCE = 1e-3
OFFSET_TOLERANCE_FEET = 1e-4

# --------------------------------------------------------------------------
# small text helpers
# --------------------------------------------------------------------------


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        try:
            return value.ToString()
        except Exception:
            return ""


def normalize_enum_name(name, strip_prefixes=("PG_", "UT_", "OST_")):
    """``PG_IDENTITY_DATA`` and ``IdentityData`` both become ``identitydata``.

    This is the whole trick behind the legacy-enum bridges: Autodesk generated
    the ForgeTypeId property names from the enum member names, so once
    prefixes, underscores and case are gone the two spellings coincide for all
    but a handful of members, and those are in the quirk tables below.
    """
    name = _safe_text(name).strip()
    for prefix in strip_prefixes:
        if name.upper().startswith(prefix):
            name = name[len(prefix):]
            break
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _match_normalized(candidate, available_names, strip_prefixes=("PG_", "UT_", "OST_")):
    wanted = normalize_enum_name(candidate, strip_prefixes)
    if not wanted:
        return None
    for name in available_names or []:
        if normalize_enum_name(name, strip_prefixes) == wanted:
            return name
    return None


def feet_to_mm(value):
    try:
        return float(value) * FEET_TO_MM
    except Exception:
        return 0.0


def format_mm(value_feet):
    return "{:.1f} mm".format(feet_to_mm(value_feet))


# --------------------------------------------------------------------------
# parameter groups: GroupTypeId property name  <->  BuiltInParameterGroup
# --------------------------------------------------------------------------

# Where the normalised names do not coincide. Verified against the 2021
# RevitAPI.dll identifiers and the 2026 GroupTypeId properties: everything
# else that exists on both sides matches by normalisation alone.
GROUP_NAME_QUIRKS = {
    "CurtainGridn1": "PG_CURTAIN_GRID_1",
    "CurtainGridn2": "PG_CURTAIN_GRID_2",
    "CurtainMullionn1": "PG_CURTAIN_MULLION_1",
    "CurtainMullionn2": "PG_CURTAIN_MULLION_2",
    "Profilen1": "PG_PROFILE_1",
    "Profilen2": "PG_PROFILE_2",
    "Termination": "PG_TERMINTATION",
}
OTHER_GROUP = "INVALID"


def builtin_group_for(parameter, available_group_names):
    """The ``BuiltInParameterGroup`` member name for a parameter record.

    ``available_group_names`` is what the host actually has (``Enum.GetNames``
    on the running Revit). Returns ``(name, note)``; the note says when the
    parameter had to land in Other because its group is newer than the host.
    """
    available = list(available_group_names or [])
    recorded = _safe_text(parameter.get("builtin_group"))
    if recorded and recorded in available:
        return recorded, None

    group_property = _safe_text(parameter.get("group_property"))
    if group_property:
        quirk = GROUP_NAME_QUIRKS.get(group_property)
        if quirk and quirk in available:
            return quirk, None
        matched = _match_normalized(group_property, available)
        if matched:
            return matched, None

    group_id = _safe_text(parameter.get("group_type_id"))
    if not group_property and not group_id and not recorded:
        return OTHER_GROUP, None

    label = group_property or group_id or recorded
    return OTHER_GROUP, (
        "parameter '{}': group '{}' does not exist in this Revit; placed under "
        "Other".format(_safe_text(parameter.get("name")), label))


# --------------------------------------------------------------------------
# parameter data types: ForgeTypeId / SpecTypeId property  <->  ParameterType
# --------------------------------------------------------------------------

# The non-measurable specs, by the SpecTypeId property that names them in
# 2022+. Measurable specs never need this table: 2021 has SpecTypeId for all
# of them and UnitUtils.GetUnitType names the UnitType, whose normalised name
# is the ParameterType name.
NON_MEASURABLE_PARAMETER_TYPES = {
    "String.Text": "Text",
    "String.Url": "URL",
    "String.MultilineText": "MultilineText",
    "Int.Integer": "Integer",
    "Int.NumberOfPoles": "NumberOfPoles",
    "Boolean.YesNo": "YesNo",
    "Reference.Material": "Material",
    "Reference.Image": "Image",
    "Reference.LoadClassification": "LoadClassification",
    "Reference.FamilyType": "FamilyType",
}
# Data types that exist in newer releases and have no legacy equivalent at
# all; a parameter carrying one is created by storage type and said so.
UNSUPPORTED_SPEC_PROPERTIES = ("Reference.FillPattern",)

STORAGE_TYPE_FALLBACKS = {
    "Double": "Number",
    "Integer": "Integer",
    "String": "Text",
}


def parameter_type_for(parameter, available_parameter_types, unit_type_name=None):
    """The legacy ``ParameterType`` member for a parameter record.

    The ladder, in order: the legacy name the exporter recorded (a 2021/2022
    host knew it); the ``UnitType`` the host resolved from the ForgeTypeId
    string (measurable specs); the non-measurable table; the SpecTypeId
    property name itself; the storage type. Returns ``(name, note)`` where
    ``note`` explains any rung below the first two, or ``(None, note)`` when
    nothing fits (an ElementId of an unknown kind).
    """
    available = list(available_parameter_types or [])
    name = _safe_text(parameter.get("name"))

    recorded = _safe_text(parameter.get("parameter_type"))
    if recorded and recorded in available:
        return recorded, None

    unit_type_name = _safe_text(unit_type_name)
    if unit_type_name:
        matched = _match_normalized(unit_type_name, available)
        if matched:
            return matched, None

    spec_property = _safe_text(parameter.get("spec_property"))
    if spec_property in NON_MEASURABLE_PARAMETER_TYPES:
        candidate = NON_MEASURABLE_PARAMETER_TYPES[spec_property]
        if candidate in available:
            return candidate, None

    if spec_property and spec_property not in UNSUPPORTED_SPEC_PROPERTIES:
        matched = _match_normalized(spec_property, available)
        if matched:
            return matched, None

    storage_type = _safe_text(parameter.get("storage_type"))
    fallback = STORAGE_TYPE_FALLBACKS.get(storage_type)
    label = spec_property or _safe_text(parameter.get("spec_type_id")) or recorded or storage_type
    if fallback and fallback in available:
        return fallback, (
            "parameter '{}': data type '{}' does not exist in this Revit; "
            "created as {} (its values are unitless here)".format(name, label, fallback))
    return None, (
        "parameter '{}': data type '{}' cannot be created in this Revit; "
        "parameter skipped".format(name, label))


# --------------------------------------------------------------------------
# categories that a target release does not have
# --------------------------------------------------------------------------

# Revit 2022 and later added model categories that 2021 has no member for
# (verified against the 2021 assembly). Each lands in the category people
# used before the new one existed; the swap is always named in the report.
CATEGORY_FALLBACKS = {
    "OST_FoodServiceEquipment": "OST_SpecialityEquipment",
    "OST_MedicalEquipment": "OST_SpecialityEquipment",
    "OST_FireProtection": "OST_SpecialityEquipment",
    "OST_Signage": "OST_SpecialityEquipment",
    "OST_Hardscape": "OST_Site",
    "OST_TemporaryStructure": "OST_GenericModel",
    "OST_VerticalCirculation": "OST_GenericModel",
    "OST_AudioVisualDevices": "OST_CommunicationDevices",
    "OST_MechanicalControlDevices": "OST_MechanicalEquipment",
    "OST_PlumbingEquipment": "OST_PlumbingFixtures",
}
GENERIC_MODEL_CATEGORY = "OST_GenericModel"


def category_for(builtin_category_name, available_category_names):
    """``(OST_ name to use, note)`` for the target Revit."""
    available = list(available_category_names or [])
    wanted = _safe_text(builtin_category_name)
    if wanted and wanted in available:
        return wanted, None
    fallback = CATEGORY_FALLBACKS.get(wanted)
    if fallback and fallback in available:
        return fallback, (
            "category {} does not exist in this Revit; rebuilt as {}".format(
                wanted, fallback))
    if GENERIC_MODEL_CATEGORY in available or not available:
        return GENERIC_MODEL_CATEGORY, (
            "category '{}' does not exist in this Revit; rebuilt as Generic "
            "Models".format(wanted or "(unknown)"))
    return None, "category '{}' cannot be resolved in this Revit".format(wanted)


# Categories whose families are 2D, view-based or otherwise not made of
# solids a rebuild could carry. Kept as names because the export side reads
# them off ``Category.BuiltInCategory``.
UNSUPPORTED_CATEGORIES = frozenset([
    "OST_DetailComponents",
    "OST_ProfileFamilies",
    "OST_GenericAnnotation",
    "OST_TitleBlocks",
    "OST_Mass",
])
UNSUPPORTED_PLACEMENT_TYPES = frozenset([
    "ViewBased",
    "CurveBasedDetail",
    "Adaptive",
    "Invalid",
])


def family_support_reason(category_builtin, category_type, placement_type,
                          is_in_place=False):
    """``None`` when the family can be packaged, else the sentence saying why not."""
    if bool(is_in_place):
        return "in-place families have no family document to package"
    category_type = _safe_text(category_type)
    if category_type and category_type != "Model":
        return "{} families are 2D and are not carried".format(
            category_type.lower())
    category_builtin = _safe_text(category_builtin)
    if category_builtin in UNSUPPORTED_CATEGORIES:
        return "{} families are not carried".format(
            category_builtin.replace("OST_", ""))
    placement_type = _safe_text(placement_type)
    if placement_type in UNSUPPORTED_PLACEMENT_TYPES:
        return "{} families are not carried".format(placement_type or "this kind of")
    return None


# --------------------------------------------------------------------------
# MEP system types
# --------------------------------------------------------------------------

DOMAIN_HVAC = "DomainHvac"
DOMAIN_PIPING = "DomainPiping"
DOMAIN_ELECTRICAL = "DomainElectrical"
DOMAIN_CABLE_TRAY_CONDUIT = "DomainCableTrayConduit"

SYSTEM_TYPE_QUIRKS = {
    "DataCircuit": "Data",
    "UndefinedSystemClassification": "UndefinedSystemType",
}


def system_type_name_for(classification_name, available_system_type_names):
    """Map an ``MEPSystemClassification`` name onto a domain's system type enum.

    ``Duct/Pipe/ElectricalSystemType`` reuse the classification names apart
    from ``DataCircuit`` -> ``Data`` and the two undefined spellings.
    """
    available = list(available_system_type_names or [])
    wanted = _safe_text(classification_name)
    if wanted in available:
        return wanted, None
    quirk = SYSTEM_TYPE_QUIRKS.get(wanted)
    if quirk and quirk in available:
        return quirk, None
    matched = _match_normalized(wanted, available, strip_prefixes=())
    if matched:
        return matched, None
    for fallback in ("UndefinedSystemType", "Global", "Fitting"):
        if fallback in available:
            return fallback, (
                "connector system '{}' does not exist in this Revit; created "
                "as {}".format(wanted, fallback))
    return None, "connector system '{}' cannot be created in this Revit".format(wanted)


# --------------------------------------------------------------------------
# geometry attribute groups
# --------------------------------------------------------------------------

DETAIL_LEVELS = ("Fine", "Medium", "Coarse")


def visibility_record(coarse=True, medium=True, fine=True, plan_rcp_cut=True,
                      front_back=True, left_right=True, top_bottom=True,
                      only_when_cut=False, raw=None):
    return {
        "coarse": bool(coarse),
        "medium": bool(medium),
        "fine": bool(fine),
        "plan_rcp_cut": bool(plan_rcp_cut),
        "front_back": bool(front_back),
        "left_right": bool(left_right),
        "top_bottom": bool(top_bottom),
        "only_when_cut": bool(only_when_cut),
        "raw": raw,
    }


def detail_levels_to_try(visibility):
    """Preferred detail level first, then the other two as retries.

    A form shown only in Coarse is invisible in a Fine view, so exporting at
    Fine would write nothing for it. Start at the finest level the form is
    shown in and keep the others as the retry order for an empty export.
    """
    visibility = visibility or {}
    order = []
    if visibility.get("fine", True):
        order.append("Fine")
    if visibility.get("medium", True):
        order.append("Medium")
    if visibility.get("coarse", True):
        order.append("Coarse")
    for level in DETAIL_LEVELS:
        if level not in order:
            order.append(level)
    return order


def geometry_group_key(material, subcategory, visibility, visible):
    """The signature two solids must share to travel in one geometry file.

    ``material`` and ``visible`` are the small records the exporter builds
    (``kind`` + ``name``/``value``); every attribute the rebuild sets per
    solid is in the key, so a group is exactly "same treatment afterwards".
    """
    material = material or {}
    visible = visible or {}
    visibility = visibility or {}
    return "|".join([
        _safe_text(material.get("kind")),
        _safe_text(material.get("name")),
        _safe_text(subcategory),
        ",".join(
            "1" if visibility.get(flag, True) else "0"
            for flag in ("coarse", "medium", "fine", "plan_rcp_cut",
                         "front_back", "left_right", "top_bottom",
                         "only_when_cut")),
        _safe_text(visible.get("kind")),
        _safe_text(visible.get("name")),
        _safe_text(visible.get("value")),
    ])


def group_elements(records):
    """``[{group record}]`` from per-element attribute records, in first-seen order.

    Each input record carries ``element_id`` plus ``material``, ``subcategory``,
    ``visibility`` and ``visible``; the output merges the ids of every element
    sharing one signature.
    """
    groups = []
    by_key = {}
    for record in records or []:
        key = geometry_group_key(
            record.get("material"), record.get("subcategory"),
            record.get("visibility"), record.get("visible"))
        group = by_key.get(key)
        if group is None:
            group = {
                "index": len(groups) + 1,
                "key": key,
                "element_ids": [],
                "material": record.get("material") or {"kind": "category"},
                "subcategory": _safe_text(record.get("subcategory")),
                "visibility": record.get("visibility") or visibility_record(),
                "visible": record.get("visible") or {"kind": "literal", "value": True},
                "detail_levels": detail_levels_to_try(record.get("visibility")),
            }
            by_key[key] = group
            groups.append(group)
        group["element_ids"].append(record.get("element_id"))
    return groups


def geometry_file_name(group_index, geometry_format=GEOMETRY_SAT):
    extension = GEOMETRY_DWG if geometry_format == GEOMETRY_DWG else GEOMETRY_SAT
    return "g{:02d}.{}".format(int(group_index), extension)


# --------------------------------------------------------------------------
# bounding boxes: the import self-check
# --------------------------------------------------------------------------


def bbox_size(bbox):
    """``(dx, dy, dz)`` of a ``[[minx,miny,minz],[maxx,maxy,maxz]]`` box."""
    try:
        low, high = bbox[0], bbox[1]
        return tuple(float(high[i]) - float(low[i]) for i in range(3))
    except Exception:
        return (0.0, 0.0, 0.0)


def bbox_center(bbox):
    try:
        low, high = bbox[0], bbox[1]
        return tuple((float(high[i]) + float(low[i])) / 2.0 for i in range(3))
    except Exception:
        return (0.0, 0.0, 0.0)


def size_ratio(recorded_bbox, imported_bbox):
    """imported / recorded along the largest recorded extent; ``None`` if degenerate."""
    recorded = bbox_size(recorded_bbox)
    imported = bbox_size(imported_bbox)
    axis = max(range(3), key=lambda i: recorded[i])
    if recorded[axis] <= 1e-9:
        return None
    return imported[axis] / recorded[axis]


def size_matches(recorded_bbox, imported_bbox, tolerance=SIZE_MATCH_TOLERANCE):
    ratio = size_ratio(recorded_bbox, imported_bbox)
    if ratio is None:
        return True
    return abs(ratio - 1.0) <= tolerance


def bbox_offset(recorded_bbox, imported_bbox):
    """The move that puts the imported box's centre on the recorded one."""
    recorded = bbox_center(recorded_bbox)
    imported = bbox_center(imported_bbox)
    return tuple(recorded[i] - imported[i] for i in range(3))


def offset_is_significant(offset, tolerance=OFFSET_TOLERANCE_FEET):
    return any(abs(component) > tolerance for component in offset or ())


# --------------------------------------------------------------------------
# connectors: which face
# --------------------------------------------------------------------------


def dot(a, b):
    return sum(float(a[i]) * float(b[i]) for i in range(3))


def subtract(a, b):
    return tuple(float(a[i]) - float(b[i]) for i in range(3))


def length(vector):
    return dot(vector, vector) ** 0.5


def face_match_score(connector_origin, connector_normal, face_origin, face_normal,
                     origin_inside_face, angle_tolerance=0.01, plane_tolerance=1e-4):
    """Rank a planar face as the home of a connector; ``None`` when it cannot be.

    0 is an exact home (same plane, same outward normal, origin inside the
    face); a positive score is a coplanar face the origin lies outside of,
    ranked by how far. Anything on another plane is not a candidate.
    """
    if abs(dot(connector_normal, face_normal) - 1.0) > angle_tolerance:
        return None
    distance = abs(dot(subtract(connector_origin, face_origin), face_normal))
    if distance > plane_tolerance:
        return None
    if origin_inside_face:
        return 0.0
    return 1.0


def connector_angle(recorded_x, created_x, normal):
    """The signed rotation about ``normal`` taking ``created_x`` onto ``recorded_x``.

    A rectangular or oval connector created by the API gets an X axis Revit
    picks; ``CONNECTOR_ANGLE`` rotates it, and this is the angle to set.
    """
    import math
    cross = (
        float(created_x[1]) * float(recorded_x[2]) - float(created_x[2]) * float(recorded_x[1]),
        float(created_x[2]) * float(recorded_x[0]) - float(created_x[0]) * float(recorded_x[2]),
        float(created_x[0]) * float(recorded_x[1]) - float(created_x[1]) * float(recorded_x[0]),
    )
    return math.atan2(dot(cross, normal), dot(created_x, recorded_x))


# --------------------------------------------------------------------------
# templates
# --------------------------------------------------------------------------

# FAMILY_HOSTING_BEHAVIOR as its value string; the ints are the fallback when
# the exporter could not read the string.
HOSTING_HINTS = {
    "wall": "wall based",
    "floor": "floor based",
    "ceiling": "ceiling based",
    "roof": "roof based",
    "face": "face based",
}
HOSTING_INTS = {1: "wall", 2: "floor", 3: "ceiling", 4: "roof", 5: "face"}
HOSTING_TOKENS = ("wall based", "floor based", "ceiling based", "roof based",
                  "face based", "line based", "two level", "adaptive",
                  "pattern based")


def hosting_hint(family):
    """The template-name fragment for a family record's hosting, or ``""``."""
    family = family or {}
    label = _safe_text(family.get("hosting_behavior_label")).strip().lower()
    if label in HOSTING_HINTS:
        return HOSTING_HINTS[label]
    try:
        code = int(family.get("hosting_behavior"))
    except Exception:
        code = 0
    if code in HOSTING_INTS:
        return HOSTING_HINTS[HOSTING_INTS[code]]
    if _safe_text(family.get("placement_type")) == "CurveBased":
        return "line based"
    return ""


def _template_has_hosting(name_lower, hint):
    if hint:
        return hint in name_lower
    return not any(token in name_lower for token in HOSTING_TOKENS)


# Category templates whose hosting is built in without saying so in the
# name: "Metric Door.rft" is wall-hosted, "Metric Generic Model wall
# based.rft" says it.
INHERENTLY_WALL_HOSTED_LABELS = ("door", "window")


def category_label_tokens(category_label):
    """The spellings a template name may use for a category label.

    Revit names categories in the plural ("Doors", "Lighting Fixtures",
    "Pipe Accessories") and templates in the singular ("Metric Door.rft",
    "Metric Lighting Fixture.rft", "Metric Pipe Accessory.rft").
    """
    label = _safe_text(category_label).strip().lower()
    if not label:
        return []
    tokens = [label]
    if label.endswith("ies"):
        tokens.append(label[:-3] + "y")
    elif label.endswith("s") and not label.endswith("ss"):
        tokens.append(label[:-1])
    return tokens


def rank_templates(template_names, family):
    """Order the ``.rft`` file names by how well they fit a family record.

    Hosting outranks category: a family's category can be reassigned after
    it is created, its hosting cannot. The ladder: two-level templates for
    two-level families; the category template with the right hosting; a
    Generic Model template with the right hosting; the category template
    without it; plain Generic Model; anything else with the right hosting;
    the rest. Names are matched case-insensitively and singular/plural blind.
    """
    family = family or {}
    hint = hosting_hint(family)
    tokens = category_label_tokens(family.get("category_label"))
    placement = _safe_text(family.get("placement_type"))

    def score(name):
        lower = _safe_text(name).lower()
        if not lower.endswith(".rft"):
            return None
        is_generic = "generic model" in lower
        is_category = any(token in lower for token in tokens) and not is_generic
        hosted_ok = _template_has_hosting(lower, hint)
        if (is_category and hint == "wall based"
                and any(label in lower for label in INHERENTLY_WALL_HOSTED_LABELS)):
            hosted_ok = True
        two_level = "two level" in lower or "column" in lower
        if placement == "TwoLevelsBased" and two_level and (is_category or not hint):
            return 0
        if is_category and hosted_ok:
            return 1
        if is_generic and hosted_ok and hint:
            return 2
        if is_category:
            return 3
        if is_generic and hosted_ok:
            return 2
        if is_generic:
            return 4
        if hint and hint in lower:
            return 5
        return 6

    ranked = []
    for name in list(template_names or []):
        value = score(name)
        if value is None:
            continue
        # Shorter names win ties: "Metric Door.rft" over
        # "Metric Door - Curtain Wall.rft".
        ranked.append((value, len(_safe_text(name)), _safe_text(name).lower(), name))
    ranked.sort()
    return [entry[3] for entry in ranked]


# --------------------------------------------------------------------------
# the manifest
# --------------------------------------------------------------------------


def value_record(kind, value=None, element_name=None, element_class=None):
    """One parameter value: ``kind`` is double / int / string / element / none."""
    record = {"kind": _safe_text(kind) or "none"}
    if kind == "element":
        record["element_name"] = _safe_text(element_name)
        record["element_class"] = _safe_text(element_class)
        record["value"] = None
    elif kind == "none":
        record["value"] = None
    else:
        record["value"] = value
    return record


def new_manifest(family_name, exported):
    """An empty manifest with every section present, so readers need no guards."""
    return {
        "format": PACKAGE_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "exported": dict(exported or {}),
        "family": {"name": _safe_text(family_name)},
        "parameters": [],
        "types": [],
        "default_values": {},
        "materials": [],
        "subcategories": [],
        "geometry": {"format": GEOMETRY_SAT, "units": "feet", "groups": [],
                     "source_type": ""},
        "connectors": [],
        "curves": [],
        "reference_planes": [],
        "notes": [],
    }


def add_note(manifest, note):
    note = _safe_text(note).strip()
    if not note:
        return
    notes = manifest.setdefault("notes", [])
    if note not in notes:
        notes.append(note)


def validate_manifest(data):
    """``(ok, reason)`` for a parsed manifest."""
    if not isinstance(data, dict):
        return False, "manifest is not a JSON object"
    if _safe_text(data.get("format")) != PACKAGE_FORMAT:
        return False, "not a Families Downgrade package"
    try:
        version = int(data.get("schema_version"))
    except Exception:
        return False, "manifest has no schema version"
    if version > SCHEMA_VERSION:
        return False, (
            "written by a newer Families Downgrade (schema {}); update the "
            "extension".format(version))
    family = data.get("family")
    if not isinstance(family, dict) or not _safe_text(family.get("name")):
        return False, "manifest names no family"
    return True, ""


def dump_manifest(manifest):
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True)


def write_manifest(folder, manifest):
    path = os.path.join(folder, MANIFEST_NAME)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(u"" + dump_manifest(manifest))
    return path


def read_manifest(path):
    with io.open(path, "r", encoding="utf-8") as handle:
        return json.loads(handle.read())


# --------------------------------------------------------------------------
# packages on disk
# --------------------------------------------------------------------------


class PackageInfo(object):
    """One package folder as the rebuild page lists it."""

    def __init__(self, folder, manifest=None, reason="", is_selected=True):
        self.folder = _safe_text(folder)
        self.manifest = manifest
        self.reason = _safe_text(reason)
        self.is_selected = bool(is_selected) and manifest is not None
        family = (manifest or {}).get("family") or {}
        exported = (manifest or {}).get("exported") or {}
        self.family_name = _safe_text(family.get("name")) or os.path.basename(
            self.folder.rstrip("\\/")).replace(PACKAGE_SUFFIX, "")
        self.category_label = _safe_text(family.get("category_label"))
        self.source_version = _safe_text(exported.get("revit_version"))
        self.type_count = len((manifest or {}).get("types") or [])
        self.group_count = len(((manifest or {}).get("geometry") or {}).get("groups") or [])

    @property
    def is_usable(self):
        return self.manifest is not None

    @property
    def package_key(self):
        return self.folder.lower()

    def __str__(self):
        return self.family_name


def _child_folders(root):
    """``root``'s sub-folders, name-sorted; empty when it cannot be listed."""
    try:
        entries = sorted(os.listdir(root), key=lambda name: name.lower())
    except Exception:
        return []
    found = []
    for name in entries:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            found.append(path)
    return found


def _is_package_folder(folder):
    return os.path.isfile(os.path.join(folder, MANIFEST_NAME))


def _package_folders(root):
    """Every package folder at or below ``root``, two levels deep.

    One level is the folder an export wrote into. The second is there because
    a picker is pointed at the parent of that folder just as often, and an
    empty list with no reason is the worst answer the rebuild page can give.
    """
    root = _safe_text(root)
    if not root or not os.path.isdir(root):
        return []
    # A package folder chosen directly still counts.
    if _is_package_folder(root):
        return [root]
    found = []
    for child in _child_folders(root):
        if _is_package_folder(child):
            found.append(child)
            continue
        for grandchild in _child_folders(child):
            if _is_package_folder(grandchild):
                found.append(grandchild)
    found.sort(key=lambda path: (os.path.basename(path).lower(), path.lower()))
    return found


def find_packages(root):
    """Every package under ``root`` (one level deep), usable or not."""
    packages = []
    for folder in _package_folders(root):
        try:
            manifest = read_manifest(os.path.join(folder, MANIFEST_NAME))
        except Exception as ex:
            packages.append(PackageInfo(folder, None, "manifest cannot be read: {}".format(ex)))
            continue
        ok, reason = validate_manifest(manifest)
        if not ok:
            packages.append(PackageInfo(folder, None, reason))
            continue
        packages.append(PackageInfo(folder, manifest))
    return packages


def package_folder_name(family_name, type_name=None):
    """``<Family>.downgrade`` or ``<Family> - <Type>.downgrade``, filename-safe."""
    label = _safe_text(family_name)
    if _safe_text(type_name):
        label = "{} - {}".format(label, _safe_text(type_name))
    cleaned = INVALID_FILENAME_PATTERN.sub("_", label).strip(" ._")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        cleaned = "Family"
    return "{}{}".format(cleaned, PACKAGE_SUFFIX)


def build_unique_package_folder(root, family_name, used_paths, type_name=None):
    used_paths = used_paths if used_paths is not None else set()
    folder_name = package_folder_name(family_name, type_name)
    base = folder_name[:-len(PACKAGE_SUFFIX)]
    candidate = os.path.join(root, folder_name)
    index = 2
    while candidate.lower() in used_paths:
        candidate = os.path.join(root, "{}_{}{}".format(base, index, PACKAGE_SUFFIX))
        index += 1
    used_paths.add(candidate.lower())
    return candidate


def planned_package_folder_names(family_options, split_types=False, type_names_for=None):
    """The package folders an export would create, ``_2`` suffixes included.

    With ``split_types`` on, ``type_names_for(option)`` names the types each
    family will be split into (unknown before the family document is open,
    so the caller passes what it knows and the warning is best effort).
    """
    used = set()
    names = []
    for option in list(family_options or []):
        family_name = _safe_text(getattr(option, "name", ""))
        type_names = []
        if split_types and type_names_for is not None:
            type_names = list(type_names_for(option) or [])
        if type_names:
            for type_name in type_names:
                names.append(os.path.basename(
                    build_unique_package_folder("", family_name, used, type_name)))
        else:
            names.append(os.path.basename(build_unique_package_folder("", family_name, used)))
    return names


def planned_rfa_filenames(packages):
    """The ``.rfa`` names a rebuild would write for these packages, in order."""
    used = set()
    names = []
    for package in list(packages or []):
        family_name = _safe_text(getattr(package, "family_name", ""))
        file_name = sanitize_export_filename(family_name)
        base, extension = os.path.splitext(file_name)
        candidate = file_name
        index = 2
        while candidate.lower() in used:
            candidate = "{}_{}{}".format(base, index, extension)
            index += 1
        used.add(candidate.lower())
        names.append(candidate)
    return names


def build_overwrite_text(existing_names, total_count, noun="file(s)"):
    existing_names = list(existing_names or [])
    lines = [
        "{} of {} {} already exist in that folder and will be replaced:".format(
            len(existing_names), int(total_count or 0), noun),
        "",
    ]
    for name in existing_names[:SUMMARY_DISPLAY_LIMIT]:
        lines.append("- {}".format(name))
    if len(existing_names) > SUMMARY_DISPLAY_LIMIT:
        lines.append("- Plus {} more.".format(len(existing_names) - SUMMARY_DISPLAY_LIMIT))
    return "\n".join(lines)


def owned_package_files(folder):
    """The files inside a package folder that an export may replace."""
    folder = _safe_text(folder)
    if not os.path.isdir(folder):
        return []
    owned = []
    for name in os.listdir(folder):
        lower = name.lower()
        if lower == MANIFEST_NAME or re.match(r"^g\d+\.(sat|dwg)$", lower):
            owned.append(os.path.join(folder, name))
    return owned


# --------------------------------------------------------------------------
# options
# --------------------------------------------------------------------------


class ExportOptions(object):
    """Geometry travels as SAT and the user is not asked about it: it is how
    solids move between the two Revits, not the file they end up with. DWG
    stays reachable in code as the automatic fallback for a group whose SAT
    comes out empty (see ``export_group_file``)."""

    def __init__(self, folder="", split_types=False, geometry_format=GEOMETRY_SAT):
        self.folder = _safe_text(folder)
        self.split_types = bool(split_types)
        self.geometry_format = (
            geometry_format if geometry_format in GEOMETRY_FORMATS else GEOMETRY_SAT)


class DowngradeOptions(object):
    """What the one-page downgrade asks for: which Revit, and where."""

    def __init__(self, output_folder="", target_version="", split_types=False,
                 template_path=""):
        self.output_folder = _safe_text(output_folder)
        self.target_version = _safe_text(target_version)
        self.split_types = bool(split_types)
        # An .rft the user picked after the target Revit found none of its
        # own; empty on every run that did not need it.
        self.template_path = _safe_text(template_path)


class RebuildOptions(object):
    def __init__(self, package_folder="", output_folder="", template_path=""):
        self.package_folder = _safe_text(package_folder)
        self.output_folder = _safe_text(output_folder)
        # A user-picked .rft used when the template folder offers no match.
        self.template_path = _safe_text(template_path)


# --------------------------------------------------------------------------
# results and report text
# --------------------------------------------------------------------------


class DowngradeResult(object):
    """One family's outcome: where it went, how it ended, what it lost."""

    def __init__(self, family_name, target, status, notes=None):
        self.family_name = _safe_text(family_name) or "(Unknown Family)"
        self.target = _safe_text(target)
        self.status = _safe_text(status)
        self.notes = []
        for note in list(notes or []):
            self.add_note(note)

    def add_note(self, note):
        note = _safe_text(note).strip()
        if note and note not in self.notes:
            self.notes.append(note)


class DowngradeSummary(object):
    def __init__(self, mode=MODE_EXPORT):
        self.mode = mode
        self.written = []
        self.skipped = []
        self.failed = []
        self.notes = []
        self.cancelled = False
        self.report_path = ""

    def add_note(self, note):
        note = _safe_text(note).strip()
        if note and note not in self.notes:
            self.notes.append(note)

    @property
    def total(self):
        return len(self.written) + len(self.skipped) + len(self.failed)


def _mode_label(mode):
    if mode == MODE_REBUILD:
        return "Rebuild"
    if mode == MODE_DOWNGRADE:
        return "Downgrade"
    return "Export"


def combine_downgrade_summaries(export_summary, rebuild_summary, expected_names=None):
    """One story from the two halves of a downgrade.

    The user asked for family files, not for a packaging step and a rebuilding
    step, so the run reports as one. A family this Revit packaged that the
    other Revit never mentioned is the case worth naming: without this it
    would simply be absent from every list, which reads as success.
    """
    export_summary = export_summary or DowngradeSummary(MODE_EXPORT)
    rebuild_summary = rebuild_summary or DowngradeSummary(MODE_REBUILD)
    summary = DowngradeSummary(MODE_DOWNGRADE)
    summary.cancelled = bool(export_summary.cancelled) or bool(rebuild_summary.cancelled)
    summary.written = list(rebuild_summary.written or [])
    summary.skipped = list(export_summary.skipped or []) + list(rebuild_summary.skipped or [])
    summary.failed = list(export_summary.failed or []) + list(rebuild_summary.failed or [])

    reported = set()
    for group in (summary.written, summary.skipped, summary.failed):
        for result in group:
            reported.add(_safe_text(getattr(result, "family_name", "")).lower())
    for name in list(expected_names or []):
        if _safe_text(name).lower() not in reported:
            summary.failed.append(DowngradeResult(
                name, "", "the target Revit did not report this family"))

    for note in list(export_summary.notes or []) + list(rebuild_summary.notes or []):
        summary.add_note(note)
    return summary


def _append_result_lines(lines, title, results, with_notes=False, limit=SUMMARY_DISPLAY_LIMIT):
    results = list(results or [])
    if not results:
        return
    lines.append("")
    lines.append(title)
    shown = results if limit is None else results[:limit]
    for result in shown:
        target = _safe_text(getattr(result, "target", ""))
        status = _safe_text(getattr(result, "status", ""))
        line = "- {}".format(_safe_text(getattr(result, "family_name", "")))
        if target:
            line = "{} -> {}".format(line, target)
        if status:
            line = "{}: {}".format(line, status)
        lines.append(line)
        if with_notes:
            for note in list(getattr(result, "notes", []) or []):
                lines.append("    . {}".format(note))
    if limit is not None and len(results) > limit:
        lines.append("- Plus {} more.".format(len(results) - limit))


def build_summary_text(summary):
    """The dialog shown when a run ends: counts, whole-run notes, short lists."""
    summary = summary or DowngradeSummary()
    label = _mode_label(summary.mode)
    lines = [
        "Families Downgrade {} cancelled.".format(label.lower())
        if summary.cancelled else "Families Downgrade {} completed.".format(label.lower()),
        "{}: {}".format("Packages written" if summary.mode == MODE_EXPORT
                        else "Families rebuilt", len(summary.written)),
        "Skipped: {}".format(len(summary.skipped)),
        "Failed: {}".format(len(summary.failed)),
    ]
    dropped = sum(len(getattr(result, "notes", []) or []) for result in summary.written)
    if dropped:
        lines.append("Things not carried (see the report): {}".format(dropped))
    if summary.report_path:
        lines.append("Report: {}".format(summary.report_path))
    for note in list(summary.notes or []):
        lines.append("")
        lines.append(_safe_text(note))
    _append_result_lines(lines, "Written:" if summary.mode == MODE_EXPORT else "Rebuilt:",
                         summary.written)
    _append_result_lines(lines, "Skipped:", summary.skipped)
    _append_result_lines(lines, "Failed:", summary.failed)
    return "\n".join(lines)


def build_report_text(summary, host_version="", folder=""):
    """The report file: everything, with the per-family notes in full."""
    summary = summary or DowngradeSummary()
    label = _mode_label(summary.mode)
    lines = [
        "Families Downgrade - {} report".format(label.lower()),
        "Revit {}".format(_safe_text(host_version)) if host_version else "Revit (unknown version)",
        "Folder: {}".format(_safe_text(folder)) if folder else "",
        "",
        "A downgrade rebuilds a family from its geometry and data; it is not a "
        "file conversion. Rebuilt solids are static (no parametric geometry), "
        "and everything a family lost on the way is listed under its name.",
        "",
        "Written: {}   Skipped: {}   Failed: {}".format(
            len(summary.written), len(summary.skipped), len(summary.failed)),
    ]
    if summary.cancelled:
        lines.append("The run was cancelled.")
    for note in list(summary.notes or []):
        lines.append("")
        lines.append(_safe_text(note))
    _append_result_lines(lines, "Written:" if summary.mode == MODE_EXPORT else "Rebuilt:",
                         summary.written, with_notes=True, limit=None)
    _append_result_lines(lines, "Skipped:", summary.skipped, with_notes=True, limit=None)
    _append_result_lines(lines, "Failed:", summary.failed, with_notes=True, limit=None)
    return "\n".join(line for line in lines if line is not None)


def write_report(folder, summary, host_version=""):
    """Write the report next to the outputs; the path, or ``""`` if it failed."""
    path = os.path.join(_safe_text(folder), REPORT_FILENAME)
    try:
        text = build_report_text(summary, host_version, folder)
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(u"" + text)
    except Exception as ex:
        summary.add_note("The report file could not be written: {}".format(ex))
        return ""
    summary.report_path = path
    return path
