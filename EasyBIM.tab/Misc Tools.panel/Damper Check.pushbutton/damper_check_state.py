# -*- coding: utf-8 -*-
"""Pure logic for Damper Check: the duct graph, the walk, the verdicts.

Nothing in here touches Revit.  ``damper_check_revit.scan`` hands over one
snapshot of plain dicts - ints, floats and unicode only - and everything
that decides whether a terminal is isolated happens here, where the desktop
tests can drive it without an engine.

The question the tool answers is "does every end branch carry a damper?",
and the two words that need pinning down are decided here:

* An **end branch** is judged on the *physical* connector graph, never on
  system membership.  Ducts, flex ducts, fittings, accessories, terminals and
  equipment are nodes; a connected pair of physical connectors is an edge.
  Mechanical equipment is a rim node - it sits in the graph but the walk
  never passes through it, so a VAV box splits its inlet side from its
  downstream side and each downstream run roots at the VAV.  A terminal's
  end branch is the run from the terminal toward the trunk up to the nearest
  damper, or, when there is none, up to the first junction serving more
  terminals than the limit N, or the root (equipment, an open end, or nothing).

* A **damper** is whatever family type the user ticked as one.  Keywords only
  pre-tick the list; the ticked ``Family : Type`` names travel by name in the
  settings file because an ElementId means nothing in the next document.

Every terminal lands in exactly one named bucket.  Skips - unreadable
connectors, an unconnected terminal, a walk cut by a guard - are buckets of
their own so nothing is ever dropped silently.
"""

from __future__ import print_function

from easybim import type_checklist
from easybim.duct_network_state import KIND_ACCESSORY
from easybim.duct_network_state import KIND_DUCT
from easybim.duct_network_state import KIND_EQUIPMENT
from easybim.duct_network_state import KIND_FITTING
from easybim.duct_network_state import KIND_FLEX
from easybim.duct_network_state import KIND_OTHER
from easybim.duct_network_state import KIND_TERMINAL
from easybim.duct_network_state import SYSTEM_CLASSES
from easybim.duct_network_state import SYSTEM_CLASS_KEYS
from easybim.duct_network_state import build_graph
from easybim.duct_network_state import normalize_system_class
from easybim.duct_network_state import safe_text
from easybim.duct_network_state import to_float as _float
from easybim.duct_network_state import to_int as _int
from easybim.duct_network_state import type_key
from easybim.type_checklist import keyword_match

# Re-exported for the tests and the UI, which reach them through this module.
__all_shared__ = (KIND_DUCT, KIND_FLEX, KIND_FITTING, KIND_ACCESSORY, KIND_TERMINAL,
                  KIND_EQUIPMENT, KIND_OTHER, SYSTEM_CLASSES, SYSTEM_CLASS_KEYS,
                  build_graph, normalize_system_class, safe_text, type_key, keyword_match)

#: Categories the setup checklist shows first, in this order; every other
#: category discovered in the model follows alphabetically, collapsed.
CATEGORY_ORDER = (
    u"Duct Accessories",
    u"Duct Fittings",
    u"Air Terminals",
    u"Mechanical Equipment",
)
EXPANDED_CATEGORIES = (u"Duct Accessories",)

#: Pre-tick keywords for Duct Accessories.  Exclusions win, so a "Fire
#: Damper" stays unticked until the user says otherwise.
DEFAULT_INCLUDE_KEYWORDS = (
    u"damper", u"vcd", u"obd", u"bdd", u"isolation", u"shut-off", u"shutoff",
    u"shut off", u"volume", u"balancing", u"butterfly", u"blast gate",
)
DEFAULT_EXCLUDE_KEYWORDS = (
    u"fire", u"smoke", u"backdraft", u"back-draft", u"relief", u"gravity",
    u"access", u"silencer", u"attenuator", u"flex connector",
)
#: Outside Duct Accessories only the word itself pre-ticks ("Tap with
#: Damper", an "OBD" terminal); equipment is never pre-ticked - a VAV box
#: isolating its run is the user's call, not a keyword's.
NON_ACCESSORY_KEYWORDS = (u"damper",)

