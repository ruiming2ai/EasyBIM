# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, shutil, sys, tempfile, types, unittest
from test_212_cache import P, W, Obj
from test_213_session import cloud_row
import test_runtime_reference_repairs as fixtures
from easybim_etransmit import revit, engine, files as f


class ResourceDiscovery(fixtures.References):
    def test_unloaded_type_not_listed_by_external_utils_still_yields_cloud_identity_and_name(self):
        row=cloud_row()
        resource=Obj(InSessionPath='',ServerId='ACC',Version='',GetReferenceInformation=lambda:row['resource_information'])
        link=Obj(Id=Obj(Value=4815243),Name='Architecture.rvt',IsNestedLink=False,
                 GetExternalResourceReferences=lambda:{'RVT':resource})
        self.b.elements=lambda d,k:[link] if k=='RevitLinkType' else []
        self.db.RevitLinkType=Obj(IsLoaded=lambda d,i:False)
        result=dict(references=[],issues=[])
        self.b.scan_open(Obj(GetElement=lambda i:link),'Host.rvt',dict(central='',workshared=False),result)
        actual=result['references'][0]
        self.assertEqual(actual['source'],'');self.assertFalse(actual['loaded'])
        self.assertEqual(actual['link_name'],'Architecture.rvt')
        self.assertEqual(actual['cloud_identity'],dict(project_guid=P,model_guid=W,region='US'))
        self.assertEqual(result['issues'],[])

    def test_empty_keynote_is_unconfigured_but_configured_missing_path_is_not(self):
        row=dict(kind='KeynoteTable',source='',in_session_path='',
                 resource_information=dict(Path='',PathType='Absolute',ModelIdentity='00000000-0000-0000-0000-000000000000'))
        self.assertTrue(revit.unconfigured_keynote(row))
        row['source']='MissingKeynotes.txt'
        self.assertFalse(revit.unconfigured_keynote(row))
        row['source']='';row['resource_information']['ModelIdentity']=W
        self.assertFalse(revit.unconfigured_keynote(row))

    def test_native_keynote_path_survives_empty_external_representation(self):
        row=dict(kind='KeynoteTable',source='',saved_path='Configured.txt',
                 resource_information=dict(Path='',ModelIdentity='00000000-0000-0000-0000-000000000000'))
        self.assertFalse(revit.unconfigured_keynote(row))


for name in fixtures.References.__dict__:
    if name.startswith('test_') and name not in ResourceDiscovery.__dict__:setattr(ResourceDiscovery,name,None)


