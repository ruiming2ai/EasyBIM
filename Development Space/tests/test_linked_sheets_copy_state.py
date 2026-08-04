"""The decisions Linked Sheets Copy makes before it touches the model.

Everything here is what the confirmation window shows and the executor obeys,
so a change that alters one without the other fails here first.
"""

import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (REPO_ROOT / "EasyBIM.tab" / "Sheet.panel" /
               "Linked Sheets Copy.pushbutton")
if str(COMMAND_DIR) not in sys.path:
    sys.path.insert(0, str(COMMAND_DIR))

import linked_sheets_copy_state as state


def view_row(name=u"Level 1", level_key=10, supported=True, scale=100,
             view_family=u"FloorPlan", **kwargs):
    return state.ViewRow(
        viewport_key=kwargs.pop("viewport_key", 900),
        view_key=kwargs.pop("view_key", 901),
        view_name=name,
        view_type_label=kwargs.pop("view_type_label", u"Floor Plan"),
        supported=supported,
        skip_reason=kwargs.pop("skip_reason", u""),
        level_key=level_key,
        scale=scale,
        view_family=view_family,
        **kwargs)


def sheet_row(number=u"A-101", name=u"Level 1 Plan", views=None, key=1,
              **kwargs):
    row = state.SheetRow(key, number, name,
                         view_rows=views if views is not None
                         else [view_row()], **kwargs)
    row.is_checked = True
    return row


def facts(**kwargs):
    kwargs.setdefault("view_family_types", [u"FloorPlan", u"CeilingPlan"])
    return state.HostFacts(**kwargs)


def mapped_levels(linked_key=10, host_key=20, host_name=u"Level 1",
                  linked_name=u"Level 1", linked_elevation=0.0,
                  host_elevation=0.0):
    linked = state.LinkedLevel(linked_key, linked_name, linked_elevation)
    host = state.HostLevelChoice(host_key, host_name, host_elevation)
    return state.resolve_level_map([linked], [host], {linked_key: host_key})


# ---------------------------------------------------------------------------


class OptionDefaultTests(unittest.TestCase):
    def test_only_the_content_copying_options_start_off(self):
        defaults = state.default_options()
        self.assertTrue(defaults[state.OPT_TITLE_BLOCK])
        self.assertTrue(defaults[state.OPT_TITLE_BLOCK_PARAMS])
        for key in (state.OPT_SHEET_PARAMS, state.OPT_SHEET_DETAILING,
                    state.OPT_REVISION_CLOUDS, state.OPT_VIEW_ANNOTATIONS,
                    state.OPT_VIEW_TEMPLATE, state.OPT_SCOPE_BOX,
                    state.OPT_HIDE_OTHER_LINKS):
            self.assertFalse(defaults[key], key)

    def test_title_and_viewport_matching_ship_on(self):
        # "View, view title, view title length should be exactly matching the
        # linked sheet" was the stated requirement.
        defaults = state.default_options()
        self.assertTrue(defaults[state.OPT_VIEWPORT_TYPE])
        self.assertTrue(defaults[state.OPT_VIEW_TITLE])

    def test_every_option_carries_a_tooltip(self):
        for key, label, _default, tooltip in state.OPTION_DEFS:
            self.assertTrue(label, key)
            self.assertTrue(tooltip, key)

    def test_the_double_drawing_option_says_so_in_its_tooltip(self):
        lookup = dict((key, tooltip)
                      for key, _l, _d, tooltip in state.OPTION_DEFS)
        self.assertIn(u"twice", lookup[state.OPT_VIEW_ANNOTATIONS])


