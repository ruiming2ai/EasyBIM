"""Damper Check's verdicts, pinned on synthetic duct networks.

Every network below is a snapshot the way ``damper_check_revit.scan`` hands
one over - plain dicts, ids as ints - so the tests read like the drawings:
a terminal, some duct, a tee, a damper, an AHU.
"""

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Damper Check.pushbutton"

# The state and settings modules import their shared halves from
# ``easybim`` (pure Python, no Revit), which pyRevit puts on the path at
# runtime and the tests put there here.
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


state = _load("damper_check_state")
settings_mod = _load("damper_check_settings")


# ------------------------------------------------------------ snapshot kit

VCD = u"Damper - Volume : 200x200"
OBD = u"Diffuser - OBD : 600x600"
DIFF = u"Diffuser : 600x600"
VAV = u"VAV Box : Size 8"
AHU = u"AHU : Unit 1"


class Net(object):
    """Builds a snapshot from ``add`` calls and ``join`` calls."""

    def __init__(self):
        self.elements = {}
        self.scope = None
        self.meta = {}

    def add(self, element_id, kind, key=u"", size=1.0, level=u"L1", space=u"",
            system_name=u"SA-1", system_class=u"SupplyAir", error=u"",
            ends=2):
        family, _sep, type_name = key.partition(u" : ")
        connectors = []
        for index in range(ends):
            connectors.append({"id": index, "type": u"End", "connected": False,
                               "partners": []})
        if kind in ("duct", "flex"):
            connectors.append({"id": ends, "type": u"Curve", "connected": False,
                               "partners": []})
        self.elements[element_id] = {
            "id": element_id, "kind": kind, "category": u"", "family": family,
            "type": type_name, "type_key": key, "type_id": 0, "level": level,
            "space": space, "system_name": system_name,
            "system_class": system_class, "size": size,
            "connectors": connectors, "error": error, "name": u"",
        }
        return self

    def join(self, a, a_conn, b, b_conn):
        """Connect connector ``a_conn`` of ``a`` with ``b_conn`` of ``b``."""
        for owner, own, other, other_conn in ((a, a_conn, b, b_conn), (b, b_conn, a, a_conn)):
            for connector in self.elements[owner]["connectors"]:
                if connector["id"] == own:
                    connector["connected"] = True
                    connector["partners"].append([other, other_conn])
        return self

    def snapshot(self):
        return {"schema": 1, "elements": self.elements, "meta": dict(self.meta),
                "scope_terminal_ids": self.scope}


def config(dampers=(VCD,), threshold=1, classes=None, scope="model"):
    return state.config_from_settings({
        "damper_types": list(dampers), "threshold": threshold,
        "system_classes": list(classes) if classes is not None else None,
        "scope": scope,
    })


def bucket_of(report, terminal_id):
    for bucket in report["buckets"]:
        for item in bucket["items"]:
            if item["terminal_id"] == terminal_id:
                return bucket["key"], item
    raise AssertionError("terminal %s is in no bucket" % terminal_id)


def linear_run(with_damper=True):
    """AHU(1) - duct(2) - [damper(3)] - duct(4) - terminal(5)."""
    net = Net()
    net.add(1, "equipment", AHU, size=3.0, ends=1)
    net.add(2, "duct", u"Rect : Std", size=2.0)
    net.add(4, "duct", u"Rect : Std", size=0.5)
    net.add(5, "terminal", DIFF, ends=1)
    net.join(1, 0, 2, 0)
    if with_damper:
        net.add(3, "accessory", VCD)
        net.join(2, 1, 3, 0).join(3, 1, 4, 0)
    else:
        net.join(2, 1, 4, 0)
    net.join(4, 1, 5, 0)
    return net


