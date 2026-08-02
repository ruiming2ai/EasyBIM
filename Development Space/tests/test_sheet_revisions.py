import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "sheet_revisions.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "sheet_revisions_under_test", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sr = _load_module()


def eid(value):
    return value


class FakeClrList(object):
    def __init__(self):
        self._items = []

    def Add(self, item):
        self._items.append(item)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class SimpleSheet(object):
    """Minimal sheet: additional list drives everything."""

    def __init__(self, all_ids, additional_ids):
        self.cloud_ids = set(all_ids) - set(additional_ids)
        self.additional_ids = list(additional_ids)

    def GetAllRevisionIds(self):
        return sorted(self.cloud_ids | set(self.additional_ids))

    def GetAdditionalRevisionIds(self):
        return list(self.additional_ids)

    def SetAdditionalRevisionIds(self, id_list):
        self.additional_ids = [x for x in id_list]


class LockedIdsTests(unittest.TestCase):
    def test_cloud_driven_difference(self):
        sheet = SimpleSheet([1, 2, 3], [1])
        self.assertEqual(
            sr.get_locked_revision_ids(sheet, eid), set([2, 3]))

    def test_tolerates_missing_methods(self):
        class Broken(object):
            pass
        self.assertEqual(
            sr.get_locked_revision_ids(Broken(), eid), set())


class EnsureRevisionsTests(unittest.TestCase):
    def test_add_revision_path(self):
        class AddSheet(SimpleSheet):
            def AddRevision(self, rid):
                self.additional_ids.append(rid)
        sheet = AddSheet([1], [1])
        added, skipped, failed, failed_ids = sr.ensure_revisions_on_sheet(
            sheet, [1, 5], eid, FakeClrList)
        self.assertEqual((added, skipped, failed), (1, 1, 0))
        self.assertEqual(failed_ids, [])
        self.assertIn(5, sheet.GetAllRevisionIds())

    def test_additional_fallback_when_add_raises(self):
        class NoAddSheet(SimpleSheet):
            def AddRevision(self, rid):
                raise RuntimeError("not supported")
        sheet = NoAddSheet([1], [1])
        added, skipped, failed, failed_ids = sr.ensure_revisions_on_sheet(
            sheet, [7], eid, FakeClrList)
        self.assertEqual((added, skipped, failed), (1, 0, 0))
        self.assertIn(7, sheet.GetAdditionalRevisionIds())

    def test_failure_collects_ids(self):
        class DeadSheet(object):
            def GetAllRevisionIds(self):
                return []
        added, skipped, failed, failed_ids = sr.ensure_revisions_on_sheet(
            DeadSheet(), [9], eid, FakeClrList)
        self.assertEqual((added, skipped, failed), (0, 0, 1))
        self.assertEqual(failed_ids, [9])


class RemoveRevisionsTests(unittest.TestCase):
    def test_rebuild_path_removes_only_additional(self):
        sheet = SimpleSheet([1, 2, 3], [1, 2])
        changed, removed = sr.remove_revisions_from_sheet(
            sheet, [2, 3], eid, eid, FakeClrList)
        self.assertTrue(changed)
        self.assertEqual(removed, [2])
        self.assertEqual(sheet.GetAdditionalRevisionIds(), [1])
        self.assertIn(3, sheet.GetAllRevisionIds())  # cloud survives

    def test_remove_revision_fallback_when_set_raises(self):
        removed_calls = []

        class FallbackSheet(SimpleSheet):
            def SetAdditionalRevisionIds(self, id_list):
                raise RuntimeError("no")

            def RemoveRevision(self, rid):
                removed_calls.append(rid)
                self.additional_ids = [
                    x for x in self.additional_ids if x != rid]
        sheet = FallbackSheet([1, 2], [1, 2])
        changed, removed = sr.remove_revisions_from_sheet(
            sheet, [2], eid, eid, FakeClrList)
        self.assertTrue(changed)
        self.assertEqual(removed, [2])
        self.assertEqual(removed_calls, [2])

    def test_noop_when_target_not_additional(self):
        sheet = SimpleSheet([1, 2], [1])
        changed, removed = sr.remove_revisions_from_sheet(
            sheet, [2], eid, eid, FakeClrList)
        self.assertFalse(changed)
        self.assertEqual(removed, [])


