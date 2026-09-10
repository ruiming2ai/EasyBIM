# -*- coding: utf-8 -*-
"""Revit API layer for Damper Check: one read-only pass into plain dicts.

Everything that touches the API lives here and nothing but ints, floats,
booleans and unicode crosses back into ``damper_check_state``.  There is no
Transaction in this module and there never will be: the tool judges, it
does not fix.

The pass reads the *physical* connector graph.  Per connector it keeps only
``ConnectorType`` End/Curve (Physical is accepted defensively) in the HVAC
domain, and per ``AllRefs`` partner it drops the owner itself, any
``MEPSystem`` (the system hands back a logical connector that would swallow
the whole system as one neighbour) and any ``InsulationLiningBase`` (duct
insulation and lining are MEPCurves that mirror their host's connectors).
Partners whose owner has not been read yet are pulled onto the worklist, so
a Generic Model damper never splits a branch.  Per-connector reads of
``Origin``, ``MEPSystem`` and ``CoordinateSystem`` are deliberately absent -
on a hospital model they are the difference between seconds and minutes.

Every accessor is probed, never assumed: the enum names are compared as
text, ``ElementId`` values ride ``easybim.compat``, and an element whose
connector manager throws is recorded with its error and kept, so its
neighbours' references still join it into the graph.  ``db`` is injectable
so the desktop tests drive the adapter with fakes shaped like the API.
"""

from __future__ import print_function

import time

try:
    from pyrevit import DB
except Exception:  # off-Revit (unit tests)
    DB = None

try:
    from easybim.compat import eid_to_int
    from easybim.compat import element_id_factory
    from easybim.compat import safe_text
except Exception:  # standalone load without the easybim package
    def safe_text(value):
        if value is None:
            return ""
        try:
            return str(value)
        except Exception:
            try:
                return value.ToString()
            except Exception:
                return ""

    def eid_to_int(element_id):
        if element_id is None:
            return None
        for attr_name in ("IntegerValue", "Value"):
            try:
                return int(getattr(element_id, attr_name))
            except Exception:
                pass
        try:
            return int(element_id)
        except Exception:
            return None

    def element_id_factory(element_id_type):
        def _construct(value):
            return element_id_type(int(value))
        return _construct

try:
    from easybim.circuit_schedule_revit import show_elements as _lib_show_elements
except Exception:
    _lib_show_elements = None


#: ``(BuiltInCategory member name, kind, checklist label)``
INSTANCE_CATEGORIES = (
    ("OST_DuctAccessory", "accessory", u"Duct Accessories"),
    ("OST_DuctFitting", "fitting", u"Duct Fittings"),
    ("OST_DuctTerminal", "terminal", u"Air Terminals"),
    ("OST_MechanicalEquipment", "equipment", u"Mechanical Equipment"),
)
CURVE_CATEGORIES = (
    ("OST_DuctCurves", "duct", u"Ducts"),
    ("OST_FlexDuctCurves", "flex", u"Flex Ducts"),
)
INSULATION_CATEGORIES = ("OST_DuctInsulations", "OST_DuctLinings")
CURVE_CLASSES = (("Duct", "duct"), ("FlexDuct", "flex"))

PHYSICAL_TYPES = ("End", "Curve", "Physical")
HVAC_DOMAIN = "DomainHvac"

DEFAULT_BUDGET_SECONDS = 90.0
DEFAULT_ELEMENT_CAP = 150000
CATALOG_BUDGET_SECONDS = 20.0
PROGRESS_EVERY = 200

#: Views that can never show a duct - the Active view radio greys out here.
NON_GRAPHICAL_VIEW_TYPES = (
    "Schedule", "DrawingSheet", "ProjectBrowser", "SystemBrowser", "Undefined",
    "Internal", "Report", "CostReport", "LoadsReport", "PresureLossReport",
    "PressureLossReport", "PanelSchedule", "ColumnSchedule", "Legend",
)


# ----------------------------------------------------------------- probes


def _db(db):
    return db if db is not None else DB


