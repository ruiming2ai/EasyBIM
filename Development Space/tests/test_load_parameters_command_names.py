"""Bundle, XAML and IronPython wiring checks for Load Parameters.

Modelled on ``test_tag_align_command_names.py``.  These are the checks that
catch the drift a Python syntax check cannot: a handler renamed in the code but
not the XAML, a control renamed in the XAML but not the code, a Python-3-only
construct that IronPython 2.7 will refuse at runtime, and the transaction
contract quietly going missing.
"""

import ast
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PANEL_DIR = REPO_ROOT / "EasyBIM.tab" / "Parameters.panel"
COMMAND_DIR = PANEL_DIR / "Load Parameters.pushbutton"
UI_MODULE = COMMAND_DIR / "load_parameters_ui.py"
STATE_MODULE = COMMAND_DIR / "load_parameters_state.py"
REVIT_MODULE = COMMAND_DIR / "load_parameters_revit.py"

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged",
                 "TextChanged", "PreviewKeyDown")

# Every window's XAML and the class that has to back it.  Handler names repeat
# across windows (cancel_click, close_click), so checking against the whole
# module would pass even if one class forgot one.
WINDOW_CLASSES = {
    "LoadParametersWindow.xaml": "LoadParametersWindow",
    "SharedParameterFileWindow.xaml": "SharedParameterFileWindow",
    "ConfirmWindow.xaml": "ConfirmWindow",
    "ReportWindow.xaml": "ReportWindow",
}

EXPECTED_MODULES = (
    "script.py",
    "load_parameters_state.py",
    "load_parameters_revit.py",
    "load_parameters_ui.py",
)

CONTROL_ATTRIBUTE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

# Inherited from System.Windows.Window / forms.WPFWindow, so they are not
# x:Name lookups and must not be demanded of the XAML.
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


def _function_source(path, function_name):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            end = getattr(node, "end_lineno", None) or len(lines)
            return u"\n".join(lines[node.lineno - 1:end])
    return u""


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
    """PascalCase ``self.X`` names inside one class - the WPF controls."""
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


