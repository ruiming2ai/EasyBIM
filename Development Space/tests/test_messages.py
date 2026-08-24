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
    # A queued job identifies its document by path/title; an opened file
    # always has them, and without them the job falls back to whatever is
    # active, which is a different code path.
    PathName = r"C:\Models\sample.rvt"
    Title = "sample.rvt"


class FakeUidoc(object):
    def __init__(self, doc):
        self.Document = doc


class FakeApplication(object):
    def __init__(self, docs):
        self.Documents = list(docs)


class FakeUiapp(object):
    """``active`` is what Revit reports as current; ``documents`` is what is
    open.  During ``DocumentOpened`` the newly opened file is in the second
    but not yet the first, which is the whole point of the queued path."""

    def __init__(self, doc, documents=None):
        self.ActiveUIDocument = FakeUidoc(doc) if doc is not None else None
        self.Application = FakeApplication(
            documents if documents is not None else ([doc] if doc else [])
        )

    def activate(self, doc):
        self.ActiveUIDocument = FakeUidoc(doc)


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


class FileOpenDefersToIdlingTests(unittest.TestCase):
    """The regression: the doc-opened hook must not raise the picker itself.

    ``run_start_message_on_file_open`` runs inside Revit's ``DocumentOpened``
    event, where the new document is not active yet and the open is still
    finishing - a modal raised there is refused or lands behind Revit.  While
    the onboarding alert existed its own modal blocked the hook until Revit
    had settled, so the inline call worked by accident; removing the alert
    removed that barrier and both windows stopped appearing.
    """

    def setUp(self):
        self.messages = _load_messages()
        self.doc = FakeDocument()

    def test_the_hook_queues_instead_of_opening_the_windows_inline(self):
        with mock.patch.object(self.messages, "_show_workset_picker_for_doc") as picker, \
             mock.patch.object(self.messages, "_print_coordination_review_report") as report, \
             mock.patch.object(self.messages, "_enqueue_startup_actions") as enqueue, \
             mock.patch.object(self.messages, "_disable_passive_coordination_review_detector"):
            self.messages.run_start_message_on_file_open(doc=self.doc)

        enqueue.assert_called_once_with(
            doc=self.doc, open_worksets_after=True, run_coord_report_after=True,
        )
        picker.assert_not_called()
        report.assert_not_called()

    def test_defer_queues_even_though_a_valid_document_is_in_hand(self):
        with mock.patch.object(self.messages, "_run_startup_actions_now") as run_now, \
             mock.patch.object(self.messages, "_enqueue_startup_actions") as enqueue:
            self.messages.show_start_message(
                force=True, doc=self.doc, defer=True,
                open_worksets_after=True, run_coord_report_after=True,
            )

        enqueue.assert_called_once()
        run_now.assert_not_called()

    def test_the_ribbon_button_still_opens_the_windows_straight_away(self):
        # The button is a user click, not an event handler: nothing to wait for.
        with mock.patch.object(self.messages, "_run_startup_actions_now") as run_now, \
             mock.patch.object(self.messages, "_enqueue_startup_actions") as enqueue:
            self.messages.run_start_message_workflow(doc=self.doc, force=True)

        run_now.assert_called_once()
        enqueue.assert_not_called()


class QueuedJobStillOpensBothWindowsTests(unittest.TestCase):
    """End to end over the real state machine: hook -> queue -> Idling.

    Only the two windows and the envvar-backed state store are faked, so the
    job dict really is built by ``_enqueue_startup_job`` and really is driven
    by ``_process_startup_job``.
    """

    def setUp(self):
        self.messages = _load_messages()
        self.doc = FakeDocument()
        self.other = FakeDocument()
        self.other.PathName = r"C:\Models\other.rvt"
        self.other.Title = "other.rvt"
        self._state = {"next_id": 1, "jobs": []}

    def _install_state_store(self):
        def _load():
            return self._state

        def _save(state):
            self._state = state
            return True

        return (
            mock.patch.object(self.messages, "_load_startup_state", side_effect=_load),
            mock.patch.object(self.messages, "_save_startup_state", side_effect=_save),
        )

    def test_the_windows_open_once_the_document_becomes_active(self):
        load_patch, save_patch = self._install_state_store()
        calls = []

        # Revit during DocumentOpened: our file is open, but the previously
        # active document is still the active one.
        uiapp = FakeUiapp(self.other, documents=[self.other, self.doc])

        with load_patch, save_patch, \
             mock.patch.object(self.messages, "_show_workset_picker_for_doc",
                               side_effect=lambda d: calls.append(("picker", d))), \
             mock.patch.object(self.messages, "_print_coordination_review_report",
                               side_effect=lambda d: calls.append(("report", d))), \
             mock.patch.object(self.messages, "_disable_passive_coordination_review_detector"), \
             mock.patch.object(self.messages, "_clear_file_open_trigger_pending"), \
             mock.patch.object(self.messages, "_load_file_open_trigger_state",
                               return_value={"pending": False, "created_at": 0.0}):

            self.messages.run_start_message_on_file_open(doc=self.doc)

            # The hook itself opened nothing, but left work behind.
            self.assertEqual([], calls)
            self.assertTrue(self.messages.has_pending_startup_jobs())

            # Idling ticks while our document is still not the active one:
            # the job waits rather than showing the picker on the wrong doc.
            self.messages.process_startup_jobs(uiapp)
            self.assertEqual([], calls)

            # Revit finishes the open and activates it.
            uiapp.activate(self.doc)
            self.messages.process_startup_jobs(uiapp)
            self.messages.process_startup_jobs(uiapp)

        self.assertEqual(["picker", "report"], [name for name, _ in calls])
        for _, passed_doc in calls:
            self.assertIs(self.doc, passed_doc)

    def test_the_queue_drains_so_the_windows_do_not_reopen(self):
        load_patch, save_patch = self._install_state_store()
        calls = []
        uiapp = FakeUiapp(self.doc, documents=[self.doc])

        with load_patch, save_patch, \
             mock.patch.object(self.messages, "_show_workset_picker_for_doc",
                               side_effect=lambda d: calls.append("picker")), \
             mock.patch.object(self.messages, "_print_coordination_review_report",
                               side_effect=lambda d: calls.append("report")), \
             mock.patch.object(self.messages, "_disable_passive_coordination_review_detector"), \
             mock.patch.object(self.messages, "_clear_file_open_trigger_pending"), \
             mock.patch.object(self.messages, "_load_file_open_trigger_state",
                               return_value={"pending": False, "created_at": 0.0}):

            self.messages.run_start_message_on_file_open(doc=self.doc)
            for _ in range(6):
                self.messages.process_startup_jobs(uiapp)

            self.assertFalse(self.messages.has_pending_startup_jobs())

        self.assertEqual(["picker", "report"], calls)


if __name__ == "__main__":
    unittest.main()
