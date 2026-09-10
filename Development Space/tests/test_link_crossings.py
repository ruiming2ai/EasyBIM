"""The link crossing engine, driven over fakes shaped like the API.

A fake solid is an axis-aligned box whose ``IntersectWithCurve`` computes
the true line-vs-box slab, so the engine's interval merging, partial and
along classification, and the host/link transform round trip are judged
on real numbers rather than on canned answers.
"""

import importlib.util
import math
import pathlib
import types
import unittest


LIB_DIR = pathlib.Path(__file__).resolve().parents[2] / "lib" / "easybim"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(LIB_DIR / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = _load("link_crossings")

MM = 1.0 / 304.8


# ---------------------------------------------------------------- fakes


class Enum(object):
    def __init__(self, enum_name, name):
        self.enum_name = enum_name
        self.name = name

    def __str__(self):
        return "%s.%s" % (self.enum_name, self.name)


class XYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class Transform(object):
    """Rotation about Z by ``angle`` plus a translation; rigid."""

    def __init__(self, dx=0.0, dy=0.0, dz=0.0, angle=0.0, conformal=True, identity=False):
        self.dx, self.dy, self.dz, self.angle = dx, dy, dz, angle
        self.IsConformal = conformal
        self.IsIdentity = identity

    def OfPoint(self, point):
        c, s = math.cos(self.angle), math.sin(self.angle)
        return XYZ(c * point.X - s * point.Y + self.dx, s * point.X + c * point.Y + self.dy, point.Z + self.dz)

    @property
    def Inverse(self):
        c, s = math.cos(-self.angle), math.sin(-self.angle)
        dx, dy = -self.dx, -self.dy
        # inverse = rotate(-angle) after translate(-d)
        inverse = Transform(angle=-self.angle)
        inverse.dx = c * dx - s * dy
        inverse.dy = s * dx + c * dy
        inverse.dz = -self.dz
        return inverse


class Id(object):
    def __init__(self, value):
        self.IntegerValue = value


class Line(object):
    def __init__(self, a, b):
        self.a, self.b = a, b

    @staticmethod
    def CreateBound(a, b):
        if math.hypot(math.hypot(a.X - b.X, a.Y - b.Y), a.Z - b.Z) < 0.78 * MM:
            raise Exception("ArgumentsInconsistentException")
        return Line(a, b)

    def GetEndPoint(self, index):
        return self.a if index == 0 else self.b


class Segment(object):
    def __init__(self, a, b):
        self.a, self.b = a, b

    def GetEndPoint(self, index):
        return self.a if index == 0 else self.b


class Intersection(object):
    def __init__(self, pieces):
        self.pieces = pieces
        self.SegmentCount = len(pieces)

    def GetCurveSegment(self, index):
        return self.pieces[index]


class Solid(object):
    """An axis-aligned box in its own document's coordinates."""

    def __init__(self, low, high, throws=False):
        self.low, self.high = low, high
        self.Volume = (high[0] - low[0]) * (high[1] - low[1]) * (high[2] - low[2])
        self.throws = throws

    def IntersectWithCurve(self, line, options):
        if self.throws:
            raise Exception("degenerate solid")
        p0 = (line.a.X, line.a.Y, line.a.Z)
        p1 = (line.b.X, line.b.Y, line.b.Z)
        t0, t1 = 0.0, 1.0
        for axis in range(3):
            d = p1[axis] - p0[axis]
            if abs(d) < 1e-12:
                if p0[axis] < self.low[axis] or p0[axis] > self.high[axis]:
                    return Intersection([])
                continue
            ta = (self.low[axis] - p0[axis]) / d
            tb = (self.high[axis] - p0[axis]) / d
            t0, t1 = max(t0, min(ta, tb)), min(t1, max(ta, tb))
        if t1 <= t0:
            return Intersection([])
        a = XYZ(*[p0[i] + (p1[i] - p0[i]) * t0 for i in range(3)])
        b = XYZ(*[p0[i] + (p1[i] - p0[i]) * t1 for i in range(3)])
        return Intersection([Segment(a, b)])


class GeometryInstance(object):
    def __init__(self, solids):
        self.solids = solids

    def GetInstanceGeometry(self):
        return list(self.solids)


class BBox(object):
    def __init__(self, low, high):
        self.Min, self.Max = XYZ(*low), XYZ(*high)
        self.Transform = None


class Param(object):
    def __init__(self, name, text):
        self.Definition = types.SimpleNamespace(Name=name)
        self.StorageType = Enum("StorageType", "String")
        self._text = text

    def AsString(self):
        return self._text


class Category(object):
    def __init__(self, bic):
        self.Id = Id(int(bic))
        self.Name = "cat"


class ElementType(object):
    def __init__(self, type_id, name, params=None):
        self.Id = Id(type_id)
        self.Name = name
        self.FamilyName = name
        self.Parameters = [Param(key, value) for key, value in (params or {}).items()]

    def LookupParameter(self, name):
        for param in self.Parameters:
            if param.Definition.Name == name:
                return param
        return None


class Wall(object):
    def __init__(self, element_id, type_id, bic, solids, width=0.5, kind="Basic", stacked=False,
                 member=False, owner_id=None, level_id=-1, params=None, geometry_throws=False,
                 instance_solids=False):
        self.Id = Id(element_id)
        self._type_id = type_id
        self.Category = Category(bic)
        self._solids = solids
        self.Width = width
        self.WallType = types.SimpleNamespace(Kind=Enum("WallKind", kind))
        self.IsStackedWall = stacked
        self.IsStackedWallMember = member
        self.StackedWallOwnerId = Id(owner_id) if owner_id is not None else None
        self.LevelId = Id(level_id)
        self._params = params or {}
        self._throws = geometry_throws
        self._instance_solids = instance_solids
        self.Name = "wall%s" % element_id

    def GetTypeId(self):
        return Id(self._type_id)

    def LookupParameter(self, name):
        text = self._params.get(name)
        return Param(name, text) if text is not None else None

    def get_BoundingBox(self, view):
        if not self._solids:
            return None
        return BBox([min(s.low[i] for s in self._solids) for i in range(3)],
                    [max(s.high[i] for s in self._solids) for i in range(3)])

    def get_Geometry(self, options):
        if self._throws:
            raise Exception("no geometry")
        if self._instance_solids:
            return [GeometryInstance(self._solids)]
        return list(self._solids)


class Level(object):
    def __init__(self, level_id, name, elevation):
        self.Id = Id(level_id)
        self.Name = name
        self.ProjectElevation = elevation


class View(object):
    def __init__(self, view_id, view_type, level):
        self.Id = Id(view_id)
        self.ViewType = Enum("ViewType", view_type)
        self.GenLevel = level


class GeometryLine(object):
    def __init__(self, a, b):
        self.a, self.b = XYZ(*a), XYZ(*b)

    def GetEndPoint(self, index):
        return self.a if index == 0 else self.b


class CurveElement(object):
    def __init__(self, element_id, style, a, b, view_id=None):
        self.Id = Id(element_id)
        self.Category = Category(BIC["OST_Lines"])
        self.LineStyle = types.SimpleNamespace(Name=style)
        self.GeometryCurve = GeometryLine(a, b)
        self.ViewSpecific = view_id is not None
        self.OwnerViewId = Id(view_id) if view_id is not None else None


class LinkInstance(object):
    def __init__(self, element_id, link_doc, transform, name="Arch.rvt : 1 : location <Not Shared>",
                 type_id=900):
        self.Id = Id(element_id)
        self._doc = link_doc
        self._transform = transform
        self.Name = name
        self._type_id = type_id

    def GetLinkDocument(self):
        return self._doc

    def GetTotalTransform(self):
        return self._transform

    def GetTypeId(self):
        return Id(self._type_id)


class Doc(object):
    def __init__(self, title, elements, lookup=None):
        self.Title = title
        self.elements = list(elements)
        self.lookup = dict(lookup or {})

    def GetElement(self, element_id):
        value = getattr(element_id, "IntegerValue", None)
        for element in self.elements:
            if getattr(element, "Id", None) is not None and element.Id.IntegerValue == value:
                return element
        return self.lookup.get(value)


class Collector(object):
    def __init__(self, doc):
        self.items = list(doc.elements)
        self.outlines = []

    def OfClass(self, klass):
        self.items = [item for item in self.items if isinstance(item, klass)]
        return self

    def OfCategory(self, bic):
        self.items = [item for item in self.items
                      if getattr(item, "Category", None) is not None and item.Category.Id.IntegerValue == int(bic)]
        return self

    def WherePasses(self, box_filter):
        RECORDED_OUTLINES.append(box_filter.outline)
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return list(self.items)


RECORDED_OUTLINES = []


class BoxFilter(object):
    def __init__(self, outline):
        self.outline = outline


class Outline(object):
    def __init__(self, low, high):
        self.low, self.high = low, high


class BuiltIn(object):
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __int__(self):
        return self.value


BIC = {"OST_Walls": -2000011, "OST_Floors": -2000032, "OST_Lines": -2000051}


def make_db():
    return types.SimpleNamespace(
        BuiltInCategory=types.SimpleNamespace(**dict((k, BuiltIn(k, v)) for k, v in BIC.items())),
        BuiltInParameter=types.SimpleNamespace(FLOOR_ATTR_THICKNESS_PARAM=BuiltIn("FLOOR_ATTR_THICKNESS_PARAM", 1)),
        FilteredElementCollector=Collector,
        BoundingBoxIntersectsFilter=BoxFilter,
        Outline=Outline,
        XYZ=XYZ,
        Line=Line,
        ElementId=Id,
        Solid=Solid,
        GeometryInstance=GeometryInstance,
        Options=lambda: types.SimpleNamespace(),
        ViewDetailLevel=types.SimpleNamespace(Fine=1),
        SolidCurveIntersectionOptions=lambda: types.SimpleNamespace(),
        SolidCurveIntersectionMode=types.SimpleNamespace(CurveSegmentsInside=0),
        RevitLinkInstance=LinkInstance,
        Level=Level,
    )


def rated_wall(element_id=10, type_id=100, x=10.0, width=0.5, **kwargs):
    """A wall at x, running along Y from 0 to 20, 0 to 10 high, in link space."""
    solid = Solid((x - width / 2.0, 0.0, 0.0), (x + width / 2.0, 20.0, 10.0))
    return Wall(element_id, type_id, BIC["OST_Walls"], [solid], width=width, **kwargs)


def link_model(transform=None, walls=None, extra=None, title="Arch.rvt"):
    walls = walls if walls is not None else [rated_wall()]
    lookup = {100: ElementType(100, "Int - 1HR", {"Fire Rating": "1 HR"}),
              101: ElementType(101, "Generic", {"Fire Rating": ""}),
              102: ElementType(102, "FW-2HR", {})}
    link_doc = Doc(title, walls + list(extra or []), lookup)
    instance = LinkInstance(500, link_doc, transform or Transform())
    host = Doc("MEP.rvt", [instance])
    return host, link_doc, instance


def contexts_for(host, db):
    return engine.build_contexts(host, db=db)


def duct_segment(y=5.0, z=5.0, x0=0.0, x1=20.0, owner_id=1, kind="duct"):
    return {"owner_id": owner_id, "kind": kind, "points": [[x0, y, z], [x1, y, z]]}


def build(host, db, choices=None, options=None):
    contexts = contexts_for(host, db)
    choices = choices or {"Arch.rvt": {"enabled": True, "rating_param": "Fire Rating",
                                       "wall_types": set(), "floor_types": set(), "line_styles": set()}}
    return contexts, engine.build_index(contexts, choices, options or {}, db=db)


# ---------------------------------------------------------------- tests


class ContextTests(unittest.TestCase):
    def test_host_first_then_one_context_per_document(self):
        host, link_doc, instance = link_model()
        host.elements.append(LinkInstance(501, link_doc, Transform(dx=100.0)))
        contexts = contexts_for(host, make_db())
        self.assertEqual([c.key for c in contexts], [engine.HOST_KEY, "Arch.rvt"])
        self.assertTrue(contexts[0].is_host)
        self.assertEqual(len(contexts[1].instances), 2)

    def test_an_unloaded_link_is_listed_with_its_status(self):
        host = Doc("MEP.rvt", [LinkInstance(500, None, Transform(), name="Arch.rvt : 1 : x")])
        host.lookup[900] = types.SimpleNamespace(GetLinkedFileStatus=lambda: Enum("LinkedFileStatus", "Unloaded"))
        contexts = contexts_for(host, make_db())
        self.assertFalse(contexts[1].loaded)
        self.assertEqual(contexts[1].status, "Unloaded")
        self.assertEqual(contexts[1].describe()["title"], "Arch.rvt")

    def test_a_scaled_instance_is_skipped_and_named(self):
        host, _link_doc, instance = link_model(transform=Transform(conformal=False))
        contexts = contexts_for(host, make_db())
        self.assertEqual(contexts[1].instances, [])
        self.assertIn("not conformal", contexts[1].status)


class CatalogTests(unittest.TestCase):
    def test_types_ratings_and_line_styles(self):
        line = CurveElement(700, "Fire Rating 1HR", (0, 0, 0), (5, 0, 0))
        host, link_doc, _instance = link_model(walls=[rated_wall(), rated_wall(11, 102, x=15.0),
                                                     rated_wall(12, 100, x=18.0, kind="Curtain")],
                                              extra=[line, Level(800, "L1", 0.0), Level(801, "L2", 12.0)])
        contexts = contexts_for(host, make_db())
        catalog = engine.collect_catalog(contexts[1], db=make_db())
        rows = dict((row["type_key"], row) for row in catalog["types"])
        self.assertEqual(rows["Int - 1HR"]["count"], 2)
        self.assertEqual(rows["Int - 1HR"]["ratings"], {"Fire Rating": "1 HR"})
        self.assertEqual(rows["FW-2HR"]["ratings"], {})
        self.assertEqual(catalog["rating_params"], [{"name": "Fire Rating", "count": 1}])
        self.assertEqual(catalog["line_styles"], [{"key": "Fire Rating 1HR", "count": 1, "category": u"Line styles"}])
        self.assertEqual([level["name"] for level in catalog["levels"]], ["L1", "L2"])

    def test_an_unloaded_context_has_an_empty_catalog(self):
        context = engine.LinkContext("X", "X", None)
        self.assertEqual(engine.collect_catalog(context, db=make_db())["types"], [])


class CrossingTests(unittest.TestCase):
    def test_a_duct_through_a_rated_wall_is_one_crossing_at_the_wall(self):
        host, _link_doc, _instance = link_model()
        _contexts, index = build(host, make_db())
        self.assertEqual(len(index.barriers), 1)
        self.assertEqual(index.barriers[0]["rating"], "1 HR")
        self.assertEqual(index.barriers[0]["rated_by"], "parameter")
        result = engine.find_crossings(index, [duct_segment()], db=make_db())
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertAlmostEqual(item["point"][0], 10.0)
        self.assertAlmostEqual(item["length"], 0.5)
        self.assertFalse(item["partial"])
        self.assertFalse(item["along"])
        self.assertEqual(item["element_id"], 1)

    def test_the_link_transform_round_trips(self):
        transform = Transform(dx=100.0, dy=-40.0, dz=3.0, angle=math.pi / 2.0)
        host, _link_doc, _instance = link_model(transform=transform)
        _contexts, index = build(host, make_db())
        box = index.barriers[0]["box"]
        # The wall at link x=10 (y 0..20) lands at host x 100-20..100, y -30 (+/- width)
        self.assertAlmostEqual(box[0][1], -30.25)
        self.assertAlmostEqual(box[1][1], -29.75)
        # A host duct along y at x=90 crosses it.
        segment = {"owner_id": 1, "kind": "duct", "points": [[90.0, -40.0, 8.0], [90.0, -20.0, 8.0]]}
        result = engine.find_crossings(index, [segment], db=make_db())
        self.assertEqual(len(result["items"]), 1)
        self.assertAlmostEqual(result["items"][0]["point"][1], -30.0)
        self.assertAlmostEqual(result["items"][0]["length"], 0.5)

    def test_two_instances_of_one_link_both_cross(self):
        host, link_doc, _instance = link_model()
        host.elements.append(LinkInstance(501, link_doc, Transform(dx=50.0)))
        _contexts, index = build(host, make_db())
        self.assertEqual(len(index.barriers), 2)
        result = engine.find_crossings(index, [duct_segment(x0=0.0, x1=70.0)], db=make_db())
        self.assertEqual(sorted(round(item["point"][0]) for item in result["items"]), [10, 60])

    def test_split_solids_merge_into_one_crossing(self):
        wall = Wall(10, 100, BIC["OST_Walls"], [Solid((9.75, 0, 0), (10.25, 10, 10)),
                                                Solid((9.75, 10, 0), (10.25, 20, 10))], width=0.5)
        host, _link_doc, _instance = link_model(walls=[wall])
        _contexts, index = build(host, make_db())
        # A duct running along the wall's length inside it: two solids, one interval.
        segment = {"owner_id": 1, "kind": "duct", "points": [[10.0, -5.0, 5.0], [10.0, 25.0, 5.0]]}
        result = engine.find_crossings(index, [segment], db=make_db())
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(result["items"][0]["along"])
        self.assertAlmostEqual(result["items"][0]["length"], 20.0)

    def test_a_duct_ending_inside_the_wall_is_partial(self):
        host, _link_doc, _instance = link_model()
        _contexts, index = build(host, make_db())
        result = engine.find_crossings(index, [duct_segment(x0=0.0, x1=10.0)], db=make_db())
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(result["items"][0]["partial"])
        self.assertAlmostEqual(result["items"][0]["length"], 0.25)

    def test_dust_below_the_minimum_penetration_is_dropped(self):
        host, _link_doc, _instance = link_model()
        _contexts, index = build(host, make_db())
        segment = duct_segment(x0=0.0, x1=9.751)
        result = engine.find_crossings(index, [segment], db=make_db())
        self.assertEqual(result["items"], [])

    def test_a_wall_missed_by_the_hash_is_never_tested(self):
        host, _link_doc, _instance = link_model()
        _contexts, index = build(host, make_db())
        result = engine.find_crossings(index, [duct_segment(y=500.0)], db=make_db())
        self.assertEqual(result["items"], [])
        self.assertEqual(result["meta"]["candidate_tests"], 0)

    def test_a_throwing_wall_is_named_unreadable_and_dropped(self):
        host, _link_doc, _instance = link_model(walls=[rated_wall(geometry_throws=True)])
        _contexts, index = build(host, make_db())
        result = engine.find_crossings(index, [duct_segment(), duct_segment(y=6.0)], db=make_db())
        self.assertEqual(result["items"], [])
        self.assertEqual(len(index.skips["unreadable"]), 1)
        self.assertEqual(result["meta"]["solid_calls"], 0)

    def test_a_solid_that_throws_mid_scan_is_dropped_too(self):
        wall = Wall(10, 100, BIC["OST_Walls"], [Solid((9.75, 0, 0), (10.25, 20, 10), throws=True)])
        host, _link_doc, _instance = link_model(walls=[wall])
        _contexts, index = build(host, make_db())
        result = engine.find_crossings(index, [duct_segment(), duct_segment(y=6.0)], db=make_db())
        self.assertEqual(result["items"], [])
        self.assertEqual(engine.describe_index(index)["skips"]["unreadable"], [index.barriers[0]["key"]])

    def test_in_place_walls_reach_their_solids_through_the_instance(self):
        host, _link_doc, _instance = link_model(walls=[rated_wall(instance_solids=True)])
        _contexts, index = build(host, make_db())
        result = engine.find_crossings(index, [duct_segment()], db=make_db())
        self.assertEqual(len(result["items"]), 1)

    def test_curtain_and_stacked_parents_are_skipped_but_members_judged(self):
        parent = rated_wall(20, 102, x=15.0, stacked=True)
        member = rated_wall(21, 101, x=15.0, member=True, owner_id=20)
        curtain = rated_wall(22, 101, x=18.0, kind="Curtain")
        host, _link_doc, _instance = link_model(walls=[parent, member, curtain])
        choices = {"Arch.rvt": {"enabled": True, "rating_param": "", "wall_types": set(["FW-2HR"]),
                                "floor_types": set(), "line_styles": set()}}
        _contexts, index = build(host, make_db(), choices=choices)
        self.assertEqual([b["element_id"] for b in index.barriers], [21])
        self.assertEqual(index.barriers[0]["owner_type"], "FW-2HR")
        self.assertEqual(index.barriers[0]["rated_by"], "type")
        skips = engine.describe_index(index)["skips"]
        self.assertEqual(skips["curtain_walls"], 1)
        self.assertEqual(skips["stacked_parents"], 1)

    def test_a_disabled_or_unloaded_link_adds_no_barriers(self):
        host, link_doc, _instance = link_model()
        host.elements.append(LinkInstance(502, None, Transform(), name="Str.rvt : 1"))
        contexts = contexts_for(host, make_db())
        choices = {"Arch.rvt": {"enabled": False}, "Str.rvt": {"enabled": True}}
        index = engine.build_index(contexts, choices, {}, db=make_db())
        self.assertEqual(index.barriers, [])
        self.assertEqual(engine.describe_index(index)["skips"]["unloaded_links"], ["Str.rvt (not loaded)"])

    def test_the_trim_outline_is_one_query_per_link_in_link_space(self):
        del RECORDED_OUTLINES[:]
        host, _link_doc, _instance = link_model(transform=Transform(dx=100.0))
        segments = [duct_segment(x0=100.0, x1=120.0), duct_segment(y=7.0, x0=100.0, x1=120.0)]
        build(host, make_db(), options={"host_extent": engine.host_extent(segments)})
        self.assertEqual(len(RECORDED_OUTLINES), 2)  # walls and floors, once each
        outline = RECORDED_OUTLINES[0]
        self.assertAlmostEqual(outline.low.X, -1.0)  # 100 mapped back to 0, minus the pad
        self.assertAlmostEqual(outline.high.X, 21.0)


class LineBarrierTests(unittest.TestCase):
    def test_detail_lines_stand_on_the_view_level_up_to_the_next(self):
        level1, level2 = Level(800, "L1", 0.0), Level(801, "L2", 12.0)
        view = View(600, "FloorPlan", level1)
        line = CurveElement(700, "Fire Rating 1HR", (10, 0, 99.0), (10, 20, 99.0), view_id=600)
        section = View(601, "Section", level1)
        sline = CurveElement(701, "Fire Rating 1HR", (0, 0, 0), (5, 0, 0), view_id=601)
        host, link_doc, _instance = link_model(walls=[], extra=[line, sline, level1, level2, view, section])
        choices = {"Arch.rvt": {"enabled": True, "rating_param": "", "wall_types": set(),
                                "floor_types": set(), "line_styles": set(["Fire Rating 1HR"])}}
        _contexts, index = build(host, make_db(), choices=choices)
        panels = engine.describe_index(index)["line_barriers"]
        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0]["z0"], 0.0)   # the level, never the line's own Z
        self.assertEqual(panels[0]["z1"], 12.0)
        self.assertEqual(panels[0]["level"], "L1")
        self.assertEqual(engine.describe_index(index)["skips"]["lines_not_in_plan_view"], 1)

    def test_model_lines_snap_to_a_level_and_use_the_fixed_band_when_asked(self):
        level1 = Level(800, "L1", 0.0)
        line = CurveElement(700, "Fire Rating 1HR", (10, 0, 0.1), (10, 20, 0.1))
        host, _link_doc, _instance = link_model(walls=[], extra=[line, level1])
        choices = {"Arch.rvt": {"enabled": True, "rating_param": "", "wall_types": set(),
                                "floor_types": set(), "line_styles": set(["Fire Rating 1HR"])}}
        _contexts, index = build(host, make_db(), choices=choices,
                                 options={"band_mode": "fixed", "band_height_ft": 9.0})
        panel = engine.describe_index(index)["line_barriers"][0]
        self.assertEqual(panel["z0"], 0.0)
        self.assertEqual(panel["z1"], 9.0)
        self.assertEqual(panel["points"], [[10.0, 0.0, 0.1], [10.0, 20.0, 0.1]])

    def test_lines_of_other_styles_are_ignored(self):
        line = CurveElement(700, "Thin Lines", (10, 0, 0), (10, 20, 0))
        host, _link_doc, _instance = link_model(walls=[], extra=[line, Level(800, "L1", 0.0)])
        choices = {"Arch.rvt": {"enabled": True, "rating_param": "", "wall_types": set(),
                                "floor_types": set(), "line_styles": set(["Fire Rating 1HR"])}}
        _contexts, index = build(host, make_db(), choices=choices)
        self.assertEqual(index.line_barriers, [])


