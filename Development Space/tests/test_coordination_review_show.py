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


class FakeBoundingBox(object):
    Min = "bbox-min"
    Max = "bbox-max"


class RevitLinkInstance(object):
    def __init__(self, element_id, name, type_id, visible=True):
        self.Id = FakeElementId(element_id)
        self.Name = name
        self._type_id = FakeElementId(type_id)
        self._visible = visible

    def GetTypeId(self):
        return self._type_id

    def get_BoundingBox(self, view):
        return FakeBoundingBox() if self._visible else None


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


class FakeView(object):
    def __init__(self, element_id):
        self.Id = FakeElementId(element_id)


class FakeUIView(object):
    def __init__(self, view_id, fail_zoom=False):
        self.ViewId = FakeElementId(view_id)
        self.zoomed = None
        self._fail_zoom = fail_zoom

    def ZoomAndCenterRectangle(self, corner_a, corner_b):
        if self._fail_zoom:
            raise RuntimeError("zoom failed")
        self.zoomed = (corner_a, corner_b)


ACTIVE_VIEW_ID = 100


class FakeUIDocument(object):
    """Selection, active view and open UI views only: the View Issues flow
    must never open or search other views, so there is deliberately no
    ``ShowElements`` here."""

    def __init__(self, fail_select=False, ui_view=None, ui_views=None):
        self.Selection = FakeSelection()
        self.ActiveView = FakeView(ACTIVE_VIEW_ID)
        self.ui_view = FakeUIView(ACTIVE_VIEW_ID) if ui_view is None else ui_view
        self._ui_views = [self.ui_view] if ui_views is None else list(ui_views)
        if fail_select:
            self.Selection.SetElementIds = self._raise

    def _raise(self, *args):
        raise RuntimeError("selection failed")

    def GetOpenUIViews(self):
        return list(self._ui_views)


class FakeUIApplication(object):
    def __init__(self, can_post=True, fail_post=False, events=None):
        self.posted = []
        self._can_post = can_post
        self._fail_post = fail_post
        self._events = events

    def CanPostCommand(self, command_id):
        return self._can_post

    def PostCommand(self, command_id):
        if self._fail_post:
            raise RuntimeError("post failed")
        self.posted.append(command_id)
        if self._events is not None:
            self._events.append("post")


class FakeUIApplicationWithoutCanPost(object):
    def __init__(self):
        self.posted = []

    def PostCommand(self, command_id):
        self.posted.append(command_id)


class FakeRevitCommandId(object):
    @staticmethod
    def LookupPostableCommandId(member):
        return ("command-id", member)


class ModernUI(object):
    """Revit 2022+: CoordinationSelectLink; plain SelectLink no longer exists."""

    class PostableCommand(object):
        CoordinationSelectLink = "PostableCommand.CoordinationSelectLink"
        CoordinationReviewUseCurrentProject = "PostableCommand.CoordinationReviewUseCurrentProject"
        CopyMonitorSelectLink = "PostableCommand.CopyMonitorSelectLink"

    RevitCommandId = FakeRevitCommandId


class LegacyUI(object):
    """Revit 2021 and earlier: SelectLink / UseCurrentProject."""

    class PostableCommand(object):
        SelectLink = "PostableCommand.SelectLink"
        UseCurrentProject = "PostableCommand.UseCurrentProject"

    RevitCommandId = FakeRevitCommandId


class UIWithBothNames(object):
    class PostableCommand(object):
        SelectLink = "PostableCommand.SelectLink"
        CoordinationSelectLink = "PostableCommand.CoordinationSelectLink"

    RevitCommandId = FakeRevitCommandId


class UIWithoutCoordinationReview(object):
    class PostableCommand(object):
        CopyMonitorSelectLink = "PostableCommand.CopyMonitorSelectLink"

    RevitCommandId = FakeRevitCommandId