DEFAULT_THRESHOLD = 1
MAX_THRESHOLD = 9999
#: A guard bug must cost a truncated branch, never a hung Revit.
MAX_NODES = 250000
MAX_CLIMB = 10000
SHOW_ID_CAP = 400
ROW_CAP = type_checklist.ROW_CAP

#: Report search: names by substring over these, ids as whole tokens.
SEARCH_TEXT_FIELDS = ("title", "detail", "system_name", "level", "space", "label")
SEARCH_ID_FIELDS = ("terminal_id", "damper_id")

SCOPE_MODEL = "model"
SCOPE_VIEW = "view"

# stop reasons a terminal's walk can end with
STOP_DAMPER = "damper"
STOP_ROOT = "root"
STOP_INTEGRAL = "integral"
STOP_UNCONNECTED = "unconnected"
STOP_TRUNCATED = "truncated"
STOP_UNREADABLE = "unreadable"

# how a network's root was chosen
ROOT_EQUIPMENT = "equipment"
ROOT_OPEN_END = "open_end"
ROOT_ISLAND = "island"
ROOT_ANY = "any"

#: ``(key, title, is_problem)`` in report order - problems first.
BUCKETS = (
    ("no_damper_equipment", u"No damper between terminal and equipment", True),
    ("no_damper_open_end", u"No damper - branch not connected to a main (open end)", True),
    ("no_damper_island", u"No damper - network reaches no equipment", True),
    ("damper_too_far", u"Nearest damper serves more terminals than the limit", True),
    ("terminal_unconnected", u"Terminal not connected to any duct", True),
    ("truncated", u"Walk truncated by a guard - not judged", True),
    ("unreadable", u"Connectors unreadable - not judged", True),
    ("covered_shared", u"Covered - damper shared within the limit", False),
    ("covered_single", u"Covered - own damper", False),
    ("covered_integral", u"Covered - terminal type has an integral damper", False),
    ("covered_by_equipment", u"Covered - served by isolating equipment", False),
    ("ignored", u"Ignored - set aside on review", False),
)

#: The bucket a row lands in once you have set it aside.  It is never a
#: problem, and it never counts toward the summary's tally, which is the
#: whole point of ignoring something.
IGNORED_BUCKET = "ignored"
BUCKET_TITLES = dict((key, title) for key, title, _problem in BUCKETS)
PROBLEM_BUCKETS = tuple(key for key, _title, problem in BUCKETS if problem)


# ---------------------------------------------------------------- helpers


def clamp_threshold(value):
    number = _int(value, DEFAULT_THRESHOLD)
    if number < 1:
        return 1
    if number > MAX_THRESHOLD:
        return MAX_THRESHOLD
    return number


# ------------------------------------------------------- type checklist


def keyword_preselect(row):
    """What the keywords alone would decide for one catalog row."""
    kind = row.get("kind")
    if kind == KIND_EQUIPMENT:
        return False, u""
    include = DEFAULT_INCLUDE_KEYWORDS if kind == KIND_ACCESSORY else NON_ACCESSORY_KEYWORDS
    return keyword_match(row.get("type_key"), include, DEFAULT_EXCLUDE_KEYWORDS)


def preselect_types(type_rows, settings):
    """Catalog rows -> rows with ``is_checked`` and a ``reason``.

    A choice saved by name wins over the keywords in both directions, so
    a damper the user unticked once does not come back ticked next time.
    """
    settings = settings or {}
    rows = type_checklist.preselect_rows(
        type_rows, settings.get("damper_types"), settings.get("unticked_types"),
        keyword_preselect)
    return type_checklist.sort_rows(rows, CATEGORY_ORDER)


def group_types(rows):
    """Rows -> groups with Duct Accessories first and expanded."""
    return type_checklist.group_rows(rows, CATEGORY_ORDER, EXPANDED_CATEGORIES)


