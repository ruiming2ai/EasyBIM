# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, shutil, sys, tempfile, unittest, types
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'));sys.path.insert(0,os.path.join(ROOT,'lib'));sys.path.insert(0,os.path.dirname(__file__))
from easybim_etransmit import engine, files as f, revit
from test_payload_acquisition import compound
from test_212_cache import Obj, P, W
from test_213_session import cloud_row
class EngineFinalizationBackend(object):
    independent_host_supported=True
    def __init__(self,host,link,root):self.host=host;self.link=link;self.root=root;self.calls=[];self.staging_root=None
    def set_staging_root(self,p):self.staging_root=p
    def source_relative(self,s):return os.path.basename(s)
    def defer_file_copy(self,*a):return True
    def can_deliver_direct(self,*a):return False
    def is_virtual_source(self,s):return False
    def resolve_acquired_source(self,s,o=''):return s
    def inventory_before_copy(self,source,opts):
        if source!=self.host:return None
        row=dict(id='42',element_id='42',kind='RevitLink',source=self.link,td=False,loaded=True,special='external',cloud_identity=dict(project_guid=P,model_guid=W,region='US'))
        return dict(references=[row],issues=[],version='2025',is_workshared=True,inspection_status='LIVE_DOCUMENT_REFERENCE_INVENTORY')
    def acquire_file(self,source,target,owner='',cancelled=None,pulse=None):return f.copy_file(source,target,cancelled,pulse)
    def acquired_identity(self,source,meta):return f.canonical(source)
    def requires_final_host_open(self,record,rows,options):return bool(record.get('is_primary_host') and options.get('repath') and any(r.get('cloud_identity') for r in rows))
    def finish(self,stage,target,rows,options):
        self.calls.append(('finish',target,options.get('verify_in_process',False)));self.assert_final=target==os.path.join(self.root,'Host.rvt');shutil.copyfile(stage,target)
        assert options.get('independent_host')
        assert all(f.file_exists(r['target']) for r in rows)
        with open(target,'ab') as out:out.write(b'finalized')
        for row in rows:row['verification']='SAVED_REFERENCE_CHECKED';row['repath']='API_LOCAL_LINK_RELATIVE'
        return dict(issues=[],verified_in_process=False,worker_repaired=True,
                    host_finalized=True,saved_references_checked=True,
                    verification_status='SAVED_REFERENCES_CHECKED',independent_package_central=True)
    def verify_package(self,target,rows,options):self.calls.append(('verify',target));raise AssertionError('parent must not reopen finalized host')
    def mark_transmitted_package(self,*a):self.calls.append(('mark_transmitted',));raise AssertionError('parent must not mark finalized host transmitted')
    def source_context(self,*a):return {}
