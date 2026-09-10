# -*- coding: utf-8 -*-
"""Pure logic for Fire Damper Check: which barriers count, where the ducts
cross them, and whether a fire damper is there.

Nothing in here touches Revit.  ``duct_network_revit.scan`` hands over the
ducts and the ticked dampers (with their location curves and connector
origins), ``link_crossings`` hands over the rated barriers and every solid
crossing, and everything that decides a verdict happens here, where the
desktop tests can drive it.

The three questions the user asked are answered as data:

* **Which walls are rated** - a per-link choice folded from three sources:
  a rating parameter read by name (non-empty means rated), the wall and
  floor types the user ticked (keywords only pre-tick), and the drawn
  fire-rating lines whose line styles the user ticked.  Every choice is
  remembered by *name per link title*, never by ElementId.
* **What an end-of-duct-at-a-wall looks like** - crossings within 1.5 m of
  each other on one barrier are one *cluster* (the duct that stops at the
  damper's flange and the damper whose body sits in the wall are the same
  event), and every ticked fire damper is attached to the cluster with its
  distance, its connector hops from the crossing duct, and whether its own
  body crosses the barrier.
* **What counts as covered** - the user's rule: a ticked damper within the
  tolerance on the same run (within the hop limit), or one whose body is in
  the barrier.  A damper within tolerance on *another* run is its own
  problem bucket, so two parallel ducts through one wall cannot share one.

``analyze`` runs once per scan; ``classify`` is arithmetic, so the report's
tolerance and hop boxes re-verdict without a rescan.
"""

from __future__ import print_function

import math

from easybim import type_checklist
from easybim.duct_network_state import KIND_ACCESSORY
from easybim.duct_network_state import KIND_DUCT
from easybim.duct_network_state import KIND_EQUIPMENT
from easybim.duct_network_state import KIND_FITTING
from easybim.duct_network_state import KIND_FLEX
from easybim.duct_network_state import KIND_OTHER
from easybim.duct_network_state import KIND_TERMINAL
from easybim.duct_network_state import build_graph
from easybim.duct_network_state import hop_distances
from easybim.duct_network_state import safe_text
from easybim.duct_network_state import to_float as _float
from easybim.duct_network_state import to_int as _int
from easybim.type_checklist import keyword_match


MM = 1.0 / 304.8

#: Fire dampers: the accessories (and fittings, and odd categories) that
#: carry these words; a smoke *detector* is not a damper.
DAMPER_KEYWORDS = (u"fire", u"smoke", u"fsd", u"fd", u"combination")
DAMPER_EXCLUDE = (u"detector", u"sensor", u"alarm", u"stat")
DAMPER_WHOLE_WORD = (u"fd", u"fsd")

WALL_TYPE_KEYWORDS = (u"fire", u"rated", u"fr", u"hr", u"fw", u"1h", u"2h", u"1hr", u"2hr", u"3hr", u"4hr")
WALL_TYPE_WHOLE_WORD = (u"fr", u"hr", u"fw", u"1h", u"2h", u"1hr", u"2hr", u"3hr", u"4hr")
LINE_STYLE_KEYWORDS = (u"fire", u"rating", u"rated", u"hr")
LINE_STYLE_WHOLE_WORD = (u"hr",)

DEFAULT_RATING_PARAM = u"Fire Rating"

CATEGORY_ORDER = (u"Duct Accessories", u"Duct Fittings", u"Air Terminals", u"Mechanical Equipment")
EXPANDED_CATEGORIES = (u"Duct Accessories",)
BARRIER_ORDER = (u"Walls", u"Floors")

DEFAULT_NEAR_MM = 600
DEFAULT_HOPS = 3
MAX_HOPS = 20
MAX_NEAR_MM = 100000
#: Crossings closer than this on one barrier are one event.
CLUSTER_FT = 1500.0 * MM
#: Dampers farther than this from every crossing are not even candidates.
CANDIDATE_REACH_FT = 6000.0 * MM
SHOW_ID_CAP = 50

SCOPE_MODEL = "model"
SCOPE_VIEW = "view"

#: ``(key, title, is_problem)`` in report order: problems, reviews, covered.
BUCKETS = (
    ("missing", u"Duct crosses a rated barrier with no fire damper", True),
    ("flex_crossing", u"Flex duct crosses a rated barrier", True),
    ("other_run_damper", u"A fire damper is within tolerance but on another run", True),
    ("fitting_crossing", u"A fitting overlaps a rated barrier - review", False),
    ("along_barrier", u"Duct runs along or inside a rated barrier - review", False),
    ("barrier_unreadable", u"Barrier geometry unreadable - not judged", False),
    ("covered_in_barrier", u"Covered - fire damper in the barrier", False),
    ("covered_within", u"Covered - fire damper within tolerance on the run", False),
    ("idle_damper", u"Fire damper at no identified rated barrier - review", False),
    ("ignored", u"Ignored - set aside on review", False),
)

#: The bucket a row lands in once you have set it aside.  Never a problem,
#: and never counted in the summary's tally.
IGNORED_BUCKET = "ignored"
BUCKET_TITLES = dict((key, title) for key, title, _problem in BUCKETS)
PROBLEM_BUCKETS = tuple(key for key, _title, problem in BUCKETS if problem)

SEARCH_TEXT_FIELDS = ("title", "detail", "link", "barrier", "level", "system_name", "rating")
SEARCH_ID_FIELDS = ("element_ids", "damper_id", "barrier_element_id")


# ---------------------------------------------------------------- helpers


