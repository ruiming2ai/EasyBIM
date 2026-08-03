import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Views.panel"
    / "View Template.pushbutton"
    / "view_template_transfer_state.py"
)
COMMAND_DIR = MODULE_PATH.parent
PANEL_DIR = COMMAND_DIR.parent


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

        self.assertEqual(
            [group.view_type_label for group in groups],
            ["Ceiling Plan", "Floor Plan", "Section"],
        )
        self.assertTrue(all(group.is_expanded for group in groups))
        self.assertEqual(
            [template.name for template in groups[1].templates],
            ["A Plan Target"],
        )

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


class ViewTypeAndLockTests(unittest.TestCase):
    def test_view_type_families_group_revit_view_types(self):
        module = _load_module()

        self.assertTrue(module.is_compatible_view_types("FloorPlan", "AreaPlan"))
        self.assertTrue(module.is_compatible_view_types("FloorPlan", "EngineeringPlan"))
        self.assertTrue(module.is_compatible_view_types("Section", "Elevation"))
        self.assertTrue(module.is_compatible_view_types("Section", "Detail"))
        self.assertTrue(module.is_compatible_view_types("ThreeD", "Walkthrough"))
        self.assertFalse(module.is_compatible_view_types("FloorPlan", "CeilingPlan"))
        self.assertFalse(module.is_compatible_view_types("FloorPlan", "Schedule"))
        self.assertFalse(module.is_compatible_view_types("DraftingView", "Legend"))

    def test_unknown_view_types_are_permissive(self):
        module = _load_module()

        self.assertTrue(module.is_compatible_view_types("FloorPlan", "SomeFutureType"))
        self.assertTrue(module.is_compatible_view_types("", "FloorPlan"))
        self.assertIsNone(module.view_type_family("SomeFutureType"))

    def test_excluded_view_types_are_not_eligible(self):
        module = _load_module()

        for view_type_name in module.EXCLUDED_VIEW_TYPES:
            self.assertFalse(module.is_eligible_view_type(view_type_name))
        self.assertFalse(module.is_eligible_view_type("DrawingSheet"))
        self.assertFalse(module.is_eligible_view_type("Schedule"))
        self.assertFalse(module.is_eligible_view_type("Legend"))
        self.assertTrue(module.is_eligible_view_type("FloorPlan"))
        self.assertTrue(module.is_eligible_view_type("ThreeD"))

    def test_view_lock_detection(self):
        module = _load_module()

        self.assertTrue(module.is_view_locked_by_template(123))
        self.assertFalse(module.is_view_locked_by_template(None))
        self.assertFalse(module.is_view_locked_by_template(-1))
        self.assertFalse(module.is_view_locked_by_template(0))

    def test_has_transfer_candidates_boundaries(self):
        module = _load_module()

        self.assertFalse(module.has_transfer_candidates(0, 0))
        self.assertFalse(module.has_transfer_candidates(0, 1))
        self.assertFalse(module.has_transfer_candidates(1, 0))
        self.assertTrue(module.has_transfer_candidates(1, 1))
        self.assertTrue(module.has_transfer_candidates(2, 0))
        self.assertTrue(module.has_transfer_candidates(0, 2))

    def test_view_option_normalizes_template_id_and_marks_dependents(self):
        module = _load_module()

        plain = module.ViewOption(1, "Level 1", "FloorPlan", "Floor Plan")
        invalid = module.ViewOption(
            2, "Level 2", "FloorPlan", "Floor Plan", template_id_int=-1
        )
        locked = module.ViewOption(
            3, "Level 3", "FloorPlan", "Floor Plan",
            template_id_int=77, template_name="Arch Plan",
        )
        dependent = module.ViewOption(
            4, "Level 4", "FloorPlan", "Floor Plan", is_dependent=True
        )

        self.assertIsNone(plain.template_id_int)
        self.assertIsNone(invalid.template_id_int)
        self.assertEqual(locked.template_id_int, 77)
        self.assertEqual(dependent.display, "Level 4 (dependent)")
        self.assertEqual(plain.display, "Level 1")


