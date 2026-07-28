import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "lib"
    / "easybim"
    / "auto_update.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("auto_update", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AutoUpdateTests(unittest.TestCase):
    def test_is_repo_compliant_requires_main_clean_and_matching_remote(self):
        module = _load_module()

        self.assertTrue(
            module.is_repo_compliant(
                {
                    "branch": "main",
                    "head": "abc123",
                    "remote_head": "abc123",
                    "tracked_dirty": False,
                    "has_untracked": False,
                }
            )
        )

        self.assertFalse(
            module.is_repo_compliant(
                {
                    "branch": "Auto-Update",
                    "head": "abc123",
                    "remote_head": "abc123",
                    "tracked_dirty": False,
                    "has_untracked": False,
                }
            )
        )

        self.assertFalse(
            module.is_repo_compliant(
                {
                    "branch": "main",
                    "head": "abc123",
                    "remote_head": "def456",
                    "tracked_dirty": False,
                    "has_untracked": False,
                }
            )
        )

        self.assertFalse(
            module.is_repo_compliant(
                {
                    "branch": "main",
                    "head": "abc123",
                    "remote_head": "abc123",
                    "tracked_dirty": True,
                    "has_untracked": False,
                }
            )
        )

        self.assertFalse(
            module.is_repo_compliant(
                {
                    "branch": "main",
                    "head": "abc123",
                    "remote_head": "abc123",
                    "tracked_dirty": False,
                    "has_untracked": True,
                }
            )
        )

    def test_classify_sync_result_distinguishes_noop_update_and_repair(self):
        module = _load_module()

        noop_result = module.classify_sync_result(
            before_state={
                "branch": "main",
                "head": "abc123",
                "remote_head": "abc123",
                "tracked_dirty": False,
                "has_untracked": False,
            },
            after_state={
                "branch": "main",
                "head": "abc123",
                "remote_head": "abc123",
                "tracked_dirty": False,
                "has_untracked": False,
            },
        )
        self.assertEqual(noop_result["status"], "no_op")

        updated_result = module.classify_sync_result(
            before_state={
                "branch": "main",
                "head": "old111",
                "remote_head": "new222",
                "tracked_dirty": False,
                "has_untracked": False,
            },
            after_state={
                "branch": "main",
                "head": "new222",
                "remote_head": "new222",
                "tracked_dirty": False,
                "has_untracked": False,
            },
        )
        self.assertEqual(updated_result["status"], "updated")

        repaired_result = module.classify_sync_result(
            before_state={
                "branch": "feature",
                "head": "same333",
                "remote_head": "same333",
                "tracked_dirty": True,
                "has_untracked": True,
            },
            after_state={
                "branch": "main",
                "head": "same333",
                "remote_head": "same333",
                "tracked_dirty": False,
                "has_untracked": False,
            },
        )
        self.assertEqual(repaired_result["status"], "repaired")

    def test_classify_fetch_failure_recognizes_silent_startup_skip_cases(self):
        module = _load_module()

        self.assertEqual(
            module.classify_fetch_failure(
                returncode=128,
                stderr_text="fatal: unable to access 'https://example': Could not resolve host: github.com",
            ),
            "network",
        )
        self.assertEqual(
            module.classify_fetch_failure(
                returncode=128,
                stderr_text="fatal: unable to access 'https://example': Failed to connect to github.com port 443",
            ),
            "network",
        )
        self.assertEqual(
            module.classify_fetch_failure(
                returncode=128,
                stderr_text="fatal: Authentication failed for 'https://example'",
            ),
            "error",
        )

    def test_should_skip_startup_returns_true_after_guard_is_set(self):
        module = _load_module()

        self.assertFalse(module.should_skip_startup({"attempted": False}))
        self.assertFalse(module.should_skip_startup(None))
        self.assertTrue(module.should_skip_startup({"attempted": True}))


if __name__ == "__main__":
    unittest.main()