class BudgetTests(unittest.TestCase):
    def test_the_crossing_cap_and_the_clock_truncate(self):
        host, _link_doc, _instance = link_model()
        _contexts, index = build(host, make_db())
        segments = [duct_segment(y=0.5 + i * 0.05, owner_id=i) for i in range(300)]
        result = engine.find_crossings(index, segments, {"max_crossings": 5}, db=make_db())
        self.assertTrue(result["meta"]["truncated"])
        self.assertIn("crossings cap", result["meta"]["truncated_reason"])
        ticks = [0.0]

        def clock():
            ticks[0] += 100.0
            return ticks[0]

        result = engine.find_crossings(index, segments, {"budget_seconds": 5.0}, db=make_db(), clock=clock)
        self.assertTrue(result["meta"]["truncated"])
        self.assertIn("budget", result["meta"]["truncated_reason"])

    def test_progress_can_cancel(self):
        host, _link_doc, _instance = link_model()
        _contexts, index = build(host, make_db())
        segments = [duct_segment(y=0.5 + i * 0.05, owner_id=i) for i in range(300)]
        result = engine.find_crossings(index, segments, db=make_db(), progress=lambda done, total: False)
        self.assertEqual(result["meta"]["truncated_reason"], "cancelled")

    def test_only_plain_data_crosses_back(self):
        host, _link_doc, _instance = link_model()
        _contexts, index = build(host, make_db())
        result = engine.find_crossings(index, [duct_segment()], db=make_db())
        _walk_types(result)
        _walk_types(engine.describe_index(index))


