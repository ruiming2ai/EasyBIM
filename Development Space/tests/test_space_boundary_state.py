"""Space Boundary's geometry, gates, plan and drift verdicts.

The fingerprint carries the whole tool: if a room's boundary and the region
drawn from it do not compare equal across two reads, every region reads as
edited and the review lane is worthless. So most of this file is about the
ways Revit can hand the same loop back differently.
"""

import importlib.util
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Space Boundary.pushbutton"
LIB_PARENT = str(REPO_ROOT / "lib")
if LIB_PARENT not in sys.path:
    sys.path.insert(0, LIB_PARENT)


def _ensure_easybim_package():
    package = sys.modules.get("easybim")
    if package is None:
        return
    if not getattr(package, "__path__", None):
        package.__path__ = [str(REPO_ROOT / "lib" / "easybim")]


_ensure_easybim_package()

# pyRevit puts the bundle folder on sys.path while its script runs, which is
# how the sibling modules import each other; do the same here.
if str(COMMAND_DIR) not in sys.path:
    sys.path.insert(0, str(COMMAND_DIR))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(COMMAND_DIR / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = _load("space_boundary_state")
settings_mod = _load("space_boundary_settings")


SQUARE = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]


def rotate(points, by):
    return points[by:] + points[:by]


def loop_records(points):
    """A point ring as the line records the Revit layer hands over."""
    records = []
    for index in range(len(points)):
        a = points[index]
        b = points[(index + 1) % len(points)]
        records.append(("L", a[0], a[1], b[0], b[1]))
    return records


class QuantiseTests(unittest.TestCase):
    def test_half_goes_up_on_every_runtime(self):
        """IronPython 2.7 rounds half away from zero, CPython 3 half to even.

        round() would make the tool and these tests disagree on exactly the
        values that land on a grid line, and only inside Revit.
        """
        self.assertEqual(state.quantise(0.5), 1)
        self.assertEqual(state.quantise(1.5), 2)
        self.assertEqual(state.quantise(2.5), 3)
        self.assertEqual(state.quantise(-0.5), 0)
        self.assertEqual(state.quantise(-1.5), -1)

    def test_the_grid_is_settable(self):
        self.assertEqual(state.quantise(12.0, 5.0), 2)
        self.assertEqual(state.quantise(13.0, 5.0), 3)


class CanonicalTests(unittest.TestCase):
    def test_the_start_point_cannot_matter(self):
        base = state.canonical_loop(SQUARE)
        for turn in range(4):
            self.assertEqual(state.canonical_loop(rotate(SQUARE, turn)), base)

    def test_the_winding_cannot_matter(self):
        self.assertEqual(state.canonical_loop(list(reversed(SQUARE))),
                         state.canonical_loop(SQUARE))

    def test_the_loop_order_cannot_matter(self):
        hole = [(200.0, 200.0), (400.0, 200.0), (400.0, 400.0), (200.0, 400.0)]
        self.assertEqual(state.canonical_loops([SQUARE, hole]),
                         state.canonical_loops([hole, SQUARE]))

    def test_a_wall_split_into_two_collinear_segments_hashes_the_same(self):
        split = [(0.0, 0.0), (500.0, 0.0), (1000.0, 0.0),
                 (1000.0, 1000.0), (0.0, 1000.0)]
        self.assertEqual(state.canonical_loop(split), state.canonical_loop(SQUARE))

    def test_noise_below_the_grid_is_absorbed(self):
        jittered = [(0.4, -0.3), (1000.2, 0.1), (999.6, 1000.4), (0.2, 999.8)]
        self.assertEqual(state.canonical_loop(jittered), state.canonical_loop(SQUARE))

    def test_a_degenerate_loop_comes_back_short_not_crashing(self):
        self.assertEqual(len(state.canonical_loop([(0.0, 0.0), (1.0, 1.0)])), 2)
        self.assertEqual(state.canonical_loops([[(0.0, 0.0), (1.0, 1.0)]]), ())


class FingerprintTests(unittest.TestCase):
    def test_a_translation_keeps_the_shape_and_changes_the_place(self):
        here = state.fingerprints([SQUARE])
        there = state.fingerprints([[(x + 5000.0, y + 3000.0) for x, y in SQUARE]])
        self.assertEqual(here["shape"], there["shape"])
        self.assertNotEqual(here["abs"], there["abs"])
        verdict, sentence = state.compare(here, there)
        self.assertEqual(verdict, "moved")
        self.assertIn(u"5000 mm east", sentence)
        self.assertIn(u"3000 mm north", sentence)

    def test_moving_one_vertex_is_an_edit(self):
        moved = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1050.0), (0.0, 1000.0)]
        verdict, sentence = state.compare(state.fingerprints([SQUARE]),
                                          state.fingerprints([moved]))
        self.assertEqual(verdict, "edited")
        self.assertIn(u"Reshaped", sentence)

    def test_the_same_loop_twice_is_unchanged(self):
        verdict, sentence = state.compare(state.fingerprints([SQUARE]),
                                          state.fingerprints([rotate(SQUARE, 2)]))
        self.assertEqual(verdict, "unchanged")
        self.assertEqual(sentence, u"")

    def test_area_subtracts_the_hole(self):
        hole = [(200.0, 200.0), (400.0, 200.0), (400.0, 400.0), (200.0, 400.0)]
        self.assertEqual(state.fingerprints([SQUARE])["area_mm2"], 1000000)
        self.assertEqual(state.fingerprints([SQUARE, hole])["area_mm2"], 1000000 - 40000)

    def test_nothing_to_compare_says_so(self):
        verdict, sentence = state.compare({}, state.fingerprints([SQUARE]))
        self.assertEqual(verdict, "unknown")
        self.assertIn(u"nothing to compare", sentence)


class TessellationTests(unittest.TestCase):
    def test_an_arc_samples_identically_every_time(self):
        record = ("A", 0.0, 0.0, 1000.0, 0.0, 500.0, 500.0)
        first = state.tessellate_loop([record])
        second = state.tessellate_loop([record])
        self.assertEqual(first, second)
        self.assertGreater(len(first), 4)
        self.assertAlmostEqual(first[0][0], 0.0, places=6)
        self.assertAlmostEqual(first[-1][0], 1000.0, places=6)

    def test_a_flat_arc_degrades_to_its_chord(self):
        flat = state.tessellate_loop([("A", 0.0, 0.0, 1000.0, 0.0, 500.0, 0.0)])
        self.assertEqual(flat, [(0.0, 0.0), (1000.0, 0.0)])

    def test_a_finer_sag_gives_more_points(self):
        record = ("A", 0.0, 0.0, 1000.0, 0.0, 500.0, 500.0)
        coarse = state.tessellate_loop([record], sag_mm=50.0)
        fine = state.tessellate_loop([record], sag_mm=0.5)
        self.assertGreater(len(fine), len(coarse))

    def test_tessellated_records_pass_straight_through(self):
        points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
        self.assertEqual(state.tessellate_loop([("T", points)]), points)

    def test_shared_vertices_are_not_repeated(self):
        records = [("L", 0.0, 0.0, 10.0, 0.0), ("L", 10.0, 0.0, 10.0, 10.0)]
        self.assertEqual(state.tessellate_loop(records),
                         [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)])


