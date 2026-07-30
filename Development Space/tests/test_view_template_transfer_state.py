import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Views.panel"
    / "View Template.pulldown"
    / "Batch Transfer View Template Settings.pushbutton"
    / "view_template_transfer_state.py"
)
COMMAND_DIR = MODULE_PATH.parent
PULLDOWN_DIR = COMMAND_DIR.parent


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "view_template_transfer_state",
        str(MODULE_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ViewTemplateTransferStateTests(unittest.TestCase):
    def test_order_parameter_ids_follows_revit_order_and_appends_missing(self):
        module = _load_module()

        ordered = module.order_parameter_ids(
            template_parameter_ids=[30, 10, 50, 20],
            ordered_parameter_ids=[10, 20, 30],
            names_by_id={
                10: "View Scale",
                20: "Scale Value    1:",
                30: "Display Model",
                50: "Custom Project View Parameter",
            },
        )

        self.assertEqual(ordered, [10, 20, 30, 50])

    def test_template_parameter_row_defaults_selection_to_source_include(self):
        module = _load_module()

        included = module.TemplateParameterRow(10, "View Scale", '1/8" = 1\'-0"', True)
        excluded = module.TemplateParameterRow(20, "View Range", "Edit...", False)

        self.assertTrue(included.is_selected)
        self.assertFalse(excluded.is_selected)
        self.assertEqual(included.value_text, '1/8" = 1\'-0"')

    def test_group_target_templates_excludes_source_and_expands_all_groups(self):
        module = _load_module()
        options = [
            module.ViewTemplateOption(1, "A Plan Source", "Floor Plan"),
            module.ViewTemplateOption(2, "A Plan Target", "Floor Plan"),
            module.ViewTemplateOption(3, "A RCP Target", "Ceiling Plan"),
            module.ViewTemplateOption(4, "A Section Target", "Section"),
        ]

        groups = module.group_target_templates_by_view_type(
            options,
            selected_source_id_int=1,
            view_type_filter=module.ALL_VIEW_TYPES,
        )

        self.assertEqual([group.view_type_label for group in groups], ["Ceiling Plan", "Floor Plan", "Section"])
        self.assertTrue(all(group.is_expanded for group in groups))
        self.assertEqual([template.name for template in groups[1].templates], ["A Plan Target"])

    def test_check_and_clear_visible_targets_preserve_hidden_selections(self):
        module = _load_module()
        floor = module.ViewTemplateOption(1, "A Plan Target", "Floor Plan")
        section = module.ViewTemplateOption(2, "A Section Target", "Section")
        section.is_selected = True
        options = [floor, section]

        module.set_visible_template_selection(options, "Floor Plan", True)
        self.assertTrue(floor.is_selected)
        self.assertTrue(section.is_selected)

        module.set_visible_template_selection(options, "Floor Plan", False)
        self.assertFalse(floor.is_selected)
        self.assertTrue(section.is_selected)

    def test_transfer_non_controlled_sets_copy_selected_and_preserve_unselected(self):
        module = _load_module()

        temp_non_controlled = module.calculate_temporary_non_controlled_ids(
            target_template_parameter_ids=[10, 20, 30, 40],
            selected_parameter_ids=[10, 30, 99],
        )
        final_non_controlled = module.calculate_final_non_controlled_ids(
            target_template_parameter_ids=[10, 20, 30, 40],
            original_target_non_controlled_ids=[20, 30],
            selected_parameter_ids=[10, 30, 99],
            source_non_controlled_ids=[30],
        )

        self.assertEqual(temp_non_controlled, [10, 30])
        self.assertEqual(final_non_controlled, [20, 30])

    def test_pyrevit_bundle_metadata_names_dropdown_and_button(self):
        pulldown_yaml = (PULLDOWN_DIR / "bundle.yaml").read_text()
        button_yaml = (COMMAND_DIR / "bundle.yaml").read_text()

        self.assertIn("View\n  Template", pulldown_yaml)
        self.assertIn("Batch Transfer View\n  Template Settings", button_yaml)
        self.assertIn("author: Ruiming Liu", button_yaml)

    def test_window_xaml_exposes_native_table_and_target_controls(self):
        source = (COMMAND_DIR / "BatchTransferViewTemplateSettings.xaml").read_text()

        self.assertIn('Title="Batch Transfer View Template Settings"', source)
        self.assertIn('Text="Parameter"', source)
        self.assertIn('Text="Value"', source)
        self.assertIn('Text="Include"', source)
        self.assertIn('x:Name="source_template_cb"', source)
        self.assertIn('Text="View Type Filter:"', source)
        self.assertIn('Content="Check All"', source)
        self.assertIn('Content="Clear"', source)
        self.assertIn('Content="Transfer"', source)


if __name__ == "__main__":
    unittest.main()
