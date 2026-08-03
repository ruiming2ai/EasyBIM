import ast
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (
    REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Clash Detection Mode.pushbutton"
)
LIB_DIR = REPO_ROOT / "lib" / "easybim"
UI_DIR = LIB_DIR / "ui"

SETUP_XAML = COMMAND_DIR / "ClashDetectionSetupWindow.xaml"
PANEL_XAML = UI_DIR / "clash_detection_panel.xaml"
PANEL_WINDOW_XAML = UI_DIR / "clash_detection_panel_window.xaml"
ALERT_XAML = UI_DIR / "clash_detection_alert.xaml"

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = (
    "Click",
    "TextChanged",
    "SelectionChanged",
    "Checked",
    "Unchecked",
    "PreviewKeyDown",
    "KeyDown",
)


def _xaml_names(path):
    names = set()
    for element in ET.parse(str(path)).getroot().iter():
        name = element.attrib.get(X_NAME)
        if name:
            names.add(name)
    return names


def _xaml_handlers(path):
    handlers = set()
    for element in ET.parse(str(path)).getroot().iter():
        for attr in HANDLER_ATTRS:
            value = element.attrib.get(attr)
            if value:
                handlers.add(value)
    return handlers


def _xaml_bindings(path):
    source = path.read_text(encoding="utf-8")
    return set(re.findall(r"\{Binding ([A-Za-z_][A-Za-z0-9_]*)", source))


def _module_methods(path):
    methods = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef):
            methods.add(node.name)
    return methods


def _assigned_attrs(path):
    """Every ``self.<name> = ...`` target in a module."""
    attrs = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute):
                attrs.add(target.attr)
    return attrs


