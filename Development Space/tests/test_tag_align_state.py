import importlib.util
import math
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Tag Align.pushbutton"
    / "tag_align_state.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("tag_align_state", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = _load_module()


def make_rule(**kwargs):
    defaults = {
        "tag_type_id": "tag-1",
        "tag_type_label": "Door Tag : Standard",
        "tag_family_name": "Door Tag",
        "host_category_id": "cat-doors",
        "host_category_name": "Doors",
        "host_family_name": "Single-Flush",
        "host_type_id": "type-900",
        "host_type_label": "900 x 2100",
        "view_scale": 100,
        "has_direction": True,
        "angle_deg": 0.0,
        "axis_symmetric": False,
        "offset_view": (0.0, 2.0),
        "offset_local": (0.0, 2.0),
    }
    defaults.update(kwargs)
    return state.ReferenceRule(**defaults)


def make_target(**kwargs):
    defaults = {
        "element_id": 1,
        "category_id": "cat-doors",
        "family_name": "Single-Flush",
        "type_id": "type-900",
        "type_label": "900 x 2100",
        "has_direction": True,
        "angle_deg": 0.0,
        "axis_symmetric": False,
    }
    defaults.update(kwargs)
    return state.TargetInfo(**defaults)


class AngleTests(unittest.TestCase):
    def test_normalize_angle_folds_into_span(self):
        self.assertAlmostEqual(state.normalize_angle(-90.0), 270.0)
        self.assertAlmostEqual(state.normalize_angle(370.0), 10.0)
        self.assertAlmostEqual(state.normalize_angle(190.0, 180.0), 10.0)

    def test_angular_distance_wraps_the_short_way(self):
        self.assertAlmostEqual(state.angular_distance(350.0, 10.0), 20.0)
        self.assertAlmostEqual(state.angular_distance(10.0, 350.0), 20.0)

    def test_folding_treats_opposite_directions_as_one_axis(self):
        self.assertAlmostEqual(state.angular_distance(0.0, 179.0, fold=True), 1.0)
        self.assertAlmostEqual(state.angular_distance(0.0, 179.0, fold=False), 179.0)

    def test_fold_only_applies_when_both_sides_are_undirected(self):
        self.assertTrue(state.should_fold(True, True, True))
        self.assertFalse(state.should_fold(True, False, True))
        self.assertFalse(state.should_fold(True, True, False))

    def test_tolerance_boundary(self):
        self.assertTrue(state.angles_match(0.0, 1.0))
        self.assertFalse(state.angles_match(0.0, 1.5))

    def test_format_angle_names_the_cardinal_directions(self):
        self.assertIn("Horizontal", state.format_angle(0.0))
        self.assertIn("Vertical", state.format_angle(90.0))
        self.assertEqual("No orientation", state.format_angle(0.0, has_direction=False))
        self.assertIn("37", state.format_angle(37.0))


class OffsetMathTests(unittest.TestCase):
    def test_rotate_offset_round_trip(self):
        for angle in (0.0, 45.0, 90.0, 180.0, 233.0):
            rotated = state.rotate_offset((3.0, -1.5), angle)
            back = state.rotate_offset(rotated, -angle)
            self.assertAlmostEqual(back[0], 3.0, places=9)
            self.assertAlmostEqual(back[1], -1.5, places=9)

    def test_rotating_a_vertical_offset_by_ninety_degrees_points_left(self):
        rotated = state.rotate_offset((0.0, 2.0), 90.0)
        self.assertAlmostEqual(rotated[0], -2.0, places=9)
        self.assertAlmostEqual(rotated[1], 0.0, places=9)

    def test_scale_offset_converts_between_view_scales(self):
        scaled = state.scale_offset((0.0, 2.0), 100, 50, True)
        self.assertAlmostEqual(scaled[1], 1.0)

    def test_scale_offset_is_a_no_op_when_the_option_is_off(self):
        scaled = state.scale_offset((0.0, 2.0), 100, 50, False)
        self.assertAlmostEqual(scaled[1], 2.0)

    def test_compose_uses_the_view_frame_when_orientation_matched(self):
        rule = make_rule()
        target = make_target(angle_deg=90.0)
        offset = state.compose_target_offset(rule, target, False, 100, True)
        self.assertAlmostEqual(offset[0], 0.0)
        self.assertAlmostEqual(offset[1], 2.0)

    def test_compose_rotates_with_the_element_in_local_frame(self):
        rule = make_rule()
        target = make_target(angle_deg=90.0)
        offset = state.compose_target_offset(rule, target, True, 100, True)
        self.assertAlmostEqual(offset[0], -2.0, places=9)
        self.assertAlmostEqual(offset[1], 0.0, places=9)

    def test_compose_secondary_returns_none_when_the_reference_had_no_elbow(self):
        rule = make_rule()
        target = make_target()
        self.assertIsNone(
            state.compose_target_secondary(
                rule, target, False, 100, True, "elbow_view", "elbow_local"
            )
        )

    def test_comparable_offset_uses_paper_units_when_scaling(self):
        rule = make_rule(view_scale=100, offset_view=(0.0, 2.0))
        self.assertAlmostEqual(state.comparable_offset(rule, True)[1], 0.02)
        self.assertAlmostEqual(state.comparable_offset(rule, False)[1], 2.0)