def clamp_near(value):
    number = _int(value, DEFAULT_NEAR_MM)
    if number < 1:
        return 1
    if number > MAX_NEAR_MM:
        return MAX_NEAR_MM
    return number


def clamp_hops(value):
    number = _int(value, DEFAULT_HOPS)
    if number < 0:
        return 0
    if number > MAX_HOPS:
        return MAX_HOPS
    return number


def _distance(a, b):
    return math.sqrt(sum((float(a[axis]) - float(b[axis])) ** 2 for axis in (0, 1, 2)))


def _mid(a, b):
    return [(float(a[axis]) + float(b[axis])) / 2.0 for axis in (0, 1, 2)]


def _mm(feet):
    return int(round(float(feet) / MM))


# ------------------------------------------------------- pre-selection


def damper_rule(row):
    kind = row.get("kind")
    if kind in (KIND_TERMINAL, KIND_EQUIPMENT):
        return False, u""
    return keyword_match(row.get("type_key"), DAMPER_KEYWORDS, DAMPER_EXCLUDE, DAMPER_WHOLE_WORD)


def wall_rule_for(rating_param):
    rating_param = safe_text(rating_param).strip()

    def _rule(row):
        if row.get("curtain"):
            return False, u"curtain wall - no solid body, not checked"
        if row.get("stacked"):
            return False, u"stacked wall - its members are judged"
        ratings = row.get("ratings") or {}
        if rating_param and ratings.get(rating_param):
            return True, u"rated '{0}'".format(ratings[rating_param])
        return keyword_match(row.get("type_key"), WALL_TYPE_KEYWORDS, (), WALL_TYPE_WHOLE_WORD)
    return _rule


def line_rule(row):
    return keyword_match(row.get("key"), LINE_STYLE_KEYWORDS, (), LINE_STYLE_WHOLE_WORD)


def link_settings_for(settings, link_key):
    links = (settings or {}).get("links") or {}
    entry = dict(links.get(link_key) or {})
    entry.setdefault("enabled", True)
    entry.setdefault("rating_param", DEFAULT_RATING_PARAM)
    for name in ("wall_types", "unticked_wall_types", "floor_types", "unticked_floor_types",
                 "line_styles", "unticked_line_styles"):
        entry.setdefault(name, [])
    return entry


def rating_param_options(catalog_link):
    """Names to offer, most-used first, the built-in name always present."""
    rows = list((catalog_link or {}).get("rating_params") or [])
    names = [row["name"] for row in rows]
    if DEFAULT_RATING_PARAM not in names:
        rows.append({"name": DEFAULT_RATING_PARAM, "count": 0})
    rows.sort(key=lambda row: (row["name"] != DEFAULT_RATING_PARAM, -_int(row.get("count")),
                               safe_text(row.get("name")).lower()))
    return rows


def preselect_dampers(type_rows, settings):
    settings = settings or {}
    rows = type_checklist.preselect_rows(type_rows, settings.get("damper_types"),
                                         settings.get("unticked_damper_types"), damper_rule)
    return type_checklist.sort_rows(rows, CATEGORY_ORDER)


def preselect_barrier_types(catalog_types, link_settings, kind):
    """Wall or floor type rows for one link, ticked by rating, type or keyword."""
    link_settings = link_settings or {}
    rule = wall_rule_for(link_settings.get("rating_param"))
    rows = [row for row in catalog_types or [] if row.get("kind") == kind]
    if kind == "wall":
        saved, unticked = link_settings.get("wall_types"), link_settings.get("unticked_wall_types")
    else:
        saved, unticked = link_settings.get("floor_types"), link_settings.get("unticked_floor_types")
    rows = type_checklist.preselect_rows(rows, saved, unticked, rule)
    return sorted(rows, key=lambda row: safe_text(row.get("type_key")).lower())


def preselect_line_styles(style_rows, link_settings):
    link_settings = link_settings or {}
    rows = type_checklist.preselect_rows(style_rows, link_settings.get("line_styles"),
                                         link_settings.get("unticked_line_styles"), line_rule)
    return sorted(rows, key=lambda row: safe_text(row.get("key")).lower())


def group_damper_types(rows):
    return type_checklist.group_rows(rows, CATEGORY_ORDER, EXPANDED_CATEGORIES)


def group_barrier_types(rows):
    return type_checklist.group_rows(rows, BARRIER_ORDER, BARRIER_ORDER)


def damper_count_text(rows):
    return type_checklist.count_text(rows, u"types ticked as fire dampers")


def apply_setup(settings, damper_rows, link_choices, options):
    """Fold the setup window's choices into a new settings dict.

    ``link_choices``: ``{link key: {"enabled", "rating_param", "wall_rows",
    "floor_rows", "line_rows"}}`` with the rows as ticked on screen.  Names
    from other models and other links survive.
    """
    settings = dict(settings or {})
    saved, unticked = type_checklist.fold_choices(
        settings.get("damper_types"), settings.get("unticked_damper_types"), damper_rows, damper_rule)
    settings["damper_types"] = saved
    settings["unticked_damper_types"] = unticked

    links = dict(settings.get("links") or {})
    for key, choice in (link_choices or {}).items():
        entry = link_settings_for(settings, key)
        entry["enabled"] = bool(choice.get("enabled", True))
        entry["rating_param"] = safe_text(choice.get("rating_param")).strip()
        rule = wall_rule_for(entry["rating_param"])
        entry["wall_types"], entry["unticked_wall_types"] = type_checklist.fold_choices(
            entry["wall_types"], entry["unticked_wall_types"], choice.get("wall_rows"), rule)
        entry["floor_types"], entry["unticked_floor_types"] = type_checklist.fold_choices(
            entry["floor_types"], entry["unticked_floor_types"], choice.get("floor_rows"), rule)
        entry["line_styles"], entry["unticked_line_styles"] = type_checklist.fold_choices(
            entry["line_styles"], entry["unticked_line_styles"], choice.get("line_rows"), line_rule)
        links[safe_text(key)] = entry
    settings["links"] = links

    options = options or {}
    if "near_mm" in options:
        settings["near_mm"] = clamp_near(options["near_mm"])
    if "hops" in options:
        settings["hops"] = clamp_hops(options["hops"])
    if "scope" in options:
        settings["scope"] = SCOPE_VIEW if options["scope"] == SCOPE_VIEW else SCOPE_MODEL
    if "include_floors" in options:
        settings["include_floors"] = bool(options["include_floors"])
    if "band" in options and isinstance(options["band"], dict):
        band = dict(settings.get("band") or {})
        band.update(options["band"])
        settings["band"] = band
    return settings


