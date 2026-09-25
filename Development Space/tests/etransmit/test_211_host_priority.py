# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, tempfile, shutil, json, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f, engine, session, batch
from test_payload_acquisition import compound

class Obj(object):
    def __init__(self, **kw): self.__dict__.update(kw)

class Names(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_names_');self.addCleanup(shutil.rmtree,self.root)
    def planner(self):
        try: from easybim_etransmit import jobnames
        except ImportError: self.fail('Shared model-named job planner is missing')
        return jobnames.plan
    def test_folders_follow_model_stems_and_virtual_display_names(self):
        p=self.planner(); models=[r'C:\Project\MEP Building.rvt','open://abc/Current State.rvt']
        paths=p(models,self.root,True)
        self.assertEqual([os.path.basename(x) for x in paths],['MEP Building','Current State'])
    def test_duplicate_stems_preserve_existing_and_original_suffixed_names(self):
        p=self.planner();os.mkdir(os.path.join(self.root,'Host'))
        models=['/a/Host.rvt','/b/host.rvt','/c/Host (2).rvt']
        paths=p(models,self.root,True)
        self.assertEqual(len(set(x.lower() for x in paths)),3)
        self.assertNotEqual(paths[0].lower(),os.path.join(self.root,'Host').lower())
        self.assertEqual(os.path.basename(paths[2]),'Host (2)')
    def test_zip_each_uses_the_job_model_name(self):
        path=os.path.join(self.root,'MEP.rvt')
        with open(path,'wb') as out:out.write(b'host')
        class B(object):
            def scan(self,*a):return dict(references=[],issues=[])
            def finish(self,stage,target,*a):shutil.copyfile(stage,target);return []
        opts=f.defaults();opts.update(zip_per_model=True)
        root=os.path.join(self.root,'out')
        batch.run_batch([path],root,lambda *a:B(),opts)
        with open(os.path.join(root,'batch.json')) as inp:job=json.load(inp)['jobs'][0]
        self.assertEqual(os.path.basename(job['root']),'MEP')
        self.assertEqual(os.path.basename(job['zip_path']),'MEP.zip')
    def test_new_folder_does_not_collide_with_another_jobs_zip(self):
        p=self.planner()
        for models in [['Host.rvt','Host.zip.rvt'],['Host.zip.rvt','Host.rvt']]:
            paths=p(models,self.root,True)
            entries=[v.lower() for path in paths for v in (path,path+'.zip')]
            self.assertEqual(len(set(entries)),4,repr(paths))

    def test_invalid_or_oversize_name_fails_before_creating_outputs(self):
        p=self.planner()
        for path in ['open://x/CON.rvt','open://x/bad:name.rvt','open://x/'+('x'*256)+'.rvt']:
            with self.assertRaises(ValueError):p([path],self.root,True)
        self.assertEqual(os.listdir(self.root),[])

class HostSafety(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_host_');self.addCleanup(shutil.rmtree,self.root)
        self.events=[];self.payload=compound()
        self.doc=Obj(Title='Current Host',PathName='',IsLinked=False,IsFamilyDocument=False,
                     IsModified=True,IsDetached=True,IsModelInCloud=False,IsWorkshared=False,
                     IsReadOnly=False,IsModifiable=False)
        self.doc.GetDocumentVersion=lambda document:Obj(VersionGUID='new',NumberOfSaves=2)
        def save(path,opts):
            self.events.append('save')
            with open(path,'wb') as out:out.write(self.payload)
            self.doc.PathName=path;self.doc.IsModified=False
        self.doc.SaveAs=save
        self.db=Obj(SaveAsOptions=lambda:Obj(Dispose=lambda:None),
            BasicFileInfo=Obj(Extract=lambda p:Obj(GetDocumentVersion=lambda:Obj(VersionGUID='new',NumberOfSaves=2))))
        self.r=session.Registry(self.db,Obj(VersionNumber='2024'),os.path.join(self.root,'recovery'),collect_plugins=False)
        self.r.scanner.scan_open=lambda *a:self.events.append('scan')
        self.r.scanner.elements=lambda *a:[]
    def register(self):
        self.assertTrue(hasattr(self.r,'register_live'),'Lightweight host registration is missing')
        return self.r.register_live(self.doc)
    def authorize(self,key):
        self.r.authorize_snapshot(key)
    def test_host_saved_before_inventory_and_retained_after_dependency_failure(self):
        key=self.register();self.authorize(key)
        def scan(doc,source,info,result):
            self.events.append('scan');result['references'].append(dict(id='p',element_id='p',source=os.path.join(self.root,'missing.pdf'),kind='PDF',special='image'))
        self.r.scanner.scan_open=scan
        opts=f.defaults();opts.update(preserve_live_host=True)
        class B(session.SessionBackend):
            def finish(self,*a):raise AssertionError('protected host must never be repathed')
            def verify_package(self,*a):raise AssertionError('must not open original cloud-linked snapshot to verify links')
        out=os.path.join(self.root,'out');b=B(self.db,Obj(),out,self.r)
        result=engine.transmit([key],out,b,opts)
        self.assertEqual(self.events[:2],['save','scan'])
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1,repr(result['issues']))
        host=result['files'][0]
        self.assertEqual(host['host_snapshot_status'],'VERIFIED')
        self.assertEqual(f.digest(host['target']),f.digest(host['host_snapshot_path']))
        self.assertEqual(host['model_verification'],'SNAPSHOT_ONLY_NOT_REOPENED')
        with open(host['target'],'rb') as inp:self.assertEqual(inp.read(),self.payload)
        with open(os.path.join(out,'START_HERE.txt'),'r') as inp: self.assertIn('HostSnapshot',inp.read())
    def test_current_save_not_published_or_reused_disk_when_authorized(self):
        old=os.path.join(self.root,'old.rvt')
        with open(old,'wb') as out:out.write(b'OLD SAVED STATE')
        self.doc.PathName=old;self.doc.IsModified=False;self.doc.IsDetached=False
        key=self.register();self.authorize(key)
        path=self.r.snapshot(key)
        self.assertNotEqual(path,old)
        with open(old,'rb') as inp:self.assertEqual(inp.read(),b'OLD SAVED STATE')
        self.assertEqual(self.events,['save'])
    def test_failed_native_save_preserves_original_and_actual_error(self):
        key=self.register();self.authorize(key)
        def fail(path,opts):raise RuntimeError('disk full diagnostic')
        self.doc.SaveAs=fail
        with self.assertRaises(session.SourceError) as caught:self.r.snapshot(key)
        self.assertIn('disk full diagnostic',str(caught.exception))
        self.assertEqual(self.doc.PathName,'')
    def test_post_save_event_failure_recovered_only_with_revision_proof(self):
        key=self.register();self.authorize(key)
        base=self.doc.SaveAs
        def save(path,opts):base(path,opts);raise RuntimeError('external post-save event')
        self.doc.SaveAs=save
        path=self.r.snapshot(key)
        self.assertTrue(os.path.isfile(path));self.assertEqual(self.r.get(key)['snapshot_verification'],'REVISION_CHECKED')
        self.assertEqual(self.r.get(key)['snapshot_warnings'][0]['code'],'HOST_SAVE_EVENT_RECOVERED')
    def test_save_success_without_current_file_is_not_verified(self):
        key=self.register();self.authorize(key)
        self.doc.SaveAs=lambda *a:None
        with self.assertRaises(session.SourceError):self.r.snapshot(key)
    def test_save_success_but_metadata_wrong_fails_closed(self):
        key=self.register();self.authorize(key)
        self.db.BasicFileInfo.Extract=lambda p:Obj(GetDocumentVersion=lambda:Obj(VersionGUID='other',NumberOfSaves=99))
        with self.assertRaises(session.SourceError):self.r.snapshot(key)
    def test_cancelled_snapshot_consent_stays_closed_without_old_fallback(self):
        key=self.register()
        with self.assertRaises(session.SourceError):self.r.snapshot(key)
        self.assertEqual(self.events,[])
    def test_cancel_before_save_never_changes_working_document(self):
        key=self.register();self.authorize(key);self.r.cancelled=lambda:True
        with self.assertRaises(f.Cancelled):self.r.snapshot(key)
        self.assertEqual(self.events,[])

    def test_current_host_title_is_used_even_if_original_name_differs(self):
        self.doc.PathName=os.path.join(self.root,'Old Host.rvt')
        key=self.register()
        self.assertEqual(self.r.get(key)['name'],'Current Host.rvt')

    def test_scan_uses_original_central_base_after_saveas_changes_it(self):
        original=os.path.join(self.root,'project','Central.rvt')
        self.doc.IsWorkshared=True;self.doc.GetWorksharingCentralModelPath=lambda:original
        self.db.WorksharingSaveAsOptions=lambda:Obj(Dispose=lambda:None)
        self.db.SaveAsOptions=lambda:Obj(Dispose=lambda:None,SetWorksharingOptions=lambda o:None)
        self.r.scanner.visible=lambda p:p
        key=self.register();self.authorize(key)
        base=self.doc.SaveAs
        def save(path,opts):
            base(path,opts);self.doc.GetWorksharingCentralModelPath=lambda:path
        self.doc.SaveAs=save
        seen=[];self.r.scanner.scan_open=lambda d,s,i,r:seen.append(i['central'])
        self.r.snapshot(key);self.r.inventory(key)
        self.assertEqual(seen,[original])

    def test_plugin_scan_failure_cannot_turn_native_inventory_into_failed_host(self):
        key=self.register();self.r.collect_plugins=True
        def fail(*a):raise RuntimeError('plugin reader failed')
        self.r.scanner.scan_plugins=fail
        result=self.r.inventory(key)
        self.assertFalse(result.get('open_failed'),repr(result))
        self.assertTrue(any(x['code']=='PLUGIN_SOURCE_COVERAGE' for x in result['issues']))

    def test_protected_host_restores_accidentally_changed_delivery_copy(self):
        key=self.register();self.authorize(key)
        out=os.path.join(self.root,'out')
        target=os.path.join(out,*self.r.relative(key).split('/'))
        def scan(*a):
            with open(target,'wb') as dest:dest.write(b'UNEXPECTED CHANGE')
        self.r.scanner.scan_open=scan
        opts=f.defaults();opts['preserve_live_host']=True
        result=engine.transmit([key],out,session.SessionBackend(self.db,Obj(),out,self.r),opts)
        rec=result['files'][0]
        self.assertEqual(f.digest(rec['target']),rec['host_snapshot_sha256'])
        self.assertTrue(any(x['code']=='HOST_DELIVERY_COPY_RESTORED' for x in result['issues']))

    def test_backup_copy_failure_keeps_valid_host_count_and_recovery_file(self):
        key=self.register();self.authorize(key);out=os.path.join(self.root,'out')
        old=f.copy_file
        def fail_backup(source,target,*args,**kw):
            if 'HostSnapshot' in target:raise IOError('backup disk space')
            return old(source,target,*args,**kw)
        f.copy_file=fail_backup
        try:
            opts=f.defaults();opts['preserve_live_host']=True
            result=engine.transmit([key],out,session.SessionBackend(self.db,Obj(),out,self.r),opts)
        finally:f.copy_file=old
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1,repr(result['issues']))
        self.assertTrue(f.file_exists(self.r.get(key)['snapshot_path']))
        self.assertTrue(any(x['code']=='HOST_SNAPSHOT_BACKUP_FAILED' for x in result['issues']))

    def test_no_serialize_or_close_linked_document(self):
        self.doc.IsLinked=True;key=self.register()
        with self.assertRaises(session.SourceError):self.r.authorize_snapshot(key)
        with self.assertRaises(session.SourceError):self.r.snapshot(key)
        self.assertEqual(self.events,[])

