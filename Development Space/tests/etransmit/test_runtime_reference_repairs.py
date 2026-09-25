# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import os, sys, shutil, tempfile, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f, engine as e
from easybim_etransmit.revit import Backend

class Obj(object):
    def __init__(self,**kw):self.__dict__.update(kw)

class References(unittest.TestCase):
    def setUp(self):
        self.db=Obj(ModelPathUtils=Obj(ConvertModelPathToUserVisiblePath=lambda p:p,ConvertUserVisiblePathToModelPath=lambda p:p),
                    ExternalFileUtils=Obj(GetAllExternalFileReferences=lambda d:[]),
                    ExternalResourceTypes=Obj(BuiltInExternalResourceTypes=Obj(RevitLink='RVT',SystemsAnalysisReport='Report')),
                    ExternalResourceUtils=Obj(GetAllExternalResourceReferences=lambda d:[]),BuiltInCategory=Obj())
        self.b=Backend(self.db,Obj(VersionNumber='2024'),'C:\\Out')
        self.b.staging_root='C:\\Temp\\ET';self.b.elements=lambda d,k:[]
    def test_content_library_path_uses_saved_absolute_not_host_folder(self):
        path='C:\\ProgramData\\Autodesk\\RVT 2024\\Libraries\\English-Imperial\\US\\UniformatClassifications.txt'
        ref=Obj(GetPath=lambda:'UniformatClassifications.txt',GetAbsolutePath=lambda:path,
                PathType='Content',GetLinkedFileStatus=lambda:'Loaded',ExternalFileReferenceType='AssemblyCodeTable')
        td=Obj(IsTransmitted=False,GetAllExternalFileReferenceIds=lambda:[Obj(Value=1)],GetLastSavedReferenceData=lambda i:ref)
        self.db.TransmissionData=Obj(ReadTransmissionData=lambda p:td)
        row=self.b.rows('C:\\Temp\\ET\\stage.rvt','C:\\Downloads\\Host.rvt')[0]
        self.assertEqual(row['source'],path)
        self.assertTrue(row.get('optional_library'))
        self.assertEqual(row.get('path_type'),'Content')
    def test_seen_native_link_is_enriched_with_exact_external_source(self):
        ident=Obj(Value=20);actual='C:\\Users\\tester\\DC\\ACCDocs\\Account\\Project\\Project Files\\Consumed\\Arch.rvt'
        resource=Obj(InSessionPath='C:\\Temp\\ET\\Arch.rvt',ServerId='provider',Version='',
                     GetReferenceInformation=lambda:{'Path':actual,'PathType':'Absolute','ModelId':'id-only'})
        element=Obj(IsNestedLink=False,GetExternalResourceReferences=lambda:{'RVT':resource})
        self.db.ExternalResourceUtils.GetAllExternalResourceReferences=lambda d:[ident]
        result=dict(references=[dict(id='20',element_id='20',kind='RevitLink',td=False,special='native',
                                   source='C:\\Temp\\ET\\Arch.rvt',loaded=False)],issues=[])
        self.b.scan_open(Obj(GetElement=lambda i:element),'C:\\Downloads\\Host.rvt',dict(central='',workshared=False),result)
        self.assertEqual(len(result['references']),1)
        row=result['references'][0]
        self.assertEqual(row['source'],actual)
        self.assertEqual(row['id'],'20');self.assertFalse(row['loaded'])
        self.assertEqual(row['special'],'external')
        self.assertEqual(row['resource_information']['Path'],actual)
    def test_unknown_metadata_value_is_not_invented_as_source(self):
        self.assertEqual(self.b.resource_source('',{'ModelIdentity':'C:\\NotAPathField.rvt'},'RevitLink'),'')
    def test_report_directory_path_is_preserved(self):
        path='L:\\BIM\\Reports'
        self.assertEqual(self.b.resource_source('',{'Path':path,'PathType':'Absolute'},'SystemsAnalysisReport'),path)
    def test_unknown_basic_metadata_can_fallback_to_valid_native_container(self):
        from test_payload_acquisition import compound
        root=tempfile.mkdtemp(prefix='ET_meta_');self.addCleanup(shutil.rmtree,root)
        path=os.path.join(root,'Host.rvt')
        with open(path,'wb') as out:out.write(compound())
        def fail(p):raise RuntimeError('BasicFileInfo storage has changed')
        self.db.BasicFileInfo=Obj(Extract=fail)
        result=self.b.basic(path)
        self.assertEqual(result['version'],'2024')
        self.assertIn('BasicFileInfo',result.get('metadata_warning',''))
    def test_image_relative_option_uses_final_host_base(self):
        self.b.guard=lambda path:None  # Windows API path formatting test on either OS
        self.db.ImageTypeSource=Obj(Link='Link')
        self.db.ImageTypeOptions=lambda path,rel,src:Obj(Path=path)
        row=dict(target='C:\\Out\\PDF\\Details.pdf',page=1,resolution=300)
        try:options=self.b.image_options(row,True,'C:\\Out\\RVT\\Host.rvt')
        except TypeError: self.fail('image_options must accept final model path for real relative paths')
        self.assertEqual(options.Path,'..\\PDF\\Details.pdf')

