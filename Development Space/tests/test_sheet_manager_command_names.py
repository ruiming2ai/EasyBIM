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
    "CreateSheetsFromTemplateDialog.xaml",
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
        self.assertNotIn("def skip_discrepancies", dialogs_source)

    def test_customized_excel_has_no_discrepancy_skip_bypass(self):
        xaml = (COMMAND_DIR / "LoadCustomizedExcelDialog.xaml").read_text(
            encoding="utf-8")
        dialogs_source = (COMMAND_DIR / "sheet_manager_dialogs.py").read_text(
            encoding="utf-8")
        self.assertNotIn("Skip Discrepancies", xaml)
        self.assertNotIn("skipdiscrepancies_b", xaml)
        self.assertNotIn("skip_discrepancies", xaml)
        self.assertNotIn("skipnumbers_b", xaml)
        self.assertNotIn("skipnames_b", xaml)
        self.assertNotIn("def skip_discrepancies", dialogs_source)
        self.assertNotIn("def skip_number_discrepancies", dialogs_source)
        self.assertNotIn("def skip_name_discrepancies", dialogs_source)
        self.assertIn("Correct the visible discrepancies", dialogs_source)

    def test_customized_excel_lists_sheet_name_discrepancies(self):
        xaml = (COMMAND_DIR / "LoadCustomizedExcelDialog.xaml").read_text(
            encoding="utf-8")

        self.assertIn('Text="Sheet Names Discrepancy"', xaml)
        self.assertNotIn('Text="Missing Sheet Names"', xaml)
        self.assertIn('Header="Sheet Name (Excel)"', xaml)
        self.assertIn('Binding="{Binding excel_name}"', xaml)
        self.assertIn('Header="Sheet Name (Revit)"', xaml)
        self.assertIn('Binding="{Binding revit_name}"', xaml)

    def test_customized_excel_can_create_selected_missing_sheets(self):
        xaml = (COMMAND_DIR / "LoadCustomizedExcelDialog.xaml").read_text(
            encoding="utf-8")
        dialogs_source = (COMMAND_DIR / "sheet_manager_dialogs.py").read_text(
            encoding="utf-8")
        ui_source = (COMMAND_DIR / "sheet_manager_ui.py").read_text(
            encoding="utf-8")
        revit_source = (COMMAND_DIR / "sheet_manager_revit.py").read_text(
            encoding="utf-8")
        self.assertIn('x:Name="create_selected_b"', xaml)
        self.assertIn('Content="Create Selected Sheets"', xaml)
        self.assertIn('Click="create_selected_sheets"', xaml)
        self.assertIn('Header="Create"', xaml)
        self.assertIn("can_create", xaml)
        self.assertIn("CreateSheetsFromTemplateWindow", dialogs_source)
        self.assertIn("def create_selected_sheets", dialogs_source)
        self.assertIn("create_sheets_from_template", ui_source)
        self.assertIn("def collect_sheet_template_options", revit_source)
        self.assertIn("DB.ViewSheet.Create", revit_source)
        self.assertIn("GetAdditionalRevisionIds", revit_source)
        self.assertIn("_copy_writable_parameter_values", revit_source)

    def test_customized_excel_separates_create_and_rename_actions_by_panel(self):
        xaml = (COMMAND_DIR / "LoadCustomizedExcelDialog.xaml").read_text(
            encoding="utf-8")
        dialogs_source = (COMMAND_DIR / "sheet_manager_dialogs.py").read_text(
            encoding="utf-8")
        ui_source = (COMMAND_DIR / "sheet_manager_ui.py").read_text(
            encoding="utf-8")
        revit_source = (COMMAND_DIR / "sheet_manager_revit.py").read_text(
            encoding="utf-8")
        number_panel = xaml.split('x:Name="number_discrepancies_dg"', 1)[1]\
            .split('x:Name="name_discrepancies_dg"', 1)[0]
        name_panel = xaml.split('x:Name="name_discrepancies_dg"', 1)[1]

        self.assertIn('Content="Create Selected Sheets"', number_panel)
        self.assertNotIn('Rename to Match Excel', number_panel)
        self.assertIn('Content="Rename to Match Excel"', name_panel)
        self.assertNotIn('Create Selected Sheets', name_panel)
        self.assertIn('Header="Rename"', xaml)
        self.assertIn('IsChecked="{Binding rename_selected', xaml)
        self.assertIn('IsEnabled="{Binding can_rename}"', xaml)
        self.assertIn('Click="rename_selected_sheets"', xaml)
        self.assertIn("def rename_selected_sheets", dialogs_source)
        self.assertIn("rename_sheets_to_excel", ui_source)
        self.assertIn("def rename_sheets_to_excel", revit_source)

    def test_created_sheet_identity_is_reapplied_after_template_values(self):
        source = (COMMAND_DIR / "sheet_manager_revit.py").read_text(
            encoding="utf-8")
        creation_body = source.split("def create_sheets_from_template", 1)[1]\
            .split("\ndef read_light_snapshot", 1)[0]
        sheet_values = creation_body.index(
            "_copy_writable_parameter_values(\n                    template_sheet")
        titleblock_values = creation_body.index(
            "_copy_writable_parameter_values(template_tblock")
        first_excel_number = creation_body.index(
            "sheet.SheetNumber = import_row.sheet_number")
        first_excel_name = creation_body.index(
            "sheet.Name = import_row.sheet_name")
        last_excel_number = creation_body.rindex(
            "sheet.SheetNumber = import_row.sheet_number")
        last_excel_name = creation_body.rindex(
            "sheet.Name = import_row.sheet_name")

        self.assertLess(first_excel_number, sheet_values)
        self.assertLess(first_excel_name, sheet_values)
        self.assertLess(sheet_values, last_excel_number)
        self.assertLess(titleblock_values, last_excel_number)
        self.assertLess(sheet_values, last_excel_name)
        self.assertLess(titleblock_values, last_excel_name)
        self.assertNotEqual(first_excel_number, last_excel_number)
        self.assertNotEqual(first_excel_name, last_excel_name)
        copy_body = source.split("def _copy_writable_parameter_values", 1)[1]\
            .split("\ndef create_sheets_from_template", 1)[0]
        self.assertIn("target_param_id = eid_to_int(target_param.Id)",
                      copy_body)
        self.assertIn("if target_param_id in excluded_ids:", copy_body)

    def test_parameter_copy_does_not_overwrite_target_sheet_identity(self):
        source = (COMMAND_DIR / "sheet_manager_revit.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_copy_writable_parameter_values")
        namespace = {
            "DB": type("DB", (), {
                "StorageType": type("StorageType", (), {
                    "String": "String",
                    "Integer": "Integer",
                    "Double": "Double",
                    "ElementId": "ElementId",
                })
            }),
            "eid_to_int": lambda element_id: element_id,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]),
                     "sheet_manager_revit.py", "exec"), namespace)
        copy_values = namespace["_copy_writable_parameter_values"]

        class Definition(object):
            Name = "Sheet Number"

        class Parameter(object):
            IsReadOnly = False
            StorageType = "String"

            def __init__(self, param_id, value):
                self.Id = param_id
                self.Definition = Definition()
                self.value = value

            def AsString(self):
                return self.value

            def Set(self, value):
                self.value = value

        class Element(object):
            def __init__(self, parameters, lookup):
                self.Parameters = parameters
                self._lookup = lookup

            def LookupParameter(self, name):
                if name == "Sheet Number":
                    return self._lookup
                return None

        template_number = Parameter(101, "Template A101")
        target_number = Parameter(-1006202, "Excel E1")

        copied = copy_values(
            Element([template_number], None),
            Element([], target_number),
            set([-1006202]))

        self.assertEqual(copied, 0)
        self.assertEqual(target_number.value, "Excel E1")

    def test_rename_to_excel_keeps_other_selected_rows_when_one_fails(self):
        source = (COMMAND_DIR / "sheet_manager_revit.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "rename_sheets_to_excel")

        class Transaction(object):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class SubTransaction(Transaction):
            def __init__(self, doc):
                self.doc = doc

            def Start(self):
                return None

            def Commit(self):
                return None

            def RollBack(self):
                return None

        namespace = {
            "DB": type("DB", (), {"SubTransaction": SubTransaction}),
            "revit": type("Revit", (), {
                "Transaction": staticmethod(
                    lambda title, doc: Transaction())
            }),
            "exception_text": str,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]),
                     "sheet_manager_revit.py", "exec"), namespace)
        rename_sheets = namespace["rename_sheets_to_excel"]

        class Sheet(object):
            def __init__(self, name, reject_name=None):
                self._name = name
                self._reject_name = reject_name

            @property
            def Name(self):
                return self._name

            @Name.setter
            def Name(self, value):
                if value == self._reject_name:
                    raise ValueError("Rejected sheet name")
                self._name = value

        class Discrepancy(object):
            def __init__(self, number, excel_name, sheet):
                self.number = number
                self.excel_name = excel_name
                self.revit_sheet = sheet

        accepted = Sheet("Revit Detail")
        rejected = Sheet("Revit Schedule", reject_name="Excel Schedule")
        renamed, failures = rename_sheets(
            object(), [
                Discrepancy("A001", "Excel Detail", accepted),
                Discrepancy("A002", "Excel Schedule", rejected),
            ])

        self.assertEqual(renamed, [accepted])
        self.assertEqual(accepted.Name, "Excel Detail")
        self.assertEqual(rejected.Name, "Revit Schedule")
        self.assertEqual(len(failures), 1)
        self.assertIn("A002", failures[0])

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
