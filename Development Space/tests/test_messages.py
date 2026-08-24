"""``easybim.messages`` after the onboarding dialog was removed.

The module used to show a firm-specific "Please Check Your Current Workset...
GB Standards & Best Practices" alert before running its two follow-up
actions (the Active Workset picker and the Coordination Review summary).
The alert was cut because it does not generalize to other firms; the two
follow-up windows are the part every firm wants and must keep firing
exactly as before, through the same public entry points the hook and the
ribbon button already call.
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


class FakeUidoc(object):
    def __init__(self, doc):
        self.Document = doc


class FakeUiapp(object):
    def __init__(self, doc):
        self.ActiveUIDocument = FakeUidoc(doc) if doc is not None else None


class NoDialogMachineryTests(unittest.TestCase):
    """The removed alert, and its firm-specific copy, must not come back."""

    def setUp(self):
        self.messages = _load_messages()

    def test_the_start_message_constant_is_gone(self):
        self.assertFalse(hasattr(self.messages, "START_MESSAGE"))

    def test_the_wpf_bold_alert_renderer_is_gone(self):
        self.assertFalse(hasattr(self.messages, "_alert_wpf_with_bold"))

    def test_no_firm_specific_copy_survives_anywhere_in_the_module(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for stale in ("GB Standard", "Starting View", "Please Check Your Current"):
            self.assertNotIn(stale, source, stale)

    def test_show_start_message_no_longer_takes_a_dialog_title(self):
        import inspect
        params = list(inspect.signature(self.messages.show_start_message).parameters)
        self.assertNotIn("title", params)


class ShowStartMessageTests(unittest.TestCase):
    """``show_start_message`` now goes straight to the follow-up actions."""

    def setUp(self):
        self.messages = _load_messages()
        self.doc = FakeDocument()

    def test_runs_immediately_when_a_valid_document_is_already_at_hand(self):
        with mock.patch.object(self.messages, "_run_startup_actions_now") as run_now, \
             mock.patch.object(self.messages, "_enqueue_startup_actions") as enqueue:
            self.messages.show_start_message(
                force=True, doc=self.doc,
                open_worksets_after=True, run_coord_report_after=True,
            )

        run_now.assert_called_once_with(
            doc=self.doc, open_worksets_after=True, run_coord_report_after=True,
        )
        enqueue.assert_not_called()

    def test_queues_when_no_valid_document_is_available_yet(self):
        with mock.patch.object(self.messages, "_get_uiapp", return_value=FakeUiapp(None)), \
             mock.patch.object(self.messages, "_run_startup_actions_now") as run_now, \
             mock.patch.object(self.messages, "_enqueue_startup_actions") as enqueue:
            self.messages.show_start_message(
                force=True, doc=None,
                open_worksets_after=True, run_coord_report_after=False,
            )

        enqueue.assert_called_once_with(
            doc=None, open_worksets_after=True, run_coord_report_after=False,
        )
        run_now.assert_not_called()

    def test_does_nothing_when_neither_follow_up_action_is_requested(self):
        # force=True used to be enough to pop the alert; today, with nothing
        # to show and nothing asked for, there is nothing left to do.
        with mock.patch.object(self.messages, "_run_startup_actions_now") as run_now, \
             mock.patch.object(self.messages, "_enqueue_startup_actions") as enqueue:
            self.messages.show_start_message(
                force=True, doc=self.doc,
                open_worksets_after=False, run_coord_report_after=False,
            )

        run_now.assert_not_called()
        enqueue.assert_not_called()

    def test_family_documents_are_still_skipped_unless_forced(self):
        family_doc = FakeDocument()
        family_doc.IsFamilyDocument = True

        with mock.patch.object(self.messages, "_run_startup_actions_now") as run_now, \
             mock.patch.object(self.messages, "_enqueue_startup_actions") as enqueue:
            self.messages.show_start_message(
                force=False, doc=family_doc,
                open_worksets_after=True, run_coord_report_after=True,
            )

        run_now.assert_not_called()
        enqueue.assert_not_called()


class RunStartMessageWorkflowTests(unittest.TestCase):
    """The button and hook call this; both windows must still fire from it."""

    def setUp(self):
        self.messages = _load_messages()
        self.doc = FakeDocument()

    def test_the_workset_picker_and_the_coordination_report_both_run(self):
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


if __name__ == "__main__":
    unittest.main()
