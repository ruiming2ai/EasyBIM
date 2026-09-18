"""Pin the existing local-axis offset behavior through the real function."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = ROOT / "EasyBIM.tab/Misc Tools.panel/Batch Duplicate Host.pushbutton/batch_duplicate_host_revit.py"

class V:
    def __init__(self, x, y, z): self.X, self.Y, self.Z = x, y, z
    def Multiply(self, s): return V(self.X*s, self.Y*s, self.Z*s)
    def Add(self, v): return V(self.X+v.X, self.Y+v.Y, self.Z+v.Z)

class PlacementContract(unittest.TestCase):
    def test_rotated_target_offsets_follow_local_axes(self):
        tree = ast.parse(PATH.read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_get_target_offset_in_host_coordinates")
        ns = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(PATH), "exec"), ns)
        target = type("Target", (), {"target_local_x_axis": V(0,1,0),
                                    "target_local_y_axis": V(-1,0,0),
                                    "target_local_z_axis": V(0,0,1)})()
        result = ns[fn.name](target, V(2,3,4))
        self.assertEqual((-3,2,4), (result.X, result.Y, result.Z))
        target.target_local_x_axis = None
        self.assertIsNone(ns[fn.name](target, V(2,3,4)))
