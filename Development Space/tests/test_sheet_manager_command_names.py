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
    "LoadCustomizedExcelDialog.xaml",
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
# lib modules the Sheet Manager owns; opted into the IronPython-2.7 gate.
LIB_MODULES = [
    REPO_ROOT / "lib" / "easybim" / "external_events.py",
    REPO_ROOT / "lib" / "easybim" / "sheet_revisions.py",
]


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
            "loadall_b", "loadcustomexcel_b", "loadsheetlist_b",
            "loadprintset_b",
            "filterrev_b", "filterparam_b", "sort_b",
            "addtbparam_b", "addsheetparam_b",
            "export_b", "import_b", "copysheetinfo_b",
            "searchreplace_b", "saveprintset_b", "selecttblocks_b",
            "apply_b", "refresh_b",
        }
        missing = required - names
        self.assertFalse(missing, "missing x:Name(s): %s" % missing)

    def test_customized_excel_button_sits_between_all_sheets_and_sheet_list(self):
        xaml = MAIN_XAML.read_text(encoding="utf-8")
        self.assertLess(xaml.index('x:Name="loadall_b"'),
                        xaml.index('x:Name="loadcustomexcel_b"'))
        self.assertLess(xaml.index('x:Name="loadcustomexcel_b"'),
                        xaml.index('x:Name="loadsheetlist_b"'))

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
        for path in sorted(COMMAND_DIR.glob("*.py")) + LIB_MODULES:
            source = path.read_text(encoding="utf-8")
            self.assertFalse(
                re.search(r"(?<![A-Za-z0-9_])[fF]['\"]", source),
                "%s appears to contain an f-string" % path.name)
            ast.parse(source)

    def test_script_keeps_the_engine_alive(self):
        # The modeless window and its ExternalEvent handler are Python
        # objects that must outlive the command run (AGENTS 2026-08-02:
        # any event-owning button needs the flag).
        source = (COMMAND_DIR / "script.py").read_text(encoding="utf-8")
        self.assertIn("__persistentengine__ = True", source)

    def test_script_drops_stale_modules_when_idle(self):
        # A persistent engine keeps sys.modules across a pyRevit reload;
        # the launcher must drop its own modules while no window is open,
        # gated by the SAME envvar the window sets.
        script_source = (COMMAND_DIR / "script.py").read_text(
            encoding="utf-8")
        ui_source = (COMMAND_DIR / "sheet_manager_ui.py").read_text(
            encoding="utf-8")
        self.assertIn("def _drop_stale_modules", script_source)
        match_script = re.search(
            r'ACTIVE_ENVVAR = "([A-Z_]+)"', script_source)
        match_ui = re.search(r'ACTIVE_ENVVAR = "([A-Z_]+)"', ui_source)
        self.assertIsNotNone(match_script)
        self.assertIsNotNone(match_ui)
        self.assertEqual(match_script.group(1), match_ui.group(1))
        for name in ("sheet_manager_ui", "sheet_manager_revit",
                     "sheet_manager_state", "sheet_manager_dialogs",
                     "sheet_manager_xlsx", "easybim.external_events"):
            self.assertIn('"%s"' % name, script_source)

    def test_ui_routes_revit_work_through_the_bridge(self):
        ui_source = (COMMAND_DIR / "sheet_manager_ui.py").read_text(
            encoding="utf-8")
        self.assertIn("from easybim import external_events", ui_source)
        self.assertIn("ExternalEventBridge(", ui_source)
        self.assertIn("window.Show()", ui_source)
        # The modal-era workaround must be gone.
        self.assertNotIn("pending_selection_ids", ui_source)
        # Selection happens silently: no prompt asking to close.
        self.assertNotIn("Close and select", ui_source)

    def test_customized_excel_load_stays_a_sheet_table_source(self):
        ui_source = (COMMAND_DIR / "sheet_manager_ui.py").read_text(
            encoding="utf-8")
        dialogs_source = (COMMAND_DIR / "sheet_manager_dialogs.py").read_text(
            encoding="utf-8")
        self.assertIn("def load_customized_excel", ui_source)
        self.assertIn("read_visible_excel_rows", ui_source)
        self.assertIn("ExcelPrintSetSession", ui_source)
        self.assertIn("LoadCustomizedExcelWindow", ui_source)
        self.assertIn("def skip_number_discrepancies", dialogs_source)
        self.assertIn("def skip_name_discrepancies", dialogs_source)

    def test_pdf_export_and_print_post_checked_visible_rows_as_in_session_set(self):
        ui_source = (COMMAND_DIR / "sheet_manager_ui.py").read_text(
            encoding="utf-8")
        print_sets_source = (REPO_ROOT / "lib" / "easybim" / "print_sets.py").read_text(
            encoding="utf-8")
        pdf_body = ui_source.split("    def pdf_export(", 1)[1]\
            .split("\n    def print_sheets(", 1)[0]
        print_body = ui_source.split("    def print_sheets(", 1)[1]\
            .split("\n\n\n# ------------------------------------------------------------ launcher", 1)[0]
        checked_body = ui_source.split("    def _checked_print_sheets(", 1)[1]\
            .split("\n    def _reload_and_post_command(", 1)[0]
        post_body = ui_source.split("    def _reload_and_post_command(", 1)[1]\
            .split("\n    def pdf_export(", 1)[0]
        self.assertIn("_checked_print_sheets", pdf_body)
        self.assertIn("_checked_print_sheets", print_body)
        self.assertIn("Check at least one sheet row first", checked_body)
        self.assertIn("CanBePrinted", checked_body)
        self.assertIn("Uncheck non-printable placeholder", checked_body)
        self.assertIn("print_sets.set_in_session_print_set", post_body)
        self.assertNotIn("smrevit.select_elements", post_body)
        self.assertLess(post_body.index("print_sets.set_in_session_print_set"),
                        post_body.index("uiapp.PostCommand"))
        self.assertIn("OrderedViewList", print_sets_source)
        self.assertIn("PrintRange.Select", print_sets_source)
        self.assertIn("CurrentViewSheetSet = view_sheet_setting.InSession",
                      print_sets_source)

    def test_revision_template_binds_generated_attrs_only(self):
        source = (COMMAND_DIR / "sheet_manager_ui.py")\
            .read_text(encoding="utf-8")
        self.assertIn("XamlReader", source)
        self.assertNotIn("Click=", source.split("_CHECKBOX_CELL_TEMPLATE")[1]
                         .split("')")[0])


if __name__ == "__main__":
    unittest.main()
