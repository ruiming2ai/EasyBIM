"""Fire Damper Check's verdicts, pinned on synthetic ducts, walls and dampers.

Crossings arrive the way ``link_crossings.find_crossings`` produces them;
the walls are plain barrier records; the ducts and dampers are a snapshot
the way ``duct_network_revit.scan`` hands one over with curves and
connector origins.  Distances are in feet, the report speaks millimetres.
"""

import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Fire Damper Check.pushbutton"
LIB_PARENT = str(REPO_ROOT / "lib")
if LIB_PARENT not in sys.path:
    sys.path.insert(0, LIB_PARENT)

def _ensure_easybim_package():
    """Other test modules stub ``easybim`` in sys.modules, sometimes without a
    ``__path__``; give it one so fresh submodule imports resolve to lib."""
    package = sys.modules.get("easybim")
    if package is None:
        return
    if not getattr(package, "__path__", None):
        package.__path__ = [str(REPO_ROOT / "lib" / "easybim")]


_ensure_easybim_package()


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(COMMAND_DIR / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = _load("fire_damper_check_state")
settings_mod = _load("fire_damper_check_settings")

MM = 1.0 / 304.8
FD = u"Fire Damper : 1hr"
DUCT = u"Rectangular Duct : Std"
WALL_KEY = u"Arch.rvt|500|wall|10"


def wall_record(key=WALL_KEY, x=10.0, rating=u"1 HR", instance_id=500, level=u"L1"):
    return {"key": key, "context_key": u"Arch.rvt", "link_title": u"Arch.rvt", "instance_id": instance_id,
            "element_id": 10, "kind": "wall", "type_name": u"Int - 1HR", "owner_type": u"",
            "rating": rating, "rated_by": u"parameter", "thickness": 0.5, "level": level,
            "box": [[x - 0.25, 0.0, 0.0], [x + 0.25, 20.0, 10.0]],
            "label": u"Arch.rvt · Int - 1HR (rated 1 HR)"}


def crossing(element_id, kind="duct", key=WALL_KEY, point=(10.0, 5.0, 5.0), partial=False, along=False):
    return {"element_id": element_id, "element_kind": kind, "barrier_key": key, "point": list(point),
            "length": 0.5, "thickness": 0.5, "partial": partial, "along": along}


class Snap(object):
    def __init__(self):
        self.elements = {}

    def duct(self, element_id, p0, p1, level=u"L1", system=u"SA-1", kind="duct"):
        record = {"id": element_id, "kind": kind, "type_key": DUCT, "level": level, "system_name": system,
                  "connectors": [{"id": 0, "type": u"End", "connected": False, "partners": []},
                                 {"id": 1, "type": u"End", "connected": False, "partners": []}]}
        if kind == "duct":
            record["curve"] = [list(p0), list(p1)]
        else:
            record["polyline"] = [list(p0), list(p1)]
        self.elements[element_id] = record
        return self

    def damper(self, element_id, p0, p1, key=FD, level=u"L1", with_origins=True, bbox=None):
        connectors = [{"id": 0, "type": u"End", "connected": False, "partners": []},
                      {"id": 1, "type": u"End", "connected": False, "partners": []}]
        if with_origins:
            connectors[0]["origin"] = list(p0)
            connectors[1]["origin"] = list(p1)
        record = {"id": element_id, "kind": "accessory", "type_key": key, "level": level,
                  "connectors": connectors}
        if bbox:
            record["bbox"] = bbox
        self.elements[element_id] = record
        return self

    def fitting(self, element_id, box, key=u"Elbow : Round"):
        self.elements[element_id] = {"id": element_id, "kind": "fitting", "type_key": key, "level": u"L1",
                                     "connectors": [], "bbox": box}
        return self

    def join(self, a, a_conn, b, b_conn):
        for owner, own, other, other_conn in ((a, a_conn, b, b_conn), (b, b_conn, a, a_conn)):
            for connector in self.elements[owner]["connectors"]:
                if connector["id"] == own:
                    connector["connected"] = True
                    connector["partners"].append([other, other_conn])
        return self

    def snapshot(self, meta=None):
        return {"elements": self.elements, "meta": meta or {"elements_read": len(self.elements), "seconds": 0.5}}


def config(near_mm=600, hops=3, dampers=(FD,)):
    return state.config_from_settings({"damper_types": list(dampers), "near_mm": near_mm, "hops": hops})


def desc(barriers=None, line_barriers=None, skips=None):
    return {"barriers": barriers if barriers is not None else [wall_record()],
            "line_barriers": line_barriers or [], "skips": skips or {}}


def named(report, key):
    """One bucket by key - never by position, so a new bucket cannot move it."""
    for bucket in report["buckets"]:
        if bucket["key"] == key:
            return bucket
    raise AssertionError("no bucket %r" % key)


def bucket_of(report, needle):
    for bucket in report["buckets"]:
        for item in bucket["items"]:
            if needle in item["element_ids"]:
                return bucket["key"], item
    raise AssertionError("%s is in no bucket" % needle)


# ----------------------------------------------------------------- verdicts


class VerdictTests(unittest.TestCase):
    def test_a_damper_whose_body_crosses_the_wall_covers_the_duct(self):
        # duct 1 ends at the wall face; damper 2 sits in the wall; duct 3 continues.
        snap = (Snap().duct(1, (0, 5, 5), (9.75, 5, 5)).damper(2, (9.75, 5, 5), (10.25, 5, 5))
                .duct(3, (10.25, 5, 5), (20, 5, 5)).join(1, 1, 2, 0).join(2, 1, 3, 0))
        crossings = [crossing(2, "damper"), crossing(1, partial=True, point=(9.9, 5, 5))]
        analysis = state.analyze(snap.snapshot(), crossings, desc(), config())
        self.assertEqual(len(analysis["clusters"]), 1)
        report = state.classify(analysis, 600, 3)
        key, item = bucket_of(report, 1)
        self.assertEqual(key, "covered_in_barrier")
        self.assertEqual(item["damper_id"], 2)
        self.assertEqual(item["show"]["host_ids"], [1, 2])
        self.assertEqual(item["show"]["link_instance_id"], 500)
        self.assertEqual(item["show"]["link_element_id"], 10)
        self.assertIn(u"sits in the barrier", item["detail"])

    def test_a_damper_within_tolerance_on_the_run_covers_and_the_tolerance_is_live(self):
        # duct 1 through the wall, damper 2 attached 400 mm past the wall face
        # on the same run: its centre is 491 mm from the penetration point.
        far = 10.25 + 400 * MM
        snap = (Snap().duct(1, (0, 5, 5), (far, 5, 5)).damper(2, (far, 5, 5), (far + 0.1, 5, 5))
                .join(1, 1, 2, 0))
        analysis = state.analyze(snap.snapshot(), [crossing(1)], desc(), config())
        self.assertEqual(bucket_of(state.classify(analysis, 600, 3), 1)[0], "covered_within")
        self.assertEqual(bucket_of(state.classify(analysis, 400, 3), 1)[0], "missing")
        item = bucket_of(state.classify(analysis, 400, 3), 1)[1]
        self.assertIn(u"nearest ticked damper is", item["detail"])

    def test_a_damper_on_another_run_within_tolerance_does_not_count(self):
        # duct 1 with damper 2; parallel duct 3 300 mm away through the same wall, no damper.
        snap = (Snap().duct(1, (0, 5, 5), (11, 5, 5)).damper(2, (11, 5, 5), (11.3, 5, 5)).join(1, 1, 2, 0)
                .duct(3, (0, 5 + 300 * MM, 5), (20, 5 + 300 * MM, 5)))
        crossings = [crossing(1), crossing(3, point=(10.0, 5 + 300 * MM, 5.0))]
        analysis = state.analyze(snap.snapshot(), crossings, desc(), config())
        report = state.classify(analysis, 600, 3)
        self.assertEqual(bucket_of(report, 1)[0], "covered_within")
        key, item = bucket_of(report, 3)
        self.assertEqual(key, "other_run_damper")
        self.assertIn(u"covers another duct", item["detail"])
        self.assertEqual(report["problem_count"], 1)

    def test_the_hop_limit_is_live(self):
        # duct 1 - duct 4 - duct 5 - damper 2: three hops.
        snap = (Snap().duct(1, (0, 5, 5), (10.5, 5, 5)).duct(4, (10.5, 5, 5), (10.6, 5, 5))
                .duct(5, (10.6, 5, 5), (10.7, 5, 5)).damper(2, (10.7, 5, 5), (11.0, 5, 5))
                .join(1, 1, 4, 0).join(4, 1, 5, 0).join(5, 1, 2, 0))
        analysis = state.analyze(snap.snapshot(), [crossing(1)], desc(), config())
        self.assertEqual(bucket_of(state.classify(analysis, 600, 3), 1)[0], "covered_within")
        self.assertEqual(bucket_of(state.classify(analysis, 600, 2), 1)[0], "other_run_damper")

    def test_flex_through_a_rated_wall_is_a_problem_even_with_a_damper(self):
        snap = (Snap().duct(1, (0, 5, 5), (11, 5, 5), kind="flex").damper(2, (11, 5, 5), (11.3, 5, 5))
                .join(1, 1, 2, 0))
        analysis = state.analyze(snap.snapshot(), [crossing(1, "flex")], desc(), config())
        self.assertEqual(bucket_of(state.classify(analysis, 600, 3), 1)[0], "flex_crossing")

    def test_a_duct_running_inside_the_wall_is_a_review_row(self):
        snap = Snap().duct(1, (10, -5, 5), (10, 25, 5))
        analysis = state.analyze(snap.snapshot(), [crossing(1, along=True, point=(10, 10, 5))], desc(), config())
        self.assertEqual(bucket_of(state.classify(analysis, 600, 3), 1)[0], "along_barrier")

    def test_a_fitting_overlapping_the_wall_is_a_review_row(self):
        snap = Snap().fitting(7, [[9.5, 4.0, 4.0], [10.5, 6.0, 6.0]])
        analysis = state.analyze(snap.snapshot(), [], desc(), config())
        report = state.classify(analysis, 600, 3)
        key, item = bucket_of(report, 7)
        self.assertEqual(key, "fitting_crossing")
        self.assertEqual(item["show"]["link_element_id"], 10)

    def test_unreadable_barriers_are_listed(self):
        analysis = state.analyze(Snap().snapshot(), [], desc(skips={"unreadable": [WALL_KEY]}), config())
        report = state.classify(analysis, 600, 3)
        self.assertEqual(report["counts"]["barrier_unreadable"], 1)
        self.assertEqual(named(report, "barrier_unreadable")["items"][0]["show"]["link_element_id"], 10)

    def test_a_damper_far_from_every_barrier_is_idle_and_one_in_a_barrier_is_not(self):
        snap = (Snap().duct(1, (0, 5, 5), (11, 5, 5)).damper(2, (11, 5, 5), (11.3, 5, 5)).join(1, 1, 2, 0)
                .damper(9, (50, 50, 5), (50.3, 50, 5)))
        analysis = state.analyze(snap.snapshot(), [crossing(1)], desc(), config())
        report = state.classify(analysis, 600, 3)
        self.assertEqual(report["counts"]["idle_damper"], 1)
        idle = named(report, "idle_damper")["items"]
        self.assertEqual(idle[0]["damper_id"], 9)
        self.assertNotIn(2, [item["damper_id"] for item in idle])

    def test_a_damper_without_origins_is_judged_by_its_box(self):
        snap = (Snap().duct(1, (0, 5, 5), (11, 5, 5))
                .damper(2, None, None, with_origins=False, bbox=[[11.0, 4.8, 4.8], [11.3, 5.2, 5.2]])
                .join(1, 1, 2, 0))
        analysis = state.analyze(snap.snapshot(), [crossing(1)], desc(), config())
        self.assertEqual(analysis["skips"]["dampers_without_axis"], 1)
        self.assertEqual(bucket_of(state.classify(analysis, 600, 3), 1)[0], "covered_within")

    def test_a_barrier_of_this_model_shows_as_a_host_element(self):
        record = wall_record(key=u"This model|host|wall|10", instance_id=None)
        snap = Snap().duct(1, (0, 5, 5), (20, 5, 5))
        analysis = state.analyze(snap.snapshot(), [crossing(1, key=record["key"])], desc([record]), config())
        item = bucket_of(state.classify(analysis, 600, 3), 1)[1]
        self.assertEqual(item["show"]["host_ids"], [1, 10])
        self.assertIsNone(item["show"]["link_instance_id"])

    def test_view_scope_limits_the_ducts_not_the_dampers(self):
        snap = (Snap().duct(1, (0, 5, 5), (11, 5, 5)).damper(2, (11, 5, 5), (11.3, 5, 5)).join(1, 1, 2, 0)
                .duct(3, (0, 8, 5), (20, 8, 5)))
        info = state.segments_from_snapshot(snap.snapshot(), config(), scope_ids=[1])
        self.assertEqual([s["owner_id"] for s in info["segments"] if s["kind"] == "duct"], [1])
        self.assertIn(2, info["dampers"])
        self.assertEqual(info["skips"]["out_of_scope"], 1)


class PanelTests(unittest.TestCase):
    def panel(self, z0=0.0, z1=12.0):
        return {"key": u"Arch.rvt|500|line|700", "points": [[10.0, 0.0, 0.0], [10.0, 20.0, 0.0]],
                "z0": z0, "z1": z1, "thickness": 200 * MM, "label": u"Arch.rvt · line 'Fire Rating 1HR'",
                "link_title": u"Arch.rvt", "instance_id": 500, "element_id": 700, "kind": "line",
                "rating": u"", "level": u"L1"}

    def test_a_duct_crossing_the_line_within_the_band_is_a_crossing(self):
        hits = state.segment_vs_panel([0, 5, 5], [20, 5, 5], self.panel())
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0][1][0], 10.0)

    def test_above_the_band_and_parallel_are_not(self):
        self.assertEqual(state.segment_vs_panel([0, 5, 15], [20, 5, 15], self.panel()), [])
        self.assertEqual(state.segment_vs_panel([10, -5, 5], [10, 25, 5], self.panel()), [])
        self.assertEqual(state.segment_vs_panel([0, 5, 5], [9, 5, 5], self.panel()), [])

    def test_a_line_crossing_becomes_a_normal_verdict(self):
        snap = Snap().duct(1, (0, 5, 5), (20, 5, 5))
        analysis = state.analyze(snap.snapshot(), [], desc(barriers=[], line_barriers=[self.panel()]), config())
        self.assertEqual(len(analysis["clusters"]), 1)
        key, item = bucket_of(state.classify(analysis, 600, 3), 1)
        self.assertEqual(key, "missing")
        self.assertIn(u"line 'Fire Rating 1HR'", item["title"])
        self.assertEqual(item["show"]["link_element_id"], 700)


