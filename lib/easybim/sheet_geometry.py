# -*- coding: utf-8 -*-
"""Where a model point lands on a sheet, given the viewport it is seen through.

This is the one piece of maths behind both `View Align` (move an existing
viewport until a model point matches a reference) and `Linked Sheets Copy`
(place a brand new viewport so a model point matches a linked sheet).  Keeping
two copies of it would guarantee that "align" and "copy and align" eventually
disagree, which is the exact drift `AGENTS.md` asks to avoid.

Nothing here imports Revit.  A :class:`SheetProjection` is built from plain
floats by the Revit-facing caller, so the whole module is unit-testable off
Revit - which matters, because a sign error in here is invisible until
somebody overlays two printed sheets.

The maths, with ``P`` the model point, ``O`` the view origin, ``r``/``u`` the
view's right/up directions and ``s`` its scale::

    (pu, pv)    = ((P - O)*r / s, (P - O)*u / s)      # paper feet
    (cu, cv)    = centre of View.Outline              # paper feet
    (dx, dy)    = R(theta) * (pu - cu, pv - cv)       # theta = viewport rotation
    sheet_point = Viewport.GetBoxCenter() + (dx, dy)

Two assumptions are load-bearing, and both are Revit's, not ours:

1. ``View.Outline`` is expressed as scaled offsets from ``View.Origin`` along
   ``RightDirection``/``UpDirection``, so ``pu``/``pv`` are directly comparable
   to its ``U``/``V`` bounds.
2. ``Viewport.GetBoxCenter()`` is the sheet position of the centre of
   ``View.Outline``.

Because ``Outline`` moves whenever the crop, the annotation crop or the view
scale changes, a :class:`SheetProjection` is only valid for as long as the view
is untouched.  Build it *after* every configuration write, never before.
"""

from __future__ import print_function

import math


EPS = 1e-9

# Revit stores lengths in decimal feet.
MM_PER_FOOT = 304.8


def to_xyz(value):
    """Read a point/vector as a plain ``(x, y, z)`` tuple of floats.

    Accepts a Revit ``XYZ`` (or anything with ``X``/``Y``/``Z``), a sequence,
    or an existing tuple, so tests can pass tuples where Revit passes ``XYZ``.
    Returns ``None`` when the value cannot be read at all.
    """
    if value is None:
        return None
    for attrs in (("X", "Y", "Z"),):
        try:
            return (float(getattr(value, attrs[0])),
                    float(getattr(value, attrs[1])),
                    float(getattr(value, attrs[2])))
        except Exception:
            pass
    try:
        parts = list(value)
    except Exception:
        return None
    if len(parts) < 2:
        return None
    try:
        return (float(parts[0]), float(parts[1]),
                float(parts[2]) if len(parts) > 2 else 0.0)
    except Exception:
        return None


