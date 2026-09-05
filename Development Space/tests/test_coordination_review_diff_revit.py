import importlib.util
import pathlib
import sys
import unittest


LIB_ROOT = pathlib.Path(__file__).resolve().parents[2] / "lib" / "easybim"


def _load_module():
    # The adapter falls back to a sibling import when the ``easybim`` package
    # is absent; make sure a stale package stub from another test does not
    # shadow that path.
    for name in ("easybim.coordination_review_diff", "coordination_review_diff", "coordination_review_diff_revit"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        "coordination_review_diff_revit", str(LIB_ROOT / "coordination_review_diff_revit.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeElementId(object):
    def __init__(self, value):
        self.IntegerValue = int(value)
        self.Value = int(value)

    def __int__(self):
        return int(self.IntegerValue)


class FakeXYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class FakeTransform(object):
    """Pure translation."""

    def __init__(self, dx=0.0, dy=0.0, dz=0.0):
        self._d = (dx, dy, dz)

    def OfPoint(self, xyz):
        return FakeXYZ(xyz.X + self._d[0], xyz.Y + self._d[1], xyz.Z + self._d[2])


class FakeLine(object):
    def __init__(self, start, end):
        self._points = (FakeXYZ(*start), FakeXYZ(*end))

    def GetEndPoint(self, index):
        return self._points[index]


class FakeArc(object):
    def __init__(self, center, radius):
        self.Center = FakeXYZ(*center)
        self.Radius = float(radius)

    def GetEndPoint(self, index):
        raise AssertionError("arcs are read through Center/Radius")


class FakeLocationCurve(object):
    def __init__(self, curve):
        self.Curve = curve


class FakeLocationPoint(object):
    def __init__(self, point):
        self.Point = FakeXYZ(*point)


class FakeBBox(object):
    def __init__(self, low, high):
        self.Min = FakeXYZ(*low)
        self.Max = FakeXYZ(*high)


class FakeCategory(object):
    def __init__(self, builtin_value):
        self.Id = FakeElementId(builtin_value)


class BIC(object):
    OST_Levels = -2000240
    OST_Grids = -2000220
    OST_Columns = -2000100
    OST_StructuralColumns = -2001330
    OST_Walls = -2000011
    OST_Floors = -2000032
    OST_ShaftOpening = -2000996
    OST_MechanicalEquipment = -2001140
    # Deliberately missing: OST_FloorOpening, OST_SWallRectOpening, OST_RoofOpening,
    # OST_PlumbingFixtures, ... - the adapter must skip them.


class FakeElementType(object):
    def __init__(self, element_id, name, family_name=None):
        self.Id = FakeElementId(element_id)
        self.Name = name
        if family_name is not None:
            self.FamilyName = family_name


class FakeElement(object):
    def __init__(self, element_id, builtin, name="", type_id=None, monitored_links=None):
        self.Id = FakeElementId(element_id)
        self.Category = FakeCategory(builtin)
        self.Name = name
        self._type_id = FakeElementId(type_id) if type_id is not None else None
        self._monitored = list(monitored_links or [])

    def GetTypeId(self):
        return self._type_id if self._type_id is not None else FakeElementId(-1)

    def IsMonitoringLinkElement(self):
        return bool(self._monitored)

    def GetMonitoredLinkElementIds(self):
        return [FakeElementId(value) for value in self._monitored]


class Level(FakeElement):
    def __init__(self, element_id, name, elevation, monitored_links=None):
        FakeElement.__init__(self, element_id, BIC.OST_Levels, name, None, monitored_links)
        self.Elevation = float(elevation)


class Grid(FakeElement):
    def __init__(self, element_id, name, curve, monitored_links=None):
        FakeElement.__init__(self, element_id, BIC.OST_Grids, name, None, monitored_links)
        self.Curve = curve


class Wall(FakeElement):
    def __init__(self, element_id, type_id, start, end, monitored_links=None):
        FakeElement.__init__(self, element_id, BIC.OST_Walls, "", type_id, monitored_links)
        self.Location = FakeLocationCurve(FakeLine(start, end))


class Column(FakeElement):
    def __init__(self, element_id, type_id, point, monitored_links=None):
        FakeElement.__init__(self, element_id, BIC.OST_StructuralColumns, "", type_id, monitored_links)
        self.Location = FakeLocationPoint(point)


class Floor(FakeElement):
    def __init__(self, element_id, type_id, low, high, monitored_links=None):
        FakeElement.__init__(self, element_id, BIC.OST_Floors, "", type_id, monitored_links)
        self.Location = None
        self._bbox = FakeBBox(low, high)

    def get_BoundingBox(self, view):
        return self._bbox


class FakeCollector(object):
    def __init__(self, doc):
        self._doc = doc
        self._builtin = None

    def WherePasses(self, element_filter):
        raise RuntimeError("multi-category filter unavailable in the fake")

    def OfCategory(self, builtin):
        self._builtin = int(builtin)
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return [
            element
            for element in self._doc.elements
            if isinstance(element, FakeElement) and int(element.Category.Id) == self._builtin
        ]


class FakeUnitFormatUtils(object):
    @staticmethod
    def Format(units, spec_id, value, *rest):
        return "{0:.0f} mm".format(float(value) * 304.8)


class FakeSpecTypeId(object):
    Length = "spec-length"


class FakeDB(object):
    BuiltInCategory = BIC
    FilteredElementCollector = FakeCollector
    XYZ = FakeXYZ
    Level = Level
    Grid = Grid


class FakeDBWithUnits(FakeDB):
    UnitFormatUtils = FakeUnitFormatUtils
    SpecTypeId = FakeSpecTypeId


class FakeDocument(object):
    def __init__(self, elements, title="Host.rvt", units="units"):
        self.elements = list(elements)
        self.Title = title
        self._units = units
        self._by_id = dict((int(e.Id), e) for e in elements)

    def GetElement(self, element_id):
        return self._by_id.get(int(element_id))

    def GetUnits(self):
        if self._units is None:
            raise RuntimeError("no units")
        return self._units


class FakeLinkInstance(object):
    def __init__(self, element_id, name, link_doc, transform=None):
        self.Id = FakeElementId(element_id)
        self.Name = name
        self._link_doc = link_doc
        self._transform = transform or FakeTransform()

    def GetLinkDocument(self):
        return self._link_doc

    def GetTotalTransform(self):
        return self._transform


LINK_ID = 900
OTHER_LINK_ID = 901


def _host_doc():
    wall_type = FakeElementType(500, "Generic 200mm", "Basic Wall")
    column_type = FakeElementType(501, "UC 305")
    floor_type = FakeElementType(502, "Slab 200", "Floor")
    return FakeDocument(
        [
            wall_type,
            column_type,
            floor_type,
            Level(1, "Level 1", 0.0, [LINK_ID]),
            Level(2, "Level 2", 12.0, [LINK_ID]),
            Level(3, "Level X", 30.0),  # not monitoring anything
            Level(4, "Level Y", 40.0, [OTHER_LINK_ID]),  # monitors another link
            Grid(5, "A", FakeLine((0, 0, 0), (0, 100, 0)), [LINK_ID]),
            Grid(6, "R1", FakeArc((0, 0, 0), 50.0), [LINK_ID]),
            Wall(7, 500, (0, 0, 0), (10, 0, 0), [LINK_ID]),
            Column(8, 501, (5, 5, 0), [LINK_ID]),
            Floor(9, 502, (0, 0, 0), (20, 20, 1), [LINK_ID]),
        ]
    )


def _link_doc():
    wall_type = FakeElementType(600, "Generic 200mm", "Basic Wall")
    column_type = FakeElementType(601, "UC 356")
    floor_type = FakeElementType(602, "Slab 200", "Floor")
    return FakeDocument(
        [
            wall_type,
            column_type,
            floor_type,
            Level(101, "Level 1", 0.0),
            Level(102, "Level 2", 12.5),  # moved
            Level(103, "Level 3", 24.0),  # not monitored
            Grid(105, "A", FakeLine((0, -50, 0), (0, 150, 0))),
            Grid(106, "R1", FakeArc((0, 0, 0), 50.0)),
            Wall(107, 600, (0, 0, 0), (10, 0, 0)),
            Column(108, 601, (5, 5, 0)),  # type changed
            Floor(109, 602, (0, 0, 0), (20, 20, 1)),
        ],
        title="ARCH.rvt",
    )


class AvailableBuiltinsTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_missing_enum_members_are_skipped(self):
        rows = self.module.available_builtins(FakeDB)
        labels = dict()
        for label, _member, value in rows:
            labels.setdefault(label, []).append(value)
        self.assertEqual(labels["Opening"], [BIC.OST_ShaftOpening])
        self.assertEqual(labels["MEP"], [BIC.OST_MechanicalEquipment])
        self.assertEqual(sorted(labels["Column"]), sorted([BIC.OST_Columns, BIC.OST_StructuralColumns]))

    def test_labels_filter(self):
        rows = self.module.available_builtins(FakeDB, labels=set(["Level"]))
        self.assertEqual([label for label, _m, _v in rows], ["Level"])

    def test_no_db_yields_nothing(self):
        self.assertEqual(self.module.available_builtins(None), [])


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc = _host_doc()

    def test_level_elevation_uses_transform(self):
        item = self.module.snapshot(self.doc, self.doc.GetElement(FakeElementId(2)), "Level", FakeTransform(0, 0, 3.0), FakeDB)
        self.assertEqual(item["category"], "Level")
        self.assertEqual(item["name"], "Level 2")
        self.assertAlmostEqual(item["elevation"], 15.0)

    def test_project_elevation_is_preferred_over_elevation(self):
        level = Level(2, "Level 2", 12.0)
        level.ProjectElevation = 11.0
        item = self.module.snapshot(self.doc, level, "Level", None, FakeDB)
        self.assertAlmostEqual(item["elevation"], 11.0)

    def test_grid_line_endpoints_are_transformed(self):
        item = self.module.snapshot(self.doc, self.doc.GetElement(FakeElementId(5)), "Grid", FakeTransform(1, 2, 0), FakeDB)
        self.assertEqual(item["curve"], ((1.0, 2.0, 0.0), (1.0, 102.0, 0.0)))
        self.assertIsNone(item["arc"])

    def test_grid_arc_is_read_as_centre_and_radius(self):
        item = self.module.snapshot(self.doc, self.doc.GetElement(FakeElementId(6)), "Grid", None, FakeDB)
        self.assertIsNone(item["curve"])
        self.assertEqual(item["arc"], ((0.0, 0.0, 0.0), 50.0))

    def test_wall_location_curve_and_family_type_name(self):
        item = self.module.snapshot(self.doc, self.doc.GetElement(FakeElementId(7)), "Wall", None, FakeDB)
        self.assertEqual(item["curve"], ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))
        self.assertEqual(item["type_name"], "Basic Wall: Generic 200mm")

    def test_column_location_point_and_plain_type_name(self):
        item = self.module.snapshot(self.doc, self.doc.GetElement(FakeElementId(8)), "Column", FakeTransform(0, 0, 1), FakeDB)
        self.assertEqual(item["point"], (5.0, 5.0, 1.0))
        self.assertEqual(item["type_name"], "UC 305")

    def test_floor_falls_back_to_bounding_box_centre(self):
        item = self.module.snapshot(self.doc, self.doc.GetElement(FakeElementId(9)), "Floor", None, FakeDB)
        self.assertEqual(item["point"], (10.0, 10.0, 0.5))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc = _host_doc()

    def test_host_items_are_limited_to_elements_monitoring_the_link(self):
        items = self.module.collect_host_monitoring_items(self.doc, LINK_ID, db=FakeDB)
        self.assertEqual(sorted(item["id"] for item in items), [1, 2, 5, 6, 7, 8, 9])
        categories = dict((item["id"], item["category"]) for item in items)
        self.assertEqual(categories[1], "Level")
        self.assertEqual(categories[5], "Grid")
        self.assertEqual(categories[7], "Wall")
        self.assertEqual(categories[8], "Column")
        self.assertEqual(categories[9], "Floor")

    def test_no_link_id_yields_nothing(self):
        self.assertEqual(self.module.collect_host_monitoring_items(self.doc, None, db=FakeDB), [])

    def test_link_items_are_limited_to_requested_categories(self):
        items = self.module.collect_link_items(_link_doc(), FakeTransform(), set(["Level", "Wall"]), db=FakeDB)
        self.assertEqual(sorted(item["id"] for item in items), [101, 102, 103, 107])

    def test_link_items_empty_without_categories(self):
        self.assertEqual(self.module.collect_link_items(_link_doc(), FakeTransform(), set(), db=FakeDB), [])


class LengthFormatterTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_project_units_are_used_when_available(self):
        fmt = self.module.make_length_formatter(FakeDocument([]), db=FakeDBWithUnits)
        self.assertEqual(fmt(1.0), "305 mm")

    def test_fallback_is_internal_feet(self):
        fmt = self.module.make_length_formatter(FakeDocument([], units=None), db=FakeDB)
        self.assertEqual(fmt(1.0), "1.0000 ft")


class BuildReportTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc = _host_doc()

    def test_full_report_for_a_loaded_link(self):
        link = FakeLinkInstance(LINK_ID, "ARCH.rvt : 1", _link_doc())
        report = self.module.build_link_issue_report(self.doc, link, db=FakeDB)

        self.assertEqual(report["error"], "")
        self.assertTrue(report["link_loaded"])
        self.assertEqual(report["link_name"], "ARCH.rvt : 1")
        self.assertEqual(report["link_id"], LINK_ID)
        self.assertEqual(report["doc_title"], "Host.rvt")
        self.assertEqual(report["monitored_count"], 7)
        kinds = [issue["kind"] for issue in report["issues"]]
        self.assertEqual(kinds, ["level_moved", "type_changed", "new_in_link"])
        self.assertEqual(report["issue_count"], 3)
        self.assertEqual(report["ok_count"], 5)
        self.assertEqual(report["estimated_count"], 1)
        self.assertEqual([group["kind"] for group in report["groups"]], kinds)
        moved = report["issues"][0]
        self.assertEqual((moved["host_id"], moved["link_id"]), (2, 102))
        self.assertIn("Level 'Level 2' moved by +0.5000 ft", moved["message"])
        self.assertEqual(report["level_offset_text"], "")

    def test_link_transform_is_applied_before_comparing(self):
        link = FakeLinkInstance(LINK_ID, "ARCH.rvt : 1", _link_doc(), FakeTransform(0, 0, 2.0))
        report = self.module.build_link_issue_report(self.doc, link, db=FakeDB)
        level_issues = [i for i in report["issues"] if i["kind"] == "level_moved"]
        self.assertEqual(sorted(i["host_id"] for i in level_issues), [1, 2])
        deltas = dict((i["host_id"], round(i["delta_ft"], 4)) for i in level_issues)
        self.assertEqual(deltas, {1: 2.0, 2: 2.5})
        self.assertEqual(report["level_offset_text"], "")

    def test_common_offset_is_explained(self):
        link_doc = FakeDocument(
            [Level(101, "Level 1", 0.0), Level(102, "Level 2", 12.0)], title="ARCH.rvt"
        )
        link = FakeLinkInstance(LINK_ID, "ARCH.rvt : 1", link_doc, FakeTransform(0, 0, 0.5))
        report = self.module.build_link_issue_report(self.doc, link, db=FakeDB)
        level_issues = [i for i in report["issues"] if i["kind"] == "level_moved"]
        self.assertEqual(len(level_issues), 2)
        self.assertIn("Every matched level differs by the same +0.5000 ft", report["level_offset_text"])

    def test_unloaded_link_reports_error(self):
        link = FakeLinkInstance(LINK_ID, "ARCH.rvt : 1", None)
        report = self.module.build_link_issue_report(self.doc, link, db=FakeDB)
        self.assertFalse(report["link_loaded"])
        self.assertIn("is not loaded", report["error"])
        self.assertEqual(report["issues"], [])

    def test_missing_inputs_report_error(self):
        report = self.module.build_link_issue_report(None, None, db=FakeDB)
        self.assertIn("No link instance", report["error"])

    def test_nothing_monitoring_gives_empty_report(self):
        link = FakeLinkInstance(OTHER_LINK_ID + 5, "MEP.rvt : 1", _link_doc())
        report = self.module.build_link_issue_report(self.doc, link, db=FakeDB)
        self.assertEqual(report["error"], "")
        self.assertEqual(report["monitored_count"], 0)
        self.assertEqual(report["issues"], [])

    def test_custom_length_formatter_reaches_messages(self):
        link = FakeLinkInstance(LINK_ID, "ARCH.rvt : 1", _link_doc())
        report = self.module.build_link_issue_report(
            self.doc, link, db=FakeDB, format_length=lambda v: "{0:.0f} mm".format(v * 304.8)
        )
        self.assertIn("+152 mm", report["issues"][0]["message"])


if __name__ == "__main__":
    unittest.main()
