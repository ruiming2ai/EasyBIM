"""Bundle, XAML and IronPython wiring checks for Families Downgrade.

Modelled on ``test_families_transfer_command_names.py``: a handler renamed in
the code but not the XAML, a control renamed in the XAML but not the code, a
Python-3-only construct IronPython 2.7 will refuse at runtime, and the design
decisions that would be silently wrong rather than loudly broken if a
refactor undid them.
"""

import ast
import io
import pathlib
import re
import tokenize
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PANEL_DIR = REPO_ROOT / "EasyBIM.tab" / "Family.panel"
COMMAND_DIR = PANEL_DIR / "Families Downgrade.pushbutton"
SCRIPT_MODULE = COMMAND_DIR / "script.py"
UI_MODULE = COMMAND_DIR / "families_downgrade_ui.py"
STATE_MODULE = COMMAND_DIR / "families_downgrade_state.py"
REVIT_MODULE = COMMAND_DIR / "families_downgrade_revit.py"
EXPORT_MODULE = COMMAND_DIR / "families_downgrade_export.py"
REBUILD_MODULE = COMMAND_DIR / "families_downgrade_rebuild.py"
LIB_DIR = REPO_ROOT / "lib" / "easybim"

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged",
                 "TextChanged", "PreviewKeyDown", "CellEditEnding")

WINDOW_CLASSES = {
    "ModeWindow.xaml": "ModeWindow",
    "ExportOptionsWindow.xaml": "ExportOptionsWindow",
    "RebuildWindow.xaml": "RebuildWindow",
}

