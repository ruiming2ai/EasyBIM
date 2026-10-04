"""Experiment isolation and scenario planning; no licensed Revit involved."""
from __future__ import unicode_literals
import io
import json
import os
import sys
import tempfile
import shutil
import unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))

class LabContract(unittest.TestCase):
    def module(self):
        path=os.path.join(ROOT,'lib','easybim_etransmit_tests','scenarios.py')
        self.assertTrue(os.path.isfile(path),'Seven-scenario lab implementation is absent')
        from easybim_etransmit_tests import scenarios
        return scenarios
    def test_eight_groups_with_meaningful_distinct_trials(self):
        s=self.module()
        self.assertEqual(list('ABCDEFGH'),sorted(s.SCENARIOS))
        self.assertEqual(8,len(set(x['title'] for x in s.SCENARIOS.values())))
        for key in sorted(s.SCENARIOS):
            self.assertTrue(s.SCENARIOS[key]['title'].endswith('(test)'))
            self.assertTrue(s.SCENARIOS[key]['question'])
            self.assertTrue(s.trials(key))
        self.assertNotEqual(s.trials('D'),s.trials('E'))
    def test_unknown_scenario_is_rejected(self):
        s=self.module()
        with self.assertRaises(ValueError):s.trials('BAD')
    def test_no_source_save_or_upgrade_in_collection(self):
        s=self.module()
        o=s.collection_options({'repath':True,'upgrade':True,'cleanup':True,'zip':True})
        for k in ('repath','upgrade','cleanup','discard_worksets','purge','zip','zip_per_model'):
            self.assertFalse(o[k],k)
        self.assertTrue(o['saved_state_only']); self.assertTrue(o['simple_repath'])
    def test_duplicate_pairs_are_by_exact_source_not_basename(self):
        s=self.module()
        rows=[dict(element_id='1',kind='CADLink',source=r'N:\One\same.dwg',target='a'),
              dict(element_id='2',kind='CADLink',source=r'N:\One\same.dwg',target='a'),
              dict(element_id='3',kind='CADLink',source=r'N:\Other\same.dwg',target='b')]
        self.assertEqual([['1','2']],[[r['element_id'] for r in g] for g in s.duplicate_groups(rows)])
    def test_empty_and_singletons_are_not_duplicates(self):
        s=self.module()
        self.assertEqual([],s.duplicate_groups([dict(element_id='1',kind='CADLink',source='',target='x')]))
    def test_clean_reopen_is_separate_from_dirty_postsave(self):
        s=self.module()
        result=s.assess(normal=True,paths=True,relative=True,dirty=True,errors=[])
        self.assertEqual('PATHS_MATCH_REVIEW_DIRTY_STATE',result)
        self.assertNotEqual('VERIFIED',s.assess(False,True,True,False,[]))
        self.assertEqual('VERIFIED_TEST_ONLY',s.assess(True,True,True,False,[]))
    def test_eight_buttons_in_test_pulldown_and_bootstrap_noop(self):
        s=self.module()
        parent=os.path.join(ROOT,'EasyBIM.tab','Test.panel','Test.pulldown')
        paths=[p for p in os.listdir(parent) if p.endswith('.smartbutton')]
        self.assertEqual(8,len(paths))
        from easybim_etransmit_tests import bootstrap
        self.assertFalse(bootstrap.install(None,env={}))
        self.assertFalse(bootstrap.install(None,env={'EASYBIM_ETRANSMIT_WORKER_MODE':'1'}))
    def test_source_target_mapping_must_not_escape_package(self):
        s=self.module()
        root=tempfile.mkdtemp();self.addCleanup(shutil.rmtree,root)
        for bad in ('../outside.rvt','/outside.rvt',r'C:\outside.rvt',r'Links\..\..\outside.rvt'):
            with self.assertRaises(ValueError):s.package_path(root,bad)
        self.assertEqual(os.path.realpath(os.path.join(root,'Links','CAD','file.dwg')),s.package_path(root,'Links/CAD/file.dwg'))
    def test_selected_ids_not_silently_substituted(self):
        s=self.module()
        with self.assertRaises(ValueError):s.select_rows([dict(element_id='1',kind='CADLink',target='a')],['2'])
    def test_input_rows_immutable_and_old_success_claims_cleared(self):
        s=self.module()
        rows=[dict(element_id='1',kind='CADLink',source='a',target='b',repath='API_CAD_LINK',verification='PATH_CHECKED')]
        result=s.fresh_rows(rows)
        self.assertNotIn('repath',result[0]);self.assertNotIn('verification',result[0]);self.assertIn('repath',rows[0])

if __name__=='__main__':unittest.main()
