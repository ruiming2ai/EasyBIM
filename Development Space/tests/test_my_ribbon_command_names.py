"""Bundle, XAML and IronPython wiring checks for My Ribbon, plus the design
decisions that would be silently wrong rather than loudly broken if a refactor
undid them.  Modelled on ``test_families_transfer_command_names.py``."""

import ast
import io
import pathlib
import re
import struct
import tokenize
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PANEL_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel"
COMMAND_DIR = PANEL_DIR / "My Ribbon.pushbutton"
SCRIPT_MODULE = COMMAND_DIR / "script.py"
UI_MODULE = COMMAND_DIR / "my_ribbon_ui.py"
STATE_MODULE = COMMAND_DIR / "my_ribbon_state.py"
HOST_MODULE = COMMAND_DIR / "my_ribbon_host.py"
LIB_MODULE = REPO_ROOT / "lib" / "easybim" / "my_ribbon.py"
IDLING_MODULE = REPO_ROOT / "lib" / "easybim" / "idling.py"
STARTUP_MODULE = REPO_ROOT / "startup.py"
SPEC_DIR = REPO_ROOT / "Development Space" / "docs" / "superpowers" / "specs"

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged",
                 "SelectedItemChanged", "TextChanged", "MouseDoubleClick",
                 "PreviewKeyDown", "CellEditEnding")

WINDOW_CLASSES = {
    "MyRibbonWindow.xaml": "MyRibbonWindow",
    "SourceSelectionWindow.xaml": "SourceSelectionWindow",
    "ButtonSelectionWindow.xaml": "ButtonSelectionWindow",
    "DestinationWindow.xaml": "DestinationWindow",
    "ImportPreviewWindow.xaml": "ImportPreviewWindow",
    "CredentialsWindow.xaml": "CredentialsWindow",
}