def _fixture():
    link_type = RevitLinkType(10, "ARCH.rvt")
    instance = RevitLinkInstance(11, "ARCH.rvt : 1", 10)
    orphan_type = RevitLinkType(20, "MEP.rvt")
    hidden_type = RevitLinkType(40, "STR.rvt")
    hidden_instance = RevitLinkInstance(41, "STR.rvt : 1", 40, visible=False)
    wall = Wall(30)
    doc = FakeDocument([link_type, instance, orphan_type, hidden_type, hidden_instance, wall])
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

    def test_command_order_is_modern_name_first(self):
        self.assertEqual(
            self.module.COORDINATION_REVIEW_COMMANDS,
            ("CoordinationSelectLink", "SelectLink"),
        )

    def test_revit_2022_plus_uses_coordination_select_link(self):
        command_id, member = self.module.resolve_coordination_review_command(ui=ModernUI)
        self.assertEqual(member, "CoordinationSelectLink")
        self.assertEqual(
            command_id, ("command-id", ModernUI.PostableCommand.CoordinationSelectLink)
        )

    def test_legacy_revit_falls_back_to_select_link(self):
        command_id, member = self.module.resolve_coordination_review_command(ui=LegacyUI)
        self.assertEqual(member, "SelectLink")
        self.assertEqual(command_id, ("command-id", LegacyUI.PostableCommand.SelectLink))

    def test_modern_name_wins_when_both_exist(self):
        command_id, member = self.module.resolve_coordination_review_command(ui=UIWithBothNames)
        self.assertEqual(member, "CoordinationSelectLink")

    def test_copy_monitor_command_is_never_used(self):
        command_id, member = self.module.resolve_coordination_review_command(
            ui=UIWithoutCoordinationReview
        )
        self.assertIsNone(command_id)
        self.assertEqual(member, "")

    def test_missing_ui_api_yields_none(self):
        class EmptyUI(object):
            pass

        command_id, member = self.module.resolve_coordination_review_command(ui=EmptyUI)
        self.assertIsNone(command_id)
        self.assertEqual(member, "")


class FrameLinkInActiveViewTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc, self.link_type, self.instance, self.orphan_type, self.wall = _fixture()

    def test_zooms_current_view_to_link_bounding_box(self):
        uidoc = FakeUIDocument()
        outcome = self.module.frame_link_in_active_view(uidoc, self.instance)
        self.assertTrue(outcome["visible"])
        self.assertTrue(outcome["framed"])
        self.assertEqual(uidoc.ui_view.zoomed, ("bbox-min", "bbox-max"))

    def test_hidden_link_is_not_visible_and_not_zoomed(self):
        uidoc = FakeUIDocument()
        hidden = self.doc.GetElement(FakeElementId(41))
        outcome = self.module.frame_link_in_active_view(uidoc, hidden)
        self.assertFalse(outcome["visible"])
        self.assertFalse(outcome["framed"])
        self.assertIn("not visible in the current view", outcome["reason"])
        self.assertIsNone(uidoc.ui_view.zoomed)

    def test_zoom_failure_keeps_link_visible(self):
        uidoc = FakeUIDocument(ui_view=FakeUIView(ACTIVE_VIEW_ID, fail_zoom=True))
        outcome = self.module.frame_link_in_active_view(uidoc, self.instance)
        self.assertTrue(outcome["visible"])
        self.assertFalse(outcome["framed"])
        self.assertIn("zoom failed", outcome["reason"])

    def test_missing_ui_view_for_active_view_keeps_link_visible(self):
        uidoc = FakeUIDocument(ui_views=[FakeUIView(999)])
        outcome = self.module.frame_link_in_active_view(uidoc, self.instance)
        self.assertTrue(outcome["visible"])
        self.assertFalse(outcome["framed"])

    def test_no_uidoc_is_not_visible(self):
        outcome = self.module.frame_link_in_active_view(None, self.instance)
        self.assertFalse(outcome["visible"])
        self.assertFalse(outcome["framed"])


class ShowLinkCoordinationReviewTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc, self.link_type, self.instance, self.orphan_type, self.wall = _fixture()
        self.uidoc = FakeUIDocument()
        self.uiapp = FakeUIApplication()

    def _show(self, element_id, uiapp=None, uidoc=None, ui=ModernUI, before_post=None):
        return self.module.show_link_coordination_review(
            uiapp or self.uiapp,
            uidoc or self.uidoc,
            self.doc,
            element_id,
            db=FakeDB,
            ui=ui,
            before_post=before_post,
        )

    def test_selects_frames_and_posts_coordination_review(self):
        result = self._show(11)
        self.assertTrue(result["ok"])
        self.assertTrue(result["selected"])
        self.assertTrue(result["visible"])
        self.assertTrue(result["framed"])
        self.assertTrue(result["posted"])
        self.assertEqual(result["link_name"], "ARCH.rvt : 1")
        self.assertEqual([int(i) for i in self.uidoc.Selection.ids], [11])
        self.assertEqual(self.uidoc.ui_view.zoomed, ("bbox-min", "bbox-max"))
        self.assertEqual(
            self.uiapp.posted,
            [("command-id", ModernUI.PostableCommand.CoordinationSelectLink)],
        )
        self.assertIn("click the highlighted link ARCH.rvt : 1", result["message"])

    def test_never_opens_or_searches_other_views(self):
        self.assertFalse(hasattr(self.uidoc, "ShowElements"))
        result = self._show(11)
        self.assertTrue(result["ok"])
        self.assertNotIn("ShowElements", open(str(MODULE_PATH)).read())

    def test_link_hidden_in_current_view_is_selected_but_not_posted(self):
        result = self._show(41)
        self.assertFalse(result["ok"])
        self.assertTrue(result["selected"])
        self.assertFalse(result["visible"])
        self.assertFalse(result["posted"])
        self.assertEqual([int(i) for i in self.uidoc.Selection.ids], [41])
        self.assertEqual(self.uiapp.posted, [])
        self.assertIn("not visible in the current view", result["message"])
        self.assertIn("click View Issues again", result["message"])

    def test_before_post_runs_after_framing_and_before_posting(self):
        events = []
        uiapp = FakeUIApplication(events=events)

        def _before_post(result):
            events.append(("before_post", result["link_name"], result["framed"]))

        result = self._show(11, uiapp=uiapp, before_post=_before_post)
        self.assertTrue(result["ok"])
        self.assertEqual(events, [("before_post", "ARCH.rvt : 1", True), "post"])

    def test_before_post_is_skipped_when_link_is_hidden(self):
        events = []
        result = self._show(41, before_post=lambda result: events.append("before_post"))
        self.assertFalse(result["ok"])
        self.assertEqual(events, [])

    def test_before_post_exception_does_not_block_post(self):
        def _boom(result):
            raise RuntimeError("alert failed")

        result = self._show(11, before_post=_boom)
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.uiapp.posted), 1)

    def test_zoom_failure_still_posts(self):
        uidoc = FakeUIDocument(ui_view=FakeUIView(ACTIVE_VIEW_ID, fail_zoom=True))
        result = self._show(11, uidoc=uidoc)
        self.assertTrue(result["ok"])
        self.assertTrue(result["visible"])
        self.assertFalse(result["framed"])
        self.assertEqual(len(self.uiapp.posted), 1)

    def test_legacy_revit_posts_select_link(self):
        result = self._show(11, ui=LegacyUI)
        self.assertTrue(result["ok"])
        self.assertEqual(self.uiapp.posted, [("command-id", LegacyUI.PostableCommand.SelectLink)])

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
        self.assertIsNone(self.uidoc.ui_view.zoomed)
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

    def test_post_is_attempted_without_can_post_command(self):
        uiapp = FakeUIApplicationWithoutCanPost()
        result = self._show(11, uiapp=uiapp)
        self.assertTrue(result["ok"])
        self.assertEqual(len(uiapp.posted), 1)

    def test_post_is_attempted_even_when_can_post_says_no(self):
        uiapp = FakeUIApplication(can_post=False)
        result = self._show(11, uiapp=uiapp)
        self.assertTrue(result["ok"])
        self.assertEqual(len(uiapp.posted), 1)

    def test_refused_post_explains_revit_cannot_run_it(self):
        uiapp = FakeUIApplication(can_post=False, fail_post=True)
        result = self._show(11, uiapp=uiapp)
        self.assertFalse(result["ok"])
        self.assertTrue(result["selected"])
        self.assertFalse(result["posted"])
        self.assertIn("cannot open Coordination Review right now", result["message"])
        self.assertIn("post failed", result["message"])
        self.assertIn("link is selected", result["message"])

    def test_post_exception_is_reported(self):
        uiapp = FakeUIApplication(fail_post=True)
        result = self._show(11, uiapp=uiapp)
        self.assertFalse(result["ok"])
        self.assertTrue(result["selected"])
        self.assertFalse(result["posted"])
        self.assertIn("Could not open Coordination Review", result["message"])
        self.assertIn("post failed", result["message"])

    def test_missing_command_member_is_reported(self):
        result = self._show(11, ui=UIWithoutCoordinationReview)
        self.assertFalse(result["ok"])
        self.assertTrue(result["selected"])
        self.assertIn("not available in this version", result["message"])

    def test_missing_uiapp_is_reported(self):
        result = self.module.show_link_coordination_review(
            None, self.uidoc, self.doc, 11, db=FakeDB, ui=ModernUI
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["selected"])
        self.assertIn("no Revit application", result["message"])


if __name__ == "__main__":
    unittest.main()