class EngineRepairs(unittest.TestCase):
    def test_metadata_failure_is_not_retried_in_finishing(self):
        root=tempfile.mkdtemp(prefix='ET_fail_');self.addCleanup(shutil.rmtree,root)
        host=os.path.join(root,'Host.rvt')
        with open(host,'wb') as out:out.write(b'host')
        calls=[]
        def fail(*args):raise RuntimeError('cannot read metadata')
        backend=Obj(scan=fail,finish=lambda *args:calls.append(args))
        result=e.transmit([host],os.path.join(root,'out'),backend)
        self.assertEqual(calls,[])
        self.assertEqual(result['files'][0].get('processing_status'),'SKIPPED_INVENTORY_FAILURE')
    def test_payload_acquisition_is_used_before_scan(self):
        from test_payload_acquisition import compound
        import zipfile
        from easybim_etransmit import model_payload as p
        root=tempfile.mkdtemp(prefix='ET_wrap_');self.addCleanup(shutil.rmtree,root)
        host=os.path.join(root,'Host.rvt')
        with zipfile.ZipFile(host,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('Host.rvt',compound())
        class B(object):
            def set_staging_root(self,work):self.store=p.Store(work)
            def acquire_file(self,source,target,owner='',cancelled=None,pulse=None):
                return self.store.copy(source,target,owner,cancelled,pulse)
            def scan(self,source,stage,options):
                if p.probe(stage)['container']!='CFB':raise ValueError('ZIP reached Revit')
                return dict(references=[],issues=[],version='2024',opened_in_revit=True)
            def finish(self,*a):return []
        result=e.transmit([host],os.path.join(root,'out'),B())
        self.assertEqual(result['status'],'COLLECTED',repr(result['issues']))
        self.assertEqual(result['files'][0].get('acquisition_container'),'ZIP')
        self.assertEqual(result['files'][0].get('opened_in_revit'),True)
    def test_package_verification_runs_after_all_processing(self):
        root=tempfile.mkdtemp(prefix='ET_verify_');self.addCleanup(shutil.rmtree,root)
        host=os.path.join(root,'Host.rvt')
        with open(host,'wb') as out:out.write(b'host')
        calls=[]
        def verify(*args):
            self.assertTrue(calls and calls[0]=='finish'); calls.append('verify'); return []
        b=Obj(scan=lambda *a:dict(references=[],issues=[]),
              finish=lambda *a:calls.append('finish') or [],verify_package=verify)
        result=e.transmit([host],os.path.join(root,'out'),b)
        self.assertEqual(calls,['finish','verify','verify'])
        self.assertEqual(result['files'][0].get('model_verification'),'OPENED_AND_REFERENCES_CHECKED')

    def test_parent_is_processed_after_collected_link(self):
        root=tempfile.mkdtemp(prefix='ET_order_');self.addCleanup(shutil.rmtree,root)
        host=os.path.join(root,'Host.rvt');link=os.path.join(root,'Arch.rvt')
        for path in (host,link):
            with open(path,'wb') as out:out.write(b'rvt')
        calls=[]
        def scan(source,*a):return dict(references=[dict(id='1',source=link)] if source==host else [],issues=[])
        b=Obj(scan=scan,finish=lambda s,t,*a:calls.append(os.path.basename(t)) or [])
        e.transmit([host],os.path.join(root,'out'),b)
        self.assertEqual(calls,['Arch.rvt','Host.rvt'])

if __name__=='__main__':unittest.main(verbosity=2)
