"""Drive the Families Downgrade engines against fake Revit objects.

The decisions that matter here cannot be settled by reading the source: the
detail-level retry when a scratch view exports nothing, the exported file
being found by folder diff, the parameter ladder picking a different rung on
a 2021-shaped API than on a 2026-shaped one, the import self-check re-reading
a wrongly scaled file, and the connector face search. Each is exercised on
fakes shaped like the API of the release in question, and the fake ``DB``
namespace is reshaped between tests to stand in for a different Revit.

Stubs are installed for the run and ``sys.modules`` restored afterwards, as
``test_families_transfer_links.py`` does.
"""

import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import types
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (
    REPO_ROOT / "EasyBIM.tab" / "Family.panel" / "Families Downgrade.pushbutton"
)
LIB_DIR = REPO_ROOT / "lib" / "easybim"


# --------------------------------------------------------------------------
# fake .NET / Revit
# --------------------------------------------------------------------------


class FakeEnumValue(object):
    def __init__(self, enum_name, name):
        self.enum_name = enum_name
        self.name = name

    def ToString(self):
        return self.name

    def __eq__(self, other):
        return isinstance(other, FakeEnumValue) and other.name == self.name and \
            other.enum_name == self.enum_name

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((self.enum_name, self.name))

    def __repr__(self):
        return "%s.%s" % (self.enum_name, self.name)


def make_enum(enum_name, names):
    return type(enum_name, (object,), dict((n, FakeEnumValue(enum_name, n)) for n in names))


class FakeForgeTypeId(object):
    def __init__(self, type_id=""):
        self.TypeId = type_id

    def __eq__(self, other):
        return isinstance(other, FakeForgeTypeId) and other.TypeId == self.TypeId

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.TypeId)

    def __repr__(self):
        return "ForgeTypeId(%r)" % self.TypeId


class FakeProperty(object):
    def __init__(self, name, value):
        self.Name = name
        self._value = value

    def GetValue(self, _obj, _index):
        return self._value


class FakeClrType(object):
    """What ``clr.GetClrType`` hands back for a fake static class."""

    def __init__(self, python_type):
        self._type = python_type

    def GetProperties(self, _flags):
        found = []
        for name, value in sorted(vars(self._type).items()):
            if isinstance(value, FakeForgeTypeId):
                found.append(FakeProperty(name, value))
        return found

    def GetNestedTypes(self):
        nested = []
        for name, value in sorted(vars(self._type).items()):
            if isinstance(value, type) and not name.startswith("_"):
                nested.append(FakeClrType(value))
        return nested

    @property
    def Name(self):
        return self._type.__name__


class FakeEnumStatic(object):
    @staticmethod
    def GetNames(enum_type):
        target = getattr(enum_type, "_type", enum_type)
        return [name for name, value in vars(target).items()
                if isinstance(value, FakeEnumValue)]

    @staticmethod
    def GetName(enum_type, value):
        return None

    @staticmethod
    def Parse(enum_type, name):
        target = getattr(enum_type, "_type", enum_type)
        return getattr(target, name)


class FakeGuid(object):
    def __init__(self, text):
        self.text = text


class FakeXYZ(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)


class FakeBBox(object):
    def __init__(self, low, high):
        self.Min = FakeXYZ(*low)
        self.Max = FakeXYZ(*high)


