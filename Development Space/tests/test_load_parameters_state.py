"""Pure-logic tests for the Load Parameters state module.

The module is loaded standalone, without the ``easybim`` package or any Revit
import on the path - which is the whole reason it is kept free of them.
"""

import importlib.util
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (REPO_ROOT / "EasyBIM.tab" / "Parameters.panel"
               / "Load Parameters.pushbutton")
STATE_MODULE_PATH = COMMAND_DIR / "load_parameters_state.py"


def _load_state_module():
    spec = importlib.util.spec_from_file_location(
        "load_parameters_state", str(STATE_MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = _load_state_module()


def _param(name, guid, spec=u"spec:text", **kwargs):
    return state.ParameterRow(name=name, guid=guid, spec_id=spec,
                              spec_label=u"Text", **kwargs)


def _family(name, **kwargs):
    return state.FamilyRow(name=name, **kwargs)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [_param(u"A", u"{1}"), _param(u"B", u"{2}"),
                     _param(u"C", u"{3}")]

    def test_select_all_and_none(self):
        state.select_all(self.rows)
        self.assertEqual(3, len(state.checked_rows(self.rows)))
        state.select_none(self.rows)
        self.assertEqual(0, len(state.checked_rows(self.rows)))

    def test_invert_selection(self):
        self.rows[0].is_checked = True
        state.invert_selection(self.rows)
        self.assertEqual([False, True, True],
                         [row.is_checked for row in self.rows])

    def test_apply_check_returns_only_the_movers(self):
        self.rows[0].is_checked = True
        changed = state.apply_check_to_rows(self.rows, True)
        # Row 0 was already ticked, so notifying it would be wasted work.
        self.assertEqual([u"B", u"C"], [row.name for row in changed])

    def test_apply_check_returns_nothing_when_nothing_moves(self):
        self.assertEqual([], state.apply_check_to_rows(self.rows, False))


class PropagationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [_param(u"A", u"{1}"), _param(u"B", u"{2}"),
                     _param(u"C", u"{3}")]

    def test_propagate_skips_the_edited_row(self):
        edited = self.rows[1]
        edited.is_instance = True
        changed = state.propagate_value(self.rows, "is_instance", True,
                                        skip_row=edited)
        self.assertEqual([u"A", u"C"], [row.name for row in changed])
        self.assertTrue(all(row.is_instance for row in self.rows))

    def test_propagate_reports_no_change_when_values_match(self):
        for row in self.rows:
            row.is_instance = True
        self.assertEqual([], state.propagate_value(self.rows, "is_instance",
                                                   True))

    def test_set_group_writes_id_and_label(self):
        changed = state.set_group(self.rows, u"autodesk.group:data",
                                  u"Data")
        self.assertEqual(3, len(changed))
        self.assertEqual(u"Data", self.rows[0].group_label)
        self.assertEqual(u"autodesk.group:data", self.rows[0].group_id)


class FilterAndMergeTests(unittest.TestCase):
    def test_filter_matches_name_and_source(self):
        rows = [_param(u"Fire Rating", u"{1}"),
                _param(u"Asset Tag", u"{2}", source_label=u"Std.txt > Data")]
        self.assertEqual(1, len(state.filter_rows(rows, u"fire")))
        self.assertEqual(1, len(state.filter_rows(rows, u"std.txt")))
        self.assertEqual(2, len(state.filter_rows(rows, u"   ")))

    def test_merge_keeps_existing_rows_untouched(self):
        first = _param(u"A", u"{1}")
        first.is_checked = True
        merged = state.merge_rows([first], [_param(u"A", u"{1}"),
                                            _param(u"B", u"{2}")])
        self.assertEqual(2, len(merged))
        self.assertIs(first, merged[0])
        self.assertTrue(merged[0].is_checked)

    def test_family_key_distinguishes_source(self):
        project = _family(u"Door", element_id=12)
        folder = _family(u"Door", source=state.SOURCE_FOLDER,
                         path=u"C:/lib/Door.rfa")
        self.assertNotEqual(project.key, folder.key)


class CategorySyncTests(unittest.TestCase):
    def test_only_checked_families_contribute_categories(self):
        families = [_family(u"Door A", category=u"Doors"),
                    _family(u"Window A", category=u"Windows")]
        families[0].is_checked = True
        categories = [state.CategoryRow(u"Doors", 1),
                      state.CategoryRow(u"Windows", 2)]
        wanted = state.categories_for_families(families, categories)
        self.assertEqual([u"Doors"], [row.name for row in wanted])


class BackupFileTests(unittest.TestCase):
    def test_revit_backups_are_recognised(self):
        self.assertTrue(state.is_backup_family_file(u"Door.0001.rfa"))
        self.assertTrue(state.is_backup_family_file(u"My Door.0123.rfa"))

    def test_real_families_are_not_backups(self):
        for name in (u"Door.rfa", u"Door.001.rfa", u"Door.00012.rfa",
                     u"Door.abcd.rfa", u"Door.rft"):
            self.assertFalse(state.is_backup_family_file(name), name)

    def test_listing_drops_backups_and_non_families(self):
        kept = state.family_files_from_names(
            u"C:/lib", [u"Door.rfa", u"Door.0001.rfa", u"notes.txt",
                        u"Window.RFA"])
        self.assertEqual([u"Door.rfa", u"Window.RFA"],
                         [name for _, name in kept])


class SharedParameterFileTests(unittest.TestCase):
    def test_header_is_the_format_revit_expects(self):
        lines = state.SHARED_PARAMETER_HEADER.split(u"\n")
        self.assertEqual(u"# This is a Revit shared parameter file.", lines[0])
        self.assertEqual(u"*META\tVERSION\tMINVERSION", lines[2])
        self.assertEqual(u"META\t2\t1", lines[3])

    def test_header_is_pure_ascii(self):
        # Every non-ASCII byte in the file is written by Revit itself, which
        # is what keeps text encoding out of our hands.
        state.SHARED_PARAMETER_HEADER.encode("ascii")

    def test_header_carries_no_param_lines(self):
        self.assertNotIn(u"*PARAM", state.SHARED_PARAMETER_HEADER)
        self.assertNotIn(u"*GROUP", state.SHARED_PARAMETER_HEADER)

    def test_project_rows_get_a_named_group(self):
        row = _param(u"A", u"{1}")
        self.assertEqual(state.DEFAULT_DEFINITION_GROUP,
                         state.definition_group_name(row))

    def test_file_rows_keep_their_own_group(self):
        row = _param(u"A", u"{1}", definition_group=u"Fire")
        self.assertEqual(u"Fire", state.definition_group_name(row))


class DedupeAndCollisionTests(unittest.TestCase):
    def test_same_guid_is_dropped_once(self):
        rows = [_param(u"A", u"{1}"), _param(u"A", u"{1}"),
                _param(u"B", u"{2}")]
        unique, dropped = state.dedupe_parameter_rows(rows)
        self.assertEqual(2, len(unique))
        self.assertEqual(1, len(dropped))

    def test_guid_comparison_ignores_case(self):
        rows = [_param(u"A", u"{ABC}"), _param(u"A", u"{abc}")]
        unique, _ = state.dedupe_parameter_rows(rows)
        self.assertEqual(1, len(unique))

    def test_same_name_different_guid_is_a_collision(self):
        rows = [_param(u"Fire Rating", u"{1}"), _param(u"Fire Rating", u"{2}")]
        collisions = state.name_collisions(rows)
        self.assertEqual(1, len(collisions))
        self.assertEqual(2, len(collisions[0]))

    def test_distinct_names_do_not_collide(self):
        self.assertEqual([], state.name_collisions(
            [_param(u"A", u"{1}"), _param(u"B", u"{2}")]))

    def test_guid_conflict_against_the_project_is_reported(self):
        rows = [_param(u"Fire Rating", u"{PICKED}")]
        conflicts = state.guid_conflicts(rows, {u"fire rating": u"{INMODEL}"})
        self.assertEqual(1, len(conflicts))

    def test_matching_guid_is_not_a_conflict(self):
        rows = [_param(u"Fire Rating", u"{same}")]
        self.assertEqual([], state.guid_conflicts(
            rows, {u"fire rating": u"{SAME}"}))


class FamilyPlanTests(unittest.TestCase):
    def setUp(self):
        self.param = _param(u"Fire Rating", u"{1}")
        self.param.is_checked = True
        self.family = _family(u"Door", element_id=7, category=u"Doors")
        self.family.is_checked = True

    def test_unchecked_rows_are_not_planned(self):
        self.param.is_checked = False
        plan = state.build_family_plan([self.param], [self.family])
        self.assertEqual([], plan.cells)
        self.assertFalse(plan.has_work())

    def test_a_plain_pair_is_an_add(self):
        plan = state.build_family_plan([self.param], [self.family])
        self.assertEqual([state.WILL_ADD],
                         [cell.status for cell in plan.cells])
        self.assertTrue(plan.has_work())

    def test_existing_parameter_skips_in_skip_mode(self):
        plan = state.build_family_plan(
            [self.param], [self.family], state.MODE_SKIP,
            existing_lookup=lambda family: set([u"fire rating"]))
        self.assertEqual([state.SKIP_EXISTS],
                         [cell.status for cell in plan.cells])
        self.assertFalse(plan.has_work())

    def test_existing_parameter_updates_in_update_mode(self):
        plan = state.build_family_plan(
            [self.param], [self.family], state.MODE_UPDATE,
            existing_lookup=lambda family: set([u"fire rating"]))
        self.assertEqual([state.WILL_UPDATE],
                         [cell.status for cell in plan.cells])
        self.assertTrue(plan.has_work())

    def test_unsupported_spec_is_blocked_not_planned(self):
        self.param.supported = False
        self.param.unsupported_reason = u"Family Type"
        plan = state.build_family_plan([self.param], [self.family])
        self.assertEqual([], plan.cells)
        self.assertEqual(1, len(plan.blocked))

    def test_name_collision_blocks_both_rows(self):
        other = _param(u"Fire Rating", u"{2}")
        other.is_checked = True
        plan = state.build_family_plan([self.param, other], [self.family])
        self.assertEqual(2, len(plan.blocked))
        self.assertEqual([], plan.cells)

    def test_uneditable_family_is_a_named_skip(self):
        self.family.editable = False
        self.family.skip_reason = u"another user has this family checked out"
        plan = state.build_family_plan([self.param], [self.family])
        self.assertEqual([state.SKIP_NOT_EDITABLE],
                         [cell.status for cell in plan.cells])

    def test_read_only_file_is_a_named_skip(self):
        folder = _family(u"Door", source=state.SOURCE_FOLDER,
                         path=u"C:/lib/Door.rfa", is_read_only=True)
        folder.is_checked = True
        plan = state.build_family_plan([self.param], [folder])
        self.assertEqual([state.SKIP_READONLY_FILE],
                         [cell.status for cell in plan.cells])

    def test_older_file_is_flagged_for_upgrade(self):
        folder = _family(u"Door", source=state.SOURCE_FOLDER,
                         path=u"C:/lib/Door.rfa", saved_in_version=u"2019")
        folder.is_checked = True
        plan = state.build_family_plan([self.param], [folder])
        self.assertEqual(1, len(plan.upgrade_files))

    def test_folder_and_project_copies_of_one_family_are_flagged(self):
        folder = _family(u"Door", source=state.SOURCE_FOLDER,
                         path=u"C:/lib/Door.rfa")
        folder.is_checked = True
        plan = state.build_family_plan(
            [self.param], [self.family, folder],
            project_family_names=[u"Door"])
        self.assertEqual([u"door"], plan.overlap_names)


class ProjectPlanTests(unittest.TestCase):
    def setUp(self):
        self.param = _param(u"Fire Rating", u"{1}")
        self.param.is_checked = True
        self.category = state.CategoryRow(u"Doors", 1)
        self.category.is_checked = True

    def test_unbound_parameter_is_an_add(self):
        plan = state.build_project_plan([self.param], [self.category])
        self.assertEqual([state.WILL_ADD],
                         [cell.status for cell in plan.cells])

    def test_already_bound_category_is_an_update(self):
        plan = state.build_project_plan(
            [self.param], [self.category],
            bound_lookup=lambda row: (True, False, [u"Doors"]))
        self.assertEqual([state.WILL_UPDATE],
                         [cell.status for cell in plan.cells])

    def test_instance_type_flip_is_reported(self):
        self.param.is_instance = True
        plan = state.build_project_plan(
            [self.param], [self.category],
            bound_lookup=lambda row: (True, False, [u"Doors"]))
        self.assertEqual([(u"Fire Rating", u"Type", u"Instance")],
                         plan.binding_flips)

    def test_matching_binding_kind_is_not_a_flip(self):
        plan = state.build_project_plan(
            [self.param], [self.category],
            bound_lookup=lambda row: (True, False, [u"Doors"]))
        self.assertEqual([], plan.binding_flips)

    def test_no_categories_means_no_cells(self):
        self.category.is_checked = False
        plan = state.build_project_plan([self.param], [self.category])
        self.assertEqual([], plan.cells)


class ConfirmTextTests(unittest.TestCase):
    def _plan_with_upgrade(self):
        param = _param(u"Fire Rating", u"{1}")
        param.is_checked = True
        folder = _family(u"Door", source=state.SOURCE_FOLDER,
                         path=u"C:/lib/Door.rfa", saved_in_version=u"2019")
        folder.is_checked = True
        return state.build_family_plan([param], [folder])

    def test_upgrade_warning_names_the_consequence(self):
        text = state.build_confirm_text(self._plan_with_upgrade())
        self.assertIn(u"upgraded", text)
        self.assertIn(u"2019", text)

    def test_nested_families_are_called_out(self):
        text = state.build_confirm_text(self._plan_with_upgrade())
        self.assertIn(u"nested", text)

    def test_update_mode_is_stated(self):
        text = state.build_confirm_text(self._plan_with_upgrade(),
                                        state.MODE_UPDATE)
        self.assertIn(u"updated in place", text)

    def test_blocked_rows_are_listed(self):
        param = _param(u"Family Type Param", u"{1}", supported=False,
                       unsupported_reason=u"cannot be recreated")
        param.is_checked = True
        family = _family(u"Door", element_id=1)
        family.is_checked = True
        text = state.build_confirm_text(
            state.build_family_plan([param], [family]))
        self.assertIn(u"Blocked", text)
        self.assertIn(u"cannot be recreated", text)


class SummaryTests(unittest.TestCase):
    def test_rollback_zeroes_what_it_undid(self):
        summary = state.RunSummary()
        summary.note_applied(u"Door", u"Fire Rating")
        summary.note_applied(u"Window", u"Fire Rating")
        summary.clear_applied()
        self.assertEqual([], summary.applied)
        self.assertEqual(2, summary.rolled_back)

    def test_cancelled_report_keeps_the_two_buckets_apart(self):
        summary = state.RunSummary()
        summary.note_applied(u"Door", u"Fire Rating")
        summary.files_written.append(u"C:/lib/Window.rfa")
        summary.clear_applied()
        summary.cancelled = True
        text = state.build_report_text(summary)
        self.assertIn(u"rolled back", text)
        self.assertIn(u"C:/lib/Window.rfa", text)
        self.assertIn(u"no undo", text)

    def test_report_never_claims_rolled_back_work(self):
        summary = state.RunSummary()
        summary.note_applied(u"Door", u"Fire Rating")
        summary.clear_applied()
        summary.cancelled = True
        self.assertNotIn(u"Applied:", state.build_report_text(summary))

    def test_failures_are_reported_with_their_reason(self):
        summary = state.RunSummary()
        summary.note_failed(u"Door", u"Fire Rating", u"name is built-in")
        self.assertIn(u"name is built-in", state.build_report_text(summary))

    def test_headline_matches_the_action(self):
        summary = state.RunSummary()
        summary.note_applied(u"(project)", u"Fire Rating")
        self.assertIn(u"binding",
                      state.build_headline(summary, state.ACTION_PROJECT))
        self.assertIn(u"families",
                      state.build_headline(summary, state.ACTION_FAMILIES))


if __name__ == "__main__":
    unittest.main()
