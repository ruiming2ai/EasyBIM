"""Tree and search logic for Circuit Schedule.

The hierarchy is the tool: if the roots, the nesting or the loop guard are
wrong, the browser lies about how the building is fed.  All of it is decided in
``circuit_schedule_state`` off plain dicts, so all of it is checked here without
Revit.
"""

import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "circuit_schedule_state.py"
)

spec = importlib.util.spec_from_file_location("circuit_schedule_state", str(MODULE_PATH))
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


def equipment(element_id, name, is_panel=True, category="Electrical Equipment",
              type_name="225 A"):
    return {
        "id": element_id,
        "name": name,
        "type_name": type_name,
        "category": category,
        "level": "Level 1",
        "is_panel": is_panel,
    }


def load(element_id, name, category="Electrical Fixtures", type_name="Duplex"):
    return equipment(element_id, name, is_panel=False, category=category,
                     type_name=type_name)


def circuit(circuit_id, panel_id, number, element_ids, panel_name="",
            load_name="Load", **extra):
    data = {
        "id": circuit_id,
        "panel_id": panel_id,
        "panel_name": panel_name,
        "circuit_number": number,
        "load_name": load_name,
        "rating": "20 A",
        "poles": "1",
        "voltage": "120 V",
        "apparent_load": "1440 VA",
        "system_type": "Power",
        "element_ids": list(element_ids),
    }
    data.update(extra)
    return data


def snapshot(elements, circuits):
    return {
        "doc_title": "Test.rvt",
        "elements": dict((item["id"], item) for item in elements),
        "circuits": list(circuits),
    }


#: MSB feeds LP-1 on circuit 3; LP-1 feeds two receptacles on circuit 12.
def sample():
    return snapshot(
        [
            equipment(1, "MSB"),
            equipment(2, "LP-1"),
            load(10, "Recept A"),
            load(11, "Recept B"),
        ],
        [
            circuit(100, 1, "3", [2], panel_name="MSB", load_name="LP-1"),
            circuit(200, 2, "12", [10, 11], panel_name="LP-1",
                    load_name="Recept - East"),
        ],
    )


def titles(nodes):
    return [node.title for node in nodes]


def find(roots, predicate):
    for node in state.iter_nodes(roots):
        if predicate(node):
            return node
    return None


def find_kind(roots, kind, title):
    """A circuit is often named after the board it feeds, so both are needed."""
    return find(roots, lambda node: node.node_kind == kind and node.title == title)


def numbers(nodes):
    return [node.number_text for node in nodes]


class BuildTreeTests(unittest.TestCase):
    def test_root_is_the_equipment_nothing_feeds(self):
        roots, _index, _parents = state.build_tree(sample())
        self.assertEqual(titles(roots), ["MSB"])

    def test_fed_panel_is_not_a_root(self):
        roots, _index, _parents = state.build_tree(sample())
        self.assertNotIn("LP-1", titles(roots))

    def test_sub_panel_nests_under_the_circuit_that_feeds_it(self):
        roots, _index, _parents = state.build_tree(sample())
        msb = roots[0]
        feeder = msb.children[0]
        self.assertEqual(feeder.node_kind, state.NODE_CIRCUIT)
        self.assertEqual(len(feeder.children), 1)
        panel = feeder.children[0]
        self.assertEqual(panel.node_kind, state.NODE_EQUIPMENT)
        self.assertEqual(panel.title, "LP-1")
        self.assertEqual(panel.depth, 2)

    def test_end_loads_are_leaves_under_their_branch_circuit(self):
        roots, _index, _parents = state.build_tree(sample())
        panel = roots[0].children[0].children[0]
        branch = panel.children[0]
        self.assertEqual(branch.number_text, "12")
        self.assertEqual([child.node_kind for child in branch.children],
                         [state.NODE_LOAD, state.NODE_LOAD])
        self.assertEqual(branch.children[0].children, [])

    def test_equipment_and_load_are_different_node_kinds(self):
        # This split is what makes a downstream panel look unlike a receptacle.
        roots, _index, _parents = state.build_tree(sample())
        kinds = set(node.node_kind for node in state.iter_nodes(roots))
        self.assertEqual(
            kinds, set([state.NODE_EQUIPMENT, state.NODE_CIRCUIT, state.NODE_LOAD]))

    def test_root_says_it_has_nothing_upstream(self):
        roots, _index, _parents = state.build_tree(sample())
        self.assertEqual(roots[0].subtitle, state.ROOT_SUBTITLE)

    def test_downstream_panel_names_its_feeder(self):
        roots, _index, _parents = state.build_tree(sample())
        panel = roots[0].children[0].children[0]
        self.assertIn("MSB / 3", panel.subtitle)

    def test_circuit_subtitle_carries_the_electrical_readings(self):
        roots, _index, _parents = state.build_tree(sample())
        feeder = roots[0].children[0]
        self.assertIn("20 A", feeder.subtitle)
        self.assertIn("1P", feeder.subtitle)
        self.assertIn("120 V", feeder.subtitle)