class FakeElementId(object):
    InvalidElementId = None

    def __init__(self, value):
        self.Value = value
        self.IntegerValue = value

    def __eq__(self, other):
        return isinstance(other, FakeElementId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


FakeElementId.InvalidElementId = FakeElementId(-1)


class FakeStatus(object):
    def __init__(self, name):
        self.name = name

    def ToString(self):
        return self.name

    def __eq__(self, other):
        return isinstance(other, FakeStatus) and other.name == self.name

    def __ne__(self, other):
        return not self.__eq__(other)


class FakeTransactionStatus(object):
    Committed = FakeStatus("Committed")
    RolledBack = FakeStatus("RolledBack")


class FakeHandlingOptions(object):
    def SetFailuresPreprocessor(self, _p):
        return self

    def SetClearAfterRollback(self, _v):
        return self


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.started = False

    def Start(self):
        self.started = True

    def Commit(self):
        return FakeTransactionStatus.Committed

    def RollBack(self):
        return FakeTransactionStatus.RolledBack

    def GetFailureHandlingOptions(self):
        return FakeHandlingOptions()

    def SetFailureHandlingOptions(self, _o):
        pass


class FakeElement(object):
    def __init__(self, value, hideable=True, bbox=None, name=""):
        self.Id = FakeElementId(value)
        self._hideable = hideable
        self._bbox = bbox
        self.Name = name

    def CanBeHidden(self, _view):
        return self._hideable

    def get_BoundingBox(self, _view):
        return self._bbox


class FakeView3D(FakeElement):
    def __init__(self, value):
        FakeElement.__init__(self, value)
        self.DetailLevel = None
        self.ViewTemplateId = None
        self.hidden = []
        self.IsTemplate = False

    def HideElements(self, ids):
        self.hidden.extend(list(ids))


class FakeCollector(object):
    def __init__(self, doc, view_id=None):
        self.doc = doc
        self.view_id = view_id
        self._items = list(getattr(doc, "elements", []) or [])

    def WhereElementIsNotElementType(self):
        return self

    def OfClass(self, cls):
        self._items = [item for item in self._items if isinstance(item, cls)]
        return self

    def __iter__(self):
        return iter(self._items)


class FakeSolid(object):
    def __init__(self, volume):
        self.Volume = volume
        self.Faces = []


class FakeGeometryInstance(object):
    def __init__(self, items):
        self._items = items

    def GetInstanceGeometry(self):
        return list(self._items)


class FakeExportDoc(object):
    """A family document for the export tests: Export writes what the plan says."""

    def __init__(self, folder, plan):
        self.folder = folder
        # detail level name -> "bodies" | "empty" | "none"
        self.plan = plan
        self.elements = []
        self.deleted = []
        self.views_created = []
        self.export_levels = []
        self._next_id = 1000

    def GetElement(self, element_id):
        for element in self.elements + self.views_created:
            if element.Id == element_id:
                return element
        return None

    def Regenerate(self):
        pass

    def Delete(self, element_id):
        self.deleted.append(element_id)

    def Export(self, folder, name, views, options):
        view = self.GetElement(list(views)[0])
        level = view.DetailLevel.name if view is not None and view.DetailLevel else "Fine"
        self.export_levels.append(level)
        outcome = self.plan.get(level, "none")
        if outcome == "none":
            return False
        # Revit's own name for a single-view export carries a view suffix.
        path = os.path.join(folder, "{}-3D View 1.sat".format(name))
        with open(path, "wb") as handle:
            handle.write(b"700 0 1 0\nheader\n")
            if outcome == "bodies":
                handle.write(b"body $-1 -1 $-1 $1 $-1 $-1 #\n")
        return True

    def new_view(self):
        self._next_id += 1
        view = FakeView3D(self._next_id)
        self.views_created.append(view)
        return view


class FakeView3DStatic(object):
    @staticmethod
    def CreateIsometric(doc, _view_type_id):
        return doc.new_view()


class FakeCategory(object):
    def __init__(self, name, builtin_name="", category_type="Model", subcategories=None):
        self.Name = name
        self.Id = FakeElementId(hash(name) % 100000)
        self.CategoryType = FakeEnumValue("CategoryType", category_type)
        self.SubCategories = list(subcategories or [])
        self.LineColor = None
        self.Material = None
        self._builtin_name = builtin_name

    @property
    def BuiltInCategory(self):
        return FakeEnumValue("BuiltInCategory", self._builtin_name)


class FakeParameterDefinition(object):
    def __init__(self, name):
        self.Name = name


class FakeParameter(object):
    def __init__(self, builtin="", storage="Integer", value=0, read_only=False, definition=None):
        self.Definition = definition or FakeParameterDefinition(builtin)
        self.Definition.BuiltInParameter = FakeEnumValue("BuiltInParameter", builtin or "INVALID")
        self.StorageType = FakeEnumValue("StorageType", storage)
        self.HasValue = True
        self.IsReadOnly = read_only
        self._value = value
        self.written = []

    def AsInteger(self):
        return int(self._value)

    def AsDouble(self):
        return float(self._value)

    def AsString(self):
        return self._value

    def AsElementId(self):
        return self._value

    def AsValueString(self):
        return str(self._value)

    def Set(self, value):
        self.written.append(value)
        self._value = value
        return True


class FakeSolidElement(FakeElement):
    """A GenericForm-shaped element with a material and a Visible parameter."""

    def __init__(self, value, is_solid=True, material_param=None, visible_param=None,
                 subcategory=None, bbox=None):
        FakeElement.__init__(self, value, bbox=bbox)
        self.IsSolid = is_solid
        self._params = {"MATERIAL_ID_PARAM": material_param, "IS_VISIBLE_PARAM": visible_param}
        self.Subcategory = subcategory

    def get_Parameter(self, builtin):
        return self._params.get(builtin.name)


class FakeFamilyParameter(object):
    def __init__(self, name):
        self.Definition = FakeParameterDefinition(name)


class FakeFamilyManager(object):
    def __init__(self, associations=None):
        self._associations = associations or {}
        self.Types = []
        self.CurrentType = None

    def GetAssociatedFamilyParameter(self, parameter):
        return self._associations.get(parameter)


class FakeVisibility(object):
    IsShownInCoarse = True
    IsShownInMedium = True
    IsShownInFine = True
    IsShownInPlanRCPCut = True
    IsShownInFrontBack = True
    IsShownInLeftRight = True
    IsShownInTopBottom = True
    IsShownOnlyWhenCut = False


class FakePlanarFace(object):
    def __init__(self, origin, normal, inside_points):
        self.Origin = FakeXYZ(*origin)
        self.FaceNormal = FakeXYZ(*normal)
        self._inside = [tuple(p) for p in inside_points]
        self.Reference = ("ref", origin, normal)

    def Project(self, xyz):
        point = (xyz.X, xyz.Y, xyz.Z)
        distance = 0.0 if point in self._inside else 5.0
        return types.SimpleNamespace(Distance=distance)


class FakeImportDoc(object):
    """A target family document whose Import scales by the unit it was given."""

    def __init__(self, sat_solids=None):
        self.elements = []
        self.deleted = []
        self.moved = []
        self.imports = []
        self._next = 5000
        self.sat_solids = sat_solids if sat_solids is not None else [Solid(2.0), Solid(3.0)]

    def Import(self, path, options, view):
        unit = getattr(options, "Unit", None)
        unit_name = unit.name if unit is not None else "Default"
        self.imports.append(unit_name)
        self._next += 1
        # Default reads the header as inches: twelve times too small.
        scale = 1.0 if unit_name == "Foot" else (1.0 / 12.0)
        bbox = FakeBBox((0, 0, 0), (12.0 * scale, 6.0 * scale, 3.0 * scale))
        instance = FakeImportInstance(self._next, bbox, self.sat_solids)
        self.elements.append(instance)
        return instance.Id

    def GetElement(self, element_id):
        for element in self.elements:
            if element.Id == element_id:
                return element
        return None

    def Regenerate(self):
        pass

    def Delete(self, element_id):
        self.deleted.append(element_id)


class FakeImportInstance(FakeElement):
    def __init__(self, value, bbox, solids):
        FakeElement.__init__(self, value, bbox=bbox)
        self._solids = solids
        self._type_id = FakeElementId(value + 100000)

    def get_Geometry(self, _options):
        return [GeometryInstance(list(self._solids))]

    def GetTypeId(self):
        return self._type_id


class FakeFreeFormStatic(object):
    created = []
    refuse = set()

    @classmethod
    def Create(cls, doc, solid):
        if solid in cls.refuse:
            raise RuntimeError("cannot create")
        element = FakeElement(9000 + len(cls.created))
        element.solid = solid
        cls.created.append(element)
        return element


class FakeConnectorStatic(object):
    calls = []

    @classmethod
    def CreateDuctConnector(cls, doc, system_type, shape, reference):
        cls.calls.append(("duct", system_type, shape, reference))
        return FakeElement(7001)

    @classmethod
    def CreatePipeConnector(cls, doc, system_type, reference):
        cls.calls.append(("pipe", system_type, reference))
        return FakeElement(7002)

    @classmethod
    def CreateElectricalConnector(cls, doc, system_type, reference):
        cls.calls.append(("electrical", system_type, reference))
        return FakeElement(7003)

    @classmethod
    def CreateCableTrayConnector(cls, doc, reference):
        cls.calls.append(("cabletray", reference))
        return FakeElement(7004)

    @classmethod
    def CreateConduitConnector(cls, doc, reference):
        cls.calls.append(("conduit", reference))
        return FakeElement(7005)


class FakeSolidUtils(object):
    @staticmethod
    def SplitVolumes(solid):
        return [solid]


class FakeGroupTypeId(object):
    Geometry = FakeForgeTypeId("autodesk.parameter.group:geometry-1.0.0")
    IdentityData = FakeForgeTypeId("autodesk.parameter.group:identity-data-1.0.0")
    General = FakeForgeTypeId("autodesk.parameter.group:general-1.0.0")
    Termination = FakeForgeTypeId("autodesk.parameter.group:termination-1.0.0")


class FakeSpecString(object):
    Text = FakeForgeTypeId("autodesk.spec:spec.string-2.0.0")
    Url = FakeForgeTypeId("autodesk.spec.string:url-2.0.0")


class FakeSpecInt(object):
    Integer = FakeForgeTypeId("autodesk.spec:spec.int-2.0.0")


class FakeSpecTypeId(object):
    Length = FakeForgeTypeId("autodesk.spec.aec:length-2.0.0")
    AirFlow = FakeForgeTypeId("autodesk.spec.aec.hvac:air-flow-2.0.0")
    Number = FakeForgeTypeId("autodesk.spec.aec:number-2.0.0")
    String = FakeSpecString
    Int = FakeSpecInt


class FakeSpecTypeId2021(object):
    """2021 has SpecTypeId for measurable specs only."""
    Length = FakeForgeTypeId("autodesk.spec.aec:length-2.0.0")
    AirFlow = FakeForgeTypeId("autodesk.spec.aec.hvac:air-flow-2.0.0")
    Number = FakeForgeTypeId("autodesk.spec.aec:number-2.0.0")


ParameterType2021 = make_enum("ParameterType", [
    "Text", "URL", "MultilineText", "Integer", "NumberOfPoles", "YesNo", "Material",
    "Image", "LoadClassification", "FamilyType", "Number", "Length", "Area",
    "HVACAirflow", "ElectricalApparentPower", "Invalid"])
BuiltInParameterGroup2021 = make_enum("BuiltInParameterGroup", [
    "PG_GEOMETRY", "PG_IDENTITY_DATA", "PG_TEXT", "PG_TERMINTATION", "INVALID"])
UnitType2021 = make_enum("UnitType", ["UT_Length", "UT_HVAC_Airflow", "UT_Number"])
BuiltInCategory = make_enum("BuiltInCategory", [
    "OST_MechanicalEquipment", "OST_GenericModel", "OST_PlumbingFixtures",
    "OST_SpecialityEquipment", "OST_Doors"])
BuiltInParameter = make_enum("BuiltInParameter", [
    "MATERIAL_ID_PARAM", "IS_VISIBLE_PARAM", "GEOM_VISIBILITY_PARAM",
    "FAMILY_HOSTING_BEHAVIOR", "FAMILY_WORK_PLANE_BASED", "FAMILY_ALWAYS_VERTICAL",
    "FAMILY_SHARED", "FAMILY_ALLOW_CUT_WITH_VOIDS", "ROOM_CALCULATION_POINT",
    "FAMILY_KEEP_TEXT_READABLE", "FAMILY_ROUNDCONNECTOR_DIMENSION",
    "FAMILY_CONTENT_PART_TYPE", "OMNICLASS_CODE", "OMNICLASS_DESCRIPTION",
    "ELEM_REFERENCE_NAME", "DATUM_PLANE_DEFINES_ORIGIN", "CONNECTOR_ANGLE"])


class FakeUnitUtils2021(object):
    _by_id = {
        "autodesk.spec.aec:length-2.0.0": UnitType2021.UT_Length,
        "autodesk.spec.aec.hvac:air-flow-2.0.0": UnitType2021.UT_HVAC_Airflow,
        "autodesk.spec.aec:number-2.0.0": UnitType2021.UT_Number,
    }

    @classmethod
    def GetUnitType(cls, forge_id):
        try:
            return cls._by_id[forge_id.TypeId]
        except KeyError:
            raise RuntimeError("not a spec")

    @classmethod
    def IsSpec(cls, forge_id):
        return forge_id.TypeId in cls._by_id


class FakeSpecUtils2026(object):
    valid = set(v.TypeId for v in (FakeSpecTypeId.Length, FakeSpecTypeId.AirFlow,
                                   FakeSpecTypeId.Number, FakeSpecString.Text,
                                   FakeSpecString.Url, FakeSpecInt.Integer))

    @classmethod
    def IsValidDataType(cls, forge_id):
        return forge_id.TypeId in cls.valid


class FakeParameterUtils2026(object):
    valid_groups = set(v.TypeId for v in (FakeGroupTypeId.Geometry, FakeGroupTypeId.IdentityData,
                                          FakeGroupTypeId.General, FakeGroupTypeId.Termination))

    @classmethod
    def IsBuiltInGroup(cls, forge_id):
        return forge_id.TypeId in cls.valid_groups


class FakeDefinition2021(object):
    ParameterType = None
    ParameterGroup = None


class FakeDefinition2026(object):
    def GetDataType(self):
        return None

    def GetGroupTypeId(self):
        return None


class FakeSATExportOptions(object):
    pass


class FakeDWGExportOptions(object):
    pass


class FakeImportOptions(object):
    def __init__(self):
        self.Unit = None
        self.Placement = None


ImportUnit = make_enum("ImportUnit", ["Default", "Foot", "Inch", "Millimeter", "Meter",
                                      "Centimeter", "Decimeter", "Custom"])
ImportPlacement = make_enum("ImportPlacement", ["Origin", "Shared", "Centered", "Site"])
ViewDetailLevel = make_enum("ViewDetailLevel", ["Coarse", "Medium", "Fine", "Undefined"])
ViewFamily = make_enum("ViewFamily", ["ThreeDimensional", "FloorPlan"])
DuctSystemType = make_enum("DuctSystemType", ["SupplyAir", "ReturnAir", "ExhaustAir",
                                              "OtherAir", "Global", "Fitting",
                                              "UndefinedSystemType"])
PipeSystemType = make_enum("PipeSystemType", ["SupplyHydronic", "ReturnHydronic", "Sanitary",
                                              "Vent", "Global", "Fitting",
                                              "UndefinedSystemType"])
ElectricalSystemType = make_enum("ElectricalSystemType", ["PowerCircuit", "Data", "Telephone",
                                                          "UndefinedSystemType"])
ConnectorProfileType = make_enum("ConnectorProfileType", ["Round", "Rectangular", "Oval",
                                                          "Invalid"])
FlowDirectionType = make_enum("FlowDirectionType", ["In", "Out", "Bidirectional"])
MEPSystemClassification = make_enum("MEPSystemClassification", [
    "SupplyAir", "ReturnAir", "PowerCircuit", "DataCircuit", "SupplyHydronic",
    "UndefinedSystemClassification"])
FailureSeverity = make_enum("FailureSeverity", ["Warning", "Error"])
FailureProcessingResult = make_enum("FailureProcessingResult", ["Continue", "ProceedWithRollBack"])


class FakeElementTransformUtils(object):
    moves = []

    @classmethod
    def MoveElement(cls, doc, element_id, xyz):
        cls.moves.append((element_id, (xyz.X, xyz.Y, xyz.Z)))
        doc.moved.append(element_id)


class GenericForm(object):
    pass


class FamilyInstance(object):
    pass


class ImportInstance(object):
    pass


class Solid(FakeSolid):
    pass


class GeometryInstance(FakeGeometryInstance):
    pass


class PlanarFace(FakePlanarFace):
    pass


class Material(object):
    pass


class View3DClass(FakeView3D):
    pass


class FakeOptions(object):
    def __init__(self):
        self.ComputeReferences = False


def build_fake_db():
    db = types.SimpleNamespace(
        ElementId=FakeElementId,
        XYZ=FakeXYZ,
        Transaction=FakeTransaction,
        TransactionStatus=FakeTransactionStatus,
        IFailuresPreprocessor=object,
        FailureSeverity=FailureSeverity,
        FailureProcessingResult=FailureProcessingResult,
        FilteredElementCollector=FakeCollector,
        View3D=FakeView3DStatic,
        ViewDetailLevel=ViewDetailLevel,
        ViewFamily=ViewFamily,
        SATExportOptions=FakeSATExportOptions,
        DWGExportOptions=FakeDWGExportOptions,
        SATImportOptions=FakeImportOptions,
        DWGImportOptions=FakeImportOptions,
        SolidGeometry=make_enum("SolidGeometry", ["ACIS", "Polymesh"]),
        ACADVersion=make_enum("ACADVersion", ["R2007", "R2018"]),
        ImportUnit=ImportUnit,
        ImportPlacement=ImportPlacement,
        Options=FakeOptions,
        Solid=Solid,
        GeometryInstance=GeometryInstance,
        PlanarFace=PlanarFace,
        FreeFormElement=FakeFreeFormStatic,
        SolidUtils=FakeSolidUtils,
        ElementTransformUtils=FakeElementTransformUtils,
        ConnectorElement=FakeConnectorStatic,
        ConnectorProfileType=ConnectorProfileType,
        FlowDirectionType=FlowDirectionType,
        MEPSystemClassification=MEPSystemClassification,
        Mechanical=types.SimpleNamespace(DuctSystemType=DuctSystemType),
        Plumbing=types.SimpleNamespace(PipeSystemType=PipeSystemType),
        Electrical=types.SimpleNamespace(ElectricalSystemType=ElectricalSystemType,
                                         ElectricalLoadClassification=object),
        GenericForm=GenericForm,
        FamilyInstance=FamilyInstance,
        ImportInstance=ImportInstance,
        Material=Material,
        Category=types.SimpleNamespace(),
        BuiltInCategory=BuiltInCategory,
        BuiltInParameter=BuiltInParameter,
        ForgeTypeId=FakeForgeTypeId,
        SpecTypeId=FakeSpecTypeId,
        GroupTypeId=FakeGroupTypeId,
        SpecUtils=FakeSpecUtils2026,
        ParameterUtils=FakeParameterUtils2026,
        Definition=FakeDefinition2026,
        UnitUtils=types.SimpleNamespace(),
        SaveAsOptions=object,
        Sketch=type("Sketch", (object,), {}),
        CurveElement=type("CurveElement", (object,), {}),
        SymbolicCurve=type("SymbolicCurve", (object,), {}),
        ModelCurve=type("ModelCurve", (object,), {}),
        ReferencePlane=type("ReferencePlane", (object,), {}),
        ConnectorElementType=object,
        Line=type("Line", (object,), {}),
        Arc=type("Arc", (object,), {}),
        Ellipse=type("Ellipse", (object,), {}),
        NurbSpline=type("NurbSpline", (object,), {}),
        HermiteSpline=type("HermiteSpline", (object,), {}),
        ViewFamilyType=type("ViewFamilyType", (object,), {}),
        View=type("View", (object,), {}),
        Family=type("Family", (object,), {}),
        FamilySymbol=type("FamilySymbol", (object,), {}),
        GraphicsStyleType=make_enum("GraphicsStyleType", ["Projection", "Cut"]),
        FillPatternElement=types.SimpleNamespace(),
        FillPatternTarget=make_enum("FillPatternTarget", ["Model", "Drafting"]),
        Color=lambda r, g, b: (r, g, b),
        Plane=types.SimpleNamespace(),
        SketchPlane=types.SimpleNamespace(),
    )
    return db


def shape_db_like_2021(db):
    db.ParameterType = ParameterType2021
    db.BuiltInParameterGroup = BuiltInParameterGroup2021
    db.Definition = FakeDefinition2021
    db.SpecTypeId = FakeSpecTypeId2021
    db.UnitUtils = FakeUnitUtils2021
    for name in ("GroupTypeId", "SpecUtils", "ParameterUtils"):
        if hasattr(db, name):
            delattr(db, name)


def shape_db_like_2026(db):
    db.Definition = FakeDefinition2026
    db.SpecTypeId = FakeSpecTypeId
    db.GroupTypeId = FakeGroupTypeId
    db.SpecUtils = FakeSpecUtils2026
    db.ParameterUtils = FakeParameterUtils2026
    db.UnitUtils = types.SimpleNamespace()
    for name in ("ParameterType", "BuiltInParameterGroup"):
        if hasattr(db, name):
            delattr(db, name)


FAKE_DB = build_fake_db()

TEMP_DIR = tempfile.mkdtemp(prefix="fdg-revit-")


class FakeScript(object):
    @staticmethod
    def get_logger():
        return types.SimpleNamespace(debug=lambda *a, **k: None,
                                     info=lambda *a, **k: None,
                                     warning=lambda *a, **k: None)

    @staticmethod
    def get_universal_data_file(file_id, extension):
        return os.path.join(TEMP_DIR, "{}.{}".format(file_id, extension))


class FakeClr(object):
    @staticmethod
    def AddReference(*_a, **_k):
        return None

    @staticmethod
    def GetClrType(python_type):
        return FakeClrType(python_type)


STUBS = {
    "clr": FakeClr,
    "System": types.SimpleNamespace(
        Enum=FakeEnumStatic, Guid=FakeGuid, Double=float,
        Reflection=types.SimpleNamespace(BindingFlags=types.SimpleNamespace(Public=1, Static=2)),
        Collections=types.SimpleNamespace(
            Generic=types.SimpleNamespace(List=None))),
    "System.Collections": types.SimpleNamespace(),
    "System.Collections.Generic": types.SimpleNamespace(),
    "System.Windows": types.SimpleNamespace(),
    "System.Windows.Forms": types.SimpleNamespace(DialogResult=types.SimpleNamespace(OK=1),
                                                  FolderBrowserDialog=object),
    "Autodesk": types.SimpleNamespace(),
    "Autodesk.Revit": types.SimpleNamespace(),
    "Autodesk.Revit.UI": types.SimpleNamespace(),
    "Autodesk.Revit.UI.Selection": types.SimpleNamespace(ISelectionFilter=object,
                                                         ObjectType=types.SimpleNamespace(Element=1)),
    "pyrevit": types.SimpleNamespace(DB=FAKE_DB, script=FakeScript),
    "pyrevit.compat": types.SimpleNamespace(
        get_elementid_value_func=lambda: (lambda eid: eid.Value)),
    "pyrevit.script": FakeScript,
    "pyrevit.forms": types.SimpleNamespace(),
    "pyrevit.framework": types.SimpleNamespace(Windows=types.SimpleNamespace()),
}


class _ClrList(list):
    """A .NET List: a Python list with ``Add``."""

    def Add(self, item):
        self.append(item)


class _FakeGenericList(object):
    """``List[T]()`` -> a list with ``Add``."""

    def __getitem__(self, _item):
        return _ClrList


STUBS["System.Collections.Generic"].List = _FakeGenericList()
STUBS["System"].Collections.Generic.List = STUBS["System.Collections.Generic"].List


state = None
fdr = None
export = None
rebuild = None
_saved_modules = {}
_saved_path = None


def _install_stub(name, value):
    _saved_modules.setdefault(name, sys.modules.get(name))
    if isinstance(value, types.ModuleType):
        module = value
    else:
        module = types.ModuleType(name)
        for key in dir(value):
            if not key.startswith("__"):
                setattr(module, key, getattr(value, key))
    sys.modules[name] = module
    return module


def _load_as(dotted_name, path):
    _saved_modules.setdefault(dotted_name, sys.modules.get(dotted_name))
    spec = importlib.util.spec_from_file_location(dotted_name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = module
    spec.loader.exec_module(module)
    return module


def setUpModule():
    global state, fdr, export, rebuild, _saved_path
    _saved_path = list(sys.path)
    for name, value in STUBS.items():
        _install_stub(name, value)
    # Real pure modules the engines lean on.
    _install_stub("easybim", types.SimpleNamespace())
    _load_as("easybim.compat", LIB_DIR / "compat.py")
    _load_as("easybim.family_selection_state", LIB_DIR / "family_selection_state.py")
    _load_as("easybim.family_selection_revit", LIB_DIR / "family_selection_revit.py")
    sys.path.insert(0, str(COMMAND_DIR))
    state = _load_as("families_downgrade_state", COMMAND_DIR / "families_downgrade_state.py")
    fdr = _load_as("families_downgrade_revit", COMMAND_DIR / "families_downgrade_revit.py")
    export = _load_as("families_downgrade_export", COMMAND_DIR / "families_downgrade_export.py")
    rebuild = _load_as("families_downgrade_rebuild", COMMAND_DIR / "families_downgrade_rebuild.py")


def tearDownModule():
    sys.path[:] = _saved_path
    for name, previous in _saved_modules.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    shutil.rmtree(TEMP_DIR, ignore_errors=True)


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


class ExportGroupFileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="fdg-export-")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def _group(self, ids=(1, 2)):
        return {"index": 1, "element_ids": list(ids),
                "detail_levels": ["Fine", "Medium", "Coarse"]}

    def test_an_empty_export_at_fine_retries_at_medium_and_the_file_is_renamed(self):
        doc = FakeExportDoc(self.folder, {"Fine": "empty", "Medium": "bodies"})
        doc.elements = [FakeElement(1), FakeElement(2), FakeElement(3), FakeElement(4, hideable=False)]
        name, note = export.export_group_file(doc, self.folder, self._group(), state.GEOMETRY_SAT, "vt")
        self.assertEqual(("g01.sat", ""), (name, note))
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "g01.sat")))
        self.assertEqual(["Fine", "Medium"], doc.export_levels)
        # Nothing but the group's own elements is left visible; the element that
        # cannot be hidden is left alone rather than making the whole hide throw.
        hidden = [element_id.Value for element_id in doc.views_created[-1].hidden]
        self.assertEqual([3], hidden)
        # Scratch views never outlive their export.
        self.assertEqual([view.Id for view in doc.views_created],
                         [element_id for element_id in doc.deleted])
        self.assertEqual(["g01.sat"], sorted(os.listdir(self.folder)))

    def test_nothing_visible_at_any_level_is_a_reason_not_a_file(self):
        doc = FakeExportDoc(self.folder, {"Fine": "empty", "Medium": "none", "Coarse": "empty"})
        doc.elements = [FakeElement(1)]
        name, note = export.export_group_file(doc, self.folder, self._group((1,)), state.GEOMETRY_SAT, "vt")
        self.assertEqual("", name)
        self.assertIn("nothing visible", note)
        self.assertEqual(["Fine", "Medium", "Coarse"], doc.export_levels)
        self.assertEqual([], os.listdir(self.folder))

    def test_sat_body_detection(self):
        path = os.path.join(self.folder, "x.sat")
        with open(path, "wb") as handle:
            handle.write(b"700 0 1 0\nbody $1 #\n")
        self.assertTrue(export.sat_has_bodies(path))
        with open(path, "wb") as handle:
            handle.write(b"700 0 1 0\nnobody here\n")
        self.assertFalse(export.sat_has_bodies(path))
        self.assertFalse(export.sat_has_bodies(os.path.join(self.folder, "missing.sat")))

    def test_dwg_export_options_carry_acis_solids_and_the_oldest_file_version(self):
        options = export._export_options(state.GEOMETRY_DWG)
        self.assertIsInstance(options, FakeDWGExportOptions)
        self.assertEqual("ACIS", options.ExportOfSolids.name)
        self.assertEqual("R2007", options.FileVersion.name)
        self.assertTrue(options.HideReferencePlane)
        sat = export._export_options(state.GEOMETRY_SAT)
        self.assertIsInstance(sat, FakeSATExportOptions)
        self.assertTrue(sat.HideScopeBox)


