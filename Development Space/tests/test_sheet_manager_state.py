import importlib.util
import pathlib
import sys
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Sheet.panel"
    / "Sheet Manager.pushbutton"
)


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


st = _load_module(
    "sheet_manager_state", COMMAND_DIR / "sheet_manager_state.py")


REVISIONS = [
    {"id": 11, "sequence": 1, "number": "1", "description": "Revision 1"},
    {"id": 22, "sequence": 2, "number": "2", "description": "Revision 2"},
]


def build_columns(with_params=True):
    columns = st.fixed_columns()
    if with_params:
        columns.append(st.ColumnSpec(
            "p:Drawn By", st.KIND_SHEET_PARAM, "Drawn By", "p0",
            param_name="Drawn By"))
        columns.append(st.ColumnSpec(
            "tb:Sheet Width", st.KIND_TB_PARAM, "TB: Sheet Width", "p1",
            param_name="Sheet Width"))
        columns.append(st.ColumnSpec(
            "p:Locked", st.KIND_SHEET_PARAM, "Locked", "p2",
            param_name="Locked", is_read_only=True))
    columns += st.build_revision_columns(REVISIONS)
    return columns


def make_row(columns, sheet_id=1, number="A101", name="Plan",
             tblock_count=1, is_placeholder=False, cloud_ids=(),
             checked_revisions=(), values=None):
    row = st.SheetRowBase(sheet_id, number, name,
                          is_placeholder=is_placeholder,
                          tblock_count=tblock_count)
    row.revisions_cloud = set(cloud_ids)
    initial = dict(values or {})
    for revision_id in checked_revisions:
        initial["rev:{0}".format(revision_id)] = True
    st.populate_row(row, columns, initial)
    return row


def column_by_key(columns, key):
    for column in columns:
        if column.key == key:
            return column
    raise AssertionError("missing column " + key)


class ColumnModelTests(unittest.TestCase):
    def test_revision_columns_ordered_with_labels(self):
        columns = st.build_revision_columns(REVISIONS)
        self.assertEqual([c.attr for c in columns], ["r0", "r1"])
        self.assertEqual(columns[0].header, "Rev 1")
        self.assertEqual(columns[0].revision_label, "1 - Revision 1")
        self.assertEqual(columns[1].revision_id, 22)

    def test_next_attr_index_skips_used_suffixes(self):
        columns = build_columns()
        self.assertEqual(st.next_attr_index(columns, "p"), 3)
        self.assertEqual(st.next_attr_index(columns, "r"), 2)
        self.assertEqual(st.next_attr_index(columns, "s"), 0)


class PopulateTests(unittest.TestCase):
    def test_every_attr_and_state_exists(self):
        columns = build_columns()
        row = make_row(columns, checked_revisions=[11],
                       values={"p:Drawn By": "AB"})
        for column in columns:
            if column.kind in (st.KIND_SELECT, st.KIND_INDEX):
                continue
            self.assertTrue(hasattr(row, column.attr), column.attr)
            self.assertTrue(hasattr(row, column.attr + "_state"))
        self.assertTrue(row.r0)
        self.assertFalse(row.r1)
        self.assertEqual(row.p0, "AB")

    def test_multi_titleblock_cells_duplicated_and_locked(self):
        columns = build_columns()
        row = make_row(columns, tblock_count=2,
                       values={"tb:Sheet Width": "36"})
        self.assertEqual(row.p1, st.DUPLICATED_TEXT)
        self.assertEqual(row.get_state("p1"), st.STATE_DUPLICATED)
        self.assertFalse(st.can_edit_cell(
            row, column_by_key(columns, "tb:Sheet Width")))

    def test_placeholder_tb_cells_locked_and_blank(self):
        columns = build_columns()
        row = make_row(columns, tblock_count=0, is_placeholder=True,
                       values={"tb:Sheet Width": "36"})
        self.assertEqual(row.p1, "")
        self.assertEqual(row.get_state("p1"), st.STATE_LOCKED)

    def test_readonly_param_locked(self):
        columns = build_columns()
        row = make_row(columns, values={"p:Locked": "x"})
        self.assertEqual(row.get_state("p2"), st.STATE_LOCKED)
        self.assertFalse(st.can_edit_cell(
            row, column_by_key(columns, "p:Locked")))

    def test_cloud_revision_state(self):
        columns = build_columns()
        row = make_row(columns, cloud_ids=[22],
                       checked_revisions=[11, 22])
        self.assertEqual(row.get_state("r0"), st.STATE_NORMAL)
        self.assertEqual(row.get_state("r1"), st.STATE_CLOUD)


