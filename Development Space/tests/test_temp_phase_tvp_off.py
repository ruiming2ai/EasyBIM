"""Turning Temporary View Properties off restores that view's temporary phase."""

import unittest
from unittest import mock

from test_temp_phase_commit_runtime import FakeDocument, FakeUiapp, FakeView, _load_runtime


DOC_KEY = "path|c:\\models\\sample.rvt"


def _session(view_id):
    return {
        "doc_key": DOC_KEY,
        "doc_runtime_id": 42,
        "view_id": view_id,
        "view_name": "View {0}".format(view_id),
        "original_phase_id": 3,
    }


class TwoViewDocument(FakeDocument):
    def __init__(self):
        FakeDocument.__init__(self)
        self.views = {11: FakeView(11), 12: FakeView(12)}

    def GetElement(self, element_id):
        return self.views.get(int(element_id))


class TempPhaseTvpOffTests(unittest.TestCase):
    def setUp(self):
        self.runtime = _load_runtime()
        self.doc = TwoViewDocument()
        self.uiapp = FakeUiapp(self.doc)
        self.view_state = {
            "view_sessions": {
                DOC_KEY + "|11": _session(11),
                DOC_KEY + "|12": _session(12),
            },
            "last_seen_tvp": {DOC_KEY + "|11": True, DOC_KEY + "|12": True},
            "armed_documents": {"id|42": {"doc_key": DOC_KEY, "doc_runtime_id": 42}},
        }
        self.runtime._MEMORY_STATE.clear()
        self.runtime._get_view_state = lambda: self.view_state
        self.runtime._save_view_state = lambda state: None
        self.runtime._int_to_eid = lambda value: value
        self.runtime._is_tvp_active = lambda view: view.tvp_active
        self.alerts = []
        self.runtime._show_alert = lambda title, message: self.alerts.append(message)
        self.restored = []

    def _restore(self, ok):
        def restore(uiapp, doc, summary):
            items = summary["tracked_restore_views"]
            self.restored.append([item["view_id"] for item in items])
            if not ok:
                return {"ok": False}
            for item in items:
                self.view_state["view_sessions"].pop(item["key"], None)
                self.view_state["last_seen_tvp"].pop(item["key"], None)
            return {"ok": True}

        return restore

    def _tick(self, restore_ok=True):
        self.runtime._LAST_IDLE_AT[0] = 0.0
        self.runtime._LAST_TVP_WATCH_AT[0] = 0.0
        with mock.patch.object(self.runtime, "restore_tvp_summary", side_effect=self._restore(restore_ok)):
            self.runtime.handle_app_idling(self.uiapp)

    def test_turning_tvp_off_restores_only_that_view(self):
        self.doc.views[11].tvp_active = False
        self._tick()

        self.assertEqual(self.restored, [[11]])
        self.assertNotIn(DOC_KEY + "|11", self.view_state["view_sessions"])
        self.assertIn(DOC_KEY + "|12", self.view_state["view_sessions"])
        self.assertTrue(self.view_state["armed_documents"], "view 12 still needs close recovery")
        self.assertEqual(self.alerts, [], "switching TVP off is the user's own intent")

    def test_last_tracked_view_restored_disarms_the_document(self):
        self.doc.views[11].tvp_active = False
        self.doc.views[12].tvp_active = False
        self._tick()

        self.assertEqual(self.restored, [[11], [12]])
        self.assertEqual(self.view_state["view_sessions"], {})
        self.assertEqual(self.view_state["armed_documents"], {})

    def test_views_with_tvp_still_on_are_left_alone(self):
        self._tick()

        self.assertEqual(self.restored, [])
        self.assertEqual(len(self.view_state["view_sessions"]), 2)

    def test_a_view_never_seen_with_tvp_on_is_not_restored(self):
        self.view_state["last_seen_tvp"][DOC_KEY + "|11"] = False
        self.doc.views[11].tvp_active = False
        self._tick()

        self.assertEqual(self.restored, [])

    def test_failed_restore_alerts_once_and_keeps_the_session(self):
        self.doc.views[11].tvp_active = False
        self._tick(restore_ok=False)
        self._tick(restore_ok=False)

        self.assertEqual(self.restored, [[11]], "no retry on every tick")
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("view Plan", self.alerts[0])
        self.assertIn(DOC_KEY + "|11", self.view_state["view_sessions"])

    def test_the_watch_is_throttled(self):
        self.doc.views[11].tvp_active = False
        self.runtime._LAST_IDLE_AT[0] = 0.0
        self.runtime._LAST_TVP_WATCH_AT[0] = 10**12
        with mock.patch.object(self.runtime, "restore_tvp_summary", side_effect=self._restore(True)):
            self.runtime.handle_app_idling(self.uiapp)

        self.assertEqual(self.restored, [])

    def test_a_watch_failure_does_not_break_idling(self):
        with mock.patch.object(
            self.runtime,
            "_restore_views_whose_tvp_was_turned_off",
            side_effect=RuntimeError("boom"),
        ):
            self.runtime._LAST_IDLE_AT[0] = 0.0
            self.runtime.handle_app_idling(self.uiapp)


if __name__ == "__main__":
    unittest.main()
