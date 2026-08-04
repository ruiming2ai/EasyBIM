import ast
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PULLDOWN_DIR = (REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" /
                "Circuiting.pulldown")
COMMAND_DIR = PULLDOWN_DIR / "Update Circuit Rating.pushbutton"
UI_MODULE = COMMAND_DIR / "circuit_rating_ui.py"

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged", "TextChanged")

WINDOW_CLASSES = {
    "UpdateCircuitRatingWindow.xaml": "UpdateCircuitRatingWindow",
}

CONTROL_ATTRIBUTE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

# Inherited from System.Windows.Window / forms.WPFWindow, so they are not
# x:Name lookups and must not be demanded of the XAML.
WINDOW_MEMBERS = frozenset(
    [
        "Activate", "Close", "Content", "Cursor", "DialogResult", "Focus",
        "Height", "Hide", "Icon", "IsEnabled", "Left", "Owner", "Show",
        "ShowDialog", "Tag", "Title", "Top", "Topmost", "Width", "WindowState",
        "OnPropertyChanged",
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


class CircuitingBundleTests(unittest.TestCase):
    def test_the_pulldown_sits_in_misc_tools(self):
        self.assertTrue(PULLDOWN_DIR.is_dir())
        self.assertTrue(COMMAND_DIR.is_dir())

    def test_pulldown_bundle_names_the_menu(self):
        bundle = (PULLDOWN_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("title: Circuiting", bundle)
        self.assertIn("author: Ruiming Liu", bundle)

    def test_command_bundle_carries_the_ribbon_title(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Update Circuit", bundle)
        self.assertIn("Rating", bundle)
        self.assertIn("author: Ruiming Liu", bundle)

    def test_both_icon_variants_exist_on_both_levels(self):
        for folder in (PULLDOWN_DIR, COMMAND_DIR):
            for name in ("icon.png", "icon.dark.png"):
                path = folder / name
                self.assertTrue(path.exists(), str(path))
                self.assertGreater(path.stat().st_size, 0, str(path))

    def test_expected_modules_exist(self):
        for name in ("script.py", "circuit_rating_state.py",
                     "circuit_rating_revit.py", "circuit_rating_ui.py"):
            self.assertTrue((COMMAND_DIR / name).exists(), name)


class CircuitRatingXamlTests(unittest.TestCase):
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
            self.assertIsNotNone(methods, "no class %s in circuit_rating_ui" % class_name)
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

    def test_the_window_carries_the_agreed_controls(self):
        names = _xaml_names(_xaml_root("UpdateCircuitRatingWindow.xaml"))
        required = {
            "SourceParamCombo", "TargetParamList", "CircuitsGrid", "CountText",
            "AllButton", "NoneButton", "UpdateButton", "CancelButton",
        }
        self.assertFalse(required - names, "missing x:Name(s): %s" % (required - names))

    def test_the_circuit_checkbox_binds_two_way(self):
        source = (COMMAND_DIR / "UpdateCircuitRatingWindow.xaml").read_text(
            encoding="utf-8")
        self.assertIn(
            "IsChecked=\"{Binding is_selected, Mode=TwoWay, "
            "UpdateSourceTrigger=PropertyChanged}\"",
            source,
        )

    def test_update_is_the_default_and_cancel_is_the_escape(self):
        source = (COMMAND_DIR / "UpdateCircuitRatingWindow.xaml").read_text(
            encoding="utf-8")
        update = source.split('x:Name="UpdateButton"')[1].split("/>")[0]
        self.assertIn('IsDefault="True"', update)
        cancel = source.split('x:Name="CancelButton"')[1].split("/>")[0]
        self.assertIn('IsCancel="True"', cancel)


class CircuitRatingIronPythonTests(unittest.TestCase):
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
        # The state module is what the unit tests load standalone.
        source = (COMMAND_DIR / "circuit_rating_state.py").read_text(encoding="utf-8")
        for forbidden in ("import clr", "from pyrevit", "Autodesk.Revit"):
            self.assertNotIn(forbidden, source)

    def test_ui_module_stays_free_of_revit_api_imports(self):
        source = UI_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Autodesk.Revit", source)
        self.assertNotIn("import clr", source)
        # Every model read/write belongs to circuit_rating_revit.
        self.assertNotIn("circuit_rating_revit", source)


if __name__ == "__main__":
    unittest.main()