class UniqueNameTests(unittest.TestCase):
    def test_a_free_name_is_returned_unchanged(self):
        self.assertEqual(u"Level 1", state.unique_name(u"Level 1", []))

    def test_a_taken_name_gets_a_numeric_suffix(self):
        self.assertEqual(u"Level 1 2",
                         state.unique_name(u"Level 1", [u"Level 1"]))

    def test_it_keeps_counting_past_the_first_suffix(self):
        self.assertEqual(
            u"Level 1 3",
            state.unique_name(u"Level 1", [u"Level 1", u"Level 1 2"]))

    def test_the_comparison_is_case_insensitive_like_revit(self):
        self.assertEqual(u"Plan 2", state.unique_name(u"Plan", [u"PLAN"]))

    def test_an_empty_name_still_produces_something(self):
        self.assertEqual(u"Unnamed", state.unique_name(u"   ", []))


class NumberAffixTests(unittest.TestCase):
    def test_affixes_wrap_the_source_number(self):
        rows = [sheet_row(number=u"A-101")]
        state.apply_number_affixes(rows, u"L-", u"-R")
        self.assertEqual(u"L-A-101-R", rows[0].new_number)

    def test_reapplying_does_not_stack(self):
        rows = [sheet_row(number=u"A-101")]
        state.apply_number_affixes(rows, u"L-", u"")
        state.apply_number_affixes(rows, u"X-", u"")
        self.assertEqual(u"X-A-101", rows[0].new_number)

    def test_clearing_the_boxes_restores_the_source_number(self):
        rows = [sheet_row(number=u"A-101")]
        state.apply_number_affixes(rows, u"L-", u"-R")
        state.apply_number_affixes(rows, u"", u"")
        self.assertEqual(u"A-101", rows[0].new_number)


class CollisionTests(unittest.TestCase):
    def test_a_number_this_model_already_has_is_flagged(self):
        rows = [sheet_row(number=u"A-101")]
        flagged = state.detect_collisions(rows, [u"A-101"])
        self.assertEqual(1, len(flagged))
        self.assertTrue(rows[0].has_problem)

    def test_the_existing_check_ignores_case(self):
        rows = [sheet_row(number=u"a-101")]
        self.assertEqual(1, len(state.detect_collisions(rows, [u"A-101"])))

    def test_two_rows_with_one_number_collide_with_each_other(self):
        rows = [sheet_row(number=u"A-101", key=1),
                sheet_row(number=u"A-101", key=2)]
        flagged = state.detect_collisions(rows, [])
        self.assertEqual(1, len(flagged))
        self.assertIs(rows[1], flagged[0])

    def test_an_unchecked_row_never_collides(self):
        rows = [sheet_row(number=u"A-101")]
        rows[0].is_checked = False
        self.assertEqual([], state.detect_collisions(rows, [u"A-101"]))
        self.assertFalse(rows[0].has_problem)

    def test_an_empty_number_is_a_collision_of_its_own(self):
        rows = [sheet_row(number=u"  ")]
        rows[0].new_number = u"  "
        flagged = state.detect_collisions(rows, [])
        self.assertEqual(1, len(flagged))
        self.assertIn(u"empty", rows[0].collision.lower())

    def test_rerunning_clears_a_flag_that_has_been_fixed(self):
        rows = [sheet_row(number=u"A-101")]
        state.detect_collisions(rows, [u"A-101"])
        rows[0].new_number = u"A-999"
        state.detect_collisions(rows, [u"A-101"])
        self.assertFalse(rows[0].has_problem)


