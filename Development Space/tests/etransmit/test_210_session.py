# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os,sys,tempfile,shutil,unittest,copy
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f,engine
from easybim_etransmit.revit import Backend

class Obj(object):
    def __init__(self,**kw):self.__dict__.update(kw)

def mod(case):
    try:from easybim_etransmit import session
    except ImportError:case.fail('Live session registry/backend is missing')
    return session

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_Session_');self.addCleanup(shutil.rmtree,self.root)
    def doc(self,title='Open',path='',modified=False,linked=False):
        return Obj(Title=title,PathName=path,IsModified=modified,IsLinked=linked,IsDetached=not path,
                   IsWorkshared=False,IsModelInCloud=False,IsFamilyDocument=False,IsReadOnly=False,
                   IsModifiable=False,GetDocumentVersion=lambda document:Obj(VersionGUID='guid',NumberOfSaves=4))
    def registry(self):
        m=mod(self);return m.Registry(Obj(),Obj(VersionNumber='2024'),os.path.join(self.root,'working'),saved_state_only=False)
    def test_capture_keeps_document_not_just_path_and_never_opens_or_saves(self):
        r=self.registry();doc=self.doc();doc.SaveAs=lambda *a:self.fail('discovery must not save')
        r.scanner.scan_open=lambda d,s,i,result:result['references'].append(dict(id='1',element_id='1',kind='PDF',source='/drawing.pdf',special='image'))
        r.scanner.elements=lambda *a:[]
        key=r.add_live(doc);entry=r.get(key)
        self.assertIs(entry['document'],doc)
        self.assertEqual(entry['inventory']['references'][0]['source'],'/drawing.pdf')
        self.assertTrue(key.startswith('open://'))
    def test_loaded_child_traversed_once_no_link_document_save(self):
        r=self.registry();host=self.doc();link=self.doc(title='Arch',linked=True)
        count=[]
        def scan(doc,source,info,result):
            count.append(doc)
            if doc is host: result['references'].append(dict(id='10',element_id='10',kind='RevitLink',source='/Arch.rvt',special='external'))
        instance=Obj(GetLinkDocument=lambda:link,GetTypeId=lambda:Obj(Value=10))
        r.scanner.scan_open=scan;r.scanner.elements=lambda d,n:[instance,instance] if d is host and n=='RevitLinkInstance' else []
        key=r.add_live(host)
        self.assertEqual(len(count),2)
        row=r.get(key)['inventory']['references'][0]
        self.assertEqual(row['source'],'/Arch.rvt')
        self.assertEqual(row['source_evidence'],'LIVE_LINK_SAVED_FILE_PATH')
        self.assertIs(r.get(row['loaded_document_key'])['document'],link)
    def test_refused_snapshot_still_collects_live_materials(self):
        m=mod(self);r=self.registry();doc=self.doc(modified=True)
        pdf=os.path.join(self.root,'Drawing.pdf')
        with open(pdf,'wb') as out:out.write(b'%PDF-fixture')
        r.scanner.elements=lambda *a:[]
        r.scanner.scan_open=lambda d,s,i,result:result['references'].append(dict(id='1',element_id='1',kind='PDF',source=pdf,special='image'))
        key=r.add_live(doc);r.confirm_snapshot=lambda *a:False
        out=os.path.join(self.root,'out');b=m.SessionBackend(Obj(),Obj(),out,r)
        opts=f.defaults();opts['repath']=False
        result=engine.transmit([key],out,b,opts)
        self.assertEqual(engine.package_counts(result)['hosts_copied'],0)
        self.assertEqual(engine.package_counts(result)['files_copied'],1)
        self.assertTrue(any(i['code']=='HOST_SNAPSHOT_DECLINED' for i in result['issues']))
        self.assertEqual(result['references'][0]['status'],'COPIED')
    def test_snapshot_save_requires_explicit_consent_and_keeps_recovery_file(self):
        r=self.registry();doc=self.doc(modified=True);calls=[]
        def save(path,options):
            calls.append(path)
            from test_payload_acquisition import compound
            with open(path,'wb') as out:out.write(compound())
            doc.PathName=path;doc.IsModified=False
        doc.SaveAs=save;r.DB.SaveAsOptions=lambda:Obj(Dispose=lambda:None)
        r.DB.BasicFileInfo=Obj(Extract=lambda path:Obj(Format='2024',Dispose=lambda:None))
        r.scanner.elements=lambda *a:[];r.scanner.scan_open=lambda *a:None
        key=r.add_live(doc);r.confirm_snapshot=lambda d,p:True
        path=r.snapshot(key)
        self.assertEqual(calls,[path]);self.assertTrue(os.path.isfile(path))
        self.assertEqual(r.get(key)['state_basis'],'CURRENT_DOCUMENT_SAVEAS')
        self.assertEqual(r.snapshot(key),path);self.assertEqual(len(calls),1)
    def test_never_save_linked_document(self):
        r=self.registry();doc=self.doc(linked=True);doc.SaveAs=lambda *a:self.fail('must not save linked doc')
        r.scanner.elements=lambda *a:[];r.scanner.scan_open=lambda *a:None
        key=r.add_live(doc);r.confirm_snapshot=lambda *a:True
        with self.assertRaises(ValueError):r.snapshot(key)
    def test_published_graph_missing_member_does_not_guess_sibling(self):
        m=mod(self);from easybim_etransmit.cloud_sources import Graph
        v='urn:adsk.wipprod:fs.file:vf.H?version=1'
        g=Graph('b.p',v,dict(hostFile=dict(modelName='H.rvt',itemId='h',versionId=v,size=1,signedUrl='https://s3.amazonaws.com/h'),linkedFiles=dict(results=[])))
        r=self.registry();key=r.add_graph(g,None)
        b=m.SessionBackend(Obj(),Obj(),os.path.join(self.root,'out'),r);b.set_staging_root(os.path.join(self.root,'stage'))
        missing=os.path.join(b.staging_root,'NotReturned.rvt')
        with self.assertRaises(m.SourceError):b.resolve_acquired_source(missing,key)


