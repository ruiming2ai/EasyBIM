# -*- coding: utf-8 -*-
"""Revit API layer for Update Circuit Rating.

Everything that reads or writes the model lives here; the decisions live in
``circuit_rating_state``.  The model is scanned once, into plain dicts, so
switching the source parameter or a target in the window never costs another
pass over the document.
"""

from __future__ import print_function

from pyrevit import DB
from pyrevit import revit

from easybim.compat import eid_to_int
from easybim.compat import exception_text
from easybim.compat import int_to_eid
from easybim.compat import safe_text


# Neither rating parameter is guaranteed to exist on every Revit release, so
# they are resolved by name the way Slope resolves its slope parameters.
RATING_BIP_NAMES = (
    "RBS_ELEC_CIRCUIT_RATING_PARAM",   # the trip rating
    "RBS_ELEC_CIRCUIT_FRAME_PARAM",
)

_NUMERIC_STORAGE = ("Double", "Integer")
_USABLE_STORAGE = ("Double", "Integer", "String")


def _storage_name(param):
    try:
        return safe_text(param.StorageType)
    except Exception:
        return u""


def _definition_name(param):
    try:
        return safe_text(param.Definition.Name)
    except Exception:
        return u""


def _is_current_spec(param):
    """True for parameters measured in amps - they head the source list."""
    try:
        spec_id = param.Definition.GetDataType()
    except Exception:
        spec_id = None
    if spec_id is not None:
        type_id = safe_text(getattr(spec_id, "TypeId", u"")).lower()
        if type_id:
            return "current" in type_id
    try:
        return "current" in safe_text(param.Definition.ParameterType).lower()
    except Exception:
        return False


# --------------------------------------------------------------- unit helpers
# Lifted from Parameter Copy, which already carries the 2021+/legacy split.


def _get_param_unit_id(param, doc):
    definition = getattr(param, "Definition", None)

    try:
        spec_id = definition.GetDataType() if definition else None
        if spec_id is not None and safe_text(getattr(spec_id, "TypeId", u"")):
            format_options = doc.GetUnits().GetFormatOptions(spec_id)
            if format_options:
                unit_id = format_options.GetUnitTypeId()
                if safe_text(getattr(unit_id, "TypeId", u"")):
                    return unit_id
    except Exception:
        pass

    try:
        display_unit = param.DisplayUnitType
        if "undefined" not in safe_text(display_unit).lower():
            return display_unit
    except Exception:
        pass

    return None


def _to_display(param, internal_value, doc):
    unit_id = _get_param_unit_id(param, doc)
    if unit_id is None:
        return float(internal_value)
    try:
        return float(
            DB.UnitUtils.ConvertFromInternalUnits(float(internal_value), unit_id))
    except Exception:
        return float(internal_value)


def _to_internal(param, display_value, doc):
    unit_id = _get_param_unit_id(param, doc)
    if unit_id is None:
        return float(display_value)
    try:
        return float(
            DB.UnitUtils.ConvertToInternalUnits(float(display_value), unit_id))
    except Exception:
        return float(display_value)


def _parse_number(text):
    """``"100 A"`` -> ``100.0``; anything unreadable -> ``None``."""
    cleaned = u""
    for char in safe_text(text):
        if char.isdigit() or char in u".-":
            cleaned += char
        elif cleaned:
            break
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _param_value(param, doc):
    storage = _storage_name(param)
    try:
        if storage == "Double":
            return _to_display(param, param.AsDouble(), doc)
        if storage == "Integer":
            return float(param.AsInteger())
        if storage == "String":
            return _parse_number(param.AsString())
    except Exception:
        return None
    return None


# ------------------------------------------------------------------- scanning


def _element_type(element, doc):
    try:
        type_id = element.GetTypeId()
    except Exception:
        return None
    if type_id is None or eid_to_int(type_id) < 0:
        return None
    try:
        return doc.GetElement(type_id)
    except Exception:
        return None


