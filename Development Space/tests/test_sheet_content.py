"""Moving a sheet's contents as one block: the part both align tools share.

Two bugs found in the first copy of this logic are pinned here, so the shared
module cannot lose them: a view-scoped collector hands back the model elements
seen *through* the viewports, and a refused pin must be reported rather than
swallowed or allowed to abort a run that already succeeded.
"""

import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = ROOT / "lib"


_TOUCHED = ("easybim", "easybim.sheet_titleblocks", "easybim.sheet_content")


def _load():
    """Load the module with our own `easybim`, then put sys.modules back.

    Other suites install their own `easybim` double. Picking theirs up makes
    `from easybim import sheet_titleblocks` fail with "unknown location";
    leaving ours behind breaks them. Both directions have bitten this repo, so
    the stub is installed and removed inside this call.
    """
    saved = dict((name, sys.modules.get(name)) for name in _TOUCHED)

    package = types.ModuleType("easybim")
    package.__path__ = [str(LIB / "easybim")]
    sys.modules["easybim"] = package
    sys.modules.pop("easybim.sheet_titleblocks", None)
    sys.modules.pop("easybim.sheet_content", None)

    try:
        spec = importlib.util.spec_from_file_location(
            "easybim.sheet_content", str(LIB / "easybim" / "sheet_content.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules["easybim.sheet_content"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name in _TOUCHED:
            previous = saved.get(name)
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


content = _load()

TITLE_BLOCK_CATEGORY = 4157


def eid(value):
    return value


class _Point(object):
    def __init__(self, x, y, z=0.0):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class _Category(object):
    def __init__(self, category_id, name="Generic"):
        self.Id = category_id
        self.Name = name


class _Element(object):
    def __init__(self, element_id, owner_view_id=None, category_id=None,
                 revision_schedule=False):
        self.Id = element_id
        self.OwnerViewId = owner_view_id
        self.Category = _Category(category_id) if category_id is not None else None
        self.IsTitleblockRevisionSchedule = revision_schedule


class _Viewport(object):
    def __init__(self, element_id):
        self.Id = element_id
        self.Category = None
        self.IsTitleblockRevisionSchedule = False

    @property
    def OwnerViewId(self):
        # Unproven in this codebase, so retention must never depend on it.
        raise Exception("Viewport.OwnerViewId is not read")


class _Collector(object):
    def __init__(self, elements):
        self._elements = elements

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return list(self._elements)


class _Sheet(object):
    def __init__(self, sheet_id):
        self.Id = sheet_id


def _db(elements):
    DB = types.SimpleNamespace()
    DB.Viewport = _Viewport
    DB.FilteredElementCollector = lambda doc, view_id: _Collector(elements)
    return DB


class OwnedElementsTests(unittest.TestCase):
    """The collector is scoped by visibility, so ownership must be tested."""

    SHEET = 99

    def _owned(self, elements, keep=None):
        return content.owned_elements(
            _db(elements), None, _Sheet(self.SHEET), eid,
            TITLE_BLOCK_CATEGORY, keep_title_block_id=keep)

    def test_a_model_element_seen_through_a_viewport_is_excluded(self):
        wall = _Element(900, owner_view_id=None)
        note = _Element(600, owner_view_id=self.SHEET)
        self.assertEqual([600], sorted(self._owned([wall, note]).keys()))

    def test_an_element_owned_by_another_view_is_excluded(self):
        grid = _Element(901, owner_view_id=42)
        note = _Element(600, owner_view_id=self.SHEET)
        self.assertEqual([600], sorted(self._owned([grid, note]).keys()))

    def test_a_viewport_is_kept_without_reading_its_owner(self):
        # _Viewport.OwnerViewId raises; keeping it must not depend on that.
        self.assertEqual([501], sorted(self._owned([_Viewport(501)]).keys()))

    def test_only_the_named_title_block_is_kept(self):
        first = _Element(500, owner_view_id=self.SHEET,
                         category_id=TITLE_BLOCK_CATEGORY)
        second = _Element(502, owner_view_id=self.SHEET,
                          category_id=TITLE_BLOCK_CATEGORY)
        self.assertEqual([500], sorted(self._owned([first, second], keep=500).keys()))

    def test_a_title_block_revision_schedule_is_excluded(self):
        rider = _Element(700, owner_view_id=self.SHEET, revision_schedule=True)
        note = _Element(600, owner_view_id=self.SHEET)
        self.assertEqual([600], sorted(self._owned([rider, note]).keys()))

    def test_a_refused_collector_is_an_empty_sheet_not_a_crash(self):
        DB = types.SimpleNamespace()
        DB.Viewport = _Viewport

        def boom(doc, view_id):
            raise Exception("collector refused")

        DB.FilteredElementCollector = boom
        self.assertEqual({}, content.owned_elements(
            DB, None, _Sheet(self.SHEET), eid, TITLE_BLOCK_CATEGORY))


class _Pinnable(object):
    def __init__(self, refuses=False):
        self.Pinned = False
        self._refuses = refuses

    def __setattr__(self, name, value):
        if name == "Pinned" and getattr(self, "_refuses", False) and value:
            raise Exception("element is workset-locked")
        object.__setattr__(self, name, value)


class PinTests(unittest.TestCase):
    def test_an_unpinned_element_reports_false(self):
        self.assertFalse(content.unpin_if_pinned(_Pinnable()))

    def test_a_pinned_element_is_unpinned_and_reported(self):
        element = _Pinnable()
        object.__setattr__(element, "Pinned", True)
        self.assertTrue(content.unpin_if_pinned(element))
        self.assertFalse(element.Pinned)

    def test_a_restored_pin_is_counted(self):
        element = _Pinnable()
        restored, failures = content.restore_pins([(element, "A-101")])
        self.assertEqual((1, []), (restored, failures))
        self.assertTrue(element.Pinned)

    def test_a_refused_pin_is_returned_rather_than_swallowed(self):
        restored, failures = content.restore_pins(
            [(_Pinnable(refuses=True), "A-101")])
        self.assertEqual(0, restored)
        self.assertEqual(1, len(failures))
        self.assertEqual("A-101", failures[0][0])

    def test_a_refused_pin_never_raises(self):
        try:
            content.restore_pins([(_Pinnable(refuses=True), "A-101")])
        except Exception as error:
            self.fail("restoring a pin must not raise: {}".format(error))

    def test_one_refusal_does_not_stop_the_others(self):
        good = _Pinnable()
        restored, failures = content.restore_pins(
            [(_Pinnable(refuses=True), "A-101"), (good, "A-102")])
        self.assertTrue(good.Pinned)
        self.assertEqual(1, restored)
        self.assertEqual(1, len(failures))


class TargetPointTests(unittest.TestCase):
    """Where a sheet's title block should end up, per mode."""

    def test_sheet_origin_sends_the_frame_to_the_page_zero(self):
        self.assertEqual(
            (0.0, 0.0, 0.0),
            content.target_point_for(content.MODE_SHEET_ORIGIN, _Point(0.5, 0.2)))

    def test_sheet_origin_ignores_any_reference(self):
        # The sheet origin is the same point on every sheet, so a reference
        # could never contribute to this mode.
        self.assertEqual(
            (0.0, 0.0, 0.0),
            content.target_point_for(content.MODE_SHEET_ORIGIN, _Point(0.5, 0.2),
                                     _Point(9.0, 9.0)))

    def test_title_block_origin_sends_the_frame_to_the_reference(self):
        self.assertEqual(
            (2.0, 3.0, 0.0),
            content.target_point_for(content.MODE_TITLE_BLOCK_ORIGIN,
                                     _Point(0.5, 0.2), _Point(2.0, 3.0)))

    def test_z_comes_from_the_sheet_not_the_reference(self):
        moved = content.target_point_for(content.MODE_TITLE_BLOCK_ORIGIN,
                                         _Point(0.5, 0.2, 1.5),
                                         _Point(2.0, 3.0, 7.0))
        self.assertEqual(1.5, moved[2])

    def test_title_block_origin_without_a_reference_is_refused(self):
        self.assertIsNone(
            content.target_point_for(content.MODE_TITLE_BLOCK_ORIGIN, _Point(0.5, 0.2)))

    def test_a_frame_with_no_point_is_refused(self):
        self.assertIsNone(
            content.target_point_for(content.MODE_SHEET_ORIGIN, None))

    def test_an_unknown_mode_is_refused(self):
        self.assertIsNone(
            content.target_point_for("nonsense", _Point(0.5, 0.2)))


if __name__ == "__main__":
    unittest.main()
