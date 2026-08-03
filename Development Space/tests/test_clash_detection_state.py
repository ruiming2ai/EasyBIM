import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "clash_detection_state.py"
)

spec = importlib.util.spec_from_file_location("clash_detection_state", str(MODULE_PATH))
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


HOST = state.HOST_MODEL_REF
LINK = "link:900"


def _info(model_ref, element_id, family="Fam", type_name="Type", category="Pipes"):
    return state.ElementInfo(
        model_ref=model_ref,
        model_label="Current Project" if model_ref == HOST else "Structure.rvt",
        element_id=element_id,
        category_name=category,
        family_name=family,
        type_name=type_name,
    )


def _session(
    left_ids=(1,),
    right_ids=(2,),
    left_ref=HOST,
    right_ref=HOST,
    silent_mode=True,
):
    side_a = state.SideSelection(model_ref=left_ref, category_ids=left_ids, slot="a")
    side_b = state.SideSelection(model_ref=right_ref, category_ids=right_ids, slot="b")
    return state.ClashSession(side_a=side_a, side_b=side_b, silent_mode=silent_mode)


class IdentityTests(unittest.TestCase):
    def test_link_ref_roundtrip(self):
        ref = state.link_model_ref(900)
        self.assertEqual(ref, LINK)
        self.assertTrue(state.is_link_ref(ref))
        self.assertFalse(state.is_link_ref(HOST))
        self.assertEqual(state.link_instance_id_from_ref(ref), 900)
        self.assertIsNone(state.link_instance_id_from_ref(HOST))

    def test_link_ref_rejects_non_numeric(self):
        self.assertIsNone(state.link_model_ref("not-an-id"))

    def test_element_key_includes_model(self):
        self.assertEqual(state.element_key(HOST, 7), "host#7")
        self.assertEqual(state.element_key(LINK, 7), "link:900#7")
        self.assertNotEqual(state.element_key(HOST, 7), state.element_key(LINK, 7))

    def test_pair_key_is_order_independent(self):
        forward = state.pair_key(HOST, 5, HOST, 9)
        backward = state.pair_key(HOST, 9, HOST, 5)
        self.assertIsNotNone(forward)
        self.assertEqual(forward, backward)

    def test_pair_key_across_models_is_order_independent(self):
        self.assertEqual(
            state.pair_key(HOST, 5, LINK, 9),
            state.pair_key(LINK, 9, HOST, 5),
        )

    def test_pair_key_rejects_self_and_bad_ids(self):
        self.assertIsNone(state.pair_key(HOST, 5, HOST, 5))
        self.assertIsNone(state.pair_key(HOST, None, HOST, 5))

    def test_element_keys_in_pair_splits_back(self):
        key = state.pair_key(HOST, 5, LINK, 9)
        parts = state.element_keys_in_pair(key)
        self.assertEqual(len(parts), 2)
        self.assertIn(state.element_key(HOST, 5), parts)
        self.assertIn(state.element_key(LINK, 9), parts)


class ElementInfoTests(unittest.TestCase):
    def test_labels_combine_family_and_type(self):
        info = _info(HOST, 42, family="Round Duct", type_name="Taps")
        self.assertEqual(info.family_type_label, "Round Duct : Taps")
        self.assertEqual(info.display_label, "Pipes - Round Duct : Taps")
        self.assertEqual(info.detail_label, "ID: 42")
        self.assertFalse(info.is_linked)

    def test_linked_detail_names_the_link(self):
        info = _info(LINK, 42)
        self.assertTrue(info.is_linked)
        self.assertIn("Structure.rvt", info.detail_label)
        self.assertIn("ID: 42", info.detail_label)

    def test_missing_names_degrade(self):
        info = _info(HOST, 42, family="", type_name="", category="")
        self.assertEqual(info.family_type_label, "(Unknown Type)")
        self.assertEqual(info.display_label, "(Unknown Type)")

    def test_family_only_and_type_only(self):
        self.assertEqual(_info(HOST, 1, type_name="").family_type_label, "Fam")
        self.assertEqual(_info(HOST, 1, family="").family_type_label, "Type")


class CategoryPickerTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            state.CategoryOption("Pipes", 1),
            state.CategoryOption("ducts", 2),
            state.CategoryOption("Cable Trays", 3),
        ]

    def test_sort_is_case_insensitive(self):
        names = [row.name for row in state.sort_categories(self.rows)]
        self.assertEqual(names, ["Cable Trays", "ducts", "Pipes"])

    def test_select_all_none_invert(self):
        state.select_all(self.rows)
        self.assertEqual(state.checked_category_ids(self.rows), {1, 2, 3})
        state.select_none(self.rows)
        self.assertEqual(state.checked_category_ids(self.rows), set())
        self.rows[0].is_checked = True
        state.invert_selection(self.rows)
        self.assertEqual(state.checked_category_ids(self.rows), {2, 3})

    def test_restore_selection_sets_and_clears(self):
        self.rows[0].is_checked = True
        state.restore_category_selection(self.rows, [2, 3])
        self.assertEqual(state.checked_category_ids(self.rows), {2, 3})

    def test_bulk_toggle_returns_only_changed_rows(self):
        self.rows[0].is_checked = True
        changed = state.apply_check_to_rows(self.rows, True)
        self.assertEqual([row.name for row in changed], ["ducts", "Cable Trays"])
        self.assertEqual(state.checked_category_ids(self.rows), {1, 2, 3})

    def test_bulk_toggle_off_is_idempotent(self):
        self.assertEqual(state.apply_check_to_rows(self.rows, False), [])

    def test_can_start_needs_both_sides(self):
        self.assertTrue(state.can_start({1}, {2}))
        self.assertFalse(state.can_start({1}, set()))
        self.assertFalse(state.can_start(set(), {2}))
        self.assertFalse(state.can_start(None, None))


class SideSelectionTests(unittest.TestCase):
    def test_watches_and_owns(self):
        side = state.SideSelection(model_ref=LINK, category_ids=[1, 2])
        self.assertTrue(side.watches(1))
        self.assertFalse(side.watches(3))
        self.assertTrue(side.owns(LINK))
        self.assertFalse(side.owns(HOST))
        self.assertFalse(side.is_host)

    def test_host_side_is_host(self):
        self.assertTrue(state.SideSelection().is_host)


class TargetSideTests(unittest.TestCase):
    def test_host_vs_host_checks_opposite_side(self):
        session = _session(left_ids=(1,), right_ids=(2,))
        targets = session.target_sides_for(HOST, 1)
        self.assertEqual([side.slot for side in targets], ["b"])
        self.assertEqual([side.slot for side in session.target_sides_for(HOST, 2)], ["a"])

    def test_unwatched_category_has_no_targets(self):
        session = _session()
        self.assertEqual(session.target_sides_for(HOST, 99), [])
        self.assertFalse(session.watches(HOST, 99))

    def test_same_category_on_both_sides_checks_both(self):
        session = _session(left_ids=(1,), right_ids=(1,))
        self.assertEqual(
            sorted(side.slot for side in session.target_sides_for(HOST, 1)),
            ["a", "b"],
        )

    def test_link_side_only_matches_its_own_model(self):
        session = _session(left_ref=HOST, right_ref=LINK, left_ids=(1,), right_ids=(1,))
        self.assertEqual([side.slot for side in session.target_sides_for(HOST, 1)], ["b"])
        self.assertEqual([side.slot for side in session.target_sides_for(LINK, 1)], ["a"])

    def test_watched_model_refs_and_category_ids(self):
        session = _session(left_ref=HOST, right_ref=LINK, left_ids=(1,), right_ids=(5,))
        self.assertEqual(session.watched_model_refs(), {HOST, LINK})
        self.assertEqual(session.category_ids_for_model(HOST), {1})
        self.assertEqual(session.category_ids_for_model(LINK), {5})


class QueueTests(unittest.TestCase):
    def test_added_and_modified_queue_as_host_items(self):
        session = _session()
        session.enqueue_changes(added_ids=[1], modified_ids=[2], now=10.0)
        self.assertEqual(session.queue_size, 2)
        self.assertEqual(session.take_batch(), [(HOST, 1), (HOST, 2)])
        self.assertEqual(session.last_change_at, 10.0)

    def test_duplicates_are_collapsed(self):
        session = _session()
        session.enqueue_changes(added_ids=[1, 1], modified_ids=[1], now=0.0)
        self.assertEqual(session.queue_size, 1)

    def test_deleted_ids_leave_the_queue(self):
        session = _session()
        session.enqueue_changes(added_ids=[1, 2], now=0.0)
        session.enqueue_changes(deleted_ids=[1], now=1.0)
        self.assertEqual(session.take_batch(), [(HOST, 2)])
        self.assertEqual(session.take_deleted(), [1])
        self.assertEqual(session.take_deleted(), [])

    def test_link_items_queue_under_their_own_model(self):
        session = _session()
        session.enqueue_elements(LINK, [7, 8], now=5.0)
        self.assertEqual(session.take_batch(), [(LINK, 7), (LINK, 8)])
        self.assertEqual(session.last_change_at, 5.0)

    def test_queue_cap_drops_oldest(self):
        original = state.MAX_QUEUE
        state.MAX_QUEUE = 3
        try:
            session = _session()
            session.enqueue_changes(added_ids=[1, 2, 3, 4, 5], now=0.0)
            self.assertEqual(session.queue_size, 3)
            self.assertEqual(session.dropped_change_count, 2)
            self.assertEqual(
                session.take_batch(), [(HOST, 3), (HOST, 4), (HOST, 5)]
            )
        finally:
            state.MAX_QUEUE = original

    def test_take_one_pops_in_order(self):
        session = _session()
        session.enqueue_changes(added_ids=[1, 2], now=0.0)
        self.assertEqual(session.take_one(), (HOST, 1))
        self.assertEqual(session.take_one(), (HOST, 2))
        self.assertIsNone(session.take_one())

    def test_take_batch_respects_limit(self):
        session = _session()
        session.enqueue_changes(added_ids=[1, 2, 3], now=0.0)
        self.assertEqual(len(session.take_batch(limit=2)), 2)
        self.assertEqual(session.queue_size, 1)

    def test_invalid_ids_are_ignored(self):
        session = _session()
        session.enqueue_changes(added_ids=[None, "abc"], now=0.0)
        self.assertEqual(session.queue_size, 0)


