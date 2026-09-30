# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, shutil, sys, tempfile, unittest, types
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'));sys.path.insert(0,os.path.join(ROOT,'lib'));sys.path.insert(0,os.path.dirname(__file__))
from easybim_etransmit import engine, files as f, revit
from test_payload_acquisition import compound
from test_212_cache import Obj, P, W
from test_213_session import cloud_row
class EngineFinalizationBackend(object):
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
        return dict(references=[row],issues=[],version='2025',inspection_status='LIVE_DOCUMENT_REFERENCE_INVENTORY')
    def acquire_file(self,source,target,owner='',cancelled=None,pulse=None):return f.copy_file(source,target,cancelled,pulse)
    def acquired_identity(self,source,meta):return f.canonical(source)
    def requires_final_host_open(self,record,rows,options):return bool(record.get('is_primary_host') and options.get('repath') and any(r.get('cloud_identity') for r in rows))
    def finish(self,stage,target,rows,options):
        self.calls.append(('finish',target,options.get('verify_in_process',False)));self.assert_final=target==os.path.join(self.root,'Host.rvt');shutil.copyfile(stage,target)
        with open(target,'ab') as out:out.write(b'finalized')
        for row in rows:row['verification']='PATH_AND_LOAD_CHECKED';row['repath']='API_LOCAL_LINK_RELATIVE'
        return dict(issues=[],verified_in_process=False,worker_repaired=True)
    def verify_package(self,target,rows,options):self.calls.append(('verify',target));return []
    def source_context(self,*a):return {}
