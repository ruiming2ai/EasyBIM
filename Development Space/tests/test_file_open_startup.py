"""Contract for the file-open startup flow after the alert's removal.

DocumentOpened fires before the opened document is active, so windows raised
from the doc-opened hook are refused or land behind the main window - the
removed onboarding alert used to absorb that gap by blocking until its OK
click.  The hook therefore only marks the file-open trigger, and
``hooks/view-activated.py`` drains it once Revit activates the document -
the moment that OK click used to land.  These tests pin both halves and the
passive detector's lifecycle around them.
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


class FakeFamilyDocument(FakeDocument):
    IsFamilyDocument = True


class FakeUidoc(object):
    def __init__(self, doc):
        self.Document = doc


class FakeUiapp(object):
    def __init__(self, doc):
        self.ActiveUIDocument = FakeUidoc(doc)


class FileOpenStartupTests(unittest.TestCase):
    def setUp(self):
        self.messages = _load_messages()
        # Real mark/clear logic over a dict-backed store instead of pyRevit
        # envvars, so the tests drive the actual state transitions.
        self.trigger = {"pending": False, "created_at": 0.0}
        for patcher in (
            mock.patch.object(
                self.messages,
                "_load_file_open_trigger_state",
                side_effect=lambda: dict(self.trigger),
            ),
            mock.patch.object(
                self.messages,
                "_save_file_open_trigger_state",
                side_effect=lambda state: (self.trigger.update(state), True)[1],
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- the doc-opened half ----------------------------------------------

    def test_eligible_open_marks_the_trigger_and_raises_nothing(self):
        """Inline-from-DocumentOpened is the regression path: the document is
        not active yet, so windows raised here are refused or buried."""
        ran = []
        with mock.patch.object(
            self.messages,
            "run_start_message_workflow",
            side_effect=lambda **kwargs: ran.append(kwargs),
        ):
            self.messages.run_start_message_on_file_open(doc=FakeDocument())

        self.assertEqual([], ran)
        self.assertTrue(self.trigger["pending"])

    def test_open_without_a_document_context_marks_the_trigger(self):
        """The common fresh-launch case: ActiveUIDocument is not set yet."""
        self.messages.run_start_message_on_file_open(doc=None)

        self.assertTrue(self.trigger["pending"])

    def test_family_document_clears_and_detaches_instead_of_marking(self):
        self.trigger.update({"pending": True, "created_at": time.time()})
        detached = []
        with mock.patch.object(
            self.messages,
            "_disable_passive_coordination_review_detector",
            side_effect=lambda: detached.append(True),
        ):
            self.messages.run_start_message_on_file_open(doc=FakeFamilyDocument())

        self.assertFalse(self.trigger["pending"])
        self.assertEqual([True], detached)

    # -- the view-activated half --------------------------------------------

    def test_pending_trigger_runs_the_workflow_against_the_active_doc(self):
        self.trigger.update({"pending": True, "created_at": time.time()})
        doc = FakeDocument()
        ran = []
        with mock.patch.object(
            self.messages,
            "run_start_message_workflow",
            side_effect=lambda doc=None, force=False: ran.append(doc),
        ):
            self.messages.run_pending_file_open_startup(uiapp=FakeUiapp(doc))

        self.assertEqual([doc], ran)
        self.assertFalse(self.trigger["pending"])

    def test_drained_trigger_makes_the_next_call_a_noop(self):
        """The Idling delegate consumes the same trigger; whichever reader
        fires first must leave nothing for the other."""
        self.trigger.update({"pending": True, "created_at": time.time()})
        uiapp = FakeUiapp(FakeDocument())
        ran = []
        with mock.patch.object(
            self.messages,
            "run_start_message_workflow",
            side_effect=lambda doc=None, force=False: ran.append(doc),
        ):
            self.messages.run_pending_file_open_startup(uiapp=uiapp)
            self.messages.run_pending_file_open_startup(uiapp=uiapp)

        self.assertEqual(1, len(ran))

    def test_expired_trigger_is_cleared_and_the_detector_detached(self):
        """When the workflow never runs, the report's finally never detaches
        the passive detector - the expiry path has to do it itself."""
        stale = time.time() - (
            self.messages._FILE_OPEN_TRIGGER_MAX_AGE_SEC + 5.0
        )
        self.trigger.update({"pending": True, "created_at": stale})
        detached = []
        ran = []
        with mock.patch.object(
            self.messages,
            "_disable_passive_coordination_review_detector",
            side_effect=lambda: detached.append(True),
        ), mock.patch.object(
            self.messages,
            "run_start_message_workflow",
            side_effect=lambda **kwargs: ran.append(kwargs),
        ):
            self.messages.run_pending_file_open_startup(
                uiapp=FakeUiapp(FakeDocument())
            )

        self.assertEqual([], ran)
        self.assertFalse(self.trigger["pending"])
        self.assertEqual([True], detached)

    # -- wiring --------------------------------------------------------------

    def test_view_activated_hook_drains_the_trigger(self):
        hook_text = (ROOT / "hooks" / "view-activated.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("run_pending_file_open_startup", hook_text)
        self.assertIn("ensure_installed(__revit__)", hook_text)

    def test_doc_opened_hook_still_calls_the_marking_entry_point(self):
        hook_text = (ROOT / "hooks" / "doc-opened.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("run_start_message_on_file_open", hook_text)


if __name__ == "__main__":
    unittest.main()