class ValidationTests(unittest.TestCase):
    def test_mixed_tag_types_are_fatal(self):
        rules = [
            make_rule(),
            make_rule(tag_type_id="tag-2", tag_type_label="Door Tag : Large"),
        ]
        result = state.validate_references(rules)
        self.assertTrue(result.is_fatal)
        self.assertEqual(state.TAG_TYPE_MIXED, result.fatals[0].code)
        self.assertEqual([], result.accepted)

    def test_mixed_categories_are_fatal(self):
        rules = [
            make_rule(),
            make_rule(host_category_id="cat-windows", host_category_name="Windows"),
        ]
        result = state.validate_references(rules)
        self.assertTrue(result.is_fatal)
        self.assertEqual(state.CATEGORY_MIXED, result.fatals[0].code)

    def test_invalid_rules_are_dropped_with_a_note(self):
        rules = [make_rule(), make_rule(invalid_reason="labels 2 elements")]
        result = state.validate_references(rules)
        self.assertEqual(1, len(result.accepted))
        self.assertTrue(any("labels 2 elements" in note for note in result.notes))

    def test_identical_references_merge_instead_of_conflicting(self):
        rules = [make_rule(), make_rule()]
        result = state.validate_references(rules)
        self.assertFalse(result.is_blocked)
        self.assertEqual(1, len(result.accepted))
        self.assertTrue(any("duplicate" in note.lower() for note in result.notes))

    def test_same_type_same_orientation_different_offset_conflicts(self):
        rules = [make_rule(), make_rule(offset_view=(0.0, 4.0), offset_local=(0.0, 4.0))]
        result = state.validate_references(rules)
        self.assertTrue(result.is_blocked)
        self.assertEqual(state.POSITION_CONFLICT, result.conflicts[0].code)
        self.assertEqual(2, len(result.conflicts[0].members))

    def test_different_orientation_is_allowed(self):
        rules = [
            make_rule(),
            make_rule(angle_deg=90.0, offset_view=(3.0, 0.0), offset_local=(0.0, 2.0)),
        ]
        result = state.validate_references(rules)
        self.assertFalse(result.is_blocked)
        self.assertEqual(2, len(result.accepted))

    def test_paired_family_scope_creates_ambiguity_that_exact_scope_does_not(self):
        rules = [
            make_rule(host_type_id="type-900", host_type_label="900"),
            make_rule(
                host_type_id="type-1200",
                host_type_label="1200",
                offset_view=(0.0, 5.0),
                offset_local=(0.0, 5.0),
            ),
        ]

        paired = state.validate_references(rules, scope=state.SCOPE_PAIRED_FAMILY)
        self.assertTrue(paired.is_blocked)
        self.assertEqual(state.SCOPE_AMBIGUITY, paired.conflicts[0].code)

        exact = state.validate_references(rules, scope=state.SCOPE_EXACT_TYPE)
        self.assertFalse(exact.is_blocked)
        self.assertEqual(2, len(exact.accepted))

    def test_view_scale_option_decides_whether_two_references_agree(self):
        rules = [
            make_rule(view_scale=100, offset_view=(0.0, 2.0)),
            make_rule(view_scale=50, offset_view=(0.0, 1.0)),
        ]
        # Same printed distance: a duplicate once scaled.
        scaled = state.validate_references(rules, scale_with_view=True)
        self.assertFalse(scaled.is_blocked)
        self.assertEqual(1, len(scaled.accepted))

        # Different model distance: a real conflict when scaling is off.
        unscaled = state.validate_references(rules, scale_with_view=False)
        self.assertTrue(unscaled.is_blocked)

    def test_conflict_choice_selects_one_winner(self):
        rules = [
            make_rule(host_type_label="A"),
            make_rule(host_type_label="B", offset_view=(0.0, 4.0)),
        ]
        result = state.validate_references(rules)
        self.assertEqual(1, len(state.unresolved_conflicts(result)))

        result.conflicts[0].chosen_index = 1
        self.assertEqual([], state.unresolved_conflicts(result))
        accepted = state.apply_conflict_choices(result)
        self.assertEqual(1, len(accepted))
        self.assertEqual("B", accepted[0].host_type_label)

    def test_dropping_a_conflict_group_keeps_nothing_from_it(self):
        rules = [make_rule(), make_rule(offset_view=(0.0, 4.0))]
        result = state.validate_references(rules)
        result.conflicts[0].chosen_index = state.DROP_ALL
        self.assertEqual([], state.apply_conflict_choices(result))

    def test_flipped_line_based_references_collapse_into_one_orientation(self):
        rules = [
            make_rule(axis_symmetric=True, angle_deg=0.0),
            make_rule(axis_symmetric=True, angle_deg=180.0),
        ]
        collapsed = state.validate_references(rules, collapse_flip=True)
        self.assertFalse(collapsed.is_blocked)
        self.assertEqual(1, len(collapsed.accepted))

        separate = state.validate_references(rules, collapse_flip=False)
        self.assertEqual(2, len(separate.accepted))


