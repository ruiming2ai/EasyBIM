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
    PathName = r"C:\Models\sample.rvt"
    DocumentId = 42
    Title = "sample.rvt"

    def __init__(self):
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
        self.cancel_calls = 0

    def Cancel(self):
        self.cancel_calls += 1


class FakeClosedArgs(object):
    def __init__(self, document_id):
        self.DocumentId = document_id


class TempPhaseCloseRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = _load_runtime()
        self.doc = FakeDocument()
        self.uiapp = FakeUiapp(self.doc)
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

    def test_cancellable_close_is_cancelled_and_duplicate_is_coalesced(self):
        first = FakeClosingArgs(self.doc)
        second = FakeClosingArgs(self.doc)

        self.assertTrue(self.runtime.handle_doc_closing(self.uiapp, first))
        self.assertEqual(1, first.cancel_calls)
        state = self.runtime._get_state()
        self.assertEqual(1, len(state["pending_closes"]))
        generation = list(state["pending_closes"].values())[0]["generation"]

        self.assertTrue(self.runtime.handle_doc_closing(self.uiapp, second))
        self.assertEqual(1, len(state["pending_closes"]))
        self.assertEqual(generation, list(state["pending_closes"].values())[0]["generation"])

    def test_non_cancellable_close_is_left_alone(self):
        args = FakeClosingArgs(self.doc)
        args.Cancellable = False

        self.assertFalse(self.runtime.handle_doc_closing(self.uiapp, args))
        self.assertEqual(0, args.cancel_calls)
        self.assertFalse(self.runtime._get_state()["pending_closes"])

    def test_cancel_close_clears_pending_without_repost(self):
        args = FakeClosingArgs(self.doc)
        self.runtime.handle_doc_closing(self.uiapp, args)
        state = self.runtime._get_state()
        for record in state["pending_closes"].values():
            record["next_try_at"] = 0

        with mock.patch.object(self.runtime, "_show_close_decision", return_value="cancel"), mock.patch.object(
            self.runtime, "collect_tvp_summary", return_value={"has_restore_work": False}
        ):
            self.runtime.handle_app_idling(self.uiapp)

        self.assertFalse(state["pending_closes"])
        self.assertFalse(self.uiapp.posted)

    def test_close_file_now_posts_once_and_doc_closed_cleans_state(self):
        args = FakeClosingArgs(self.doc)
        self.runtime.handle_doc_closing(self.uiapp, args)
        state = self.runtime._get_state()
        for record in state["pending_closes"].values():
            record["next_try_at"] = 0

        class FakeCommandId(object):
            @staticmethod
            def LookupPostableCommandId(command):
                return ("close", command)

        class FakePostableCommand(object):
            Close = "Close"

        class FakeUi(object):
            PostableCommand = FakePostableCommand
            RevitCommandId = FakeCommandId

        with mock.patch.object(self.runtime, "_get_ui", return_value=FakeUi), mock.patch.object(
            self.runtime, "_show_close_decision", return_value="close"
        ), mock.patch.object(
            self.runtime, "collect_tvp_summary", return_value={"has_restore_work": False}
        ):
            self.runtime.handle_app_idling(self.uiapp)

        self.assertEqual(1, len(self.uiapp.posted))
        self.assertTrue(state["repost_guards"])

        self.runtime.handle_doc_closed(self.uiapp, FakeClosedArgs(42))
        self.assertFalse(state["repost_guards"])
        self.assertFalse(self.view_state["view_sessions"])

    def test_close_dialog_distinguishes_result_enum_from_command_link_id(self):
        command_link_one = object()
        command_link_two = object()
        result_command_link_one = object()

        class FakeDialog(object):
            def __init__(self, title):
                self.title = title

            def AddCommandLink(self, link_id, text):
                return None

            def Show(self):
                return result_command_link_one

        class FakeUi(object):
            TaskDialogCommandLinkId = type(
                "TaskDialogCommandLinkId",
                (object,),
                {"CommandLink1": command_link_one, "CommandLink2": command_link_two},
            )
            TaskDialogResult = type(
                "TaskDialogResult",
                (object,),
                {"CommandLink1": result_command_link_one},
            )
            TaskDialogCommonButtons = type(
                "TaskDialogCommonButtons", (object,), {"Cancel": object()}
            )

            @staticmethod
            def TaskDialog(title):
                return FakeDialog(title)

        with mock.patch.object(self.runtime, "_get_ui", return_value=FakeUi):
            decision = self.runtime._show_close_decision(
                {"dialog_view_lines": [], "doc_title": "sample.rvt"},
                {"title": "sample.rvt"},
            )

        self.assertEqual("close", decision)

    def test_restore_summary_uses_one_transaction_and_clears_session(self):
        class FakeTransaction(object):
            def __init__(self, doc, name):
                self.committed = False

            def Start(self):
                return None

            def Commit(self):
                self.committed = True

            def RollBack(self):
                raise AssertionError("rollback should not be needed")

        class FakeDb(object):
            Transaction = FakeTransaction

        def disable(view):
            view.tvp_active = False
            return True

        def active(view):
            return bool(view.tvp_active)

        def set_phase(view, phase_id):
            view.phase_id = phase_id
            return True

        summary = self.runtime.collect_tvp_summary(self.uiapp, self.doc, self.view_state)
        with mock.patch.object(self.runtime, "_get_db", return_value=FakeDb), mock.patch.object(
            self.runtime, "_is_tvp_active", side_effect=active
        ), mock.patch.object(self.runtime, "_disable_tvp", side_effect=disable), mock.patch.object(
            self.runtime, "_set_view_phase_id", side_effect=set_phase
        ):
            result = self.runtime.restore_tvp_summary(self.uiapp, self.doc, summary)

        self.assertTrue(result["ok"])
        self.assertEqual(3, self.doc.view.phase_id)
        self.assertFalse(self.doc.view.tvp_active)
        self.assertFalse(self.view_state["view_sessions"])


if __name__ == "__main__":
    unittest.main()