def tee_run(damper_upstream=True):
    """AHU(1) - duct(2) - [damper(3)] - tee(6) - duct(7)-term(8) / duct(9)-term(10)."""
    net = Net()
    net.add(1, "equipment", AHU, size=3.0, ends=1)
    net.add(2, "duct", u"Rect : Std", size=2.0)
    net.add(6, "fitting", u"Tee : Std", ends=3)
    net.add(7, "duct", u"Round : Std", size=0.5)
    net.add(8, "terminal", DIFF, ends=1)
    net.add(9, "duct", u"Round : Std", size=0.5)
    net.add(10, "terminal", DIFF, ends=1)
    net.join(1, 0, 2, 0)
    if damper_upstream:
        net.add(3, "accessory", VCD)
        net.join(2, 1, 3, 0).join(3, 1, 6, 0)
    else:
        net.join(2, 1, 6, 0)
    net.join(6, 1, 7, 0).join(7, 1, 8, 0)
    net.join(6, 2, 9, 0).join(9, 1, 10, 0)
    return net


# ------------------------------------------------------------------ tests


class LinearRunTests(unittest.TestCase):
    def test_a_terminal_behind_its_own_damper_is_covered(self):
        analysis = state.analyze(linear_run().snapshot(), config())
        report = state.classify(analysis, 1)
        key, item = bucket_of(report, 5)
        self.assertEqual(key, "covered_single")
        self.assertEqual(item["show_ids"], [5, 4, 3])
        self.assertIn(u"serves 1 terminal", item["detail"])

    def test_a_terminal_with_no_damper_reaches_the_equipment(self):
        analysis = state.analyze(linear_run(with_damper=False).snapshot(), config())
        report = state.classify(analysis, 1)
        key, item = bucket_of(report, 5)
        self.assertEqual(key, "no_damper_equipment")
        self.assertEqual(item["show_ids"], [5, 4, 2, 1])
        self.assertIn(u"AHU", item["detail"])

    def test_equipment_ticked_as_isolating_covers_the_run(self):
        analysis = state.analyze(linear_run(with_damper=False).snapshot(), config(dampers=(AHU,)))
        report = state.classify(analysis, 1)
        self.assertEqual(bucket_of(report, 5)[0], "covered_by_equipment")

    def test_a_terminal_type_ticked_as_damper_is_integral(self):
        net = linear_run(with_damper=False)
        net.elements[5]["type_key"] = OBD
        analysis = state.analyze(net.snapshot(), config(dampers=(OBD,)))
        report = state.classify(analysis, 1)
        key, item = bucket_of(report, 5)
        self.assertEqual(key, "covered_integral")
        self.assertEqual(item["show_ids"], [5])


class ThresholdTests(unittest.TestCase):
    def test_a_shared_damper_moves_between_buckets_without_a_rescan(self):
        analysis = state.analyze(tee_run().snapshot(), config())
        strict = state.classify(analysis, 1)
        self.assertEqual(bucket_of(strict, 8)[0], "damper_too_far")
        self.assertEqual(bucket_of(strict, 10)[0], "damper_too_far")
        self.assertIn(u"serves 2 terminals - limit 1", bucket_of(strict, 8)[1]["detail"])
        lenient = state.classify(analysis, 2)
        self.assertEqual(bucket_of(lenient, 8)[0], "covered_shared")
        self.assertEqual(bucket_of(lenient, 10)[0], "covered_shared")

    def test_without_a_damper_the_junction_names_how_many_it_serves(self):
        analysis = state.analyze(tee_run(damper_upstream=False).snapshot(), config())
        report = state.classify(analysis, 1)
        key, item = bucket_of(report, 8)
        self.assertEqual(key, "no_damper_equipment")
        self.assertIn(u"junction at id 6 serving 2 terminals", item["detail"])
        # The run to select stops at the junction, not the AHU.
        self.assertEqual(item["show_ids"], [8, 7, 6])

    def test_a_trunk_damper_serves_every_terminal(self):
        net = tee_run()
        # Add a third terminal tapped straight off the main duct 2.
        net.add(11, "terminal", DIFF, ends=1)
        net.join(11, 0, 2, 2)  # the duct's Curve connector (id 2)
        analysis = state.analyze(net.snapshot(), config())
        report = state.classify(analysis, 3)
        # Terminal 11 sits above the damper, on the main: no damper at all.
        key, item = bucket_of(report, 11)
        self.assertEqual(key, "no_damper_equipment")
        self.assertEqual(item["show_ids"], [11, 2, 1])
        # The damper still serves the two below it.
        self.assertEqual(bucket_of(report, 8)[0], "covered_shared")

    def test_threshold_is_clamped(self):
        self.assertEqual(state.clamp_threshold(0), 1)
        self.assertEqual(state.clamp_threshold("abc"), 1)
        self.assertEqual(state.clamp_threshold(10 ** 9), state.MAX_THRESHOLD)