class EngineSingleOpen(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET219_acc_');self.addCleanup(shutil.rmtree,self.root);self.host=os.path.join(self.root,'src','Host.rvt');self.link=os.path.join(self.root,'src','Architecture.rvt');os.makedirs(os.path.dirname(self.host))
        with open(self.host,'wb') as out:out.write(compound(suffix='host'))
        with open(self.link,'wb') as out:out.write(compound(suffix='link'))
        self.out=os.path.join(self.root,'out')
    def test_acc_host_finalizes_after_delivery_and_skips_second_verification_open(self):
        b=EngineFinalizationBackend(self.host,self.link,self.out);opts=dict(f.defaults(), simple_repath=False);opts['repath']=True;result=engine.transmit([self.host],self.out,b,opts)
        self.assertEqual([c[0] for c in b.calls],['finish']);self.assertFalse(b.calls[0][2]);self.assertTrue(b.assert_final);host=next(r for r in result['files'] if r.get('is_primary_host'));self.assertEqual(host['model_verification'],'SAVED_REFERENCES_CHECKED');self.assertTrue(host.get('worker_repaired'));self.assertFalse(host.get('verified_in_process'))
        self.assertEqual(host['transmission_status'],'NOT_TRANSMITTED')
        self.assertEqual(host['opening_guidance'],'OPEN_NORMALLY_INDEPENDENT_PACKAGE')
class RevitSingleOpen(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET219_revit_');self.addCleanup(shutil.rmtree,self.root)
        try:import System
        except ImportError:
            module=types.ModuleType('System');module.Int64=int;module.Int32=int;sys.modules['System']=module;self.addCleanup(sys.modules.pop,'System',None)
        self.stage=os.path.join(self.root,'stage.rvt');self.target=os.path.join(self.root,'Host.rvt');self.link_path=os.path.join(self.root,'Links','Revit','Architecture.rvt');os.makedirs(os.path.dirname(self.link_path))
        for p,data in ((self.stage,b'host'),(self.link_path,b'link')):
            with open(p,'wb') as out:out.write(data)
        self.opened=0;self.loads=[];self.saves=[];self.closed=[];self.loaded=True;self.metadata_calls=[]
        self.link=Obj(Id=Obj(Value=42),IsFromLocalPath=False,LoadFrom=lambda resource,ws:self.loads.append(resource) or Obj(LoadResult='LinkLoaded'),Unload=lambda x:None,GetExternalFileReference=lambda:self.saved_reference)
        self.saved_reference=Obj(PathType='Relative',GetPath=lambda:os.path.relpath(self.link_path,os.path.dirname(self.target)),GetAbsolutePath=lambda:self.link_path,GetLinkedFileStatus=lambda:'Loaded')
        self.td=Obj(IsTransmitted=False,GetAllExternalFileReferenceIds=lambda:[Obj(Value=42)],GetLastSavedReferenceData=lambda ident:self.saved_reference)
        def save_as(path,opts):
            self.saves.append(('SaveAs',path));shutil.copyfile(self.stage,path)
            self.doc.PathName=path;self.td.IsTransmitted=False
        self.doc=Obj(IsWorkshared=False,PathName=self.stage,GetElement=lambda i:self.link,SaveAs=save_as,Save=lambda:self.saves.append(('Save',self.target)),Close=lambda x:self.closed.append(x) or True)
        def resource(doc,kind,path,path_type):return Obj(doc=doc,path=path,path_type=path_type)
        db=Obj(ElementId=lambda i:Obj(Value=i),SaveAsOptions=lambda:Obj(),ExternalResourceReference=Obj(CreateLocalResource=resource),ExternalResourceTypes=Obj(BuiltInExternalResourceTypes=Obj(RevitLink='RVT')),PathType=Obj(Absolute='Absolute',Relative='Relative'),RevitLinkType=Obj(IsLoaded=lambda d,i:self.loaded),TransmissionData=Obj(ReadTransmissionData=lambda path:self.td if path==self.target else None))
        self.b=revit.Backend(db,Obj(VersionNumber='2025'),self.root);self.b.basic=lambda p:dict(version='2025',workshared=False);self.b.mp=lambda p:p;self.b.visible=lambda p:p
        def open_copy(path,*a,**kwargs):self.opened+=1;return self.doc
        def apply_metadata(path,target,rows,relative=True):
            self.metadata_calls.append((path,relative))
            if path==self.target and relative:
                self.td.IsTransmitted=True
                return True
            return False
        self.b.open_copy=open_copy;self.b.apply_metadata=apply_metadata;self.row=cloud_row();self.row.update(id='42',element_id='42',target=self.link_path,package_loaded=True,loaded=True,td=False)
    def test_finish_materializes_relative_local_resource_and_checks_saved_host_without_parent_open(self):
        result=self.b.finish(self.stage,self.target,[self.row],dict(repath=True,independent_host=True,verify_in_process=False))
        self.assertEqual(self.opened,3)
        self.assertFalse(result['verified_in_process'])
        self.assertTrue(result['host_finalized'])
        self.assertTrue(result['saved_references_checked'])
        self.assertEqual(result['verification_status'],'SAVED_REFERENCES_CHECKED')
        self.assertEqual(result['issues'],[])
        self.assertEqual([r.path_type for r in self.loads],['Relative'])
        self.assertEqual(self.row['repath'],'API_LOCAL_LINK_RELATIVE')
        self.assertEqual(self.row['verification'],'SAVED_REFERENCE_CHECKED')
        self.assertEqual(self.closed,[False,False,False])
        self.assertFalse(self.td.IsTransmitted)
        self.assertEqual([s[0] for s in self.saves],['SaveAs','Save','SaveAs'])
if __name__=='__main__':unittest.main(verbosity=2)
