"""``easybim.messages`` after the onboarding alert was removed.

The module used to show a firm-specific "Please Check Your Current Workset...
GB Standards & Best Practices" alert in front of its two windows - the Active
Workset picker and the Coordination Review summary.  The alert was cut because
it does not generalize to other firms.

The alert was load-bearing in a way its text did not suggest: it was a *modal*,
so the ``doc-opened`` hook sat inside ``ShowDialog()`` while Revit finished the
open and made the document active, and only then did the inline call raise the
picker.  Cutting the alert without replacing that wait stopped both windows
from appearing.  These tests pin the replacement: the hook defers, and the
Idling delegate runs the windows once the document really is active.
"""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_ROOT = ROOT / "lib" / "easybim"
MODULE_PATH = LIB_ROOT / "messages.py"
HOOK_PATH = ROOT / "hooks" / "doc-opened.py"


def _load_messages():
    package = types.ModuleType("easybim")
    package.__path__ = [str(LIB_ROOT)]
    sys.modules["easybim"] = package
    sys.modules.pop("easybim.messages", None)
    spec = importlib.util.spec_from_file_location(
        "easybim.messages", str(MODULE_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["easybim.messages"] = module
    spec.loader.exec_module(module)
    package.messages = module
    return module


class FakeDocument(object):
    IsValidObject = True
    IsFamilyDocument = False
    IsLinked = False
    IsWorkshared = True
    PathName = r"C:\Models\sample.rvt"
    Title = "sample.rvt"


class FakeUidoc(object):
    def __init__(self, doc):
        self.Document = doc


class FakeUiapp(object):
    def __init__(self, doc):
        self.ActiveUIDocument = FakeUidoc(doc) if doc is not None else None

    def activate(self, doc):
        self.ActiveUIDocument = FakeUidoc(doc) if doc is not None else None


class _TriggerStore(object):
    """Stands in for the pyRevit envvar the hook and the delegate share."""

    def __init__(self):
        self.state = {"pending": False, "created_at": 0.0}

    def install(self, messages):
        return (
            mock.patch.object(messages, "_load_file_open_trigger_state",
                              side_effect=lambda: dict(self.state)),
            mock.patch.object(messages, "_save_file_open_trigger_state",
                              side_effect=self._save),
        )

    def _save(self, state):
        self.state = dict(state)
        return True


class NoAlertMachineryTests(unittest.TestCase):
    """The removed alert, and its firm-specific copy, must not come back."""

    def setUp(self):
        self.messages = _load_messages()

    def test_the_message_constant_is_gone(self):
        self.assertFalse(hasattr(self.messages, "START_MESSAGE"))

    def test_the_wpf_alert_renderer_is_gone(self):
        self.assertFalse(hasattr(self.messages, "_alert_wpf_with_bold"))

    def test_no_firm_specific_copy_survives_anywhere_in_the_module(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for stale in ("GB Standard", "Starting View", "Please Check Your Current"):
            self.assertNotIn(stale, source, stale)

    def test_show_start_message_no_longer_takes_a_dialog_title(self):
        import inspect
        params = list(inspect.signature(self.messages.show_start_message).parameters)
        self.assertNotIn("title", params)


class HookDefersInsteadOfOpeningInlineTests(unittest.TestCase):
    """The regression this whole exercise is about.

    ``run_start_message_on_file_open`` runs inside ``DocumentOpened``.  It must
    leave a trigger behind rather than raise either window itself.
    """

    def setUp(self):
        self.messages = _load_messages()
        self.doc = FakeDocument()
        self.store = _TriggerStore()

    def test_the_hook_opens_nothing_and_leaves_the_trigger_pending(self):
        load_patch, save_patch = self.store.install(self.messages)
        with load_patch, save_patch, \
             mock.patch.object(self.messages, "_show_workset_picker_for_doc") as picker, \
             mock.patch.object(self.messages, "_print_coordination_review_report") as report, \
             mock.patch.object(self.messages, "_run_startup_actions_now") as run_now, \
             mock.patch.object(self.messages, "_load_startup_state",
                               return_value={"next_id": 1, "jobs": []}):
            self.messages.run_start_message_on_file_open(doc=self.doc)

            # Inside the patch: this is what the Idling delegate asks before
            # it bothers to run a pass.
            self.assertTrue(self.messages.has_pending_startup_jobs())

        picker.assert_not_called()
        report.assert_not_called()
        run_now.assert_not_called()
        self.assertTrue(self.store.state["pending"])

    def test_a_hook_with_no_document_still_defers(self):
        load_patch, save_patch = self.store.install(self.messages)
        with load_patch, save_patch:
            self.messages.run_start_message_on_file_open(doc=None)

        self.assertTrue(self.store.state["pending"])

    def test_a_family_document_neither_defers_nor_runs(self):
        family_doc = FakeDocument()
        family_doc.IsFamilyDocument = True

        load_patch, save_patch = self.store.install(self.messages)
        with load_patch, save_patch, \
             mock.patch.object(self.messages, "_run_startup_actions_now") as run_now, \
             mock.patch.object(self.messages,
                               "_disable_passive_coordination_review_detector") as detach:
            self.messages.run_start_message_on_file_open(doc=family_doc)

        run_now.assert_not_called()
        self.assertFalse(self.store.state["pending"])
        detach.assert_called_once()

    def test_the_detector_stays_attached_so_the_deferred_report_still_has_warnings(self):
        # It is detached later, by the report that consumes it.
        load_patch, save_patch = self.store.install(self.messages)
        with load_patch, save_patch, \
             mock.patch.object(self.messages,
                               "_disable_passive_coordination_review_detector") as detach:
            self.messages.run_start_message_on_file_open(doc=self.doc)

        detach.assert_not_called()


class IdlingOpensBothWindowsTests(unittest.TestCase):
    """End to end over the real trigger machinery: hook -> trigger -> Idling.

    Only the two windows and the shared trigger store are faked, so the
    handover really goes through ``_mark_file_open_trigger_pending`` and
    ``_process_file_open_trigger_pending``.
    """

    def setUp(self):
        self.messages = _load_messages()
        self.doc = FakeDocument()
        self.store = _TriggerStore()

    def _run(self, uiapp, ticks=1):
        calls = []
        load_patch, save_patch = self.store.install(self.messages)
        with load_patch, save_patch, \
             mock.patch.object(self.messages, "_show_workset_picker_for_doc",
                               side_effect=lambda d: calls.append(("picker", d))), \
             mock.patch.object(self.messages, "_print_coordination_review_report",
                               side_effect=lambda d: calls.append(("report", d))), \
             mock.patch.object(self.messages, "_load_startup_state",
                               return_value={"next_id": 1, "jobs": []}):

            self.messages.run_start_message_on_file_open(doc=self.doc)
            self.assertEqual([], calls, "the hook itself must open nothing")

            for _ in range(ticks):
                self.messages.process_startup_jobs(uiapp)
        return calls

    def test_both_windows_open_on_the_first_idling_tick(self):
        calls = self._run(FakeUiapp(self.doc))

        self.assertEqual(["picker", "report"], [name for name, _ in calls])
        for _, passed_doc in calls:
            self.assertIs(self.doc, passed_doc)

    def test_they_open_once_and_the_trigger_is_consumed(self):
        calls = self._run(FakeUiapp(self.doc), ticks=5)

        self.assertEqual(["picker", "report"], [name for name, _ in calls])
        self.assertFalse(self.store.state["pending"])

    def test_nothing_opens_while_no_document_is_active_yet(self):
        # Revit has not finished the open: the trigger waits rather than
        # showing the picker against nothing.
        calls = self._run(FakeUiapp(None), ticks=3)

        self.assertEqual([], calls)
        self.assertTrue(self.store.state["pending"])


class RibbonButtonStillRunsInlineTests(unittest.TestCase):
    """A click is not an event handler; it has nothing to wait for."""

    def setUp(self):
        self.messages = _load_messages()
        self.doc = FakeDocument()

    def test_the_button_opens_both_windows_straight_away(self):
        with mock.patch.object(self.messages, "_show_workset_picker_for_doc") as picker, \
             mock.patch.object(self.messages, "_print_coordination_review_report") as report:
            self.messages.run_start_message_workflow(doc=self.doc, force=True)

        picker.assert_called_once_with(self.doc)
        report.assert_called_once_with(self.doc)

    def test_a_picker_failure_does_not_prevent_the_coordination_report(self):
        with mock.patch.object(self.messages, "_show_workset_picker_for_doc",
                               side_effect=RuntimeError("boom")), \
             mock.patch.object(self.messages, "_print_coordination_review_report") as report:
            self.messages.run_start_message_workflow(doc=self.doc, force=True)

        report.assert_called_once_with(self.doc)


class HookScriptContractTests(unittest.TestCase):
    """The hook file still has to call the entry point that defers."""

    def test_the_doc_opened_hook_calls_the_deferring_entry_point(self):
        source = HOOK_PATH.read_text(encoding="utf-8")
        self.assertIn("run_start_message_on_file_open", source)
        # Calling the workflow straight from the hook is the bug.
        self.assertNotIn("run_start_message_workflow", source)


if __name__ == "__main__":
    unittest.main()
