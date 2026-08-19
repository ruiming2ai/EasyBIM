"""The pure staged-edit model behind the Family Types grid.

Loaded standalone - ``family_types_state`` deliberately imports nothing from
pyrevit or .NET, so every rule below is exercised off Revit.
"""

import importlib.util
import pathlib
import sys
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Family Types.pushbutton"
)


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


st = _load_module("family_types_state", COMMAND_DIR / "family_types_state.py")


PARAM_INFOS = [
    {"name": "Width", "param_id": 101, "storage_type": "Double",
     "spec_label": "length"},
    {"name": "Comments", "param_id": 102, "storage_type": "String"},
    {"name": "Load Bearing", "param_id": 103, "storage_type": "Integer",
     "is_instance": True, "is_yes_no": True},
    {"name": "Volume", "param_id": 104, "storage_type": "Double",
     "is_determined_by_formula": True, "formula": "Width * 2"},
    {"name": "Finish", "param_id": 105, "storage_type": "ElementId",
     "is_material": True},
    {"name": "Head Family", "param_id": 106, "storage_type": "ElementId"},
]


def build_columns():
    return st.fixed_columns() + st.build_parameter_columns(PARAM_INFOS)


def column_for(columns, param_name):
    for column in st.param_columns(columns):
        if column.param_name == param_name:
            return column
    raise AssertionError("no column for %s" % param_name)


def make_row(columns, name, values=None, **kwargs):
    row = st.TypeRowBase(name, **kwargs)
    st.populate_row(row, columns, values or {})
    return row


def sample_grid():
    columns = build_columns()
    rows = [
        make_row(columns, "600mm", {"p:Width": "600 mm",
                                    "p:Comments": "narrow",
                                    "p:Load Bearing": "Yes",
                                    "p:Finish": "Oak"}),
        make_row(columns, "900mm", {"p:Width": "900 mm",
                                    "p:Comments": "wide",
                                    "p:Load Bearing": "No",
                                    "p:Finish": "Pine"}),
    ]
    return columns, rows


class ColumnTests(unittest.TestCase):
    def test_type_parameters_come_before_instance_parameters(self):
        # Same stacking as Revit's own Family Types dialog, so somebody who
        # knows that dialog finds a parameter where they expect it.
        headers = [column.header for column in
                   st.param_columns(build_columns())]
        self.assertEqual(
            ["Comments", "Finish", "Head Family", "Volume", "Width",
             "Load Bearing (default)"],
            headers)

    def test_an_instance_parameter_is_marked_default(self):
        column = column_for(build_columns(), "Load Bearing")
        self.assertTrue(column.is_instance)
        self.assertTrue(column.header.endswith(st.INSTANCE_SUFFIX))
        self.assertIn("Instance parameter", column.tooltip())

    def test_the_type_name_column_is_always_first_and_editable(self):
        columns = build_columns()
        self.assertEqual(st.KIND_TYPE_NAME, columns[0].kind)
        self.assertFalse(columns[0].is_read_only)

    def test_each_parameter_gets_its_own_binding_attribute(self):
        attrs = [column.attr for column in
                 st.param_columns(build_columns())]
        self.assertEqual(len(attrs), len(set(attrs)))


class LockingTests(unittest.TestCase):
    def test_a_formula_driven_parameter_is_locked(self):
        column = column_for(build_columns(), "Volume")
        self.assertEqual(st.LOCK_FORMULA, column.lock_reason)
        self.assertIn("Formula: Width * 2", column.tooltip())

    def test_a_material_parameter_stays_editable_by_name(self):
        # Materials are unique by name inside a family document, so a text
        # cell can resolve one; that is why they are the ElementId exception.
        self.assertEqual(u"", column_for(build_columns(), "Finish").lock_reason)

    def test_any_other_element_reference_is_locked(self):
        self.assertEqual(st.LOCK_ELEMENT_REF,
                         column_for(build_columns(), "Head Family").lock_reason)

    def test_read_only_reporting_and_unmodifiable_all_lock(self):
        cases = [
            ({"is_read_only": True}, st.LOCK_READ_ONLY),
            ({"is_reporting": True}, st.LOCK_REPORTING),
            ({"user_modifiable": False}, st.LOCK_NOT_MODIFIABLE),
        ]
        for extra, expected in cases:
            info = {"name": "P", "storage_type": "Double"}
            info.update(extra)
            self.assertEqual(expected, st.lock_reason_for(info), extra)

    def test_a_locked_cell_refuses_an_edit(self):
        columns, rows = sample_grid()
        formula = column_for(columns, "Volume")
        self.assertFalse(st.can_edit_cell(rows[0], formula))
        self.assertFalse(st.apply_cell_edit(rows[0], formula, "9"))
        self.assertEqual(st.STATE_LOCKED, rows[0].get_state(formula.attr))


