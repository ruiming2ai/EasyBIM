"""The Family Types Revit layer, driven against fakes.

This file exists because parameter reading is where this tool has gone wrong
twice: first by locking most columns off a flag that means something else, and
then by rendering Yes/No parameters as 0 and 1 because the spec was not
recognised.  Neither could be caught by the state tests, which never see a
Revit object.  The fakes below reproduce each generation of the API - a 2021
host with ``GetSpecTypeId`` and ``ParameterType``, a 2022+ host with
``GetDataType``, and a host this code does not recognise at all - so the
detection rules are exercised without Revit.
"""

import importlib.util
import pathlib
import sys
import types
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (REPO_ROOT / "EasyBIM.tab" / "Family.panel"
               / "Family Types.pushbutton")
LIB_DIR = REPO_ROOT / "lib" / "easybim"


# --------------------------------------------------------------- fakes


class FakeEid(object):
    def __init__(self, value):
        self.Value = value
        self.IntegerValue = value


class FakeMaterial(object):
    def __init__(self, name, eid):
        self.Name = name
        self.Id = FakeEid(eid)


class FakeCollector(object):
    items = []

    def __init__(self, doc):
        del doc

    def OfClass(self, cls):
        del cls
        return list(FakeCollector.items)


class FakeTransaction(object):
    def __init__(self, doc, name):
        del doc
        self.name = name
        self.state = None

    def Start(self):
        self.state = "started"

    def Commit(self):
        self.state = "committed"

    def RollBack(self):
        self.state = "rolled back"


class Enum(str):
    """A CLR-ish enum member: it stringifies through ToString()."""

    def ToString(self):
        return str(self)


class ForgeTypeId(object):
    def __init__(self, type_id):
        self.TypeId = type_id

    def Equals(self, other):
        return isinstance(other, ForgeTypeId) and other.TypeId == self.TypeId


#: What Revit actually answers for a Yes/No parameter.
YES_NO_SPEC = ForgeTypeId("autodesk.spec.boolean:yesNo-1.0.0")
LENGTH_SPEC = ForgeTypeId("autodesk.spec.aec:length-2.0.0")
MATERIAL_SPEC = ForgeTypeId("autodesk.spec.reference:material-2.0.0")
INTEGER_SPEC = ForgeTypeId("autodesk.spec:int-1.0.0")


class ModernDefinition(object):
    """Revit 2022+: GetDataType, no ParameterType."""

    def __init__(self, name, spec):
        self.Name = name
        self._spec = spec

    def GetDataType(self):
        return self._spec


class LegacyDefinition(object):
    """Revit 2021: GetSpecTypeId for measurables, ParameterType for the rest."""

    def __init__(self, name, spec, parameter_type):
        self.Name = name
        self._spec = spec
        self.ParameterType = Enum(parameter_type)

    def GetSpecTypeId(self):
        if self._spec is None:
            raise Exception("not a measurable spec")
        return self._spec


class StrangeDefinition(object):
    """A host this code does not recognise: no spec, no enum."""

    def __init__(self, name):
        self.Name = name


class FakeParameter(object):
    def __init__(self, definition, storage, eid=1, is_instance=False,
                 formula=u"", reporting=False, read_only=False,
                 user_modifiable=True):
        self.Definition = definition
        self.StorageType = Enum(storage)
        self.Id = FakeEid(eid)
        self.IsInstance = is_instance
        self.Formula = formula
        self.IsDeterminedByFormula = bool(formula)
        self.IsReporting = reporting
        # Present, and deliberately never consulted.
        self.IsReadOnly = read_only
        self.UserModifiable = user_modifiable
        self.IsShared = False


class FakeType(object):
    def __init__(self, name, values=None, value_strings=None):
        self.Name = name
        self._values = dict(values or {})
        self._strings = dict(value_strings or {})

    def _key(self, parameter):
        return parameter.Definition.Name

    def HasValue(self, parameter):
        return self._key(parameter) in self._values

    def AsInteger(self, parameter):
        return self._values.get(self._key(parameter))

    def AsDouble(self, parameter):
        return self._values.get(self._key(parameter))

    def AsString(self, parameter):
        return self._values.get(self._key(parameter))

    def AsElementId(self, parameter):
        return self._values.get(self._key(parameter))

    def AsValueString(self, parameter):
        return self._strings.get(self._key(parameter))


