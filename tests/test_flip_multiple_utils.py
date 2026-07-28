import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Flip Multiple.pushbutton"
    / "flip_multiple_utils.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("flip_multiple_utils", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FlipMultipleUtilsTests(unittest.TestCase):
    def test_build_status_text_prefers_explicit_message(self):
        module = _load_module()

        self.assertEqual(
            module.build_status_text(3, "Selection updated to 3 element(s)."),
            "Selection updated to 3 element(s).",
        )

    def test_build_status_text_falls_back_to_ready_message(self):
        module = _load_module()

        self.assertEqual(
            module.build_status_text(2, ""),
            "Ready. 2 element(s) selected for flipping.",
        )
        self.assertEqual(
            module.build_status_text(0, None),
            "No elements selected. Click Select to add elements from the model.",
        )

    def test_build_completion_message_summarizes_mode_and_counts(self):
        module = _load_module()

        message = module.build_completion_message("Flip Left/Right", 5, 5)

        self.assertEqual(message, "Flip Multiple completed.")

    def test_get_mode_labels_includes_flip_up_down(self):
        module = _load_module()

        self.assertEqual(
            module.get_mode_labels(),
            [
                "Flip Work-plane",
                "Flip Front/Back",
                "Flip Left/Right",
                "Flip Up/Down",
            ],
        )

    def test_get_mode_helper_text_describes_up_down_as_geometric(self):
        module = _load_module()

        self.assertIn(
            "horizontal plane",
            module.get_mode_helper_text("up_down").lower(),
        )

    def test_collect_incompatible_type_labels_dedupes_and_sorts(self):
        module = _load_module()

        labels = module.collect_incompatible_type_labels(
            [
                {"family_name": "Door", "type_name": "Single"},
                {"family_name": "Casework", "type_name": "Base"},
                {"family_name": "Door", "type_name": "Single"},
                {"family_name": "Door", "type_name": "Double"},
            ]
        )

        self.assertEqual(
            labels,
            [
                "Casework : Base",
                "Door : Double",
                "Door : Single",
            ],
        )

    def test_build_incompatibility_message_includes_mode_and_non_family_count(self):
        module = _load_module()

        message = module.build_incompatibility_message(
            "Flip Front/Back",
            4,
            ["Casework : Base", "Door : Single"],
            1,
        )

        self.assertIn("Flip Multiple cannot run.", message)
        self.assertIn("Mode: Flip Front/Back", message)
        self.assertIn("Selected elements: 4", message)
        self.assertIn("Non-family-instance elements: 1", message)
        self.assertIn("Casework : Base", message)
        self.assertIn("Door : Single", message)


if __name__ == "__main__":
    unittest.main()