class StagingTests(unittest.TestCase):
    def test_an_edit_turns_the_cell_dirty_and_is_counted(self):
        columns, rows = sample_grid()
        width = column_for(columns, "Width")
        self.assertTrue(st.apply_cell_edit(rows[0], width, "650 mm"))
        self.assertEqual(st.STATE_DIRTY, rows[0].get_state(width.attr))
        self.assertEqual(1, st.count_staged_cells(rows, columns))

    def test_editing_back_to_the_original_clears_the_stage(self):
        columns, rows = sample_grid()
        width = column_for(columns, "Width")
        st.apply_cell_edit(rows[0], width, "650 mm")
        st.apply_cell_edit(rows[0], width, "600 mm")
        self.assertEqual(st.STATE_NORMAL, rows[0].get_state(width.attr))
        self.assertEqual(0, st.count_staged_cells(rows, columns))

    def test_a_rename_is_planned_as_a_rename_not_a_new_type(self):
        columns, rows = sample_grid()
        st.apply_cell_edit(rows[1], columns[0], "1200mm")
        changes = st.compute_staged_changes(rows, columns)
        self.assertEqual(1, len(changes.renames))
        self.assertEqual(("900mm", "1200mm"), changes.renames[0][1:])
        self.assertEqual([], changes.new_types)

    def test_a_new_row_is_planned_as_a_new_type_with_all_its_values(self):
        columns, rows = sample_grid()
        row = make_row(columns, "1500mm", {"p:Comments": "extra"})
        st.mark_row_new(row, columns)
        rows.append(row)
        changes = st.compute_staged_changes(rows, columns)
        self.assertEqual([row], changes.new_types)
        self.assertEqual([], changes.renames)
        edited = [column.param_name for _, column, _, _
                  in changes.value_edits]
        self.assertIn("Comments", edited)

    def test_a_staged_delete_is_planned_and_excluded_from_edits(self):
        columns, rows = sample_grid()
        st.apply_cell_edit(rows[1], column_for(columns, "Width"), "950 mm")
        ok, message = st.toggle_delete(rows[1], rows)
        self.assertTrue(ok, message)
        changes = st.compute_staged_changes(rows, columns)
        self.assertEqual([rows[1]], changes.deletes)
        self.assertEqual([], changes.value_edits)

    def test_the_summary_names_every_kind_of_change(self):
        columns, rows = sample_grid()
        st.apply_cell_edit(rows[0], column_for(columns, "Width"), "650 mm")
        text = st.compute_staged_changes(rows, columns).summary_text()
        self.assertEqual("1 value edit(s)", text)
        self.assertEqual("no changes",
                         st.StagedChanges().summary_text())


class TypeNameRuleTests(unittest.TestCase):
    def test_a_duplicate_name_is_flagged_on_every_row_that_shares_it(self):
        columns, rows = sample_grid()
        st.apply_cell_edit(rows[1], columns[0], "600mm")
        problems = st.find_name_problems(rows)
        self.assertEqual(1, len(problems))
        for row in rows:
            self.assertEqual(st.STATE_CONFLICT, row.get_state("type_name"))

    def test_resolving_a_duplicate_clears_the_flag(self):
        columns, rows = sample_grid()
        st.apply_cell_edit(rows[1], columns[0], "600mm")
        st.find_name_problems(rows)
        st.apply_cell_edit(rows[1], columns[0], "1200mm")
        self.assertEqual([], st.find_name_problems(rows))
        for row in rows:
            self.assertNotEqual(st.STATE_CONFLICT,
                                row.get_state("type_name"))

    def test_a_blank_name_is_flagged(self):
        columns, rows = sample_grid()
        st.apply_cell_edit(rows[0], columns[0], "   ")
        problems = st.find_name_problems(rows)
        self.assertEqual(1, len(problems))
        self.assertIn("blank", problems[0])

    def test_names_match_case_and_whitespace_insensitively(self):
        self.assertEqual(st.normalize_name("  600 MM "),
                         st.normalize_name("600 mm"))


