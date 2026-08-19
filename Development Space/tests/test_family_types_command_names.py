"""Bundle, XAML and IronPython wiring checks for Family Types.

Modelled on ``test_families_transfer_command_names.py``.  These catch the
drift a syntax check cannot: a handler renamed in the code but not the XAML, a
control renamed in the XAML but not the code, a Python-3-only construct
IronPython 2.7 will refuse at runtime, and the design decisions that would be
silently wrong rather than loudly broken if a refactor undid them.
"""

import ast
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (REPO_ROOT / "EasyBIM.tab" / "Family.panel"
               / "Family Types.pushbutton")
SCRIPT_MODULE = COMMAND_DIR / "script.py"
STATE_MODULE = COMMAND_DIR / "family_types_state.py"
REVIT_MODULE = COMMAND_DIR / "family_types_revit.py"
UI_MODULE = COMMAND_DIR / "family_types_ui.py"
XLSX_MODULE = COMMAND_DIR / "family_types_xlsx.py"
LIB_DIR = REPO_ROOT / "lib" / "easybim"
LOAD_OPTIONS_MODULE = LIB_DIR / "family_load_options.py"

MAIN_XAML = COMMAND_DIR / "FamilyTypesWindow.xaml"
IMPORT_XAML = COMMAND_DIR / "ImportTypesDialog.xaml"

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged",
                 "TextChanged", "BeginningEdit", "CellEditEnding")

WINDOWS = (
    (MAIN_XAML, "FamilyTypesWindow"),
    (IMPORT_XAML, "ImportTypesDialog"),
)

EXPECTED_FILES = (
    "bundle.yaml",
    "icon.png",
    "icon.dark.png",
    "script.py",
    "family_types_state.py",
    "family_types_revit.py",
    "family_types_ui.py",
    "family_types_xlsx.py",
    "FamilyTypesWindow.xaml",
    "ImportTypesDialog.xaml",
)


def _xaml_root(path):
    return ET.parse(str(path)).getroot()


def _xaml_names(root):
    return set(element.attrib[X_NAME] for element in root.iter()
               if X_NAME in element.attrib)


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
    for node in ast.walk(tree):
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
    """``self.<name>`` reads that look like a XAML control, not a CLR member."""
    node = _class_node(path, class_name)
    attributes = set()
    if node is None:
        return attributes
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute):
            continue
        value = child.value
        if isinstance(value, ast.Name) and value.id == "self":
            if re.match(r"^[a-z][a-z0-9]*_(tb|b|dg|cb)$", child.attr):
                attributes.add(child.attr)
    return attributes


def _module_functions(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return set(node.name for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef))


def _code_without_prose(path):
    """Source with docstrings and comments stripped.

    A decision named only in a docstring is documentation; these tests are
    about what the code does.
    """
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"#.*", "", source)
    source = re.sub(r'"""(?:.|\n)*?"""', "", source)
    source = re.sub(r"'''(?:.|\n)*?'''", "", source)
    return source


class BundleLayoutTests(unittest.TestCase):
    def test_the_button_lives_in_the_family_panel_with_everything_it_needs(self):
        self.assertTrue(COMMAND_DIR.is_dir(), COMMAND_DIR)
        for name in EXPECTED_FILES:
            self.assertTrue((COMMAND_DIR / name).is_file(),
                            "missing %s" % name)

    def test_the_bundle_declares_a_two_line_title_and_a_version_floor(self):
        text = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Family", text)
        self.assertIn("Types", text)
        self.assertIn("min_revit_version", text)

    def test_both_icons_are_the_house_size(self):
        # 96x96 RGBA is what every recent button ships; a wrong size renders
        # blurry on the ribbon rather than failing outright.
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        for name in ("icon.png", "icon.dark.png"):
            with Image.open(str(COMMAND_DIR / name)) as image:
                self.assertEqual((96, 96), image.size, name)


class WindowWiringTests(unittest.TestCase):
    def test_every_xaml_handler_exists_on_its_window_class(self):
        for xaml_path, class_name in WINDOWS:
            handlers = _xaml_handlers(_xaml_root(xaml_path))
            members = _class_members(UI_MODULE, class_name)
            self.assertIsNotNone(members, "no class %s" % class_name)
            missing = sorted(handlers - members)
            self.assertEqual([], missing,
                             "%s: %s" % (xaml_path.name, missing))

    def test_every_control_the_code_reads_exists_in_its_xaml(self):
        for xaml_path, class_name in WINDOWS:
            names = _xaml_names(_xaml_root(xaml_path))
            used = _class_control_attributes(UI_MODULE, class_name)
            missing = sorted(used - names)
            self.assertEqual([], missing,
                             "%s: %s" % (xaml_path.name, missing))

    def test_the_parsed_cell_style_carries_no_event_attributes(self):
        # XamlReader.Parse builds the cell styles at runtime; an event
        # attribute in a loose fragment has no code-behind to bind to and
        # throws when the grid renders.
        source = UI_MODULE.read_text(encoding="utf-8")
        style = source[source.index("_CELL_STYLE"):source.index(
            "def _parse_fragment")]
        for attr in HANDLER_ATTRS:
            self.assertNotIn('%s="' % attr, style)


