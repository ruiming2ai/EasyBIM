# -*- coding: utf-8 -*-
"""Tests for links_loader_state -- pure-Python logic, no Revit dependency."""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_STATE_DIR = os.path.normpath(os.path.join(
    _HERE, os.pardir, os.pardir,
    "EasyBIM.tab", "Links.panel", "Links Loader.pushbutton",
))
if _STATE_DIR not in sys.path:
    sys.path.insert(0, _STATE_DIR)

import links_loader_state as state


class TestLinkRecord(unittest.TestCase):

    def test_to_dict(self):
        rec = state.LinkRecord("A.rvt", "RevitLinkType", "C:\\A.rvt", "Absolute", True)
        d = rec.to_dict()
        self.assertEqual(d["name"], "A.rvt")
        self.assertEqual(d["element_type"], "RevitLinkType")
        self.assertEqual(d["path"], "C:\\A.rvt")
        self.assertEqual(d["path_type"], "Absolute")
        self.assertTrue(d["is_loaded"])


class TestBuildExportData(unittest.TestCase):

    def test_structure(self):
        records = [
            state.LinkRecord("A.rvt", "RevitLinkType", "C:\\A.rvt", "Absolute", True),
            state.LinkRecord("B.dwg", "CADLinkType", "C:\\B.dwg", "Relative", False),
        ]
        data = state.build_export_data(records, "2024", "Project.rvt")
        self.assertEqual(data["format"], "easybim-links-loader")
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["exported"]["revit_version"], "2024")
        self.assertEqual(data["exported"]["project_name"], "Project.rvt")
        self.assertIn("timestamp", data["exported"])
        self.assertEqual(len(data["links"]), 2)
        self.assertEqual(data["links"][0]["name"], "A.rvt")

    def test_roundtrip_json(self):
        records = [state.LinkRecord("X.rvt", "RevitLinkType", "D:\\X.rvt")]
        data = state.build_export_data(records, "2025", "Test.rvt")
        text = state.dump_json(data)
        parsed = json.loads(text)
        self.assertEqual(parsed["format"], "easybim-links-loader")
        self.assertEqual(parsed["links"][0]["name"], "X.rvt")


class TestParseImportData(unittest.TestCase):

    def _make_valid(self, **overrides):
        d = {
            "format": "easybim-links-loader",
            "schema_version": 1,
            "links": [
                {"name": "A.rvt", "path": "C:\\A.rvt", "element_type": "RevitLinkType"},
            ],
        }
        d.update(overrides)
        return d

    def test_valid(self):
        records, err = state.parse_import_data(self._make_valid())
        self.assertEqual(err, "")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "A.rvt")

    def test_wrong_format(self):
        records, err = state.parse_import_data(self._make_valid(format="wrong"))
        self.assertEqual(len(records), 0)
        self.assertIn("Unrecognised", err)

    def test_future_schema(self):
        records, err = state.parse_import_data(self._make_valid(schema_version=999))
        self.assertEqual(len(records), 0)
        self.assertIn("newer", err)

    def test_missing_links(self):
        d = {"format": "easybim-links-loader", "schema_version": 1}
        records, err = state.parse_import_data(d)
        self.assertIn("links", err)

    def test_not_a_dict(self):
        records, err = state.parse_import_data([1, 2])
        self.assertIn("valid JSON object", err)

    def test_link_missing_name(self):
        d = self._make_valid()
        d["links"] = [{"path": "C:\\no_name.rvt"}]
        records, err = state.parse_import_data(d)
        self.assertIn("missing 'name'", err)


