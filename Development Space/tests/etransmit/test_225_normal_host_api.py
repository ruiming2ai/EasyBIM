# -*- coding: utf-8 -*-
"""Stateful API boundary tests; Autodesk Revit is not executed here."""
from __future__ import unicode_literals
import copy
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import revit


class Obj(object):
    def __init__(self,**kwargs):self.__dict__.update(kwargs)
    def Dispose(self):pass


class Reference(Obj):
    def __init__(self,values,owner=''):
        self.path,self.PathType,self.loaded=values
        self.owner=owner
    def GetPath(self):return self.path
    def GetAbsolutePath(self):
        return os.path.normpath(os.path.join(os.path.dirname(self.owner),self.path)) if self.PathType=='Relative' else self.path
    def GetLinkedFileStatus(self):return 'Loaded' if self.loaded else 'Unloaded'


class Transmission(Obj):
    def __init__(self,model):
        self.saved=copy.deepcopy(model['saved'])
        self.desired=copy.deepcopy(model.get('desired',{}))
        self.IsTransmitted=model.get('transmitted',False)
    def GetAllExternalFileReferenceIds(self):return [Obj(Value=int(key)) for key in self.saved]
    def GetLastSavedReferenceData(self,ident):return Reference(self.saved[str(ident.Value)])
    def GetDesiredReferenceData(self,ident):
        values=self.desired.get(str(ident.Value))
        return Reference(values) if values is not None else None
    def SetDesiredReferenceData(self,ident,path,path_type,loaded):
        self.desired[str(ident.Value)]=[path,path_type,bool(loaded)]


class Document(Obj):
    def __init__(self,runtime,path,model):
        self.runtime=runtime;self.PathName=path;self.model=copy.deepcopy(model)
        self.IsWorkshared=model['workshared']
        self.IsModified=False
        # Reading TD is not materialization. Only the simulated Revit open
        # changes actual reference element data, and only SaveAs persists it.
        if model.get('transmitted') and not runtime.ignore_desired:
            self.model['saved'].update(copy.deepcopy(model.get('desired',{})))
    def GetElement(self,ident):
        key=str(ident.Value)
        if key not in self.model['saved']:return None
        return Obj(Path=self.model['saved'][key][0],PathType=self.model['saved'][key][1],
                   Status=self.model.get('image_status',{}).get(key,'Loaded' if self.model['saved'][key][2] else 'Unloaded'),
                   GetExternalFileReference=lambda:Reference(self.model['saved'][key],self.PathName))
    def SaveAs(self,target,options):
        ws=getattr(options,'worksharing',None)
        if self.IsWorkshared:
            if ws is None or not ws.SaveAsCentral:raise RuntimeError('Detached workshared save must establish a central')
            self.model['central']=target;self.model['is_central']=True
            if getattr(ws,'ClearTransmitted',False):self.model['transmitted']=False
        else:self.model['transmitted']=False
        self.model['desired']={}
        self.PathName=target
        self.runtime.events.append(('saveas',target,bool(getattr(ws,'ClearTransmitted',False))))
        self.runtime.write(target,self.model)
        self.IsModified=self.runtime.modified_after_save
    def Save(self):
        self.runtime.events.append(('save',self.PathName))
        self.runtime.write(self.PathName,self.model)
        self.IsModified=self.runtime.modified_after_save
    def Close(self,save):
        self.runtime.events.append(('close',self.PathName,save));return True


class Runtime(object):
    def __init__(self):self.events=[];self.ignore_desired=False;self.modified_after_save=False
    def read(self,path):
        with open(path,'r') as stream:return json.load(stream)
    def write(self,path,model):
        with open(path,'w') as stream:json.dump(model,stream)
    def read_td(self,path):
        model=self.read(path)
        return Transmission(model) if model.get('has_td',True) else None
    def write_td(self,path,td):
        model=self.read(path)
        model.update(desired=copy.deepcopy(td.desired),transmitted=bool(td.IsTransmitted))
        self.events.append(('metadata',path,td.IsTransmitted,
                            [values[1] for values in td.desired.values()]))
        self.write(path,model)
    def basic(self,path):
        model=self.read(path)
        return Obj(Format='2024',IsWorkshared=model['workshared'],
                   IsCentral=model.get('is_central',False),CentralPath=model.get('central',''))
    def open(self,path,options):
        self.events.append(('open',path,getattr(options,'DetachFromCentralOption',None)))
        model=self.read(path)
        if getattr(options,'DetachFromCentralOption',None)=='Discard':
            model.update(workshared=False,is_central=False,central='')
        return Document(self,path,model)


