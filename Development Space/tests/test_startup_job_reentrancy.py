"""Re-entrancy contract for the deferred startup jobs.

These jobs now run from the Idling delegate and each stage opens a modal
dialog. Revit can keep raising Idling behind a modal, and the job dicts handed
back by the state loader are the *same live objects*, so any stage that acted
before marking itself done could be entered twice - stacking a second dialog
and, for the coordination report, consuming the recorded warnings twice.
"""

import importlib.util
import pathlib
import sys
import time
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_ROOT = ROOT / "lib" / "easybim"


def _load_messages():
    package = types.ModuleType("easybim")
    package.__path__ = [str(LIB_ROOT)]
    sys.modules["easybim"] = package
    sys.modules.pop("easybim.messages", None)
    spec = importlib.util.spec_from_file_location(
        "easybim.messages", str(LIB_ROOT / "messages.py")
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
        self.ActiveUIDocument = FakeUidoc(doc)


class StartupJobStageOrderingTests(unittest.TestCase):
    def setUp(self):
        self.messages = _load_messages()
        self.doc = FakeDocument()
        self.uiapp = FakeUiapp(self.doc)

    #: `now` used by the stage tests; the job must look fresh (inside
    #: _STARTUP_JOB_MAX_AGE_SEC) and still be inside its post deadline, or the
    #: stage machine skips straight past the modal.
    NOW = 100.0

    def _job(self, stage):
        return {
            "stage": stage,
            "created_at": self.NOW,
            "post_deadline": self.NOW + 100.0,
            "open_worksets_after": True,
            "run_coord_report_after": True,
            "doc_is_workshared": True,
        }

    def test_workset_stage_is_advanced_before_the_picker_opens(self):
        """The stage must already read run_report while the modal is up."""
        job = self._job("show_workset_picker")
        observed = {}

        def _picker(doc):
            del doc
            observed["stage_during_modal"] = job["stage"]

        with mock.patch.object(
            self.messages, "_show_workset_picker_for_doc", side_effect=_picker
        ), mock.patch.object(
            self.messages, "_resolve_doc_from_job", return_value=self.doc
        ), mock.patch.object(
            self.messages, "_get_active_doc_from_uiapp", return_value=self.doc
        ), mock.patch.object(
            self.messages, "_job_has_doc_identity", return_value=True
        ), mock.patch.object(
            self.messages, "_is_same_doc", return_value=True
        ):
            self.messages._process_startup_job(self.uiapp, job, self.NOW)

        self.assertEqual("run_report", observed["stage_during_modal"])

    def test_report_stage_is_marked_done_before_the_report_opens(self):
        job = self._job("run_report")
        observed = {}

        def _report(doc):
            del doc
            observed["stage_during_modal"] = job["stage"]

        with mock.patch.object(
            self.messages, "_print_coordination_review_report", side_effect=_report
        ), mock.patch.object(
            self.messages, "_resolve_doc_from_job", return_value=self.doc
        ), mock.patch.object(
            self.messages, "_get_active_doc_from_uiapp", return_value=self.doc
        ), mock.patch.object(
            self.messages, "_job_has_doc_identity", return_value=True
        ):
            self.messages._process_startup_job(self.uiapp, job, self.NOW)

        self.assertEqual("done", observed["stage_during_modal"])

    def test_file_open_trigger_is_consumed_before_the_alert_opens(self):
        observed = {}
        cleared = []

        with mock.patch.object(
            self.messages,
            "_load_file_open_trigger_state",
            # Must look fresh, or the trigger expires before the alert path.
            return_value={"pending": True, "created_at": time.time()},
        ), mock.patch.object(
            self.messages, "_clear_file_open_trigger_pending", side_effect=lambda: cleared.append(True)
        ), mock.patch.object(
            self.messages, "_get_active_doc_from_uiapp", return_value=self.doc
        ), mock.patch.object(
            self.messages, "_is_doc_eligible_for_file_open", return_value=True
        ), mock.patch.object(
            self.messages,
            "run_start_message_workflow",
            side_effect=lambda doc=None, force=False: observed.setdefault(
                "cleared_before_modal", bool(cleared)
            ),
        ):
            self.messages._process_file_open_trigger_pending(uiapp=self.uiapp)

        self.assertTrue(observed["cleared_before_modal"])

    def test_failed_report_render_does_not_escape(self):
        """The text fallback is bare prints; from a delegate there may be no
        script output stream behind sys.stdout."""
        with mock.patch.object(
            self.messages, "_show_coordination_review_dialog", return_value=False
        ), mock.patch.object(
            self.messages, "_get_output_window", return_value=None
        ), mock.patch.object(
            self.messages, "_render_report_text", side_effect=RuntimeError("no stream")
        ), mock.patch.object(
            self.messages, "_disable_passive_coordination_review_detector"
        ), mock.patch.object(
            self.messages, "_build_coordination_detection_error_report", return_value={}
        ):
            # Must not raise.
            self.messages._print_coordination_review_report(self.doc)

    def test_report_keeps_captured_warnings_for_reruns(self):
        """Start Message can be re-run: the report must read the passively
        captured warnings without consuming them, or the second run shows a
        bogus Detection Error.  hooks/doc-closing.py clears them on close."""
        calls = []
        fake_passive = types.ModuleType("easybim.coordination_review_passive")

        def _build(doc, consume=True):
            calls.append(consume)
            return {"doc_title": "sample.rvt"}

        fake_passive.build_passive_coordination_report = _build
        fake_passive.unregister_passive_detector = lambda: None

        with mock.patch.dict(
            sys.modules, {"easybim.coordination_review_passive": fake_passive}
        ), mock.patch.object(
            self.messages, "_show_coordination_review_dialog", return_value=True
        ):
            self.messages._print_coordination_review_report(self.doc)
            self.messages._print_coordination_review_report(self.doc)

        self.assertEqual(calls, [False, False])


if __name__ == "__main__":
    unittest.main()
