"""The title block maths View Align and Linked Sheets Transfer share.

A sheet's title block is what the reader's eye measures every drawing against,
so a sign error here is invisible on screen and obvious on paper.  The expected
values are hand-computed rather than taken from the implementation.
"""

import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "sheet_titleblocks.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("sheet_titleblocks",
                                                  str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


titleblocks = _load_module()


class _Point(object):
    """Stands in for a Revit ``XYZ``."""

    def __init__(self, x, y, z=0.0):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)


class _Location(object):
    def __init__(self, point):
        self.Point = point


class _Element(object):
    def __init__(self, element_id, point=None):
        self.Id = element_id
        if point is not None:
            self.Location = _Location(point)


class _Collector(object):
    """The two-call chain ``sheet_title_blocks`` walks."""

    def __init__(self, elements):
        self._elements = elements

    def OfCategory(self, _category):
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return list(self._elements)


class _BuiltInCategory(object):
    OST_TitleBlocks = "OST_TitleBlocks"


class _DB(object):
    """Just enough ``DB`` for the collector call."""

    BuiltInCategory = _BuiltInCategory

    def __init__(self, elements_by_sheet_id, raises=False):
        self._by_sheet = elements_by_sheet_id
        self._raises = raises

    def FilteredElementCollector(self, _document, sheet_id):
        if self._raises:
            raise RuntimeError("collector refused")
        return _Collector(self._by_sheet.get(sheet_id, []))


class _Sheet(object):
    def __init__(self, sheet_id):
        self.Id = sheet_id


class ShiftTests(unittest.TestCase):
    def test_the_shift_moves_the_target_onto_the_source(self):
        shift = titleblocks.title_block_shift(_Point(3.0, 5.0), _Point(1.0, 2.0))
        self.assertEqual((2.0, 3.0, 0.0), shift)

    def test_the_shift_is_signed_and_not_an_absolute_distance(self):
        shift = titleblocks.title_block_shift(_Point(-1.0, -4.0), _Point(1.0, 2.0))
        self.assertEqual((-2.0, -6.0, 0.0), shift)

    def test_z_is_carried_through(self):
        shift = titleblocks.title_block_shift(_Point(0.0, 0.0, 2.5),
                                              _Point(0.0, 0.0, 0.5))
        self.assertEqual((0.0, 0.0, 2.0), shift)

    def test_two_title_blocks_already_together_are_left_alone(self):
        self.assertIsNone(
            titleblocks.title_block_shift(_Point(4.0, 9.0), _Point(4.0, 9.0)))

    def test_a_shift_under_eps_is_not_worth_a_transaction(self):
        self.assertIsNone(
            titleblocks.title_block_shift(_Point(0.0, 0.0), _Point(1e-12, 0.0)))

    def test_a_shift_above_eps_survives(self):
        self.assertIsNotNone(
            titleblocks.title_block_shift(_Point(0.0, 0.0), _Point(1e-3, 0.0)))

    def test_a_missing_point_yields_no_shift_rather_than_raising(self):
        self.assertIsNone(titleblocks.title_block_shift(None, _Point(1.0, 1.0)))
        self.assertIsNone(titleblocks.title_block_shift(_Point(1.0, 1.0), None))

    def test_a_point_without_coordinates_yields_no_shift(self):
        self.assertIsNone(
            titleblocks.title_block_shift(object(), _Point(1.0, 1.0)))


class LocationPointTests(unittest.TestCase):
    def test_reads_the_location_point(self):
        point = _Point(1.0, 2.0)
        self.assertIs(point, titleblocks.location_point(_Element(7, point)))

    def test_an_element_with_no_location_is_none_rather_than_an_error(self):
        self.assertIsNone(titleblocks.location_point(_Element(7)))


