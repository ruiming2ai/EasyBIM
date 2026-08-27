"""Checks for the progress bar a command is allowed to run without.

pyRevit builds its progress bar by asking the host for its main window
rectangle, and that lookup is unguarded: a Revit that will not answer it kills
the command with a message naming a window handle, before any work starts.
That is a real failure this tool hit, so what is pinned here is the rule that
came out of it - the bar is a convenience, never a dependency, and losing it
costs the Cancel button and nothing else.
"""

import importlib.util
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "lib" / "easybim"


def _load_progress_module():
    lib_root = str(REPO_ROOT / "lib")
    if lib_root not in sys.path:
        sys.path.insert(0, lib_root)
    spec = importlib.util.spec_from_file_location(
        "easybim_progress_under_test", str(LIB_DIR / "progress.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


progress = _load_progress_module()


class FakeBar(object):
    """A pyRevit progress bar, as far as this helper is concerned."""

    def __init__(self, update_error=None, cancelled=False, enter_error=None):
        self.updates = []
        self.entered = False
        self.exited = False
        self.cancelled = cancelled
        self._update_error = update_error
        self._enter_error = enter_error

    def __enter__(self):
        if self._enter_error:
            raise self._enter_error
        self.entered = True
        return self

    def __exit__(self, *_exc):
        self.exited = True
        return False

    def update_progress(self, done, total):
        if self._update_error:
            raise self._update_error
        self.updates.append((done, total))


def opener(bar=None, error=None):
    """Stands in for `_open_bar`, which is the only impure part."""
    def _open(_title, _cancellable):
        if error is not None:
            return None, progress.NO_BAR_NOTE
        return bar, ""
    return _open


class WithABarTests(unittest.TestCase):
    def test_updates_and_cancellation_reach_the_bar(self):
        bar = FakeBar()
        with progress.ProgressSession("Working", open_bar=opener(bar)) as session:
            session.update(1, 4)
            self.assertFalse(session.cancelled)
            bar.cancelled = True
            self.assertTrue(session.cancelled)

        self.assertEqual([(1, 4)], bar.updates)
        self.assertTrue(bar.entered)
        self.assertTrue(bar.exited)

    def test_a_working_bar_leaves_no_note(self):
        with progress.ProgressSession("Working", open_bar=opener(FakeBar())) as session:
            session.update(1, 1)
        self.assertEqual("", session.note)

    def test_a_zero_total_never_divides_the_bar_by_nothing(self):
        bar = FakeBar()
        with progress.ProgressSession("Working", open_bar=opener(bar)) as session:
            session.update(0, 0)
        self.assertEqual([(0, 1)], bar.updates)

    def test_the_bar_is_closed_even_when_the_work_raises(self):
        bar = FakeBar()
        try:
            with progress.ProgressSession("Working", open_bar=opener(bar)):
                raise RuntimeError("the work failed")
        except RuntimeError:
            pass
        self.assertTrue(bar.exited)


class WithNoBarTests(unittest.TestCase):
    """The failure this module exists for: pyRevit will not build a bar."""

    def test_the_work_still_runs_and_the_note_says_why_nothing_was_shown(self):
        done = []
        with progress.ProgressSession("Working", open_bar=opener(error=True)) as session:
            for index in range(3):
                session.update(index, 3)
                done.append(index)

        self.assertEqual([0, 1, 2], done)
        self.assertIn("would not open a progress bar", session.note)
        self.assertIn("Cancel", session.note)

    def test_with_no_bar_there_is_no_cancel_so_the_batch_runs_on(self):
        with progress.ProgressSession("Working", open_bar=opener(error=True)) as session:
            self.assertFalse(session.cancelled)

    def test_a_bar_that_constructs_but_will_not_start_is_dropped(self):
        bar = FakeBar(enter_error=RuntimeError("no main window"))
        with progress.ProgressSession("Working", open_bar=opener(bar)) as session:
            session.update(1, 2)
            self.assertFalse(session.cancelled)
        self.assertIn("would not open a progress bar", session.note)
        self.assertEqual([], bar.updates)

    def test_the_real_opener_survives_a_pyrevit_that_cannot_be_imported(self):
        # Under CPython there is no pyrevit at all, which is the same shape
        # of failure as a Revit that refuses the window lookup.
        bar, note = progress._open_bar("Working", True)
        self.assertIsNone(bar)
        self.assertEqual(progress.NO_BAR_NOTE, note)


class BarBreaksMidRunTests(unittest.TestCase):
    """A bar that opened can still start throwing half way through."""

    def test_an_update_that_raises_drops_the_bar_and_the_work_finishes(self):
        bar = FakeBar(update_error=RuntimeError("gone"))
        reached = []
        with progress.ProgressSession("Working", open_bar=opener(bar)) as session:
            for index in range(3):
                session.update(index, 3)
                reached.append(index)

        self.assertEqual([0, 1, 2], reached)
        self.assertIn("would not open a progress bar", session.note)

    def test_a_cancelled_read_that_raises_reads_as_not_cancelled(self):
        class AngryBar(FakeBar):
            @property
            def cancelled(self):
                raise RuntimeError("gone")

            @cancelled.setter
            def cancelled(self, _value):
                pass

        with progress.ProgressSession("Working", open_bar=opener(AngryBar())) as session:
            self.assertFalse(session.cancelled)
        self.assertIn("would not open a progress bar", session.note)


class UsageTests(unittest.TestCase):
    def test_families_downgrade_no_longer_builds_a_bare_progress_bar(self):
        script = (REPO_ROOT / "EasyBIM.tab" / "Family.panel"
                  / "Families Downgrade.pushbutton" / "script.py").read_text(encoding="utf-8")
        self.assertNotIn("forms.ProgressBar", script)
        self.assertIn("ProgressSession", script)

    def test_the_failure_alert_carries_the_frames_that_produced_it(self):
        script = (REPO_ROOT / "EasyBIM.tab" / "Family.panel"
                  / "Families Downgrade.pushbutton" / "script.py").read_text(encoding="utf-8")
        self.assertIn("def _failure_text(", script)
        self.assertIn("traceback.format_exc()", script)
        self.assertIn("forms.alert(_failure_text(run_error)", script)


if __name__ == "__main__":
    unittest.main()
