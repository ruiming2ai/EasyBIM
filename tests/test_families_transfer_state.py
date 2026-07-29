import ast
import importlib.util
import pathlib
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Families Transfer.pushbutton"
)
STATE_MODULE_PATH = COMMAND_DIR / "families_transfer_state.py"
REVIT_MODULE_PATH = COMMAND_DIR / "families_transfer_revit.py"


def _load_state_module():
    spec = importlib.util.spec_from_file_location(
        "families_transfer_state",
        str(STATE_MODULE_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FamiliesTransferStateTests(unittest.TestCase):
    def test_restore_selection_marks_selected_families_and_documents(self):
        module = _load_state_module()

        families = [
            module.FamilyOption("Door Single", "fam-1"),
            module.FamilyOption("Casework Base", "fam-2"),
        ]
        documents = [
            module.TargetDocumentOption("Tower.rvt", "doc-1"),
            module.TargetDocumentOption("Podium.rvt", "doc-2"),
        ]

        module.restore_family_selection(families, {"fam-2"})
        module.restore_document_selection(documents, {"doc-1"})

        self.assertFalse(families[0].is_selected)
        self.assertTrue(families[1].is_selected)
        self.assertTrue(documents[0].is_selected)
        self.assertFalse(documents[1].is_selected)

    def test_sort_helpers_order_names_case_insensitively(self):
        module = _load_state_module()

        families = [
            module.FamilyOption("z Lighting", "fam-z"),
            module.FamilyOption("A Door", "fam-a"),
            module.FamilyOption("casework", "fam-c"),
        ]
        documents = [
            module.TargetDocumentOption("z-model.rvt", "doc-z"),
            module.TargetDocumentOption("A-model.rvt", "doc-a"),
        ]

        self.assertEqual(
            [item.name for item in module.sort_family_options(families)],
            ["A Door", "casework", "z Lighting"],
        )
        self.assertEqual(
            [item.display_name for item in module.sort_target_documents(documents)],
            ["A-model.rvt", "z-model.rvt"],
        )

    def test_sanitize_export_filename_removes_invalid_characters(self):
        module = _load_state_module()

        self.assertEqual(
            module.sanitize_export_filename(' Door:/A*B?"<>| '),
            "Door_A_B.rfa",
        )
        self.assertEqual(module.sanitize_export_filename(""), "Family.rfa")
        self.assertEqual(module.sanitize_export_filename("Chair.rfa"), "Chair.rfa")

    def test_build_operation_summary_reports_loaded_skipped_and_failed(self):
        module = _load_state_module()

        summary = module.TransferSummary(
            loaded=[
                module.TransferResult("Door", "Tower.rvt", "loaded"),
                module.TransferResult("Desk", "Tower.rvt", "overwritten"),
            ],
            skipped=[module.TransferResult("System Wall", "Source.rvt", "not editable")],
            failed=[module.TransferResult("Light", "Podium.rvt", "Load failed")],
        )

        message = module.build_transfer_summary_text(summary)

        self.assertIn("Loaded/overwritten: 2", message)
        self.assertIn("Skipped: 1", message)
        self.assertIn("Failed: 1", message)
        self.assertIn("- Door -> Tower.rvt: loaded", message)
        self.assertIn("- System Wall -> Source.rvt: not editable", message)
        self.assertIn("- Light -> Podium.rvt: Load failed", message)

    def test_selected_family_keys_deduplicate_ids(self):
        module = _load_state_module()

        families = [
            module.FamilyOption("Door", "100", is_selected=True),
            module.FamilyOption("Desk", "101", is_selected=False),
            module.FamilyOption("Chair", "100", is_selected=True),
        ]

        self.assertEqual(module.get_selected_family_keys(families), ["100"])

    def test_revit_helper_does_not_use_temporary_transfer_files(self):
        self.assertTrue(REVIT_MODULE_PATH.exists(), "families_transfer_revit.py is missing")
        source = REVIT_MODULE_PATH.read_text()
        tree = ast.parse(source, filename=str(REVIT_MODULE_PATH))

        forbidden_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("tempfile", "shutil"):
                        forbidden_imports.append(alias.name)
            if isinstance(node, ast.ImportFrom) and node.module in ("tempfile", "shutil"):
                forbidden_imports.append(node.module)

        self.assertEqual([], forbidden_imports)


if __name__ == "__main__":
    unittest.main()
