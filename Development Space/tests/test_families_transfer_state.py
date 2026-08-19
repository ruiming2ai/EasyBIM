import ast
import importlib.util
import sys
import xml.etree.ElementTree as ET
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (
    REPO_ROOT
    / "EasyBIM.tab"
    / "Family.panel"
    / "Families Transfer.pushbutton"
)
LIB_DIR = REPO_ROOT / "lib" / "easybim"
UI_DIR = LIB_DIR / "ui"
STATE_MODULE_PATH = COMMAND_DIR / "families_transfer_state.py"
REVIT_MODULE_PATH = COMMAND_DIR / "families_transfer_revit.py"
SCRIPT_MODULE_PATH = COMMAND_DIR / "script.py"
UI_MODULE_PATH = COMMAND_DIR / "families_transfer_ui.py"
# The family-selection pages, collectors and helpers are shared with Families
# Downgrade and live in lib; the transfer-only pieces stay in the bundle.
SELECTION_STATE_MODULE_PATH = LIB_DIR / "family_selection_state.py"
SELECTION_UI_MODULE_PATH = LIB_DIR / "family_selection_ui.py"
SELECTION_REVIT_MODULE_PATH = LIB_DIR / "family_selection_revit.py"
SOURCE_XAML_PATH = UI_DIR / "family_selection_source.xaml"
FAMILY_XAML_PATH = UI_DIR / "family_selection_families.xaml"
TARGET_XAML_PATH = COMMAND_DIR / "TargetSelectionWindow.xaml"
LINK_XAML_PATH = UI_DIR / "family_selection_links.xaml"


def _function_source_text(path, function_name):
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            end = getattr(node, "end_lineno", None) or len(lines)
            return u"\n".join(lines[node.lineno - 1:end])
    return u""


