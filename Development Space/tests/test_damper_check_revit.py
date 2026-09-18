"""The duct-network scanner (shared by Damper Check and Fire Damper Check),
driven over fakes shaped like the API.

The fakes carry both ``ElementId`` generations (``IntegerValue`` up to 2025,
``Value`` from 2024) and the ``AllRefs`` noise a real connector hands back:
the system's logical connector, the insulation's mirrored one, the owner's
own reference.  Only plain data may cross back out of ``scan``.
"""

import importlib.util
import pathlib
import types
import unittest


LIB_DIR = pathlib.Path(__file__).resolve().parents[2] / "lib" / "easybim"


def _load(name):
    # Loaded by path, standalone: the module's guarded imports fall back to
    # local shims when the ``easybim`` package is not importable.
    spec = importlib.util.spec_from_file_location(name, str(LIB_DIR / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


revit = _load("duct_network_revit")


# ---------------------------------------------------------------- fakes


class Enum(object):
    def __init__(self, enum_name, name):
        self.enum_name = enum_name
        self.name = name

    def __str__(self):
        return "%s.%s" % (self.enum_name, self.name)  # the IronPython-ish repr

    def ToString(self):
        return self.name


def enum(enum_name, *names):
    return type(enum_name, (object,), dict((n, Enum(enum_name, n)) for n in names))


class IntId(object):
    """Revit 2015-2025 style."""

    def __init__(self, value):
        self.IntegerValue = value


class ValueId(object):
    """Revit 2026 style."""

    def __init__(self, value):
        self.Value = value


class Category(object):
    def __init__(self, bic, name):
        self.Id = IntId(int(bic))
        self.Name = name


BIC = {
    "OST_DuctAccessory": -2008016,
    "OST_DuctFitting": -2008010,
    "OST_DuctTerminal": -2008013,
    "OST_MechanicalEquipment": -2001140,
    "OST_DuctCurves": -2008000,
    "OST_FlexDuctCurves": -2008020,
    "OST_DuctInsulations": -2008123,
    "OST_DuctLinings": -2008124,
    "OST_GenericModel": -2000151,
}
CATEGORY_NAMES = {
    "OST_DuctAccessory": "Duct Accessories", "OST_DuctFitting": "Duct Fittings",
    "OST_DuctTerminal": "Air Terminals", "OST_MechanicalEquipment": "Mechanical Equipment",
    "OST_DuctCurves": "Ducts", "OST_FlexDuctCurves": "Flex Ducts",
    "OST_DuctInsulations": "Duct Insulations", "OST_DuctLinings": "Duct Linings",
    "OST_GenericModel": "Generic Models",
}

ConnectorType = enum("ConnectorType", "End", "Curve", "Logical", "Physical")
Domain = enum("Domain", "DomainHvac", "DomainPiping", "DomainElectrical")
Shape = enum("ConnectorProfileType", "Round", "Rectangular", "Oval")
DuctSystemType = enum("DuctSystemType", "SupplyAir", "ReturnAir", "ExhaustAir", "Fitting")
ViewType = enum("ViewType", "FloorPlan", "Schedule", "ThreeD")
Classification = enum("MEPSystemClassification", "SupplyAir", "ReturnAir", "ExhaustAir")


class Param(object):
    def __init__(self, string=None, double=None, element_id=None):
        self._string = string
        self._double = double
        self._element_id = element_id

    def AsString(self):
        return self._string

    def AsDouble(self):
        if self._double is None:
            raise ValueError("no double")
        return self._double

    def AsElementId(self):
        return self._element_id


class Connector(object):
    def __init__(self, owner, cid, ctype="End", domain="DomainHvac", shape="Round",
                 radius=0.25, width=0.0, height=0.0, system="SupplyAir"):
        self.Owner = owner
        self.Id = cid
        self.ConnectorType = getattr(ConnectorType, ctype)
        self.Domain = getattr(Domain, domain)
        self.Shape = getattr(Shape, shape)
        self.Radius = radius
        self.Width = width
        self.Height = height
        self._system = system
        self.refs = [self]  # AllRefs includes the owner's own connector
        self.IsConnected = False

    @property
    def AllRefs(self):
        return list(self.refs)

    @property
    def DuctSystemType(self):
        if self.Domain is not Domain.DomainHvac:
            raise Exception("InvalidOperationException")
        return getattr(DuctSystemType, self._system)


class Manager(object):
    def __init__(self, connectors):
        self.Connectors = list(connectors)


class ThrowingManager(object):
    @property
    def Connectors(self):
        raise Exception("boom")


class Element(object):
    def __init__(self, element_id, bic, family, type_name, type_id, id_class=IntId,
                 name=None, level_id=None, params=None, views=()):
        self.Id = id_class(element_id)
        self.Category = Category(BIC[bic], CATEGORY_NAMES[bic]) if bic else None
        self._family = family
        self._type_name = type_name
        self._type_id = type_id
        self.Name = name or type_name
        self.LevelId = id_class(level_id) if level_id is not None else id_class(-1)
        self._params = params or {}
        self.views = set(views)
        self._id_class = id_class

    def GetTypeId(self):
        return self._id_class(self._type_id)

    def get_Parameter(self, builtin):
        return self._params.get(builtin.name)


class Curve(Element):
    """An MEPCurve: ConnectorManager sits on the element."""

    def __init__(self, *args, **kwargs):
        connectors = kwargs.pop("connectors", None)
        Element.__init__(self, *args, **kwargs)
        self.ConnectorManager = Manager(connectors or [])


class Duct(Curve):
    pass


class FlexDuct(Curve):
    pass


class Insulation(Curve):
    pass


class MEPSystem(Element):
    def __init__(self, element_id):
        Element.__init__(self, element_id, None, "", "", -1)


class MEPModel(object):
    def __init__(self, manager):
        self.ConnectorManager = manager


class FamilyInstance(Element):
    def __init__(self, *args, **kwargs):
        connectors = kwargs.pop("connectors", None)
        manager = kwargs.pop("manager", None)
        space = kwargs.pop("space", None)
        Element.__init__(self, *args, **kwargs)
        if manager is None and connectors is not None:
            manager = Manager(connectors)
        self.MEPModel = MEPModel(manager) if (manager is not None or connectors is not None) else None
        self.Space = space


class ElementType(object):
    def __init__(self, family, name):
        self.FamilyName = family
        self.Name = name


class Level(object):
    def __init__(self, name):
        self.Name = name


class SystemType(object):
    def __init__(self, classification):
        self.SystemClassification = getattr(Classification, classification)


class Space(object):
    def __init__(self, number, name):
        self.Number = number
        self.Name = name


class View(object):
    def __init__(self, view_id, name, view_type="FloorPlan", is_template=False):
        self.Id = IntId(view_id)
        self.Name = name
        self.ViewType = getattr(ViewType, view_type)
        self.IsTemplate = is_template


class Doc(object):
    def __init__(self, elements, lookup=None, view=None):
        self.Title = "Test.rvt"
        self.elements = list(elements)
        self.lookup = dict(lookup or {})
        self.ActiveView = view

    def GetElement(self, element_id):
        value = getattr(element_id, "IntegerValue", None)
        if value is None:
            value = getattr(element_id, "Value", None)
        return self.lookup.get(value)


class Collector(object):
    def __init__(self, doc, view_id=None):
        self.items = list(doc.elements)
        if view_id is not None:
            wanted = getattr(view_id, "IntegerValue", None)
            self.items = [item for item in self.items if wanted in item.views]

    def OfClass(self, klass):
        self.items = [item for item in self.items if isinstance(item, klass)]
        return self

    def OfCategory(self, bic):
        self.items = [item for item in self.items
                      if item.Category is not None and item.Category.Id.IntegerValue == int(bic)]
        return self

    def WherePasses(self, multi_filter):
        wanted = set(int(member) for member in multi_filter.members)
        self.items = [item for item in self.items
                      if item.Category is not None and item.Category.Id.IntegerValue in wanted]
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return list(self.items)

    def ToElementIds(self):
        return [item.Id for item in self.items]


class MultiFilter(object):
    def __init__(self, members):
        self.members = list(members)


class RaisingMultiFilter(object):
    def __init__(self, members):
        raise Exception("no multicategory filter on this Revit")


class ClrList(object):
    def __init__(self):
        self.items = []

    def Add(self, member):
        self.items.append(member)

    def __iter__(self):
        return iter(self.items)


def fake_clr_list(db):
    return ClrList()


class BuiltIn(object):
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __int__(self):
        return self.value


def make_db(multi_filter=MultiFilter, element_id=IntId):
    builtin_categories = types.SimpleNamespace(**dict((name, BuiltIn(name, value)) for name, value in BIC.items()))
    builtin_params = types.SimpleNamespace(**dict((name, BuiltIn(name, index)) for index, name in enumerate((
        "RBS_DUCT_SYSTEM_TYPE_PARAM", "RBS_SYSTEM_CLASSIFICATION_PARAM", "RBS_SYSTEM_NAME_PARAM",
        "RBS_CURVE_DIAMETER_PARAM", "RBS_CURVE_WIDTH_PARAM", "RBS_CURVE_HEIGHT_PARAM",
        "RBS_START_LEVEL_PARAM", "FAMILY_LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM"))))
    return types.SimpleNamespace(
        BuiltInCategory=builtin_categories,
        BuiltInParameter=builtin_params,
        FilteredElementCollector=Collector,
        ElementMulticategoryFilter=multi_filter,
        ElementId=element_id,
        FamilyInstance=FamilyInstance,
        MEPSystem=MEPSystem,
        InsulationLiningBase=Insulation,
        Mechanical=types.SimpleNamespace(Duct=Duct, FlexDuct=FlexDuct),
    )


def join(a, b):
    """Mate two connectors both ways."""
    a.refs.append(b)
    b.refs.append(a)
    a.IsConnected = True
    b.IsConnected = True


def small_model(id_class=IntId):
    """AHU(1) - duct(2, tapped by terminal 5) - VCD(3) - duct(4) - terminal(6)."""
    params = {"RBS_SYSTEM_NAME_PARAM": Param(string="Supply Air 7"),
              "RBS_DUCT_SYSTEM_TYPE_PARAM": Param(element_id=id_class(900))}
    ahu = FamilyInstance(1, "OST_MechanicalEquipment", "AHU", "Unit 1", 101, id_class=id_class,
                         connectors=[], params=params)
    ahu.MEPModel.ConnectorManager.Connectors.append(
        Connector(ahu, 0, shape="Rectangular", width=2.0, height=1.0))
    ahu.MEPModel.ConnectorManager.Connectors.append(
        Connector(ahu, 1, domain="DomainPiping"))  # a chilled water tap: not ours
    duct = Duct(2, "OST_DuctCurves", "Rectangular Duct", "Mitered", 102, id_class=id_class,
                level_id=700, params=dict(params, RBS_CURVE_WIDTH_PARAM=Param(double=2.0),
                                          RBS_CURVE_HEIGHT_PARAM=Param(double=1.0),
                                          RBS_CURVE_DIAMETER_PARAM=Param(double=0.0)))
    duct.ConnectorManager.Connectors.extend([
        Connector(duct, 0, shape="Rectangular", width=2.0, height=1.0),
        Connector(duct, 1, shape="Rectangular", width=2.0, height=1.0),
        Connector(duct, 2, ctype="Curve", shape="Rectangular", width=2.0, height=1.0),
    ])
    vcd = FamilyInstance(3, "OST_DuctAccessory", "Damper - Volume", "200x200", 103, id_class=id_class,
                         connectors=[], params=params, level_id=700)
    vcd.MEPModel.ConnectorManager.Connectors.extend([Connector(vcd, 0), Connector(vcd, 1)])
    small = Duct(4, "OST_DuctCurves", "Round Duct", "Taps", 104, id_class=id_class,
                 params=dict(params, RBS_CURVE_DIAMETER_PARAM=Param(double=0.5)), level_id=700)
    small.ConnectorManager.Connectors.extend([Connector(small, 0), Connector(small, 1),
                                              Connector(small, 2, ctype="Curve")])
    tapped = FamilyInstance(5, "OST_DuctTerminal", "Diffuser", "600x600", 105, id_class=id_class,
                            connectors=[], params=params, views=(50,), space=Space("301", "Office"))
    tapped.MEPModel.ConnectorManager.Connectors.append(Connector(tapped, 0))
    end = FamilyInstance(6, "OST_DuctTerminal", "Diffuser", "600x600", 105, id_class=id_class,
                         connectors=[], params=params, views=(51,))
    end.MEPModel.ConnectorManager.Connectors.append(Connector(end, 0))

    a, d0, d1, dcurve = ahu.MEPModel.ConnectorManager.Connectors[0], \
        duct.ConnectorManager.Connectors[0], duct.ConnectorManager.Connectors[1], \
        duct.ConnectorManager.Connectors[2]
    v0, v1 = vcd.MEPModel.ConnectorManager.Connectors
    s0, s1 = small.ConnectorManager.Connectors[0], small.ConnectorManager.Connectors[1]
    join(a, d0)
    join(d1, v0)
    join(v1, s0)
    join(s1, end.MEPModel.ConnectorManager.Connectors[0])
    join(dcurve, tapped.MEPModel.ConnectorManager.Connectors[0])

    # AllRefs noise: the system's logical connector and the insulation's mirror.
    system = MEPSystem(800)
    d0.refs.append(Connector(system, 0, ctype="Logical"))
    insulation = Insulation(801, "OST_DuctInsulations", "Duct Insulation", "25mm", 106,
                            id_class=id_class)
    mirror = Connector(insulation, 0)
    mirror.IsConnected = True
    d0.refs.append(mirror)

    lookup = {101: ElementType("AHU", "Unit 1"), 102: ElementType("Rectangular Duct", "Mitered"),
              103: ElementType("Damper - Volume", "200x200"), 104: ElementType("Round Duct", "Taps"),
              105: ElementType("Diffuser", "600x600"), 700: Level("Level 3"),
              900: SystemType("SupplyAir")}
    view = View(50, "L3 Mechanical")
    return Doc([ahu, duct, vcd, small, tapped, end, insulation], lookup, view)


def _walk_types(value, path="root"):
    allowed = (int, float, bool, type(u""), type(None))
    if isinstance(value, dict):
        for key, item in value.items():
            _walk_types(key, path + "." + str(key))
            _walk_types(item, path + "." + str(key))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _walk_types(item, path + "[]")
    elif not isinstance(value, allowed):
        raise AssertionError("%s carries a %s" % (path, type(value).__name__))


# ---------------------------------------------------------------- tests


class ScanTests(unittest.TestCase):
    def test_the_pass_reads_every_duct_element_and_only_plain_data_crosses(self):
        doc = small_model()
        snapshot = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)
        self.assertEqual(sorted(snapshot["elements"]), [1, 2, 3, 4, 5, 6])
        _walk_types(snapshot)
        self.assertEqual(snapshot["meta"]["elements_read"], 6)
        self.assertFalse(snapshot["meta"]["truncated"])
        self.assertEqual(snapshot["doc_title"], "Test.rvt")

    def test_kinds_names_levels_spaces_and_classes(self):
        doc = small_model()
        elements = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)["elements"]
        self.assertEqual(elements[1]["kind"], "equipment")
        self.assertEqual(elements[2]["kind"], "duct")
        self.assertEqual(elements[3]["kind"], "accessory")
        self.assertEqual(elements[5]["kind"], "terminal")
        self.assertEqual(elements[3]["type_key"], "Damper - Volume : 200x200")
        self.assertEqual(elements[3]["category"], "Duct Accessories")
        self.assertEqual(elements[2]["level"], "Level 3")
        self.assertEqual(elements[5]["space"], "301 Office")
        self.assertEqual(elements[6]["space"], "")
        self.assertEqual(elements[2]["system_name"], "Supply Air 7")
        self.assertEqual(elements[2]["system_class"], "SupplyAir")
        self.assertEqual(elements[2]["size"], 2.0)
        self.assertEqual(elements[4]["size"], 0.5)
        self.assertEqual(elements[1]["size"], 2.0)  # the equipment's largest duct connector
        self.assertEqual(elements[3]["size"], 0.0)

    def test_the_tap_is_an_edge_in_both_directions_through_the_curve_connector(self):
        doc = small_model()
        elements = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)["elements"]
        curve = [c for c in elements[2]["connectors"] if c["type"] == "Curve"][0]
        self.assertEqual(curve["partners"], [[5, 0]])
        self.assertTrue(curve["connected"])
        terminal = elements[5]["connectors"][0]
        self.assertEqual(terminal["partners"], [[2, 2]])

    def test_allrefs_noise_is_filtered(self):
        doc = small_model()
        elements = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)["elements"]
        first = elements[2]["connectors"][0]
        # Own ref, the MEPSystem's logical connector and the insulation
        # mirror are all gone; only the AHU remains.
        self.assertEqual(first["partners"], [[1, 0]])
        self.assertNotIn(801, elements)
        self.assertNotIn(800, elements)

    def test_cross_domain_connectors_are_ignored(self):
        doc = small_model()
        elements = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)["elements"]
        self.assertEqual([c["id"] for c in elements[1]["connectors"]], [0])

    def test_isconnected_and_partner_disagreement_is_counted_not_trusted(self):
        doc = small_model()
        stray = doc.elements[3].ConnectorManager.Connectors[2]  # the small duct's Curve
        stray.IsConnected = True  # says connected, has no partner
        meta = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)["meta"]
        self.assertEqual(meta["isconnected_mismatch"], 1)

    def test_an_unknown_partner_is_pulled_in_as_other(self):
        doc = small_model()
        generic = FamilyInstance(9, "OST_GenericModel", "Generic Damper", "GM", 109,
                                 connectors=[], params={})
        generic.MEPModel.ConnectorManager.Connectors.extend([Connector(generic, 0), Connector(generic, 1)])
        # Splice it between the small duct and the end terminal.
        small = doc.elements[3]
        end = doc.elements[5]
        s1 = small.ConnectorManager.Connectors[1]
        e0 = end.MEPModel.ConnectorManager.Connectors[0]
        s1.refs.remove(e0)
        e0.refs.remove(s1)
        join(s1, generic.MEPModel.ConnectorManager.Connectors[0])
        join(generic.MEPModel.ConnectorManager.Connectors[1], e0)
        doc.lookup[109] = ElementType("Generic Damper", "GM")
        # Not in doc.elements on purpose: no collector finds it.
        snapshot = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)
        self.assertIn(9, snapshot["elements"])
        self.assertEqual(snapshot["elements"][9]["kind"], "other")
        self.assertEqual(snapshot["elements"][9]["category"], "Generic Models")
        self.assertEqual(snapshot["meta"]["pulled_in"], 1)

    def test_a_throwing_manager_is_recorded_and_kept(self):
        doc = small_model()
        bad = FamilyInstance(7, "OST_DuctFitting", "Elbow", "Round", 107, manager=ThrowingManager())
        doc.elements.append(bad)
        doc.lookup[107] = ElementType("Elbow", "Round")
        snapshot = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)
        self.assertIn(7, snapshot["elements"])
        self.assertIn("boom", snapshot["elements"][7]["error"])
        self.assertEqual(snapshot["elements"][7]["connectors"], [])
        self.assertEqual(snapshot["meta"]["unreadable_ids"], [7])

    def test_a_family_without_mep_model_in_a_duct_category_is_unreadable(self):
        doc = small_model()
        dumb = FamilyInstance(8, "OST_DuctAccessory", "Placeholder", "Any", 108)
        doc.elements.append(dumb)
        doc.lookup[108] = ElementType("Placeholder", "Any")
        snapshot = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)
        self.assertEqual(snapshot["elements"][8]["error"], "no connector manager")

    def test_multicategory_filter_falls_back_per_category_with_dedupe(self):
        doc = small_model()
        with_fallback = revit.scan(doc, db=make_db(multi_filter=RaisingMultiFilter))
        self.assertEqual(sorted(with_fallback["elements"]), [1, 2, 3, 4, 5, 6])

    def test_value_only_element_ids_work(self):
        doc = small_model(id_class=ValueId)
        snapshot = revit.scan(doc, db=make_db(element_id=ValueId), clr_list=fake_clr_list)
        self.assertEqual(sorted(snapshot["elements"]), [1, 2, 3, 4, 5, 6])
        self.assertEqual(snapshot["elements"][2]["level"], "Level 3")

    def test_the_curve_connector_of_a_lonely_duct_is_not_a_partner_and_not_open_noise(self):
        doc = small_model()
        elements = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)["elements"]
        small_curve = [c for c in elements[4]["connectors"] if c["type"] == "Curve"][0]
        self.assertFalse(small_curve["connected"])
        self.assertEqual(small_curve["partners"], [])

    def test_duct_system_type_is_the_last_resort_for_class(self):
        doc = small_model()
        for element in doc.elements:
            element._params.pop("RBS_DUCT_SYSTEM_TYPE_PARAM", None)
        elements = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)["elements"]
        self.assertEqual(elements[2]["system_class"], "SupplyAir")

    def test_the_classification_string_beats_the_connector(self):
        doc = small_model()
        for element in doc.elements:
            element._params.pop("RBS_DUCT_SYSTEM_TYPE_PARAM", None)
            element._params["RBS_SYSTEM_CLASSIFICATION_PARAM"] = Param(string="Return Air,Supply Air")
        elements = revit.scan(doc, db=make_db(), clr_list=fake_clr_list)["elements"]
        self.assertEqual(elements[2]["system_class"], "Return Air")


