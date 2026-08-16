"""My Ribbon host layer, driven by fakes: repository layout detection, the
clone-into-temp-then-move rule, error mapping, the pyRevit parser adapter,
the catalogue listing, guarded deletion and root registration."""
import importlib.util
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "My Ribbon.pushbutton"
)


def _load_host():
    # the way pyRevit puts the bundle folder on sys.path
    if str(COMMAND_DIR) not in sys.path:
        sys.path.insert(0, str(COMMAND_DIR))
    sys.modules.pop("my_ribbon_state", None)
    spec = importlib.util.spec_from_file_location("my_ribbon_host", str(COMMAND_DIR / "my_ribbon_host.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_state():
    spec = importlib.util.spec_from_file_location("my_ribbon_state", str(COMMAND_DIR / "my_ribbon_state.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _touch(path, text=""):
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


# -- fake libgit -------------------------------------------------------------


class _FakeCloneOptions(object):
    def __init__(self):
        self.BranchName = None
        self.RecurseSubmodules = False
        self.OnTransferProgress = None
        self.CredentialsProvider = None


class _FakeTransfer(object):
    def __init__(self, received, total):
        self.ReceivedObjects = received
        self.TotalObjects = total


class _FakeHandlers(object):
    @staticmethod
    def TransferProgressHandler(func):
        return func

    @staticmethod
    def CredentialsHandler(func):
        return func


class _FakeCreds(object):
    pass


class _FakeLibGit(object):
    """Clone writes a marker file; behaviour is scripted per test."""

    def __init__(self, behaviour="ok", steps=(1, 2, 3), remotes=None):
        self.behaviour = behaviour
        self.steps = steps
        self.calls = []
        self.remotes = remotes or []
        self.CloneOptions = _FakeCloneOptions
        self.Handlers = _FakeHandlers
        self.UsernamePasswordCredentials = _FakeCreds
        outer = self

        class Repository(object):
            def __init__(self, path):
                self.path = path

                class _Remote(object):
                    def __init__(self, name, url):
                        self.Name = name
                        self.Url = url

                class _Network(object):
                    Remotes = [_Remote(n, u) for n, u in outer.remotes]

                self.Network = _Network()

            def Dispose(self):
                pass

            @staticmethod
            def Clone(url, dest, options):
                outer.calls.append((url, dest, options))
                if outer.behaviour == "auth":
                    raise RuntimeError("request failed with status code: 401")
                if outer.behaviour == "boom":
                    raise RuntimeError("could not resolve host")
                os.makedirs(dest)
                _touch(os.path.join(dest, "cloned.txt"), url)
                if options.OnTransferProgress is not None:
                    total = len(outer.steps)
                    for step in outer.steps:
                        if options.OnTransferProgress(_FakeTransfer(step, total)) is False:
                            # LibGit2Sharp's UserCancelledException carries
                            # libgit2's last error text, never the word "cancel"
                            raise RuntimeError("transfer_progress callback returned -7")
                return dest

            @staticmethod
            def Discover(path):
                return path if path else None

        self.Repository = Repository


class RepositoryLayoutTests(unittest.TestCase):
    def setUp(self):
        self.host = _load_host()
        self.state = _load_state()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_root_that_is_an_extension(self):
        _touch(os.path.join(self.tmp, "Foo.tab", "Bar.panel", "Baz.pushbutton", "script.py"))
        _touch(os.path.join(self.tmp, ".git", "HEAD"))
        layout = self.host.find_extension_roots(self.tmp)
        self.assertTrue(layout["root_is_extension"])
        self.assertEqual(layout["extensions"], [])
        self.assertEqual(layout["tab_roots"], [self.tmp])

    def test_repository_holding_extensions_and_libs(self):
        a = os.path.join(self.tmp, "extensions", "A.extension")
        b = os.path.join(self.tmp, "extensions", "B.lib")
        _touch(os.path.join(a, "A.tab", "P.panel", "X.pushbutton", "script.py"))
        _touch(os.path.join(b, "mod.py"))
        _touch(os.path.join(self.tmp, ".git", "HEAD"))
        _touch(os.path.join(self.tmp, "deep", "er", "still", "C.extension", "C.tab", "x"))  # depth 4: ignored
        layout = self.host.find_extension_roots(self.tmp)
        self.assertFalse(layout["root_is_extension"])
        self.assertEqual(layout["extensions"], [a])
        self.assertEqual(layout["libs"], [b])

    def test_tab_folder_without_extension_name_is_reported_not_used(self):
        _touch(os.path.join(self.tmp, "src", "Tools.tab", "P.panel", "X.pushbutton", "script.py"))
        layout = self.host.find_extension_roots(self.tmp)
        self.assertEqual(layout["extensions"], [])
        self.assertEqual(layout["tab_roots"], [os.path.join(self.tmp, "src")])

    def test_manifest_name_is_read_with_or_without_bom(self):
        _touch(os.path.join(self.tmp, "extension.json"), u'\ufeff{"name": "Nice Tools", "type": "extension"}')
        self.assertEqual(self.host.read_manifest_name(self.tmp), "Nice Tools")
        _touch(os.path.join(self.tmp, "extension.json"), "{broken")
        self.assertIsNone(self.host.read_manifest_name(self.tmp))
        self.assertIsNone(self.host.read_manifest_name(os.path.join(self.tmp, "nope")))

    def test_plan_clone_destination_for_each_kind(self):
        ref, _ = self.state.parse_git_url("https://github.com/owner/MyTools")
        ext_root = os.path.join(self.tmp, "Extensions")
        repos = os.path.join(self.tmp, "repos")

        clone = os.path.join(self.tmp, "t1")
        _touch(os.path.join(clone, "Foo.tab", "P.panel", "X.pushbutton", "script.py"))
        plan = self.host.plan_clone_destination(ref, clone, ["Other.extension"], ext_root, repos)
        self.assertEqual(plan["kind"], "extension")
        self.assertEqual(plan["final_dir"], os.path.join(ext_root, "MyTools.extension"))
        self.assertEqual(plan["ext_dirs"], [plan["final_dir"]])
        self.assertEqual(plan["register_roots"], [])

        clone = os.path.join(self.tmp, "t2")
        _touch(os.path.join(clone, "extensions", "A.extension", "A.tab", "P.panel", "X.pushbutton", "script.py"))
        _touch(os.path.join(clone, "extensions", "B.extension", "B.tab", "P.panel", "X.pushbutton", "script.py"))
        plan = self.host.plan_clone_destination(ref, clone, [], ext_root, repos)
        self.assertEqual(plan["kind"], "repository")
        self.assertEqual(plan["final_dir"], os.path.join(repos, "owner__MyTools"))
        self.assertEqual(plan["ext_dirs"], [os.path.join(plan["final_dir"], "extensions", "A.extension"),
                                            os.path.join(plan["final_dir"], "extensions", "B.extension")])
        self.assertEqual(plan["register_roots"], [os.path.join(plan["final_dir"], "extensions")])

        clone = os.path.join(self.tmp, "t3")
        _touch(os.path.join(clone, "README.md"))
        plan = self.host.plan_clone_destination(ref, clone, [], ext_root, repos)
        self.assertEqual(plan["kind"], "none")
        self.assertIn("No pyRevit tools", plan["message"])

        clone = os.path.join(self.tmp, "t4")
        _touch(os.path.join(clone, "src", "Tools.tab", "P.panel", "X.pushbutton", "script.py"))
        plan = self.host.plan_clone_destination(ref, clone, [], ext_root, repos)
        self.assertEqual(plan["kind"], "none")
        self.assertIn("not named", plan["message"])


class CloneTests(unittest.TestCase):
    def setUp(self):
        self.host = _load_host()
        self.tmp = tempfile.mkdtemp()
        self.temp_root = os.path.join(self.tmp, ".myribbon-tmp")
        self.final = os.path.join(self.tmp, "Extensions", "MyTools.extension")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clone_sets_branch_submodules_and_reports_progress(self):
        libgit = _FakeLibGit(steps=(1, 2, 3))
        seen = []
        result = self.host.clone_repository("https://x/y.git", os.path.join(self.tmp, "c"), branch="dev",
                                            progress=lambda r, t: seen.append((r, t)) or True,
                                            libgit=libgit)
        self.assertTrue(os.path.isfile(os.path.join(result, "cloned.txt")))
        url, dest, options = libgit.calls[0]
        self.assertEqual(options.BranchName, "dev")
        self.assertTrue(options.RecurseSubmodules)
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_cancel_from_progress_raises_clone_cancelled(self):
        libgit = _FakeLibGit(steps=(1, 2, 3))
        with self.assertRaises(self.host.CloneCancelled):
            self.host.clone_repository("https://x/y.git", os.path.join(self.tmp, "c"),
                                       progress=lambda r, t: r < 2, libgit=libgit)

    def test_auth_and_other_errors_are_mapped(self):
        with self.assertRaises(self.host.CloneAuthError):
            self.host.clone_repository("https://x/y.git", os.path.join(self.tmp, "c"),
                                       libgit=_FakeLibGit(behaviour="auth"))
        with self.assertRaises(RuntimeError):
            self.host.clone_repository("https://x/y.git", os.path.join(self.tmp, "c"),
                                       libgit=_FakeLibGit(behaviour="boom"))

    def test_credentials_are_handed_to_the_options(self):
        libgit = _FakeLibGit()
        self.host.clone_repository("https://x/y.git", os.path.join(self.tmp, "c"),
                                   username="me", password="token", libgit=libgit)
        options = libgit.calls[0][2]
        self.assertIsNotNone(options.CredentialsProvider)
        creds = options.CredentialsProvider("https://x/y.git", "", None)
        self.assertEqual((creds.Username, creds.Password), ("me", "token"))

    def test_temp_then_move_lands_only_a_complete_clone(self):
        libgit = _FakeLibGit()
        result = self.host.clone_into_temp_then_move("https://x/y.git", self.final, self.temp_root,
                                                     libgit=libgit)
        self.assertEqual(result, self.final)
        self.assertTrue(os.path.isfile(os.path.join(self.final, "cloned.txt")))
        self.assertEqual(os.listdir(self.temp_root), [])

    def test_a_cancelled_or_failed_download_leaves_nothing_behind(self):
        with self.assertRaises(self.host.CloneCancelled):
            self.host.clone_into_temp_then_move("https://x/y.git", self.final, self.temp_root,
                                                progress=lambda r, t: False,
                                                libgit=_FakeLibGit(steps=(1,)))
        self.assertFalse(os.path.exists(self.final))
        self.assertEqual(os.listdir(self.temp_root), [])
        with self.assertRaises(RuntimeError):
            self.host.clone_into_temp_then_move("https://x/y.git", self.final, self.temp_root,
                                                libgit=_FakeLibGit(behaviour="boom"))
        self.assertFalse(os.path.exists(self.final))

    def test_existing_final_folder_is_refused(self):
        os.makedirs(self.final)
        with self.assertRaises(RuntimeError):
            self.host.clone_into_temp_then_move("https://x/y.git", self.final, self.temp_root,
                                                libgit=_FakeLibGit())

    def test_move_into_place_refuses_an_existing_target(self):
        src = os.path.join(self.tmp, "src")
        _touch(os.path.join(src, "a.txt"), "a")
        dst = os.path.join(self.tmp, "existing")
        os.makedirs(dst)
        with self.assertRaises(RuntimeError):
            self.host.move_into_place(src, dst)
        self.assertTrue(os.path.isfile(os.path.join(src, "a.txt")))
        self.assertEqual(os.listdir(dst), [])

    def test_move_into_place_creates_the_parent_and_moves(self):
        src = os.path.join(self.tmp, "src")
        _touch(os.path.join(src, "a.txt"), "a")
        dst = os.path.join(self.tmp, "deep", "er", "Final.extension")
        self.assertEqual(self.host.move_into_place(src, dst), dst)
        self.assertTrue(os.path.isfile(os.path.join(dst, "a.txt")))
        self.assertFalse(os.path.exists(src))

    def test_cleanup_temp_root(self):
        _touch(os.path.join(self.temp_root, "half", "file"))
        self.host.cleanup_temp_root(self.temp_root)
        self.assertFalse(os.path.exists(self.temp_root))

    def test_remote_url_prefers_origin(self):
        libgit = _FakeLibGit(remotes=[("upstream", "https://a/b.git"), ("origin", "https://c/d.git")])
        self.assertEqual(self.host.remote_url(self.tmp, libgit=libgit), "https://c/d.git")
        self.assertIsNone(self.host.remote_url("", libgit=libgit))


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.host = _load_host()

    def test_probe_messages(self):
        ok, status, message = self.host.probe_url("https://github.com/o/r", requester=lambda u, t: 200)
        self.assertTrue(ok)
        ok, status, message = self.host.probe_url("https://github.com/o/r", requester=lambda u, t: 404)
        self.assertFalse(ok)
        self.assertIn("404", message)
        self.assertIn("private", message)
        ok, status, message = self.host.probe_url("https://x/y", requester=lambda u, t: 401)
        self.assertIn("access token", message)

        def _boom(url, timeout):
            raise RuntimeError("name resolution failed")
        ok, status, message = self.host.probe_url("https://x/y", requester=_boom)
        self.assertFalse(ok)
        self.assertIn("Could not reach", message)


# -- fake parsed extension -----------------------------------------------------


class _Comp(object):
    def __init__(self, name, type_id, title=None, children=None, layout=None, control_id="",
                 icon=None, tooltip="", min_ver=None, max_ver=None):
        self.name = name
        self.type_id = type_id
        self.ui_title = title or name
        self.components = list(children or [])
        self.layout_items = list(layout or [])
        self.control_id = control_id
        self.icon_file = icon
        self.tooltip = tooltip
        self.min_revit_ver = min_ver
        self.max_revit_ver = max_ver

    def __iter__(self):
        if self.layout_items:
            return iter([c for c in self.components if c.name in self.layout_items])
        return iter(self.components)


class _Ext(object):
    def __init__(self, name, tabs, startup=None, hooks=None):
        self.name = name
        self.directory = "C:/ext/" + name
        self.components = tabs
        self.startup_script = startup
        self.hooks_path = hooks


class ParserAdapterTests(unittest.TestCase):
    def setUp(self):
        self.host = _load_host()

    def _extension(self):
        baz = _Comp("Baz", ".pushbutton", "Baz\nTool", control_id="CustomCtrl_%CustomCtrl_%Foo%Bar%Baz",
                    icon="C:/i.png", tooltip="  Does\n things  ", min_ver="2024")
        child = _Comp("A", ".pushbutton", control_id="…%A")
        hidden_child = _Comp("H", ".pushbutton")
        pull = _Comp("Tools", ".pulldown", children=[child, hidden_child], layout=["A"])
        one = _Comp("One", ".pushbutton")
        stack = _Comp("Stack1", ".stack", children=[one])
        secret = _Comp("Secret", ".pushbutton")
        panel_btn = _Comp("Corner", ".panelbutton")
        panel = _Comp("Bar", ".panel", "Bar Panel", children=[baz, pull, stack, secret, panel_btn],
                      layout=["Baz", "Tools", "Stack1", "Corner"])
        tab = _Comp("Foo", ".tab", children=[panel])
        lib = _Comp("lib", ".lib")
        return _Ext("Foo", [tab, lib], startup="C:/ext/Foo/startup.py", hooks="C:/ext/Foo/hooks")

    def test_describe_extension_flattens_and_tags(self):
        described = self.host.describe_extension(self._extension())
        self.assertEqual(described["name"], "Foo")
        self.assertEqual(described["tab_names"], ["Foo"])
        self.assertTrue(described["has_startup"])
        self.assertTrue(described["has_hooks"])
        titles = [b["title"] for b in described["buttons"]]
        self.assertEqual(titles, ["Baz\nTool", "A", "H", "Tools", "One", "Secret", "Corner"])
        by_name = dict((b["name"], b) for b in described["buttons"])
        baz = by_name["Baz"]
        self.assertEqual(baz["kind"], "button")
        self.assertEqual(baz["path"], [{"name": "Foo", "title": "Foo"},
                                       {"name": "Bar", "title": "Bar Panel"},
                                       {"name": "Baz", "title": "Baz\nTool"}])
        self.assertEqual(baz["control_id"], "CustomCtrl_%CustomCtrl_%Foo%Bar%Baz")
        self.assertEqual(baz["tooltip"], "Does things")
        self.assertEqual(baz["min_revit"], "2024")
        self.assertTrue(baz["in_layout"])
        self.assertEqual(by_name["Tools"]["kind"], "pulldown")
        self.assertEqual([c["name"] for c in by_name["Tools"]["children"]], ["A", "H"])
        self.assertEqual(by_name["A"]["path"][-2:], [{"name": "Tools", "title": "Tools"},
                                                      {"name": "A", "title": "A"}])
        self.assertFalse(by_name["H"]["in_layout"])
        # stack children sit flat: no stack level in the path, and the panel's
        # layout (which names the stack, not its children) does not hide them
        self.assertEqual(by_name["One"]["path"], [{"name": "Foo", "title": "Foo"},
                                                  {"name": "Bar", "title": "Bar Panel"},
                                                  {"name": "One", "title": "One"}])
        self.assertTrue(by_name["One"]["in_layout"])
        self.assertFalse(by_name["Secret"]["in_layout"])
        self.assertEqual(by_name["Corner"]["kind"], "panelbutton")
        # the tree mirrors the ribbon: panel items exclude the flattened stack
        panel = described["tabs"][0]["panels"][0]
        self.assertEqual([i["name"] for i in panel["items"]], ["Baz", "Tools", "One", "Secret", "Corner"])

    def test_no_layout_means_everything_is_shown(self):
        panel = _Comp("P", ".panel", children=[_Comp("X", ".pushbutton")])
        described = self.host.describe_extension(_Ext("E", [_Comp("T", ".tab", children=[panel])]))
        self.assertTrue(described["buttons"][0]["in_layout"])
        self.assertFalse(described["has_startup"])


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.host = _load_host()

    def test_only_ui_extensions_are_listed_and_sorted(self):
        class _Type(object):
            def __init__(self, postfix):
                self.POSTFIX = postfix

        class _Pkg(object):
            def __init__(self, name, postfix, installed=""):
                self.name = name
                self.type = _Type(postfix)
                self.url = "https://github.com/x/" + name
                self.description = "  A  tool \n set "
                self.author = "someone"
                self.builtin = False
                self.is_installed = installed

        class _FakePackages(object):
            @staticmethod
            def get_ext_packages():
                return [_Pkg("Zeta", ".extension"), _Pkg("alpha", ".extension", "C:/x"), _Pkg("lib", ".lib")]

        rows = self.host.catalogue_packages(_FakePackages, installed_names=["Zeta"])
        self.assertEqual([r["name"] for r in rows], ["alpha", "Zeta"])
        self.assertTrue(rows[0]["installed"])
        self.assertTrue(rows[1]["installed"])
        self.assertEqual(rows[1]["description"], "A tool set")


class RemovalAndRootsTests(unittest.TestCase):
    def setUp(self):
        self.host = _load_host()
        self.tmp = tempfile.mkdtemp()
        self.ext_root = os.path.join(self.tmp, "Extensions")
        self.repos = os.path.join(self.tmp, "repos")
        os.makedirs(self.ext_root)
        os.makedirs(self.repos)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_refuses_extensions_the_user_installed(self):
        ok, message = self.host.remove_installed_source(
            {"kind": "installed", "ext_name": "EasyBIM", "installed_by_my_ribbon": False},
            allowed_roots=[self.ext_root, self.repos])
        self.assertFalse(ok)
        self.assertIn("left in place", message)

    def test_deletes_a_folder_it_installed_even_with_read_only_git_files(self):
        target = os.path.join(self.ext_root, "MyTools.extension")
        packed = os.path.join(target, ".git", "objects", "pack", "x.pack")
        _touch(packed, "x")
        os.chmod(packed, stat.S_IREAD)
        ok, message = self.host.remove_installed_source(
            {"kind": "git", "ext_name": "MyTools", "installed_by_my_ribbon": True},
            allowed_roots=[self.ext_root, self.repos])
        self.assertTrue(ok, message)
        self.assertFalse(os.path.exists(target))

    def test_deletes_a_repository_clone_by_its_registered_root(self):
        repo = os.path.join(self.repos, "owner__MyTools")
        _touch(os.path.join(repo, "extensions", "A.extension", "A.tab", "x"))
        ok, message = self.host.remove_installed_source(
            {"kind": "git", "ext_name": "A", "installed_by_my_ribbon": True,
             "extra_root": os.path.join(repo, "extensions")},
            allowed_roots=[self.ext_root, self.repos])
        self.assertTrue(ok, message)
        self.assertFalse(os.path.exists(repo))

    def test_a_root_itself_is_never_deleted(self):
        _touch(os.path.join(self.repos, "keep", "file"))
        _touch(os.path.join(self.ext_root, "Other.extension", "x"))
        for extra_root in (self.repos, self.repos + os.sep, os.path.join(self.repos, ".."),
                           self.ext_root, os.path.join(self.tmp, "elsewhere", "extensions")):
            ok, message = self.host.remove_installed_source(
                {"kind": "git", "ext_name": "A", "installed_by_my_ribbon": True,
                 "extra_root": extra_root},
                allowed_roots=[self.ext_root, self.repos])
            self.assertTrue(os.path.isdir(os.path.join(self.repos, "keep")), extra_root)
            self.assertTrue(os.path.isdir(os.path.join(self.ext_root, "Other.extension")), extra_root)

    def test_missing_folder_is_fine(self):
        ok, message = self.host.remove_installed_source(
            {"kind": "git", "ext_name": "Ghost", "installed_by_my_ribbon": True},
            allowed_roots=[self.ext_root, self.repos])
        self.assertTrue(ok)

    def test_register_and_unregister_roots(self):
        class _Config(object):
            def __init__(self):
                self.roots = ["C:/custom"]
                self.saved = 0

            def get_thirdparty_ext_root_dirs(self, include_default=True):
                return list(self.roots)

            def set_thirdparty_ext_root_dirs(self, paths):
                self.roots = list(paths)

            def save_changes(self):
                self.saved += 1

        config = _Config()
        ok, _ = self.host.register_extension_root("C:/repos/x/extensions", config)
        self.assertTrue(ok)
        self.assertEqual(config.roots, ["C:/custom", "C:/repos/x/extensions"])
        ok, _ = self.host.register_extension_root("c:/REPOS/x/extensions", config)
        self.assertEqual(config.saved, 1)
        ok, _ = self.host.unregister_extension_root("C:/repos/x/extensions", config)
        self.assertEqual(config.roots, ["C:/custom"])
        self.assertEqual(config.saved, 2)

    def test_installed_extension_names_and_dirs(self):
        os.makedirs(os.path.join(self.ext_root, "Foo.extension"))
        os.makedirs(os.path.join(self.ext_root, "Bar.lib"))
        os.makedirs(os.path.join(self.ext_root, ".myribbon-tmp"))
        self.assertEqual(self.host.installed_extension_names([self.ext_root]), ["Foo"])
        self.assertEqual(self.host.find_installed_extension_dir("foo", [self.ext_root]),
                         os.path.join(self.ext_root, "Foo.extension"))
        self.assertIsNone(self.host.find_installed_extension_dir("Bar", [self.ext_root]))


if __name__ == "__main__":
    unittest.main()