class OrderingTests(unittest.TestCase):
    def test_circuit_numbers_sort_naturally(self):
        data = snapshot(
            [equipment(1, "MSB")],
            [circuit(101, 1, "10", []), circuit(102, 1, "2", []),
             circuit(103, 1, "1", [])],
        )
        roots, _index, _parents = state.build_tree(data)
        self.assertEqual(numbers(roots[0].children), ["1", "2", "10"])

    def test_panel_names_sort_naturally(self):
        data = snapshot(
            [equipment(1, "LP-10"), equipment(2, "LP-2")],
            [circuit(101, 1, "1", []), circuit(102, 2, "1", [])],
        )
        roots, _index, _parents = state.build_tree(data)
        self.assertEqual(titles(roots), ["LP-2", "LP-10"])


class EdgeCaseTests(unittest.TestCase):
    def test_a_loop_terminates_and_is_flagged(self):
        # MSB feeds A, A feeds B, B feeds A again.  Without the path guard the
        # recursion never returns and Revit hangs.
        data = snapshot(
            [equipment(1, "MSB"), equipment(2, "A"), equipment(3, "B")],
            [circuit(100, 1, "1", [2], panel_name="MSB"),
             circuit(101, 2, "1", [3], panel_name="A"),
             circuit(102, 3, "1", [2], panel_name="B")],
        )
        roots, _index, _parents = state.build_tree(data)
        cycles = [node for node in state.iter_nodes(roots) if node.is_cycle]
        self.assertTrue(cycles)
        self.assertEqual(cycles[0].children, [])
        self.assertEqual(cycles[0].subtitle, state.CYCLE_SUBTITLE)

    def test_a_board_fed_twice_is_drawn_once_and_referenced_once(self):
        # A double-ended board really has two feeders.  Both must show it, but
        # repeating the whole subtree doubles the tree for no new information.
        data = snapshot(
            [equipment(1, "MSB"), equipment(2, "DP"), load(10, "Recept")],
            [circuit(100, 1, "1", [2], panel_name="MSB"),
             circuit(101, 1, "3", [2], panel_name="MSB"),
             circuit(200, 2, "5", [10], panel_name="DP")],
        )
        roots, _index, _parents = state.build_tree(data)
        boards = [node for node in state.iter_nodes(roots) if node.title == "DP"]
        self.assertEqual(len(boards), 2)
        drawn = [node for node in boards if not node.is_reference]
        referenced = [node for node in boards if node.is_reference]
        self.assertEqual(len(drawn), 1)
        self.assertEqual(len(referenced), 1)
        self.assertTrue(drawn[0].children)
        self.assertEqual(referenced[0].children, [])
        self.assertIn("already shown", referenced[0].subtitle)

    def test_a_reference_node_is_not_a_cycle(self):
        data = snapshot(
            [equipment(1, "MSB"), equipment(2, "DP")],
            [circuit(100, 1, "1", [2]), circuit(101, 1, "3", [2])],
        )
        roots, _index, _parents = state.build_tree(data)
        self.assertFalse(any(node.is_cycle for node in state.iter_nodes(roots)))

    def test_a_chain_deeper_than_the_cap_is_truncated(self):
        # Belt and braces behind the path guard: a guard bug must cost a
        # truncated branch, never a hung Revit.
        count = state.MAX_DEPTH + 10
        elements = [equipment(index, "P{0}".format(index))
                    for index in range(1, count + 1)]
        circuits = [circuit(1000 + index, index, "1", [index + 1])
                    for index in range(1, count)]
        roots, _index, _parents = state.build_tree(snapshot(elements, circuits))
        deepest = max(node.depth for node in state.iter_nodes(roots))
        self.assertLessEqual(deepest, state.MAX_DEPTH)
        self.assertTrue(any(node.is_cycle for node in state.iter_nodes(roots)))

    def test_a_loop_with_no_root_still_produces_a_finite_tree(self):
        data = snapshot(
            [equipment(1, "A"), equipment(2, "B")],
            [circuit(101, 1, "1", [2]), circuit(102, 2, "1", [1])],
        )
        roots, _index, _parents = state.build_tree(data)
        # Everything is fed by something, so nothing qualifies as a root and
        # the browser is empty rather than hung.
        self.assertEqual(roots, [])

    def test_circuit_without_a_panel_lands_in_the_unassigned_bucket(self):
        data = snapshot(
            [equipment(1, "MSB"), load(10, "Recept")],
            [circuit(100, 1, "1", []), circuit(200, None, "7", [10])],
        )
        roots, _index, _parents = state.build_tree(data)
        bucket = roots[-1]
        self.assertEqual(bucket.node_kind, state.NODE_BUCKET)
        self.assertEqual(bucket.title, state.UNASSIGNED_TITLE)
        self.assertEqual(len(bucket.children), 1)
        self.assertEqual(bucket.children[0].number_text, "7")

    def test_circuit_pointing_at_a_missing_panel_is_unassigned(self):
        data = snapshot([load(10, "Recept")],
                        [circuit(200, 999, "7", [10])])
        roots, _index, _parents = state.build_tree(data)
        self.assertEqual(roots[-1].node_kind, state.NODE_BUCKET)

    def test_circuit_with_no_elements_is_a_childless_spare(self):
        data = snapshot([equipment(1, "MSB")], [circuit(100, 1, "5", [])])
        roots, _index, _parents = state.build_tree(data)
        spare = roots[0].children[0]
        self.assertEqual(spare.children, [])
        self.assertEqual(spare.detail, state.SPARE_DETAIL)

    def test_element_on_two_circuits_appears_under_both(self):
        data = snapshot(
            [equipment(1, "MSB"), load(10, "Shared")],
            [circuit(101, 1, "1", [10]), circuit(102, 1, "3", [10])],
        )
        roots, _index, _parents = state.build_tree(data)
        shared = [node for node in state.iter_nodes(roots)
                  if node.title == "Shared"]
        self.assertEqual(len(shared), 2)
        # Distinct keys, or the index would collapse them into one row.
        self.assertEqual(len(set(node.key for node in shared)), 2)

    def test_equipment_that_feeds_nothing_is_not_a_root(self):
        # An uncircuited panel would otherwise head the browser as if it were
        # the service entrance.
        data = snapshot([equipment(1, "MSB"), equipment(2, "Spare board")],
                        [circuit(100, 1, "1", [])])
        roots, _index, _parents = state.build_tree(data)
        self.assertEqual(titles(roots), ["MSB"])

    def test_empty_model_builds_an_empty_tree(self):
        roots, index, parents = state.build_tree(snapshot([], []))
        self.assertEqual(roots, [])
        self.assertEqual(index, {})
        self.assertEqual(parents, {})

    def test_none_snapshot_is_survivable(self):
        roots, _index, _parents = state.build_tree(None)
        self.assertEqual(roots, [])