class BudgetTests(unittest.TestCase):
    def test_the_element_cap_truncates_and_says_so(self):
        doc = small_model()
        snapshot = revit.scan(doc, {"element_cap": 3}, db=make_db(), clr_list=fake_clr_list)
        self.assertTrue(snapshot["meta"]["truncated"])
        self.assertIn("element cap", snapshot["meta"]["truncated_reason"])
        self.assertEqual(len(snapshot["elements"]), 3)

    def test_the_clock_budget_truncates(self):
        doc = small_model()
        for _index in range(500):
            doc.elements.append(Duct(1000 + _index, "OST_DuctCurves", "Rectangular Duct", "Mitered", 102,
                                     params={}))
        ticks = [0.0]

        def clock():
            ticks[0] += 10.0  # every look at the clock costs ten seconds
            return ticks[0]

        snapshot = revit.scan(doc, {"budget_seconds": 5.0}, db=make_db(), clock=clock,
                              clr_list=fake_clr_list)
        self.assertTrue(snapshot["meta"]["truncated"])
        self.assertIn("budget", snapshot["meta"]["truncated_reason"])

    def test_progress_can_cancel(self):
        doc = small_model()
        for _index in range(500):
            doc.elements.append(Duct(1000 + _index, "OST_DuctCurves", "Rectangular Duct", "Mitered", 102,
                                     params={}))
        calls = []

        def progress(done, total):
            calls.append((done, total))
            return False

        snapshot = revit.scan(doc, db=make_db(), progress=progress, clr_list=fake_clr_list)
        self.assertTrue(calls)
        self.assertEqual(snapshot["meta"]["truncated_reason"], "cancelled")