class ThrottleTests(unittest.TestCase):
    def test_no_work_never_runs(self):
        session = _session()
        self.assertFalse(session.has_work())
        self.assertFalse(session.should_run(1000.0))

    def test_debounce_waits_for_the_burst_to_settle(self):
        session = _session()
        session.enqueue_changes(added_ids=[1], now=100.0)
        self.assertFalse(session.should_run(100.0 + state.DEBOUNCE_SEC / 2))
        self.assertTrue(session.should_run(100.0 + state.DEBOUNCE_SEC * 2))

    def test_a_later_edit_restarts_the_debounce(self):
        session = _session()
        session.enqueue_changes(added_ids=[1], now=100.0)
        session.enqueue_changes(added_ids=[2], now=100.0 + state.DEBOUNCE_SEC)
        self.assertFalse(session.should_run(100.0 + state.DEBOUNCE_SEC * 1.5))
        self.assertTrue(session.should_run(100.0 + state.DEBOUNCE_SEC * 3))

    def test_pending_deletes_alone_are_work(self):
        session = _session()
        session.enqueue_changes(deleted_ids=[1], now=100.0)
        self.assertTrue(session.has_work())
        self.assertTrue(session.should_run(100.0 + state.DEBOUNCE_SEC * 2))

    def test_budget(self):
        session = _session()
        self.assertFalse(session.budget_exceeded(10.0, 10.0))
        self.assertTrue(session.budget_exceeded(10.0, 10.0 + state.BUDGET_SEC * 2))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.session = _session()
        self.key = state.pair_key(HOST, 1, HOST, 2)

    def _record(self, key=None, a=1, b=2):
        return self.session.record_clash(
            key or self.key, _info(HOST, a), _info(HOST, b)
        )

    def test_first_record_wins_and_duplicates_are_ignored(self):
        self.assertIsNotNone(self._record())
        self.assertIsNone(self._record())
        self.assertEqual(self.session.pair_count, 1)

    def test_reverse_order_is_the_same_pair(self):
        self._record()
        reversed_key = state.pair_key(HOST, 2, HOST, 1)
        self.assertTrue(self.session.has_pair(reversed_key))
        self.assertIsNone(
            self.session.record_clash(reversed_key, _info(HOST, 2), _info(HOST, 1))
        )

    def test_new_pairs_drain_once(self):
        self._record()
        self.assertEqual(len(self.session.drain_new_pairs()), 1)
        self.assertEqual(self.session.drain_new_pairs(), [])

    def test_records_are_ordered_by_detection_sequence(self):
        self._record()
        self._record(state.pair_key(HOST, 3, HOST, 4), 3, 4)
        self.assertEqual(
            [record.sequence for record in self.session.records()], [1, 2]
        )

    def test_pair_cap_stops_recording_and_flags(self):
        original = state.MAX_PAIRS
        state.MAX_PAIRS = 2
        try:
            session = _session()
            for index in range(4):
                session.record_clash(
                    state.pair_key(HOST, index, HOST, index + 100),
                    _info(HOST, index),
                    _info(HOST, index + 100),
                )
            self.assertEqual(session.pair_count, 2)
            self.assertTrue(session.pair_limit_reached)
            self.assertTrue(any("capped" in note for note in session.status_notes()))
        finally:
            state.MAX_PAIRS = original

    def test_lookup_by_element_and_model(self):
        self.session.record_clash(
            state.pair_key(HOST, 1, LINK, 5), _info(HOST, 1), _info(LINK, 5)
        )
        by_element = self.session.pairs_for_element_key(state.element_key(HOST, 1))
        self.assertEqual(len(by_element), 1)
        self.assertEqual(len(self.session.pairs_for_model_ref(LINK)), 1)
        self.assertEqual(self.session.pairs_for_model_ref(HOST + "x"), [])
        self.assertEqual(self.session.pairs_for_element_key(None), [])

    def test_summary_and_status_text(self):
        self._record()
        summary = self.session.summary()
        self.assertEqual(summary["active"], 1)
        self.assertEqual(summary["resolved"], 0)
        self.assertIn("1 live clash(es)", self.session.status_text())


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.session = _session()
        self.key = state.pair_key(HOST, 1, HOST, 2)
        self.session.record_clash(self.key, _info(HOST, 1), _info(HOST, 2))

    def test_deleting_an_element_resolves_its_pairs(self):
        removed = self.session.remove_pairs_for_element_keys(
            [state.element_key(HOST, 2)]
        )
        self.assertEqual(len(removed), 1)
        self.assertEqual(self.session.pair_count, 0)
        self.assertEqual(self.session.resolved_count, 1)

    def test_unrelated_element_resolves_nothing(self):
        self.assertEqual(
            self.session.remove_pairs_for_element_keys([state.element_key(HOST, 99)]),
            [],
        )
        self.assertEqual(self.session.pair_count, 1)

    def test_empty_key_set_is_a_no_op(self):
        self.assertEqual(self.session.remove_pairs_for_element_keys([]), [])
        self.assertEqual(self.session.remove_pairs_for_element_keys([None]), [])

    def test_a_resolved_pair_can_be_detected_again(self):
        self.session.remove_pair(self.key)
        self.assertIsNotNone(
            self.session.record_clash(self.key, _info(HOST, 1), _info(HOST, 2))
        )

    def test_re_evaluation_does_not_inflate_the_resolved_count(self):
        self.session.remove_pair(self.key, count_resolved=False)
        self.assertEqual(self.session.resolved_count, 0)

    def test_removing_an_undetected_pair_returns_none(self):
        self.assertIsNone(self.session.remove_pair("nope"))

    def test_resolving_drops_it_from_the_pending_announcements(self):
        self.session.remove_pair(self.key)
        self.assertEqual(self.session.drain_new_pairs(), [])

    def test_status_text_reports_resolutions_and_queue(self):
        self.session.remove_pair(self.key)
        self.session.enqueue_changes(added_ids=[5], now=0.0)
        text = self.session.status_text()
        self.assertIn("1 resolved", text)
        self.assertIn("checking 1", text)