class ClusterTests(unittest.TestCase):
    def test_a_connected_damper_hit_joins_its_duct_and_far_ducts_stay_apart(self):
        items = [crossing(1, point=(10, 5, 5)), crossing(2, "damper", point=(10.1, 5, 5)),
                 crossing(3, point=(10, 15, 5))]
        clusters = state.cluster_crossings(items, connected=lambda duct, damper: (duct, damper) == (1, 2))
        self.assertEqual(len(clusters), 2)
        self.assertEqual(sorted(m["element_id"] for m in clusters[0]["members"]), [1, 2])
        self.assertEqual([m["element_id"] for m in clusters[1]["members"]], [3])

    def test_an_unconnected_damper_hit_stands_alone_even_when_close(self):
        items = [crossing(1, point=(10, 5, 5)), crossing(2, "damper", point=(10.1, 5, 5))]
        clusters = state.cluster_crossings(items, connected=lambda duct, damper: False)
        self.assertEqual(len(clusters), 2)
        self.assertEqual(clusters[1]["members"][0]["element_kind"], "damper")

    def test_different_barriers_never_merge(self):
        items = [crossing(1), crossing(2, "damper", key=u"other|500|wall|11")]
        self.assertEqual(len(state.cluster_crossings(items, connected=lambda d, m: True)), 2)