class FakeManager(object):
    def __init__(self, parameters, family_types):
        self._parameters = parameters
        self.Types = list(family_types)
        self.CurrentType = self.Types[0] if self.Types else None
        self.log = []

    def GetParameters(self):
        return list(self._parameters)

    def NewType(self, name):
        family_type = FakeType(name)
        self.Types.append(family_type)
        self.CurrentType = family_type
        self.log.append(("new", name))
        return family_type

    def RenameCurrentType(self, name):
        self.log.append(("rename", self.CurrentType.Name, name))
        self.CurrentType.Name = name

    def DeleteCurrentType(self):
        self.log.append(("delete", self.CurrentType.Name))
        self.Types.remove(self.CurrentType)

    def SetValueString(self, parameter, text):
        self.log.append(("setvaluestring", self.CurrentType.Name,
                         parameter.Definition.Name, text))
        return True

    def Set(self, parameter, value):
        self.log.append(("set", self.CurrentType.Name,
                         parameter.Definition.Name, value))


class FakeDoc(object):
    def __init__(self, manager, elements=None):
        self.FamilyManager = manager
        self.Title = "Table.rfa"
        self._elements = dict(elements or {})

    def GetElement(self, eid):
        return self._elements.get(getattr(eid, "Value", None))


FAKE_DB = types.SimpleNamespace(
    Material=FakeMaterial,
    FilteredElementCollector=FakeCollector,
    Transaction=FakeTransaction,
    ElementId=FakeEid,
    SpecTypeId=types.SimpleNamespace(
        Boolean=types.SimpleNamespace(YesNo=YES_NO_SPEC)),
    ParameterType=types.SimpleNamespace(YesNo=Enum("YesNo"),
                                        Material=Enum("Material")),
)


