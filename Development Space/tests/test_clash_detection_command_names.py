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

SETUP_XAML = UI_DIR / "clash_detection_setup.xaml"
PANEL_XAML = UI_DIR / "clash_detection_panel.xaml"
PANEL_WINDOW_XAML = UI_DIR / "clash_detection_panel_window.xaml"
ALERT_XAML = UI_DIR / "clash_detection_alert.xaml"

SETUP_MODULE = LIB_DIR / "clash_detection_setup.py"
PANEL_MODULE = LIB_DIR / "clash_detection_panel.py"
ALERT_MODULE = LIB_DIR / "clash_detection_alert.py"
RIBBON_MODULE = LIB_DIR / "clash_detection_ribbon.py"

CLASH_MODULES = [
    LIB_DIR / "clash_detection_state.py",
    LIB_DIR / "clash_detection_engine.py",
    PANEL_MODULE,
    ALERT_MODULE,
    SETUP_MODULE,
    RIBBON_MODULE,
    LIB_DIR / "clash_detection_revit.py",
    LIB_DIR / "wpf_notify.py",
]

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


WPF_NS = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"


def _template_buttons(path):
    """Buttons living inside a DataTemplate, i.e. repeated per row."""
    buttons = []
    for template in ET.parse(str(path)).getroot().iter(WPF_NS + "DataTemplate"):
        buttons.extend(list(template.iter(WPF_NS + "Button")))
    return buttons


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
        for path in sorted(COMMAND_DIR.glob("*.py")) + CLASH_MODULES:
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

    def test_handlers_exist_in_setup_module(self):
        missing = _xaml_handlers(SETUP_XAML) - _module_methods(SETUP_MODULE)
        self.assertFalse(missing, "missing handler(s): %s" % missing)

    def test_setup_lives_in_lib_so_the_panel_can_open_it(self):
        # The dockable panel offers Edit Categories, and it cannot import
        # upward out of lib/ into the pushbutton folder.
        self.assertTrue(SETUP_MODULE.exists())
        self.assertTrue(SETUP_XAML.exists())
        self.assertFalse((COMMAND_DIR / "clash_detection_ui.py").exists())
        self.assertFalse((COMMAND_DIR / "ClashDetectionSetupWindow.xaml").exists())
        panel_source = PANEL_MODULE.read_text(encoding="utf-8")
        self.assertIn("clash_detection_setup", panel_source)

    def test_apply_label_used_when_editing_a_live_session(self):
        source = SETUP_MODULE.read_text(encoding="utf-8")
        self.assertIn('APPLY_LABEL = "Apply Category Changes"', source)
        self.assertIn("def request_edit_categories", source)

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
        attrs = _assigned_attrs(SETUP_MODULE)
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
            "is_a_checked",
            "is_b_checked",
            "element_a_title",
            "element_a_detail",
            "element_b_title",
            "element_b_detail",
        }
        for path in (PANEL_XAML, PANEL_WINDOW_XAML, ALERT_XAML):
            self.assertEqual(_xaml_bindings(path), expected, path.name)
        attrs = _assigned_attrs(PANEL_MODULE)
        self.assertTrue(expected.issubset(attrs), expected - attrs)

    def test_rows_are_ticked_and_shown_together(self):
        # A clash has two elements and users review several at a time, so the
        # per-row Show button gave way to checkboxes plus one Show.
        for path in (PANEL_XAML, PANEL_WINDOW_XAML, ALERT_XAML):
            source = path.read_text(encoding="utf-8")
            self.assertIn('x:Name="ShowButton"', source, path.name)
            self.assertIn('x:Name="SelectAllButton"', source, path.name)
            self.assertIn('x:Name="SelectNoneButton"', source, path.name)
            # One checkbox per element, not per pair: a clash names two
            # elements and you often want only one of them.
            self.assertIn("{Binding is_a_checked, Mode=TwoWay}", source, path.name)
            self.assertIn("{Binding is_b_checked, Mode=TwoWay}", source, path.name)
            # The row template must carry no button of its own any more: Show
            # lives once, in the footer, and acts on every ticked row.
            self.assertFalse(
                _template_buttons(path), "%s still has a per-row button" % path.name
            )

    def test_pause_and_resume_on_every_surface(self):
        for path in (PANEL_XAML, PANEL_WINDOW_XAML, ALERT_XAML):
            source = path.read_text(encoding="utf-8")
            self.assertIn('x:Name="PauseButton"', source, path.name)
            self.assertIn('x:Name="ResumeButton"', source, path.name)

    def test_resume_ships_disabled(self):
        # Resume must not look clickable until the mode is actually paused.
        for path in (PANEL_XAML, PANEL_WINDOW_XAML, ALERT_XAML):
            source = path.read_text(encoding="utf-8")
            resume_block = source.split('x:Name="ResumeButton"')[1].split("/>")[0]
            self.assertIn('IsEnabled="False"', resume_block, path.name)

    def test_panel_offers_edit_categories(self):
        for path in (PANEL_XAML, PANEL_WINDOW_XAML):
            self.assertIn(
                'x:Name="EditCategoriesButton"',
                path.read_text(encoding="utf-8"),
                path.name,
            )


