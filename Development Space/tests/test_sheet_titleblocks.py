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
    def __init__(self, element_id, point=None, category=None, owner_view_id=None):
        self.Id = element_id
        self.category = category
        self.owner_view_id = owner_view_id
        if point is not None:
            self.Location = _Location(point)


class _Collector(object):
    """Models the real collector: scoped by *visibility*, not by ownership.

    A view-scoped ``FilteredElementCollector`` also returns the model elements
    seen through the placed viewports, which is what made the first version of
    this feature move real geometry.  The earlier fake here returned only what
    a test seeded for the sheet, so it rubber-stamped the very assumption that
    is false in Revit and could never have caught it.  ``OfCategory`` filters
    for real too, for the same reason.
    """

    def __init__(self, elements):
        self._elements = list(elements)

    def OfCategory(self, category):
        return _Collector([x for x in self._elements
                           if getattr(x, "category", None) == category])

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return list(self._elements)


class _BuiltInCategory(object):
    OST_TitleBlocks = "OST_TitleBlocks"


class _DB(object):
    """Just enough ``DB`` for the collector call."""

    BuiltInCategory = _BuiltInCategory

    def __init__(self, elements_by_sheet_id, raises=False, model_elements=None):
        self._by_sheet = elements_by_sheet_id
        self._raises = raises
        self._model_elements = list(model_elements or [])

    def FilteredElementCollector(self, _document, sheet_id):
        if self._raises:
            raise RuntimeError("collector refused")
        # Everything visible on the sheet: what it owns, plus whatever shows
        # through its viewports.
        return _Collector(list(self._by_sheet.get(sheet_id, []))
                          + list(self._model_elements))


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


TB = _BuiltInCategory.OST_TitleBlocks


def _title_block(element_id, owner_view_id=99):
    return _Element(element_id, category=TB, owner_view_id=owner_view_id)


class CollectionTests(unittest.TestCase):
    def test_title_blocks_come_back_in_element_order(self):
        first, second = _title_block(11), _title_block(12)
        db = _DB({99: [first, second]})
        found = titleblocks.sheet_title_blocks(db, None, _Sheet(99))
        self.assertEqual([first, second], found)

    def test_the_first_title_block_wins_and_the_rest_are_counted(self):
        first, second, third = _title_block(11), _title_block(12), _title_block(13)
        db = _DB({99: [first, second, third]})
        block, extra = titleblocks.first_title_block(db, None, _Sheet(99))
        self.assertIs(first, block)
        self.assertEqual(2, extra)

    def test_one_title_block_reports_no_extras(self):
        db = _DB({99: [_title_block(11)]})
        _, extra = titleblocks.first_title_block(db, None, _Sheet(99))
        self.assertEqual(0, extra)

    def test_model_elements_seen_through_the_viewports_are_not_title_blocks(self):
        # The collector is visibility-scoped, so a grid on the sheet's views
        # comes back too. Only the category filter keeps it out.
        grid = _Element(500, category="OST_Grids", owner_view_id=None)
        db = _DB({99: [_title_block(11)]}, model_elements=[grid])
        found = titleblocks.sheet_title_blocks(db, None, _Sheet(99))
        self.assertEqual([11], [x.Id for x in found])

    def test_a_sheet_with_no_title_block_is_reported_not_raised(self):
        db = _DB({99: []}, model_elements=[_Element(500, category="OST_Grids")])
        self.assertEqual((None, 0),
                         titleblocks.first_title_block(db, None, _Sheet(99)))

    def test_a_refused_collector_is_an_empty_sheet_not_a_crash(self):
        db = _DB({}, raises=True)
        self.assertEqual([], titleblocks.sheet_title_blocks(db, None, _Sheet(99)))
        self.assertEqual((None, 0),
                         titleblocks.first_title_block(db, None, _Sheet(99)))


class SheetOwnedIdsTests(unittest.TestCase):
    """The guard that keeps this option on the sheet and out of the model.

    A view-scoped collector returns what is *visible*, which on a sheet
    includes every model element drawn through the placed viewports.  Moving
    one of those translates real geometry by a paper-space vector.
    """

    SHEET = 99

    # (element_id, owner_view_id, is_viewport, is_titleblock_revision_schedule)
    TITLE_BLOCK = (500, 99, False, False)
    TEXT_NOTE = (600, 99, False, False)
    VIEWPORT = (501, None, True, False)
    WALL_THROUGH_A_VIEWPORT = (900, None, False, False)
    GRID_OWNED_BY_ANOTHER_VIEW = (901, 42, False, False)
    TB_REVISION_SCHEDULE = (700, 99, False, True)

    def _owned(self, *candidates):
        return titleblocks.sheet_owned_ids(list(candidates), self.SHEET)

    def test_a_model_element_seen_through_a_viewport_is_never_moved(self):
        self.assertEqual(
            [500], self._owned(self.TITLE_BLOCK, self.WALL_THROUGH_A_VIEWPORT))

    def test_an_element_owned_by_another_view_is_never_moved(self):
        self.assertEqual(
            [500], self._owned(self.TITLE_BLOCK, self.GRID_OWNED_BY_ANOTHER_VIEW))

    def test_an_unreadable_owner_excludes_rather_than_admits(self):
        self.assertEqual([], self._owned((800, None, False, False)))

    def test_the_sheets_own_annotation_travels_with_the_frame(self):
        self.assertEqual(
            [500, 600], self._owned(self.TITLE_BLOCK, self.TEXT_NOTE))

    def test_a_viewport_is_admitted_without_consulting_its_owner(self):
        # Viewport.OwnerViewId is unproven in this codebase, so retention must
        # not depend on it - here it is None and the viewport still survives.
        self.assertEqual([501], self._owned(self.VIEWPORT))

    def test_a_title_block_revision_schedule_is_not_moved_twice(self):
        # It rides on the title block, so moving it separately shifts it twice.
        self.assertEqual(
            [500], self._owned(self.TITLE_BLOCK, self.TB_REVISION_SCHEDULE))

    def test_order_is_preserved_so_the_title_block_stays_findable(self):
        self.assertEqual(
            [500, 501, 600],
            self._owned(self.TITLE_BLOCK, self.VIEWPORT, self.TEXT_NOTE))

    def test_unusable_ids_are_dropped(self):
        self.assertEqual([], self._owned((None, 99, False, False),
                                         (-1, 99, False, False)))

    def test_nothing_in_is_nothing_out(self):
        self.assertEqual([], titleblocks.sheet_owned_ids(None, self.SHEET))

    def test_a_whole_sheet_of_visible_elements_reduces_to_the_owned_ones(self):
        self.assertEqual(
            [500, 501, 600],
            self._owned(self.TITLE_BLOCK,
                        self.WALL_THROUGH_A_VIEWPORT,
                        self.VIEWPORT,
                        self.GRID_OWNED_BY_ANOTHER_VIEW,
                        self.TEXT_NOTE,
                        self.TB_REVISION_SCHEDULE))


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
