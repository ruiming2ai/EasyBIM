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
    def test_final_verification_checks_cad_link_path(self):
        target='C:\\Out\\CAD\\site.dwg'
        ref=Obj(GetAbsolutePath=lambda:target)
        element=Obj()
        self.db.ExternalFileUtils=Obj(
            GetAllExternalFileReferences=lambda d:[],
            GetExternalFileReference=lambda doc,ident:ref)
        self.db.ElementId=lambda value:value
        self.b.visible=lambda value:value
        row=dict(element_id='20',id='20',kind='CADLink',target=target,source='old.dwg')
        old_system=sys.modules.get('System')
        sys.modules['System']=Obj(Int64=int,Int32=int)
        try:
            issues=self.b._verify_document(Obj(GetElement=lambda ident:element),
                                           'C:\\Out\\Host.rvt',[row],{})
        finally:
            if old_system is None:sys.modules.pop('System',None)
            else:sys.modules['System']=old_system
        self.assertEqual(issues,[])
        self.assertEqual(row.get('verification'),'PATH_CHECKED')

    def test_cad_link_repair_uses_packaged_target(self):
        calls=[]
        result=Obj(LoadResult='LinkLoaded',Dispose=lambda:None)
        link=Obj(LoadFrom=lambda path:calls.append(path) or result)
        self.db.ElementId=lambda value:value
        old_system=sys.modules.get('System')
        sys.modules['System']=Obj(Int64=int,Int32=int)
        row=dict(element_id='20',id='20',kind='CADLink',target='C:\\Out\\CAD\\site.dwg')
        try:
            self.b.repath_cad_links(Obj(GetElement=lambda ident:link),[row])
        finally:
            if old_system is None:sys.modules.pop('System',None)
            else:sys.modules['System']=old_system
        self.assertEqual(calls,['C:\\Out\\CAD\\site.dwg'])
        self.assertEqual(row['repath'],'API_CAD_LINK')

    def test_cad_repath_failure_is_reported_without_aborting_other_repairs(self):
        bad=Obj(LoadFrom=lambda path:(_ for _ in ()).throw(RuntimeError('bad dwg')))
        good_result=Obj(LoadResult='LinkLoaded',Dispose=lambda:None)
        good=Obj(LoadFrom=lambda path:good_result)
        self.db.ElementId=lambda value:value
        old_system=sys.modules.get('System')
        sys.modules['System']=Obj(Int64=int,Int32=int)
        rows=[dict(element_id='20',id='20',kind='CADLink',source='bad.dwg',target='C:\\Out\\CAD\\bad.dwg'),
              dict(element_id='21',id='21',kind='CADLink',source='good.dwg',target='C:\\Out\\CAD\\good.dwg')]
        try:
            issues=self.b.repath_cad_links(Obj(GetElement=lambda ident:bad if ident==20 else good),rows)
        finally:
            if old_system is None:sys.modules.pop('System',None)
            else:sys.modules['System']=old_system
        self.assertEqual(rows[0]['repath'],'FAILED')
        self.assertEqual(rows[1]['repath'],'API_CAD_LINK')
        self.assertEqual([x['code'] for x in issues],['CAD_REPATH_FAILED'])

    def test_image_relative_option_uses_absolute_file_for_revit_to_relativize(self):
        self.b.guard=lambda path:None
        self.db.ImageTypeSource=Obj(Link='Link')
        captured=[]
        self.db.ImageTypeOptions=lambda path,rel,src:captured.append((path,rel,src)) or Obj(Path=path)
        row=dict(target='C:\\Out\\Links\\PDF\\Details.pdf',page=1,resolution=300)
        self.b.image_options(row,True,'C:\\Out\\Host.rvt')
        self.assertEqual(captured[0][0],row['target'])
        self.assertTrue(captured[0][1])

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
        self.assertEqual(options.Path,'C:\\Out\\PDF\\Details.pdf')

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
        self.assertEqual(calls,['finish','verify'])
        self.assertEqual(result['files'][0].get('model_verification'),'OPENED_AND_REFERENCES_CHECKED')

    def test_worker_repaired_host_is_not_reopened_for_verification(self):
        root=tempfile.mkdtemp(prefix='ET_worker_noverify_');self.addCleanup(shutil.rmtree,root)
        host=os.path.join(root,'Host.rvt')
        with open(host,'wb') as out:out.write(b'host')
        calls=[]
        class B(object):
            def scan(self,*args):return dict(references=[],issues=[],is_workshared=False,version='2026')
            def finish(self,*args):
                calls.append('finish')
                return dict(issues=[],verified_in_process=False,worker_repaired=True)
            def verify_package(self,*args):
                calls.append('verify')
                return []
        result=e.transmit([host],os.path.join(root,'out'),B())
        self.assertEqual(calls,['finish'])
        self.assertTrue(result['files'][0].get('worker_repaired'))
        self.assertEqual(result['files'][0].get('model_verification'),'WORKER_SAVE_COMPLETED')

    def test_worker_package_central_is_marked_transmitted_only_after_repair_and_not_reopened(self):
        root=tempfile.mkdtemp(prefix='ET_pkgcentral_');self.addCleanup(shutil.rmtree,root)
        host=os.path.join(root,'Host.rvt')
        with open(host,'wb') as out:out.write(b'host')
        calls=[]
        class B(object):
            mark_transmitted_rows_supported=True
            def scan(self,*args):return dict(references=[],issues=[],is_workshared=True,version='2026')
            def finish(self,*args):
                calls.append('finish')
                return dict(issues=[],verified_in_process=False,worker_repaired=True,package_central=True)
            def mark_transmitted_package(self,*args):
                calls.append('transmit');return True
            def verify_package(self,*args):
                calls.append('verify');return []
        result=e.transmit([host],os.path.join(root,'out'),B())
        self.assertEqual(calls,['finish','transmit'])
        row=result['files'][0]
        self.assertEqual(row.get('transmission_status'),'TRANSMITTED')
        self.assertTrue(row.get('package_central_repair_base'))
        self.assertFalse(row.get('original_central_association_preserved'))
        self.assertEqual(row.get('model_verification'),'WORKER_SAVE_COMPLETED')

    def test_transmitted_workshared_host_is_verified_after_metadata_rewrite(self):
        root=tempfile.mkdtemp(prefix='ET_posttx_');self.addCleanup(shutil.rmtree,root)
        host=os.path.join(root,'Host.rvt')
        with open(host,'wb') as out:out.write(b'host')
        calls=[]
        class B(object):
            def scan(self,*args):
                return dict(references=[],issues=[],is_workshared=True,version='2026')
            def finish(self,*args):
                calls.append('finish')
                return dict(issues=[],verified_in_process=True)
            def mark_transmitted_package(self,path,rows=None):
                calls.append('transmit')
                return True
            def verify_package(self,*args):
                calls.append('verify')
                return []
        result=e.transmit([host],os.path.join(root,'out'),B())
        self.assertEqual(calls,['finish','transmit','verify'])
        self.assertEqual(result['files'][0].get('transmission_status'),'TRANSMITTED')
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