class EditTests(unittest.TestCase):
    def test_edit_dirty_then_revert_clears(self):
        columns = build_columns()
        column = column_by_key(columns, "p:Drawn By")
        row = make_row(columns, values={"p:Drawn By": "AB"})
        self.assertTrue(st.apply_cell_edit(row, column, "CD"))
        self.assertEqual(row.get_state("p0"), st.STATE_DIRTY)
        self.assertTrue(st.apply_cell_edit(row, column, "AB"))
        self.assertEqual(row.get_state("p0"), st.STATE_NORMAL)

    def test_locked_and_duplicated_rejected(self):
        columns = build_columns()
        row = make_row(columns, tblock_count=2)
        self.assertFalse(st.apply_cell_edit(
            row, column_by_key(columns, "tb:Sheet Width"), "X"))
        self.assertFalse(st.apply_cell_edit(
            row, column_by_key(columns, "p:Locked"), "X"))

    def test_propagate_skips_number_name_and_locked(self):
        columns = build_columns()
        rows = [make_row(columns, sheet_id=i, number="A%d" % i)
                for i in range(3)]
        rows[2].tblock_count = 2
        self.assertEqual(st.propagate_edit(
            rows, column_by_key(columns, "number"), "X"), [])
        self.assertEqual(st.propagate_edit(
            rows, column_by_key(columns, "name"), "X"), [])
        changed = st.propagate_edit(
            rows, column_by_key(columns, "tb:Sheet Width"), "42",
            skip_row=rows[0])
        self.assertEqual(changed, [rows[1]])

    def test_revision_toggle_dirty_and_propagate(self):
        columns = build_columns()
        column = column_by_key(columns, "rev:11")
        rows = [make_row(columns, sheet_id=i) for i in range(3)]
        self.assertTrue(st.apply_revision_toggle(rows[0], column, True))
        self.assertEqual(rows[0].get_state("r0"), st.STATE_DIRTY)
        changed = st.propagate_revision_toggle(
            rows, column, True, skip_row=rows[0])
        self.assertEqual(len(changed), 2)
        self.assertEqual(
            st.count_staged_cells(rows, columns), 3)


class StagedChangesTests(unittest.TestCase):
    def test_routing_of_revision_changes(self):
        columns = build_columns()
        row = make_row(columns, cloud_ids=[22],
                       checked_revisions=[11, 22])
        row.hidden_cloud_revs = set()
        st.apply_revision_toggle(row, column_by_key(columns, "rev:11"),
                                 False)
        st.apply_revision_toggle(row, column_by_key(columns, "rev:22"),
                                 False)
        changes = st.compute_staged_changes([row], columns)
        self.assertEqual([x[2] for x in changes.revision_removes], [11])
        self.assertEqual(
            [x[2] for x in changes.cloud_hide_requests], [22])

    def test_hidden_cloud_check_routes_to_unhide(self):
        columns = build_columns()
        row = make_row(columns)
        row.hidden_cloud_revs = set([11])
        st.apply_revision_toggle(row, column_by_key(columns, "rev:11"),
                                 True)
        changes = st.compute_staged_changes([row], columns)
        self.assertEqual(
            [x[2] for x in changes.cloud_unhide_candidates], [11])
        self.assertEqual(changes.revision_adds, [])

    def test_pending_rows_skip_rename_but_keep_params(self):
        columns = build_columns()
        row = st.SheetRowBase(None, "A200", "New", False, 0)
        st.populate_row(row, columns, {"rev:11": True})
        st.apply_cell_edit(row, column_by_key(columns, "p:Drawn By"),
                           "ZZ")
        st.mark_pending_row_dirty(row, columns)
        changes = st.compute_staged_changes([row], columns)
        self.assertEqual(changes.pending_sheets, [row])
        self.assertEqual(changes.renames, [])
        self.assertEqual(changes.name_edits, [])
        self.assertEqual([x[2] for x in changes.revision_adds], [11])
        self.assertEqual(len(changes.param_edits), 1)


