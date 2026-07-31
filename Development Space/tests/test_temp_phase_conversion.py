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
        expected = {
            "doc-closing.py": "handle_doc_closing",
            "app-idling.py": "handle_app_idling",
            "doc-closed.py": "handle_doc_closed",
        }
        for filename, handler in expected.items():
            text = (ROOT / "hooks" / filename).read_text(encoding="utf-8")
            self.assertIn("from pyrevit import EXEC_PARAMS", text)
            self.assertIn("from easybim import temp_phase_close", text)
            self.assertIn(handler, text)

        for filename in ("doc-closing.cs", "app-idling.cs", "doc-closed.cs"):
            self.assertFalse((ROOT / "hooks" / filename).exists(), filename)

        for hook_path in (ROOT / "hooks").glob("*.py"):
            text = hook_path.read_text(encoding="utf-8")
            self.assertNotIn("TempPhaseController", text, str(hook_path))
            self.assertNotIn("Assembly.Load", text, str(hook_path))

        startup = (ROOT / "startup.py").read_text(encoding="utf-8")
        self.assertIn("temp_phase_save.install", startup)

        app_idling = (ROOT / "hooks" / "app-idling.py").read_text(encoding="utf-8")
        self.assertIn("temp_phase_save.handle_app_idling", app_idling)
        doc_closed = (ROOT / "hooks" / "doc-closed.py").read_text(encoding="utf-8")
        self.assertIn("temp_phase_save.handle_doc_closed", doc_closed)

    def test_command_bundle_has_no_controller_preload(self):
        metadata = (BUTTON_ROOT / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("min_revit_version: 2025", metadata)
        self.assertIn("max_revit_version: 2026", metadata)
        self.assertNotIn("modules:", metadata)
        self.assertNotIn("TempPhaseController.dll", metadata)

    def test_command_button_is_python_based_without_controller_bridge(self):
        self.assertFalse((BUTTON_ROOT / "script.cs").exists())
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

        close_runtime = (ROOT / "lib" / "easybim" / "temp_phase_close.py").read_text(
            encoding="utf-8"
        )
        for handler in ("handle_doc_closing", "handle_app_idling", "handle_doc_closed"):
            self.assertIn("def " + handler, close_runtime)
        self.assertIn("PostableCommand", close_runtime)
        self.assertIn("CanPostCommand", close_runtime)
        self.assertIn("DocClosingHookEntry", close_runtime)
        self.assertIn("TempPhaseRestoreCommitted", close_runtime)
        self.assertIn("TempPhaseCloseReposted", close_runtime)
        self.assertIn("log_hook_context", close_runtime)

        save_runtime = (ROOT / "lib" / "easybim" / "temp_phase_save.py").read_text(
            encoding="utf-8"
        )
        for handler in (
            "handle_document_saving",
            "handle_document_saved",
            "handle_app_idling",
            "install",
        ):
            self.assertIn("def " + handler, save_runtime)
        self.assertIn("DocumentSaving", save_runtime)
        self.assertIn("DocumentSavingAs", save_runtime)
        self.assertIn("PostableCommand.Save", save_runtime)
        self.assertIn("PostableCommand.SaveAs", save_runtime)

        diagnostics = (
            TEMP_PHASE_ROOT / "TempPhase.RevitCommon" / "TempPhaseDiagnostics.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("ValidateControllerForHost", diagnostics)
        self.assertIn("VersionNumber", diagnostics)
        self.assertIn("AssemblyName", diagnostics)
        self.assertIn("ReportCommandException", diagnostics)
        self.assertIn("TaskDialog.Show", diagnostics)

    def test_manual_command_emits_python_picker_and_transaction_diagnostics(self):
        runtime = (ROOT / "lib" / "easybim" / "temp_phase_view.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("PythonPhasePickerOpening", runtime)
        self.assertIn("PythonPhasePickerClosed", runtime)
        self.assertIn("PythonApplyTransactionCommitted", runtime)
        self.assertIn("PythonApplySuccess", runtime)

    def test_python_close_runtime_uses_per_document_state_and_native_close(self):
        runtime = (ROOT / "lib" / "easybim" / "temp_phase_close.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("pending_closes", runtime)
        self.assertIn("repost_guards", runtime)
        self.assertIn("_collect_discoverable_tvp_views", runtime)
        self.assertIn("PostableCommand.Close", runtime)
        self.assertNotIn("ID_REVIT_FILE_CLOSE", runtime)

    def test_version_projects_target_revit_2025_and_2026(self):
        for version in ("2025", "2026"):
            project = (
                TEMP_PHASE_ROOT
                / ("TempPhase.Revit" + version)
                / ("TempPhase.Revit" + version + ".csproj")
            ).read_text(encoding="utf-8")
            self.assertIn("<TargetFramework>net8.0-windows</TargetFramework>", project)
            self.assertIn(
                'Include="Revit_All_Main_Versions_API_x64"',
                project,
            )
            self.assertIn('Version="' + version + '.0.0"', project)

    def test_controller_release_script_is_developer_only(self):
        script = (ROOT / "build" / "Build-TempPhase.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("developer-only", script)
        self.assertIn('ValidateSet("2025", "2026", "All")', script)
        self.assertIn("VerifyOnly", script)
        self.assertIn("TempPhaseController.Revit", script)
        self.assertNotIn("RegisterManualSession", script)
        self.assertNotIn("Stage-PackageFiles", script)


if __name__ == "__main__":
    unittest.main()
