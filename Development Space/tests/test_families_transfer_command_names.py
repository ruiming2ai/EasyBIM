"""Bundle, XAML and IronPython wiring checks for Families Transfer.

Modelled on ``test_linked_sheets_copy_command_names.py``.  These catch the
drift a syntax check cannot: a handler renamed in the code but not the XAML, a
control renamed in the XAML but not the code, a Python-3-only construct
IronPython 2.7 will refuse at runtime, and the design decisions that would be
silently wrong rather than loudly broken if a refactor undid them.
"""

import ast
import io
import pathlib
import re
import tokenize
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PANEL_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel"
COMMAND_DIR = PANEL_DIR / "Families Transfer.pushbutton"
SCRIPT_MODULE = COMMAND_DIR / "script.py"
UI_MODULE = COMMAND_DIR / "families_transfer_ui.py"
STATE_MODULE = COMMAND_DIR / "families_transfer_state.py"
REVIT_MODULE = COMMAND_DIR / "families_transfer_revit.py"
COPY_PASTE_MODULE = REPO_ROOT / "lib" / "easybim" / "copy_paste.py"
LINKED_SHEETS_REVIT = (REPO_ROOT / "EasyBIM.tab" / "Sheet.panel"
                       / "Linked Sheets Copy.pushbutton"
                       / "linked_sheets_copy_revit.py")

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged",
                 "TextChanged", "PreviewKeyDown", "CellEditEnding")

WINDOW_CLASSES = {
    "SourceSelectionWindow.xaml": "SourceSelectionWindow",
    "FamilySelectionWindow.xaml": "FamilySelectionWindow",
    "LinkSelectionWindow.xaml": "LinkSelectionWindow",
    "TargetSelectionWindow.xaml": "TargetSelectionWindow",
    "ActionWindow.xaml": "ActionWindow",
}

