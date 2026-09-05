# -*- coding: utf-8 -*-
"""Coordination Review differences computed by EasyBIM.

Revit's Coordination Review dialog cannot be opened for a chosen link from
the API, and its issue list cannot be read: ``Element.GetMonitoredLinkElementIds``
only names the link *instance* a host element monitors, never the element
inside the link.  This module therefore recomputes the differences itself
from plain snapshots of the monitoring host elements and of the link's
elements (already in host coordinates):

* Levels and grids match by name (Copy/Monitor keeps names, optionally with
  a prefix or suffix) and are compared by elevation / line position.
* Columns, walls, floors, openings and MEP elements match to the nearest
  link element of the same category, so those rows are estimates.

Snapshot dicts carry ``id``, ``category`` (Level, Grid, Column, Wall, Floor,
Opening, MEP), ``name``, ``type_name``, ``elevation`` (levels), ``point``
``(x, y, z)``, ``curve`` ``((x, y, z), (x, y, z))`` and ``arc``
``((x, y, z), radius)``.  Lengths are Revit internal feet.  Pure Python with
no Revit imports, so the unit tests load it standalone.
"""

import math


CATEGORY_LEVEL = "Level"
CATEGORY_GRID = "Grid"
NAMED_CATEGORIES = (CATEGORY_LEVEL, CATEGORY_GRID)

DEFAULT_TOLERANCE_FT = 0.001  # ~0.3 mm: Revit flags any real change.
DEFAULT_SEARCH_RADIUS_FT = 50.0

KIND_LEVEL_MOVED = "level_moved"
KIND_LEVEL_MISSING = "level_missing"
KIND_GRID_MOVED = "grid_moved"
KIND_GRID_MISSING = "grid_missing"
KIND_ELEMENT_MOVED = "element_moved"
KIND_ELEMENT_MISSING = "element_missing"
KIND_TYPE_CHANGED = "type_changed"
KIND_NAME_DIFFERS = "name_differs"
KIND_NEW_IN_LINK = "new_in_link"

KIND_ORDER = (
    KIND_LEVEL_MOVED,
    KIND_LEVEL_MISSING,
    KIND_GRID_MOVED,
    KIND_GRID_MISSING,
    KIND_ELEMENT_MOVED,
    KIND_ELEMENT_MISSING,
    KIND_TYPE_CHANGED,
    KIND_NAME_DIFFERS,
    KIND_NEW_IN_LINK,
)

KIND_TITLES = {
    KIND_LEVEL_MOVED: "Level moved",
    KIND_LEVEL_MISSING: "Level deleted in link",
    KIND_GRID_MOVED: "Grid moved",
    KIND_GRID_MISSING: "Grid deleted in link",
    KIND_ELEMENT_MOVED: "Element moved",
    KIND_ELEMENT_MISSING: "Element deleted in link",
    KIND_TYPE_CHANGED: "Type changed",
    KIND_NAME_DIFFERS: "Name differs",
    KIND_NEW_IN_LINK: "In link but not monitored",
}

# Rows produced by nearest-element matching rather than by name.
ESTIMATED_KINDS = (KIND_ELEMENT_MOVED, KIND_ELEMENT_MISSING, KIND_TYPE_CHANGED)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def _norm_name(name):
    return "".join(_safe_text(name).lower().split())


def _default_format(value_ft):
    return "{0:.4f} ft".format(float(value_ft))


def _signed(value_ft, fmt):
    sign = "+" if float(value_ft) >= 0 else "-"
    return "{0}{1}".format(sign, fmt(abs(float(value_ft))))


def _pt(value):
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except Exception:
        return None


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a):
    return math.sqrt(_dot(a, a))


def _dist(a, b):
    return _length(_sub(a, b))


def _curve(item):
    curve = (item or {}).get("curve")
    if not curve:
        return None
    try:
        start, end = _pt(curve[0]), _pt(curve[1])
    except Exception:
        return None
    if start is None or end is None:
        return None
    return (start, end)