class TestBuildImportPlan(unittest.TestCase):

    def test_update_detected(self):
        current = [state.LinkRecord("A.rvt", "RevitLinkType", "C:\\Old\\A.rvt")]
        imported = [state.LinkRecord("A.rvt", "RevitLinkType", "C:\\New\\A.rvt")]
        plan = state.build_import_plan(current, imported)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].status, "update")
        self.assertEqual(plan[0].old_path, "C:\\Old\\A.rvt")
        self.assertEqual(plan[0].new_path, "C:\\New\\A.rvt")

    def test_unchanged_detected(self):
        current = [state.LinkRecord("A.rvt", "RevitLinkType", "C:\\Same\\A.rvt")]
        imported = [state.LinkRecord("A.rvt", "RevitLinkType", "C:\\Same\\A.rvt")]
        plan = state.build_import_plan(current, imported)
        self.assertEqual(plan[0].status, "unchanged")

    def test_not_found_in_document(self):
        current = []
        imported = [state.LinkRecord("Z.rvt", "RevitLinkType", "C:\\Z.rvt")]
        plan = state.build_import_plan(current, imported)
        self.assertEqual(plan[0].status, "not_found_in_document")

    def test_unsupported_type(self):
        current = [state.LinkRecord("B.dwg", "CADLinkType", "C:\\B.dwg")]
        imported = [state.LinkRecord("B.dwg", "CADLinkType", "D:\\B.dwg")]
        plan = state.build_import_plan(current, imported)
        self.assertEqual(plan[0].status, "unsupported_type")

    def test_case_insensitive_matching(self):
        current = [state.LinkRecord("ARCH.rvt", "RevitLinkType", "C:\\Old\\ARCH.rvt")]
        imported = [state.LinkRecord("arch.rvt", "RevitLinkType", "C:\\New\\ARCH.rvt")]
        plan = state.build_import_plan(current, imported)
        self.assertEqual(plan[0].status, "update")

    def test_is_selected_default(self):
        current = [
            state.LinkRecord("A.rvt", "RevitLinkType", "C:\\Old\\A.rvt"),
            state.LinkRecord("B.rvt", "RevitLinkType", "C:\\Same\\B.rvt"),
        ]
        imported = [
            state.LinkRecord("A.rvt", "RevitLinkType", "C:\\New\\A.rvt"),
            state.LinkRecord("B.rvt", "RevitLinkType", "C:\\Same\\B.rvt"),
        ]
        plan = state.build_import_plan(current, imported)
        update_item = [p for p in plan if p.status == "update"][0]
        unchanged_item = [p for p in plan if p.status == "unchanged"][0]
        self.assertTrue(update_item.is_selected)
        self.assertFalse(unchanged_item.is_selected)


class TestBuildResultSummary(unittest.TestCase):

    def test_all_success(self):
        results = [("A.rvt", True, ""), ("B.rvt", True, "")]
        text = state.build_result_summary(results)
        self.assertIn("2 link(s) updated", text)

    def test_mixed(self):
        results = [("A.rvt", True, ""), ("B.rvt", False, "File not found")]
        text = state.build_result_summary(results)
        self.assertIn("1 link(s) updated", text)
        self.assertIn("1 link(s) failed", text)
        self.assertIn("B.rvt", text)

    def test_empty(self):
        text = state.build_result_summary([])
        self.assertIn("No links were updated", text)


class TestCheckFileExists(unittest.TestCase):

    def test_existing_file(self):
        self.assertTrue(state.check_file_exists(__file__))

    def test_nonexistent(self):
        self.assertFalse(state.check_file_exists("/nonexistent/path.rvt"))

    def test_empty_path(self):
        self.assertFalse(state.check_file_exists(""))


class TestStatusLabel(unittest.TestCase):

    def test_known_statuses(self):
        self.assertEqual(state.status_label("update"), "Will update")
        self.assertEqual(state.status_label("unchanged"), "Unchanged")
        self.assertEqual(state.status_label("unsupported_type"), "Not supported")

    def test_unknown_passthrough(self):
        self.assertEqual(state.status_label("custom"), "custom")


class TestCountUpdatable(unittest.TestCase):

    def test_counts_selected_updates(self):
        items = [
            state.ImportPlanItem("A", "RevitLinkType", "", "", "update"),
            state.ImportPlanItem("B", "RevitLinkType", "", "", "unchanged"),
            state.ImportPlanItem("C", "RevitLinkType", "", "", "update"),
        ]
        items[2].is_selected = False
        self.assertEqual(state.count_updatable(items), 1)


class TestDumpJson(unittest.TestCase):

    def test_produces_valid_json(self):
        data = {"key": "value", "num": 42}
        text = state.dump_json(data)
        parsed = json.loads(text)
        self.assertEqual(parsed["key"], "value")


if __name__ == "__main__":
    unittest.main()