class PreselectTests(unittest.TestCase):
    def test_damper_keywords_are_token_aware_and_exclude_detectors(self):
        rows = [{"type_key": u"Fire Damper : 1hr", "kind": "accessory", "category": u"Duct Accessories", "count": 1},
                {"type_key": u"Duct Smoke Detector : Std", "kind": "accessory", "category": u"Duct Accessories", "count": 1},
                {"type_key": u"FD-1 : 300x300", "kind": "accessory", "category": u"Duct Accessories", "count": 1},
                {"type_key": u"Standard : x", "kind": "accessory", "category": u"Duct Accessories", "count": 1},
                {"type_key": u"Fire Damper Tap : Round", "kind": "fitting", "category": u"Duct Fittings", "count": 1},
                {"type_key": u"Diffuser Fire : x", "kind": "terminal", "category": u"Air Terminals", "count": 1}]
        picked = dict((row["type_key"], row) for row in state.preselect_dampers(rows, {}))
        self.assertTrue(picked[u"Fire Damper : 1hr"]["is_checked"])
        self.assertFalse(picked[u"Duct Smoke Detector : Std"]["is_checked"])
        self.assertTrue(picked[u"FD-1 : 300x300"]["is_checked"])
        self.assertFalse(picked[u"Standard : x"]["is_checked"])
        self.assertTrue(picked[u"Fire Damper Tap : Round"]["is_checked"])
        self.assertFalse(picked[u"Diffuser Fire : x"]["is_checked"])

    def test_wall_types_pre_tick_by_rating_then_keyword_and_never_curtain(self):
        types = [{"key": u"wall|Int - 1HR", "type_key": u"Int - 1HR", "kind": "wall", "category": u"Walls", "count": 3,
                  "ratings": {u"Fire Rating": u"1 HR"}, "curtain": False, "stacked": False},
                 {"key": u"wall|Standard", "type_key": u"Standard", "kind": "wall", "category": u"Walls", "count": 9,
                  "ratings": {}, "curtain": False, "stacked": False},
                 {"key": u"wall|FW-2HR", "type_key": u"FW-2HR", "kind": "wall", "category": u"Walls", "count": 2,
                  "ratings": {}, "curtain": False, "stacked": False},
                 {"key": u"wall|Storefront", "type_key": u"Storefront Fire", "kind": "wall", "category": u"Walls", "count": 1,
                  "ratings": {}, "curtain": True, "stacked": False},
                 {"key": u"floor|Slab 2HR", "type_key": u"Slab 2HR", "kind": "floor", "category": u"Floors", "count": 1,
                  "ratings": {}, "curtain": False, "stacked": False}]
        link = state.link_settings_for({}, u"Arch.rvt")
        walls = dict((row["type_key"], row) for row in state.preselect_barrier_types(types, link, "wall"))
        self.assertTrue(walls[u"Int - 1HR"]["is_checked"])
        self.assertEqual(walls[u"Int - 1HR"]["reason"], u"rated '1 HR'")
        self.assertFalse(walls[u"Standard"]["is_checked"])
        self.assertTrue(walls[u"FW-2HR"]["is_checked"])
        self.assertFalse(walls[u"Storefront Fire"]["is_checked"])
        self.assertIn(u"curtain", walls[u"Storefront Fire"]["reason"])
        floors = state.preselect_barrier_types(types, link, "floor")
        self.assertEqual([row["type_key"] for row in floors], [u"Slab 2HR"])
        self.assertTrue(floors[0]["is_checked"])

    def test_line_styles_pre_tick_by_keyword(self):
        rows = [{"key": u"Fire Rating 1HR", "count": 4, "category": u"Line styles"},
                {"key": u"Thin Lines", "count": 100, "category": u"Line styles"}]
        picked = state.preselect_line_styles(rows, state.link_settings_for({}, u"Arch.rvt"))
        self.assertEqual([(row["key"], row["is_checked"]) for row in picked],
                         [(u"Fire Rating 1HR", True), (u"Thin Lines", False)])

    def test_rating_param_options_put_the_built_in_name_first(self):
        options = state.rating_param_options({"rating_params": [{"name": u"FR_Rating", "count": 5}]})
        self.assertEqual([row["name"] for row in options], [u"Fire Rating", u"FR_Rating"])