def _arc(item):
    arc = (item or {}).get("arc")
    if not arc:
        return None
    try:
        center, radius = _pt(arc[0]), _safe_float(arc[1])
    except Exception:
        return None
    if center is None or radius is None:
        return None
    return (center, radius)


def _representative_point(item):
    point = _pt((item or {}).get("point"))
    if point is not None:
        return point
    curve = _curve(item)
    if curve is not None:
        return (
            (curve[0][0] + curve[1][0]) / 2.0,
            (curve[0][1] + curve[1][1]) / 2.0,
            (curve[0][2] + curve[1][2]) / 2.0,
        )
    arc = _arc(item)
    if arc is not None:
        return arc[0]
    elevation = _safe_float((item or {}).get("elevation"))
    if elevation is not None:
        return (0.0, 0.0, elevation)
    return None


def _point_to_line_distance(point, start, end):
    direction = _sub(end, start)
    length = _length(direction)
    if length <= 1e-12:
        return _dist(point, start)
    return _length(_cross(_sub(point, start), direction)) / length


def _line_distance(curve_a, curve_b):
    """Largest offset between two straight grids treated as infinite lines."""
    return max(
        _point_to_line_distance(curve_b[0], curve_a[0], curve_a[1]),
        _point_to_line_distance(curve_b[1], curve_a[0], curve_a[1]),
        _point_to_line_distance(curve_a[0], curve_b[0], curve_b[1]),
        _point_to_line_distance(curve_a[1], curve_b[0], curve_b[1]),
    )


def _curve_distance(curve_a, curve_b):
    """Endpoint-based distance between two located curves (best endpoint order)."""
    straight = max(_dist(curve_a[0], curve_b[0]), _dist(curve_a[1], curve_b[1]))
    flipped = max(_dist(curve_a[0], curve_b[1]), _dist(curve_a[1], curve_b[0]))
    return min(straight, flipped)


def _element_distance(host, link):
    host_curve, link_curve = _curve(host), _curve(link)
    if host_curve is not None and link_curve is not None:
        return _curve_distance(host_curve, link_curve)
    host_point, link_point = _representative_point(host), _representative_point(link)
    if host_point is None or link_point is None:
        return None
    return _dist(host_point, link_point)


def _label(item):
    category = _safe_text((item or {}).get("category")) or "Element"
    name = _safe_text((item or {}).get("name"))
    type_name = _safe_text((item or {}).get("type_name"))
    detail = name or type_name
    if category in NAMED_CATEGORIES:
        return "{0} '{1}'".format(category, name or "(unnamed)")
    if detail:
        return "{0} '{1}' (id {2})".format(category, detail, (item or {}).get("id"))
    return "{0} (id {1})".format(category, (item or {}).get("id"))


def _issue(kind, host=None, link=None, delta=None, message=""):
    return {
        "kind": kind,
        "title": KIND_TITLES.get(kind, kind),
        "estimated": kind in ESTIMATED_KINDS,
        "category": _safe_text((host or link or {}).get("category")),
        "host_id": (host or {}).get("id"),
        "link_id": (link or {}).get("id"),
        "host_name": _safe_text((host or {}).get("name")),
        "link_name": _safe_text((link or {}).get("name")),
        "delta_ft": delta,
        "message": message,
    }


# ---------------------------------------------------------------------------
# name matching (levels, grids)
# ---------------------------------------------------------------------------