class TargetRowTests(unittest.TestCase):
    def _views(self, module):
        return [
            module.ViewOption(100, "Source Plan", "FloorPlan", "Floor Plan"),
            module.ViewOption(
                101, "Locked Plan", "FloorPlan", "Floor Plan",
                template_id_int=7, template_name="Arch Plan",
            ),
            module.ViewOption(102, "Free Plan", "FloorPlan", "Floor Plan"),
            module.ViewOption(103, "A Section", "Section", "Section"),
        ]

    def test_build_target_rows_marks_locked_and_incompatible(self):
        module = _load_module()

        rows = module.build_target_rows(
            self._views(module),
            module.TARGET_KIND_VIEWS,
            source_view_type_name="FloorPlan",
            source_id_int=100,
        )

        self.assertEqual([row.element_id_int for row in rows], [101, 102, 103])
        self.assertEqual(rows[0].lock_state, module.LOCK_STATE_LOCKED)
        self.assertIn("Arch Plan", rows[0].tooltip_text)
        self.assertEqual(rows[1].lock_state, module.LOCK_STATE_NORMAL)
        self.assertEqual(rows[2].lock_state, module.LOCK_STATE_INCOMPATIBLE)
        self.assertFalse(rows[0].is_checkable)
        self.assertTrue(rows[1].is_checkable)
        self.assertFalse(rows[2].is_checkable)

    def test_template_targets_ignore_view_locks(self):
        module = _load_module()
        templates = [
            module.ViewTemplateOption(1, "Plan Template", "Floor Plan", view_type_name="FloorPlan"),
            module.ViewTemplateOption(2, "Section Template", "Section", view_type_name="Section"),
        ]

        rows = module.build_target_rows(
            templates,
            module.TARGET_KIND_TEMPLATES,
            source_view_type_name="FloorPlan",
            source_id_int=None,
        )

        self.assertEqual(rows[0].lock_state, module.LOCK_STATE_NORMAL)
        self.assertEqual(rows[1].lock_state, module.LOCK_STATE_INCOMPATIBLE)

    def test_check_all_skips_locked_rows_but_clear_does_not(self):
        module = _load_module()
        rows = module.build_target_rows(
            self._views(module),
            module.TARGET_KIND_VIEWS,
            source_view_type_name="FloorPlan",
            source_id_int=100,
        )

        module.set_visible_template_selection(rows, module.ALL_VIEW_TYPES, True)
        self.assertEqual([row.is_selected for row in rows], [False, True, False])

        rows[0].is_selected = True  # simulate stale selection on a locked row
        module.set_visible_template_selection(rows, module.ALL_VIEW_TYPES, False)
        self.assertFalse(any(row.is_selected for row in rows))


