# -*- coding: utf-8 -*-
"""Pure logic for Space Boundary: the geometry fingerprint, the gates, the
plan, and every verdict about drift.

Nothing here touches Revit.  ``space_boundary_revit`` reads rooms, spaces and
filled regions into plain curve records and hands them over; everything that
decides what to draw, what to skip and what has since moved happens in this
module, where the desktop tests can drive it.

The hard part is the fingerprint.  A room's boundary and the filled region
drawn from it have to compare equal across sessions, and Revit does not
promise to hand the same loop back the same way twice: it may start the loop
at a different segment, wind it the other way, hand the loops back in a
different order, or split one wall into two collinear segments.  Any of those
would read as "somebody edited this" on a naive hash.  So a loop is reduced to
a canonical form - quantised to a millimetre grid, stripped of duplicate and
exactly-collinear vertices, forced counter-clockwise, and rotated to start at
its lowest vertex - and only then hashed.

Two digests come out of that.  ``abs`` is the canonical points as they sit in
the model.  ``shape`` is the same points with the minimum corner subtracted,
which is exact integer arithmetic, so a region that was dragged without being
reshaped has a bit-identical ``shape`` and a different ``abs``.  That is what
separates "the modeller moved it" from "the modeller reshaped it", which is
the distinction the whole review lane rests on.

One trap is worth naming because it would only show up in Revit: quantising
uses ``floor(v / grid + 0.5)`` rather than ``round()``.  IronPython 2.7 rounds
half away from zero and CPython 3 rounds half to even, so ``round()`` would
make the desktop tests and the running tool disagree on exactly the values
that sit on a grid boundary.  A test pins it.
"""

from __future__ import print_function

import hashlib
import json
import math

from easybim import json_text
from easybim import type_checklist


MM_PER_FOOT = 304.8

# -- geometry tolerances, all test-pinned ---------------------------------

#: The grid every vertex is snapped to before hashing.  Revit's own vertex
#: tolerance is about 0.16 mm, so a millimetre sits above the noise and below
#: anything a person would call a change.
GRID_MM = 1.0
#: Chord sagitta bound when an arc is sampled; matches the grid.
ARC_SAG_MM = 1.0
MAX_ARC_SEGMENTS = 64
#: A gap between consecutive segments up to this is closed by moving the
#: next start onto the previous end - never by inserting a bridge, because a
#: bridge that small is itself a curve Revit refuses.  Beyond it the loop is
#: refused by name rather than silently closed.  Wide enough to absorb a
#: dropped short segment plus rounding.
GAP_BUDGET_MM = 2.5
#: Revit refuses to build a curve shorter than its short-curve tolerance,
#: about 0.8 mm.  The adapter reads the session's real value and passes it in;
#: this is the desktop default.
SHORT_MM = 0.8
#: A loop smaller than a square centimetre is a modelling artefact.
MIN_LOOP_AREA_MM2 = 1000
#: Above this many vertices the self-intersection test is skipped and said so.
SELF_INTERSECT_CAP = 400
MAX_LOOPS_PER_ROOM = 200
MAX_VERTICES_PER_LOOP = 5000

#: Batch sizes: an acknowledgement above the first, a refusal above the second.
ACK_PAIRS = 500
MAX_PAIRS = 20000

#: How far a region's outline may sit from its room's before it reads as
#: drifted.  Two grid cells: one for each side's own rounding.
SYNC_TOLERANCE_MM = 2.0

#: Where a planned pair sits in the dry run: on the view's own level, ticked,
#: or visible in the view from another level, offered unticked.
CATEGORY_THIS_LEVEL = "this_level"
CATEGORY_OTHER_LEVEL = "other_level"

#: What a record keeps of a fingerprint: the digests and the numbers, never
#: the outline itself, so a record stays about half a kilobyte.
RECORD_FINGERPRINT_KEYS = ("abs", "shape", "area_mm2", "loops", "vertices", "min_corner")

KIND_ROOM = "room"
KIND_SPACE = "space"
KIND_LABELS = {KIND_ROOM: u"Rooms", KIND_SPACE: u"Spaces"}

SOURCE_HOST = "host"
SOURCE_LINK = "link"
HOST_KEY = u"This model"

SCOPE_ACTIVE = "active_view"
SCOPE_SELECTED = "selected_views"

SUBJECT_ALL = "all"
SUBJECT_ONE = "one"

BOUNDARY_LOCATIONS = ("Finish", "Center", "CoreBoundary", "CoreCenter")
BOUNDARY_SENTENCES = {
    "Finish": u"wall finish",
    "Center": u"wall centre",
    "CoreBoundary": u"the core boundary",
    "CoreCenter": u"the core centre",
}

#: Plan-family views can hold a horizontal loop; nothing else can.
PLAN_VIEW_TYPES = ("FloorPlan", "EngineeringPlan", "AreaPlan", "CeilingPlan")
#: Offered but left unticked: an RCP shows the room's boundary, not the ceiling.
UNTICKED_VIEW_TYPES = ("CeilingPlan",)
VERTICAL_VIEW_TYPES = ("Section", "Elevation", "Detail")
NO_MODEL_PLANE_VIEW_TYPES = ("DraftingView", "Legend")


# ---------------------------------------------------------------- helpers


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


def feet_to_mm(value):
    return to_float(value) * MM_PER_FOOT


def quantise(value_mm, grid_mm=GRID_MM):
    """Snap to the grid, rounding half up on every runtime.

    Deliberately not ``round()``: IronPython 2.7 rounds half away from zero
    and CPython 3 rounds half to even, so the tool and its tests would
    disagree on exactly the values that land on a grid line.
    """
    grid_mm = to_float(grid_mm, GRID_MM) or GRID_MM
    return int(math.floor(to_float(value_mm) / grid_mm + 0.5))


# ------------------------------------------------------------ tessellation