def config_from_settings(settings):
    settings = settings or {}
    band = settings.get("band") or {}
    return {
        "damper_types": set(safe_text(key) for key in settings.get("damper_types") or []),
        "near_ft": clamp_near(settings.get("near_mm", DEFAULT_NEAR_MM)) * MM,
        "near_mm": clamp_near(settings.get("near_mm", DEFAULT_NEAR_MM)),
        "hops": clamp_hops(settings.get("hops", DEFAULT_HOPS)),
        "scope": SCOPE_VIEW if settings.get("scope") == SCOPE_VIEW else SCOPE_MODEL,
        "include_floors": bool(settings.get("include_floors", True)),
        "min_penetration_ft": max(0.0, _float(settings.get("min_penetration_mm", 10))) * MM,
        "line_thickness_ft": max(1.0, _float(settings.get("line_thickness_mm", 200))) * MM,
        "band_mode": "fixed" if band.get("mode") == "fixed" else "next_level",
        "band_height_ft": max(300.0, _float(band.get("height_mm", 3000))) * MM,
        "min_storey_ft": max(300.0, _float(band.get("min_storey_mm", 1500))) * MM,
    }


def choices_by_key(settings, link_keys):
    """What the crossing engine needs per link, from the saved names."""
    choices = {}
    for key in link_keys:
        entry = link_settings_for(settings, key)
        choices[key] = {
            "enabled": bool(entry.get("enabled", True)),
            "rating_param": safe_text(entry.get("rating_param")).strip(),
            "wall_types": set(entry.get("wall_types") or []),
            "floor_types": set(entry.get("floor_types") or []),
            "line_styles": set(entry.get("line_styles") or []),
        }
    return choices


# --------------------------------------------------------------- segments


def _split_polyline(owner_id, kind, points):
    segments = []
    for index in range(len(points) - 1):
        segments.append({"owner_id": owner_id, "kind": kind,
                         "points": [list(points[index]), list(points[index + 1])]})
    return segments


def _farthest_pair(points):
    best = None
    for index, first in enumerate(points):
        for second in points[index + 1:]:
            distance = _distance(first, second)
            if best is None or distance > best[0]:
                best = (distance, first, second)
    return (best[1], best[2]) if best is not None else None


def segments_from_snapshot(snapshot, config, scope_ids=None):
    """Ducts as segments, flex as polylines, ticked dampers as axes, and the
    boxes of everything else that could sit in a wall.

    ``scope_ids`` (the ducts visible in the active view) limits which ducts
    are traced; dampers are always read so a crossing in view still finds
    its damper just outside it."""
    elements = (snapshot or {}).get("elements") or {}
    damper_types = (config or {}).get("damper_types") or set()
    scope = set(_int(value) for value in scope_ids) if scope_ids is not None else None
    segments = []
    fitting_boxes = []
    dampers = {}
    skips = {"ducts_without_line": 0, "dampers_without_axis": 0, "out_of_scope": 0}
    for element_id in sorted(elements, key=lambda value: _int(value)):
        record = elements[element_id]
        element_id = _int(element_id, None)
        if element_id is None:
            continue
        kind = safe_text(record.get("kind"))
        key = safe_text(record.get("type_key"))
        if key and key in damper_types and kind != KIND_EQUIPMENT:
            origins = [connector.get("origin") for connector in record.get("connectors") or []
                       if connector.get("origin")]
            axis = _farthest_pair(origins) if len(origins) >= 2 else None
            center = None
            if axis is not None and _distance(axis[0], axis[1]) >= 1.0 * MM:
                center = _mid(axis[0], axis[1])
                segments.append({"owner_id": element_id, "kind": "damper",
                                 "points": [list(axis[0]), list(axis[1])]})
            else:
                axis = None
                box = record.get("bbox")
                if box:
                    center = _mid(box[0], box[1])
                elif origins:
                    center = list(origins[0])
                skips["dampers_without_axis"] += 1
            dampers[element_id] = {"id": element_id, "label": safe_text(record.get("type_key")),
                                   "axis": [list(axis[0]), list(axis[1])] if axis else None,
                                   "center": center, "level": safe_text(record.get("level"))}
            continue
        if kind in (KIND_DUCT, KIND_FLEX) and scope is not None and element_id not in scope:
            skips["out_of_scope"] += 1
            continue
        if kind == KIND_DUCT:
            curve = record.get("curve")
            if curve and len(curve) == 2:
                segments.append({"owner_id": element_id, "kind": "duct",
                                 "points": [list(curve[0]), list(curve[1])]})
            elif record.get("polyline") and len(record["polyline"]) >= 2:
                segments.extend(_split_polyline(element_id, "duct", record["polyline"]))
            else:
                skips["ducts_without_line"] += 1
        elif kind == KIND_FLEX:
            points = record.get("polyline") or []
            if len(points) >= 2:
                segments.extend(_split_polyline(element_id, "flex", points))
            else:
                skips["ducts_without_line"] += 1
        elif kind in (KIND_FITTING, KIND_ACCESSORY, KIND_OTHER) and record.get("bbox"):
            fitting_boxes.append({"id": element_id, "kind": kind, "box": record["bbox"],
                                  "label": safe_text(record.get("type_key"))})
    return {"segments": segments, "fitting_boxes": fitting_boxes, "dampers": dampers, "skips": skips}