class SolidRecordTests(unittest.TestCase):
    def test_voids_are_left_out_and_treatments_come_from_the_parameters(self):
        body_param = FakeFamilyParameter("Body Material")
        show_param = FakeFamilyParameter("Show Frame")
        material_a = FakeParameter("MATERIAL_ID_PARAM", storage="ElementId", value=FakeElementId(-1))
        material_b = FakeParameter("MATERIAL_ID_PARAM", storage="ElementId", value=FakeElementId(-1))
        visible_a = FakeParameter("IS_VISIBLE_PARAM", value=1)
        visible_b = FakeParameter("IS_VISIBLE_PARAM", value=0)
        manager = FakeFamilyManager({material_a: body_param, visible_b: show_param})

        class Form(GenericForm, FakeSolidElement):
            pass

        class Nested(FamilyInstance, FakeElement):
            pass

        solid = Form(1, material_param=material_a, visible_param=visible_a)
        void = Form(2, is_solid=False)
        other = Form(3, material_param=material_b, visible_param=visible_b,
                     subcategory=FakeCategory("Frame"))
        nested = Nested(4)
        doc = types.SimpleNamespace(elements=[solid, void, other, nested],
                                    OwnerFamily=types.SimpleNamespace(
                                        FamilyCategory=FakeCategory("Mechanical Equipment",
                                                                    "OST_MechanicalEquipment")),
                                    FamilyManager=manager)
        manifest = state.new_manifest("Fan", {})
        reader = export.FamilyReader(doc, manifest)
        records = reader.collect_solid_records()

        self.assertEqual([1, 3, 4], [r["element_id"] for r in records])
        self.assertEqual({"kind": "parameter", "name": "Body Material"}, records[0]["material"])
        self.assertEqual({"kind": "literal", "value": True}, records[0]["visible"])
        self.assertEqual("Frame", records[1]["subcategory"])
        self.assertEqual({"kind": "parameter", "name": "Show Frame"}, records[1]["visible"])
        self.assertEqual("instance", records[2]["kind"])
        groups = state.group_elements(records)
        self.assertEqual(3, len(groups))
        self.assertTrue(any("void" in note for note in manifest["notes"]))
        self.assertTrue(any("nested" in note for note in manifest["notes"]))