def _arc_points(x0, y0, x1, y1, xm, ym, sag_mm, max_segments):
    """Sample an arc given its start, end and a point on it.

    Solved in pure Python from the circumcircle so the sampling is identical
    on every runtime; a caller that hands three collinear points gets the
    straight line back, which is the degenerate case Revit also allows.
    """
    ax, ay = x0 - xm, y0 - ym
    bx, by = x1 - xm, y1 - ym
    cross = ax * by - ay * bx
    if abs(cross) < 1e-9:
        return [(x0, y0), (x1, y1)]

    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    ux = (by * a2 - ay * b2) / (2.0 * cross)
    uy = (ax * b2 - bx * a2) / (2.0 * cross)
    cx, cy = xm + ux, ym + uy
    radius = math.sqrt(ux * ux + uy * uy)
    if radius <= 0.0:
        return [(x0, y0), (x1, y1)]

    start = math.atan2(y0 - cy, x0 - cx)
    middle = math.atan2(ym - cy, xm - cx)
    end = math.atan2(y1 - cy, x1 - cx)

    def _sweep(going_up):
        span = end - start
        if going_up:
            while span <= 0:
                span += 2.0 * math.pi
        else:
            while span >= 0:
                span -= 2.0 * math.pi
        return span

    for going_up in (True, False):
        span = _sweep(going_up)
        offset = middle - start
        if going_up:
            while offset <= 0:
                offset += 2.0 * math.pi
        else:
            while offset >= 0:
                offset -= 2.0 * math.pi
        if abs(offset) <= abs(span):
            break

    ratio = 1.0 - min(1.0, to_float(sag_mm, ARC_SAG_MM) / radius)
    step = 2.0 * math.acos(max(-1.0, min(1.0, ratio))) if radius > 0 else math.pi
    if step <= 1e-6:
        step = math.pi / 8.0
    count = int(math.ceil(abs(span) / step))
    count = max(1, min(int(max_segments), count))

    points = []
    for index in range(count + 1):
        angle = start + span * (float(index) / count)
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def tessellate_loop(records, sag_mm=ARC_SAG_MM, max_arc_segments=MAX_ARC_SEGMENTS):
    """Curve records in millimetres -> a point list.

    ``records`` are what the Revit layer hands over, one per boundary segment:
    ``("L", x0, y0, x1, y1)``, ``("A", x0, y0, x1, y1, xm, ym)`` for an arc
    through a midpoint, or ``("T", [(x, y), ...])`` for anything else, already
    tessellated by Revit.
    """
    points = []
    for record in records or []:
        kind = record[0]
        if kind == "L":
            piece = [(to_float(record[1]), to_float(record[2])),
                     (to_float(record[3]), to_float(record[4]))]
        elif kind == "A":
            piece = _arc_points(to_float(record[1]), to_float(record[2]),
                                to_float(record[3]), to_float(record[4]),
                                to_float(record[5]), to_float(record[6]),
                                sag_mm, max_arc_segments)
        else:
            piece = [(to_float(x), to_float(y)) for x, y in (record[1] or [])]
        if not piece:
            continue
        if points and _close(points[-1], piece[0]):
            piece = piece[1:]
        points.extend(piece)
    return points


def _close(a, b, tolerance=1e-9):
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


# ---------------------------------------------------------- canonical form


def _drop_repeats(points):
    cleaned = []
    for point in points:
        if cleaned and cleaned[-1] == point:
            continue
        cleaned.append(point)
    while len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
        cleaned.pop()
    return cleaned


def _drop_collinear(points):
    """Remove vertices that sit exactly on the line between their neighbours.

    Integer arithmetic, so this is exact: a wall split into two collinear
    segments hashes the same as the single wall it used to be.
    """
    if len(points) < 3:
        return points
    changed = True
    passes = 0
    while changed and len(points) >= 3 and passes < 8:
        changed = False
        passes += 1
        kept = []
        count = len(points)
        for index in range(count):
            before = points[index - 1]
            here = points[index]
            after = points[(index + 1) % count]
            cross = ((here[0] - before[0]) * (after[1] - here[1]) -
                     (here[1] - before[1]) * (after[0] - here[0]))
            if cross == 0:
                changed = True
                continue
            kept.append(here)
        if kept and len(kept) >= 3:
            points = kept
        else:
            break
    return points


def signed_area2(points):
    """Twice the signed area, in grid units.  Positive is counter-clockwise."""
    total = 0
    count = len(points)
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        total += x0 * y1 - x1 * y0
    return total


def canonical_loop(points_mm, grid_mm=GRID_MM):
    """One loop reduced to the form two runs can be compared on."""
    points = [(quantise(x, grid_mm), quantise(y, grid_mm)) for x, y in points_mm or []]
    points = _drop_repeats(points)
    points = _drop_collinear(points)
    points = _drop_repeats(points)
    if len(points) < 3:
        return tuple(points)

    if signed_area2(points) < 0:
        points = list(reversed(points))

    lowest = min(points)
    best = None
    for index, point in enumerate(points):
        if point != lowest:
            continue
        rotated = tuple(points[index:] + points[:index])
        if best is None or rotated < best:
            best = rotated
    return best if best is not None else tuple(points)


def canonical_loops(loops, grid_mm=GRID_MM):
    """Every loop canonicalised, then sorted, so loop order cannot matter."""
    canonical = []
    for loop in loops or []:
        reduced = canonical_loop(loop, grid_mm)
        if len(reduced) >= 3:
            canonical.append(reduced)
    return tuple(sorted(canonical))


def _digest(value):
    text = json.dumps(value, separators=(",", ":"), sort_keys=False)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def polygon_area2(loops):
    """Twice the enclosed area: the largest loop less the ones inside it."""
    areas = sorted((abs(signed_area2(loop)) for loop in loops or []), reverse=True)
    if not areas:
        return 0
    return areas[0] - sum(areas[1:])


def fingerprints(loops, grid_mm=GRID_MM):
    """The two digests plus the numbers the report shows a person."""
    absolute = canonical_loops(loops, grid_mm)
    vertices = sum(len(loop) for loop in absolute)
    if not absolute:
        return {"abs": u"", "shape": u"", "area_mm2": 0, "loops": 0,
                "vertices": 0, "min_corner": [0, 0], "outline": ()}
    min_x = min(point[0] for loop in absolute for point in loop)
    min_y = min(point[1] for loop in absolute for point in loop)
    shape = tuple(tuple((x - min_x, y - min_y) for x, y in loop) for loop in absolute)
    grid = to_float(grid_mm, GRID_MM) or GRID_MM
    return {
        "abs": _digest(absolute),
        "shape": _digest(shape),
        "area_mm2": int(abs(polygon_area2(absolute)) * grid * grid / 2.0),
        "loops": len(absolute),
        "vertices": vertices,
        "min_corner": [min_x, min_y],
        # The canonical loops themselves, in grid units: what the drift check
        # measures a region against.  Never written into a record.
        "outline": absolute,
    }


def digests_only(fingerprint):
    """What a record keeps of a fingerprint - the outline stays out."""
    fingerprint = fingerprint or {}
    return dict((key, fingerprint[key]) for key in RECORD_FINGERPRINT_KEYS
                if key in fingerprint)


def _point_to_segment(point, a, b):
    px, py = point
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = float(dx * dx + dy * dy)
    if length2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _point_to_loop(point, loop):
    best = None
    count = len(loop)
    for index in range(count):
        distance = _point_to_segment(point, loop[index], loop[(index + 1) % count])
        if best is None or distance < best:
            best = distance
    return best if best is not None else float("inf")


def outline_deviation(a, b, grid_mm=GRID_MM):
    """``(max_mm, sentence)`` - how far two outlines sit apart, both ways.

    Every vertex of each outline is measured to the nearest edge of the
    other, and the largest such distance is the answer.  That is a distance
    a person can check with a tape, which a digest comparison never is, and
    it is what lets the verdict carry a tolerance instead of demanding that
    two runs round every coordinate identically.
    """
    a = tuple(a or ())
    b = tuple(b or ())
    grid = to_float(grid_mm, GRID_MM) or GRID_MM
    if not a or not b:
        return None, u"There is no outline to compare."
    if len(a) != len(b):
        return float("inf"), u"{0} loop(s) against {1}.".format(len(b), len(a))
    worst = 0.0
    for loops_from, loops_to in ((a, b), (b, a)):
        for loop in loops_from:
            for point in loop:
                nearest = min(_point_to_loop(point, other) for other in loops_to)
                if nearest > worst:
                    worst = nearest
    return worst * grid, u"Largest gap between the two outlines: {0} mm.".format(
        int(math.floor(worst * grid + 0.5)))


