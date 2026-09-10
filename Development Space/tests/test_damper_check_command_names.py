"""Damper Check's bundle, XAML wiring and the pins that keep it read-only."""

import ast
import pathlib
import re
import struct
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Damper Check.pushbutton"
UI_MODULE = COMMAND_DIR / "damper_check_ui.py"
STATE_MODULE = COMMAND_DIR / "damper_check_state.py"
SETTINGS_MODULE = COMMAND_DIR / "damper_check_settings.py"
LIB_DIR = REPO_ROOT / "lib" / "easybim"
REVIT_MODULE = LIB_DIR / "duct_network_revit.py"
SHARED_MODULES = ("duct_network_revit.py", "duct_network_state.py", "type_checklist.py",
                  "local_settings.py", "check_windows.py")
# The shared bridged window reaches these by name on every results XAML.
REQUIRED_RESULTS_CONTROLS = ("StatusText", "RefreshButton", "EmptyText", "ContentScroll")
SCRIPT = COMMAND_DIR / "script.py"

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged", "TextChanged")

WINDOW_CLASSES = {
    "DamperCheckSetupWindow.xaml": "SetupWindow",
    "DamperCheckResultsWindow.xaml": "ResultsWindow",
}

CONTROL_ATTRIBUTE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

# Inherited from System.Windows.Window / forms.WPFWindow, so they are not
# x:Name lookups and must not be demanded of the XAML.
WINDOW_MEMBERS = frozenset(
    [
        "Activate", "Close", "Closed", "Closing", "Content", "Cursor", "DialogResult",
        "Dispatcher", "Focus", "Height", "Hide", "Icon", "IsEnabled", "IsVisible",
        "Left", "Owner", "Show", "ShowActivated", "ShowDialog", "Tag", "Title",
        "Top", "Topmost", "Width", "WindowState", "OnPropertyChanged",
    ]
)


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


def _class_control_attributes(path, class_name):
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


def _png_size(path):
    with open(str(path), "rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", header[16:24])