class MainWindowSessionTests(unittest.TestCase):
    """The main window is the one place that always has a way out.

    A closed panel used to be unrecoverable, so these controls have to be on
    the window the ribbon button opens - and it must open it unconditionally.
    """

    def test_ribbon_always_opens_the_main_window(self):
        source = (COMMAND_DIR / "script.py").read_text(encoding="utf-8")
        self.assertIn("clash_detection_setup.show_setup_window", source)
        self.assertNotIn("clash_detection_status", source)

    def test_status_window_is_gone(self):
        self.assertFalse((LIB_DIR / "clash_detection_status.py").exists())
        self.assertFalse((UI_DIR / "clash_detection_status.xaml").exists())

    def test_session_controls_exist(self):
        required = {
            "SessionPanel",
            "SessionStateText",
            "SessionDetailText",
            "OpenPanelButton",
            "SessionPauseButton",
            "SessionResumeButton",
            "SessionStopButton",
        }
        missing = required - _xaml_names(SETUP_XAML)
        self.assertFalse(missing, "missing x:Name(s): %s" % missing)

    def test_session_controls_start_hidden_and_resume_disabled(self):
        source = SETUP_XAML.read_text(encoding="utf-8")
        panel_block = source.split('x:Name="SessionPanel"')[1].split(">")[0]
        self.assertIn('Visibility="Collapsed"', panel_block)
        resume_block = source.split('x:Name="SessionResumeButton"')[1].split("/>")[0]
        self.assertIn('IsEnabled="False"', resume_block)

    def test_description_and_removed_hint(self):
        source = SETUP_XAML.read_text(encoding="utf-8")
        self.assertIn(
            'Text="Watch for clashes created while you keep modeling."', source
        )
        module = SETUP_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Tick at least one category", module)
        self.assertNotIn("Tick at least one category", source)


class RibbonBadgeTests(unittest.TestCase):
    def test_aliases_match_the_bundle_title(self):
        # Same coupling test_view_template_ribbon.py guards: renaming the
        # button without updating the aliases silently loses the badge.
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        title = bundle.split("title:", 1)[1].splitlines()[0].strip().strip('"')
        collapsed = " ".join(title.replace("\\n", " ").split()).lower()
        source = RIBBON_MODULE.read_text(encoding="utf-8")
        aliases = re.findall(r'"([^"]*Clash[^"]*)"', source)
        normalised = set(
            " ".join(alias.replace("\\n", " ").split()).lower() for alias in aliases
        )
        self.assertIn(collapsed, normalised)

    def test_states_cover_running_and_paused(self):
        source = RIBBON_MODULE.read_text(encoding="utf-8")
        self.assertIn('STATE_ON = "on"', source)
        self.assertIn('STATE_PAUSED = "paused"', source)
        self.assertIn("def set_state", source)


if __name__ == "__main__":
    unittest.main()