def _element_label(element, doc):
    """``Panelboard : 225 A [123456]`` - enough to find it in the model."""
    parts = []
    try:
        category = element.Category
        if category is not None:
            parts.append(safe_text(category.Name))
    except Exception:
        pass
    name = u""
    try:
        name = safe_text(element.Name)
    except Exception:
        pass
    if not name:
        element_type = _element_type(element, doc)
        if element_type is not None:
            try:
                name = safe_text(element_type.Name)
            except Exception:
                name = u""
    if name:
        parts.append(name)
    label = u" : ".join(part for part in parts if part) or u"Element"
    return u"{0} [{1}]".format(label, eid_to_int(element.Id))


def _is_electrical_equipment(element):
    try:
        category = element.Category
        if category is None:
            return False
        return eid_to_int(category.Id) == int(
            DB.BuiltInCategory.OST_ElectricalEquipment)
    except Exception:
        return False


def _circuit_elements(circuit):
    try:
        return [element for element in circuit.Elements]
    except Exception:
        return []


def _circuit_text(circuit, bip_name, fallback=u""):
    builtin = getattr(DB.BuiltInParameter, bip_name, None)
    if builtin is None:
        return fallback
    try:
        param = circuit.get_Parameter(builtin)
        if param is None:
            return fallback
        return safe_text(param.AsString() or param.AsValueString() or fallback)
    except Exception:
        return fallback


def collect_circuits(doc):
    """Every electrical circuit in the document."""
    try:
        collector = DB.FilteredElementCollector(doc) \
            .OfCategory(DB.BuiltInCategory.OST_ElectricalCircuit) \
            .WhereElementIsNotElementType()
        return list(collector)
    except Exception:
        return []


def scan_model(doc):
    """One pass: circuits, their elements, and every parameter on them.

    Returns ``(circuits_data, source_options, target_options)`` where
    ``circuits_data`` holds per-circuit element readings keyed by parameter
    name, ready for ``build_records``.
    """
    circuits = collect_circuits(doc)
    circuits_data = []
    source_names = {}
    target_options = _collect_target_options(circuits)
    target_keys = [option["key"] for option in target_options]

    for circuit in circuits:
        try:
            entry = {
                "circuit_id": eid_to_int(circuit.Id),
                "panel": _circuit_text(circuit, "RBS_ELEC_CIRCUIT_PANEL_PARAM"),
                "circuit_number": _circuit_text(
                    circuit, "RBS_ELEC_CIRCUIT_NUMBER"),
                "load_name": _circuit_text(circuit, "RBS_ELEC_CIRCUIT_NAME"),
                "existing": _read_existing(circuit, target_keys, doc),
                "readings": {},
            }
        except Exception:
            continue

        for element in _circuit_elements(circuit):
            try:
                label = _element_label(element, doc)
                is_equipment = _is_electrical_equipment(element)
                for name, value in _element_readings(
                        element, doc, source_names).items():
                    entry["readings"].setdefault(name, []).append({
                        "value": value,
                        "label": label,
                        "is_equipment": is_equipment,
                    })
            except Exception:
                continue
        circuits_data.append(entry)

    return circuits_data, _sorted_source_options(source_names), target_options


def _element_readings(element, doc, source_names):
    """Numeric readings by parameter name: instance first, then type."""
    readings = {}
    for param, origin in _element_parameters(element, doc):
        name = _definition_name(param)
        if not name:
            continue
        storage = _storage_name(param)
        if storage not in _USABLE_STORAGE:
            continue
        # The instance parameter is authoritative; the type only fills a gap.
        if name in readings:
            continue
        value = _param_value(param, doc)
        if value is None or value <= 0:
            # An unset parameter reads as blank or as 0, and neither is a
            # rating.  Ignoring both keeps the picker short and stops an
            # all-empty parameter from zeroing every circuit it touches.
            continue
        readings[name] = value
        info = source_names.setdefault(
            name, {"origins": set(), "is_current": False})
        info["origins"].add(origin)
        if _is_current_spec(param):
            info["is_current"] = True
    return readings