EXPECTED_MODULES = (
    "script.py",
    "my_ribbon_state.py",
    "my_ribbon_host.py",
    "my_ribbon_ui.py",
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
    """Source with comments and string literals blanked out, offsets kept."""
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


def _png_size(path):
    with open(str(path), "rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", header[16:24])


class MyRibbonBundleTests(unittest.TestCase):
    def test_command_folder_sits_in_the_misc_tools_panel(self):
        self.assertTrue(COMMAND_DIR.is_dir())

    def test_bundle_yaml_carries_the_ribbon_title(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn('title: "My\\nRibbon"', bundle)
        self.assertIn("tooltip:", bundle)

    def test_both_icon_variants_exist_at_the_house_size(self):
        for name in ("icon.png", "icon.dark.png"):
            icon = COMMAND_DIR / name
            self.assertTrue(icon.is_file(), name)
            self.assertGreater(icon.stat().st_size, 0, name)
            self.assertEqual(_png_size(icon), (96, 96), name)

    def test_icon_sources_live_beside_the_spec(self):
        self.assertTrue((SPEC_DIR / "2026-08-15-my-ribbon-icon.svg").is_file())
        self.assertTrue((SPEC_DIR / "2026-08-15-my-ribbon-icon.dark.svg").is_file())

    def test_expected_modules_exist(self):
        for name in EXPECTED_MODULES:
            self.assertTrue((COMMAND_DIR / name).is_file(), name)
        self.assertTrue(LIB_MODULE.is_file())


class MyRibbonXamlTests(unittest.TestCase):
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
        lowercase = re.compile(r"^[a-z][a-z0-9_]*_(tb|cb|btn|box|panel|border|radio|label)$")
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

    def test_every_star_row_carries_a_min_height(self):
        # 2026-08-11 decision: an unconstrained star row shrinks to zero and
        # paints its child outside the cell when the window is dragged short.
        for xaml_name in WINDOW_CLASSES:
            root = _xaml_root(xaml_name)
            for element in root.iter():
                if not element.tag.endswith("RowDefinition"):
                    continue
                height = element.attrib.get("Height", "")
                if height.endswith("*"):
                    self.assertIn("MinHeight", element.attrib,
                                  "%s has a star row without MinHeight" % xaml_name)


class MyRibbonContractTests(unittest.TestCase):
    def test_sources_are_installed_where_pyrevit_updates_them(self):
        # The whole point of the design: a linked repository is a normal
        # pyRevit extension under the default third-party folder, so
        # pyRevit > Update (and EasyBIM Auto Update) pull it and Reload
        # rebuilds it.  My Ribbon never runs an updater of its own.
        host = _code_without_prose(HOST_MODULE)
        self.assertIn("THIRDPARTY_EXTENSIONS_DEFAULT_DIR", host)
        for path in (SCRIPT_MODULE, HOST_MODULE, UI_MODULE, STATE_MODULE, LIB_MODULE):
            source = _code_without_prose(path)
            for forbidden in ("git_pull", "Commands.Pull", "update_pyrevit", "git_fetch"):
                self.assertNotIn(forbidden, source, "%s: %s" % (path.name, forbidden))

    def test_no_shelling_out(self):
        for path in (SCRIPT_MODULE, HOST_MODULE, UI_MODULE, STATE_MODULE, LIB_MODULE):
            source = _code_without_prose(path)
            self.assertNotIn("subprocess", source, path.name)
            self.assertNotIn("git.exe", source, path.name)

    def test_placements_share_the_live_ribbon_item(self):
        # Same mechanism as modify_ribbon: the item object itself is added to
        # the destination panel's Source.Items - nothing is written to disk
        # and no wrapper bundle is generated.
        lib = _code_without_prose(LIB_MODULE)
        self.assertIn("_add_item(items, item)", lib)
        for forbidden in ("bundle.yaml", "execfile", "makedirs(", "copyfile", "copytree"):
            self.assertNotIn(forbidden, _function_source(LIB_MODULE, "apply"), forbidden)

    def test_the_pyrevit_tab_is_never_hidden(self):
        lib = LIB_MODULE.read_text(encoding="utf-8")
        self.assertIn('PROTECTED_TABS = ("pyrevit",)', lib)
        self.assertIn("PROTECTED_TABS", _function_source(LIB_MODULE, "_apply_tab_visibility"))

    def test_apply_takes_back_its_previous_additions_first(self):
        body = _function_source(LIB_MODULE, "apply")
        self.assertLess(body.index("_undo_previous("), body.index("_ensure_destination("))
        self.assertIn("_store_mirror(new_mirror)", body)

    def test_registry_is_written_beside_and_swapped(self):
        body = _function_source(LIB_MODULE, "save_registry")
        self.assertIn('".tmp"', body)
        self.assertIn("os.rename(", body)

    def test_a_newer_settings_file_is_refused_not_half_read(self):
        body = _function_source(LIB_MODULE, "read_registry")
        self.assertIn("RegistryFormatError", body)
        self.assertIn("version > FORMAT_VERSION", body)

    def test_startup_queues_the_apply_for_the_first_idle_tick(self):
        # startup.py runs while later extensions are still loading; their
        # tabs are not on the ribbon yet.
        startup = _code_without_prose(STARTUP_MODULE)
        self.assertIn("my_ribbon.queue_startup_apply()", startup)
        self.assertNotIn("my_ribbon.apply", startup)
        idling = _function_source(IDLING_MODULE, "_run_consumers")
        self.assertIn("_run_my_ribbon_apply", idling)
        self.assertLess(idling.index("_run_my_ribbon_apply"), idling.index("_run_auto_update"))

    def test_reload_only_after_the_window_closed_and_only_from_the_main_loop(self):
        script = _code_without_prose(SCRIPT_MODULE)
        self.assertEqual(script.count("host.reload_pyrevit()"), 1)
        run = _function_source(SCRIPT_MODULE, "_run")
        self.assertIn("host.reload_pyrevit()", run)
        self.assertLess(run.index("window.ShowDialog()"), run.index("host.reload_pyrevit()"))
        self.assertNotIn("reload_pyrevit", _function_source(SCRIPT_MODULE, "_apply"))
        self.assertNotIn("reload_pyrevit", _code_without_prose(UI_MODULE))

    def test_folders_are_deleted_only_when_my_ribbon_installed_them(self):
        body = _function_source(HOST_MODULE, "remove_installed_source")
        self.assertIn("installed_by_my_ribbon", body)
        self.assertLess(body.index("installed_by_my_ribbon"), body.index("remove_tree("))
        self.assertIn("_is_inside(target, root)", body)
        host = _code_without_prose(HOST_MODULE)
        self.assertEqual(host.count("shutil.rmtree("), 1)
        for path in (SCRIPT_MODULE, UI_MODULE, STATE_MODULE, LIB_MODULE):
            self.assertNotIn("rmtree", _code_without_prose(path), path.name)

    def test_a_download_lands_only_when_complete(self):
        # cloned under the temp folder, planned, then renamed into place;
        # a cancelled or failed download leaves no half repository where
        # pyRevit would try to load it
        script = _function_source(SCRIPT_MODULE, "_download_git")
        self.assertIn("temp_clone_root", _function_source(SCRIPT_MODULE, "_clone_with_progress"))
        self.assertIn("plan_clone_destination", script)
        self.assertLess(script.index("plan_clone_destination"), script.index("host.move_into_place("))
        self.assertNotIn("os.rename", script)
        self.assertIn("cleanup_temp_root", script)
        self.assertIn("CloneCancelled", script)
        self.assertIn("CloneAuthError", script)

    def test_downloads_are_cancellable_and_probed_first(self):
        clone = _function_source(SCRIPT_MODULE, "_clone_with_progress")
        self.assertIn("cancellable=True", clone)
        self.assertIn("progress_bar.cancelled", clone)
        self.assertIn("probe_url", _function_source(SCRIPT_MODULE, "_download_git"))

    def test_credentials_are_never_written_anywhere(self):
        for path in (SCRIPT_MODULE, HOST_MODULE, UI_MODULE, STATE_MODULE, LIB_MODULE):
            source = _code_without_prose(path)
            self.assertNotIn("password_file", source, path.name)
            self.assertNotIn("keyring", source, path.name)
        # the credentials window hands them back and nothing stores them
        self.assertNotIn("credentials", _function_source(STATE_MODULE, "export_document"))
        self.assertNotIn("password", STATE_MODULE.read_text(encoding="utf-8"))

    def test_the_manager_stages_and_apply_saves(self):
        apply_body = _function_source(SCRIPT_MODULE, "_apply")
        self.assertIn("my_ribbon.save_registry(working)", apply_body)
        self.assertIn("my_ribbon.apply(working)", apply_body)
        self.assertLess(apply_body.index("save_registry"), apply_body.index("my_ribbon.apply("))
        # nothing else saves
        self.assertEqual(_code_without_prose(SCRIPT_MODULE).count("save_registry("), 1)
        self.assertNotIn("save_registry", _code_without_prose(UI_MODULE))


class MyRibbonIronPythonTests(unittest.TestCase):
    def test_command_scripts_avoid_python3_only_constructs(self):
        failures = []
        paths = sorted(COMMAND_DIR.glob("*.py")) + [LIB_MODULE]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            if re.search(r"(?<![A-Za-z0-9_])[fF]['\"]", source):
                failures.append("%s appears to contain an f-string" % path.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "dataclasses":
                    failures.append("%s imports dataclasses" % path.name)
                if isinstance(node, ast.ClassDef):
                    for decorator in node.decorator_list:
                        if getattr(decorator, "id", None) == "dataclass":
                            failures.append("%s uses @dataclass on %s" % (path.name, node.name))
                if isinstance(node, ast.AnnAssign):
                    failures.append("%s uses variable annotations" % path.name)
                if isinstance(node, ast.FunctionDef):
                    if node.returns is not None:
                        failures.append("%s uses return annotations" % path.name)
                    for arg in list(node.args.args) + list(node.args.kwonlyargs):
                        if arg.annotation is not None:
                            failures.append("%s uses argument annotations" % path.name)
                    if node.args.kwonlyargs:
                        failures.append("%s uses keyword-only arguments in %s"
                                        % (path.name, node.name))
                if isinstance(node, ast.JoinedStr):
                    failures.append("%s uses f-strings" % path.name)
        self.assertEqual([], failures)

    def test_no_python3_only_stdlib_names(self):
        for path in sorted(COMMAND_DIR.glob("*.py")) + [LIB_MODULE]:
            source = _code_without_prose(path)
            for forbidden in ("pathlib", "importlib", "print(", "nonlocal ",
                              "yield from", "async ", "await "):
                if forbidden == "print(":
                    # print_function is imported where print is used
                    if "print(" in source:
                        self.assertIn("print_function", path.read_text(encoding="utf-8"), path.name)
                    continue
                self.assertNotIn(forbidden, source, "%s: %s" % (path.name, forbidden))

    def test_none_is_never_read_as_an_attribute(self):
        for path in sorted(COMMAND_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\.None\b", source), path.name)

    def test_state_module_stays_free_of_revit_imports(self):
        source = STATE_MODULE.read_text(encoding="utf-8")
        for forbidden in ("import clr", "from pyrevit", "Autodesk.Revit", "import os",
                          "import shutil", "import io"):
            self.assertNotIn(forbidden, source)

    def test_ui_module_stays_free_of_revit_api_imports(self):
        source = UI_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Autodesk.Revit", source)
        self.assertNotIn("import clr", source)
        self.assertNotIn("my_ribbon_host", source)

    def test_host_module_stays_free_of_revit_api_and_wpf(self):
        source = HOST_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Autodesk.Revit", source)
        self.assertNotIn("from pyrevit import forms", source)
        self.assertNotIn("Windows.Controls", source)

    def test_lib_module_loads_without_pyrevit(self):
        # startup.py imports it in every session; a hard pyRevit dependency
        # at import time would take EasyBIM's startup down with it.
        source = LIB_MODULE.read_text(encoding="utf-8")
        self.assertIn("try:\n    from pyrevit import script\nexcept Exception:\n    script = None", source)


if __name__ == "__main__":
    unittest.main()
