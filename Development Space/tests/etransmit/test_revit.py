import importlib
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'lib'))
try:
    cleanup=importlib.import_module('easybim_etransmit.cleanup')
    rev=importlib.import_module('easybim_etransmit.revit')
except ImportError:
    cleanup=rev=None

class RevitContractTests(unittest.TestCase):
    def setUp(self): self.assertIsNotNone(rev,'Revit adapter and cleanup implementation missing')
    def test_load_intent(self):
        self.assertFalse(rev.load_intent('Unloaded'))
        self.assertTrue(rev.load_intent('Loaded'))
        self.assertIsNone(rev.load_intent('UndocumentedFutureState'))
    def test_id_64bit(self):
        class ID: Value=2**40
        self.assertEqual(rev.eid(ID()),str(2**40))
    def test_id_legacy(self):
        class ID: IntegerValue=123
        self.assertEqual(rev.eid(ID()),'123')
    def test_keep_all(self):
        self.assertEqual(cleanup.view_deletions([{'id':'1','type':'FloorPlan'}],'all',[]),set())
    def test_sheet_dependent_keeps_primary(self):
        rows=[{'id':'1','type':'FloorPlan'}, {'id':'2','type':'FloorPlan','primary':'1','placed':True},
              {'id':'3','type':'FloorPlan'}]
        self.assertEqual(cleanup.view_deletions(rows,'sheets',[]),{'3'})
    def test_templates_never_deleted(self):
        self.assertEqual(cleanup.view_deletions([{'id':'1','type':'FloorPlan','template':True}],'sheets',[]),set())
    def test_remove_sheets_only(self):
        rows=[{'id':'1','type':'FloorPlan'}, {'id':'2','type':'DrawingSheet'}]
        self.assertEqual(cleanup.view_deletions(rows,'no_sheets_all',[]),{'2'})
    def test_selected_keeps_sheets_and_type(self):
        rows=[{'id':'1','type':'FloorPlan'},{'id':'2','type':'DrawingSheet'},{'id':'3','type':'ThreeD'}]
        self.assertEqual(cleanup.view_deletions(rows,'sheets_selected',['ThreeD']),{'1'})
    def test_empty_selected_rejected(self):
        with self.assertRaises(ValueError): cleanup.view_deletions([],'no_sheets_selected',[])
    def test_no_sheets_selected_keeps_type(self):
        rows=[{'id':'1','type':'FloorPlan'},{'id':'2','type':'DrawingSheet'},{'id':'3','type':'ThreeD'}]
        self.assertEqual(cleanup.view_deletions(rows,'no_sheets_selected',['ThreeD']),{'1','2'})
    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError): cleanup.view_deletions([],'typo',[])
    def test_unknown_view_kinds_preserved(self):
        self.assertEqual(cleanup.view_deletions([{'id':'1','type':'ProjectBrowser','protected':True}],'sheets',[]),set())

if __name__=='__main__': unittest.main()