class TopologyTests(unittest.TestCase):
    def test_a_tap_on_the_main_parents_the_terminal_to_the_duct(self):
        net = Net()
        net.add(1, "equipment", AHU, size=3.0, ends=1)
        net.add(2, "duct", u"Rect : Main", size=2.0)
        net.add(3, "fitting", u"Tap : Round", ends=2)
        net.add(4, "flex", u"Flex : Round", size=0.5)
        net.add(5, "terminal", DIFF, ends=1)
        net.join(1, 0, 2, 0).join(3, 0, 2, 2).join(3, 1, 4, 0).join(4, 1, 5, 0)
        analysis = state.analyze(net.snapshot(), config())
        trace = analysis["terminals"][0]
        self.assertEqual(trace["path_ids"], [5, 4, 3, 2, 1])
        self.assertEqual(state.classify(analysis, 1)["counts"]["no_damper_equipment"], 1)

    def test_a_capped_stub_on_a_tee_does_not_end_the_branch(self):
        net = Net()
        net.add(1, "equipment", AHU, size=3.0, ends=1)
        net.add(2, "duct", u"Rect : Std", size=2.0)
        net.add(3, "accessory", VCD)
        net.add(6, "fitting", u"Tee : Std", ends=3)
        net.add(7, "duct", u"Round : Std", size=0.5)
        net.add(8, "terminal", DIFF, ends=1)
        net.add(9, "fitting", u"Cap : Round", ends=1)
        net.join(1, 0, 2, 0).join(2, 1, 3, 0).join(3, 1, 6, 0)
        net.join(6, 1, 7, 0).join(7, 1, 8, 0).join(6, 2, 9, 0)
        analysis = state.analyze(net.snapshot(), config())
        report = state.classify(analysis, 1)
        self.assertEqual(bucket_of(report, 8)[0], "covered_single")

    def test_an_unconnected_terminal_is_its_own_bucket(self):
        net = linear_run()
        net.add(20, "terminal", DIFF, ends=1)
        report = state.classify(state.analyze(net.snapshot(), config()), 1)
        self.assertEqual(bucket_of(report, 20)[0], "terminal_unconnected")

    def test_a_branch_hanging_off_an_open_end_says_so(self):
        net = Net()
        net.add(2, "duct", u"Rect : Std", size=2.0)  # open at end 0
        net.add(4, "duct", u"Round : Std", size=0.5)
        net.add(5, "terminal", DIFF, ends=1, system_name=u"", system_class=u"")
        net.join(2, 1, 4, 0).join(4, 1, 5, 0)
        analysis = state.analyze(net.snapshot(), config())
        self.assertEqual(analysis["groups"][0]["root_kind"], "open_end")
        self.assertEqual(analysis["groups"][0]["root_id"], 2)
        report = state.classify(analysis, 1)
        key, item = bucket_of(report, 5)
        self.assertEqual(key, "no_damper_open_end")
        self.assertTrue(item["title"].startswith(u"No system"))
        self.assertEqual(item["system_class"], "Unknown")

    def test_a_closed_island_reaches_no_equipment(self):
        net = Net()
        net.add(2, "duct", u"Rect : Std", size=2.0)
        net.add(9, "fitting", u"Cap : Rect", ends=1)
        net.add(4, "duct", u"Round : Std", size=0.5)
        net.add(5, "terminal", DIFF, ends=1)
        net.join(9, 0, 2, 0).join(2, 1, 4, 0).join(4, 1, 5, 0)
        analysis = state.analyze(net.snapshot(), config())
        self.assertEqual(analysis["groups"][0]["root_kind"], "island")
        report = state.classify(analysis, 1)
        self.assertEqual(bucket_of(report, 5)[0], "no_damper_island")

    def test_a_loop_flags_counts_as_approximate(self):
        net = tee_run()
        # Close a ring: duct 7's far end also joins duct 9 through a cross-tie.
        net.add(12, "duct", u"Round : Tie", size=0.5)
        net.join(7, 1, 12, 0).join(12, 1, 9, 1)
        analysis = state.analyze(net.snapshot(), config())
        self.assertTrue(analysis["groups"][0]["has_loops"])
        report = state.classify(analysis, 1)
        self.assertTrue(all(item["approx"] for bucket in report["buckets"] for item in bucket["items"]))

    def test_equipment_splits_the_network_and_roots_the_downstream_side(self):
        net = Net()
        net.add(1, "equipment", AHU, size=3.0, ends=1)
        net.add(2, "duct", u"Rect : Main", size=2.0)
        net.add(3, "equipment", VAV, size=0.8, ends=2)
        net.add(4, "duct", u"Round : Std", size=0.5)
        net.add(5, "terminal", DIFF, ends=1)
        net.join(1, 0, 2, 0).join(2, 1, 3, 0).join(3, 1, 4, 0).join(4, 1, 5, 0)
        analysis = state.analyze(net.snapshot(), config())
        roots = dict((group["root_id"], group) for group in analysis["groups"])
        self.assertIn(3, roots)  # the VAV roots the downstream run
        self.assertIn(1, roots)  # the AHU roots the main
        self.assertEqual(roots[3]["terminal_count"], 1)
        report = state.classify(analysis, 1)
        key, item = bucket_of(report, 5)
        self.assertEqual(key, "no_damper_equipment")
        self.assertIn(u"VAV", item["detail"])
        # Tick the VAV type and the same run reads as isolated.
        report = state.classify(state.analyze(net.snapshot(), config(dampers=(VAV,))), 1)
        self.assertEqual(bucket_of(report, 5)[0], "covered_by_equipment")

    def test_the_bigger_of_two_attached_equipment_is_the_root(self):
        net = tee_run(damper_upstream=False)
        net.add(13, "equipment", u"Fan : Small", size=0.3, ends=1)
        net.add(14, "duct", u"Round : Small", size=0.3)
        net.add(6, "fitting", u"Tee : Std", ends=4)
        net.join(13, 0, 14, 0).join(14, 1, 6, 3)
        analysis = state.analyze(net.snapshot(), config())
        group = analysis["groups"][0]
        self.assertEqual(group["root_id"], 1)
        self.assertEqual(group["also_attached"], [13])

    def test_a_pass_through_terminal_counts_once_and_climbs_from_itself(self):
        net = Net()
        net.add(1, "equipment", AHU, size=3.0, ends=1)
        net.add(2, "duct", u"Rect : Std", size=2.0)
        net.add(3, "accessory", VCD)
        net.add(5, "terminal", u"Slot : Linear", ends=2)
        net.add(6, "duct", u"Rect : Std", size=0.5)
        net.add(7, "terminal", DIFF, ends=1)
        net.join(1, 0, 2, 0).join(2, 1, 3, 0).join(3, 1, 5, 0).join(5, 1, 6, 0).join(6, 1, 7, 0)
        analysis = state.analyze(net.snapshot(), config())
        report = state.classify(analysis, 2)
        self.assertEqual(bucket_of(report, 5)[0], "covered_shared")
        self.assertEqual(bucket_of(report, 7)[0], "covered_shared")
        self.assertEqual(state.classify(analysis, 1)["counts"]["damper_too_far"], 2)

    def test_a_three_way_damper_covers_both_legs(self):
        net = tee_run(damper_upstream=False)
        net.elements[6]["kind"] = "accessory"
        net.elements[6]["type_key"] = u"Damper - Splitter : Std"
        analysis = state.analyze(net.snapshot(), config(dampers=(u"Damper - Splitter : Std",)))
        report = state.classify(analysis, 2)
        self.assertEqual(report["counts"]["covered_shared"], 2)

    def test_an_unread_partner_still_joins_the_branch(self):
        net = linear_run(with_damper=False)
        # Between duct 2 and duct 4 sits an element the scan never managed
        # to read: both ducts point at it, it points at nothing.
        net.elements[2]["connectors"][1]["partners"] = [[99, 0]]
        net.elements[4]["connectors"][0]["partners"] = [[99, 1]]
        analysis = state.analyze(net.snapshot(), config())
        self.assertIn(99, analysis["skips"]["unreadable_ids"])
        self.assertTrue(analysis["groups"][0]["has_unreadable"])
        trace = analysis["terminals"][0]
        self.assertEqual(trace["path_ids"], [5, 4, 99, 2, 1])
        self.assertEqual(state.classify(analysis, 1)["counts"]["no_damper_equipment"], 1)

    def test_an_unreadable_terminal_is_named_not_judged(self):
        net = linear_run()
        net.elements[5]["error"] = u"ConnectorManager threw"
        report = state.classify(state.analyze(net.snapshot(), config()), 1)
        self.assertEqual(bucket_of(report, 5)[0], "unreadable")