class SnapshotSafety(unittest.TestCase):
    def test_unmodified_live_document_does_not_use_changed_saved_file(self):
        from easybim_etransmit import session
        root=tempfile.mkdtemp();self.addCleanup(shutil.rmtree,root)
        path=os.path.join(root,'H.rvt')
        with open(path,'wb') as out:out.write(b'changed by another process')
        doc=Obj(Title='H',PathName=path,IsLinked=False,IsModified=False,IsDetached=False,
                IsModelInCloud=False,IsWorkshared=False,IsReadOnly=False,IsModifiable=False,
                GetDocumentVersion=lambda document:Obj(VersionGUID='loaded',NumberOfSaves=1))
        db=Obj(BasicFileInfo=Obj(Extract=lambda p:Obj(GetDocumentVersion=lambda:Obj(VersionGUID='changed',NumberOfSaves=2))))
        r=session.Registry(db,Obj(),os.path.join(root,'r'),collect_plugins=False,saved_state_only=False)
        r.scanner.scan_open=lambda *a:None;r.scanner.elements=lambda *a:[]
        key=r.add_live(doc);r.confirm_snapshot=lambda *a:False
        with self.assertRaises(session.SourceError):r.snapshot(key)

class RecoveryReporting(unittest.TestCase):
    def test_failed_snapshot_keeps_and_reports_new_working_file(self):
        from easybim_etransmit import session
        root=tempfile.mkdtemp();self.addCleanup(shutil.rmtree,root)
        doc=Obj(Title='Host',PathName='',IsLinked=False,IsModified=True,IsDetached=True,
                IsWorkshared=False,IsReadOnly=False,IsModifiable=False,IsModelInCloud=False)
        def save(path,options):
            with open(path,'wb') as out:out.write(b'saved current state')
            doc.PathName=path
            raise RuntimeError('post-save hook failed')
        doc.SaveAs=save
        db=Obj(SaveAsOptions=lambda:Obj(Dispose=lambda:None))
        r=session.Registry(db,Obj(),os.path.join(root,'recovery'),collect_plugins=False,saved_state_only=False)
        r.scanner.scan_open=lambda *a:None;r.scanner.elements=lambda *a:[]
        key=r.add_live(doc);r.confirm_snapshot=lambda *a:True
        out=os.path.join(root,'package');opts=f.defaults();opts['repath']=False
        result=engine.transmit([key],out,session.SessionBackend(db,Obj(),out,r),opts)
        self.assertTrue(os.path.isfile(doc.PathName))
        self.assertIn('source_context',result['files'][0])
        self.assertEqual(result['files'][0]['source_context']['working_document_path_after'],doc.PathName)
        with open(os.path.join(out,'START_HERE.txt'),'r') as inp:report=inp.read()
        self.assertIn('working document',report.lower());self.assertIn(doc.PathName,report)

