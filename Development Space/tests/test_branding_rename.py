import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _forbidden_terms():
    return [
        "RL" + "_Tools",
        "RL" + " Tools",
        "RL" + "Tools",
        "rl" + "tools",
        "rl" + "-tools",
        "RL" + "-Tools",
        "RL" + "TOOLS",
        "PyRevit_" + "RL" + "_Tools",
    ]


class BrandingRenameTests(unittest.TestCase):
    def test_tracked_repo_files_do_not_reference_previous_brand(self):
        result = subprocess.check_output(
            ["git", "ls-files"],
            cwd=str(ROOT),
            universal_newlines=True,
        )
        forbidden = _forbidden_terms()
        binary_suffixes = {
            ".dark.png",
            ".png",
            ".ico",
            ".jpg",
            ".jpeg",
            ".gif",
            ".pyc",
            ".dll",
        }
        binary_file_names = {".DS_Store"}
        failures = []

        for relative_path in result.splitlines():
            if any(term in relative_path for term in forbidden):
                failures.append(relative_path)
                continue

            path = ROOT / relative_path
            if path.name in binary_file_names:
                continue

            if any(relative_path.endswith(suffix) for suffix in binary_suffixes):
                continue

            if not path.exists():
                continue

            text = path.read_text(encoding="utf-8")
            for term in forbidden:
                if term in text:
                    failures.append("{} contains {}".format(relative_path, term))
                    break

        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
