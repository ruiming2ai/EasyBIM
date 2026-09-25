# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, tempfile, shutil, unittest, copy
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import session as s, files as f, engine, batch
from test_payload_acquisition import compound
from test_212_cache import P,M,V,W,Obj

class SavedCacheSession(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_session212_');self.addCleanup(shutil.rmtree,self.root)
        self.cache=os.path.join(self.root,'CollaborationCache')
        folder=os.path.join(self.cache,'USER',P);os.makedirs(folder)
        self.original=os.path.join(folder,M+'.rvt');self.payload=compound()
        with open(self.original,'wb') as o:o.write(self.payload)
        self.version=Obj(VersionGUID=V,NumberOfSaves=9,Dispose=lambda:None)
        def unsafe(*args):self.fail('must not save, sync, publish, reload or close any original Document')
        self.doc=Obj(Title='Host',PathName='Autodesk Docs://Project/Host.rvt',IsModelInCloud=True,
                     IsLinked=False,IsModified=False,IsDetached=False,IsReadOnly=False,
                     IsModifiable=False,IsWorkshared=True,GetDocumentVersion=lambda d:self.version,
                     GetCloudModelPath=lambda:Obj(GetModelGUID=lambda:M,GetProjectGUID=lambda:P,Region='US',Dispose=lambda:None),
                     Save=unsafe,SaveAs=unsafe,SaveCloudModel=unsafe,SynchronizeWithCentral=unsafe,Close=unsafe)
        self.db=Obj(BasicFileInfo=Obj(Extract=lambda p:Obj(Format='2024',IsWorkshared=True,
            GetDocumentVersion=lambda:self.version,Dispose=lambda:None)))
        self.app=Obj(VersionNumber='2024')
        self.r=s.Registry(self.db,self.app,os.path.join(self.root,'working'),collect_plugins=False)
        self.r.cache_roots=[self.cache]
        self.r.scanner.elements=lambda *a:[];self.r.scanner.scan_open=lambda *a:None
        self.addCleanup(self.r.close)
    def test_default_source_uses_readonly_local_cache_no_confirmation_or_save(self):
        self.r.confirm_snapshot=lambda *a:self.fail('cache export must not ask to save')
        key=self.r.add_live(self.doc);before=self.doc.PathName
        out=os.path.join(self.root,'out');opts=f.defaults();opts['repath']=False
        b=s.SessionBackend(self.db,self.app,out,self.r)
        result=engine.transmit([key],out,b,opts)
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1,repr(result['issues']))
        host=result['files'][0]
        self.assertEqual(f.digest(self.original),f.digest(host['target']))
        self.assertEqual(self.doc.PathName,before)
        self.assertEqual(host['source_context']['state_basis'],'VERIFIED_LOCAL_CACHE_SAVED_STATE')
        self.assertNotIn('saved_state_backup',host)
        self.assertFalse(os.path.exists(os.path.join(out,'_HostState')))
        self.assertEqual(host['saved_state_sha256'],f.digest(self.original))
    def test_loaded_link_cache_can_be_copied_without_SaveAs_or_APS(self):
        self.doc.IsLinked=True;key=self.r.add_live(self.doc)
        target=os.path.join(self.root,'package','Arch.rvt')
        b=s.SessionBackend(self.db,self.app,os.path.dirname(target),self.r)
        meta=b.acquire_file(key,target)
        self.assertEqual(f.digest(target),f.digest(self.original))
        self.assertEqual(meta['copy_method'],'COLLABORATION_CACHE_READ_ONLY')
        self.assertFalse(meta.get('saved_state_backup'),'linked models are not primary-host backups')
    def test_missing_cache_never_falls_back_to_SaveAs_or_published(self):
        os.remove(self.original);key=self.r.add_live(self.doc)
        self.r.confirm_snapshot=lambda *a:self.fail('must not prompt to save')
        self.r.cloud_file=lambda *a:self.fail('must not substitute published model')
        with self.assertRaises(s.SourceError) as ctx:self.r.snapshot(key)
        self.assertEqual(ctx.exception.code,'CACHE_MODEL_NOT_FOUND')
    def test_local_saved_host_excludes_unsaved_changes_and_no_SaveAs(self):
        local=os.path.join(self.root,'Host.rvt')
        with open(local,'wb') as o:o.write(self.payload)
        self.doc.PathName=local;self.doc.IsModelInCloud=False;self.doc.IsModified=True
        key=self.r.add_live(self.doc);path=self.r.snapshot(key)
        self.assertEqual(f.digest(path),f.digest(local))
        self.assertEqual(self.r.get(key)['state_basis'],'VERIFIED_LOCAL_FILE_SAVED_STATE')
        self.assertTrue(self.doc.IsModified)
    def test_modified_host_does_not_use_unsaved_reference_inventory(self):
        self.doc.IsModified=True
        self.r.scanner.scan_open=lambda doc,source,info,result:result['references'].append(dict(id='unsaved-only',source='NeverSaved.pdf',kind='Image'))
        key=self.r.add_live(self.doc);b=s.SessionBackend(self.db,self.app,self.root,self.r)
        self.assertIsNone(b.inventory_before_copy(key,f.defaults()))
        self.r.snapshot(key)
        old=s.Backend.scan
        def saved_scan(inner,source,stage,options):
            return dict(references=[dict(id='saved',source='Saved.pdf',kind='Image')],issues=[],version='2024')
        s.Backend.scan=saved_scan
        try:result=b.scan(key,os.path.join(self.root,'stage.rvt'),f.defaults())
        finally:s.Backend.scan=old
        self.assertEqual([r['id'] for r in result['references']],['saved'])
    def test_original_version_change_during_capture_is_rejected(self):
        key=self.r.add_live(self.doc);done=[False]
        def pulse(*args):
            if not done[0]:
                done[0]=True;self.version=Obj(VersionGUID=W,NumberOfSaves=10,Dispose=lambda:None)
        b=s.SessionBackend(self.db,self.app,self.root,self.r)
        with self.assertRaises(s.SourceError):b.acquire_file(key,os.path.join(self.root,'copy.rvt'),pulse=pulse)
    def test_cache_tampering_before_second_job_is_detected_in_owned_snapshot(self):
        key=self.r.add_live(self.doc);path=self.r.snapshot(key)
        with open(path,'ab') as o:o.write(b'not the validated snapshot')
        with self.assertRaises(s.SourceError):self.r.snapshot(key)
    def test_cloud_cache_link_is_not_skipped_by_legacy_skip_cloud_download_option(self):
        self.doc.IsLinked=True;key=self.r.add_live(self.doc)
        b=s.SessionBackend(self.db,self.app,self.root,self.r)
        self.assertFalse(b.skip_dependency(key,'owner',dict(skip_cloud_links=True)))
    def test_two_named_jobs_and_zips_retain_saved_hosts(self):
        key=self.r.add_live(self.doc)
        other=copy.copy(self.doc);other.Title='Second Host'
        other.GetCloudModelPath=lambda:Obj(GetModelGUID=lambda:W,GetProjectGUID=lambda:P,Dispose=lambda:None)
        with open(os.path.join(self.cache,'USER',P,W+'.rvt'),'wb') as o:o.write(self.payload)
        key2=self.r.add_live(other)
        opts=f.defaults();opts.update(repath=False,zip_per_model=True)
        out=os.path.join(self.root,'batch')
        results=batch.run_batch([key,key2],out,lambda root,k:s.SessionBackend(self.db,self.app,root,self.r),opts,
                                model_names={key:'Host.rvt',key2:'Second Host.rvt'})
        self.assertEqual(len(results),2)
        for name,result in zip(('Host','Second Host'),results):
            self.assertEqual(engine.package_counts(result)['hosts_copied'],1,repr(result['issues']))
            self.assertEqual(os.path.basename(result['root']),name)
            self.assertTrue(os.path.isfile(os.path.join(out,name+'.zip')))
    def test_failed_link_does_not_destroy_primary_saved_host(self):
        key=self.r.add_live(self.doc)
        self.r.get(key)['inventory']['references'].append(dict(id='1',element_id='1',kind='RevitLink',source='Autodesk Docs://Project/Missing.rvt',td=False))
        out=os.path.join(self.root,'broken-link');b=s.SessionBackend(self.db,self.app,out,self.r)
        b.finish=lambda *a:self.fail('host with missing cloud link must not be modified')
        result=engine.transmit([key],out,b,f.defaults())
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1,repr(result['issues']))
        host=result['files'][0]
        self.assertEqual(host['processing_status'],'HOST_PRESERVED_LINKS_UNAVAILABLE')
        self.assertEqual(f.digest(host['target']),f.digest(self.original))
        self.assertFalse(os.path.exists(os.path.join(out,'_HostState')))

if __name__=='__main__':unittest.main(verbosity=2)