def _module_constant(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    return None


class DamperCheckBundleTests(unittest.TestCase):
    def test_the_button_sits_on_the_misc_tools_panel(self):
        self.assertTrue(COMMAND_DIR.is_dir())
        self.assertTrue(SCRIPT.is_file())

    def test_bundle_carries_the_two_line_title_and_author(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn('title: "Damper\\nCheck"', bundle)
        self.assertIn("tooltip:", bundle)
        self.assertIn("author: Ruiming Liu", bundle)

    def test_the_tooltip_states_what_is_written(self):
        """The scan is read-only and says so, but the set-aside list is stored
        in the model - a tooltip that still promised "nothing is changed"
        would be the tool lying about the one write it makes."""
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("changes nothing", bundle)
        self.assertIn("Ignore", bundle)
        self.assertIn("stored in", bundle)
        self.assertNotIn("nothing in the model is changed", bundle)

    def test_both_icon_variants_exist_at_96_by_96(self):
        for name in ("icon.png", "icon.dark.png"):
            path = COMMAND_DIR / name
            self.assertTrue(path.exists(), str(path))
            self.assertEqual(_png_size(path), (96, 96), name)

    def test_the_in_model_store_is_the_only_writer(self):
        """One module opens a transaction, and it touches one hidden data
        element. The scan modules are pinned transaction-free above."""
        store = LIB_DIR / "model_store.py"
        self.assertTrue(store.exists())
        source = store.read_text(encoding="utf-8")
        self.assertIn("Transaction(", source)
        self.assertIn("DataStorage", source)
        for name in SHARED_MODULES:
            self.assertNotIn("Transaction(", (LIB_DIR / name).read_text(encoding="utf-8"), name)

    def test_expected_modules_exist(self):
        for name in ("script.py", "damper_check_state.py", "damper_check_settings.py",
                     "damper_check_ui.py"):
            self.assertTrue((COMMAND_DIR / name).exists(), name)
        # The scanner and the plumbing live in lib, shared with Fire Damper Check.
        self.assertFalse((COMMAND_DIR / "damper_check_revit.py").exists())
        for name in SHARED_MODULES:
            self.assertTrue((LIB_DIR / name).exists(), name)

    def test_the_readme_documents_the_tool(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Damper Check (Misc Tools)", readme)


class DamperCheckXamlTests(unittest.TestCase):
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
            self.assertIsNotNone(methods, "no class %s in damper_check_ui" % class_name)
            missing = _xaml_handlers(_xaml_root(xaml_name)) - methods
            self.assertFalse(
                missing, "%s: %s is missing %s" % (xaml_name, class_name, missing)
            )

    def test_every_control_the_code_touches_exists_in_the_xaml(self):
        """``self.SomeButton`` with no x:Name is an AttributeError on open."""
        for xaml_name, class_name in WINDOW_CLASSES.items():
            names = _xaml_names(_xaml_root(xaml_name))
            for attribute in _class_control_attributes(UI_MODULE, class_name):
                self.assertIn(
                    attribute,
                    names,
                    "%s uses self.%s but %s has no such x:Name"
                    % (class_name, attribute, xaml_name),
                )

    def test_the_chip_controls_exist_in_the_setup_window(self):
        names = _xaml_names(_xaml_root("DamperCheckSetupWindow.xaml"))
        chips = _module_constant(UI_MODULE, "CHIP_CONTROLS")
        self.assertTrue(chips)
        for _key, control_name in chips:
            self.assertIn(control_name, names)

    def test_the_setup_window_carries_the_agreed_controls(self):
        names = _xaml_names(_xaml_root("DamperCheckSetupWindow.xaml"))
        required = {
            "ScopeModelRadio", "ScopeViewRadio", "SupplyCheck", "ReturnCheck",
            "ExhaustCheck", "OtherCheck", "UnknownCheck", "TypeSearchBox", "TypePanel",
            "TypeCountText", "TypeAllButton", "TypeNoneButton", "ThresholdBox",
            "StatusText", "CheckButton", "CancelButton",
        }
        self.assertFalse(required - names, "missing x:Name(s): %s" % (required - names))

    def test_the_results_window_carries_what_the_shared_window_touches(self):
        names = _xaml_names(_xaml_root("DamperCheckResultsWindow.xaml"))
        for name in REQUIRED_RESULTS_CONTROLS:
            self.assertIn(name, names)

    def test_the_results_window_carries_the_agreed_controls(self):
        names = _xaml_names(_xaml_root("DamperCheckResultsWindow.xaml"))
        required = {
            "SearchBox", "ThresholdBox", "ThresholdDownButton", "ThresholdUpButton",
            "SummaryText", "ContentScroll", "ContentPanel", "EmptyText", "StatusText",
            "RefreshButton", "ExpandAllButton", "CollapseAllButton", "CloseButton",
        }
        self.assertFalse(required - names, "missing x:Name(s): %s" % (required - names))

    def test_check_is_the_default_and_cancel_is_the_escape(self):
        source = (COMMAND_DIR / "DamperCheckSetupWindow.xaml").read_text(encoding="utf-8")
        check = source.split('x:Name="CheckButton"')[1].split("/>")[0]
        self.assertIn('IsDefault="True"', check)
        cancel = source.split('x:Name="CancelButton"')[1].split("/>")[0]
        self.assertIn('IsCancel="True"', cancel)

    def test_the_results_close_button_is_the_escape(self):
        source = (COMMAND_DIR / "DamperCheckResultsWindow.xaml").read_text(encoding="utf-8")
        close = source.split('x:Name="CloseButton"')[1].split("/>")[0]
        self.assertIn('IsCancel="True"', close)

    def test_star_rows_carry_a_min_height(self):
        for name in WINDOW_CLASSES:
            root = _xaml_root(name)
            for element in root.iter():
                if element.tag.endswith("RowDefinition") and element.attrib.get("Height") == "*":
                    self.assertIn("MinHeight", element.attrib, name)


class DamperCheckIronPythonTests(unittest.TestCase):
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
                    if node.args.kwonlyargs:
                        failures.append("{0} uses keyword-only arguments".format(path.name))

                if isinstance(node, ast.JoinedStr):
                    failures.append("{0} uses f-strings".format(path.name))

                if isinstance(node, ast.Nonlocal):
                    failures.append("{0} uses nonlocal".format(path.name))

        self.assertEqual([], failures)

    def test_state_and_settings_modules_stay_free_of_revit_imports(self):
        for path in (STATE_MODULE, SETTINGS_MODULE):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("import clr", "Autodesk.Revit"):
                self.assertNotIn(forbidden, source, path.name)
            # pyRevit may only be reached lazily, inside a function, the way
            # tag_align_presets.local_preset_path does - never at import time.
            tree = ast.parse(source)
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual((node.module or "").split(".")[0], "pyrevit", path.name)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name.split(".")[0], "pyrevit", path.name)

    def test_the_state_module_never_names_pyrevit_at_all(self):
        self.assertNotIn("pyrevit", STATE_MODULE.read_text(encoding="utf-8"))

    def test_ui_module_stays_free_of_revit_api(self):
        for path in (UI_MODULE, LIB_DIR / "check_windows.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("Autodesk.Revit", source, path.name)
            self.assertNotIn("import clr", source, path.name)
            # Every model read belongs to the scanner, reached only through
            # the callables the launcher hands in.
            self.assertNotIn("duct_network_revit", source, path.name)
            self.assertNotIn("damper_check_revit", source, path.name)

    def test_the_revit_module_never_opens_a_transaction(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Transaction(", source)
        self.assertNotIn("TransactionGroup", source)
        self.assertNotIn("SubTransaction", source)

    def test_no_module_writes_to_the_model(self):
        paths = sorted(COMMAND_DIR.glob("*.py")) + [LIB_DIR / name for name in SHARED_MODULES]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("Transaction(", source, path.name)


class DamperCheckLauncherTests(unittest.TestCase):
    def test_the_launcher_keeps_a_persistent_engine(self):
        self.assertTrue(_module_constant(SCRIPT, "__persistentengine__"))

    def test_the_active_envvar_matches_between_script_and_ui(self):
        self.assertEqual(_module_constant(SCRIPT, "ACTIVE_ENVVAR"),
                         _module_constant(UI_MODULE, "ACTIVE_ENVVAR"))

    def test_the_launcher_drops_stale_modules_behind_the_active_flag(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("def _drop_stale_modules", source)
        self.assertIn("script.get_envvar(ACTIVE_ENVVAR)", source)
        stale = _module_constant(SCRIPT, "STALE_MODULES")
        for name in ("easybim.model_store",
                     "damper_check_ui", "damper_check_state", "damper_check_settings",
                     "easybim.duct_network_revit", "easybim.duct_network_state",
                     "easybim.type_checklist", "easybim.local_settings",
                     "easybim.check_windows", "easybim.external_events"):
            self.assertIn(name, stale)

    def test_the_bridge_is_created_inside_the_command_run(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('external_events.ExternalEventBridge("EasyBIM Damper Check")', source)
        self.assertIn("bridge.create()", source)

    def test_the_launcher_refuses_family_documents(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("forms.check_modeldoc(exitscript=True)", source)
        self.assertIn("IsFamilyDocument", source)


if __name__ == "__main__":
    unittest.main()
