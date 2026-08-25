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

    class RibbonRowPanel(object):
        """No-arg twin of the reader fake above: the rows the engine builds."""

        def __init__(self):
            self.Id = ""
            self.Text = ""
            self.Items = _FakeItems()

    class RibbonButton(object):
        def __init__(self):
            self.Id = ""
            self.Text = ""

    class RibbonSeparator(object):
        def __init__(self):
            self.Id = ""

    class RibbonPanelBreak(object):
        def __init__(self):
            self.Id = ""

    class RibbonItemSize(object):
        Standard = "standard-size"
        Large = "large-size"


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

    modify_panel = _FakePanel("Modify%Select", "Select")
    modify_panel.Source.Items.append(_FakeItem("ID_BUTTON_SELECT", "Modify"))
    modify_tab = _FakeTab("Modify", panels=[modify_panel])

    native_dynamo = _FakeItem("CustomCtrl_%Manage%Visual Programming%Dynamo", "Dynamo")
    native_dynamo.Image = "dynamo-small-image"
    native_dynamo.LargeImage = "dynamo-large-image"
    manage_panel = _FakePanel("CustomCtrl_%Manage%Visual Programming", "Visual Programming")
    manage_panel.Source.Items.extend([
        native_dynamo, _FakeItem("CustomCtrl_%Manage%Visual Programming%Dynamo Player", "Dynamo Player")])
    manage_tab = _FakeTab("Manage", panels=[manage_panel])

    ribbon = _FakeRibbon([easybim_tab, foo_tab, pyrevit_tab, modify_tab, manage_tab])
    return ribbon, {
        "easybim_tab": easybim_tab, "easybim_misc": easybim_misc,
        "foo_tab": foo_tab, "bar_panel": bar_panel, "baz": baz, "tools": tools,
        "child_a": child_a, "child_b": child_b, "stack": stack,
        "stacked_one": stacked_one, "stacked_two": stacked_two,
        "pyrevit_tab": pyrevit_tab, "modify_tab": modify_tab, "manage_tab": manage_tab,
        "native_dynamo": native_dynamo,
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

    def test_hidden_tabs_are_read_and_unioned_with_legacy_hide_tab(self):
        registry = self.mod.read_registry({
            "format": 1,
            "hidden_tabs": ["Systems", "foo", 3, ""],
            "sources": [{"id": "s1", "kind": "git", "ext_name": "Foo", "tab_names": ["Foo", "Foo Extra"],
                         "hide_tab": True},
                        {"id": "s2", "kind": "installed", "ext_name": "Bar", "tab_names": ["Bar"],
                         "hide_tab": False}]})
        self.assertEqual(registry["hidden_tabs"], ["Systems", "foo", "3", "Foo Extra"])
        old_file = self.mod.read_registry({"format": 1, "sources": [
            {"id": "s1", "kind": "git", "ext_name": "Foo", "tab_names": ["Foo"], "hide_tab": True}]})
        self.assertEqual(old_file["hidden_tabs"], ["Foo"])
        self.assertEqual(self.mod.empty_registry()["hidden_tabs"], [])
        self.assertTrue(self.mod.registry_has_work({"placements": [], "hidden_tabs": ["X"]}))

    def test_dynamo_and_ribbon_sources_keep_their_fields(self):
        registry = self.mod.read_registry({
            "format": 1,
            "sources": [{"id": "s1", "kind": "dynamo", "path": "P:/g.dyn", "title": "Graph", "bundle": "Graph.pushbutton",
                         "icon": "", "ext_name": "EasyBIM_MyRibbon", "tab_names": ["My Ribbon Library"],
                         "installed_by_my_ribbon": True},
                        {"id": "s2", "kind": "ribbon", "ext_name": "Annotate", "tab_names": ["Annotate"]},
                        {"id": "s3", "kind": "weird"}]})
        dyn = registry["sources"][0]
        self.assertEqual((dyn["kind"], dyn["path"], dyn["title"], dyn["bundle"], dyn["icon"]),
                         ("dynamo", "P:/g.dyn", "Graph", "Graph.pushbutton", None))
        self.assertEqual(registry["sources"][1]["kind"], "ribbon")
        self.assertEqual(registry["sources"][2]["kind"], "git")

    def test_dynamo_sources_are_always_ours_and_bad_bundle_names_are_dropped(self):
        registry = self.mod.read_registry({
            "format": 1,
            "sources": [{"id": "s1", "kind": "dynamo", "path": "P:/g.dyn", "bundle": "C:evil.pushbutton",
                         "installed_by_my_ribbon": False},
                        {"id": "s2", "kind": "dynamo", "path": "P:/h.dyn", "bundle": "H.pushbutton"}]})
        self.assertEqual(registry["sources"][0]["bundle"], "")
        self.assertTrue(registry["sources"][0]["installed_by_my_ribbon"])
        self.assertEqual(registry["sources"][1]["bundle"], "H.pushbutton")
        self.assertTrue(self.mod.is_bundle_folder_name("A b.pushbutton"))
        for bad in ("a/b.pushbutton", "C:x.pushbutton", "..", "x"):
            self.assertFalse(self.mod.is_bundle_folder_name(bad), bad)

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

    def test_hides_requested_tabs_including_pyrevit_but_never_easybim_modify_or_a_destination(self):
        registry = _registry()
        registry["hidden_tabs"] = ["Modify", "EasyBIM"]
        report = self._apply(registry)
        self.assertFalse(self.parts["foo_tab"].IsVisible)
        self.assertFalse(self.parts["pyrevit_tab"].IsVisible)
        self.assertTrue(self.parts["easybim_tab"].IsVisible)
        self.assertTrue(self.parts["modify_tab"].IsVisible)
        self.assertEqual(sorted(report["hidden_tabs"]), ["Foo", "pyRevit"])

    def test_hidden_tabs_list_hides_tabs_no_source_owns(self):
        registry = _registry()
        registry["sources"] = []
        registry["placements"] = []
        registry["hidden_tabs"] = ["Manage"]
        report = self._apply(registry)
        self.assertFalse(self.parts["manage_tab"].IsVisible)
        self.assertEqual(report["hidden_tabs"], ["Manage"])
        # and un-hiding is taking it out of the list
        registry["hidden_tabs"] = []
        report = self._apply(registry)
        self.assertTrue(self.parts["manage_tab"].IsVisible)
        self.assertEqual(report["shown_tabs"], ["Manage"])

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
        self.assertTrue(self.parts["pyrevit_tab"].IsVisible)
        self.assertEqual(sorted(report["shown_tabs"]), ["Foo", "pyRevit"])
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
        self.assertEqual(len(self.ribbon.Tabs), 5)

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


class LiveTabTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.ribbon, self.parts = _build_ribbon()

    def _live_tab(self):
        # AdWindows class names decide what can be shared, so the fakes carry them
        class RibbonButton(_FakeItem):
            pass

        class RibbonSplitButton(_FakeItem):
            pass

        class RibbonGallery(_FakeItem):
            pass

        class RibbonSeparator(_FakeItem):
            pass

        wall = RibbonButton("ID_OBJECTS_WALL", "Wall")
        arch = RibbonButton("ID_OBJECTS_WALL_ARCH", "Wall: Architectural")
        struct = RibbonButton("ID_OBJECTS_WALL_STRUCT", "Wall: Structural")
        wall_split = RibbonSplitButton("ID_SPLIT_WALL", "Wall", children=[arch, struct])
        one = RibbonButton("ID_ONE", "One")
        two = RibbonButton("ID_TWO", "")
        stack = RibbonRowPanel([one, two])
        gallery = RibbonGallery("ID_GALLERY", "Gallery")
        separator = RibbonSeparator("", "")
        panel = _FakePanel("Architecture%Build", "Build")
        panel.Source.Items.extend([wall, wall_split, stack, gallery, separator])
        empty = _FakePanel("Architecture%Empty", "Empty")
        return _FakeTab("Architecture", panels=[panel, empty]), {
            "wall": wall, "arch": arch, "struct": struct, "wall_split": wall_split,
            "one": one, "two": two, "gallery": gallery}

    def test_describe_ribbon_tab_reads_items_groups_stacks_and_refuses_galleries(self):
        tab, parts = self._live_tab()
        described = self.mod.describe_ribbon_tab(tab)
        self.assertEqual(described["name"], "Architecture")
        self.assertEqual(described["tab_names"], ["Architecture"])
        self.assertTrue(described["live"])
        self.assertEqual([b["name"] for b in described["buttons"]],
                         ["ID_OBJECTS_WALL", "ID_SPLIT_WALL", "ID_OBJECTS_WALL_ARCH",
                          "ID_OBJECTS_WALL_STRUCT", "ID_ONE", "ID_TWO", "ID_GALLERY"])
        by_name = dict((b["name"], b) for b in described["buttons"])
        wall = by_name["ID_OBJECTS_WALL"]
        self.assertEqual(wall["kind"], "button")
        self.assertEqual(wall["control_id"], "ID_OBJECTS_WALL")
        self.assertEqual(wall["path"], [{"name": "Architecture", "title": "Architecture"},
                                        {"name": "Architecture%Build", "title": "Build"},
                                        {"name": "ID_OBJECTS_WALL", "title": "Wall"}])
        self.assertEqual(by_name["ID_SPLIT_WALL"]["kind"], "pulldown")
        self.assertEqual([c["title"] for c in by_name["ID_SPLIT_WALL"]["children"]],
                         ["Wall: Architectural", "Wall: Structural"])
        self.assertEqual(by_name["ID_OBJECTS_WALL_ARCH"]["path"][-2]["name"], "ID_SPLIT_WALL")
        # stack children sit flat on the panel; an untitled button shows its Id tail
        self.assertEqual(by_name["ID_ONE"]["path"][1]["title"], "Build")
        self.assertEqual(by_name["ID_TWO"]["title"], "ID_TWO")
        # the gallery is refused by kind, the separator is skipped, the empty panel dropped
        self.assertEqual(by_name["ID_GALLERY"]["kind"], "ribbon-ribbongallery")
        panels = described["tabs"][0]["panels"]
        self.assertEqual([p["title"] for p in panels], ["Build"])
        self.assertEqual([i["name"] for i in panels[0]["items"]],
                         ["ID_OBJECTS_WALL", "ID_SPLIT_WALL", "ID_ONE", "ID_TWO", "ID_GALLERY"])

    def test_describe_ribbon_tab_carries_live_images_and_tooltips(self):
        class RibbonToolTip(object):
            Title = "Wall"
            Content = "Draw a wall"
        tab, parts = self._live_tab()
        parts["wall"].Image = "small"
        parts["wall"].LargeImage = "large"
        parts["wall"].ToolTip = RibbonToolTip()
        parts["arch"].ToolTip = "  plain   text "
        described = self.mod.describe_ribbon_tab(tab)
        by_name = dict((b["name"], b) for b in described["buttons"])
        self.assertEqual(by_name["ID_OBJECTS_WALL"]["icon_source"], "small")
        self.assertIsNone(by_name["ID_OBJECTS_WALL"]["icon"])
        self.assertEqual(by_name["ID_OBJECTS_WALL"]["tooltip"], "Wall - Draw a wall")
        self.assertEqual(by_name["ID_OBJECTS_WALL_ARCH"]["tooltip"], "plain text")

    def test_list_ribbon_reports_visibility_and_contextual_flags(self):
        self.parts["foo_tab"].IsVisible = False
        self.parts["manage_tab"].IsContextualTab = True
        summary = dict((t["title"], t) for t in self.mod.list_ribbon(self.ribbon))
        self.assertFalse(summary["Foo"]["is_visible"])
        self.assertTrue(summary["EasyBIM"]["is_visible"])
        self.assertTrue(summary["Manage"]["is_contextual"])
        self.assertFalse(summary["Foo"]["is_contextual"])

    def test_find_native_dynamo_button_skips_dynamo_player_our_panels_and_placed_graphs(self):
        native = self.mod.find_native_dynamo_button(self.ribbon)
        self.assertIs(native, self.parts["native_dynamo"])
        # a placed graph titled "Dynamo" on an ordinary panel is never mistaken for Revit's button
        impostor = _FakeItem("CustomCtrl_%CustomCtrl_%My Ribbon Library%Dynamo%Dynamo", "Dynamo")
        self.parts["easybim_misc"].Source.Items.insert(0, impostor)
        self.assertIs(self.mod.find_native_dynamo_button(self.ribbon, exclude=[impostor]),
                      self.parts["native_dynamo"])
        # Dynamo's own panel wins over a title match elsewhere even without the exclusion
        self.assertIs(self.mod.find_native_dynamo_button(self.ribbon), self.parts["native_dynamo"])
        self.parts["manage_tab"].Panels.clear()
        self.assertIsNone(self.mod.find_native_dynamo_button(self.ribbon, exclude=[impostor]))

    def test_live_items_are_classified_by_their_base_types_and_children_are_not_doubled(self):
        class RibbonButton(_FakeItem):
            pass

        class MySpecialButton(RibbonButton):
            pass

        class RibbonSplitButton(_FakeItem):
            def GetItems(self):
                return list(self.Items)

        special = MySpecialButton("ID_SPECIAL", "Special")
        child = RibbonButton("ID_CHILD", "Child")
        split = RibbonSplitButton("ID_SPLIT", "Split", children=[child])
        panel = _FakePanel("T%P", "P")
        panel.Source.Items.extend([special, split])
        described = self.mod.describe_ribbon_tab(_FakeTab("T", panels=[panel]))
        by_name = dict((b["name"], b) for b in described["buttons"])
        self.assertEqual(by_name["ID_SPECIAL"]["kind"], "button")
        self.assertEqual([c["name"] for c in by_name["ID_SPLIT"]["children"]], ["ID_CHILD"])


class DynamoIconTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.env = _EnvStore(self.mod)
        self.ribbon, self.parts = _build_ribbon()
        library_panel = _FakePanel("CustomCtrl_%My Ribbon Library%Dynamo", "Dynamo")
        self.graph = _FakeItem("CustomCtrl_%CustomCtrl_%My Ribbon Library%Dynamo%Renumber", "Renumber")
        self.graph.Image = "drawn-small"
        self.graph.LargeImage = "drawn-large"
        self.custom = _FakeItem("CustomCtrl_%CustomCtrl_%My Ribbon Library%Dynamo%Custom", "Custom")
        self.custom.LargeImage = "user-large"
        library_panel.Source.Items.extend([self.graph, self.custom])
        self.ribbon.Tabs.append(_FakeTab("My Ribbon Library", panels=[library_panel]))

    def _registry(self, icon=None):
        return self.mod.read_registry({
            "format": 1,
            "sources": [{"id": "s1", "kind": "dynamo", "path": "P:/r.dyn", "title": "Renumber",
                         "bundle": "Renumber.pushbutton", "icon": None, "ext_name": "EasyBIM_MyRibbon",
                         "tab_names": ["My Ribbon Library"], "installed_by_my_ribbon": True},
                        {"id": "s2", "kind": "dynamo", "path": "P:/c.dyn", "title": "Custom",
                         "bundle": "Custom.pushbutton", "icon": "C:/me.png", "ext_name": "EasyBIM_MyRibbon",
                         "tab_names": ["My Ribbon Library"], "installed_by_my_ribbon": True}],
            "destinations": [{"id": "d1", "tab": "EasyBIM", "panel": "My Tools"}],
            "placements": [
                {"id": "p1", "source": "s1", "dest": "d1", "order": 0, "kind": "button", "title": "Renumber",
                 "control_id": "CustomCtrl_%CustomCtrl_%My Ribbon Library%Dynamo%Renumber",
                 "path": [_level("My Ribbon Library"), _level("Dynamo"), _level("Renumber")]},
                {"id": "p2", "source": "s2", "dest": "d1", "order": 1, "kind": "button", "title": "Custom",
                 "control_id": "CustomCtrl_%CustomCtrl_%My Ribbon Library%Dynamo%Custom",
                 "path": [_level("My Ribbon Library"), _level("Dynamo"), _level("Custom")]}],
            "hidden_tabs": ["My Ribbon Library"]})

    def test_default_dynamo_buttons_get_revits_own_images_custom_ones_keep_theirs(self):
        report = self.mod.apply(self._registry(), ribbon=self.ribbon, autodesk_windows=_FakeAutodeskWindows())
        self.assertEqual(report["added"], ["p1", "p2"])
        self.assertEqual(self.graph.LargeImage, "dynamo-large-image")
        self.assertEqual(self.graph.Image, "dynamo-small-image")
        self.assertEqual(self.custom.LargeImage, "user-large")
        self.assertEqual(report["dynamo_icons"], ["p1"])
        # the library tab is hidden as requested
        library = [t for t in self.ribbon.Tabs if t.Title == "My Ribbon Library"][0]
        self.assertFalse(library.IsVisible)

    def test_without_dynamo_installed_the_drawn_icon_stays(self):
        self.parts["manage_tab"].Panels.clear()
        report = self.mod.apply(self._registry(), ribbon=self.ribbon, autodesk_windows=_FakeAutodeskWindows())
        self.assertEqual(self.graph.LargeImage, "drawn-large")
        self.assertEqual(report["dynamo_icons"], [])


class ListRibbonTests(unittest.TestCase):
    def test_lists_tabs_and_panels_and_marks_ours(self):
        mod = _load_module()
        _EnvStore(mod)
        ribbon, parts = _build_ribbon()
        registry = mod.read_registry(_registry())
        mod.apply(registry, ribbon=ribbon, autodesk_windows=_FakeAutodeskWindows())
        summary = mod.list_ribbon(ribbon)
        titles = [t["title"] for t in summary]
        self.assertEqual(titles, ["EasyBIM", "Foo", "pyRevit", "Modify", "Manage", "MEP Kit"])
        easybim = summary[0]
        self.assertEqual([p["title"] for p in easybim["panels"]], ["Misc Tools", "My Tools"])
        self.assertFalse(easybim["is_ours"])
        self.assertFalse(easybim["panels"][0]["is_ours"])
        self.assertTrue(easybim["panels"][1]["is_ours"])
        self.assertTrue(summary[5]["is_ours"])
        self.assertEqual(mod.list_ribbon(None) if mod._get_default_ribbon() is None else [], [])


class StackApplyTests(unittest.TestCase):
    """Stacked rows of small clones, separators and the slide-out fold."""

    def setUp(self):
        self.mod = _load_module()
        self.env = _EnvStore(self.mod)
        self.ribbon, self.parts = _build_ribbon()
        self.aw = _FakeAutodeskWindows()

    def _apply(self, registry):
        return self.mod.apply(registry, ribbon=self.ribbon, autodesk_windows=self.aw)

    def _stack_registry(self, members=2):
        placements = [
            {"id": "p_one", "source": "s1", "dest": "d1", "order": 0, "kind": "button",
             "title": "Baz Tool", "control_id": "CustomCtrl_%CustomCtrl_%Foo%Bar%Baz",
             "path": [_level("Foo"), _level("Bar"), _level("Baz", "Baz Tool")],
             "stack": "k1"},
            {"id": "p_two", "source": "s1", "dest": "d1", "order": 1, "kind": "button",
             "title": "Two", "control_id": "",
             "path": [_level("Foo"), _level("Bar"), _level("Two")],
             "stack": "k1"},
        ][:members]
        return {
            "format": 1,
            "sources": [{"id": "s1", "kind": "git", "url": "https://github.com/o/foo",
                         "ext_name": "Foo", "tab_names": ["Foo"]}],
            "destinations": [
                {"id": "d1", "tab": "EasyBIM", "panel": "My Tools", "own_tab": False}],
            "placements": placements,
        }

    def _my_tools(self):
        for panel in self.parts["easybim_tab"].Panels:
            if panel.Source.Title == "My Tools":
                return panel
        return None

    def test_a_stack_becomes_one_row_of_small_clones(self):
        handler = object()
        self.parts["baz"].CommandHandler = handler
        report = self._apply(self.mod.read_registry(self._stack_registry()))
        self.assertEqual(report["missing"], [])
        self.assertEqual(sorted(report["added"]), ["p_one", "p_two"])
        panel = self._my_tools()
        self.assertEqual(len(panel.Source.Items), 1)
        row = panel.Source.Items[0]
        self.assertTrue(row.Id.startswith(self.mod.ID_PREFIX + "row_"))
        self.assertEqual(len(row.Items), 2)
        clone = row.Items[0]
        self.assertIsNot(clone, self.parts["baz"])
        self.assertEqual(clone.Text, "Baz Tool")
        self.assertIs(clone.CommandHandler, handler)
        self.assertEqual(clone.Size, self.aw.RibbonItemSize.Standard)
        self.assertTrue(clone.Id.startswith(self.mod.ID_PREFIX))
        self.assertTrue(clone.ShowText)

    def test_the_shared_original_is_never_touched(self):
        self._apply(self.mod.read_registry(self._stack_registry()))
        baz = self.parts["baz"]
        self.assertFalse(hasattr(baz, "Size"))
        self.assertFalse(hasattr(baz, "ShowText"))
        self.assertEqual(baz.Text, "Baz Tool")
        # still exactly once on its home panel, nowhere else as itself
        home = [i for i in self.parts["bar_panel"].Source.Items if i is baz]
        self.assertEqual(len(home), 1)
        panel = self._my_tools()
        self.assertFalse(any(i is baz for i in panel.Source.Items))
        self.assertFalse(any(i is baz for i in panel.Source.Items[0].Items))

    def test_apply_twice_leaves_one_row(self):
        registry = self.mod.read_registry(self._stack_registry())
        self._apply(registry)
        self._apply(registry)
        panel = self._my_tools()
        self.assertEqual(len(panel.Source.Items), 1)
        self.assertEqual(len(panel.Source.Items[0].Items), 2)

    def test_a_missing_member_still_builds_the_row(self):
        raw = self._stack_registry()
        raw["placements"][1]["path"] = [_level("Foo"), _level("Bar"), _level("Gone")]
        report = self._apply(self.mod.read_registry(raw))
        self.assertEqual(report["added"], ["p_one"])
        self.assertEqual(len(report["missing"]), 1)
        panel = self._my_tools()
        self.assertEqual(len(panel.Source.Items), 1)
        self.assertEqual(len(panel.Source.Items[0].Items), 1)

    def test_all_members_missing_leaves_no_row(self):
        raw = self._stack_registry()
        for placement in raw["placements"]:
            placement["control_id"] = ""
            placement["path"] = [_level("Foo"), _level("Bar"), _level("Gone")]
        report = self._apply(self.mod.read_registry(raw))
        self.assertEqual(report["added"], [])
        self.assertEqual(len(report["missing"]), 2)
        self.assertEqual(len(self._my_tools().Source.Items), 0)

    def test_a_stack_of_one_places_the_shared_object_flat(self):
        report = self._apply(self.mod.read_registry(self._stack_registry(members=1)))
        self.assertEqual(report["added"], ["p_one"])
        panel = self._my_tools()
        self.assertEqual(len(panel.Source.Items), 1)
        self.assertIs(panel.Source.Items[0], self.parts["baz"])

    def test_markers_render_as_our_objects_and_stay_single(self):
        raw = self._stack_registry()
        for placement in raw["placements"]:
            placement["stack"] = ""
        raw["placements"].append(
            {"id": "p_sep", "source": "", "dest": "d1", "order": 2,
             "kind": "separator", "title": "", "control_id": "", "path": []})
        raw["placements"].append(
            {"id": "p_fold", "source": "", "dest": "d1", "order": 3,
             "kind": "slideout", "title": "", "control_id": "", "path": []})
        registry = self.mod.read_registry(raw)
        self.assertEqual(len(registry["placements"]), 4)  # markers survive the read
        report = self._apply(registry)
        self.assertIn("p_sep", report["added"])
        self.assertIn("p_fold", report["added"])
        panel = self._my_tools()
        self.assertEqual(len(panel.Source.Items), 4)
        self.assertTrue(panel.Source.Items[2].Id.startswith(self.mod.ID_PREFIX + "sep_"))
        self.assertTrue(panel.Source.Items[3].Id.startswith(self.mod.ID_PREFIX + "fold_"))
        self._apply(registry)
        self.assertEqual(len(self._my_tools().Source.Items), 4)

    def test_an_empty_registry_takes_rows_and_markers_back(self):
        raw = self._stack_registry()
        raw["placements"].append(
            {"id": "p_sep", "source": "", "dest": "d1", "order": 2,
             "kind": "separator", "title": "", "control_id": "", "path": []})
        self._apply(self.mod.read_registry(raw))
        self._apply(self.mod.empty_registry())
        # the emptied panel was ours, so the take-back drops it whole
        self.assertIsNone(self._my_tools())
        home = [i for i in self.parts["bar_panel"].Source.Items if i is self.parts["baz"]]
        self.assertEqual(len(home), 1)

    def test_dynamo_icons_land_on_the_clone_not_the_original(self):
        raw = self._stack_registry()
        raw["sources"].append({"id": "s9", "kind": "dynamo", "ext_name": "EasyBIM_MyRibbon",
                               "path": "C:\\graphs\\g.dyn", "title": "One",
                               "installed_by_my_ribbon": True})
        raw["placements"][1] = {
            "id": "p_two", "source": "s9", "dest": "d1", "order": 1, "kind": "button",
            "title": "One", "control_id": "",
            "path": [_level("Foo"), _level("Bar"), _level("One")], "stack": "k1"}
        report = self._apply(self.mod.read_registry(raw))
        self.assertIn("p_two", report["dynamo_icons"])
        row = self._my_tools().Source.Items[0]
        clone = row.Items[1]
        self.assertEqual(clone.Image, "dynamo-small-image")
        self.assertIsNone(getattr(self.parts["stacked_one"], "Image", None))

    def test_our_rows_never_pass_for_a_source(self):
        ours = _FakeAutodeskWindows.RibbonRowPanel()
        ours.Id = self.mod.ID_PREFIX + "row_k1"
        clone = _FakeAutodeskWindows.RibbonButton()
        clone.Text = "Baz Tool"
        ours.Items.Add(clone)
        items = _FakeItems([ours])
        self.assertIsNone(self.mod._find_item_by_aliases(items, ["Baz Tool"]))
        theirs = RibbonRowPanel([_FakeItem("X", "Baz Tool")])
        self.assertIsNotNone(
            self.mod._find_item_by_aliases(_FakeItems([theirs]), ["Baz Tool"]))

    def test_the_live_reader_skips_our_objects(self):
        self._apply(self.mod.read_registry(self._stack_registry()))
        described = self.mod.describe_ribbon_tab(self.parts["easybim_tab"])
        titles = [b["title"] for b in described["buttons"]]
        self.assertNotIn("Baz Tool", titles)  # the clone stays invisible
        self.assertIn("Slope", titles)


if __name__ == "__main__":
    unittest.main()
