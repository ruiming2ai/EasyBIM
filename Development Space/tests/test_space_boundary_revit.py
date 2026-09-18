# -*- coding: utf-8 -*-
"""Space Boundary's Revit adapter, driven over fakes.

The questions worth asking of this layer are the ones a Revit session would
answer far too late: is the model's own area computation setting the one that
gets used, is a room's boundary read once no matter how many views it lands
in, is the write nested group-then-transaction-then-subtransaction so one bad
room costs one room, and does a rolled-back view take its own counters with
it.  Everything here exists to make those answerable on a laptop.
"""

import importlib.util
import json
import math
import pathlib
import sys
import types
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Space Boundary.pushbutton"
LIB_PARENT = str(REPO_ROOT / "lib")
if LIB_PARENT not in sys.path:
    sys.path.insert(0, LIB_PARENT)


def _ensure_easybim_package():
    """Other test modules stub ``easybim`` in sys.modules, sometimes without a
    ``__path__``; give it one so fresh submodule imports resolve to lib."""
    package = sys.modules.get("easybim")
    if package is None:
        return
    if not getattr(package, "__path__", None):
        package.__path__ = [str(REPO_ROOT / "lib" / "easybim")]


_ensure_easybim_package()

# pyRevit puts the bundle folder on sys.path while its script runs, which is
# how the sibling modules import each other; do the same here.
if str(COMMAND_DIR) not in sys.path:
    sys.path.insert(0, str(COMMAND_DIR))


# ---- the .NET names the adapter and the store reach for, stubbed ---------


class _Guid(object):
    def __init__(self, text):
        self.text = text

    def __eq__(self, other):
        return isinstance(other, _Guid) and other.text == self.text

    def __hash__(self):
        return hash(self.text)


class _ClrList(object):
    """``List[T]()`` - enough of it for CurveLoop and ElementId collections."""

    def __init__(self, items=None):
        self.items = list(items or [])

    def __class_getitem__(cls, _type):
        return lambda *args: _ClrList(*args)

    def Add(self, item):
        self.items.append(item)

    @property
    def Count(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)


class _ListFactory(object):
    def __getitem__(self, _type):
        return lambda *args: _ClrList(*args)


_system = sys.modules.setdefault("System", types.ModuleType("System"))
_system.Guid = _Guid
_system.String = str
_system.Int64 = int
_clr = types.ModuleType("clr")
_clr.GetClrType = lambda value: value
sys.modules.setdefault("clr", _clr)

# ``System.Collections.Generic`` is installed only while this module's tests
# run, and taken out again afterwards.  Other suites assert that a CLR-only
# import *fails* under CPython - the graceful-degradation path - and unittest
# imports every test module before running any of them, so a stub registered
# at import time would quietly break them from three files away.
_GENERIC = "System.Collections.Generic"
_COLLECTIONS = "System.Collections"
_saved_modules = {}
_saved_attr = {}


def setUpModule():
    for name in (_COLLECTIONS, _GENERIC):
        _saved_modules[name] = sys.modules.get(name)
    _saved_attr["Collections"] = getattr(_system, "Collections", None)
    collections = types.ModuleType(_COLLECTIONS)
    generic = types.ModuleType(_GENERIC)
    generic.List = _ListFactory()
    collections.Generic = generic
    sys.modules[_COLLECTIONS] = collections
    sys.modules[_GENERIC] = generic
    _system.Collections = collections


