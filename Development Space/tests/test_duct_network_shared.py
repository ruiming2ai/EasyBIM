"""The halves Damper Check and Fire Damper Check share, pinned on their own.

``duct_network_state`` (the graph and hop distances), ``type_checklist``
(by-name choices, whole-word keywords, report search) and
``local_settings`` (the JSON file that never raises).
"""

import importlib
import os
import pathlib
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_PARENT = str(REPO_ROOT / "lib")
if LIB_PARENT not in sys.path:
    sys.path.insert(0, LIB_PARENT)

network = importlib.import_module("easybim.duct_network_state")
checklist = importlib.import_module("easybim.type_checklist")
local_settings = importlib.import_module("easybim.local_settings")


def snapshot(edges, kinds=None, damper_types=()):
    """A tiny snapshot from ``[(a, b), ...]`` edges."""
    kinds = kinds or {}
    elements = {}
    ids = set()
    for a, b in edges:
        ids.add(a)
        ids.add(b)
    for element_id in ids:
        elements[element_id] = {"id": element_id, "kind": kinds.get(element_id, "duct"),
                                "type_key": u"T : {0}".format(element_id), "connectors": []}
    for index, (a, b) in enumerate(edges):
        elements[a]["connectors"].append({"id": index, "type": u"End", "connected": True,
                                          "partners": [[b, index]]})
        elements[b]["connectors"].append({"id": index, "type": u"End", "connected": True,
                                          "partners": [[a, index]]})
    return {"elements": elements}


class HopDistanceTests(unittest.TestCase):
    def test_hops_count_connections_and_stop_at_the_limit(self):
        graph = network.build_graph(snapshot([(1, 2), (2, 3), (3, 4), (4, 5)]), {})
        hops = network.hop_distances(graph, 1, 3)
        self.assertEqual(hops, {1: 0, 2: 1, 3: 2, 4: 3})

    def test_equipment_is_reached_but_never_walked_through(self):
        graph = network.build_graph(snapshot([(1, 2), (2, 3), (3, 4)], kinds={3: "equipment"}), {})
        hops = network.hop_distances(graph, 1, 10)
        self.assertEqual(hops, {1: 0, 2: 1, 3: 2})
        # Starting at the equipment itself does walk out of it.
        self.assertEqual(network.hop_distances(graph, 3, 1), {3: 0, 2: 1, 4: 1})

    def test_an_unread_stub_is_walked_through(self):
        snap = snapshot([(1, 2), (2, 3)])
        snap["elements"][2]["connectors"][1]["partners"] = [[99, 0]]
        snap["elements"][3]["connectors"][0]["partners"] = [[99, 1]]
        graph = network.build_graph(snap, {})
        self.assertTrue(graph["nodes"][99]["error"])
        hops = network.hop_distances(graph, 1, 5)
        self.assertEqual(hops[3], 3)

    def test_an_unknown_start_yields_nothing(self):
        graph = network.build_graph(snapshot([(1, 2)]), {})
        self.assertEqual(network.hop_distances(graph, 7, 3), {})

    def test_marked_types_become_dampers(self):
        graph = network.build_graph(snapshot([(1, 2)]), {"damper_types": set([u"T : 2"])})
        self.assertTrue(graph["nodes"][2]["is_damper"])
        self.assertFalse(graph["nodes"][1]["is_damper"])


class KeywordTests(unittest.TestCase):
    def test_whole_word_keywords_do_not_match_inside_words(self):
        ticked, _reason = checklist.keyword_match(u"Standard", (u"hr",), (), whole_word=(u"hr",))
        self.assertFalse(ticked)
        ticked, reason = checklist.keyword_match(u"Wall - 1 HR", (u"hr",), (), whole_word=(u"hr",))
        self.assertTrue(ticked)
        self.assertIn(u"hr", reason)

    def test_substring_keywords_and_exclusions_win(self):
        ticked, _r = checklist.keyword_match(u"Fire Damper", (u"damper",), (u"fire",))
        self.assertFalse(ticked)
        ticked, _r = checklist.keyword_match(u"Volume Damper", (u"damper",), (u"fire",))
        self.assertTrue(ticked)