class ResolutionTests(unittest.TestCase):
    def test_category_must_match(self):
        match = state.resolve_reference_for_target(
            [make_rule()], make_target(category_id="cat-windows")
        )
        self.assertFalse(match.matched)
        self.assertEqual(state.SKIP_CATEGORY_MISMATCH, match.skip_reason)

    def test_exact_scope_skips_a_sibling_type(self):
        match = state.resolve_reference_for_target(
            [make_rule()],
            make_target(type_id="type-1200"),
            scope=state.SCOPE_EXACT_TYPE,
        )
        self.assertEqual(state.SKIP_NO_RULE_FOR_TYPE, match.skip_reason)

    def test_paired_family_scope_covers_a_sibling_type(self):
        match = state.resolve_reference_for_target(
            [make_rule()],
            make_target(type_id="type-1200"),
            scope=state.SCOPE_PAIRED_FAMILY,
        )
        self.assertTrue(match.matched)
        self.assertFalse(match.use_local_frame)

    def test_paired_family_scope_still_stops_at_another_family(self):
        match = state.resolve_reference_for_target(
            [make_rule()],
            make_target(family_name="Double-Flush", type_id="type-x"),
            scope=state.SCOPE_PAIRED_FAMILY,
        )
        self.assertEqual(state.SKIP_NO_RULE_FOR_FAMILY, match.skip_reason)

    def test_exact_type_wins_over_a_family_sibling(self):
        exact = make_rule(host_type_id="type-1200", offset_view=(0.0, 9.0))
        match = state.resolve_reference_for_target(
            [make_rule(), exact],
            make_target(type_id="type-1200"),
            scope=state.SCOPE_PAIRED_FAMILY,
        )
        self.assertIs(exact, match.rule)

    def test_any_orientation_uses_the_local_frame_for_every_angle(self):
        match = state.resolve_reference_for_target(
            [make_rule()], make_target(angle_deg=37.0), any_orientation=True
        )
        self.assertTrue(match.matched)
        self.assertTrue(match.use_local_frame)

    def test_unmatched_orientation_is_skipped_by_default(self):
        match = state.resolve_reference_for_target(
            [make_rule()], make_target(angle_deg=37.0)
        )
        self.assertEqual(state.SKIP_NO_REFERENCE_FOR_ORIENTATION, match.skip_reason)

    def test_rotate_fallback_picks_the_nearest_reference(self):
        near = make_rule(angle_deg=90.0, offset_view=(3.0, 0.0))
        match = state.resolve_reference_for_target(
            [make_rule(), near], make_target(angle_deg=80.0), rotate_fallback=True
        )
        self.assertIs(near, match.rule)
        self.assertTrue(match.use_local_frame)

    def test_orientation_bucket_selects_between_two_references(self):
        horizontal = make_rule(angle_deg=0.0, offset_view=(0.0, 2.0))
        vertical = make_rule(angle_deg=90.0, offset_view=(3.0, 0.0))
        rules = [horizontal, vertical]
        self.assertIs(
            horizontal,
            state.resolve_reference_for_target(rules, make_target(angle_deg=0.0)).rule,
        )
        self.assertIs(
            vertical,
            state.resolve_reference_for_target(rules, make_target(angle_deg=90.0)).rule,
        )

    def test_element_without_orientation_prefers_a_reference_without_one(self):
        directionless = make_rule(has_direction=False)
        match = state.resolve_reference_for_target(
            [make_rule(), directionless], make_target(has_direction=False)
        )
        self.assertIs(directionless, match.rule)

    def test_element_without_orientation_is_ambiguous_among_several_references(self):
        rules = [make_rule(angle_deg=0.0), make_rule(angle_deg=90.0)]
        match = state.resolve_reference_for_target(rules, make_target(has_direction=False))
        self.assertEqual(state.SKIP_AMBIGUOUS_ORIENTATION, match.skip_reason)

    def test_session_wiring_reaches_the_resolver(self):
        session = state.TagAlignSession()
        session.reference_mode = state.MODE_SINGLE
        session.any_orientation = True
        session.references = [make_rule()]
        match = state.resolve_for_session(session, make_target(angle_deg=63.0))
        self.assertTrue(match.matched)
        self.assertTrue(match.use_local_frame)

    def test_multi_reference_mode_never_uses_the_any_orientation_shortcut(self):
        session = state.TagAlignSession()
        session.reference_mode = state.MODE_MULTI
        session.any_orientation = True
        session.references = [make_rule()]
        self.assertFalse(session.uses_local_frame_everywhere)
        match = state.resolve_for_session(session, make_target(angle_deg=63.0))
        self.assertEqual(state.SKIP_NO_REFERENCE_FOR_ORIENTATION, match.skip_reason)