def tearDownModule():
    for name, module in _saved_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    if _saved_attr.get("Collections") is None:
        try:
            del _system.Collections
        except AttributeError:
            pass
    else:
        _system.Collections = _saved_attr["Collections"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(COMMAND_DIR / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


state = _load("space_boundary_state")
storage = _load("space_boundary_storage")
revit = _load("space_boundary_revit")


MM = 304.8


# ------------------------------------------------------------ Revit fakes


class ElementId(object):
    def __init__(self, value):
        self.IntegerValue = int(value)

    def __eq__(self, other):
        return isinstance(other, ElementId) and other.IntegerValue == self.IntegerValue

    def __hash__(self):
        return hash(self.IntegerValue)

    def __repr__(self):
        return "ElementId({0})".format(self.IntegerValue)


class XYZ(object):
    def __init__(self, x, y, z):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)


class Transform(object):
    """Translation only - all a link placement needs to be told apart."""

    def __init__(self, dx=0.0, dy=0.0, dz=0.0):
        self.dx, self.dy, self.dz = float(dx), float(dy), float(dz)

    def OfPoint(self, point):
        return XYZ(point.X + self.dx, point.Y + self.dy, point.Z + self.dz)


class Curve(object):
    def __init__(self, start, end):
        self._start, self._end = start, end

    def GetEndPoint(self, index):
        return self._start if index == 0 else self._end


class Line(Curve):
    @staticmethod
    def CreateBound(start, end):
        if abs(start.X - end.X) < 1e-12 and abs(start.Y - end.Y) < 1e-12:
            raise Exception("curve is too short")
        return Line(start, end)


class Arc(Curve):
    """``Arc.Create(start, end, pointOnArc)`` - Revit's order, not the
    intuitive one.  A fake that took (start, middle, end) let the wrong call
    through and every curved room failed in Revit as "discontinuous"."""

    def __init__(self, start, end, point_on_arc):
        Curve.__init__(self, start, end)
        self.middle = point_on_arc

    @staticmethod
    def Create(start, end, point_on_arc):
        return Arc(start, end, point_on_arc)

    def Evaluate(self, _param, _normalised):
        return self.middle


class CurveLoop(object):
    """Refuses a non-contiguous append, exactly as Revit does."""

    def __init__(self):
        self.curves = []

    def Append(self, curve):
        if self.curves:
            tail = self.curves[-1].GetEndPoint(1)
            head = curve.GetEndPoint(0)
            if abs(tail.X - head.X) > 1e-9 or abs(tail.Y - head.Y) > 1e-9:
                raise Exception("the curves are not contiguous")
        self.curves.append(curve)

    def __iter__(self):
        return iter(self.curves)


class Element(object):
    def __init__(self, doc, element_id, name=u"", unique_id=None):
        self.Document = doc
        self.Id = ElementId(element_id)
        self.Name = name
        self.UniqueId = unique_id or u"uid-{0}".format(element_id)
        self.params = {}
        self.entity = None

    def get_Parameter(self, builtin):
        return self.params.get(builtin)

    def GetEntity(self, schema):
        if self.entity is None or self.entity.schema is not schema:
            return Entity(None)
        return self.entity

    def SetEntity(self, entity):
        self.entity = entity


class Param(object):
    def __init__(self, text=None, element_id=None):
        self._text = text
        self._id = element_id

    def AsString(self):
        return self._text

    def AsElementId(self):
        return self._id


class Level(Element):
    def __init__(self, doc, element_id, name, elevation_ft):
        Element.__init__(self, doc, element_id, name)
        self.ProjectElevation = float(elevation_ft)
        self.Elevation = float(elevation_ft)


class ViewRange(object):
    def __init__(self, level_id, cut_offset_ft):
        self._level_id, self._offset = level_id, cut_offset_ft

    def GetLevelId(self, plane):
        return ElementId(self._level_id)

    def GetOffset(self, plane):
        return self._offset


class View(Element):
    def __init__(self, doc, element_id, name, view_type=u"ViewType.FloorPlan", level=None,
                 is_template=False, phase_id=None, cut_offset_ft=None):
        Element.__init__(self, doc, element_id, name)
        self.ViewType = view_type
        self.GenLevel = level
        self.IsTemplate = is_template
        self.hidden_categories = set()
        self.link_overrides = {}
        self._cut = cut_offset_ft
        if phase_id is not None:
            self.params[u"VIEW_PHASE"] = Param(element_id=ElementId(phase_id))

    def GetViewRange(self):
        if self._cut is None:
            raise Exception("not a plan")
        return ViewRange(self.GenLevel.Id.IntegerValue if self.GenLevel else -1, self._cut)

    def GetCategoryHidden(self, category_id):
        return category_id.IntegerValue in self.hidden_categories

    def GetLinkOverrides(self, instance_id):
        return self.link_overrides.get(instance_id.IntegerValue)


class LinkOverrides(object):
    def __init__(self, mode, linked_view_id=-1):
        self.LinkVisibilityType = u"LinkVisibility." + mode
        self.LinkedViewId = ElementId(linked_view_id)


class Room(Element):
    def __init__(self, doc, element_id, number=u"", name=u"", level=None, area=10.0,
                 loops=None, placed=True, group_id=-1, option=None, unique_id=None):
        Element.__init__(self, doc, element_id, name, unique_id=unique_id)
        self.params[u"ROOM_NUMBER"] = Param(text=number)
        self.params[u"ROOM_NAME"] = Param(text=name)
        self.LevelId = level.Id if level is not None else ElementId(-1)
        self.Area = area
        self.Location = object() if placed else None
        self.GroupId = ElementId(group_id)
        self.DesignOption = option
        self.Category = types.SimpleNamespace(Id=ElementId(-2000160))
        self.BaseOffset = 0.0
        self.UpperLimit = None
        self.LimitOffset = 0.0
        self.UnboundedHeight = 8.0
        self._loops = loops if loops is not None else []
        self.boundary_calls = 0
        self.options_seen = []

    def GetBoundarySegments(self, options):
        self.boundary_calls += 1
        self.options_seen.append(getattr(options, "SpatialElementBoundaryLocation", None))
        return self._loops


class Segment(object):
    def __init__(self, curve):
        self._curve = curve

    def GetCurve(self):
        return self._curve


class FilledRegionType(Element):
    pass


class FilledRegion(Element):
    created = []
    refuse = None
    #: When set, a region reports no outline until the document regenerates -
    #: the way a real sketch reads before Revit has finished it.
    lazy = False

    def __init__(self, doc, element_id, type_id, view_id, loops):
        Element.__init__(self, doc, element_id, u"Region")
        self.type_id = type_id
        self.OwnerViewId = view_id
        self.loops = loops
        self.line_style_id = None

    def GetBoundaries(self):
        if FilledRegion.lazy and not self.Document.regenerated:
            return []
        return list(self.loops)

    def SetLineStyleId(self, style_id):
        self.line_style_id = style_id

    @staticmethod
    def Create(doc, type_id, view_id, loops):
        if FilledRegion.refuse is not None and FilledRegion.refuse(doc, view_id, loops):
            raise Exception("Revit will not draw that")
        region = FilledRegion(doc, doc.next_id, type_id, view_id, loops)
        doc.next_id += 1
        doc.add(region)
        FilledRegion.created.append(region)
        return region


class RevitLinkInstance(Element):
    def __init__(self, doc, element_id, name, link_doc, transform=None):
        Element.__init__(self, doc, element_id, name)
        self._link_doc = link_doc
        self._transform = transform or Transform()
        self.hidden_in = set()

    def CanBeHidden(self, view):
        return True

    def IsHidden(self, view):
        return view.Id.IntegerValue in self.hidden_in

    def GetLinkDocument(self):
        return self._link_doc

    def GetTotalTransform(self):
        return self._transform


class Collector(object):
    """``FilteredElementCollector(doc)`` or, with a view id, what that view
    shows: the fake document says per view, and "raise" for a view Revit
    would refuse to read."""

    def __init__(self, doc, view_id=None):
        self.doc = doc
        self.items = list(doc.elements)
        if view_id is not None:
            shown = doc.visible_by_view.get(view_id.IntegerValue)
            if shown == "raise":
                raise Exception("the view cannot be read")
            shown = set(shown or [])
            self.items = [item for item in self.items if item.Id.IntegerValue in shown]

    def ToElementIds(self):
        return [item.Id for item in self.items]

    def OfClass(self, klass):
        self.items = [item for item in self.items if isinstance(item, klass)]
        return self

    def OfCategory(self, member):
        self.items = [item for item in self.items
                      if getattr(item, "category_member", None) == member]
        return self

    def WhereElementIsNotElementType(self):
        return self

    def WherePasses(self, schema_filter):
        self.items = [item for item in self.items if schema_filter.matches(item)]
        return self

    def ToElements(self):
        return list(self.items)


class Warning(object):
    def __init__(self, text, element_ids):
        self._text = text
        self._ids = [ElementId(value) for value in element_ids]

    def GetDescriptionText(self):
        return self._text

    def GetFailingElements(self):
        return self._ids


class Doc(object):
    def __init__(self, title=u"Host", workshared=False):
        self.Title = title
        self.IsFamilyDocument = False
        self.IsWorkshared = workshared
        self.IsModifiable = False
        self.elements = []
        self.by_id = {}
        self.next_id = 500
        self.warnings = []
        self.ActiveView = None
        self.deleted = []
        self.visible_by_view = {}
        self.regenerated = 0

    def Regenerate(self):
        self.regenerated += 1

    def add(self, element):
        self.elements.append(element)
        self.by_id[element.Id.IntegerValue] = element
        return element

    def GetElement(self, element_id):
        return self.by_id.get(getattr(element_id, "IntegerValue", element_id))

    def GetWarnings(self):
        return list(self.warnings)

    def Delete(self, element_id):
        element = self.by_id.pop(getattr(element_id, "IntegerValue", element_id), None)
        if element is None:
            raise Exception("no such element")
        self.elements = [item for item in self.elements if item is not element]
        self.deleted.append(element)


# ---- transactions: the whole point is the order they nest in -------------


LOG = []


class TransactionGroup(object):
    def __init__(self, doc, name):
        self.doc, self.name = doc, name

    def Start(self):
        LOG.append(("group.start", self.name))
        self.doc.IsModifiable = False

    def Assimilate(self):
        LOG.append(("group.assimilate", self.name))

    def RollBack(self):
        LOG.append(("group.rollback", self.name))


class Transaction(object):
    fail_on_view = None

    def __init__(self, doc, name):
        self.doc, self.name = doc, name

    def Start(self):
        LOG.append(("txn.start", self.name))
        self.doc.IsModifiable = True

    def Commit(self):
        LOG.append(("txn.commit", self.name))
        self.doc.IsModifiable = False

    def RollBack(self):
        LOG.append(("txn.rollback", self.name))
        self.doc.IsModifiable = False


class SubTransaction(object):
    def __init__(self, doc):
        self.doc = doc

    def Start(self):
        LOG.append(("sub.start", None))

    def Commit(self):
        LOG.append(("sub.commit", None))

    def RollBack(self):
        LOG.append(("sub.rollback", None))


# ---- Extensible Storage --------------------------------------------------


class Field(object):
    def __init__(self, name):
        self.name = name


class Schema(object):
    def __init__(self, guid, fields=("record",)):
        self.GUID = guid
        self._fields = list(fields)

    def GetField(self, name):
        return Field(name) if name in self._fields else None


class SchemaRegistry(object):
    def __init__(self):
        self.by_guid = {}
        self.builds = 0

    def Lookup(self, guid):
        return self.by_guid.get(guid.text)


class SchemaBuilder(object):
    def __init__(self, guid, registry):
        self.guid, self._registry, self._fields = guid, registry, []

    def SetSchemaName(self, name):
        pass

    def SetVendorId(self, name):
        pass

    def SetReadAccessLevel(self, level):
        pass

    def SetWriteAccessLevel(self, level):
        pass

    def AddSimpleField(self, name, clr_type):
        self._fields.append(name)

    def Finish(self):
        self._registry.builds += 1
        schema = Schema(self.guid, self._fields)
        self._registry.by_guid[self.guid.text] = schema
        return schema


class Entity(object):
    def __init__(self, schema, value=None):
        self.schema = schema
        self.value = value

    def IsValid(self):
        return self.schema is not None

    class _Getter(object):
        def __init__(self, entity):
            self.entity = entity

        def __getitem__(self, _type):
            return lambda field: self.entity.value

    class _Setter(object):
        def __init__(self, entity):
            self.entity = entity

        def __getitem__(self, _type):
            def _set(field, value):
                self.entity.value = value
            return _set

    @property
    def Get(self):
        return Entity._Getter(self)

    @property
    def Set(self):
        return Entity._Setter(self)


class SchemaFilter(object):
    def __init__(self, guid, registry):
        self.guid, self.registry = guid, registry

    def matches(self, element):
        entity = getattr(element, "entity", None)
        return entity is not None and entity.schema is not None


BIC = types.SimpleNamespace(OST_Rooms=-2000160, OST_MEPSpaces=-2003600,
                            OST_InvisibleLines=-2000064, OST_RvtLinks=-2001352)


class GraphicsStyle(Element):
    def __init__(self, doc, element_id, category_name, category_id):
        Element.__init__(self, doc, element_id, u"")
        self.GraphicsStyleCategory = types.SimpleNamespace(Name=category_name,
                                                           Id=ElementId(category_id))

    @property
    def Name(self):
        raise AttributeError("IronPython cannot bind Name on this type")

    @Name.setter
    def Name(self, value):
        pass
BIP = types.SimpleNamespace(ROOM_NUMBER=u"ROOM_NUMBER", ROOM_NAME=u"ROOM_NAME",
                            ROOM_PHASE=u"ROOM_PHASE", PHASE_CREATED=u"PHASE_CREATED",
                            VIEW_PHASE=u"VIEW_PHASE")


def make_db(registry=None, boundary_location=u"SpatialElementBoundaryLocation.Finish",
            settings_throws=False, no_settings=False, checkout_owner=None,
            no_get_boundaries=False):
    registry = registry if registry is not None else SchemaRegistry()

    class Options(object):
        def __init__(self):
            self.SpatialElementBoundaryLocation = None

    def get_settings(doc):
        if settings_throws:
            raise Exception("the setting is unavailable")
        return types.SimpleNamespace(
            GetSpatialElementBoundaryLocation=lambda member: u"{0}.{1}".format(
                u"SpatialElementBoundaryLocation", member))

    def checkout_status(doc, element_id):
        if checkout_owner is None:
            return u"CheckoutStatus.OwnedByCurrentUser"
        return u"CheckoutStatus.OwnedByOtherUser"

    db = types.SimpleNamespace(
        ElementId=ElementId, XYZ=XYZ, Line=Line, Arc=Arc, CurveLoop=CurveLoop,
        Level=Level, View=View, FilledRegion=FilledRegion, FilledRegionType=FilledRegionType,
        RevitLinkInstance=RevitLinkInstance, FilteredElementCollector=Collector,
        BuiltInCategory=BIC, BuiltInParameter=BIP,
        PlanViewPlane=types.SimpleNamespace(CutPlane=u"CutPlane"),
        Element=types.SimpleNamespace(Name=types.SimpleNamespace(
            GetValue=lambda element: getattr(element, "fallback_name", u""))),
        Transaction=Transaction, SubTransaction=SubTransaction, TransactionGroup=TransactionGroup,
        SpatialElementBoundaryOptions=Options,
        SpatialElementBoundaryLocation=types.SimpleNamespace(
            Finish=u"Finish", Center=u"Center", CoreBoundary=u"CoreBoundary",
            CoreCenter=u"CoreCenter"),
        WorksharingUtils=types.SimpleNamespace(
            GetCheckoutStatus=checkout_status,
            GetWorksharingTooltipInfo=lambda doc, eid: types.SimpleNamespace(
                Owner=checkout_owner or u"")),
        ExtensibleStorage=types.SimpleNamespace(
            Schema=registry,
            SchemaBuilder=lambda guid: SchemaBuilder(guid, registry),
            AccessLevel=types.SimpleNamespace(Public=1),
            Entity=lambda schema: Entity(schema),
            ExtensibleStorageFilter=lambda guid: SchemaFilter(guid, registry)),
    )
    if no_settings:
        db.AreaVolumeSettings = None
        db.SpatialElementType = None
    else:
        db.AreaVolumeSettings = types.SimpleNamespace(GetAreaVolumeSettings=get_settings)
        db.SpatialElementType = types.SimpleNamespace(Room=boundary_location.rsplit(u".", 1)[-1],
                                                      Space=u"Center")
    if no_get_boundaries:
        db.FilledRegion = _NoBoundaryRegion
    return db


class _NoBoundaryRegion(FilledRegion):
    """A pre-2022 region: it draws, but it cannot say what its outline is."""

    GetBoundaries = None


def ft(value_mm):
    return float(value_mm) / MM


def square_segments(x0, y0, size, transform_free=True):
    """A closed square of line segments, in feet, as Revit hands them over."""
    corners = [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)]
    segments = []
    for index in range(4):
        a = corners[index]
        b = corners[(index + 1) % 4]
        segments.append(Segment(Line(XYZ(ft(a[0]), ft(a[1]), 0.0),
                                     XYZ(ft(b[0]), ft(b[1]), 0.0))))
    return [segments]