def _builtin(db, enum_name, member_name):
    enum = getattr(db, enum_name, None) if db is not None else None
    if enum is None:
        return None
    return getattr(enum, member_name, None)


def _builtin_int(member):
    if member is None:
        return None
    try:
        return int(member)
    except Exception:
        pass
    try:
        return int(getattr(member, "value__"))
    except Exception:
        return None


def enum_name(value):
    """``ConnectorType.End`` and ``End`` both read as ``End``."""
    text = safe_text(value)
    if u"." in text:
        text = text.rsplit(u".", 1)[1]
    return text


def _category_int(element):
    try:
        category = element.Category
        if category is None:
            return None
        return eid_to_int(category.Id)
    except Exception:
        return None


def _category_name(element):
    try:
        category = element.Category
        return safe_text(category.Name) if category is not None else u""
    except Exception:
        return u""


def _kind_map(db):
    """``{category int: (kind, label)}`` for the categories this Revit has."""
    mapping = {}
    for member_name, kind, label in INSTANCE_CATEGORIES + CURVE_CATEGORIES:
        value = _builtin_int(_builtin(db, "BuiltInCategory", member_name))
        if value is not None:
            mapping[value] = (kind, label)
    return mapping


def _kind_of(element, kind_map):
    entry = kind_map.get(_category_int(element))
    if entry is not None:
        return entry[0], entry[1]
    return "other", _category_name(element)


def _connector_manager(element):
    """``(manager, error)`` - MEPCurve first, then ``MEPModel``; never raises."""
    try:
        manager = getattr(element, "ConnectorManager", None)
        if manager is not None:
            return manager, u""
    except Exception as ex:
        return None, u"ConnectorManager: {0}".format(safe_text(ex))
    try:
        mep_model = getattr(element, "MEPModel", None)
    except Exception as ex:
        return None, u"MEPModel: {0}".format(safe_text(ex))
    if mep_model is None:
        return None, u""
    try:
        manager = mep_model.ConnectorManager
    except Exception as ex:
        return None, u"MEPModel.ConnectorManager: {0}".format(safe_text(ex))
    return manager, u""


def _is_hvac_physical(connector):
    try:
        if enum_name(connector.ConnectorType) not in PHYSICAL_TYPES:
            return False
        return enum_name(connector.Domain) == HVAC_DOMAIN
    except Exception:
        return False


def _insulation_ints(db):
    values = set()
    for member_name in INSULATION_CATEGORIES:
        value = _builtin_int(_builtin(db, "BuiltInCategory", member_name))
        if value is not None:
            values.add(value)
    return values


def _is_system_or_insulation(owner, db, insulation_ints):
    for class_name in ("MEPSystem", "InsulationLiningBase"):
        klass = getattr(db, class_name, None) if db is not None else None
        if klass is None:
            continue
        try:
            if isinstance(owner, klass):
                return True
        except Exception:
            pass
    return _category_int(owner) in insulation_ints


def _physical_partners(connector, owner_id, db, insulation_ints):
    """``(partners, owners)`` - the physical mates only, as ``[owner id,
    connector id]`` pairs, plus the owner elements for the pull-in list."""
    partners = []
    owners = []
    try:
        refs = list(connector.AllRefs)
    except Exception:
        return partners, owners
    for ref in refs:
        if not _is_hvac_physical(ref):
            continue
        try:
            owner = ref.Owner
        except Exception:
            continue
        if owner is None:
            continue
        partner_id = None
        try:
            partner_id = eid_to_int(owner.Id)
        except Exception:
            partner_id = None
        if partner_id is None or partner_id == owner_id:
            continue
        if _is_system_or_insulation(owner, db, insulation_ints):
            continue
        try:
            connector_id = int(ref.Id)
        except Exception:
            connector_id = -1
        partners.append([partner_id, connector_id])
        owners.append(owner)
    return partners, owners


def _connector_size(connector):
    try:
        shape = enum_name(connector.Shape)
    except Exception:
        shape = u""
    try:
        if shape == u"Round":
            return float(connector.Radius) * 2.0
        if shape in (u"Rectangular", u"Oval"):
            return max(float(connector.Width), float(connector.Height))
    except Exception:
        pass
    return 0.0