def match_by_name(host_items, link_items):
    """Pair host and link items by name; each link item is claimed once.

    Order of preference: exact name, whitespace/case-insensitive name, then
    a prefix/suffix relation (Copy/Monitor can add either), longest first.
    Returns ``(pairs, unmatched_hosts, unmatched_links)``.
    """
    remaining = list(link_items or [])
    pairs = []
    unmatched_hosts = []

    def _claim(index):
        return remaining.pop(index)

    for host in list(host_items or []):
        host_name = _safe_text(host.get("name"))
        host_key = _norm_name(host_name)
        found = None

        for index, link in enumerate(remaining):
            if _safe_text(link.get("name")) == host_name and host_name:
                found = _claim(index)
                break
        if found is None and host_key:
            for index, link in enumerate(remaining):
                if _norm_name(link.get("name")) == host_key:
                    found = _claim(index)
                    break
        if found is None and host_key:
            best_index, best_len = None, 0
            for index, link in enumerate(remaining):
                link_key = _norm_name(link.get("name"))
                if not link_key:
                    continue
                shorter = min(len(host_key), len(link_key))
                related = (
                    host_key.startswith(link_key)
                    or host_key.endswith(link_key)
                    or link_key.startswith(host_key)
                    or link_key.endswith(host_key)
                )
                if related and shorter > best_len:
                    best_index, best_len = index, shorter
            if best_index is not None:
                found = _claim(best_index)

        if found is None:
            unmatched_hosts.append(host)
        else:
            pairs.append((host, found))

    return pairs, unmatched_hosts, remaining


# ---------------------------------------------------------------------------
# levels
# ---------------------------------------------------------------------------

def _compare_levels(host_levels, link_levels, tolerance, fmt):
    issues = []
    ok_count = 0
    deltas = []
    pairs, unmatched_hosts, unmatched_links = match_by_name(host_levels, link_levels)

    for host, link in pairs:
        host_elevation = _safe_float(host.get("elevation"))
        link_elevation = _safe_float(link.get("elevation"))
        if _safe_text(host.get("name")) != _safe_text(link.get("name")):
            issues.append(
                _issue(
                    KIND_NAME_DIFFERS,
                    host,
                    link,
                    message="{0} is named '{1}' in the link.".format(
                        _label(host), _safe_text(link.get("name"))
                    ),
                )
            )
        if host_elevation is None or link_elevation is None:
            ok_count += 1
            continue
        delta = link_elevation - host_elevation
        deltas.append(delta)
        if abs(delta) > tolerance:
            issues.append(
                _issue(
                    KIND_LEVEL_MOVED,
                    host,
                    link,
                    delta,
                    "{0} moved by {1} in the link (host {2}, link {3}).".format(
                        _label(host),
                        _signed(delta, fmt),
                        fmt(host_elevation),
                        fmt(link_elevation),
                    ),
                )
            )
        else:
            ok_count += 1

    for host in unmatched_hosts:
        hint = ""
        host_elevation = _safe_float(host.get("elevation"))
        if host_elevation is not None and unmatched_links:
            nearest = None
            for link in unmatched_links:
                link_elevation = _safe_float(link.get("elevation"))
                if link_elevation is None:
                    continue
                gap = abs(link_elevation - host_elevation)
                if nearest is None or gap < nearest[0]:
                    nearest = (gap, link)
            if nearest is not None:
                hint = " Nearest link level is '{0}', {1} away.".format(
                    _safe_text(nearest[1].get("name")), fmt(nearest[0])
                )
        issues.append(
            _issue(
                KIND_LEVEL_MISSING,
                host,
                None,
                message="{0} was not found in the link (deleted or renamed).{1}".format(
                    _label(host), hint
                ),
            )
        )

    for link in unmatched_links:
        issues.append(
            _issue(
                KIND_NEW_IN_LINK,
                None,
                link,
                message="{0} exists in the link but is not monitored in this model.".format(
                    _label(link)
                ),
            )
        )

    level_offset = 0.0
    if deltas and abs(deltas[0]) > tolerance:
        if all(abs(delta - deltas[0]) <= tolerance for delta in deltas):
            level_offset = deltas[0]

    return issues, ok_count, level_offset


# ---------------------------------------------------------------------------
# grids
# ---------------------------------------------------------------------------