def _element_parameters(element, doc):
    pairs = []
    try:
        for param in element.Parameters:
            pairs.append((param, u"Instance"))
    except Exception:
        pass
    element_type = _element_type(element, doc)
    if element_type is not None:
        try:
            for param in element_type.Parameters:
                pairs.append((param, u"Type"))
        except Exception:
            pass
    return pairs


def _sorted_source_options(source_names):
    """Current-type parameters first, then the rest, both alphabetical."""
    options = []
    for name in source_names:
        info = source_names[name]
        origins = u"/".join(sorted(info["origins"]))
        options.append({
            "key": name,
            "label": u"{0}  ({1})".format(name, origins),
            "is_current": info["is_current"],
        })
    options.sort(key=lambda option: (
        0 if option["is_current"] else 1, option["key"].lower()))
    return options


def _collect_target_options(circuits):
    """Writable numeric parameters on the circuits, Rating and Frame first."""
    preferred = set()
    for bip_name in RATING_BIP_NAMES:
        builtin = getattr(DB.BuiltInParameter, bip_name, None)
        if builtin is not None:
            preferred.add(int(builtin))

    found = {}
    for circuit in circuits:
        try:
            params = list(circuit.Parameters)
        except Exception:
            continue
        for param in params:
            try:
                if param.IsReadOnly:
                    continue
            except Exception:
                continue
            if _storage_name(param) not in _NUMERIC_STORAGE:
                continue
            key = eid_to_int(param.Id)
            if key in found:
                continue
            name = _definition_name(param)
            if not name:
                continue
            found[key] = {
                "key": key,
                "label": name,
                "is_default": key in preferred,
            }

    options = list(found.values())
    options.sort(key=lambda option: (
        0 if option["is_default"] else 1, option["label"].lower()))
    return options


def _params_by_key(element):
    """``{parameter id: parameter}`` - one walk instead of one per lookup."""
    mapping = {}
    try:
        for param in element.Parameters:
            mapping[eid_to_int(param.Id)] = param
    except Exception:
        pass
    return mapping


def _read_existing(circuit, keys, doc):
    params = _params_by_key(circuit)
    existing = {}
    for key in keys:
        param = params.get(key)
        existing[key] = _param_value(param, doc) if param is not None else None
    return existing


# -------------------------------------------------------------------- writing


def apply_ratings(doc, plan, target_labels):
    """Write every planned circuit inside one transaction.

    A circuit that refuses the write is recorded and the rest still go
    through, matching how Sheet Manager applies staged changes.
    """
    results = []
    if not plan:
        return results

    with revit.Transaction("Update Circuit Rating", doc=doc):
        for item in plan:
            written = []
            error = None
            try:
                circuit = doc.GetElement(
                    int_to_eid(item.get("circuit_id"), DB.ElementId))
                if circuit is None:
                    raise ValueError("circuit not found")
                params = _params_by_key(circuit)
                for key in item.get("target_keys") or []:
                    param = params.get(key)
                    label = target_labels.get(key, safe_text(key))
                    if param is None:
                        continue
                    if _write_param(param, item.get("value"), doc):
                        written.append(label)
            except Exception as write_error:
                error = exception_text(write_error)
            results.append({
                "circuit_id": item.get("circuit_id"),
                "circuit_label": item.get("circuit_label"),
                "written": written,
                "error": error,
            })
    return results


def _write_param(param, display_value, doc):
    if display_value is None:
        return False
    storage = _storage_name(param)
    try:
        if storage == "Double":
            return bool(param.Set(_to_internal(param, display_value, doc)))
        if storage == "Integer":
            return bool(param.Set(int(round(float(display_value)))))
    except Exception:
        return False
    return False
