import ast
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (
    REPO_ROOT / "EasyBIM.tab" / "Sheet.panel" / "Sheet Manager.pushbutton"
)

MAIN_XAML = COMMAND_DIR / "SheetManagerWindow.xaml"
DIALOG_XAMLS = [
    "ApplyResultsDialog.xaml",
    "LoadFromSourceDialog.xaml",
    "FilterByRevisionDialog.xaml",
    "FilterByParameterDialog.xaml",
    "SortDialog.xaml",
    "SavePrintSetDialog.xaml",
    "CopySheetInfoDialog.xaml",
    "SearchReplaceDialog.xaml",
]

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "TextChanged", "SelectionChanged",
                 "BeginningEdit", "CellEditEnding", "Checked",
                 "Unchecked")


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


def _module_methods(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    methods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            methods.add(node.name)
    return methods


class SheetManagerBundleTests(unittest.TestCase):
    def test_panel_and_bundle_exist(self):
        self.assertTrue(COMMAND_DIR.is_dir())
        self.assertFalse(
            (REPO_ROOT / "EasyBIM.tab" / "Print.panel").exists())

    def test_bundle_yaml_title_and_version(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Sheet", bundle)
        self.assertIn("Manager", bundle)
        self.assertIn("min_revit_version: 2023", bundle)
        self.assertIn("author: Ruiming Liu", bundle)

    def test_all_xaml_files_parse(self):
        for xaml_name in [MAIN_XAML.name] + DIALOG_XAMLS:
            path = COMMAND_DIR / xaml_name
            self.assertTrue(path.exists(), xaml_name)
            ET.parse(str(path))

    def test_main_window_named_controls(self):
        root = ET.parse(str(MAIN_XAML)).getroot()
        names = _xaml_names(root)
        required = {
            "search_tb", "sheets_dg", "status_tb", "source_tb",
            "loadall_b", "loadsheetlist_b", "loadprintset_b",
            "filterrev_b", "filterparam_b", "sort_b",
            "addtbparam_b", "addsheetparam_b",
            "export_b", "import_b", "copysheetinfo_b",
            "searchreplace_b", "saveprintset_b", "selecttblocks_b",
            "apply_b",
        }
        missing = required - names
        self.assertFalse(missing, "missing x:Name(s): %s" % missing)

    def test_main_window_handlers_exist_in_ui_module(self):
        root = ET.parse(str(MAIN_XAML)).getroot()
        handlers = _xaml_handlers(root)
        methods = _module_methods(COMMAND_DIR / "sheet_manager_ui.py")
        missing = handlers - methods
        self.assertFalse(missing, "missing handler(s): %s" % missing)

    def test_dialog_handlers_exist_in_dialogs_module(self):
        methods = _module_methods(
            COMMAND_DIR / "sheet_manager_dialogs.py")
        for xaml_name in DIALOG_XAMLS:
            root = ET.parse(str(COMMAND_DIR / xaml_name)).getroot()
            missing = _xaml_handlers(root) - methods
            self.assertFalse(
                missing,
                "%s: missing handler(s): %s" % (xaml_name, missing))

    def test_scripts_stay_ironpython27_safe(self):
        # No f-strings; keep the py2-compatible idiom the runtime needs.
        for path in sorted(COMMAND_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertFalse(
                re.search(r"(?<![A-Za-z0-9_])[fF]['\"]", source),
                "%s appears to contain an f-string" % path.name)
            ast.parse(source)

    def test_revision_template_binds_generated_attrs_only(self):
        source = (COMMAND_DIR / "sheet_manager_ui.py")\
            .read_text(encoding="utf-8")
        self.assertIn("XamlReader", source)
        self.assertNotIn("Click=", source.split("_CHECKBOX_CELL_TEMPLATE")[1]
                         .split("')")[0])


if __name__ == "__main__":
    unittest.main()