class DeleteRuleTests(unittest.TestCase):
    def test_the_last_surviving_type_cannot_be_deleted(self):
        columns = build_columns()
        rows = [make_row(columns, "only")]
        ok, message = st.toggle_delete(rows[0], rows)
        self.assertFalse(ok)
        self.assertIn("at least one type", message)
        self.assertFalse(rows[0].is_marked_deleted)

    def test_deleting_down_to_one_type_is_allowed(self):
        columns, rows = sample_grid()
        self.assertTrue(st.toggle_delete(rows[0], rows)[0])
        self.assertEqual(1, st.surviving_type_count(rows))

    def test_a_delete_can_be_taken_back(self):
        columns, rows = sample_grid()
        st.toggle_delete(rows[0], rows)
        st.toggle_delete(rows[0], rows)
        self.assertFalse(rows[0].is_marked_deleted)
        self.assertEqual(2, st.surviving_type_count(rows))

    def test_the_default_row_can_be_neither_renamed_nor_deleted(self):
        # A family with no named types keeps its values on the current type,
        # which has no name of its own to rename or delete.
        columns = build_columns()
        row = make_row(columns, st.DEFAULT_TYPE_LABEL, is_default_row=True)
        self.assertFalse(row.can_rename)
        self.assertFalse(row.can_delete)
        self.assertFalse(st.apply_cell_edit(row, columns[0], "Nope"))
        self.assertFalse(st.toggle_delete(row, [row])[0])

    def test_the_default_rows_values_are_still_editable(self):
        columns = build_columns()
        row = make_row(columns, st.DEFAULT_TYPE_LABEL, is_default_row=True)
        self.assertTrue(
            st.apply_cell_edit(row, column_for(columns, "Width"), "700 mm"))


class SearchTests(unittest.TestCase):
    def test_search_matches_type_names_values_and_parameter_names(self):
        columns, rows = sample_grid()
        self.assertEqual(1, len(st.search_rows(rows, columns, "600")))
        self.assertEqual(1, len(st.search_rows(rows, columns, "narrow")))
        self.assertEqual(2, len(st.search_rows(rows, columns, "comments")))
        self.assertEqual(2, len(st.search_rows(rows, columns, "")))


class YesNoTests(unittest.TestCase):
    def test_the_usual_spellings_of_yes_and_no_are_understood(self):
        for text in ("Yes", "y", "TRUE", "1", "x"):
            self.assertIs(True, st.parse_bool_cell(text), text)
        for text in ("No", "n", "false", "0"):
            self.assertIs(False, st.parse_bool_cell(text), text)

    def test_anything_else_is_refused_rather_than_guessed(self):
        self.assertIsNone(st.parse_bool_cell("maybe"))
        self.assertIsNone(st.parse_bool_cell(""))


class ExportMatrixTests(unittest.TestCase):
    def test_the_header_carries_the_name_over_the_element_id(self):
        columns = build_columns()
        header, _, _, _ = st.build_export_matrix(columns, [])
        self.assertEqual(u"Family Type", header[0])
        self.assertIn(u"Comments\n102", header)
        self.assertIn(u"Load Bearing (default)\n103", header)

    def test_a_parameter_with_no_id_falls_back_to_a_name_only_header(self):
        column = st.build_parameter_columns(
            [{"name": "Width", "storage_type": "Double"}])[0]
        self.assertEqual(u"Width", st.export_header_cell(column))

    def test_a_header_round_trips_back_to_its_name_and_id(self):
        columns = build_columns()
        cell = st.export_header_cell(column_for(columns, "Load Bearing"))
        header, param_id = st.parse_header_cell(cell)
        self.assertEqual(103, param_id)
        self.assertEqual("Load Bearing", st.header_to_param_name(header))

    def test_read_only_columns_are_flagged_for_locking(self):
        columns = build_columns()
        header, _, _, locks = st.build_export_matrix(columns, [])
        volume_at = header.index(u"Volume\n104")
        comments_at = header.index(u"Comments\n102")
        self.assertTrue(locks[volume_at])
        self.assertFalse(locks[comments_at])

    def test_rows_export_their_staged_values_and_skip_deletions(self):
        columns, rows = sample_grid()
        st.apply_cell_edit(rows[0], column_for(columns, "Width"), "650 mm")
        st.toggle_delete(rows[1], rows)
        _, _, data, _ = st.build_export_matrix(columns, rows)
        self.assertEqual(1, len(data))
        self.assertEqual(u"600mm", data[0][0])
        self.assertIn(u"650 mm", data[0])

    def test_the_metadata_row_describes_every_export_column(self):
        columns = build_columns()
        _, metadata, _, _ = st.build_export_matrix(columns, [])
        self.assertEqual(len(st.param_columns(columns)), len(metadata))
        for row in metadata:
            self.assertEqual(len(st.METADATA_HEADERS), len(row))