class LevelResolutionTests(unittest.TestCase):
    def setUp(self):
        self.linked = [state.LinkedLevel(10, u"Niveau 1", 0.0),
                       state.LinkedLevel(11, u"Niveau 2", 12.0)]
        self.hosts = [state.HostLevelChoice(20, u"Level 1", 0.0),
                      state.HostLevelChoice(21, u"Niveau 2", 99.0)]

    def test_copy_monitor_wins_over_a_name_match(self):
        rows = state.resolve_level_map(self.linked, self.hosts,
                                       {11: [20]})
        by_key = dict((row.linked_key, row) for row in rows)
        self.assertEqual(20, by_key[11].host_key)
        self.assertEqual(state.SOURCE_MONITORED, by_key[11].source)

    def test_a_name_match_is_used_when_nothing_is_monitored(self):
        rows = state.resolve_level_map(self.linked, self.hosts, {})
        by_key = dict((row.linked_key, row) for row in rows)
        self.assertEqual(21, by_key[11].host_key)
        self.assertEqual(state.SOURCE_NAME, by_key[11].source)

    def test_an_elevation_match_is_the_last_resort(self):
        rows = state.resolve_level_map(self.linked, self.hosts, {})
        by_key = dict((row.linked_key, row) for row in rows)
        self.assertEqual(20, by_key[10].host_key)
        self.assertEqual(state.SOURCE_ELEVATION, by_key[10].source)

    def test_an_elevation_outside_the_tolerance_is_not_matched(self):
        linked = [state.LinkedLevel(10, u"Roof", 5.0)]
        hosts = [state.HostLevelChoice(20, u"Level 1", 0.0)]
        rows = state.resolve_level_map(linked, hosts, {})
        self.assertFalse(rows[0].is_mapped)
        self.assertEqual(state.SOURCE_UNMAPPED, rows[0].source)

    def test_monitoring_in_another_link_instance_is_already_filtered_out(self):
        # The Revit layer only passes pairs for the selected instance, so an
        # empty map here has to mean "guess", not "matched".
        rows = state.resolve_level_map(self.linked, self.hosts, {})
        self.assertNotEqual(state.SOURCE_MONITORED, rows[0].source)

    def test_two_host_levels_monitoring_one_linked_level_are_flagged(self):
        rows = state.resolve_level_map(self.linked, self.hosts,
                                       {10: [20, 21]})
        self.assertTrue(rows[0].ambiguous)
        self.assertEqual(20, rows[0].host_key)

    def test_a_manual_pick_overrides_everything_and_says_so(self):
        rows = state.resolve_level_map(self.linked, self.hosts, {10: [20]})
        rows[0].set_manual(self.hosts[1])
        self.assertEqual(21, rows[0].host_key)
        self.assertEqual(state.SOURCE_MANUAL, rows[0].source)
        self.assertFalse(rows[0].ambiguous)

    def test_clearing_a_manual_pick_unmaps_the_row(self):
        rows = state.resolve_level_map(self.linked, self.hosts, {10: [20]})
        rows[0].set_manual(None)
        self.assertFalse(rows[0].is_mapped)

    def test_an_unknown_monitored_host_level_falls_through_to_the_guess(self):
        rows = state.resolve_level_map(self.linked, self.hosts, {11: [999]})
        by_key = dict((row.linked_key, row) for row in rows)
        self.assertEqual(state.SOURCE_NAME, by_key[11].source)

    def test_the_elevation_delta_is_reported(self):
        rows = state.resolve_level_map(self.linked, self.hosts, {11: [20]})
        by_key = dict((row.linked_key, row) for row in rows)
        self.assertAlmostEqual(-12.0, by_key[11].delta_elevation)
        self.assertIn(u"-12", by_key[11].delta_text)


