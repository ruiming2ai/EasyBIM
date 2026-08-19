"""The projection View Align and Linked Sheets Transfer both place viewports with.

A sign error in here is invisible until somebody overlays two printed sheets,
so the cases are hand-computed rather than taken from the implementation.
"""

import importlib.util
import math
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "sheet_geometry.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("sheet_geometry",
                                                  str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


geometry = _load_module()


class _Point(object):
    """Stands in for a Revit ``XYZ``."""

    def __init__(self, x, y, z=0.0):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)


class _Bound(object):
    def __init__(self, u, v):
        self.U = float(u)
        self.V = float(v)


class _Outline(object):
    def __init__(self, min_uv, max_uv):
        self.Min = _Bound(*min_uv)
        self.Max = _Bound(*max_uv)


class _View(object):
    def __init__(self, origin, right, up, scale, outline):
        self.Origin = origin
        self.RightDirection = right
        self.UpDirection = up
        self.Scale = scale
        self.Outline = outline


class _Viewport(object):
    def __init__(self, center, rotation=u"None"):
        self._center = center
        self.Rotation = rotation

    def GetBoxCenter(self):
        return self._center


def _plan_view(scale=100, outline=((-1.0, -1.0), (1.0, 1.0)), origin=(0, 0, 0)):
    return _View(_Point(*origin), _Point(1, 0, 0), _Point(0, 1, 0), scale,
                 _Outline(*outline))


class ToXyzTests(unittest.TestCase):
    def test_reads_a_revit_style_point(self):
        self.assertEqual((1.0, 2.0, 3.0), geometry.to_xyz(_Point(1, 2, 3)))

    def test_reads_a_tuple(self):
        self.assertEqual((1.0, 2.0, 3.0), geometry.to_xyz((1, 2, 3)))

    def test_two_component_sequence_gets_a_zero_z(self):
        self.assertEqual((1.0, 2.0, 0.0), geometry.to_xyz((1, 2)))

    def test_none_stays_none(self):
        self.assertIsNone(geometry.to_xyz(None))


class RotationTests(unittest.TestCase):
    def test_no_rotation(self):
        self.assertEqual(0.0, geometry.rotation_angle_from_text(u"None"))

    def test_clockwise_is_negative(self):
        self.assertAlmostEqual(-math.pi / 2,
                               geometry.rotation_angle_from_text(u"Clockwise"))

    def test_counterclockwise_is_positive_and_not_read_as_clockwise(self):
        # "Counterclockwise" contains "clockwise"; the substring order matters.
        self.assertAlmostEqual(
            math.pi / 2,
            geometry.rotation_angle_from_text(u"Counterclockwise"))

    def test_unknown_value_degrades_to_zero(self):
        self.assertEqual(0.0, geometry.rotation_angle_from_text(u"Whatever"))

    def test_clockwise_maps_dx_dy_to_dy_minus_dx(self):
        x, y = geometry.rotate_xy(1.0, 0.0, -math.pi / 2)
        self.assertAlmostEqual(0.0, x)
        self.assertAlmostEqual(-1.0, y)


class ProjectionTests(unittest.TestCase):
    def test_the_outline_centre_lands_on_the_box_centre(self):
        projection, reason = geometry.build_projection(
            _plan_view(), _Viewport(_Point(2.0, 3.0, 0.0)))
        self.assertEqual(u"", reason)
        # Outline centre is (0, 0) in paper feet, i.e. the model origin.
        point = projection.project((0.0, 0.0, 0.0))
        self.assertAlmostEqual(2.0, point[0])
        self.assertAlmostEqual(3.0, point[1])

    def test_a_model_offset_is_divided_by_the_scale(self):
        projection, _ = geometry.build_projection(
            _plan_view(scale=100), _Viewport(_Point(0.0, 0.0, 0.0)))
        # 100 ft east at 1:100 prints 1 ft east of the box centre.
        point = projection.project((100.0, 0.0, 0.0))
        self.assertAlmostEqual(1.0, point[0])
        self.assertAlmostEqual(0.0, point[1])

    def test_the_same_model_point_prints_further_out_at_a_larger_scale(self):
        far = geometry.build_projection(
            _plan_view(scale=50), _Viewport(_Point(0.0, 0.0, 0.0)))[0]
        near = geometry.build_projection(
            _plan_view(scale=100), _Viewport(_Point(0.0, 0.0, 0.0)))[0]
        self.assertAlmostEqual(2.0, far.project((100.0, 0.0, 0.0))[0])
        self.assertAlmostEqual(1.0, near.project((100.0, 0.0, 0.0))[0])

    def test_an_offset_outline_shifts_the_result(self):
        # Outline centred on (1, 0): the model origin now prints 1 ft left.
        projection, _ = geometry.build_projection(
            _plan_view(outline=((0.0, -1.0), (2.0, 1.0))),
            _Viewport(_Point(0.0, 0.0, 0.0)))
        point = projection.project((0.0, 0.0, 0.0))
        self.assertAlmostEqual(-1.0, point[0])

    def test_a_rotated_view_rotates_the_offset(self):
        # Right direction is +Y, so a model offset along +X prints downwards.
        view = _View(_Point(0, 0, 0), _Point(0, 1, 0), _Point(-1, 0, 0), 100,
                     _Outline((-1.0, -1.0), (1.0, 1.0)))
        projection, _ = geometry.build_projection(
            view, _Viewport(_Point(0.0, 0.0, 0.0)))
        point = projection.project((100.0, 0.0, 0.0))
        self.assertAlmostEqual(0.0, point[0])
        self.assertAlmostEqual(-1.0, point[1])

    def test_a_clockwise_viewport_rotates_the_printed_offset(self):
        projection, _ = geometry.build_projection(
            _plan_view(), _Viewport(_Point(0.0, 0.0, 0.0), u"Clockwise"))
        point = projection.project((100.0, 0.0, 0.0))
        self.assertAlmostEqual(0.0, point[0])
        self.assertAlmostEqual(-1.0, point[1])

    def test_the_depth_component_is_dropped(self):
        projection, _ = geometry.build_projection(
            _plan_view(), _Viewport(_Point(0.0, 0.0, 0.0)))
        flat = projection.project((100.0, 0.0, 0.0))
        deep = projection.project((100.0, 0.0, 500.0))
        self.assertAlmostEqual(flat[0], deep[0])
        self.assertAlmostEqual(flat[1], deep[1])


