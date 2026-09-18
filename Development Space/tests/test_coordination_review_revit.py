import importlib.util
import pathlib
import sys
import types
import unittest


LIB_ROOT = pathlib.Path(__file__).resolve().parents[2] / "lib" / "easybim"


def _load_module():
    """Load the module with stub siblings: the real ones import Revit lazily,
    but this test drives every call through injection anyway."""
    package = types.ModuleType("easybim")
    package.__path__ = [str(LIB_ROOT)]
    sys.modules["easybim"] = package

    for name in ("coordination_review_diff_revit", "coordination_review_show"):
        stub = types.ModuleType("easybim." + name)
        sys.modules["easybim." + name] = stub
        setattr(package, name, stub)

    sys.modules.pop("easybim.coordination_review_revit", None)
    spec = importlib.util.spec_from_file_location(
        "easybim.coordination_review_revit", str(LIB_ROOT / "coordination_review_revit.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["easybim.coordination_review_revit"] = module
    spec.loader.exec_module(module)
    return module


class FakeDocument(object):
    Title = "Tower.rvt"


class FakeLink(object):
    def __init__(self, link_id, name):
        self.Id = link_id
        self.Name = name


class FakeProgress(object):
    def __init__(self, cancel_after=None):
        self.updates = []
        self._cancel_after = cancel_after

    @property
    def cancelled(self):
        if self._cancel_after is None:
            return False
        return len(self.updates) >= self._cancel_after

    def update(self, done, total):
        self.updates.append((done, total))


def _link_report(name, issues=0, estimated=0, error=""):
    return {
        "link_name": name,
        "issue_count": issues,
        "estimated_count": estimated,
        "error": error,
        "groups": [{"kind": "level_moved", "title": "Level moved", "issues": [{}] * issues}],
    }


class BuildDocumentReviewReportTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.doc = FakeDocument()
        self.links = {11: FakeLink(11, "ARCH.rvt"), 12: FakeLink(12, "STR.rvt")}
        self.reports = {
            11: _link_report("ARCH.rvt", issues=3, estimated=1),
            12: _link_report("STR.rvt", issues=0),
        }
        self.resolved = []
        self.compared = []

    def _resolve(self, doc, link_id, db=None):
        self.resolved.append(link_id)
        link = self.links.get(int(link_id))
        return (link, "") if link else (None, "Element {} is not a Revit link.".format(link_id))

    def _build(self, doc, link_instance, db=None):
        self.compared.append(int(link_instance.Id))
        return self.reports[int(link_instance.Id)]

    def _run(self, counts, progress=None, passive_report=None):
        return self.module.build_document_review_report(
            self.doc,
            passive_report=passive_report,
            progress=progress,
            collect_link_ids=lambda doc, db=None: counts,
            build_link_report=self._build,
            resolve_link=self._resolve,
        )

    def test_every_monitored_link_is_compared(self):
        report = self._run({11: 5, 12: 2})

        self.assertEqual(report["source"], "computed")
        self.assertEqual(report["doc_title"], "Tower.rvt")
        self.assertEqual(report["monitored_link_count"], 2)
        self.assertEqual(report["checked_count"], 2)
        self.assertEqual(sorted(self.compared), [11, 12])
        self.assertEqual(report["problem_count"], 1)
        self.assertEqual(report["total_issues"], 3)
        self.assertFalse(report["detection_error"])

        arch = [row for row in report["links"] if row["link_id"] == 11][0]
        self.assertEqual(arch["name"], "ARCH.rvt")
        self.assertEqual(arch["issue_count"], 3)
        self.assertEqual(arch["estimated_count"], 1)
        self.assertEqual(arch["monitoring_count"], 5)
        self.assertEqual(arch["error"], "")

    def test_links_nothing_monitors_are_never_touched(self):
        """A link with no Copy/Monitor relationship has nothing to review, so
        it must not cost a comparison."""
        report = self._run({11: 1})

        self.assertEqual(self.compared, [11])
        self.assertEqual(self.resolved, [11])
        self.assertEqual(report["monitored_link_count"], 1)
        self.assertEqual([row["link_id"] for row in report["links"]], [11])

    def test_no_monitored_links_exits_without_comparing(self):
        report = self._run({})

        self.assertEqual(report["monitored_link_count"], 0)
        self.assertEqual(report["links"], [])
        self.assertEqual(self.compared, [])
        self.assertFalse(report["detection_error"])

    def test_busiest_link_is_checked_first(self):
        self._run({11: 2, 12: 40})
        self.assertEqual(self.compared, [12, 11])

    def test_an_unloaded_link_becomes_a_row_not_a_failure(self):
        self.reports[12] = _link_report("STR.rvt", error="Link STR.rvt is not loaded.")
        report = self._run({11: 1, 12: 1})

        self.assertEqual(report["checked_count"], 2)
        row = [row for row in report["links"] if row["link_id"] == 12][0]
        self.assertIn("not loaded", row["error"])
        # An unreadable link must not be counted as clean.
        self.assertEqual(report["problem_count"], 1)
        self.assertEqual(report["total_issues"], 3)

    def test_an_unresolvable_link_becomes_a_row(self):
        report = self._run({11: 1, 99: 1})

        row = [row for row in report["links"] if row["link_id"] == 99][0]
        self.assertIn("not a Revit link", row["error"])
        self.assertEqual(row["name"], "Link 99")
        self.assertEqual(report["checked_count"], 2)

    def test_a_comparison_that_throws_becomes_a_row(self):
        def _boom(doc, link_instance, db=None):
            raise RuntimeError("collector blew up")

        report = self.module.build_document_review_report(
            self.doc,
            collect_link_ids=lambda doc, db=None: {11: 1},
            build_link_report=_boom,
            resolve_link=self._resolve,
        )
        self.assertIn("collector blew up", report["links"][0]["error"])

    def test_progress_ticks_once_per_link(self):
        progress = FakeProgress()
        self._run({11: 5, 12: 2}, progress=progress)
        self.assertEqual(progress.updates, [(1, 2), (2, 2)])

    def test_cancel_reports_what_was_finished(self):
        progress = FakeProgress(cancel_after=1)
        report = self._run({11: 5, 12: 2}, progress=progress)

        self.assertTrue(report["cancelled"])
        self.assertEqual(report["checked_count"], 1)
        self.assertEqual(self.compared, [11])

    def test_a_progress_bar_that_throws_never_stops_the_run(self):
        class BrokenProgress(object):
            cancelled = False

            def update(self, done, total):
                raise RuntimeError("no bar")

        report = self._run({11: 1, 12: 1}, progress=BrokenProgress())
        self.assertEqual(report["checked_count"], 2)

    def test_no_document(self):
        report = self.module.build_document_review_report(None)
        self.assertTrue(report["detection_error"])
        self.assertEqual(report["links"], [])


class FlaggedByRevitTests(unittest.TestCase):
    """Revit's captured warning survives only as a hint on a row; it never
    decides whether a link gets checked."""

    def setUp(self):
        self.module = _load_module()

    def test_flagged_ids_come_from_the_passive_link_map(self):
        passive = {"link_map": {"11": {"element_id": 11}, "__GENERIC__": {"element_id": None}}}
        self.assertEqual(self.module.flagged_link_ids(passive), {11})

    def test_missing_or_empty_passive_report(self):
        self.assertEqual(self.module.flagged_link_ids(None), set())
        self.assertEqual(self.module.flagged_link_ids({}), set())

    def test_rows_carry_the_flag(self):
        doc = FakeDocument()
        links = {11: FakeLink(11, "ARCH.rvt"), 12: FakeLink(12, "STR.rvt")}
        report = self.module.build_document_review_report(
            doc,
            passive_report={"link_map": {"11": {"element_id": 11}}, "capture": {"matched": 1}},
            collect_link_ids=lambda doc, db=None: {11: 1, 12: 1},
            build_link_report=lambda doc, link, db=None: _link_report(link.Name),
            resolve_link=lambda doc, link_id, db=None: (links[int(link_id)], ""),
        )

        flags = dict((row["link_id"], row["flagged_by_revit"]) for row in report["links"])
        self.assertEqual(flags, {11: True, 12: False})
        self.assertEqual(report["capture"], {"matched": 1})


if __name__ == "__main__":
    unittest.main()
