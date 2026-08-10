"""Bundle, XAML and IronPython wiring checks for Linked Sheets Copy.

Modelled on ``test_load_parameters_command_names.py``.  These catch the drift a
syntax check cannot: a handler renamed in the code but not the XAML, a control
renamed in the XAML but not the code, a Python-3-only construct IronPython 2.7
will refuse at runtime, and the design decisions that would be silently wrong
rather than loudly broken if a refactor undid them.
"""

import ast
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PANEL_DIR = REPO_ROOT / "EasyBIM.tab" / "Sheet.panel"
COMMAND_DIR = PANEL_DIR / "Linked Sheets Copy.pushbutton"
UI_MODULE = COMMAND_DIR / "linked_sheets_copy_ui.py"
STATE_MODULE = COMMAND_DIR / "linked_sheets_copy_state.py"
REVIT_MODULE = COMMAND_DIR / "linked_sheets_copy_revit.py"
GEOMETRY_MODULE = REPO_ROOT / "lib" / "easybim" / "sheet_geometry.py"
COPY_PASTE_MODULE = REPO_ROOT / "lib" / "easybim" / "copy_paste.py"
VIEW_ALIGN = (REPO_ROOT / "EasyBIM.tab" / "Views.panel"
              / "View Align.pushbutton" / "script.py")

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged",
                 "TextChanged", "PreviewKeyDown", "CellEditEnding")

WINDOW_CLASSES = {
    "LinkedSheetsCopyWindow.xaml": "LinkedSheetsCopyWindow",
    "ConfirmWindow.xaml": "ConfirmWindow",
    "ReportWindow.xaml": "ReportWindow",
}

EXPECTED_MODULES = (
    "script.py",
    "linked_sheets_copy_state.py",
    "linked_sheets_copy_revit.py",
    "linked_sheets_copy_ui.py",
)

CONTROL_ATTRIBUTE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

WINDOW_MEMBERS = frozenset([
    "Activate", "Close", "Content", "Cursor", "DialogResult", "Focus",
    "Height", "Hide", "Icon", "IsEnabled", "Left", "Owner", "Show",
    "ShowDialog", "Tag", "Title", "Top", "Topmost", "Width", "WindowState",
])


def _xaml_root(name):
    return ET.parse(str(COMMAND_DIR / name)).getroot()


def _xaml_names(root):
    names = set()
    for element in root.iter():
        name = element.attrib.get(X_NAME)
        if name:
            names.add(name)
    return names


def _xaml_handlers(root):
    handlers = set()
    for element in root.iter():
        for attr in HANDLER_ATTRS:
            value = element.attrib.get(attr)
            if value:
                handlers.add(value)
    return handlers


def _class_node(path, class_name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    return None


def _class_members(path, class_name):
    node = _class_node(path, class_name)
    if node is None:
        return None
    members = set()
    for child in node.body:
        if isinstance(child, ast.FunctionDef):
            members.add(child.name)
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    members.add(target.id)
    return members


def _class_control_attributes(path, class_name):
    node = _class_node(path, class_name)
    attributes = set()
    if node is None:
        return attributes
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute):
            continue
        value = child.value
        if isinstance(value, ast.Name) and value.id == "self":
            if (CONTROL_ATTRIBUTE.match(child.attr)
                    and child.attr not in WINDOW_MEMBERS):
                attributes.add(child.attr)
    return attributes


def _function_source(path, function_name):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            end = getattr(node, "end_lineno", None) or len(lines)
            return u"\n".join(lines[node.lineno - 1:end])
    return u""