def _connector_records(manager, owner_id, db, insulation_ints, with_size=False):
    """``(records, partner_owners, mismatch, error)`` for one element."""
    try:
        connectors = list(manager.Connectors)
    except Exception as ex:
        return [], [], 0, u"Connectors: {0}".format(safe_text(ex))
    records = []
    owners = []
    mismatch = 0
    for connector in connectors:
        if not _is_hvac_physical(connector):
            continue
        try:
            connector_id = int(connector.Id)
        except Exception:
            connector_id = len(records)
        try:
            connected = bool(connector.IsConnected)
        except Exception:
            connected = False
        partners, partner_owners = _physical_partners(connector, owner_id, db, insulation_ints)
        if connected != bool(partners):
            mismatch += 1
        record = {
            "id": connector_id,
            "type": enum_name(connector.ConnectorType),
            "connected": connected,
            "partners": partners,
        }
        if with_size:
            record["size"] = _connector_size(connector)
        records.append(record)
        owners.extend(partner_owners)
    return records, owners, mismatch, u""


# -------------------------------------------------------------- readings


def _param(element, bip_name, db):
    builtin = _builtin(db, "BuiltInParameter", bip_name)
    if builtin is None:
        return None
    try:
        return element.get_Parameter(builtin)
    except Exception:
        return None


def _param_string(element, bip_name, db):
    param = _param(element, bip_name, db)
    if param is None:
        return u""
    try:
        return safe_text(param.AsString() or u"")
    except Exception:
        return u""


def _param_double(element, bip_name, db):
    param = _param(element, bip_name, db)
    if param is None:
        return 0.0
    try:
        return float(param.AsDouble())
    except Exception:
        return 0.0


def _param_element_id(element, bip_name, db):
    param = _param(element, bip_name, db)
    if param is None:
        return None
    try:
        return eid_to_int(param.AsElementId())
    except Exception:
        return None


def _type_names(element, doc, cache):
    """``(family, type name, type id)`` - cached by type id."""
    try:
        type_id = eid_to_int(element.GetTypeId())
    except Exception:
        type_id = None
    if type_id is not None and type_id in cache:
        return cache[type_id]
    family = u""
    type_name = u""
    if type_id is not None and type_id >= 0:
        try:
            element_type = doc.GetElement(element.GetTypeId())
        except Exception:
            element_type = None
        if element_type is not None:
            try:
                family = safe_text(getattr(element_type, "FamilyName", u"") or u"")
            except Exception:
                family = u""
            try:
                type_name = safe_text(element_type.Name)
            except Exception:
                type_name = u""
    if not family:
        try:
            family = safe_text(element.Symbol.Family.Name)
        except Exception:
            pass
    if not type_name:
        try:
            type_name = safe_text(element.Name)
        except Exception:
            pass
    result = (family, type_name, type_id if type_id is not None else -1)
    if type_id is not None:
        cache[type_id] = result
    return result


def _level_name(element, doc, db, cache):
    level_id = None
    try:
        level_id = eid_to_int(element.LevelId)
    except Exception:
        level_id = None
    if level_id is None or level_id < 0:
        for bip_name in ("RBS_START_LEVEL_PARAM", "FAMILY_LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM"):
            level_id = _param_element_id(element, bip_name, db)
            if level_id is not None and level_id >= 0:
                break
    if level_id is None or level_id < 0:
        return u""
    if level_id in cache:
        return cache[level_id]
    name = u""
    try:
        level = doc.GetElement(element_id_factory(db.ElementId)(level_id))
        name = safe_text(level.Name) if level is not None else u""
    except Exception:
        name = u""
    cache[level_id] = name
    return name


def _space_label(element):
    try:
        space = element.Space
    except Exception:
        return u""
    if space is None:
        return u""
    parts = []
    for attr in ("Number", "Name"):
        try:
            text = safe_text(getattr(space, attr, u"")).strip()
        except Exception:
            text = u""
        if text:
            parts.append(text)
    return u" ".join(parts)