def _load_selection_state_module():
    """The shared, pure-Python selection helpers from lib."""
    spec = importlib.util.spec_from_file_location(
        "family_selection_state",
        str(SELECTION_STATE_MODULE_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StateSurface(object):
    """The state surface the command sees: its own module over the shared one.

    The bundle module holds only the transfer-specific pieces and imports the
    rest from ``easybim.family_selection_state``; the tests below were written
    against the one flat namespace the command has, so that is what they get.
    """

    def __init__(self, bundle_module, selection_module):
        self._bundle = bundle_module
        self._selection = selection_module

    def __getattr__(self, name):
        if hasattr(self._bundle, name):
            return getattr(self._bundle, name)
        return getattr(self._selection, name)


def _load_state_module():
    lib_root = str(REPO_ROOT / "lib")
    if lib_root not in sys.path:
        sys.path.insert(0, lib_root)
    spec = importlib.util.spec_from_file_location(
        "families_transfer_state",
        str(STATE_MODULE_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return _StateSurface(module, _load_selection_state_module())


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

    def test_source_window_load_more_names_the_recent_project(self):
        self.assertTrue(SOURCE_XAML_PATH.exists(), "SourceSelectionWindow.xaml is missing")
        source = SOURCE_XAML_PATH.read_text()

        self.assertIn("Load More from Recent Project", source)
        self.assertIn('Click="load_more_click"', source)
        self.assertNotIn('Content="Load More from Project"', source)

    def test_the_model_pick_button_left_the_source_window(self):
        # It lives on the family browser now; two entry points to the same
        # pick is exactly the drift this file exists to catch.
        source = SOURCE_XAML_PATH.read_text()

        self.assertNotIn("Click to Select More in Project", source)
        self.assertNotIn('Click="select_click"', source)

    def test_family_window_carries_the_model_pick_button(self):
        self.assertTrue(FAMILY_XAML_PATH.exists(), "FamilySelectionWindow.xaml is missing")
        source = FAMILY_XAML_PATH.read_text()

        self.assertIn("Select in the model", source)
        self.assertNotIn("Select More in the model", source)
        self.assertIn('Click="pick_more_click"', source)
        self.assertIn('x:Name="pick_more_btn"', source)

    def test_the_family_browser_keeps_its_own_title(self):
        # Only the in-window heading switches between the two modes.
        source = FAMILY_XAML_PATH.read_text()

        self.assertIn('Title="Transferable Families"', source)
        self.assertIn('x:Name="header_tb"', source)

    def test_source_window_xaml_exposes_the_chosen_link_families_card(self):
        source = SOURCE_XAML_PATH.read_text()

        self.assertIn("Load More from Revit Links", source)
        self.assertIn('Click="load_link_families_click"', source)
        self.assertIn('Click="link_family_select_all_click"', source)
        self.assertIn('Click="link_family_select_none_click"', source)
        self.assertIn('x:Name="LinkFamilyListPanel"', source)
        self.assertIn('x:Name="LinkFamilySearchBox"', source)
        # Lowercase x:Names are invisible to the drift checker's
        # ^[A-Z] filter, so they are asserted by hand.
        self.assertIn('x:Name="link_family_count_tb"', source)

    def test_the_links_themselves_moved_off_the_source_window(self):
        source = SOURCE_XAML_PATH.read_text()

        self.assertNotIn('x:Name="LinkListPanel"', source)
        self.assertNotIn('x:Name="LinkSearchBox"', source)

    def test_link_selection_window_lists_the_links(self):
        self.assertTrue(LINK_XAML_PATH.exists(), "LinkSelectionWindow.xaml is missing")
        source = LINK_XAML_PATH.read_text()

        self.assertIn('x:Name="LinkListPanel"', source)
        self.assertIn('x:Name="LinkSearchBox"', source)
        self.assertIn('x:Name="link_count_tb"', source)
        self.assertIn('Click="link_select_all_click"', source)
        self.assertIn('Click="link_select_none_click"', source)
        self.assertIn('Click="back_click"', source)
        self.assertIn('Click="next_click"', source)

    def test_hide_unchecked_is_on_both_second_pages(self):
        for path in (FAMILY_XAML_PATH, LINK_XAML_PATH):
            source = path.read_text()
            self.assertIn("Hide Un-checked", source, path.name)
            self.assertIn('x:Name="hide_unchecked_cb"', source, path.name)
            self.assertIn('Click="hide_unchecked_click"', source, path.name)

        ui_source = SELECTION_UI_MODULE_PATH.read_text()
        self.assertEqual(2, ui_source.count("def hide_unchecked_click"))

    def test_no_source_card_can_be_squashed_to_nothing(self):
        # A star row with no MinHeight shrinks to zero and paints its child
        # outside the cell - which is what let the cards eat their own
        # headers when the window was dragged short.
        root = ET.parse(str(SOURCE_XAML_PATH)).getroot()
        star_rows = 0
        for row in root.iter(
                "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}RowDefinition"):
            if row.attrib.get("Height") == "*":
                star_rows += 1
                self.assertIn("MinHeight", row.attrib)
        self.assertGreaterEqual(star_rows, 4)

    def test_the_links_card_sits_below_the_opened_rfa_card(self):
        source = SOURCE_XAML_PATH.read_text()

        self.assertLess(
            source.index("Add Opened .rfa Files"),
            source.index('Content="Load More from Revit Links"'),
        )

    def test_link_families_are_collected_only_after_a_link_is_checked(self):
        # Scanning every linked document up front would cost a full collector
        # pass per link before the user has said which one they care about.
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

        called = []
        for node in source_step_body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "ShowDialog"
            ):
                break
            called.extend(
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            )

        self.assertNotIn("get_link_family_options", called)
        self.assertNotIn("prepare_link_documents", called)

    def test_the_link_flow_runs_over_three_pages(self):
        source = SCRIPT_MODULE_PATH.read_text()

        self.assertIn('STEP_LINK_DOCS = "link_docs"', source)
        self.assertIn("LinkSelectionWindow", source)
        # Page 1 -> page 2.
        self.assertIn("step = STEP_LINK_DOCS", source)
        # Page 2 -> page 3, and page 3 Back -> page 2 rather than page 1.
        self.assertIn("STEP_LINK_FAMILIES if link_docs_window.result", source)

    def test_the_link_browser_local_avoids_the_family_window_substring(self):
        # test_family_add_branch_returns_to_source_without_target_navigation
        # matches if-branches by substring over ast.dump and breaks on the
        # first hit, so a local containing "family_window" silently rebinds
        # it to a different branch - where it still passes, testing nothing.
        source = SCRIPT_MODULE_PATH.read_text()

        self.assertNotIn("link_family_window", source)
        self.assertIn("link_docs_window", source)

    def test_the_links_are_not_re_enumerated_just_to_write_the_report(self):
        source = SCRIPT_MODULE_PATH.read_text()

        self.assertIn("_probed_links()", source)
        self.assertNotIn("_note_link_refusals(summary, _link_documents())", source)

    def test_a_tag_reaches_its_family_through_the_type_id(self):
        # Only FamilyInstance and TextElement declare Symbol in the whole
        # Revit API, so every tag class needs the GetTypeId route - and
        # TextElement.Symbol is a TextElementType, which is why the
        # isinstance guard is load-bearing rather than defensive.
        body = _function_source_text(SELECTION_REVIT_MODULE_PATH, "family_from_element")

        self.assertIn("GetTypeId", body)
        self.assertIn("isinstance(symbol, DB.FamilySymbol)", body)
        self.assertIn("InvalidElementId", body)

    def test_transfer_and_export_take_a_progress_callback(self):
        source = REVIT_MODULE_PATH.read_text()
        tree = ast.parse(source, filename=str(REVIT_MODULE_PATH))

        for name in ("transfer_families", "export_families"):
            target = None
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == name:
                    target = node
                    break
            self.assertIsNotNone(target, "{} is missing".format(name))
            self.assertIn("progress", [arg.arg for arg in target.args.args], name)

        self.assertIn("ProgressBar", SCRIPT_MODULE_PATH.read_text())

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
        self.assertTrue(SELECTION_UI_MODULE_PATH.exists(), "family_selection_ui.py is missing")
        source = SELECTION_UI_MODULE_PATH.read_text()
        tree = ast.parse(source, filename=str(SELECTION_UI_MODULE_PATH))

        self.assertIn("_selected_family_controls", source)
        self.assertIn("selected_family_keys", source)
        self.assertIn('"load_more"', source)

        populate_method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_populate_selected_families":
                populate_method = node
                break

        self.assertIsNotNone(populate_method, "_populate_selected_families is missing")
        # The rows are built by the shared list helper now - all three cards
        # and both second pages build the same list - so the assertion is
        # that this card goes through it, and that it makes CheckBoxes.
        populate_source = ast.dump(populate_method)
        self.assertIn("_fill_checkbox_list", populate_source)

        filler = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_fill_checkbox_list":
                filler = node
                break
        self.assertIsNotNone(filler, "_fill_checkbox_list is missing")
        self.assertIn("CheckBox", ast.dump(filler))

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
        self.assertTrue(SELECTION_UI_MODULE_PATH.exists(), "family_selection_ui.py is missing")
        source = SELECTION_UI_MODULE_PATH.read_text()
        tree = ast.parse(source, filename=str(SELECTION_UI_MODULE_PATH))

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
        self.assertTrue(SELECTION_UI_MODULE_PATH.exists(), "family_selection_ui.py is missing")
        source = SELECTION_UI_MODULE_PATH.read_text()

        self.assertIn("Windows.Controls.Expander", source)
        self.assertIn("def expand_all_click", source)
        self.assertIn("def collapse_all_click", source)

    def test_wpf_search_handlers_guard_until_window_is_ready(self):
        self.assertTrue(SELECTION_UI_MODULE_PATH.exists(), "family_selection_ui.py is missing")
        tree = ast.parse(SELECTION_UI_MODULE_PATH.read_text(), filename=str(SELECTION_UI_MODULE_PATH))

        guarded_handlers = {
            "selected_source_search_changed",
            "open_family_search_changed",
            "family_search_changed",
            "link_search_changed",
            "link_family_search_changed",
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

    def test_a_link_family_key_carries_the_document_it_came_from(self):
        module = _load_state_module()

        key = module.make_link_family_key("path|c:\\jobs\\arch model.rvt", "12345")

        self.assertTrue(module.is_link_family_key(key))
        self.assertFalse(module.is_project_family_key(key))
        self.assertFalse(module.is_open_family_document_key(key))
        self.assertEqual(
            "path|c:\\jobs\\arch model.rvt",
            module.link_document_key_from_family_key(key),
        )

    def test_the_same_element_id_in_two_links_yields_two_keys(self):
        # An ElementId is only unique inside its own document, so without the
        # document key two links would collapse onto one row.
        module = _load_state_module()

        first = module.make_link_family_key("path|c:\\a.rvt", "12345")
        second = module.make_link_family_key("path|c:\\b.rvt", "12345")

        self.assertNotEqual(first, second)
        self.assertNotEqual(first, module.make_project_family_key("12345"))

    def test_make_link_family_key_is_idempotent(self):
        module = _load_state_module()

        once = module.make_link_family_key("path|c:\\a.rvt", "12345")

        self.assertEqual(once, module.make_link_family_key("path|c:\\a.rvt", once))

    def test_link_document_key_is_empty_for_other_kinds_of_key(self):
        module = _load_state_module()

        self.assertEqual("", module.link_document_key_from_family_key("project|7"))
        self.assertEqual("", module.link_document_key_from_family_key(""))

    def test_split_selected_family_keys_sorts_all_three_sources(self):
        module = _load_state_module()

        project_keys, open_keys, link_keys = module.split_selected_family_keys([
            module.make_project_family_key("7"),
            module.make_open_family_document_key("doc-desk"),
            module.make_link_family_key("path|c:\\a.rvt", "12345"),
            "nonsense",
        ])

        self.assertEqual({"project|7"}, project_keys)
        self.assertEqual({"doc-desk"}, open_keys)
        self.assertEqual({"link|path|c:\\a.rvt|12345"}, link_keys)

    def test_unchecking_a_link_drops_the_families_chosen_from_it(self):
        module = _load_state_module()

        chosen = [
            module.FamilyOption("Door", module.make_link_family_key("path|c:\\a.rvt", "1")),
            module.FamilyOption("Column", module.make_link_family_key("path|c:\\b.rvt", "2")),
        ]

        kept = module.prune_link_families_to_checked_links(chosen, {"path|c:\\b.rvt"})

        self.assertEqual(["Column"], [family.name for family in kept])
        self.assertEqual([], module.prune_link_families_to_checked_links(chosen, set()))

    def test_link_document_option_starts_unchecked_and_unprobed(self):
        module = _load_state_module()

        option = module.LinkDocumentOption("Arch.rvt", "path|c:\\a.rvt")

        self.assertFalse(option.is_selected)
        self.assertTrue(option.is_loaded)
        self.assertIsNone(option.is_extractable)
        self.assertEqual("", option.note)

    def test_family_option_carries_the_document_that_owns_it(self):
        module = _load_state_module()

        default = module.FamilyOption("Door", "project|1")
        link_family = module.FamilyOption(
            "Door", "link|path|c:\\a.rvt|1",
            source_kind=module.SOURCE_LINK,
            source_document="link-doc",
            source_label="Arch.rvt",
        )

        self.assertIsNone(default.source_document)
        self.assertEqual("", default.source_label)
        self.assertEqual("link-doc", link_family.source_document)
        self.assertEqual("Arch.rvt", link_family.source_label)

    def test_merge_transferable_families_takes_all_three_sources(self):
        module = _load_state_module()

        project_families = [
            module.FamilyOption("Door", "project|1", is_selected=True, category_name="Doors"),
        ]
        open_documents = [
            module.OpenFamilyDocumentOption(
                "Desk.rfa", "doc-desk", is_selected=True, category_name="Furniture"),
            module.OpenFamilyDocumentOption("Light.rfa", "doc-light", is_selected=False),
        ]
        link_families = [
            module.FamilyOption(
                "Column", "link|path|c:\\a.rvt|9", is_selected=True,
                source_kind=module.SOURCE_LINK, category_name="Columns"),
        ]

        merged = module.merge_transferable_family_options(
            project_families, open_documents, link_families)

        self.assertEqual(
            ["Column", "Door", "Desk.rfa"],
            [family.name for family in merged],
        )
        self.assertEqual(
            [module.SOURCE_LINK, module.SOURCE_PROJECT, module.SOURCE_OPEN_RFA],
            [family.source_kind for family in merged],
        )

    def test_merge_transferable_families_still_works_with_two_sources(self):
        module = _load_state_module()

        merged = module.merge_transferable_family_options(
            [module.FamilyOption("Door", "project|1", is_selected=True)], [])

        self.assertEqual(["Door"], [family.name for family in merged])

    def test_a_whole_source_reason_is_reported_once_not_per_family(self):
        module = _load_state_module()

        summary = module.TransferSummary(
            skipped=[
                module.TransferResult("Door", "Tower.rvt", "already in the target"),
                module.TransferResult("Column", "Tower.rvt", "already in the target"),
            ],
        )
        summary.add_note("Arch.rvt: Revit will not open a family out of this link")
        summary.add_note("Arch.rvt: Revit will not open a family out of this link")

        message = module.build_transfer_summary_text(summary)

        self.assertEqual(1, message.count("will not open a family out of this link"))
        self.assertLess(
            message.index("will not open a family out of this link"),
            message.index("Skipped:\n"),
        )

    def test_a_cancelled_run_says_so_on_the_first_line(self):
        module = _load_state_module()

        summary = module.TransferSummary(cancelled=True)

        self.assertTrue(
            module.build_transfer_summary_text(summary).startswith(
                "Families Transfer cancelled."))

    def test_the_count_line_reads_the_same_on_every_list(self):
        module = _load_state_module()

        self.assertEqual("2 selected, 3 unchecked.",
                         module.format_selection_count_text(2, 5))
        self.assertEqual("0 selected, 0 unchecked.",
                         module.format_selection_count_text(0, 0))
        # A stale checked count must never render a negative.
        self.assertEqual("7 selected, 0 unchecked.",
                         module.format_selection_count_text(7, 5))

    def test_the_footer_counts_every_source_not_one_of_them(self):
        module = _load_state_module()

        self.assertEqual("1 family selected in total.",
                         module.format_total_selection_text(1))
        self.assertEqual("7 families selected in total.",
                         module.format_total_selection_text(7))
        self.assertEqual("0 families selected in total.",
                         module.format_total_selection_text(0))

    def test_the_footer_never_names_a_single_source(self):
        # It used to read "N active-project families selected." while sitting
        # under three cards, so it looked like the whole selection.
        ui_source = SELECTION_UI_MODULE_PATH.read_text()
        script_source = SCRIPT_MODULE_PATH.read_text()

        for text in ("active-project families selected",
                     "link family/families selected"):
            self.assertNotIn(text, ui_source)
            self.assertNotIn(text, script_source)

        self.assertNotIn("source_status", script_source)
        self.assertIn("_refresh_total", ui_source)

    def test_every_card_read_refreshes_the_footer(self):
        # A tick in any card has to move the total, so all three readers
        # must call it - the link card was the easy one to forget.
        tree = ast.parse(SELECTION_UI_MODULE_PATH.read_text(), filename=str(SELECTION_UI_MODULE_PATH))
        readers = {
            "_read_selected_family_keys",
            "_read_selected_document_keys",
            "_read_selected_link_family_keys",
        }

        # Scoped to the source window on purpose: FamilySelectionWindow has a
        # method of the same name and no footer to refresh.
        window = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "SourceSelectionWindow":
                window = node
                break
        self.assertIsNotNone(window, "SourceSelectionWindow is missing")

        found = set()
        for node in window.body:
            if not isinstance(node, ast.FunctionDef) or node.name not in readers:
                continue
            found.add(node.name)
            self.assertIn("_refresh_total", ast.dump(node), node.name)

        self.assertEqual(readers, found)

    def test_hide_unchecked_keeps_only_ticked_rows(self):
        module = _load_state_module()

        options = [
            module.FamilyOption("Door", "project|1", is_selected=True),
            module.FamilyOption("Desk", "project|2", is_selected=False),
        ]

        self.assertEqual(
            ["Door", "Desk"],
            [o.name for o in module.filter_unchecked_options(options, False)])
        self.assertEqual(
            ["Door"],
            [o.name for o in module.filter_unchecked_options(options, True)])

    def test_everything_hide_unchecked_keeps_is_ticked(self):
        # This is the invariant that lets the WPF sync leave hidden rows
        # alone: a hidden row is always an unticked row, so it never holds
        # state a later read would need.
        module = _load_state_module()

        options = [
            module.OpenFamilyDocumentOption("A.rfa", "a", is_selected=True),
            module.OpenFamilyDocumentOption("B.rfa", "b", is_selected=False),
            module.LinkDocumentOption("C.rvt", "c", is_selected=True),
        ]

        for option in module.filter_unchecked_options(options, True):
            self.assertTrue(option.is_selected)

    def test_search_then_hide_matches_hide_then_search(self):
        module = _load_state_module()

        options = [
            module.FamilyOption("Door Single", "project|1", is_selected=True, category_name="Doors"),
            module.FamilyOption("Door Double", "project|2", is_selected=False, category_name="Doors"),
            module.FamilyOption("Desk", "project|3", is_selected=True, category_name="Furniture"),
        ]

        forward = module.filter_unchecked_options(
            module.filter_family_options(options, "door"), True)
        backward = module.filter_family_options(
            module.filter_unchecked_options(options, True), "door")

        self.assertEqual([o.name for o in forward], [o.name for o in backward])
        self.assertEqual(["Door Single"], [o.name for o in forward])

    def test_only_select_none_with_the_filter_on_needs_a_rebuild(self):
        module = _load_state_module()

        self.assertFalse(module.needs_repopulate_after_bulk_toggle(True, False))
        self.assertFalse(module.needs_repopulate_after_bulk_toggle(False, False))
        # Select All cannot reveal a hidden row - the hidden rows are exactly
        # the ones it does not touch.
        self.assertFalse(module.needs_repopulate_after_bulk_toggle(True, True))
        # Select None unchecks every visible row, so every one must vanish.
        self.assertTrue(module.needs_repopulate_after_bulk_toggle(False, True))

    def test_the_export_warning_names_the_files_it_would_replace(self):
        module = _load_state_module()

        text = module.build_export_overwrite_text(["Door.rfa", "Desk.rfa"], 12)

        self.assertIn("2 of 12 file(s) already exist", text)
        self.assertIn("- Door.rfa", text)
        self.assertIn("- Desk.rfa", text)

    def test_planned_export_names_match_what_export_would_write(self):
        # Same sanitising and same _2 suffix the export itself applies, so
        # the warning cannot name a different set of files than it replaces.
        module = _load_state_module()

        names = module.planned_export_filenames([
            module.FamilyOption("Door:Single", "project|1"),
            module.FamilyOption("Door_Single", "project|2"),
            module.FamilyOption("Desk", "project|3"),
        ])

        self.assertEqual(["Door_Single.rfa", "Door_Single_2.rfa", "Desk.rfa"], names)

    def test_transferability_does_not_restrict_to_model_categories(self):
        self.assertTrue(SELECTION_REVIT_MODULE_PATH.exists(), "family_selection_revit.py is missing")
        source = SELECTION_REVIT_MODULE_PATH.read_text()
        tree = ast.parse(source, filename=str(SELECTION_REVIT_MODULE_PATH))

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