class EngineSingleOpen(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET219_acc_');self.addCleanup(shutil.rmtree,self.root);self.host=os.path.join(self.root,'src','Host.rvt');self.link=os.path.join(self.root,'src','Architecture.rvt');os.makedirs(os.path.dirname(self.host))
        with open(self.host,'wb') as out:out.write(compound(suffix='host'))
        with open(self.link,'wb') as out:out.write(compound(suffix='link'))
        self.out=os.path.join(self.root,'out')
    def test_acc_host_finalizes_after_delivery_and_skips_second_verification_open(self):
        b=EngineFinalizationBackend(self.host,self.link,self.out);opts=f.defaults();opts['repath']=True;result=engine.transmit([self.host],self.out,b,opts)
        self.assertEqual([c[0] for c in b.calls],['finish']);self.assertFalse(b.calls[0][2]);self.assertTrue(b.assert_final);host=next(r for r in result['files'] if r.get('is_primary_host'));self.assertEqual(host['model_verification'],'WORKER_SAVE_COMPLETED');self.assertTrue(host.get('worker_repaired'));self.assertFalse(host.get('verified_in_process'))

class ImageFinalizationBackend(object):
    def __init__(self,host,pdf,root):
        self.host=host;self.pdf=pdf;self.root=root;self.calls=[];self.staging_root=None
    def set_staging_root(self,p):self.staging_root=p
    def source_relative(self,s):return os.path.basename(s)
    def defer_file_copy(self,*a):return True
    def can_deliver_direct(self,*a):return False
    def is_virtual_source(self,s):return False
    def resolve_acquired_source(self,s,o=''):return s
    def inventory_before_copy(self,source,opts):
        if source!=self.host:return None
        row=dict(id='55',element_id='55',kind='Image',source=self.pdf,
                 loaded=True,special='image',page=1,resolution=300)
        return dict(references=[row],issues=[],version='2025',is_workshared=True,
                    inspection_status='LIVE_DOCUMENT_REFERENCE_INVENTORY')
    def acquire_file(self,source,target,owner='',cancelled=None,pulse=None):
        return f.copy_file(source,target,cancelled,pulse)
    def acquired_identity(self,source,meta):return f.canonical(source)
    def requires_final_host_open(self,record,rows,options):
        return revit.Backend.requires_final_host_open(self,record,rows,options)
    def finish(self,stage,target,rows,options):
        self.calls.append(dict(stage=stage,target=target,rows=[dict(r) for r in rows],
                               options=dict(options)))
        shutil.copyfile(stage,target)
        for row in rows:row['repath']='API_IMAGE_RELATIVE'
        return dict(issues=[],verified_in_process=False,worker_repaired=True,
                    package_central=True)
    mark_transmitted_rows_supported=True
    def mark_transmitted_package(self,*args):
        self.calls.append(dict(transmit=True))
        return True
    def verify_package(self,*args):
        self.fail_verify=True;return []
    def source_context(self,*a):return {}


class ImageFinalLocation(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET219_pdf_')
        self.addCleanup(shutil.rmtree,self.root)
        source=os.path.join(self.root,'src');os.makedirs(source)
        self.host=os.path.join(source,'Host.rvt')
        self.pdf=os.path.join(source,'Details.pdf')
        with open(self.host,'wb') as out:out.write(compound(suffix='host'))
        with open(self.pdf,'wb') as out:out.write(b'pdf')
        self.out=os.path.join(self.root,'out')

    def test_image_host_is_repaired_only_after_delivery_at_final_package_path(self):
        backend=ImageFinalizationBackend(self.host,self.pdf,self.out)
        opts=f.defaults();opts['repath']=True
        result=engine.transmit([self.host],self.out,backend,opts)
        repair_calls=[c for c in backend.calls if not c.get('transmit')]
        self.assertEqual(len(repair_calls),1,repr(result['issues']))
        call=repair_calls[0]
        self.assertEqual(f.canonical(call['target']),
                         f.canonical(os.path.join(self.out,'Host.rvt')))
        self.assertTrue(f.within(call['rows'][0]['target'],self.out))
        self.assertNotEqual(f.canonical(call['stage']),f.canonical(call['target']))
        host=next(r for r in result['files'] if r.get('is_primary_host'))
        self.assertEqual(host.get('model_verification'),'WORKER_SAVE_COMPLETED')
        self.assertEqual(host.get('transmission_status'),'TRANSMITTED')
        self.assertTrue(host.get('package_central_repair_base'))
        self.assertFalse(getattr(backend,'fail_verify',False))


class RevitSingleOpen(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET219_revit_');self.addCleanup(shutil.rmtree,self.root)
        try:import System
        except ImportError:
            module=types.ModuleType('System');module.Int64=int;module.Int32=int;sys.modules['System']=module;self.addCleanup(sys.modules.pop,'System',None)
        self.stage=os.path.join(self.root,'stage.rvt');self.target=os.path.join(self.root,'Host.rvt');self.link_path=os.path.join(self.root,'Links','Revit','Architecture.rvt');os.makedirs(os.path.dirname(self.link_path))
        for p,data in ((self.stage,b'host'),(self.link_path,b'link')):
            with open(p,'wb') as out:out.write(data)
        self.opened=0;self.loads=[];self.saves=[];self.closed=[];self.loaded=True
        self.link=Obj(Id=Obj(Value=42),IsFromLocalPath=False,LoadFrom=lambda resource,ws:self.loads.append(resource) or Obj(LoadResult='LinkLoaded'),Unload=lambda x:None,GetExternalFileReference=lambda:Obj(GetAbsolutePath=lambda:self.link_path))
        def save_as(path,opts):self.saves.append(('SaveAs',path));shutil.copyfile(self.stage,path)
        self.doc=Obj(IsWorkshared=False,GetElement=lambda i:self.link,SaveAs=save_as,Save=lambda:self.saves.append(('Save',self.target)),Close=lambda x:self.closed.append(x) or True)
        def resource(doc,kind,path,path_type):return Obj(doc=doc,path=path,path_type=path_type)
        db=Obj(ElementId=lambda i:Obj(Value=i),SaveAsOptions=lambda:Obj(),ExternalResourceReference=Obj(CreateLocalResource=resource),ExternalResourceTypes=Obj(BuiltInExternalResourceTypes=Obj(RevitLink='RVT')),PathType=Obj(Absolute='Absolute',Relative='Relative'),RevitLinkType=Obj(IsLoaded=lambda d,i:self.loaded))
        self.b=revit.Backend(db,Obj(VersionNumber='2025'),self.root);self.b.basic=lambda p:dict(version='2025',workshared=False);self.b.mp=lambda p:p;self.b.visible=lambda p:p
        def open_copy(path,*a):self.opened+=1;return self.doc
        self.b.open_copy=open_copy;self.b.apply_metadata=lambda *a,**k:True;self.row=cloud_row();self.row.update(id='42',element_id='42',target=self.link_path,package_loaded=True,loaded=True,td=False)
    def test_finish_converts_to_relative_local_resource_and_verifies_before_close(self):
        result=self.b.finish(self.stage,self.target,[self.row],dict(repath=True,verify_in_process=True));self.assertEqual(self.opened,1);self.assertTrue(result['verified_in_process']);self.assertEqual(result['issues'],[]);self.assertEqual([r.path_type for r in self.loads],['Absolute','Relative']);self.assertEqual(self.row['repath'],'API_LOCAL_LINK_RELATIVE');self.assertEqual(self.row['verification'],'PATH_AND_LOAD_CHECKED');self.assertEqual(self.closed,[False])
if __name__=='__main__':unittest.main(verbosity=2)