def _grid_distance(host, link):
    """``(distance, comparable)``; ``comparable`` False when geometry kinds differ."""
    host_curve, link_curve = _curve(host), _curve(link)
    if host_curve is not None and link_curve is not None:
        return _line_distance(host_curve, link_curve), True
    host_arc, link_arc = _arc(host), _arc(link)
    if host_arc is not None and link_arc is not None:
        return max(_dist(host_arc[0], link_arc[0]), abs(host_arc[1] - link_arc[1])), True
    if (host_curve is None and host_arc is None) or (link_curve is None and link_arc is None):
        return 0.0, True
    return None, False


def _compare_grids(host_grids, link_grids, tolerance, fmt):
    issues = []
    ok_count = 0
    pairs, unmatched_hosts, unmatched_links = match_by_name(host_grids, link_grids)

    for host, link in pairs:
        if _safe_text(host.get("name")) != _safe_text(link.get("name")):
            issues.append(
                _issue(
                    KIND_NAME_DIFFERS,
                    host,
                    link,
                    message="{0} is named '{1}' in the link.".format(
                        _label(host), _safe_text(link.get("name"))
                    ),
                )
            )
        distance, comparable = _grid_distance(host, link)
        if not comparable:
            issues.append(
                _issue(
                    KIND_GRID_MOVED,
                    host,
                    link,
                    None,
                    "{0} changed shape in the link (straight/arc).".format(_label(host)),
                )
            )
        elif distance > tolerance:
            issues.append(
                _issue(
                    KIND_GRID_MOVED,
                    host,
                    link,
                    distance,
                    "{0} moved by {1} in the link.".format(_label(host), fmt(distance)),
                )
            )
        else:
            ok_count += 1

    for host in unmatched_hosts:
        issues.append(
            _issue(
                KIND_GRID_MISSING,
                host,
                None,
                message="{0} was not found in the link (deleted or renamed).".format(
                    _label(host)
                ),
            )
        )

    for link in unmatched_links:
        issues.append(
            _issue(
                KIND_NEW_IN_LINK,
                None,
                link,
                message="{0} exists in the link but is not monitored in this model.".format(
                    _label(link)
                ),
            )
        )

    return issues, ok_count


# ---------------------------------------------------------------------------
# other monitored categories (nearest element)
# ---------------------------------------------------------------------------

def _cell(point, size):
    return (int(math.floor(point[0] / size)), int(math.floor(point[1] / size)))


def _candidates(host, link_items, grid, size):
    point = _representative_point(host)
    if point is None:
        return []
    cx, cy = _cell(point, size)
    found = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            found.extend(grid.get((cx + dx, cy + dy), ()))
    return found


def _compare_category(host_items, link_items, tolerance, radius, fmt):
    issues = []
    ok_count = 0
    size = max(float(radius), 1.0)

    grid = {}
    for index, link in enumerate(link_items):
        point = _representative_point(link)
        if point is None:
            continue
        grid.setdefault(_cell(point, size), []).append(index)

    candidates = []
    for host_index, host in enumerate(host_items):
        for link_index in _candidates(host, link_items, grid, size):
            distance = _element_distance(host, link_items[link_index])
            if distance is None or distance > radius:
                continue
            candidates.append((distance, host_index, link_index))
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))

    matched = {}
    claimed = set()
    for distance, host_index, link_index in candidates:
        if host_index in matched or link_index in claimed:
            continue
        matched[host_index] = (link_index, distance)
        claimed.add(link_index)

    for host_index, host in enumerate(host_items):
        if host_index not in matched:
            issues.append(
                _issue(
                    KIND_ELEMENT_MISSING,
                    host,
                    None,
                    message=(
                        "{0} has no counterpart within {1} in the link "
                        "(deleted or moved further)."
                    ).format(_label(host), fmt(radius)),
                )
            )
            continue

        link_index, distance = matched[host_index]
        link = link_items[link_index]
        host_type = _safe_text(host.get("type_name"))
        link_type = _safe_text(link.get("type_name"))
        type_differs = bool(host_type and link_type and _norm_name(host_type) != _norm_name(link_type))

        if distance > tolerance:
            note = ""
            if type_differs:
                note = " Its type is '{0}' there.".format(link_type)
            issues.append(
                _issue(
                    KIND_ELEMENT_MOVED,
                    host,
                    link,
                    distance,
                    "{0} moved by {1} in the link.{2}".format(_label(host), fmt(distance), note),
                )
            )
        elif type_differs:
            issues.append(
                _issue(
                    KIND_TYPE_CHANGED,
                    host,
                    link,
                    None,
                    "{0} has type '{1}' in the link.".format(_label(host), link_type),
                )
            )
        else:
            ok_count += 1

    return issues, ok_count


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def _by_category(items):
    buckets = {}
    for item in list(items or []):
        buckets.setdefault(_safe_text((item or {}).get("category")), []).append(item)
    return buckets