def _system_class(element, doc, db, cache, connectors=None):
    """Supply / Return / Exhaust by name, from the element's own system type
    parameter first (it exists whether or not a system instance does)."""
    type_id = _param_element_id(element, "RBS_DUCT_SYSTEM_TYPE_PARAM", db)
    if type_id is not None and type_id >= 0:
        if type_id in cache:
            return cache[type_id]
        name = u""
        try:
            system_type = doc.GetElement(element_id_factory(db.ElementId)(type_id))
            name = enum_name(system_type.SystemClassification) if system_type is not None else u""
        except Exception:
            name = u""
        cache[type_id] = name
        if name:
            return name
    text = _param_string(element, "RBS_SYSTEM_CLASSIFICATION_PARAM", db)
    if text:
        return text.split(u",")[0].strip()
    for connector in connectors or []:
        try:
            return enum_name(connector.DuctSystemType)
        except Exception:
            continue
    return u""


def _duct_size(element, db):
    diameter = _param_double(element, "RBS_CURVE_DIAMETER_PARAM", db)
    if diameter > 0.0:
        return diameter
    return max(_param_double(element, "RBS_CURVE_WIDTH_PARAM", db),
               _param_double(element, "RBS_CURVE_HEIGHT_PARAM", db))


# -------------------------------------------------------------- collectors


def _collector(doc, db, view_id=None):
    collector_type = getattr(db, "FilteredElementCollector", None)
    if collector_type is None:
        return None
    try:
        if view_id is not None:
            return collector_type(doc, view_id)
        return collector_type(doc)
    except Exception:
        return None


def _default_clr_list(db):
    from System.Collections.Generic import List as ClrList

    return ClrList[db.BuiltInCategory]()


def collect_instances(doc, members, db=None, clr_list=None):
    """Instances of the given BuiltInCategory members, one multi-category
    pass when the CLR list is available, else per category with dedupe."""
    db = _db(db)
    members = [member for member in members if member is not None]
    if not members:
        return []
    try:
        categories = (clr_list or _default_clr_list)(db)
        for member in members:
            categories.Add(member)
        collector = _collector(doc, db).WherePasses(db.ElementMulticategoryFilter(categories))
        return list(collector.WhereElementIsNotElementType().ToElements())
    except Exception:
        pass
    elements = []
    seen = set()
    for member in members:
        try:
            found = list(_collector(doc, db).OfCategory(member)
                         .WhereElementIsNotElementType().ToElements())
        except Exception:
            continue
        for element in found:
            key = eid_to_int(element.Id)
            if key in seen:
                continue
            seen.add(key)
            elements.append(element)
    return elements


def _curve_elements(doc, db):
    mechanical = getattr(db, "Mechanical", None)
    elements = []
    for class_name, _kind in CURVE_CLASSES:
        klass = getattr(mechanical, class_name, None) if mechanical is not None else None
        if klass is None:
            continue
        try:
            elements.extend(list(_collector(doc, db).OfClass(klass)
                                 .WhereElementIsNotElementType().ToElements()))
        except Exception:
            continue
    return elements


def _family_instances(doc, db):
    klass = getattr(db, "FamilyInstance", None)
    if klass is None:
        return []
    try:
        return list(_collector(doc, db).OfClass(klass).WhereElementIsNotElementType().ToElements())
    except Exception:
        return []


def _has_hvac_connector(element):
    manager, _error = _connector_manager(element)
    if manager is None:
        return False
    try:
        for connector in manager.Connectors:
            if _is_hvac_physical(connector):
                return True
    except Exception:
        return False
    return False


def view_info(doc, db=None):
    db = _db(db)
    try:
        view = doc.ActiveView
    except Exception:
        view = None
    if view is None:
        return {"id": None, "name": u"", "is_graphical": False}
    try:
        is_template = bool(view.IsTemplate)
    except Exception:
        is_template = False
    try:
        view_type = enum_name(view.ViewType)
    except Exception:
        view_type = u""
    name = u""
    try:
        name = safe_text(view.Name)
    except Exception:
        pass
    return {
        "id": eid_to_int(view.Id),
        "name": name,
        "is_graphical": (not is_template) and view_type not in NON_GRAPHICAL_VIEW_TYPES,
    }