# ------------------------------------------------------------------ tests


class BoundaryLocationTests(unittest.TestCase):
    def test_the_models_own_setting_is_used_not_the_finish_default(self):
        db = make_db(boundary_location=u"SpatialElementBoundaryLocation.CoreBoundary")
        name, source, note = revit.resolved_boundary_location(Doc(), state.KIND_ROOM, db=db)
        self.assertEqual(name, "CoreBoundary")
        self.assertEqual(source, "document")
        self.assertEqual(note, u"")

    def test_a_space_reads_the_space_setting_not_the_room_one(self):
        db = make_db(boundary_location=u"SpatialElementBoundaryLocation.CoreBoundary")
        name, _source, _note = revit.resolved_boundary_location(Doc(), state.KIND_SPACE, db=db)
        self.assertEqual(name, "Center")

    def test_a_setting_that_throws_falls_back_to_finish_and_says_so(self):
        db = make_db(settings_throws=True)
        name, source, note = revit.resolved_boundary_location(Doc(), state.KIND_ROOM, db=db)
        self.assertEqual(name, "Finish")
        self.assertEqual(source, "default")
        self.assertIn(u"wall finish was assumed", note)

    def test_a_build_without_the_setting_is_named_rather_than_silent(self):
        db = make_db(no_settings=True)
        name, source, note = revit.resolved_boundary_location(Doc(), state.KIND_ROOM, db=db)
        self.assertEqual((name, source), ("Finish", "default"))
        self.assertTrue(note)

    def test_the_resolved_location_reaches_the_boundary_options(self):
        db = make_db(boundary_location=u"SpatialElementBoundaryLocation.CoreCenter")
        doc = Doc()
        room = Room(doc, 1, loops=square_segments(0, 0, 1000))
        revit.read_boundaries({"element": room, "uid": u"r1"}, "CoreCenter", db=db)
        self.assertEqual(room.options_seen, [u"CoreCenter"])


class BoundaryReadingTests(unittest.TestCase):
    def test_a_square_room_comes_back_in_millimetres(self):
        db = make_db()
        doc = Doc()
        room = Room(doc, 1, loops=square_segments(0, 0, 2000))
        result = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db)
        self.assertEqual(result["code"], u"")
        self.assertEqual(len(result["loops"]), 1)
        self.assertAlmostEqual(result["fingerprints"]["area_mm2"], 4000000.0, places=0)

    def test_a_linked_room_arrives_in_host_coordinates(self):
        db = make_db()
        doc = Doc()
        room = Room(doc, 1, loops=square_segments(0, 0, 1000))
        plain = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db)
        moved = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db,
                                      transform=Transform(ft(5000), ft(0), 0.0))
        # Same shape, different place: exactly what a link's transform does.
        self.assertEqual(plain["fingerprints"]["shape"], moved["fingerprints"]["shape"])
        self.assertNotEqual(plain["fingerprints"]["abs"], moved["fingerprints"]["abs"])
        self.assertAlmostEqual(moved["fingerprints"]["min_corner"][0], 5000.0, places=0)

    def test_an_arc_segment_survives_as_an_arc_record(self):
        db = make_db()
        doc = Doc()
        arc = Arc(XYZ(ft(0), ft(0), 0.0), XYZ(ft(1000), ft(0), 0.0), XYZ(ft(500), ft(-200), 0.0))
        segments = [Segment(arc),
                    Segment(Line(XYZ(ft(1000), ft(0), 0.0), XYZ(ft(1000), ft(1000), 0.0))),
                    Segment(Line(XYZ(ft(1000), ft(1000), 0.0), XYZ(ft(0), ft(1000), 0.0))),
                    Segment(Line(XYZ(ft(0), ft(1000), 0.0), XYZ(ft(0), ft(0), 0.0)))]
        room = Room(doc, 1, loops=[segments])
        result = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db)
        kinds = [record[0] for record in result["loops"][0]["records"]]
        self.assertEqual(kinds[0], "A")
        # Tessellated for the fingerprint, so the arc is more than two points.
        self.assertGreater(len(result["loops"][0]["points"]), 8)

    def test_the_sessions_short_curve_tolerance_decides_what_is_dropped(self):
        """Revit's own number, read from the session, not a guess: the
        adapter reads Application.ShortCurveTolerance and the repair drops
        exactly what Line.CreateBound would refuse."""
        db = make_db()
        doc = Doc()
        doc.Application = types.SimpleNamespace(ShortCurveTolerance=1.0 / 304.8)  # 1 mm
        segments = square_segments(0, 0, 2000)[0]
        stub = Segment(Line(XYZ(ft(2000), 0.0, 0.0), XYZ(ft(2000), ft(0.9), 0.0)))
        segments[1] = Segment(Line(XYZ(ft(2000), ft(0.9), 0.0), XYZ(ft(2000), ft(2000), 0.0)))
        segments.insert(1, stub)
        room = Room(doc, 1, loops=[segments])
        result = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db)
        self.assertEqual(result["code"], u"")
        self.assertIn("short_segment", result["notes"])
        self.assertEqual(len(result["loops"][0]["records"]), 4)
        self.assertAlmostEqual(revit.short_curve_mm(doc), 1.05, places=6)
        self.assertEqual(revit.short_curve_mm(Doc()), state.SHORT_MM)

    def test_a_room_with_no_segments_reads_as_not_enclosed(self):
        db = make_db()
        room = Room(Doc(), 1, loops=[])
        result = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db)
        self.assertEqual(result["code"], "not_enclosed")
        self.assertTrue(result["sentence"])

    def test_a_boundary_read_that_throws_is_named_not_swallowed(self):
        db = make_db()

        class Angry(Room):
            def GetBoundarySegments(self, options):
                raise Exception("no can do")

        result = revit.read_boundaries({"element": Angry(Doc(), 1), "uid": u"r1"}, "Finish", db=db)
        self.assertEqual(result["code"], "boundary_unreadable")
        self.assertIn(u"no can do", result["sentence"])