class NumberPlanningTests(unittest.TestCase):
    def test_simple_rename(self):
        temp, final = st.plan_number_assignments(
            [(1, "A101", "A150")], ["A101", "A102"])
        self.assertEqual(final, [(1, "A150")])
        self.assertEqual(len(temp), 1)
        self.assertNotIn(temp[0][1], ("A101", "A102", "A150"))

    def test_swap_and_cycle_unique_temps(self):
        renames = [(1, "A1", "A2"), (2, "A2", "A3"), (3, "A3", "A1")]
        temp, final = st.plan_number_assignments(
            renames, ["A1", "A2", "A3"])
        temp_numbers = [n for _, n in temp]
        self.assertEqual(len(set(temp_numbers)), 3)
        for number in temp_numbers:
            self.assertNotIn(number, ("A1", "A2", "A3"))
        self.assertEqual(final, [(1, "A2"), (2, "A3"), (3, "A1")])

    def test_temp_numbers_contain_no_prohibited_characters(self):
        # Regression: temps used "~", which Revit rejects in sheet numbers.
        renames = [(1, "11070-P061-001", "11070-P061-002"),
                   (2, "11070-P061-002", "11070-P061-001")]
        temp, _ = st.plan_number_assignments(
            renames, ["11070-P061-001", "11070-P061-002"])
        for _, number in temp:
            self.assertEqual(st.invalid_number_chars(number), [], number)

    def test_invalid_number_chars(self):
        self.assertEqual(st.invalid_number_chars("11070-P061-001"), [])
        self.assertEqual(st.invalid_number_chars("A1.01 East"), [])
        self.assertEqual(st.invalid_number_chars("A~1"), ["~"])
        self.assertEqual(st.invalid_number_chars("A[1]{2}"),
                         ["[", "]", "{", "}"])

    def test_find_number_problems(self):
        columns = build_columns()
        rows = [make_row(columns, sheet_id=1, number="A101"),
                make_row(columns, sheet_id=2, number="A101"),
                make_row(columns, sheet_id=3, number=" ")]
        empty_rows, duplicates = st.find_number_problems(rows)
        self.assertEqual(len(empty_rows), 1)
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0][0], "A101")
        self.assertEqual(len(duplicates[0][1]), 2)


class NumberConflictTests(unittest.TestCase):
    """Swaps stage as transient collisions marked 'conflict' (orange)."""

    def test_half_staged_swap_flags_both_then_clears(self):
        columns = build_columns()
        number_col = column_by_key(columns, "number")
        row_a = make_row(columns, sheet_id=1, number="A101", name="A")
        row_b = make_row(columns, sheet_id=2, number="A102", name="B")
        self.assertTrue(st.apply_cell_edit(row_a, number_col, "A102"))
        count = st.refresh_number_conflicts([row_a, row_b], number_col)
        self.assertEqual(count, 2)
        self.assertEqual(row_a.get_state("number"), st.STATE_CONFLICT)
        self.assertEqual(row_b.get_state("number"), st.STATE_CONFLICT)
        # finish the swap
        self.assertTrue(st.apply_cell_edit(row_b, number_col, "A101"))
        count = st.refresh_number_conflicts([row_a, row_b], number_col)
        self.assertEqual(count, 0)
        self.assertEqual(row_a.get_state("number"), st.STATE_DIRTY)
        self.assertEqual(row_b.get_state("number"), st.STATE_DIRTY)

    def test_revert_clears_conflict_back_to_normal(self):
        columns = build_columns()
        number_col = column_by_key(columns, "number")
        row_a = make_row(columns, sheet_id=1, number="A101")
        row_b = make_row(columns, sheet_id=2, number="A102")
        st.apply_cell_edit(row_a, number_col, "A102")
        st.refresh_number_conflicts([row_a, row_b], number_col)
        st.apply_cell_edit(row_a, number_col, "A101")
        st.refresh_number_conflicts([row_a, row_b], number_col)
        self.assertEqual(row_a.get_state("number"), st.STATE_NORMAL)
        self.assertEqual(row_b.get_state("number"), st.STATE_NORMAL)

    def test_empty_number_counts_as_conflict(self):
        columns = build_columns()
        row = make_row(columns, sheet_id=1, number=" ")
        self.assertEqual(len(st.conflicted_number_rows([row])), 1)