class AcquisitionIntegration(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_Graph_');self.addCleanup(shutil.rmtree,self.root)
    def graph(self):
        from easybim_etransmit.cloud_sources import Graph
        from test_payload_acquisition import compound
        self.payload=compound();self.version='urn:adsk.wipprod:fs.file:vf.H?version=2'
        return Graph('b.p',self.version,dict(hostFile=dict(modelName='H.rvt',itemId='h',versionId=self.version,size=len(self.payload),signedUrl='https://s3.amazonaws.com/h'),linkedFiles=dict(results=[dict(modelName='Arch.rvt',itemId='a',size=len(self.payload),signedUrl='https://s3.amazonaws.com/a')])) )
    def test_host_only_native_graph_collects_architecture_and_preserves_bytes(self):
        from easybim_etransmit import session
        graph=self.graph();downloads=[]
        def download(url,path,size,pulse=None):
            downloads.append(url)
            with open(path,'wb') as out:out.write(self.payload)
        registry=session.Registry(Obj(),Obj(),os.path.join(self.root,'recovery'),collect_plugins=False,saved_state_only=False)
        self.addCleanup(registry.close);key=registry.add_graph(graph,Obj(download=download))
        class B(session.SessionBackend):
            def basic(self,path):return dict(version='2024',workshared=False,central='')
            def rows(self,stage,owner=''):
                return [dict(id='7',element_id='7',kind='RevitLink',source=os.path.join(self.staging_root,'Arch.rvt'),loaded=True)] if owner==key else []
        opts=f.defaults();opts.update(deep=False,repath=False)
        out=os.path.join(self.root,'out');result=engine.transmit([key],out,B(Obj(),Obj(VersionNumber='2024'),out,registry),opts)
        self.assertEqual(engine.package_counts(result)['files_copied'],2,repr(result['issues']))
        self.assertEqual(len(downloads),2)
        self.assertTrue(all(x['source_context']['mode']=='PUBLISHED_VERSION' for x in result['files']))
        for row in result['files']:
            with open(row['target'],'rb') as inp:self.assertEqual(inp.read(),self.payload)
    def test_binding_other_cloud_host_is_rejected_before_any_download(self):
        from easybim_etransmit import session
        r=session.Registry(Obj(),Obj(),os.path.join(self.root,'r'),collect_plugins=False,saved_state_only=False)
        self.addCleanup(r.close)
        r.entries['live']=dict(cloud=dict(model_guid='actual-host'),children=[])
        graph=self.graph()
        client=Obj(version=lambda *a:dict(attributes=dict(extension=dict(data=dict(modelGuid='other-host')))))
        with self.assertRaises(session.SourceError):r.bind_graph('live',graph,client)
    def test_all_authoritative_members_are_collected_even_if_metadata_inventory_is_incomplete(self):
        from easybim_etransmit import session
        graph=self.graph();r=session.Registry(Obj(),Obj(),os.path.join(self.root,'r'),collect_plugins=False,saved_state_only=False);self.addCleanup(r.close)
        key=r.add_graph(graph,None)
        b=session.SessionBackend(Obj(),Obj(),self.root,r)
        # The response itself supplies file membership, not fictitious Revit element IDs.
        extra=getattr(b,'additional_sources',None)
        self.assertTrue(callable(extra),'authoritative extra source inventory is missing')
        self.assertEqual(extra(key),[graph.entries[1]['source']])

if __name__=='__main__':unittest.main(verbosity=2)
