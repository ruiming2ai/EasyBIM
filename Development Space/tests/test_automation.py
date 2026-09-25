"""Preferences and startup behavior tested without a Revit installation."""
import importlib.util
import itertools
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.modules = mock.patch.dict(sys.modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        package = types.ModuleType("easybim")
        package.__path__ = [str(ROOT / "lib" / "easybim")]
        sys.modules["easybim"] = package
        for name in ("automation_settings", "messages", "coordination_review_passive", "automation_window"):
            full_name = "easybim." + name
            sys.modules.pop(full_name, None)
            spec = importlib.util.spec_from_file_location(full_name, str(ROOT / "lib" / "easybim" / (name + ".py")))
            module = importlib.util.module_from_spec(spec)
            sys.modules[full_name] = module
            spec.loader.exec_module(module)
            setattr(package, name, module)
            setattr(self, name, module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / "automation.json"
        patcher = mock.patch.object(self.automation_settings, "settings_path", return_value=str(self.path))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.doc = types.SimpleNamespace(IsValidObject=True, IsFamilyDocument=False,
                                         IsLinked=False, IsWorkshared=True)
        self.uiapp = types.SimpleNamespace(ActiveUIDocument=types.SimpleNamespace(Document=self.doc))

    def save(self, workset, coordination):
        settings = {"workset_enabled": workset, "coordination_review_enabled": coordination}
        self.assertEqual((True, ""), self.automation_settings.save_settings(settings))
        return settings

    def test_missing_and_invalid_settings(self):
        defaults = dict(self.automation_settings.DEFAULTS)
        self.assertEqual((defaults, ""), self.automation_settings.load_settings())
        self.path.write_text('{"workset_enabled": false, "coordination_review_enabled": "false"}')
        settings, error = self.automation_settings.load_settings()
        self.assertEqual({"workset_enabled": False, "coordination_review_enabled": True}, settings)
        self.assertTrue(error)
        self.path.write_text("broken json")
        settings, error = self.automation_settings.load_settings()
        self.assertEqual(defaults, settings)
        self.assertTrue(error)

    def test_all_combinations_persist_and_run_in_order(self):
        for workset, coordination in itertools.product((False, True), repeat=2):
            expected = self.save(workset, coordination)
            self.assertEqual((expected, ""), self.automation_settings.load_settings())
            calls = []
            with mock.patch.object(self.messages, "_show_workset_picker_for_doc", side_effect=lambda doc: calls.append("workset")), mock.patch.object(
                self.messages, "_print_coordination_review_report", side_effect=lambda doc: calls.append("coordination")
            ):
                self.messages.run_start_message_workflow(self.doc)
            self.assertEqual((["workset"] if workset else []) + (["coordination"] if coordination else []), calls)

    def test_failed_replace_preserves_previous_preferences(self):
        original = self.save(False, True)
        with mock.patch.object(self.automation_settings.os, "replace", side_effect=OSError("access denied")):
            ok, error = self.automation_settings.save_settings(dict(self.automation_settings.DEFAULTS))
        self.assertFalse(ok)
        self.assertIn("access denied", error)
        self.assertEqual((original, ""), self.automation_settings.load_settings())
        self.assertEqual([self.path], list(self.path.parent.iterdir()))

    def test_disabled_pending_trigger_is_consumed_without_active_document(self):
        self.save(False, False)
        with mock.patch.object(self.messages, "_load_file_open_trigger_state", return_value={"pending": True}), mock.patch.object(
            self.messages, "_clear_file_open_trigger_pending"
        ) as clear, mock.patch.object(self.messages, "run_start_message_workflow") as run:
            self.messages._process_file_open_trigger_pending()
        clear.assert_called_once_with()
        run.assert_not_called()

    def test_both_disabled_does_not_schedule_work(self):
        self.save(False, False)
        with mock.patch.object(self.messages, "_mark_file_open_trigger_pending") as mark:
            self.messages.run_start_message_on_file_open(self.doc)
        mark.assert_not_called()

    def test_queued_actions_respect_all_current_preferences(self):
        for workset, coordination in itertools.product((False, True), repeat=2):
            self.save(workset, coordination)
            job = dict(stage="show_workset_picker", created_at=100, post_deadline=200,
                       open_worksets_after=True, run_coord_report_after=True, doc_is_workshared=True)
            with mock.patch.object(self.messages, "_resolve_doc_from_job", return_value=self.doc), mock.patch.object(
                self.messages, "_job_has_doc_identity", return_value=False
            ), mock.patch.object(self.messages, "_is_same_doc", return_value=True), mock.patch.object(
                self.messages, "_show_workset_picker_for_doc"
            ) as picker, mock.patch.object(self.messages, "_print_coordination_review_report") as report:
                done = self.messages._process_startup_job(self.uiapp, job, 100)
                if not done:
                    self.assertTrue(self.messages._process_startup_job(self.uiapp, job, 101))
            self.assertEqual(int(workset), picker.call_count)
            self.assertEqual(int(coordination), report.call_count)

    def test_disabling_during_workset_dialog_skips_next_action(self):
        self.save(True, True)
        with mock.patch.object(self.messages, "_show_workset_picker_for_doc", side_effect=lambda doc: self.save(True, False)), mock.patch.object(
            self.messages, "_print_coordination_review_report"
        ) as report:
            self.messages.run_start_message_workflow(self.doc)
        report.assert_not_called()

    def test_listener_registration_is_gated_and_records_cleared(self):
        passive = self.coordination_review_passive
        self.save(True, False)
        with mock.patch.object(passive, "unregister_passive_detector") as unregister, mock.patch.object(
            passive, "clear_all_records"
        ) as clear, mock.patch.object(passive, "_make_failures_processing_handler") as handler:
            for source in ("startup", "doc-opening", "doc-opened"):
                self.assertFalse(passive.register_passive_detector(self.uiapp, source=source))
            self.assertEqual(3, unregister.call_count)
            self.assertEqual(3, clear.call_count)
            handler.assert_not_called()

    def test_apply_listener_settings_enables_and_disables(self):
        passive = self.coordination_review_passive
        with mock.patch.object(passive, "register_passive_detector") as register, mock.patch.object(
            passive, "unregister_passive_detector"
        ) as unregister, mock.patch.object(passive, "clear_all_records") as clear:
            self.automation_settings.apply_listener_settings(self.save(True, True), self.uiapp)
            register.assert_called_once_with(self.uiapp, source="automation-settings")
            self.automation_settings.apply_listener_settings(self.save(True, False), self.uiapp)
            unregister.assert_called_once_with(self.uiapp, source="automation-settings")
            clear.assert_called_once_with()

    def test_unregister_detaches_stale_local_and_current_shared_handler(self):
        passive = self.coordination_review_passive
        class Event:
            def __init__(self):
                self.handlers = []
            def __isub__(self, handler):
                self.handlers.remove(handler)
                return self
        app = types.SimpleNamespace(FailuresProcessing=Event())
        local, shared = object(), object()
        app.FailuresProcessing.handlers = [local, shared]
        passive._HANDLER_REF = local
        with mock.patch.object(passive, "_get_envvar", return_value=shared), mock.patch.object(passive, "_update_diagnostics"):
            passive.unregister_passive_detector(app)
        self.assertEqual([], app.FailuresProcessing.handlers)

    def test_window_open_cancel_and_failed_save_have_no_side_effects(self):
        windows = []
        class Event:
            def __iadd__(self, callback):
                return self
        class Window:
            def __init__(self, path):
                self.workset_cb = types.SimpleNamespace(IsChecked=None)
                self.coordination_cb = types.SimpleNamespace(IsChecked=None)
                self.status_tb = types.SimpleNamespace(Text="")
                self.save_btn = types.SimpleNamespace(Click=Event())
                self.closed = False
                windows.append(self)
            def ShowDialog(self):
                pass
            def Close(self):
                self.closed = True
        pyrevit = types.ModuleType("pyrevit")
        pyrevit.forms = types.SimpleNamespace(WPFWindow=Window)
        pyrevit.script = mock.Mock()
        with mock.patch.dict(sys.modules, {"pyrevit": pyrevit}), mock.patch.object(
            self.automation_settings, "apply_listener_settings"
        ) as apply:
            self.automation_window.show_automation_window()
            window = windows[-1]
            self.assertTrue(window.workset_cb.IsChecked)
            self.assertFalse(self.path.exists())
            apply.assert_not_called()
            window.workset_cb.IsChecked = False
            with mock.patch.object(self.automation_settings, "save_settings", return_value=(False, "write failed")):
                window._save(None, None)
            self.assertFalse(window.closed)
            self.assertEqual("write failed", window.status_tb.Text)
            apply.assert_not_called()
            window._save(None, None)
            self.assertTrue(window.closed)
            self.assertFalse(self.automation_settings.load_settings()[0]["workset_enabled"])
            apply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