def sync_verdict(region_now, room_now, grid_mm=GRID_MM, tolerance_mm=SYNC_TOLERANCE_MM):
    """``(in_sync | drifted | unknown, deviation_mm, sentence)``.

    The question the review lane actually answers: does this region match
    its room *now*?  Measured on the two outlines when both are there, and
    only on the digests when a fingerprint arrived without one.  ``unknown``
    is a real answer and is never quietly read as in step.
    """
    region_now = region_now or {}
    room_now = room_now or {}
    if not region_now.get("abs") or not room_now.get("abs"):
        return "unknown", None, u"One side has no outline to compare."
    if region_now.get("outline") and room_now.get("outline"):
        deviation, sentence = outline_deviation(region_now["outline"], room_now["outline"],
                                                grid_mm)
        if deviation is None:
            return "unknown", None, sentence
        if deviation <= to_float(tolerance_mm, SYNC_TOLERANCE_MM):
            return "in_sync", deviation, u""
        return "drifted", deviation, sentence
    if region_now["abs"] == room_now["abs"]:
        return "in_sync", 0.0, u""
    return "drifted", None, u"The region's outline is not the room's."


def cut_plane_visible(base_mm, top_mm, cut_mm):
    """Whether a plan's cut plane passes through a room's height.

    That is the rule Revit itself draws a room by in a plan: below its base
    or at or above its upper limit, the room is not there to be seen.
    """
    base = to_float(base_mm)
    top = to_float(top_mm)
    cut = to_float(cut_mm)
    if top <= base:
        return False
    return base <= cut < top


def compare(before, after, grid_mm=GRID_MM):
    """``(verdict, sentence)`` for two fingerprints of the same thing.

    ``unchanged`` / ``moved`` / ``edited`` - and ``moved`` carries the offset,
    which is exact because the shape digest is translation-invariant.
    """
    before = before or {}
    after = after or {}
    if not before.get("abs") or not after.get("abs"):
        return "unknown", u"There is nothing to compare against."
    if before["abs"] == after["abs"]:
        return "unchanged", u""
    if before.get("shape") and before["shape"] == after.get("shape"):
        grid = to_float(grid_mm, GRID_MM) or GRID_MM
        old = before.get("min_corner") or [0, 0]
        new = after.get("min_corner") or [0, 0]
        dx = int(round((new[0] - old[0]) * grid))
        dy = int(round((new[1] - old[1]) * grid))
        return "moved", u"Moved {0} mm east and {1} mm north, same shape.".format(dx, dy)
    delta_area = to_int(after.get("area_mm2")) - to_int(before.get("area_mm2"))
    return "edited", u"Reshaped: {0} vertices now against {1}, area {2} {3} mm².".format(
        to_int(after.get("vertices")), to_int(before.get("vertices")),
        u"up" if delta_area >= 0 else u"down", abs(delta_area))


# --------------------------------------------------------------- geometry


def is_self_intersecting(points, cap=SELF_INTERSECT_CAP):
    """``(intersecting, tested)`` - honest about not testing a huge loop."""
    count = len(points or [])
    if count < 4:
        return False, True
    if count > cap:
        return False, False
    for i in range(count):
        a0 = points[i]
        a1 = points[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or j == (i + 1) % count:
                continue
            b0 = points[j]
            b1 = points[(j + 1) % count]
            if _segments_cross(a0, a1, b0, b1):
                return True, True
    return False, True


def _side(a, b, point):
    value = ((b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]))
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _segments_cross(a0, a1, b0, b1):
    d1 = _side(a0, a1, b0)
    d2 = _side(a0, a1, b1)
    d3 = _side(b0, b1, a0)
    d4 = _side(b0, b1, a1)
    return d1 * d2 < 0 and d3 * d4 < 0


def repair_loop(records, gap_budget_mm=GAP_BUDGET_MM, short_mm=SHORT_MM):
    """``(records, code, sentence, notes)`` - the decisions, not the drawing.

    A segment shorter than Revit will build goes.  A segment followed by its
    own reverse is one of two things: a *duplicate* traversal, which a room
    separation line drawn along a wall leaves, where the second copy goes; or
    a *spur* - a stub wall poking into the room, where the boundary walks in
    and straight back out - where both go, or the loop would be left with a
    gap the width of the stub.  The record after the pair tells them apart.
    A small gap between neighbours is closed by moving the next start onto
    the previous end, never by inserting a bridge, because a bridge that
    small is itself a curve Revit refuses.  A gap too large to close refuses
    the loop by name rather than letting Revit throw.
    """
    notes = []
    sized = []
    for record in records or []:
        start, end = _record_ends(record)
        if start is None:
            continue
        if _distance(start, end) < short_mm and record[0] != "A":
            notes.append("short_segment")
            continue
        sized.append(record)

    sized = _drop_reversals(sized, short_mm, notes)

    kept = []
    for record in sized:
        start, _end = _record_ends(record)
        if kept:
            previous_end = _record_ends(kept[-1])[1]
            gap = _distance(previous_end, start)
            if gap > gap_budget_mm:
                return [], "gap_too_large", CODE_SENTENCES["gap_too_large"].format(
                    int(round(gap))), notes
            if gap > 1e-9:
                notes.append("gap_bridged")
                record = _with_start(record, previous_end)
        kept.append(record)

    if len(kept) < 3:
        return [], "loop_open", CODE_SENTENCES["loop_open"], notes

    first_start = _record_ends(kept[0])[0]
    last_end = _record_ends(kept[-1])[1]
    closing = _distance(last_end, first_start)
    if closing > gap_budget_mm:
        return [], "loop_open", CODE_SENTENCES["loop_open"], notes
    if closing > 1e-9:
        notes.append("gap_bridged")
        kept[-1] = _with_end(kept[-1], first_start)
    return kept, "", u"", notes


def _drop_reversals(records, short_mm, notes):
    """Remove reversed pairs, as duplicates or as spurs, until none are left."""
    items = list(records)
    for _pass in range(len(items) + 1):
        changed = False
        result = []
        index = 0
        while index < len(items):
            record = items[index]
            following = items[index + 1] if index + 1 < len(items) else None
            if following is not None:
                start, end = _record_ends(record)
                next_start, next_end = _record_ends(following)
                if _close(start, next_end, short_mm) and _close(end, next_start, short_mm):
                    after = items[index + 2] if index + 2 < len(items) else \
                        (result[0] if result else None)
                    after_start = _record_ends(after)[0] if after is not None else None
                    is_spur = after_start is not None and \
                        _close(after_start, start, short_mm) and \
                        not _close(after_start, end, short_mm)
                    if is_spur:
                        notes.append("spur_removed")
                    else:
                        notes.append("reversed_duplicate")
                        result.append(record)
                    index += 2
                    changed = True
                    continue
            result.append(record)
            index += 1
        items = result
        if not changed:
            break
    return items


def _with_start(record, point):
    kind = record[0]
    if kind == "L":
        return ("L", point[0], point[1], record[3], record[4])
    if kind == "A":
        return ("A", point[0], point[1], record[3], record[4], record[5], record[6])
    points = list(record[1] or [])
    if points:
        points[0] = (point[0], point[1])
    return ("T", points)


def _with_end(record, point):
    kind = record[0]
    if kind == "L":
        return ("L", record[1], record[2], point[0], point[1])
    if kind == "A":
        return ("A", record[1], record[2], point[0], point[1], record[5], record[6])
    points = list(record[1] or [])
    if points:
        points[-1] = (point[0], point[1])
    return ("T", points)


