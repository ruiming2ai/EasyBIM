"""lib/easybim/my_ribbon.py - registry file and the ribbon apply engine.

Runs on desktop Python against a fake AdWindows ribbon, the way
``test_modify_ribbon.py`` drives ``modify_ribbon``.  The envvar mirror is a
plain dict here so the clear-ours-first behaviour can be exercised across
several apply() calls in one process.
"""
import importlib.util
import json
import os
import pathlib
import shutil
import tempfile
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "my_ribbon.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("my_ribbon", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- fake ribbon --------------------------------------------------------------


class _FakeItems(list):
    def Add(self, item):
        self.append(item)

    def Remove(self, item):
        for index, existing in enumerate(self):
            if existing is item:
                del self[index]
                return True
        return False


class _FakeRibbon(object):
    def __init__(self, tabs=None):
        self.Tabs = _FakeItems(tabs or [])


class _FakeTab(object):
    def __init__(self, tab_id, title=None, panels=None):
        self.Id = tab_id
        self.Title = title or tab_id
        self.Name = self.Title
        self.AutomationName = self.Title
        self.Panels = _FakeItems(panels or [])
        self.IsVisible = True
        self.IsEnabled = True


class _FakePanelSource(object):
    def __init__(self, panel_id="", title=""):
        self.Id = panel_id
        self.Title = title
        self.AutomationName = title
        self.Name = title
        self.Items = _FakeItems()


class _FakePanel(object):
    def __init__(self, panel_id="", title=""):
        self.Source = _FakePanelSource(panel_id, title)


class _FakeItem(object):
    def __init__(self, item_id, text, children=None):
        self.Id = item_id
        self.Text = text
        self.AutomationName = text
        self.IsEnabled = True
        if children is not None:
            self.Items = _FakeItems(children)


class RibbonRowPanel(object):
    """Named like the AdWindows class so the engine sees a stack."""

    def __init__(self, children):
        self.Id = ""
        self.Text = ""
        self.Items = _FakeItems(children)


class _FakeAutodeskWindows(object):
    class RibbonPanelSource(_FakePanelSource):
        def __init__(self):
            _FakePanelSource.__init__(self)

    class RibbonPanel(_FakePanel):
        def __init__(self):
            self.Source = None

    class RibbonTab(_FakeTab):
        def __init__(self):
            _FakeTab.__init__(self, "")


def _build_ribbon():
    easybim_misc = _FakePanel("CustomCtrl_%EasyBIM%Misc Tools", "Misc Tools")
    easybim_misc.Source.Items.extend([
        _FakeItem("CustomCtrl_%CustomCtrl_%EasyBIM%Misc Tools%Slope", "Slope"),
        _FakeItem("CustomCtrl_%CustomCtrl_%EasyBIM%Misc Tools%Tag Align", "Tag\nAlign"),
    ])
    easybim_tab = _FakeTab("EasyBIM", panels=[easybim_misc])

    baz = _FakeItem("CustomCtrl_%CustomCtrl_%Foo%Bar%Baz", "Baz Tool")
    child_a = _FakeItem("CustomCtrl_%CustomCtrl_%CustomCtrl_%Foo%Bar%Tools%A", "A")
    child_b = _FakeItem("CustomCtrl_%CustomCtrl_%CustomCtrl_%Foo%Bar%Tools%B", "B")
    tools = _FakeItem("CustomCtrl_%CustomCtrl_%Foo%Bar%Tools", "Tools", children=[child_a, child_b])
    stacked_one = _FakeItem("CustomCtrl_%CustomCtrl_%Foo%Bar%One", "One")
    stacked_two = _FakeItem("CustomCtrl_%CustomCtrl_%Foo%Bar%Two", "Two")
    stack = RibbonRowPanel([stacked_one, stacked_two])
    bar_panel = _FakePanel("CustomCtrl_%Foo%Bar", "Bar")
    bar_panel.Source.Items.extend([baz, tools, stack])
    foo_tab = _FakeTab("Foo", panels=[bar_panel])

    pyrevit_panel = _FakePanel("CustomCtrl_%pyRevit%pyRevit", "pyRevit")
    pyrevit_panel.Source.Items.append(
        _FakeItem("CustomCtrl_%CustomCtrl_%pyRevit%pyRevit%Reload", "Reload"))
    pyrevit_tab = _FakeTab("pyRevit", panels=[pyrevit_panel])

    ribbon = _FakeRibbon([easybim_tab, foo_tab, pyrevit_tab])
    return ribbon, {
        "easybim_tab": easybim_tab, "easybim_misc": easybim_misc,
        "foo_tab": foo_tab, "bar_panel": bar_panel, "baz": baz, "tools": tools,
        "child_a": child_a, "child_b": child_b, "stack": stack,
        "stacked_one": stacked_one, "stacked_two": stacked_two,
        "pyrevit_tab": pyrevit_tab,
    }


