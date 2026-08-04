import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" /
               "Circuiting.pulldown" / "Update Circuit Rating.pushbutton")
if str(COMMAND_DIR) not in sys.path:
    sys.path.insert(0, str(COMMAND_DIR))

import circuit_rating_state as state


RATING = -1001
FRAME = -1002

RATING_TARGET = {"key": RATING, "label": u"Rating"}
FRAME_TARGET = {"key": FRAME, "label": u"Frame"}


def sample(value, label=u"Element", is_equipment=False):
    return {"value": value, "label": label, "is_equipment": is_equipment}


def record(circuit_id=1, panel=u"HP-1", number=u"1", samples=None,
           existing=None, load_name=u"Load"):
    return {
        "circuit_id": circuit_id,
        "panel": panel,
        "circuit_number": number,
        "load_name": load_name,
        "samples": samples if samples is not None else [],
        "existing": existing or {},
    }


class PickHighestTests(unittest.TestCase):
    def test_no_sample_means_no_pick(self):
        self.assertIsNone(state.pick_highest([]))
        self.assertIsNone(state.pick_highest(None))

    def test_highest_value_wins(self):
        best = state.pick_highest([
            sample(20.0, u"Receptacle"),
            sample(225.0, u"Panelboard"),
            sample(100.0, u"Disconnect"),
        ])
        self.assertEqual(225.0, best["value"])
        self.assertEqual(u"Panelboard", best["label"])

    def test_equipment_breaks_a_tie(self):
        """A panel and a receptacle both at 20 A: credit the panel."""
        best = state.pick_highest([
            sample(20.0, u"Receptacle"),
            sample(20.0, u"Panelboard", is_equipment=True),
        ])
        self.assertEqual(u"Panelboard", best["label"])

    def test_pick_does_not_depend_on_element_order(self):
        first = state.pick_highest([sample(20.0, u"B"), sample(20.0, u"A")])
        second = state.pick_highest([sample(20.0, u"A"), sample(20.0, u"B")])
        self.assertEqual(first["label"], second["label"])

    def test_missing_values_are_ignored_not_counted_as_zero(self):
        best = state.pick_highest([sample(None, u"Blank"), sample(15.0, u"Ok")])
        self.assertEqual(15.0, best["value"])


class FormatTests(unittest.TestCase):
    def test_whole_numbers_lose_their_decimals(self):
        self.assertEqual(u"100", state.format_amps(100.0))
        self.assertEqual(u"20", state.format_amps(20))

    def test_fractions_survive(self):
        self.assertEqual(u"12.5", state.format_amps(12.5))

    def test_nothing_formats_to_nothing(self):
        self.assertEqual(u"", state.format_amps(None))


class BuildRecordsTests(unittest.TestCase):
    def test_source_key_selects_the_readings(self):
        scan = [{
            "circuit_id": 7,
            "panel": u"HP-1",
            "circuit_number": u"2",
            "load_name": u"Load",
            "existing": {RATING: 20.0},
            "readings": {
                u"Rating": [sample(60.0)],
                u"Apparent Current": [sample(48.0)],
            },
        }]
        records = state.build_records(scan, u"Apparent Current")
        self.assertEqual(48.0, records[0]["samples"][0]["value"])

    def test_unknown_source_key_yields_no_samples(self):
        scan = [{"circuit_id": 7, "readings": {u"Rating": [sample(60.0)]}}]
        self.assertEqual([], state.build_records(scan, u"Nope")[0]["samples"])


class BuildRowsTests(unittest.TestCase):
    def test_a_circuit_with_no_sample_is_dropped_and_counted(self):
        rows, skipped = state.build_rows(
            [record(1, samples=[sample(20.0)]), record(2, samples=[])],
            [RATING_TARGET])
        self.assertEqual([1], [row.circuit_id for row in rows])
        self.assertEqual(1, skipped)

    def test_a_circuit_is_kept_when_only_some_elements_carry_the_parameter(self):
        """The user's rule: partial coverage still updates the circuit."""
        rows, skipped = state.build_rows(
            [record(1, samples=[sample(225.0, u"Panelboard")])],
            [RATING_TARGET])
        self.assertEqual(1, len(rows))
        self.assertEqual(0, skipped)
        self.assertEqual(u"Panelboard", rows[0].donor_label)

    def test_rows_start_checked(self):
        rows, _ = state.build_rows(
            [record(1, samples=[sample(20.0)])], [RATING_TARGET])
        self.assertTrue(rows[0].is_selected)

    def test_previous_tick_state_is_restored(self):
        records = [record(1, samples=[sample(20.0)]),
                   record(2, number=u"3", samples=[sample(30.0)])]
        rows, _ = state.build_rows(records, [RATING_TARGET],
                                   selected_ids=set([2]))
        self.assertEqual(
            {1: False, 2: True},
            dict((row.circuit_id, row.is_selected) for row in rows))

    def test_existing_column_lists_every_ticked_target(self):
        rows, _ = state.build_rows(
            [record(1, samples=[sample(100.0)],
                    existing={RATING: 20.0, FRAME: 100.0})],
            [RATING_TARGET, FRAME_TARGET])
        self.assertEqual(u"Rating 20; Frame 100", rows[0].existing_text)

    def test_a_target_the_circuit_has_no_value_for_shows_a_dash(self):
        rows, _ = state.build_rows(
            [record(1, samples=[sample(100.0)], existing={RATING: None})],
            [RATING_TARGET])
        self.assertEqual(u"Rating -", rows[0].existing_text)

    def test_no_change_is_flagged_only_when_every_target_matches(self):
        matching, _ = state.build_rows(
            [record(1, samples=[sample(100.0)],
                    existing={RATING: 100.0, FRAME: 100.0})],
            [RATING_TARGET, FRAME_TARGET])
        self.assertEqual(state.STATUS_NO_CHANGE, matching[0].status)

        partial, _ = state.build_rows(
            [record(1, samples=[sample(100.0)],
                    existing={RATING: 100.0, FRAME: 60.0})],
            [RATING_TARGET, FRAME_TARGET])
        self.assertEqual(state.STATUS_UPDATE, partial[0].status)

    def test_rows_sort_by_panel_then_circuit_number_numerically(self):
        records = [
            record(1, panel=u"HP-1", number=u"10", samples=[sample(20.0)]),
            record(2, panel=u"HP-1", number=u"2", samples=[sample(20.0)]),
            record(3, panel=u"AP-1", number=u"1", samples=[sample(20.0)]),
        ]
        rows, _ = state.build_rows(records, [RATING_TARGET])
        self.assertEqual([3, 2, 1], [row.circuit_id for row in rows])