class RepairTests(unittest.TestCase):
    def test_a_clean_loop_passes(self):
        records, code, sentence, notes = state.repair_loop(loop_records(SQUARE))
        self.assertEqual(code, "")
        self.assertEqual(len(records), 4)
        self.assertEqual(notes, [])

    def test_a_zero_length_segment_is_dropped(self):
        records = loop_records(SQUARE)
        records.insert(2, ("L", 1000.0, 0.0, 1000.0, 0.0))
        kept, code, _sentence, notes = state.repair_loop(records)
        self.assertEqual(code, "")
        self.assertEqual(len(kept), 4)
        self.assertIn("short_segment", notes)

    def test_a_small_gap_is_closed_by_moving_the_endpoint_never_by_a_bridge(self):
        """A bridge half a millimetre long is itself a curve Revit refuses -
        the room would fail on exactly the repair meant to save it."""
        records = loop_records(SQUARE)
        records[1] = ("L", 1000.5, 0.0, 1000.0, 1000.0)
        kept, code, _sentence, notes = state.repair_loop(records)
        self.assertEqual(code, "")
        self.assertIn("gap_bridged", notes)
        self.assertEqual(len(kept), 4)
        self.assertEqual((kept[1][1], kept[1][2]), (1000.0, 0.0))
        for record in kept:
            start, end = state._record_ends(record)
            self.assertGreaterEqual(state._distance(start, end), state.SHORT_MM)

    def test_a_closing_gap_is_closed_the_same_way(self):
        records = loop_records(SQUARE)
        records[-1] = ("L", 0.0, 1000.0, 0.0, 0.6)
        kept, code, _sentence, notes = state.repair_loop(records)
        self.assertEqual(code, "")
        self.assertEqual((kept[-1][3], kept[-1][4]), (0.0, 0.0))

    def test_a_spur_goes_with_its_partner_and_leaves_no_gap(self):
        """A stub wall inside the room: the boundary walks in and straight
        back out. Dropping only the return leg used to leave a gap the width
        of the stub, and the loop was refused."""
        records = [("L", 0.0, 0.0, 400.0, 0.0),
                   ("L", 400.0, 0.0, 400.0, 300.0),   # in
                   ("L", 400.0, 300.0, 400.0, 0.0),   # and out
                   ("L", 400.0, 0.0, 1000.0, 0.0),
                   ("L", 1000.0, 0.0, 1000.0, 1000.0),
                   ("L", 1000.0, 1000.0, 0.0, 1000.0),
                   ("L", 0.0, 1000.0, 0.0, 0.0)]
        kept, code, _sentence, notes = state.repair_loop(records)
        self.assertEqual(code, "")
        self.assertIn("spur_removed", notes)
        self.assertEqual(len(kept), 5)
        for index in range(len(kept)):
            _s, end = state._record_ends(kept[index])
            start, _e = state._record_ends(kept[(index + 1) % len(kept)])
            self.assertTrue(state._close(end, start, 1e-6))

    def test_a_two_leg_spur_is_peeled_back(self):
        records = [("L", 0.0, 0.0, 400.0, 0.0),
                   ("L", 400.0, 0.0, 400.0, 300.0),
                   ("L", 400.0, 300.0, 500.0, 300.0),
                   ("L", 500.0, 300.0, 400.0, 300.0),
                   ("L", 400.0, 300.0, 400.0, 0.0),
                   ("L", 400.0, 0.0, 1000.0, 0.0),
                   ("L", 1000.0, 0.0, 1000.0, 1000.0),
                   ("L", 1000.0, 1000.0, 0.0, 1000.0),
                   ("L", 0.0, 1000.0, 0.0, 0.0)]
        kept, code, _sentence, notes = state.repair_loop(records)
        self.assertEqual(code, "")
        self.assertEqual(notes.count("spur_removed"), 2)
        self.assertEqual(len(kept), 5)

    def test_the_sessions_own_short_tolerance_is_honoured(self):
        records = loop_records(SQUARE)
        records.insert(1, ("L", 1000.0, 0.0, 1000.0, 1.0))
        records[2] = ("L", 1000.0, 1.0, 1000.0, 1000.0)
        kept, code, _s, notes = state.repair_loop(records, gap_budget_mm=2.5, short_mm=1.25)
        self.assertEqual(code, "")
        self.assertIn("short_segment", notes)
        self.assertEqual(len(kept), 4)

    def test_a_wide_gap_refuses_the_loop_by_name(self):
        records = loop_records(SQUARE)
        records[1] = ("L", 1005.0, 0.0, 1000.0, 1000.0)
        kept, code, sentence, _notes = state.repair_loop(records)
        self.assertEqual(kept, [])
        self.assertEqual(code, "gap_too_large")
        self.assertIn(u"5 mm gap", sentence)

    def test_a_reversed_duplicate_from_a_separation_line_is_dropped(self):
        records = loop_records(SQUARE)
        records.insert(1, ("L", 1000.0, 0.0, 0.0, 0.0))
        kept, code, _sentence, notes = state.repair_loop(records)
        self.assertEqual(code, "")
        self.assertIn("reversed_duplicate", notes)

    def test_an_open_loop_is_refused(self):
        records = loop_records(SQUARE)[:-1]
        records[0] = ("L", 0.0, 500.0, 1000.0, 0.0)
        kept, code, _sentence, _notes = state.repair_loop(records)
        self.assertEqual(kept, [])
        self.assertEqual(code, "loop_open")

    def test_too_few_segments_is_an_open_loop(self):
        kept, code, _s, _n = state.repair_loop([("L", 0.0, 0.0, 1.0, 0.0)])
        self.assertEqual(code, "loop_open")


class SimplifiedOutlineTests(unittest.TestCase):
    def test_a_clean_polygon_comes_back_as_it_is(self):
        self.assertEqual(state.simplified_outline(SQUARE), SQUARE)

    def test_near_duplicates_collinear_points_and_spikes_go(self):
        messy = [(0.0, 0.0), (0.3, 0.0), (500.0, 0.0), (1000.0, 0.0), (1000.0, 500.0),
                 (1300.0, 500.0), (1000.0, 500.0), (1000.0, 1000.0), (0.0, 1000.0),
                 (0.0, 0.2)]
        self.assertEqual(state.simplified_outline(messy), SQUARE)

    def test_too_little_left_is_nothing_rather_than_a_sliver(self):
        self.assertEqual(state.simplified_outline([(0.0, 0.0), (1000.0, 0.0), (0.2, 0.0)]), [])


class ShapeTests(unittest.TestCase):
    def test_a_hole_is_told_from_the_room(self):
        hole = [(200.0, 200.0), (400.0, 200.0), (400.0, 400.0), (200.0, 400.0)]
        found = state.classify_loops([state.canonical_loop(hole), state.canonical_loop(SQUARE)])
        self.assertEqual(len(found["outer"]), 1)
        self.assertEqual(len(found["inner"]), 1)
        self.assertEqual(found["outer"][0], state.canonical_loop(SQUARE))

    def test_two_separate_parts_are_both_outer_and_noted(self):
        far = [(5000.0, 0.0), (6000.0, 0.0), (6000.0, 1000.0), (5000.0, 1000.0)]
        found = state.classify_loops([state.canonical_loop(SQUARE), state.canonical_loop(far)])
        self.assertEqual(len(found["outer"]), 2)
        self.assertIn("disjoint_part", found["notes"])

    def test_a_bowtie_is_caught_before_revit_sees_it(self):
        bowtie = [(0, 0), (1000, 1000), (1000, 0), (0, 1000)]
        intersecting, tested = state.is_self_intersecting(bowtie)
        self.assertTrue(intersecting)
        self.assertTrue(tested)

    def test_a_square_does_not_intersect_itself(self):
        intersecting, tested = state.is_self_intersecting(state.canonical_loop(SQUARE))
        self.assertFalse(intersecting)
        self.assertTrue(tested)

    def test_a_huge_loop_is_left_untested_and_says_so(self):
        big = [(float(i), float(i * i % 97)) for i in range(state.SELF_INTERSECT_CAP + 5)]
        intersecting, tested = state.is_self_intersecting(big)
        self.assertFalse(intersecting)
        self.assertFalse(tested)


