"""My Ribbon pure logic: git links, folder names, staged registry edits,
picker tags, and the import/export planner.  Desktop Python only."""
import importlib.util
import pathlib
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "General.panel"
    / "My Ribbon.pushbutton"
)
STATE_MODULE_PATH = COMMAND_DIR / "my_ribbon_state.py"


def _load_state():
    spec = importlib.util.spec_from_file_location("my_ribbon_state", str(STATE_MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _level(name, title=None):
    return {"name": name, "title": title or name}


def _button(name, title=None, kind="button", control_id="", tab="Foo", panel="Bar",
            container=None, **extra):
    path = [_level(tab), _level(panel)]
    if container:
        path.append(_level(container))
    path.append(_level(name, title))
    row = {"kind": kind, "title": title or name, "control_id": control_id, "path": path}
    row.update(extra)
    return row


class GitLinkTests(unittest.TestCase):
    def setUp(self):
        self.state = _load_state()

    def _ok(self, text):
        ref, error = self.state.parse_git_url(text)
        self.assertIsNone(error, "{0!r} -> {1}".format(text, error))
        return ref

    def test_plain_github_links(self):
        for text in ("https://github.com/Owner/Repo",
                     "https://github.com/Owner/Repo/",
                     "https://github.com/Owner/Repo.git",
                     "http://www.github.com/Owner/Repo",
                     "github.com/Owner/Repo",
                     "Owner/Repo"):
            ref = self._ok(text)
            self.assertEqual(ref.host, "github.com")
            self.assertEqual(ref.owner, "Owner")
            self.assertEqual(ref.repo, "Repo")
            self.assertIsNone(ref.branch)
            self.assertEqual(ref.clone_url, "https://github.com/Owner/Repo.git")
            self.assertEqual(ref.key, "github.com/owner/repo")
            self.assertEqual(ref.label, "Owner/Repo")

    def test_tree_and_blob_links_carry_branch_and_subpath(self):
        ref = self._ok("https://github.com/o/r/tree/develop/extensions/Foo.extension")
        self.assertEqual((ref.owner, ref.repo), ("o", "r"))
        self.assertEqual(ref.branch, "develop")
        self.assertEqual(ref.subpath, "extensions/Foo.extension")
        self.assertEqual(ref.key, "github.com/o/r@develop")
        ref = self._ok("https://github.com/o/r/blob/main/README.md")
        self.assertEqual(ref.branch, "main")
        self.assertEqual(ref.subpath, "README.md")
        ref = self._ok("https://github.com/o/r/tree/main")
        self.assertEqual(ref.branch, "main")
        self.assertIsNone(ref.subpath)

    def test_a_repository_named_tree_or_src_is_not_mistaken_for_a_marker(self):
        ref = self._ok("https://github.com/o/tree")
        self.assertEqual(ref.repo, "tree")
        ref = self._ok("https://github.com/o/src")
        self.assertEqual(ref.repo, "src")

    def test_ssh_forms_become_https(self):
        for text in ("git@github.com:o/r.git", "git@github.com:o/r",
                     "ssh://git@github.com/o/r.git", "git://github.com/o/r"):
            ref = self._ok(text)
            self.assertEqual(ref.clone_url, "https://github.com/o/r.git")

    def test_other_hosts(self):
        ref = self._ok("https://gitlab.com/group/sub/repo/-/tree/main/x")
        self.assertEqual(ref.host, "gitlab.com")
        self.assertEqual(ref.path_parts, ["group", "sub", "repo"])
        self.assertEqual(ref.branch, "main")
        ref = self._ok("https://bitbucket.org/o/r/src/master/")
        self.assertEqual((ref.repo, ref.branch), ("r", "master"))
        ref = self._ok("https://dev.azure.com/org/project/_git/repo")
        self.assertEqual(ref.repo, "repo")
        self.assertEqual(ref.owner, "project")
        ref = self._ok("https://user:token@git.example.com/team/tools.git")
        self.assertEqual(ref.clone_url, "https://git.example.com/team/tools.git")

    def test_bad_input(self):
        for text, fragment in (("", "Paste a link"),
                               ("hello world", "spaces"),
                               ("ftp://x.y/z", "https://"),
                               ("https://github.com", "server, not"),
                               ("https://github.com/", "server, not"),
                               ("C:/tools/repo", "local path"),
                               ("C:\\tools\\repo", "not a web link"),
                               ("just-text", "not a web link")):
            ref, error = self.state.parse_git_url(text)
            self.assertIsNone(ref, text)
            self.assertIn(fragment, error, text)


class ExtensionNameTests(unittest.TestCase):
    def setUp(self):
        self.state = _load_state()

    def test_repo_name_is_used_without_manifest(self):
        ref, _ = self.state.parse_git_url("https://github.com/o/MyTools")
        self.assertEqual(self.state.extension_dir_name(ref, []), "MyTools.extension")

    def test_manifest_name_wins_and_double_suffix_is_avoided(self):
        ref, _ = self.state.parse_git_url("https://github.com/o/MyTools.extension")
        self.assertEqual(self.state.extension_dir_name(ref, [], manifest_name="Nice Tools"),
                         "Nice Tools.extension")
        self.assertEqual(self.state.extension_dir_name(ref, []), "MyTools.extension")

    def test_clash_appends_owner_then_counter(self):
        ref, _ = self.state.parse_git_url("https://github.com/o/MyTools")
        self.assertEqual(self.state.extension_dir_name(ref, ["mytools.extension"]),
                         "MyTools (o).extension")
        self.assertEqual(
            self.state.extension_dir_name(ref, ["MyTools.extension", "MyTools (o).extension"]),
            "MyTools (o) 2.extension")

    def test_folder_names_are_windows_safe(self):
        self.assertEqual(self.state.sanitize_folder_name('a<b>:c"d/e\\f|g?h*i. '), "a_b__c_d_e_f_g_h_i")
        self.assertEqual(self.state.sanitize_folder_name("   "), "Extension")


class StagedRegistryTests(unittest.TestCase):
    def setUp(self):
        self.state = _load_state()
        self.registry = {"format": 1, "sources": [], "destinations": [], "placements": []}

    def _source(self, url="https://github.com/o/r", **extra):
        row = {"kind": "git", "url": url, "ext_name": "r", "label": "o/r",
               "tab_names": ["Foo"], "installed_by_my_ribbon": True, "hide_tab": True}
        row.update(extra)
        return row

    def test_add_source_dedupes_by_normalised_link(self):
        first = self.state.add_source(self.registry, self._source())
        again = self.state.add_source(self.registry, self._source(url="https://www.github.com/O/R.git"))
        self.assertIs(first, again)
        self.assertEqual(first["id"], "s1")
        other = self.state.add_source(self.registry, self._source(branch="dev"))
        self.assertEqual(other["id"], "s2")
        installed = self.state.add_source(self.registry, {"kind": "installed", "ext_name": "EasyBIM"})
        self.assertEqual(installed["id"], "s3")
        self.assertIs(self.state.add_source(self.registry, {"kind": "installed", "ext_name": "easybim"}),
                      installed)

    def test_two_extensions_of_one_repository_are_two_sources(self):
        # a monorepo: same URL, different extension folders
        a = self.state.add_source(self.registry, self._source(ext_name="A", label="o/r (A)",
                                                              extra_root="C:/repos/o__r/extensions"))
        b = self.state.add_source(self.registry, self._source(ext_name="B", label="o/r (B)",
                                                              extra_root="C:/repos/o__r/extensions"))
        self.assertIsNot(a, b)
        self.assertEqual(a["ext_name"], "A")
        self.assertEqual(b["ext_name"], "B")
        # ...while the same extension of the same repository is still one source
        self.assertIs(self.state.add_source(self.registry, self._source(ext_name="a", label="x")), a)

    def test_add_source_refreshes_tab_names_of_an_existing_source(self):
        first = self.state.add_source(self.registry, self._source())
        self.state.add_source(self.registry, self._source(tab_names=["Foo", "Foo Extra"]))
        self.assertEqual(first["tab_names"], ["Foo", "Foo Extra"])

    def test_placements_are_ordered_per_panel_and_moved_and_removed(self):
        source = self.state.add_source(self.registry, self._source())
        dest = self.state.add_destination(self.registry, "EasyBIM", "My Tools")
        self.assertEqual(dest["id"], "d1")
        self.assertIs(self.state.add_destination(self.registry, "easybim", "my tools"), dest)
        a = self.state.add_placement(self.registry, source["id"], dest["id"], _button("A"))
        b = self.state.add_placement(self.registry, source["id"], dest["id"], _button("B"))
        c = self.state.add_placement(self.registry, source["id"], dest["id"], _button("C"))
        self.assertEqual([p["id"] for p in self.state.placements_in(self.registry, "d1")],
                         [a["id"], b["id"], c["id"]])
        self.assertIs(self.state.add_placement(self.registry, source["id"], dest["id"], _button("A")), a)

        self.assertTrue(self.state.move_placement(self.registry, c["id"], -1))
        self.assertEqual([p["title"] for p in self.state.placements_in(self.registry, "d1")],
                         ["A", "C", "B"])
        self.assertFalse(self.state.move_placement(self.registry, a["id"], -1))
        self.assertTrue(self.state.remove_placement(self.registry, c["id"]))
        rows = self.state.placements_in(self.registry, "d1")
        self.assertEqual([(p["title"], p["order"]) for p in rows], [("A", 0), ("B", 1)])

        other = self.state.add_destination(self.registry, "MEP Kit", "Sheets", own_tab=True)
        self.assertTrue(self.state.move_placement_to(self.registry, a["id"], other["id"]))
        self.assertEqual([p["title"] for p in self.state.placements_in(self.registry, other["id"])], ["A"])
        self.assertEqual([p["order"] for p in self.state.placements_in(self.registry, "d1")], [0])

    def test_removing_a_source_or_destination_drops_its_placements(self):
        source = self.state.add_source(self.registry, self._source())
        keep = self.state.add_source(self.registry, {"kind": "installed", "ext_name": "EasyBIM"})
        dest = self.state.add_destination(self.registry, "EasyBIM", "My Tools")
        self.state.add_placement(self.registry, source["id"], dest["id"], _button("A"))
        kept = self.state.add_placement(self.registry, keep["id"], dest["id"], _button("Slope", tab="EasyBIM"))
        removed = self.state.remove_source(self.registry, source["id"])
        self.assertEqual([p["title"] for p in removed], ["A"])
        self.assertEqual([p["id"] for p in self.registry["placements"]], [kept["id"]])
        self.assertEqual(kept["order"], 0)
        removed = self.state.remove_destination(self.registry, dest["id"])
        self.assertEqual([p["id"] for p in removed], [kept["id"]])
        self.assertEqual(self.registry["destinations"], [])
        self.assertEqual(self.registry["placements"], [])

    def test_hide_tab_and_rename_destination(self):
        source = self.state.add_source(self.registry, self._source(hide_tab=False))
        self.state.set_hide_tab(self.registry, source["id"], True)
        self.assertTrue(source["hide_tab"])
        dest = self.state.add_destination(self.registry, "EasyBIM", "My Tools")
        self.state.rename_destination(self.registry, dest["id"], panel="Favourites")
        self.assertEqual((dest["tab"], dest["panel"]), ("EasyBIM", "Favourites"))
        self.state.rename_destination(self.registry, dest["id"], panel="   ")
        self.assertEqual(dest["panel"], "Favourites")

    def test_change_count_and_status_line(self):
        saved = {"format": 1, "sources": [], "destinations": [], "placements": []}
        source = self.state.add_source(self.registry, self._source())
        dest = self.state.add_destination(self.registry, "EasyBIM", "My Tools")
        self.state.add_placement(self.registry, source["id"], dest["id"], _button("A"))
        # source + destination + placement + the tab it hides
        self.assertEqual(self.registry["hidden_tabs"], ["Foo"])
        self.assertEqual(self.state.count_changes(saved, self.registry), 4)
        self.assertEqual(self.state.status_line(self.registry, 3),
                         u"1 source · 1 button · 3 changes not applied")
        self.assertEqual(self.state.status_line(self.registry, 0), u"1 source · 1 button")
        self.assertEqual(self.state.count_changes(self.registry, self.registry), 0)


class TabVisibilityStateTests(unittest.TestCase):
    def setUp(self):
        self.state = _load_state()
        self.registry = {"format": 1, "sources": [], "destinations": [], "placements": [],
                         "hidden_tabs": []}

    def _source(self, **extra):
        row = {"kind": "git", "url": "https://github.com/o/r", "ext_name": "r", "label": "o/r",
               "tab_names": ["Foo", "Foo Extra"], "installed_by_my_ribbon": True, "hide_tab": False}
        row.update(extra)
        return row

    def test_set_and_replace_hidden_tabs_ignore_case_and_keep_order(self):
        self.state.set_tabs_hidden(self.registry, ["Systems", "Manage"], True)
        self.state.set_tabs_hidden(self.registry, ["systems"], True)
        self.assertEqual(self.registry["hidden_tabs"], ["Systems", "Manage"])
        self.assertTrue(self.state.is_tab_hidden(self.registry, "SYSTEMS"))
        self.state.set_tabs_hidden(self.registry, ["MANAGE", "Nope"], False)
        self.assertEqual(self.registry["hidden_tabs"], ["Systems"])
        self.state.replace_hidden_tabs(self.registry, ["A", "a", "B"])
        self.assertEqual(self.registry["hidden_tabs"], ["A", "B"])

    def test_per_source_flag_and_the_list_stay_in_step(self):
        source = self.state.add_source(self.registry, self._source())
        self.assertEqual(self.registry["hidden_tabs"], [])
        self.state.set_hide_tab(self.registry, source["id"], True)
        self.assertEqual(self.registry["hidden_tabs"], ["Foo", "Foo Extra"])
        # un-hiding one of its tabs in the Show/Hide window clears the shortcut flag
        self.state.replace_hidden_tabs(self.registry, ["Foo"])
        self.assertFalse(source["hide_tab"])
        self.state.replace_hidden_tabs(self.registry, ["Foo", "Foo Extra", "Other"])
        self.assertTrue(source["hide_tab"])
        # a source added with hide_tab on hides its tabs straight away
        other = self.state.add_source(self.registry, self._source(url="https://github.com/x/y", ext_name="y",
                                                                  tab_names=["Y"], hide_tab=True))
        self.assertIn("Y", self.registry["hidden_tabs"])
        self.assertTrue(other["hide_tab"])

    def test_unticking_one_source_keeps_a_tab_another_source_still_hides(self):
        a = self.state.add_source(self.registry, self._source(hide_tab=True))
        b = self.state.add_source(self.registry, self._source(url="https://github.com/x/y", ext_name="y",
                                                              tab_names=["Foo Extra", "Y"], hide_tab=True))
        self.state.set_hide_tab(self.registry, a["id"], False)
        self.assertFalse(a["hide_tab"])
        self.assertEqual(self.registry["hidden_tabs"], ["Foo Extra", "Y"])
        self.state.set_hide_tab(self.registry, b["id"], False)
        self.assertEqual(self.registry["hidden_tabs"], [])

    def test_removing_a_source_unhides_tabs_it_hid_unless_another_still_does(self):
        a = self.state.add_source(self.registry, self._source(hide_tab=True))
        b = self.state.add_source(self.registry, self._source(url="https://github.com/x/y", ext_name="y",
                                                              tab_names=["Foo Extra"], hide_tab=True))
        self.state.set_tabs_hidden(self.registry, ["Manage"], True)
        self.state.remove_source(self.registry, a["id"])
        self.assertEqual(self.registry["hidden_tabs"], ["Foo Extra", "Manage"])
        self.state.remove_source(self.registry, b["id"])
        self.assertEqual(self.registry["hidden_tabs"], ["Manage"])

    def test_change_count_sees_hidden_tabs(self):
        saved = {"format": 1, "sources": [], "destinations": [], "placements": [], "hidden_tabs": ["A"]}
        working = {"format": 1, "sources": [], "destinations": [], "placements": [], "hidden_tabs": ["a", "B"]}
        self.assertEqual(self.state.count_changes(saved, working), 1)


class NewSourceKindTests(unittest.TestCase):
    def setUp(self):
        self.state = _load_state()
        self.registry = {"format": 1, "sources": [], "destinations": [], "placements": [],
                         "hidden_tabs": []}

    def test_ribbon_and_dynamo_keys(self):
        self.assertEqual(self.state.source_key({"kind": "ribbon", "ext_name": "Annotate"}), "rib:annotate")
        self.assertEqual(self.state.source_key({"kind": "dynamo", "path": "P:/Dyn//Graph.dyn"}),
                         "dyn:p:\\dyn\\graph.dyn")
        self.assertEqual(self.state.source_key({"kind": "dynamo", "path": "p:\\dyn\\graph.DYN\\"}),
                         "dyn:p:\\dyn\\graph.dyn")
        self.assertEqual(self.state.normalize_path("\\\\server\\share\\x.dyn"), "\\\\server\\share\\x.dyn")

    def test_imported_dynamo_sources_stay_deletable(self):
        current = {"format": 1, "sources": [], "destinations": [], "placements": [], "hidden_tabs": []}
        incoming = {"format": 1, "sources": [{"id": "s1", "kind": "dynamo", "path": "P:/g.dyn", "title": "G",
                                              "bundle": "G.pushbutton", "label": "G", "ext_name": "EasyBIM_MyRibbon",
                                              "tab_names": ["My Ribbon Library"], "installed_by_my_ribbon": False},
                                             {"id": "s2", "kind": "git", "url": "https://github.com/o/r",
                                              "ext_name": "r", "label": "o/r", "installed_by_my_ribbon": True}],
                    "destinations": [], "placements": [], "hidden_tabs": []}
        plan = self.state.plan_import(current, incoming, "merge")
        kinds = dict((s["kind"], s) for s in plan["result"]["sources"])
        self.assertTrue(kinds["dynamo"]["installed_by_my_ribbon"])
        self.assertFalse(kinds["git"]["installed_by_my_ribbon"])

    def test_dynamo_source_keeps_its_fields_and_dedupes_by_path(self):
        one = self.state.add_source(self.registry, {"kind": "dynamo", "path": "P:/a.dyn", "title": "A",
                                                    "bundle": "A.pushbutton", "icon": None,
                                                    "ext_name": "EasyBIM_MyRibbon",
                                                    "tab_names": ["My Ribbon Library"],
                                                    "installed_by_my_ribbon": True})
        self.assertEqual((one["path"], one["title"], one["bundle"], one["icon"], one["label"]),
                         ("P:/a.dyn", "A", "A.pushbutton", None, "A"))
        again = self.state.add_source(self.registry, {"kind": "dynamo", "path": "p:\\A.DYN", "title": "A2"})
        self.assertIs(again, one)
        ribbon = self.state.add_source(self.registry, {"kind": "ribbon", "ext_name": "Annotate",
                                                       "label": "Annotate (Revit)", "tab_names": ["Annotate"]})
        self.assertEqual(ribbon["kind"], "ribbon")
        self.assertFalse(ribbon["installed_by_my_ribbon"])

    def test_live_refused_kinds_are_not_placeable(self):
        self.assertFalse(self.state.is_placeable({"kind": "ribbon-ribbongallery"}))
        self.assertTrue(self.state.is_placeable({"kind": "button"}))
        tags = self.state.button_tags({"kind": "ribbon-ribbongallery"})
        self.assertTrue(tags[0].startswith("cannot be placed"))
        self.assertIn("gallery", tags[0])


DYN_2X = u"""{
  "Uuid": "123", "IsCustomNode": false, "Name": "Renumber Sheets",
  "Nodes": [
    {"ConcreteType": "PythonNodeModels.PythonNode, PythonNodeModels", "Engine": "CPython3", "Id": "a"},
    {"ConcreteType": "PythonNodeModels.PythonNode, PythonNodeModels", "Id": "b"},
    {"ConcreteType": "Dynamo.Graph.Nodes.ZeroTouch.DSFunction, DynamoCore", "Id": "c"}
  ],
  "NodeLibraryDependencies": [
    {"Name": "Clockwork for Dynamo 2.x", "Version": "2.3.0", "ReferenceType": "Package"},
    {"Name": "Rhythm", "Version": "2023.1.1", "ReferenceType": "Package"},
    {"Name": "Clockwork for Dynamo 2.x", "Version": "2.3.0", "ReferenceType": "Package"}
  ]
}"""

DYN_1X = u'<?xml version="1.0" encoding="utf-8"?><Workspace Version="1.3.4.6666" X="0" Y="0" zoom="1" Name="Old Graph"><Elements><Dynamo.Nodes.PythonNode type="DSIronPythonNode.PythonNode" /></Elements></Workspace>'

DYN_2X_MANUAL = u"""{
  "Uuid": "456", "IsCustomNode": false, "Name": "Manual Graph",
  "Nodes": [
    {"ConcreteType": "PythonNodeModels.PythonNode, PythonNodeModels", "Engine": "IronPython2", "Id": "a"}
  ],
  "View": {
    "Dynamo": {"ScaleFactor": 1.0, "HasRunWithoutCrash": true, "RunType": "Manual", "RunPeriod": "1000"}
  }
}"""

DYN_1X_MANUAL = u'<?xml version="1.0" encoding="utf-8"?><Workspace Version="1.3.4.6666" Name="Old Graph" ' \
                u'RunType="Manual" RunPeriod="1000" HasRunWithoutCrash="False"><Elements /></Workspace>'


class DynamoHelperTests(unittest.TestCase):
    def setUp(self):
        self.state = _load_state()

    def test_facts_from_a_dynamo_2_graph(self):
        facts = self.state.dynamo_facts_from_text(u"\ufeff" + DYN_2X, "Renumber Sheets.dyn")
        self.assertEqual(facts["format"], "2.x")
        self.assertEqual(facts["name"], "Renumber Sheets")
        self.assertFalse(facts["is_custom_node"])
        self.assertEqual(facts["python_engines"], ["CPython3", "IronPython2"])
        self.assertEqual(facts["packages"], ["Clockwork for Dynamo 2.x", "Rhythm"])
        self.assertEqual(facts["problem"], "")
        self.assertEqual(self.state.dynamo_tags(facts),
                         ["contains Python nodes (CPython3, IronPython2)",
                          "uses packages: Clockwork for Dynamo 2.x, Rhythm"])

    def test_facts_from_a_dynamo_1_graph_and_bad_files(self):
        facts = self.state.dynamo_facts_from_text(DYN_1X, "old.dyn")
        self.assertEqual((facts["format"], facts["name"], facts["python_engines"]),
                         ("1.x", "Old Graph", ["IronPython2"]))
        self.assertEqual(self.state.dynamo_tags(facts)[0], "Dynamo 1.x graph")
        self.assertIn("custom node", self.state.dynamo_facts_from_text(DYN_2X, "node.dyf")["problem"])
        self.assertIn("Python script", self.state.dynamo_facts_from_text("print(1)", "x.py")["problem"])
        self.assertIn("does not look like", self.state.dynamo_facts_from_text("garbage", "x.dyn")["problem"])
        custom = self.state.dynamo_facts_from_text(DYN_2X.replace('"IsCustomNode": false', '"IsCustomNode": true'), "x.dyn")
        self.assertTrue(custom["is_custom_node"])
        self.assertIn("custom node", custom["problem"])

    def test_the_run_mode_and_the_python_engine_are_read_from_the_graph(self):
        """Both decide how the bundle is written, so both come off the file."""
        auto = self.state.dynamo_facts_from_text(DYN_2X, "g.dyn")
        self.assertEqual(auto["run_type"], "")
        self.assertEqual(auto["engine"], "mixed")
        self.assertTrue(self.state.dynamo_uses_cpython(auto))
        self.assertFalse(self.state.dynamo_needs_forced_run(auto))

        manual = self.state.dynamo_facts_from_text(DYN_2X_MANUAL, "g.dyn")
        self.assertEqual(manual["run_type"], "Manual")
        self.assertEqual(manual["engine"], "IronPython2")
        self.assertFalse(self.state.dynamo_uses_cpython(manual))
        self.assertTrue(self.state.dynamo_needs_forced_run(manual))
        self.assertIn("saved in Manual run mode", " ".join(self.state.dynamo_tags(manual)))

        old = self.state.dynamo_facts_from_text(DYN_1X_MANUAL, "old.dyn")
        self.assertEqual((old["format"], old["run_type"]), ("1.x", "Manual"))
        self.assertTrue(self.state.dynamo_needs_forced_run(old))

        # a graph that says Automatic is left alone, and so is one that says nothing
        auto_2x = self.state.dynamo_facts_from_text(
            DYN_2X_MANUAL.replace('"RunType": "Manual"', '"RunType": "Automatic"'), "g.dyn")
        self.assertFalse(self.state.dynamo_needs_forced_run(auto_2x))
        self.assertFalse(self.state.dynamo_needs_forced_run({}))

    def test_forcing_automatic_run_changes_that_one_value_and_nothing_else(self):
        for text in (DYN_2X_MANUAL, DYN_1X_MANUAL):
            patched, changed = self.state.force_automatic_run(text)
            self.assertTrue(changed)
            # putting the one word back gives the file byte for byte
            self.assertEqual(patched.replace("Automatic", "Manual"), text)
            self.assertEqual(
                self.state.dynamo_facts_from_text(patched, "g.dyn")["run_type"], "Automatic")
            # running it again is a no-op
            self.assertEqual(self.state.force_automatic_run(patched), (patched, False))

    def test_a_graph_that_cannot_be_patched_confidently_is_handed_back_untouched(self):
        for text in ("garbage", DYN_2X,
                     DYN_2X_MANUAL + DYN_2X_MANUAL):  # two RunType values, no single answer
            self.assertEqual(self.state.force_automatic_run(text), (text, False))

    def test_the_bundle_yaml_asks_for_a_clean_engine_only_for_cpython(self):
        plain = self.state.render_dynamo_bundle_yaml("T", "tip", "C:\\g.dyn")
        self.assertIn("automate: true", plain)
        self.assertNotIn("clean:", plain)
        clean = self.state.render_dynamo_bundle_yaml("T", "tip", None, clean=True)
        self.assertIn("clean: true", clean)
        self.assertNotIn("dynamo_path", clean)

    def test_bundle_folder_names_are_one_plain_component(self):
        ok = self.state.is_bundle_folder_name
        self.assertTrue(ok("Renumber Sheets.pushbutton"))
        for bad in ("", "x", ".pushbutton", "a/b.pushbutton", "a\\b.pushbutton", "C:x.pushbutton",
                    "..", " a.pushbutton", "a.pushbutton "):
            self.assertFalse(ok(bad), bad)

    def test_unique_bundles_rename_clashes_and_their_placements(self):
        registry = {"format": 1, "sources": [], "destinations": [{"id": "d1", "tab": "T", "panel": "P"}],
                    "placements": [], "hidden_tabs": []}
        a = self.state.add_source(registry, {"kind": "dynamo", "path": "P:/a.dyn", "title": "A",
                                             "bundle": "A.pushbutton"})
        b = self.state.add_source(registry, {"kind": "dynamo", "path": "P:/b.dyn", "title": "A",
                                             "bundle": "A.pushbutton"})          # clash
        c = self.state.add_source(registry, {"kind": "dynamo", "path": "P:/c.dyn", "title": "C",
                                             "bundle": "C:evil.pushbutton"})     # invalid
        d = self.state.add_source(registry, {"kind": "dynamo", "path": "P:/d.dyn", "title": "D",
                                             "bundle": ""})                      # unnamed
        for source in (a, b, c, d):
            self.state.add_placement(registry, source["id"], "d1", {
                "kind": "button", "title": source["title"], "control_id": "old",
                "path": [{"name": "My Ribbon Library", "title": "My Ribbon Library"},
                         {"name": "Dynamo", "title": "Dynamo"},
                         {"name": self.state.strip_pushbutton(source["bundle"]) or "x", "title": source["title"]}]})
        renames = self.state.unique_dynamo_bundles(registry, ["D.pushbutton", "a 2.pushbutton"])
        self.assertEqual(a["bundle"], "A.pushbutton")
        self.assertEqual(b["bundle"], "A 3.pushbutton")       # A and "a 2" are taken
        self.assertEqual(c["bundle"], "C.pushbutton")
        self.assertEqual(d["bundle"], "D 2.pushbutton")       # D.pushbutton is on disk
        self.assertEqual([old for old, new in renames], ["A.pushbutton", "C:evil.pushbutton", ""])
        placement_b = self.state.find_placement(registry, b["id"], registry["placements"][1]["path"])
        self.assertEqual(placement_b["path"][-1]["name"], "A 3")
        self.assertEqual(placement_b["control_id"], "CustomCtrl_%CustomCtrl_%My Ribbon Library%Dynamo%A 3")
        # a second pass changes nothing
        self.assertEqual(self.state.unique_dynamo_bundles(registry, ["D.pushbutton"]), [])

    def test_yaml_without_an_original_leaves_dynamo_path_out(self):
        yaml = self.state.render_dynamo_bundle_yaml("T", "tip", None)
        self.assertNotIn("dynamo_path", yaml)
        self.assertIn("automate: true", yaml)

    def test_bundle_name_and_yaml(self):
        self.assertEqual(self.state.dynamo_bundle_name("Renumber: Sheets?", []), "Renumber_ Sheets_.pushbutton")
        self.assertEqual(self.state.dynamo_bundle_name("A", ["a.pushbutton"]), "A 2.pushbutton")
        yaml = self.state.render_dynamo_bundle_yaml(u'Re "numb"\ner', "Dynamo graph: P:\\x.dyn",
                                                    "P:\\Dyn\\x y.dyn")
        self.assertEqual(yaml.splitlines(), [
            'title: "Re \\"numb\\"\\ner"',
            'tooltip: "Dynamo graph: P:\\\\x.dyn"',
            'author: "EasyBIM My Ribbon"',
            "engine:",
            "  automate: true",
            '  dynamo_path: "P:\\\\Dyn\\\\x y.dyn"',
        ])
        tooltip = self.state.dynamo_tooltip("P:\\x.dyn", {"format": "2.x", "python_engines": ["CPython3"],
                                                           "packages": []})
        self.assertEqual(tooltip.splitlines(), ["Dynamo graph: P:\\x.dyn",
                                                "Contains Python nodes (CPython3)",
                                                "Ctrl+click opens it in Dynamo."])


class PickerTagTests(unittest.TestCase):
    def setUp(self):
        self.state = _load_state()

    def test_tags(self):
        self.assertEqual(self.state.button_tags(_button("A")), [])
        self.assertEqual(self.state.button_tags(_button("A", min_revit="2024"), host_version="2026"),
                         ["Revit 2024+"])
        self.assertEqual(self.state.button_tags(_button("A", min_revit=2024), host_version=2023),
                         ["Revit 2024+ (not this Revit)"])
        self.assertEqual(self.state.button_tags(_button("A", min_revit=2015, max_revit=2027)),
                         ["Revit 2015-2027"])
        self.assertEqual(self.state.button_tags(_button("A", max_revit=2024), host_version=2026),
                         ["Revit up to 2024 (not this Revit)"])
        self.assertEqual(self.state.button_tags(_button("A", in_layout=False)),
                         ["not shown in its own ribbon"])
        self.assertEqual(self.state.button_tags(_button("A", kind="pulldown")), ["whole drop-down"])
        tags = self.state.button_tags(_button("A", kind="panelbutton"))
        self.assertTrue(tags[0].startswith("cannot be placed"))
        self.assertFalse(self.state.is_placeable(_button("A", kind="nobutton")))
        self.assertTrue(self.state.is_placeable(_button("A", kind="linkbutton")))


class ImportExportTests(unittest.TestCase):
    def setUp(self):
        self.state = _load_state()
        self.current = {"format": 1, "sources": [], "destinations": [], "placements": []}
        source = self.state.add_source(self.current, {
            "kind": "git", "url": "https://github.com/o/r", "ext_name": "r", "label": "o/r",
            "tab_names": ["Foo"], "installed_by_my_ribbon": True, "hide_tab": True})
        dest = self.state.add_destination(self.current, "EasyBIM", "My Tools")
        self.state.add_placement(self.current, source["id"], dest["id"], _button("A"))

        self.incoming = {"format": 1, "sources": [], "destinations": [], "placements": []}
        same = self.state.add_source(self.incoming, {
            "kind": "git", "url": "https://github.com/O/R.git", "ext_name": "r", "label": "O/R",
            "tab_names": ["Foo"], "installed_by_my_ribbon": True, "hide_tab": True})
        new = self.state.add_source(self.incoming, {
            "kind": "git", "url": "https://github.com/x/y", "ext_name": "y", "label": "x/y",
            "tab_names": ["Y"], "installed_by_my_ribbon": True, "hide_tab": True})
        installed = self.state.add_source(self.incoming, {"kind": "installed", "ext_name": "pyRevitTools",
                                                          "label": "pyRevitTools", "tab_names": ["pyRevit"]})
        d1 = self.state.add_destination(self.incoming, "EasyBIM", "My Tools")
        d2 = self.state.add_destination(self.incoming, "MEP Kit", "Sheets", own_tab=True)
        self.state.add_placement(self.incoming, same["id"], d1["id"], _button("A"))   # duplicate
        self.state.add_placement(self.incoming, same["id"], d1["id"], _button("B"))
        self.state.add_placement(self.incoming, new["id"], d2["id"], _button("Z", tab="Y", panel="P"))
        self.state.add_placement(self.incoming, installed["id"], d2["id"],
                                 _button("Select", tab="pyRevit", panel="Selection"))

    def test_export_document_is_a_copy_with_the_same_shape(self):
        self.current["hidden_tabs"] = ["Foo"]
        document = self.state.export_document(self.current)
        self.assertEqual(document["format"], 1)
        self.assertEqual(document["exported_by"], "EasyBIM My Ribbon")
        self.assertEqual(document["hidden_tabs"], ["Foo"])
        self.assertEqual(document["placements"], self.current["placements"])
        document["placements"][0]["title"] = "changed"
        self.assertEqual(self.current["placements"][0]["title"], "A")

    def test_merge_reuses_sources_and_panels_and_skips_duplicates(self):
        plan = self.state.plan_import(self.current, self.incoming, "merge",
                                      installed_ext_names=["r", "EasyBIM"])
        self.assertEqual(plan["sources_reused"], ["o/r"])
        self.assertEqual(sorted(plan["sources_added"]), ["pyRevitTools", "x/y"])
        self.assertEqual(plan["sources_to_install"], ["x/y"])
        self.assertEqual(plan["sources_not_here"], ["pyRevitTools"])
        self.assertEqual(plan["destinations_added"], ["MEP Kit > Sheets"])
        self.assertEqual(plan["placements_added"], ["B", "Z", "Select"])
        self.assertEqual(plan["placements_skipped"], ["A (already placed)"])
        result = plan["result"]
        self.assertEqual(len(result["sources"]), 3)
        self.assertEqual(len(result["destinations"]), 2)
        self.assertEqual(len(result["placements"]), 4)
        # the merged file did not touch the caller's registry
        self.assertEqual(len(self.current["placements"]), 1)
        # ids stay unique and orders are dense
        ids = [p["id"] for p in result["placements"]]
        self.assertEqual(len(ids), len(set(ids)))
        d1 = self.state.find_destination(result, "EasyBIM", "My Tools")
        self.assertEqual([p["order"] for p in self.state.placements_in(result, d1["id"])], [0, 1])

    def test_import_never_trusts_the_file_about_what_this_computer_installed(self):
        # installed_by_my_ribbon decides what Remove may delete; a colleague's
        # extra_root path means nothing here
        for source in self.incoming["sources"]:
            source["installed_by_my_ribbon"] = True
            source["extra_root"] = "C:/Users/colleague/repos/x/extensions"
        plan = self.state.plan_import(self.current, self.incoming, "merge")
        added = [s for s in plan["result"]["sources"] if s.get("label") in ("x/y", "pyRevitTools")]
        self.assertEqual(len(added), 2)
        for source in added:
            self.assertFalse(source["installed_by_my_ribbon"])
            self.assertIsNone(source["extra_root"])
        # the reused existing source keeps its own facts
        existing = self.state.find_source(plan["result"], {"kind": "git", "url": "https://github.com/o/r",
                                                            "ext_name": "r"})
        self.assertTrue(existing["installed_by_my_ribbon"])

    def test_hidden_tabs_merge_and_replace(self):
        self.current["hidden_tabs"] = ["Foo"]
        self.incoming["hidden_tabs"] = ["foo", "Systems"]
        plan = self.state.plan_import(self.current, self.incoming, "merge")
        # "Y" comes from the incoming x/y source that hides its own tab
        self.assertEqual(plan["result"]["hidden_tabs"], ["Foo", "Systems", "Y"])
        self.assertEqual(plan["tabs_hidden"], ["Systems", "Y"])
        self.assertIn("Tabs to hide: Systems, Y.", self.state.format_import_preview(plan))
        plan = self.state.plan_import(self.current, self.incoming, "replace")
        self.assertEqual(plan["result"]["hidden_tabs"], ["foo", "Systems", "Y"])
        # a ribbon-kind source never asks to be installed
        self.incoming["sources"].append({"id": "s9", "kind": "ribbon", "ext_name": "Annotate",
                                         "label": "Annotate (Revit)", "tab_names": ["Annotate"]})
        plan = self.state.plan_import(self.current, self.incoming, "merge", installed_ext_names=["r"])
        self.assertNotIn("Annotate (Revit)", plan["sources_to_install"])
        self.assertNotIn("Annotate (Revit)", plan["sources_not_here"])

    def test_replace_starts_from_the_file(self):
        plan = self.state.plan_import(self.current, self.incoming, "replace",
                                      installed_ext_names=["r"])
        self.assertEqual(plan["sources_reused"], [])
        self.assertEqual(len(plan["sources_added"]), 3)
        self.assertEqual(plan["placements_added"], ["A", "B", "Z", "Select"])
        self.assertEqual(plan["placements_skipped"], [])
        self.assertEqual(len(plan["result"]["placements"]), 4)

    def test_preview_text_is_plain_sentences(self):
        plan = self.state.plan_import(self.current, self.incoming, "merge",
                                      installed_ext_names=["r"])
        lines = self.state.format_import_preview(plan)
        self.assertEqual(lines[0], "Merge the file into what you have.")
        self.assertIn("Sources: 2 new (x/y, pyRevitTools), 1 already linked (o/r).", lines)
        self.assertIn("To download and install here: 1 (x/y).", lines)
        self.assertTrue(any(line.startswith("Not installed on this computer") for line in lines))
        self.assertIn("Buttons: 3 to add (B, Z, Select).", lines)
        self.assertIn("Skipped: A (already placed).", lines)

    def test_placement_whose_source_is_missing_from_the_file_is_skipped(self):
        self.incoming["placements"].append({
            "id": "p9", "source": "ghost", "dest": "d1", "order": 0, "kind": "button",
            "title": "Ghost", "control_id": "", "path": [_level("T"), _level("P"), _level("G")]})
        plan = self.state.plan_import(self.current, self.incoming, "merge")
        self.assertTrue(any(item.startswith("Ghost") for item in plan["placements_skipped"]))


class StackLayoutTests(unittest.TestCase):
    """Stacks of 2-3 small buttons, separators and the slide-out fold."""

    def setUp(self):
        self.state = _load_state()
        self.registry = {"format": 1, "sources": [
            {"id": "s1", "kind": "git", "url": "https://github.com/o/r", "ext_name": "R"}],
            "destinations": [], "placements": [], "hidden_tabs": []}
        self.dest = self.state.add_destination(self.registry, "EasyBIM", "My Tools")
        self.a = self.state.add_placement(self.registry, "s1", self.dest["id"], _button("A"))
        self.b = self.state.add_placement(self.registry, "s1", self.dest["id"], _button("B"))
        self.c = self.state.add_placement(self.registry, "s1", self.dest["id"], _button("C"))

    def _order(self):
        rows = self.state.placements_in(self.registry, self.dest["id"])
        return [(r.get("title"), self.state.safe_text(r.get("stack"))) for r in rows]

    def test_group_with_next_makes_a_stack_of_two_then_three(self):
        ok, reason = self.state.group_with_next(self.registry, self.a["id"])
        self.assertTrue(ok, reason)
        stack_id = self.a["stack"]
        self.assertTrue(stack_id)
        self.assertEqual(self.b["stack"], stack_id)
        self.assertEqual(self.c["stack"], "")
        ok, reason = self.state.group_with_next(self.registry, self.a["id"])
        self.assertTrue(ok, reason)
        self.assertEqual(self.c["stack"], stack_id)
        self.assertEqual(self._order(), [("A", stack_id), ("B", stack_id), ("C", stack_id)])

    def test_a_stack_holds_at_most_three(self):
        self.state.group_with_next(self.registry, self.a["id"])
        self.state.group_with_next(self.registry, self.a["id"])
        self.state.add_placement(self.registry, "s1", self.dest["id"], _button("D"))
        ok, reason = self.state.group_with_next(self.registry, self.a["id"])
        self.assertFalse(ok)
        self.assertIn("at most", reason)

    def test_only_plain_buttons_stack(self):
        pulldown = self.state.add_placement(
            self.registry, "s1", self.dest["id"], _button("Tools", kind="pulldown"))
        ok, reason = self.state.group_with_next(self.registry, pulldown["id"])
        self.assertFalse(ok)
        self.assertIn("drop-down", reason)
        ok, reason = self.state.group_with_next(self.registry, self.c["id"])
        self.assertFalse(ok)  # the row below is the pulldown
        sep = self.state.add_separator(self.registry, self.dest["id"])
        ok, reason = self.state.group_with_next(self.registry, sep["id"])
        self.assertFalse(ok)

    def test_the_last_row_has_nothing_to_stack_with(self):
        ok, reason = self.state.group_with_next(self.registry, self.c["id"])
        self.assertFalse(ok)
        self.assertIn("below", reason)

    def test_a_plain_row_joins_the_stack_below_it(self):
        self.state.group_with_next(self.registry, self.b["id"])  # b+c stacked
        stack_id = self.b["stack"]
        ok, reason = self.state.group_with_next(self.registry, self.a["id"])
        self.assertTrue(ok, reason)
        self.assertEqual(self.a["stack"], stack_id)
        self.assertEqual(self._order(), [("A", stack_id), ("B", stack_id), ("C", stack_id)])

    def test_ungroup_dissolves_the_whole_stack(self):
        self.state.group_with_next(self.registry, self.a["id"])
        stack_id = self.a["stack"]
        self.assertTrue(self.state.ungroup(self.registry, self.b["id"]))
        self.assertEqual([self.a["stack"], self.b["stack"]], ["", ""])
        self.state.group_with_next(self.registry, self.a["id"])
        self.assertTrue(self.state.ungroup(self.registry, self.a["stack"]))
        self.assertEqual([self.a["stack"], self.b["stack"]], ["", ""])
        self.assertFalse(self.state.ungroup(self.registry, stack_id))

    def test_normalize_pulls_members_together_and_dissolves_singles(self):
        self.a["stack"] = "k1"
        self.c["stack"] = "k1"  # scattered around b
        self.state.normalize_layout(self.registry)
        self.assertEqual(self._order(), [("A", "k1"), ("C", "k1"), ("B", "")])
        self.c["stack"] = ""
        self.state.normalize_layout(self.registry)
        self.assertEqual(self.a["stack"], "")  # a stack of one is no stack

    def test_normalize_sheds_a_fourth_member_and_marker_stacks(self):
        d = self.state.add_placement(self.registry, "s1", self.dest["id"], _button("D"))
        for row in (self.a, self.b, self.c, d):
            row["stack"] = "k1"
        sep = self.state.add_separator(self.registry, self.dest["id"])
        sep["stack"] = "k1"
        self.state.normalize_layout(self.registry)
        self.assertEqual([r["stack"] for r in (self.a, self.b, self.c)], ["k1"] * 3)
        self.assertEqual(d["stack"], "")
        self.assertEqual(sep["stack"], "")

    def test_member_moves_inside_and_out_at_the_edge(self):
        self.state.group_with_next(self.registry, self.a["id"])
        stack_id = self.a["stack"]
        self.assertTrue(self.state.move_node(self.registry, "placement", self.b["id"], -1))
        self.assertEqual(self._order(), [("B", stack_id), ("A", stack_id), ("C", "")])
        # the top member moving up steps out of the stack... which dissolves
        # the remaining single
        self.assertTrue(self.state.move_node(self.registry, "placement", self.b["id"], -1))
        self.assertEqual(self.b["stack"], "")
        self.assertEqual(self.a["stack"], "")

    def test_a_stack_moves_as_one_block(self):
        self.state.group_with_next(self.registry, self.b["id"])  # b+c below a
        stack_id = self.b["stack"]
        self.assertTrue(self.state.move_node(self.registry, "stack", stack_id, -1))
        self.assertEqual(self._order(), [("B", stack_id), ("C", stack_id), ("A", "")])
        self.assertFalse(self.state.move_node(self.registry, "stack", stack_id, -1))

    def test_a_plain_row_steps_over_a_whole_block(self):
        self.state.group_with_next(self.registry, self.a["id"])  # a+b, then c
        stack_id = self.a["stack"]
        self.assertTrue(self.state.move_node(self.registry, "placement", self.c["id"], -1))
        self.assertEqual(self._order(), [("C", ""), ("A", stack_id), ("B", stack_id)])

    def test_leaving_the_panel_leaves_the_stack(self):
        other = self.state.add_destination(self.registry, "EasyBIM", "Second")
        self.state.group_with_next(self.registry, self.a["id"])
        self.state.group_with_next(self.registry, self.a["id"])  # a+b+c
        self.state.move_placement_to(self.registry, self.b["id"], other["id"])
        self.assertEqual(self.b["stack"], "")
        self.assertEqual(self.a["stack"], self.c["stack"])
        self.assertTrue(self.a["stack"])

    def test_move_stack_to_keeps_the_block_stacked(self):
        other = self.state.add_destination(self.registry, "EasyBIM", "Second")
        self.state.add_placement(self.registry, "s1", other["id"], _button("X"))
        self.state.group_with_next(self.registry, self.a["id"])
        stack_id = self.a["stack"]
        self.assertTrue(self.state.move_stack_to(self.registry, stack_id, other["id"]))
        rows = self.state.placements_in(self.registry, other["id"])
        self.assertEqual([r.get("title") for r in rows], ["X", "A", "B"])
        self.assertEqual([self.state.safe_text(r.get("stack")) for r in rows],
                         ["", stack_id, stack_id])

    def test_markers_append_and_the_panel_folds_once(self):
        sep = self.state.add_separator(self.registry, self.dest["id"])
        self.assertEqual(sep["kind"], "separator")
        self.assertEqual(sep["source"], "")
        self.assertEqual(sep["path"], [])
        fold = self.state.add_slideout(self.registry, self.dest["id"])
        self.assertIsNotNone(fold)
        self.assertTrue(self.state.has_slideout(self.registry, self.dest["id"]))
        self.assertIsNone(self.state.add_slideout(self.registry, self.dest["id"]))
        self.assertIsNone(self.state.add_separator(self.registry, "nope"))

    def test_markers_are_not_counted_as_buttons(self):
        self.state.add_separator(self.registry, self.dest["id"])
        self.state.add_slideout(self.registry, self.dest["id"])
        self.assertEqual(self.state.summarize(self.registry)["placements"], 3)
        self.assertIn("3 buttons", self.state.status_line(self.registry, 0))

    def test_removing_a_member_dissolves_a_pair(self):
        self.state.group_with_next(self.registry, self.a["id"])
        self.state.remove_placement(self.registry, self.b["id"])
        self.assertEqual(self.a["stack"], "")

    def test_export_and_replace_import_carry_the_layout(self):
        self.state.group_with_next(self.registry, self.a["id"])
        self.state.add_separator(self.registry, self.dest["id"])
        document = self.state.export_document(self.registry)
        empty = {"format": 1, "sources": [], "destinations": [], "placements": [],
                 "hidden_tabs": []}
        plan = self.state.plan_import(empty, document, "replace")
        rows = self.state.placements_in(
            plan["result"], plan["result"]["destinations"][0]["id"])
        self.assertEqual([r.get("kind") for r in rows],
                         ["button", "button", "button", "separator"])
        stacks = [self.state.safe_text(r.get("stack")) for r in rows]
        self.assertTrue(stacks[0] and stacks[0] == stacks[1])

    def test_an_imported_stack_never_collides_with_an_existing_id(self):
        self.state.group_with_next(self.registry, self.a["id"])  # takes "k1" here
        incoming = {
            "format": 1,
            "sources": [{"id": "s7", "kind": "installed", "ext_name": "Other",
                         "tab_names": ["Other"]}],
            "destinations": [{"id": "d7", "tab": "Their Tab", "panel": "Their Panel",
                              "own_tab": True}],
            "placements": [
                dict(_button("X"), id="px", source="s7", dest="d7", order=0, stack="k1"),
                dict(_button("Y"), id="py", source="s7", dest="d7", order=1, stack="k1"),
            ],
            "hidden_tabs": [],
        }
        plan = self.state.plan_import(self.registry, incoming, "merge",
                                      installed_ext_names=["Other"])
        new_dest = self.state.find_destination(plan["result"], "Their Tab", "Their Panel")
        rows = self.state.placements_in(plan["result"], new_dest["id"])
        imported = self.state.safe_text(rows[0].get("stack"))
        self.assertTrue(imported)
        self.assertEqual(imported, self.state.safe_text(rows[1].get("stack")))
        self.assertNotEqual(imported, "k1")  # our own k1 keeps its members
        ours = [self.state.safe_text(r.get("stack"))
                for r in self.state.placements_in(plan["result"], self.dest["id"])]
        self.assertEqual(ours, ["k1", "k1", ""])

    def test_merge_import_keeps_an_existing_panels_layout(self):
        self.state.group_with_next(self.registry, self.a["id"])
        incoming = self.state.export_document(self.registry)
        incoming["placements"].append({
            "id": "p_sep", "source": "", "dest": incoming["destinations"][0]["id"],
            "order": 9, "kind": "separator", "title": "", "control_id": "", "path": []})
        current = self.state.export_document(self.registry)
        for row in current["placements"]:
            row["stack"] = ""
        plan = self.state.plan_import(current, incoming, "merge")
        rows = self.state.placements_in(
            plan["result"], plan["result"]["destinations"][0]["id"])
        # the panel already exists here: its flat layout stays, the file's
        # separator is not spliced in
        self.assertEqual([r.get("kind") for r in rows], ["button"] * 3)

    def test_a_round_one_registry_shape_still_imports(self):
        incoming = self.state.export_document(self.registry)
        for row in incoming["placements"]:
            row.pop("stack", None)
        empty = {"format": 1, "sources": [], "destinations": [], "placements": [],
                 "hidden_tabs": []}
        plan = self.state.plan_import(empty, incoming, "replace")
        self.assertEqual(len(plan["placements_added"]), 3)


if __name__ == "__main__":
    unittest.main()