class SetupTests(unittest.TestCase):
    def test_apply_setup_folds_per_link_and_keeps_other_links(self):
        settings = {"links": {u"Str.rvt": {"enabled": True, "rating_param": u"X", "wall_types": [u"Core"]}}}
        wall_rows = [{"type_key": u"Int - 1HR", "kind": "wall", "ratings": {}, "is_checked": True},
                     {"type_key": u"FW-2HR", "kind": "wall", "ratings": {}, "is_checked": False}]
        line_rows = [{"key": u"Fire Rating 1HR", "is_checked": True}]
        damper_rows = [{"type_key": u"Fire Damper : 1hr", "kind": "accessory", "is_checked": True},
                       {"type_key": u"FD-1 : x", "kind": "accessory", "is_checked": False}]
        new = state.apply_setup(settings, damper_rows,
                                {u"Arch.rvt": {"enabled": True, "rating_param": u"Fire Rating",
                                               "wall_rows": wall_rows, "floor_rows": [], "line_rows": line_rows}},
                                {"near_mm": 800, "hops": 2, "scope": "view", "include_floors": False,
                                 "band": {"mode": "fixed", "height_mm": 2500}})
        self.assertEqual(new["damper_types"], [u"Fire Damper : 1hr"])
        self.assertEqual(new["unticked_damper_types"], [u"FD-1 : x"])
        arch = new["links"][u"Arch.rvt"]
        self.assertEqual(arch["wall_types"], [u"Int - 1HR"])
        self.assertEqual(arch["unticked_wall_types"], [u"FW-2HR"])
        self.assertEqual(arch["line_styles"], [u"Fire Rating 1HR"])
        self.assertEqual(new["links"][u"Str.rvt"]["wall_types"], [u"Core"])
        self.assertEqual((new["near_mm"], new["hops"], new["scope"], new["include_floors"]), (800, 2, "view", False))
        self.assertEqual(new["band"]["mode"], "fixed")
        choices = state.choices_by_key(new, [u"Arch.rvt", u"New.rvt"])
        self.assertEqual(choices[u"Arch.rvt"]["wall_types"], set([u"Int - 1HR"]))
        self.assertEqual(choices[u"New.rvt"]["rating_param"], u"Fire Rating")

    def test_config_converts_millimetres_to_feet(self):
        cfg = state.config_from_settings({"near_mm": 600, "hops": 99, "line_thickness_mm": 200,
                                          "band": {"mode": "fixed", "height_mm": 3000}})
        self.assertAlmostEqual(cfg["near_ft"], 600 * MM)
        self.assertEqual(cfg["hops"], state.MAX_HOPS)
        self.assertAlmostEqual(cfg["line_thickness_ft"], 200 * MM)
        self.assertEqual(cfg["band_mode"], "fixed")
        self.assertTrue(cfg["include_floors"])


