import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "coordination_review_diff.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("coordination_review_diff", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _item(element_id, category, name="", type_name="", elevation=None, point=None, curve=None, arc=None):
    return {
        "id": element_id,
        "category": category,
        "name": name,
        "type_name": type_name,
        "elevation": elevation,
        "point": point,
        "curve": curve,
        "arc": arc,
    }


def _level(element_id, name, elevation):
    return _item(element_id, "Level", name=name, elevation=elevation)


def _grid(element_id, name, start, end):
    return _item(element_id, "Grid", name=name, curve=(start, end))


def _arc_grid(element_id, name, center, radius):
    return _item(element_id, "Grid", name=name, arc=(center, radius))


def _kinds(result):
    return [issue["kind"] for issue in result["issues"]]


class NameMatchingTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_exact_name_wins_over_looser_matches(self):
        hosts = [_level(1, "Level 1", 0.0)]
        links = [_level(11, "S-Level 1", 0.0), _level(12, "Level 1", 0.0)]
        pairs, unmatched_hosts, unmatched_links = self.module.match_by_name(hosts, links)
        self.assertEqual([(h["id"], l["id"]) for h, l in pairs], [(1, 12)])
        self.assertEqual(unmatched_hosts, [])
        self.assertEqual([l["id"] for l in unmatched_links], [11])

    def test_case_and_whitespace_are_ignored(self):
        pairs, _, _ = self.module.match_by_name([_level(1, "LEVEL 1", 0.0)], [_level(11, "Level1", 0.0)])
        self.assertEqual(len(pairs), 1)

    def test_prefix_or_suffix_relation_matches(self):
        pairs, _, _ = self.module.match_by_name(
            [_level(1, "S-Level 1", 0.0), _level(2, "Level 2 STR", 0.0)],
            [_level(11, "Level 1", 0.0), _level(12, "Level 2", 0.0)],
        )
        self.assertEqual(sorted((h["id"], l["id"]) for h, l in pairs), [(1, 11), (2, 12)])

    def test_each_link_item_is_claimed_once(self):
        pairs, unmatched_hosts, _ = self.module.match_by_name(
            [_level(1, "Level 1", 0.0), _level(2, "Level 1", 10.0)],
            [_level(11, "Level 1", 0.0)],
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual([h["id"] for h in unmatched_hosts], [2])


class LevelComparisonTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_identical_levels_are_unchanged(self):
        result = self.module.compare(
            [_level(1, "Level 1", 0.0), _level(2, "Level 2", 12.0)],
            [_level(11, "Level 1", 0.0), _level(12, "Level 2", 12.0)],
        )
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["ok_count"], 2)
        self.assertEqual(result["monitored_count"], 2)
        self.assertEqual(result["level_offset_ft"], 0.0)

    def test_moved_level_is_reported_with_signed_delta(self):
        result = self.module.compare(
            [_level(1, "Level 1", 0.0), _level(2, "Level 2", 12.0)],
            [_level(11, "Level 1", 0.0), _level(12, "Level 2", 12.5)],
        )
        self.assertEqual(_kinds(result), ["level_moved"])
        issue = result["issues"][0]
        self.assertEqual(issue["host_id"], 2)
        self.assertEqual(issue["link_id"], 12)
        self.assertAlmostEqual(issue["delta_ft"], 0.5)
        self.assertIn("Level 'Level 2' moved by +0.5000 ft", issue["message"])
        self.assertFalse(issue["estimated"])
        self.assertEqual(result["ok_count"], 1)

    def test_change_within_tolerance_is_unchanged(self):
        result = self.module.compare([_level(1, "L1", 0.0)], [_level(11, "L1", 0.0005)])
        self.assertEqual(result["issues"], [])

    def test_missing_level_names_nearest_link_level(self):
        result = self.module.compare(
            [_level(1, "Roof", 40.0)],
            [_level(11, "Parapet", 41.0), _level(12, "Level 1", 0.0)],
        )
        kinds = _kinds(result)
        self.assertIn("level_missing", kinds)
        missing = [i for i in result["issues"] if i["kind"] == "level_missing"][0]
        self.assertIn("Level 'Roof' was not found in the link", missing["message"])
        self.assertIn("Nearest link level is 'Parapet', 1.0000 ft away", missing["message"])

    def test_unmonitored_link_levels_are_informational(self):
        result = self.module.compare([_level(1, "L1", 0.0)], [_level(11, "L1", 0.0), _level(12, "L2", 10.0)])
        self.assertEqual(_kinds(result), ["new_in_link"])
        self.assertEqual(result["issues"][0]["link_id"], 12)
        self.assertIsNone(result["issues"][0]["host_id"])

    def test_renamed_level_is_reported_and_still_compared(self):
        result = self.module.compare([_level(1, "Level 1", 0.0)], [_level(11, "LEVEL 1", 2.0)])
        self.assertEqual(sorted(_kinds(result)), ["level_moved", "name_differs"])
        renamed = [i for i in result["issues"] if i["kind"] == "name_differs"][0]
        self.assertIn("is named 'LEVEL 1' in the link", renamed["message"])

    def test_common_offset_is_flagged_but_rows_are_kept(self):
        result = self.module.compare(
            [_level(1, "L1", 0.0), _level(2, "L2", 10.0), _level(3, "L3", 20.0)],
            [_level(11, "L1", 0.5), _level(12, "L2", 10.5), _level(13, "L3", 20.5)],
        )
        self.assertEqual(_kinds(result), ["level_moved"] * 3)
        self.assertAlmostEqual(result["level_offset_ft"], 0.5)

    def test_mixed_deltas_have_no_common_offset(self):
        result = self.module.compare(
            [_level(1, "L1", 0.0), _level(2, "L2", 10.0)],
            [_level(11, "L1", 0.5), _level(12, "L2", 10.0)],
        )
        self.assertEqual(result["level_offset_ft"], 0.0)


class GridComparisonTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_same_line_with_different_extents_is_unchanged(self):
        result = self.module.compare(
            [_grid(1, "A", (0, 0, 0), (0, 100, 0))],
            [_grid(11, "A", (0, -20, 0), (0, 150, 0))],
        )
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["ok_count"], 1)

    def test_parallel_shift_is_reported_with_distance(self):
        result = self.module.compare(
            [_grid(1, "A", (0, 0, 0), (0, 100, 0))],
            [_grid(11, "A", (2, 0, 0), (2, 100, 0))],
        )
        self.assertEqual(_kinds(result), ["grid_moved"])
        self.assertAlmostEqual(result["issues"][0]["delta_ft"], 2.0)
        self.assertIn("Grid 'A' moved by 2.0000 ft", result["issues"][0]["message"])

    def test_rotated_grid_is_reported(self):
        result = self.module.compare(
            [_grid(1, "A", (0, 0, 0), (0, 100, 0))],
            [_grid(11, "A", (0, 0, 0), (10, 100, 0))],
        )
        self.assertEqual(_kinds(result), ["grid_moved"])
        self.assertGreater(result["issues"][0]["delta_ft"], 1.0)

    def test_arc_grid_radius_change_is_reported(self):
        result = self.module.compare(
            [_arc_grid(1, "R1", (0, 0, 0), 50.0)],
            [_arc_grid(11, "R1", (0, 0, 0), 52.0)],
        )
        self.assertEqual(_kinds(result), ["grid_moved"])
        self.assertAlmostEqual(result["issues"][0]["delta_ft"], 2.0)

    def test_shape_change_between_line_and_arc_is_reported(self):
        result = self.module.compare(
            [_grid(1, "A", (0, 0, 0), (0, 100, 0))],
            [_arc_grid(11, "A", (0, 0, 0), 50.0)],
        )
        self.assertEqual(_kinds(result), ["grid_moved"])
        self.assertIsNone(result["issues"][0]["delta_ft"])
        self.assertIn("changed shape", result["issues"][0]["message"])

    def test_missing_and_unmonitored_grids(self):
        result = self.module.compare(
            [_grid(1, "A", (0, 0, 0), (0, 100, 0))],
            [_grid(11, "B", (5, 0, 0), (5, 100, 0))],
        )
        self.assertEqual(_kinds(result), ["grid_missing", "new_in_link"])


class ElementComparisonTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_matching_column_is_unchanged(self):
        result = self.module.compare(
            [_item(1, "Column", type_name="UC 305", point=(10, 10, 0))],
            [_item(11, "Column", type_name="UC 305", point=(10, 10, 0))],
        )
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["ok_count"], 1)

    def test_type_change_is_reported(self):
        result = self.module.compare(
            [_item(1, "Column", type_name="UC 305", point=(10, 10, 0))],
            [_item(11, "Column", type_name="UC 356", point=(10, 10, 0))],
        )
        self.assertEqual(_kinds(result), ["type_changed"])
        self.assertTrue(result["issues"][0]["estimated"])
        self.assertIn("has type 'UC 356' in the link", result["issues"][0]["message"])

    def test_moved_column_is_reported_with_distance(self):
        result = self.module.compare(
            [_item(1, "Column", type_name="UC 305", point=(10, 10, 0))],
            [_item(11, "Column", type_name="UC 305", point=(13, 14, 0))],
        )
        self.assertEqual(_kinds(result), ["element_moved"])
        self.assertAlmostEqual(result["issues"][0]["delta_ft"], 5.0)
        self.assertIn("Column 'UC 305' (id 1) moved by 5.0000 ft", result["issues"][0]["message"])

    def test_moved_and_retyped_column_mentions_both(self):
        result = self.module.compare(
            [_item(1, "Column", type_name="UC 305", point=(10, 10, 0))],
            [_item(11, "Column", type_name="UC 356", point=(13, 14, 0))],
        )
        self.assertEqual(_kinds(result), ["element_moved"])
        self.assertIn("Its type is 'UC 356' there", result["issues"][0]["message"])

    def test_nothing_within_radius_means_deleted(self):
        result = self.module.compare(
            [_item(1, "Column", type_name="UC 305", point=(0, 0, 0))],
            [_item(11, "Column", type_name="UC 305", point=(500, 500, 0))],
        )
        self.assertEqual(_kinds(result), ["element_missing"])
        self.assertIn("no counterpart within 50.0000 ft", result["issues"][0]["message"])

    def test_each_link_element_is_claimed_once(self):
        result = self.module.compare(
            [
                _item(1, "Column", type_name="C", point=(0, 0, 0)),
                _item(2, "Column", type_name="C", point=(1, 0, 0)),
            ],
            [
                _item(11, "Column", type_name="C", point=(0, 0, 0)),
                _item(12, "Column", type_name="C", point=(4, 0, 0)),
            ],
        )
        self.assertEqual(_kinds(result), ["element_moved"])
        self.assertEqual(result["issues"][0]["host_id"], 2)
        self.assertEqual(result["issues"][0]["link_id"], 12)
        self.assertAlmostEqual(result["issues"][0]["delta_ft"], 3.0)

    def test_categories_never_cross_match(self):
        result = self.module.compare(
            [_item(1, "Wall", type_name="W", curve=((0, 0, 0), (10, 0, 0)))],
            [_item(11, "Column", type_name="W", point=(5, 0, 0))],
        )
        self.assertEqual(_kinds(result), ["element_missing"])

    def test_wall_with_flipped_endpoints_is_unchanged(self):
        result = self.module.compare(
            [_item(1, "Wall", type_name="W", curve=((0, 0, 0), (10, 0, 0)))],
            [_item(11, "Wall", type_name="W", curve=((10, 0, 0), (0, 0, 0)))],
        )
        self.assertEqual(result["issues"], [])

    def test_wall_moved_is_reported(self):
        result = self.module.compare(
            [_item(1, "Wall", type_name="W", curve=((0, 0, 0), (10, 0, 0)))],
            [_item(11, "Wall", type_name="W", curve=((0, 1, 0), (10, 1, 0)))],
        )
        self.assertEqual(_kinds(result), ["element_moved"])
        self.assertAlmostEqual(result["issues"][0]["delta_ft"], 1.0)

    def test_floor_matched_by_bounding_box_centre(self):
        result = self.module.compare(
            [_item(1, "Floor", type_name="F", point=(50, 50, 10))],
            [_item(11, "Floor", type_name="F", point=(50, 50, 10))],
        )
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["ok_count"], 1)

    def test_far_apart_link_elements_across_cells_are_still_found(self):
        result = self.module.compare(
            [_item(1, "MEP", type_name="AHU", point=(49.0, 0, 0))],
            [_item(11, "MEP", type_name="AHU", point=(51.0, 0, 0))],
        )
        self.assertEqual(_kinds(result), ["element_moved"])
        self.assertAlmostEqual(result["issues"][0]["delta_ft"], 2.0)

    def test_link_elements_nothing_monitors_are_not_reported(self):
        result = self.module.compare(
            [_item(1, "Wall", type_name="W", curve=((0, 0, 0), (10, 0, 0)))],
            [
                _item(11, "Wall", type_name="W", curve=((0, 0, 0), (10, 0, 0))),
                _item(12, "Wall", type_name="W", curve=((0, 20, 0), (10, 20, 0))),
            ],
        )
        self.assertEqual(result["issues"], [])


class ReportShapeTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_empty_inputs(self):
        result = self.module.compare([], [])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["ok_count"], 0)
        self.assertEqual(result["monitored_count"], 0)
        self.assertEqual(result["estimated_count"], 0)

    def test_issues_are_ordered_by_kind_then_name(self):
        result = self.module.compare(
            [
                _item(1, "Column", type_name="C", point=(0, 0, 0)),
                _grid(2, "B", (0, 0, 0), (0, 10, 0)),
                _grid(3, "A", (5, 0, 0), (5, 10, 0)),
                _level(4, "L1", 0.0),
            ],
            [
                _item(11, "Column", type_name="C", point=(3, 4, 0)),
                _grid(12, "B", (1, 0, 0), (1, 10, 0)),
                _grid(13, "A", (7, 0, 0), (7, 10, 0)),
                _level(14, "L1", 1.0),
            ],
        )
        self.assertEqual(_kinds(result), ["level_moved", "grid_moved", "grid_moved", "element_moved"])
        self.assertEqual([i["host_name"] for i in result["issues"][1:3]], ["A", "B"])
        self.assertEqual(result["estimated_count"], 1)

    def test_custom_length_formatter_is_used(self):
        result = self.module.compare(
            [_level(1, "L1", 0.0)],
            [_level(11, "L1", 1.0)],
            format_length=lambda value: "{0:.0f} mm".format(value * 304.8),
        )
        self.assertIn("moved by +305 mm", result["issues"][0]["message"])

    def test_group_issues_keeps_display_order_and_titles(self):
        result = self.module.compare(
            [_item(1, "Column", type_name="C", point=(0, 0, 0)), _level(2, "L1", 0.0)],
            [_item(11, "Column", type_name="D", point=(0, 0, 0)), _level(12, "L1", 2.0)],
        )
        groups = self.module.group_issues(result["issues"])
        self.assertEqual([g["kind"] for g in groups], ["level_moved", "type_changed"])
        self.assertEqual([g["title"] for g in groups], ["Level moved", "Type changed"])
        self.assertEqual([g["estimated"] for g in groups], [False, True])
        self.assertEqual(len(groups[0]["issues"]), 1)

    def test_group_issues_empty(self):
        self.assertEqual(self.module.group_issues([]), [])


if __name__ == "__main__":
    unittest.main()