class GateTests(unittest.TestCase):
    def test_plan_views_pass_and_everything_else_is_named(self):
        for name in ("FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan"):
            self.assertTrue(state.view_verdict(name, False)[0], name)
        for name, code in (("Section", "view_plane_vertical"),
                           ("Elevation", "view_plane_vertical"),
                           ("DraftingView", "view_no_model_plane"),
                           ("Legend", "view_no_model_plane"),
                           ("ThreeD", "view_no_detail_items"),
                           ("Schedule", "view_no_detail_items"),
                           ("DrawingSheet", "view_no_detail_items")):
            ok, found, sentence = state.view_verdict(name, False)
            self.assertFalse(ok, name)
            self.assertEqual(found, code, name)
            self.assertTrue(sentence)

    def test_a_template_is_refused_whatever_its_type(self):
        ok, code, _sentence = state.view_verdict("FloorPlan", True)
        self.assertFalse(ok)
        self.assertEqual(code, "view_is_template")

    def test_ceiling_plans_are_offered_but_not_ticked(self):
        self.assertIn("CeilingPlan", state.PLAN_VIEW_TYPES)
        self.assertIn("CeilingPlan", state.UNTICKED_VIEW_TYPES)

    def test_room_gates(self):
        good = {"placed": True, "area": 12.0}
        self.assertTrue(state.room_verdict(good)[0])
        self.assertEqual(state.room_verdict({"placed": False, "area": 0.0})[1], "not_placed")
        self.assertEqual(state.room_verdict({"placed": True, "area": 0.0})[1], "not_enclosed")
        self.assertEqual(state.room_verdict({"placed": True, "area": 5.0, "redundant": True})[1],
                         "redundant")
        option = {"placed": True, "area": 5.0, "design_option": u"Scheme B"}
        ok, code, sentence = state.room_verdict(option)
        self.assertFalse(ok)
        self.assertEqual(code, "in_design_option")
        self.assertIn(u"Scheme B", sentence)
        self.assertTrue(state.room_verdict(option, include_design_options=True)[0])

    def test_a_view_draws_its_own_level_and_phase(self):
        view = {"level_key": "L1", "phase_name": u"New Construction", "name": u"Level 1"}
        self.assertTrue(state.view_shows_room(view, {"level_key": "L1",
                                                     "phase_name": u"New Construction"})[0])
        self.assertEqual(state.view_shows_room(view, {"level_key": "L2",
                                                      "phase_name": u"New Construction"})[1],
                         "wrong_level")
        self.assertEqual(state.view_shows_room(view, {"level_key": "L1",
                                                      "phase_name": u"Existing"})[1], "wrong_phase")
        self.assertEqual(state.view_shows_room({"level_key": None}, {})[1], "view_no_level")

    def test_every_code_has_a_sentence(self):
        """A skip must never reach a person as a bare token."""
        for code, sentence in state.CODE_SENTENCES.items():
            self.assertTrue(sentence, code)
            self.assertTrue(state.sentence_for(code))


def make_view(uid="v1", name=u"Level 1", level="L1", phase=u"New Construction",
              view_type="FloorPlan", template=False):
    return {"uid": uid, "id": 100, "name": name, "view_type": view_type,
            "is_template": template, "level_key": level, "level_name": u"Level 1",
            "phase_name": phase}


def make_room(uid="r1", number=u"101", name=u"Office", level="L1",
              phase=u"New Construction", link_uid=u"", **extra):
    room = {"uid": uid, "id": 500, "number": number, "name": name, "placed": True,
            "area": 20.0, "level_key": level, "phase_name": phase,
            "link_uid": link_uid, "link_title": u"Arch.rvt" if link_uid else u""}
    room.update(extra)
    return room


def make_boundary():
    return {"loops": [SQUARE], "fingerprints": state.fingerprints([SQUARE]), "code": "",
            "sentence": u""}


class PlanTests(unittest.TestCase):
    def config(self, **extra):
        base = {"kind": "room", "boundary_location": "Finish", "boundary_source": "document",
                "grid_mm": 1.0, "region_type": {"id": 9, "name": u"Solid grey"}}
        base.update(extra)
        return base

    def test_one_room_one_view_makes_one_item(self):
        plan = state.build_plan(self.config(), [make_view()], [make_room()],
                                {"r1": make_boundary()}, {})
        self.assertEqual(len(plan["items"]), 1)
        item = plan["items"][0]
        self.assertEqual(item["action"], "create")
        self.assertEqual(item["key"], state.pair_key("v1", "r1", u""))
        self.assertIn(u"101 Office", item["title"])
        self.assertEqual(plan["counts"]["pairs"], 1)

    def test_every_pair_is_either_an_item_or_a_named_skip(self):
        """The accounting rule: nothing may fall between the two."""
        views = [make_view(), make_view("v2", u"Level 2", level="L2"),
                 make_view("v3", u"Section A", view_type="Section")]
        rooms = [make_room(), make_room("r2", u"102", level="L2"),
                 make_room("r3", u"103", placed=False, area=0.0)]
        plan = state.build_plan(self.config(), views, rooms,
                                {"r1": make_boundary(), "r2": make_boundary(),
                                 "r3": make_boundary()}, {})
        pair_skips = [skip for skip in plan["skips"] if skip["scope"] == u"pair"]
        self.assertEqual(len(plan["items"]) + len(pair_skips) + plan["counts"]["not_visible"],
                         plan["counts"]["pairs"])
        # Nothing is known about what the views show, so the level decides:
        # each plan draws its own room, the other-level pair is not offered.
        self.assertEqual(len(plan["items"]), 2)
        self.assertEqual(plan["counts"]["not_visible"], 2)
        for skip in plan["skips"]:
            self.assertTrue(skip["reason"], skip["code"])
        self.assertFalse([skip for skip in pair_skips if skip["code"] in ("wrong_level", "wrong_phase")])

    def test_a_section_view_is_skipped_once_not_once_per_room(self):
        plan = state.build_plan(self.config(), [make_view("v9", u"Section", view_type="Section")],
                                [make_room(), make_room("r2")],
                                {"r1": make_boundary(), "r2": make_boundary()}, {})
        view_skips = [skip for skip in plan["skips"] if skip["scope"] == u"view"]
        self.assertEqual(len(view_skips), 1)
        self.assertEqual(plan["counts"]["pairs"], 0)

    def test_an_existing_region_is_skipped_unless_replacing(self):
        existing = {state.pair_key("v1", "r1", u""): {"region_id": 77}}
        plan = state.build_plan(self.config(), [make_view()], [make_room()],
                                {"r1": make_boundary()}, existing)
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["skips"][0]["code"], "already_drawn")
        self.assertTrue(plan["skips"][0]["quiet"])

        plan = state.build_plan(self.config(replace_existing=True), [make_view()], [make_room()],
                                {"r1": make_boundary()}, existing)
        self.assertEqual(plan["items"][0]["action"], "replace")
        self.assertEqual(plan["items"][0]["replaces_region_id"], 77)

    def test_a_boundary_that_could_not_be_read_names_its_reason(self):
        broken = {"loops": [], "code": "gap_too_large", "sentence": u"a 5 mm gap"}
        plan = state.build_plan(self.config(), [make_view()], [make_room()], {"r1": broken}, {})
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["skips"][0]["code"], "gap_too_large")

    def test_linked_rooms_carry_their_link_into_the_key_and_title(self):
        room = make_room(link_uid=u"lnk-1")
        plan = state.build_plan(self.config(), [make_view()], [room], {"r1": make_boundary()}, {})
        item = plan["items"][0]
        self.assertEqual(item["link_uid"], u"lnk-1")
        self.assertTrue(item["key"].startswith(u"lnk-1|"))
        self.assertIn(u"Arch.rvt", item["title"])

    def test_a_big_batch_asks_and_a_huge_one_refuses(self):
        counts = {"create": state.ACK_PAIRS + 1, "replace": 0}
        self.assertTrue(any(ask["key"] == "large_batch" for ask in state.acknowledgements(counts)))
        self.assertEqual(state.acknowledgements({"create": 10, "replace": 0}), [])
        self.assertTrue(any(ask["key"] == "replace"
                            for ask in state.acknowledgements({"create": 0, "replace": 3})))
        self.assertTrue(state.refusal({"create": state.MAX_PAIRS + 1, "replace": 0}))
        self.assertEqual(state.refusal({"create": 5, "replace": 0}), u"")

    def test_the_summary_names_the_measurement_rule(self):
        plan = state.build_plan(self.config(), [make_view()], [make_room()],
                                {"r1": make_boundary()}, {})
        summary = state.plan_summary(plan)
        self.assertIn(u"wall finish", summary)
        self.assertIn(u"One undo step", summary)