def simplified_outline(points_mm, min_mm=SHORT_MM):
    """The polygon Revit is likeliest to accept, when the true loop is refused.

    No vertex closer to its neighbour than Revit will build, no exactly
    collinear vertex, no spike - the things a sketch validator rejects.  It
    is the same outline within a millimetre, drawn with straight edges, and
    the tool says so when it has to fall back on it.
    """
    points = [(to_float(x), to_float(y)) for x, y in points_mm or []]
    for _pass in range(len(points) + 2):
        before = len(points)
        cleaned = []
        for point in points:
            if cleaned and _distance(cleaned[-1], point) < min_mm:
                continue
            cleaned.append(point)
        while len(cleaned) > 1 and _distance(cleaned[-1], cleaned[0]) < min_mm:
            cleaned.pop()
        points = cleaned
        if len(points) >= 3:
            for index in range(len(points)):
                a = points[index - 1]
                b = points[index]
                c = points[(index + 1) % len(points)]
                ab = _distance(a, b)
                bc = _distance(b, c)
                cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
                if ab == 0.0 or bc == 0.0 or abs(cross) <= 1e-6 * ab * bc:
                    del points[index]
                    break
        if len(points) == before:
            break
    return points if len(points) >= 3 else []


def _record_ends(record):
    if not record:
        return None, None
    kind = record[0]
    if kind == "L":
        return (to_float(record[1]), to_float(record[2])), (to_float(record[3]), to_float(record[4]))
    if kind == "A":
        return (to_float(record[1]), to_float(record[2])), (to_float(record[3]), to_float(record[4]))
    points = record[1] or []
    if len(points) < 2:
        return None, None
    return (to_float(points[0][0]), to_float(points[0][1])), \
           (to_float(points[-1][0]), to_float(points[-1][1]))


def _distance(a, b):
    if a is None or b is None:
        return 0.0
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def classify_loops(loops):
    """``{"outer": [...], "inner": [...], "notes": [...]}`` by signed area.

    Loop 0 is not assumed to be the outer one: Revit does not promise it, and
    a room with separation-line islands legitimately returns two outer loops.
    Size decides, and containment sorts the rest into holes.
    """
    sized = []
    notes = []
    for index, loop in enumerate(loops or []):
        area = abs(signed_area2(loop))
        if area <= 0:
            notes.append("empty_loop")
            continue
        sized.append((area, index, loop))
    if not sized:
        return {"outer": [], "inner": [], "notes": notes}
    sized.sort(reverse=True)
    biggest = sized[0]
    outer = [biggest[2]]
    inner = []
    for area, _index, loop in sized[1:]:
        if _point_inside(loop[0], biggest[2]):
            inner.append(loop)
        else:
            outer.append(loop)
            notes.append("disjoint_part")
    return {"outer": outer, "inner": inner, "notes": notes}


def _point_inside(point, polygon):
    """Ray cast; the polygon is a closed integer ring."""
    inside = False
    count = len(polygon)
    x, y = point
    for index in range(count):
        x0, y0 = polygon[index]
        x1, y1 = polygon[(index + 1) % count]
        if (y0 > y) != (y1 > y):
            if y1 != y0:
                crossing = x0 + (y - y0) * (x1 - x0) / float(y1 - y0)
                if crossing > x:
                    inside = not inside
    return inside


# ------------------------------------------------------------------ gates


#: Every skip the tool can produce.  A test asserts each has a sentence, so a
#: new code can never reach a person as a bare token.
CODE_SENTENCES = {
    "not_placed": u"It is not placed in the model, so it has no boundary.",
    "not_enclosed": u"Its boundary is not enclosed, so Revit reports no area.",
    "redundant": u"Revit reports it as a redundant room.",
    "loop_open": u"Its boundary does not close, so no region can be drawn.",
    "gap_too_large": u"Its boundary has a {0} mm gap, too wide to bridge.",
    "self_intersecting": u"Its boundary crosses itself, which Revit will not accept.",
    "sliver": u"It is smaller than a square centimetre.",
    "too_many_loops": u"It has more boundary loops than the tool will draw.",
    "too_many_vertices": u"One of its loops has more vertices than the tool will draw.",
    "in_design_option": u"It belongs to design option '{0}'; tick the option box to include it.",
    "already_drawn": u"A region for it already exists in this view.",
    "owned_by_other": u"'{0}' has it checked out.",
    "view_is_template": u"It is a view template.",
    "view_plane_vertical": u"A section or elevation cannot hold a horizontal boundary.",
    "view_no_model_plane": u"A drafting view or legend has no model coordinates.",
    "view_no_detail_items": u"That kind of view cannot hold a filled region.",
    "view_no_level": u"It has no level, so no rooms can be matched to it.",
    "no_region_type": u"This model has no filled region type to draw with.",
    "link_not_loaded": u"The link is not loaded, so its rooms cannot be read.",
    "wrong_level": u"It is not on this view's level.",
    "wrong_phase": u"It is not in this view's phase.",
    "boundary_unreadable": u"Its boundary could not be read: {0}.",
    "revit_refused": u"Revit refused to draw it: {0}.",
    "source_not_in_run": u"Its source was not part of this run.",
    "cancelled": u"The scan stopped before reaching it.",
    "not_visible": u"It is not visible in this view, so no region was planned.",
    "visibility_unknown": u"What {0} shows could not be read, so every room on its level was "
                          u"offered.",
    "link_hidden": u"{0} is hidden in {1}, so none of its rooms were planned.",
    "rooms_hidden_in_view": u"The {0} category is hidden in {1} (often by its view template), "
                            u"so none of {2}'s rooms were planned.",
}


def sentence_for(code, *args):
    text = CODE_SENTENCES.get(code, u"")
    if args and u"{0}" in text:
        try:
            return text.format(*args)
        except Exception:
            return text
    return text


def view_verdict(view_type_name, is_template):
    """``(ok, code, sentence)`` for one view."""
    if is_template:
        return False, "view_is_template", CODE_SENTENCES["view_is_template"]
    name = safe_text(view_type_name)
    if name in PLAN_VIEW_TYPES:
        return True, "", u""
    if name in VERTICAL_VIEW_TYPES:
        return False, "view_plane_vertical", CODE_SENTENCES["view_plane_vertical"]
    if name in NO_MODEL_PLANE_VIEW_TYPES:
        return False, "view_no_model_plane", CODE_SENTENCES["view_no_model_plane"]
    return False, "view_no_detail_items", CODE_SENTENCES["view_no_detail_items"]


def room_verdict(room, include_design_options=False):
    """``(ok, code, sentence)`` for one room or space, before any geometry."""
    room = room or {}
    if room.get("redundant"):
        return False, "redundant", CODE_SENTENCES["redundant"]
    if not room.get("placed"):
        return False, "not_placed", CODE_SENTENCES["not_placed"]
    if to_float(room.get("area")) <= 0.0:
        return False, "not_enclosed", CODE_SENTENCES["not_enclosed"]
    option = safe_text(room.get("design_option"))
    if option and not include_design_options:
        return False, "in_design_option", sentence_for("in_design_option", option)
    return True, "", u""