EXPECTED_MODULES = (
    "script.py",
    "families_transfer_state.py",
    "families_transfer_revit.py",
    "families_transfer_ui.py",
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


def _code_without_prose(path):
    """Source with comments and string literals blanked out.

    These contract tests assert on what the code *does*. The modules explain
    the same API facts in their own docstrings - naming
    ``RevitUIFamilyLoadOptions`` to say it is *not* usable, for instance -
    which would otherwise satisfy the very assertions written to catch its
    use. Offsets are preserved so line numbers still line up.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except Exception:
        return source

    for token in tokens:
        if token.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (start_row, start_col), (end_row, end_col) = token.start, token.end
        for row in range(start_row, end_row + 1):
            line = lines[row - 1]
            begin = start_col if row == start_row else 0
            finish = end_col if row == end_row else len(line)
            lines[row - 1] = line[:begin] + (" " * (finish - begin)) + line[finish:]
    return "".join(lines)


def _function_source(path, function_name):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            end = getattr(node, "end_lineno", None) or len(lines)
            return u"\n".join(lines[node.lineno - 1:end])
    return u""


class FamiliesTransferBundleTests(unittest.TestCase):
    def test_command_folder_sits_in_the_misc_tools_panel(self):
        self.assertTrue(COMMAND_DIR.is_dir())

    def test_bundle_yaml_carries_the_ribbon_title(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Families", bundle)
        self.assertIn("Transfer", bundle)

    def test_both_icon_variants_exist(self):
        for name in ("icon.png", "icon.dark.png"):
            icon = COMMAND_DIR / name
            self.assertTrue(icon.is_file(), name)
            self.assertGreater(icon.stat().st_size, 0, name)

    def test_expected_modules_exist(self):
        for name in EXPECTED_MODULES:
            self.assertTrue((COMMAND_DIR / name).is_file(), name)


class FamiliesTransferXamlTests(unittest.TestCase):
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

    def test_every_lowercase_control_has_a_matching_x_name(self):
        # CONTROL_ATTRIBUTE only matches ^[A-Z], so the snake_case controls
        # this tool uses for its TextBlocks and CheckBoxes are invisible to
        # the check below. A typo in one of those fails only inside Revit.
        lowercase = re.compile(r"^[a-z][a-z0-9_]*_(tb|cb|btn|box|panel)$")
        for xaml_name, class_name in sorted(WINDOW_CLASSES.items()):
            names = _xaml_names(_xaml_root(xaml_name))
            members = _class_members(UI_MODULE, class_name) or set()
            node = _class_node(UI_MODULE, class_name)
            self.assertIsNotNone(node, class_name)
            for child in ast.walk(node):
                if not isinstance(child, ast.Attribute):
                    continue
                value = child.value
                if not (isinstance(value, ast.Name) and value.id == "self"):
                    continue
                if not lowercase.match(child.attr) or child.attr in members:
                    continue
                self.assertIn(
                    child.attr, names,
                    "%s uses self.%s but %s has no such x:Name"
                    % (class_name, child.attr, xaml_name))

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


class FamiliesTransferContractTests(unittest.TestCase):
    def test_a_linked_file_is_never_opened_from_disk(self):
        # The whole promise of the Revit Links source is that the linked .rvt
        # stays shut: GetLinkDocument already hands back a live Document.
        # "Fixing" a refusal by opening the file would silently upgrade an
        # older model to the running Revit version, which is why Load
        # Parameters reads headers with BasicFileInfo instead.
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("OpenDocumentFile", source)
        self.assertNotIn("OpenAndActivateDocument", source)

    def test_no_link_document_is_ever_closed(self):
        # "Transfer & Close All .rfa" walks a list of documents and closes
        # them.  A link document must never be reachable from that list.
        body = _function_source(REVIT_MODULE, "close_open_family_documents")
        self.assertIn("IsLinked", body)

    def test_link_families_are_edited_from_their_own_document(self):
        # A Family element belongs to the document that holds it; calling
        # EditFamily on the active project with a link's Family throws.
        body = _function_source(REVIT_MODULE, "resolve_family")
        self.assertIn("source_document", body)

    def test_transfer_and_export_can_be_cancelled(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        for name in ("transfer_families", "export_families"):
            body = _function_source(REVIT_MODULE, name)
            self.assertIn("progress", body, name)
        self.assertIn("cancelled", source)
        self.assertIn("ProgressBar", SCRIPT_MODULE.read_text(encoding="utf-8"))

    def test_a_name_clash_always_resolves_in_favour_of_the_destination(self):
        # DuplicateTypeAction has exactly two members - UseDestinationTypes
        # and Abort - verified against the assembly metadata for 2021-2026.
        # There is no overwrite on the copy path, so the only thing worth
        # asserting is that the handler never aborts: returning Abort would
        # cancel the whole paste mid-batch.
        source = COPY_PASTE_MODULE.read_text(encoding="utf-8")
        self.assertIn("IDuplicateTypeNamesHandler", source)
        self.assertIn("DuplicateTypeAction.UseDestinationTypes", source)
        self.assertNotIn("DuplicateTypeAction.Abort", source)

    def test_every_cross_document_copy_shares_one_options_factory(self):
        # AGENTS.md records this as repo-wide: a new copy call site must not
        # be able to forget the duplicate-type handler.
        self.assertTrue(COPY_PASTE_MODULE.is_file())
        self.assertEqual(
            COPY_PASTE_MODULE.read_text(encoding="utf-8")
            .count("DB.CopyPasteOptions()"), 1)
        for path in (REVIT_MODULE, LINKED_SHEETS_REVIT):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("DB.CopyPasteOptions()", source, path.name)
            self.assertIn("from easybim.copy_paste import copy_paste_options",
                          source, path.name)

    def test_revit_is_asked_before_a_family_is_overwritten(self):
        # RevitUIFamilyLoadOptions, which the LoadFamily docs name, is not an
        # instantiable type in any shipped version - the static accessor is
        # the only way to Revit's own prompt.
        source = _code_without_prose(REVIT_MODULE)
        self.assertIn("GetRevitUIFamilyLoadOptions", source)
        # Not the bare constructor - GetRevitUIFamilyLoadOptions() ends with
        # that same substring, so the boundary matters.
        self.assertIsNone(
            re.search(r"(?<![A-Za-z0-9_])RevitUIFamilyLoadOptions\(", source))
        # and there is still a silent answer for UI-less mode
        self.assertIn("FamilyTransferLoadOptions", source)

    def test_a_declined_overwrite_is_not_reported_as_a_failure(self):
        body = _function_source(REVIT_MODULE, "_load_family_document_into_targets")
        self.assertIn("_is_declined_overwrite", body)
        self.assertIn("summary.skipped.append", body)

    def test_export_asks_before_it_replaces_files_on_disk(self):
        source = _code_without_prose(SCRIPT_MODULE)
        self.assertIn("_pick_export_folder_confirming_overwrites", source)
        self.assertIn("build_export_overwrite_text", source)

    def test_the_link_source_never_stages_a_file_on_disk(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        for forbidden in ("import tempfile", "import shutil"):
            self.assertNotIn(forbidden, source)


class FamiliesTransferIronPythonTests(unittest.TestCase):
    def test_command_scripts_avoid_python3_only_constructs(self):
        failures = []
        paths = sorted(COMMAND_DIR.glob("*.py")) + [COPY_PASTE_MODULE]
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

    def test_ui_module_stays_free_of_revit_api_imports(self):
        source = UI_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Autodesk.Revit", source)
        self.assertNotIn("import clr", source)


if __name__ == "__main__":
    unittest.main()
