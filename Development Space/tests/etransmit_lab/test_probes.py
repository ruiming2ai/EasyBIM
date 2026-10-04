# -*- coding: utf-8 -*-
"""File-backed API-boundary tests; fake serialization is NOT Autodesk Revit."""
from __future__ import unicode_literals
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
sys.path.insert(0,os.path.join(ROOT,'Development Space','tests','etransmit'))
from test_225_normal_host_api import Runtime,Document,Obj,Reference
from easybim_etransmit_tests.probes import ProbeBackend,run_trial
from easybim_etransmit_tests.evidence import Evidence
from easybim_etransmit_tests.runner import copy_arm
from easybim_etransmit_tests.controller import load_package
from easybim_etransmit_tests.base import files as f

class FakeDocument(Document):
    def __init__(self,*args):
        Document.__init__(self,*args);self.IsDetached=False;self.IsLinked=False;self.IsValidObject=True
    def GetElement(self,ident):
        key=str(ident.Value)
        if key not in self.model['saved']:return None
        def load(arg):
            relative=not isinstance(arg,f.string_types)
            path=arg.path if relative else arg
            for other,values in self.model['saved'].items():
                actual=os.path.normpath(os.path.join(os.path.dirname(self.PathName),values[0])) if values[1]=='Relative' else values[0]
                if other!=key and actual==path:return Obj(LoadResult='LinkExists',ElementId=Obj(Value=int(other)))
            saved=os.path.relpath(path,os.path.dirname(self.PathName)) if relative else path
            self.model['saved'][key]=[saved,'Relative' if relative else 'Absolute',True]
            self.IsModified=True
            return Obj(LoadResult='LinkLoaded',ElementId=ident)
        return Obj(Id=ident,UniqueId='uid-'+key,IsLink=True,LoadFrom=load,
                   GetType=lambda:Obj(FullName='Autodesk.Revit.DB.CADLinkType'),
                   GetExternalFileReference=lambda:Reference(self.model['saved'][key],self.PathName))
    def Close(self,save):self.IsValidObject=False;return Document.Close(self,save)