class FakeCloud(object):
    def __init__(self, cloud_id, revision_id, owner_view_id, hidden):
        self.Id = cloud_id
        self.RevisionId = revision_id
        self.OwnerViewId = owner_view_id
        self._hidden = hidden

    def IsHidden(self, view):
        return self._hidden

    def CanBeHidden(self, view):
        return True


class FakeView(object):
    def __init__(self, view_id):
        self.Id = view_id
        self.hidden_calls = []
        self.unhidden_calls = []

    def HideElements(self, id_list):
        self.hidden_calls.append(list(id_list))

    def UnhideElements(self, id_list):
        self.unhidden_calls.append(list(id_list))


class FakeCollectorSheet(object):
    def __init__(self, sheet_id, placed_view_ids):
        self.Id = sheet_id
        self._placed = list(placed_view_ids)

    def GetAllPlacedViews(self):
        return list(self._placed)


class FakeCollector(object):
    def __init__(self, elements):
        self._elements = elements

    def OfClass(self, cls):
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        return list(self._elements)


class FakeDoc(object):
    def __init__(self, clouds, views):
        self.clouds = clouds
        self.views = views

    def GetElement(self, element_id):
        return self.views.get(element_id)


class FakeDB(object):
    RevisionCloud = object

    def __init__(self, doc):
        self._doc = doc

    def FilteredElementCollector(self, doc):
        return FakeCollector(doc.clouds)


class FakeFramework(object):
    @staticmethod
    def get_type(value):
        return value


class CloudMapTests(unittest.TestCase):
    def _build(self):
        clouds = [
            FakeCloud(101, 11, 500, hidden=False),  # view 500 on sheet 1
            FakeCloud(102, 22, 1, hidden=True),     # on sheet 1 itself
            FakeCloud(103, 22, 600, hidden=True),   # view 600 on sheet 2
        ]
        views = {500: FakeView(500), 600: FakeView(600),
                 1: FakeView(1), 2: FakeView(2)}
        doc = FakeDoc(clouds, views)
        sheets = [FakeCollectorSheet(1, [500]),
                  FakeCollectorSheet(2, [600])]
        db = FakeDB(doc)
        return doc, sheets, db

    def test_grouping_and_hidden_flags(self):
        doc, sheets, db = self._build()
        cloud_map = sr.collect_sheet_cloud_map(
            doc, sheets, db, FakeFramework, eid)
        self.assertEqual(sorted(cloud_map.keys()), [1, 2])
        sheet1 = cloud_map[1]
        self.assertEqual(sorted(sheet1.keys()), [11, 22])
        self.assertFalse(sheet1[11][0]["is_hidden"])
        self.assertTrue(sheet1[22][0]["is_hidden"])
        hidden = sr.hidden_cloud_revision_ids(sheet1, set([11]))
        self.assertEqual(hidden, set([22]))
        hidden_when_on = sr.hidden_cloud_revision_ids(
            sheet1, set([11, 22]))
        self.assertEqual(hidden_when_on, set())

    def test_set_clouds_hidden_and_unhide_guards(self):
        doc, sheets, db = self._build()
        cloud_map = sr.collect_sheet_cloud_map(
            doc, sheets, db, FakeFramework, eid)
        view = doc.views[500]
        entries = cloud_map[1][11]
        changed, failed = sr.set_clouds_hidden_in_view(
            view, entries, True, FakeClrList, eid)
        self.assertEqual((changed, failed), (1, []))
        self.assertEqual(view.hidden_calls, [[101]])
        # already hidden -> hide is a no-op
        entries_hidden = cloud_map[1][22]
        view1 = doc.views[1]
        changed, failed = sr.set_clouds_hidden_in_view(
            view1, entries_hidden, True, FakeClrList, eid)
        self.assertEqual((changed, failed), (0, []))
        # unhide only touches hidden clouds
        changed, failed = sr.set_clouds_hidden_in_view(
            view1, entries_hidden, False, FakeClrList, eid)
        self.assertEqual((changed, failed), (1, []))
        self.assertEqual(view1.unhidden_calls, [[102]])


if __name__ == "__main__":
    unittest.main()