class PlanTests(unittest.TestCase):
    def test_a_straightforward_sheet_is_created_with_its_view(self):
        rows = [sheet_row()]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        self.assertEqual(1, len(plan.sheets_to_create))
        self.assertEqual(1, len(plan.views_to_create))
        self.assertTrue(plan.has_work())

    def test_an_unchecked_sheet_is_not_in_the_plan_at_all(self):
        rows = [sheet_row()]
        rows[0].is_checked = False
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        self.assertEqual([], plan.sheets)

    def test_a_taken_sheet_number_skips_the_sheet(self):
        plan = state.build_plan([sheet_row(number=u"A-101")], mapped_levels(),
                                state.default_options(),
                                facts(sheet_numbers=[u"A-101"]))
        self.assertEqual(state.ACTION_SKIP, plan.sheets[0].action)
        self.assertFalse(plan.has_work())

    def test_a_duplicate_inside_the_batch_only_creates_the_first(self):
        rows = [sheet_row(number=u"A-101", key=1),
                sheet_row(number=u"A-101", key=2)]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        self.assertEqual(1, len(plan.sheets_to_create))
        self.assertEqual(state.ACTION_SKIP, plan.sheets[1].action)

    def test_an_unsupported_view_is_skipped_but_the_sheet_is_still_built(self):
        rows = [sheet_row(views=[
            view_row(),
            view_row(name=u"Section A", supported=False,
                     skip_reason=u"Section is not reproduced")])]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        self.assertEqual(1, len(plan.sheets_to_create))
        self.assertEqual(1, len(plan.views_to_create))
        skipped = [view for view in plan.sheets[0].views
                   if view.action == state.ACTION_SKIP]
        self.assertIn(u"Section", skipped[0].reason)

    def test_an_unmapped_level_skips_only_that_view(self):
        rows = [sheet_row(views=[view_row(level_key=10),
                                 view_row(name=u"Roof", level_key=77)])]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        self.assertEqual(1, len(plan.sheets_to_create))
        self.assertEqual(1, len(plan.views_to_create))

    def test_a_view_with_no_level_is_skipped(self):
        rows = [sheet_row(views=[view_row(level_key=None)])]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        self.assertEqual([], plan.views_to_create)

    def test_a_missing_view_family_type_skips_the_view(self):
        rows = [sheet_row(views=[view_row(view_family=u"StructuralPlan")])]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        self.assertEqual([], plan.views_to_create)
        self.assertIn(u"StructuralPlan", plan.sheets[0].views[0].reason)

    def test_an_area_plan_without_its_scheme_is_skipped(self):
        rows = [sheet_row(views=[view_row(view_family=u"AreaPlan",
                                          area_scheme_name=u"Rentable")])]
        plan = state.build_plan(
            rows, mapped_levels(), state.default_options(),
            facts(view_family_types=[u"AreaPlan"], area_schemes=[u"Gross"]))
        self.assertEqual([], plan.views_to_create)
        self.assertIn(u"Rentable", plan.sheets[0].views[0].reason)

    def test_an_area_plan_with_its_scheme_is_created(self):
        rows = [sheet_row(views=[view_row(view_family=u"AreaPlan",
                                          area_scheme_name=u"Rentable")])]
        plan = state.build_plan(
            rows, mapped_levels(), state.default_options(),
            facts(view_family_types=[u"AreaPlan"], area_schemes=[u"Rentable"]))
        self.assertEqual(1, len(plan.views_to_create))

    def test_a_scaleless_view_is_skipped(self):
        rows = [sheet_row(views=[view_row(scale=0)])]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        self.assertEqual([], plan.views_to_create)

    def test_a_view_name_the_host_already_has_is_renamed(self):
        rows = [sheet_row(views=[view_row(name=u"Level 1")])]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(),
                                facts(view_names=[u"Level 1"]))
        item = plan.views_to_create[0]
        self.assertEqual(u"Level 1 2", item.target_name)
        self.assertTrue(any(u"Renamed" in note for note in item.notes))

    def test_two_copied_views_of_one_name_do_not_collide_with_each_other(self):
        rows = [sheet_row(views=[view_row(name=u"Plan"),
                                 view_row(name=u"Plan", viewport_key=902)])]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        names = [item.target_name for item in plan.views_to_create]
        self.assertEqual([u"Plan", u"Plan 2"], names)

    def test_a_crop_that_is_off_is_called_out(self):
        rows = [sheet_row(views=[view_row(crop_active=False)])]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        notes = plan.views_to_create[0].notes
        self.assertTrue(any(u"Crop is off" in note for note in notes))

    def test_a_placeholder_sheet_says_it_has_no_title_block(self):
        rows = [sheet_row(views=[], is_placeholder=True)]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        self.assertTrue(any(u"Placeholder" in note
                            for note in plan.sheets[0].notes))