class MessagePriority(unittest.TestCase):
    def test_host_failures_precede_optional_plugin_warnings(self):
        r=dict(models=[],requested_models=['H.rvt'],files=[],status='FAILED',issues=[
            engine.issue('PLUGIN_SOURCE_COVERAGE','H','plugin notice'),
            engine.issue('HOST_SNAPSHOT_FAILED','H','save failed','error')])
        msg=engine.completion_message([r],1)
        self.assertLess(msg.find('HOST_SNAPSHOT_FAILED'),msg.find('PLUGIN_SOURCE_COVERAGE'))


class UIHostContract(unittest.TestCase):
    def test_snapshot_authorized_before_batch_not_inside_progress(self):
        with open(os.path.join(ROOT,'lib','easybim_etransmit','ui.py')) as inp:ui=inp.read()
        self.assertIn('registry.register_live(row.Document)',ui)
        self.assertIn('registry.authorize_snapshot(key)',ui)
        self.assertNotIn('registry.confirm_snapshot=confirm',ui)
        self.assertIn('snapshots_authorized',ui)
    def test_safe_defaults_are_explicit_in_dialog(self):
        with open(os.path.join(ROOT,'EasyBIM.tab','Links.panel','e-transmit.pushbutton','window.xaml')) as inp:xaml=inp.read()
        self.assertIn('Name="PreserveHost"',xaml)
        self.assertIn('Name="SkipCloudLinks"',xaml)
        self.assertIn('current state',xaml)