class ReportTextTests(unittest.TestCase):
    def test_summary_and_notes(self):
        snap = Snap().duct(1, (0, 5, 5), (20, 5, 5))
        analysis = state.analyze(snap.snapshot({"elements_read": 1, "seconds": 0.5, "truncated": True,
                                                "truncated_reason": u"cancelled"}),
                                 [crossing(1)], desc(skips={"curtain_walls": 2, "unreadable": []}), config())
        report = state.classify(analysis, 600, 3)
        line = state.summary_line(analysis, report)
        self.assertIn(u"1 of 1 crossings lack a fire damper", line)
        self.assertIn(u"Scan truncated: cancelled", line)
        notes = state.scan_notes(analysis, config(), [{"title": u"Str.rvt", "loaded": False, "status": u"Unloaded"}])
        self.assertTrue(any(u"Str.rvt" in note for note in notes))
        self.assertTrue(any(u"curtain" in note for note in notes))

    def test_search_matches_ids_in_lists(self):
        snap = Snap().duct(1, (0, 5, 5), (11, 5, 5)).damper(2, (11, 5, 5), (11.3, 5, 5)).join(1, 1, 2, 0)
        analysis = state.analyze(snap.snapshot(), [crossing(1)], desc(), config())
        report = state.classify(analysis, 600, 3)
        self.assertEqual(state.filter_report(report, u"2")["counts"]["covered_within"], 1)
        self.assertEqual(state.filter_report(report, u"7")["counts"]["covered_within"], 0)
        self.assertEqual(state.filter_report(report, u"arch 1hr")["counts"]["covered_within"], 1)