class ModelChangeTests(unittest.TestCase):
    def test_a_missing_title_block_is_named_before_it_is_copied(self):
        rows = [sheet_row(title_block_label=u"A1 Sheet: Metric")]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        self.assertTrue(any(u"A1 Sheet: Metric" in change
                            for change in plan.model_changes))

    def test_a_title_block_this_model_already_has_is_not_listed(self):
        rows = [sheet_row(title_block_label=u"A1 Sheet: Metric")]
        plan = state.build_plan(
            rows, mapped_levels(), state.default_options(),
            facts(title_block_types=[u"A1 Sheet: Metric"]))
        self.assertEqual([], plan.model_changes)

    def test_copying_annotations_warns_about_drawing_twice(self):
        options = state.default_options()
        options[state.OPT_VIEW_ANNOTATIONS] = True
        plan = state.build_plan([sheet_row()], mapped_levels(), options,
                                facts())
        self.assertTrue(any(u"twice" in warning for warning in plan.warnings))

    def test_leaving_the_other_links_visible_is_stated(self):
        plan = state.build_plan([sheet_row()], mapped_levels(),
                                state.default_options(), facts())
        self.assertTrue(any(u"By Host View" in warning
                            for warning in plan.warnings))

    def test_hiding_the_other_links_drops_that_warning(self):
        options = state.default_options()
        options[state.OPT_HIDE_OTHER_LINKS] = True
        plan = state.build_plan([sheet_row()], mapped_levels(), options,
                                facts())
        self.assertFalse(any(u"By Host View" in warning
                             for warning in plan.warnings))

    def test_a_moved_link_is_always_mentioned(self):
        plan = state.build_plan([sheet_row()], mapped_levels(),
                                state.default_options(), facts())
        self.assertTrue(any(u"moved" in warning for warning in plan.warnings))


class ConfirmTextTests(unittest.TestCase):
    def test_the_headline_counts_sheets_and_views(self):
        plan = state.build_plan([sheet_row()], mapped_levels(),
                                state.default_options(), facts(),
                                link_label=u"ARCH.rvt")
        headline = state.build_confirm_headline(plan)
        self.assertIn(u"1 sheet", headline)
        self.assertIn(u"1 view", headline)
        self.assertIn(u"ARCH.rvt", headline)

    def test_the_body_names_the_level_mapping_that_will_be_used(self):
        plan = state.build_plan([sheet_row()], mapped_levels(),
                                state.default_options(), facts())
        body = state.build_confirm_text(plan)
        self.assertIn(u"LEVELS", body)
        self.assertIn(u"Level 1", body)
        self.assertIn(state.SOURCE_MONITORED, body)

    def test_the_body_names_every_skipped_view_with_its_reason(self):
        rows = [sheet_row(views=[
            view_row(name=u"Legend 1", supported=False,
                     skip_reason=u"Legend is not reproduced")])]
        plan = state.build_plan(rows, mapped_levels(),
                                state.default_options(), facts())
        body = state.build_confirm_text(plan)
        self.assertIn(u"Legend 1", body)
        self.assertIn(u"Legend is not reproduced", body)