def view_shows_room(view, room):
    """``(ok, code, sentence)`` - this view's level and this view's phase.

    Stated in the window in exactly those words, so the rule is never a
    surprise: a Level 2 plan draws Level 2 rooms.
    """
    view = view or {}
    room = room or {}
    # A view carries every key its level answers to: its own level id for
    # rooms in this model, and the level's elevation for rooms in a link,
    # whose ids mean nothing here.
    keys = view.get("level_keys")
    if keys is None:
        keys = [view.get("level_key")] if view.get("level_key") is not None else []
    if not keys:
        return False, "view_no_level", CODE_SENTENCES["view_no_level"]
    if room.get("level_key") not in keys:
        return False, "wrong_level", CODE_SENTENCES["wrong_level"]
    view_phase = view.get("phase_name")
    room_phase = room.get("phase_name")
    if view_phase and room_phase and view_phase != room_phase:
        return False, "wrong_phase", CODE_SENTENCES["wrong_phase"]
    return True, "", u""


# ------------------------------------------------------------------ setup


#: How many rows one expander draws before it says "search to narrow".
ROW_CAP = type_checklist.ROW_CAP

#: Sources come back under one heading; views under their own kind, with the
#: plans a person actually draws in first.
SOURCE_ORDER = (u"Sources",)
VIEW_ORDER = (u"Floor plans", u"Engineering plans", u"Area plans", u"Ceiling plans")


def source_rule(row, kind=None):
    """``(ticked, reason)`` for a model that could supply rooms or spaces.

    Rooms: ticked when the source actually has some, which is what makes
    the MEP case work without being told - the rooms are in the architectural
    link and the run picks them up from there.  Spaces: this model only, by
    default; a linked model's spaces are the other discipline's business and
    are offered unticked.
    """
    row = row or {}
    if not row.get("loaded", True):
        return False, u"not loaded"
    if kind == KIND_SPACE and not row.get("is_host"):
        return False, u"spaces are read from this model unless you tick it"
    count = to_int(row.get("count"))
    if count:
        return True, u"{0:,} to convert".format(count)
    return False, u"none of this kind"


def source_rule_for(kind):
    """The rule as a one-argument callable, the shape the checklist wants."""
    def _rule(row):
        return source_rule(row, kind)
    return _rule


def view_rule(row):
    """``(ticked, reason)`` for one view.

    A ceiling plan is offered because people do put room outlines on an RCP,
    but it is not ticked: an RCP shows the room's boundary, never the edge of
    the ceiling, and that is worth choosing on purpose.
    """
    row = row or {}
    if row.get("view_type") in UNTICKED_VIEW_TYPES:
        return False, u"shows the room outline, not the ceiling edge"
    return bool(row.get("default_ticked", True)), u""


def preselect_sources(rows, settings):
    settings = settings or {}
    return type_checklist.preselect_rows(rows, settings.get("source_keys"),
                                         settings.get("unticked_source_keys"),
                                         source_rule_for(settings.get("kind")))


def preselect_views(rows, settings):
    settings = settings or {}
    return type_checklist.preselect_rows(rows, settings.get("view_names"),
                                         settings.get("unticked_view_names"), view_rule)


def retick_sources(rows, kind=None):
    """Re-run the rule after the kind changed, keeping deliberate ticks.

    Flipping Rooms to Spaces changes every count and the rule itself, so a
    source ticked only because the rule ticked it has to be reconsidered;
    one the user touched by hand is left exactly as they left it.
    """
    for row in rows or []:
        if row.get("touched"):
            continue
        ticked, reason = source_rule(row, kind)
        row["is_checked"], row["reason"] = bool(ticked), reason
    return rows


def group_sources(rows):
    return type_checklist.group_rows(rows, order=SOURCE_ORDER, expanded=SOURCE_ORDER)


def group_views(rows):
    return type_checklist.group_rows(rows, order=VIEW_ORDER, expanded=VIEW_ORDER[:1])


def source_count_text(rows):
    return type_checklist.count_text(rows, u"sources ticked")


def view_count_text(rows):
    return type_checklist.count_text(rows, u"views ticked")


def ticked(rows):
    return [row for row in rows or [] if row.get("is_checked")]


def apply_setup(settings, source_rows, view_rows, options):
    """The window's state -> the settings dict that is saved and re-read.

    Both lists are folded through ``type_checklist``, so a link or a view
    that was not in this model keeps whatever was decided about it last time
    rather than being quietly forgotten.
    """
    result = dict(settings or {})
    result.update(options or {})
    saved, unticked = type_checklist.fold_choices(
        result.get("source_keys"), result.get("unticked_source_keys"), source_rows,
        source_rule_for(result.get("kind")))
    result["source_keys"], result["unticked_source_keys"] = saved, unticked
    saved, unticked = type_checklist.fold_choices(
        result.get("view_names"), result.get("unticked_view_names"), view_rows, view_rule)
    result["view_names"], result["unticked_view_names"] = saved, unticked
    return result


def config_from_settings(settings, boundary_location, region_type=None, line_style=None):
    """What ``build_plan`` and the writer read; one place decides it."""
    settings = settings or {}
    return {
        "kind": settings.get("kind") or KIND_ROOM,
        "scope": settings.get("scope") or SCOPE_ACTIVE,
        "subject": settings.get("subject") or SUBJECT_ALL,
        "boundary_source": settings.get("boundary_source") or "document",
        "boundary_location": boundary_location or "Finish",
        "region_type": dict(region_type or {}),
        "line_style": dict(line_style or {}),
        "replace_existing": bool(settings.get("replace_existing")),
        "include_design_options": bool(settings.get("include_design_options")),
        "grid_mm": to_float(settings.get("grid_mm"), GRID_MM) or GRID_MM,
    }


def effective_location(settings, resolved_name, resolved_source, resolved_note):
    """``(name, source, sentence)`` once the user's override is applied."""
    settings = settings or {}
    if (settings.get("boundary_source") or "document") == "override":
        name = settings.get("boundary_override") or "Finish"
        if name not in BOUNDARY_LOCATIONS:
            name = "Finish"
        return name, "override", u"Overridden: {0}".format(
            BOUNDARY_SENTENCES.get(name, name))
    sentence = u"This model computes to {0}.".format(
        BOUNDARY_SENTENCES.get(resolved_name, resolved_name))
    if resolved_note:
        sentence = u"{0} {1}".format(sentence, resolved_note)
    return resolved_name, resolved_source, sentence


# ------------------------------------------------------------------- plan


def pair_key(view_uid, room_uid, link_uid=u""):
    """What one region is called, for the whole life of the relationship."""
    return u"{0}|{1}|{2}".format(safe_text(link_uid), safe_text(view_uid), safe_text(room_uid))