EXPECTED_MODULES = (
    "script.py",
    "families_downgrade_state.py",
    "families_downgrade_revit.py",
    "families_downgrade_export.py",
    "families_downgrade_rebuild.py",
    "families_downgrade_ui.py",
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
    return set(element.attrib[X_NAME] for element in root.iter() if X_NAME in element.attrib)


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


def _self_attributes(path, class_name, pattern):
    node = _class_node(path, class_name)
    attributes = set()
    if node is None:
        return attributes
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            value = child.value
            if isinstance(value, ast.Name) and value.id == "self" and pattern.match(child.attr):
                attributes.add(child.attr)
    return attributes


def _code_without_prose(path):
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


def _method_source(path, class_name, method_name):
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    node = _class_node(path, class_name)
    if node is None:
        return u""
    for child in node.body:
        if isinstance(child, ast.FunctionDef) and child.name == method_name:
            end = getattr(child, "end_lineno", None) or len(lines)
            return u"\n".join(lines[child.lineno - 1:end])
    return u""


class FamiliesDowngradeBundleTests(unittest.TestCase):
    def test_command_folder_sits_in_the_family_panel(self):
        self.assertTrue(COMMAND_DIR.is_dir())

    def test_bundle_yaml_carries_title_author_and_the_version_gate(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Families", bundle)
        self.assertIn("Downgrade", bundle)
        self.assertIn("author: Ruiming Liu", bundle)
        # 2021 is the whole point of the tool, and its API is the floor of
        # every ladder in the rebuild module.
        self.assertIn("min_revit_version: 2021", bundle)
        # Rebuild needs no document: Revit 2021 straight after launch.
        self.assertIn("context: zero-doc", bundle)

    def test_both_icon_variants_exist(self):
        for name in ("icon.png", "icon.dark.png"):
            icon = COMMAND_DIR / name
            self.assertTrue(icon.is_file(), name)
            self.assertGreater(icon.stat().st_size, 0, name)

    def test_expected_modules_exist(self):
        for name in EXPECTED_MODULES:
            self.assertTrue((COMMAND_DIR / name).is_file(), name)

    def test_the_selection_pages_come_from_lib(self):
        script = SCRIPT_MODULE.read_text(encoding="utf-8")
        for name in ("easybim.family_selection_revit", "easybim.family_selection_state",
                     "easybim.family_selection_ui"):
            self.assertIn(name, script)
        for xaml in ("SourceSelectionWindow.xaml", "FamilySelectionWindow.xaml",
                     "LinkSelectionWindow.xaml"):
            self.assertFalse((COMMAND_DIR / xaml).exists(), xaml)
        for class_name in ("SourceSelectionWindow", "FamilySelectionWindow",
                           "LinkSelectionWindow"):
            self.assertIsNone(_class_node(UI_MODULE, class_name), class_name)


class FamiliesDowngradeXamlTests(unittest.TestCase):
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
                self.assertIn(handler, members,
                              "%s references %s but %s has no such method"
                              % (xaml_name, handler, class_name))

    def test_every_control_the_code_touches_has_an_x_name(self):
        lowercase = re.compile(r"^[a-z][a-z0-9_]*_(tb|cb|btn|box|panel|rb)$")
        for xaml_name, class_name in sorted(WINDOW_CLASSES.items()):
            names = _xaml_names(_xaml_root(xaml_name))
            members = _class_members(UI_MODULE, class_name) or set()
            for pattern in (lowercase, CONTROL_ATTRIBUTE):
                for attribute in sorted(_self_attributes(UI_MODULE, class_name, pattern)):
                    if attribute in members or attribute in WINDOW_MEMBERS:
                        continue
                    self.assertIn(attribute, names,
                                  "%s uses self.%s but %s has no such x:Name"
                                  % (class_name, attribute, xaml_name))

    def test_the_mode_page_tells_the_truth_about_what_a_downgrade_is(self):
        text = (COMMAND_DIR / "ModeWindow.xaml").read_text(encoding="utf-8")
        self.assertIn("cannot be saved for an older release", text)
        self.assertIn("static", text)


class FamiliesDowngradeContractTests(unittest.TestCase):
    def test_export_writes_sat_through_a_3d_view_and_can_fall_back_to_dwg(self):
        source = _code_without_prose(EXPORT_MODULE)
        self.assertIn("SATExportOptions", source)
        self.assertIn("DWGExportOptions", source)
        self.assertIn("SolidGeometry.ACIS", source)
        self.assertIn("ACADVersion.R2007", source)
        self.assertIn("View3D.CreateIsometric", source)
        self.assertIn(".Export(", source)

    def test_an_empty_export_is_retried_at_the_other_detail_levels(self):
        body = _function_source(EXPORT_MODULE, "export_group_file")
        self.assertIn("detail_levels", body)
        self.assertIn("sat_has_bodies", body)
        self.assertIn("continue", body)

    def test_the_source_is_never_saved(self):
        for path in (EXPORT_MODULE, REVIT_MODULE):
            source = _code_without_prose(path)
            self.assertNotIn(".Save(", source, path.name)
            self.assertNotIn(".SaveAs(", source, path.name)
        # EditFamily documents are closed without saving; open .rfa documents
        # ride a transaction group that is rolled back.
        session = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("Close(False)", (LIB_DIR / "family_selection_revit.py").read_text(encoding="utf-8"))
        self.assertIn("TransactionGroup", session)
        self.assertIn("RollBack()", session)

    def test_rebuild_imports_and_turns_bodies_into_native_solids(self):
        source = _code_without_prose(REBUILD_MODULE)
        self.assertIn("SATImportOptions", source)
        self.assertIn("DWGImportOptions", source)
        self.assertIn(".Import(", source)
        self.assertIn("FreeFormElement.Create", source)
        self.assertIn("SolidUtils.SplitVolumes", source)
        self.assertIn("ImportPlacement.Origin", source)

    def test_the_import_is_checked_against_the_exported_size_and_position(self):
        body = _function_source(REBUILD_MODULE, "import_group")
        self.assertIn("size_matches", body)
        self.assertIn("IMPORT_UNIT_CANDIDATES", body)
        self.assertIn("bbox_offset", body)
        self.assertIn("MoveElement", body)

    def test_the_rebuild_saves_with_overwrite_and_closes_without_saving(self):
        source = _code_without_prose(REBUILD_MODULE)
        self.assertIn("OverwriteExistingFile = True", source)
        self.assertIn("close_family_doc(doc)", source)

    def test_warnings_are_swallowed_and_rollbacks_are_reported(self):
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("IFailuresPreprocessor", source)
        self.assertIn("DeleteWarning", source)
        self.assertIn("ProceedWithRollBack", source)
        body = _function_source(REVIT_MODULE, "run_transaction")
        self.assertIn("_committed(status)", body)
        self.assertIn("rolled the change back", body)
        self.assertIn("TransactionStatus.Committed", _function_source(REVIT_MODULE, "_committed"))

    def test_the_shared_parameter_file_is_restored_before_it_is_deleted(self):
        body = _method_source(REVIT_MODULE, "SharedParameterScratch", "close")
        self.assertIn("_restore_shared_parameters_filename", body)
        self.assertLess(body.index("_restore_shared_parameters_filename"), body.index("os.remove"))
        batch = _function_source(REBUILD_MODULE, "rebuild_family_packages")
        self.assertIn("finally:", batch)
        self.assertIn("scratch.close()", batch)

    def test_the_parameter_ladder_has_a_2021_and_a_forge_rung(self):
        body = _function_source(REBUILD_MODULE, "resolve_spec")
        self.assertIn("uses_forge", body)
        self.assertIn("UnitUtils.GetUnitType", body)
        self.assertIn("parameter_type_for", body)
        body = _function_source(REBUILD_MODULE, "resolve_group")
        self.assertIn("GroupTypeId", body)
        self.assertIn("builtin_group_for", body)

    def test_categories_the_target_lacks_go_through_the_fallback_table(self):
        body = _function_source(REBUILD_MODULE, "apply_family_record")
        self.assertIn("category_for", body)
        state_source = STATE_MODULE.read_text(encoding="utf-8")
        for name in ("OST_PlumbingEquipment", "OST_MechanicalControlDevices",
                     "OST_MedicalEquipment"):
            self.assertIn(name, state_source)

    def test_both_halves_run_behind_a_cancellable_progress_bar(self):
        script = SCRIPT_MODULE.read_text(encoding="utf-8")
        self.assertIn("ProgressBar", script)
        self.assertIn("cancellable=True", script)
        for module, name in ((EXPORT_MODULE, "export_family_packages"),
                             (REBUILD_MODULE, "rebuild_family_packages")):
            body = _function_source(module, name)
            self.assertIn("keep_going", body, name)
            self.assertIn("cancelled = True", body, name)

    def test_the_folders_are_asked_about_before_anything_is_replaced(self):
        script = _code_without_prose(SCRIPT_MODULE)
        self.assertIn("_confirm_replacements", script)
        self.assertIn("planned_package_folder_names", script)
        self.assertIn("planned_rfa_filenames", script)

    def test_a_report_is_written_next_to_the_outputs_in_both_modes(self):
        script = _code_without_prose(SCRIPT_MODULE)
        self.assertEqual(2, script.count("state.write_report("))

    def test_a_partial_package_never_takes_the_users_files_with_it(self):
        source = _code_without_prose(EXPORT_MODULE)
        self.assertNotIn("rmtree", source)
        self.assertIn("_discard_partial", source)


class FamiliesDowngradeIronPythonTests(unittest.TestCase):
    def test_command_scripts_avoid_python3_only_constructs(self):
        failures = []
        for path in sorted(COMMAND_DIR.glob("*.py")):
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
                        failures.append("%s uses keyword-only arguments" % path.name)
                if isinstance(node, ast.JoinedStr):
                    failures.append("%s uses f-strings" % path.name)
                if isinstance(node, ast.Nonlocal):
                    failures.append("%s uses nonlocal" % path.name)
        self.assertEqual([], failures)

    def test_none_is_never_read_as_an_attribute(self):
        for path in sorted(COMMAND_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\.None\b", source), path.name)

    def test_state_module_stays_free_of_revit_imports(self):
        source = STATE_MODULE.read_text(encoding="utf-8")
        for forbidden in ("import clr", "from pyrevit", "Autodesk.Revit"):
            self.assertNotIn(forbidden, source)

    def test_ui_module_stays_free_of_revit_api_imports(self):
        source = UI_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Autodesk.Revit", source)
        self.assertNotIn("import clr", source)


if __name__ == "__main__":
    unittest.main()
