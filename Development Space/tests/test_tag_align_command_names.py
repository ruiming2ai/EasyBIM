import ast
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Tag Align.pushbutton"
UI_MODULE = COMMAND_DIR / "tag_align_ui.py"

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged", "TextChanged")

# Every window's XAML and the class that has to back it.  Handler names repeat
# across windows (ok_click, cancel_click), so checking them against the whole
# module would pass even if one class forgot one.
WINDOW_CLASSES = {
    "TagAlignWindow.xaml": "TagAlignWindow",
    "ReferenceOrientationWindow.xaml": "ReferenceOrientationWindow",
    "ReferenceConflictWindow.xaml": "ReferenceConflictWindow",
    "AlignTagOptionsWindow.xaml": "AlignTagOptionsWindow",
    "ModeWindow.xaml": "ModeWindow",
    "BatchWindow.xaml": "BatchWindow",
    "SelectionFilterWindow.xaml": "SelectionFilterWindow",
    "ReportWindow.xaml": "ReportWindow",
    "SavePresetWindow.xaml": "SavePresetWindow",
    "LoadPresetWindow.xaml": "LoadPresetWindow",
}


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


CONTROL_ATTRIBUTE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

# Inherited from System.Windows.Window / forms.WPFWindow, so they are not
# x:Name lookups and must not be demanded of the XAML.
WINDOW_MEMBERS = frozenset(
    [
        "Activate", "Close", "Content", "Cursor", "DialogResult", "Focus",
        "Height", "Hide", "Icon", "IsEnabled", "Left", "Owner", "Show",
        "ShowDialog", "Tag", "Title", "Top", "Topmost", "Width", "WindowState",
    ]
)