class ScopeTests(unittest.TestCase):
    def test_active_view_scope_lists_the_terminals_in_that_view(self):
        doc = small_model()
        snapshot = revit.scan(doc, {"scope": "view", "view_id": 50, "view_name": "L3"},
                              db=make_db(), clr_list=fake_clr_list)
        self.assertEqual(snapshot["scope_terminal_ids"], [5])
        self.assertEqual(snapshot["meta"]["scope"], "view")
        # The walk still read the whole model.
        self.assertEqual(len(snapshot["elements"]), 6)

    def test_whole_model_scope_carries_no_terminal_list(self):
        doc = small_model()
        snapshot = revit.scan(doc, {"scope": "model"}, db=make_db(), clr_list=fake_clr_list)
        self.assertIsNone(snapshot["scope_terminal_ids"])

    def test_view_info_greys_non_graphical_views(self):
        self.assertTrue(revit.view_info(Doc([], view=View(1, "Plan")), make_db())["is_graphical"])
        self.assertFalse(revit.view_info(Doc([], view=View(1, "Sched", "Schedule")), make_db())["is_graphical"])
        self.assertFalse(revit.view_info(Doc([], view=View(1, "T", is_template=True)), make_db())["is_graphical"])
        self.assertFalse(revit.view_info(Doc([], view=None), make_db())["is_graphical"])