class LinkedSheetsCopyBundleTests(unittest.TestCase):
    def test_command_folder_sits_in_the_sheet_panel(self):
        self.assertTrue(COMMAND_DIR.is_dir())

    def test_panel_layout_lists_the_new_button(self):
        # An unlisted bundle is appended by pyRevit, so placement needs this.
        layout = (PANEL_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Linked Sheets Copy", layout)

    def test_panel_layout_still_lists_every_sibling_button(self):
        layout = (PANEL_DIR / "bundle.yaml").read_text(encoding="utf-8")
        for sibling in ("Sheet Manager", "Revision Manager", "Print Set",
                        "Print Sheets", "Isolate"):
            self.assertIn(sibling, layout)

    def test_bundle_yaml_carries_the_ribbon_title(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Linked Sheets", bundle)
        self.assertIn("Copy", bundle)
        self.assertIn("author: Ruiming Liu", bundle)

    def test_bundle_declares_the_revit_floor(self):
        # RevitLinkGraphicsSettings and View.SetLinkOverrides are 2024+, and
        # there is no fallback: without them the tool has no purpose.
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("min_revit_version: 2024", bundle)

    def test_tooltip_states_the_promise_and_the_limit(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("By Linked View", bundle)
        self.assertIn("model coordinates", bundle)
        self.assertIn("Only plan views", bundle)

    def test_both_icon_variants_exist(self):
        for name in ("icon.png", "icon.dark.png"):
            icon = COMMAND_DIR / name
            self.assertTrue(icon.is_file(), name)
            self.assertGreater(icon.stat().st_size, 0, name)

    def test_expected_modules_exist(self):
        for name in EXPECTED_MODULES:
            self.assertTrue((COMMAND_DIR / name).is_file(), name)


class LinkedSheetsCopyXamlTests(unittest.TestCase):
    def test_every_window_xaml_parses(self):
        for name in WINDOW_CLASSES:
            _xaml_root(name)

    def test_no_stray_xaml_files(self):
        found = set(path.name for path in COMMAND_DIR.glob("*.xaml"))
        self.assertEqual(set(WINDOW_CLASSES), found)

    def test_every_handler_resolves_on_its_own_window_class(self):
        for xaml_name, class_name in sorted(WINDOW_CLASSES.items()):
            members = _class_members(UI_MODULE, class_name)
            self.assertIsNotNone(members, class_name)
            for handler in sorted(_xaml_handlers(_xaml_root(xaml_name))):
                self.assertIn(
                    handler, members,
                    "%s references %s but %s has no such method"
                    % (xaml_name, handler, class_name))

    def test_every_control_attribute_has_a_matching_x_name(self):
        for xaml_name, class_name in sorted(WINDOW_CLASSES.items()):
            names = _xaml_names(_xaml_root(xaml_name))
            members = _class_members(UI_MODULE, class_name) or set()
            for attribute in sorted(
                    _class_control_attributes(UI_MODULE, class_name)):
                if attribute in members:
                    continue
                self.assertIn(
                    attribute, names,
                    "%s uses self.%s but %s has no such x:Name"
                    % (class_name, attribute, xaml_name))

    def test_the_sheet_checkbox_column_carries_no_label(self):
        # A CheckBox with Content swallows the row click, which breaks the
        # Extended range selection the grid is set up for.
        source = (COMMAND_DIR
                  / "LinkedSheetsCopyWindow.xaml").read_text(encoding="utf-8")
        self.assertNotIn('<CheckBox Content="{Binding', source)

    def test_the_sheet_grid_allows_range_selection(self):
        source = (COMMAND_DIR
                  / "LinkedSheetsCopyWindow.xaml").read_text(encoding="utf-8")
        self.assertIn('SelectionMode="Extended"', source)
        self.assertIn('SelectionUnit="FullRow"', source)

    def test_the_editable_columns_push_every_keystroke_through(self):
        # The collision re-check reads the row, so the binding must not wait
        # for focus to leave the cell.
        source = (COMMAND_DIR
                  / "LinkedSheetsCopyWindow.xaml").read_text(encoding="utf-8")
        self.assertIn(
            '{Binding new_number, Mode=TwoWay, '
            'UpdateSourceTrigger=PropertyChanged}', source)
        self.assertIn(
            '{Binding new_name, Mode=TwoWay, '
            'UpdateSourceTrigger=PropertyChanged}', source)

    def test_the_level_dropdown_offers_the_rows_own_choices(self):
        source = (COMMAND_DIR
                  / "LinkedSheetsCopyWindow.xaml").read_text(encoding="utf-8")
        self.assertIn('ItemsSource="{Binding choices}"', source)
        self.assertIn('SelectedItem="{Binding host_choice', source)

    def test_disabled_buttons_explain_themselves(self):
        # Every window has exactly one button that starts or becomes disabled,
        # and a disabled button that cannot say why is the repo's least
        # favourite kind.
        for name, count in (("LinkedSheetsCopyWindow.xaml", 1),
                            ("ConfirmWindow.xaml", 1),
                            ("ReportWindow.xaml", 1)):
            source = (COMMAND_DIR / name).read_text(encoding="utf-8")
            self.assertEqual(
                count, source.count('ToolTipService.ShowOnDisabled="True"'),
                name)


class LinkedSheetsCopyContractTests(unittest.TestCase):
    """Assertions that lock in decisions a refactor could quietly undo."""

    def test_the_whole_run_is_one_undo_step(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("TransactionGroup", source)
        self.assertIn("Assimilate", source)
        self.assertIn("HasStarted", source)

    def test_each_sheet_rolls_back_on_its_own(self):
        # One sheet whose title block will not copy must not take the rest of
        # the batch with it.
        body = _function_source(REVIT_MODULE, "_run_one_sheet")
        self.assertIn("TransactionGroup", body)
        self.assertIn("RollBack", body)

    def test_a_cancelled_run_zeroes_the_counters(self):
        body = _function_source(REVIT_MODULE, "run_copy")
        self.assertIn("clear_applied", body)
        self.assertIn("RollBack", body)

    def test_a_rolled_back_sheet_is_taken_out_of_the_report_too(self):
        body = _function_source(REVIT_MODULE, "_run_one_sheet")
        self.assertIn("summary.snapshot()", body)
        self.assertIn("summary.restore(snapshot)", body)

    def test_derived_geometry_is_regenerated_before_it_is_read(self):
        # View.Outline and the title block instance are both stale until the
        # document has regenerated, and both are read back straight away.
        for name in ("place_and_align", "_build_sheet"):
            self.assertIn("doc.Regenerate()",
                          _function_source(REVIT_MODULE, name), name)

    def test_the_link_is_pointed_at_the_linked_view(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("RevitLinkGraphicsSettings", source)
        self.assertIn("LinkVisibility.ByLinkView", source)
        self.assertIn("LinkedViewId", source)
        self.assertIn("SetLinkOverrides", source)

    def test_the_2024_floor_is_checked_at_runtime_as_well(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn('hasattr(DB, "RevitLinkGraphicsSettings")', source)

    def test_a_name_clash_always_resolves_in_favour_of_this_model(self):
        source = COPY_PASTE_MODULE.read_text(encoding="utf-8")
        self.assertIn("IDuplicateTypeNamesHandler", source)
        self.assertIn("DuplicateTypeAction.UseDestinationTypes", source)
        self.assertNotIn("DuplicateTypeAction.UseOtherTypes", source)

    def test_every_cross_document_copy_carries_that_handler(self):
        # The factory moved to lib/ once a second tool needed it; one call
        # site repo-wide is what stops a new copy forgetting the handler.
        self.assertEqual(
            COPY_PASTE_MODULE.read_text(encoding="utf-8")
            .count("DB.CopyPasteOptions()"), 1,
            "CopyPasteOptions is built in one place so the duplicate-type "
            "handler cannot be forgotten at a new call site")
        self.assertIn("SetDuplicateTypeNamesHandler",
                      COPY_PASTE_MODULE.read_text(encoding="utf-8"))
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("DB.CopyPasteOptions()", source)
        self.assertIn("from easybim.copy_paste import copy_paste_options",
                      source)

    def test_the_viewport_is_measured_after_it_is_configured(self):
        # This is the bug View Align has: it solves the anchor in a precheck
        # pass, then writes the crop, which moves View.Outline underneath it.
        body = _function_source(REVIT_MODULE, "place_and_align")
        rotation = body.index("viewport.Rotation =")
        change_type = body.index("ChangeTypeId")
        measure = body.index("build_projection(host_view, viewport)")
        move = body.index("SetBoxCenter")
        self.assertLess(rotation, measure)
        self.assertLess(change_type, measure)
        self.assertLess(measure, move)

    def test_the_view_is_fully_built_before_the_viewport_exists(self):
        body = _function_source(REVIT_MODULE, "_build_view")
        create = body.index("create_host_view(")
        place = body.index("place_and_align(")
        self.assertLess(create, place)

    def test_a_second_point_is_checked_after_every_placement(self):
        # One matching point proves the translation and nothing else; a wrong
        # scale or rotation only shows up away from the anchor.
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("_second_point_deviation", source)
        self.assertIn("ALIGNMENT_TOLERANCE_MM", source)

    def test_the_link_placement_is_vetted_before_anything_is_built(self):
        body = _function_source(REVIT_MODULE, "link_transform_blocker")
        self.assertIn("HasReflection", body)
        self.assertIn("BasisZ", body)
        self.assertIn("Scale", body)

    def test_the_crop_is_forced_on_in_the_new_view(self):
        # With the crop off, host geometry drives the viewport size and the
        # printed extent stops matching the link.
        body = _function_source(REVIT_MODULE, "_apply_crop")
        self.assertIn("CropBoxActive = True", body)

    def test_the_crop_transform_is_composed_with_the_links(self):
        body = _function_source(REVIT_MODULE, "build_host_crop_box")
        self.assertIn("transform.Multiply(source_transform)", body)

    def test_element_id_parameters_are_never_copied_across_documents(self):
        body = _function_source(REVIT_MODULE, "copy_parameters")
        self.assertIn("DB.StorageType.ElementId", body)
        self.assertIn("continue", body)

    def test_tags_and_dimensions_are_never_copied_out_of_a_linked_view(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        for excluded in ("IndependentTag", "Dimension", "SpotDimension",
                         "RoomTag"):
            self.assertIn(excluded, source)

    def test_revision_clouds_need_an_explicit_opt_in(self):
        body = _function_source(REVIT_MODULE, "sheet_detailing_ids")
        self.assertIn("include_revision_clouds", body)
        self.assertIn("RevisionCloud", body)

    def test_the_title_block_is_moved_to_the_linked_position(self):
        # Sheet coordinates are absolute; a title block at a different place
        # puts every correctly-placed viewport in the wrong spot on the page.
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("_position_title_block", source)
        self.assertIn("MoveElement", source)

    def test_the_active_view_is_never_changed_from_a_modal_window(self):
        ui_source = UI_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("ActiveView", ui_source)
        self.assertIn("open_requested", ui_source)

    def test_the_alignment_maths_lives_in_one_place(self):
        # Two copies of it would let "align" and "copy and align" drift apart.
        self.assertTrue(GEOMETRY_MODULE.is_file())
        revit_source = REVIT_MODULE.read_text(encoding="utf-8")
        align_source = VIEW_ALIGN.read_text(encoding="utf-8")
        self.assertIn("from easybim import sheet_geometry", revit_source)
        self.assertIn("from easybim import sheet_geometry", align_source)
        self.assertIn("sheet_geometry.build_projection", align_source)

    def test_view_align_no_longer_carries_its_own_copy(self):
        align_source = VIEW_ALIGN.read_text(encoding="utf-8")
        self.assertNotIn("outline.Min.U", align_source)
        self.assertNotIn("math.cos", align_source)


class LinkedSheetsCopyIronPythonTests(unittest.TestCase):
    def test_command_scripts_avoid_python3_only_constructs(self):
        failures = []
        paths = sorted(COMMAND_DIR.glob("*.py")) + [GEOMETRY_MODULE]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))

            if re.search(r"(?<![A-Za-z0-9_])[fF]['\"]", source):
                failures.append("%s appears to contain an f-string"
                                % path.name)

            for node in ast.walk(tree):
                if (isinstance(node, ast.ImportFrom)
                        and node.module == "dataclasses"):
                    failures.append("%s imports dataclasses" % path.name)

                if isinstance(node, ast.ClassDef):
                    for decorator in node.decorator_list:
                        if getattr(decorator, "id", None) == "dataclass":
                            failures.append("%s uses @dataclass on %s"
                                            % (path.name, node.name))

                if isinstance(node, ast.AnnAssign):
                    failures.append("%s uses variable annotations"
                                    % path.name)

                if isinstance(node, ast.FunctionDef):
                    if node.returns is not None:
                        failures.append("%s uses return annotations"
                                        % path.name)
                    for arg in list(node.args.args) + list(
                            node.args.kwonlyargs):
                        if arg.annotation is not None:
                            failures.append("%s uses argument annotations"
                                            % path.name)

                if isinstance(node, ast.JoinedStr):
                    failures.append("%s uses f-strings" % path.name)

        self.assertEqual([], failures)

    def test_none_is_never_read_as_an_attribute(self):
        # DB.StorageType.None is a syntax error in Python; getattr is the only
        # way to reach it, and it is easy to reintroduce by hand.
        for path in sorted(COMMAND_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\.None\b", source), path.name)

    def test_state_module_stays_free_of_revit_imports(self):
        # The state module is what the test suite loads standalone.
        source = STATE_MODULE.read_text(encoding="utf-8")
        for forbidden in ("import clr", "from pyrevit", "Autodesk.Revit"):
            self.assertNotIn(forbidden, source)

    def test_geometry_module_stays_free_of_revit_imports(self):
        source = GEOMETRY_MODULE.read_text(encoding="utf-8")
        for forbidden in ("import clr", "from pyrevit", "Autodesk.Revit"):
            self.assertNotIn(forbidden, source)

    def test_ui_module_stays_free_of_revit_api_imports(self):
        source = UI_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Autodesk.Revit", source)
        self.assertNotIn("import clr", source)


if __name__ == "__main__":
    unittest.main()
