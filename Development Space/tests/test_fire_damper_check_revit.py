"""Fire Damper Check's adapter is a composition of two lib engines; these
tests pin the composition - what it asks each engine for - with stubs in
place of the engines, which have their own fake-driven suites."""

import importlib.util
import pathlib
import sys
import types
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Fire Damper Check.pushbutton"
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


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(COMMAND_DIR / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


revit = _load("fire_damper_check_revit")


class Context(object):
    def __init__(self, key, loaded=True):
        self.key = key
        self.title = key
        self.loaded = loaded
        self.doc = object() if loaded else None
        self.status = u""
        self.is_host = key == u"This model"

    def describe(self):
        return {"key": self.key, "title": self.title, "loaded": self.loaded, "status": self.status,
                "is_host": self.is_host, "instance_count": 1}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._network = revit.duct_network_revit
        self._crossings = revit.link_crossings
        calls = self.calls

        def collect_types(doc, db=None, clock=None):
            calls.append(("collect_types", doc))
            return {"types": [{"type_key": u"Fire Damper : 1hr"}], "view": {"id": 5, "name": u"L1"},
                    "meta": {"probe_truncated": True}}

        def scan(doc, options, db=None, progress=None):
            calls.append(("scan", options))
            return {"elements": {}, "meta": {}}

        def element_ids_in_view(doc, view_id, members, db=None):
            calls.append(("in_view", view_id, tuple(members)))
            return [1, 2]

        def build_contexts(doc, db=None):
            return [Context(u"This model"), Context(u"Arch.rvt"), Context(u"Str.rvt", loaded=False)]

        def collect_catalog(context, db=None, include_floors=True):
            calls.append(("catalog", context.key, include_floors))
            return {"types": [] if not context.loaded else [{"type_key": u"W"}], "line_styles": [],
                    "levels": [], "rating_params": []}

        def build_index(contexts, choices, options, db=None):
            calls.append(("index", sorted(choices), options))
            return "INDEX"

        def describe_index(index):
            return {"barriers": [], "line_barriers": [], "skips": {}}

        def find_crossings(index, segments, options=None, db=None, progress=None, clock=None):
            calls.append(("crossings", index, len(segments)))
            return {"items": [], "meta": {}}

        def show_crossing(uidoc, show, contexts=None, db=None):
            calls.append(("show", show))
            return True

        revit.duct_network_revit = types.SimpleNamespace(
            collect_types=collect_types, scan=scan, element_ids_in_view=element_ids_in_view)
        revit.link_crossings = types.SimpleNamespace(
            build_contexts=build_contexts, collect_catalog=collect_catalog, build_index=build_index,
            describe_index=describe_index, find_crossings=find_crossings, show_crossing=show_crossing,
            host_extent=lambda segments: "EXTENT")

    def tearDown(self):
        revit.duct_network_revit = self._network
        revit.link_crossings = self._crossings

    def test_the_catalog_joins_duct_types_with_every_context(self):
        catalog = revit.collect_catalog("DOC", clock=lambda: 0.0)
        self.assertEqual(catalog["damper_types"], [{"type_key": u"Fire Damper : 1hr"}])
        self.assertEqual([link["key"] for link in catalog["links"]], [u"This model", u"Arch.rvt", u"Str.rvt"])
        self.assertEqual(catalog["links"][2]["loaded"], False)
        self.assertEqual(catalog["links"][1]["types"], [{"type_key": u"W"}])
        self.assertTrue(catalog["meta"]["probe_truncated"])
        self.assertEqual(catalog["view"]["name"], u"L1")

    def test_the_network_scan_asks_for_curves_and_the_dampers_origins(self):
        revit.scan_network("DOC", [u"Fire Damper : 1hr"], scope="view", view_id=5, view_name=u"L1")
        options = [call[1] for call in self.calls if call[0] == "scan"][0]
        self.assertTrue(options["with_curves"])
        self.assertEqual(options["origin_type_keys"], [u"Fire Damper : 1hr"])
        self.assertEqual((options["scope"], options["view_id"], options["view_name"]), ("view", 5, u"L1"))

    def test_view_scope_reads_ducts_and_flex(self):
        self.assertEqual(revit.duct_ids_in_view("DOC", 5), [1, 2])
        self.assertEqual(self.calls[-1], ("in_view", 5, ("OST_DuctCurves", "OST_FlexDuctCurves")))

    def test_index_describe_and_crossings_compose(self):
        contexts, index = revit.build_index("DOC", {u"Arch.rvt": {"enabled": True}}, {"include_floors": False})
        self.assertEqual(index, "INDEX")
        description = revit.describe(contexts, index)
        self.assertEqual([link["key"] for link in description["links"]], [u"This model", u"Arch.rvt", u"Str.rvt"])
        result = revit.find_crossings(index, [{"points": [[0, 0, 0], [1, 0, 0]]}])
        self.assertEqual(result["items"], [])
        self.assertEqual(self.calls[-1], ("crossings", "INDEX", 1))
        self.assertEqual(revit.host_extent([]), "EXTENT")

    def test_show_delegates(self):
        self.assertTrue(revit.show("UIDOC", {"host_ids": [1]}))
        self.assertEqual(self.calls[-1], ("show", {"host_ids": [1]}))


if __name__ == "__main__":
    unittest.main()
