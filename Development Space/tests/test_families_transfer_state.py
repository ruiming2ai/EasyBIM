import ast
import importlib.util
import pathlib
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Families Transfer.pushbutton"
)
STATE_MODULE_PATH = COMMAND_DIR / "families_transfer_state.py"
REVIT_MODULE_PATH = COMMAND_DIR / "families_transfer_revit.py"
SCRIPT_MODULE_PATH = COMMAND_DIR / "script.py"
UI_MODULE_PATH = COMMAND_DIR / "families_transfer_ui.py"
SOURCE_XAML_PATH = COMMAND_DIR / "SourceSelectionWindow.xaml"
FAMILY_XAML_PATH = COMMAND_DIR / "FamilySelectionWindow.xaml"
TARGET_XAML_PATH = COMMAND_DIR / "TargetSelectionWindow.xaml"


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

    def test_open_family_document_options_default_unchecked(self):
        module = _load_state_module()

        option = module.OpenFamilyDocumentOption("Chair.rfa", "path|chair.rfa")

        self.assertEqual(option.display_name, "Chair.rfa")
        self.assertEqual(option.document_key, "path|chair.rfa")
        self.assertEqual(option.category_name, "Unknown Category")
        self.assertFalse(option.is_selected)
        self.assertEqual(str(option), "Chair.rfa")

    def test_source_keys_distinguish_project_families_from_open_rfa_files(self):
        module = _load_state_module()

        project_key = module.make_project_family_key("42")
        open_rfa_key = module.make_open_family_document_key("42")

        self.assertEqual(project_key, "project|42")
        self.assertEqual(open_rfa_key, "open-rfa|42")
        self.assertNotEqual(project_key, open_rfa_key)

    def test_merge_transferable_families_adds_only_checked_open_rfa_sources(self):
        module = _load_state_module()

        project_families = [
            module.FamilyOption(
                "Wall Cabinet",
                module.make_project_family_key("200"),
                is_selected=True,
                category_name="Casework",
            ),
        ]
        open_rfas = [
            module.OpenFamilyDocumentOption(
                "Desk.rfa",
                "doc-desk",
                is_selected=True,
                category_name="Furniture",
            ),
            module.OpenFamilyDocumentOption(
                "Light.rfa",
                "doc-light",
                is_selected=False,
                category_name="Lighting Fixtures",
            ),
        ]

        merged = module.merge_transferable_family_options(project_families, open_rfas)

        self.assertEqual([item.name for item in merged], ["Wall Cabinet", "Desk.rfa"])
        self.assertEqual(
            [item.family_key for item in merged],
            [module.make_project_family_key("200"), module.make_open_family_document_key("doc-desk")],
        )
        self.assertEqual([item.source_kind for item in merged], ["project", "open_rfa"])
        self.assertEqual([item.category_name for item in merged], ["Casework", "Furniture"])
        self.assertTrue(merged[0].is_selected)
        self.assertTrue(merged[1].is_selected)

    def test_selected_source_family_options_returns_checked_project_families(self):
        module = _load_state_module()

        selected_key = module.make_project_family_key("100")
        other_key = module.make_project_family_key("200")
        families = [
            module.FamilyOption("Door Tag", selected_key, category_name="Door Tags"),
            module.FamilyOption("Base Cabinet", other_key, category_name="Casework"),
        ]

        selected = module.get_selected_source_family_options(families, {selected_key})

        self.assertEqual([item.name for item in selected], ["Door Tag"])
        self.assertEqual([item.category_name for item in selected], ["Door Tags"])
        self.assertTrue(selected[0].is_selected)

    def test_merge_source_family_options_deduplicates_picked_rows(self):
        module = _load_state_module()

        door_key = module.make_project_family_key("100")
        casework_key = module.make_project_family_key("200")
        existing = [
            module.FamilyOption("Door Tag", door_key, is_selected=True, category_name="Door Tags"),
        ]
        additions = [
            module.FamilyOption("Door Tag Duplicate", door_key, is_selected=True, category_name="Door Tags"),
            module.FamilyOption("Base Cabinet", casework_key, is_selected=True, category_name="Casework"),
        ]

        merged = module.merge_source_family_options(existing, additions)

        self.assertEqual([item.family_key for item in merged], [casework_key, door_key])
        self.assertEqual([item.name for item in merged], ["Base Cabinet", "Door Tag"])
        self.assertTrue(all(item.is_selected for item in merged))

    def test_group_family_options_sorts_by_category_then_family_name(self):
        module = _load_state_module()

        families = [
            module.FamilyOption("Window Tag", "project|3", category_name="Window Tags"),
            module.FamilyOption("Base Cabinet", "project|2", category_name="Casework"),
            module.FamilyOption("Upper Cabinet", "project|1", category_name="Casework"),
            module.FamilyOption("No Category", "project|4", category_name=""),
        ]

        groups = module.group_family_options_by_category(families)

        self.assertEqual(
            [(group.category_name, [family.name for family in group.families]) for group in groups],
            [
                ("Casework", ["Base Cabinet", "Upper Cabinet"]),
                ("Window Tags", ["Window Tag"]),
                ("Unknown Category", ["No Category"]),
            ],
        )

    def test_family_search_matches_name_or_category_without_changing_selection(self):
        module = _load_state_module()

        families = [
            module.FamilyOption("Room Name", "project|1", is_selected=True, category_name="Generic Annotations"),
            module.FamilyOption("Single Door", "project|2", is_selected=False, category_name="Doors"),
        ]

        category_match = module.filter_family_options(families, "annotation")
        name_match = module.filter_family_options(families, "door")
        all_items = module.filter_family_options(families, "")

        self.assertEqual([item.name for item in category_match], ["Room Name"])
        self.assertEqual([item.name for item in name_match], ["Single Door"])
        self.assertEqual([item.name for item in all_items], ["Room Name", "Single Door"])
        self.assertTrue(families[0].is_selected)
        self.assertFalse(families[1].is_selected)

    def test_open_rfa_search_matches_name_or_category_without_changing_selection(self):
        module = _load_state_module()

        documents = [
            module.OpenFamilyDocumentOption(
                "North Arrow.rfa",
                "doc-north",
                is_selected=True,
                category_name="Generic Annotations",
            ),
            module.OpenFamilyDocumentOption(
                "Work Table.rfa",
                "doc-table",
                is_selected=False,
                category_name="Furniture",
            ),
        ]

        filtered = module.filter_open_family_document_options(documents, "annotation")

        self.assertEqual([item.display_name for item in filtered], ["North Arrow.rfa"])
        self.assertTrue(documents[0].is_selected)
        self.assertFalse(documents[1].is_selected)

    def test_selected_open_family_document_keys_deduplicate_ids(self):
        module = _load_state_module()

        documents = [
            module.OpenFamilyDocumentOption("Desk.rfa", "doc-desk", is_selected=True),
            module.OpenFamilyDocumentOption("Desk Copy.rfa", "doc-desk", is_selected=True),
            module.OpenFamilyDocumentOption("Light.rfa", "doc-light", is_selected=False),
        ]

        self.assertEqual(module.get_selected_open_family_document_keys(documents), ["doc-desk"])

    def test_build_operation_summary_reports_closed_rfa_files(self):
        module = _load_state_module()

        summary = module.TransferSummary(
            loaded=[module.TransferResult("Desk.rfa", "Tower.rvt", "loaded")],
            closed_rfa_count=3,
        )

        message = module.build_transfer_summary_text(summary)

        self.assertIn("Loaded/overwritten: 1", message)
        self.assertIn("Closed .rfa files: 3", message)

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

    def test_source_step_does_not_collect_all_project_families_before_first_window(self):
        self.assertTrue(SCRIPT_MODULE_PATH.exists(), "script.py is missing")
        tree = ast.parse(SCRIPT_MODULE_PATH.read_text(), filename=str(SCRIPT_MODULE_PATH))

        run_function = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_run":
                run_function = node
                break
        self.assertIsNotNone(run_function, "_run is missing")

        source_step_body = None
        for node in ast.walk(run_function):
            if not isinstance(node, ast.If):
                continue
            comparison = ast.dump(node.test)
            if "step" in comparison and "STEP_SOURCE" in comparison:
                source_step_body = node.body
                break
        self.assertIsNotNone(source_step_body, "STEP_SOURCE branch is missing")

        calls_before_show_dialog = []
        for node in source_step_body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "ShowDialog"
            ):
                break
            calls_before_show_dialog.extend(
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            )

        self.assertNotIn("get_source_family_options", calls_before_show_dialog)

    def test_source_window_xaml_exposes_pick_and_load_more_buttons(self):
        self.assertTrue(SOURCE_XAML_PATH.exists(), "SourceSelectionWindow.xaml is missing")
        source = SOURCE_XAML_PATH.read_text()

        self.assertIn("Click to Select More in Project", source)
        self.assertIn("Load More from Project", source)
        self.assertIn('Click="load_more_click"', source)

    def test_target_window_title_loads_families_into_selected_open_files(self):
        self.assertTrue(TARGET_XAML_PATH.exists(), "TargetSelectionWindow.xaml is missing")
        source = TARGET_XAML_PATH.read_text()

        self.assertIn('Title="Load Families into Selected Open Files"', source)
        self.assertIn('Text="Load Families into Selected Open Files"', source)
        self.assertNotIn("Select Open Files", source)

    def test_family_selection_xaml_exposes_expand_and_collapse_all(self):
        self.assertTrue(FAMILY_XAML_PATH.exists(), "FamilySelectionWindow.xaml is missing")
        source = FAMILY_XAML_PATH.read_text()

        self.assertIn("Expand All", source)
        self.assertIn("Collapse All", source)
        self.assertIn('Click="expand_all_click"', source)
        self.assertIn('Click="collapse_all_click"', source)

    def test_family_selection_xaml_uses_add_instead_of_next(self):
        self.assertTrue(FAMILY_XAML_PATH.exists(), "FamilySelectionWindow.xaml is missing")
        source = FAMILY_XAML_PATH.read_text()

        self.assertIn('Content="Add"', source)
        self.assertIn('Click="add_click"', source)
        self.assertNotIn('Content="Next"', source)
        self.assertNotIn('Click="next_click"', source)

    def test_source_window_supports_family_checkboxes_and_load_more_result(self):
        self.assertTrue(UI_MODULE_PATH.exists(), "families_transfer_ui.py is missing")
        source = UI_MODULE_PATH.read_text()
        tree = ast.parse(source, filename=str(UI_MODULE_PATH))

        self.assertIn("_selected_family_controls", source)
        self.assertIn("selected_family_keys", source)
        self.assertIn('"load_more"', source)

        populate_method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_populate_selected_families":
                populate_method = node
                break

        self.assertIsNotNone(populate_method, "_populate_selected_families is missing")
        populate_source = ast.dump(populate_method)
        self.assertIn("CheckBox", populate_source)

    def test_source_next_goes_to_targets_without_full_family_selector(self):
        self.assertTrue(SCRIPT_MODULE_PATH.exists(), "script.py is missing")
        tree = ast.parse(SCRIPT_MODULE_PATH.read_text(), filename=str(SCRIPT_MODULE_PATH))

        next_branch = None
        load_more_branch = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test_source = ast.dump(node.test)
            if "source_window" in test_source and "result" in test_source and "next" in test_source:
                next_branch = node.body
            if "source_window" in test_source and "result" in test_source and "load_more" in test_source:
                load_more_branch = node.body

        self.assertIsNotNone(next_branch, "source next branch is missing")
        self.assertIsNotNone(load_more_branch, "source load_more branch is missing")

        next_assignments = ast.dump(ast.Module(body=next_branch, type_ignores=[]))
        load_more_assignments = ast.dump(ast.Module(body=load_more_branch, type_ignores=[]))

        self.assertIn("STEP_TARGETS", next_assignments)
        self.assertNotIn("STEP_FAMILIES", next_assignments)
        self.assertIn("STEP_FAMILIES", load_more_assignments)

    def test_family_selection_window_exposes_add_result(self):
        self.assertTrue(UI_MODULE_PATH.exists(), "families_transfer_ui.py is missing")
        source = UI_MODULE_PATH.read_text()
        tree = ast.parse(source, filename=str(UI_MODULE_PATH))

        add_method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "add_click":
                add_method = node
                break

        self.assertIsNotNone(add_method, "add_click is missing")
        add_source = ast.dump(add_method)
        self.assertIn("selected_family_keys", add_source)
        self.assertIn("add", add_source)

    def test_family_add_branch_returns_to_source_without_target_navigation(self):
        self.assertTrue(SCRIPT_MODULE_PATH.exists(), "script.py is missing")
        tree = ast.parse(SCRIPT_MODULE_PATH.read_text(), filename=str(SCRIPT_MODULE_PATH))

        add_branch = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test_source = ast.dump(node.test)
            if "family_window" in test_source and "result" in test_source and "add" in test_source:
                add_branch = node.body
                break

        self.assertIsNotNone(add_branch, "family add branch is missing")
        add_assignments = ast.dump(ast.Module(body=add_branch, type_ignores=[]))
        self.assertIn("STEP_SOURCE", add_assignments)
        self.assertNotIn("STEP_TARGETS", add_assignments)
        self.assertIn("merge_source_family_options", add_assignments)
        self.assertIn("get_selected_source_family_options", add_assignments)

    def test_family_selection_window_uses_expanders_and_expand_collapse_handlers(self):
        self.assertTrue(UI_MODULE_PATH.exists(), "families_transfer_ui.py is missing")
        source = UI_MODULE_PATH.read_text()

        self.assertIn("Windows.Controls.Expander", source)
        self.assertIn("def expand_all_click", source)
        self.assertIn("def collapse_all_click", source)

    def test_wpf_search_handlers_guard_until_window_is_ready(self):
        self.assertTrue(UI_MODULE_PATH.exists(), "families_transfer_ui.py is missing")
        tree = ast.parse(UI_MODULE_PATH.read_text(), filename=str(UI_MODULE_PATH))

        guarded_handlers = {
            "selected_source_search_changed",
            "open_family_search_changed",
            "family_search_changed",
        }
        found_handlers = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in guarded_handlers:
                continue
            found_handlers.add(node.name)
            handler_source = ast.dump(node)
            self.assertIn("_is_ready", handler_source)
            self.assertIn("Return", handler_source)

        self.assertEqual(guarded_handlers, found_handlers)

    def test_transferability_does_not_restrict_to_model_categories(self):
        self.assertTrue(REVIT_MODULE_PATH.exists(), "families_transfer_revit.py is missing")
        source = REVIT_MODULE_PATH.read_text()
        tree = ast.parse(source, filename=str(REVIT_MODULE_PATH))

        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "is_transferable_family":
                target = node
                break

        self.assertIsNotNone(target, "is_transferable_family is missing")
        attribute_names = set(
            node.attr
            for node in ast.walk(target)
            if isinstance(node, ast.Attribute)
        )

        self.assertNotIn("FamilyCategory", attribute_names)
        self.assertNotIn("CategoryType", attribute_names)


if __name__ == "__main__":
    unittest.main()