class CurveRecordTests(unittest.TestCase):
    def test_lines_and_full_circles_are_recorded(self):
        class Line(FAKE_DB.Line):
            IsBound = True

            def GetEndPoint(self, index):
                return FakeXYZ(index, 0, 0)

        record = export.curve_record(Line())
        self.assertEqual({"type": "Line", "start": [0.0, 0.0, 0.0], "end": [1.0, 0.0, 0.0]}, record)

        class Circle(FAKE_DB.Arc):
            IsBound = False
            Center = FakeXYZ(1, 2, 3)
            Radius = 0.5
            XDirection = FakeXYZ(1, 0, 0)
            YDirection = FakeXYZ(0, 1, 0)
            Normal = FakeXYZ(0, 0, 1)

        record = export.curve_record(Circle())
        self.assertEqual("Arc", record["type"])
        self.assertEqual(0.0, record["start"])
        self.assertAlmostEqual(6.2831853, record["end"], places=5)
        self.assertIsNone(export.curve_record(object()))


# --------------------------------------------------------------------------
# rebuild: the parameter ladder on two API shapes
# --------------------------------------------------------------------------


class Caps(object):
    def __init__(self, uses_forge, parameter_types=(), builtin_groups=(), has_group_type_id=False,
                 has_builtin_group=False):
        self.uses_forge = uses_forge
        self.parameter_type_names = list(parameter_types)
        self.builtin_group_names = list(builtin_groups)
        self.has_group_type_id = has_group_type_id
        self.has_builtin_group = has_builtin_group
        self.category_names = ["OST_MechanicalEquipment", "OST_GenericModel"]