class ChoiceTests(unittest.TestCase):
    def rule(self, row):
        return (u"damper" in row["type_key"].lower(), u"kw")

    def test_saved_choices_win_and_fold_keeps_other_models(self):
        rows = [{"type_key": u"A Damper : 1", "category": u"X", "count": 1},
                {"type_key": u"B : 2", "category": u"X", "count": 2}]
        picked = checklist.preselect_rows(rows, [u"B : 2"], [u"A Damper : 1"], self.rule)
        by_key = dict((row["type_key"], row) for row in picked)
        self.assertTrue(by_key[u"B : 2"]["is_checked"])
        self.assertEqual(by_key[u"A Damper : 1"]["reason"], u"unticked before")
        for row in picked:
            row["is_checked"] = row["type_key"] == u"A Damper : 1"
        saved, unticked = checklist.fold_choices([u"Other : Z"], [u"Old : Y"], picked, self.rule)
        self.assertEqual(saved, [u"A Damper : 1", u"Other : Z"])
        self.assertEqual(unticked, [u"Old : Y"])  # B never needed a note

    def test_rows_keyed_by_key_work_too(self):
        rows = [{"key": u"Fire Rating 1HR", "category": u"Lines", "count": 4}]
        picked = checklist.preselect_rows(rows, [u"Fire Rating 1HR"], [], None)
        self.assertTrue(picked[0]["is_checked"])
        saved, _unticked = checklist.fold_choices([], [], picked, None)
        self.assertEqual(saved, [u"Fire Rating 1HR"])

    def test_groups_follow_the_order_then_alphabet(self):
        rows = [{"type_key": u"z", "category": u"Zed", "count": 1},
                {"type_key": u"a", "category": u"Alpha", "count": 2},
                {"type_key": u"m", "category": u"Main", "count": 3}]
        groups = checklist.group_rows(rows, order=(u"Main",), expanded=(u"Main",))
        self.assertEqual([g["category"] for g in groups], [u"Main", u"Alpha", u"Zed"])
        self.assertTrue(groups[0]["is_expanded"])
        self.assertEqual(groups[0]["instance_count"], 3)
        self.assertEqual(checklist.count_text(rows, u"rows ticked"), u"0 of 3 rows ticked.")


class SearchTests(unittest.TestCase):
    def test_id_fields_may_be_lists(self):
        item = {"title": u"Duct", "ids": [12, 34], "damper_id": None}
        self.assertTrue(checklist.matches(u"34", item, ("title",), ("ids", "damper_id")))
        self.assertFalse(checklist.matches(u"3", item, ("title",), ("ids", "damper_id")))
        self.assertTrue(checklist.matches(u"duct 12", item, ("title",), ("ids",)))

    def test_filter_report_recounts_problems(self):
        report = {"buckets": [{"key": "bad", "items": [{"title": u"x", "id": 1}, {"title": u"y", "id": 2}]},
                              {"key": "ok", "items": [{"title": u"x", "id": 3}]}]}
        filtered = checklist.filter_report(report, u"x", ("bad",), ("title",), ("id",))
        self.assertEqual(filtered["counts"], {"bad": 1, "ok": 1})
        self.assertEqual(filtered["problem_count"], 1)
        self.assertEqual(checklist.cap_items(list(range(5)), 2), ([0, 1], 3))


class LocalSettingsTests(unittest.TestCase):
    def defaults(self):
        return {"schema": 1, "value": 1}

    def normalize(self, raw):
        settings = self.defaults()
        if isinstance(raw, dict):
            try:
                settings["value"] = int(raw.get("value", 1))
            except Exception:
                pass
        return settings

    def test_round_trip_and_the_tmp_is_gone(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "deep", "s.json")
        ok, error = local_settings.save("id", {"value": 7, "junk": 1}, self.normalize, 1, path=path)
        self.assertTrue(ok, error)
        self.assertFalse(os.path.exists(path + ".tmp"))
        settings, note = local_settings.load("id", self.defaults, self.normalize, path=path)
        self.assertEqual(settings, {"schema": 1, "value": 7})
        self.assertEqual(note, u"")

    def test_missing_and_corrupt_files_yield_defaults(self):
        settings, note = local_settings.load("id", self.defaults, self.normalize,
                                             path=os.path.join(tempfile.gettempdir(), "nope.json"))
        self.assertEqual(settings, self.defaults())
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "s.json")
        with open(path, "w") as handle:
            handle.write("{oops")
        settings, note = local_settings.load("id", self.defaults, self.normalize, path=path)
        self.assertEqual(settings, self.defaults())
        self.assertIn(u"could not be read", note)

    def test_name_list_sorts_and_dedupes(self):
        self.assertEqual(local_settings.name_list([u"b", u"a", u"a", u"", None]), [u"a", u"b"])
        self.assertEqual(local_settings.name_list("not a list"), [])


if __name__ == "__main__":
    unittest.main()