class BoundaryCacheTests(unittest.TestCase):
    def test_a_room_is_read_once_however_many_views_want_it(self):
        db = make_db()
        doc = Doc()
        room = Room(doc, 1, loops=square_segments(0, 0, 1000))
        rows = [{"element": room, "uid": u"r1"}] * 5
        result = revit.read_boundary_map(rows, "Finish", db=db)
        self.assertEqual(room.boundary_calls, 1)
        self.assertEqual(list(result["boundaries"]), [u"r1"])

    def test_each_rooms_own_transform_is_used(self):
        db = make_db()
        doc = Doc()
        here = Room(doc, 1, loops=square_segments(0, 0, 1000))
        there = Room(doc, 2, loops=square_segments(0, 0, 1000), unique_id=u"r2")
        rows = [{"element": here, "uid": u"r1", "transform": None},
                {"element": there, "uid": u"r2", "transform": Transform(ft(9000), 0.0, 0.0)}]
        boundaries = revit.read_boundary_map(rows, "Finish", db=db)["boundaries"]
        self.assertAlmostEqual(boundaries[u"r1"]["fingerprints"]["min_corner"][0], 0.0, places=0)
        self.assertAlmostEqual(boundaries[u"r2"]["fingerprints"]["min_corner"][0], 9000.0, places=0)

    def test_a_cancelled_scan_names_the_rooms_it_never_reached(self):
        db = make_db()
        doc = Doc()
        rows = []
        for index in range(60):
            room = Room(doc, index + 1, loops=square_segments(0, 0, 1000),
                        unique_id=u"r{0}".format(index))
            rows.append({"element": room, "uid": u"r{0}".format(index)})
        result = revit.read_boundary_map(rows, "Finish", db=db, progress=lambda done, total: False)
        self.assertTrue(result["cancelled"])
        codes = set(entry["code"] for entry in result["boundaries"].values())
        self.assertIn("cancelled", codes)
        for entry in result["boundaries"].values():
            self.assertTrue(entry["code"] == u"" or entry["sentence"])


class SourceTests(unittest.TestCase):
    def _doc_with_links(self, titles_and_docs):
        doc = Doc()
        for index, (name, link_doc, transform) in enumerate(titles_and_docs):
            instance = RevitLinkInstance(doc, 100 + index, name, link_doc, transform)
            doc.add(instance)
        return doc

    def test_this_model_is_always_the_first_source(self):
        doc = self._doc_with_links([])
        rows = revit.collect_sources(doc, db=make_db())
        self.assertEqual(rows[0]["key"], state.HOST_KEY)
        self.assertTrue(rows[0]["is_host"])

    def test_an_unloaded_link_is_listed_with_a_reason_not_dropped(self):
        arch = Doc(title=u"Arch")
        doc = self._doc_with_links([(u"Arch.rvt : 1", arch, Transform()),
                                    (u"Struct.rvt : 1", None, Transform())])
        rows = revit.collect_sources(doc, db=make_db())
        by_key = dict((row["key"], row) for row in rows)
        self.assertIn(u"Struct.rvt", by_key)
        self.assertFalse(by_key[u"Struct.rvt"]["loaded"])
        self.assertEqual(by_key[u"Struct.rvt"]["status"], u"not loaded")

    def test_two_instances_of_one_link_are_two_sources(self):
        arch = Doc(title=u"Arch")
        doc = self._doc_with_links([(u"Arch.rvt : 1", arch, Transform()),
                                    (u"Arch.rvt : 2", arch, Transform(10.0, 0.0, 0.0))])
        rows = [row for row in revit.collect_sources(doc, db=make_db()) if not row["is_host"]]
        self.assertEqual([row["key"] for row in rows], [u"Arch", u"Arch (2)"])
        # Distinct uids are what keep their regions from being confused for
        # each other: the pair key carries the link instance, not the file.
        self.assertNotEqual(rows[0]["uid"], rows[1]["uid"])
        self.assertNotEqual(state.pair_key(u"v", u"r", rows[0]["uid"]),
                            state.pair_key(u"v", u"r", rows[1]["uid"]))


class SpatialCollectionTests(unittest.TestCase):
    def _model(self):
        doc = Doc()
        level = Level(doc, 10, u"Level 1", 0.0)
        doc.add(level)
        phase = Element(doc, 20, u"New Construction")
        doc.add(phase)
        room = Room(doc, 1, number=u"101", name=u"Office", level=level,
                    loops=square_segments(0, 0, 1000))
        room.category_member = BIC.OST_Rooms
        room.params[u"ROOM_PHASE"] = Param(element_id=ElementId(20))
        doc.add(room)
        return doc, level, room

    def test_a_host_room_is_keyed_by_its_level_id(self):
        doc, level, _room = self._model()
        rows = revit.collect_spatial(doc, state.KIND_ROOM, db=make_db())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["level_key"], u"id:10")
        self.assertEqual(rows[0]["number"], u"101")
        self.assertEqual(rows[0]["phase_name"], u"New Construction")

    def test_a_linked_room_is_keyed_by_elevation_because_ids_mean_nothing(self):
        doc, _level, _room = self._model()
        rows = revit.collect_spatial(doc, state.KIND_ROOM, db=make_db(), link_uid=u"L1",
                                     link_title=u"Arch", transform=Transform(0.0, 0.0, 0.0))
        self.assertTrue(rows[0]["level_key"].startswith(u"el:"))
        self.assertEqual(rows[0]["link_title"], u"Arch")

    def test_a_links_level_elevation_is_transformed_into_host_terms(self):
        doc, _level, _room = self._model()
        shifted = revit.collect_spatial(doc, state.KIND_ROOM, db=make_db(), link_uid=u"L1",
                                        transform=Transform(0.0, 0.0, ft(4000)))
        self.assertEqual(shifted[0]["level_key"], revit.elevation_key(4000.0))

    def test_a_room_carries_its_height_in_host_millimetres(self):
        doc, level, room = self._model()
        room.BaseOffset = 1.0
        room.UnboundedHeight = 10.0
        rows = revit.collect_spatial(doc, state.KIND_ROOM, db=make_db())
        self.assertAlmostEqual(rows[0]["base_mm"], 304.8, places=3)
        self.assertAlmostEqual(rows[0]["top_mm"], 304.8 + 3048.0, places=3)
        shifted = revit.collect_spatial(doc, state.KIND_ROOM, db=make_db(), link_uid=u"L1",
                                        transform=Transform(0.0, 0.0, ft(4000)))
        self.assertAlmostEqual(shifted[0]["base_mm"], 4304.8, places=3)

    def test_an_upper_limit_beats_the_unbounded_height(self):
        doc, level, room = self._model()
        upper = Level(doc, 11, u"Level 2", 12.0)
        doc.add(upper)
        room.UpperLimit = upper
        room.LimitOffset = -1.0
        rows = revit.collect_spatial(doc, state.KIND_ROOM, db=make_db())
        self.assertAlmostEqual(rows[0]["top_mm"], 11.0 * 304.8, places=3)

    def test_a_redundant_room_carries_revits_own_complaint(self):
        doc, _level, _room = self._model()
        doc.warnings.append(Warning(u"Redundant Room", [1]))
        warnings = revit.redundant_room_ids(doc, db=make_db())
        rows = revit.collect_spatial(doc, state.KIND_ROOM, db=make_db(), warnings=warnings)
        self.assertTrue(rows[0]["redundant"])
        ok, code, _sentence = state.room_verdict(rows[0])
        self.assertFalse(ok)
        self.assertEqual(code, "redundant")


class ViewTests(unittest.TestCase):
    def _model(self):
        doc = Doc()
        level = Level(doc, 10, u"Level 1", 0.0)
        doc.add(level)
        phase = Element(doc, 20, u"New Construction")
        doc.add(phase)
        return doc, level

    def test_templates_and_non_plan_views_never_reach_the_list(self):
        doc, level = self._model()
        doc.add(View(doc, 1, u"L1 Power", level=level, phase_id=20))
        doc.add(View(doc, 2, u"Template", level=level, is_template=True))
        doc.add(View(doc, 3, u"Section A", view_type=u"ViewType.Section", level=level))
        doc.add(View(doc, 4, u"Legend", view_type=u"ViewType.Legend"))
        rows = revit.collect_views(doc, db=make_db())
        self.assertEqual([row["name"] for row in rows], [u"L1 Power"])
        self.assertEqual(rows[0]["level_name"], u"Level 1")
        self.assertEqual(rows[0]["phase_name"], u"New Construction")

    def test_a_ceiling_plan_is_offered_but_not_ticked(self):
        doc, level = self._model()
        doc.add(View(doc, 1, u"L1 Power", level=level))
        doc.add(View(doc, 2, u"L1 RCP", view_type=u"ViewType.CeilingPlan", level=level))
        rows = dict((row["name"], row) for row in revit.collect_views(doc, db=make_db()))
        self.assertTrue(rows[u"L1 Power"]["default_ticked"])
        self.assertFalse(rows[u"L1 RCP"]["default_ticked"])

    def test_a_view_answers_to_both_its_level_id_and_its_elevation(self):
        doc, level = self._model()
        doc.add(View(doc, 1, u"L1 Power", level=level))
        row = revit.collect_views(doc, db=make_db())[0]
        self.assertIn(u"id:10", row["level_keys"])
        self.assertIn(revit.elevation_key(0.0), row["level_keys"])
        # Which is what lets one view draw a host room and a linked one.
        self.assertTrue(state.view_shows_room(row, {"level_key": u"id:10"})[0])
        self.assertTrue(state.view_shows_room(row, {"level_key": revit.elevation_key(0.0)})[0])

    def test_a_plan_carries_its_cut_plane_and_a_view_without_one_says_none(self):
        doc, level = self._model()
        doc.add(View(doc, 1, u"L1 Power", level=level, cut_offset_ft=4.0))
        doc.add(View(doc, 2, u"L1 Area", view_type=u"ViewType.AreaPlan", level=level))
        rows = dict((row["name"], row) for row in revit.collect_views(doc, db=make_db()))
        self.assertAlmostEqual(rows[u"L1 Power"]["cut_mm"], 4.0 * 304.8, places=3)
        self.assertIsNone(rows[u"L1 Area"]["cut_mm"])

    def test_the_active_view_is_found_by_unique_id(self):
        doc, level = self._model()
        view = View(doc, 1, u"L1 Power", level=level)
        doc.add(view)
        doc.ActiveView = view
        rows = revit.collect_views(doc, db=make_db())
        self.assertEqual(revit.active_view_row(doc, rows, db=make_db())["name"], u"L1 Power")