class ParameterLadderTests(unittest.TestCase):
    def tearDown(self):
        shape_db_like_2026(FAKE_DB)
        fdr.FORGE._specs = None
        fdr.FORGE._groups = None

    def _caps_2021(self):
        shape_db_like_2021(FAKE_DB)
        return Caps(False, FakeEnumStatic.GetNames(ParameterType2021),
                    FakeEnumStatic.GetNames(BuiltInParameterGroup2021), has_builtin_group=True)

    def _caps_2026(self):
        shape_db_like_2026(FAKE_DB)
        fdr.FORGE._specs = None
        fdr.FORGE._groups = None
        return Caps(True, has_group_type_id=True)

    def test_2021_resolves_a_measurable_forge_spec_through_the_unit_type(self):
        caps = self._caps_2021()
        notes = []
        kind, value = rebuild.resolve_spec(
            {"name": "Flow", "spec_type_id": "autodesk.spec.aec.hvac:air-flow-2.0.0",
             "spec_property": "AirFlow", "storage_type": "Double"}, caps, notes.append)
        self.assertEqual(("legacy", ParameterType2021.HVACAirflow), (kind, value))
        self.assertEqual([], notes)

    def test_2021_resolves_text_and_family_type_and_notes_a_newer_spec(self):
        caps = self._caps_2021()
        notes = []
        kind, value = rebuild.resolve_spec(
            {"name": "Note", "spec_property": "String.Text", "storage_type": "String"}, caps, notes.append)
        self.assertEqual(("legacy", ParameterType2021.Text), (kind, value))
        kind, value = rebuild.resolve_spec(
            {"name": "Nested", "spec_property": "Reference.FamilyType",
             "family_type_category": "OST_GenericModel"}, caps, notes.append)
        self.assertEqual(("category", "OST_GenericModel"), (kind, value))
        kind, value = rebuild.resolve_spec(
            {"name": "Spin", "spec_type_id": "autodesk.spec.aec:angular-speed-2.0.0",
             "spec_property": "AngularSpeed", "storage_type": "Double"}, caps, notes.append)
        self.assertEqual(("legacy", ParameterType2021.Number), (kind, value))
        self.assertTrue(any("AngularSpeed" in note and "unitless" in note for note in notes))

    def test_2021_groups_come_from_the_normalised_names_and_the_quirks(self):
        caps = self._caps_2021()
        notes = []
        self.assertEqual(BuiltInParameterGroup2021.PG_IDENTITY_DATA, rebuild.resolve_group(
            {"name": "p", "group_property": "IdentityData"}, caps, notes.append))
        self.assertEqual(BuiltInParameterGroup2021.PG_TERMINTATION, rebuild.resolve_group(
            {"name": "p", "group_property": "Termination"}, caps, notes.append))
        self.assertEqual(BuiltInParameterGroup2021.INVALID, rebuild.resolve_group(
            {"name": "p", "group_property": "ElectricalAnalysis"}, caps, notes.append))
        self.assertTrue(any("ElectricalAnalysis" in note for note in notes))

    def test_2026_keeps_valid_forge_ids_and_falls_back_by_property_or_storage(self):
        caps = self._caps_2026()
        notes = []
        kind, value = rebuild.resolve_spec(
            {"name": "L", "spec_type_id": "autodesk.spec.aec:length-2.0.0",
             "spec_property": "Length", "storage_type": "Double"}, caps, notes.append)
        self.assertEqual(("forge", FakeSpecTypeId.Length), (kind, value))
        # A 2021 exporter recorded only the legacy name for a text parameter.
        kind, value = rebuild.resolve_spec(
            {"name": "T", "parameter_type": "Text", "storage_type": "String"}, caps, notes.append)
        self.assertEqual(("forge", FakeSpecString.Text), (kind, value))
        # An id this host does not know, with no property to fall back on.
        kind, value = rebuild.resolve_spec(
            {"name": "X", "spec_type_id": "autodesk.spec.aec:from-the-future-2.0.0",
             "storage_type": "Double"}, caps, notes.append)
        self.assertEqual(("forge", FakeSpecTypeId.Number), (kind, value))
        self.assertTrue(any("from-the-future" in note for note in notes))

    def test_2026_groups_accept_recorded_ids_and_recover_legacy_names(self):
        caps = self._caps_2026()
        notes = []
        self.assertEqual(FakeGroupTypeId.Geometry, rebuild.resolve_group(
            {"name": "p", "group_type_id": "autodesk.parameter.group:geometry-1.0.0"},
            caps, notes.append))
        # A 2021 exporter only knew PG_IDENTITY_DATA.
        self.assertEqual(FakeGroupTypeId.IdentityData, rebuild.resolve_group(
            {"name": "p", "builtin_group": "PG_IDENTITY_DATA"}, caps, notes.append))
        self.assertEqual(FakeGroupTypeId.Termination, rebuild.resolve_group(
            {"name": "p", "builtin_group": "PG_TERMINTATION"}, caps, notes.append))
        self.assertEqual([], notes)
        other = rebuild.resolve_group({"name": "p", "group_property": "LifeSafety"}, caps, notes.append)
        self.assertEqual(FakeForgeTypeId(""), other)
        self.assertTrue(any("LifeSafety" in note for note in notes))