# ------------------------------------------------------------ line panels


def _segments_cross_2d(p0, p1, a, b):
    """Parameter ``t`` along p0->p1 where it crosses a->b in plan, or None."""
    rx, ry = p1[0] - p0[0], p1[1] - p0[1]
    sx, sy = b[0] - a[0], b[1] - a[1]
    denominator = rx * sy - ry * sx
    if abs(denominator) < 1e-12:
        return None
    qx, qy = a[0] - p0[0], a[1] - p0[1]
    t = (qx * sy - qy * sx) / denominator
    u = (qx * ry - qy * rx) / denominator
    if t < -1e-9 or t > 1.0 + 1e-9 or u < -1e-9 or u > 1.0 + 1e-9:
        return None
    return max(0.0, min(1.0, t))


def segment_vs_panel(p0, p1, panel):
    """Every ``(t, point)`` where the segment passes through the vertical
    panel a drawn line stands for, within its z-band."""
    hits = []
    points = panel.get("points") or []
    z0, z1 = float(panel.get("z0", 0.0)), float(panel.get("z1", 0.0))
    for index in range(len(points) - 1):
        t = _segments_cross_2d(p0, p1, points[index], points[index + 1])
        if t is None:
            continue
        point = [p0[axis] + (p1[axis] - p0[axis]) * t for axis in (0, 1, 2)]
        if point[2] < z0 - 1e-9 or point[2] > z1 + 1e-9:
            continue
        hits.append((t, point))
    return hits


def line_crossings(segments, line_barriers):
    """Crossings of the drawn-line barriers, shaped like the engine's."""
    items = []
    for panel in line_barriers or []:
        box = _panel_box(panel)
        for segment in segments or []:
            p0, p1 = segment["points"]
            if not _box_touches_segment(box, p0, p1):
                continue
            for _t, point in segment_vs_panel(p0, p1, panel):
                thickness = float(panel.get("thickness", 0.0))
                items.append({
                    "element_id": segment.get("owner_id"),
                    "element_kind": segment.get("kind"),
                    "barrier_key": panel["key"],
                    "point": point,
                    "length": thickness,
                    "thickness": thickness,
                    "partial": False,
                    "along": False,
                })
    return items


def _panel_box(panel):
    xs = [point[0] for point in panel.get("points") or []]
    ys = [point[1] for point in panel.get("points") or []]
    if not xs:
        return None
    return [[min(xs), min(ys), float(panel.get("z0", 0.0))],
            [max(xs), max(ys), float(panel.get("z1", 0.0))]]


def _box_touches_segment(box, p0, p1, pad=1.0):
    if box is None:
        return False
    for axis in (0, 1, 2):
        low = min(p0[axis], p1[axis]) - pad
        high = max(p0[axis], p1[axis]) + pad
        if high < box[0][axis] or low > box[1][axis]:
            return False
    return True


def _boxes_overlap(a, b):
    for axis in (0, 1, 2):
        if a[1][axis] < b[0][axis] or a[0][axis] > b[1][axis]:
            return False
    return True


# --------------------------------------------------------------- clusters


def cluster_crossings(items, connected=None, reach=CLUSTER_FT):
    """One cluster per duct crossing; a damper-axis hit joins the duct
    crossings it is *connected to* within reach on the same barrier, else
    stands alone.

    Connectivity, not distance, decides the join: two parallel ducts through
    one wall are two events, and the damper on one must never cover the
    other.  ``connected(duct_id, damper_id)`` comes from the duct graph.
    """
    duct_items = [item for item in items or [] if item.get("element_kind") in ("duct", "flex")]
    damper_items = [item for item in items or [] if item.get("element_kind") == "damper"]
    order = lambda item: (safe_text(item.get("barrier_key")), _int(item.get("element_id")), list(item["point"]))
    clusters = []
    for item in sorted(duct_items, key=order):
        clusters.append({"barrier_key": item["barrier_key"], "point": list(item["point"]), "members": [item]})
    for item in sorted(damper_items, key=order):
        attached = False
        for cluster in clusters:
            if cluster["barrier_key"] != item["barrier_key"]:
                continue
            if _distance(cluster["point"], item["point"]) > reach:
                continue
            duct_ids = [m["element_id"] for m in cluster["members"] if m.get("element_kind") in ("duct", "flex")]
            if connected is None or any(connected(duct_id, item["element_id"]) for duct_id in duct_ids):
                cluster["members"].append(item)
                attached = True
        if not attached:
            clusters.append({"barrier_key": item["barrier_key"], "point": list(item["point"]), "members": [item]})
    for cluster in clusters:
        cluster["members"].sort(key=lambda m: (safe_text(m.get("element_kind")), _int(m.get("element_id"))))
        # Keyed by what it is, never by where it fell in the list: a
        # set-aside decision has to find the same crossing on the next scan.
        ducts = [_int(m["element_id"]) for m in cluster["members"]
                 if m.get("element_kind") in ("duct", "flex")]
        dampers = [_int(m["element_id"]) for m in cluster["members"]
                   if m.get("element_kind") == "damper"]
        named = sorted(ducts) or sorted(dampers)
        cluster["key"] = u"cross:{0}:{1}".format(
            cluster["barrier_key"], u",".join(u"{0}".format(value) for value in named))
    return clusters