class GuardTests(unittest.TestCase):
    def test_the_node_cap_truncates_instead_of_hanging(self):
        net = linear_run(with_damper=False)
        analysis = state.analyze(net.snapshot(), config(), max_nodes=2)
        self.assertTrue(analysis["groups"][0]["truncated"])
        report = state.classify(analysis, 1)
        self.assertEqual(bucket_of(report, 5)[0], "truncated")

    def test_the_climb_cap_truncates(self):
        analysis = state.analyze(linear_run(with_damper=False).snapshot(), config(), max_climb=1)
        self.assertEqual(state.classify(analysis, 1)["counts"]["truncated"], 1)

    def test_show_ids_are_capped(self):
        trace = {"path_ids": list(range(1000)), "path_under": [1] * 1000, "damper_index": None}
        self.assertEqual(len(state.show_ids_for(trace, 1)), state.SHOW_ID_CAP)

    def test_rows_are_capped_with_the_hidden_count_stated(self):
        shown, hidden = state.cap_items(list(range(state.ROW_CAP + 5)))
        self.assertEqual(len(shown), state.ROW_CAP)
        self.assertEqual(hidden, 5)


class ScopeTests(unittest.TestCase):
    def test_active_view_scope_limits_terminals_but_the_walk_stays_whole(self):
        net = tee_run()
        net.scope = [8]
        analysis = state.analyze(net.snapshot(), config())
        self.assertEqual([trace["id"] for trace in analysis["terminals"]], [8])
        self.assertEqual(analysis["skips"]["out_of_scope_terminals"], 1)
        # Terminal 10 is out of scope but still counted under the damper.
        self.assertEqual(state.classify(analysis, 1)["counts"]["damper_too_far"], 1)

    def test_system_class_chips_filter_terminals(self):
        net = tee_run()
        net.elements[10]["system_class"] = u"ReturnAir"
        analysis = state.analyze(net.snapshot(), config(classes=("SupplyAir",)))
        self.assertEqual([trace["id"] for trace in analysis["terminals"]], [8])
        self.assertEqual(analysis["skips"]["class_filtered_terminals"], 1)

    def test_unknown_classes_normalise_to_the_unknown_chip(self):
        for raw in (u"", u"Fitting", u"Global", u"UndefinedSystemType", None):
            self.assertEqual(state.normalize_system_class(raw), "Unknown")
        self.assertEqual(state.normalize_system_class(u"Supply Air"), "SupplyAir")
        self.assertEqual(state.normalize_system_class(u"ExhaustAir"), "ExhaustAir")

    def test_the_footer_names_what_the_chips_left_out(self):
        self.assertEqual(state.excluded_note(config()), u"")
        self.assertIn(u"Return", state.excluded_note(config(classes=("SupplyAir",))))