def _class_control_attributes(path, class_name):
    """PascalCase ``self.X`` names inside one class - the WPF controls."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    attributes = set()
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Attribute):
                continue
            value = child.value
            if isinstance(value, ast.Name) and value.id == "self":
                if CONTROL_ATTRIBUTE.match(child.attr) and child.attr not in WINDOW_MEMBERS:
                    attributes.add(child.attr)
    return attributes


def _class_methods(path, class_name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return set(
                child.name for child in node.body if isinstance(child, ast.FunctionDef)
            )
    return None


class TagAlignBundleTests(unittest.TestCase):
    def test_command_folder_sits_in_misc_tools(self):
        self.assertTrue(COMMAND_DIR.is_dir())

    def test_bundle_yaml_carries_the_ribbon_title(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Tag", bundle)
        self.assertIn("Align", bundle)
        self.assertIn("author: Ruiming Liu", bundle)

    def test_both_icon_variants_exist(self):
        for name in ("icon.png", "icon.dark.png"):
            path = COMMAND_DIR / name
            self.assertTrue(path.exists(), name)
            self.assertGreater(path.stat().st_size, 0, name)

    def test_expected_modules_exist(self):
        for name in ("script.py", "tag_align_state.py", "tag_align_revit.py",
                     "tag_align_ui.py", "tag_align_presets.py"):
            self.assertTrue((COMMAND_DIR / name).exists(), name)


class TagAlignXamlTests(unittest.TestCase):
    def test_every_xaml_parses(self):
        for name in WINDOW_CLASSES:
            path = COMMAND_DIR / name
            self.assertTrue(path.exists(), name)
            ET.parse(str(path))

    def test_no_stray_xaml_files(self):
        found = set(path.name for path in COMMAND_DIR.glob("*.xaml"))
        self.assertEqual(set(WINDOW_CLASSES), found)

    def test_every_handler_resolves_on_its_own_window_class(self):
        for xaml_name, class_name in WINDOW_CLASSES.items():
            methods = _class_methods(UI_MODULE, class_name)
            self.assertIsNotNone(methods, "no class %s in tag_align_ui" % class_name)
            missing = _xaml_handlers(_xaml_root(xaml_name)) - methods
            self.assertFalse(
                missing, "%s: %s is missing %s" % (xaml_name, class_name, missing)
            )

    def test_every_control_the_code_touches_exists_in_the_xaml(self):
        """The drift that actually bites: ``self.SomeButton`` with no x:Name.

        WPF only creates the attribute for controls the XAML names, so a rename
        on one side is an AttributeError the moment the window opens.  Control
        attributes are the PascalCase ones; the window's own state stays
        snake_case.
        """
        for xaml_name, class_name in WINDOW_CLASSES.items():
            names = _xaml_names(_xaml_root(xaml_name))
            for attribute in _class_control_attributes(UI_MODULE, class_name):
                self.assertIn(
                    attribute,
                    names,
                    "%s uses self.%s but %s has no such x:Name"
                    % (class_name, attribute, xaml_name),
                )

    def test_main_window_carries_the_agreed_controls(self):
        names = _xaml_names(_xaml_root("TagAlignWindow.xaml"))
        required = {
            "SelectOneButton", "SelectMultipleButton", "ClearReferencesButton",
            "ReferenceListPanel", "ReferenceSummaryText",
            "ScopeCategoryRadio", "ScopePairedRadio", "ScopeExactRadio",
            "ScaleWithViewCheck", "CopyLeaderCheck", "ChangeTagTypeCheck",
            "IncludePinnedCheck", "CollapseFlipCheck", "RotateFallbackCheck",
            "AlignButton", "AlignAndTagButton", "HintText",
        }
        self.assertFalse(required - names, "missing x:Name(s): %s" % (required - names))

    def test_align_buttons_start_disabled_and_still_explain_themselves(self):
        source = (COMMAND_DIR / "TagAlignWindow.xaml").read_text(encoding="utf-8")
        for marker in ('x:Name="AlignButton"', 'x:Name="AlignAndTagButton"'):
            block = source.split(marker)[1].split("/>")[0]
            self.assertIn('IsEnabled="False"', block, marker)
            # A greyed control with no tooltip leaves the user guessing why.
            self.assertIn('ToolTipService.ShowOnDisabled="True"', block, marker)

    def test_category_scope_comes_first_and_is_the_default(self):
        source = (COMMAND_DIR / "TagAlignWindow.xaml").read_text(encoding="utf-8")
        order = [
            source.index('x:Name="ScopeCategoryRadio"'),
            source.index('x:Name="ScopePairedRadio"'),
            source.index('x:Name="ScopeExactRadio"'),
        ]
        self.assertEqual(sorted(order), order, "scope radios are out of order")

        for name, expected in (
            ("ScopeCategoryRadio", True),
            ("ScopePairedRadio", False),
            ("ScopeExactRadio", False),
        ):
            block = source.split('x:Name="%s"' % name)[1].split("/>")[0]
            self.assertEqual(
                expected, 'IsChecked="True"' in block, "%s default is wrong" % name
            )

    def test_batch_window_has_the_four_bar_buttons(self):
        names = _xaml_names(_xaml_root("BatchWindow.xaml"))
        for required in ("FilterButton", "SelectButton", "DeselectButton", "ProcessButton"):
            self.assertIn(required, names)

    def test_main_window_carries_the_settings_buttons(self):
        names = _xaml_names(_xaml_root("TagAlignWindow.xaml"))
        self.assertIn("SaveSettingsButton", names)
        self.assertIn("LoadSettingsButton", names)

        source = (COMMAND_DIR / "TagAlignWindow.xaml").read_text(encoding="utf-8")
        save = source.split('x:Name="SaveSettingsButton"')[1].split("/>")[0]
        self.assertIn('IsEnabled="False"', save)
        self.assertIn('ToolTipService.ShowOnDisabled="True"', save)
        # Loading has to work before anything is picked - that is the point.
        load = source.split('x:Name="LoadSettingsButton"')[1].split("/>")[0]
        self.assertNotIn('IsEnabled="False"', load)

    def test_save_window_offers_all_three_destinations(self):
        names = _xaml_names(_xaml_root("SavePresetWindow.xaml"))
        for required in ("LocalRadio", "ModelRadio", "SharedRadio",
                         "SharedPathTextBox", "BrowseButton", "NameTextBox"):
            self.assertIn(required, names)

    def test_save_window_names_the_acc_sync_caveat(self):
        source = (COMMAND_DIR / "SavePresetWindow.xaml").read_text(encoding="utf-8")
        self.assertIn("Sync to Central", source)
        self.assertIn("Desktop Connector", source)


class TagAlignIronPythonTests(unittest.TestCase):
    def test_command_scripts_avoid_python3_only_constructs(self):
        failures = []
        for path in sorted(COMMAND_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))

            if re.search(r"(?<![A-Za-z0-9_])[fF]['\"]", source):
                failures.append("{0} appears to contain an f-string".format(path.name))

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "dataclasses":
                    failures.append("{0} imports dataclasses".format(path.name))

                if isinstance(node, ast.ClassDef):
                    for decorator in node.decorator_list:
                        if getattr(decorator, "id", None) == "dataclass":
                            failures.append(
                                "{0} uses @dataclass on {1}".format(path.name, node.name)
                            )

                if isinstance(node, ast.AnnAssign):
                    failures.append("{0} uses variable annotations".format(path.name))

                if isinstance(node, ast.FunctionDef):
                    if node.returns is not None:
                        failures.append("{0} uses return annotations".format(path.name))
                    for arg in list(node.args.args) + list(node.args.kwonlyargs):
                        if arg.annotation is not None:
                            failures.append(
                                "{0} uses argument annotations".format(path.name)
                            )

                if isinstance(node, ast.JoinedStr):
                    failures.append("{0} uses f-strings".format(path.name))

        self.assertEqual([], failures)

    def test_state_module_stays_free_of_revit_imports(self):
        # The state module is the part the test suite loads standalone; a Revit
        # import here would make every test in test_tag_align_state unrunnable.
        source = (COMMAND_DIR / "tag_align_state.py").read_text(encoding="utf-8")
        for forbidden in ("import clr", "from pyrevit", "Autodesk.Revit"):
            self.assertNotIn(forbidden, source)

    def test_ui_module_stays_free_of_revit_api_imports(self):
        source = UI_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Autodesk.Revit", source)
        self.assertNotIn("import clr", source)


if __name__ == "__main__":
    unittest.main()