class BundleTests(unittest.TestCase):
    def test_bundle_lives_in_misc_tools(self):
        self.assertTrue(COMMAND_DIR.is_dir())
        for required in ("script.py", "bundle.yaml", "icon.png", "icon.dark.png"):
            self.assertTrue((COMMAND_DIR / required).exists(), required)

    def test_bundle_yaml_metadata(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Clash Detection", bundle)
        self.assertIn("author: Ruiming Liu", bundle)
        self.assertIn("min_revit_version: 2023", bundle)

    def test_script_keeps_the_engine_alive(self):
        # The engine holds live Revit event delegates after the command
        # returns; a recycled engine would silently stop detection.
        source = (COMMAND_DIR / "script.py").read_text(encoding="utf-8")
        self.assertIn("__persistentengine__ = True", source)

    def test_scripts_stay_ironpython27_safe(self):
        paths = sorted(COMMAND_DIR.glob("*.py")) + [
            LIB_DIR / "clash_detection_state.py",
            LIB_DIR / "clash_detection_engine.py",
            LIB_DIR / "clash_detection_panel.py",
            LIB_DIR / "clash_detection_alert.py",
            LIB_DIR / "wpf_notify.py",
        ]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            self.assertFalse(
                re.search(r"(?<![A-Za-z0-9_])[fF]['\"]", source),
                "%s appears to contain an f-string" % path.name,
            )
            ast.parse(source)


class SetupWindowTests(unittest.TestCase):
    def test_xaml_parses(self):
        ET.parse(str(SETUP_XAML))

    def test_named_controls(self):
        required = {
            "LeftModelCombo",
            "RightModelCombo",
            "LeftCategoryList",
            "RightCategoryList",
            "SilentModeCheck",
            "StatusText",
            "StartButton",
            "CancelButton",
        }
        missing = required - _xaml_names(SETUP_XAML)
        self.assertFalse(missing, "missing x:Name(s): %s" % missing)

    def test_handlers_exist_in_ui_module(self):
        missing = _xaml_handlers(SETUP_XAML) - _module_methods(
            COMMAND_DIR / "clash_detection_ui.py"
        )
        self.assertFalse(missing, "missing handler(s): %s" % missing)

    def test_both_lists_allow_multi_select(self):
        # Shift-click ranges and press-and-drag selection are the whole
        # reason this dialog exists instead of the native one.
        source = SETUP_XAML.read_text(encoding="utf-8")
        self.assertEqual(source.count('SelectionMode="Extended"'), 2)

    def test_start_button_replaces_ok(self):
        source = SETUP_XAML.read_text(encoding="utf-8")
        self.assertIn('Content="Start Ongoing Detection Mode"', source)
        self.assertNotIn('Content="OK"', source)

    def test_silent_mode_checkbox_wording(self):
        source = SETUP_XAML.read_text(encoding="utf-8")
        self.assertIn(
            'Content="Silent Mode and Update Clash on Dynamic Panel"', source
        )

    def test_category_rows_bind_to_row_attributes(self):
        bindings = _xaml_bindings(SETUP_XAML)
        self.assertEqual(bindings, {"is_checked", "name"})
        attrs = _assigned_attrs(COMMAND_DIR / "clash_detection_ui.py")
        self.assertTrue(bindings.issubset(attrs), bindings - attrs)

    def test_checkbox_and_label_are_separate_hit_targets(self):
        # A CheckBox carrying the label would swallow the row click and
        # break range selection, so the name lives in its own TextBlock.
        source = SETUP_XAML.read_text(encoding="utf-8")
        self.assertNotIn('<CheckBox Content="{Binding name}"', source)
        self.assertIn('<TextBlock Text="{Binding name}"', source)


class PanelTests(unittest.TestCase):
    def test_xaml_files_parse(self):
        for path in (PANEL_XAML, PANEL_WINDOW_XAML, ALERT_XAML):
            self.assertTrue(path.exists(), path.name)
            ET.parse(str(path))

    def test_panel_is_a_usercontrol_and_fallback_is_a_window(self):
        self.assertTrue(
            PANEL_XAML.read_text(encoding="utf-8").lstrip().startswith("<UserControl")
        )
        self.assertTrue(
            PANEL_WINDOW_XAML.read_text(encoding="utf-8").lstrip().startswith("<Window")
        )

    def test_panel_and_fallback_expose_the_same_controls(self):
        # One module binds both views, so their contracts cannot drift.
        self.assertEqual(_xaml_names(PANEL_XAML), _xaml_names(PANEL_WINDOW_XAML))
        self.assertEqual(
            _xaml_handlers(PANEL_XAML), _xaml_handlers(PANEL_WINDOW_XAML)
        )

    def test_panel_named_controls(self):
        required = {
            "HeaderText",
            "ScopeText",
            "RowsListView",
            "EmptyText",
            "StatusText",
            "StopButton",
        }
        missing = required - _xaml_names(PANEL_XAML)
        self.assertFalse(missing, "missing x:Name(s): %s" % missing)

    def test_panel_offers_stop_detection(self):
        self.assertIn(
            'Content="Stop Detection"', PANEL_XAML.read_text(encoding="utf-8")
        )

    def test_panel_handlers_exist_in_panel_module(self):
        methods = _module_methods(LIB_DIR / "clash_detection_panel.py")
        for path in (PANEL_XAML, PANEL_WINDOW_XAML):
            missing = _xaml_handlers(path) - methods
            self.assertFalse(missing, "%s: missing %s" % (path.name, missing))

    def test_alert_handlers_exist_in_alert_module(self):
        missing = _xaml_handlers(ALERT_XAML) - _module_methods(
            LIB_DIR / "clash_detection_alert.py"
        )
        self.assertFalse(missing, "missing handler(s): %s" % missing)

    def test_alert_offers_stop_and_silent_mode(self):
        source = ALERT_XAML.read_text(encoding="utf-8")
        self.assertIn('Content="Stop Detection"', source)
        self.assertIn('Content="Silent Mode"', source)

    def test_row_bindings_match_the_row_class(self):
        expected = {
            "element_a_title",
            "element_a_detail",
            "element_b_title",
            "element_b_detail",
        }
        for path in (PANEL_XAML, PANEL_WINDOW_XAML, ALERT_XAML):
            self.assertEqual(_xaml_bindings(path), expected, path.name)
        attrs = _assigned_attrs(LIB_DIR / "clash_detection_panel.py")
        self.assertTrue(expected.issubset(attrs), expected - attrs)

    def test_every_row_carries_a_show_button(self):
        for path in (PANEL_XAML, PANEL_WINDOW_XAML, ALERT_XAML):
            self.assertIn(
                'Content="Show"', path.read_text(encoding="utf-8"), path.name
            )


if __name__ == "__main__":
    unittest.main()