class StatsTests(unittest.TestCase):
    def test_a_damper_isolating_nothing_is_counted(self):
        net = tee_run()
        net.add(15, "accessory", VCD)
        net.add(16, "fitting", u"Cap : Round", ends=1)
        net.add(6, "fitting", u"Tee : Std", ends=4)
        net.join(6, 3, 15, 0).join(15, 1, 16, 0)
        # Re-join the tee's other legs (the re-add reset its connectors).
        net.join(6, 0, 3, 1).join(6, 1, 7, 0).join(6, 2, 9, 0)
        analysis = state.analyze(net.snapshot(), config())
        self.assertEqual(analysis["stats"]["dampers"], 2)
        self.assertEqual(analysis["stats"]["idle_dampers"], 1)

    def test_summary_line_reads_the_report(self):
        net = linear_run(with_damper=False)
        net.meta = {"elements_read": 5, "seconds": 0.25}
        analysis = state.analyze(net.snapshot(), config())
        report = state.classify(analysis, 1)
        line = state.summary_line(analysis, report)
        self.assertIn(u"Read 5 elements in 0.2 s", line)
        self.assertIn(u"1 of 1 terminals lack a damper", line)
        self.assertTrue(line.endswith(u"The scan changes nothing in the model."))

    def test_summary_line_names_a_truncated_scan(self):
        net = linear_run()
        net.meta = {"elements_read": 5, "seconds": 90.0, "truncated": True,
                    "truncated_reason": u"90 s budget reached"}
        analysis = state.analyze(net.snapshot(), config())
        line = state.summary_line(analysis, state.classify(analysis, 1))
        self.assertIn(u"Scan truncated: 90 s budget reached", line)
        self.assertIn(u"all 1 terminals in scope are isolated", line)