class BuildPlanTests(unittest.TestCase):
    def _rows(self):
        rows, _ = state.build_rows(
            [record(1, samples=[sample(100.0)]),
             record(2, number=u"2", samples=[sample(60.0)])],
            [RATING_TARGET])
        return rows

    def test_only_ticked_rows_are_planned(self):
        rows = self._rows()
        rows[1].is_selected = False
        plan = state.build_plan(rows, [RATING_TARGET])
        self.assertEqual([1], [item["circuit_id"] for item in plan])
        self.assertEqual([RATING], plan[0]["target_keys"])

    def test_no_target_means_no_plan(self):
        self.assertEqual([], state.build_plan(self._rows(), []))

    def test_the_planned_value_is_the_raw_reading(self):
        plan = state.build_plan(self._rows(), [RATING_TARGET])
        self.assertEqual(100.0, plan[0]["value"])


class SummarizeTests(unittest.TestCase):
    def test_failures_are_counted_separately_and_explained(self):
        plan = [{"circuit_id": 1}, {"circuit_id": 2}]
        results = [
            {"circuit_id": 1, "circuit_label": u"HP-1 - 1",
             "written": [u"Rating"], "error": None},
            {"circuit_id": 2, "circuit_label": u"HP-1 - 2",
             "written": [], "error": u"read only"},
        ]
        summary = state.summarize(plan, results)
        self.assertEqual(2, summary["attempted"])
        self.assertEqual(1, summary["updated"])
        self.assertEqual(1, summary["failed"])
        self.assertIn(u"HP-1 - 1: Rating", summary["detail"])
        self.assertIn(u"HP-1 - 2: read only", summary["detail"])


class ZeroRatingReportTests(unittest.TestCase):
    def _circuit(self, panel=u"HP-1", number=u"1", name=u"Load", values=None):
        return {
            "panel": panel,
            "circuit_number": number,
            "load_name": name,
            "values": values if values is not None else {},
        }

    def test_a_rated_circuit_is_not_reported(self):
        self.assertFalse(state.is_zero_rating({RATING: 20.0}, [RATING]))

    def test_zero_and_blank_both_count_as_zero_amp(self):
        self.assertTrue(state.is_zero_rating({RATING: 0.0}, [RATING]))
        self.assertTrue(state.is_zero_rating({RATING: None}, [RATING]))
        self.assertTrue(state.is_zero_rating({}, [RATING]))

    def test_any_zero_target_reports_the_circuit(self):
        self.assertTrue(
            state.is_zero_rating({RATING: 20.0, FRAME: 0.0}, [RATING, FRAME]))

    def test_nothing_is_reported_when_nothing_was_targeted(self):
        self.assertFalse(state.is_zero_rating({RATING: 0.0}, []))

    def test_only_zero_amp_circuits_reach_the_report(self):
        rows = state.build_zero_rating_rows(
            [self._circuit(number=u"1", values={RATING: 20.0}),
             self._circuit(number=u"2", name=u"Spare", values={RATING: 0.0})],
            [RATING])
        self.assertEqual(1, len(rows))
        self.assertEqual(u"Spare", rows[0].circuit_name)
        self.assertEqual(u"2", rows[0].circuit_number)
        self.assertEqual(u"HP-1", rows[0].panel)

    def test_the_report_covers_circuits_the_run_never_touched(self):
        """A circuit nobody could rate is the one worth reporting."""
        rows = state.build_zero_rating_rows(
            [self._circuit(panel=u"AP-9", number=u"4", values={})], [RATING])
        self.assertEqual([u"AP-9"], [row.panel for row in rows])

    def test_the_report_sorts_by_panel_then_circuit_number(self):
        rows = state.build_zero_rating_rows(
            [self._circuit(panel=u"HP-1", number=u"10", values={RATING: 0.0}),
             self._circuit(panel=u"HP-1", number=u"2", values={RATING: 0.0}),
             self._circuit(panel=u"AP-1", number=u"1", values={RATING: 0.0})],
            [RATING])
        self.assertEqual(
            [(u"AP-1", u"1"), (u"HP-1", u"2"), (u"HP-1", u"10")],
            [(row.panel, row.circuit_number) for row in rows])


class ValuesMatchTests(unittest.TestCase):
    def test_conversion_noise_still_counts_as_equal(self):
        self.assertTrue(state.values_match(100.0, 100.0 + 1e-9))

    def test_a_missing_side_never_matches(self):
        self.assertFalse(state.values_match(None, 100.0))
        self.assertFalse(state.values_match(100.0, None))


if __name__ == "__main__":
    unittest.main()
