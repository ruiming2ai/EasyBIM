import importlib.util
import os
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
    IsWorkshared = False
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
        self.ActiveView = doc.view


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

    def __init__(self, doc, document_id=None):
        self.Document = doc
        if document_id is not None:
            self.DocumentId = document_id
        self.cancel_calls = 0

    def Cancel(self):
        self.cancel_calls += 1


class FakeClosedArgs(object):
    def __init__(self, document_id, status=None, doc=None):
        self.DocumentId = document_id
        if status is not None:
            self.Status = status
        if doc is not None:
            self.Document = doc


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

    def test_unarmed_document_with_untracked_tvp_is_not_intercepted(self):
        self.view_state = {
            "last_seen_tvp": {},
            "view_sessions": {},
            "armed_documents": {},
        }
        with mock.patch.object(
            self.runtime,
            "collect_tvp_summary",
            return_value={"has_restore_work": True},
        ) as collect_summary:
            args = FakeClosingArgs(self.doc)
            self.assertFalse(self.runtime.handle_doc_closing(self.uiapp, args))

        self.assertEqual(0, args.cancel_calls)
        collect_summary.assert_not_called()
        self.assertFalse(self.runtime._get_state()["pending_closes"])

    def test_successful_apply_arm_is_document_specific(self):
        state = {"armed_documents": {}}
        view_runtime = self.runtime.temp_phase_view
        identity = view_runtime._arm_document(state, self.doc)

        self.assertEqual("path|c:\\models\\sample.rvt", identity)
        self.assertEqual(1, len(state["armed_documents"]))
        self.assertTrue(
            self.runtime._is_document_armed(
                state,
                "path|c:\\models\\sample.rvt",
                42,
            )
        )

    def test_successful_button_apply_arms_only_active_document(self):
        view_runtime = self.runtime.temp_phase_view
        state = {
            "last_seen_tvp": {},
            "view_sessions": {},
            "armed_documents": {},
        }
        view_runtime._get_uiapp = lambda: self.uiapp
        view_runtime._get_state = lambda: state
        view_runtime._save_state = lambda unused_state: None
        view_runtime._get_view_phase_id = lambda unused_view: 4

        with mock.patch.object(
            view_runtime, "_show_phase_picker", return_value=(view_runtime.ACTION_APPLY, 5)
        ), mock.patch.object(
            view_runtime,
            "_apply_selected_phase_transaction",
            return_value=True,
        ):
            view_runtime.run_pushbutton()

        self.assertEqual(
            {
                "path|c:\\models\\sample.rvt"
            },
            set(state["armed_documents"]),
        )
        self.assertEqual(1, len(state["view_sessions"]))
        self.assertFalse(
            self.runtime._is_document_armed(
                state,
                "path|c:\\models\\other.rvt",
                43,
            )
        )

    def test_picker_cancel_and_failed_apply_do_not_arm_document(self):
        view_runtime = self.runtime.temp_phase_view
        state = {
            "last_seen_tvp": {},
            "view_sessions": {},
            "armed_documents": {},
        }
        view_runtime._get_uiapp = lambda: self.uiapp
        view_runtime._get_state = lambda: state
        view_runtime._save_state = lambda unused_state: None
        view_runtime._get_view_phase_id = lambda unused_view: 4
        view_runtime._collect_document_phases = lambda unused_doc: [
            {"id": 4, "name": "Existing"}
        ]

        with mock.patch.object(
            view_runtime, "_show_phase_picker", return_value=(None, None)
        ):
            view_runtime.run_pushbutton()
        self.assertFalse(state["armed_documents"])

        with mock.patch.object(
            view_runtime, "_show_phase_picker", return_value=(view_runtime.ACTION_APPLY, 5)
        ), mock.patch.object(
            view_runtime,
            "_apply_selected_phase_transaction",
            return_value=False,
        ), mock.patch.object(view_runtime, "_show_alert"):
            view_runtime.run_pushbutton()
        self.assertFalse(state["armed_documents"])

    def test_armed_document_with_untracked_tvp_is_intercepted(self):
        self.view_state = {
            "last_seen_tvp": {},
            "view_sessions": {},
            "armed_documents": {},
        }
        self.runtime.temp_phase_view._arm_document(self.view_state, self.doc)
        summary = {
            "has_restore_work": True,
            "doc_is_workshared": False,
            "tracked_restore_views": [],
            "untracked_tvp_views": [{"view_id": 11}],
        }
        with mock.patch.object(
            self.runtime, "collect_tvp_summary", return_value=summary
        ) as collect_summary:
            args = FakeClosingArgs(self.doc)
            self.assertTrue(self.runtime.handle_doc_closing(self.uiapp, args))

        self.assertEqual(1, args.cancel_calls)
        collect_summary.assert_called_once()
        self.assertEqual(1, len(self.runtime._get_state()["pending_closes"]))

    def test_document_trigger_isolated_from_second_open_document(self):
        other_doc = FakeDocument()
        other_doc.PathName = r"C:\Models\other.rvt"
        other_doc.DocumentId = 43
        other_doc.Title = "other.rvt"
        self.uiapp.Application.Documents.append(other_doc)
        self.view_state = {
            "last_seen_tvp": {},
            "view_sessions": {},
            "armed_documents": {
                "path|c:\\models\\sample.rvt": {
                    "doc_key": "path|c:\\models\\sample.rvt",
                    "doc_runtime_id": 42,
                }
            },
        }

        args = FakeClosingArgs(other_doc)
        self.assertFalse(self.runtime.handle_doc_closing(self.uiapp, args))
        self.assertEqual(0, args.cancel_calls)

    def test_armed_inactive_document_is_still_intercepted(self):
        other_doc = FakeDocument()
        other_doc.PathName = r"C:\Models\other.rvt"
        other_doc.DocumentId = 43
        other_doc.Title = "other.rvt"
        self.uiapp.Application.Documents.append(other_doc)
        self.view_state = {
            "last_seen_tvp": {},
            "view_sessions": {},
            "armed_documents": {},
        }
        self.runtime.temp_phase_view._arm_document(self.view_state, self.doc)
        summary = {
            "has_restore_work": True,
            "doc_is_workshared": False,
            "tracked_restore_views": [],
            "untracked_tvp_views": [{"view_id": 11}],
        }
        with mock.patch.object(
            self.runtime, "collect_tvp_summary", return_value=summary
        ):
            args = FakeClosingArgs(self.doc)
            self.assertTrue(self.runtime.handle_doc_closing(self.uiapp, args))
        self.assertEqual(1, args.cancel_calls)

    def test_doc_closed_clears_document_trigger(self):
        self.view_state["armed_documents"] = {
            "path|c:\\models\\sample.rvt": {
                "doc_key": "path|c:\\models\\sample.rvt",
                "doc_runtime_id": 42,
            }
        }

        self.assertTrue(
            self.runtime.handle_doc_closed(self.uiapp, FakeClosedArgs(42))
        )
        self.assertFalse(self.view_state["armed_documents"])

    def test_document_closed_resolves_close_handoff_when_event_id_differs_runtime_id(self):
        """DocumentClosed must use the saved path/runtime identity, not only its event id."""
        self.view_state["armed_documents"] = {}
        self.runtime.temp_phase_view._arm_document(self.view_state, self.doc)
        summary = {
            "has_restore_work": True,
            "doc_is_workshared": False,
            "tracked_restore_views": [{"view_id": 11}],
            "untracked_tvp_views": [],
        }
        closing = FakeClosingArgs(self.doc, document_id=9001)
        with mock.patch.object(self.runtime, "collect_tvp_summary", return_value=summary):
            self.assertTrue(self.runtime.handle_doc_closing(self.uiapp, closing))

        state = self.runtime._get_state()
        self.assertTrue(state.get("closing_identities"))
        handoff = next(iter(state["closing_identities"].values()))
        self.assertEqual("path|c:\\models\\sample.rvt", handoff.get("doc_key"))
        self.assertEqual(9001, handoff.get("event_document_id"))
        self.assertEqual(42, handoff.get("doc_runtime_id"))

        # The close event exposes 9001, while the live document runtime id was
        # 42.  The saved handoff still identifies the original session.
        self.assertTrue(
            self.runtime.handle_doc_closed(
                self.uiapp,
                FakeClosedArgs(9001, status="Succeeded"),
            )
        )
        self.assertFalse(self.view_state["armed_documents"])
        self.assertFalse(self.view_state["view_sessions"])

    def test_cancelled_close_discards_handoff_but_retains_arm_and_sessions(self):
        self.view_state["armed_documents"] = {}
        self.runtime.temp_phase_view._arm_document(self.view_state, self.doc)
        summary = {
            "has_restore_work": True,
            "doc_is_workshared": False,
            "tracked_restore_views": [{"view_id": 11}],
            "untracked_tvp_views": [],
        }
        with mock.patch.object(self.runtime, "collect_tvp_summary", return_value=summary):
            self.assertTrue(
                self.runtime.handle_doc_closing(
                    self.uiapp,
                    FakeClosingArgs(self.doc, document_id=9002),
                )
            )

        state = self.runtime._get_state()
        self.assertTrue(state.get("closing_identities"))
        self.assertFalse(
            self.runtime.handle_doc_closed(
                self.uiapp,
                FakeClosedArgs(9002, status="Cancelled"),
            )
        )
        self.assertFalse(state.get("closing_identities"))
        self.assertTrue(self.view_state["armed_documents"])
        self.assertTrue(self.view_state["view_sessions"])

    def test_legacy_arm_without_tracked_session_is_ignored_and_removed(self):
        self.view_state = {
            "last_seen_tvp": {},
            "view_sessions": {},
            "armed_documents": {
                "path|c:\\models\\sample.rvt": {
                    "doc_key": "path|c:\\models\\sample.rvt",
                    "doc_runtime_id": 42,
                    "armed_by": "successful_apply",
                }
            },
        }
        with mock.patch.object(
            self.runtime,
            "collect_tvp_summary",
            return_value={"has_restore_work": True},
        ) as collect_summary:
            args = FakeClosingArgs(self.doc, document_id=42)
            self.assertFalse(self.runtime.handle_doc_closing(self.uiapp, args))

        self.assertEqual(0, args.cancel_calls)
        collect_summary.assert_not_called()
        self.assertFalse(self.view_state["armed_documents"])

    def test_arm_from_another_revit_process_is_ignored_and_removed(self):
        self.view_state["armed_documents"] = {}
        self.runtime.temp_phase_view._arm_document(self.view_state, self.doc)
        record = self.view_state["armed_documents"]["path|c:\\models\\sample.rvt"]
        record["revit_process_id"] = os.getpid() + 1000000

        with mock.patch.object(
            self.runtime,
            "collect_tvp_summary",
            return_value={"has_restore_work": True},
        ) as collect_summary:
            args = FakeClosingArgs(self.doc, document_id=42)
            self.assertFalse(self.runtime.handle_doc_closing(self.uiapp, args))

        self.assertEqual(0, args.cancel_calls)
        collect_summary.assert_not_called()
        self.assertFalse(self.view_state["armed_documents"])

    def test_reopening_same_path_after_successful_close_is_unarmed(self):
        self.view_state["armed_documents"] = {}
        self.runtime.temp_phase_view._arm_document(self.view_state, self.doc)
        summary = {
            "has_restore_work": True,
            "doc_is_workshared": False,
            "tracked_restore_views": [{"view_id": 11}],
            "untracked_tvp_views": [],
        }
        with mock.patch.object(self.runtime, "collect_tvp_summary", return_value=summary):
            self.assertTrue(
                self.runtime.handle_doc_closing(
                    self.uiapp,
                    FakeClosingArgs(self.doc, document_id=9003),
                )
            )
        self.assertTrue(
            self.runtime.handle_doc_closed(
                self.uiapp,
                FakeClosedArgs(9003, status="Succeeded"),
            )
        )
        self.assertFalse(self.view_state["armed_documents"])

        reopened = FakeDocument()
        reopened.DocumentId = 99
        reopened_uiapp = FakeUiapp(reopened)
        with mock.patch.object(
            self.runtime,
            "collect_tvp_summary",
            return_value={"has_restore_work": True},
        ) as collect_summary:
            args = FakeClosingArgs(reopened, document_id=9004)
            self.assertFalse(self.runtime.handle_doc_closing(reopened_uiapp, args))

        self.assertEqual(0, args.cancel_calls)
        collect_summary.assert_not_called()

    def test_normal_close_after_restore_clears_arm_when_no_work_remains(self):
        self.view_state["armed_documents"] = {}
        self.runtime.temp_phase_view._arm_document(self.view_state, self.doc)
        with mock.patch.object(
            self.runtime,
            "collect_tvp_summary",
            return_value={"has_restore_work": False},
        ) as collect_summary:
            args = FakeClosingArgs(self.doc, document_id=9005)
            self.assertFalse(self.runtime.handle_doc_closing(self.uiapp, args))

        self.assertEqual(0, args.cancel_calls)
        collect_summary.assert_called_once()
        self.assertTrue(self.runtime._get_state()["closing_identities"])
        self.assertTrue(
            self.runtime.handle_doc_closed(
                self.uiapp,
                FakeClosedArgs(9005, status="Succeeded"),
            )
        )
        self.assertFalse(self.view_state["armed_documents"])

    def test_current_process_arm_survives_keep_file_open(self):
        self.view_state["armed_documents"] = {}
        self.runtime.temp_phase_view._arm_document(self.view_state, self.doc)
        summary = {
            "has_restore_work": True,
            "doc_is_workshared": False,
            "tracked_restore_views": [{"view_id": 11}],
            "untracked_tvp_views": [],
        }
        with mock.patch.object(self.runtime, "collect_tvp_summary", return_value=summary):
            self.assertTrue(
                self.runtime.handle_doc_closing(
                    self.uiapp,
                    FakeClosingArgs(self.doc),
                )
            )

        state = self.runtime._get_state()
        for record in state["pending_closes"].values():
            record["next_try_at"] = 0
        with mock.patch.object(
            self.runtime,
            "collect_tvp_summary",
            return_value={"has_restore_work": False},
        ), mock.patch.object(self.runtime, "_show_close_decision", return_value="cancel"):
            self.runtime.handle_app_idling(self.uiapp)

        self.assertTrue(self.view_state["armed_documents"])
        self.assertEqual(
            os.getpid(),
            self.view_state["armed_documents"]["path|c:\\models\\sample.rvt"][
                "revit_process_id"
            ],
        )

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

    def test_close_repost_posts_once_and_doc_closed_cleans_state(self):
        args = FakeClosingArgs(self.doc)
        self.runtime.handle_doc_closing(self.uiapp, args)
        state = self.runtime._get_state()
        token, record = next(iter(state["pending_closes"].items()))

        class FakeCommandId(object):
            @staticmethod
            def LookupPostableCommandId(command):
                return ("close", command)

        class FakePostableCommand(object):
            Close = "Close"

        class FakeUi(object):
            PostableCommand = FakePostableCommand
            RevitCommandId = FakeCommandId

        with mock.patch.object(self.runtime, "_get_ui", return_value=FakeUi):
            self.assertTrue(self.runtime._post_close_once(self.uiapp, state, token, record))

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
                {
                    "CommandLink1": command_link_one,
                    "CommandLink2": command_link_two,
                    "CommandLink3": object(),
                },
            )
            TaskDialogResult = type(
                "TaskDialogResult",
                (object,),
                {
                    "CommandLink1": result_command_link_one,
                    "CommandLink2": object(),
                    "CommandLink3": object(),
                },
            )
            TaskDialogCommonButtons = type(
                "TaskDialogCommonButtons", (object,), {"Cancel": object()}
            )

            @staticmethod
            def TaskDialog(title):
                return FakeDialog(title)

        with mock.patch.object(self.runtime, "_get_ui", return_value=FakeUi):
            decision = self.runtime._show_close_decision(
                {
                    "dialog_view_lines": [],
                    "doc_title": "sample.rvt",
                    "doc_is_workshared": False,
                },
                {"title": "sample.rvt"},
            )

        self.assertEqual("save_close", decision)

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