class ImportPlanTests(unittest.TestCase):
    def _workbook(self, data_rows, columns=None):
        columns = columns or build_columns()
        header, _, _, _ = st.build_export_matrix(columns, [])
        return [(1, header)] + list(
            enumerate(data_rows, start=2)), columns

    def _row_cells(self, columns, type_name, values):
        cells = [type_name]
        for column in st.param_columns(columns):
            cells.append(values.get(column.param_name, u""))
        return cells

    def test_a_matching_name_with_a_different_value_reads_as_changed(self):
        columns, rows = sample_grid()
        export_rows, _ = self._workbook(
            [self._row_cells(columns, "600mm", {"Width": "650 mm",
                                                "Comments": "narrow",
                                                "Load Bearing": "Yes",
                                                "Finish": "Oak"})],
            columns)
        plan = st.plan_import(export_rows, [], rows, columns)
        entry = plan.entries[0]
        self.assertEqual(st.IMPORT_CHANGED, entry.status)
        self.assertEqual(1, len(entry.edits))
        self.assertTrue(entry.is_selected)

    def test_an_unknown_name_reads_as_a_new_type(self):
        columns, rows = sample_grid()
        export_rows, _ = self._workbook(
            [self._row_cells(columns, "1500mm", {"Comments": "extra"})],
            columns)
        plan = st.plan_import(export_rows, [], rows, columns)
        new = [e for e in plan.entries if e.status == st.IMPORT_NEW]
        self.assertEqual(1, len(new))
        self.assertEqual("1500mm", new[0].type_name)
        self.assertTrue(new[0].is_selected)

    def test_an_identical_row_reads_as_unchanged_and_is_not_ticked(self):
        columns, rows = sample_grid()
        _, _, data, _ = st.build_export_matrix(columns, rows)
        export_rows, _ = self._workbook(data, columns)
        plan = st.plan_import(export_rows, [], rows, columns)
        for entry in plan.entries:
            self.assertEqual(st.IMPORT_UNCHANGED, entry.status)
            self.assertFalse(entry.is_selected)

    def test_a_type_absent_from_the_file_is_listed_but_never_ticked(self):
        columns, rows = sample_grid()
        export_rows, _ = self._workbook(
            [self._row_cells(columns, "600mm", {"Width": "600 mm",
                                                "Comments": "narrow",
                                                "Load Bearing": "Yes",
                                                "Finish": "Oak"})],
            columns)
        plan = st.plan_import(export_rows, [], rows, columns)
        missing = plan.missing()
        self.assertEqual(1, len(missing))
        self.assertEqual("900mm", missing[0].type_name)
        self.assertFalse(missing[0].is_selected)

    def test_a_write_to_a_read_only_column_is_counted_not_staged(self):
        columns, rows = sample_grid()
        export_rows, _ = self._workbook(
            [self._row_cells(columns, "600mm", {"Volume": "999"})], columns)
        plan = st.plan_import(export_rows, [], rows, columns)
        self.assertEqual(1, plan.skipped_locked)
        for _, column in [(e, c) for e in plan.entries
                          for c, _ in e.edits]:
            self.assertFalse(column.is_read_only)

    def test_columns_match_on_the_id_even_when_the_name_changed(self):
        # The id is the round-trip key precisely because it survives a rename
        # in Revit, which the header text does not.
        columns, rows = sample_grid()
        header = [u"Family Type", u"Renamed In Revit\n102"]
        export_rows = [(1, header), (2, [u"600mm", u"different"])]
        plan = st.plan_import(export_rows, [], rows, columns)
        self.assertEqual([], plan.unmatched_columns)
        self.assertEqual(st.IMPORT_CHANGED, plan.entries[0].status)
        self.assertEqual("Comments", plan.entries[0].edits[0][0].param_name)

    def test_columns_fall_back_to_the_name_when_there_is_no_id(self):
        columns, rows = sample_grid()
        export_rows = [(1, [u"Family Type", u"Comments"]),
                       (2, [u"600mm", u"different"])]
        plan = st.plan_import(export_rows, [], rows, columns)
        self.assertEqual([], plan.unmatched_columns)
        self.assertEqual("Comments", plan.entries[0].edits[0][0].param_name)

    def test_a_column_that_matches_nothing_is_reported_not_guessed(self):
        columns, rows = sample_grid()
        export_rows = [(1, [u"Family Type", u"Not A Parameter\n999"]),
                       (2, [u"600mm", u"x"])]
        plan = st.plan_import(export_rows, [], rows, columns)
        self.assertEqual([u"Not A Parameter"], plan.unmatched_columns)
        self.assertEqual(st.IMPORT_UNCHANGED, plan.entries[0].status)

    def test_blank_and_duplicate_file_rows_are_ignored(self):
        columns, rows = sample_grid()
        export_rows = [(1, [u"Family Type", u"Comments\n102"]),
                       (2, [u"", u""]),
                       (3, [u"600mm", u"one"]),
                       (4, [u"600mm", u"two"])]
        plan = st.plan_import(export_rows, [], rows, columns)
        named = [e for e in plan.entries if e.type_name == "600mm"]
        self.assertEqual(1, len(named))
        self.assertEqual(u"one", named[0].edits[0][1])


