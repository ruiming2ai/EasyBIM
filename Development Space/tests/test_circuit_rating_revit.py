import ast
import pathlib
import types
import unittest


COMMAND_DIR = (pathlib.Path(__file__).resolve().parents[2] / "EasyBIM.tab" /
               "Misc Tools.panel" / "Circuiting.pulldown" /
               "Update Circuit Rating.pushbutton")
SOURCE_PATH = COMMAND_DIR / "circuit_rating_revit.py"


def _load_scan_functions():
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    keep_names = {
        "_storage_name", "_definition_name", "_is_current_spec",
        "_element_type", "_element_parameters", "_element_readings",
        "_collect_target_options",
    }
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets
                       if isinstance(target, ast.Name)]
            if set(targets) & {"RATING_BIP_NAMES", "_NUMERIC_STORAGE",
                               "_USABLE_STORAGE"}:
                nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in keep_names:
            nodes.append(node)
    namespace = {
        "safe_text": lambda value: u"" if value is None else str(value),
        "eid_to_int": lambda value: int(value),
        "DB": types.SimpleNamespace(
            BuiltInParameter=types.SimpleNamespace(
                RBS_ELEC_CIRCUIT_RATING_PARAM=-1001,
                RBS_ELEC_CIRCUIT_FRAME_PARAM=-1002)),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH),
                 "exec"), namespace)
    namespace["_param_value"] = lambda param, doc: param.value
    return namespace


class _Parameter(object):
    def __init__(self, key, name, value, spec, storage="Double",
                 read_only=False):
        self.Id = key
        self.value = value
        self.StorageType = storage
        self.IsReadOnly = read_only
        self.Definition = types.SimpleNamespace(
            Name=name,
            GetDataType=lambda: types.SimpleNamespace(TypeId=spec),
        )


class _Element(object):
    def __init__(self, parameters):
        self.Parameters = parameters

    def GetTypeId(self):
        return None


class _Circuit(object):
    def __init__(self, parameters):
        self.Parameters = parameters


class CurrentParameterScanTests(unittest.TestCase):
    def setUp(self):
        self.api = _load_scan_functions()

    def test_source_scan_keeps_only_populated_current_parameters(self):
        current = _Parameter(1, u"Current Rating", 100.0,
                             u"autodesk.spec.aec:electricalCurrent-2.0.0")
        voltage = _Parameter(2, u"Voltage", 480.0,
                             u"autodesk.spec.aec:electricalVoltage-2.0.0")
        generic = _Parameter(3, u"Capacity", 225.0,
                             u"autodesk.spec.aec:number-1.0.0")
        text = _Parameter(4, u"Rating Text", 100.0,
                          u"autodesk.spec.aec:electricalCurrent-2.0.0",
                          storage="String")
        source_names = {}

        readings = self.api["_element_readings"](
            _Element([current, voltage, generic, text]), None, source_names)

        self.assertEqual({u"Current Rating": 100.0}, readings)
        self.assertEqual({u"Current Rating"}, set(source_names))

    def test_target_scan_keeps_writable_current_parameters_found_on_any_circuit(self):
        rating = _Parameter(-1001, u"Rating", 100.0,
                            u"autodesk.spec.aec:electricalCurrent-2.0.0")
        frame = _Parameter(-1002, u"Frame", 125.0,
                           u"autodesk.spec.aec:electricalCurrent-2.0.0")
        voltage = _Parameter(2, u"Voltage", 480.0,
                             u"autodesk.spec.aec:electricalVoltage-2.0.0")
        generic = _Parameter(3, u"Capacity", 225.0,
                             u"autodesk.spec.aec:number-1.0.0")
        read_only = _Parameter(4, u"Read Only Current", 60.0,
                               u"autodesk.spec.aec:electricalCurrent-2.0.0",
                               read_only=True)

        options = self.api["_collect_target_options"]([
            _Circuit([rating, voltage, generic, read_only]),
            _Circuit([frame]),
        ])

        self.assertEqual([u"Frame", u"Rating"],
                         sorted(option["label"] for option in options))
        self.assertTrue(all(option["is_default"] for option in options))


if __name__ == "__main__":
    unittest.main()