class CatalogTests(unittest.TestCase):
    def test_types_come_with_kinds_categories_and_counts(self):
        doc = small_model()
        generic = FamilyInstance(9, "OST_GenericModel", "Generic Damper", "GM", 109, connectors=[Connector(None, 0)])
        generic.MEPModel.ConnectorManager.Connectors[0].Owner = generic
        door = FamilyInstance(10, "OST_GenericModel", "Door", "Single", 110)  # no MEPModel
        doc.elements.extend([generic, door])
        doc.lookup[109] = ElementType("Generic Damper", "GM")
        doc.lookup[110] = ElementType("Door", "Single")
        catalog = revit.collect_types(doc, db=make_db(), clr_list=fake_clr_list)
        rows = dict((row["type_key"], row) for row in catalog["types"])
        self.assertEqual(rows["Diffuser : 600x600"]["count"], 2)
        self.assertEqual(rows["Diffuser : 600x600"]["kind"], "terminal")
        self.assertEqual(rows["Damper - Volume : 200x200"]["category"], "Duct Accessories")
        self.assertEqual(rows["Generic Damper : GM"]["kind"], "other")
        self.assertEqual(rows["Generic Damper : GM"]["category"], "Generic Models")
        self.assertNotIn("Door : Single", rows)
        self.assertNotIn("Rectangular Duct : Mitered", rows)  # ducts are never dampers
        self.assertEqual(catalog["view"]["name"], "L3 Mechanical")
        self.assertTrue(catalog["view"]["is_graphical"])
        _walk_types(catalog)

    def test_the_probe_stops_at_its_budget(self):
        doc = small_model()
        for index in range(600):
            doc.elements.append(FamilyInstance(2000 + index, "OST_GenericModel", "Door", "Single", 110))
        doc.lookup[110] = ElementType("Door", "Single")
        ticks = [0.0]

        def clock():
            ticks[0] += 100.0
            return ticks[0]

        catalog = revit.collect_types(doc, db=make_db(), clock=clock, budget_seconds=1.0,
                                      clr_list=fake_clr_list)
        self.assertTrue(catalog["meta"]["probe_truncated"])


class HelperTests(unittest.TestCase):
    def test_enum_names_read_with_or_without_the_type_prefix(self):
        self.assertEqual(revit.enum_name(ConnectorType.End), "End")
        self.assertEqual(revit.enum_name("End"), "End")
        self.assertEqual(revit.enum_name(None), "")

    def test_show_falls_back_to_false_without_the_lib_helper(self):
        old = revit._lib_show_elements
        revit._lib_show_elements = None
        try:
            self.assertFalse(revit.show_elements(None, [1]))
        finally:
            revit._lib_show_elements = old


if __name__ == "__main__":
    unittest.main()