# --------------------------------------------------------------------------
# rebuild: geometry import self-check and FreeForm creation
# --------------------------------------------------------------------------


class ImportGroupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="fdg-import-")
        with open(os.path.join(self.folder, "g01.sat"), "wb") as handle:
            handle.write(b"700 0 1 0\nbody\n")
        FakeFreeFormStatic.created = []
        FakeFreeFormStatic.refuse = set()
        FakeElementTransformUtils.moves = []

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def _context(self, doc):
        result = state.DowngradeResult("Fan", "", "")
        manifest = state.new_manifest("Fan", {})
        ctx = rebuild.RebuildContext(doc, manifest, Caps(True), None, result)
        ctx.view3d = FakeView3D(1)
        return ctx

    def test_a_wrongly_scaled_import_is_re_read_in_feet_and_becomes_free_forms(self):
        doc = FakeImportDoc()
        ctx = self._context(doc)
        group = {"index": 1, "file": "g01.sat", "bbox": [[0, 0, 0], [12.0, 6.0, 3.0]]}
        elements = rebuild.import_group(ctx, self.folder, group)
        self.assertEqual(["Default", "Foot"], doc.imports)
        self.assertEqual(2, len(elements))
        self.assertTrue(all(element in FakeFreeFormStatic.created for element in elements))
        # The wrongly scaled first import and the finished import are both gone.
        self.assertEqual(2, len([d for d in doc.deleted if d.Value < 100000]))
        self.assertTrue(any("imported as foot" in note for note in ctx.result.notes))
        self.assertEqual([], FakeElementTransformUtils.moves)

    def test_an_offset_import_is_moved_onto_the_exported_position(self):
        doc = FakeImportDoc()

        real_import = doc.Import

        def shifted_import(path, options, view):
            element_id = real_import(path, options, view)
            instance = doc.GetElement(element_id)
            box = instance._bbox
            instance._bbox = FakeBBox((box.Min.X + 1, box.Min.Y, box.Min.Z),
                                      (box.Max.X + 1, box.Max.Y, box.Max.Z))
            return element_id
        doc.Import = shifted_import
        ctx = self._context(doc)
        group = {"index": 1, "file": "g01.sat", "bbox": [[0, 0, 0], [12.0, 6.0, 3.0]]}
        rebuild.import_group(ctx, self.folder, group)
        self.assertEqual(1, len(FakeElementTransformUtils.moves))
        self.assertAlmostEqual(-1.0, FakeElementTransformUtils.moves[0][1][0])
        self.assertTrue(any("moved by" in note for note in ctx.result.notes))

    def test_a_body_that_refuses_to_become_a_free_form_keeps_the_import_instance(self):
        doc = FakeImportDoc()
        FakeFreeFormStatic.refuse = set(doc.sat_solids[:1])
        ctx = self._context(doc)
        group = {"index": 2, "file": "g01.sat", "bbox": None}
        elements = rebuild.import_group(ctx, self.folder, group)
        self.assertEqual(1, len(elements))
        self.assertIsInstance(elements[0], FakeImportInstance)
        self.assertTrue(any("kept as an import instance" in note for note in ctx.result.notes))
        self.assertEqual([], doc.deleted)

    def test_a_missing_file_is_a_note_not_a_crash(self):
        doc = FakeImportDoc()
        ctx = self._context(doc)
        self.assertEqual([], rebuild.import_group(ctx, self.folder, {"index": 3, "file": "g03.sat"}))
        self.assertTrue(any("missing" in note for note in ctx.result.notes))
        self.assertEqual([], rebuild.import_group(ctx, self.folder, {"index": 4, "file": ""}))