def _level(name, title=None):
    return {"name": name, "title": title or name}


def _registry():
    return {
        "format": 1,
        "sources": [
            {"id": "s1", "kind": "git", "url": "https://github.com/o/foo", "ext_name": "Foo",
             "tab_names": ["Foo"], "installed_by_my_ribbon": True, "hide_tab": True},
            {"id": "s2", "kind": "installed", "ext_name": "pyRevitCore",
             "tab_names": ["pyRevit"], "hide_tab": True},
        ],
        "destinations": [
            {"id": "d1", "tab": "EasyBIM", "panel": "My Tools", "own_tab": False},
            {"id": "d2", "tab": "MEP Kit", "panel": "Sheets", "own_tab": True},
        ],
        "placements": [
            {"id": "p_baz", "source": "s1", "dest": "d1", "order": 1, "kind": "button",
             "title": "Baz Tool", "control_id": "CustomCtrl_%CustomCtrl_%Foo%Bar%Baz",
             "path": [_level("Foo"), _level("Bar"), _level("Baz", "Baz Tool")]},
            {"id": "p_child_a", "source": "s1", "dest": "d2", "order": 0, "kind": "button",
             "title": "A", "control_id": "",
             "path": [_level("Foo"), _level("Bar"), _level("Tools"), _level("A")]},
            {"id": "p_stacked", "source": "s1", "dest": "d1", "order": 0, "kind": "button",
             "title": "Two", "control_id": "",
             "path": [_level("Foo"), _level("Bar"), _level("Two")]},
            {"id": "p_tools", "source": "s1", "dest": "d2", "order": 1, "kind": "pulldown",
             "title": "Tools", "control_id": "",
             "path": [_level("Foo"), _level("Bar"), _level("Tools")]},
            {"id": "p_gone", "source": "s1", "dest": "d1", "order": 2, "kind": "button",
             "title": "Gone", "control_id": "CustomCtrl_%CustomCtrl_%Foo%Bar%Gone",
             "path": [_level("Foo"), _level("Bar"), _level("Gone")]},
        ],
    }


class _EnvStore(object):
    def __init__(self, module):
        self.values = {}
        module._get_envvar = self.get
        module._set_envvar = self.set

    def get(self, name, default=None):
        value = self.values.get(name)
        return default if value is None else value

    def set(self, name, value):
        self.values[name] = value
        return True


class MyRibbonRegistryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_document_reads_as_empty_registry(self):
        registry = self.mod.read_registry({})
        self.assertEqual(registry["format"], self.mod.FORMAT_VERSION)
        self.assertEqual(registry["sources"], [])
        self.assertEqual(registry["placements"], [])

    def test_newer_format_is_refused(self):
        with self.assertRaises(self.mod.RegistryFormatError):
            self.mod.read_registry({"format": self.mod.FORMAT_VERSION + 1, "sources": []})

    def test_document_without_format_is_refused(self):
        with self.assertRaises(self.mod.RegistryFormatError):
            self.mod.read_registry({"sources": [{"id": "x"}]})

    def test_read_normalises_and_drops_broken_entries(self):
        registry = self.mod.read_registry({
            "format": 1,
            "sources": [{"id": "s1", "kind": "weird", "hide_tab": 1, "tab_names": ["A", ""]},
                        {"kind": "git"}],
            "destinations": [{"id": "d1", "tab": "T", "panel": "P"}, {"tab": "no id"}],
            "placements": [
                {"id": "p1", "dest": "d1", "path": ["Tab", {"name": "Panel"}, {"name": "B", "title": "Bee"}],
                 "order": "3"},
                {"id": "p2", "dest": "d1", "path": []},
                {"id": "p3", "path": ["Tab", "Panel", "Item"]},
            ],
        })
        self.assertEqual(len(registry["sources"]), 1)
        self.assertEqual(registry["sources"][0]["kind"], "git")
        self.assertTrue(registry["sources"][0]["hide_tab"])
        self.assertEqual(registry["sources"][0]["tab_names"], ["A"])
        self.assertEqual(len(registry["destinations"]), 1)
        self.assertEqual(len(registry["placements"]), 1)
        placement = registry["placements"][0]
        self.assertEqual(placement["order"], 3)
        self.assertEqual(placement["path"][0], {"name": "Tab", "title": "Tab"})
        self.assertEqual(placement["path"][2], {"name": "B", "title": "Bee"})
        self.assertEqual(placement["title"], "Bee")
        self.assertEqual(placement["kind"], "button")

    def test_catalogue_sources_keep_their_catalogue_name(self):
        registry = self.mod.read_registry({
            "format": 1,
            "sources": [{"id": "s1", "kind": "catalogue", "name": "pyRevitTools", "ext_name": "pyRevitTools"},
                        {"id": "s2", "kind": "catalogue", "ext_name": "Other"},
                        {"id": "s3", "kind": "git", "name": "ignored", "ext_name": "X"}]})
        self.assertEqual(registry["sources"][0]["name"], "pyRevitTools")
        self.assertEqual(registry["sources"][1]["name"], "Other")
        self.assertNotIn("name", registry["sources"][2])

    def test_save_and_load_round_trip(self):
        path = os.path.join(self.tmp, "sub", "registry.json")
        registry = self.mod.read_registry(_registry())
        ok, error = self.mod.save_registry(registry, path)
        self.assertTrue(ok, error)
        self.assertFalse(os.path.exists(path + ".tmp"))
        loaded, error = self.mod.load_registry(path)
        self.assertEqual(error, "")
        self.assertEqual(loaded, registry)
        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["format"], self.mod.FORMAT_VERSION)

    def test_missing_file_is_an_empty_registry_without_error(self):
        registry, error = self.mod.load_registry(os.path.join(self.tmp, "nope.json"))
        self.assertEqual(error, "")
        self.assertEqual(registry["placements"], [])

    def test_unreadable_file_reports_instead_of_wiping(self):
        path = os.path.join(self.tmp, "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        registry, error = self.mod.load_registry(path)
        self.assertIn("Could not read", error)
        self.assertEqual(registry["placements"], [])

    def test_newer_file_reports_the_reason(self):
        path = os.path.join(self.tmp, "newer.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"format": 99}, handle)
        registry, error = self.mod.load_registry(path)
        self.assertIn("newer My Ribbon", error)
        self.assertEqual(registry["placements"], [])

    def test_registry_has_work(self):
        self.assertFalse(self.mod.registry_has_work(self.mod.empty_registry()))
        self.assertTrue(self.mod.registry_has_work(self.mod.read_registry(_registry())))
        hide_only = self.mod.empty_registry()
        hide_only["sources"].append({"id": "s", "hide_tab": True, "tab_names": ["X"]})
        self.assertTrue(self.mod.registry_has_work(hide_only))

    def test_slug(self):
        self.assertEqual(self.mod.slug("My Tools"), "My_Tools")
        self.assertEqual(self.mod.slug("  MEP / Kit (2) "), "MEP_Kit_2")
        self.assertEqual(self.mod.slug(u"Électricité"), "lectricit")
        self.assertEqual(self.mod.slug(""), "Untitled")


class MyRibbonApplyTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.env = _EnvStore(self.mod)
        self.ribbon, self.parts = _build_ribbon()
        self.aw = _FakeAutodeskWindows()

    def _apply(self, registry=None):
        registry = self.mod.read_registry(registry or _registry())
        return self.mod.apply(registry, ribbon=self.ribbon, autodesk_windows=self.aw)

    def _find_tab(self, title):
        for tab in self.ribbon.Tabs:
            if tab.Title == title:
                return tab
        return None

    def _find_panel(self, tab, title):
        for panel in tab.Panels:
            if panel.Source.Title == title:
                return panel
        return None

    def test_shares_the_same_objects_into_new_panel_and_new_tab(self):
        report = self._apply()
        my_tools = self._find_panel(self.parts["easybim_tab"], "My Tools")
        self.assertIsNotNone(my_tools)
        # order 0 = stacked "Two", order 1 = Baz; "Gone" is missing
        self.assertEqual(list(my_tools.Source.Items),
                         [self.parts["stacked_two"], self.parts["baz"]])
        self.assertIs(my_tools.Source.Items[1], self.parts["baz"])

        mep_kit = self._find_tab("MEP Kit")
        self.assertIsNotNone(mep_kit)
        self.assertTrue(mep_kit.Id.startswith(self.mod.ID_PREFIX))
        sheets = self._find_panel(mep_kit, "Sheets")
        self.assertEqual(list(sheets.Source.Items),
                         [self.parts["child_a"], self.parts["tools"]])

        self.assertEqual(sorted(report["added"]),
                         ["p_baz", "p_child_a", "p_stacked", "p_tools"])
        self.assertEqual(len(report["missing"]), 1)
        self.assertEqual(report["missing"][0]["placement"], "p_gone")
        self.assertIn("Gone", report["missing"][0]["reason"])
        self.assertEqual(report["created_tabs"], ["MEP Kit"])
        self.assertEqual(sorted(report["created_panels"]),
                         ["EasyBIM > My Tools", "MEP Kit > Sheets"])
        self.assertEqual(report["errors"], [])

    def test_source_items_stay_where_they_were(self):
        self._apply()
        bar_items = list(self.parts["bar_panel"].Source.Items)
        self.assertEqual(bar_items, [self.parts["baz"], self.parts["tools"], self.parts["stack"]])

    def test_hides_requested_tabs_but_never_pyrevit_or_a_destination(self):
        report = self._apply()
        self.assertFalse(self.parts["foo_tab"].IsVisible)
        self.assertTrue(self.parts["pyrevit_tab"].IsVisible)
        self.assertTrue(self.parts["easybim_tab"].IsVisible)
        self.assertEqual(report["hidden_tabs"], ["Foo"])

    def test_hiding_a_destination_tab_is_refused(self):
        registry = _registry()
        registry["sources"].append(
            {"id": "s3", "kind": "installed", "ext_name": "EasyBIM",
             "tab_names": ["EasyBIM"], "hide_tab": True})
        self._apply(registry)
        self.assertTrue(self.parts["easybim_tab"].IsVisible)

    def test_apply_twice_adds_nothing_twice(self):
        self._apply()
        self._apply()
        my_tools = self._find_panel(self.parts["easybim_tab"], "My Tools")
        self.assertEqual(len(my_tools.Source.Items), 2)
        self.assertEqual(len([t for t in self.ribbon.Tabs if t.Title == "MEP Kit"]), 1)
        mep_kit = self._find_tab("MEP Kit")
        self.assertEqual(len([p for p in mep_kit.Panels if p.Source.Title == "Sheets"]), 1)
        self.assertEqual(len(self._find_panel(mep_kit, "Sheets").Source.Items), 2)

    def test_removed_placement_is_taken_back_and_empty_containers_go(self):
        self._apply()
        registry = _registry()
        registry["placements"] = [p for p in registry["placements"] if p["dest"] == "d1"]
        registry["destinations"] = [d for d in registry["destinations"] if d["id"] == "d1"]
        report = self._apply(registry)
        self.assertIsNone(self._find_tab("MEP Kit"))
        my_tools = self._find_panel(self.parts["easybim_tab"], "My Tools")
        self.assertEqual(list(my_tools.Source.Items),
                         [self.parts["stacked_two"], self.parts["baz"]])
        self.assertEqual(report["errors"], [])

    def test_empty_registry_takes_everything_back(self):
        self._apply()
        report = self._apply({"format": 1})
        self.assertIsNone(self._find_tab("MEP Kit"))
        self.assertIsNone(self._find_panel(self.parts["easybim_tab"], "My Tools"))
        self.assertTrue(self.parts["foo_tab"].IsVisible)
        self.assertEqual(report["shown_tabs"], ["Foo"])
        self.assertEqual(list(self.parts["bar_panel"].Source.Items),
                         [self.parts["baz"], self.parts["tools"], self.parts["stack"]])

    def test_placement_into_an_existing_panel_appends_after_its_items(self):
        registry = _registry()
        registry["destinations"] = [{"id": "d1", "tab": "EasyBIM", "panel": "Misc Tools"}]
        registry["placements"] = [registry["placements"][0]]
        self._apply(registry)
        items = list(self.parts["easybim_misc"].Source.Items)
        self.assertEqual(items[-1], self.parts["baz"])
        self.assertEqual(len(items), 3)
        # and a second apply keeps it at exactly one copy
        self._apply(registry)
        self.assertEqual(len(self.parts["easybim_misc"].Source.Items), 3)

    def test_placing_a_button_into_its_own_panel_is_not_duplicated(self):
        registry = _registry()
        registry["destinations"] = [{"id": "d1", "tab": "Foo", "panel": "Bar"}]
        registry["placements"] = [registry["placements"][0]]
        report = self._apply(registry)
        self.assertEqual(report["added"], ["p_baz"])
        self.assertEqual(len(self.parts["bar_panel"].Source.Items), 3)

    def test_missing_destination_tab_is_reported_when_not_our_own(self):
        registry = _registry()
        registry["destinations"] = [{"id": "d1", "tab": "Nowhere", "panel": "P", "own_tab": False}]
        registry["placements"] = [registry["placements"][0]]
        report = self._apply(registry)
        self.assertEqual(report["added"], [])
        self.assertIn("Nowhere", report["missing"][0]["reason"])
        self.assertIsNone(self._find_tab("Nowhere"))

    def test_structural_match_tolerates_title_and_newline_differences(self):
        registry = _registry()
        registry["placements"] = [{
            "id": "p_tag", "source": "s2", "dest": "d1", "order": 0, "kind": "button",
            "title": "Tag Align", "control_id": "",
            "path": [_level("EasyBIM"), _level("Misc Tools"), _level("Tag Align", "Tag\\nAlign")],
        }]
        registry["destinations"] = [{"id": "d1", "tab": "MEP Kit", "panel": "Tags", "own_tab": True}]
        report = self._apply(registry)
        self.assertEqual(report["added"], ["p_tag"])
        tags = self._find_panel(self._find_tab("MEP Kit"), "Tags")
        self.assertIs(tags.Source.Items[0], self.parts["easybim_misc"].Source.Items[1])

    def test_id_match_wins_over_a_renamed_title(self):
        # Upstream renamed the button text; the Id is unchanged, so it still resolves.
        self.parts["baz"].Text = "Baz Renamed"
        registry = _registry()
        registry["placements"] = [registry["placements"][0]]
        report = self._apply(registry)
        self.assertEqual(report["added"], ["p_baz"])

    def test_report_survives_a_ribbon_that_is_missing(self):
        report = self.mod.apply(self.mod.read_registry(_registry()), ribbon=None,
                                autodesk_windows=None)
        self.assertTrue(report["errors"])

    def test_apply_saved_touches_nothing_when_there_is_no_file_and_no_leftovers(self):
        report = self.mod.apply_saved(path=os.path.join(tempfile.gettempdir(), "no-such-registry.json"),
                                      ribbon=self.ribbon, autodesk_windows=self.aw)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["added"], [])
        self.assertEqual(len(self.ribbon.Tabs), 3)

    def test_apply_saved_reads_the_file(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "r.json")
            self.mod.save_registry(self.mod.read_registry(_registry()), path)
            report = self.mod.apply_saved(path=path, ribbon=self.ribbon, autodesk_windows=self.aw)
            self.assertIn("p_baz", report["added"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_startup_queue_flag(self):
        self.assertFalse(self.mod.has_pending_startup_apply())
        self.assertTrue(self.mod.queue_startup_apply())
        self.assertTrue(self.mod.has_pending_startup_apply())
        report = self.mod.run_pending_startup_apply(
            path=os.path.join(tempfile.gettempdir(), "no-such-registry.json"),
            ribbon=self.ribbon, autodesk_windows=self.aw)
        self.assertFalse(self.mod.has_pending_startup_apply())
        self.assertEqual(report["errors"], [])


class ListRibbonTests(unittest.TestCase):
    def test_lists_tabs_and_panels_and_marks_ours(self):
        mod = _load_module()
        _EnvStore(mod)
        ribbon, parts = _build_ribbon()
        registry = mod.read_registry(_registry())
        mod.apply(registry, ribbon=ribbon, autodesk_windows=_FakeAutodeskWindows())
        summary = mod.list_ribbon(ribbon)
        titles = [t["title"] for t in summary]
        self.assertEqual(titles, ["EasyBIM", "Foo", "pyRevit", "MEP Kit"])
        easybim = summary[0]
        self.assertEqual([p["title"] for p in easybim["panels"]], ["Misc Tools", "My Tools"])
        self.assertFalse(easybim["is_ours"])
        self.assertFalse(easybim["panels"][0]["is_ours"])
        self.assertTrue(easybim["panels"][1]["is_ours"])
        self.assertTrue(summary[3]["is_ours"])
        self.assertEqual(mod.list_ribbon(None) if mod._get_default_ribbon() is None else [], [])


if __name__ == "__main__":
    unittest.main()