class DesignDecisionTests(unittest.TestCase):
    def test_the_state_and_xlsx_layers_stay_importable_off_revit(self):
        # The desktop suite loads them standalone; a pyrevit or clr import
        # would make every state test unrunnable.
        for path in (STATE_MODULE, XLSX_MODULE):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("from pyrevit", "import pyrevit", "import clr",
                              "from Autodesk"):
                self.assertNotIn(forbidden, source, path.name)

    def test_instance_parameters_are_columns_and_are_marked(self):
        # A FamilyType stores a value for every family parameter, instance
        # ones included - that per-type value is the default a new instance
        # is placed with, so dropping them would lose real data.
        source = STATE_MODULE.read_text(encoding="utf-8")
        self.assertIn("INSTANCE_SUFFIX", source)
        self.assertIn("(default)", source)
        self.assertIn("is_instance", _code_without_prose(REVIT_MODULE))

    def test_the_export_header_carries_the_parameter_element_id(self):
        # Name over id in one cell: the id is the round-trip key, and it is on
        # the visible sheet so a reader can see what an import matched on.
        body = _code_without_prose(STATE_MODULE)
        self.assertIn('u"{0}\\n{1}".format(column.header, column.param_id)',
                      body)
        self.assertIn("def parse_header_cell", body)
        # text_wrap is what makes the second line visible in Excel.
        self.assertIn("text_wrap", _code_without_prose(XLSX_MODULE))

    def test_deletes_and_renames_are_planned_before_new_types(self):
        # Both free a name; creating first would collide on a swap.
        body = _code_without_prose(REVIT_MODULE)
        apply_at = body.index("def apply_changes")
        section = body[apply_at:]
        self.assertLess(section.index("changes.deletes"),
                        section.index("changes.new_types"))
        self.assertLess(section.index("changes.renames"),
                        section.index("changes.new_types"))

    def test_a_new_type_is_seeded_from_an_existing_one(self):
        # FamilyManager.NewType copies the current type's values into the type
        # it creates. A row shown blank in the grid would therefore come out
        # of Revit carrying whatever the current type held - so the window
        # seeds a new row from a real type and writes every value back.
        body = _code_without_prose(UI_MODULE)
        self.assertIn("def _add_row_from", body)
        self.assertIn("_first_surviving_row", body)
        self.assertIn("self._add_row_from(u\"New Type\"", body)

    def test_the_family_picker_reuses_the_shared_collectors(self):
        # A second family-collection implementation is the drift the
        # Conventions section warns about.
        source = SCRIPT_MODULE.read_text(encoding="utf-8")
        self.assertIn(
            "from easybim.family_selection_revit import collect_families",
            source)
        self.assertIn(
            "from easybim.family_selection_revit import edit_family", source)
        self.assertIn("forms.SelectFromList.show",
                      _code_without_prose(SCRIPT_MODULE))

    def test_a_family_can_never_be_left_without_a_type(self):
        source = STATE_MODULE.read_text(encoding="utf-8")
        self.assertIn("def surviving_type_count", source)
        self.assertIn("A family must keep at least one type.", source)

    def test_formula_driven_and_reporting_parameters_are_read_only(self):
        # Revit computes them; a value written over one is either refused or
        # silently recomputed, and the grid should say so instead.
        body = _code_without_prose(STATE_MODULE)
        self.assertIn("is_determined_by_formula", body)
        self.assertIn("is_reporting", body)
        self.assertIn("user_modifiable", body)

    def test_the_reload_asks_the_user_rather_than_deciding_for_them(self):
        # Load Parameters fixes overwriteParameterValues to False because its
        # job must not touch project values. Here changing those values IS the
        # job, so the question goes to the user - via the shared prompt.
        source = SCRIPT_MODULE.read_text(encoding="utf-8")
        self.assertIn(
            "from easybim.family_load_options import FamilyLoadOptions",
            source)
        self.assertIn(
            "from easybim.family_load_options import build_overwrite_prompt",
            source)
        self.assertIn("FamilyLoadOptions(ask=build_overwrite_prompt())",
                      _code_without_prose(SCRIPT_MODULE))
        self.assertTrue(LOAD_OPTIONS_MODULE.is_file())

    def test_a_family_the_user_already_had_open_is_not_closed(self):
        # EditFamily hands back the document the user is editing; closing it
        # would end their session.
        body = _code_without_prose(SCRIPT_MODULE)
        self.assertIn("_family_document_is_open", body)
        self.assertIn("if not was_already_open:", body)

    def test_the_family_document_is_never_saved_to_disk(self):
        for path in (SCRIPT_MODULE, REVIT_MODULE):
            body = _code_without_prose(path)
            self.assertNotIn(".SaveAs(", body, path.name)
            self.assertNotIn(".Save(", body, path.name)

    def test_import_stages_into_the_grid_instead_of_writing(self):
        # Nothing reaches Revit from the import path; Apply is the only door.
        body = _code_without_prose(STATE_MODULE)
        self.assertIn("def apply_import_selection", body)
        self.assertNotIn("Transaction", body)

    def test_the_window_is_modal_and_owns_no_revit_events(self):
        # A persistent engine is only for buttons that keep Revit event
        # delegates alive after the command returns; this one does not.
        source = SCRIPT_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("__persistentengine__", source)
        self.assertIn("ShowDialog", UI_MODULE.read_text(encoding="utf-8"))


class IronPythonCompatibilityTests(unittest.TestCase):
    def test_command_scripts_avoid_python3_only_constructs(self):
        failures = []
        paths = sorted(COMMAND_DIR.glob("*.py")) + [LOAD_OPTIONS_MODULE]
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

    def test_the_launcher_defines_main_and_guards_it(self):
        self.assertIn("main", _module_functions(SCRIPT_MODULE))
        self.assertIn('if __name__ == "__main__":',
                      SCRIPT_MODULE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