class BoxCenterForTests(unittest.TestCase):
    def test_the_requested_point_lands_where_it_was_asked_to(self):
        projection, _ = geometry.build_projection(
            _plan_view(), _Viewport(_Point(0.0, 0.0, 0.0)))
        model_point = (250.0, -80.0, 0.0)
        target = (1.75, 0.5, 0.0)

        moved = projection.box_center_for(model_point, target)
        after = geometry.SheetProjection(
            projection.origin, projection.right, projection.up,
            projection.scale, projection.outline_center, moved,
            projection.rotation)
        landed = after.project(model_point)
        self.assertAlmostEqual(target[0], landed[0])
        self.assertAlmostEqual(target[1], landed[1])

    def test_it_survives_a_rotated_viewport(self):
        projection, _ = geometry.build_projection(
            _plan_view(), _Viewport(_Point(4.0, 4.0, 0.0), u"Counterclockwise"))
        model_point = (33.0, 71.0, 0.0)
        target = (0.25, -0.75, 0.0)

        moved = projection.box_center_for(model_point, target)
        after = geometry.SheetProjection(
            projection.origin, projection.right, projection.up,
            projection.scale, projection.outline_center, moved,
            projection.rotation)
        landed = after.project(model_point)
        self.assertAlmostEqual(target[0], landed[0])
        self.assertAlmostEqual(target[1], landed[1])

    def test_z_is_carried_through_untouched(self):
        projection, _ = geometry.build_projection(
            _plan_view(), _Viewport(_Point(0.0, 0.0, 7.0)))
        moved = projection.box_center_for((10.0, 10.0, 0.0), (1.0, 1.0, 0.0))
        self.assertAlmostEqual(7.0, moved[2])


def _rotate_z(point, degrees):
    angle = math.radians(degrees)
    x, y = geometry.rotate_xy(point[0], point[1], angle)
    return (x, y, point[2])


def _link_transform(point, degrees, offset):
    rotated = _rotate_z(point, degrees)
    return (rotated[0] + offset[0], rotated[1] + offset[1],
            rotated[2] + offset[2])