def selection_count_text(rows):
    return type_checklist.count_text(rows, u"types ticked as dampers")


def apply_setup(settings, rows, threshold=None, scope=None, system_classes=None):
    """Fold the setup window's choices into a new settings dict.

    Names from other models survive: a type absent from this model keeps
    whatever was saved for it, so one settings file serves a whole content
    library.
    """
    settings = dict(settings or {})
    saved, unticked = type_checklist.fold_choices(
        settings.get("damper_types"), settings.get("unticked_types"), rows, keyword_preselect)
    settings["damper_types"] = saved
    settings["unticked_types"] = unticked
    if threshold is not None:
        settings["threshold"] = clamp_threshold(threshold)
    if scope is not None:
        settings["scope"] = SCOPE_VIEW if scope == SCOPE_VIEW else SCOPE_MODEL
    if system_classes is not None:
        chosen = [key for key in SYSTEM_CLASS_KEYS if key in set(system_classes)]
        settings["system_classes"] = chosen
    return settings


def config_from_settings(settings):
    settings = settings or {}
    classes = settings.get("system_classes")
    if classes is None:
        classes = list(SYSTEM_CLASS_KEYS)
    return {
        "damper_types": set(safe_text(key) for key in settings.get("damper_types") or []),
        "threshold": clamp_threshold(settings.get("threshold", DEFAULT_THRESHOLD)),
        "system_classes": set(normalize_system_class(key) for key in classes),
        "scope": SCOPE_VIEW if settings.get("scope") == SCOPE_VIEW else SCOPE_MODEL,
    }


# ------------------------------------------------------------------ graph
#
# ``build_graph`` lives in ``easybim.duct_network_state``; what follows is
# Damper Check's own reading of that graph.


def find_components(graph):
    """Connected pieces of the network, never expanding through equipment.

    Each is ``{"nodes": set, "rims": set}`` - the equipment on its rim is
    listed but not owned, so one VAV box borders two components.
    """
    nodes = graph["nodes"]
    adjacency = graph["adjacency"]
    seen = set()
    components = []
    for start in sorted(nodes):
        if start in seen or nodes[start]["is_equipment"]:
            continue
        members = set()
        rims = set()
        queue = [start]
        seen.add(start)
        while queue:
            current = queue.pop()
            members.add(current)
            for neighbour in adjacency.get(current, ()):
                if nodes[neighbour]["is_equipment"]:
                    rims.add(neighbour)
                    continue
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                queue.append(neighbour)
        components.append({"nodes": members, "rims": rims})
    return components


def choose_root(graph, component):
    """``(root_id, root_kind)`` - the trunk end the walk climbs toward."""
    nodes = graph["nodes"]
    adjacency = graph["adjacency"]
    members = component["nodes"]

    best = None
    for rim in sorted(component["rims"]):
        neighbour_size = 0.0
        for neighbour in adjacency.get(rim, ()):
            if neighbour in members:
                neighbour_size = max(neighbour_size, nodes[neighbour]["size"])
        candidate = (neighbour_size, -rim)
        if best is None or candidate > best[0]:
            best = (candidate, rim)
    if best is not None:
        return best[1], ROOT_EQUIPMENT

    open_ducts = [nid for nid in members if nodes[nid]["is_duct"] and nodes[nid]["open_end"]]
    if open_ducts:
        return _largest(nodes, open_ducts), ROOT_OPEN_END

    ducts = [nid for nid in members if nodes[nid]["is_duct"]]
    if ducts:
        return _largest(nodes, ducts), ROOT_ISLAND

    return min(members), ROOT_ANY


def _largest(nodes, ids):
    return sorted(ids, key=lambda nid: (-nodes[nid]["size"], nid))[0]