# --------------------------------------------------------------------------
# rebuild: connectors
# --------------------------------------------------------------------------


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        FakeConnectorStatic.calls = []

    def test_the_exact_face_wins_over_a_coplanar_one_and_other_planes_are_ignored(self):
        exact = FakePlanarFace((0, 0, 2), (0, 0, 1), [(1, 1, 2)])
        coplanar = FakePlanarFace((5, 5, 2), (0, 0, 1), [])
        other = FakePlanarFace((0, 0, 9), (0, 0, 1), [(1, 1, 2)])
        flipped = FakePlanarFace((0, 0, 2), (0, 0, -1), [(1, 1, 2)])
        faces = [(other, None), (coplanar, None), (flipped, None), (exact, None)]
        face, score = rebuild.find_connector_face(
            faces, {"origin": [1, 1, 2], "basis_z": [0, 0, 1]})
        self.assertIs(exact, face)
        self.assertEqual(0.0, score)
        face, score = rebuild.find_connector_face(
            [(coplanar, None), (other, None)], {"origin": [1, 1, 2], "basis_z": [0, 0, 1]})
        self.assertIs(coplanar, face)
        self.assertGreater(score, 0.0)
        self.assertEqual((None, None), rebuild.find_connector_face(
            [(other, None)], {"origin": [1, 1, 2], "basis_z": [0, 0, 1]}))

    def test_connectors_are_created_for_their_domain_with_the_mapped_system(self):
        ctx = types.SimpleNamespace(doc=object(), note=lambda text: None)
        rebuild._create_connector(ctx, {"kind": "duct", "system_classification": "SupplyAir",
                                        "shape": "Rectangular"}, "ref")
        rebuild._create_connector(ctx, {"kind": "electrical",
                                        "system_classification": "DataCircuit"}, "ref")
        rebuild._create_connector(ctx, {"kind": "pipe",
                                        "system_classification": "SupplyHydronic"}, "ref")
        rebuild._create_connector(ctx, {"kind": "conduit"}, "ref")
        self.assertEqual("duct", FakeConnectorStatic.calls[0][0])
        self.assertEqual(DuctSystemType.SupplyAir, FakeConnectorStatic.calls[0][1])
        self.assertEqual(ConnectorProfileType.Rectangular, FakeConnectorStatic.calls[0][2])
        self.assertEqual(ElectricalSystemType.Data, FakeConnectorStatic.calls[1][1])
        self.assertEqual(PipeSystemType.SupplyHydronic, FakeConnectorStatic.calls[2][1])
        self.assertEqual("conduit", FakeConnectorStatic.calls[3][0])
        with self.assertRaises(ValueError):
            rebuild._create_connector(ctx, {"kind": "unknown"}, "ref")


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