class FilterTests(unittest.TestCase):
    def test_all_ten_ops(self):
        cases = [
            ("AB", "equals", "ab", True),
            ("AB", "not_equals", "cd", True),
            ("Plan HVAC", "contains", "hvac", True),
            ("Plan HVAC", "not_contains", "roof", True),
            ("A101", "begins", "a1", True),
            ("A101", "ends", "01", True),
            ("10", "greater", "9", True),
            ("9", "less", "10", True),
            ("x", "has_value", "", True),
            ("  ", "no_value", "", True),
        ]
        for value, op, arg, expected in cases:
            self.assertEqual(
                st.evaluate_filter_rule(value, op, arg), expected,
                (value, op, arg))
        self.assertFalse(st.evaluate_filter_rule("2", "greater", "10"))
        self.assertTrue(st.evaluate_filter_rule("b", "greater", "a"))

    def test_rules_and_extra_lookup(self):
        columns = build_columns()
        row_a = make_row(columns, sheet_id=1,
                         values={"p:Drawn By": "AB"})
        row_b = make_row(columns, sheet_id=2,
                         values={"p:Drawn By": "CD"})
        column_map = st.columns_by_key(columns)
        rows = st.filter_rows_by_rules(
            [row_a, row_b], column_map,
            [("p:Drawn By", "equals", "ab")])
        self.assertEqual(rows, [row_a])
        rows = st.filter_rows_by_rules(
            [row_a, row_b], column_map,
            [("tb:Missing", "equals", "X")],
            extra_lookup=lambda row, key:
            "X" if row is row_b else "Y")
        self.assertEqual(rows, [row_b])

    def test_revision_filter_inactive_when_all_or_empty(self):
        columns = build_columns()
        row_a = make_row(columns, sheet_id=1, checked_revisions=[11])
        row_b = make_row(columns, sheet_id=2)
        all_ids = set([11, 22])
        self.assertEqual(
            st.filter_rows_by_revisions([row_a, row_b], columns,
                                        set([11, 22]), all_ids),
            [row_a, row_b])
        self.assertEqual(
            st.filter_rows_by_revisions([row_a, row_b], columns,
                                        set(), all_ids),
            [row_a, row_b])
        self.assertEqual(
            st.filter_rows_by_revisions([row_a, row_b], columns,
                                        set([11]), all_ids),
            [row_a])


class SortSearchTests(unittest.TestCase):
    def test_multilevel_sort_stable_and_numeric(self):
        columns = build_columns()
        column_map = st.columns_by_key(columns)
        rows = [
            make_row(columns, 1, "3", "C", values={"p:Drawn By": "X"}),
            make_row(columns, 2, "10", "A", values={"p:Drawn By": "X"}),
            make_row(columns, 3, "2", "B", values={"p:Drawn By": "Y"}),
        ]
        by_number = st.sort_rows(rows, column_map, [("number", True)])
        self.assertEqual([r.number for r in by_number], ["2", "3", "10"])
        multi = st.sort_rows(
            rows, column_map,
            [("p:Drawn By", True), ("number", False)])
        self.assertEqual([r.number for r in multi], ["10", "3", "2"])

    def test_search_covers_params_not_revisions(self):
        columns = build_columns()
        row = make_row(columns, values={"p:Drawn By": "Zebra"},
                       checked_revisions=[11])
        self.assertEqual(
            st.search_rows([row], columns, "zebra"), [row])
        self.assertEqual(st.search_rows([row], columns, "True"), [])

    def test_search_replace_case_modes_and_locked_skip(self):
        columns = build_columns()
        row = make_row(columns, number="A101", name="Plan Level 1",
                       tblock_count=2, values={"p:Drawn By": "Plan"})
        plan = st.plan_search_replace([row], columns, "Plan", "Sheet")
        keys = sorted(column.key for _, column, _, _ in plan)
        self.assertEqual(keys, ["name", "p:Drawn By"])
        plan_ci = st.plan_search_replace(
            [row], columns, "pLAN", "Sheet", match_case=False)
        self.assertEqual(len(plan_ci), 2)
        self.assertEqual(plan_ci[0][3], "Sheet Level 1")
        self.assertEqual(
            st.plan_search_replace([row], columns, "Plan", "Plan"), [])


class ExportMatrixTests(unittest.TestCase):
    def test_matrix_headers_values_locks_metadata(self):
        columns = build_columns()
        row = make_row(columns, sheet_id=7, checked_revisions=[22],
                       values={"p:Drawn By": "AB",
                               "tb:Sheet Width": "36"})
        headers, metadata, data, locks = st.build_export_matrix(
            columns, [row])
        self.assertEqual(headers[0], "ElementId")
        self.assertEqual(
            headers[1:4], ["Sheet Number", "Sheet Name", "Drawn By"])
        self.assertEqual(data[0][0], "7")
        self.assertEqual(data[0][-2:], ["No", "Yes"])
        self.assertTrue(locks[0][0])
        readonly_meta = [m for m in metadata if m[0] == "p:Locked"][0]
        self.assertEqual(readonly_meta[7], "Yes")
        revision_meta = [m for m in metadata if m[0] == "rev:11"][0]
        self.assertEqual(revision_meta[5], "11")
        self.assertEqual(revision_meta[6], "1")


if __name__ == "__main__":
    unittest.main()