class PackageRevitOperations(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_revit213_');self.addCleanup(shutil.rmtree,self.root)
        try:import System
        except ImportError:
            module=types.ModuleType('System');module.Int64=int;module.Int32=int
            sys.modules['System']=module;self.addCleanup(sys.modules.pop,'System',None)
        self.target=os.path.join(self.root,'Host.rvt');self.stage=os.path.join(self.root,'stage.rvt')
        self.link_path=os.path.join(self.root,'Architecture.rvt')
        self.loads=[];self.unloads=[];self.resources=[];self.saves=[];self.closed=[]
        self.loaded=True
        def load(resource,worksets):
            self.loads.append(resource)
            return Obj(LoadResult='LinkLoaded')
        self.link=Obj(IsFromLocalPath=False,LoadFrom=load,Unload=lambda x:self.unloads.append(True),
            GetExternalFileReference=lambda:Obj(GetAbsolutePath=lambda:self.link_path))
        def save(path,options):
            self.saves.append(path)
            with open(path,'wb') as out:out.write(b'processed copy')
        self.doc=Obj(IsWorkshared=False,GetElement=lambda i:self.link,SaveAs=save,
                     Close=lambda save:self.closed.append(save) or True)
        def resource(doc,kind,path,path_type):
            value=Obj(doc=doc,kind=kind,path=path,path_type=path_type)
            self.resources.append(value);return value
        self.db=Obj(ElementId=lambda i:Obj(Value=i),SaveAsOptions=lambda:Obj(),
            ExternalResourceReference=Obj(CreateLocalResource=resource),
            ExternalResourceTypes=Obj(BuiltInExternalResourceTypes=Obj(RevitLink='RVT')),
            PathType=Obj(Absolute='Absolute',Relative='Relative'),
            RevitLinkType=Obj(IsLoaded=lambda d,i:self.loaded))
        self.b=revit.Backend(self.db,Obj(VersionNumber='2024'),self.root)
        self.b.basic=lambda p:dict(version='2024',workshared=False)
        self.b.open_copy=lambda *a:self.doc
        self.b.mp=lambda path:path;self.b.visible=lambda path:path
        self.b.apply_metadata=lambda *a,**kw:True
        self.row=cloud_row();self.row.update(target=self.link_path,original_loaded=False,package_loaded=True)

    def test_cloud_link_uses_local_resource_overload_on_existing_element(self):
        self.assertEqual(self.b.finish(self.stage,self.target,[self.row],dict(repath=True)),[])
        self.assertEqual(self.loads,self.resources)
        resource=self.resources[0]
        self.assertIs(resource.doc,self.doc)
        self.assertEqual(resource.path,self.link_path)
        self.assertEqual(resource.path_type,'Absolute')
        self.assertEqual(self.saves,[self.target]);self.assertEqual(self.unloads,[])
        self.assertEqual(self.closed,[False])

    def test_unchecked_load_option_unloads_only_packaged_link(self):
        self.row['package_loaded']=False
        self.b.finish(self.stage,self.target,[self.row],dict(repath=True))
        self.assertEqual(self.unloads,[True])

    def test_cloud_link_with_native_td_representation_still_converts_resource(self):
        self.row['td']=True
        self.b.finish(self.stage,self.target,[self.row],dict(repath=True))
        self.assertEqual(self.loads,self.resources)
        self.assertEqual(len(self.resources),1)

    def test_failed_cloud_conversion_does_not_save_partial_model(self):
        self.link.LoadFrom=lambda *a:Obj(LoadResult='LinkNotFound')
        with self.assertRaises(RuntimeError):self.b.finish(self.stage,self.target,[self.row],dict(repath=True))
        self.assertEqual(self.saves,[]);self.assertEqual(self.closed,[False])

    def test_bare_cached_rvt_is_saved_as_independent_copy(self):
        self.b.finish(self.stage,self.target,[],dict(repath=True,normalize_saved_cache=True))
        self.assertEqual(self.saves,[self.target])

    def test_verifier_checks_requested_loaded_state(self):
        self.loaded=False
        problems=self.b.verify_package(self.target,[self.row],{})
        self.assertTrue(any(p['code']=='LINK_VERIFICATION_FAILED' for p in problems))
        self.loaded=True
        self.assertEqual(self.b.verify_package(self.target,[self.row],{}),[])
        self.assertEqual(self.row['verification'],'PATH_AND_LOAD_CHECKED')

    def test_verifier_checks_preserved_unloaded_state(self):
        self.row['package_loaded']=False
        self.loaded=True
        self.assertTrue(self.b.verify_package(self.target,[self.row],{}))
        self.loaded=False
        self.assertEqual(self.b.verify_package(self.target,[self.row],{}),[])

    def test_transmission_data_receives_requested_package_load_state(self):
        calls=[];ident=Obj(Value=4815243)
        td=Obj(GetAllExternalFileReferenceIds=lambda:[ident],
               SetDesiredReferenceData=lambda *args:calls.append(args))
        self.db.TransmissionData=Obj(ReadTransmissionData=lambda p:td,WriteTransmissionData=lambda *a:None)
        self.row['resource_information']={}
        # Use the real method, not the finishing-test stub.
        revit.Backend.apply_metadata(self.b,self.stage,self.target,[self.row])
        self.assertTrue(calls[0][3]);self.assertFalse(self.row['loaded'])
        self.row['package_loaded']=False
        revit.Backend.apply_metadata(self.b,self.stage,self.target,[self.row])
        self.assertFalse(calls[1][3])

    def test_cloud_td_repath_waits_until_resource_conversion(self):
        calls=[];ident=Obj(Value=4815243)
        td=Obj(GetAllExternalFileReferenceIds=lambda:[ident],SetDesiredReferenceData=lambda *args:calls.append(args))
        self.db.TransmissionData=Obj(ReadTransmissionData=lambda p:td,WriteTransmissionData=lambda *a:None)
        revit.Backend.apply_metadata(self.b,self.stage,self.target,[self.row])
        self.assertEqual(calls,[])
        self.row['repath']='API_LOCAL_LINK'
        revit.Backend.apply_metadata(self.b,self.target,self.target,[self.row])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][2],'Relative')


class KeynoteCollection(unittest.TestCase):
    def test_unconfigured_placeholder_does_not_create_failed_file(self):
        root=tempfile.mkdtemp(prefix='ET_keynote213_');self.addCleanup(shutil.rmtree,root)
        host=os.path.join(root,'Host.rvt')
        with open(host,'wb') as out:out.write(b'host')
        rows=[dict(id='117704',kind='KeynoteTable',source='',unconfigured=True),
              dict(id='2',kind='KeynoteTable',source=os.path.join(root,'Missing.txt'))]
        b=Obj(scan=lambda *a:dict(references=rows,issues=[]))
        opts=f.defaults();opts['repath']=False
        result=engine.transmit([host],os.path.join(root,'out'),b,opts)
        self.assertEqual(result['references'][0]['status'],'UNCONFIGURED')
        self.assertEqual(len([i for i in result['issues'] if i['severity']=='error']),1)
        self.assertEqual(len(result['files']),2)

if __name__=='__main__':unittest.main(verbosity=2)