def collect_types(doc, db=None, clock=None, budget_seconds=CATALOG_BUDGET_SECONDS, clr_list=None):
    """The checklist: every family type with duct connectors, with counts.

    The four duct categories come from one collector; every other family
    instance is probed for an HVAC connector under a wall-clock budget so a
    Generic Model damper can be ticked too.
    """
    db = _db(db)
    clock = clock or time.time
    started = clock()
    kind_map = _kind_map(db)
    known_ints = set(value for value, entry in kind_map.items()
                     if entry[0] in ("accessory", "fitting", "terminal", "equipment"))
    members = [_builtin(db, "BuiltInCategory", name) for name, _kind, _label in INSTANCE_CATEGORIES]

    type_cache = {}
    rows = {}

    def _count(element, kind, category):
        family, type_name, type_id = _type_names(element, doc, type_cache)
        key = u"{0} : {1}".format(family.strip(), type_name.strip())
        row = rows.get(key)
        if row is None:
            row = {"type_key": key, "family": family, "type": type_name, "kind": kind,
                   "category": category, "count": 0, "type_id": type_id}
            rows[key] = row
        row["count"] += 1

    for element in collect_instances(doc, members, db=db, clr_list=clr_list):
        kind, category = _kind_of(element, kind_map)
        _count(element, kind, category)

    probe_truncated = False
    probed = 0
    for element in _family_instances(doc, db):
        if _category_int(element) in known_ints:
            continue
        probed += 1
        if probed % PROGRESS_EVERY == 0 and clock() - started > budget_seconds:
            probe_truncated = True
            break
        if not _has_hvac_connector(element):
            continue
        _count(element, "other", _category_name(element) or u"Other")

    ordered = sorted(rows.values(), key=lambda row: (row["category"], row["type_key"]))
    return {
        "types": ordered,
        "view": view_info(doc, db),
        "meta": {"seconds": clock() - started, "probe_truncated": probe_truncated},
    }


def scan_terminal_ids_in_view(doc, view_id, db=None):
    db = _db(db)
    member = _builtin(db, "BuiltInCategory", "OST_DuctTerminal")
    if member is None or view_id is None:
        return None
    try:
        collector = _collector(doc, db, element_id_factory(db.ElementId)(view_id))
        ids = collector.OfCategory(member).WhereElementIsNotElementType().ToElementIds()
        return sorted(eid_to_int(element_id) for element_id in ids)
    except Exception:
        return None


# -------------------------------------------------------------------- scan


