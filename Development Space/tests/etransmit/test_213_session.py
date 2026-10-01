# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import copy, json, os, shutil, unittest, zipfile
import test_212_session as fixtures
from test_212_cache import P, M, V, W, Obj
from test_payload_acquisition import compound
from easybim_etransmit import cache_sources as c, session as s, files as f, engine, worker

N='55555555-5555-4555-8555-555555555555'

def cloud_row(model=W,name='Architecture.rvt',ident='4815243',loaded=False):
    return dict(id=ident+':0',element_id=ident,kind='RevitLink',link_name=name,
        source='',in_session_path='',loaded=loaded,special='external',td=False,
        resource_information=dict(LinkedModelProjectId=P,LinkedModelModelId=model,LinkedModelRegion='US'))


class SavedCloudLinks(fixtures.SavedCacheSession):
    def put_link(self,model=W,name='Architecture.rvt'):
        folder=os.path.join(self.cache,'USER',P,'LinkedModels')
        if not os.path.isdir(folder):os.makedirs(folder)
        path=os.path.join(folder,model+'.rvt')
        with open(path,'wb') as out:out.write(compound(suffix=name))
        return path

    def saved_scans(self,rows):
        old=s.Backend.scan
        def scan(backend,source,stage,options):
            name=source.rsplit('/',1)[-1]
            return dict(references=copy.deepcopy(rows.get(name,[])),issues=[],version='2024')
        s.Backend.scan=scan
        self.addCleanup(setattr,s.Backend,'scan',old)

    def run_export(self,rows,load=True,repath=False):
        self.doc.IsModified=True
        key=self.r.add_live(self.doc)
        self.r.get(key)['inventory']['references']=copy.deepcopy(rows.get('Host.rvt',[]))
        self.saved_scans(rows)
        out=os.path.join(self.root,'out')
        backend=s.SessionBackend(self.db,self.app,out,self.r)
        options=f.defaults();options.update(repath=repath,load_unloaded_files=load,skip_cloud_links=True)
        backend.verify_package=lambda *args:self.fail('worker-checked host must not reopen in the parent')
        backend.mark_transmitted_package=lambda *args:self.fail('ordinary finalized host must not be marked transmitted')
        return engine.transmit([key],out,backend,options)

    def assert_finalized_host(self,result):
        host=next(row for row in result['files'] if row.get('is_primary_host'))
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1,repr(result['issues']))
        self.assertEqual(host['status'],'COPIED')
        self.assertTrue(host['host_finalized'])
        self.assertTrue(host['saved_references_checked'])
        self.assertTrue(host['independent_package_central'])
        self.assertEqual(host['model_verification'],'SAVED_REFERENCES_CHECKED')
        self.assertEqual(host['transmission_status'],'NOT_TRANSMITTED')
        self.assertEqual(host['opening_guidance'],'OPEN_NORMALLY_INDEPENDENT_PACKAGE')
        self.assertFalse(host['original_central_association_preserved'])
        return host

    def assert_retained_original(self,result):
        host=next(row for row in result['files'] if row.get('is_primary_host'))
        self.assertEqual(engine.package_counts(result)['hosts_copied'],0,repr(result['issues']))
        self.assertEqual(host['status'],'NOT_FINALIZED')
        self.assertFalse(host.get('host_finalized'))
        self.assertFalse(os.path.exists(host['target']))
        self.assertFalse(f.within(host['recovery_path'],result['root']))
        self.assertEqual(f.digest(host['recovery_path']),f.digest(self.original))
        self.assertFalse(os.path.exists(os.path.join(result['root'],'_HostState')))
        self.addCleanup(f.remove_tree_retry,result['recovery_directory'])
        return host

    def test_anthropology_unloaded_identity_only_link_is_copied(self):
        original=self.put_link();stamp=os.stat(original).st_mtime;digest=f.digest(original)
        result=self.run_export({'Host.rvt':[cloud_row()]})
        self.assert_finalized_host(result)
        self.assertEqual(engine.package_counts(result)['revit_links_copied'],1,repr(result['issues']))
        link=result['files'][1]
        self.assertEqual(link['source_context']['mode'],'CACHED_CLOUD_REFERENCE')
        self.assertEqual(link['source_context']['inventory_basis'],'DIRECT_LINK_COPY')
        self.assertEqual(f.digest(link['target']),digest)
        self.assertEqual((f.digest(original),os.stat(original).st_mtime),(digest,stamp))
        self.assertEqual(self.doc.PathName,'Autodesk Docs://Project/Host.rvt')
        self.assertTrue(self.doc.IsModified)
        self.assertFalse(any(i.get('severity')=='error' for i in result['issues']))

    def test_collected_cloud_link_is_not_reopened_for_nested_dependencies(self):
        self.put_link();self.put_link(N,'Structure.rvt')
        result=self.run_export({'Host.rvt':[cloud_row()],
            'Architecture.rvt':[cloud_row(N,'Structure.rvt','2')],
            'Structure.rvt':[cloud_row(W,'Architecture.rvt','3')]})
        self.assert_finalized_host(result)
        self.assertEqual(len(result['files']),2,repr(result['issues']))
        self.assertEqual(engine.package_counts(result)['revit_links_copied'],1)
        link=result['files'][1]
        self.assertEqual(link['inventory_status'],'NOT_INSPECTED_LINK_FILE')
        self.assertEqual(link['inspection_status'],'DIRECT_COPY_ONLY')

    def test_missing_cache_is_reported_with_element_and_identity(self):
        result=self.run_export({'Host.rvt':[cloud_row()]},repath=True)
        failure=next(i for i in result['issues'] if i['code']=='CACHE_MODEL_NOT_FOUND')
        self.assertEqual(failure['element_id'],'4815243')
        self.assertEqual(failure['cloud_identity']['model_guid'],W)
        self.assertEqual(failure['cache_evidence']['attempts'],[])
        self.assertEqual(engine.package_counts(result)['revit_links_copied'],0)
        host=self.assert_retained_original(result)
        self.assertEqual(host['processing_status'],'HOST_PRESERVED_LINKS_UNAVAILABLE')
        with open(os.path.join(result['root'],'REPORT.txt')) as inp:report=inp.read()
        self.assertIn('Element 4815243',report)
        self.assertIn('Cache roots:',report)

    def test_obsolete_load_option_is_ignored_and_unloaded_state_preserved(self):
        self.put_link();result=self.run_export({'Host.rvt':[cloud_row()]},repath=True)
        self.assert_finalized_host(result)
        ref=result['references'][0]
        self.assertFalse(ref['loaded']);self.assertFalse(ref['original_loaded'])
        self.assertFalse(ref['package_loaded'])

    def test_load_option_off_preserves_unloaded_state(self):
        self.put_link();result=self.run_export({'Host.rvt':[cloud_row()]},load=False,repath=True)
        self.assert_finalized_host(result)
        self.assertFalse(result['references'][0]['package_loaded'])

    def test_repath_off_does_not_request_loading(self):
        self.put_link();result=self.run_export({'Host.rvt':[cloud_row()]},load=True,repath=False)
        self.assert_finalized_host(result)
        self.assertFalse(result['references'][0]['package_loaded'])

    def test_mismatched_link_uses_live_inventory_but_never_scans_nested_plugins(self):
        self.doc.IsLinked=True
        self.r.collect_plugins=True
        self.r.scanner.scan_plugins=lambda *a:self.fail('different loaded revision is not authoritative')
        key=self.r.add_live(self.doc)
        entry=self.r.get(key);entry['inventory']['references']=[dict(id='live',source='Wrong.pdf',kind='Image')]
        self.r._cache_store=c.Store(self.db,self.app,os.path.join(self.r.temp_root(),'cache'),roots=[self.cache])
        self.r._cache_store.read_info=lambda p:dict(version=dict(guid=W,saves=9),format='2024')
        self.r.snapshot(key)
        self.saved_scans({'Host.rvt':[dict(id='saved',source='Right.pdf',kind='Image')]})
        backend=s.SessionBackend(self.db,self.app,self.root,self.r)
        self.assertIsNone(backend.inventory_after_copy(key,f.defaults()))
        result=backend.scan(key,'unused',f.defaults())
        self.assertEqual([r['source'] for r in result['references']],['Wrong.pdf'])
        self.assertTrue(any(i['code']=='SAVED_CACHE_DIFFERS_FROM_LOADED' for i in result['issues']))

    def test_cancellation_during_processing_retains_single_original_outside_package(self):
        key=self.r.add_live(self.doc);out=os.path.join(self.root,'cancel')
        backend=s.SessionBackend(self.db,self.app,out,self.r)
        def finalize(application,stage,target,rows,options,**kwargs):
            self.assertTrue(options['independent_host'])
            with open(target,'wb') as out:out.write(b'partial processing')
            raise f.Cancelled()
        worker.run_separate_revit=finalize
        backend.verify_package=lambda *args:self.fail('cancelled host must not be verified')
        result=engine.transmit([key],out,backend,f.defaults())
        self.assertEqual(result['status'],'CANCELLED')
        self.assertEqual(self.assert_retained_original(result)['processing_status'],'ROLLED_BACK')

    def test_zip_contains_no_hoststate_and_only_one_host_rvt(self):
        result=self.run_export({});archive=os.path.join(self.root,'package.zip')
        self.assert_finalized_host(result)
        engine.zip_package(result['root'],archive)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual([n for n in z.namelist() if n.lower().endswith('.rvt')],['Host.rvt'])
            self.assertFalse(any('_HostState' in n for n in z.namelist()))
        with open(os.path.join(result['root'],'manifest.json')) as inp:data=json.load(inp)
        self.assertNotIn('saved_state_backup',data['files'][0])
        self.assertEqual(data['counts']['revit_links_requested'],0)

    def test_worker_failure_retains_original_without_restoring_unfinished_target(self):
        self.finalization_failure(False)

    def test_worker_cancellation_retains_original_without_restoring_unfinished_target(self):
        self.finalization_failure(True)

    def finalization_failure(self,cancelled):
        key=self.r.add_live(self.doc);out=os.path.join(self.root,'rollback-failed')
        backend=s.SessionBackend(self.db,self.app,out,self.r)
        def finalize(application,stage,target,rows,options,**kwargs):
            self.assertTrue(options['independent_host'])
            with open(target,'wb') as stream:stream.write(b'failed processing')
            if cancelled:raise f.Cancelled()
            raise RuntimeError('processor failed')
        worker.run_separate_revit=finalize
        backend.verify_package=lambda *a:self.fail('unfinished host cannot be verified')
        old=engine.shutil.copyfile
        restore_attempts=[]
        def denied(*args):
            restore_attempts.append(args)
            raise IOError('simulated target write failure')
        engine.shutil.copyfile=denied
        try:result=engine.transmit([key],out,backend,f.defaults())
        finally:engine.shutil.copyfile=old
        host=self.assert_retained_original(result);recovery=host['recovery_path']
        self.assertEqual(host['processing_status'],'ROLLED_BACK' if cancelled else 'FAILED')
        self.assertEqual(result['status'],'CANCELLED' if cancelled else 'FAILED')
        self.assertEqual(restore_attempts,[],'unfinished target must be removed rather than restored into the package')
        self.assertFalse(any(i['code']=='HOST_ROLLBACK_FAILED' for i in result['issues']))
        with open(os.path.join(out,'REPORT.txt')) as inp:report=inp.read()
        self.assertIn(recovery,report)


for name in fixtures.SavedCacheSession.__dict__:
    if name.startswith('test_') and name not in SavedCloudLinks.__dict__:setattr(SavedCloudLinks,name,None)

if __name__=='__main__':unittest.main(verbosity=2)