class RegionGeometryTests(unittest.TestCase):
    def test_a_loop_is_built_contiguous_so_revit_accepts_it(self):
        db = make_db()
        doc = Doc()
        room = Room(doc, 1, loops=square_segments(0, 0, 1000))
        boundary = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db)
        loops = revit._materialise_loops(db, boundary["loops"], 0.0)
        self.assertEqual(len(loops), 1)
        self.assertEqual(len(list(loops)[0].curves), 4)

    def test_an_arc_is_drawn_as_an_arc_not_a_chord(self):
        db = make_db()
        record = ("A", 0.0, 0.0, 1000.0, 0.0, 500.0, -200.0)
        curves = revit._curves_for(db, record, 0.0)
        self.assertEqual(len(curves), 1)
        self.assertIsInstance(curves[0], Arc)
        # And it ends where the record ends, not at the record's midpoint.
        self.assertAlmostEqual(curves[0].GetEndPoint(1).X * MM, 1000.0, places=6)
        self.assertAlmostEqual(curves[0].GetEndPoint(1).Y * MM, 0.0, places=6)
        self.assertAlmostEqual(curves[0].middle.X * MM, 500.0, places=6)

    def test_a_curved_room_builds_a_contiguous_loop(self):
        """The second Revit run: every room with a curved wall was refused as
        "this curve will make the loop discontinuous", because the arc was
        being rebuilt ending at its midpoint. The fake CurveLoop refuses a
        non-contiguous append the way Revit does."""
        db = make_db()
        doc = Doc()
        arc = Arc(XYZ(ft(0), ft(0), 0.0), XYZ(ft(1000), ft(0), 0.0), XYZ(ft(500), ft(-200), 0.0))
        segments = [Segment(arc),
                    Segment(Line(XYZ(ft(1000), ft(0), 0.0), XYZ(ft(1000), ft(1000), 0.0))),
                    Segment(Line(XYZ(ft(1000), ft(1000), 0.0), XYZ(ft(0), ft(1000), 0.0))),
                    Segment(Line(XYZ(ft(0), ft(1000), 0.0), XYZ(ft(0), ft(0), 0.0)))]
        room = Room(doc, 1, loops=[segments])
        boundary = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db)
        loops = revit._materialise_loops(db, boundary["loops"], 0.0)
        curves = list(loops)[0].curves
        self.assertEqual(len(curves), 4)
        self.assertIsInstance(curves[0], Arc)

    def test_an_arc_revit_refuses_falls_back_to_a_line(self):
        db = make_db()

        class NoArc(Arc):
            @staticmethod
            def Create(start, end, point_on_arc):
                raise Exception("the arc is degenerate")

        db.Arc = NoArc
        curves = revit._curves_for(db, ("A", 0.0, 0.0, 1000.0, 0.0, 500.0, 0.0), 0.0)
        self.assertIsInstance(curves[0], Line)

    def test_a_region_reads_its_own_outline_back(self):
        db = make_db()
        doc = Doc()
        room = Room(doc, 1, loops=square_segments(0, 0, 1000))
        boundary = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db)
        loops = revit._materialise_loops(db, boundary["loops"], 0.0)
        region = FilledRegion.Create(doc, ElementId(9), ElementId(1), loops)
        points = revit.read_region_loops(region, db=db)
        self.assertEqual(len(points), 1)
        self.assertEqual(state.fingerprints(points)["shape"],
                         boundary["fingerprints"]["shape"])

    def test_a_region_that_cannot_say_what_it_is_reads_as_unreadable(self):
        db = make_db()
        doc = Doc()
        region = _NoBoundaryRegion(doc, 1, ElementId(9), ElementId(1), [])
        self.assertIsNone(revit.read_region_loops(region, db=db))