class RealUIConsent(unittest.TestCase):
    def setUp(self):
        import types,importlib
        self.root=tempfile.mkdtemp(prefix='ET_consent_');self.addCleanup(shutil.rmtree,self.root)
        self.old_pyrevit=sys.modules.get('pyrevit');self.old_ui=sys.modules.pop('easybim_etransmit.ui',None)
        self.prompts=[];self.allow=False
        fake=types.ModuleType('pyrevit')
        fake.forms=Obj(WPFWindow=object,ProgressBar=object,alert=self.alert)
        fake.DB=Obj();fake.script=Obj()
        sys.modules['pyrevit']=fake
        self.ui=importlib.import_module('easybim_etransmit.ui')
        self.addCleanup(self.unload)
        d=object.__new__(self.ui.Dialog);self.dialog=d
        d.models=[self.ui.Choice('Current Host',document=Obj(),mode='LIVE_DOCUMENT')]
        d.categories=[self.ui.Choice(label,key) for key,label in f.CATEGORIES]
        d.Models=Obj(CommitEdit=lambda:None);d.Output=Obj(Text=self.root)
        for name in ('DeepScan','Repath','PreserveHost','SkipCloudLinks','Separate','Reports'):
            setattr(d,name,Obj(IsChecked=True))
        for name in ('Cleanup','Upgrade','DiscardWorksets','Purge','Zip','ZipPerModel','SaveSettings'):
            setattr(d,name,Obj(IsChecked=False))
        d.ViewMode=Obj(SelectedItem=Obj(Key='all'));d.view_types=[];d.mappings=[];d.extras=[]
        d.result=None;d.snapshots_authorized=False;self.closed=False
        d.Close=self.close
    def alert(self,message,**kw):
        self.prompts.append(message);return self.allow
    def close(self):self.closed=True
    def unload(self):
        sys.modules.pop('easybim_etransmit.ui',None)
        if self.old_ui is not None:sys.modules['easybim_etransmit.ui']=self.old_ui
        if self.old_pyrevit is None:sys.modules.pop('pyrevit',None)
        else:sys.modules['pyrevit']=self.old_pyrevit
    def test_declining_initial_consent_creates_no_batch_or_partial_output(self):
        self.dialog.transmit_click(None,None)
        self.assertIsNone(self.dialog.result);self.assertFalse(self.closed)
        self.assertFalse(self.dialog.snapshots_authorized)
        self.assertEqual(os.listdir(self.root),[])
        self.assertIn('CURRENT OPEN',self.prompts[0])
    def test_model_named_output_path_is_validated_before_save_consent(self):
        self.dialog.models[0].Name='M'*100
        self.allow=True
        validate=f.validate_destination_path
        if os.name!='nt':
            f.validate_destination_path=lambda p:validate('C:'+p.replace('/',chr(92)))
        try:self.dialog.transmit_click(None,None)
        finally:f.validate_destination_path=validate
        self.assertIsNone(self.dialog.result)
        self.assertFalse(self.closed)
        self.assertEqual(os.listdir(self.root),[])
        self.assertIn('No model was saved',self.prompts[0])

    def test_accepting_current_state_consent_authorizes_batch_not_published_fallback(self):
        self.allow=True;self.dialog.transmit_click(None,None)
        self.assertTrue(self.dialog.snapshots_authorized);self.assertTrue(self.closed)
        self.assertEqual(self.dialog.result[0][0].Mode,'LIVE_DOCUMENT')
        self.assertTrue(self.dialog.result[2]['preserve_live_host'])
        self.assertTrue(self.dialog.result[2]['skip_cloud_links'])
        self.assertEqual(os.listdir(self.root),[])

