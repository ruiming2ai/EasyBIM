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

    def test_customized_excel_comparison_is_a_grouped_readonly_table(self):
        root = ET.parse(str(COMMAND_DIR / "LoadCustomizedExcelDialog.xaml")).getroot()
        controls = {e.attrib[X_NAME]: e for e in root.iter() if X_NAME in e.attrib}
        self.assertIn("compare_b", controls)
        self.assertIn("comparison_source_cb", controls)
        self.assertIn("comparison_tab", controls)
        grid = controls["comparison_dg"]
        self.assertEqual(grid.attrib["IsReadOnly"], "True")
        self.assertEqual(grid.attrib["CanUserSortColumns"], "False")
        self.assertTrue(any(e.tag.endswith("GroupStyle") for e in grid.iter()))
        headers = [e.attrib.get("Header") for e in grid.iter()]
        for header in ("Sheet Number", "Sheet Name (Excel)", "Sheet Name (Revit)", "Reason / Details"):
            self.assertIn(header, headers)

    def test_filter_and_sort_dialogs_support_dynamic_rules(self):
        filter_xaml = (COMMAND_DIR / "FilterByParameterDialog.xaml").read_text(
            encoding="utf-8")
        sort_xaml = (COMMAND_DIR / "SortDialog.xaml").read_text(
            encoding="utf-8")
        dialogs_source = (COMMAND_DIR / "sheet_manager_dialogs.py").read_text(
            encoding="utf-8")

        self.assertIn('x:Name="addfilter_b"', filter_xaml)
        self.assertIn('Content="Add Filter"', filter_xaml)
        self.assertIn('Click="add_filter_clicked"', filter_xaml)
        self.assertIn("value_cb.IsEditable = True", dialogs_source)
        self.assertIn("def add_filter_clicked", dialogs_source)
        self.assertNotIn("RULE_COUNT", dialogs_source)
        ui_source = (COMMAND_DIR / "sheet_manager_ui.py").read_text(
            encoding="utf-8")
        self.assertIn("def _source_rows", ui_source)
        self.assertIn("def _filter_value_options", ui_source)
        self.assertIn("self._source_rows()", ui_source)

        self.assertIn('x:Name="addsort_b"', sort_xaml)
        self.assertIn('Content="Add Sort Level"', sort_xaml)
        self.assertIn('Click="add_sort_level_clicked"', sort_xaml)
        self.assertIn('ScrollViewer', sort_xaml)
        self.assertIn("def add_sort_level_clicked", dialogs_source)
        self.assertNotIn("LEVEL_COUNT", dialogs_source)

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

    def test_customized_excel_can_skip_selected_discrepancies_per_panel(self):
        xaml = (COMMAND_DIR / "LoadCustomizedExcelDialog.xaml").read_text(
            encoding="utf-8")
        dialogs_source = (COMMAND_DIR / "sheet_manager_dialogs.py").read_text(
            encoding="utf-8")
        self.assertEqual(xaml.count('Header="Select"'), 2)
        self.assertEqual(xaml.count('IsChecked="{Binding is_selected'), 2)
        self.assertNotIn("Binding create_selected", xaml)
        self.assertNotIn("Binding rename_selected", xaml)
        self.assertIn('x:Name="skip_numbers_b"', xaml)
        self.assertIn('x:Name="skip_names_b"', xaml)
        self.assertEqual(xaml.count('Content="Skip and Ignore"'), 2)
        self.assertIn('Click="skip_selected_numbers"', xaml)
        self.assertIn('Click="skip_selected_names"', xaml)
        self.assertIn("def skip_selected_numbers", dialogs_source)
        self.assertIn("def skip_selected_names", dialogs_source)
        self.assertIn("ignore_number_rows", dialogs_source)
        self.assertIn("ignore_name_rows", dialogs_source)

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
        self.assertIn('Header="Select"', xaml)
        self.assertNotIn('IsEnabled="{Binding can_create}"', xaml)
        self.assertIn("CreateSheetsFromTemplateWindow", dialogs_source)
        self.assertIn("def create_selected_sheets", dialogs_source)
        self.assertIn("template_sheet_id", ui_source)
        self.assertIn("CustomizedExcelBatch", ui_source)
        self.assertIn("def collect_sheet_template_options", revit_source)
        self.assertIn("DB.ViewSheet.Create", revit_source)
        self.assertIn("GetAdditionalRevisionIds", revit_source)
        self.assertIn("_copy_writable_parameter_values", revit_source)

    def test_customized_excel_template_dialog_fits_its_footer(self):
        xaml = (COMMAND_DIR / "CreateSheetsFromTemplateDialog.xaml").read_text(
            encoding="utf-8")
        self.assertIn('SizeToContent="Height"', xaml)
        self.assertIn('MinHeight="250"', xaml)

    def test_customized_excel_excludes_sheet_collection_from_template_copy(self):
        source = (COMMAND_DIR / "sheet_manager_revit.py").read_text(
            encoding="utf-8")
        excluded = source.split("_EXCLUDED_SHEET_PARAM_IDS =", 1)[1]\
            .split("def excluded_sheet_param_ids", 1)[0]
        self.assertIn('"SHEET_COLLECTION"', excluded)
        self.assertIn("SheetCollectionId", source)

    def test_customized_excel_is_staged_and_applied_as_its_own_batch(self):
        ui_source = (COMMAND_DIR / "sheet_manager_ui.py").read_text(
            encoding="utf-8")
        dialogs_source = (COMMAND_DIR / "sheet_manager_dialogs.py").read_text(
            encoding="utf-8")
        self.assertIn("CustomizedExcelBatch", ui_source)
        self.assertIn("batch.select", ui_source)
        self.assertIn("stage_creations", dialogs_source)
        self.assertIn("stage_renames", dialogs_source)
        self.assertNotIn("rename_sheets_to_excel(", ui_source)

    def test_staged_changes_block_excel_export_import_and_native_output(self):
        source = (COMMAND_DIR / "sheet_manager_ui.py").read_text(
            encoding="utf-8")
        for method_name in ("export_to_excel", "import_from_excel",
                            "load_customized_excel", "_checked_print_sheets"):
            body = source.split("def {0}".format(method_name), 1)[1]\
                .split("\n    def ", 1)[0]
            self.assertIn("_block_if_staged_changes", body)
        self.assertIn("def _block_if_staged_changes", source)

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
        self.assertIn('Header="Select"', xaml)
        self.assertIn('IsChecked="{Binding is_selected', xaml)
        self.assertNotIn('IsEnabled="{Binding can_rename}"', xaml)
        self.assertIn('Click="rename_selected_sheets"', xaml)
        self.assertIn("def rename_selected_sheets", dialogs_source)
        self.assertIn("stage_renames", dialogs_source)
        self.assertIn("CustomizedExcelBatch", ui_source)
        self.assertNotIn("def rename_sheets_to_excel", revit_source)

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