def walk_component(graph, component, root, max_nodes=MAX_NODES):
    """Iterative DFS from the root: ``parent``, ``under`` (terminals in each
    subtree), loop and truncation flags.  Iterative because IronPython's
    recursion limit is shallower than a long duct run."""
    nodes = graph["nodes"]
    adjacency = graph["adjacency"]
    members = set(component["nodes"]) | set(component["rims"])

    parent = {root: None}
    order = []
    visited = set([root])
    has_loops = False
    truncated = False

    stack = [(root, iter(sorted(adjacency.get(root, ()))))]
    while stack and not truncated:
        current, neighbours = stack[-1]
        advanced = False
        for neighbour in neighbours:
            if neighbour not in members:
                continue
            if neighbour in visited:
                if neighbour != parent.get(current):
                    has_loops = True
                continue
            visited.add(neighbour)
            parent[neighbour] = current
            if len(visited) > max_nodes:
                truncated = True
                break
            if nodes[neighbour]["is_equipment"]:
                order.append(neighbour)  # a rim: a leaf, never expanded
                continue
            stack.append((neighbour, iter(sorted(adjacency.get(neighbour, ())))))
            advanced = True
            break
        if not advanced and not truncated:
            order.append(current)
            stack.pop()

    if truncated:
        # Close out whatever is still open so the counts below stay sane.
        while stack:
            order.append(stack.pop()[0])

    under = {}
    for node_id in order:
        under[node_id] = under.get(node_id, 0) + (1 if nodes[node_id]["is_terminal"] else 0)
        parent_id = parent.get(node_id)
        if parent_id is not None:
            under[parent_id] = under.get(parent_id, 0) + under[node_id]

    return {
        "root": root,
        "parent": parent,
        "under": under,
        "has_loops": has_loops,
        "truncated": truncated,
        "visited": len(visited),
    }


def trace_terminal(graph, walk, terminal_id, max_climb=MAX_CLIMB):
    """One terminal's climb toward the root, stopping at the nearest damper."""
    nodes = graph["nodes"]
    node = nodes[terminal_id]
    trace = {
        "id": terminal_id,
        "path_ids": [terminal_id],
        "path_under": [walk["under"].get(terminal_id, 0)] if walk else [0],
        "damper_index": None,
        "damper_id": None,
        "damper_label": u"",
        "stop": STOP_ROOT,
    }
    if node["error"]:
        trace["stop"] = STOP_UNREADABLE
        return trace
    if not graph["adjacency"].get(terminal_id):
        trace["stop"] = STOP_UNCONNECTED
        return trace
    if node["is_damper"]:
        trace["stop"] = STOP_INTEGRAL
        return trace
    if walk is None or terminal_id not in walk["parent"]:
        trace["stop"] = STOP_TRUNCATED
        return trace

    parent = walk["parent"]
    under = walk["under"]
    current = terminal_id
    steps = 0
    while parent.get(current) is not None:
        current = parent[current]
        steps += 1
        if steps > max_climb:
            trace["stop"] = STOP_TRUNCATED
            return trace
        trace["path_ids"].append(current)
        trace["path_under"].append(under.get(current, 0))
        if nodes[current]["is_damper"] and not nodes[current]["is_equipment"]:
            trace["damper_index"] = len(trace["path_ids"]) - 1
            trace["damper_id"] = current
            trace["damper_label"] = nodes[current]["label"]
            trace["stop"] = STOP_DAMPER
            return trace
    trace["stop"] = STOP_ROOT
    return trace


# --------------------------------------------------------------- analysis


