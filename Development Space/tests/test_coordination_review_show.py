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
    def __init__(self, element_id, visible=True):
        self.Id = FakeElementId(element_id)
        self.Name = "Wall"
        self._visible = visible

    def get_BoundingBox(self, view):
        return FakeBoundingBox() if self._visible else None


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
    """Selection, active view and open UI views only: Show must never open
    or search other views, so there is deliberately no ``ShowElements``."""

    def __init__(self, fail_select=False, ui_view=None):
        self.Selection = FakeSelection()
        self.ActiveView = FakeView(ACTIVE_VIEW_ID)
        self.ui_view = FakeUIView(ACTIVE_VIEW_ID) if ui_view is None else ui_view
        if fail_select:
            self.Selection.SetElementIds = self._raise

    def _raise(self, *args):
        raise RuntimeError("selection failed")

    def GetOpenUIViews(self):
        return [self.ui_view]


def _fixture():
    link_type = RevitLinkType(10, "ARCH.rvt")
    instance = RevitLinkInstance(11, "ARCH.rvt : 1", 10)
    orphan_type = RevitLinkType(20, "MEP.rvt")
    wall = Wall(30)
    hidden_wall = Wall(31, visible=False)
    doc = FakeDocument([link_type, instance, orphan_type, wall, hidden_wall])
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


class ElementFromIdTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc, _, _, _, self.wall = _fixture()

    def test_returns_element(self):
        self.assertIs(self.module.element_from_id(self.doc, "30", db=FakeDB), self.wall)

    def test_missing_or_invalid(self):
        self.assertIsNone(self.module.element_from_id(self.doc, 999, db=FakeDB))
        self.assertIsNone(self.module.element_from_id(self.doc, "x", db=FakeDB))
        self.assertIsNone(self.module.element_from_id(None, 30, db=FakeDB))


class FakeUIDocumentFromDoc(object):
    """Stand-in for the public ``UIDocument(Document)`` constructor."""

    def __init__(self, doc):
        self.Document = doc


class FakeUI(object):
    UIDocument = FakeUIDocumentFromDoc


class FakeUIApplication(object):
    def __init__(self, uidoc):
        self.ActiveUIDocument = uidoc


class FakeControlledApplication(object):
    """No ActiveUIDocument, like pyRevit's ``__revit__`` in the startup engine."""


class ResolveUidocTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc, _, _, _, _ = _fixture()

    def test_active_ui_document_is_used_when_present(self):
        uidoc = FakeUIDocument()
        resolved = self.module.resolve_uidoc(FakeUIApplication(uidoc), self.doc, ui=FakeUI)
        self.assertIs(uidoc, resolved)

    def test_controlled_application_falls_back_to_uidocument_from_doc(self):
        resolved = self.module.resolve_uidoc(FakeControlledApplication(), self.doc, ui=FakeUI)
        self.assertIsInstance(resolved, FakeUIDocumentFromDoc)
        self.assertIs(self.doc, resolved.Document)

    def test_application_without_active_document_also_falls_back(self):
        resolved = self.module.resolve_uidoc(FakeUIApplication(None), self.doc, ui=FakeUI)
        self.assertIsInstance(resolved, FakeUIDocumentFromDoc)

    def test_no_application_still_resolves_from_doc(self):
        resolved = self.module.resolve_uidoc(None, self.doc, ui=FakeUI)
        self.assertIs(self.doc, resolved.Document)

    def test_nothing_to_resolve_from(self):
        class EmptyUI(object):
            pass

        self.assertIsNone(self.module.resolve_uidoc(None, None, ui=FakeUI))
        self.assertIsNone(self.module.resolve_uidoc(FakeControlledApplication(), self.doc, ui=EmptyUI))


class SelectElementTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc, _, _, _, self.wall = _fixture()
        self.uidoc = FakeUIDocument()

    def test_selects_and_frames_in_current_view(self):
        result = self.module.select_element(self.uidoc, self.wall, db=FakeDB)
        self.assertTrue(result["selected"])
        self.assertTrue(result["visible"])
        self.assertTrue(result["framed"])
        self.assertEqual([int(i) for i in self.uidoc.Selection.ids], [30])
        self.assertEqual(self.uidoc.ui_view.zoomed, ("bbox-min", "bbox-max"))
        self.assertIn("selected and framed", result["message"])

    def test_never_opens_or_searches_other_views(self):
        self.assertFalse(hasattr(self.uidoc, "ShowElements"))
        self.assertNotIn("ShowElements", MODULE_PATH.read_text())

    def test_hidden_element_is_selected_with_a_hint(self):
        hidden = self.doc.GetElement(FakeElementId(31))
        result = self.module.select_element(self.uidoc, hidden, db=FakeDB)
        self.assertTrue(result["selected"])
        self.assertFalse(result["visible"])
        self.assertFalse(result["framed"])
        self.assertIsNone(self.uidoc.ui_view.zoomed)
        self.assertIn("not visible in the current view", result["message"])

    def test_zoom_failure_keeps_selection(self):
        uidoc = FakeUIDocument(ui_view=FakeUIView(ACTIVE_VIEW_ID, fail_zoom=True))
        result = self.module.select_element(uidoc, self.wall, db=FakeDB)
        self.assertTrue(result["selected"])
        self.assertTrue(result["visible"])
        self.assertFalse(result["framed"])
        self.assertIn("Element 30 selected.", result["message"])

    def test_selection_failure_is_reported(self):
        uidoc = FakeUIDocument(fail_select=True)
        result = self.module.select_element(uidoc, self.wall, db=FakeDB)
        self.assertFalse(result["selected"])
        self.assertIn("Could not select element 30", result["message"])

    def test_missing_uidoc_is_reported(self):
        result = self.module.select_element(None, self.wall, db=FakeDB)
        self.assertFalse(result["selected"])
        self.assertIn("no active Revit UI document", result["message"])


if __name__ == "__main__":
    unittest.main()