class ImportApplyTests(unittest.TestCase):
    def _plan_for(self, columns, rows, data_rows):
        header, _, _, _ = st.build_export_matrix(columns, [])
        export_rows = [(1, header)] + list(enumerate(data_rows, start=2))
        return st.plan_import(export_rows, [], rows, columns)

    def _cells(self, columns, type_name, values):
        cells = [type_name]
        for column in st.param_columns(columns):
            cells.append(values.get(column.param_name, u""))
        return cells

    def test_only_the_ticked_entries_are_staged(self):
        columns, rows = sample_grid()
        plan = self._plan_for(columns, rows, [
            self._cells(columns, "600mm", {"Comments": "changed"}),
            self._cells(columns, "1500mm", {"Comments": "brand new"}),
        ])
        chosen = [e.key for e in plan.entries
                  if e.status == st.IMPORT_CHANGED]
        result = st.apply_import_selection(
            plan, chosen, rows, columns, st.TypeRowBase)
        self.assertEqual(1, result.updated)
        self.assertEqual(0, result.created)
        self.assertEqual(2, len(rows))

    def test_a_new_type_is_added_as_a_fully_staged_row(self):
        columns, rows = sample_grid()
        plan = self._plan_for(columns, rows, [
            self._cells(columns, "1500mm", {"Comments": "brand new"})])
        st.apply_import_selection(
            plan, [e.key for e in plan.selectable()], rows, columns,
            st.TypeRowBase)
        added = rows[-1]
        self.assertTrue(added.is_new)
        self.assertEqual("1500mm", added.type_name)
        changes = st.compute_staged_changes(rows, columns)
        self.assertEqual([added], changes.new_types)

    def test_missing_types_are_left_alone_unless_delete_is_asked_for(self):
        columns, rows = sample_grid()
        plan = self._plan_for(columns, rows, [
            self._cells(columns, "600mm", {"Comments": "narrow"})])
        st.apply_import_selection(plan, [], rows, columns, st.TypeRowBase)
        self.assertFalse(rows[1].is_marked_deleted)

    def test_delete_missing_marks_them_without_writing_anything(self):
        columns, rows = sample_grid()
        plan = self._plan_for(columns, rows, [
            self._cells(columns, "600mm", {"Comments": "narrow"})])
        result = st.apply_import_selection(
            plan, [], rows, columns, st.TypeRowBase, delete_missing=True)
        self.assertEqual(1, result.deleted)
        self.assertTrue(rows[1].is_marked_deleted)

    def test_delete_missing_still_keeps_one_type_alive(self):
        columns, rows = sample_grid()
        plan = self._plan_for(columns, rows, [
            self._cells(columns, "not in family", {"Comments": "x"})])
        result = st.apply_import_selection(
            plan, [], rows, columns, st.TypeRowBase, delete_missing=True)
        self.assertEqual(1, result.deleted)
        self.assertEqual(1, len(result.refused_deletes))
        self.assertEqual(1, st.surviving_type_count(rows))


if __name__ == "__main__":
    unittest.main()