class LinkedSheetOverlayTests(unittest.TestCase):
    """The promise Linked Sheets Transfer makes, simulated end to end.

    The link is rotated 30 degrees and moved a long way from the origin, which
    is the case that defeats copying a viewport's position off the page.
    """

    DEGREES = 30.0
    OFFSET = (517.25, -302.5, 11.0)

    def _reference(self):
        # The linked viewport, in the linked model's own coordinates.
        return geometry.build_projection(
            _plan_view(scale=100, outline=((-1.2, -0.8), (1.2, 0.8))),
            _Viewport(_Point(1.5, 2.25, 0.0)))[0]

    def _host(self, scale=100, box_center=(0.0, 0.0, 0.0)):
        # The new host view: same crop extents, but its frame carries the
        # link's rotation, which is what composing the crop transform does.
        right = _Point(*_rotate_z((1.0, 0.0, 0.0), self.DEGREES))
        up = _Point(*_rotate_z((0.0, 1.0, 0.0), self.DEGREES))
        origin = _Point(*_link_transform((0.0, 0.0, 0.0), self.DEGREES,
                                         self.OFFSET))
        view = _View(origin, right, up, scale,
                     _Outline((-1.2, -0.8), (1.2, 0.8)))
        return geometry.build_projection(view, _Viewport(_Point(*box_center)))[0]

    def _place(self, host, anchor_link, reference):
        anchor_host = _link_transform(anchor_link, self.DEGREES, self.OFFSET)
        target = reference.project(anchor_link)
        moved = host.box_center_for(anchor_host, target)
        return geometry.SheetProjection(
            host.origin, host.right, host.up, host.scale,
            host.outline_center, moved, host.rotation)

    def test_a_second_point_lands_on_the_same_spot_on_the_page(self):
        reference = self._reference()
        anchor = (10.0, 20.0, 0.0)
        corner = (-140.0, 95.0, 0.0)

        placed = self._place(self._host(), anchor, reference)
        expected = reference.project(corner)
        actual = placed.project(_link_transform(corner, self.DEGREES,
                                                self.OFFSET))
        self.assertLess(geometry.deviation_mm(expected, actual), 0.001)

    def test_the_anchor_itself_lands_exactly(self):
        reference = self._reference()
        anchor = (10.0, 20.0, 0.0)
        placed = self._place(self._host(), anchor, reference)
        expected = reference.project(anchor)
        actual = placed.project(_link_transform(anchor, self.DEGREES,
                                                self.OFFSET))
        self.assertLess(geometry.deviation_mm(expected, actual), 0.001)

    def test_a_wrong_scale_matches_the_anchor_and_misses_the_corner(self):
        # This is exactly why one anchor point is not enough, and why every
        # placement is checked at a second point out at the crop corner.
        reference = self._reference()
        anchor = (10.0, 20.0, 0.0)
        corner = (-140.0, 95.0, 0.0)

        placed = self._place(self._host(scale=50), anchor, reference)
        anchor_miss = geometry.deviation_mm(
            reference.project(anchor),
            placed.project(_link_transform(anchor, self.DEGREES, self.OFFSET)))
        corner_miss = geometry.deviation_mm(
            reference.project(corner),
            placed.project(_link_transform(corner, self.DEGREES, self.OFFSET)))

        self.assertLess(anchor_miss, 0.001)
        self.assertGreater(corner_miss, 1.0)

    def test_a_wrong_rotation_also_only_shows_up_at_the_corner(self):
        reference = self._reference()
        anchor = (10.0, 20.0, 0.0)
        corner = (-140.0, 95.0, 0.0)

        # A host view built without the link's rotation: axis-aligned frame.
        view = _View(_Point(*_link_transform((0.0, 0.0, 0.0), self.DEGREES,
                                             self.OFFSET)),
                     _Point(1, 0, 0), _Point(0, 1, 0), 100,
                     _Outline((-1.2, -0.8), (1.2, 0.8)))
        unrotated = geometry.build_projection(
            view, _Viewport(_Point(0.0, 0.0, 0.0)))[0]
        placed = self._place(unrotated, anchor, reference)

        anchor_miss = geometry.deviation_mm(
            reference.project(anchor),
            placed.project(_link_transform(anchor, self.DEGREES, self.OFFSET)))
        corner_miss = geometry.deviation_mm(
            reference.project(corner),
            placed.project(_link_transform(corner, self.DEGREES, self.OFFSET)))

        self.assertLess(anchor_miss, 0.001)
        self.assertGreater(corner_miss, 1.0)


class BuildProjectionGuardTests(unittest.TestCase):
    def test_a_missing_view_is_a_reason_not_a_crash(self):
        projection, reason = geometry.build_projection(None, _Viewport(
            _Point(0, 0, 0)))
        self.assertIsNone(projection)
        self.assertIn(u"View", reason)

    def test_a_missing_viewport_is_a_reason(self):
        projection, reason = geometry.build_projection(_plan_view(), None)
        self.assertIsNone(projection)
        self.assertIn(u"Viewport", reason)

    def test_a_zero_scale_is_refused_rather_than_treated_as_one(self):
        # View Align silently substituted 1, which produced a viewport that
        # was placed confidently and wrongly.
        projection, reason = geometry.build_projection(
            _plan_view(scale=0), _Viewport(_Point(0, 0, 0)))
        self.assertIsNone(projection)
        self.assertIn(u"scale", reason.lower())

    def test_a_degenerate_direction_is_refused(self):
        view = _View(_Point(0, 0, 0), _Point(0, 0, 0), _Point(0, 1, 0), 100,
                     _Outline((-1, -1), (1, 1)))
        projection, reason = geometry.build_projection(
            view, _Viewport(_Point(0, 0, 0)))
        self.assertIsNone(projection)
        self.assertIn(u"invalid", reason.lower())


class DeviationTests(unittest.TestCase):
    def test_a_foot_is_three_hundred_and_four_point_eight_millimetres(self):
        self.assertAlmostEqual(304.8, geometry.feet_to_mm(1.0))

    def test_deviation_is_planar_and_in_millimetres(self):
        value = geometry.deviation_mm((0.0, 0.0, 0.0), (3.0, 4.0, 99.0))
        self.assertAlmostEqual(5.0 * 304.8, value)

    def test_a_missing_point_gives_no_deviation(self):
        self.assertIsNone(geometry.deviation_mm(None, (0.0, 0.0, 0.0)))


if __name__ == "__main__":
    unittest.main()