class IgnoreTests(unittest.TestCase):
    """Findings the reviewer sets aside, and the keys those records live under."""

    def analysis(self):
        snap = Snap().duct(1, (0, 5, 5), (20, 5, 5))
        return state.analyze(snap.snapshot(), [crossing(1)], desc(), config())

    def test_the_crossing_key_names_the_barrier_and_the_duct(self):
        report = state.classify(self.analysis(), 600, 3)
        self.assertEqual(bucket_of(report, 1)[1]["key"], u"cross:{0}:1".format(WALL_KEY))

    def test_the_key_does_not_move_when_another_crossing_appears(self):
        """Keyed by content, not by position: a second crossing elsewhere must
        not rename the first, or last week's decision points at the wrong row."""
        snap = Snap().duct(1, (0, 5, 5), (20, 5, 5)).duct(3, (0, 15, 5), (20, 15, 5))
        second = state.analyze(snap.snapshot(),
                               [crossing(1), crossing(3, point=(10, 15, 5))], desc(), config())
        report = state.classify(second, 600, 3)
        self.assertEqual(bucket_of(report, 1)[1]["key"], u"cross:{0}:1".format(WALL_KEY))

    def test_an_ignored_crossing_leaves_the_problem_tally(self):
        analysis = self.analysis()
        plain = state.classify(analysis, 600, 3)
        self.assertEqual((plain["problem_count"], plain["ignored_count"]), (1, 0))
        key = bucket_of(plain, 1)[1]["key"]

        aside = state.classify(analysis, 600, 3, [key])
        bucket, item = bucket_of(aside, 1)
        self.assertEqual(bucket, "ignored")
        self.assertEqual((aside["problem_count"], aside["ignored_count"]), (0, 1))
        self.assertTrue(item["is_ignored"])
        self.assertEqual(item["original_bucket"], "missing")
        self.assertIn(u"Set aside on review", item["detail"])
        self.assertEqual(item["show"]["link_element_id"], 10)

    def test_every_row_kind_can_be_set_aside(self):
        snap = Snap().fitting(7, [[9.5, 4.0, 4.0], [10.5, 6.0, 6.0]])
        snap.damper(9, (50, 50, 5), (50.3, 50, 5))
        analysis = state.analyze(snap.snapshot(), [], desc(skips={"unreadable": [WALL_KEY]}), config())
        report = state.classify(analysis, 600, 3)
        keys = [item["key"] for bucket in report["buckets"] for item in bucket["items"]]
        self.assertIn(u"fitting:7:{0}".format(WALL_KEY), keys)
        self.assertIn(u"barrier:{0}".format(WALL_KEY), keys)
        self.assertIn(u"idle:9", keys)
        aside = state.classify(analysis, 600, 3, keys)
        self.assertEqual(aside["counts"]["ignored"], len(keys))
        self.assertEqual(aside["counts"]["fitting_crossing"], 0)
        self.assertEqual(aside["counts"]["idle_damper"], 0)

    def test_the_ignored_bucket_is_never_a_problem(self):
        self.assertIn("ignored", [key for key, _title, _problem in state.BUCKETS])
        self.assertNotIn("ignored", state.PROBLEM_BUCKETS)

    def test_a_key_matching_no_finding_is_reported_stale_and_noted(self):
        analysis = self.analysis()
        report = state.classify(analysis, 600, 3, [u"cross:gone:1"])
        self.assertEqual(report["stale_ignored"], [u"cross:gone:1"])
        notes = state.scan_notes(analysis, config(), [], report=report)
        self.assertTrue(any(u"match no finding now" in note for note in notes))
        self.assertTrue(any(u"stored in this model" in note for note in notes))

    def test_the_summary_counts_what_was_set_aside(self):
        analysis = self.analysis()
        report = state.classify(analysis, 600, 3, [bucket_of(state.classify(analysis, 600, 3), 1)[1]["key"]])
        line = state.summary_line(analysis, report)
        self.assertIn(u"1 set aside", line)
        self.assertIn(u"The scan changes nothing in the model.", line)