class ReportTests(unittest.TestCase):
    def test_the_headline_counts_what_was_made(self):
        summary = state.RunSummary()
        summary.sheets_created = 2
        summary.views_created = 5
        summary.viewports_placed = 5
        self.assertIn(u"2 sheet", state.build_report_headline(summary))

    def test_a_cancelled_run_says_nothing_was_written(self):
        summary = state.RunSummary()
        summary.cancelled = True
        self.assertIn(u"Cancelled", state.build_report_headline(summary))

    def test_a_rollback_zeroes_the_counters(self):
        # A report claiming work that no longer exists is worse than none.
        summary = state.RunSummary()
        summary.sheets_created = 3
        summary.first_sheet_key = 42
        summary.clear_applied()
        self.assertEqual(0, summary.sheets_created)
        self.assertIsNone(summary.first_sheet_key)

    def test_the_worst_deviation_is_the_one_reported(self):
        summary = state.RunSummary()
        summary.add_row(u"A-101", u"view a", u"placed", deviation_mm=0.01)
        summary.add_row(u"A-101", u"view b", u"placed", deviation_mm=0.44)
        self.assertAlmostEqual(0.44, summary.worst_deviation)
        body = state.build_report_text(summary)
        self.assertIn(u"0.440", body)
        self.assertIn(u"Above", body)

    def test_one_sheets_rollback_removes_only_that_sheets_work(self):
        summary = state.RunSummary()
        summary.sheets_created = 1
        summary.views_created = 2
        summary.add_row(u"A-101", u"view a", u"placed")
        summary.first_sheet_key = 7

        snapshot = summary.snapshot()
        summary.sheets_created += 1
        summary.views_created += 3
        summary.add_row(u"A-102", u"view b", u"placed")
        summary.restore(snapshot)

        self.assertEqual(1, summary.sheets_created)
        self.assertEqual(2, summary.views_created)
        self.assertEqual(1, len(summary.rows))
        self.assertEqual(7, summary.first_sheet_key)

    def test_a_rollback_of_the_first_sheet_forgets_which_sheet_to_open(self):
        summary = state.RunSummary()
        snapshot = summary.snapshot()
        summary.first_sheet_key = 7
        summary.sheets_created = 1
        summary.restore(snapshot)
        self.assertIsNone(summary.first_sheet_key)
        self.assertEqual(0, summary.sheets_created)

    def test_a_clean_run_says_there_were_no_failures(self):
        summary = state.RunSummary()
        summary.add_row(u"A-101", u"view a", u"placed", deviation_mm=0.0)
        self.assertIn(u"No failures", state.build_report_text(summary))

    def test_failures_are_listed_with_their_detail(self):
        summary = state.RunSummary()
        summary.add_error(u"A-101", u"sheet", u"boom")
        body = state.build_report_text(summary)
        self.assertIn(u"FAILED", body)
        self.assertIn(u"boom", body)


class FilterTests(unittest.TestCase):
    def test_an_empty_filter_keeps_everything(self):
        rows = [sheet_row(number=u"A-101"), sheet_row(number=u"A-102", key=2)]
        self.assertEqual(2, len(state.filter_rows(rows, u"")))

    def test_the_filter_reaches_view_names(self):
        rows = [sheet_row(views=[view_row(name=u"Ground Floor")])]
        self.assertEqual(1, len(state.filter_rows(rows, u"ground")))

    def test_the_filter_reaches_the_edited_number(self):
        rows = [sheet_row(number=u"A-101")]
        rows[0].new_number = u"Z-900"
        self.assertEqual(1, len(state.filter_rows(rows, u"z-900")))


class LevelKeyTests(unittest.TestCase):
    def test_all_level_keys_ignores_the_checkboxes(self):
        rows = [sheet_row(views=[view_row(level_key=10)]),
                sheet_row(views=[view_row(level_key=11)], key=2)]
        rows[1].is_checked = False
        self.assertEqual([10, 11], state.all_level_keys(rows))

    def test_required_level_keys_follows_the_checkboxes(self):
        rows = [sheet_row(views=[view_row(level_key=10)]),
                sheet_row(views=[view_row(level_key=11)], key=2)]
        rows[1].is_checked = False
        self.assertEqual([10], state.required_level_keys(rows))

    def test_an_unsupported_view_needs_no_level(self):
        rows = [sheet_row(views=[view_row(level_key=10, supported=False)])]
        self.assertEqual([], state.all_level_keys(rows))


if __name__ == "__main__":
    unittest.main()
