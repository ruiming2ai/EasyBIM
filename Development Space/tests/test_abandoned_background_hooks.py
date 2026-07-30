import importlib.util
import pathlib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]


ABANDONED_BACKGROUND_PATHS = (
    "lib/easybim/temp_phase_view.py",
    "lib/easybim/close_stop.py",
    "lib/easybim/file_close_guard.py",
    "lib/easybim/save_tvp_prompt.py",
    "lib/easybim/file_saved_notice.py",
    "hooks/command-before-exec.py",
    "hooks/command-before-exec[ID_FILE_CLOSE].py",
    "hooks/command-before-exec[ID_FILE_SAVE].py",
    "hooks/command-before-exec[ID_FILE_SAVE_TO_CENTRAL].py",
    "hooks/command-before-exec[ID_FILE_SAVE_TO_CENTRAL_SHORTCUT].py",
    "hooks/command-before-exec[ID_FILE_SAVE_TO_MASTER].py",
    "hooks/command-before-exec[ID_FILE_SAVE_TO_MASTER_SHORTCUT].py",
    "hooks/command-before-exec[ID_PROJECT_CLOSE].py",
    "hooks/command-before-exec[ID_REVIT_FILE_CLOSE].py",
    "hooks/command-before-exec[ID_REVIT_FILE_SAVE].py",
    "hooks/command-before-exec[ID_REVIT_PROJECT_CLOSE].py",
    "hooks/doc-saved.py",
    "hooks/doc-saved-as.py",
    "hooks/doc-saving.py",
    "hooks/doc-saving-as.py",
    "hooks/doc-synced.py",
)


ABANDONED_RUNTIME_IMPORTS = (
    "from easybim import close_stop",
    "from easybim import temp_phase_view",
    "from easybim import file_saved_notice",
    "from easybim import save_tvp_prompt",
    "import close_stop",
    "import temp_phase_view",
    "import file_saved_notice",
    "import save_tvp_prompt",
)


def _load_easybim_module(module_name):
    module_path = ROOT / "lib" / "easybim" / "{}.py".format(module_name)
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AbandonedBackgroundHookTests(unittest.TestCase):
    def test_temp_phase_close_stop_and_file_saved_background_files_are_removed(self):
        remaining = [
            relative_path
            for relative_path in ABANDONED_BACKGROUND_PATHS
            if (ROOT / relative_path).exists()
        ]

        self.assertEqual([], remaining)

    def test_runtime_code_no_longer_imports_abandoned_background_modules(self):
        failures = []
        for base in ("hooks", "lib", "EasyBIM.tab"):
            for path in (ROOT / base).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for token in ABANDONED_RUNTIME_IMPORTS:
                    if token in text:
                        failures.append("{} contains {}".format(path.relative_to(ROOT), token))

        self.assertEqual([], failures)

    def test_startup_jobs_pending_check_is_false_without_file_open_or_jobs(self):
        messages = _load_easybim_module("messages")

        with mock.patch.object(
            messages,
            "_load_file_open_trigger_state",
            return_value={"pending": False, "created_at": 0.0},
        ):
            with mock.patch.object(messages, "_load_startup_state", return_value={"jobs": []}):
                self.assertFalse(messages.has_pending_startup_jobs())

    def test_startup_jobs_pending_check_detects_file_open_trigger_or_jobs(self):
        messages = _load_easybim_module("messages")

        with mock.patch.object(
            messages,
            "_load_file_open_trigger_state",
            return_value={"pending": True, "created_at": 100.0},
        ):
            with mock.patch.object(messages, "_load_startup_state", return_value={"jobs": []}):
                self.assertTrue(messages.has_pending_startup_jobs())

        with mock.patch.object(
            messages,
            "_load_file_open_trigger_state",
            return_value={"pending": False, "created_at": 0.0},
        ):
            with mock.patch.object(messages, "_load_startup_state", return_value={"jobs": [{"id": 1}]}):
                self.assertTrue(messages.has_pending_startup_jobs())

    def test_coordination_review_detector_rearms_on_document_opening(self):
        hook_path = ROOT / "hooks" / "doc-opening.py"
        self.assertTrue(hook_path.exists())
        text = hook_path.read_text(encoding="utf-8")
        self.assertIn("register_passive_detector", text)

    def test_coordination_review_detector_is_disabled_after_startup_report(self):
        messages = _load_easybim_module("messages")

        with mock.patch.object(
            messages,
            "_build_coordination_detection_error_report",
            return_value={
                "doc_title": "Test",
                "link_map": {},
                "grouped": {},
                "link_totals": {},
                "total_matching_warnings": 0,
                "total_link_assignments": 0,
                "source": "passive_coordination_review_warning",
                "detection_error": True,
            },
        ):
            with mock.patch.object(messages, "_show_coordination_review_dialog", return_value=True):
                with mock.patch.object(messages, "_disable_passive_coordination_review_detector") as disabled:
                    messages._print_coordination_review_report(object())

        disabled.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
