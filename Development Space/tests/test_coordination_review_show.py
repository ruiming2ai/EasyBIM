import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "coordination_review_show.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("coordination_review_show", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeElementId(object):
    def __init__(self, value):
        self.IntegerValue = int(value)
        self.Value = int(value)

    def __int__(self):
        return int(self.IntegerValue)

    def __eq__(self, other):
        return int(self) == int(other)

    def __hash__(self):
        return hash(int(self))


class RevitLinkInstance(object):
    def __init__(self, element_id, name, type_id):
        self.Id = FakeElementId(element_id)
        self.Name = name
        self._type_id = FakeElementId(type_id)

    def GetTypeId(self):
        return self._type_id


class RevitLinkType(object):
    def __init__(self, element_id, name):
        self.Id = FakeElementId(element_id)
        self.Name = name


class Wall(object):
    def __init__(self, element_id):
        self.Id = FakeElementId(element_id)
        self.Name = "Wall"


class FakeCollector(object):
    def __init__(self, doc):
        self._doc = doc
        self._class = None

    def OfClass(self, cls):
        self._class = cls
        return self

    def ToElements(self):
        return [e for e in self._doc.elements.values() if isinstance(e, self._class)]


class FakeDB(object):
    ElementId = FakeElementId
    RevitLinkInstance = RevitLinkInstance
    RevitLinkType = RevitLinkType
    FilteredElementCollector = FakeCollector


class FakeDocument(object):
    def __init__(self, elements):
        self.elements = dict((int(e.Id), e) for e in elements)

    def GetElement(self, element_id):
        return self.elements.get(int(element_id))


class FakeSelection(object):
    def __init__(self):
        self.ids = None

    def SetElementIds(self, ids):
        self.ids = list(ids)


class FakeUIDocument(object):
    def __init__(self, fail_select=False, fail_show=False):
        self.Selection = FakeSelection()
        self.shown = None
        self._fail_select = fail_select
        self._fail_show = fail_show
        if fail_select:
            self.Selection.SetElementIds = self._raise

    def _raise(self, *args):
        raise RuntimeError("selection failed")

    def ShowElements(self, ids):
        if self._fail_show:
            raise RuntimeError("show failed")
        self.shown = list(ids)


class FakeUIApplication(object):
    def __init__(self, can_post=True, fail_post=False):
        self.posted = []
        self._can_post = can_post
        self._fail_post = fail_post

    def CanPostCommand(self, command_id):
        return self._can_post

    def PostCommand(self, command_id):
        if self._fail_post:
            raise RuntimeError("post failed")
        self.posted.append(command_id)


class FakePostableCommand(object):
    SelectLink = "PostableCommand.SelectLink"
    UseCurrentProject = "PostableCommand.UseCurrentProject"


class FakeRevitCommandId(object):
    @staticmethod
    def LookupPostableCommandId(member):
        return ("command-id", member)


class FakeUI(object):
    PostableCommand = FakePostableCommand
    RevitCommandId = FakeRevitCommandId


class FakeUIWithoutSelectLink(object):
    class PostableCommand(object):
        UseCurrentProject = "PostableCommand.UseCurrentProject"

    RevitCommandId = FakeRevitCommandId


def _fixture():
    link_type = RevitLinkType(10, "ARCH.rvt")
    instance = RevitLinkInstance(11, "ARCH.rvt : 1", 10)
    orphan_type = RevitLinkType(20, "MEP.rvt")
    wall = Wall(30)
    doc = FakeDocument([link_type, instance, orphan_type, wall])
    return doc, link_type, instance, orphan_type, wall


class ResolveLinkInstanceTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc, self.link_type, self.instance, self.orphan_type, self.wall = _fixture()

    def test_instance_id_resolves_to_itself(self):
        instance, error = self.module.resolve_link_instance(self.doc, 11, db=FakeDB)
        self.assertIs(instance, self.instance)
        self.assertEqual(error, "")

    def test_type_id_resolves_to_placed_instance(self):
        instance, error = self.module.resolve_link_instance(self.doc, "10", db=FakeDB)
        self.assertIs(instance, self.instance)
        self.assertEqual(error, "")

    def test_type_without_instance_reports_error(self):
        instance, error = self.module.resolve_link_instance(self.doc, 20, db=FakeDB)
        self.assertIsNone(instance)
        self.assertIn("no placed link instance", error)

    def test_non_link_element_reports_error(self):
        instance, error = self.module.resolve_link_instance(self.doc, 30, db=FakeDB)
        self.assertIsNone(instance)
        self.assertIn("not a Revit link", error)

    def test_missing_element_reports_error(self):
        instance, error = self.module.resolve_link_instance(self.doc, 999, db=FakeDB)
        self.assertIsNone(instance)
        self.assertIn("no longer available", error)

    def test_invalid_id_reports_error(self):
        instance, error = self.module.resolve_link_instance(self.doc, "abc", db=FakeDB)
        self.assertIsNone(instance)
        self.assertIn("Could not resolve", error)

    def test_no_document_reports_error(self):
        instance, error = self.module.resolve_link_instance(None, 11, db=FakeDB)
        self.assertIsNone(instance)
        self.assertIn("no active Revit document", error)


class CommandResolutionTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_select_link_is_preferred(self):
        command_id, member = self.module.resolve_coordination_review_command(ui=FakeUI)
        self.assertEqual(member, "SelectLink")
        self.assertEqual(command_id, ("command-id", FakePostableCommand.SelectLink))

    def test_missing_member_yields_none(self):
        command_id, member = self.module.resolve_coordination_review_command(
            ui=FakeUIWithoutSelectLink
        )
        self.assertIsNone(command_id)
        self.assertEqual(member, "")

    def test_missing_ui_api_yields_none(self):
        class EmptyUI(object):
            pass

        command_id, member = self.module.resolve_coordination_review_command(ui=EmptyUI)
        self.assertIsNone(command_id)
        self.assertEqual(member, "")


class ShowLinkCoordinationReviewTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc, self.link_type, self.instance, self.orphan_type, self.wall = _fixture()
        self.uidoc = FakeUIDocument()
        self.uiapp = FakeUIApplication()

    def _show(self, element_id, uiapp=None, uidoc=None, ui=FakeUI):
        return self.module.show_link_coordination_review(
            uiapp or self.uiapp,
            uidoc or self.uidoc,
            self.doc,
            element_id,
            db=FakeDB,
            ui=ui,
        )

    def test_selects_zooms_and_posts_for_link_instance(self):
        result = self._show(11)
        self.assertTrue(result["ok"])
        self.assertTrue(result["selected"])
        self.assertTrue(result["posted"])
        self.assertEqual(result["link_name"], "ARCH.rvt : 1")
        self.assertEqual([int(i) for i in self.uidoc.Selection.ids], [11])
        self.assertEqual([int(i) for i in self.uidoc.shown], [11])
        self.assertEqual(self.uiapp.posted, [("command-id", FakePostableCommand.SelectLink)])
        self.assertIn("Opening Coordination Review for ARCH.rvt : 1", result["message"])

    def test_link_type_id_selects_its_instance(self):
        result = self._show(10)
        self.assertTrue(result["ok"])
        self.assertEqual([int(i) for i in self.uidoc.Selection.ids], [11])
        self.assertEqual(len(self.uiapp.posted), 1)

    def test_unresolvable_element_does_not_touch_revit(self):
        result = self._show(30)
        self.assertFalse(result["ok"])
        self.assertFalse(result["selected"])
        self.assertFalse(result["posted"])
        self.assertIsNone(self.uidoc.Selection.ids)
        self.assertEqual(self.uiapp.posted, [])
        self.assertIn("not a Revit link", result["message"])

    def test_selection_failure_skips_post(self):
        uidoc = FakeUIDocument(fail_select=True)
        result = self._show(11, uidoc=uidoc)
        self.assertFalse(result["ok"])
        self.assertFalse(result["selected"])
        self.assertFalse(result["posted"])
        self.assertEqual(self.uiapp.posted, [])
        self.assertIn("Could not select link", result["message"])

    def test_zoom_failure_is_tolerated(self):
        uidoc = FakeUIDocument(fail_show=True)
        result = self._show(11, uidoc=uidoc)
        self.assertTrue(result["ok"])
        self.assertTrue(result["selected"])
        self.assertEqual(len(self.uiapp.posted), 1)

    def test_cannot_post_keeps_selection_and_reports(self):
        uiapp = FakeUIApplication(can_post=False)
        result = self._show(11, uiapp=uiapp)
        self.assertFalse(result["ok"])
        self.assertTrue(result["selected"])
        self.assertFalse(result["posted"])
        self.assertEqual(uiapp.posted, [])
        self.assertIn("cannot open Coordination Review", result["message"])
        self.assertIn("link is selected", result["message"])

    def test_post_exception_is_reported(self):
        uiapp = FakeUIApplication(fail_post=True)
        result = self._show(11, uiapp=uiapp)
        self.assertFalse(result["ok"])
        self.assertTrue(result["selected"])
        self.assertFalse(result["posted"])
        self.assertIn("post failed", result["message"])

    def test_missing_command_member_is_reported(self):
        result = self._show(11, ui=FakeUIWithoutSelectLink)
        self.assertFalse(result["ok"])
        self.assertTrue(result["selected"])
        self.assertIn("not available in this version", result["message"])

    def test_missing_uiapp_is_reported(self):
        result = self.module.show_link_coordination_review(
            None, self.uidoc, self.doc, 11, db=FakeDB, ui=FakeUI
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["selected"])
        self.assertIn("no Revit application", result["message"])


if __name__ == "__main__":
    unittest.main()
