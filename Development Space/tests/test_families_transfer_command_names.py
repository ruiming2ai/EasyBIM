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
LIB_DIR = REPO_ROOT / "lib" / "easybim"
UI_DIR = LIB_DIR / "ui"
COPY_PASTE_MODULE = LIB_DIR / "copy_paste.py"
LOAD_OPTIONS_MODULE = LIB_DIR / "family_load_options.py"
# The family-selection pages, collectors and link cascade are shared with
# Families Downgrade and live in lib; the checks below cover them from here
# because this bundle is where they were built and where their tests live.
SELECTION_STATE_MODULE = LIB_DIR / "family_selection_state.py"
SELECTION_UI_MODULE = LIB_DIR / "family_selection_ui.py"
SELECTION_REVIT_MODULE = LIB_DIR / "family_selection_revit.py"
SELECTION_MODULES = (SELECTION_STATE_MODULE, SELECTION_UI_MODULE,
                     SELECTION_REVIT_MODULE)
LINKED_SHEETS_REVIT = (REPO_ROOT / "EasyBIM.tab" / "Sheet.panel"
                       / "Linked Sheets Copy.pushbutton"
                       / "linked_sheets_copy_revit.py")

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged",
                 "TextChanged", "PreviewKeyDown", "CellEditEnding")

# Bundle-owned windows keep their XAML next to script.py.
WINDOW_CLASSES = {
    "TargetSelectionWindow.xaml": "TargetSelectionWindow",
    "ActionWindow.xaml": "ActionWindow",
}

# The shared pages: (XAML path, module that defines the class, class name).
SHARED_WINDOWS = (
    (UI_DIR / "family_selection_source.xaml", SELECTION_UI_MODULE,
     "SourceSelectionWindow"),
    (UI_DIR / "family_selection_families.xaml", SELECTION_UI_MODULE,
     "FamilySelectionWindow"),
    (UI_DIR / "family_selection_links.xaml", SELECTION_UI_MODULE,
     "LinkSelectionWindow"),
)


def _all_windows():
    """Every window this command opens: (XAML path, module path, class)."""
    windows = [(COMMAND_DIR / name, UI_MODULE, class_name)
               for name, class_name in sorted(WINDOW_CLASSES.items())]
    windows.extend(SHARED_WINDOWS)
    return windows

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


def _xaml_root(path):
    if not isinstance(path, pathlib.Path):
        path = COMMAND_DIR / path
    return ET.parse(str(path)).getroot()


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
        for xaml_path, _module, _class_name in _all_windows():
            _xaml_root(xaml_path)

    def test_no_stray_xaml_files(self):
        found = set(path.name for path in COMMAND_DIR.glob("*.xaml"))
        self.assertEqual(set(WINDOW_CLASSES), found)
        for xaml_path, _module, class_name in SHARED_WINDOWS:
            self.assertTrue(xaml_path.is_file(), class_name)

    def test_the_selection_pages_live_in_lib(self):
        # Shared with Families Downgrade: the pages moved to lib/easybim and the
        # bundle must not grow a second copy of them.
        for old_name in ("SourceSelectionWindow.xaml",
                         "FamilySelectionWindow.xaml",
                         "LinkSelectionWindow.xaml"):
            self.assertFalse((COMMAND_DIR / old_name).exists(), old_name)
        for path in SELECTION_MODULES:
            self.assertTrue(path.is_file(), path.name)
        script = SCRIPT_MODULE.read_text(encoding="utf-8")
        for name in ("easybim.family_selection_revit",
                     "easybim.family_selection_state",
                     "easybim.family_selection_ui"):
            self.assertIn(name, script)
        for class_name in ("SourceSelectionWindow", "FamilySelectionWindow",
                           "LinkSelectionWindow"):
            self.assertIsNone(_class_node(UI_MODULE, class_name), class_name)

    def test_every_handler_resolves_on_its_own_window_class(self):
        for xaml_path, module_path, class_name in _all_windows():
            members = _class_members(module_path, class_name)
            self.assertIsNotNone(members, class_name)
            for handler in sorted(_xaml_handlers(_xaml_root(xaml_path))):
                self.assertIn(
                    handler, members,
                    "%s references %s but %s has no such method"
                    % (xaml_path.name, handler, class_name))

    def test_every_lowercase_control_has_a_matching_x_name(self):
        # CONTROL_ATTRIBUTE only matches ^[A-Z], so the snake_case controls
        # this tool uses for its TextBlocks and CheckBoxes are invisible to
        # the check below. A typo in one of those fails only inside Revit.
        lowercase = re.compile(r"^[a-z][a-z0-9_]*_(tb|cb|btn|box|panel)$")
        for xaml_path, module_path, class_name in _all_windows():
            names = _xaml_names(_xaml_root(xaml_path))
            members = _class_members(module_path, class_name) or set()
            node = _class_node(module_path, class_name)
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
                    % (class_name, child.attr, xaml_path.name))

    def test_every_control_attribute_has_a_matching_x_name(self):
        for xaml_path, module_path, class_name in _all_windows():
            names = _xaml_names(_xaml_root(xaml_path))
            members = _class_members(module_path, class_name) or set()
            for attribute in sorted(
                    _class_control_attributes(module_path, class_name)):
                if attribute in members:
                    continue
                self.assertIn(
                    attribute, names,
                    "%s uses self.%s but %s has no such x:Name"
                    % (class_name, attribute, xaml_path.name))