# ---------------------------------------------------------------- analyze


def analyze(snapshot, crossings, index_description, config, segments_info=None, scope_ids=None):
    """Run once per scan: clusters with every candidate damper attached."""
    snapshot = snapshot or {}
    config = config or {}
    index_description = index_description or {}
    crossing_meta = {}
    if isinstance(crossings, dict):
        crossing_meta = dict(crossings.get("meta") or {})
        crossings = crossings.get("items") or []
    segments_info = segments_info or segments_from_snapshot(snapshot, config, scope_ids=scope_ids)
    graph = build_graph(snapshot, {"damper_types": config.get("damper_types") or set()})
    nodes = graph["nodes"]

    barriers = {}
    for record in (index_description.get("barriers") or []) + (index_description.get("line_barriers") or []):
        barriers[record["key"]] = record
    panel_items = line_crossings(segments_info["segments"], index_description.get("line_barriers") or [])
    all_items = list(crossings or []) + panel_items

    hop_cache = {}

    def _hops_from(duct_id):
        if duct_id not in hop_cache:
            hop_cache[duct_id] = hop_distances(graph, duct_id, MAX_HOPS)
        return hop_cache[duct_id]

    def _connected(duct_id, damper_id):
        return _hops_from(_int(duct_id)).get(_int(damper_id)) is not None

    clusters = cluster_crossings(all_items, connected=_connected)

    dampers = segments_info["dampers"]
    damper_min_distance = dict((damper_id, None) for damper_id in dampers)
    damper_in_barrier = set()

    for cluster in clusters:
        barrier = barriers.get(cluster["barrier_key"]) or {}
        duct_members = [m for m in cluster["members"] if m.get("element_kind") in ("duct", "flex")]
        damper_members = [m for m in cluster["members"] if m.get("element_kind") == "damper"]
        duct_ids = sorted(set(_int(m["element_id"]) for m in duct_members))
        candidates = {}
        for member in damper_members:
            damper_id = _int(member["element_id"])
            damper_in_barrier.add(damper_id)
            candidates[damper_id] = {"id": damper_id, "in_barrier": True, "distance_ft": 0.0, "hops": None}
        for damper_id, info in dampers.items():
            center = info.get("center")
            if center is None:
                continue
            distance = _distance(center, cluster["point"])
            if damper_min_distance.get(damper_id) is None or distance < damper_min_distance[damper_id]:
                damper_min_distance[damper_id] = distance
            if damper_id in candidates:
                candidates[damper_id]["distance_ft"] = distance
                continue
            if distance <= CANDIDATE_REACH_FT:
                candidates[damper_id] = {"id": damper_id, "in_barrier": False,
                                         "distance_ft": distance, "hops": None}
        for damper_id, candidate in candidates.items():
            best = None
            for duct_id in duct_ids:
                hops = _hops_from(duct_id).get(damper_id)
                if hops is not None and (best is None or hops < best):
                    best = hops
            candidate["hops"] = best
            candidate["label"] = dampers.get(damper_id, {}).get("label", u"")
        first = nodes.get(duct_ids[0]) if duct_ids else None
        cluster["candidates"] = sorted(candidates.values(),
                                       key=lambda c: (not c["in_barrier"], c["distance_ft"], c["id"]))
        cluster["duct_ids"] = duct_ids
        cluster["damper_ids"] = sorted(set(_int(m["element_id"]) for m in damper_members))
        cluster["has_flex"] = any(m.get("element_kind") == "flex" for m in duct_members)
        cluster["all_along"] = bool(duct_members) and all(m.get("along") for m in duct_members)
        cluster["partial"] = any(m.get("partial") for m in duct_members)
        cluster["barrier"] = barrier.get("label", cluster["barrier_key"])
        cluster["link"] = barrier.get("link_title", u"")
        cluster["rating"] = barrier.get("rating", u"")
        cluster["barrier_kind"] = barrier.get("kind", u"")
        cluster["barrier_element_id"] = barrier.get("element_id")
        cluster["link_instance_id"] = barrier.get("instance_id")
        cluster["barrier_level"] = barrier.get("level", u"")
        cluster["level"] = (first or {}).get("level", u"") or barrier.get("level", u"")
        cluster["system_name"] = (first or {}).get("system_name", u"")
        cluster["duct_label"] = (first or {}).get("label", u"")

    fitting_rows = []
    solid_barriers = [record for record in index_description.get("barriers") or []]
    for fitting in segments_info["fitting_boxes"]:
        for record in solid_barriers:
            if _boxes_overlap(fitting["box"], record["box"]):
                fitting_rows.append({
                    "element_id": fitting["id"], "label": fitting["label"], "kind": fitting["kind"],
                    "barrier_key": record["key"], "barrier": record["label"], "link": record["link_title"],
                    "rating": record.get("rating", u""), "level": record.get("level", u""),
                    "barrier_element_id": record.get("element_id"), "link_instance_id": record.get("instance_id"),
                    "point": _mid(fitting["box"][0], fitting["box"][1]),
                })

    damper_rows = []
    for damper_id in sorted(dampers):
        info = dampers[damper_id]
        damper_rows.append({
            "id": damper_id, "label": info.get("label", u""), "level": info.get("level", u""),
            "center": info.get("center"), "in_barrier": damper_id in damper_in_barrier,
            "min_distance_ft": damper_min_distance.get(damper_id), "axis_known": info.get("axis") is not None,
        })

    skips = dict(index_description.get("skips") or {})
    skips.update(segments_info["skips"])
    unreadable_rows = []
    for key in skips.get("unreadable") or []:
        record = barriers.get(key) or {}
        unreadable_rows.append({"barrier_key": key, "barrier": record.get("label", key),
                                "link": record.get("link_title", u""), "level": record.get("level", u""),
                                "barrier_element_id": record.get("element_id"),
                                "link_instance_id": record.get("instance_id"),
                                "point": _mid(record["box"][0], record["box"][1]) if record.get("box") else None})

    return {
        "clusters": clusters,
        "fittings": fitting_rows,
        "dampers": damper_rows,
        "unreadable": unreadable_rows,
        "stats": {
            "barriers": len(index_description.get("barriers") or []),
            "line_barriers": len(index_description.get("line_barriers") or []),
            "segments": len(segments_info["segments"]),
            "crossings": len(all_items),
            "clusters": len(clusters),
            "dampers": len(dampers),
        },
        "skips": skips,
        "meta": dict(snapshot.get("meta") or {}),
        "crossing_meta": crossing_meta,
    }