def analyze(snapshot, config, max_nodes=MAX_NODES, max_climb=MAX_CLIMB):
    """Run the walk once per scan.  ``classify`` then applies the live N."""
    snapshot = snapshot or {}
    config = config or {}
    graph = build_graph(snapshot, config)
    nodes = graph["nodes"]
    meta = dict(snapshot.get("meta") or {})

    scope_ids = snapshot.get("scope_terminal_ids")
    scope_set = None
    if scope_ids is not None:
        scope_set = set(_int(value, None) for value in scope_ids)
    wanted_classes = config.get("system_classes")
    if wanted_classes is None:
        wanted_classes = set(SYSTEM_CLASS_KEYS)

    skips = {
        "unreadable_ids": sorted(nid for nid in nodes if nodes[nid]["error"]),
        "out_of_scope_terminals": 0,
        "class_filtered_terminals": 0,
    }

    walks_by_node = {}
    groups = []
    group_of = {}
    for index, component in enumerate(find_components(graph)):
        root, root_kind = choose_root(graph, component)
        walk = walk_component(graph, component, root, max_nodes=max_nodes)
        key = "c{0}".format(index)
        members = component["nodes"]
        for nid in members:
            walks_by_node[nid] = walk
            group_of[nid] = key
        groups.append({
            "key": key,
            "root_id": root,
            "root_kind": root_kind,
            "root_label": nodes[root]["label"],
            "root_is_damper": bool(nodes[root]["is_damper"]),
            "also_attached": sorted(rim for rim in component["rims"] if rim != root),
            "node_count": len(members),
            "terminal_count": len([nid for nid in members if nodes[nid]["is_terminal"]]),
            "damper_count": len([nid for nid in members if nodes[nid]["is_damper"]]),
            "has_loops": walk["has_loops"],
            "truncated": walk["truncated"],
            "has_unreadable": any(nodes[nid]["error"] for nid in members),
        })
    groups_by_key = dict((group["key"], group) for group in groups)

    terminals = []
    for nid in sorted(nodes):
        node = nodes[nid]
        if not node["is_terminal"]:
            continue
        if scope_set is not None and nid not in scope_set:
            skips["out_of_scope_terminals"] += 1
            continue
        if node["system_class"] not in wanted_classes:
            skips["class_filtered_terminals"] += 1
            continue
        walk = walks_by_node.get(nid)
        trace = trace_terminal(graph, walk, nid, max_climb=max_climb)
        group_key = group_of.get(nid)
        group = groups_by_key.get(group_key) or {}
        trace.update({
            "group": group_key,
            "label": node["label"],
            "level": node["level"],
            "space": node["space"],
            "system_name": node["system_name"],
            "system_class": node["system_class"],
            "root_kind": group.get("root_kind", ROOT_ANY),
            "root_id": group.get("root_id"),
            "root_label": group.get("root_label", u""),
            "root_is_damper": bool(group.get("root_is_damper")),
            "approx": bool(group.get("has_loops")),
        })
        terminals.append(trace)

    damper_ids = [nid for nid in nodes if nodes[nid]["is_damper"] and not nodes[nid]["is_equipment"]]
    idle = 0
    for nid in damper_ids:
        walk = walks_by_node.get(nid)
        if walk is None or walk["under"].get(nid, 0) == 0:
            idle += 1

    return {
        "groups": groups,
        "terminals": terminals,
        "stats": {
            "nodes": len(nodes),
            "edges": len(graph["edges"]),
            "dampers": len(damper_ids),
            "idle_dampers": idle,
            "terminals": len(terminals),
            "pulled_in": _int(meta.get("pulled_in")),
        },
        "skips": skips,
        "meta": meta,
    }


# ------------------------------------------------------------- verdicts


def _bucket_for(trace, threshold):
    stop = trace.get("stop")
    if stop == STOP_UNCONNECTED:
        return "terminal_unconnected"
    if stop == STOP_UNREADABLE:
        return "unreadable"
    if stop == STOP_TRUNCATED:
        return "truncated"
    if stop == STOP_INTEGRAL:
        return "covered_integral"
    if stop == STOP_DAMPER:
        served = trace["path_under"][trace["damper_index"]]
        if served <= 1:
            return "covered_single"
        if served <= threshold:
            return "covered_shared"
        return "damper_too_far"
    root_kind = trace.get("root_kind")
    if root_kind == ROOT_EQUIPMENT:
        if trace.get("root_is_damper"):
            return "covered_by_equipment"
        return "no_damper_equipment"
    if root_kind == ROOT_OPEN_END:
        return "no_damper_open_end"
    return "no_damper_island"


def _junction_index(trace, threshold):
    for index, served in enumerate(trace.get("path_under") or []):
        if served > threshold:
            return index
    return None


