import importlib.util
import os
import pathlib
import tempfile
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "lib"
    / "rltools"
    / "coordination_review_native.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("coordination_review_native", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAMPLE_REPORT_HTML = """
<html>
  <body>
    <table>
      <tr><th>Message</th><th>Action</th><th>Comment</th></tr>
      <tr><td data-level="0">New/Unresolved</td><td></td><td></td></tr>
      <tr><td data-level="1">Grids</td><td></td><td></td></tr>
      <tr><td data-level="2">Maintain relative position of Grids</td><td></td><td></td></tr>
      <tr><td data-level="3">Grid moved</td><td>Postpone</td><td></td></tr>
      <tr>
        <td data-level="4">24091_Belmont Village_WWP_AR_2025.rvt : Shared Views, Levels, Grids : Grids : Grid : 3 : id 357263</td>
        <td></td>
        <td></td>
      </tr>
      <tr>
        <td data-level="4">_GB GRID : Grids : Grid : 3 : id 2396881</td>
        <td></td>
        <td></td>
      </tr>
      <tr><td data-level="0">Postponed</td><td></td><td></td></tr>
      <tr><td data-level="1">Grids</td><td></td><td></td></tr>
      <tr><td data-level="2">Check whether an Element exists</td><td></td><td></td></tr>
      <tr><td data-level="3">Element deleted</td><td>Do nothing</td><td>previously reviewed</td></tr>
    </table>
  </body>
</html>
"""


class CoordinationReviewNativeTests(unittest.TestCase):
    def test_parse_native_report_groups_status_rule_action_and_element_ids(self):
        module = _load_module()

        rows = module.parse_native_coordination_review_html(
            SAMPLE_REPORT_HTML,
            link_name="24091_Belmont Village_WWP_AR_2025.rvt",
            link_key="link-1",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "New/Unresolved")
        self.assertEqual(rows[0]["category_path"], ["Grids", "Maintain relative position of Grids"])
        self.assertEqual(rows[0]["message"], "Grid moved")
        self.assertEqual(rows[0]["action"], "Postpone")
        self.assertEqual(rows[0]["linked_element_ids"], [357263])
        self.assertEqual(rows[0]["host_element_ids"], [2396881])
        self.assertEqual(rows[1]["status"], "Postponed")
        self.assertEqual(rows[1]["action"], "Do nothing")
        self.assertEqual(rows[1]["comment"], "previously reviewed")

    def test_build_report_from_native_rows_matches_existing_window_shape(self):
        module = _load_module()
        rows = module.parse_native_coordination_review_html(
            SAMPLE_REPORT_HTML,
            link_name="24091_Belmont Village_WWP_AR_2025.rvt",
            link_key="link-1",
        )

        report = module.build_report_from_native_rows(
            "24091_Belmont Village Westwood_P_2025",
            rows,
        )

        self.assertEqual(report["doc_title"], "24091_Belmont Village Westwood_P_2025")
        self.assertEqual(report["total_matching_warnings"], 2)
        self.assertEqual(report["total_link_assignments"], 2)
        self.assertEqual(report["link_map"]["link-1"]["name"], "24091_Belmont Village_WWP_AR_2025.rvt")
        self.assertEqual(report["link_totals"]["link-1"], 2)
        issue_names = sorted(report["grouped"]["link-1"].keys())
        self.assertEqual(
            issue_names,
            [
                "New/Unresolved > Grids > Maintain relative position of Grids > Grid moved [Action: Postpone]",
                "Postponed > Grids > Check whether an Element exists > Element deleted [Action: Do nothing] [Comment: previously reviewed]",
            ],
        )

    def test_delete_html_cache_removes_successful_and_partial_reports(self):
        module = _load_module()

        temp_dir = tempfile.mkdtemp()
        html_path = os.path.join(temp_dir, "coordination-review.html")
        other_path = os.path.join(temp_dir, "keep.txt")
        with open(html_path, "w") as handle:
            handle.write(SAMPLE_REPORT_HTML)
        with open(other_path, "w") as handle:
            handle.write("keep")

        removed = module.delete_html_cache([html_path, os.path.join(temp_dir, "missing.html")])

        self.assertEqual(removed, [html_path])
        self.assertFalse(os.path.exists(html_path))
        self.assertTrue(os.path.exists(other_path))

    def test_build_error_report_uses_error_only_shape(self):
        module = _load_module()

        report = module.build_error_report(
            "Coordination_Model",
            "wait_for_native_dialog",
            "Coordination Review dialog was not found.",
        )

        self.assertEqual(report["doc_title"], "Coordination_Model")
        self.assertEqual(report["grouped"], {})
        self.assertEqual(report["total_matching_warnings"], 0)
        self.assertEqual(report["automation_error"]["stage"], "wait_for_native_dialog")
        self.assertIn("not found", report["automation_error"]["message"])


if __name__ == "__main__":
    unittest.main()
