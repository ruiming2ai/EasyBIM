"""Runtime contract tests for the Temp Phase close/commit workflow.

These tests deliberately use small Revit API fakes.  They exercise the
state machine around the post-restore dialog and completion events without
opening transactions or requiring a Revit installation.
"""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_runtime():
    lib_root = ROOT / "lib" / "easybim"
    package = types.ModuleType("easybim")
    package.__path__ = [str(lib_root)]
    sys.modules["easybim"] = package

    view_spec = importlib.util.spec_from_file_location(
        "easybim.temp_phase_view", str(lib_root / "temp_phase_view.py")
    )
    view_module = importlib.util.module_from_spec(view_spec)
    sys.modules["easybim.temp_phase_view"] = view_module
    view_spec.loader.exec_module(view_module)
    package.temp_phase_view = view_module

    # The close module keeps delegate/state data in a stable runtime module so
    # pyRevit reloads do not duplicate handlers.  Remove the test copy before
    # loading each module to keep tests isolated.
    sys.modules.pop("easybim._temp_phase_close_runtime", None)
    close_spec = importlib.util.spec_from_file_location(
        "easybim.temp_phase_close", str(lib_root / "temp_phase_close.py")
    )
    close_module = importlib.util.module_from_spec(close_spec)
    sys.modules["easybim.temp_phase_close"] = close_module
    close_spec.loader.exec_module(close_module)
    return close_module


class FakeView(object):
    IsValidObject = True
    IsTemplate = False

    def __init__(self, view_id=11):
        self.Id = view_id
        self.Name = "Plan"
        self.phase_id = 4
        self.tvp_active = True


class FakeDocument(object):
    IsValidObject = True
    IsFamilyDocument = False
    IsLinked = False
    IsWorkshared = False
    PathName = r"C:\Models\sample.rvt"
    DocumentId = 42
    Title = "sample.rvt"

    def __init__(self, workshared=False):
        self.IsWorkshared = workshared
        self.view = FakeView()

    def GetHashCode(self):
        return 4200

    def GetElement(self, element_id):
        return self.view if int(element_id) == self.view.Id else None


class FakeUidoc(object):
    def __init__(self, doc):
        self.Document = doc


class FakeApplication(object):
    IsClosing = False

    def __init__(self, doc):
        self.Documents = [doc]


class FakeUiapp(object):
    def __init__(self, doc):
        self.ActiveUIDocument = FakeUidoc(doc)
        self.Application = FakeApplication(doc)
        self.posted = []

    def CanPostCommand(self, command_id):
        return True

    def PostCommand(self, command_id):
        self.posted.append(command_id)


class FakeClosingArgs(object):
    Cancellable = True

    def __init__(self, doc):
        self.Document = doc
        self.DocumentId = doc.DocumentId
        self.cancel_calls = 0

    def Cancel(self):
        self.cancel_calls += 1


class FakeCompletionArgs(object):
    def __init__(self, doc, status="Succeeded"):
        self.Document = doc
        self.DocumentId = doc.DocumentId
        self.Status = status


class FakeDialog(object):
    def __init__(self, result):
        self.result = result
        self.links = []

    def AddCommandLink(self, link_id, text):
        self.links.append((link_id, text))

    def Show(self):
        return self.result


class FakeUi(object):
    """Combined TaskDialog and PostableCommand surface used by the tests."""

    class _PostableCommand(object):
        Close = "Close"
        Save = "Save"
        SynchronizeAndModifySettings = "SynchronizeAndModifySettings"

    class _RevitCommandId(object):
        @staticmethod
        def LookupPostableCommandId(command):
            return ("command-id", command)

    PostableCommand = _PostableCommand
    RevitCommandId = _RevitCommandId

    class TaskDialogCommandLinkId(object):
        CommandLink1 = object()
        CommandLink2 = object()
        CommandLink3 = object()

    class TaskDialogResult(object):
        CommandLink1 = object()
        CommandLink2 = object()
        CommandLink3 = object()

    class TaskDialogCommonButtons(object):
        Cancel = object()

    def __init__(self, result):
        self.dialogs = []
        self.result = result

    def TaskDialog(self, title):
        dialog = FakeDialog(self.result)
        self.dialogs.append(dialog)
        return dialog


class TempPhaseCommitRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = _load_runtime()
        self.doc = FakeDocument()
        self.uiapp = FakeUiapp(self.doc)
        self.command_ui = FakeUi(None)
        self.view_state = {
            "last_seen_tvp": {"path|c:\\models\\sample.rvt|11": True},
            "view_sessions": {
                "path|c:\\models\\sample.rvt|11": {
                    "doc_key": "path|c:\\models\\sample.rvt",
                    "doc_runtime_id": 42,
                    "view_id": 11,
                    "view_name": "Plan",
                    "original_phase_id": 3,
                }
            },
        }
        self.runtime._MEMORY_STATE.clear()
        self.runtime._get_view_state = lambda: self.view_state
        self.runtime._save_view_state = lambda state: None
        self.runtime._int_to_eid = lambda value: value

    def _queue_restore_and_choose(self, decision, workshared=False):
        """Queue a cancelled close and drive it through the decision dialog."""
        if workshared:
            self.doc.IsWorkshared = True
        closing = FakeClosingArgs(self.doc)
        self.assertTrue(self.runtime.handle_doc_closing(self.uiapp, closing))
        state = self.runtime._get_state()
        for record in state["pending_closes"].values():
            record["next_try_at"] = 0

        summary = {
            "has_restore_work": False,
            "doc_is_workshared": bool(workshared),
            "doc_title": "sample.rvt",
            "dialog_view_lines": [],
        }
        with mock.patch.object(
            self.runtime, "collect_tvp_summary", return_value=summary
        ), mock.patch.object(
            self.runtime, "_show_close_decision", return_value=decision
        ), mock.patch.object(
            self.runtime, "_get_ui", return_value=self.command_ui
        ):
            self.runtime.handle_app_idling(self.uiapp)
        return state

    def _set_idle_ready(self, state):
        del state
        # The idle throttle lives in a module global now, not in the state.
        self.runtime._LAST_IDLE_AT[0] = 0.0

    def test_non_workshared_dialog_exposes_save_and_keep_open_only(self):
        ui = FakeUi(FakeUi.TaskDialogResult.CommandLink1)
        with mock.patch.object(self.runtime, "_get_ui", return_value=ui):
            decision = self.runtime._show_close_decision(
                {
                    "doc_title": "sample.rvt",
                    "doc_is_workshared": False,
                    "dialog_view_lines": [],
                    "tracked_restore_views": [],
                    "untracked_tvp_views": [],
                },
                {"title": "sample.rvt"},
            )

        self.assertEqual("save_close", decision)
        labels = [text for _, text in ui.dialogs[0].links]
        self.assertIn("Save and Close", labels)
        self.assertIn("Keep File Open", labels)
        self.assertFalse(any("Synchronize" in text for text in labels))

    def test_workshared_dialog_maps_sync_and_keep_open_links(self):
        ui = FakeUi(FakeUi.TaskDialogResult.CommandLink2)
        with mock.patch.object(self.runtime, "_get_ui", return_value=ui):
            decision = self.runtime._show_close_decision(
                {
                    "doc_title": "sample.rvt",
                    "doc_is_workshared": True,
                    "dialog_view_lines": [],
                    "tracked_restore_views": [],
                    "untracked_tvp_views": [],
                },
                {"title": "sample.rvt"},
            )

        self.assertEqual("sync_close", decision)
        labels = [text for _, text in ui.dialogs[0].links]
        self.assertEqual(3, len(labels))
        self.assertIn("Save and Close", labels)
        self.assertIn("Synchronize and Close", labels)
        self.assertIn("Keep File Open", labels)

    def test_quiet_idling_tick_does_not_save_state(self):
        """The nothing-pending tick must not write state back."""
        self.runtime._LAST_IDLE_AT[0] = 0.0
        with mock.patch.object(self.runtime, "_save_state") as save_state:
            self.runtime.handle_app_idling(self.uiapp)
        save_state.assert_not_called()

    def test_idling_is_throttled_before_any_state_read(self):
        """Within the throttle window the tick pays no state traffic at all."""
        self.runtime._LAST_IDLE_AT[0] = 0.0
        self.runtime.handle_app_idling(self.uiapp)
        with mock.patch.object(self.runtime, "_get_state") as get_state:
            self.runtime.handle_app_idling(self.uiapp)
        get_state.assert_not_called()

    def test_keep_open_choice_does_not_start_commit(self):
        state = self._queue_restore_and_choose("cancel")
        self.assertFalse(self.uiapp.posted)
        self.assertFalse(state.get("commit_operations"))
        self.assertFalse(state.get("pending_commit_closes"))

    def test_save_success_posts_close_once_after_completion(self):
        state = self._queue_restore_and_choose("save_close")
        self.assertEqual(
            [("command-id", FakeUi.PostableCommand.Save)], self.uiapp.posted
        )
        self.assertEqual(1, len(state.get("commit_operations", {})))

        args = FakeCompletionArgs(self.doc, "Succeeded")
        self.runtime.document_saved_handler(self.uiapp, args)
        self.assertFalse(state.get("commit_operations"))
        self.assertEqual(1, len(state.get("pending_commit_closes", {})))

        self._set_idle_ready(state)
        with mock.patch.object(self.runtime, "_get_ui", return_value=FakeUi(None)):
            self.runtime.handle_app_idling(self.uiapp)
        self.assertEqual(
            [
                ("command-id", FakeUi.PostableCommand.Save),
                ("command-id", FakeUi.PostableCommand.Close),
            ],
            self.uiapp.posted,
        )

        # A duplicate completion event must not create a second Close post.
        self.runtime.document_saved_handler(self.uiapp, args)
        self._set_idle_ready(state)
        with mock.patch.object(self.runtime, "_get_ui", return_value=FakeUi(None)):
            self.runtime.handle_app_idling(self.uiapp)
        self.assertEqual(2, len(self.uiapp.posted))

    def test_document_saved_as_is_accepted_for_save_flow(self):
        state = self._queue_restore_and_choose("save_close")
        self.runtime.document_saved_as_handler(
            self.uiapp, FakeCompletionArgs(self.doc, "Succeeded")
        )
        self.assertFalse(state.get("commit_operations"))
        self.assertEqual(1, len(state.get("pending_commit_closes", {})))

    def test_cancelled_save_leaves_restored_document_open(self):
        state = self._queue_restore_and_choose("save_close")
        self.runtime.document_saved_handler(
            self.uiapp, FakeCompletionArgs(self.doc, "Cancelled")
        )
        self.assertFalse(state.get("commit_operations"))
        self.assertFalse(state.get("pending_commit_closes"))
        self._set_idle_ready(state)
        with mock.patch.object(self.runtime, "_show_alert"):
            self.runtime.handle_app_idling(self.uiapp)
        self.assertEqual(1, len(self.uiapp.posted))

    def test_sync_success_posts_close_once_after_completion(self):
        state = self._queue_restore_and_choose("sync_close", workshared=True)
        self.assertEqual(
            [("command-id", FakeUi.PostableCommand.SynchronizeAndModifySettings)],
            self.uiapp.posted,
        )
        self.runtime.document_synchronized_with_central_handler(
            self.uiapp, FakeCompletionArgs(self.doc, "Succeeded")
        )
        self.assertFalse(state.get("commit_operations"))
        self.assertEqual(1, len(state.get("pending_commit_closes", {})))

        self._set_idle_ready(state)
        with mock.patch.object(self.runtime, "_get_ui", return_value=FakeUi(None)):
            self.runtime.handle_app_idling(self.uiapp)
        self.assertEqual(
            [
                ("command-id", FakeUi.PostableCommand.SynchronizeAndModifySettings),
                ("command-id", FakeUi.PostableCommand.Close),
            ],
            self.uiapp.posted,
        )

    def test_cancelled_sync_leaves_document_open(self):
        state = self._queue_restore_and_choose("sync_close", workshared=True)
        self.runtime.document_synchronized_with_central_handler(
            self.uiapp, FakeCompletionArgs(self.doc, "Cancelled")
        )
        self.assertFalse(state.get("commit_operations"))
        self.assertFalse(state.get("pending_commit_closes"))
        self.assertEqual(1, len(self.uiapp.posted))


if __name__ == "__main__":
    unittest.main()