def build_plan(config, views, rooms, boundaries, existing, visibility=None):
    """One plan object; the preview and the executor both read this one.

    ``boundaries`` is keyed by room uid, so a room's boundary is read once and
    reused across every view it appears in - the difference between two
    thousand reads and ten thousand on a real job.

    ``visibility`` is ``{view uid: {"visible": set of room uids or None,
    "note": text}}`` - what each view actually shows.  A room the view does
    not show is counted and said once per view, never rowed: there is nothing
    to decide about it.  A room the view shows from another level is offered
    ticked under its own heading, so a view range that reaches down a storey
    is drawn as the person set it and still shows as what it is.  A view
    whose contents could not be read offers every room on its level and says
    so.
    """
    config = config or {}
    grid_mm = to_float(config.get("grid_mm"), GRID_MM) or GRID_MM
    replace = bool(config.get("replace_existing"))
    include_options = bool(config.get("include_design_options"))
    existing = existing or {}
    visibility = visibility or {}
    # A room somebody picked by hand is wanted wherever it can be drawn: it
    # is ticked whatever level it sits on, and a view whose contents could
    # not be read still offers it.
    picked = (config.get("subject") or SUBJECT_ALL) == SUBJECT_ONE

    items = []
    skips = []
    pairs = 0
    notes = []
    not_visible = 0
    other_level = 0

    room_gate = {}
    for room in rooms or []:
        ok, code, sentence = room_verdict(room, include_options)
        if not ok:
            room_gate[room["uid"]] = (code, sentence)

    for view in views or []:
        ok, code, sentence = view_verdict(view.get("view_type"), view.get("is_template"))
        if not ok:
            skips.append(_skip(u"view", view.get("name"), code, sentence, view_uid=view.get("uid")))
            continue
        shown = visibility.get(safe_text(view.get("uid"))) or {}
        visible = shown.get("visible")
        if shown.get("note"):
            notes.append(shown["note"])
        hidden_here = 0
        for room in rooms or []:
            pairs += 1
            key = pair_key(view.get("uid"), room.get("uid"), room.get("link_uid"))
            title = _pair_title(view, room)
            gate = room_gate.get(room["uid"])
            if gate is not None:
                skips.append(_skip(u"pair", title, gate[0], gate[1], key=key))
                continue
            if visible is not None and room.get("uid") not in visible:
                # Not this view's to draw, and nothing to decide: counted,
                # said once per view below, never a row.
                not_visible += 1
                hidden_here += 1
                continue
            on_level, level_code, level_sentence = view_shows_room(view, room)
            if visible is None and not on_level and not picked:
                # Nothing is known about what the view shows, so the level is
                # the only rule left; a room on another level is not offered.
                not_visible += 1
                hidden_here += 1
                continue
            boundary = (boundaries or {}).get(room.get("uid")) or {}
            if boundary.get("code"):
                skips.append(_skip(u"pair", title, boundary["code"],
                                   boundary.get("sentence") or sentence_for(boundary["code"]),
                                   key=key))
                continue
            loops = boundary.get("loops") or []
            if not loops:
                skips.append(_skip(u"pair", title, "not_enclosed",
                                   CODE_SENTENCES["not_enclosed"], key=key))
                continue
            found = existing.get(key)
            if found and not replace:
                skips.append(_skip(u"pair", title, "already_drawn",
                                   CODE_SENTENCES["already_drawn"], key=key, quiet=True))
                continue
            if not on_level:
                other_level += 1
            items.append({
                "key": key,
                "action": "replace" if found else "create",
                "title": title,
                "category": CATEGORY_THIS_LEVEL if on_level else CATEGORY_OTHER_LEVEL,
                "category_reason": u"" if on_level else level_sentence,
                # Everything the preview offers starts ticked; the heading a
                # pair sits under says why it is there, the tick is the run.
                "default_ticked": True,
                "room_uid": room.get("uid"),
                "room_id": room.get("id"),
                "room_number": room.get("number"),
                "room_name": room.get("name"),
                "link_uid": room.get("link_uid") or u"",
                "link_title": room.get("link_title") or u"",
                "view_uid": view.get("uid"),
                "view_id": view.get("id"),
                "view_name": view.get("name"),
                "level_name": view.get("level_name"),
                "phase_name": view.get("phase_name"),
                "boundary_key": room.get("uid"),
                "fingerprints": boundary.get("fingerprints") or {},
                "replaces_region_id": found.get("region_id") if found else None,
                "in_group": bool(room.get("in_group")),
            })
        if hidden_here:
            notes.append(u"{0:,} room(s) are not visible in {1} and were not planned.".format(
                hidden_here, safe_text(view.get("name"))))

    counts = {
        "views": len(views or []),
        "rooms": len(rooms or []),
        "pairs": pairs,
        "create": len([item for item in items if item["action"] == "create"]),
        "replace": len([item for item in items if item["action"] == "replace"]),
        "skipped": len([skip for skip in skips if skip["scope"] == u"pair"]),
        "not_visible": not_visible,
        "other_level": other_level,
    }
    return {
        "mode": "create",
        "kind": config.get("kind") or KIND_ROOM,
        "boundary_location": config.get("boundary_location") or "Finish",
        "boundary_source": config.get("boundary_source") or "document",
        "grid_mm": grid_mm,
        "region_type": config.get("region_type") or {},
        "line_style": config.get("line_style") or {},
        "items": items,
        "skips": skips,
        "counts": counts,
        "acknowledgements": acknowledgements(counts),
        "refusal": refusal(counts),
        "notes": notes,
    }


def _pair_title(view, room):
    label = safe_text(room.get("number"))
    name = safe_text(room.get("name"))
    if label and name:
        label = u"{0} {1}".format(label, name)
    else:
        label = label or name or u"Unnamed"
    if room.get("link_title"):
        label = u"{0} · {1}".format(safe_text(room["link_title"]), label)
    return u"{0} · {1}".format(label, safe_text(view.get("name")))


def _skip(scope, title, code, sentence, key=u"", view_uid=u"", quiet=False):
    return {"scope": scope, "key": safe_text(key), "title": safe_text(title),
            "code": code, "reason": sentence or sentence_for(code),
            "view_uid": safe_text(view_uid), "quiet": bool(quiet)}


def acknowledgements(counts):
    """Explicit ticks for anything surprising, per the house write rules."""
    counts = counts or {}
    asks = []
    planned = to_int(counts.get("create")) + to_int(counts.get("replace"))
    if planned > ACK_PAIRS:
        asks.append({"key": "large_batch",
                     "text": u"This draws {0:,} filled regions. They are one undo step, but "
                             u"it will take a while.".format(planned)})
    if to_int(counts.get("replace")):
        asks.append({"key": "replace",
                     "text": u"{0:,} existing region(s) will be deleted and drawn again. Any "
                             u"hand edits to them are lost.".format(to_int(counts["replace"]))})
    return asks


def refusal(counts):
    """``(text)`` when the batch is too large to attempt at all."""
    counts = counts or {}
    planned = to_int(counts.get("create")) + to_int(counts.get("replace"))
    if planned > MAX_PAIRS:
        return u"{0:,} regions is beyond what this tool will draw in one run. Narrow the " \
               u"views or the source and try again.".format(planned)
    return u""


def plan_is_runnable(plan):
    plan = plan or {}
    return bool(plan.get("items")) and not plan.get("refusal")


# ------------------------------------------------------------ the record


RECORD_VERSION = 1


def make_record(item, room_fingerprints, region_fingerprints, config, created_utc=u""):
    """What gets written into the region's own Extensible Storage entity.

    Names travel beside the ids on purpose: when the room is deleted, its
    name is the only thing left that tells a person what the orphaned region
    used to be, and that is exactly the moment they need it.
    """
    config = config or {}
    return {
        "v": RECORD_VERSION,
        "kind": config.get("kind") or KIND_ROOM,
        "source": SOURCE_LINK if item.get("link_uid") else SOURCE_HOST,
        "link_uid": safe_text(item.get("link_uid")),
        "link_title": safe_text(item.get("link_title")),
        "room_uid": safe_text(item.get("room_uid")),
        "room_number": safe_text(item.get("room_number")),
        "room_name": safe_text(item.get("room_name")),
        "level_name": safe_text(item.get("level_name")),
        "phase_name": safe_text(item.get("phase_name")),
        "view_uid": safe_text(item.get("view_uid")),
        "view_name": safe_text(item.get("view_name")),
        "boundary_location": config.get("boundary_location") or "Finish",
        "line_style": safe_text((config.get("line_style") or {}).get("name")),
        "grid_mm": to_float(config.get("grid_mm"), GRID_MM) or GRID_MM,
        "room": digests_only(room_fingerprints),
        "region": digests_only(region_fingerprints),
        "in_group": bool(item.get("in_group")),
        "created_utc": safe_text(created_utc),
    }