class CreateRegionTests(unittest.TestCase):
    def setUp(self):
        del LOG[:]
        FilledRegion.created = []
        FilledRegion.refuse = None
        self.registry = SchemaRegistry()

    def tearDown(self):
        FilledRegion.refuse = None

    def _plan(self, doc, db, views, rooms):
        boundaries = {}
        items = []
        for room, room_uid in rooms:
            boundaries[room_uid] = revit.read_boundaries({"element": room, "uid": room_uid},
                                                         "Finish", db=db)
        for view, view_id in views:
            for _room, room_uid in rooms:
                items.append({
                    "key": state.pair_key(view.UniqueId, room_uid),
                    "action": "create", "title": u"{0} in {1}".format(room_uid, view.Name),
                    "room_uid": room_uid, "room_id": 1, "room_number": u"101",
                    "room_name": u"Office", "link_uid": u"", "link_title": u"",
                    "view_uid": view.UniqueId, "view_id": view_id, "view_name": view.Name,
                    "level_name": u"Level 1", "phase_name": u"New Construction",
                    "boundary_key": room_uid, "fingerprints": boundaries[room_uid].get(
                        "fingerprints") or {},
                    "replaces_region_id": None, "in_group": False,
                })
        return {"mode": "create", "kind": state.KIND_ROOM, "boundary_location": "Finish",
                "grid_mm": 1.0, "region_type": {"id": 9, "name": u"Solid"},
                "items": items, "skips": [], "boundaries": boundaries,
                "counts": {}, "acknowledgements": [], "refusal": u"", "notes": []}

    def _model(self, db):
        doc = Doc()
        level = Level(doc, 10, u"Level 1", 0.0)
        doc.add(level)
        doc.add(FilledRegionType(doc, 9, u"Solid"))
        view = View(doc, 1, u"L1 Power", level=level)
        doc.add(view)
        room = Room(doc, 2, number=u"101", name=u"Office", level=level,
                    loops=square_segments(0, 0, 2000))
        doc.add(room)
        return doc, view, room

    def test_the_nesting_is_group_then_transaction_then_subtransaction(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        result = revit.create_regions(doc, plan, db=db, created_utc=u"2026-09-10T00:00:00Z")
        self.assertEqual(result["created"], 1)
        self.assertEqual([entry[0] for entry in LOG],
                         ["group.start", "txn.start", "sub.start", "sub.commit",
                          "txn.commit", "group.assimilate"])

    def test_a_loop_revit_refuses_is_drawn_again_from_its_simplified_outline(self):
        """A stub, an arc Revit will not build, loops touching at a vertex:
        the exact loop is refused, the cleaned polygon is not, and the note
        says which it was."""
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        attempts = {"count": 0}

        def refuse(_doc, _view_id, _loops):
            attempts["count"] += 1
            return attempts["count"] == 1

        FilledRegion.refuse = refuse
        result = revit.create_regions(doc, plan, db=db)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["failed"], [])
        self.assertEqual(attempts["count"], 2)
        self.assertTrue([note for note in result["notes"] if u"simplified outline" in note])
        # The fallback is the same square, drawn with straight edges.
        drawn = FilledRegion.created[0]
        self.assertEqual(len(list(drawn.loops)[0].curves), 4)

    def test_a_loop_that_will_not_build_still_falls_back(self):
        """A CurveLoop.Append refusal happens before FilledRegion.Create is
        ever reached; it has to be caught the same way, or the fallback never
        runs and the room is lost with the loop."""
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        # Break the exact records so the loop is discontinuous; the tessellated
        # points, which the fallback draws from, stay whole.
        records = plan["boundaries"][u"r1"]["loops"][0]["records"]
        records[1] = ("L", 2000.0, 0.0, 2000.0, 1999.0)
        result = revit.create_regions(doc, plan, db=db)
        self.assertEqual(result["created"], 1)
        self.assertTrue([note for note in result["notes"] if u"simplified outline" in note])

    def test_a_loop_refused_twice_fails_with_both_reasons(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        FilledRegion.refuse = lambda _d, _v, _l: True
        result = revit.create_regions(doc, plan, db=db)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["failed"][0]["code"], "revit_refused")
        self.assertIn(u"will not draw", result["failed"][0]["reason"])
        self.assertIn(u"simplified outline", result["failed"][0]["reason"])

    def test_a_loop_refused_twice_fails_with_revits_own_words(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        FilledRegion.refuse = lambda _d, _v, _l: True
        result = revit.create_regions(doc, plan, db=db)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["failed"][0]["code"], "revit_refused")
        self.assertIn(u"will not draw", result["failed"][0]["reason"])

    def test_the_boundary_line_style_is_set_on_each_region(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        plan["line_style"] = {"id": 12, "name": u"<Invisible lines>"}
        revit.create_regions(doc, plan, db=db)
        self.assertEqual(FilledRegion.created[0].line_style_id, ElementId(12))

    def test_no_line_style_chosen_leaves_the_types_default_alone(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        revit.create_regions(doc, plan, db=db)
        self.assertIsNone(FilledRegion.created[0].line_style_id)

    def test_the_record_is_refreshed_after_one_regeneration_per_view(self):
        """The bug the first Revit run found: a region reports no outline
        until Revit regenerates, so the record written straight after Create
        carried an empty digest and every later hand edit read as in step."""
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        other = Room(doc, 3, number=u"102", level=doc.by_id[10],
                     loops=square_segments(5000, 0, 2000), unique_id=u"r2")
        doc.add(other)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1"), (other, u"r2")])
        FilledRegion.lazy = True
        try:
            revit.create_regions(doc, plan, db=db)
        finally:
            FilledRegion.lazy = False
        self.assertEqual(doc.regenerated, 1)
        for region in FilledRegion.created:
            record = json.loads(region.entity.value)
            self.assertTrue(record["region"]["abs"])
            self.assertEqual(record["region"]["abs"], record["room"]["abs"])
            self.assertNotIn("outline", record["region"])
        # And the nesting the write promised is untouched by it.
        kinds = [entry[0] for entry in LOG]
        self.assertEqual(kinds[0], "group.start")
        self.assertEqual(kinds[-2:], ["txn.commit", "group.assimilate"])
        self.assertEqual(kinds.count("sub.commit"), 2)

    def test_the_record_lands_on_the_region_inside_that_transaction(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        revit.create_regions(doc, plan, db=db, created_utc=u"2026-09-10T00:00:00Z")
        region = FilledRegion.created[0]
        record = json.loads(region.entity.value)
        self.assertEqual(record["room_uid"], u"r1")
        self.assertEqual(record["view_name"], u"L1 Power")
        self.assertEqual(record["boundary_location"], "Finish")
        self.assertEqual(record["created_utc"], u"2026-09-10T00:00:00Z")
        self.assertTrue(record["room"]["shape"])
        self.assertTrue(record["region"]["shape"])

    def test_one_room_revit_refuses_costs_one_room(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        other = Room(doc, 3, number=u"102", level=doc.by_id[10],
                     loops=square_segments(5000, 0, 2000), unique_id=u"r2")
        doc.add(other)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1"), (other, u"r2")])
        seen = {"count": 0}

        def refuse(_doc, _view_id, _loops):
            # The first room is refused twice: its exact loop and then its
            # simplified outline, which is what "Revit refuses it" now means.
            seen["count"] += 1
            return seen["count"] <= 2

        FilledRegion.refuse = refuse
        result = revit.create_regions(doc, plan, db=db)
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["code"], "revit_refused")
        self.assertIn(("sub.rollback", None), LOG)
        self.assertIn(("txn.commit", u"Space Boundary: L1 Power"), LOG)

    def test_a_rolled_back_view_takes_its_counters_with_it(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])

        class Exploding(Transaction):
            def Commit(self):
                raise Exception("the view would not commit")

        db.Transaction = Exploding
        result = revit.create_regions(doc, plan, db=db)
        # The work is gone from the model, so it is gone from the report too.
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["views_done"], 0)
        self.assertEqual(result["failed"][0]["code"], "revit_refused")
        self.assertIn(("txn.rollback", u"Space Boundary: L1 Power"), LOG)

    def test_a_view_owned_by_someone_else_is_a_named_skip(self):
        db = make_db(registry=self.registry, checkout_owner=u"jsmith")
        doc, view, room = self._model(db)
        doc.IsWorkshared = True
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        result = revit.create_regions(doc, plan, db=db)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"][0]["code"], "owned_by_other")
        self.assertIn(u"jsmith", result["skipped"][0]["reason"])
        self.assertNotIn("txn.start", [entry[0] for entry in LOG])

    def test_only_the_ticked_pairs_are_drawn(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        other = Room(doc, 3, number=u"102", level=doc.by_id[10],
                     loops=square_segments(5000, 0, 2000), unique_id=u"r2")
        doc.add(other)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1"), (other, u"r2")])
        wanted = plan["items"][1]["key"]
        result = revit.create_regions(doc, plan, db=db, checked_keys=[wanted])
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(FilledRegion.created), 1)

    def test_nothing_ticked_opens_no_transaction_at_all(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        result = revit.create_regions(doc, plan, db=db, checked_keys=[])
        self.assertEqual(result["created"], 0)
        self.assertEqual(LOG, [])

    def test_a_replace_deletes_the_old_region_first_and_counts_apart(self):
        db = make_db(registry=self.registry)
        doc, view, room = self._model(db)
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        revit.create_regions(doc, plan, db=db)
        old = FilledRegion.created[0]
        del LOG[:]
        plan = self._plan(doc, db, [(view, 1)], [(room, u"r1")])
        plan["items"][0]["action"] = "replace"
        plan["items"][0]["replaces_region_id"] = old.Id.IntegerValue
        result = revit.create_regions(doc, plan, db=db)
        self.assertEqual((result["created"], result["replaced"]), (0, 1))
        self.assertIn(old, doc.deleted)


class LinkedRunTests(unittest.TestCase):
    """Two placements of one architectural link, converted in one run."""

    def setUp(self):
        del LOG[:]
        FilledRegion.created = []
        FilledRegion.refuse = None

    def test_two_placements_draw_two_independent_sets_at_their_transforms(self):
        db = make_db(registry=SchemaRegistry())
        host = Doc()
        level = Level(host, 10, u"Level 1", 0.0)
        host.add(level)
        host.add(FilledRegionType(host, 9, u"Solid"))
        view = View(host, 1, u"L1 Power", level=level)
        host.add(view)

        arch = Doc(title=u"Arch")
        arch_level = Level(arch, 60, u"Level 1", 0.0)
        arch.add(arch_level)
        arch_room = Room(arch, 61, number=u"101", name=u"Office", level=arch_level,
                         loops=square_segments(0, 0, 2000), unique_id=u"arch-room")
        arch_room.category_member = BIC.OST_Rooms
        arch.add(arch_room)

        placements = [(u"L1", Transform(0.0, 0.0, 0.0)),
                      (u"L2", Transform(ft(30000), 0.0, 0.0))]
        rooms = []
        for link_uid, transform in placements:
            rooms.extend(revit.collect_spatial(arch, state.KIND_ROOM, db=db, link_uid=link_uid,
                                               link_title=u"Arch", transform=transform))
        # One room element, two placements: two rows, and the uid a plan keys
        # by has to carry the placement or the second would overwrite the first.
        self.assertEqual(len(rooms), 2)
        for index, row in enumerate(rooms):
            row["uid"] = u"{0}|{1}".format(row["link_uid"], row["uid"])
        boundaries = revit.read_boundary_map(rooms, "Finish", db=db)["boundaries"]
        corners = sorted(round(entry["fingerprints"]["min_corner"][0])
                         for entry in boundaries.values())
        self.assertEqual(corners, [0, 30000])
        shapes = set(entry["fingerprints"]["shape"] for entry in boundaries.values())
        self.assertEqual(len(shapes), 1)

    def test_a_link_whose_document_is_gone_is_a_named_row_not_an_empty_list(self):
        doc = Doc()
        doc.add(RevitLinkInstance(doc, 100, u"Arch.rvt : 1", None))
        rows = revit.collect_sources(doc, db=make_db())
        link_rows = [row for row in rows if not row["is_host"]]
        self.assertEqual(len(link_rows), 1)
        self.assertFalse(link_rows[0]["loaded"])
        self.assertTrue(link_rows[0]["status"])
        self.assertTrue(state.CODE_SENTENCES["link_not_loaded"])


class RelationshipReadTests(unittest.TestCase):
    def setUp(self):
        del LOG[:]
        FilledRegion.created = []
        FilledRegion.refuse = None
        self.registry = SchemaRegistry()

    def _drawn(self, db):
        doc = Doc()
        level = Level(doc, 10, u"Level 1", 0.0)
        doc.add(level)
        doc.add(FilledRegionType(doc, 9, u"Solid"))
        view = View(doc, 1, u"L1 Power", level=level)
        doc.add(view)
        room = Room(doc, 2, number=u"101", name=u"Office", level=level,
                    loops=square_segments(0, 0, 2000))
        doc.add(room)
        boundary = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db)
        plan = {"mode": "create", "kind": state.KIND_ROOM, "boundary_location": "Finish",
                "grid_mm": 1.0, "region_type": {"id": 9},
                "boundaries": {u"r1": boundary},
                "items": [{"key": state.pair_key(view.UniqueId, u"r1"), "action": "create",
                           "title": u"101", "room_uid": u"r1", "room_id": 2,
                           "room_number": u"101", "room_name": u"Office", "link_uid": u"",
                           "link_title": u"", "view_uid": view.UniqueId, "view_id": 1,
                           "view_name": u"L1 Power", "level_name": u"Level 1",
                           "phase_name": u"", "boundary_key": u"r1",
                           "fingerprints": boundary["fingerprints"],
                           "replaces_region_id": None, "in_group": False}]}
        revit.create_regions(doc, plan, db=db, created_utc=u"2026-09-10T00:00:00Z")
        return doc, view, room

    def test_every_tracked_region_comes_back_with_its_record_and_its_outline(self):
        db = make_db(registry=self.registry)
        doc, view, _room = self._drawn(db)
        rows = revit.read_relationships(doc, db=db)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["readable"])
        self.assertEqual(rows[0]["record"]["room_uid"], u"r1")
        self.assertEqual(rows[0]["owner_view_uid"], view.UniqueId)
        self.assertEqual(rows[0]["fingerprints"]["abs"], rows[0]["record"]["region"]["abs"])

    def test_an_untouched_region_reads_as_in_step_with_its_room(self):
        db = make_db(registry=self.registry)
        doc, _view, room = self._drawn(db)
        row = revit.read_relationships(doc, db=db)[0]
        now = revit.read_boundaries({"element": room, "uid": u"r1"}, "Finish", db=db)
        verdict, _sentence = state.compare(row["record"]["room"], now["fingerprints"])
        self.assertEqual(verdict, "unchanged")

    def test_deleting_regions_is_one_transaction_and_names_what_stayed(self):
        db = make_db(registry=self.registry)
        doc, _view, _room = self._drawn(db)
        region = FilledRegion.created[0]
        del LOG[:]
        result = revit.delete_regions(doc, [region.Id.IntegerValue, 9999], db=db)
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual([entry[0] for entry in LOG].count("txn.start"), 1)