def subtract(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def length(a):
    return math.sqrt(dot(a, a))


def rotate_xy(x_val, y_val, angle):
    """Rotate a 2D vector counter-clockwise by ``angle`` radians."""
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    return (x_val * cos_a - y_val * sin_a,
            x_val * sin_a + y_val * cos_a)


def rotation_angle_from_text(rotation_text):
    """Turn a ``ViewportRotation`` value's text into radians.

    Matched on the stringified enum rather than by comparing enum members, so
    a Revit release that renames or adds a member degrades to 0 instead of
    throwing.  ``Clockwise`` is negative: a clockwise viewport rotation maps
    the view's ``(dx, dy)`` to ``(dy, -dx)`` on the sheet.
    """
    text = (rotation_text or u"").lower()
    if "clockwise" in text and "counter" not in text:
        return -math.pi * 0.5
    if "counter" in text:
        return math.pi * 0.5
    if "upside" in text or "180" in text:
        return math.pi
    return 0.0


def feet_to_mm(value):
    return float(value) * MM_PER_FOOT


class SheetProjection(object):
    """A frozen snapshot of one viewport, sufficient to place a model point.

    ``origin``, ``right``, ``up`` and ``box_center`` are ``(x, y, z)`` tuples;
    ``outline_center`` is ``(u, v)`` in paper feet; ``scale`` is the view's
    integer scale denominator (50 for 1:50); ``rotation`` is in radians.
    """

    def __init__(self, origin, right, up, scale, outline_center, box_center,
                 rotation=0.0):
        self.origin = origin
        self.right = right
        self.up = up
        self.scale = float(scale)
        self.outline_center = (float(outline_center[0]),
                               float(outline_center[1]))
        self.box_center = box_center
        self.rotation = float(rotation)

    def is_usable(self):
        if None in (self.origin, self.right, self.up, self.box_center):
            return False
        if abs(self.scale) < EPS:
            return False
        return length(self.right) >= EPS and length(self.up) >= EPS

    def paper_uv(self, model_point):
        """The model point in the view's own paper coordinates (feet)."""
        offset = subtract(model_point, self.origin)
        return (dot(offset, self.right) / self.scale,
                dot(offset, self.up) / self.scale)

    def project(self, model_point):
        """Sheet coordinates (feet) at which ``model_point`` is drawn."""
        paper_u, paper_v = self.paper_uv(model_point)
        local_dx = paper_u - self.outline_center[0]
        local_dy = paper_v - self.outline_center[1]
        rot_dx, rot_dy = rotate_xy(local_dx, local_dy, self.rotation)
        return (self.box_center[0] + rot_dx,
                self.box_center[1] + rot_dy,
                self.box_center[2])

    def box_center_for(self, model_point, sheet_point):
        """The box centre that would draw ``model_point`` at ``sheet_point``.

        This is the whole placement step: project where the point currently
        lands, and shift the box by the difference.  Z is carried through from
        the current box centre - a sheet is flat and Revit keeps its own value
        there.
        """
        current = self.project(model_point)
        return (self.box_center[0] + (sheet_point[0] - current[0]),
                self.box_center[1] + (sheet_point[1] - current[1]),
                self.box_center[2])


def build_projection(view, viewport, rotation_text=None):
    """Snapshot a live Revit view/viewport pair, or ``(None, reason)``.

    The caller passes the Revit objects; everything is read defensively
    because each of these properties throws on some view types, and a
    diagnosable reason is worth more than a traceback.
    """
    if view is None:
        return None, u"View is missing."
    if viewport is None:
        return None, u"Viewport is missing."

    try:
        origin = to_xyz(view.Origin)
        right = to_xyz(view.RightDirection)
        up = to_xyz(view.UpDirection)
    except Exception:
        return None, u"View does not expose orientation vectors."
    if None in (origin, right, up):
        return None, u"View does not expose orientation vectors."
    if length(right) < EPS or length(up) < EPS:
        return None, u"View orientation vectors are invalid."

    try:
        scale = float(view.Scale)
    except Exception:
        return None, u"View scale is unavailable."
    if abs(scale) < EPS:
        return None, u"View scale is zero."

    try:
        outline = view.Outline
        outline_center = ((float(outline.Min.U) + float(outline.Max.U)) * 0.5,
                          (float(outline.Min.V) + float(outline.Max.V)) * 0.5)
    except Exception:
        return None, u"View outline is unavailable."

    try:
        box_center = to_xyz(viewport.GetBoxCenter())
    except Exception:
        box_center = None
    if box_center is None:
        return None, u"Viewport box center is unavailable."

    if rotation_text is None:
        try:
            rotation_text = u"{0}".format(viewport.Rotation)
        except Exception:
            rotation_text = u""

    projection = SheetProjection(
        origin, right, up, scale, outline_center, box_center,
        rotation_angle_from_text(rotation_text))
    if not projection.is_usable():
        return None, u"View orientation vectors are invalid."
    return projection, u""


def deviation_mm(point_a, point_b):
    """Planar distance between two sheet points, in millimetres."""
    if point_a is None or point_b is None:
        return None
    dx = point_a[0] - point_b[0]
    dy = point_a[1] - point_b[1]
    return feet_to_mm(math.sqrt(dx * dx + dy * dy))