def make_record(**extra):
    record = state.make_record(
        {"room_uid": "r1", "room_number": u"101", "room_name": u"Office",
         "view_uid": "v1", "view_name": u"Level 1", "level_name": u"Level 1",
         "phase_name": u"New Construction", "link_uid": u"", "link_title": u""},
        state.fingerprints([SQUARE]), state.fingerprints([SQUARE]),
        {"kind": "room", "boundary_location": "Finish", "grid_mm": 1.0},
        created_utc=u"2026-09-10T00:00:00Z")
    record.update(extra)
    return record


def live_entry(record, region_uid="reg-1", owner_view="v1", fingerprints=None, readable=True):
    return {"region_id": 900, "region_uid": region_uid, "owner_view_uid": owner_view,
            "owner_view_id": 100, "record": record, "readable": readable,
            "fingerprints": fingerprints if fingerprints is not None
            else state.fingerprints([SQUARE])}


def rooms_now(fingerprints=None, key=u"|r1"):
    return {key: {"fingerprints": fingerprints if fingerprints is not None
                  else state.fingerprints([SQUARE]),
                  "level_key": "L1", "phase_name": u"New Construction"}}


def views_now():
    return {"v1": make_view()}


def bucket_of(report, key):
    for bucket in report["buckets"]:
        for item in bucket["items"]:
            if item["key"] == key:
                return bucket["key"], item
    raise AssertionError("%s is in no bucket" % key)