class VisibilityTests(unittest.TestCase):
    def _host(self):
        doc = Doc()
        level = Level(doc, 10, u"Level 1", 0.0)
        doc.add(level)
        view = View(doc, 1, u"L1 Power", level=level, cut_offset_ft=4.0)
        doc.add(view)
        rooms = []
        for index in (1, 2, 3):
            room = Room(doc, 100 + index, number=u"10{0}".format(index), level=level,
                        loops=square_segments(0, 0, 1000), unique_id=u"r{0}".format(index))
            room.category_member = BIC.OST_Rooms
            doc.add(room)
            rooms.append(room)
        return doc, view, rooms

    def test_this_models_rooms_are_what_the_view_collector_returns(self):
        db = make_db()
        doc, view, rooms = self._host()
        doc.visible_by_view[1] = [101, 103]
        view_rows = revit.collect_views(doc, db=db)
        room_rows = revit.collect_spatial(doc, state.KIND_ROOM, db=db)
        found = revit.visibility_map(doc, view_rows, room_rows, [], state.KIND_ROOM, db=db)
        self.assertEqual(found[view.UniqueId]["visible"], set([u"r1", u"r3"]))
        self.assertEqual(found[view.UniqueId]["note"], u"")

    def test_a_view_revit_will_not_read_answers_none_and_says_so(self):
        db = make_db()
        doc, view, rooms = self._host()
        doc.visible_by_view[1] = "raise"
        view_rows = revit.collect_views(doc, db=db)
        room_rows = revit.collect_spatial(doc, state.KIND_ROOM, db=db)
        found = revit.visibility_map(doc, view_rows, room_rows, [], state.KIND_ROOM, db=db)
        self.assertIsNone(found[view.UniqueId]["visible"])
        self.assertIn(u"L1 Power", found[view.UniqueId]["note"])

    def _linked(self, mode, linked_view_id=-1):
        db = make_db()
        doc, view, _host_rooms = self._host()
        doc.visible_by_view[1] = []
        arch = Doc(title=u"Arch")
        arch_level = Level(arch, 60, u"Level 1", 0.0)
        arch.add(arch_level)
        linked_view = View(arch, 70, u"Arch L1", level=arch_level, cut_offset_ft=4.0)
        arch.add(linked_view)
        low = Room(arch, 61, number=u"201", level=arch_level, loops=square_segments(0, 0, 1000),
                   unique_id=u"low")
        high = Room(arch, 62, number=u"202", level=arch_level, loops=square_segments(0, 0, 1000),
                    unique_id=u"high")
        high.BaseOffset = 10.0
        for room in (low, high):
            room.category_member = BIC.OST_Rooms
            arch.add(room)
        instance = RevitLinkInstance(doc, 100, u"Arch.rvt : 1", arch)
        doc.add(instance)
        if mode is not None:
            view.link_overrides[100] = LinkOverrides(mode, linked_view_id)
        sources = revit.collect_sources(doc, db=db)
        for row in sources:
            row["is_checked"] = True
        rooms = revit.rooms_from_sources(sources, state.KIND_ROOM, db=db)["rooms"]
        views = revit.collect_views(doc, db=db)
        return db, doc, view, arch, sources, rooms, views, instance

    def test_by_linked_view_asks_the_linked_view_itself(self):
        db, doc, view, arch, sources, rooms, views, _i = self._linked("ByLinkView", 70)
        arch.visible_by_view[70] = [62]
        found = revit.visibility_map(doc, views, rooms, sources, state.KIND_ROOM, db=db)
        visible = found[view.UniqueId]["visible"]
        self.assertEqual(len(visible), 1)
        self.assertTrue(list(visible)[0].endswith(u"high"))

    def test_by_host_view_applies_the_host_cut_plane_to_each_linked_room(self):
        db, doc, view, arch, sources, rooms, views, _i = self._linked("ByHostView")
        found = revit.visibility_map(doc, views, rooms, sources, state.KIND_ROOM, db=db)
        visible = found[view.UniqueId]["visible"]
        # The cut plane sits 4 ft up: through the low room, under the high one.
        self.assertEqual([uid.rsplit(u"|", 1)[-1] for uid in sorted(visible)], [u"low"])

    def test_no_overrides_at_all_reads_as_by_host_view(self):
        db, doc, view, arch, sources, rooms, views, _i = self._linked(None)
        found = revit.visibility_map(doc, views, rooms, sources, state.KIND_ROOM, db=db)
        self.assertEqual(len(found[view.UniqueId]["visible"]), 1)

    def test_a_link_hidden_in_the_view_shows_nothing(self):
        db, doc, view, arch, sources, rooms, views, instance = self._linked("ByHostView")
        instance.hidden_in.add(1)
        found = revit.visibility_map(doc, views, rooms, sources, state.KIND_ROOM, db=db)
        self.assertEqual(found[view.UniqueId]["visible"], set())

    def test_the_category_hidden_in_the_host_view_hides_the_linked_rooms_and_says_so(self):
        """The second Revit run: MEP views hide Rooms by view template, so a
        link planned nothing - correctly - and nobody was told why."""
        db, doc, view, arch, sources, rooms, views, _i = self._linked("ByHostView")
        view.hidden_categories.add(BIC.OST_Rooms)
        found = revit.visibility_map(doc, views, rooms, sources, state.KIND_ROOM, db=db)
        self.assertEqual(found[view.UniqueId]["visible"], set())
        note = found[view.UniqueId]["note"]
        self.assertIn(u"Rooms", note)
        self.assertIn(u"L1 Power", note)
        self.assertIn(u"Arch", note)
        self.assertIn(u"view template", note)

    def test_a_link_hidden_in_the_view_says_so_by_name(self):
        db, doc, view, arch, sources, rooms, views, instance = self._linked("ByHostView")
        instance.hidden_in.add(1)
        found = revit.visibility_map(doc, views, rooms, sources, state.KIND_ROOM, db=db)
        self.assertIn(u"Arch", found[view.UniqueId]["note"])
        self.assertIn(u"hidden in L1 Power", found[view.UniqueId]["note"])

    def test_a_linked_view_revit_will_not_read_is_named_not_emptied(self):
        db, doc, view, arch, sources, rooms, views, _i = self._linked("ByLinkView", 70)
        arch.visible_by_view[70] = "raise"
        found = revit.visibility_map(doc, views, rooms, sources, state.KIND_ROOM, db=db)
        self.assertIsNone(found[view.UniqueId]["visible"])
        self.assertIn(u"Arch", found[view.UniqueId]["note"])