def _load():
    for name, attrs in (("pyrevit", {"DB": FAKE_DB}), ("easybim", {})):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module

    def load_as(dotted, path):
        spec = importlib.util.spec_from_file_location(dotted, str(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[dotted] = module
        spec.loader.exec_module(module)
        return module

    load_as("easybim.compat", LIB_DIR / "compat.py")
    if str(COMMAND_DIR) not in sys.path:
        sys.path.insert(0, str(COMMAND_DIR))
    state = load_as("family_types_state",
                    COMMAND_DIR / "family_types_state.py")
    revit = load_as("family_types_revit",
                    COMMAND_DIR / "family_types_revit.py")
    return state, revit


st, rv = _load()


def read(parameters, family_types, elements=None):
    manager = FakeManager(parameters, family_types)
    doc = FakeDoc(manager, elements)
    columns, rows, notes = rv.read_family_table(doc)
    return doc, manager, columns, rows, notes


def column_for(columns, name):
    for column in st.param_columns(columns):
        if column.param_name == name:
            return column
    raise AssertionError("no column for %s" % name)


class YesNoDetectionTests(unittest.TestCase):
    """The bug that shipped twice: Yes/No columns rendering as 0 and 1."""

    def _detect(self, definition, family_types=None, values=None):
        parameter = FakeParameter(definition, "Integer")
        types_ = family_types
        if types_ is None:
            types_ = [FakeType("A", values or {}, values or {})]
        return rv._is_yes_no(definition, parameter, types_)

    def test_a_modern_host_is_recognised_by_the_spec_object(self):
        self.assertTrue(self._detect(ModernDefinition("Load", YES_NO_SPEC)))

    def test_a_legacy_host_is_recognised_by_the_parameter_type_enum(self):
        self.assertTrue(
            self._detect(LegacyDefinition("Load", None, "YesNo")))

    def test_an_unrecognised_spec_falls_back_to_how_revit_renders_it(self):
        # The route that needs no spec knowledge at all, and the one that
        # would have caught this bug: 0/1 shown as a word is a checkbox.
        definition = StrangeDefinition("Load")
        family_types = [FakeType("A", {"Load": 1}, {"Load": "Yes"})]
        self.assertTrue(rv._is_yes_no(
            definition, FakeParameter(definition, "Integer"), family_types))

    def test_the_fallback_works_in_any_language(self):
        definition = StrangeDefinition("Charge")
        family_types = [FakeType("A", {"Charge": 0}, {"Charge": "Non"})]
        self.assertTrue(rv._is_yes_no(
            definition, FakeParameter(definition, "Integer"), family_types))

    def test_a_plain_integer_is_not_mistaken_for_a_checkbox(self):
        definition = ModernDefinition("Shelves", INTEGER_SPEC)
        family_types = [FakeType("A", {"Shelves": 1}, {"Shelves": "1"})]
        self.assertFalse(rv._is_yes_no(
            definition, FakeParameter(definition, "Integer"), family_types))

    def test_an_integer_that_renders_with_a_unit_is_not_a_checkbox(self):
        # "1 mm" is a number wearing a suffix, not a Yes.
        definition = StrangeDefinition("Poles")
        family_types = [FakeType("A", {"Poles": 1}, {"Poles": "1 mm"})]
        self.assertFalse(rv._is_yes_no(
            definition, FakeParameter(definition, "Integer"), family_types))

    def test_an_integer_outside_zero_and_one_is_never_a_checkbox(self):
        definition = StrangeDefinition("Shelves")
        family_types = [FakeType("A", {"Shelves": 7}, {"Shelves": "7"})]
        self.assertFalse(rv._is_yes_no(
            definition, FakeParameter(definition, "Integer"), family_types))

    def test_a_yes_no_column_reaches_the_grid_as_a_bool(self):
        # End to end: the column is flagged, and the cell holds True/False
        # rather than the 0 and 1 the user was shown.
        parameters = [FakeParameter(ModernDefinition("Load", YES_NO_SPEC),
                                    "Integer", eid=103, is_instance=True)]
        family_types = [FakeType("A", {"Load": 1}, {"Load": "Yes"}),
                        FakeType("B", {"Load": 0}, {"Load": "No"})]
        _, _, columns, rows, _ = read(parameters, family_types)
        column = column_for(columns, "Load")
        self.assertTrue(column.is_yes_no)
        self.assertIs(True, getattr(rows[0], column.attr))
        self.assertIs(False, getattr(rows[1], column.attr))
        self.assertEqual(u"", column.lock_reason)


class LockingTests(unittest.TestCase):
    def test_the_parameter_read_only_flags_never_lock_a_column(self):
        # The first bug. Both flags are set on this parameter and neither may
        # have any effect - a family parameter is written through
        # FamilyManager.Set, not Parameter.Set.
        parameters = [FakeParameter(ModernDefinition("Comments", None),
                                    "String", read_only=True,
                                    user_modifiable=False)]
        _, _, columns, _, _ = read(parameters, [FakeType("A")])
        self.assertEqual(u"", column_for(columns, "Comments").lock_reason)

    def test_a_formula_locks(self):
        parameters = [FakeParameter(ModernDefinition("Vol", LENGTH_SPEC),
                                    "Double", formula="Width * 2")]
        _, _, columns, _, _ = read(parameters, [FakeType("A")])
        self.assertEqual(st.LOCK_FORMULA,
                         column_for(columns, "Vol").lock_reason)

    def test_a_reporting_parameter_locks(self):
        parameters = [FakeParameter(ModernDefinition("Span", LENGTH_SPEC),
                                    "Double", reporting=True)]
        _, _, columns, _, _ = read(parameters, [FakeType("A")])
        self.assertEqual(st.LOCK_REPORTING,
                         column_for(columns, "Span").lock_reason)

    def test_a_material_stays_editable_but_other_references_do_not(self):
        parameters = [
            FakeParameter(ModernDefinition("Finish", MATERIAL_SPEC),
                          "ElementId", eid=105),
            FakeParameter(ModernDefinition("Head", ForgeTypeId(
                "autodesk.spec.reference:familyType-2.0.0")),
                "ElementId", eid=106),
        ]
        family_types = [FakeType("A", {"Finish": FakeEid(5)})]
        FakeCollector.items = [FakeMaterial("Oak", 5)]
        _, _, columns, _, _ = read(parameters, family_types,
                                   elements={5: FakeMaterial("Oak", 5)})
        self.assertEqual(u"", column_for(columns, "Finish").lock_reason)
        self.assertEqual(st.LOCK_ELEMENT_REF,
                         column_for(columns, "Head").lock_reason)


class ApplyTests(unittest.TestCase):
    def test_a_checkbox_writes_an_integer_back(self):
        parameter = FakeParameter(ModernDefinition("Load", YES_NO_SPEC),
                                  "Integer", eid=103)
        family_types = [FakeType("A", {"Load": 0}, {"Load": "No"})]
        doc, manager, columns, rows, _ = read([parameter], family_types)
        column = column_for(columns, "Load")
        st.apply_cell_edit(rows[0], column, True)
        result = rv.apply_changes(doc, st.compute_staged_changes(rows, columns))
        self.assertEqual([], result.problems)
        self.assertIn(("set", "A", "Load", 1), manager.log)

    def test_a_bulk_toggle_writes_every_selected_type(self):
        parameter = FakeParameter(ModernDefinition("Load", YES_NO_SPEC),
                                  "Integer", eid=103)
        family_types = [FakeType("A", {"Load": 0}, {"Load": "No"}),
                        FakeType("B", {"Load": 0}, {"Load": "No"})]
        doc, manager, columns, rows, _ = read([parameter], family_types)
        column = column_for(columns, "Load")
        st.propagate_cell_edit(rows, column, True)
        result = rv.apply_changes(doc, st.compute_staged_changes(rows, columns))
        self.assertEqual([], result.problems)
        self.assertEqual(2, result.values)
        self.assertIn(("set", "A", "Load", 1), manager.log)
        self.assertIn(("set", "B", "Load", 1), manager.log)


if __name__ == "__main__":
    unittest.main()
