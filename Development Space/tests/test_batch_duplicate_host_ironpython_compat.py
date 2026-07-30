import ast
import pathlib
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Batch Duplicate Host.pushbutton"
)


class BatchDuplicateHostIronPythonCompatibilityTests(unittest.TestCase):
    def test_command_python_files_avoid_python3_only_constructs(self):
        self.maxDiff = None
        failures = []

        for path in sorted(COMMAND_DIR.glob("*.py")):
            source = path.read_text()
            tree = ast.parse(source, filename=str(path))

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "dataclasses":
                    failures.append("{} imports dataclasses".format(path.name))

                if isinstance(node, ast.ClassDef):
                    for decorator in node.decorator_list:
                        if getattr(decorator, "id", None) == "dataclass":
                            failures.append("{} uses @dataclass on {}".format(path.name, node.name))

                if hasattr(ast, "AnnAssign") and isinstance(node, ast.AnnAssign):
                    failures.append("{} uses variable annotations".format(path.name))

                if isinstance(node, ast.FunctionDef):
                    if node.returns is not None:
                        failures.append("{} uses return annotations".format(path.name))
                    for arg in list(node.args.args) + list(node.args.kwonlyargs):
                        if arg.annotation is not None:
                            failures.append("{} uses argument annotations".format(path.name))

                if hasattr(ast, "JoinedStr") and isinstance(node, ast.JoinedStr):
                    failures.append("{} uses f-strings".format(path.name))

        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