def encode(record):
    """Compact, ASCII-only JSON; the entity holds one string and nothing else.

    Not ``json.dumps``: under IronPython the standard encoder chokes on any
    string with a character above ``\\x7f`` - a room called "Café" took the
    whole write down with "'unknown' codec can't decode byte 0xe9".
    ``json_text`` escapes such characters itself, and ``decode`` reads the
    escapes back on both runtimes.
    """
    return json_text.dumps(record or {}, separators=(",", ":"), sort_keys=True)


def decode(text):
    """Whatever is in the model -> a record this build can read.

    A record written by a newer EasyBIM is read as far as it is understood.
    The worst outcome of a strange record must be one row saying so, never a
    broken command.
    """
    try:
        raw = json.loads(safe_text(text) or "{}")
    except Exception:
        return {}
    if not isinstance(raw, dict) or not raw:
        # An empty entity is "no record", and a caller testing the result
        # should get something falsy rather than a shell of empty keys.
        return {}
    record = dict(raw)
    for name in ("room", "region"):
        if not isinstance(record.get(name), dict):
            record[name] = {}
    return record


# ------------------------------------------------------------------ drift


#: ``(key, title, is_problem)`` in report order: what drifted first, then the
#: quiet rows, then what was set aside.
DRIFT_BUCKETS = (
    ("room_moved", u"The room's boundary changed", True),
    ("both_changed", u"The room changed and the region was edited", True),
    ("region_edited", u"The region was edited by hand", True),
    ("region_moved", u"The region was moved", True),
    ("drifted", u"The region no longer matches its room", True),
    ("copied_region", u"Copied from another view", True),
    ("duplicate", u"More than one region for one room", True),
    ("orphan_room", u"The room is gone", True),
    ("orphan_view", u"The view no longer shows this room", True),
    ("unreadable", u"The region could not be judged", False),
    ("link_not_loaded", u"The link is not loaded", False),
    ("source_not_in_run", u"Source not in this run", False),
    ("in_sync", u"In step with the room", False),
    ("accepted", u"Accepted differences - set aside on review", False),
)
DRIFT_TITLES = dict((key, title) for key, title, _problem in DRIFT_BUCKETS)
DRIFT_PROBLEMS = tuple(key for key, _title, problem in DRIFT_BUCKETS if problem)
ACCEPTED_BUCKET = "accepted"

#: What each bucket lets you do about it.  Update redraws the region from
#: the room as it is now; Delete is for a region nothing could be redrawn
#: from; Accept Difference is offered on every problem row and is permanent
#: until restored - the list lives in the model.
CAN_UPDATE = ("room_moved", "both_changed", "region_edited", "region_moved", "drifted")
CAN_DELETE = ("copied_region", "duplicate", "orphan_room", "orphan_view")



def room_key(link_uid, room_uid):
    return u"{0}|{1}".format(safe_text(link_uid), safe_text(room_uid))


def classify_drift(live, rooms_now, views_now, accepted=None, loaded_links=None,
                   run_sources=None):
    """Every stored relationship judged against the model as it is now.

    ``live`` is one entry per region carrying a record, its outline as it is
    now, and ``room_visible`` - whether its own view still shows the room.
    ``rooms_now`` is keyed by ``room_key`` and holds the room's outline
    recomputed at the location that region was drawn with, so a model whose
    Area and Volume setting changed since does not read as thousands of
    false drifts.

    The verdict is the question a person asks: does the region match its
    room *now*?  The digests the record kept only say who moved - and when
    they cannot, the row still says the region drifted and by how much,
    because "unknown" is never read as "in step".

    ``accepted`` is what a reviewer set aside with Accept Difference.  It is
    permanent: an accepted region stays accepted whatever the room or the
    region do next, until somebody restores it.
    """
    accepted = set(safe_text(value) for value in accepted or [])
    rooms_now = rooms_now or {}
    views_now = views_now or {}
    loaded_links = set(safe_text(value) for value in loaded_links or [])
    run_sources = set(safe_text(value) for value in run_sources or []) if run_sources else None

    items_by_bucket = dict((key, []) for key, _title, _problem in DRIFT_BUCKETS)
    notes = []
    by_pair = {}

    for entry in live or []:
        record = entry.get("record") or {}
        link_uid = safe_text(record.get("link_uid"))
        key = room_key(link_uid, record.get("room_uid"))
        pair = (key, safe_text(entry.get("owner_view_uid")))
        by_pair.setdefault(pair, []).append(entry)

    drift_by_link = {}
    for entry in live or []:
        record = entry.get("record") or {}
        region_uid = safe_text(entry.get("region_uid"))
        link_uid = safe_text(record.get("link_uid"))
        key = room_key(link_uid, record.get("room_uid"))
        owner_view = safe_text(entry.get("owner_view_uid"))
        row = _drift_row(entry, record)

        if region_uid in accepted:
            _file(items_by_bucket, row, ACCEPTED_BUCKET,
                  u"Accepted as a deliberate difference; it stays accepted until restored.")
            continue
        if link_uid and link_uid not in loaded_links:
            _file(items_by_bucket, row, "link_not_loaded", CODE_SENTENCES["link_not_loaded"])
            continue
        if run_sources is not None and (link_uid or HOST_KEY) not in run_sources:
            _file(items_by_bucket, row, "source_not_in_run", CODE_SENTENCES["source_not_in_run"])
            continue
        if len(by_pair.get((key, owner_view)) or []) > 1:
            oldest = sorted(by_pair[(key, owner_view)],
                            key=lambda item: safe_text((item.get("record") or {}).get("created_utc")))
            if entry is not oldest[0]:
                _file(items_by_bucket, row, "duplicate",
                      u"Another region already stands for this room in this view.")
                continue
        if owner_view and safe_text(record.get("view_uid")) and owner_view != safe_text(record.get("view_uid")):
            _file(items_by_bucket, row, "copied_region",
                  u"Its record names '{0}', but it now sits in another view, so it was "
                  u"copied. Its record cannot be trusted.".format(safe_text(record.get("view_name"))))
            continue

        room = rooms_now.get(key)
        if room is None:
            _file(items_by_bucket, row, "orphan_room",
                  u"'{0}' is no longer in the model.".format(_room_label(record)))
            continue
        if entry.get("room_visible") is False:
            view = views_now.get(owner_view) or {}
            _file(items_by_bucket, row, "orphan_view",
                  u"{0} no longer shows this room; updating would not fix that.".format(
                      safe_text(view.get("name")) or u"The view"))
            continue
        if not entry.get("readable", True):
            _file(items_by_bucket, row, "unreadable",
                  u"Its outline could not be read, so it was not judged.")
            continue

        # Judged at the location the region was actually drawn with, not
        # today's: changing the model's Area and Volume setting must not turn
        # every region in the job into a false drift.
        room_fingerprints = (room.get("by_location") or {}).get(
            safe_text(record.get("boundary_location"))) or room.get("fingerprints")
        grid_mm = record.get("grid_mm")
        verdict, deviation, sentence = sync_verdict(entry.get("fingerprints"), room_fingerprints,
                                                    grid_mm)
        if verdict == "unknown":
            _file(items_by_bucket, row, "unreadable", sentence)
            continue

        room_verdict_name, room_sentence = compare(record.get("room"), room_fingerprints, grid_mm)
        region_verdict_name, region_sentence = compare(record.get("region"),
                                                       entry.get("fingerprints"), grid_mm)
        room_changed = room_verdict_name in ("moved", "edited")
        region_changed = region_verdict_name in ("moved", "edited")
        if link_uid:
            counter = drift_by_link.setdefault(link_uid, [0, 0])
            counter[1] += 1
            if room_changed:
                counter[0] += 1

        if verdict == "in_sync":
            if room_verdict_name == "unchanged" and region_verdict_name == "unchanged":
                _file(items_by_bucket, row, "in_sync", u"")
            else:
                # The outlines agree; only the record is behind - written
                # before Revit had finished the sketch, or by an older build.
                _file(items_by_bucket, row, "in_sync",
                      u"Matches the room now; only its record is behind.")
            continue

        if room_changed and region_changed:
            _file(items_by_bucket, row, "both_changed",
                  u"The room: {0} The region: {1} Updating would discard the edit.".format(
                      room_sentence, region_sentence))
        elif room_changed:
            _file(items_by_bucket, row, "room_moved", u"{0} {1}".format(room_sentence, sentence))
        elif region_verdict_name == "edited":
            _file(items_by_bucket, row, "region_edited", u"{0} {1}".format(region_sentence, sentence))
        elif region_verdict_name == "moved":
            _file(items_by_bucket, row, "region_moved", u"{0} {1}".format(region_sentence, sentence))
        else:
            _file(items_by_bucket, row, "drifted", sentence)
        row["deviation_mm"] = deviation

    for link_uid, counter in drift_by_link.items():
        changed, total = counter
        if total >= 5 and changed >= max(3, int(total * 0.8)):
            notes.append(u"Nearly every region from one link drifted at once ({0} of {1}). The "
                         u"link was probably moved or reloaded, rather than {1} rooms "
                         u"changing.".format(changed, total))

    buckets = []
    counts = {}
    for key, title, problem in DRIFT_BUCKETS:
        rows = items_by_bucket[key]
        rows.sort(key=lambda row: safe_text(row.get("title")).lower())
        counts[key] = len(rows)
        buckets.append({"key": key, "title": title, "is_problem": problem,
                        "noun": u"region" if len(rows) == 1 else u"regions", "items": rows})
    return {
        "buckets": buckets,
        "counts": counts,
        "problem_count": sum(counts[key] for key in DRIFT_PROBLEMS),
        "tracked": len(live or []),
        "accepted_count": counts[ACCEPTED_BUCKET],
        "notes": notes,
    }