class GeometryConsistencyTests(unittest.TestCase):
    """The local frame is what the Revit layer builds; keep the two in step."""

    def test_local_components_reproduce_the_view_offset(self):
        angle = 33.0
        dx = math.cos(math.radians(angle))
        dy = math.sin(math.radians(angle))
        offset_view = (1.25, -3.5)
        # Mirrors tag_align_revit.to_local.
        offset_local = (
            offset_view[0] * dx + offset_view[1] * dy,
            -offset_view[0] * dy + offset_view[1] * dx,
        )
        rebuilt = state.rotate_offset(offset_local, angle)
        self.assertAlmostEqual(rebuilt[0], offset_view[0], places=9)
        self.assertAlmostEqual(rebuilt[1], offset_view[1], places=9)


class SummaryTests(unittest.TestCase):
    def _summary(self):
        summary = state.ProcessSummary()
        summary.considered = 30
        summary.aligned = 20
        summary.tagged = 4
        summary.already_aligned = 2
        for index in range(6):
            summary.skip("Door {0}".format(index), state.SKIP_PINNED, element_id=index)
        summary.fail("Door 99", "read-only element", element_id=99)
        summary.note("Merged a duplicate reference.")
        return summary

    def test_report_lists_counts_and_grouped_reasons(self):
        text = state.build_report_text(self._summary(), create_tags=True)
        self.assertIn("Elements processed: 30", text)
        self.assertIn("Tags aligned: 20", text)
        self.assertIn("Tags created: 4", text)
        self.assertIn("Already in position: 2", text)
        self.assertIn(state.SKIP_TEXT[state.SKIP_PINNED], text)
        self.assertIn("Failed: 1", text)
        self.assertIn("plus 3 more", text)

    def test_report_hides_tag_counts_for_plain_align(self):
        self.assertNotIn("Tags created", state.build_report_text(self._summary()))

    def test_preview_text_mirrors_the_plan(self):
        text = state.build_preview_text(self._summary(), create_tags=True)
        self.assertIn("About to process 30 element(s).", text)
        self.assertIn("Create new tags: 4", text)

    def test_cancelled_report_says_nothing_was_kept(self):
        summary = self._summary()
        summary.cancelled = True
        self.assertIn("nothing was kept", state.build_report_text(summary))

    def test_group_skips_orders_by_count(self):
        summary = state.ProcessSummary()
        summary.skip("a", state.SKIP_PINNED)
        summary.skip("b", state.SKIP_NO_TAG)
        summary.skip("c", state.SKIP_NO_TAG)
        rows = state.group_skips(summary)
        self.assertEqual(state.SKIP_TEXT[state.SKIP_NO_TAG], rows[0][0])
        self.assertEqual(2, rows[0][1])

    def test_skipped_element_ids_cover_skips_and_failures(self):
        self.assertEqual([0, 1, 2, 3, 4, 5, 99], self._summary().skipped_element_ids())

    def test_merge_accumulates_across_clicks(self):
        total = state.ProcessSummary()
        for _ in range(3):
            step = state.ProcessSummary()
            step.considered = 1
            step.aligned = 1
            total.merge(step)
        self.assertEqual(3, total.considered)
        self.assertEqual(3, total.changed)

    def test_running_text_counts_skips_and_failures_together(self):
        summary = self._summary()
        text = state.build_running_text(summary, create_tags=True)
        self.assertIn("20 aligned", text)
        self.assertIn("4 tagged", text)
        self.assertIn("7 skipped", text)

    def test_fatal_text_names_every_offending_value(self):
        rules = [
            make_rule(),
            make_rule(tag_type_id="tag-2", tag_type_label="Door Tag : Large"),
        ]
        text = state.build_fatal_text(state.validate_references(rules))
        self.assertIn("Door Tag : Standard", text)
        self.assertIn("Door Tag : Large", text)
        self.assertIn("select again", text)