class BreadcrumbTests(unittest.TestCase):
    def test_breadcrumb_reads_root_first(self):
        roots, _index, parents = state.build_tree(sample())
        recept = find(roots, lambda node: node.title == "Recept A")
        trail = state.breadcrumb(recept, parents, separator=" > ")
        self.assertEqual(trail.split(" > ")[0], "MSB")
        self.assertEqual(trail.split(" > ")[-1], "Recept A")
        self.assertIn("LP-1", trail)

    def test_breadcrumb_of_a_root_is_just_the_root(self):
        roots, _index, parents = state.build_tree(sample())
        self.assertEqual(state.breadcrumb(roots[0], parents), "MSB")

    def test_breadcrumb_of_nothing_is_blank(self):
        self.assertEqual(state.breadcrumb(None, {}), "")


class TokenTests(unittest.TestCase):
    def test_circuit_number_splits_on_every_separator(self):
        self.assertEqual(state.tokens("12,14,16"), ["12", "14", "16"])
        self.assertEqual(state.tokens("LP-1/12"), ["lp", "1", "12"])

    def test_sort_key_reads_the_leading_number(self):
        self.assertEqual(
            sorted(["10", "2", "1A"], key=state.circuit_number_sort_key),
            ["1A", "2", "10"])


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.roots, self.index, self.parents = state.build_tree(sample())

    def visible(self):
        return [node.title for node in state.iter_nodes(self.roots)
                if node.is_visible]

    def matched(self):
        return [node.title for node in state.iter_nodes(self.roots)
                if node.matched]

    def matched_numbers(self):
        return [node.number_text for node in state.iter_nodes(self.roots)
                if node.matched and node.number_text]

    def test_circuit_number_matches_its_own_circuit(self):
        count = state.apply_filter(self.roots, "12")
        self.assertEqual(count, 1)
        self.assertEqual(self.matched_numbers(), ["12"])

    def test_a_longer_number_does_not_match_a_shorter_circuit(self):
        # The reason circuit numbers are tokenised rather than substring-matched:
        # in a large project "12" would otherwise drag in every 112 and 120.
        data = snapshot(
            [equipment(1, "MSB")],
            [circuit(101, 1, "12", []), circuit(102, 1, "112", [])],
        )
        roots, _index, _parents = state.build_tree(data)
        state.apply_filter(roots, "12")
        hits = [node.number_text for node in state.iter_nodes(roots)
                if node.matched]
        self.assertEqual(hits, ["12"])

    def test_multi_number_circuit_matches_each_of_its_numbers(self):
        data = snapshot([equipment(1, "MSB")],
                        [circuit(101, 1, "12,14,16", [])])
        roots, _index, _parents = state.build_tree(data)
        for query in ("12", "14", "16"):
            self.assertEqual(state.apply_filter(roots, query), 1, query)
        self.assertEqual(state.apply_filter(roots, "13"), 0)

    def test_panel_and_number_together(self):
        for query in ("LP-1/12", "LP-1 12"):
            count = state.apply_filter(self.roots, query)
            self.assertEqual(count, 1, query)
            self.assertEqual(self.matched_numbers(), ["12"], query)

    def test_panel_name_alone_matches_case_insensitively(self):
        state.apply_filter(self.roots, "lp-1")
        self.assertTrue(
            find_kind(self.roots, state.NODE_EQUIPMENT, "LP-1").matched)

    def test_load_name_is_searchable(self):
        state.apply_filter(self.roots, "recept a")
        self.assertIn("Recept A", self.matched())

    def test_ancestors_of_a_match_stay_visible_and_open(self):
        state.apply_filter(self.roots, "12")
        self.assertIn("MSB", self.visible())
        self.assertIn("LP-1", self.visible())
        self.assertTrue(self.roots[0].is_expanded)

    def test_unrelated_branches_are_hidden(self):
        data = snapshot(
            [equipment(1, "MSB"), equipment(2, "LP-1"), equipment(3, "LP-2"),
             load(10, "Recept")],
            [circuit(100, 1, "1", [2]), circuit(101, 1, "3", [3]),
             circuit(200, 2, "12", [10])],
        )
        roots, _index, _parents = state.build_tree(data)
        state.apply_filter(roots, "12")
        visible = [node.title for node in state.iter_nodes(roots)
                   if node.is_visible]
        self.assertIn("LP-1", visible)
        self.assertNotIn("LP-2", visible)

    def test_the_number_column_is_only_populated_for_circuits(self):
        for node in state.iter_nodes(self.roots):
            if node.node_kind != state.NODE_CIRCUIT:
                self.assertEqual(node.number_text, u"", node.title)

    def test_a_matched_circuit_keeps_its_loads_reachable(self):
        state.apply_filter(self.roots, "12")
        recept = find(self.roots, lambda node: node.title == "Recept A")
        self.assertTrue(recept.is_visible)

    def test_searching_a_panel_name_lists_that_panel_circuits(self):
        # A circuit carries its panel name, so searching the panel matches its
        # circuits too and the panel opens onto them.  That is the point of
        # searching a panel by name.
        state.apply_filter(self.roots, "lp-1")
        panel = find_kind(self.roots, state.NODE_EQUIPMENT, "LP-1")
        self.assertTrue(panel.matched)
        self.assertTrue(panel.is_expanded)
        self.assertTrue(panel.children[0].matched)

    def test_a_match_on_the_panel_row_alone_leaves_it_closed(self):
        # Nothing below matched, so opening it would bury the hit under every
        # circuit and load the board owns.
        state.apply_filter(self.roots, "225")   # the equipment type name
        panel = find_kind(self.roots, state.NODE_EQUIPMENT, "LP-1")
        self.assertTrue(panel.matched)
        self.assertFalse(panel.is_expanded)
        # The subtree stays reachable, just closed.
        self.assertTrue(find(self.roots, lambda n: n.title == "Recept A").is_visible)

    def test_no_match_hides_everything(self):
        count = state.apply_filter(self.roots, "zzz")
        self.assertEqual(count, 0)
        self.assertEqual(self.visible(), [])

    def test_clearing_the_query_restores_the_tree(self):
        state.apply_filter(self.roots, "zzz")
        state.apply_filter(self.roots, "")
        self.assertEqual(len(self.visible()),
                         len(list(state.iter_nodes(self.roots))))
        self.assertEqual(self.matched(), [])
        self.assertTrue(self.roots[0].is_expanded)
        panel = find(self.roots, lambda node: node.title == "LP-1")
        self.assertFalse(panel.is_expanded)

    def test_blank_query_is_not_a_match(self):
        node = self.roots[0]
        self.assertFalse(state.node_matches(node, "   "))