class HelperTests(unittest.TestCase):
    def test_templates_are_found_recursively_and_ranked_for_the_family(self):
        root = tempfile.mkdtemp(prefix="fdg-templates-")
        try:
            os.makedirs(os.path.join(root, "English"))
            for name in ("Metric Generic Model.rft", "Metric Generic Model face based.rft"):
                open(os.path.join(root, "English", name), "w").close()
            open(os.path.join(root, "notes.txt"), "w").close()
            templates = fdr.list_templates(root)
            self.assertEqual(sorted(templates), ["Metric Generic Model face based.rft",
                                                 "Metric Generic Model.rft"])
            path, note = fdr.choose_template(templates, {"category_label": "Lighting Fixtures",
                                                         "hosting_behavior_label": "Face"})
            self.assertTrue(path.endswith("Metric Generic Model face based.rft"))
            self.assertIn("face based", note)
            path, note = fdr.choose_template({}, {}, fallback_path=os.path.join(
                root, "English", "Metric Generic Model.rft"))
            self.assertTrue(path.endswith("Metric Generic Model.rft"))
            self.assertIn("fallback", note)
            self.assertEqual(("", "no family template was found"), fdr.choose_template({}, {}))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_run_transaction_reports_exceptions_and_rollbacks_as_failures(self):
        doc = object()
        ok, error = fdr.run_transaction(doc, "t", lambda: None)
        self.assertEqual((True, ""), (ok, error))

        def boom():
            raise RuntimeError("no")
        ok, error = fdr.run_transaction(doc, "t", boom)
        self.assertFalse(ok)
        self.assertIn("no", error)

    def test_the_family_session_rolls_back_an_open_rfa_and_closes_an_edited_one(self):
        class Group(object):
            def __init__(self, doc, name):
                self.started = False
                self.ended = False
                self.rolled_back = False

            def Start(self):
                self.started = True

            def HasStarted(self):
                return self.started

            def HasEnded(self):
                return self.ended

            def RollBack(self):
                self.rolled_back = True
                self.ended = True

        FAKE_DB.TransactionGroup = Group
        open_doc = types.SimpleNamespace(closed=[])
        option = types.SimpleNamespace(name="Open", source_kind="open_rfa",
                                       family_key="open-rfa|x", family_document=open_doc)
        session, reason = fdr.open_family_session(option, None)
        self.assertEqual("", reason)
        session.close()
        self.assertTrue(session.group is None)

        family = types.SimpleNamespace(IsInPlace=False, IsEditable=True, Id=FakeElementId(5))
        edited = types.SimpleNamespace(closed=[])
        edited.Close = lambda save: edited.closed.append(save)
        source = types.SimpleNamespace(EditFamily=lambda fam: edited)
        option = types.SimpleNamespace(name="Fan", source_kind="project",
                                       family_key="project|5", family=family,
                                       source_document=None, element_id=None)
        session, reason = fdr.open_family_session(option, source)
        self.assertEqual("", reason)
        session.close()
        self.assertEqual([False], edited.closed)

    def test_an_already_open_family_gets_the_actionable_reason(self):
        family = types.SimpleNamespace(IsInPlace=False, IsEditable=True, Id=FakeElementId(5))

        def refuse(fam):
            raise RuntimeError("The family is already open in this session")
        source = types.SimpleNamespace(EditFamily=refuse)
        option = types.SimpleNamespace(name="Fan", source_kind="project", family_key="project|5",
                                       family=family, source_document=None, element_id=None)
        session, reason = fdr.open_family_session(option, source)
        self.assertIsNone(session)
        self.assertIn("Opened .rfa card", reason)

    def test_the_scratch_shared_parameter_file_restores_the_filename_and_deletes_itself(self):
        class Definitions(object):
            def Create(self, options):
                return ("definition", options)

        class Groups(object):
            def Create(self, name):
                return types.SimpleNamespace(Definitions=Definitions())

        class App(object):
            SharedParametersFilename = "C:/team/shared.txt"

            def OpenSharedParameterFile(self):
                return types.SimpleNamespace(Groups=Groups())

        FAKE_DB.ExternalDefinitionCreationOptions = lambda name, spec: types.SimpleNamespace(
            name=name, spec=spec)
        app = App()
        scratch = fdr.SharedParameterScratch(app).open()
        self.assertEqual("", scratch.error)
        self.assertTrue(os.path.isfile(scratch.path))
        self.assertEqual(scratch.path, app.SharedParametersFilename)
        definition = scratch.definition("Width", "aaaa-bbbb", "spec")
        self.assertEqual("definition", definition[0])
        self.assertIs(definition, scratch.definition("Width", "AAAA-BBBB", "spec"))
        scratch.close()
        self.assertEqual("C:/team/shared.txt", app.SharedParametersFilename)
        self.assertFalse(os.path.isfile(scratch.path))


if __name__ == "__main__":
    unittest.main()