class SelectiveTransferStateTests(unittest.TestCase):
    def test_vg_constants_cover_exactly_the_seven_groups(self):
        module = _load_module()

        self.assertEqual(len(module.VG_GROUP_KEYS), 7)
        self.assertEqual(
            set(module.VG_PARAM_NAME_TO_GROUP.values()),
            set(module.VG_GROUP_KEYS),
        )
        self.assertEqual(len(module.VG_PARAM_NAME_TO_GROUP), 7)
        self.assertEqual(
            set(module.VG_GROUP_COLUMNS.keys()),
            set(module.VG_GROUP_KEYS),
        )
        self.assertEqual(
            set(module.VG_GROUP_TITLES.keys()),
            set(module.VG_GROUP_KEYS),
        )

    def test_vg_group_lookup_is_case_insensitive(self):
        module = _load_module()

        self.assertEqual(
            module.vg_group_for_parameter_name("V/G Overrides Model"),
            module.GROUP_MODEL,
        )
        self.assertEqual(
            module.vg_group_for_parameter_name("  v/g overrides rvt links "),
            module.GROUP_LINKS,
        )
        self.assertEqual(
            module.vg_group_for_parameter_name("V/G Overrides Worksets"),
            module.GROUP_WORKSETS,
        )
        self.assertIsNone(module.vg_group_for_parameter_name("View Scale"))

    def test_selection_rows_start_unchecked_and_map_groups(self):
        module = _load_module()

        rows = module.build_parameter_selection_rows(
            [
                (1, "View Scale"),
                (2, "V/G Overrides Model"),
                (3, "V/G Overrides Filters"),
            ]
        )

        self.assertEqual(
            [row.group_key for row in rows],
            [None, module.GROUP_MODEL, module.GROUP_FILTERS],
        )
        self.assertFalse(rows[0].has_edit)
        self.assertTrue(rows[1].has_edit)
        self.assertTrue(all(row.include_checked is False for row in rows))

    def test_group_include_state_all_none_partial_empty(self):
        module = _load_module()
        row = module.ParameterSelectionRow(2, "V/G Overrides Model", module.GROUP_MODEL)

        self.assertIs(module.compute_group_include_state(row), False)  # no items

        row.items = [module.VgItemRow(10, "Casework"), module.VgItemRow(11, "Ceilings")]
        self.assertIs(module.compute_group_include_state(row), False)

        row.items[0].is_checked = True
        self.assertIsNone(module.compute_group_include_state(row))

        row.items[1].is_checked = True
        self.assertIs(module.compute_group_include_state(row), True)

    def test_include_click_toggles_plain_rows_and_group_masters(self):
        module = _load_module()
        plain = module.ParameterSelectionRow(1, "View Scale")
        group = module.ParameterSelectionRow(2, "V/G Overrides Model", module.GROUP_MODEL)
        group.items = [module.VgItemRow(10, "Casework"), module.VgItemRow(11, "Ceilings")]

        module.apply_include_click(plain, True)
        self.assertTrue(plain.is_selected)
        module.apply_include_click(plain, False)
        self.assertFalse(plain.is_selected)

        module.apply_include_click(group, True)
        self.assertTrue(all(item.is_checked for item in group.items))
        self.assertIs(group.include_checked, True)
        module.apply_include_click(group, False)
        self.assertFalse(any(item.is_checked for item in group.items))

    def test_resolve_include_click_selects_all_on_partial_groups(self):
        module = _load_module()
        plain = module.ParameterSelectionRow(1, "View Scale")
        group = module.ParameterSelectionRow(2, "V/G Overrides Model", module.GROUP_MODEL)
        group.items = [module.VgItemRow(10, "Casework"), module.VgItemRow(11, "Ceilings")]

        # Plain rows and unchecked/fully-checked groups follow the checkbox.
        self.assertTrue(module.resolve_include_click(plain, True))
        self.assertFalse(module.resolve_include_click(plain, False))
        self.assertTrue(module.resolve_include_click(group, True))
        module.apply_group_master_toggle(group, True)
        self.assertFalse(module.resolve_include_click(group, False))

        # WPF reports an indeterminate box as unchecked once Click fires;
        # a partially-checked group must still resolve to select-all.
        group.items[1].is_checked = False
        self.assertTrue(module.resolve_include_click(group, False))

    def test_selection_rows_fall_back_to_builtin_ids_for_localized_names(self):
        module = _load_module()

        rows = module.build_parameter_selection_rows(
            [
                (-1006963, u"Sichtbarkeit Modell"),
                (3, "V/G Overrides Filters"),
                (4, "View Scale"),
            ],
            group_key_by_id={-1006963: module.GROUP_MODEL},
        )

        self.assertEqual(rows[0].group_key, module.GROUP_MODEL)
        self.assertEqual(rows[1].group_key, module.GROUP_FILTERS)
        self.assertIsNone(rows[2].group_key)

    def test_links_empty_hint_reflects_api_availability(self):
        module = _load_module()

        self.assertEqual(
            module.vg_group_empty_hint(module.GROUP_LINKS),
            module.VG_GROUP_EMPTY_HINTS[module.GROUP_LINKS],
        )
        self.assertEqual(
            module.vg_group_empty_hint(module.GROUP_LINKS, links_api_available=False),
            module.VG_LINKS_UNSUPPORTED_HINT,
        )
        self.assertEqual(
            module.vg_group_empty_hint(module.GROUP_FILTERS, links_api_available=False),
            module.VG_GROUP_EMPTY_HINTS[module.GROUP_FILTERS],
        )

    def test_build_transfer_plan_full_partial_and_empty(self):
        module = _load_module()
        rows = module.build_parameter_selection_rows(
            [
                (1, "View Scale"),
                (2, "V/G Overrides Model"),
                (3, "V/G Overrides Filters"),
                (4, "Detail Level"),
            ]
        )
        rows[0].is_selected = True
        rows[1].items = [module.VgItemRow(10, "Casework"), module.VgItemRow(11, "Ceilings")]
        module.apply_group_master_toggle(rows[1], True)
        rows[2].items = [module.VgItemRow(20, "Filter A"), module.VgItemRow(21, "Filter B")]
        rows[2].items[0].is_checked = True

        plan = module.build_transfer_plan(rows)

        self.assertFalse(plan.transfer_all)
        self.assertEqual(plan.full_parameter_ids, [1, 2])
        self.assertEqual(plan.partial_groups, {module.GROUP_FILTERS: [20]})
        self.assertEqual(plan.partial_group_parameter_ids, [3])
        self.assertEqual(plan.setting_count, 3)
        self.assertFalse(module.plan_is_empty(plan))

        self.assertTrue(module.plan_is_empty(module.build_transfer_plan([])))
        self.assertTrue(module.plan_is_empty(None))
        self.assertFalse(
            module.plan_is_empty(module.TransferPlan(transfer_all=True))
        )

    def test_adjust_final_ids_forces_partial_groups_controlled(self):
        module = _load_module()

        self.assertEqual(
            module.adjust_final_ids_for_partial_groups([3, 5, 7], [3]),
            [5, 7],
        )
        self.assertEqual(
            module.adjust_final_ids_for_partial_groups([3, 5], []),
            [3, 5],
        )

    def test_snapshot_and_restore_checked_keys_round_trip(self):
        module = _load_module()
        items = [
            module.VgItemRow(10, "Casework"),
            module.VgItemRow(11, "Ceilings"),
            module.VgItemRow(12, "Columns"),
        ]
        items[0].is_checked = True
        items[2].is_checked = True

        snapshot = module.snapshot_checked_keys(items)
        items[0].is_checked = False
        items[1].is_checked = True
        module.restore_checked_keys(items, snapshot)

        self.assertEqual(
            [item.is_checked for item in items], [True, False, True]
        )

    def test_selective_state_matches_only_its_source(self):
        module = _load_module()
        selective = module.SelectiveState("view", 100, [])

        self.assertTrue(selective.matches_source("view", 100))
        self.assertFalse(selective.matches_source("template", 100))
        self.assertFalse(selective.matches_source("view", 101))

    def test_vg_item_row_indents_subcategories(self):
        module = _load_module()
        parent = module.VgItemRow(10, "Casework", values={"check0": True, "text0": "Weight 3"})
        child = module.VgItemRow(11, "Hidden Lines", parent_key=10, indent=1)

        self.assertTrue(parent.check0)
        self.assertEqual(parent.text0, "Weight 3")
        self.assertEqual(child.parent_key, 10)
        self.assertTrue(child.name.startswith(u" "))
        self.assertFalse(child.is_checked)


