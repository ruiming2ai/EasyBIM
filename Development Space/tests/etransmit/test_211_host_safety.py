# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, unittest, tempfile, shutil
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import session as s, engine, files as f
from test_payload_acquisition import compound

class Obj(object):
    def __init__(self,**kw): self.__dict__.update(kw)

class HostSafety(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_Host_211_')
        self.addCleanup(shutil.rmtree,self.root)
        self.original=os.path.join(self.root,'Host.rvt')
        self.old=compound(suffix='-old'); self.current=compound(suffix='-live')
        with open(self.original,'wb') as out: out.write(self.old)
        self.calls=[]
        self.doc=Obj(Title='Host',PathName=self.original,IsModified=True,IsDetached=False,
                     IsModelInCloud=False,IsLinked=False,IsFamilyDocument=False,
                     IsWorkshared=True,IsReadOnly=False,IsModifiable=False)
        self.version=Obj(VersionGUID='post-save-guid',NumberOfSaves=4,Dispose=lambda:None)
        self.doc.GetDocumentVersion=lambda doc:self.version
        def save(path,options):
            self.assertFalse(options.OverwriteExistingFile)
            self.calls.append(path)
            with open(path,'wb') as out: out.write(self.current)
            self.doc.PathName=path;self.doc.IsModified=False
        self.doc.SaveAs=save
        self.doc.Save=lambda *a:self.fail('must not save original')
        self.doc.SynchronizeWithCentral=lambda *a:self.fail('must not sync')
        self.doc.SaveCloudModel=lambda *a:self.fail('must not publish/save cloud')
        self.doc.Close=lambda *a:self.fail('must not close original')
        self.db=Obj(SaveAsOptions=lambda:Obj(SetWorksharingOptions=lambda ws:None,Dispose=lambda:None),
                    WorksharingSaveAsOptions=lambda:Obj(Dispose=lambda:None),
                    BasicFileInfo=Obj(Extract=lambda path:Obj(Format='2024',IsWorkshared=True,
                       GetDocumentVersion=lambda:self.version,Dispose=lambda:None)))
        self.app=Obj(VersionNumber='2024')
        self.r=s.Registry(self.db,self.app,os.path.join(self.root,'working'),collect_plugins=False,saved_state_only=False)
        self.addCleanup(self.r.close)
        self.r.scanner.elements=lambda *a:[]
        self.r.scanner.scan_open=lambda *a:None
    def live(self):return self.r.add_live(self.doc)
    def authorize(self,key):
        fn=getattr(self.r,'authorize_snapshots',None)
        self.assertTrue(callable(fn),'explicit batch authorization is missing')
        fn([key])
    def test_batch_authorization_saves_current_bytes_without_reprompt(self):
        key=self.live();self.authorize(key)
        self.r.confirm_snapshot=lambda *a:self.fail('authorized host must not prompt again')
        path=self.r.snapshot(key)
        with open(path,'rb') as inp:self.assertEqual(inp.read(),self.current)
        with open(self.original,'rb') as inp:self.assertEqual(inp.read(),self.old)
        self.assertEqual(self.calls,[path])
        self.assertEqual(self.r.get(key)['snapshot_sha256'],f.digest(path))
    def test_unmodified_host_is_serialized_not_copied_from_disk(self):
        self.doc.IsModified=False
        key=self.live();self.r.confirm_snapshot=lambda *a:True
        self.r.snapshot(key)
        self.assertEqual(len(self.calls),1,'matching saved metadata must not substitute disk bytes for live SaveAs')
    def test_snapshot_cache_detects_changed_file(self):
        key=self.live();self.r.confirm_snapshot=lambda *a:True
        path=self.r.snapshot(key)
        with open(path,'wb') as out:out.write(compound(suffix='-tampered'))
        with self.assertRaises(s.SourceError):self.r.snapshot(key)
        self.assertEqual(len(self.calls),1)
    def test_invalid_saved_payload_is_not_accepted(self):
        self.current=b'<html>not a Revit document</html>'
        key=self.live();self.r.confirm_snapshot=lambda *a:True
        with self.assertRaises(s.SourceError):self.r.snapshot(key)
        self.assertTrue(os.path.exists(self.calls[0]),'never delete a working SaveAs file')
    def test_cancelled_revit_save_not_retried_or_used_as_valid_host(self):
        def fail(path,opts):self.calls.append(path);raise RuntimeError('Save cancelled by add-in')
        self.doc.SaveAs=fail;key=self.live();self.r.confirm_snapshot=lambda *a:True
        for _ in range(2):
            with self.assertRaises(s.SourceError):self.r.snapshot(key)
        self.assertEqual(len(self.calls),1,'failed host must not be re-saved implicitly')
    def test_live_host_target_is_short_and_model_named(self):
        key=self.live()
        self.assertEqual(self.r.relative(key),'Host.rvt')
    def test_unresolved_link_does_not_process_or_lose_current_host(self):
        key=self.live();self.authorize(key)
        self.r.get(key)['inventory']['references'].append(dict(id='1',element_id='1',
            source='Autodesk Docs://Project/Missing.rvt',kind='RevitLink',special='external',td=False,loaded=True))
        class B(s.SessionBackend):
            def finish(inner,*args):self.fail('do not reopen/repath a live host with unresolved RVTs')
            def verify_package(inner,*args):self.fail('do not load missing cloud links during host verification')
        out=os.path.join(self.root,'out')
        result=engine.transmit([key],out,B(self.db,self.app,out,self.r),f.defaults())
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1,repr(result['issues']))
        host=result['files'][0]
        self.assertEqual(host['processing_status'],'HOST_PRESERVED_LINKS_UNAVAILABLE')
        self.assertEqual(host['model_verification'],'DEFERRED')
        self.assertEqual(f.digest(host['target']),host['current_state_sha256'])
        self.assertFalse(os.path.exists(os.path.join(out,'_HostState')))
        self.assertEqual(host['current_state_integrity'],'VERIFIED')
    def test_cloud_link_skip_does_not_skip_selected_cloud_host(self):
        self.doc.IsModelInCloud=True
        self.doc.GetCloudModelPath=lambda:Obj(GetModelGUID=lambda:'host',GetProjectGUID=lambda:'project',Dispose=lambda:None)
        key=self.live();self.authorize(key)
        child=Obj(Title='Arch',PathName='Autodesk Docs://Project/Arch.rvt',IsLinked=True,
                  IsWorkshared=True,IsModelInCloud=True,IsModified=False,
                  GetCloudModelPath=lambda:Obj(GetModelGUID=lambda:'child',GetProjectGUID=lambda:'project',Dispose=lambda:None))
        childkey=self.r.add_live(child)
        self.r.get(key)['inventory']['references'].append(dict(id='2',element_id='2',source=childkey,
            kind='RevitLink',special='external',td=False,loaded=True))
        out=os.path.join(self.root,'cloudout');opts=f.defaults();opts['skip_cloud_links']=True
        b=s.SessionBackend(self.db,self.app,out,self.r)
        b.finish=lambda *a:self.fail('missing cloud link should not trigger host modifications')
        result=engine.transmit([key],out,b,opts)
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1,repr(result['issues']))
        self.assertEqual(result['references'][0]['status'],'SKIPPED_CLOUD_LINK')
        self.assertFalse(any(i['severity']=='error' for i in result['issues']),repr(result['issues']))
    def test_optional_plugin_inspection_happens_after_protected_host_copy(self):
        self.r.collect_plugins=True;seen=[]
        def plugins(doc,base,result):
            self.assertTrue(self.calls,'plugin scan ran before host snapshot')
            self.assertTrue(os.path.exists(os.path.join(out,'Host.rvt')))
            seen.append(True)
            raise RuntimeError('optional vendor schema failure')
        self.r.scanner.scan_plugins=plugins
        key=self.live();self.authorize(key)
        out=os.path.join(self.root,'pluginout');opts=f.defaults();opts['repath']=False
        b=s.SessionBackend(self.db,self.app,out,self.r)
        result=engine.transmit([key],out,b,opts)
        self.assertEqual(seen,[True],repr(result['issues']))
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1)
        self.assertFalse(any(i['severity']=='error' for i in result['issues']))
        self.assertTrue(any(i['code']=='PLUGIN_SOURCE_COVERAGE' for i in result['issues']))
    def test_optional_plugin_inventory_is_cached_but_included_in_each_package(self):
        self.r.collect_plugins=True;calls=[]
        workbook=os.path.join(self.root,'Workbook.xlsx')
        with open(workbook,'wb') as out:out.write(b'workbook fixture')
        def plugins(doc,base,result):
            calls.append(True)
            result['references'].append(dict(id='plugin:1',element_id='1',source=workbook,
                kind='PluginSpreadsheet',special='plugin_spreadsheet',category='spreadsheets',td=False))
        self.r.scanner.scan_plugins=plugins
        key=self.live();self.authorize(key);opts=f.defaults();opts['repath']=False
        for number in (1,2):
            out=os.path.join(self.root,'plugin-package-'+str(number))
            b=s.SessionBackend(self.db,self.app,out,self.r)
            result=engine.transmit([key],out,b,opts)
            self.assertEqual(engine.package_counts(result)['files_copied'],2,repr(result['issues']))
        self.assertEqual(calls,[True],'plugin inventory should be read once and reused safely')
    def test_new_unsaved_changes_after_snapshot_are_not_hidden_by_cache(self):
        key=self.live();self.authorize(key)
        self.r.snapshot(key)
        self.doc.IsModified=True
        with self.assertRaises(s.SourceError):self.r.snapshot(key)
        self.assertEqual(len(self.calls),1)
    def test_deferred_host_does_not_create_unused_pdf_aliases(self):
        pdf=os.path.join(self.root,'Drawing.pdf')
        with open(pdf,'wb') as out:out.write(b'%PDF-fixture')
        key=self.live();self.authorize(key)
        self.r.get(key)['inventory']['references'].extend([
            dict(id='1',element_id='1',source='Autodesk Docs://Project/Missing.rvt',kind='RevitLink',td=False),
            dict(id='2',element_id='2',source=pdf,kind='Image',special='image',td=False)])
        old=engine.portable_image_alias;attempts=[]
        def unnecessary(root,row):
            attempts.append(True)
            raise RuntimeError('must not create an operational alias for an unprocessed host')
        engine.portable_image_alias=unnecessary
        try:
            out=os.path.join(self.root,'deferred-alias');opts=f.defaults();opts['skip_cloud_links']=True
            result=engine.transmit([key],out,s.SessionBackend(self.db,self.app,out,self.r),opts)
        finally:engine.portable_image_alias=old
        self.assertEqual(attempts,[])
        self.assertFalse(any(i['code']=='PORTABLE_ALIAS_FAILED' for i in result['issues']))
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1)
    def test_authorization_rejects_linked_document(self):
        self.doc.IsLinked=True;key=self.live()
        fn=getattr(self.r,'authorize_snapshots',None)
        self.assertTrue(callable(fn))
        with self.assertRaises(s.SourceError):fn([key])
        self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main(verbosity=2)