def show_ids_for(trace, threshold, cap=SHOW_ID_CAP):
    """The run to select: to the damper, else to the junction, else to the root."""
    path = list(trace.get("path_ids") or [])
    if trace.get("damper_index") is not None:
        path = path[:trace["damper_index"] + 1]
    else:
        junction = _junction_index(trace, threshold)
        if junction is not None:
            path = path[:junction + 1]
    return path[:cap]


def _detail_for(trace, bucket, threshold):
    root = u"{0} (id {1})".format(trace.get("root_label") or u"?", trace.get("root_id"))
    if bucket in ("covered_single", "covered_shared", "damper_too_far"):
        served = trace["path_under"][trace["damper_index"]]
        text = u"Nearest damper {0} (id {1}) serves {2} terminal{3}".format(
            trace.get("damper_label") or u"?", trace.get("damper_id"), served,
            u"" if served == 1 else u"s")
        if bucket == "damper_too_far":
            text += u" - limit {0}".format(threshold)
        return text
    if bucket == "covered_integral":
        return u"The terminal type itself is ticked as a damper"
    if bucket == "covered_by_equipment":
        return u"Served by {0}, ticked as isolating".format(root)
    if bucket == "no_damper_equipment":
        return _no_damper_text(trace, threshold, u"reaches equipment {0}".format(root))
    if bucket == "no_damper_open_end":
        return _no_damper_text(trace, threshold, u"ends at open duct {0}".format(root))
    if bucket == "no_damper_island":
        return _no_damper_text(trace, threshold, u"network reaches no equipment (rooted at {0})".format(root))
    if bucket == "terminal_unconnected":
        return u"No duct connector on this terminal is connected"
    if bucket == "unreadable":
        return u"The terminal's connectors could not be read"
    if bucket == "truncated":
        return u"A guard stopped the walk before a verdict - narrow the scope"
    return u""


def _no_damper_text(trace, threshold, ending):
    junction = _junction_index(trace, threshold)
    if junction is not None and junction < len(trace["path_ids"]):
        served = trace["path_under"][junction]
        return u"No damper before the junction at id {0} serving {1} terminals; run {2}".format(
            trace["path_ids"][junction], served, ending)
    return u"No damper on the run; it {0}".format(ending)


def _row_title(trace):
    parts = [trace.get("system_name") or u"No system"]
    if trace.get("level"):
        parts.append(trace["level"])
    if trace.get("space"):
        parts.append(trace["space"])
    parts.append(u"{0} (id {1})".format(trace.get("label") or u"Terminal", trace.get("id")))
    return u" · ".join(parts)


def finding_key(trace):
    """The name a set-aside decision is stored under, inside the model.

    A terminal is what the user judges, so the terminal is what the key
    names; the verdict may change on the next scan and the decision still
    means "I have looked at this one".
    """
    return u"terminal:{0}".format(_int(trace.get("id")))