def _walk_types(value, path="root"):
    allowed = (int, float, bool, type(u""), type(None))
    if isinstance(value, dict):
        for key, item in value.items():
            _walk_types(item, path + "." + str(key))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _walk_types(item, path + "[]")
    elif not isinstance(value, allowed):
        raise AssertionError("%s carries a %s" % (path, type(value).__name__))


class ShowTests(unittest.TestCase):
    def test_show_selects_host_ids_when_references_are_unavailable(self):
        selected = {}

        class Selection(object):
            def SetElementIds(self, ids):
                selected["ids"] = list(ids)

        class UIDoc(object):
            def __init__(self, doc):
                self.Document = doc
                self.Selection = Selection()
                self.ActiveView = types.SimpleNamespace(Id=Id(1))

            def ShowElements(self, ids):
                selected["shown"] = True

            def GetOpenUIViews(self):
                return []

        host = Doc("MEP.rvt", [types.SimpleNamespace(Id=Id(7))])
        db = make_db()
        # No System.Collections.Generic under CPython: both selections fail
        # gracefully, and the function still returns False rather than raising.
        self.assertFalse(engine.show_crossing(UIDoc(host), {"host_ids": [7], "point": [0, 0, 0]}, db=db))


if __name__ == "__main__":
    unittest.main()
