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
    def test_pyrevit_hooks_use_typed_event_signatures(self):
        expected = {
            "doc-closing.cs": "DocumentClosingEventArgs",
            "app-idling.cs": "IdlingEventArgs",
            "doc-closed.cs": "DocumentClosedEventArgs",
        }
        for filename, event_type in expected.items():
            text = (ROOT / "hooks" / filename).read_text(encoding="utf-8")
            self.assertIn(event_type, text)
            self.assertIn("FindControllerAssembly", text)
            self.assertIn("Assembly.Load", text)
            self.assertIn("GetProperty(\"Shared\"", text)
            self.assertNotIn("using EasyBIM.TempPhase", text)

    def test_command_bundle_uses_packaged_versioned_controllers(self):
        metadata = (BUTTON_ROOT / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("min_revit_version: 2025", metadata)
        self.assertIn("max_revit_version: 2026", metadata)
        self.assertNotIn("modules:", metadata)
        self.assertNotIn("TempPhaseController.dll", metadata)

    def test_command_wrapper_reports_module_load_failures(self):
        script = (BUTTON_ROOT / "script.cs").read_text(encoding="utf-8")
        self.assertIn("ExecParams", script)
        self.assertIn("ScriptPath", script)
        self.assertIn("Assembly.LoadFrom", script)
        self.assertIn("ModuleCandidatePath", script)
        self.assertIn("FindControllerAssembly", script)
        self.assertIn("TempPhaseController.Revit", script)
        self.assertIn("ControllerModuleMissing", script)
        self.assertIn("ControllerModuleWrongYearLoaded", script)
        self.assertIn("TaskDialog.Show", script)
        self.assertIn("TargetInvocationException", script)

        diagnostics = (
            TEMP_PHASE_ROOT / "TempPhase.RevitCommon" / "TempPhaseDiagnostics.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("ValidateControllerForHost", diagnostics)
        self.assertIn("VersionNumber", diagnostics)
        self.assertIn("AssemblyName", diagnostics)
        self.assertIn("ReportCommandException", diagnostics)
        self.assertIn("TaskDialog.Show", diagnostics)

    def test_manual_command_emits_startup_and_picker_diagnostics(self):
        controller = (
            TEMP_PHASE_ROOT
            / "TempPhase.RevitCommon"
            / "TempPhaseController.cs"
        ).read_text(encoding="utf-8")
        service = (
            TEMP_PHASE_ROOT
            / "TempPhase.RevitCommon"
            / "TempPhaseService.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("LogCommandStart", controller)
        self.assertIn("ManualCommandDispatch", controller)
        self.assertIn("ManualPhasePickerOpening", service)
        self.assertIn("ManualPhasePickerClosed", service)
        self.assertIn("ManualApplyTransactionCommitted", service)

    def test_controller_uses_typed_close_command_and_per_document_state(self):
        service = (
            TEMP_PHASE_ROOT
            / "TempPhase.RevitCommon"
            / "TempPhaseService.cs"
        ).read_text(encoding="utf-8")
        controller = (
            TEMP_PHASE_ROOT
            / "TempPhase.RevitCommon"
            / "TempPhaseController.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("Dictionary<string, PendingCloseRequest>", service)
        self.assertIn("PostableCommand.Close", service)
        self.assertNotIn("ID_REVIT_FILE_CLOSE", service)
        self.assertIn("public static TempPhaseController Shared", controller)

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

    def test_packaged_controller_release_script(self):
        script = (ROOT / "build" / "Build-TempPhase.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('ValidateSet("2025", "2026", "All")', script)
        self.assertIn("VerifyOnly", script)
        self.assertIn('TempPhaseController.Revit{0}.dll', script)
        self.assertIn("Update-TrackedController", script)
        self.assertIn("GetAssemblyName", script)
        self.assertIn("RevitAPI", script)
        self.assertNotIn("Stage-PackageFiles", script)
        self.assertNotIn("ExtensionRoot", script)

        for version in ("2025", "2026"):
            packaged = ROOT / "bin" / ("TempPhaseController.Revit" + version + ".dll")
            self.assertTrue(packaged.exists(), str(packaged))


if __name__ == "__main__":
    unittest.main()