class FamiliesTransferContractTests(unittest.TestCase):
    def test_a_linked_file_is_never_opened_from_disk(self):
        # The whole promise of the Revit Links source is that the linked .rvt
        # stays shut: GetLinkDocument already hands back a live Document.
        # "Fixing" a refusal by opening the file would silently upgrade an
        # older model to the running Revit version, which is why Load
        # Parameters reads headers with BasicFileInfo instead.
        for path in (REVIT_MODULE, SELECTION_REVIT_MODULE):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("OpenDocumentFile", source, path.name)
            self.assertNotIn("OpenAndActivateDocument", source, path.name)

    def test_no_link_document_is_ever_closed(self):
        # "Transfer & Close All .rfa" walks a list of documents and closes
        # them.  A link document must never be reachable from that list.
        body = _function_source(REVIT_MODULE, "close_open_family_documents")
        self.assertIn("IsLinked", body)

    def test_link_families_are_edited_from_their_own_document(self):
        # A Family element belongs to the document that holds it; calling
        # EditFamily on the active project with a link's Family throws.
        body = _function_source(SELECTION_REVIT_MODULE, "resolve_family")
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

    def test_the_user_is_asked_before_a_family_is_overwritten(self):
        # Our own prompt, not Revit's: Revit's carries the same checkbox but
        # scopes it to one LoadFamily call, and this tool loads one family
        # per call, so the tick would never cover a batch. The prompt moved to
        # lib when Family Types became its second consumer; this bundle must
        # still reach it rather than grow a copy.
        source = _code_without_prose(LOAD_OPTIONS_MODULE)
        self.assertIn("AddCommandLink", source)
        self.assertIn("VerificationText", source)
        self.assertIn("WasVerificationChecked", source)
        self.assertNotIn("GetRevitUIFamilyLoadOptions", source)
        self.assertNotIn("GetRevitUIFamilyLoadOptions",
                         _code_without_prose(REVIT_MODULE))
        self.assertIn(
            "from easybim.family_load_options import build_overwrite_prompt",
            SCRIPT_MODULE.read_text(encoding="utf-8"))
        self.assertNotIn("AddCommandLink", _code_without_prose(SCRIPT_MODULE))

    def test_the_prompt_cannot_collide_with_the_other_checkbox(self):
        # Setting VerificationText and ExtraCheckBoxText on one TaskDialog
        # makes Show() throw.
        for path in (LOAD_OPTIONS_MODULE, SCRIPT_MODULE):
            source = _code_without_prose(path)
            self.assertNotIn("ExtraCheckBoxText", source, path.name)
            self.assertNotIn("WasExtraCheckBoxChecked", source, path.name)

    def test_the_prompt_owns_its_own_title(self):
        # Without TitleAutoPrefix = False, Revit prepends the command name
        # and the window stops matching the one it is imitating.
        body = _function_source(LOAD_OPTIONS_MODULE, "build_overwrite_prompt")
        self.assertIn("TitleAutoPrefix = False", body)
        self.assertIn("Family Already Exists", body)

    def test_a_declined_overwrite_is_not_reported_as_a_failure(self):
        # The decline is now a fact our own options object records, not an
        # exception message we pattern-match.
        body = _function_source(REVIT_MODULE, "_load_family_document_into_targets")
        self.assertIn("load_options.declined", body)
        self.assertIn("summary.skipped.append", body)

    def test_one_options_object_carries_the_remembered_answer(self):
        # The apply-to-all lives on it, so building one per family would mean
        # the tick never covered anything.
        body = _function_source(REVIT_MODULE, "transfer_families")
        self.assertIn("FamilyTransferLoadOptions(ask=overwrite_prompt)", body)
        loop_at = body.index("for done, family_option in enumerate(families)")
        self.assertLess(body.index("FamilyTransferLoadOptions("), loop_at)
        # The name this bundle has always used still resolves - it is now an
        # alias for the shared class rather than a second implementation.
        revit_source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn(
            "FamilyTransferLoadOptions = family_load_options.FamilyLoadOptions",
            revit_source)
        self.assertNotIn("class FamilyTransferLoadOptions", revit_source)

    def test_export_asks_before_it_replaces_files_on_disk(self):
        source = _code_without_prose(SCRIPT_MODULE)
        self.assertIn("_pick_export_folder_confirming_overwrites", source)
        self.assertIn("build_export_overwrite_text", source)

    def test_the_link_source_never_stages_a_file_on_disk(self):
        for path in (REVIT_MODULE, SELECTION_REVIT_MODULE):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("import tempfile", "import shutil"):
                self.assertNotIn(forbidden, source, path.name)


class FamiliesTransferIronPythonTests(unittest.TestCase):
    def test_command_scripts_avoid_python3_only_constructs(self):
        failures = []
        paths = (sorted(COMMAND_DIR.glob("*.py")) + [COPY_PASTE_MODULE]
                 + list(SELECTION_MODULES))
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
        for path in sorted(COMMAND_DIR.glob("*.py")) + list(SELECTION_MODULES):
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\.None\b", source), path.name)

    def test_state_module_stays_free_of_revit_imports(self):
        # The state modules are what the test suite loads standalone.
        for path in (STATE_MODULE, SELECTION_STATE_MODULE):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("import clr", "from pyrevit", "Autodesk.Revit"):
                self.assertNotIn(forbidden, source, path.name)

    def test_ui_module_stays_free_of_revit_api_imports(self):
        for path in (UI_MODULE, SELECTION_UI_MODULE):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("Autodesk.Revit", source, path.name)
            self.assertNotIn("import clr", source, path.name)


if __name__ == "__main__":
    unittest.main()