class ExpansionTests(unittest.TestCase):
    def test_expand_all_then_collapse_all(self):
        roots, _index, _parents = state.build_tree(sample())
        state.set_expanded(roots, True)
        self.assertTrue(all(node.is_expanded for node in state.iter_nodes(roots)
                            if node.children))
        state.set_expanded(roots, False)
        self.assertFalse(any(node.is_expanded for node in state.iter_nodes(roots)))

    def test_expand_to_a_depth_stops_there(self):
        # max_depth is the deepest row that opens, so 1 shows the source board
        # and its circuits but not the boards those circuits feed.
        roots, _index, _parents = state.build_tree(sample())
        state.set_expanded(roots, True, max_depth=1)
        self.assertTrue(roots[0].is_expanded)
        self.assertTrue(roots[0].children[0].is_expanded)
        self.assertFalse(roots[0].children[0].children[0].is_expanded)

    def test_expansion_survives_a_refresh(self):
        roots, _index, _parents = state.build_tree(sample())
        state.set_expanded(roots, True)
        saved = state.expansion_state(roots)

        rebuilt, _index, _parents = state.build_tree(sample())
        state.restore_expansion(rebuilt, saved)
        self.assertTrue(all(node.is_expanded for node in state.iter_nodes(rebuilt)
                            if node.children))


