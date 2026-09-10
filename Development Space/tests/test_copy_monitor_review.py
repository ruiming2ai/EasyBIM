import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/"lib"))
from easybim import copy_monitor_review as ui

class ReviewTests(unittest.TestCase):
    def report(self,status):
        return dict(status=status, record=dict(id=status, link_uid="L",source_uid="S",
                    destination_uid="D",baseline_source={"family":"Light","type_label":"A"},
                    parameter_issues=[]), source_changes=["Length"], destination_changes=[],
                    source={"params":{"Length":2}},destination={"params":{"Length":1}})
    def test_pending_filter_retains_failures_and_excludes_acknowledged(self):
        rows = ui.build_rows([self.report(x) for x in ("unchanged","accepted","scan_error","source_changed")])
        self.assertEqual(["scan_error","source_changed"],[r.StatusKey for r in ui.filter_rows(rows,"Pending")])
    def test_cancelled_scan_never_reports_all_clear(self):
        result = ui.scan_summary(dict(completed=3,total=10,cancelled=True))
        self.assertIn("Cancelled",result)
        self.assertIn("3 of 10",result)
    def test_details_include_source_and_local_changes(self):
        row = ui.build_rows([self.report("conflict")])[0]
        self.assertIn("Length",row.Details)
        self.assertIn("2",row.Details)
        self.assertIn("1",row.Details)
