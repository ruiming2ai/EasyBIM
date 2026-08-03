"""Runtime contract tests for the manual Temp Phase restore commands.

The Restore and Restore All Views buttons reuse the close-recovery restore
transaction, so these tests pin the wiring: which views each command hands
to the shared restore, what it does when there is nothing to restore, and
that a fully restored document stops arming close recovery.
"""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_ROOT = ROOT / "lib" / "easybim"


def _load_runtime():
    package = types.ModuleType("easybim")
    package.__path__ = [str(LIB_ROOT)]
    sys.modules["easybim"] = package

    view_spec = importlib.util.spec_from_file_location(
        "easybim.temp_phase_view", str(LIB_ROOT / "temp_phase_view.py")
    )
    view_module = importlib.util.module_from_spec(view_spec)
    sys.modules["easybim.temp_phase_view"] = view_module
    view_spec.loader.exec_module(view_module)
    package.temp_phase_view = view_module

    sys.modules.pop("easybim._temp_phase_close_runtime", None)
    close_spec = importlib.util.spec_from_file_location(
        "easybim.temp_phase_close", str(LIB_ROOT / "temp_phase_close.py")
    )
    close_module = importlib.util.module_from_spec(close_spec)
    sys.modules["easybim.temp_phase_close"] = close_module
    close_spec.loader.exec_module(close_module)
    package.temp_phase_close = close_module

    return view_module, close_module


class FakeView(object):
    IsValidObject = True
    IsTemplate = False

    def __init__(self, view_id=11, name="Plan"):
        self.Id = view_id
        self.Name = name


class FakeDocument(object):
    IsValidObject = True
    IsFamilyDocument = False
    IsLinked = False
    IsWorkshared = False
    PathName = r"C:\Models\sample.rvt"
    DocumentId = 42
    Title = "sample.rvt"

    def GetHashCode(self):
        return 4200


class FakeUidoc(object):
    def __init__(self, doc, view):
        self.Document = doc
        self.ActiveView = view


class FakeUiapp(object):
    def __init__(self, doc, view):
        self.ActiveUIDocument = FakeUidoc(doc, view)


def _tracked(view_id, name):
    return {
        "key": "path|c:\\models\\sample.rvt|{0}".format(view_id),
        "session": {"original_phase_id": 3, "view_id": view_id},
        "view": FakeView(view_id, name),
        "view_id": view_id,
        "view_name": name,
    }


def _untracked(view_id, name):
    return {"view": FakeView(view_id, name), "view_id": view_id, "view_name": name}


def _summary(tracked=None, untracked=None):
    tracked = list(tracked or [])
    untracked = list(untracked or [])
    return {
        "doc_key": "path|c:\\models\\sample.rvt",
        "doc_runtime_id": 42,
        "doc_title": "sample.rvt",
        "doc_is_workshared": False,
        "tracked_restore_views": tracked,
        "untracked_tvp_views": untracked,
        "discoverable_tvp_views": list(untracked),
        "dialog_view_lines": [],
        "tracked_restore_count": len(tracked),
        "untracked_tvp_count": len(untracked),
        "has_restore_work": bool(tracked or untracked),
    }


def _ok_result(restored=1, cleared=0):
    return {
        "ok": True,
        "restored_phase_views": restored,
        "disabled_untracked_tvp_views": cleared,
        "failed_views": [],
        "message": "",
    }


class TempPhaseRestoreRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.view_module, self.close_module = _load_runtime()
        self.doc = FakeDocument()
        self.view = FakeView(11, "Plan")
        self.uiapp = FakeUiapp(self.doc, self.view)
        self.view_module._get_uiapp = lambda uiapp=None: self.uiapp
        self.alerts = []
        self.view_module._show_alert = lambda title, message: self.alerts.append(message)

    def test_summary_for_view_keeps_only_the_active_view(self):
        summary = _summary(
            tracked=[_tracked(11, "Plan"), _tracked(12, "Section")],
            untracked=[_untracked(13, "Elevation")],
        )

        narrowed = self.view_module._summary_for_view(summary, 11)

        self.assertEqual([11], [item["view_id"] for item in narrowed["tracked_restore_views"]])
        self.assertEqual([], narrowed["untracked_tvp_views"])
        self.assertEqual(1, narrowed["tracked_restore_count"])
        self.assertTrue(narrowed["has_restore_work"])
        # The source summary must not be mutated; the caller may still need it.
        self.assertEqual(2, len(summary["tracked_restore_views"]))

    def test_summary_for_view_matches_untracked_views_too(self):
        summary = _summary(untracked=[_untracked(11, "Plan"), _untracked(12, "Section")])

        narrowed = self.view_module._summary_for_view(summary, 11)

        self.assertEqual([11], [item["view_id"] for item in narrowed["untracked_tvp_views"]])
        self.assertTrue(narrowed["has_restore_work"])

    def test_restore_active_view_restores_only_that_view(self):
        summary = _summary(tracked=[_tracked(11, "Plan"), _tracked(12, "Section")])

        with mock.patch.object(
            self.close_module, "collect_tvp_summary", return_value=summary
        ), mock.patch.object(
            self.close_module, "restore_tvp_summary", return_value=_ok_result()
        ) as restore, mock.patch.object(
            self.view_module, "_clear_document_arm_if_idle"
        ):
            self.assertTrue(self.view_module.run_restore_active_view())

        passed = restore.call_args[1]["summary"]
        self.assertEqual([11], [item["view_id"] for item in passed["tracked_restore_views"]])
        self.assertIn("Plan", self.alerts[0])

    def test_restore_all_views_passes_the_whole_document_summary(self):
        summary = _summary(
            tracked=[_tracked(11, "Plan"), _tracked(12, "Section")],
            untracked=[_untracked(13, "Elevation")],
        )

        with mock.patch.object(
            self.close_module, "collect_tvp_summary", return_value=summary
        ), mock.patch.object(
            self.close_module, "restore_tvp_summary", return_value=_ok_result(2, 1)
        ) as restore, mock.patch.object(
            self.view_module, "_clear_document_arm_if_idle"
        ):
            self.assertTrue(self.view_module.run_restore_all_views())

        passed = restore.call_args[1]["summary"]
        self.assertEqual(2, len(passed["tracked_restore_views"]))
        self.assertEqual(1, len(passed["untracked_tvp_views"]))
        self.assertIn("Views returned to their original phase: 2", self.alerts[0])
        self.assertIn("Additional views with temporary settings cleared: 1", self.alerts[0])

    def test_restore_active_view_without_work_opens_no_transaction(self):
        summary = _summary(tracked=[_tracked(12, "Section")])

        with mock.patch.object(
            self.close_module, "collect_tvp_summary", return_value=summary
        ), mock.patch.object(self.close_module, "restore_tvp_summary") as restore:
            self.assertFalse(self.view_module.run_restore_active_view())

        restore.assert_not_called()
        self.assertIn("no temporary phase or view settings", self.alerts[0])

    def test_restore_all_views_without_work_opens_no_transaction(self):
        with mock.patch.object(
            self.close_module, "collect_tvp_summary", return_value=_summary()
        ), mock.patch.object(self.close_module, "restore_tvp_summary") as restore:
            self.assertFalse(self.view_module.run_restore_all_views())

        restore.assert_not_called()
        self.assertIn("No temporary phase or view settings", self.alerts[0])

    def test_failed_restore_surfaces_the_reason_and_keeps_arming(self):
        summary = _summary(tracked=[_tracked(11, "Plan")])
        failure = {
            "ok": False,
            "restored_phase_views": 0,
            "disabled_untracked_tvp_views": 0,
            "failed_views": ["Plan"],
            "message": "Temporary view properties could not be restored.",
        }

        with mock.patch.object(
            self.close_module, "collect_tvp_summary", return_value=summary
        ), mock.patch.object(
            self.close_module, "restore_tvp_summary", return_value=failure
        ), mock.patch.object(
            self.view_module, "_clear_document_arm_if_idle"
        ) as disarm:
            self.assertFalse(self.view_module.run_restore_active_view())

        disarm.assert_not_called()
        self.assertIn("could not be restored", self.alerts[0])

    def test_fully_restored_document_stops_arming_close_recovery(self):
        view_state = {
            "last_seen_tvp": {},
            "view_sessions": {},
            "armed_documents": {
                "path|c:\\models\\sample.rvt": {
                    "doc_key": "path|c:\\models\\sample.rvt",
                    "doc_runtime_id": 42,
                    "arm_schema_version": 2,
                }
            },
        }
        self.view_module._get_state = lambda: view_state
        self.view_module._save_state = lambda state: None
        self.close_module._get_view_state = lambda: view_state
        self.close_module._save_view_state = lambda state: None

        self.assertTrue(
            self.view_module._clear_document_arm_if_idle(self.close_module, self.doc)
        )
        self.assertEqual({}, view_state["armed_documents"])

    def test_document_with_remaining_sessions_stays_armed(self):
        view_state = {
            "last_seen_tvp": {},
            "view_sessions": {
                "path|c:\\models\\sample.rvt|12": {
                    "doc_key": "path|c:\\models\\sample.rvt",
                    "doc_runtime_id": 42,
                    "view_id": 12,
                    "original_phase_id": 3,
                }
            },
            "armed_documents": {
                "path|c:\\models\\sample.rvt": {
                    "doc_key": "path|c:\\models\\sample.rvt",
                    "doc_runtime_id": 42,
                    "arm_schema_version": 2,
                }
            },
        }
        self.view_module._get_state = lambda: view_state
        self.view_module._save_state = lambda state: None
        self.close_module._get_view_state = lambda: view_state
        self.close_module._save_view_state = lambda state: None

        self.assertFalse(
            self.view_module._clear_document_arm_if_idle(self.close_module, self.doc)
        )
        self.assertIn("path|c:\\models\\sample.rvt", view_state["armed_documents"])

    def test_restore_requires_a_project_document(self):
        self.view_module._get_uiapp = lambda uiapp=None: FakeUiapp(None, None)

        with mock.patch.object(self.close_module, "restore_tvp_summary") as restore:
            self.assertFalse(self.view_module.run_restore_active_view())
            self.assertFalse(self.view_module.run_restore_all_views())

        restore.assert_not_called()
        self.assertTrue(all("project document" in alert for alert in self.alerts))


if __name__ == "__main__":
    unittest.main()
