import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
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

    def test_instance_rows_carry_view_issues_label(self):
        """The per-link button opens Revit's Coordination Review; it is
        labelled View Issues so nobody expects it to zoom to the element."""
        module = _load_module()
        links = module.build_problem_link_records(_make_report())

        labels = set()
        for link in links:
            for issue in link["issues"]:
                for row in issue["instance_rows"]:
                    labels.add(row["show_label"])

        self.assertEqual(labels, {"View Issues"})


class CheckedLinkRecordTests(unittest.TestCase):
    """Every monitored link gets a row, including a clean one: a clean row is
    a proved result, which is the point of comparing rather than waiting for
    Revit's one-shot warning."""

    def setUp(self):
        self.module = _load_module()

    def _row(self, link_id, name, issues=0, error="", monitoring=3, estimated=0, flagged=False):
        return {
            "link_id": link_id,
            "name": name,
            "issue_count": issues,
            "estimated_count": estimated,
            "monitoring_count": monitoring,
            "error": error,
            "flagged_by_revit": flagged,
            "report": {"groups": [{"kind": "level_moved", "title": "Level moved"}]},
        }

    def test_kinds_and_ordering_put_problems_first(self):
        report = {
            "links": [
                self._row(1, "Clean.rvt"),
                self._row(2, "Broken.rvt", error="Link is not loaded."),
                self._row(3, "Few.rvt", issues=1),
                self._row(4, "Many.rvt", issues=9),
            ]
        }
        records = self.module.build_checked_link_records(report)

        self.assertEqual([r["name"] for r in records], ["Many.rvt", "Few.rvt", "Broken.rvt", "Clean.rvt"])
        self.assertEqual(
            [r["kind"] for r in records],
            [self.module.KIND_PROBLEM, self.module.KIND_PROBLEM, self.module.KIND_ERROR, self.module.KIND_CLEAN],
        )

    def test_problem_rows_open_expanded_and_carry_their_groups(self):
        records = self.module.build_checked_link_records({"links": [self._row(1, "A.rvt", issues=2)]})
        record = records[0]

        self.assertTrue(record["is_expanded"])
        self.assertEqual(record["badge_text"], "2 difference(s)")
        self.assertIn("2 differences found", record["status_text"])
        self.assertIn("3 monitored elements", record["status_text"])
        self.assertEqual(len(record["groups"]), 1)

    def test_clean_rows_are_collapsed_and_say_so(self):
        records = self.module.build_checked_link_records({"links": [self._row(1, "A.rvt", monitoring=1)]})
        record = records[0]

        self.assertFalse(record["is_expanded"])
        self.assertEqual(record["badge_text"], "no differences")
        self.assertIn("No differences found (1 monitored element)", record["status_text"])

    def test_error_rows_show_the_reason(self):
        records = self.module.build_checked_link_records(
            {"links": [self._row(1, "A.rvt", error="Link A.rvt is not loaded.")]}
        )
        self.assertEqual(records[0]["badge_text"], "not checked")
        self.assertIn("not loaded", records[0]["status_text"])

    def test_estimated_matches_are_disclosed(self):
        records = self.module.build_checked_link_records(
            {"links": [self._row(1, "A.rvt", issues=4, estimated=2)]}
        )
        self.assertIn("2 estimated by nearest-element matching", records[0]["status_text"])

    def test_revit_flag_is_carried_through(self):
        records = self.module.build_checked_link_records({"links": [self._row(1, "A.rvt", flagged=True)]})
        self.assertTrue(records[0]["flagged_by_revit"])

    def test_no_links(self):
        self.assertEqual(self.module.build_checked_link_records({}), [])


class CheckedLinkSummaryTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_nothing_monitored_is_the_proved_not_applicable(self):
        summary = self.module.summarize_checked_links({"monitored_link_count": 0})
        self.assertTrue(summary["is_empty"])
        self.assertIn("Nothing in this model uses Copy/Monitor", summary["headline"])

    def test_all_links_clean_is_a_proved_all_clear(self):
        summary = self.module.summarize_checked_links(
            {"monitored_link_count": 3, "checked_count": 3, "problem_count": 0, "total_issues": 0}
        )
        self.assertTrue(summary["is_empty"])
        self.assertIn("No differences found across 3 monitored links", summary["headline"])
        self.assertIn("compared with its counterpart", summary["detail"])

    def test_singular_link(self):
        summary = self.module.summarize_checked_links(
            {"monitored_link_count": 1, "checked_count": 1, "problem_count": 0}
        )
        self.assertIn("1 monitored link.", summary["headline"])

    def test_differences_are_counted_in_the_status(self):
        summary = self.module.summarize_checked_links(
            {"monitored_link_count": 4, "checked_count": 4, "problem_count": 2, "total_issues": 7}
        )
        self.assertFalse(summary["is_empty"])
        self.assertIn("7 differences across 2 links of 4 monitored links", summary["status"])

    def test_a_cancelled_run_never_reports_an_all_clear(self):
        summary = self.module.summarize_checked_links(
            {"monitored_link_count": 5, "checked_count": 2, "problem_count": 0, "cancelled": True}
        )
        self.assertFalse(summary["is_empty"])
        self.assertIn("cancelled", summary["status"])


if __name__ == "__main__":
    unittest.main()