class LoadParametersBundleTests(unittest.TestCase):
    def test_command_folder_sits_in_the_parameters_panel(self):
        self.assertTrue(COMMAND_DIR.is_dir())

    def test_panel_layout_lists_the_new_button(self):
        # An unlisted bundle is appended by pyRevit, so placement needs this.
        layout = (PANEL_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Load Parameters", layout)

    def test_bundle_yaml_carries_the_ribbon_title(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Load", bundle)
        self.assertIn("Parameters", bundle)
        self.assertIn("author: Ruiming Liu", bundle)

    def test_bundle_declares_the_revit_floor(self):
        # GroupTypeId, SpecTypeId and the ForgeTypeId overloads of
        # AddParameter and ParameterBindings.Insert are all used unguarded.
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("min_revit_version: 2023", bundle)

    def test_tooltip_states_where_folder_families_end_up(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn(".rfa", bundle)
        self.assertIn("not loaded into the project", bundle)

    def test_both_icon_variants_exist(self):
        for name in ("icon.png", "icon.dark.png"):
            icon = COMMAND_DIR / name
            self.assertTrue(icon.is_file(), name)
            self.assertGreater(icon.stat().st_size, 0, name)

    def test_expected_modules_exist(self):
        for name in EXPECTED_MODULES:
            self.assertTrue((COMMAND_DIR / name).is_file(), name)


class LoadParametersXamlTests(unittest.TestCase):
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

    def test_checkbox_lists_keep_the_label_out_of_the_checkbox(self):
        # A CheckBox carrying the label swallows the row click and breaks
        # range selection, which is the whole point of Extended lists.
        source = (COMMAND_DIR
                  / "LoadParametersWindow.xaml").read_text(encoding="utf-8")
        self.assertNotIn('<CheckBox Content="{Binding name}"', source)
        self.assertIn('<TextBlock Text="{Binding name}"', source)

    def test_target_lists_allow_range_selection(self):
        source = (COMMAND_DIR
                  / "LoadParametersWindow.xaml").read_text(encoding="utf-8")
        self.assertEqual(3, source.count('SelectionMode="Extended"'))
        self.assertEqual(2, source.count('HorizontalContentAlignment="Stretch"'))

    def test_parameter_grid_selects_whole_rows(self):
        source = (COMMAND_DIR
                  / "LoadParametersWindow.xaml").read_text(encoding="utf-8")
        self.assertIn('SelectionUnit="FullRow"', source)

    def test_disabled_buttons_explain_themselves(self):
        source = (COMMAND_DIR
                  / "LoadParametersWindow.xaml").read_text(encoding="utf-8")
        # Both Load buttons start disabled, and a disabled button that cannot
        # say why is the repo's least favourite kind.
        self.assertEqual(2, source.count(
            'ToolTipService.ShowOnDisabled="True"'))

    def test_both_load_actions_are_present(self):
        source = (COMMAND_DIR
                  / "LoadParametersWindow.xaml").read_text(encoding="utf-8")
        self.assertIn('Content="Load to Families"', source)
        self.assertIn('Content="Load to Project"', source)


class LoadParametersContractTests(unittest.TestCase):
    """Assertions that lock in decisions a refactor could quietly undo."""

    def test_project_families_are_written_in_one_undo_step(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("TransactionGroup", source)
        self.assertIn("Assimilate", source)
        self.assertIn("HasStarted", source)

    def test_family_reload_never_overwrites_parameter_values(self):
        # Families Transfer sets this True, which is right for a transfer and
        # wrong here: adding a parameter must not wipe existing type values.
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("overwriteParameterValues.Value = False", source)
        self.assertNotIn("overwriteParameterValues.Value = True", source)

    def test_older_family_files_are_detected_before_they_are_written(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("BasicFileInfo", source)
        # BasicFileInfo.SavedInVersion was removed in Revit 2020, so reading
        # only that name made this check dead code on every supported Revit -
        # getattr fell through to "" and no family was ever flagged. Format is
        # the replacement; SavedInVersion may remain only as a fallback.
        self.assertIn('getattr(info, "Format"', source)
        self.assertLess(source.index('getattr(info, "Format"'),
                        source.index('getattr(info, "SavedInVersion"'))

    def test_an_unreadable_version_claims_nothing(self):
        # Without the truthiness guard, _version_number("") returns 0, 0 is
        # less than any host version, and every file reads as "older".
        body = _function_source(REVIT_MODULE, "_folder_family_row")
        self.assertIn("saved_number and saved_number < host_version", body)

    def test_shared_parameter_filename_is_always_restored(self):
        # It persists into Revit's .ini across sessions, so a leak follows the
        # user around forever.
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("_restore_shared_parameters_filename", source)
        self.assertIn("finally:", source)

    def test_open_shared_parameter_file_none_is_handled(self):
        # Revit returns None rather than raising for an unreadable file.
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("if definition_file is None:", source)

    def test_existing_binding_categories_are_merged_not_replaced(self):
        # ReInsert overwrites the whole category set, so rebinding without a
        # union would silently unbind everything not ticked this time.
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("ReInsert", source)
        self.assertIn("Union with what is already bound", source)

    def test_family_parameter_guid_read_is_gated_on_is_shared(self):
        # FamilyParameter.GUID throws on a non-shared parameter.
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("if bool(parameter.IsShared):", source)

    def test_add_handler_uses_each_events_own_delegate_type(self):
        # AddHandler throws at runtime on a mismatched delegate, and only in
        # Revit - so it is worth pinning here rather than discovering there.
        source = UI_MODULE.read_text(encoding="utf-8")
        self.assertIn("ButtonBase.ClickEvent, RoutedEventHandler", source)
        self.assertIn("SelectionChangedEventHandler(self.grid_combo_changed)",
                      source)

    def test_cell_templates_carry_no_event_attributes(self):
        # A XamlReader.Parse fragment cannot resolve a handler name, so every
        # cell interaction is wired at grid level instead.
        source = UI_MODULE.read_text(encoding="utf-8")
        for fragment in ("_CHECK_CELL", "_TEXT_CELL", "_GROUP_CELL",
                         "_BINDING_CELL"):
            self.assertIn(fragment, source)
        # Leading \s so IsChecked="{Binding ...}" - a binding, not an event -
        # is not mistaken for a handler.
        for attribute in ("Click", "SelectionChanged", "Checked"):
            self.assertIsNone(
                re.search(r'\s%s="' % attribute, source), attribute)

    def test_folder_families_run_outside_the_transaction_group(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        group_start = source.index("def load_into_project_families")
        folder_start = source.index("def load_into_folder_families")
        folder_body = source[folder_start:]
        self.assertLess(group_start, folder_start)
        self.assertNotIn("TransactionGroup", folder_body)


class LoadParametersIronPythonTests(unittest.TestCase):
    def test_command_scripts_avoid_python3_only_constructs(self):
        failures = []
        for path in sorted(COMMAND_DIR.glob("*.py")):
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

    def test_state_module_stays_free_of_revit_imports(self):
        # The state module is the part the test suite loads standalone; a
        # Revit import here makes test_load_parameters_state unrunnable.
        source = STATE_MODULE.read_text(encoding="utf-8")
        for forbidden in ("import clr", "from pyrevit", "Autodesk.Revit"):
            self.assertNotIn(forbidden, source)

    def test_ui_module_stays_free_of_revit_api_imports(self):
        source = UI_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Autodesk.Revit", source)
        self.assertNotIn("import clr", source)


if __name__ == "__main__":
    unittest.main()