# --------------------------------------------------------------- verdicts


def _verdict(cluster, near_ft, hops):
    if cluster.get("has_flex"):
        return "flex_crossing", None
    if cluster.get("all_along"):
        return "along_barrier", None
    duct_ids = cluster.get("duct_ids") or []
    candidates = cluster.get("candidates") or []
    for candidate in candidates:
        if candidate["in_barrier"] and (not duct_ids or (candidate["hops"] is not None and candidate["hops"] <= hops)):
            return "covered_in_barrier", candidate
    for candidate in candidates:
        if candidate["distance_ft"] <= near_ft and candidate["hops"] is not None and candidate["hops"] <= hops:
            return "covered_within", candidate
    for candidate in candidates:
        if candidate["distance_ft"] <= near_ft:
            return "other_run_damper", candidate
    if not duct_ids:
        # Only a damper crossed here, and its run is unknown: the damper is
        # in the barrier, which is what the tolerance rule asks for.
        return "covered_in_barrier", candidates[0] if candidates else None
    return "missing", None


def _cluster_title(cluster):
    parts = [cluster.get("link") or u"This model"]
    barrier = cluster.get("barrier") or cluster.get("barrier_key")
    parts.append(barrier)
    if cluster.get("level"):
        parts.append(cluster["level"])
    if cluster.get("duct_ids"):
        parts.append(u"{0} (id {1})".format(cluster.get("duct_label") or u"Duct",
                                            u", ".join(u"{0}".format(v) for v in cluster["duct_ids"][:3])))
    elif cluster.get("damper_ids"):
        parts.append(u"damper id {0}".format(cluster["damper_ids"][0]))
    return u" · ".join(parts)


def _cluster_detail(bucket, cluster, candidate, near_mm, hops):
    if bucket == "flex_crossing":
        return u"A flex duct passes through a rated barrier; a fire damper does not make that right."
    if bucket == "along_barrier":
        return u"The duct runs along or inside the barrier rather than through it - check the modelling."
    if bucket == "covered_in_barrier":
        label = (candidate or {}).get("label") or u"fire damper"
        return u"{0} (id {1}) sits in the barrier{2}.".format(
            label, (candidate or {}).get("id"),
            u", {0} connection(s) from the duct".format(candidate["hops"]) if candidate and candidate.get("hops") is not None else u"")
    if bucket == "covered_within":
        return u"{0} (id {1}) is {2} mm away, {3} connection(s) along the run - within {4} mm.".format(
            candidate.get("label") or u"fire damper", candidate.get("id"), _mm(candidate["distance_ft"]),
            candidate.get("hops"), near_mm)
    if bucket == "other_run_damper":
        return u"{0} (id {1}) is {2} mm away but not on this run within {3} connection(s) - it covers another duct.".format(
            candidate.get("label") or u"fire damper", candidate.get("id"), _mm(candidate["distance_ft"]), hops)
    nearest = None
    for entry in cluster.get("candidates") or []:
        if nearest is None or entry["distance_ft"] < nearest["distance_ft"]:
            nearest = entry
    if nearest is not None:
        return u"No fire damper within {0} mm on this run; the nearest ticked damper is {1} mm away (id {2}).".format(
            near_mm, _mm(nearest["distance_ft"]), nearest["id"])
    return u"No ticked fire damper anywhere near this crossing."


def _show_for(cluster, damper_id=None):
    host_ids = list(cluster.get("duct_ids") or [])
    if damper_id is not None:
        host_ids.append(damper_id)
    show = {"host_ids": host_ids[:SHOW_ID_CAP], "point": cluster.get("point"),
            "link_instance_id": None, "link_element_id": None}
    if cluster.get("link_instance_id") is None and cluster.get("barrier_element_id") is not None \
            and cluster.get("barrier_kind") in ("wall", "floor"):
        show["host_ids"].append(cluster["barrier_element_id"])  # a barrier of this model
    elif cluster.get("link_instance_id") is not None:
        show["link_instance_id"] = cluster["link_instance_id"]
        show["link_element_id"] = cluster.get("barrier_element_id")
    return show