def _drift_row(entry, record):
    return {
        "key": safe_text(entry.get("region_uid")),
        "title": _drift_title(record),
        "detail": u"",
        "bucket": "",
        "region_id": entry.get("region_id"),
        "region_uid": safe_text(entry.get("region_uid")),
        "room_uid": safe_text(record.get("room_uid")),
        "link_uid": safe_text(record.get("link_uid")),
        "view_id": entry.get("owner_view_id"),
        "view_uid": safe_text(entry.get("owner_view_uid")),
        "is_accepted": False, "is_checked": False,
        "can_update": False, "can_delete": False,
        "deviation_mm": None,
    }


def _drift_title(record):
    parts = []
    if record.get("link_title"):
        parts.append(safe_text(record["link_title"]))
    parts.append(_room_label(record))
    if record.get("view_name"):
        parts.append(safe_text(record["view_name"]))
    return u" · ".join(part for part in parts if part)


def _room_label(record):
    number = safe_text(record.get("room_number"))
    name = safe_text(record.get("room_name"))
    if number and name:
        return u"{0} {1}".format(number, name)
    return number or name or u"Unnamed"


def _file(items_by_bucket, row, bucket, detail):
    row["bucket"] = bucket
    row["detail"] = detail
    row["is_accepted"] = bucket == ACCEPTED_BUCKET
    row["can_update"] = bucket in CAN_UPDATE
    row["can_delete"] = bucket in CAN_DELETE
    items_by_bucket[bucket].append(row)


# ---------------------------------------------------------------- reports


def plan_summary(plan):
    plan = plan or {}
    counts = plan.get("counts") or {}
    location = plan.get("boundary_location") or "Finish"
    parts = [u"{0:,} region(s) to draw".format(
        to_int(counts.get("create")) + to_int(counts.get("replace")))]
    if to_int(counts.get("other_level")):
        parts.append(u"{0:,} of them visible from another level".format(
            to_int(counts["other_level"])))
    if to_int(counts.get("replace")):
        parts.append(u"{0:,} replacing an existing one".format(to_int(counts["replace"])))
    if to_int(counts.get("skipped")):
        parts.append(u"{0:,} skipped, each with a reason".format(to_int(counts["skipped"])))
    if to_int(counts.get("not_visible")):
        parts.append(u"{0:,} not visible in their view".format(to_int(counts["not_visible"])))
    return u"{0}. Measured to {1}. One undo step.".format(
        u", ".join(parts), BOUNDARY_SENTENCES.get(location, location))


def drift_summary(report):
    report = report or {}
    counts = report.get("counts") or {}
    tracked = to_int(report.get("tracked"))
    problems = to_int(report.get("problem_count"))
    if not tracked:
        return u"No region in this model carries a room relationship yet."
    if problems:
        text = u"{0:,} of {1:,} tracked region(s) have drifted".format(problems, tracked)
    else:
        text = u"All {0:,} tracked region(s) are in step with their rooms".format(tracked)
    set_aside = to_int(report.get("accepted_count"))
    if set_aside:
        text += u", {0:,} accepted".format(set_aside)
    return text + u"."


def scan_notes(plan_or_report, config, extra=None):
    """The honest limits, said once, in the notes panel."""
    config = config or {}
    notes = list((plan_or_report or {}).get("notes") or [])
    notes.extend(extra or [])
    location = config.get("boundary_location") or "Finish"
    if config.get("boundary_source") == "document":
        notes.append(u"Boundaries follow this model's own Area and Volume Computation setting: "
                     u"{0}.".format(BOUNDARY_SENTENCES.get(location, location)))
    else:
        notes.append(u"Boundaries follow the override you chose: {0}. This model computes to "
                     u"something else.".format(BOUNDARY_SENTENCES.get(location, location)))
    notes.append(u"A view is drawn with the rooms it actually shows. Rooms it shows from "
                 u"another level are offered unticked; rooms it hides are not planned.")
    notes.append(u"A region counts as in step while its outline sits within {0:g} mm of the "
                 u"room's.".format(SYNC_TOLERANCE_MM))
    notes.append(u"The relationship is stored in each region inside this model, and the "
                 u"accepted differences in a hidden record beside it, so both come back next "
                 u"time and reach the team after a Sync to Central.")
    notes.append(u"A region cannot be added to a group, so a room in a group drifts when the "
                 u"group moves.")
    notes.append(u"Sub-millimetre nudges can read as an edit rather than a move, because the "
                 u"comparison works on a millimetre grid.")
    return notes