class DriftTests(unittest.TestCase):
    def test_a_region_that_matches_its_room_is_in_step(self):
        report = state.classify_drift([live_entry(make_record())], rooms_now(), views_now())
        self.assertEqual(bucket_of(report, "reg-1")[0], "in_sync")
        self.assertEqual(report["problem_count"], 0)

    def test_a_moved_wall_shows_as_the_room_changing(self):
        bigger = [(0.0, 0.0), (1500.0, 0.0), (1500.0, 1000.0), (0.0, 1000.0)]
        report = state.classify_drift([live_entry(make_record())],
                                      rooms_now(state.fingerprints([bigger])), views_now())
        bucket, item = bucket_of(report, "reg-1")
        self.assertEqual(bucket, "room_moved")
        self.assertTrue(item["can_update"])
        self.assertEqual(report["problem_count"], 1)

    def test_a_hand_edited_region_is_told_from_a_moved_one(self):
        reshaped = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1400.0), (0.0, 1000.0)]
        report = state.classify_drift(
            [live_entry(make_record(), fingerprints=state.fingerprints([reshaped]))],
            rooms_now(), views_now())
        bucket, item = bucket_of(report, "reg-1")
        self.assertEqual(bucket, "region_edited")
        self.assertTrue(item["can_update"])

        shifted = [(x + 250.0, y) for x, y in SQUARE]
        report = state.classify_drift(
            [live_entry(make_record(), fingerprints=state.fingerprints([shifted]))],
            rooms_now(), views_now())
        bucket, item = bucket_of(report, "reg-1")
        self.assertEqual(bucket, "region_moved")
        self.assertIn(u"250 mm east", item["detail"])

    def test_both_changing_says_redrawing_discards_the_edit(self):
        bigger = [(0.0, 0.0), (1500.0, 0.0), (1500.0, 1000.0), (0.0, 1000.0)]
        reshaped = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1400.0), (0.0, 1000.0)]
        report = state.classify_drift(
            [live_entry(make_record(), fingerprints=state.fingerprints([reshaped]))],
            rooms_now(state.fingerprints([bigger])), views_now())
        bucket, item = bucket_of(report, "reg-1")
        self.assertEqual(bucket, "both_changed")
        self.assertIn(u"discard the edit", item["detail"])

    def test_a_deleted_room_leaves_a_named_orphan(self):
        report = state.classify_drift([live_entry(make_record())], {}, views_now())
        bucket, item = bucket_of(report, "reg-1")
        self.assertEqual(bucket, "orphan_room")
        self.assertIn(u"101 Office", item["detail"])
        self.assertTrue(item["can_delete"])
        self.assertFalse(item["can_update"])

    def test_a_region_copied_to_another_view_is_never_trusted(self):
        report = state.classify_drift([live_entry(make_record(), owner_view="v2")],
                                      rooms_now(), {"v1": make_view(), "v2": make_view("v2")})
        bucket, item = bucket_of(report, "reg-1")
        self.assertEqual(bucket, "copied_region")
        self.assertTrue(item["can_delete"])
        self.assertFalse(item["can_update"])

    def test_the_second_region_for_one_room_is_the_duplicate(self):
        old = make_record()
        new = make_record(created_utc=u"2026-09-11T00:00:00Z")
        report = state.classify_drift(
            [live_entry(old, "reg-1"), live_entry(new, "reg-2")], rooms_now(), views_now())
        self.assertEqual(bucket_of(report, "reg-1")[0], "in_sync")
        self.assertEqual(bucket_of(report, "reg-2")[0], "duplicate")

    def test_a_view_that_no_longer_shows_the_room_cannot_be_updated(self):
        """What the view shows decides, not the level: a room the view no
        longer displays is an orphan, and one it displays from another level
        is judged like any other."""
        entry = live_entry(make_record())
        entry["room_visible"] = False
        report = state.classify_drift([entry], rooms_now(), views_now())
        bucket, item = bucket_of(report, "reg-1")
        self.assertEqual(bucket, "orphan_view")
        self.assertFalse(item["can_update"])
        self.assertTrue(item["can_delete"])
        entry = live_entry(make_record())
        entry["room_visible"] = True
        report = state.classify_drift([entry], rooms_now(), {"v1": make_view(level="L2")})
        self.assertEqual(bucket_of(report, "reg-1")[0], "in_sync")

    def test_an_unloaded_link_is_named_and_not_judged(self):
        record = make_record(link_uid=u"lnk-1", link_title=u"Arch.rvt")
        report = state.classify_drift([live_entry(record)], {}, views_now(), loaded_links=[])
        self.assertEqual(bucket_of(report, "reg-1")[0], "link_not_loaded")

    def test_a_source_left_out_of_the_run_is_not_an_orphan(self):
        record = make_record(link_uid=u"lnk-1", link_title=u"Arch.rvt")
        report = state.classify_drift([live_entry(record)], {}, views_now(),
                                      loaded_links=[u"lnk-1"], run_sources=[state.HOST_KEY])
        self.assertEqual(bucket_of(report, "reg-1")[0], "source_not_in_run")

    def test_an_unreadable_region_is_not_judged(self):
        report = state.classify_drift([live_entry(make_record(), readable=False)],
                                      rooms_now(), views_now())
        self.assertEqual(bucket_of(report, "reg-1")[0], "unreadable")

    def test_unknown_is_never_read_as_in_step(self):
        """The bug the first Revit run found: a record written before Revit
        had finished the sketch carried an empty region digest, and every
        later hand edit read as "in step". Now the outlines decide, and a
        side with nothing to compare is said so."""
        stale = make_record()
        stale["region"] = {}
        reshaped = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1400.0), (0.0, 1000.0)]
        report = state.classify_drift(
            [live_entry(stale, fingerprints=state.fingerprints([reshaped]))],
            rooms_now(), views_now())
        bucket, item = bucket_of(report, "reg-1")
        self.assertIn(bucket, state.DRIFT_PROBLEMS)
        self.assertTrue(item["can_update"])
        self.assertIn(u"mm", item["detail"])
        # Nothing to compare at all: named, never filed as in step.
        report = state.classify_drift(
            [live_entry(stale, fingerprints=state.fingerprints([]))], rooms_now(), views_now())
        self.assertEqual(bucket_of(report, "reg-1")[0], "unreadable")

    def test_an_outline_that_matches_with_a_stale_record_is_in_step_and_says_so(self):
        stale = make_record()
        stale["region"] = {}
        report = state.classify_drift([live_entry(stale)], rooms_now(), views_now())
        bucket, item = bucket_of(report, "reg-1")
        self.assertEqual(bucket, "in_sync")
        self.assertIn(u"record is behind", item["detail"])
        self.assertNotIn("can_accept", item)

    def test_the_verdict_is_the_outline_not_the_record(self):
        """A region reshaped so the stored digests cannot say who moved still
        reads as drifted, with the gap in millimetres."""
        record = make_record()
        record["room"] = {}
        record["region"] = {}
        reshaped = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1400.0), (0.0, 1000.0)]
        report = state.classify_drift(
            [live_entry(record, fingerprints=state.fingerprints([reshaped]))],
            rooms_now(), views_now())
        bucket, item = bucket_of(report, "reg-1")
        self.assertEqual(bucket, "drifted")
        self.assertEqual(int(item["deviation_mm"]), 400)
        self.assertTrue(item["can_update"])

    def test_a_sub_tolerance_wobble_is_in_step(self):
        wobbly = [(0.4, -0.6), (1000.9, 0.3), (999.6, 1000.7), (0.2, 1000.1)]
        report = state.classify_drift(
            [live_entry(make_record(), fingerprints=state.fingerprints([wobbly]))],
            rooms_now(), views_now())
        self.assertEqual(bucket_of(report, "reg-1")[0], "in_sync")

    def test_an_accepted_difference_leaves_the_tally_and_stays_accepted(self):
        """Permanent: whatever the room or the region do next, the row stays
        accepted until somebody reopens it."""
        bigger = [(0.0, 0.0), (1500.0, 0.0), (1500.0, 1000.0), (0.0, 1000.0)]
        live = [live_entry(make_record())]
        drifted = state.classify_drift(live, rooms_now(state.fingerprints([bigger])), views_now())
        self.assertEqual(drifted["problem_count"], 1)
        aside = state.classify_drift(live, rooms_now(state.fingerprints([bigger])), views_now(),
                                     accepted=["reg-1"])
        bucket, item = bucket_of(aside, "reg-1")
        self.assertEqual(bucket, "accepted")
        self.assertEqual(aside["problem_count"], 0)
        self.assertEqual(aside["accepted_count"], 1)
        self.assertTrue(item["is_accepted"])
        self.assertFalse(item["can_update"])
        # The room moves again, and the region is dragged: still accepted.
        larger = [(0.0, 0.0), (2500.0, 0.0), (2500.0, 1000.0), (0.0, 1000.0)]
        shifted = [(x + 900.0, y) for x, y in SQUARE]
        again = state.classify_drift(
            [live_entry(make_record(), fingerprints=state.fingerprints([shifted]))],
            rooms_now(state.fingerprints([larger])), views_now(), accepted=["reg-1"])
        self.assertEqual(bucket_of(again, "reg-1")[0], "accepted")
        self.assertIn(u"accepted", state.drift_summary(again))

    def test_a_problem_row_can_be_accepted_and_an_in_step_one_has_nothing_to_accept(self):
        for key, _title, problem in state.DRIFT_BUCKETS:
            if problem:
                self.assertIn(key, state.DRIFT_PROBLEMS)
        self.assertNotIn("in_sync", state.DRIFT_PROBLEMS)
        self.assertNotIn("accepted", state.DRIFT_PROBLEMS)

    def test_a_whole_link_drifting_at_once_is_said_once(self):
        bigger = state.fingerprints([[(0.0, 0.0), (1500.0, 0.0), (1500.0, 1000.0), (0.0, 1000.0)]])
        live = []
        rooms = {}
        for index in range(6):
            uid = "r%d" % index
            record = make_record(link_uid=u"lnk-1", link_title=u"Arch.rvt", room_uid=uid)
            live.append(live_entry(record, "reg-%d" % index))
            rooms[state.room_key(u"lnk-1", uid)] = {
                "fingerprints": bigger, "level_key": "L1", "phase_name": u"New Construction"}
        report = state.classify_drift(live, rooms, views_now(), loaded_links=[u"lnk-1"])
        self.assertEqual(report["counts"]["room_moved"], 6)
        self.assertTrue(any(u"moved or reloaded" in note for note in report["notes"]))

    def test_the_summary_reads_the_report(self):
        report = state.classify_drift([live_entry(make_record())], rooms_now(), views_now())
        self.assertIn(u"in step", state.drift_summary(report))
        self.assertIn(u"No region", state.drift_summary({}))