class PresentationTests(unittest.TestCase):
    """The glyph and colour live in the state layer so they can be tested.

    XAML only decides weight and case; what each row *is* is decided here.
    """

    def test_each_kind_gets_its_own_glyph(self):
        roots, _index, _parents = state.build_tree(sample())
        seen = dict((node.node_kind, node.glyph)
                    for node in state.iter_nodes(roots))
        self.assertEqual(seen[state.NODE_EQUIPMENT],
                         state.GLYPHS[state.NODE_EQUIPMENT])
        self.assertEqual(seen[state.NODE_CIRCUIT], state.GLYPHS[state.NODE_CIRCUIT])
        self.assertEqual(seen[state.NODE_LOAD], state.GLYPHS[state.NODE_LOAD])
        self.assertEqual(len(set(seen.values())), 3)

    def test_the_accent_darkens_towards_the_source(self):
        roots, _index, _parents = state.build_tree(sample())
        self.assertEqual(roots[0].accent, state.DEPTH_ACCENTS[0])
        self.assertEqual(roots[0].children[0].accent, state.DEPTH_ACCENTS[1])

    def test_the_accent_ramp_clamps_past_its_end(self):
        self.assertEqual(state.accent_for_depth(999), state.DEPTH_ACCENTS[-1])
        self.assertEqual(state.accent_for_depth(-1), state.DEPTH_ACCENTS[0])

    def test_every_accent_is_a_hex_brush_string(self):
        # Bound straight to a Brush; WPF type-converts the string.
        for accent in state.DEPTH_ACCENTS:
            self.assertRegex(accent, r"^#[0-9A-F]{6}$")


