# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import copy, json, os, shutil, unittest, zipfile
import test_212_session as fixtures
from test_212_cache import P, M, V, W, Obj
from test_payload_acquisition import compound
from easybim_etransmit import cache_sources as c, session as s, files as f, engine

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
        self.saved_scans(rows)
        out=os.path.join(self.root,'out')
        backend=s.SessionBackend(self.db,self.app,out,self.r)
        options=f.defaults();options.update(repath=repath,load_unloaded_files=load,skip_cloud_links=True)
        backend.finish=lambda *args:[]
        backend.verify_package=lambda *args:[]
        return engine.transmit([key],out,backend,options)

    def test_anthropology_unloaded_identity_only_link_is_copied(self):
        original=self.put_link();stamp=os.stat(original).st_mtime;digest=f.digest(original)
        result=self.run_export({'Host.rvt':[cloud_row()]})
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
        self.assertEqual(result['files'][0]['processing_status'],'HOST_PRESERVED_LINKS_UNAVAILABLE')
        self.assertEqual(f.digest(result['files'][0]['target']),f.digest(self.original))
        with open(os.path.join(result['root'],'REPORT.txt')) as inp:report=inp.read()
        self.assertIn('Element 4815243',report)
        self.assertIn('Cache roots:',report)

    def test_load_option_records_original_and_requested_states(self):
        self.put_link();result=self.run_export({'Host.rvt':[cloud_row()]},repath=True)
        ref=result['references'][0]
        self.assertFalse(ref['loaded']);self.assertFalse(ref['original_loaded'])
        self.assertTrue(ref['package_loaded'])

    def test_load_option_off_preserves_unloaded_state(self):
        self.put_link();result=self.run_export({'Host.rvt':[cloud_row()]},load=False,repath=True)
        self.assertFalse(result['references'][0]['package_loaded'])

    def test_repath_off_does_not_request_loading(self):
        self.put_link();result=self.run_export({'Host.rvt':[cloud_row()]},load=True,repath=False)
        self.assertFalse(result['references'][0]['package_loaded'])

    def test_mismatched_link_uses_saved_dependency_inventory_and_no_live_plugins(self):
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
        self.assertEqual([r['source'] for r in result['references']],['Right.pdf'])
        self.assertTrue(any(i['code']=='SAVED_CACHE_DIFFERS_FROM_LOADED' for i in result['issues']))

    def test_cancellation_during_processing_restores_single_host(self):
        key=self.r.add_live(self.doc);out=os.path.join(self.root,'cancel')
        backend=s.SessionBackend(self.db,self.app,out,self.r)
        def finish(stage,target,*args):
            with open(target,'wb') as out:out.write(b'partial processing')
            raise f.Cancelled()
        backend.finish=finish
        result=engine.transmit([key],out,backend,f.defaults())
        self.assertEqual(result['status'],'CANCELLED')
        self.assertEqual(f.digest(result['files'][0]['target']),f.digest(self.original))
        self.assertEqual(result['files'][0]['processing_status'],'ROLLED_BACK')
        self.assertFalse(os.path.exists(os.path.join(out,'_HostState')))

    def test_zip_contains_no_hoststate_and_only_one_host_rvt(self):
        result=self.run_export({});archive=os.path.join(self.root,'package.zip')
        engine.zip_package(result['root'],archive)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual([n for n in z.namelist() if n.lower().endswith('.rvt')],['Host.rvt'])
            self.assertFalse(any('_HostState' in n for n in z.namelist()))
        with open(os.path.join(result['root'],'manifest.json')) as inp:data=json.load(inp)
        self.assertNotIn('saved_state_backup',data['files'][0])
        self.assertEqual(data['counts']['revit_links_requested'],0)

    def test_failed_rollback_retains_original_outside_deliverable(self):
        self.failed_rollback(False)

    def test_failed_cancellation_rollback_retains_original_outside_deliverable(self):
        self.failed_rollback(True)

    def failed_rollback(self,cancelled):
        key=self.r.add_live(self.doc);out=os.path.join(self.root,'rollback-failed')
        backend=s.SessionBackend(self.db,self.app,out,self.r)
        def finish(stage,target,*args):
            with open(target,'wb') as stream:stream.write(b'failed processing')
            if cancelled:raise f.Cancelled()
            raise RuntimeError('processor failed')
        backend.finish=finish
        backend.verify_package=lambda *a:self.fail('failed rollback cannot be verified')
        old=engine.shutil.copyfile
        def denied(*args):raise IOError('simulated target write failure')
        engine.shutil.copyfile=denied
        try:result=engine.transmit([key],out,backend,f.defaults())
        finally:engine.shutil.copyfile=old
        host=result['files'][0];recovery=host['recovery_path']
        self.addCleanup(shutil.rmtree,os.path.dirname(recovery))
        self.assertEqual(host['processing_status'],'ROLLBACK_FAILED')
        self.assertFalse(f.within(recovery,out))
        self.assertEqual(f.digest(recovery),f.digest(self.original))
        self.assertTrue(any(i['code']=='HOST_ROLLBACK_FAILED' for i in result['issues']))
        with open(os.path.join(out,'REPORT.txt')) as inp:report=inp.read()
        self.assertIn(recovery,report)


for name in fixtures.SavedCacheSession.__dict__:
    if name.startswith('test_') and name not in SavedCloudLinks.__dict__:setattr(SavedCloudLinks,name,None)

if __name__=='__main__':unittest.main(verbosity=2)