class RecordTests(unittest.TestCase):
    def test_a_record_round_trips_through_the_entity_text(self):
        record = make_record()
        restored = state.decode(state.encode(record))
        self.assertEqual(restored["room_number"], u"101")
        self.assertEqual(restored["room"]["abs"], record["room"]["abs"])
        self.assertEqual(restored["v"], state.RECORD_VERSION)

    def test_unicode_room_names_survive(self):
        record = make_record(room_name=u"Bureau d'études étage")
        restored = state.decode(state.encode(record))
        self.assertEqual(restored["room_name"], u"Bureau d'études étage")

    def test_junk_decodes_to_nothing_rather_than_throwing(self):
        self.assertEqual(state.decode("{not json"), {})
        self.assertEqual(state.decode(""), {})
        self.assertEqual(state.decode("[1,2]"), {})
        self.assertEqual(state.decode('{"room": 5}')["room"], {})

    def test_a_linked_record_remembers_which_link(self):
        record = state.make_record({"link_uid": u"lnk-1", "link_title": u"Arch.rvt",
                                    "room_uid": "r1"}, {}, {}, {"kind": "room"})
        self.assertEqual(record["source"], state.SOURCE_LINK)
        self.assertEqual(state.make_record({"room_uid": "r1"}, {}, {}, {})["source"],
                         state.SOURCE_HOST)


class NotesTests(unittest.TestCase):
    def test_the_notes_say_the_rules_out_loud(self):
        notes = state.scan_notes({}, {"boundary_source": "document", "boundary_location": "Center"})
        joined = u" ".join(notes)
        self.assertIn(u"wall centre", joined)
        self.assertIn(u"rooms it actually shows", joined)
        self.assertIn(u"2 mm", joined)
        self.assertIn(u"Sync to Central", joined)
        self.assertIn(u"group", joined)

    def test_an_override_says_it_differs_from_the_model(self):
        notes = state.scan_notes({}, {"boundary_source": "override", "boundary_location": "Finish"})
        self.assertTrue(any(u"override" in note for note in notes))


class SettingsTests(unittest.TestCase):
    def test_defaults_and_normalisation(self):
        defaults = settings_mod.default_settings()
        self.assertEqual(defaults["kind"], state.KIND_ROOM)
        self.assertEqual(defaults["scope"], state.SCOPE_ACTIVE)
        self.assertEqual(settings_mod.normalize(None), defaults)
        rough = settings_mod.normalize({"kind": "nonsense", "grid_mm": 900, "scope": "x",
                                        "source_keys": [u"b", u"a", u"a"], "junk": 1})
        self.assertEqual(rough["kind"], state.KIND_ROOM)
        self.assertEqual(rough["grid_mm"], 25)
        self.assertEqual(rough["scope"], state.SCOPE_ACTIVE)
        self.assertEqual(rough["source_keys"], [u"a", u"b"])
        self.assertNotIn("junk", rough)

    def test_the_boundary_override_is_one_of_the_four(self):
        self.assertEqual(settings_mod.normalize({"boundary_override": "Nope"})["boundary_override"],
                         "Finish")
        for name in state.BOUNDARY_LOCATIONS:
            self.assertEqual(settings_mod.normalize({"boundary_override": name})["boundary_override"],
                             name)


def source_row(key=u"Arch.rvt", rooms=12, spaces=0, loaded=True, host=False):
    return {"key": key, "title": key, "uid": u"" if host else u"L1", "loaded": loaded,
            "is_host": host, "category": u"Sources", "count": rooms,
            "room_count": rooms, "space_count": spaces}


class SetupTests(unittest.TestCase):
    """The rule that decides which sources and views arrive ticked."""

    def test_a_source_with_rooms_is_ticked_and_says_how_many(self):
        ticked, reason = state.source_rule(source_row(rooms=12))
        self.assertTrue(ticked)
        self.assertIn(u"12", reason)

    def test_a_source_with_none_of_this_kind_is_not_ticked(self):
        ticked, reason = state.source_rule(source_row(rooms=0))
        self.assertFalse(ticked)
        self.assertTrue(reason)

    def test_an_unloaded_link_is_never_ticked_and_says_why(self):
        ticked, reason = state.source_rule(source_row(loaded=False))
        self.assertFalse(ticked)
        self.assertEqual(reason, u"not loaded")

    def test_the_mep_case_ticks_itself_without_being_told(self):
        """The spaces are in this model and the rooms are in the link, so
        each kind picks up whichever source actually holds it."""
        rows = [source_row(key=state.HOST_KEY, rooms=0, spaces=40, host=True),
                source_row(key=u"Arch.rvt", rooms=120, spaces=0)]
        for row in rows:
            row["count"] = row["space_count"]
        state.retick_sources(rows)
        self.assertEqual([row["is_checked"] for row in rows], [True, False])
        for row in rows:
            row["count"] = row["room_count"]
        state.retick_sources(rows)
        self.assertEqual([row["is_checked"] for row in rows], [False, True])

    def test_a_source_the_user_touched_survives_the_kind_changing(self):
        rows = [source_row(key=u"Arch.rvt", rooms=0, spaces=0)]
        rows[0]["is_checked"] = True
        rows[0]["touched"] = True
        state.retick_sources(rows)
        self.assertTrue(rows[0]["is_checked"])

    def test_a_ceiling_plan_is_offered_but_not_ticked_and_says_why(self):
        ticked, reason = state.view_rule({"view_type": "CeilingPlan", "default_ticked": False})
        self.assertFalse(ticked)
        self.assertIn(u"ceiling edge", reason)
        self.assertTrue(state.view_rule({"view_type": "FloorPlan", "default_ticked": True})[0])

    def test_a_source_ticked_last_week_stays_ticked_and_a_new_one_follows_the_rule(self):
        settings = {"source_keys": [u"Old.rvt"], "unticked_source_keys": []}
        rows = state.preselect_sources([source_row(key=u"Old.rvt", rooms=0),
                                        source_row(key=u"New.rvt", rooms=5)], settings)
        by_key = dict((row["key"], row) for row in rows)
        self.assertTrue(by_key[u"Old.rvt"]["is_checked"])
        self.assertEqual(by_key[u"Old.rvt"]["reason"], u"ticked before")
        self.assertTrue(by_key[u"New.rvt"]["is_checked"])

    def test_a_source_absent_from_this_model_is_remembered_not_forgotten(self):
        """An architectural link unloaded for one job must still be ticked
        when it comes back next week."""
        settings = {"source_keys": [u"Gone.rvt", u"Here.rvt"], "unticked_source_keys": []}
        rows = state.preselect_sources([source_row(key=u"Here.rvt", rooms=5)], settings)
        folded = state.apply_setup(settings, rows, [], {})
        self.assertIn(u"Gone.rvt", folded["source_keys"])
        self.assertIn(u"Here.rvt", folded["source_keys"])

    def test_unticking_a_source_the_rule_would_tick_is_remembered(self):
        settings = {"source_keys": [], "unticked_source_keys": []}
        rows = state.preselect_sources([source_row(key=u"Arch.rvt", rooms=5)], settings)
        rows[0]["is_checked"] = False
        folded = state.apply_setup(settings, rows, [], {})
        self.assertEqual(folded["unticked_source_keys"], [u"Arch.rvt"])
        self.assertEqual(folded["source_keys"], [])

    def test_apply_setup_folds_the_views_too(self):
        settings = {"view_names": [], "unticked_view_names": []}
        rows = state.preselect_views(
            [{"key": u"L1 Power", "name": u"L1 Power", "view_type": "FloorPlan",
              "category": u"Floor plans", "default_ticked": True}], settings)
        folded = state.apply_setup(settings, [], rows, {"kind": state.KIND_SPACE})
        self.assertEqual(folded["view_names"], [u"L1 Power"])
        self.assertEqual(folded["kind"], state.KIND_SPACE)

    def test_the_config_carries_what_the_plan_needs(self):
        config = state.config_from_settings(
            {"kind": state.KIND_SPACE, "replace_existing": True, "grid_mm": 5},
            "CoreBoundary", {"id": 9, "name": u"Solid"})
        self.assertEqual(config["kind"], state.KIND_SPACE)
        self.assertEqual(config["boundary_location"], "CoreBoundary")
        self.assertTrue(config["replace_existing"])
        self.assertEqual(config["grid_mm"], 5.0)
        self.assertEqual(config["region_type"]["name"], u"Solid")