class SkippedCloudLinks(unittest.TestCase):
    setUp=HostSafety.setUp
    register=HostSafety.register
    authorize=HostSafety.authorize
    def test_skipped_cloud_link_still_collects_materials_without_saving_link(self):
        child=Obj(Title='Architecture',PathName='',IsLinked=True,IsModified=False,IsDetached=False,
                  IsModelInCloud=True,IsWorkshared=True,IsReadOnly=True,IsModifiable=False)
        child.SaveAs=lambda *a:self.fail('never save linked Document')
        child.GetDocumentVersion=lambda d:Obj(VersionGUID='child',NumberOfSaves=2)
        drawing=os.path.join(self.root,'Arch.pdf')
        with open(drawing,'wb') as out:out.write(b'%PDF fixture')
        def scan(doc,source,info,result):
            if doc is child:result['references'].append(dict(id='1',source=drawing,kind='PDF'))
        self.r.scanner.scan_open=scan
        instance=Obj(GetLinkDocument=lambda:child,GetTypeId=lambda:Obj(Value=7))
        self.r.scanner.elements=lambda doc,kind:[instance] if doc is self.doc and kind=='RevitLinkInstance' else []
        key=self.register();self.authorize(key)
        opts=f.defaults();opts.update(preserve_live_host=True,skip_cloud_links=True,repath=False)
        out=os.path.join(self.root,'out')
        result=engine.transmit([key],out,session.SessionBackend(self.db,Obj(),out,self.r),opts)
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1)
        self.assertEqual(engine.package_counts(result)['files_copied'],2)
        self.assertTrue(any(i['code']=='CLOUD_LINK_COPY_SKIPPED' for i in result['issues']))
        self.assertFalse(any(i['severity']=='error' for i in result['issues']),repr(result['issues']))

if __name__=='__main__':unittest.main(verbosity=2)