class BundleAndXamlTests(unittest.TestCase):
    def test_pyrevit_bundle_names_single_view_template_pushbutton(self):
        button_yaml = (COMMAND_DIR / "bundle.yaml").read_text()

        self.assertIn("View\n  Template", button_yaml)
        self.assertIn("min_revit_version: 2023", button_yaml)
        self.assertIn("author: Ruiming Liu", button_yaml)
        self.assertFalse((PANEL_DIR / "View Template.pulldown").exists())
        self.assertTrue((COMMAND_DIR / "icon.png").exists())

    def test_main_window_xaml_exposes_mode_controls_and_buttons(self):
        source = (COMMAND_DIR / "ViewTemplateTransferWindow.xaml").read_text()

        self.assertIn('x:Name="source_view_rb"', source)
        self.assertIn('x:Name="source_view_cb"', source)
        self.assertIn('x:Name="source_template_rb"', source)
        self.assertIn('x:Name="source_template_cb"', source)
        self.assertIn('x:Name="target_views_rb"', source)
        self.assertIn('x:Name="target_templates_rb"', source)
        self.assertIn('x:Name="target_tv"', source)
        self.assertIn('x:Name="view_type_filter_cb"', source)
        self.assertIn('GroupName="SourceMode"', source)
        self.assertIn('GroupName="TargetMode"', source)
        self.assertIn('Content="Transfer from View"', source)
        self.assertIn('Content="Transfer from View Template"', source)
        self.assertIn('Content="Transfer to Views"', source)
        self.assertIn('Content="Transfer to View Templates"', source)
        # No left settings panel remains.
        self.assertNotIn('x:Name="settings_lv"', source)
        self.assertNotIn("View properties", source)

    def test_main_window_locks_and_button_row(self):
        source = (COMMAND_DIR / "ViewTemplateTransferWindow.xaml").read_text()

        self.assertIn('Value="locked"', source)
        self.assertIn('Value="incompatible"', source)
        self.assertIn('Value="#9B9B9B"', source)
        self.assertIn('ToolTipService.ShowOnDisabled="True"', source)
        self.assertIn('Content="Selective Transfer"', source)
        self.assertIn('Content="Transfer All"', source)
        self.assertIn('Margin="0,0,16,0"', source)
        self.assertNotIn('Content="Transfer"' + '"', source)

    def test_selective_dialog_xaml_uses_tri_state_and_edit_column(self):
        source = (COMMAND_DIR / "SelectiveTransferDialog.xaml").read_text()

        self.assertIn('x:Name="params_dg"', source)
        self.assertIn('IsThreeState="False"', source)
        self.assertIn('IsChecked="{Binding include_checked, Mode=OneWay}"', source)
        self.assertIn('Content="Edit..."', source)
        self.assertIn('Binding="{Binding has_edit}"', source)

        columns = source.split("<DataGrid.Columns>", 1)[1].split(
            "</DataGrid.Columns>", 1
        )[0]
        self.assertNotIn("Click=", columns)

    def test_vg_edit_dialog_xaml_has_empty_hint_and_code_built_grid(self):
        source = (COMMAND_DIR / "VgEditDialog.xaml").read_text()

        self.assertIn('x:Name="items_dg"', source)
        self.assertIn('x:Name="empty_hint_tb"', source)
        self.assertIn('x:Name="check_all_btn"', source)
        self.assertIn('Closing="window_closing"', source)
        self.assertNotIn("<DataGrid.Columns>", source)

    def test_revit_module_covers_new_transfer_paths(self):
        source = (COMMAND_DIR / "view_template_transfer_revit.py").read_text()

        self.assertIn("CreateViewTemplate", source)
        self.assertIn("ApplyViewTemplateParameters", source)
        self.assertIn("SetNonControlledTemplateParameterIds", source)
        self.assertIn("SetCategoryOverrides", source)
        self.assertIn("SetCategoryHidden", source)
        self.assertIn("SetFilterOverrides", source)
        self.assertIn("SetFilterVisibility", source)
        self.assertIn("SetIsFilterEnabled", source)
        self.assertIn("SetWorksetVisibility", source)
        self.assertIn("SetLinkOverrides", source)
        self.assertIn("TransactionGroup", source)
        # Revit 2023 has no per-view link override API; the group is gated.
        self.assertIn("def links_api_available", source)
        self.assertIn("RevitLinkGraphicsSettings", source)
        # Language-independent V/G group detection.
        self.assertIn("VIS_GRAPHICS_RVT_LINKS", source)
        # Item-level notes must not count as skipped targets.
        self.assertIn("add_note", source)


if __name__ == "__main__":
    unittest.main()