class CollectionTests(unittest.TestCase):
    def test_title_blocks_come_back_in_element_order(self):
        first, second = _Element(11), _Element(12)
        db = _DB({99: [first, second]})
        found = titleblocks.sheet_title_blocks(db, None, _Sheet(99))
        self.assertEqual([first, second], found)

    def test_the_first_title_block_wins_and_the_rest_are_counted(self):
        first, second, third = _Element(11), _Element(12), _Element(13)
        db = _DB({99: [first, second, third]})
        block, extra = titleblocks.first_title_block(db, None, _Sheet(99))
        self.assertIs(first, block)
        self.assertEqual(2, extra)

    def test_one_title_block_reports_no_extras(self):
        db = _DB({99: [_Element(11)]})
        _, extra = titleblocks.first_title_block(db, None, _Sheet(99))
        self.assertEqual(0, extra)

    def test_a_sheet_with_no_title_block_is_reported_not_raised(self):
        db = _DB({99: []})
        self.assertEqual((None, 0),
                         titleblocks.first_title_block(db, None, _Sheet(99)))

    def test_a_refused_collector_is_an_empty_sheet_not_a_crash(self):
        db = _DB({}, raises=True)
        self.assertEqual([], titleblocks.sheet_title_blocks(db, None, _Sheet(99)))
        self.assertEqual((None, 0),
                         titleblocks.first_title_block(db, None, _Sheet(99)))


class PlanSheetMoveTests(unittest.TestCase):
    # 500 is the title block; 501/502 are viewports; 600/601 are annotation.
    SHEET = [500, 501, 502, 600, 601]

    def test_without_the_sub_option_only_the_title_block_moves(self):
        moving = titleblocks.plan_sheet_move(
            self.SHEET, aligned_viewport_ids=[501],
            move_other_content=False, title_block_id=500)
        self.assertEqual([500], moving)

    def test_the_viewports_being_aligned_are_left_to_the_alignment_maths(self):
        moving = titleblocks.plan_sheet_move(
            self.SHEET, aligned_viewport_ids=[501, 502],
            move_other_content=True, title_block_id=500)
        self.assertNotIn(501, moving)
        self.assertNotIn(502, moving)

    def test_an_unselected_viewport_keeps_its_place_inside_the_frame(self):
        moving = titleblocks.plan_sheet_move(
            self.SHEET, aligned_viewport_ids=[501],
            move_other_content=True, title_block_id=500)
        self.assertIn(502, moving)

    def test_the_rest_of_the_sheet_travels_with_the_frame(self):
        moving = titleblocks.plan_sheet_move(
            self.SHEET, aligned_viewport_ids=[501, 502],
            move_other_content=True, title_block_id=500)
        self.assertEqual([500, 600, 601], moving)

    def test_the_title_block_is_always_first_so_the_caller_can_count_it(self):
        moving = titleblocks.plan_sheet_move(
            [600, 500, 601], aligned_viewport_ids=[],
            move_other_content=True, title_block_id=500)
        self.assertEqual(500, moving[0])

    def test_the_title_block_is_never_listed_twice(self):
        moving = titleblocks.plan_sheet_move(
            self.SHEET, aligned_viewport_ids=[],
            move_other_content=True, title_block_id=500)
        self.assertEqual(1, moving.count(500))

    def test_no_title_block_means_nothing_moves(self):
        self.assertEqual([], titleblocks.plan_sheet_move(
            self.SHEET, aligned_viewport_ids=[501],
            move_other_content=False, title_block_id=None))

    def test_no_title_block_still_carries_the_rest_when_asked(self):
        moving = titleblocks.plan_sheet_move(
            self.SHEET, aligned_viewport_ids=[501, 502],
            move_other_content=True, title_block_id=None)
        self.assertEqual([500, 600, 601], moving)

    def test_an_empty_sheet_is_not_an_error(self):
        self.assertEqual([], titleblocks.plan_sheet_move(
            None, aligned_viewport_ids=None,
            move_other_content=True, title_block_id=None))


if __name__ == "__main__":
    unittest.main()