def classify(analysis, near_mm, hops, ignored=None):
    """Analysis + the live tolerance and hop limit + the set-aside keys."""
    analysis = analysis or {}
    near_mm = clamp_near(near_mm)
    hops = clamp_hops(hops)
    near_ft = near_mm * MM
    ignored = set(safe_text(key) for key in ignored or [])
    seen_keys = set()
    items_by_bucket = dict((key, []) for key, _title, _problem in BUCKETS)

    def _add(row):
        """File a row, sending it to Ignored when it has been set aside."""
        key = safe_text(row.get("key"))
        seen_keys.add(key)
        original = row["bucket"]
        if key in ignored:
            row["bucket"] = IGNORED_BUCKET
            row["detail"] = u"Set aside on review - was \"{0}\". {1}".format(
                BUCKET_TITLES.get(original, original), row.get("detail") or u"")
        row["original_bucket"] = original
        row["is_ignored"] = row["bucket"] == IGNORED_BUCKET
        items_by_bucket[row["bucket"]].append(row)

    for cluster in analysis.get("clusters") or []:
        bucket, candidate = _verdict(cluster, near_ft, hops)
        damper_id = candidate.get("id") if candidate else None
        _add({
            "key": cluster["key"],
            "title": _cluster_title(cluster),
            "detail": _cluster_detail(bucket, cluster, candidate, near_mm, hops),
            "bucket": bucket,
            "show": _show_for(cluster, damper_id),
            "element_ids": list(cluster.get("duct_ids") or []) + list(cluster.get("damper_ids") or []),
            "damper_id": damper_id,
            "barrier_element_id": cluster.get("barrier_element_id"),
            "link": cluster.get("link") or u"",
            "barrier": cluster.get("barrier") or u"",
            "rating": cluster.get("rating") or u"",
            "level": cluster.get("level") or u"",
            "system_name": cluster.get("system_name") or u"",
        })

    for row in analysis.get("fittings") or []:
        _add({
            "key": u"fitting:{0}:{1}".format(row["element_id"], row["barrier_key"]),
            "title": u"{0} · {1} · {2} (id {3})".format(row.get("link") or u"This model", row.get("barrier"),
                                                        row.get("label") or u"Fitting", row["element_id"]),
            "detail": u"Its bounding box overlaps the barrier; only ducts and dampers are traced exactly - check by eye.",
            "bucket": "fitting_crossing",
            "show": {"host_ids": [row["element_id"]], "point": row.get("point"),
                     "link_instance_id": row.get("link_instance_id"),
                     "link_element_id": row.get("barrier_element_id") if row.get("link_instance_id") is not None else None},
            "element_ids": [row["element_id"]],
            "damper_id": None,
            "barrier_element_id": row.get("barrier_element_id"),
            "link": row.get("link") or u"", "barrier": row.get("barrier") or u"",
            "rating": row.get("rating") or u"", "level": row.get("level") or u"", "system_name": u"",
        })

    for row in analysis.get("unreadable") or []:
        _add({
            "key": u"barrier:{0}".format(row["barrier_key"]),
            "title": u"{0} · {1}".format(row.get("link") or u"This model", row.get("barrier")),
            "detail": u"Its geometry could not be read, so nothing crossing it was judged.",
            "bucket": "barrier_unreadable",
            "show": {"host_ids": [], "point": row.get("point"),
                     "link_instance_id": row.get("link_instance_id"),
                     "link_element_id": row.get("barrier_element_id") if row.get("link_instance_id") is not None else None},
            "element_ids": [], "damper_id": None,
            "barrier_element_id": row.get("barrier_element_id"),
            "link": row.get("link") or u"", "barrier": row.get("barrier") or u"",
            "rating": u"", "level": row.get("level") or u"", "system_name": u"",
        })

    for row in analysis.get("dampers") or []:
        if row.get("in_barrier"):
            continue
        distance = row.get("min_distance_ft")
        if distance is not None and distance <= near_ft:
            continue
        _add({
            "key": u"idle:{0}".format(row["id"]),
            "title": u"{0} (id {1}){2}".format(row.get("label") or u"Fire damper", row["id"],
                                               u" · " + row["level"] if row.get("level") else u""),
            "detail": (u"No identified rated barrier within {0} mm{1} - the wall may not be ticked, or the damper is misplaced.".format(
                near_mm, u"" if distance is None else u" (nearest crossing {0} mm)".format(_mm(distance)))
                       + (u"" if row.get("axis_known") else u" Its connector origins could not be read; judged by its box.")),
            "bucket": "idle_damper",
            "show": {"host_ids": [row["id"]], "point": row.get("center"), "link_instance_id": None, "link_element_id": None},
            "element_ids": [row["id"]], "damper_id": row["id"], "barrier_element_id": None,
            "link": u"", "barrier": u"", "rating": u"", "level": row.get("level") or u"", "system_name": u"",
        })

    buckets = []
    counts = {}
    for key, title, problem in BUCKETS:
        items = items_by_bucket[key]
        items.sort(key=lambda item: (item["link"].lower(), item["level"].lower(), item["title"].lower()))
        counts[key] = len(items)
        buckets.append({"key": key, "title": title, "is_problem": problem, "items": items})
    problems = sum(counts[key] for key in PROBLEM_BUCKETS)
    return {
        "buckets": buckets,
        "counts": counts,
        "near_mm": near_mm,
        "hops": hops,
        "problem_count": problems,
        "crossing_count": len(analysis.get("clusters") or []),
        "ignored_count": counts[IGNORED_BUCKET],
        # Set aside once, then fixed or gone: the record still names them, so
        # the notes can say so rather than letting the count drift unexplained.
        "stale_ignored": sorted(ignored - seen_keys),
    }


# ---------------------------------------------------------------- search


