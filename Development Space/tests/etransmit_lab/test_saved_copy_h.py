# -*- coding: utf-8 -*-
"""H regressions with serialized fake documents, NOT native Autodesk Revit."""
from __future__ import unicode_literals
import os
import copy
import json
import sys
import unittest

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
sys.path.insert(0,os.path.join(ROOT,'Development Space','tests','etransmit'))
import test_probes as fixture
FakeDocument=fixture.FakeDocument
Obj=fixture.Obj
from easybim_etransmit_tests.probes import ProbeBackend

from easybim_etransmit_tests.saved_copy import SavedCopyBackend
BackendUnderTest=SavedCopyBackend

def call_fixture(method, instance):
    # Python 2 enforces the declaring class on unbound methods. Reuse the
    # underlying setup function, not an unrelated class's bound method.
    function = getattr(method, 'im_func', getattr(method, '__func__', method))
    return function(instance)


class SavedCopyH(unittest.TestCase):
    def setUp(self):
        # Reuse only setup, not inherited tests (avoid double counting them).
        call_fixture(fixture.Probes.setUp, self)
        self.docs=[];self.created=[];self.name_mode='leaf';self.keep_detached=False
        self.close_fails=False
        owner=self
        class TaskDocument(FakeDocument):
            def __init__(self,runtime,path,model,detached):
                FakeDocument.__init__(self,runtime,path,model)
                self.disk_path=path;self.IsDetached=detached;self.Title='input_detached'
                self.IsReadOnly=False;self.IsModifiable=False;self.IsModelInCloud=False
                if detached and owner.name_mode=='leaf':self.PathName='input_detached.rvt'
                if detached and owner.name_mode=='numbered':self.PathName='input_detached_1.rvt'
                if detached and owner.name_mode=='empty':self.PathName=''
                if detached and owner.name_mode=='escape':self.PathName='../outside.rvt'
                if detached and owner.name_mode=='absolute_escape':self.PathName=os.path.join(os.path.dirname(owner.root),'outside.rvt')
            def SaveAs(self,target,options):
                FakeDocument.SaveAs(self,target,options)
                self.disk_path=target;self.IsDetached=owner.keep_detached
            def GetElement(self,ident):
                element=FakeDocument.GetElement(self,ident)
                if element:element.WorksetId=Obj(IntegerValue=1)
                return element
            def Close(self,save):
                if owner.close_fails:return False
                value=FakeDocument.Close(self,save)
                if self in owner.docs:owner.docs.remove(self)
                return value
        def opening(path,options):
            doc=TaskDocument(self.runtime,path,self.runtime.read(path),getattr(options,'DetachFromCentralOption',None) is not None)
            self.runtime.events.append(('open',path,getattr(options,'DetachFromCentralOption',None)))
            self.docs.append(doc);self.created.append(doc);return doc
        self.app.Documents=self.docs;self.app.OpenDocumentFile=opening
        self.DB.WorksetConfiguration=lambda mode: Obj(Open=lambda ids:None)
        self.DB.WorksetId=Obj
        self.DB.ImportInstance=Obj
        self.DB.FilteredElementCollector=lambda doc: Obj(OfClass=lambda cls:[])
        self.DB.TransmissionData.IsDocumentTransmitted=lambda p:self.runtime.read(p).get('transmitted',False)
        self.DB.BasicFileInfo.Extract=lambda p: Obj(Format='2026',IsWorkshared=self.runtime.read(p)['workshared'],IsCentral=self.runtime.read(p).get('is_central',False),CentralPath=self.runtime.read(p).get('central',''))
        self.model=self.runtime.read(self.stage);self.model.update(workshared=True,is_central=False,central='N:/original/Host.rvt')
        self.runtime.write(self.stage,self.model)
        self.backend=BackendUnderTest(self.DB,self.app,self.root,self.ev)
        self.backend.set_staging_root(self.root)
    def restore(self):call_fixture(fixture.Probes.restore, self)
    def tearDown(self):
        self.close_fails=False
        for doc in list(self.docs):doc.Close(False)
    def open_expected(self,**kwargs):
        try:return self.backend.open_copy(self.stage,**kwargs)
        except Exception as exc:self.fail('Valid task copy rejected: '+str(exc))
    def test_detached_leaf_name_is_not_an_output_path(self):
        doc=self.open_expected(close_worksets=True)
        self.assertEqual('input_detached.rvt',doc.PathName)
        self.assertIn(doc,self.backend.owned_documents)
        self.backend.cleanup_documents();self.assertEqual([],self.docs)
    def test_numbered_detached_leaf_is_accepted(self):
        self.name_mode='numbered'
        doc=self.open_expected()
        self.assertTrue(doc.IsDetached)
        self.backend.cleanup_documents();self.assertFalse(doc.IsValidObject)
    def test_empty_detached_name_is_accepted(self):
        self.name_mode='empty'
        doc=self.backend.open_copy(self.stage)
        self.assertEqual('',doc.PathName)
        self.backend.cleanup_documents();self.assertEqual([],self.docs)
    def test_rejected_new_document_is_closed_before_exception_returns(self):
        self.name_mode='escape'
        with self.assertRaises(ValueError):self.backend.open_copy(self.stage)
        self.assertEqual([],self.docs)
    def test_absolute_outside_document_is_rejected_and_closed(self):
        self.name_mode='absolute_escape'
        with self.assertRaises(ValueError):self.backend.open_copy(self.stage)
        self.assertEqual([],self.docs)
    def test_preexisting_document_is_never_adopted_or_closed(self):
        self.name_mode='full'
        source=self.app.OpenDocumentFile(self.stage,Obj())
        self.app.OpenDocumentFile=lambda *args:source
        with self.assertRaises(RuntimeError):self.backend.open_copy(self.stage)
        self.assertEqual([],self.backend.owned_documents)
        self.assertTrue(source.IsValidObject)
    def test_linked_document_is_never_closed(self):
        self.name_mode='full'
        original=self.app.OpenDocumentFile
        def opening(*args):
            doc=original(*args);doc.IsLinked=True;return doc
        self.app.OpenDocumentFile=opening
        with self.assertRaises(RuntimeError):self.backend.open_copy(self.stage)
        self.assertEqual([],self.backend.owned_documents)
        self.assertTrue(self.created[-1].IsValidObject)
    def test_relative_file_argument_never_reaches_revit(self):
        with self.assertRaises(ValueError):self.backend.open_copy('input_detached.rvt')
        self.assertEqual([],self.created)
    def test_failure_to_close_retains_ownership_for_final_cleanup(self):
        self.close_fails=True;self.name_mode='escape'
        with self.assertRaises(Exception):self.backend.open_copy(self.stage)
        self.assertEqual(1,len(self.backend.owned_documents))
        self.close_fails=False;self.backend.cleanup_documents()
        self.assertEqual([],self.docs)
    def test_snapshot_is_recorded_before_rejection(self):
        self.name_mode='escape'
        with self.assertRaises(ValueError):self.backend.open_copy(self.stage)
        states=[e for e in self.ev.events if e['event']=='document_state']
        self.assertTrue(states)
        self.assertEqual('../outside.rvt',states[0]['state']['PathName'])

    def run_h(self):
        from easybim_etransmit_tests import saved_copy
        self.assertTrue(hasattr(saved_copy,'run_saved_copy'), 'H end-to-end test is missing')
        return saved_copy.run_saved_copy(self.backend,self.stage,self.target,self.rows,
                                        {'selected_ids':['1']})
    def test_save_cad_and_normal_reopen_start_from_detached_leaf(self):
        before=self.runtime.read(self.stage)
        result=self.run_h()
        self.assertTrue(result['saved_reference_verified'])
        self.assertTrue(result['normal_open_verified'])
        self.assertEqual(self.link,result['rows'][0]['after_reopen']['absolute'])
        self.assertEqual(before,self.runtime.read(self.stage))
        self.assertEqual([],self.docs)
        opens=[e for e in self.runtime.events if e[0]=='open']
        self.assertEqual('Preserve',opens[0][2])
        self.assertTrue(all(e[2] is None for e in opens[1:]))
        self.assertEqual(1,len([e for e in self.runtime.events if e[0]=='saveas']))
        self.assertEqual(1,len([e for e in self.runtime.events if e[0]=='save']))
    def test_dirty_postsave_does_not_prevent_cad_trial_or_claim_production_safety(self):
        self.runtime.modified_after_save=True
        result=self.run_h()
        self.assertTrue(result['saved_reference_verified'])
        self.assertTrue(result['dirty_seen'])
        self.assertIn('REVIEW',result['status'])
        self.assertEqual(1,len([e for e in self.runtime.events if e[0]=='save']))
    def test_other_cad_reference_is_not_repathed(self):
        before=copy.deepcopy(self.model['saved']['2'])
        self.run_h()
        self.assertEqual(before,self.runtime.read(self.target)['saved']['2'])
    def test_stale_inventory_blocks_before_save(self):
        self.rows[0]['source']='N:/not-the-saved-reference.dwg'
        self.rows[0]['native_source']='N:/not-the-saved-reference.dwg'
        with self.assertRaises(RuntimeError):self.run_h()
        self.assertFalse(os.path.isfile(self.target))
        self.assertEqual([],self.docs)
    def test_save_failure_does_not_report_success_or_leak_document(self):
        original=self.app.OpenDocumentFile
        def opening(*args):
            doc=original(*args)
            def fail(*args):raise RuntimeError('SAVE_FAILURE')
            doc.SaveAs=fail
            return doc
        self.app.OpenDocumentFile=opening
        with self.assertRaises(RuntimeError):self.run_h()
        self.assertEqual([],self.docs)
    def test_saved_path_that_reverts_on_reopen_is_not_verified(self):
        original=self.runtime.write
        def write(path,model):
            data=copy.deepcopy(model)
            if path==self.target:data['saved']['1']=copy.deepcopy(self.model['saved']['1'])
            return original(path,data)
        self.runtime.write=write
        result=self.run_h()
        self.assertFalse(result['saved_reference_verified'])
        self.assertIn('FAILED',result['status'])
    def test_transmitted_input_copy_is_cleared_by_save_not_metadata_flag_hack(self):
        self.model['transmitted']=True;self.runtime.write(self.stage,self.model)
        result=self.run_h()
        self.assertTrue(result['normal_open_verified'])
        self.assertFalse(self.runtime.read(self.target)['transmitted'])
        self.assertTrue(self.runtime.read(self.stage)['transmitted'])
        self.assertFalse([e for e in self.runtime.events if e[0]=='metadata'])
    def test_in_memory_detached_flag_after_save_is_not_final_open_state(self):
        self.keep_detached=True
        result=self.run_h()
        self.assertTrue(result['normal_open_verified'])
    def test_cad_repair_requires_exactly_one_selected_loaded_dwg(self):
        from easybim_etransmit_tests import saved_copy
        self.assertTrue(hasattr(saved_copy,'run_saved_copy'))
        for ids in ([],['1','2'],['missing']):
            with self.assertRaises(ValueError):
                saved_copy.run_saved_copy(self.backend,self.stage,self.target,self.rows,{'selected_ids':ids})
        self.assertEqual([],self.docs)
    def test_unloaded_selected_cad_is_not_silently_loaded(self):
        self.rows[0]['loaded']=False
        with self.assertRaises(ValueError):self.run_h()
        self.assertEqual([],self.docs)
    def test_h_dispatch_exists_and_legacy_scenarios_keep_original_trials(self):
        from easybim_etransmit_tests import scenarios
        self.assertIn('H',scenarios.SCENARIOS)
        self.assertEqual(('saved_copy_cad',),scenarios.trials('H'))
        self.assertEqual(('strict_guard','close_reopen_evidence'),scenarios.trials('A'))
    def test_h_button_is_present(self):
        folder=os.path.join(ROOT,'EasyBIM.tab','Test.panel','Test.pulldown',
                            'e-transmit H Saved Copy Repath (test).smartbutton')
        self.assertTrue(os.path.isfile(os.path.join(folder,'script.py')))
        with open(os.path.join(folder,'script.py')) as source:
            self.assertIn("'H'",source.read())

    def test_runner_h_uses_corrected_backend_and_meaningful_summary(self):
        from easybim_etransmit_tests import runner
        from easybim_etransmit_tests.controller import new_root
        from easybim_etransmit_tests.evidence import Evidence
        root=new_root(self.root,'H')
        old_pyrevit=sys.modules.get('pyrevit');old_subscribe=Evidence.subscribe
        sys.modules['pyrevit']=Obj(DB=self.DB)
        Evidence.subscribe=lambda *args:None
        try:
            result=runner.execute(dict(stage=self.stage,rows=self.rows,options=dict(
                scenario='H',experiment_root=root,input_root=self.root,selected_ids=['1'])),
                Obj(Application=self.app))
        finally:
            Evidence.subscribe=old_subscribe
            if old_pyrevit is None:sys.modules.pop('pyrevit',None)
            else:sys.modules['pyrevit']=old_pyrevit
        self.assertTrue(result['input_unchanged'])
        self.assertTrue(result['trial_results'][0]['saved_reference_verified'])
        self.assertNotEqual('EXPERIMENTS_COMPLETED',result['status'])
        with open(os.path.join(root,'TEST_REPORT.txt')) as inp:report=inp.read()
        self.assertIn('Reopened CAD:',report)
        self.assertEqual([],self.docs)
    def test_runner_h_failure_is_explicit_and_preserves_selected_rows(self):
        from easybim_etransmit_tests import runner
        from easybim_etransmit_tests.controller import new_root
        from easybim_etransmit_tests.evidence import Evidence
        root=new_root(self.root,'H')
        old_pyrevit=sys.modules.get('pyrevit');old_subscribe=Evidence.subscribe
        sys.modules['pyrevit']=Obj(DB=self.DB);Evidence.subscribe=lambda *args:None
        self.name_mode='escape'
        try:
            result=runner.execute(dict(stage=self.stage,rows=self.rows,options=dict(
                scenario='H',experiment_root=root,input_root=self.root,selected_ids=['1'])),
                Obj(Application=self.app))
        finally:
            Evidence.subscribe=old_subscribe
            if old_pyrevit is None:sys.modules.pop('pyrevit',None)
            else:sys.modules['pyrevit']=old_pyrevit
        self.assertEqual('FAILED',result['status'])
        self.assertEqual('open_copy',result['trial_results'][0]['failed_stage'])
        self.assertEqual(2,len(result['trial_results'][0]['rows']))
        self.assertEqual([],self.docs)
    def test_legacy_backend_control_still_reproduces_original_error(self):
        old=ProbeBackend(self.DB,self.app,self.root,self.ev)
        with self.assertRaises(ValueError):old.open_copy(self.stage)
        self.assertFalse(old.owned_documents)
    def test_normal_reopen_returning_detached_document_is_rejected(self):
        original=self.app.OpenDocumentFile
        def opening(path,options):
            doc=original(path,options)
            if not getattr(options,'DetachFromCentralOption',None):doc.IsDetached=True
            return doc
        self.app.OpenDocumentFile=opening
        with self.assertRaises(RuntimeError):self.run_h()
        self.assertEqual([],self.docs)
    def test_candidate_central_still_associated_with_source_is_rejected(self):
        original=self.runtime.write
        def write(path,model):
            data=copy.deepcopy(model)
            if path==self.target:data['central']='N:/original/Host.rvt'
            return original(path,data)
        self.runtime.write=write
        with self.assertRaises(RuntimeError):self.run_h()
        self.assertEqual([],self.docs)
    def test_read_only_new_document_is_rejected_and_closed(self):
        original=self.app.OpenDocumentFile
        def opening(*args):
            doc=original(*args);doc.IsReadOnly=True;return doc
        self.app.OpenDocumentFile=opening
        with self.assertRaises(RuntimeError):self.backend.open_copy(self.stage)
        self.assertEqual([],self.docs)

    def test_dirty_on_initial_open_remains_review_even_when_save_cleans_flag(self):
        original=self.app.OpenDocumentFile
        def opening(*args):
            doc=original(*args)
            if doc.IsDetached:doc.IsModified=True
            return doc
        self.app.OpenDocumentFile=opening
        result=self.run_h()
        self.assertTrue(result['saved_reference_verified'])
        self.assertTrue(result['dirty_seen'])
        self.assertIn('REVIEW',result['status'])
    def test_cad_target_collision_is_not_accepted(self):
        self.model['saved']['2']=[self.link,'Absolute',True]
        self.runtime.write(self.stage,self.model)
        with self.assertRaises(RuntimeError) as caught:self.run_h()
        self.assertIn('CAD_TARGET_COLLISION',str(caught.exception))
        self.assertEqual([],self.docs)
    def test_correct_path_but_unloaded_after_save_is_not_verified(self):
        original=self.runtime.write
        def write(path,model):
            data=copy.deepcopy(model)
            if path==self.target:data['saved']['1'][2]=False
            return original(path,data)
        self.runtime.write=write
        result=self.run_h()
        self.assertFalse(result['saved_reference_verified'])
        self.assertIn('FAILED',result['status'])
    def test_rebased_old_path_after_saveas_does_not_block_same_cad_unique_id(self):
        original=self.app.OpenDocumentFile
        def opening(*args):
            doc=original(*args)
            if doc.IsDetached:
                save=doc.SaveAs
                def saveas(target,options):
                    doc.model['saved']['1'][0]='N:/rebased/oldpath.dwg'
                    return save(target,options)
                doc.SaveAs=saveas
            return doc
        self.app.OpenDocumentFile=opening
        self.assertTrue(self.run_h()['saved_reference_verified'])
    def test_clean_relative_saved_path_has_test_only_success(self):
        original=self.app.OpenDocumentFile
        def opening(*args):
            doc=original(*args);get=doc.GetElement
            def getelement(ident):
                element=get(ident)
                if element:
                    reload=element.LoadFrom
                    def load(path):
                        result=reload(path)
                        doc.model['saved'][str(ident.Value)]=[
                            os.path.relpath(path,os.path.dirname(doc.PathName)),'Relative',True]
                        return result
                    element.LoadFrom=load
                return element
            doc.GetElement=getelement
            return doc
        self.app.OpenDocumentFile=opening
        result=self.run_h()
        self.assertTrue(result['path_is_relative'])
        self.assertEqual('CAD_REPATH_VERIFIED_TEST_ONLY',result['status'])

if __name__=='__main__':unittest.main()