class SettingsTests(unittest.TestCase):
    def test_round_trip_and_normalisation(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "s.json")
        ok, error = settings_mod.save({"near_mm": 0, "hops": 50, "links": {u"Arch.rvt": {"wall_types": [u"b", u"a"],
                                                                                        "rating_param": None}},
                                       "band": {"mode": "weird"}, "junk": 1}, path)
        self.assertTrue(ok, error)
        loaded, note = settings_mod.load(path)
        self.assertEqual(loaded["near_mm"], 1)
        self.assertEqual(loaded["hops"], settings_mod.MAX_HOPS)
        self.assertEqual(loaded["links"][u"Arch.rvt"]["wall_types"], [u"a", u"b"])
        self.assertEqual(loaded["links"][u"Arch.rvt"]["rating_param"], u"")
        self.assertEqual(loaded["band"]["mode"], "next_level")
        self.assertNotIn("junk", loaded)
        self.assertEqual(settings_mod.normalize(None), settings_mod.default_settings())

    def test_defaults_agree_with_the_state_module(self):
        defaults = settings_mod.default_settings()
        self.assertEqual(defaults["near_mm"], state.DEFAULT_NEAR_MM)
        self.assertEqual(defaults["hops"], state.DEFAULT_HOPS)
        self.assertEqual(settings_mod.DEFAULT_RATING_PARAM, state.DEFAULT_RATING_PARAM)


if __name__ == "__main__":
    unittest.main()