def compare(
    host_items,
    link_items,
    tolerance_ft=DEFAULT_TOLERANCE_FT,
    search_radius_ft=DEFAULT_SEARCH_RADIUS_FT,
    format_length=None,
):
    """Compare monitoring host snapshots with link snapshots.

    Returns ``issues`` (ordered by kind, category, name), ``ok_count``,
    ``monitored_count``, ``estimated_count`` and ``level_offset_ft`` (the
    common elevation delta when every matched level differs by the same
    amount, which usually means a Copy/Monitor level offset - the level rows
    are still reported so nothing is hidden).
    """
    fmt = format_length if callable(format_length) else _default_format
    tolerance = float(tolerance_ft)
    radius = float(search_radius_ft)

    hosts = _by_category(host_items)
    links = _by_category(link_items)
    issues = []
    ok_count = 0

    level_issues, level_ok, level_offset = _compare_levels(
        hosts.get(CATEGORY_LEVEL, []), links.get(CATEGORY_LEVEL, []), tolerance, fmt
    )
    issues.extend(level_issues)
    ok_count += level_ok

    grid_issues, grid_ok = _compare_grids(
        hosts.get(CATEGORY_GRID, []), links.get(CATEGORY_GRID, []), tolerance, fmt
    )
    issues.extend(grid_issues)
    ok_count += grid_ok

    for category in sorted(hosts.keys()):
        if category in NAMED_CATEGORIES:
            continue
        category_issues, category_ok = _compare_category(
            hosts.get(category, []), links.get(category, []), tolerance, radius, fmt
        )
        issues.extend(category_issues)
        ok_count += category_ok

    order = dict((kind, index) for index, kind in enumerate(KIND_ORDER))
    issues.sort(
        key=lambda issue: (
            order.get(issue.get("kind"), len(order)),
            _safe_text(issue.get("category")).lower(),
            _norm_name(issue.get("host_name") or issue.get("link_name")),
            _safe_text(issue.get("host_id") or issue.get("link_id")),
        )
    )

    return {
        "issues": issues,
        "ok_count": ok_count,
        "monitored_count": len(list(host_items or [])),
        "estimated_count": len([issue for issue in issues if issue.get("estimated")]),
        "level_offset_ft": level_offset,
    }


def group_issues(issues):
    """Group issues by kind in display order: ``[{kind, title, estimated, issues}]``."""
    buckets = {}
    for issue in list(issues or []):
        buckets.setdefault(issue.get("kind"), []).append(issue)

    groups = []
    for kind in KIND_ORDER:
        rows = buckets.pop(kind, None)
        if rows:
            groups.append(
                {
                    "kind": kind,
                    "title": KIND_TITLES.get(kind, kind),
                    "estimated": kind in ESTIMATED_KINDS,
                    "issues": rows,
                }
            )
    for kind in sorted(buckets.keys()):
        groups.append(
            {"kind": kind, "title": KIND_TITLES.get(kind, kind), "estimated": False, "issues": buckets[kind]}
        )
    return groups