class PickTests(unittest.TestCase):
    def setUp(self):
        self._module = revit._selection_module

    def tearDown(self):
        revit._selection_module = self._module

    def _fake_selection(self, references, cancel=False):
        class ISelectionFilter(object):
            pass

        module = types.SimpleNamespace(ISelectionFilter=ISelectionFilter,
                                       ObjectType=types.SimpleNamespace(Element=u"Element"))
        revit._selection_module = lambda: module
        seen = {}

        def pick_objects(object_type, selection_filter, prompt):
            seen["filter"] = selection_filter
            seen["prompt"] = prompt
            if cancel:
                raise Exception("OperationCanceledException")
            return [reference for reference in references
                    if selection_filter.AllowElement(reference.element)]

        return seen, types.SimpleNamespace(PickObjects=pick_objects)

    def test_many_rooms_come_back_and_a_wall_is_kept_out_of_the_pick(self):
        db = make_db()
        doc = Doc()
        level = Level(doc, 10, u"Level 1", 0.0)
        rooms = [Room(doc, 1, number=u"101", name=u"Office", level=level, unique_id=u"a"),
                 Room(doc, 2, number=u"102", name=u"Store", level=level, unique_id=u"b")]
        wall = Element(doc, 3, u"Wall")
        wall.Category = types.SimpleNamespace(Id=ElementId(-2000011))
        for element in rooms + [wall]:
            doc.add(element)
        references = [types.SimpleNamespace(ElementId=element.Id, element=element)
                      for element in rooms + [wall, rooms[0]]]
        seen, selection = self._fake_selection(references)
        uidoc = types.SimpleNamespace(Document=doc, Selection=selection)
        picked = revit.pick_spatial(uidoc, state.KIND_ROOM, db=db)
        self.assertEqual([entry["label"] for entry in picked], [u"101 Office", u"102 Store"])
        self.assertIn(u"Finish", seen["prompt"])
        self.assertFalse(seen["filter"].AllowReference(None, None))

    def test_a_cancelled_pick_is_an_empty_list(self):
        db = make_db()
        _seen, selection = self._fake_selection([], cancel=True)
        uidoc = types.SimpleNamespace(Document=Doc(), Selection=selection)
        self.assertEqual(revit.pick_spatial(uidoc, state.KIND_ROOM, db=db), [])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.registry = SchemaRegistry()

    def test_a_write_outside_a_transaction_is_refused_not_silently_dropped(self):
        db = make_db(registry=self.registry)
        doc = Doc()
        region = FilledRegion(doc, 1, ElementId(9), ElementId(1), [])
        doc.IsModifiable = False
        ok, reason = storage.write_record(doc, region, {"v": 1}, db=db)
        self.assertFalse(ok)
        self.assertIn(u"transaction", reason.lower())
        self.assertIsNone(region.entity)

    def test_a_write_inside_the_callers_transaction_lands(self):
        db = make_db(registry=self.registry)
        doc = Doc()
        region = FilledRegion(doc, 1, ElementId(9), ElementId(1), [])
        doc.IsModifiable = True
        ok, reason = storage.write_record(doc, region, {"v": 1, "room_uid": u"r1"}, db=db)
        self.assertTrue(ok)
        self.assertEqual(reason, u"")
        self.assertEqual(storage.read_record(region, db=db)["room_uid"], u"r1")

    def test_the_schema_is_looked_up_before_it_is_built(self):
        db = make_db(registry=self.registry)
        doc = Doc()
        doc.IsModifiable = True
        for index in range(3):
            region = FilledRegion(doc, index, ElementId(9), ElementId(1), [])
            storage.write_record(doc, region, {"v": 1}, db=db)
        self.assertEqual(self.registry.builds, 1)

    def test_a_foreign_schema_under_our_guid_is_reported_not_crashed_into(self):
        registry = SchemaRegistry()
        registry.by_guid[storage.SCHEMA_GUID] = Schema(_Guid(storage.SCHEMA_GUID),
                                                       fields=("something_else",))
        db = make_db(registry=registry)
        available, reason = storage.availability(Doc(), db=db)
        self.assertFalse(available)
        self.assertIn(u"another add-in", reason.lower())

    def test_an_empty_entity_reads_as_no_record(self):
        db = make_db(registry=self.registry)
        doc = Doc()
        doc.IsModifiable = True
        region = FilledRegion(doc, 1, ElementId(9), ElementId(1), [])
        storage.write_record(doc, region, {}, db=db)
        self.assertEqual(storage.read_record(region, db=db), {})

    def test_a_unicode_room_name_survives_the_round_trip(self):
        db = make_db(registry=self.registry)
        doc = Doc()
        doc.IsModifiable = True
        region = FilledRegion(doc, 1, ElementId(9), ElementId(1), [])
        storage.write_record(doc, region, {"v": 1, "room_name": u"会議室"}, db=db)
        self.assertEqual(storage.read_record(region, db=db)["room_name"], u"会議室")

    def test_a_family_document_cannot_carry_the_record(self):
        db = make_db(registry=self.registry)
        doc = Doc()
        doc.IsFamilyDocument = True
        available, reason = storage.availability(doc, db=db)
        self.assertFalse(available)
        self.assertIn(u"project", reason.lower())

    def test_regions_without_a_record_are_not_returned(self):
        db = make_db(registry=self.registry)
        doc = Doc()
        doc.IsModifiable = True
        tracked = doc.add(FilledRegion(doc, 1, ElementId(9), ElementId(1), []))
        doc.add(FilledRegion(doc, 2, ElementId(9), ElementId(1), []))
        storage.write_record(doc, tracked, {"v": 1, "room_uid": u"r1"}, db=db)
        rows = storage.read_all(doc, db=db)
        self.assertEqual([row["element"] for row in rows], [tracked])

    def test_the_checkout_guard_is_quiet_on_a_model_that_is_not_workshared(self):
        db = make_db(registry=self.registry, checkout_owner=u"jsmith")
        doc = Doc(workshared=False)
        self.assertEqual(storage.checkout_reason(doc, ElementId(1), db=db), u"")


class NameTests(unittest.TestCase):
    def test_a_name_ironpython_cannot_bind_falls_back_to_get_value(self):
        """The empty combo: ``FilledRegionType.Name`` raises under IronPython
        and a getattr default handed back nothing, row after row."""
        db = make_db()
        style = GraphicsStyle(Doc(), 1, u"Thin Lines", -2000051)
        style.fallback_name = u"Thin Lines"
        self.assertEqual(revit.element_name(style, db=db), u"Thin Lines")
        plain = Element(Doc(), 2, u"Solid")
        self.assertEqual(revit.element_name(plain, db=db), u"Solid")

    def test_region_types_are_named_even_when_name_raises(self):
        db = make_db()
        doc = Doc()

        class ShyType(FilledRegionType):
            @property
            def Name(self):
                raise AttributeError("no")

            @Name.setter
            def Name(self, value):
                self.fallback_name = value

        doc.add(ShyType(doc, 3, u"Solid Grey"))
        rows = revit.collect_region_types(doc, db=db)
        self.assertEqual([row["name"] for row in rows], [u"Solid Grey"])


class LineStyleTests(unittest.TestCase):
    def _doc(self):
        doc = Doc()
        doc.add(GraphicsStyle(doc, 11, u"Thin Lines", -2000051))
        doc.add(GraphicsStyle(doc, 12, u"<Lignes invisibles>", BIC.OST_InvisibleLines))
        doc.add(GraphicsStyle(doc, 13, u"Wide Lines", -2000052))
        return doc

    def test_the_invisible_style_is_found_by_category_not_by_its_localised_name(self):
        db = make_db()
        doc = self._doc()
        db.FilledRegion.GetValidLineStyleIdsForFilledRegion = staticmethod(
            lambda document: [ElementId(11), ElementId(12), ElementId(13)])
        try:
            rows = revit.collect_line_styles(doc, db=db)
        finally:
            del db.FilledRegion.GetValidLineStyleIdsForFilledRegion
        self.assertEqual(rows[0]["name"], u"<Lignes invisibles>")
        self.assertTrue(rows[0]["is_invisible"])
        self.assertEqual([row["name"] for row in rows[1:]], [u"Thin Lines", u"Wide Lines"])

    def test_a_revit_without_the_api_offers_no_styles_rather_than_failing(self):
        self.assertEqual(revit.collect_line_styles(self._doc(), db=make_db()), [])


class RegionTypeTests(unittest.TestCase):
    def test_the_region_types_come_back_sorted_by_name(self):
        db = make_db()
        doc = Doc()
        doc.add(FilledRegionType(doc, 3, u"Solid Grey"))
        doc.add(FilledRegionType(doc, 4, u"Diagonal"))
        rows = revit.collect_region_types(doc, db=db)
        self.assertEqual([row["name"] for row in rows], [u"Diagonal", u"Solid Grey"])

    def test_a_model_with_no_region_type_is_an_empty_list_the_gate_can_see(self):
        self.assertEqual(revit.collect_region_types(Doc(), db=make_db()), [])
        self.assertTrue(state.CODE_SENTENCES["no_region_type"])


if __name__ == "__main__":
    unittest.main()
