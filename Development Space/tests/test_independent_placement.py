import math
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "lib"))
from easybim import independent_placement as p

class Frames(unittest.TestCase):
    def test_link_rotation_and_translation(self):
        t = p.frame((100,0,0), (0,1,0), (-1,0,0), (0,0,1))
        self.assertEqual([100,10,0], p.transform_point(t, [10,0,0]))
        result = p.compose(t, p.frame((10,0,0)))
        self.assertEqual([100,10,0], result["origin"])

    def test_relative_offset_rotates_without_accumulating(self):
        saved = p.relative(p.frame((10,0,0)), p.frame((12,0,0)))
        moved = p.frame((20,0,0), (0,1,0), (-1,0,0), (0,0,1))
        expected = p.compose(moved, saved)
        self.assertEqual([20,2,0], expected["origin"])
        self.assertEqual([0,1,0], expected["x"])

    def test_reflection_is_preserved(self):
        mirror = p.frame((0,0,0), (-1,0,0), (0,1,0), (0,0,1))
        point = p.transform_point(p.inverse(mirror), [-2,3,4])
        self.assertEqual([2,3,4], point)
        self.assertLess(p.determinant(mirror), 0)

    def test_bad_frames_are_refused_not_identity(self):
        for value in [p.frame((float("nan"),0,0)), p.frame((0,0,0), (0,0,0))]:
            with self.assertRaises(ValueError): p.validate_frame(value)

    def test_pose_verification_catches_rotation_and_mirror(self):
        self.assertTrue(p.placement_matches(p.frame(), p.frame((0.001,0,0))))
        self.assertFalse(p.placement_matches(p.frame(), p.frame((0.1,0,0))))
        self.assertFalse(p.placement_matches(p.frame(), p.frame((0,0,0),(-1,0,0))))
        self.assertFalse(p.placement_matches(p.frame(), p.frame((0,0,0),(0,1,0),(-1,0,0))))

    def test_existing_orientation_can_remain_fixed(self):
        reference = p.frame((10,0,0),(0,1,0),(-1,0,0))
        desired = p.desired_frame(reference, [2,3,4], p.frame(), False)
        self.assertEqual([7,2,4], desired["origin"])
        self.assertEqual([1,0,0], desired["x"])

class ParameterPlans(unittest.TestCase):
    def param(self, **kw):
        v = dict(key="shared:123", name="Length", spec="length", storage="Double",
                 value=2.5, writable=True, placement=False)
        v.update(kw)
        return v
    def test_same_guid_ignores_localized_name(self):
        assignments, issues = p.parameter_plan([self.param()], [self.param(name="Longueur",value=1)])
        self.assertEqual(2.5, assignments[0]["source"]["value"])
        self.assertEqual([], issues)
    def test_ambiguous_and_read_only_are_not_written(self):
        src = self.param()
        assignments, issues = p.parameter_plan([src], [self.param(),self.param()])
        self.assertEqual([], assignments)
        self.assertIn("ambiguous", issues[0]["reason"])
        assignments, issues = p.parameter_plan([src], [self.param(writable=False)])
        self.assertEqual([], assignments)
        self.assertIn("read-only", issues[0]["reason"])
    def test_placement_values_are_calculated_separately(self):
        assignments, issues = p.parameter_plan([self.param(placement=True)], [self.param()])
        self.assertEqual([], assignments)
        self.assertIn("placement", issues[0]["reason"])
    def test_matching_names_do_not_override_different_specs(self):
        assignments, issues = p.parameter_plan([self.param()], [self.param(spec="voltage")])
        self.assertEqual([], assignments)
        self.assertIn("incompatible", issues[0]["reason"])
    def test_uncertified_hosted_family_is_refused(self):
        self.assertEqual("", p.support_reason("OneLevelBased", False, False, False))
        for placement in ("WorkPlaneBased","OneLevelBasedHosted","Adaptive","CurveBased"):
            self.assertTrue(p.support_reason(placement,False,False,False))
        self.assertTrue(p.support_reason("OneLevelBased",True,False,False))
