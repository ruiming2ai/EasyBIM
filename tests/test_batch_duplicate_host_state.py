import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Batch Duplicate Host.pushbutton"
    / "batch_duplicate_host_state.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "batch_duplicate_host_state",
        str(MODULE_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BatchDuplicateHostStateTests(unittest.TestCase):
    def test_build_summary_text_caps_skipped_and_notes(self):
        module = _load_module()

        summary = module.PlacementSummary(
            created_element_ids=list(range(5)),
            skipped=[
                module.SkippedPlacement("Target {}".format(index), "Reason {}".format(index))
                for index in range(25)
            ],
            notes=["Note {}".format(index) for index in range(12)],
        )

        message = module.build_summary_text(summary, 30)

        self.assertIn("Targets processed: 30", message)
        self.assertIn("Elements created: 5", message)
        self.assertIn("Skipped: 25", message)
        self.assertIn("- Target 0: Reason 0", message)
        self.assertIn("- Plus 5 more.", message)
        self.assertIn("Notes:", message)
        self.assertIn("- Note 0", message)
        self.assertIn("- Plus 2 more.", message)
        self.assertNotIn("Target 24", message)
        self.assertNotIn("Note 11", message)

    def test_restore_selection_state_marks_selected_items(self):
        module = _load_module()

        categories = [
            module.CategoryOption("Pipes", "cat-2"),
            module.CategoryOption("Plumbing Fixtures", "cat-1"),
        ]
        groups = [
            module.FamilyGroupOption(
                "Family B",
                [
                    module.FamilyTypeOption("Family B", "Type 2", "type-2"),
                    module.FamilyTypeOption("Family B", "Type 1", "type-1"),
                ],
            )
        ]

        module.restore_category_selection(categories, {"cat-1"})
        module.restore_type_selection(groups, {"type-2"})

        self.assertFalse(categories[0].is_selected)
        self.assertTrue(categories[1].is_selected)
        self.assertTrue(groups[0].types[0].is_selected)
        self.assertFalse(groups[0].types[1].is_selected)

    def test_sort_helpers_keep_current_project_first_and_sort_names(self):
        module = _load_module()

        documents = [
            module.TargetDocumentOption("z-link.rvt", document_key="z", is_current_project=False),
            module.TargetDocumentOption("Current Project", document_key="host", is_current_project=True),
            module.TargetDocumentOption("A-link.rvt", document_key="a", is_current_project=False),
        ]
        categories = [
            module.CategoryOption("Plumbing Fixtures", "cat-2"),
            module.CategoryOption("Pipes", "cat-1"),
        ]
        groups = [
            module.FamilyGroupOption(
                "Family B",
                [
                    module.FamilyTypeOption("Family B", "Type Z", "type-z"),
                    module.FamilyTypeOption("Family B", "Type A", "type-a"),
                ],
            ),
            module.FamilyGroupOption(
                "Family A",
                [
                    module.FamilyTypeOption("Family A", "Type B", "type-b"),
                ],
            ),
        ]

        sorted_documents = module.sort_target_documents(documents)
        sorted_categories = module.sort_categories(categories)
        sorted_groups = module.sort_family_groups(groups)

        self.assertEqual(
            [document.display_name for document in sorted_documents],
            ["Current Project", "A-link.rvt", "z-link.rvt"],
        )
        self.assertEqual(
            [category.name for category in sorted_categories],
            ["Pipes", "Plumbing Fixtures"],
        )
        self.assertEqual(
            [group.name for group in sorted_groups],
            ["Family A", "Family B"],
        )
        self.assertEqual(
            [family_type.type_name for family_type in sorted_groups[1].types],
            ["Type A", "Type Z"],
        )

    def test_wizard_state_keeps_align_orientation_and_offsets(self):
        module = _load_module()

        state = module.WizardState(
            selected_document_key="host",
            selected_category_ids={"cat-1", "cat-2"},
            selected_type_ids={"type-1"},
            x_offset_text='6"',
            y_offset_text="2'",
            z_offset_text="0",
            align_orientation=True,
        )

        self.assertEqual(state.selected_document_key, "host")
        self.assertEqual(state.selected_category_ids, {"cat-1", "cat-2"})
        self.assertEqual(state.selected_type_ids, {"type-1"})
        self.assertEqual(state.x_offset_text, '6"')
        self.assertEqual(state.y_offset_text, "2'")
        self.assertEqual(state.z_offset_text, "0")
        self.assertTrue(state.align_orientation)


if __name__ == "__main__":
    unittest.main()