class LocationTests(unittest.TestCase):
    def test_the_documents_own_setting_is_used_and_said_out_loud(self):
        name, source, sentence = state.effective_location(
            {"boundary_source": "document"}, "CoreBoundary", "document", u"")
        self.assertEqual((name, source), ("CoreBoundary", "document"))
        self.assertIn(u"This model computes to", sentence)

    def test_a_note_from_the_model_is_carried_into_the_sentence(self):
        _name, _source, sentence = state.effective_location(
            {"boundary_source": "document"}, "Finish", "default", u"could not be read")
        self.assertIn(u"could not be read", sentence)

    def test_an_override_wins_and_says_it_is_an_override(self):
        name, source, sentence = state.effective_location(
            {"boundary_source": "override", "boundary_override": "Center"},
            "Finish", "document", u"")
        self.assertEqual((name, source), ("Center", "override"))
        self.assertIn(u"Overridden", sentence)

    def test_an_unknown_override_falls_back_rather_than_being_passed_on(self):
        name, _source, _sentence = state.effective_location(
            {"boundary_source": "override", "boundary_override": "Nonsense"},
            "Finish", "document", u"")
        self.assertEqual(name, "Finish")


class RecordedLocationTests(unittest.TestCase):
    """A region is judged by the rule it was drawn with, not today's."""

    def test_changing_the_models_setting_does_not_invent_drift(self):
        drawn = state.fingerprints([SQUARE])
        moved = state.fingerprints([[(x + 900.0, y) for x, y in SQUARE]])
        record = make_record(boundary_location="Finish")
        record["room"] = drawn
        entry = live_entry(record, fingerprints=state.fingerprints([SQUARE]))
        # The model now computes to Center, so the room measures somewhere
        # else - but this region was drawn to Finish, and that is what counts.
        rooms = {u"|r1": {"level_key": "L1", "phase_name": u"New Construction",
                          "fingerprints": moved,
                          "by_location": {"Finish": drawn, "Center": moved}}}
        report = state.classify_drift([entry], rooms, views_now())
        self.assertEqual(bucket_of(report, u"reg-1")[0], "in_sync")

    def test_a_room_measured_at_its_own_location_still_reports_a_real_change(self):
        drawn = state.fingerprints([SQUARE])
        reshaped = state.fingerprints([[(0.0, 0.0), (1400.0, 0.0), (1400.0, 1000.0),
                                        (0.0, 1000.0)]])
        record = make_record(boundary_location="Finish")
        record["room"] = drawn
        entry = live_entry(record, fingerprints=state.fingerprints([SQUARE]))
        rooms = {u"|r1": {"level_key": "L1", "phase_name": u"New Construction",
                          "fingerprints": drawn,
                          "by_location": {"Finish": reshaped}}}
        report = state.classify_drift([entry], rooms, views_now())
        self.assertEqual(bucket_of(report, u"reg-1")[0], "room_moved")

    def test_a_record_with_no_matching_location_falls_back_to_the_plain_read(self):
        drawn = state.fingerprints([SQUARE])
        record = make_record(boundary_location="CoreCenter")
        record["room"] = drawn
        entry = live_entry(record, fingerprints=state.fingerprints([SQUARE]))
        rooms = {u"|r1": {"level_key": "L1", "phase_name": u"New Construction",
                          "fingerprints": drawn, "by_location": {"Finish": drawn}}}
        report = state.classify_drift([entry], rooms, views_now())
        self.assertEqual(bucket_of(report, u"reg-1")[0], "in_sync")


class SyncTests(unittest.TestCase):
    def test_the_deviation_is_symmetric_and_in_millimetres(self):
        a = state.fingerprints([SQUARE])["outline"]
        bigger = [(0.0, 0.0), (1300.0, 0.0), (1300.0, 1000.0), (0.0, 1000.0)]
        b = state.fingerprints([bigger])["outline"]
        forward, _s = state.outline_deviation(a, b)
        backward, _s = state.outline_deviation(b, a)
        self.assertEqual(forward, backward)
        self.assertEqual(int(forward), 300)

    def test_a_loop_count_mismatch_is_infinite_not_a_number(self):
        a = state.fingerprints([SQUARE])["outline"]
        hole = [(200.0, 200.0), (300.0, 200.0), (300.0, 300.0), (200.0, 300.0)]
        b = state.fingerprints([SQUARE, hole])["outline"]
        deviation, sentence = state.outline_deviation(a, b)
        self.assertEqual(deviation, float("inf"))
        self.assertIn(u"loop", sentence)

    def test_within_tolerance_is_in_step_and_over_it_is_drifted(self):
        room = state.fingerprints([SQUARE])
        nudged = state.fingerprints([[(x + 1.0, y) for x, y in SQUARE]])
        self.assertEqual(state.sync_verdict(nudged, room)[0], "in_sync")
        shoved = state.fingerprints([[(x + 3.0, y) for x, y in SQUARE]])
        verdict, deviation, sentence = state.sync_verdict(shoved, room)
        self.assertEqual(verdict, "drifted")
        self.assertEqual(int(deviation), 3)
        self.assertIn(u"3 mm", sentence)

    def test_digests_alone_still_decide_when_no_outline_travelled(self):
        room = state.digests_only(state.fingerprints([SQUARE]))
        same = state.digests_only(state.fingerprints([SQUARE]))
        other = state.digests_only(state.fingerprints([[(x + 500.0, y) for x, y in SQUARE]]))
        self.assertEqual(state.sync_verdict(same, room)[0], "in_sync")
        self.assertEqual(state.sync_verdict(other, room)[0], "drifted")
        self.assertEqual(state.sync_verdict({}, room)[0], "unknown")

    def test_a_record_never_carries_the_outline(self):
        fingerprint = state.fingerprints([SQUARE])
        self.assertIn("outline", fingerprint)
        kept = state.digests_only(fingerprint)
        self.assertNotIn("outline", kept)
        self.assertEqual(kept["abs"], fingerprint["abs"])
        record = make_record()
        self.assertNotIn("outline", record["room"])
        self.assertNotIn("outline", record["region"])

    def test_the_cut_plane_rule(self):
        self.assertTrue(state.cut_plane_visible(0.0, 3000.0, 1200.0))
        self.assertTrue(state.cut_plane_visible(0.0, 3000.0, 0.0))
        self.assertFalse(state.cut_plane_visible(0.0, 3000.0, 3000.0))
        self.assertFalse(state.cut_plane_visible(0.0, 3000.0, -100.0))
        self.assertFalse(state.cut_plane_visible(3000.0, 0.0, 1200.0))