class IndependentHostAPI(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET225_normal_');self.addCleanup(shutil.rmtree,self.root)
        self.stage=os.path.join(self.root,'stage.rvt');self.target=os.path.join(self.root,'Host.rvt')
        self.runtime=Runtime()
        self.model=dict(workshared=True,is_central=False,central='N:/Original/Host.rvt',
                        saved={'1':['N:/Original/Architecture.rvt','Absolute',False],
                               '2':['N:/Original/site.dxf','Absolute',True]},
                        desired={},transmitted=False)
        self.runtime.write(self.stage,self.model)
        class SaveOptions(Obj):
            def SetWorksharingOptions(self,options):self.worksharing=options
        self.db=Obj(ModelPathUtils=Obj(ConvertUserVisiblePathToModelPath=lambda value:value,
                                      ConvertModelPathToUserVisiblePath=lambda value:value),
                    BasicFileInfo=Obj(Extract=self.runtime.basic),
                    TransmissionData=Obj(ReadTransmissionData=self.runtime.read_td,
                                         WriteTransmissionData=self.runtime.write_td,
                                         IsDocumentTransmitted=lambda path:bool(self.runtime.read(path)['transmitted'])),
                    PathType=Obj(Absolute='Absolute',Relative='Relative'),
                    SaveAsOptions=SaveOptions,WorksharingSaveAsOptions=Obj,
                    OpenOptions=lambda:Obj(SetOpenWorksetsConfiguration=lambda value:None),
                    WorksetConfiguration=lambda value:Obj(),
                    WorksetConfigurationOption=Obj(OpenAllWorksets='All'),
                    DetachFromCentralOption=Obj(DetachAndPreserveWorksets='Preserve',DetachAndDiscardWorksets='Discard'),
                    ElementId=lambda value:Obj(Value=value),
                    RevitLinkType=Obj(IsLoaded=lambda doc,ident:bool(doc.model['saved'][str(ident.Value)][2])),
                    ExternalFileUtils=Obj(GetExternalFileReference=lambda doc,ident:Reference(doc.model['saved'][str(ident.Value)],doc.PathName)))
        self.app=Obj(VersionNumber='2024',OpenDocumentFile=self.runtime.open)
        self.backend=revit.Backend(self.db,self.app,self.root)
        self.rows=[dict(id='1',element_id='1',kind='RevitLink',special='native',td=True,
                        source='N:/Original/Architecture.rvt',loaded=False,
                        target=os.path.join(self.root,'Links','Architecture.rvt')),
                   dict(id='2',element_id='2',kind='CADLink',special='native',td=True,
                        source='N:/Original/site.dxf',loaded=True,
                        target=os.path.join(self.root,'CAD','site.dxf'))]
        self.options=dict(repath=True,independent_host=True,simple_repath=True,cleanup=False,upgrade=False)
        old_system=sys.modules.get('System')
        system=types.ModuleType('System');system.Int64=int;system.Int32=int
        sys.modules['System']=system
        def restore_system():
            if old_system is None:sys.modules.pop('System',None)
            else:sys.modules['System']=old_system
        self.addCleanup(restore_system)

    def test_two_opens_materialize_relative_native_paths_before_clearing_transmitted(self):
        result=self.backend.finish(self.stage,self.target,self.rows,self.options)
        output=self.runtime.read(self.target)
        self.assertTrue(output['workshared']);self.assertTrue(output['is_central'])
        self.assertEqual(output['central'],self.target)
        self.assertFalse(output['transmitted']);self.assertEqual(output['desired'],{})
        self.assertEqual(output['saved']['1'],[os.path.join('Links','Architecture.rvt'),'Relative',False])
        self.assertEqual(output['saved']['2'],[os.path.join('CAD','site.dxf'),'Relative',True])
        self.assertEqual([event[1] for event in self.runtime.events if event[0]=='open'],[self.stage,self.target])
        self.assertEqual([event[2] for event in self.runtime.events if event[0]=='open'],['Preserve','Preserve'])
        self.assertEqual([event[2] for event in self.runtime.events if event[0]=='saveas'],[True,True])
        self.assertEqual([event[3] for event in self.runtime.events if event[0]=='metadata'],[['Absolute','Absolute'],['Relative','Relative']])
        self.assertEqual(self.runtime.events[-2][0],'saveas');self.assertEqual(self.runtime.events[-1][0],'close')
        self.assertTrue(result['host_finalized']);self.assertTrue(result['saved_references_checked'])
        self.assertEqual([row['verification'] for row in self.rows],['SAVED_REFERENCE_CHECKED']*2)
        self.assertEqual(self.runtime.read(self.stage)['saved'],self.model['saved'])

    def test_clearing_flag_without_consuming_desired_references_cannot_pass(self):
        self.runtime.ignore_desired=True
        with self.assertRaises(RuntimeError):
            self.backend.finish(self.stage,self.target,self.rows,self.options)
        # The engine must restore its backup after this rejected intermediate
        # file; it must never advertise this state as a finalized host.
        self.assertTrue(self.runtime.read(self.target)['transmitted'])
        self.assertEqual(self.runtime.read(self.target)['saved'],self.model['saved'])

    def test_saved_verification_ignores_correct_desired_data_when_saved_paths_are_wrong(self):
        model=copy.deepcopy(self.model)
        model.update(central=self.target,is_central=True,
                     desired={'1':[os.path.join('Links','Architecture.rvt'),'Relative',False]})
        self.runtime.write(self.target,model)
        self.rows[0]['repath']='TRANSMISSION_DATA'
        with self.assertRaises(RuntimeError):
            self.backend.verify_independent_package(self.target,self.rows[:1],self.options)

    def test_repath_off_still_normalizes_workshared_host_without_transmission_writes(self):
        options=dict(self.options,repath=False)
        self.assertTrue(self.backend.requires_final_host_open(dict(is_primary_host=True,is_workshared=True),[],options))
        result=self.backend.finish(self.stage,self.target,[],options)
        output=self.runtime.read(self.target)
        self.assertFalse(output['transmitted']);self.assertEqual(output['central'],self.target)
        self.assertEqual(output['saved'],self.model['saved'])
        self.assertEqual([event[0] for event in self.runtime.events],['open','saveas','close'])
        self.assertFalse([event for event in self.runtime.events if event[0]=='saveas'][0][2])
        self.assertTrue(result['host_finalized'])

    def test_repath_off_clears_an_already_transmitted_source_copy_on_save(self):
        self.model['transmitted']=True;self.runtime.write(self.stage,self.model)
        self.backend.finish(self.stage,self.target,[],dict(self.options,repath=False))
        self.assertFalse(self.runtime.read(self.target)['transmitted'])
        self.assertTrue([event for event in self.runtime.events if event[0]=='saveas'][0][2])

    def test_nonworkshared_host_without_td_or_references_can_finalize(self):
        self.model.update(workshared=False,is_central=False,central='',has_td=False,saved={})
        self.runtime.write(self.stage,self.model)
        result=self.backend.finish(self.stage,self.target,[],self.options)
        self.assertTrue(result['host_finalized']);self.assertTrue(result['saved_references_checked'])
        self.assertFalse(self.runtime.read(self.target)['workshared'])

    def test_an_original_central_association_is_not_reported_as_finalized(self):
        self.runtime.write(self.target,self.model)
        with self.assertRaises(RuntimeError):
            self.backend.verify_independent_package(self.target,[],dict(repath=False))

    def test_upgrade_consent_failure_is_not_a_successful_result(self):
        self.app.VersionNumber='2025'
        with self.assertRaises(RuntimeError):
            self.backend.finish(self.stage,self.target,[],self.options)
        self.assertEqual(self.runtime.events,[])

    def test_save_callback_changes_prevent_a_finalized_claim(self):
        self.runtime.modified_after_save=True
        with self.assertRaises(RuntimeError):
            self.backend.finish(self.stage,self.target,self.rows,self.options)
        self.assertEqual([event[0] for event in self.runtime.events],['metadata','open','saveas','close'])

    def test_authorized_cleanup_discard_removes_worksharing(self):
        from easybim_etransmit import cleanup
        old_run=cleanup.run;cleanup.run=lambda *args:None
        self.addCleanup(setattr,cleanup,'run',old_run)
        result=self.backend.finish(self.stage,self.target,self.rows,
                                   dict(self.options,cleanup=True,discard_worksets=True))
        self.assertFalse(self.runtime.read(self.target)['workshared'])
        self.assertFalse(self.runtime.read(self.target)['transmitted'])
        self.assertEqual([event[2] for event in self.runtime.events if event[0]=='open'],['Discard',None])
        self.assertTrue(result['host_finalized'])

    def test_api_only_image_without_td_is_checked_after_save(self):
        self.model.update(workshared=False,central='',has_td=False,
                          saved={'1':['old.pdf','Absolute',True]})
        self.runtime.write(self.stage,self.model)
        row=dict(id='1',element_id='1',kind='Image',special='image',td=False,
                 source='old.pdf',target=os.path.join(self.root,'PDF','A.pdf'),loaded=True)
        def repair(doc,rows):
            doc.model['saved']['1']=[os.path.join('PDF','A.pdf'),'Relative',True]
            rows[0]['repath']='API_IMAGE_RELATIVE'
            return [],True
        self.backend.repath_images=repair
        result=self.backend.finish(self.stage,self.target,[row],self.options)
        self.assertEqual(row['verification'],'PATH_CHECKED')
        self.assertEqual([event[0] for event in self.runtime.events],['open','saveas','save','close'])
        self.assertTrue(result['saved_references_checked'])

    def test_api_only_image_reported_successful_at_wrong_path_is_rejected(self):
        self.model.update(workshared=False,central='',has_td=False,
                          saved={'1':['old.pdf','Absolute',True]})
        self.runtime.write(self.stage,self.model)
        row=dict(id='1',element_id='1',kind='Image',special='image',td=False,
                 source='old.pdf',target=os.path.join(self.root,'PDF','A.pdf'),loaded=True)
        def repair(doc,rows):
            rows[0]['repath']='API_IMAGE_RELATIVE'
            return [],True
        self.backend.repath_images=repair
        with self.assertRaises(RuntimeError):
            self.backend.finish(self.stage,self.target,[row],self.options)

    def test_api_image_at_correct_location_must_be_relative_and_have_expected_load_state(self):
        for path_type,status,loaded in (('Absolute','Loaded',True),
                                        ('Relative','FailedToLoad',True),
                                        ('Relative','Loaded',False),
                                        ('Relative','Unloaded',True)):
            self.model.update(workshared=False,central='',has_td=False,
                              saved={'1':['old.pdf','Absolute',True]})
            self.runtime.write(self.stage,self.model)
            row=dict(id='1',element_id='1',kind='Image',special='image',td=False,
                     source='old.pdf',target=os.path.join(self.root,'PDF','A.pdf'),loaded=loaded)
            def repair(doc,rows):
                path=rows[0]['target'] if path_type=='Absolute' else os.path.join('PDF','A.pdf')
                doc.model['saved']['1']=[path,path_type,status=='Loaded']
                doc.model['image_status']={'1':status}
                rows[0]['repath']='API_IMAGE_RELATIVE'
                return [],True
            self.backend.repath_images=repair
            with self.assertRaises(RuntimeError):
                self.backend.finish(self.stage,self.target,[row],self.options)

    def test_api_only_revit_link_at_correct_location_must_be_relative(self):
        self.model.update(workshared=False,central='',has_td=False,
                          saved={'1':['N:/Original/Architecture.rvt','Absolute',False]})
        self.runtime.write(self.stage,self.model)
        row=dict(self.rows[0],td=False,special='external')
        def repair(doc,rows):
            doc.model['saved']['1']=[rows[0]['target'],'Absolute',False]
            rows[0]['repath']='API_LOCAL_LINK_RELATIVE'
            return [],True
        self.backend._repath_external_revit_links_relative=repair
        with self.assertRaises(RuntimeError):
            self.backend.finish(self.stage,self.target,[row],self.options)

    def test_api_cad_without_final_saved_metadata_cannot_claim_portable_success(self):
        self.model.update(workshared=False,central='',has_td=False,
                          saved={'2':['N:/Original/site.dwg','Absolute',True]})
        self.runtime.write(self.stage,self.model)
        row=dict(self.rows[1],td=False,source='N:/Original/site.dwg',
                 target=os.path.join(self.root,'CAD','site.dwg'))
        def repair(doc,rows):
            doc.model['saved']['2']=[rows[0]['target'],'Absolute',True]
            rows[0]['repath']='API_CAD_LINK'
            return []
        self.backend.repath_cad_links=repair
        with self.assertRaises(RuntimeError) as raised:
            self.backend.finish(self.stage,self.target,[row],self.options)
        self.assertIn('no saved metadata',str(raised.exception))
        # The first API check allows an absolute staging path; the final
        # saved-reference check refuses to label it a portable deliverable.
        self.assertEqual(row['verification'],'PATH_CHECKED')
        self.assertFalse(self.runtime.read(self.target)['transmitted'])
        self.assertEqual([event[0] for event in self.runtime.events],['open','saveas','save','close'])

    def test_nonworkshared_transmitted_copy_without_repath_is_saved_normally(self):
        self.model.update(workshared=False,central='',transmitted=True)
        self.runtime.write(self.stage,self.model)
        result=self.backend.finish(self.stage,self.target,[],dict(self.options,repath=False))
        self.assertTrue(result['host_finalized'])
        self.assertFalse(self.runtime.read(self.target)['transmitted'])
        self.assertEqual([event[0] for event in self.runtime.events],['open','saveas','close'])

    def test_cleanup_removed_api_image_is_not_reported_as_repaired(self):
        row=dict(id='1',element_id='1',kind='Image',special='image',
                 target=os.path.join(self.root,'PDF','A.pdf'),repath='API_IMAGE_RELATIVE')
        issues=self.backend._verify_document(Obj(GetElement=lambda ident:None),
                                             self.target,[row],dict(cleanup=True))
        self.assertEqual(issues,[]);self.assertEqual(row['repath'],'REMOVED_BY_CLEANUP')
        self.assertNotIn('verification',row)


if __name__=='__main__':unittest.main(verbosity=2)