def scan(doc, options=None, db=None, progress=None, clock=None, clr_list=None):
    """The one pass.  ``progress(done, total)`` may return False to cancel.

    Returns the snapshot ``damper_check_state.analyze`` reads: ``elements``
    keyed by int id, ``scope_terminal_ids`` (None for the whole model) and a
    ``meta`` block that says exactly how far the pass got.
    """
    db = _db(db)
    options = options or {}
    clock = clock or time.time
    started = clock()
    budget = float(options.get("budget_seconds") or DEFAULT_BUDGET_SECONDS)
    cap = int(options.get("element_cap") or DEFAULT_ELEMENT_CAP)

    kind_map = _kind_map(db)
    insulation_ints = _insulation_ints(db)
    members = [_builtin(db, "BuiltInCategory", name) for name, _kind, _label in INSTANCE_CATEGORIES]

    worklist = []
    worklist.extend(_curve_elements(doc, db))
    worklist.extend(collect_instances(doc, members, db=db, clr_list=clr_list))
    total = len(worklist)

    elements = {}
    type_cache = {}
    level_cache = {}
    class_cache = {}
    meta = {
        "elements_read": 0,
        "connectors_read": 0,
        "seconds": 0.0,
        "truncated": False,
        "truncated_reason": u"",
        "unreadable_ids": [],
        "isconnected_mismatch": 0,
        "pulled_in": 0,
        "scope": "view" if options.get("scope") == "view" else "model",
        "view_name": safe_text(options.get("view_name")),
    }

    index = 0
    while index < len(worklist):
        element = worklist[index]
        index += 1
        try:
            element_id = eid_to_int(element.Id)
        except Exception:
            element_id = None
        if element_id is None or element_id in elements:
            continue

        if len(elements) >= cap:
            meta["truncated"] = True
            meta["truncated_reason"] = u"element cap of {0:,} reached".format(cap)
            break
        if index % PROGRESS_EVERY == 0:
            if clock() - started > budget:
                meta["truncated"] = True
                meta["truncated_reason"] = u"{0:.0f} s budget reached".format(budget)
                break
            if progress is not None:
                try:
                    keep_going = progress(index, max(total, index))
                except Exception:
                    keep_going = True
                if keep_going is False:
                    meta["truncated"] = True
                    meta["truncated_reason"] = u"cancelled"
                    break

        kind, category = _kind_of(element, kind_map)
        manager, error = _connector_manager(element)
        records = []
        partner_owners = []
        raw_connectors = []
        if manager is not None:
            records, partner_owners, mismatch, read_error = _connector_records(
                manager, element_id, db, insulation_ints, with_size=(kind == "equipment"))
            meta["isconnected_mismatch"] += mismatch
            error = error or read_error
            try:
                raw_connectors = [c for c in manager.Connectors if _is_hvac_physical(c)]
            except Exception:
                raw_connectors = []
        elif not error and kind != "other":
            error = u"no connector manager"
        if error:
            meta["unreadable_ids"].append(element_id)
        meta["connectors_read"] += len(records)

        for owner in partner_owners:
            try:
                owner_id = eid_to_int(owner.Id)
            except Exception:
                continue
            if owner_id is None or owner_id in elements:
                continue
            worklist.append(owner)
        if kind == "other":
            # Only a partner reference can bring an other-category element
            # here - the collectors above stop at the duct categories.
            meta["pulled_in"] += 1

        family, type_name, type_id = _type_names(element, doc, type_cache)
        if kind in ("duct", "flex"):
            size = _duct_size(element, db)
        elif kind == "equipment":
            size = max([record.get("size", 0.0) for record in records] or [0.0])
        else:
            size = 0.0

        name = u""
        try:
            name = safe_text(element.Name)
        except Exception:
            pass

        elements[element_id] = {
            "id": element_id,
            "kind": kind,
            "category": category,
            "family": family,
            "type": type_name,
            "type_key": u"{0} : {1}".format(family.strip(), type_name.strip()),
            "type_id": type_id,
            "level": _level_name(element, doc, db, level_cache),
            "space": _space_label(element) if kind == "terminal" else u"",
            "system_name": _param_string(element, "RBS_SYSTEM_NAME_PARAM", db),
            "system_class": _system_class(element, doc, db, class_cache, raw_connectors),
            "size": float(size),
            "connectors": records,
            "error": error,
            "name": name,
        }
        meta["elements_read"] += 1

    scope_ids = None
    if meta["scope"] == "view":
        scope_ids = scan_terminal_ids_in_view(doc, options.get("view_id"), db=db)
        if scope_ids is None:
            meta["scope"] = "model"
            meta["scope_note"] = u"The active view could not be read; the whole model was used."

    meta["seconds"] = clock() - started
    meta["unreadable_ids"] = sorted(set(meta["unreadable_ids"]))
    return {
        "schema": 1,
        "doc_title": safe_text(getattr(doc, "Title", u"")),
        "elements": elements,
        "scope_terminal_ids": scope_ids,
        "meta": meta,
    }


# -------------------------------------------------------------------- show


def show_elements(uidoc, element_ids):
    """Select the run and frame it - Circuit Schedule's helper, already in lib."""
    if _lib_show_elements is not None:
        return _lib_show_elements(uidoc, element_ids)
    return False