class VisibilityPlanTests(unittest.TestCase):
    def config(self):
        return {"kind": "room", "boundary_location": "Finish", "boundary_source": "document",
                "grid_mm": 1.0, "region_type": {"id": 9, "name": u"Solid grey"}}

    def _plan(self, visible):
        views = [make_view()]
        rooms = [make_room(), make_room("r2", u"102", level="L2"),
                 make_room("r3", u"103", level="L2")]
        boundaries = {"r1": make_boundary(), "r2": make_boundary(), "r3": make_boundary()}
        return state.build_plan(self.config(), views, rooms, boundaries, {},
                                visibility={"v1": {"visible": visible, "note": u""}})

    def test_a_room_the_view_shows_from_another_level_is_offered_ticked_under_its_heading(self):
        plan = self._plan(set(["r1", "r2"]))
        by_uid = dict((item["room_uid"], item) for item in plan["items"])
        self.assertEqual(sorted(by_uid), ["r1", "r2"])
        self.assertTrue(by_uid["r1"]["default_ticked"])
        self.assertEqual(by_uid["r1"]["category"], state.CATEGORY_THIS_LEVEL)
        # Ticked like the rest - the heading says why it is there.
        self.assertTrue(by_uid["r2"]["default_ticked"])
        self.assertEqual(by_uid["r2"]["category"], state.CATEGORY_OTHER_LEVEL)
        self.assertTrue(by_uid["r2"]["category_reason"])
        self.assertEqual(plan["counts"]["other_level"], 1)
        self.assertNotIn(u"unticked", state.plan_summary(plan))

    def test_a_room_the_view_hides_is_counted_and_said_once_never_rowed(self):
        plan = self._plan(set(["r1", "r2"]))
        self.assertEqual(plan["counts"]["not_visible"], 1)
        self.assertFalse([skip for skip in plan["skips"] if skip["scope"] == u"pair"])
        self.assertTrue([note for note in plan["notes"] if u"not visible in Level 1" in note])
        self.assertIn(u"not visible", state.plan_summary(plan))

    def test_a_room_on_the_views_level_that_the_view_hides_is_not_planned(self):
        """Visibility settings win over the level: hidden is hidden."""
        plan = self._plan(set(["r2"]))
        self.assertEqual([item["room_uid"] for item in plan["items"]], ["r2"])
        self.assertEqual(plan["counts"]["not_visible"], 2)

    def test_a_view_that_could_not_be_read_offers_its_own_level_and_says_so(self):
        views = [make_view()]
        rooms = [make_room(), make_room("r2", u"102", level="L2")]
        boundaries = {"r1": make_boundary(), "r2": make_boundary()}
        plan = state.build_plan(self.config(), views, rooms, boundaries, {},
                                visibility={"v1": {"visible": None, "note": u"could not read"}})
        self.assertEqual([item["room_uid"] for item in plan["items"]], ["r1"])
        self.assertIn(u"could not read", plan["notes"])

    def test_the_accounting_rule_holds_with_visibility(self):
        plan = self._plan(set(["r1", "r2"]))
        pair_skips = [skip for skip in plan["skips"] if skip["scope"] == u"pair"]
        self.assertEqual(len(plan["items"]) + len(pair_skips) + plan["counts"]["not_visible"],
                         plan["counts"]["pairs"])


class PickedRoomTests(unittest.TestCase):
    def config(self, subject):
        return {"kind": "room", "boundary_location": "Finish", "boundary_source": "document",
                "grid_mm": 1.0, "region_type": {"id": 9, "name": u"Solid grey"},
                "subject": subject}

    def test_a_picked_room_is_ticked_whatever_level_it_sits_on(self):
        views = [make_view()]
        rooms = [make_room("r2", u"102", level="L2")]
        plan = state.build_plan(self.config(state.SUBJECT_ONE), views, rooms,
                                {"r2": make_boundary()}, {},
                                visibility={"v1": {"visible": set(["r2"]), "note": u""}})
        self.assertEqual(len(plan["items"]), 1)
        self.assertTrue(plan["items"][0]["default_ticked"])
        self.assertEqual(plan["items"][0]["category"], state.CATEGORY_OTHER_LEVEL)
        # And when nothing is known about the view, a pick is still honoured.
        plan = state.build_plan(self.config(state.SUBJECT_ONE), views, rooms,
                                {"r2": make_boundary()}, {},
                                visibility={"v1": {"visible": None, "note": u""}})
        self.assertEqual(len(plan["items"]), 1)
        self.assertTrue(plan["items"][0]["default_ticked"])

    def test_a_picked_room_the_view_hides_is_still_not_drawn(self):
        views = [make_view()]
        rooms = [make_room("r2", u"102", level="L2")]
        plan = state.build_plan(self.config(state.SUBJECT_ONE), views, rooms,
                                {"r2": make_boundary()}, {},
                                visibility={"v1": {"visible": set(), "note": u""}})
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["counts"]["not_visible"], 1)

    def test_everything_the_preview_offers_starts_ticked(self):
        views = [make_view()]
        rooms = [make_room("r2", u"102", level="L2")]
        plan = state.build_plan(self.config(state.SUBJECT_ALL), views, rooms,
                                {"r2": make_boundary()}, {},
                                visibility={"v1": {"visible": set(["r2"]), "note": u""}})
        self.assertTrue(all(item["default_ticked"] for item in plan["items"]))

    def test_a_views_note_is_carried_even_when_it_answered(self):
        """A link whose Rooms category the view hides answers "none" - and
        the reason must reach the preview, not just the count."""
        views = [make_view()]
        rooms = [make_room("r1", link_uid=u"L1")]
        note = state.sentence_for("rooms_hidden_in_view", u"Rooms", u"Level 1", u"Arch.rvt")
        plan = state.build_plan(self.config(state.SUBJECT_ALL), views, rooms,
                                {"r1": make_boundary()}, {},
                                visibility={"v1": {"visible": set(), "note": note}})
        self.assertIn(note, plan["notes"])
        self.assertIn(u"Arch.rvt", note)
        self.assertIn(u"view template", note)


class SpaceSourceTests(unittest.TestCase):
    def test_spaces_come_from_this_model_by_default(self):
        host = source_row(key=state.HOST_KEY, rooms=0, spaces=40, host=True)
        link = source_row(key=u"Arch.rvt", rooms=120, spaces=30)
        for row in (host, link):
            row["count"] = row["space_count"]
        self.assertTrue(state.source_rule(host, state.KIND_SPACE)[0])
        ticked, reason = state.source_rule(link, state.KIND_SPACE)
        self.assertFalse(ticked)
        self.assertIn(u"this model", reason)
        # Rooms keep the count rule: the link has them, so it is ticked.
        for row in (host, link):
            row["count"] = row["room_count"]
        self.assertTrue(state.source_rule(link, state.KIND_ROOM)[0])

    def test_preselect_and_fold_use_the_kind_from_the_settings(self):
        settings = {"kind": state.KIND_SPACE, "source_keys": [], "unticked_source_keys": []}
        rows = state.preselect_sources(
            [source_row(key=state.HOST_KEY, rooms=0, spaces=4, host=True),
             source_row(key=u"Arch.rvt", rooms=0, spaces=9)], settings)
        for row in rows:
            row["count"] = row["space_count"]
        state.retick_sources(rows, state.KIND_SPACE)
        self.assertEqual([row["is_checked"] for row in rows], [True, False])
        # Unticking the link is what the rule would do anyway, so it is not
        # remembered as a deliberate choice.
        folded = state.apply_setup(settings, rows, [], {"kind": state.KIND_SPACE})
        self.assertEqual(folded["unticked_source_keys"], [])
        self.assertEqual(folded["source_keys"], [state.HOST_KEY])


if __name__ == "__main__":
    unittest.main()
