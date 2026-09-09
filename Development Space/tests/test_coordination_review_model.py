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
