"""My Ribbon pure logic: git links, folder names, staged registry edits,
picker tags, and the import/export planner.  Desktop Python only."""
import importlib.util
import pathlib
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
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
        self.assertEqual(self.state.count_changes(saved, self.registry), 3)
        self.assertEqual(self.state.status_line(self.registry, 3),
                         u"1 source · 1 button · 3 changes not applied")
        self.assertEqual(self.state.status_line(self.registry, 0), u"1 source · 1 button")
        self.assertEqual(self.state.count_changes(self.registry, self.registry), 0)


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
        document = self.state.export_document(self.current)
        self.assertEqual(document["format"], 1)
        self.assertEqual(document["exported_by"], "EasyBIM My Ribbon")
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


if __name__ == "__main__":
    unittest.main()