class IgnoreTests(unittest.TestCase):
    """Findings the reviewer sets aside, and the key that record is kept under."""

    def analysis(self):
        return state.analyze(linear_run(with_damper=False).snapshot(), config())

    def test_the_key_names_the_terminal(self):
        report = state.classify(self.analysis(), 1)
        self.assertEqual(bucket_of(report, 5)[1]["key"], u"terminal:5")

    def test_an_ignored_finding_leaves_the_problem_tally(self):
        analysis = self.analysis()
        plain = state.classify(analysis, 1)
        self.assertEqual((plain["problem_count"], plain["ignored_count"]), (1, 0))
        self.assertEqual(bucket_of(plain, 5)[0], "no_damper_equipment")

        aside = state.classify(analysis, 1, [u"terminal:5"])
        key, item = bucket_of(aside, 5)
        self.assertEqual(key, "ignored")
        self.assertEqual((aside["problem_count"], aside["ignored_count"]), (0, 1))
        self.assertTrue(item["is_ignored"])
        self.assertEqual(item["original_bucket"], "no_damper_equipment")
        self.assertIn(u"Set aside on review", item["detail"])
        self.assertIn(u"No damper between terminal and equipment", item["detail"])
        # The row still carries what it needs to be shown and restored.
        self.assertEqual(item["show_ids"], [5, 4, 2, 1])

    def test_restoring_is_dropping_the_key(self):
        analysis = self.analysis()
        self.assertEqual(bucket_of(state.classify(analysis, 1, []), 5)[0], "no_damper_equipment")

    def test_the_ignored_bucket_is_never_a_problem(self):
        self.assertIn("ignored", [key for key, _title, _problem in state.BUCKETS])
        self.assertNotIn("ignored", state.PROBLEM_BUCKETS)

    def test_a_key_matching_no_finding_is_reported_stale(self):
        report = state.classify(self.analysis(), 1, [u"terminal:999"])
        self.assertEqual(report["stale_ignored"], [u"terminal:999"])
        self.assertEqual(report["ignored_count"], 0)

    def test_the_key_survives_the_verdict_changing(self):
        """Fit the damper and the terminal keeps its name, so a decision made
        last week still points at the same thing."""
        fixed = state.analyze(linear_run(with_damper=True).snapshot(), config())
        key, item = bucket_of(state.classify(fixed, 1), 5)
        self.assertEqual(key, "covered_single")
        self.assertEqual(item["key"], u"terminal:5")
        aside = state.classify(fixed, 1, [u"terminal:5"])
        self.assertEqual(bucket_of(aside, 5)[1]["original_bucket"], "covered_single")

    def test_the_summary_counts_what_was_set_aside(self):
        analysis = self.analysis()
        report = state.classify(analysis, 1, [u"terminal:5"])
        line = state.summary_line(analysis, report)
        self.assertIn(u"1 set aside", line)
        self.assertIn(u"The scan changes nothing in the model.", line)