class Probes(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ETTest_');self.addCleanup(shutil.rmtree,self.root)
        self.stage=os.path.join(self.root,'input.rvt');self.target=os.path.join(self.root,'Host.rvt')
        self.runtime=Runtime()
        self.runtime.write(self.stage,dict(workshared=False,saved={'1':['N:/old/a.dwg','Absolute',True],
                                                                 '2':['N:/old/a.dwg','Absolute',True]},transmitted=False,desired={}))
        def opening(path,options):
            self.runtime.events.append(('open',path,getattr(options,'DetachFromCentralOption',None)))
            return FakeDocument(self.runtime,path,self.runtime.read(path))
        self.app=Obj(VersionNumber='2026',OpenDocumentFile=opening)
        class Save(Obj):
            def SetWorksharingOptions(self,ws):self.worksharing=ws
        self.DB=Obj(SaveAsOptions=Save,WorksharingSaveAsOptions=Obj,
            OpenOptions=lambda:Obj(SetOpenWorksetsConfiguration=lambda x:None),
            WorksetConfiguration=lambda x:Obj(),WorksetConfigurationOption=Obj(OpenAllWorksets='All',CloseAllWorksets='None'),
            DetachFromCentralOption=Obj(DetachAndPreserveWorksets='Preserve',DetachAndDiscardWorksets='Discard'),
            ModelPathUtils=Obj(ConvertUserVisiblePathToModelPath=lambda x:x,ConvertModelPathToUserVisiblePath=lambda x:x),
            BasicFileInfo=Obj(Extract=lambda p:Obj(Format='2026',IsWorkshared=False,IsCentral=False,CentralPath='')),
            TransmissionData=Obj(ReadTransmissionData=self.runtime.read_td,WriteTransmissionData=self.runtime.write_td),
            ExternalFileUtils=Obj(GetExternalFileReference=lambda d,i:d.GetElement(i).GetExternalFileReference()),
            ElementId=lambda n:Obj(Value=n),PathType=Obj(Relative='Relative',Absolute='Absolute'),
            ExternalResourceTypes=Obj(BuiltInExternalResourceTypes=Obj(CADLink='CAD')),
            ExternalResourceReference=Obj(CreateLocalResource=lambda d,t,p,pt:Obj(path=p)))
        self.ev=Evidence(os.path.join(self.root,'Diagnostics'))
        self.backend=ProbeBackend(self.DB,self.app,self.root,self.ev)
        self.backend.set_staging_root(self.root)
        self.old=sys.modules.get('System');sys.modules['System']=Obj(Int64=int,Int32=int)
        self.addCleanup(self.restore)
        self.link=os.path.join(self.root,'Links','a.dwg');os.makedirs(os.path.dirname(self.link))
        with open(self.link,'wb') as out:out.write(b'DWG')
        self.rows=[dict(element_id=str(n),kind='CADLink',source='N:/old/a.dwg',native_source='N:/old/a.dwg',target=self.link) for n in (1,2)]
    def restore(self):
        if self.old is None:sys.modules.pop('System',None)
        else:sys.modules['System']=self.old
    def test_strict_guard_reproduces_dirty_failure_and_keeps_candidate(self):
        self.runtime.modified_after_save=True
        with self.assertRaises(RuntimeError) as caught:
            run_trial(self.backend,self.stage,self.target,[], 'strict_guard',{})
        self.assertIn('REPRODUCED_POST_SAVE_DIRTY_GUARD',str(caught.exception))
        self.assertTrue(os.path.isfile(self.target));self.assertEqual([],self.backend.owned_documents)
    def test_tolerant_arm_does_not_silently_certify_dirty_save(self):
        self.runtime.modified_after_save=True
        result=run_trial(self.backend,self.stage,self.target,[],'close_reopen_evidence',{})
        self.assertEqual('NORMAL_REOPEN_REVIEW_DIRTY_STATE',result['status'])
        self.assertFalse(any(e[0]=='save' for e in self.runtime.events))
    def test_cad_string_reaches_api_and_reads_disk_after_reopen(self):
        result=run_trial(self.backend,self.stage,self.target,self.rows,'cad_string',{'selected_ids':['1']})
        self.assertEqual('PATHS_MATCH_ABSOLUTE_REVIEW',result['status'])
        self.assertTrue(result['rows'][0]['path_matches_after_reopen'])
        self.assertEqual('N:/old/a.dwg',self.runtime.read(self.stage)['saved']['1'][0])
    def test_cad_relative_overload_is_distinct_and_persists(self):
        result=run_trial(self.backend,self.stage,self.target,self.rows,'cad_local_relative',{'selected_ids':['1']})
        self.assertEqual('VERIFIED_TEST_ONLY',result['status'])
        self.assertEqual('Relative',result['rows'][0]['after_reopen']['path_type'])
    def test_duplicate_shared_target_returns_other_type(self):
        result=run_trial(self.backend,self.stage,self.target,self.rows,'shared_target',{'selected_ids':['1','2']})
        self.assertEqual('CAD_ERRORS_RECORDED',result['status'])
        self.assertIn('CAD_TARGET_COLLISION',result['errors'][0])
    def test_duplicate_per_type_targets_avoid_collision(self):
        result=run_trial(self.backend,self.stage,self.target,self.rows,'per_type_target',{'selected_ids':['1','2']})
        self.assertEqual([],result['errors'])
        self.assertEqual(2,len(set(r['target'] for r in result['rows'])))
        self.assertTrue(all(r['path_matches_after_reopen'] for r in result['rows']))
    def test_mismatched_original_is_not_repaired(self):
        self.rows[0]['native_source']='N:/wrong/a.dwg'
        result=run_trial(self.backend,self.stage,self.target,self.rows,'cad_string',{'selected_ids':['1']})
        self.assertEqual('CAD_ERRORS_RECORDED',result['status'])
        self.assertIn('mismatch',result['errors'][0])
    def test_observation_only_inventory_does_not_save(self):
        result=run_trial(self.backend,self.stage,self.target,self.rows,'saved_inventory',{})
        self.assertEqual('INVENTORY_COMPARISON_COMPLETE',result['status'])
        self.assertFalse(any(e[0] in ('save','saveas') for e in self.runtime.events))
        self.assertFalse(os.path.isfile(self.target))
    def test_external_target_cannot_be_written(self):
        self.rows[0]['target']=os.path.join(os.path.dirname(self.root),'escape.dwg')
        result=run_trial(self.backend,self.stage,self.target,self.rows,'cad_string',{'selected_ids':['1']})
        self.assertEqual('CAD_ERRORS_RECORDED',result['status'])
        self.assertIn('outside',result['errors'][0])
    def test_copy_arm_has_real_independent_bytes(self):
        arm=os.path.join(self.root,'Arm')
        stage,target,rows=copy_arm(self.root,self.stage,self.rows,arm)
        with open(stage,'wb') as out:out.write(b'CHANGED')
        self.assertEqual('N:/old/a.dwg',self.runtime.read(self.stage)['saved']['1'][0])
        self.assertTrue(os.path.isfile(rows[0]['target']))
    def test_readback_catches_reverted_disk_path(self):
        doc=self.backend.normalize(self.stage,self.target)
        self.backend.cad_repair(doc,self.rows[0],relative=True)
        def revert():
            doc.model['saved']['1']=['N:/old/a.dwg','Absolute',True]
            FakeDocument.Save(doc)
        doc.Save=revert
        result=self.backend.save_repair_and_verify(doc,self.target,[self.rows[0]])
        self.assertEqual('PATHS_NOT_VERIFIED',result)
    def test_scope_rejects_source_path_open(self):
        other=ProbeBackend(self.DB,self.app,os.path.join(self.root,'Other'),self.ev)
        with self.assertRaises(ValueError):other.open_copy(self.stage)

    def test_external_resources_read_even_when_native_reference_throws(self):
        doc=self.backend.open_copy(self.stage)
        info={'LinkedModelProjectId':'11111111-1111-1111-1111-111111111111',
              'LinkedModelModelId':'22222222-2222-2222-2222-222222222222','LinkedModelRegion':'US'}
        resource=Obj(InSessionPath='Autodesk Docs://Project/Host.rvt',ServerId='server',Version='18',GetReferenceInformation=lambda:info)
        doc.GetElement=lambda i:Obj(Id=i,UniqueId='uid',GetType=lambda:Obj(FullName='Autodesk.Revit.DB.RevitLinkType'),
                                    GetExternalResourceReferences=lambda:Obj(Values=[resource]))
        def absent(d,i):raise ValueError('No native reference for cloud resource')
        self.DB.ExternalFileUtils.GetExternalFileReference=absent
        actual=self.backend.actual_reference(doc,{'element_id':'1','kind':'RevitLink'})
        self.assertEqual(info,actual.get('resources',[{}])[0].get('information'))
        self.backend.close_document(doc)
    def test_cloud_identity_comparison_uses_guids_not_open_uri(self):
        from easybim_etransmit_tests.probes import inventory_comparison
        identity=dict(project_guid='11111111-1111-1111-1111-111111111111',model_guid='22222222-2222-2222-2222-222222222222',region='US')
        row=dict(element_id='1',kind='RevitLink',source='open://opaque/Host.rvt',cloud_identity=identity)
        info={'LinkedModelProjectId':identity['project_guid'],'LinkedModelModelId':identity['model_guid'],'LinkedModelRegion':'US'}
        actual=dict(present=True,absolute='Autodesk Docs://Project/Host.rvt',resources=[dict(information=info)])
        result=inventory_comparison(row,actual)
        self.assertEqual('MATCH_CLOUD_IDENTITY',result['conclusion'])
        self.assertIsNone(result['path_match'])
    def test_duplicate_shared_arm_forces_same_target_even_if_input_split(self):
        second=os.path.join(self.root,'Links','second.dwg')
        with open(second,'wb') as out:out.write(b'DWG')
        self.rows[1]['target']=second
        result=run_trial(self.backend,self.stage,self.target,self.rows,'shared_target',{'selected_ids':['1','2']})
        self.assertEqual(1,len(set(r['target'] for r in result['rows'])))
        self.assertEqual('CAD_ERRORS_RECORDED',result['status'])
    def test_duplicate_inputs_different_bytes_are_not_comparable(self):
        second=os.path.join(self.root,'Links','second.dwg')
        with open(second,'wb') as out:out.write(b'OTHER')
        self.rows[1]['target']=second
        with self.assertRaises(ValueError):
            run_trial(self.backend,self.stage,self.target,self.rows,'shared_target',{'selected_ids':['1','2']})
        self.assertFalse(os.path.isfile(self.target))
    def test_relocated_manifest_resolves_only_listed_files(self):
        data=dict(files=[dict(source='owner',is_primary_host=True,status='COPIED',relative='input.rvt',target='C:/old/input.rvt',sha256=f.digest(self.stage)),
                         dict(status='COPIED',relative='Links/a.dwg',target='C:/old/Links/a.dwg',sha256=f.digest(self.link))],
                  references=[dict(owner='owner',element_id='1',kind='CADLink',target='C:/old/Links/a.dwg',source='N:/old/a.dwg')])
        path=os.path.join(self.root,'manifest.json')
        with io.open(path,'w',encoding='utf-8') as out:out.write(f.text(json.dumps(data)))
        result=load_package(path)
        self.assertEqual(os.path.realpath(self.link),result['rows'][0]['target'])
        with open(self.link,'wb') as out:out.write(b'CHANGED')
        with self.assertRaises(ValueError):load_package(path)
    def test_unknown_native_presence_is_unverified_not_absent(self):
        from easybim_etransmit_tests.probes import inventory_comparison
        result=inventory_comparison({'element_id':'1','kind':'CADLink'},{'present':None,'error':'read failed'})
        self.assertEqual('UNVERIFIED_ELEMENT_READ',result['conclusion'])

    def execute_fixture(self,scenario):
        from easybim_etransmit_tests import runner
        from easybim_etransmit_tests.controller import new_root
        root=new_root(self.root,scenario)
        old_pyrevit=sys.modules.get('pyrevit');old_subscribe=Evidence.subscribe
        sys.modules['pyrevit']=Obj(DB=self.DB)
        Evidence.subscribe=lambda self,*args:None  # Revit event source unavailable in portable fixture
        try:
            result=runner.execute(dict(stage=self.stage,rows=self.rows,options=dict(scenario=scenario,
                experiment_root=root,input_root=self.root,selected_ids=['1'],source_context={})),Obj(Application=self.app))
            return root,result
        finally:
            Evidence.subscribe=old_subscribe
            if old_pyrevit is None:sys.modules.pop('pyrevit',None)
            else:sys.modules['pyrevit']=old_pyrevit
    def test_runner_preserves_failed_arm_and_continues_comparison(self):
        self.runtime.modified_after_save=True;before=f.digest(self.stage)
        root,result=self.execute_fixture('A')
        self.assertEqual('FAILED',result['trial_results'][0]['status'])
        self.assertEqual('NORMAL_REOPEN_REVIEW_DIRTY_STATE',result['trial_results'][1]['status'])
        self.assertEqual(before,f.digest(self.stage))
        for trial in ('strict_guard','close_reopen_evidence'):
            self.assertTrue(os.path.isfile(os.path.join(root,'Trials',trial,'input.rvt')))
            self.assertTrue(os.path.isfile(os.path.join(root,'Trials',trial,'RESULT.json')))
        self.assertTrue(os.path.isfile(os.path.join(root,'TEST_REPORT.txt')))
    def test_runner_inventory_is_before_save_and_read_only(self):
        root,result=self.execute_fixture('F')
        self.assertEqual('INVENTORY_COMPARISON_COMPLETE',result['trial_results'][0]['status'])
        self.assertTrue(result['input_unchanged'])
        self.assertFalse(any(e[0] in ('save','saveas') for e in self.runtime.events))
    def test_runner_recovery_baseline_records_failure_candidate(self):
        self.runtime.modified_after_save=True
        root,result=self.execute_fixture('G')
        self.assertEqual('FAILED',result['trial_results'][0]['status'])
        self.assertIn('save callback modified',result['trial_results'][0]['message'])
        self.assertTrue(os.path.isfile(os.path.join(root,'Trials','baseline_with_evidence','input.rvt')))
        self.assertTrue(result['input_unchanged'])
    def test_private_backend_is_not_production_backend(self):
        from easybim_etransmit import revit as production
        from easybim_etransmit_tests.base import revit as frozen
        self.assertIsNot(production.Backend,frozen.Backend)
        self.assertEqual('easybim_etransmit.revit',production.Backend.__module__)

    def test_runner_sequence_has_two_distinct_metadata_arms(self):
        root,result=self.execute_fixture('C')
        self.assertEqual(2,len(result['trial_results']))
        self.assertEqual(['without_metadata','with_cad_metadata'],[r['trial'] for r in result['trial_results']])
        self.assertTrue(result['input_unchanged'])
        self.assertTrue(all(r['status']=='NORMAL_REOPEN_TEST_ONLY' for r in result['trial_results']))
    def test_runner_event_probe_records_environment_without_disabling(self):
        root,result=self.execute_fixture('B')
        with io.open(os.path.join(root,'Diagnostics','events.jsonl'),encoding='utf-8') as inp:
            events=[json.loads(line) for line in inp]
        census=[e for e in events if e['event']=='loaded_environment']
        self.assertEqual(1,len(census))
        self.assertIn('No add-ins were disabled',census[0]['isolation'])
        self.assertTrue(result['input_unchanged'])


    def test_open_copy_accepts_filename_only_detached_path_and_tracks_it_for_cleanup(self):
        original=self.app.OpenDocumentFile
        def opening(path,options):
            doc=original(path,options)
            doc.PathName='input_detached.rvt'
            doc.IsDetached=True
            return doc
        self.app.OpenDocumentFile=opening
        doc=self.backend.open_copy(self.stage)
        self.assertIn(doc,self.backend.owned_documents)
        self.backend.cleanup_documents()
        self.assertEqual([],self.backend.owned_documents)

    def test_proven_repath_trial_continues_past_dirty_save_and_verifies_cad_after_normal_reopen(self):
        self.runtime.modified_after_save=True
        result=run_trial(self.backend,self.stage,self.target,[copy.deepcopy(self.rows[0])],'proven_repath',{})
        self.assertEqual('REPATH_VERIFIED_TEST_ONLY',result['status'])
        self.assertTrue(result['initial_save_dirty'])
        self.assertTrue(result['normal_open_verified'])
        self.assertTrue(result['rows'][0]['path_matches_after_reopen'])
        self.assertEqual('Relative',result['rows'][0]['after_reopen']['path_type'])
        self.assertEqual('N:/old/a.dwg',self.runtime.read(self.stage)['saved']['1'][0])

    def test_scenario_h_is_one_focused_proven_repath_trial(self):
        from easybim_etransmit_tests import scenarios
        self.assertEqual(('proven_repath',),scenarios.trials('H'))

if __name__=='__main__':unittest.main()