def classify(analysis, threshold, ignored=None):
    """Analysis + live N + the set-aside keys -> the report the window draws."""
    threshold = clamp_threshold(threshold)
    analysis = analysis or {}
    ignored = set(safe_text(key) for key in ignored or [])
    seen_keys = set()
    items_by_bucket = dict((key, []) for key, _title, _problem in BUCKETS)
    for trace in analysis.get("terminals") or []:
        bucket = _bucket_for(trace, threshold)
        key = finding_key(trace)
        seen_keys.add(key)
        original = bucket
        if key in ignored:
            bucket = IGNORED_BUCKET
        row = {
            "key": key,
            "original_bucket": original,
            "is_ignored": bucket == IGNORED_BUCKET,
            "terminal_id": trace.get("id"),
            "title": _row_title(trace),
            "detail": _detail_for(trace, bucket, threshold),
            "show_ids": show_ids_for(trace, threshold),
            "group": trace.get("group"),
            "approx": bool(trace.get("approx")),
            "bucket": bucket,
            "system_name": trace.get("system_name") or u"",
            "system_class": trace.get("system_class") or "Unknown",
            "level": trace.get("level") or u"",
            "space": trace.get("space") or u"",
            "label": trace.get("label") or u"",
            "damper_id": trace.get("damper_id"),
        }
        if row["is_ignored"]:
            row["detail"] = u"Set aside on review - was \"{0}\". {1}".format(
                BUCKET_TITLES.get(original, original), row["detail"])
        items_by_bucket[bucket].append(row)

    buckets = []
    counts = {}
    for key, title, problem in BUCKETS:
        items = items_by_bucket[key]
        items.sort(key=lambda item: (item["system_name"].lower(), item["level"].lower(),
                                     item["label"].lower(), item["terminal_id"]))
        counts[key] = len(items)
        buckets.append({"key": key, "title": title, "is_problem": problem, "items": items})

    problems = sum(counts[key] for key in PROBLEM_BUCKETS)
    return {
        "buckets": buckets,
        "counts": counts,
        "threshold": threshold,
        "problem_count": problems,
        "terminal_count": len(analysis.get("terminals") or []),
        "ignored_count": counts[IGNORED_BUCKET],
        # Set aside once, then fixed or gone: the record still names them, so
        # the notes can say so rather than letting the count drift unexplained.
        "stale_ignored": sorted(ignored - seen_keys),
    }


# ---------------------------------------------------------------- search


def tokens(text):
    return type_checklist.tokens(text)


def matches(query, item):
    """Every query token must hit: numbers as whole id tokens ("12" does
    not find 112), words as substrings of the names."""
    return type_checklist.matches(query, item, SEARCH_TEXT_FIELDS, SEARCH_ID_FIELDS)


def filter_report(report, query):
    """The same report with only matching rows; counts follow the rows."""
    return type_checklist.filter_report(report, query, PROBLEM_BUCKETS,
                                        SEARCH_TEXT_FIELDS, SEARCH_ID_FIELDS)


def cap_items(items, cap=ROW_CAP):
    """``(shown, hidden_count)`` - branches never grow past the cap."""
    return type_checklist.cap_items(items, cap)


# ---------------------------------------------------------------- status


def summary_line(analysis, report):
    analysis = analysis or {}
    report = report or {}
    meta = analysis.get("meta") or {}
    stats = analysis.get("stats") or {}
    skips = analysis.get("skips") or {}
    seconds = _float(meta.get("seconds"))
    parts = [u"Read {0:,} elements in {1:.1f} s".format(_int(meta.get("elements_read")), seconds)]
    total = _int(report.get("terminal_count"))
    problems = _int(report.get("problem_count"))
    if total == 0:
        parts.append(u"no air terminals in scope")
    elif problems == 0:
        parts.append(u"all {0} terminals in scope are isolated".format(total))
    else:
        parts.append(u"{0} of {1} terminals lack a damper".format(problems, total))
    unreadable = len(skips.get("unreadable_ids") or [])
    if unreadable:
        parts.append(u"{0} unreadable (listed)".format(unreadable))
    idle = _int(stats.get("idle_dampers"))
    if idle:
        parts.append(u"{0} damper{1} isolating nothing".format(idle, u"" if idle == 1 else u"s"))
    set_aside = _int(report.get("ignored_count"))
    if set_aside:
        parts.append(u"{0} set aside".format(set_aside))
    text = u" - ".join(parts) + u"."
    if meta.get("truncated"):
        text += u" Scan truncated: {0}".format(safe_text(meta.get("truncated_reason")) or u"budget reached")
        if not text.endswith(u"."):
            text += u"."
    return text + u" The scan changes nothing in the model."


def excluded_note(config):
    """Footer note naming what the chips left out, never silently absent."""
    chosen = (config or {}).get("system_classes")
    if chosen is None:
        return u""
    left_out = [label for key, label in SYSTEM_CLASSES if key not in chosen]
    if not left_out:
        return u""
    return u"Excluded by the scope chips: {0}.".format(u", ".join(left_out))
