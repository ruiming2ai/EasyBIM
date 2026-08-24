import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMP_PHASE_ROOT = ROOT / "src" / "TempPhase"
BUTTON_ROOT = (
    ROOT
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Temp Phase.pushbutton"
)


class TempPhaseConversionTests(unittest.TestCase):
    def test_pyrevit_hooks_are_single_python_dispatchers(self):
        # app-idling is deliberately absent: Revit raises Idling continuously
        # and pyRevit recompiles a hook script on every dispatch, so close
        # recovery runs from the one-time .NET delegate in easybim.idling.
        expected = {
            "doc-closing.py": "handle_doc_closing",
            "doc-closed.py": "handle_doc_closed",
        }
        for filename, handler in expected.items():
            text = (ROOT / "hooks" / filename).read_text(encoding="utf-8")
            self.assertIn("from pyrevit import EXEC_PARAMS", text)
            self.assertIn("from easybim import temp_phase_close", text)
            self.assertIn(handler, text)

        startup_text = (ROOT / "startup.py").read_text(encoding="utf-8")
        self.assertIn("from easybim import idling", startup_text)
        # The application is passed explicitly: a bare install() finds no event
        # source during application init.  See test_idling_dispatcher.
        self.assertIn("idling.install(", startup_text)

        idling_runtime = (ROOT / "lib" / "easybim" / "idling.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("handle_app_idling", idling_runtime)

        for filename in ("doc-closing.cs", "app-idling.cs", "doc-closed.cs"):
            self.assertFalse((ROOT / "hooks" / filename).exists(), filename)

        for hook_path in (ROOT / "hooks").glob("*.py"):
            text = hook_path.read_text(encoding="utf-8")
            self.assertNotIn("TempPhaseController", text, str(hook_path))
            self.assertNotIn("Assembly.Load", text, str(hook_path))

        startup = (ROOT / "startup.py").read_text(encoding="utf-8")
        self.assertNotIn("temp_phase_save", startup)
        self.assertIn("install_completion_handlers", startup)

        # The app-idling hook is gone: pyRevit re-reads and recompiles a hook
        # script on every Idling event, so all of its work moved to the
        # one-time .NET delegate in easybim.idling.
        self.assertFalse((ROOT / "hooks" / "app-idling.py").exists())

        idling_runtime = (ROOT / "lib" / "easybim" / "idling.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("temp_phase_save", idling_runtime)
        for consumer in (
            "has_pending_startup_jobs",
            "process_startup_jobs",
            "has_pending_startup_auto_update",
            "run_pending_startup_auto_update",
            "handle_app_idling",
        ):
            self.assertIn(consumer, idling_runtime)

        doc_closed = (ROOT / "hooks" / "doc-closed.py").read_text(encoding="utf-8")
        self.assertNotIn("temp_phase_save", doc_closed)

    def test_command_bundle_has_no_controller_preload(self):
        metadata = (BUTTON_ROOT / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("min_revit_version: 2015", metadata)
        self.assertIn("max_revit_version: 2027", metadata)
        self.assertNotIn("modules:", metadata)
        self.assertNotIn("TempPhaseController.dll", metadata)

    def test_single_button_window_offers_both_restore_actions(self):
        """One ribbon button opens one window carrying every option."""
        panel_root = BUTTON_ROOT.parent
        self.assertEqual([], sorted(panel_root.glob("Temp Phase*.pulldown")))
        self.assertEqual([], sorted(panel_root.glob("Restore*.pushbutton")))

        runtime = (ROOT / "lib" / "easybim" / "temp_phase_view.py").read_text(
            encoding="utf-8"
        )
        for entry_point in ("run_restore_active_view", "run_restore_all_views"):
            self.assertIn("def " + entry_point, runtime)
        self.assertIn("ACTION_RESTORE_VIEW", runtime)
        self.assertIn("ACTION_RESTORE_ALL", runtime)

        picker_xaml = (ROOT / "lib" / "easybim" / "ui" / "temp_phase_picker.xaml").read_text(
            encoding="utf-8"
        )
        self.assertIn('x:Name="restore_btn"', picker_xaml)
        self.assertIn('x:Name="restore_all_btn"', picker_xaml)
        self.assertIn('Content="Restore"', picker_xaml)
        self.assertIn('Content="Restore All Views"', picker_xaml)

    def test_command_button_is_python_based_without_controller_bridge(self):
        self.assertFalse((BUTTON_ROOT / "script.cs").exists())
        self.assertFalse((BUTTON_ROOT / "bin" / "TempPhaseController.dll").exists())
        self.assertTrue((BUTTON_ROOT / "icon.png").exists())
        self.assertTrue((BUTTON_ROOT / "icon.dark.png").exists())

        script = (BUTTON_ROOT / "script.py").read_text(encoding="utf-8")
        self.assertIn("from easybim import temp_phase_view", script)
        self.assertIn("log_command_context", script)
        self.assertIn("run_pushbutton", script)

        runtime = (ROOT / "lib" / "easybim" / "temp_phase_view.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("TempPhaseController", runtime)
        self.assertNotIn("Assembly.Load", runtime)
        self.assertIn("sessionRecorded", runtime)
        self.assertIn("PythonCommandContext", runtime)
        self.assertIn('state.setdefault("armed_documents", {})', runtime)
        self.assertIn('_arm_document(state, doc, armed_by="successful_apply")', runtime)
        self.assertIn("ARM_SCHEMA_VERSION", runtime)
        self.assertIn("arm_schema_version", runtime)
        self.assertIn("revit_process_id", runtime)

        close_runtime = (ROOT / "lib" / "easybim" / "temp_phase_close.py").read_text(
            encoding="utf-8"
        )
        for handler in ("handle_doc_closing", "handle_app_idling", "handle_doc_closed"):
            self.assertIn("def " + handler, close_runtime)
        self.assertIn("PostableCommand", close_runtime)
        self.assertIn("CanPostCommand", close_runtime)
        self.assertIn("DocClosingHookEntry", close_runtime)
        self.assertIn("_is_document_armed", close_runtime)
        self.assertIn("DocClosingSkippedUnarmedDocument", close_runtime)
        self.assertIn("DocClosingArmedDocument", close_runtime)
        self.assertIn("PythonTempPhaseDocumentTriggerCleared", close_runtime)
        self.assertIn("closing_identities", close_runtime)
        self.assertIn("DocClosingIdentityRecorded", close_runtime)
        self.assertIn("DocClosedIdentityResolved", close_runtime)
        self.assertIn("TempPhaseArmStaleRemoved", close_runtime)
        self.assertIn("TempPhaseRestoreCommitted", close_runtime)
        self.assertIn("TempPhaseCloseReposted", close_runtime)
        self.assertIn("DocumentSaved", close_runtime)
        self.assertIn("DocumentSavedAs", close_runtime)
        self.assertIn("DocumentSynchronizedWithCentral", close_runtime)
        self.assertIn("SynchronizeAndModifySettings", close_runtime)
        self.assertIn("Save and Close", close_runtime)
        self.assertIn("Synchronize and Close", close_runtime)
        self.assertIn("Keep File Open", close_runtime)
        self.assertIn("log_hook_context", close_runtime)

        self.assertFalse(
            (ROOT / "lib" / "easybim" / "temp_phase_save.py").exists()
        )

    def test_manual_command_emits_python_picker_and_transaction_diagnostics(self):
        runtime = (ROOT / "lib" / "easybim" / "temp_phase_view.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("PythonPhasePickerOpening", runtime)
        self.assertIn("PythonPhasePickerClosed", runtime)
        self.assertIn("PythonApplyTransactionCommitted", runtime)
        self.assertIn("PythonApplySuccess", runtime)

    def test_readme_documents_per_document_close_trigger(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Close-Stop is armed per document", readme)
        self.assertIn("files where the button has not been used close normally", readme)
        self.assertIn("does not arm close recovery for other open files", readme)

    def test_manual_picker_is_wpf_first_with_winforms_fallback(self):
        runtime = (ROOT / "lib" / "easybim" / "temp_phase_view.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_show_phase_picker_wpf", runtime)
        self.assertIn("_show_phase_picker_winforms", runtime)
        self.assertIn("_WPF_UNAVAILABLE", runtime)
        self.assertIn("PresentationFramework", runtime)
        self.assertIn("TextWrapping", runtime)
        self.assertIn('ok_button.Content = "Apply"', runtime)
        self.assertIn('cancel_button.Content = "Cancel"', runtime)

    def test_close_warning_uses_compact_rich_dialog_with_native_fallback(self):
        dialog = (ROOT / "lib" / "easybim" / "temp_phase_dialog.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("Please Sync or Save the model again to remove all the Temporary Phases", dialog)
        self.assertIn("and Views Settings before closing!!", dialog)
        self.assertIn("build_warning_runs", dialog)
        self.assertIn("WPFWindow", dialog)
        self.assertIn("temp_phase_warning.xaml", dialog)
        self.assertIn("warning_icon_image", dialog)
        self.assertIn("warning_icon_fallback", dialog)
        self.assertIn("SystemIcons.Warning", dialog)
        self.assertIn("\\u26a0", dialog)
        self.assertIn("IsDefault", dialog)
        self.assertIn("IsCancel", dialog)

        close_runtime = (ROOT / "lib" / "easybim" / "temp_phase_close.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("show_close_decision", close_runtime)
        self.assertIn("TempPhaseDialogWpfUnavailable", close_runtime)
        self.assertIn("TaskDialogIcon", close_runtime)
        self.assertIn("MainInstruction = compact_message", close_runtime)
        self.assertIn("MainContent = restored_message", close_runtime)

        picker_xaml = (ROOT / "lib" / "easybim" / "ui" / "temp_phase_picker.xaml").read_text(
            encoding="utf-8"
        )
        self.assertIn('x:Name="phase_cb"', picker_xaml)
        self.assertIn('x:Name="apply_btn"', picker_xaml)
        self.assertIn('x:Name="cancel_btn"', picker_xaml)

        warning_xaml = (ROOT / "lib" / "easybim" / "ui" / "temp_phase_warning.xaml").read_text(
            encoding="utf-8"
        )
        self.assertIn('x:Name="warning_icon_image"', warning_xaml)
        self.assertIn('x:Name="warning_icon_fallback"', warning_xaml)
        self.assertIn('x:Name="warning_message"', warning_xaml)

    def test_python_close_runtime_uses_per_document_state_and_native_close(self):
        runtime = (ROOT / "lib" / "easybim" / "temp_phase_close.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("pending_closes", runtime)
        self.assertIn("repost_guards", runtime)
        self.assertIn("_collect_discoverable_tvp_views", runtime)
        self.assertIn("PostableCommand.Close", runtime)
        self.assertNotIn("ID_REVIT_FILE_CLOSE", runtime)

    def test_csharp_fallback_is_fully_removed(self):
        """The Python runtime stands alone; no C# fallback ships with EasyBIM."""
        self.assertFalse(TEMP_PHASE_ROOT.exists())
        self.assertFalse((ROOT / "src").exists())
        self.assertFalse((ROOT / "build" / "Build-TempPhase.ps1").exists())
        self.assertFalse((ROOT / "bin" / "TempPhaseController.Revit2025.dll").exists())
        self.assertFalse((ROOT / "bin" / "TempPhaseController.Revit2026.dll").exists())
        self.assertFalse((ROOT / "hooks" / "TempPhase").exists())
        self.assertFalse((ROOT / "lib" / "TempPhase").exists())


if __name__ == "__main__":
    unittest.main()
