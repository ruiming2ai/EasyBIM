# -*- coding: utf-8 -*-
"""The duct network as a plain graph - shared by Damper Check and Fire Damper
Check.

``duct_network_revit.scan`` hands over one snapshot of dicts; this module
turns it into nodes and an undirected adjacency and answers "how many
connections apart are these two elements?".  Mechanical equipment is a rim:
it sits in the graph but is never walked through, so a VAV box separates
its inlet side from its downstream side in every checker that uses this.

No Revit imports.  Hoisted out of Damper Check when Fire Damper Check
became the second consumer.  ``safe_text`` is a local copy on purpose: the
desktop tests load these modules without the ``easybim`` package's Revit
neighbours (see ``easybim.compat``).
"""

from __future__ import print_function

import collections


KIND_DUCT = "duct"
KIND_FLEX = "flex"
KIND_FITTING = "fitting"
KIND_ACCESSORY = "accessory"
KIND_TERMINAL = "terminal"
KIND_EQUIPMENT = "equipment"
KIND_OTHER = "other"

#: ``(key, label)`` - Supply / Return / Exhaust as ducts report them.
#: Anything else lands in Unknown so a system-less branch is never filtered
#: out of sight.
SYSTEM_CLASSES = (
    ("SupplyAir", u"Supply"),
    ("ReturnAir", u"Return"),
    ("ExhaustAir", u"Exhaust"),
    ("OtherAir", u"Other"),
    ("Unknown", u"Unknown"),
)
SYSTEM_CLASS_KEYS = tuple(key for key, _label in SYSTEM_CLASSES)


def safe_text(value):
    """Best-effort unicode that never raises."""
    if value is None:
        return u""
    try:
        return u"{0}".format(value)
    except Exception:
        try:
            return value.decode("utf-8", "replace")
        except Exception:
            return u""


def to_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def type_key(family, type_name):
    """``Family : Type`` - the by-name identity a ticked choice travels under."""
    return u"{0} : {1}".format(safe_text(family).strip(), safe_text(type_name).strip())


def normalize_system_class(value):
    text = safe_text(value).strip()
    for key, _label in SYSTEM_CLASSES:
        if text == key:
            return key
    compact = text.replace(u" ", u"").lower()
    for key, _label in SYSTEM_CLASSES:
        if compact and compact == key.lower():
            return key
    return "Unknown"


# ------------------------------------------------------------------ graph


def element_label(record):
    key = safe_text(record.get("type_key"))
    if not key.strip(u" :"):
        key = safe_text(record.get("name")) or safe_text(record.get("category")) or u"Element"
    return key


def make_node(element_id, record, marked_types):
    """One graph node.  ``is_damper`` means "of a ticked type" - what the
    tick means (isolation damper, fire damper) is the checker's business."""
    kind = safe_text(record.get("kind")) or KIND_OTHER
    key = safe_text(record.get("type_key"))
    open_end = False
    for connector in record.get("connectors") or []:
        if safe_text(connector.get("type")) != u"End":
            continue  # a duct's Curve connector is never an open end
        if not connector.get("connected") and not connector.get("partners"):
            open_end = True
            break
    return {
        "id": element_id,
        "kind": kind,
        "type_key": key,
        "label": element_label(record),
        "is_damper": bool(key) and key in marked_types,
        "is_terminal": kind == KIND_TERMINAL,
        "is_equipment": kind == KIND_EQUIPMENT,
        "is_duct": kind in (KIND_DUCT, KIND_FLEX),
        "size": to_float(record.get("size")),
        "open_end": open_end,
        "error": bool(safe_text(record.get("error"))),
        "level": safe_text(record.get("level")),
        "space": safe_text(record.get("space")),
        "system_name": safe_text(record.get("system_name")),
        "system_class": normalize_system_class(record.get("system_class")),
    }


def stub_node(element_id):
    """A partner the scan never managed to read: unreadable, no connectors of
    its own, but the edge to it survives so the branch is not cut there."""
    return {
        "id": element_id, "kind": KIND_OTHER, "type_key": u"",
        "label": u"Element {0} (unread)".format(element_id),
        "is_damper": False, "is_terminal": False, "is_equipment": False,
        "is_duct": False, "size": 0.0, "open_end": False, "error": True,
        "level": u"", "space": u"", "system_name": u"", "system_class": "Unknown",
    }


def build_graph(snapshot, config):
    """Snapshot -> ``{"nodes", "adjacency", "edges"}``.

    ``config["damper_types"]`` is the set of ticked ``Family : Type`` keys;
    nodes of those types carry ``is_damper``.  Edges are deduplicated on
    the unordered pair, so a tap and its main are joined exactly once.
    """
    elements = (snapshot or {}).get("elements") or {}
    marked = (config or {}).get("damper_types") or set()
    nodes = {}
    adjacency = {}
    edges = set()

    for element_id, record in elements.items():
        element_id = to_int(element_id, None)
        if element_id is None:
            continue
        nodes[element_id] = make_node(element_id, record, marked)
        adjacency.setdefault(element_id, set())

    for element_id, record in elements.items():
        element_id = to_int(element_id, None)
        if element_id is None:
            continue
        for connector in record.get("connectors") or []:
            for partner in connector.get("partners") or []:
                partner_id = to_int(partner[0] if partner else None, None)
                if partner_id is None or partner_id == element_id:
                    continue
                if partner_id not in nodes:
                    nodes[partner_id] = stub_node(partner_id)
                    adjacency.setdefault(partner_id, set())
                pair = (min(element_id, partner_id), max(element_id, partner_id))
                edges.add(pair)
                adjacency[element_id].add(partner_id)
                adjacency[partner_id].add(element_id)

    return {"nodes": nodes, "adjacency": adjacency, "edges": edges}


def hop_distances(graph, start_id, max_hops):
    """``{element id: hops}`` for everything within ``max_hops`` connections
    of ``start_id``.  Equipment is reached but never walked through, and an
    unread stub is walked through - a missing read must not cut a run."""
    nodes = graph.get("nodes") or {}
    adjacency = graph.get("adjacency") or {}
    if start_id not in adjacency:
        return {start_id: 0} if start_id in nodes else {}
    found = {start_id: 0}
    queue = collections.deque([start_id])
    while queue:
        current = queue.popleft()
        hops = found[current]
        if hops >= max_hops:
            continue
        node = nodes.get(current) or {}
        if node.get("is_equipment") and current != start_id:
            continue
        for neighbour in adjacency.get(current, ()):
            if neighbour in found:
                continue
            found[neighbour] = hops + 1
            queue.append(neighbour)
    return found