class ClearTests(unittest.TestCase):
    def test_clear_wipes_every_counter_and_buffer(self):
        session = _session()
        session.enqueue_changes(added_ids=[1], deleted_ids=[9], now=5.0)
        session.record_clash(
            state.pair_key(HOST, 1, HOST, 2), _info(HOST, 1), _info(HOST, 2)
        )
        session.remove_pairs_for_element_keys([state.element_key(HOST, 1)])
        session.pair_limit_reached = True
        session.dropped_change_count = 3

        session.clear()

        self.assertEqual(session.queue_size, 0)
        self.assertEqual(session.pending_deleted_count, 0)
        self.assertEqual(session.pair_count, 0)
        self.assertEqual(session.resolved_count, 0)
        self.assertEqual(session.dropped_change_count, 0)
        self.assertEqual(session.scanned_count, 0)
        self.assertEqual(session.last_change_at, 0.0)
        self.assertFalse(session.pair_limit_reached)
        self.assertEqual(session.drain_new_pairs(), [])
        self.assertEqual(session.records(), [])
        self.assertFalse(session.has_work())


class BoundsTests(unittest.TestCase):
    def test_tuning_constants_stay_bounded(self):
        # These are the guardrails that keep the mode cheap; a regression
        # here is a performance regression, not a style change.
        self.assertLessEqual(state.MAX_ELEMENTS_PER_TICK, 50)
        self.assertLessEqual(state.BUDGET_SEC, 0.1)
        self.assertGreater(state.DEBOUNCE_SEC, 0.0)
        self.assertLessEqual(state.MAX_QUEUE, 10000)
        self.assertLessEqual(state.MAX_PAIRS, 5000)

    def test_dropped_changes_are_reported_not_silent(self):
        session = _session()
        session.dropped_change_count = 7
        self.assertTrue(any("dropped" in note for note in session.status_notes()))


if __name__ == "__main__":
    unittest.main()
