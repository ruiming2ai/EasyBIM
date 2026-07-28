import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "lib"
    / "easybim"
    / "coordination_review_model.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("coordination_review_model", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_report():
    return {
        "doc_title": "Coordination_Model",
        "link_map": {
            10: {"name": "ARCH_Model"},
            20: {"name": "STR_Model"},
            30: {"name": "MEP_Model"},
        },
        "grouped": {
            10: {
                "Missing coordination element": {
                    "count": 2,
                    "instance_ids": set([1001, 1002, 1003]),
                },
                "Room is separated from the highlighted room": {
                    "count": 1,
                    "instance_ids": set([1010]),
                },
            },
            20: {
                "Missing coordination element": {
                    "count": 4,
                    "instance_ids": set([2001, 2002]),
                },
            },
        },
        "link_totals": {
            10: 3,
            20: 4,
            30: 0,
        },
        "total_matching_warnings": 7,
        "total_link_assignments": 7,
    }


class CoordinationReviewModelTests(unittest.TestCase):
    def test_build_view_model_hides_clean_links_by_default(self):
        module = _load_module()

        model = module.build_coordination_review_view_model(_make_report())

        self.assertFalse(model["is_empty"])
        self.assertEqual([item["name"] for item in model["links"]], ["STR_Model", "ARCH_Model"])

    def test_build_view_model_sorts_issue_groups_by_count_descending(self):
        module = _load_module()

        model = module.build_coordination_review_view_model(_make_report())

        self.assertEqual(
            [item["text"] for item in model["links"][1]["issues"]],
            [
                "Missing coordination element",
                "Room is separated from the highlighted room",
            ],
        )

    def test_build_view_model_marks_links_and_issues_expanded_by_default(self):
        module = _load_module()

        model = module.build_coordination_review_view_model(_make_report())

        self.assertTrue(all(item["is_expanded"] for item in model["links"]))
        self.assertTrue(
            all(issue["is_expanded"] for item in model["links"] for issue in item["issues"])
        )

    def test_build_view_model_derives_distinct_issue_filter_options(self):
        module = _load_module()

        model = module.build_coordination_review_view_model(_make_report())

        self.assertEqual(
            [item["label"] for item in model["issue_filter_options"]],
            [
                module.ALL_ISSUES_FILTER_LABEL,
                "Missing coordination element",
                "Room is separated from the highlighted room",
            ],
        )

    def test_build_view_model_applies_link_filter(self):
        module = _load_module()

        model = module.build_coordination_review_view_model(
            _make_report(),
            selected_link_value="10",
        )

        self.assertEqual([item["name"] for item in model["links"]], ["ARCH_Model"])

    def test_build_view_model_applies_issue_type_filter(self):
        module = _load_module()

        model = module.build_coordination_review_view_model(
            _make_report(),
            selected_issue_value="Missing coordination element",
        )

        self.assertEqual([item["name"] for item in model["links"]], ["STR_Model", "ARCH_Model"])
        self.assertEqual(
            [item["text"] for item in model["links"][1]["issues"]],
            ["Missing coordination element"],
        )

    def test_build_view_model_respects_instance_cap_and_overflow_label(self):
        module = _load_module()

        model = module.build_coordination_review_view_model(_make_report(), instance_cap=2)
        issue = model["links"][1]["issues"][0]

        self.assertEqual([row["instance_id"] for row in issue["instance_rows"]], [1001, 1002])
        self.assertEqual(issue["overflow_count"], 1)
        self.assertEqual(issue["overflow_label"], "+1 more")

    def test_build_view_model_ignores_invalid_instance_ids(self):
        module = _load_module()
        report = _make_report()
        report["grouped"][10]["Missing coordination element"]["instance_ids"] = set([1001, "bad-id", 1002])

        model = module.build_coordination_review_view_model(report)
        issue = model["links"][1]["issues"][0]

        self.assertEqual([row["instance_id"] for row in issue["instance_rows"]], [1001, 1002])
        self.assertEqual(issue["total_instances"], 2)

    def test_build_view_model_returns_empty_state_when_no_problem_links_exist(self):
        module = _load_module()
        report = _make_report()
        report["grouped"] = {}
        report["link_totals"] = {10: 0, 20: 0, 30: 0}
        report["total_matching_warnings"] = 0
        report["total_link_assignments"] = 0

        model = module.build_coordination_review_view_model(report)

        self.assertTrue(model["is_empty"])
        self.assertEqual(model["links"], [])
        self.assertEqual(len(model["link_filter_options"]), 1)
        self.assertEqual(len(model["issue_filter_options"]), 1)

    def test_build_view_model_preserves_detection_error_flag(self):
        module = _load_module()
        report = _make_report()
        report["grouped"] = {}
        report["link_totals"] = {}
        report["total_matching_warnings"] = 0
        report["total_link_assignments"] = 0
        report["detection_error"] = True

        model = module.build_coordination_review_view_model(report)

        self.assertTrue(model["is_empty"])
        self.assertTrue(model["detection_error"])


if __name__ == "__main__":
    unittest.main()