class SearchTests(unittest.TestCase):
    def test_numbers_match_whole_ids_only(self):
        item = {"terminal_id": 112, "damper_id": None, "title": u"SA-1 · L1 · Diffuser (id 112)",
                "detail": u"", "system_name": u"SA-1", "level": u"L1", "space": u"", "label": u"Diffuser"}
        self.assertFalse(state.matches(u"12", item))
        self.assertTrue(state.matches(u"112", item))

    def test_words_match_by_substring_and_all_tokens_must_hit(self):
        item = {"terminal_id": 5, "damper_id": 3, "title": u"SA-1 · Level 3 · Diffuser (id 5)",
                "detail": u"Nearest damper VCD", "system_name": u"SA-1", "level": u"Level 3",
                "space": u"301 Office", "label": u"Diffuser"}
        self.assertTrue(state.matches(u"office diff", item))
        self.assertFalse(state.matches(u"office kitchen", item))
        self.assertTrue(state.matches(u"3", item))  # the damper id
        self.assertTrue(state.matches(u"", item))

    def test_filter_report_recounts(self):
        analysis = state.analyze(tee_run().snapshot(), config())
        report = state.classify(analysis, 1)
        filtered = state.filter_report(report, u"8")
        self.assertEqual(filtered["counts"]["damper_too_far"], 1)
        self.assertEqual(filtered["problem_count"], 1)
        self.assertEqual(report["counts"]["damper_too_far"], 2)


class ChecklistTests(unittest.TestCase):
    def rows(self):
        return [
            {"type_key": u"Damper - Volume : 200x200", "kind": "accessory", "category": u"Duct Accessories", "count": 3},
            {"type_key": u"Fire Damper : 1hr", "kind": "accessory", "category": u"Duct Accessories", "count": 2},
            {"type_key": u"Silencer : 600", "kind": "accessory", "category": u"Duct Accessories", "count": 1},
            {"type_key": u"Tap with Damper : Round", "kind": "fitting", "category": u"Duct Fittings", "count": 9},
            {"type_key": u"Elbow : Round", "kind": "fitting", "category": u"Duct Fittings", "count": 40},
            {"type_key": u"Diffuser - OBD : 600x600", "kind": "terminal", "category": u"Air Terminals", "count": 12},
            {"type_key": u"VAV with damper : 8", "kind": "equipment", "category": u"Mechanical Equipment", "count": 4},
            {"type_key": u"Generic Damper : GM", "kind": "other", "category": u"Generic Models", "count": 1},
        ]

    def test_keywords_pre_tick_and_exclusions_win(self):
        rows = dict((row["type_key"], row) for row in state.preselect_types(self.rows(), {}))
        self.assertTrue(rows[u"Damper - Volume : 200x200"]["is_checked"])
        self.assertFalse(rows[u"Fire Damper : 1hr"]["is_checked"])
        self.assertIn(u"fire", rows[u"Fire Damper : 1hr"]["reason"])
        self.assertFalse(rows[u"Silencer : 600"]["is_checked"])
        self.assertTrue(rows[u"Tap with Damper : Round"]["is_checked"])
        self.assertFalse(rows[u"Elbow : Round"]["is_checked"])
        self.assertFalse(rows[u"Diffuser - OBD : 600x600"]["is_checked"])  # only "damper" outside accessories
        self.assertFalse(rows[u"VAV with damper : 8"]["is_checked"])  # equipment never pre-ticks
        self.assertTrue(rows[u"Generic Damper : GM"]["is_checked"])

    def test_saved_choices_win_in_both_directions(self):
        settings = {"damper_types": [u"Diffuser - OBD : 600x600"],
                    "unticked_types": [u"Damper - Volume : 200x200"]}
        rows = dict((row["type_key"], row) for row in state.preselect_types(self.rows(), settings))
        self.assertTrue(rows[u"Diffuser - OBD : 600x600"]["is_checked"])
        self.assertEqual(rows[u"Diffuser - OBD : 600x600"]["reason"], u"ticked before")
        self.assertFalse(rows[u"Damper - Volume : 200x200"]["is_checked"])

    def test_groups_put_accessories_first_and_expanded(self):
        groups = state.group_types(state.preselect_types(self.rows(), {}))
        names = [group["category"] for group in groups]
        self.assertEqual(names[:4], [u"Duct Accessories", u"Duct Fittings", u"Air Terminals",
                                     u"Mechanical Equipment"])
        self.assertEqual(names[4], u"Generic Models")
        self.assertTrue(groups[0]["is_expanded"])
        self.assertFalse(any(group["is_expanded"] for group in groups[1:]))
        self.assertEqual(groups[0]["instance_count"], 6)
        self.assertEqual(groups[0]["type_count"], 3)

    def test_apply_setup_keeps_names_from_other_models(self):
        rows = state.preselect_types(self.rows(), {})
        for row in rows:
            row["is_checked"] = row["type_key"] in (u"Fire Damper : 1hr", u"Elbow : Round")
        old = {"damper_types": [u"Other Model Damper : X"], "unticked_types": [u"Other Model VCD : Y"]}
        new = state.apply_setup(old, rows, threshold=4, scope="view", system_classes=["ReturnAir", "SupplyAir"])
        self.assertEqual(new["damper_types"], [u"Elbow : Round", u"Fire Damper : 1hr", u"Other Model Damper : X"])
        # Unticking a keyword hit is remembered; unticking a non-hit needs no note.
        self.assertEqual(new["unticked_types"], [u"Damper - Volume : 200x200", u"Generic Damper : GM",
                                                 u"Other Model VCD : Y", u"Tap with Damper : Round"])
        self.assertEqual(new["threshold"], 4)
        self.assertEqual(new["scope"], "view")
        self.assertEqual(new["system_classes"], ["SupplyAir", "ReturnAir"])

    def test_selection_count_text(self):
        rows = state.preselect_types(self.rows(), {})
        self.assertEqual(state.selection_count_text(rows), u"3 of 8 types ticked as dampers.")


