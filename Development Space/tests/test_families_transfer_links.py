"""Run the Revit-links path of Families Transfer against fake Revit objects.

Everything here exists because the one question the source cannot answer is
whether ``Document.EditFamily`` works on a linked document.  The tool is
built as a cascade around that unknown, and a cascade is only worth having if
every rung has been shown to work - so all three are driven here: the linked
file already open in the session, ``EditFamily`` on the link itself, and the
``CopyElements`` fallback when the link refuses.

This is the first test to import a ``*_revit`` module.  It follows the fakes
pattern of ``test_coordination_review_passive.py`` and restores ``sys.modules``
afterwards so the stubs cannot leak into the rest of the suite.
"""

import importlib.util
import os
import shutil
import pathlib
import sys
import tempfile
import types
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Families Transfer.pushbutton"
)


# --------------------------------------------------------------------------
# fake Revit
# --------------------------------------------------------------------------

class FakeElementId(object):
    InvalidElementId = None  # replaced below, once the class exists

    def __init__(self, value):
        self.Value = value
        self.IntegerValue = value

    def __eq__(self, other):
        return isinstance(other, FakeElementId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


FakeElementId.InvalidElementId = FakeElementId(-1)


class FakeFamilySymbol(object):
    def __init__(self, family):
        self.Family = family


class FakeTextElementType(object):
    """TextElement.Symbol returns one of these, not a FamilySymbol."""


class FakeTypeHost(object):
    """The minimum Document an element needs to resolve its own type."""

    def __init__(self, type_element):
        self._type_element = type_element

    def GetElement(self, element_id):
        del element_id
        return self._type_element


class FakeFamily(object):
    def __init__(self, element_id, name, category="Doors", in_place=False, editable=True):
        self.Id = FakeElementId(element_id)
        self.Name = name
        self.IsInPlace = in_place
        self.IsEditable = editable
        self.FamilyCategory = types.SimpleNamespace(Name=category)


class FakeLinkInstance(object):
    def __init__(self, name, link_doc):
        self.Name = name
        self._link_doc = link_doc

    def GetLinkDocument(self):
        return self._link_doc


class FakeFamilyDocument(object):
    def __init__(self, family):
        self.family = family
        self.closed = False
        self.saved_as = None
        self.loaded_into = []

    def LoadFamily(self, target_doc, load_options):
        del load_options
        self.loaded_into.append(target_doc.Title)
        return True

    def SaveAs(self, path, options):
        del options
        self.saved_as = path

    def Close(self, save):
        del save
        self.closed = True


class FakeDocument(object):
    def __init__(self, title, path, families=None, links=None,
                 is_linked=False, read_only=False, edit_error=None):
        self.Title = title
        self.PathName = path
        self.IsFamilyDocument = False
        self.IsLinked = is_linked
        self.IsReadOnly = read_only
        self.families = list(families or [])
        self.links = list(links or [])
        self.edit_error = edit_error
        self.edited = []
        self.copied_in = []
        self.closed = False
        # Every argument Close was called with, so a bare Close() - which
        # saves - cannot pass unnoticed.
        self.close_saved = []

    def GetElement(self, element_id):
        for family in self.families:
            if family.Id.Value == element_id.Value:
                return family
        return None

    def EditFamily(self, family):
        self.edited.append(family.Name)
        if self.edit_error:
            raise Exception(self.edit_error)
        return FakeFamilyDocument(family)

    def Close(self, save=True):
        self.close_saved.append(bool(save))
        self.closed = True
        return True


class FakeCollector(object):
    def __init__(self, doc):
        self._doc = doc
        self._wanted = None

    def OfClass(self, cls):
        self._wanted = cls
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        if self._wanted is FakeFamily:
            return list(self._doc.families)
        if self._wanted is FakeLinkInstance:
            return list(self._doc.links)
        return []


class FakeTransaction(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.committed = False
        self.rolled_back = False

    def Start(self):
        return True

    def Commit(self):
        self.committed = True

    def RollBack(self):
        self.rolled_back = True


class FakeElementTransformUtils(object):
    @staticmethod
    def CopyElements(source, element_ids, destination, transform, options):
        del source, transform, options
        destination.copied_in.extend(list(element_ids))
        return list(element_ids)


class _IdCollection(list):
    def Add(self, item):
        self.append(item)


class _FakeGenericList(object):
    def __getitem__(self, item):
        del item
        return _IdCollection


class FakeBasicFileInfo(object):
    """Header reader. ``_headers`` is what each path pretends to contain."""

    _headers = {}

    def __init__(self, file_format, is_later):
        self.Format = file_format
        self.IsSavedInLaterVersion = is_later

    @classmethod
    def Extract(cls, path):
        header = cls._headers.get(path)
        if header is None:
            raise Exception("BasicFileInfo storage could not be read")
        return cls(header[0], header[1])


class FakeOpenOptions(object):
    def __init__(self):
        self.DoNotLoadLinks = False
        self.DetachFromCentralOption = None


class FakeApplication(object):
    def __init__(self, documents=None, open_map=None, open_error=None):
        self.Documents = list(documents or [])
        self.VersionNumber = "2026"
        self._open_map = dict(open_map or {})
        self._open_error = open_error
        self.opened = []
        self.purges = 0
        self.failure_handlers = []

    def OpenDocumentFile(self, path, options=None):
        del options
        self.opened.append(str(path))
        if self._open_error:
            raise Exception(self._open_error)
        document = self._open_map.get(str(path))
        if document is None:
            raise Exception("The document can not be opened.")
        return document

    def PurgeReleasedAPIObjects(self):
        self.purges += 1


FAKE_DB = types.SimpleNamespace(
    BasicFileInfo=FakeBasicFileInfo,
    OpenOptions=FakeOpenOptions,
    DetachFromCentralOption=types.SimpleNamespace(
        DetachAndDiscardWorksets="DetachAndDiscardWorksets"),
    ModelPathUtils=types.SimpleNamespace(
        ConvertUserVisiblePathToModelPath=lambda path: path),
    FailureSeverity=types.SimpleNamespace(Warning="Warning"),
    FailureProcessingResult=types.SimpleNamespace(Continue="Continue"),
    FilteredElementCollector=FakeCollector,
    ElementId=FakeElementId,
    FamilySymbol=FakeFamilySymbol,
    Family=FakeFamily,
    RevitLinkInstance=FakeLinkInstance,
    Transform=types.SimpleNamespace(Identity=object()),
    ElementTransformUtils=FakeElementTransformUtils,
    Transaction=FakeTransaction,
    SaveAsOptions=lambda: types.SimpleNamespace(OverwriteExistingFile=False),
    IFamilyLoadOptions=object,
    FamilySource=types.SimpleNamespace(Family="Family"),
)


STUBS = {
    "clr": dict(AddReference=lambda *a, **k: None),
    "Autodesk": {},
    "Autodesk.Revit": {},
    "Autodesk.Revit.UI": {},
    "Autodesk.Revit.UI.Selection": dict(
        ISelectionFilter=object, ObjectType=types.SimpleNamespace(Element=1)),
    "System": {},
    "System.Windows": {},
    "System.Windows.Forms": dict(
        DialogResult=types.SimpleNamespace(OK=1), FolderBrowserDialog=object,
        OpenFileDialog=object),
    "System.Collections": {},
    "System.Collections.Generic": dict(List=_FakeGenericList()),
    "pyrevit": dict(DB=FAKE_DB),
    "pyrevit.compat": dict(get_elementid_value_func=lambda: (lambda eid: eid.Value)),
    "easybim": {},
    "easybim.copy_paste": dict(copy_paste_options=lambda: object()),
}

state = None
ft_revit = None
ft_files = None
_saved_modules = {}
_saved_path = None


def setUpModule():
    """Install the stubs, load both modules, then put sys.modules back."""
    global state, ft_revit, ft_files, _saved_path
    _saved_path = list(sys.path)

    for name, attrs in STUBS.items():
        _saved_modules[name] = sys.modules.get(name)
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module

    sys.path.insert(0, str(COMMAND_DIR))
    for name in ("families_transfer_state", "families_transfer_revit",
                 "families_transfer_files"):
        _saved_modules.setdefault(name, sys.modules.get(name))
        spec = importlib.util.spec_from_file_location(
            name, str(COMMAND_DIR / (name + ".py")))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

    state = sys.modules["families_transfer_state"]
    ft_revit = sys.modules["families_transfer_revit"]
    ft_files = sys.modules["families_transfer_files"]


def tearDownModule():
    sys.path[:] = _saved_path
    for name, previous in _saved_modules.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

ARCH_PATH = "C:/jobs/Arch.rvt"
STRUCT_PATH = "C:/jobs/Struct.rvt"


def build_world(edit_error=None, arch_also_open=False, target_has_door=True):
    """A host with two links (one placed twice) plus one unloaded link."""
    arch_families = [
        FakeFamily(101, "Door_Single", "Doors"),
        FakeFamily(102, "Window_Fixed", "Windows"),
        FakeFamily(103, "InPlace_Mass", "Generic Models", in_place=True),
    ]
    arch_link = FakeDocument("Arch", ARCH_PATH, families=arch_families,
                             is_linked=True, read_only=True, edit_error=edit_error)
    struct_link = FakeDocument(
        "Struct", STRUCT_PATH,
        # Same raw element id as Arch's Door_Single: ids only mean anything
        # inside their own document.
        families=[FakeFamily(101, "Column_W", "Structural Columns")],
        is_linked=True, read_only=True, edit_error=edit_error)

    host = FakeDocument(
        "Host", "C:/jobs/Host.rvt",
        families=[FakeFamily(1, "Host_Wall", "Walls")],
        links=[
            FakeLinkInstance("Arch.rvt : 1", arch_link),
            FakeLinkInstance("Arch.rvt : 2", arch_link),
            FakeLinkInstance("Struct.rvt : 1", struct_link),
            FakeLinkInstance("Missing.rvt : 1", None),
        ])

    target_families = [FakeFamily(9, "Door_Single", "Doors")] if target_has_door else []
    target = FakeDocument("Target", "C:/jobs/Target.rvt", families=target_families)

    open_documents = [host, target]
    if arch_also_open:
        open_documents.append(
            FakeDocument("Arch", ARCH_PATH, families=arch_families))

    uiapp = types.SimpleNamespace(
        Application=types.SimpleNamespace(Documents=open_documents))
    return types.SimpleNamespace(host=host, target=target, uiapp=uiapp)


def checked_links(world):
    links = ft_revit.get_link_document_options(world.host)
    for link in links:
        link.is_selected = bool(link.is_loaded)
    ft_revit.prepare_link_documents(world.uiapp, world.host, links)
    return links


def target_options(world):
    return [state.TargetDocumentOption(
        "Target.rvt", "path|target", document=world.target)]


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

class LinkListingTests(unittest.TestCase):
    def test_two_instances_of_one_link_produce_one_row(self):
        links = ft_revit.get_link_document_options(build_world().host)

        self.assertEqual(
            ["Arch.rvt", "Missing.rvt", "Struct.rvt"],
            sorted(link.display_name for link in links))

    def test_an_unloaded_link_is_listed_but_marked_not_loaded(self):
        # Hiding it would read as "the tool cannot see my link".
        links = ft_revit.get_link_document_options(build_world().host)
        by_name = dict((link.display_name, link) for link in links)

        self.assertFalse(by_name["Missing.rvt"].is_loaded)
        self.assertEqual("not loaded", by_name["Missing.rvt"].note)
        self.assertTrue(by_name["Arch.rvt"].is_loaded)

    def test_a_link_is_keyed_by_its_path(self):
        links = ft_revit.get_link_document_options(
            build_world().host, {"path|" + ARCH_PATH.lower()})
        by_name = dict((link.display_name, link) for link in links)

        self.assertTrue(by_name["Arch.rvt"].is_selected)
        self.assertFalse(by_name["Struct.rvt"].is_selected)


class LinkFamilyCollectionTests(unittest.TestCase):
    def test_in_place_families_are_never_offered(self):
        families = ft_revit.get_link_family_options(checked_links(build_world()))

        self.assertNotIn("InPlace_Mass", [family.name for family in families])
        self.assertEqual(
            ["Column_W", "Door_Single", "Window_Fixed"],
            sorted(family.name for family in families))

    def test_the_same_element_id_in_two_links_stays_two_families(self):
        families = ft_revit.get_link_family_options(checked_links(build_world()))
        keys = [f.family_key for f in families if f.name in ("Door_Single", "Column_W")]

        self.assertEqual(2, len(set(keys)))
        for key in keys:
            self.assertTrue(state.is_link_family_key(key))

    def test_every_row_names_the_link_it_came_from(self):
        families = ft_revit.get_link_family_options(checked_links(build_world()))
        by_name = dict((family.name, family) for family in families)

        self.assertEqual("Arch.rvt", by_name["Door_Single"].source_label)
        self.assertEqual("Struct.rvt", by_name["Column_W"].source_label)
        self.assertEqual(state.SOURCE_LINK, by_name["Door_Single"].source_kind)

    def test_unchecked_links_are_not_read_at_all(self):
        world = build_world()
        links = ft_revit.get_link_document_options(world.host)

        self.assertEqual([], ft_revit.get_link_family_options(links))

    def test_a_link_is_read_once_and_then_cached(self):
        world = build_world()
        links = checked_links(world)
        cache = {}

        first = ft_revit.get_link_family_options(links, cache=cache)
        second = ft_revit.get_link_family_options(links, cache=cache)

        self.assertEqual(len(first), len(second))
        self.assertEqual(2, len(cache))


class ProbeTests(unittest.TestCase):
    def test_a_link_that_hands_over_a_family_is_extractable(self):
        links = checked_links(build_world())

        for link in links:
            if link.is_loaded:
                self.assertTrue(link.is_extractable, link.display_name)
                self.assertEqual("", link.note)

    def test_a_refusal_is_recorded_once_per_link_not_per_family(self):
        links = checked_links(build_world(edit_error="The document is read-only."))
        arch = [link for link in links if link.display_name == "Arch.rvt"][0]

        self.assertFalse(arch.is_extractable)
        self.assertIn("read-only", arch.note)
        # One EditFamily attempt for the whole link, not one per family.
        self.assertEqual(1, len(arch.document.edited))

    def test_an_open_copy_of_the_linked_file_is_used_instead(self):
        world = build_world(edit_error="The document is read-only.", arch_also_open=True)
        links = checked_links(world)
        by_name = dict((link.display_name, link) for link in links)

        # Arch is open in the session, so the read-only question never arises.
        self.assertTrue(by_name["Arch.rvt"].is_extractable)
        self.assertIn("already open", by_name["Arch.rvt"].note)
        # Struct is only a link, and it still refuses.
        self.assertFalse(by_name["Struct.rvt"].is_extractable)

    def test_the_verdict_is_cached_and_not_reprobed(self):
        world = build_world()
        links = checked_links(world)
        arch = [link for link in links if link.display_name == "Arch.rvt"][0]

        ft_revit.prepare_link_documents(world.uiapp, world.host, links)

        self.assertEqual(1, len(arch.document.edited))


class TransferTests(unittest.TestCase):
    def _transfer(self, world, progress=None):
        links = checked_links(world)
        families = ft_revit.get_link_family_options(links)
        for family in families:
            family.is_selected = True
        summary = ft_revit.transfer_families(
            world.host, families, target_options(world), progress=progress)
        return links, families, summary

    def test_an_editable_link_loads_through_the_family_document(self):
        world = build_world()
        _, families, summary = self._transfer(world)

        self.assertEqual(3, len(summary.loaded))
        self.assertEqual([], summary.failed)
        self.assertEqual(
            set(["loaded"]), set(result.status for result in summary.loaded))
        self.assertEqual([], world.target.copied_in)

    def test_a_refusing_link_falls_back_to_copying_into_the_target(self):
        world = build_world(edit_error="The document is read-only.")
        _, _, summary = self._transfer(world)
        statuses = dict((r.family_name, r.status) for r in summary.loaded)

        self.assertEqual("copied from link", statuses["Window_Fixed"])
        self.assertEqual("copied from link", statuses["Column_W"])
        self.assertEqual(2, len(world.target.copied_in))

    def test_a_copy_never_silently_loses_to_a_family_already_in_the_target(self):
        # UseDestinationTypes means the target's own Door_Single wins, so a
        # copy would report success having changed nothing.
        world = build_world(edit_error="The document is read-only.")
        _, _, summary = self._transfer(world)
        skipped = dict((r.family_name, r.status) for r in summary.skipped)

        self.assertIn("Door_Single", skipped)
        self.assertIn("cannot overwrite", skipped["Door_Single"])
        self.assertNotIn("Door_Single", [r.family_name for r in summary.loaded])

    def test_the_copy_route_works_when_the_target_is_empty(self):
        world = build_world(edit_error="read-only", target_has_door=False)
        _, _, summary = self._transfer(world)

        self.assertEqual(3, len(summary.loaded))
        self.assertEqual([], summary.skipped)

    def test_cancelling_stops_the_batch_and_says_where(self):
        world = build_world()
        _, _, summary = self._transfer(world, progress=lambda done, total: done < 1)

        self.assertTrue(summary.cancelled)
        self.assertEqual(1, len(summary.loaded))
        self.assertTrue(any("Cancelled after 1 of 3" in note for note in summary.notes))
        self.assertIn("cancelled", state.build_transfer_summary_text(summary).lower())

    def test_progress_is_reported_once_per_family(self):
        world = build_world()
        ticks = []

        def _tick(done, total):
            ticks.append((done, total))
            return True

        self._transfer(world, progress=_tick)

        self.assertEqual([(0, 3), (1, 3), (2, 3)], ticks)

    def test_a_project_family_still_transfers_unchanged(self):
        world = build_world()
        families = ft_revit.get_source_family_options(world.host)
        for family in families:
            family.is_selected = True

        summary = ft_revit.transfer_families(
            world.host, families, target_options(world))

        self.assertEqual(["Host_Wall"], [r.family_name for r in summary.loaded])
        self.assertEqual(["Host_Wall"], world.host.edited)


class ExportTests(unittest.TestCase):
    def test_an_editable_link_exports_one_rfa_per_family(self):
        world = build_world()
        families = ft_revit.get_link_family_options(checked_links(world))

        summary = ft_revit.export_families(world.host, families, "C:/out")

        self.assertEqual(3, len(summary.loaded))
        self.assertTrue(all(
            result.target_name.endswith(".rfa") for result in summary.loaded))

    def test_a_refusing_link_reports_that_export_has_no_fallback(self):
        # Writing an .rfa needs a family document, which is the thing the
        # link refused; there is no copy-based route to a file.
        world = build_world(edit_error="The document is read-only.")
        families = ft_revit.get_link_family_options(checked_links(world))

        summary = ft_revit.export_families(world.host, families, "C:/out")

        self.assertEqual([], summary.loaded)
        self.assertEqual(3, len(summary.skipped))
        for result in summary.skipped:
            self.assertIn("cannot be exported", result.status)

    def test_two_links_holding_one_name_export_to_two_files(self):
        world = build_world(target_has_door=False)
        links = checked_links(world)
        families = ft_revit.get_link_family_options(links)
        families = [f for f in families if f.name in ("Door_Single", "Column_W")]
        families[0].name = "Door_Single"
        families[1].name = "Door_Single"

        summary = ft_revit.export_families(world.host, families, "C:/out")
        paths = [result.target_name for result in summary.loaded]

        self.assertEqual(2, len(set(paths)))


class CloseGuardTests(unittest.TestCase):
    def test_a_linked_document_is_never_closed(self):
        linked = FakeDocument("Arch", ARCH_PATH, is_linked=True)
        plain = FakeDocument("Desk", "C:/jobs/Desk.rfa")

        summary = ft_revit.close_open_family_documents([
            state.OpenFamilyDocumentOption("Arch.rvt", "k1", document=linked),
            state.OpenFamilyDocumentOption("Desk.rfa", "k2", document=plain),
        ])

        self.assertFalse(linked.closed)
        self.assertTrue(plain.closed)
        self.assertEqual(1, summary.closed_rfa_count)
        self.assertEqual(
            ["linked documents are never closed"],
            [result.status for result in summary.skipped])


class ProjectFileSourceTests(unittest.TestCase):
    """Families out of `.rvt` files that are never opened in a tab.

    The three things that would be silently wrong rather than loudly broken:
    a newer file must be refused from its header, an older file must be
    upgraded in memory only, and no document may ever be closed with a save.
    """

    def setUp(self):
        # Real (empty) files on disk: inspect_project_files checks
        # os.path.isfile before it reads a header, and that guard is part of
        # what is being tested.
        self._folder = tempfile.mkdtemp(prefix="easybim-ft-")
        self.addCleanup(shutil.rmtree, self._folder, True)

        def _make(name):
            path = os.path.join(self._folder, name)
            with open(path, "wb") as handle:
                handle.write(b"")
            return path

        self.CURRENT = _make("Current.rvt")
        self.OLDER = _make("Older.rvt")
        self.NEWER = _make("Newer.rvt")
        self.BROKEN = _make("Broken.rvt")
        self.MISSING = os.path.join(self._folder, "Gone.rvt")

        FakeBasicFileInfo._headers = {
            self.CURRENT: ("2026", False),
            self.OLDER: ("2021", False),
            self.NEWER: ("", True),
            # BROKEN is deliberately absent: Extract throws for it.
        }
        self.current_doc = FakeDocument(
            "Current", self.CURRENT,
            families=[FakeFamily(11, "Door_Single", "Doors"),
                      FakeFamily(12, "InPlace_Mass", "Generic Models", in_place=True)])
        self.older_doc = FakeDocument(
            "Older", self.OLDER,
            families=[FakeFamily(11, "Column_W", "Structural Columns")])
        self.app = FakeApplication(open_map={
            self.CURRENT: self.current_doc,
            self.OLDER: self.older_doc,
        })

    def _inspect(self, paths):
        return ft_files.inspect_project_files(paths)

    def test_a_newer_file_is_refused_without_being_opened(self):
        options = self._inspect([self.NEWER])
        by_name = dict((o.display_name, o) for o in options)

        self.assertFalse(by_name["Newer.rvt"].is_readable)
        self.assertIn("newer Revit", by_name["Newer.rvt"].note)
        self.assertFalse(by_name["Newer.rvt"].is_selected)
        # The whole point: no open was attempted.
        self.assertEqual([], self.app.opened)

    def test_a_path_that_is_not_there_is_reported_as_missing(self):
        options = self._inspect([self.MISSING])

        self.assertFalse(options[0].is_readable)
        self.assertIn("missing", options[0].note)
        self.assertEqual([], self.app.opened)

    def test_an_unreadable_header_says_so_rather_than_guessing(self):
        options = self._inspect([self.BROKEN])

        self.assertFalse(options[0].is_readable)
        self.assertIn("header", options[0].note)

    def test_an_older_file_is_accepted_and_its_version_recorded(self):
        options = self._inspect([self.OLDER])

        self.assertTrue(options[0].is_readable)
        self.assertEqual("2021", options[0].file_version)
        self.assertTrue(options[0].is_selected)

    def test_a_row_says_an_older_file_is_upgraded_in_memory_only(self):
        options = self._inspect([self.OLDER, self.CURRENT])
        by_name = dict((o.display_name, o) for o in options)

        older = state.format_project_file_label(
            by_name["Older.rvt"].display_name, by_name["Older.rvt"].file_version,
            "2026", by_name["Older.rvt"].note)
        current = state.format_project_file_label(
            by_name["Current.rvt"].display_name, by_name["Current.rvt"].file_version,
            "2026", by_name["Current.rvt"].note)

        self.assertIn("in memory only", older)
        self.assertNotIn("in memory only", current)

    def test_the_same_file_is_never_listed_twice(self):
        options = ft_files.inspect_project_files(
            [self.CURRENT], ft_files.inspect_project_files([self.CURRENT]))

        self.assertEqual(1, len(options))

    def test_scanning_opens_each_checked_file_once_and_leaves_it_open(self):
        options = self._inspect([self.CURRENT, self.OLDER])
        rows, notes, cancelled = ft_files.scan_project_files(self.app, options)

        self.assertFalse(cancelled)
        self.assertEqual([], notes)
        self.assertEqual(2, len(self.app.opened))
        # Held open on purpose: EditFamily needs the owning document, and
        # closing here would invalidate every handle just collected.
        self.assertIsNotNone(options[0].document)
        self.assertEqual(2, self.app.purges)

        self.assertEqual(["Column_W", "Door_Single"],
                         sorted(row.name for row in rows))
        self.assertNotIn("InPlace_Mass", [row.name for row in rows])

    def test_an_unchecked_file_is_never_opened(self):
        options = self._inspect([self.CURRENT, self.OLDER])
        for option in options:
            option.is_selected = option.display_name == "Older.rvt"

        ft_files.scan_project_files(self.app, options)

        self.assertEqual([self.OLDER], self.app.opened)

    def test_every_row_names_the_file_it_came_from(self):
        options = self._inspect([self.CURRENT, self.OLDER])
        rows, _, _ = ft_files.scan_project_files(self.app, options)
        by_name = dict((row.name, row) for row in rows)

        self.assertEqual("Current.rvt", by_name["Door_Single"].source_label)
        self.assertEqual(state.SOURCE_FILE, by_name["Door_Single"].source_kind)
        self.assertIs(self.current_doc, by_name["Door_Single"].source_document)

    def test_two_files_holding_one_element_id_stay_two_families(self):
        # Both fixtures use id 11 on purpose.
        options = self._inspect([self.CURRENT, self.OLDER])
        rows, _, _ = ft_files.scan_project_files(self.app, options)
        keys = [row.family_key for row in rows]

        self.assertEqual(2, len(set(keys)))
        for key in keys:
            self.assertTrue(state.is_file_family_key(key))

    def test_a_cancel_lands_between_files(self):
        options = self._inspect([self.CURRENT, self.OLDER])
        rows, _, cancelled = ft_files.scan_project_files(
            self.app, options, progress=lambda done, total: done < 1)

        self.assertTrue(cancelled)
        self.assertEqual(1, len(self.app.opened))
        self.assertEqual(1, len(rows))

    def test_a_file_that_will_not_open_is_reported_not_raised(self):
        options = self._inspect([self.CURRENT])
        app = FakeApplication(open_map={})

        rows, notes, _ = ft_files.scan_project_files(app, options)

        self.assertEqual([], rows)
        self.assertEqual(1, len(notes))
        self.assertFalse(options[0].is_readable)
        self.assertEqual(notes, ft_files.file_notes(options))

    def test_closing_never_saves(self):
        # Document.Close() saves; an older file upgraded on open counts as
        # modified, so a bare close would rewrite the user's file.
        options = self._inspect([self.CURRENT, self.OLDER])
        ft_files.scan_project_files(self.app, options)

        closed = ft_files.close_project_documents(options)

        self.assertEqual(2, closed)
        self.assertTrue(self.current_doc.closed)
        self.assertTrue(self.older_doc.closed)
        self.assertEqual([False, False], self.current_doc.close_saved
                         + self.older_doc.close_saved)
        for option in options:
            self.assertIsNone(option.document)

    def test_the_open_is_cheap_and_detached(self):
        options = self._inspect([self.CURRENT])
        ft_files.scan_project_files(self.app, options)
        opened_options = ft_files._open_options()

        self.assertTrue(opened_options.DoNotLoadLinks)
        self.assertEqual("DetachAndDiscardWorksets",
                         opened_options.DetachFromCentralOption)


class ElementToFamilyTests(unittest.TestCase):
    """What the user can click and have land in the list.

    Exactly two classes in the Revit API declare ``Symbol`` - FamilyInstance
    and TextElement - so every tag has to go the GetTypeId route, and
    TextElement.Symbol returning a TextElementType is why the isinstance
    guard in _family_from_element is load-bearing.
    """

    def setUp(self):
        self.family = FakeFamily(500, "Door_Tag", "Door Tags")
        self.symbol = FakeFamilySymbol(self.family)

    def test_a_family_instance_resolves_through_its_symbol(self):
        instance = types.SimpleNamespace(Symbol=self.symbol)

        self.assertIs(self.family, ft_revit._family_from_element(instance))

    def test_a_tag_resolves_through_its_type_id(self):
        # IndependentTag derives from Element and has no Symbol at all.
        tag = types.SimpleNamespace(
            GetTypeId=lambda: FakeElementId(500),
            Document=FakeTypeHost(self.symbol),
        )

        self.assertIs(self.family, ft_revit._family_from_element(tag))

    def test_a_spatial_tag_resolves_the_same_way(self):
        room_tag = types.SimpleNamespace(
            GetTypeId=lambda: FakeElementId(500),
            Document=FakeTypeHost(self.symbol),
        )

        self.assertIs(self.family, ft_revit._family_from_element(room_tag))

    def test_a_text_note_is_not_mistaken_for_a_family(self):
        # TextElement.Symbol succeeds and returns the wrong kind of object.
        text_note = types.SimpleNamespace(
            Symbol=FakeTextElementType(),
            GetTypeId=lambda: FakeElementId(700),
            Document=FakeTypeHost(FakeTextElementType()),
        )

        self.assertIsNone(ft_revit._family_from_element(text_note))

    def test_a_matchline_yields_nothing_because_it_has_no_type(self):
        # A matchline is a sketched system element: no Family, no
        # FamilySymbol, and GetTypeId reports InvalidElementId.
        matchline = types.SimpleNamespace(
            GetTypeId=lambda: FakeElementId.InvalidElementId,
            Document=FakeTypeHost(None),
        )

        self.assertIsNone(ft_revit._family_from_element(matchline))

    def test_a_family_element_passes_straight_through(self):
        self.assertIs(self.family, ft_revit._family_from_element(self.family))

    def test_none_and_junk_are_survived(self):
        self.assertIsNone(ft_revit._family_from_element(None))
        self.assertIsNone(ft_revit._family_from_element(object()))

    def test_the_selection_filter_accepts_a_tag(self):
        tag = types.SimpleNamespace(
            GetTypeId=lambda: FakeElementId(500),
            Document=FakeTypeHost(self.symbol),
        )

        self.assertTrue(ft_revit.FamilyTransferSelectionFilter().AllowElement(tag))

    def test_the_selection_filter_rejects_a_matchline(self):
        matchline = types.SimpleNamespace(
            GetTypeId=lambda: FakeElementId.InvalidElementId,
            Document=FakeTypeHost(None),
        )

        self.assertFalse(ft_revit.FamilyTransferSelectionFilter().AllowElement(matchline))


class ResolveTests(unittest.TestCase):
    def test_a_link_family_resolves_against_its_own_document(self):
        world = build_world()
        families = ft_revit.get_link_family_options(checked_links(world))
        door = [f for f in families if f.name == "Door_Single"][0]
        door.family = None

        resolved = ft_revit.resolve_family(world.host, door)

        self.assertIsNotNone(resolved)
        self.assertEqual("Door_Single", resolved.Name)

    def test_the_fallback_scan_runs_once_per_document_not_once_per_family(self):
        world = build_world()
        families = ft_revit.get_link_family_options(checked_links(world))
        for family in families:
            family.family = None
            family.element_id = None

        index_cache = {}
        resolved = [
            ft_revit.resolve_family(world.host, family, index_cache)
            for family in families
        ]

        self.assertEqual(3, len([item for item in resolved if item is not None]))
        self.assertEqual(2, len(index_cache))


if __name__ == "__main__":
    unittest.main()