def matches(query, item):
    return type_checklist.matches(query, item, SEARCH_TEXT_FIELDS, SEARCH_ID_FIELDS)


def filter_report(report, query):
    return type_checklist.filter_report(report, query, PROBLEM_BUCKETS, SEARCH_TEXT_FIELDS, SEARCH_ID_FIELDS)


# ----------------------------------------------------------------- status


def summary_line(analysis, report):
    analysis = analysis or {}
    report = report or {}
    stats = analysis.get("stats") or {}
    meta = analysis.get("meta") or {}
    crossing_meta = analysis.get("crossing_meta") or {}
    seconds = _float(meta.get("seconds")) + _float(crossing_meta.get("seconds"))
    parts = [u"Read {0:,} duct elements against {1:,} rated barriers in {2:.1f} s".format(
        _int(meta.get("elements_read")), _int(stats.get("barriers")) + _int(stats.get("line_barriers")), seconds)]
    total = _int(report.get("crossing_count"))
    problems = _int(report.get("problem_count"))
    if total == 0:
        parts.append(u"no duct crosses a rated barrier")
    elif problems == 0:
        parts.append(u"all {0} crossings carry a fire damper".format(total))
    else:
        parts.append(u"{0} of {1} crossings lack a fire damper".format(problems, total))
    idle = _int((report.get("counts") or {}).get("idle_damper"))
    if idle:
        parts.append(u"{0} fire damper{1} at no identified barrier".format(idle, u"" if idle == 1 else u"s"))
    set_aside = _int(report.get("ignored_count"))
    if set_aside:
        parts.append(u"{0} set aside".format(set_aside))
    text = u" - ".join(parts) + u"."
    for block in (meta, crossing_meta):
        if block.get("truncated"):
            text += u" Scan truncated: {0}.".format(safe_text(block.get("truncated_reason")) or u"budget reached")
    return text + u" The scan changes nothing in the model."


def scan_notes(analysis, config, link_descriptions, report=None):
    """Every named skip and honest limit, as lines for the notes expander."""
    analysis = analysis or {}
    skips = analysis.get("skips") or {}
    config = config or {}
    notes = []
    for link in link_descriptions or []:
        if not link.get("loaded"):
            notes.append(u"Link '{0}' is not loaded ({1}) - its barriers were not read.".format(
                link.get("title"), link.get("status") or u"not loaded"))
        elif link.get("status"):
            notes.append(u"Link '{0}': {1}.".format(link.get("title"), link.get("status")))
    if skips.get("unreadable"):
        notes.append(u"{0} barrier(s) had unreadable geometry - listed in their own group, never counted clean.".format(
            len(skips["unreadable"])))
    if skips.get("solid_budget"):
        notes.append(u"{0} barrier(s) were not read because the solid budget was reached - narrow the links or the scope.".format(
            skips["solid_budget"]))
    if skips.get("curtain_walls"):
        notes.append(u"{0} curtain wall(s) skipped: they have no solid body, so nothing crossing them is judged.".format(
            skips["curtain_walls"]))
    if skips.get("stacked_parents"):
        notes.append(u"{0} stacked wall(s): their members were judged; a member counts as rated when its own or its owner's type is ticked.".format(
            skips["stacked_parents"]))
    if skips.get("lines_not_in_plan_view"):
        notes.append(u"{0} drawn line(s) sit in sections, elevations or drafting views and were ignored - only plan views carry a level.".format(
            skips["lines_not_in_plan_view"]))
    if skips.get("lines_no_level"):
        notes.append(u"{0} drawn line(s) had no level to stand on and were ignored.".format(skips["lines_no_level"]))
    if skips.get("lines_capped"):
        notes.append(u"{0} drawn line(s) beyond the line cap were ignored.".format(skips["lines_capped"]))
    if skips.get("ducts_without_line"):
        notes.append(u"{0} duct(s) had no readable location line and were not traced.".format(skips["ducts_without_line"]))
    if skips.get("out_of_scope"):
        notes.append(u"{0} duct(s) outside the active view were not traced; dampers were still read everywhere.".format(
            skips["out_of_scope"]))
    if skips.get("dampers_without_axis"):
        notes.append(u"{0} ticked damper(s) had no readable connector origins - judged by their box, never as 'in the barrier'.".format(
            skips["dampers_without_axis"]))
    if (analysis.get("stats") or {}).get("line_barriers"):
        notes.append(u"Drawn lines are assumed {0} mm thick and to stand from the view's level to the next ({1}); they mark where the architect drew them.".format(
            int(round(_float(config.get("line_thickness_ft", 200 * MM)) / MM)),
            u"fixed {0} mm".format(int(round(_float(config.get("band_height_ft", 3000 * MM)) / MM)))
            if config.get("band_mode") == "fixed" else u"next level"))
    for block_name in ("meta", "crossing_meta"):
        block = analysis.get(block_name) or {}
        if block.get("truncated"):
            notes.append(u"The scan stopped early: {0}. What was not read is not judged.".format(
                block.get("truncated_reason") or u"budget reached"))
    stale = list((report or {}).get("stale_ignored") or []) if report else []
    if stale:
        notes.append(u"{0} set-aside record(s) in this model match no finding now - fixed, deleted, or out of the current scope.".format(
            len(stale)))
    notes.append(u"Set-aside decisions are stored in this model, so they come back next time and reach the team after a Sync to Central.")
    notes.append(u"Fittings are judged by bounding box only; a crossing made by an elbow body alone is not traced.")
    notes.append(u"Nested links cannot be reached. A rating is a transcription of the link, not a compliance judgement. The scan itself writes nothing; only the findings you set aside are stored in the model.")
    return notes