class SettingsTests(unittest.TestCase):
    def test_defaults_when_nothing_is_saved(self):
        settings, note = settings_mod.load(os.path.join(tempfile.gettempdir(), "no-such-file.json"))
        self.assertEqual(settings, settings_mod.default_settings())
        self.assertEqual(note, u"")

    def test_round_trip_through_a_temp_file(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "nested", "settings.json")
        ok, error = settings_mod.save({"threshold": 3, "scope": "view", "damper_types": [u"B : 2", u"A : 1", u"A : 1"],
                                       "system_classes": ["ExhaustAir"], "junk": 1}, path)
        self.assertTrue(ok, error)
        self.assertFalse(os.path.exists(path + ".tmp"))
        loaded, note = settings_mod.load(path)
        self.assertEqual(loaded["threshold"], 3)
        self.assertEqual(loaded["scope"], "view")
        self.assertEqual(loaded["damper_types"], [u"A : 1", u"B : 2"])
        self.assertEqual(loaded["system_classes"], ["ExhaustAir"])
        self.assertNotIn("junk", loaded)
        self.assertEqual(loaded["schema"], settings_mod.SCHEMA_VERSION)

    def test_a_corrupt_file_yields_defaults_and_a_note(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "settings.json")
        with open(path, "w") as handle:
            handle.write("{not json")
        settings, note = settings_mod.load(path)
        self.assertEqual(settings, settings_mod.default_settings())
        self.assertIn(u"could not be read", note)

    def test_normalize_survives_wrong_types(self):
        settings = settings_mod.normalize({"threshold": "x", "scope": 5, "system_classes": "SupplyAir",
                                           "damper_types": "not a list", "unticked_types": None})
        self.assertEqual(settings["threshold"], 1)
        self.assertEqual(settings["scope"], "model")
        self.assertEqual(settings["system_classes"], list(settings_mod.SYSTEM_CLASS_KEYS))
        self.assertEqual(settings["damper_types"], [])
        self.assertEqual(settings_mod.normalize(None), settings_mod.default_settings())

    def test_the_state_and_settings_modules_agree_on_the_chips(self):
        self.assertEqual(tuple(settings_mod.SYSTEM_CLASS_KEYS), state.SYSTEM_CLASS_KEYS)

    def test_the_saved_file_is_plain_json(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "settings.json")
        settings_mod.save(settings_mod.default_settings(), path)
        with open(path) as handle:
            self.assertEqual(json.load(handle)["schema"], 1)


if __name__ == "__main__":
    unittest.main()