class SummaryTests(unittest.TestCase):
    def test_summary_counts_circuits_and_panels(self):
        roots, _index, _parents = state.build_tree(sample())
        text = state.summarize(sample(), roots)
        self.assertIn("2 circuit(s)", text)
        self.assertIn("2 panel(s)", text)

    def test_summary_reports_unassigned_circuits(self):
        data = snapshot([equipment(1, "MSB")],
                        [circuit(100, 1, "1", []), circuit(200, None, "7", [])])
        roots, _index, _parents = state.build_tree(data)
        self.assertIn("1 unassigned", state.summarize(data, roots))

    def test_summary_reports_loops(self):
        data = snapshot(
            [equipment(1, "A"), equipment(2, "B"), equipment(3, "MSB")],
            [circuit(90, 3, "1", [1]), circuit(101, 1, "1", [2]),
             circuit(102, 2, "1", [1])],
        )
        roots, _index, _parents = state.build_tree(data)
        self.assertIn("loop(s)", state.summarize(data, roots))


class NodeFactoryTests(unittest.TestCase):
    def test_every_node_comes_from_the_injected_factory(self):
        # The WPF layer subclasses Node to add change notification; if any node
        # were built directly the pane would stop updating for that row.
        built = []

        class Recording(state.Node):
            def __init__(self, *args, **kwargs):
                state.Node.__init__(self, *args, **kwargs)
                built.append(self)

        roots, _index, _parents = state.build_tree(sample(), node_factory=Recording)
        self.assertTrue(built)
        self.assertEqual(len(built), len(list(state.iter_nodes(roots))))

    def test_notify_fires_when_a_flag_changes(self):
        seen = []

        class Notifying(state.Node):
            def notify(self, attr):
                seen.append(attr)

        data = snapshot(
            [equipment(1, "MSB"), equipment(2, "LP-1"), equipment(3, "LP-2")],
            [circuit(100, 1, "1", [2]), circuit(101, 1, "3", [3]),
             circuit(200, 2, "12", [])],
        )
        roots, _index, _parents = state.build_tree(data, node_factory=Notifying)
        state.apply_filter(roots, "12")
        # LP-2's branch is hidden, circuit 12 is matched, MSB opens to reveal it.
        self.assertIn("is_visible", seen)
        self.assertIn("matched", seen)
        self.assertIn("is_expanded", seen)


if __name__ == "__main__":
    unittest.main()