class ReferenceRowTests(unittest.TestCase):
    def test_row_describes_the_offset_with_the_source_scale(self):
        row = state.build_reference_row(make_rule())
        self.assertEqual("Door Tag : Standard", row["tag_type"])
        self.assertEqual("Single-Flush : 900 x 2100", row["host"])
        self.assertIn("Up", row["offset"])
        self.assertIn("1:100", row["offset"])

    def test_describe_offset_names_the_direction_signs(self):
        text = state.describe_offset((-1.0, -2.0))
        self.assertIn("Left", text)
        self.assertIn("Down", text)


class FilterTreeTests(unittest.TestCase):
    def _entries(self):
        return [
            {"element_id": 1, "category": "Doors", "family": "Single", "type": "900"},
            {"element_id": 2, "category": "Doors", "family": "Single", "type": "900"},
            {"element_id": 3, "category": "Doors", "family": "Single", "type": "1200"},
            {"element_id": 4, "category": "Doors", "family": "Double", "type": "1800"},
            {"element_id": 5, "category": "Windows", "family": "Fixed", "type": "600"},
        ]

    def test_tree_counts_roll_up(self):
        tree = state.build_filter_tree(self._entries())
        by_name = dict((node.label, node) for node in tree)
        self.assertEqual(4, by_name["Doors"].count)
        self.assertEqual(1, by_name["Windows"].count)
        single = [n for n in by_name["Doors"].children if n.label == "Single"][0]
        self.assertEqual(3, single.count)
        self.assertEqual(2, len(single.children))

    def test_unticking_a_type_removes_only_its_entries(self):
        entries = self._entries()
        tree = state.build_filter_tree(entries)
        for category in tree:
            for family in category.children:
                for type_node in family.children:
                    type_node.is_checked = type_node.label == "900"

        kept = state.filter_entries(entries, state.checked_type_keys(tree))
        self.assertEqual([1, 2], [entry["element_id"] for entry in kept])

    def test_missing_names_get_placeholders_rather_than_vanishing(self):
        tree = state.build_filter_tree([{"element_id": 1}])
        self.assertEqual("<no category>", tree[0].label)
        self.assertEqual("<no family>", tree[0].children[0].label)
        self.assertEqual("<no type>", tree[0].children[0].children[0].label)


class SessionTests(unittest.TestCase):
    def test_defaults_match_the_agreed_behaviour(self):
        session = state.TagAlignSession()
        self.assertEqual(state.SCOPE_PAIRED_FAMILY, session.scope)
        self.assertTrue(session.scale_with_view)
        self.assertTrue(session.copy_leader)
        self.assertTrue(session.collapse_flip)
        self.assertTrue(session.confirm_before_process)
        self.assertFalse(session.change_tag_type)
        self.assertFalse(session.include_pinned)
        self.assertFalse(session.rotate_fallback)
        self.assertFalse(session.has_references)

    def test_reset_clears_the_orientation_answer_too(self):
        session = state.TagAlignSession()
        session.references = [make_rule()]
        session.reference_mode = state.MODE_SINGLE
        session.any_orientation = True
        session.reset_references()
        self.assertFalse(session.has_references)
        self.assertIsNone(session.reference_mode)
        self.assertFalse(session.any_orientation)

    def test_tag_type_and_category_come_from_the_references(self):
        session = state.TagAlignSession()
        session.references = [make_rule()]
        self.assertEqual("tag-1", session.tag_type_id())
        self.assertEqual("Door Tag : Standard", session.tag_type_label())
        self.assertEqual("cat-doors", session.host_category_id())
        self.assertEqual("Doors", session.host_category_name())


if __name__ == "__main__":
    unittest.main()
