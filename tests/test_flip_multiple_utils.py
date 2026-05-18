import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "RL_Tools.tab"
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
