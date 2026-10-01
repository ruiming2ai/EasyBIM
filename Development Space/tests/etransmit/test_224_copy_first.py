# -*- coding: utf-8 -*-
from __future__ import unicode_literals
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
    def __init__(self,**kw): self.__dict__.update(kw)


class ElementId(object):
    def __init__(self,value): self.Value=int(value)


class Reference(object):
    def __init__(self,path,path_type,status):
        self.path=path;self.PathType=path_type;self.status=status
    def GetPath(self): return self.path
    def GetLinkedFileStatus(self): return self.status


class TransmissionSnapshot(object):
    """A fresh read keeps last-saved data separate from desired references."""
    def __init__(self,store):
        self.last=dict(store.last);self.desired=dict(store.desired)
        self.IsTransmitted=store.transmitted;self.disposed=False
    def GetAllExternalFileReferenceIds(self):
        return [ElementId(key) for key in sorted(self.last)]
    def GetLastSavedReferenceData(self,ident):
        return Reference(*self.last[str(ident.Value)])
    def GetDesiredReferenceData(self,ident):
        desired=self.desired.get(str(ident.Value))
        if desired is None:return None
        return Reference(desired[0],desired[1],'Loaded' if desired[2] else 'Unloaded')
    def SetDesiredReferenceData(self,ident,path,path_type,loaded):
        self.desired[str(ident.Value)]=(path,path_type,bool(loaded))
    def Dispose(self): self.disposed=True


class TransmissionStore(object):
    def __init__(self,last):
        self.last=dict(last);self.desired={};self.transmitted=False
        self.reads=[];self.writes=[]
    def read(self,path):
        snapshot=TransmissionSnapshot(self)
        self.reads.append(snapshot)
        return snapshot
    def write(self,path,snapshot):
        self.desired=dict(snapshot.desired)
        self.transmitted=bool(snapshot.IsTransmitted)
        self.writes.append(path)


class CopyFirstTransmission(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET224_copy_')
        self.addCleanup(shutil.rmtree,self.root)
        self.target=os.path.join(self.root,'Host.rvt')
        self.stage=os.path.join(self.root,'stage.rvt')
        self.cad=os.path.join(self.root,'Links','CAD','Plan.dwg')
        self.link=os.path.join(self.root,'Links','Revit','Architecture.rvt')
        self.relative_cad=os.path.join('Links','CAD','Plan.dwg')
        self.relative_link=os.path.join('Links','Revit','Architecture.rvt')
        for path in (self.target,self.stage,self.cad,self.link):
            directory=os.path.dirname(path)
            if not os.path.isdir(directory):os.makedirs(directory)
            with open(path,'wb') as out:out.write(b'packaged-file')
        self.last={
            '1':('N:/source/Plan.dwg','Absolute','Loaded'),
            '2':('N:/source/Architecture.rvt','Absolute','Loaded'),
            '3':('../Shared/Unknown.txt','Relative','Unloaded')}
        self.store=TransmissionStore(self.last)
        self.db=Obj(
            ModelPathUtils=Obj(ConvertUserVisiblePathToModelPath=lambda path:path,
                               ConvertModelPathToUserVisiblePath=lambda path:path),
            TransmissionData=Obj(ReadTransmissionData=self.store.read,
                                 WriteTransmissionData=self.store.write,
                                 IsDocumentTransmitted=lambda path:self.store.transmitted),
            PathType=Obj(Absolute='Absolute',Relative='Relative'),
            ElementId=ElementId)
        self.backend=revit.Backend(self.db,Obj(VersionNumber='2024'),self.root)
        self.backend.basic=lambda path:dict(version='2024',workshared=True)

    def native_rows(self):
        return [
            dict(id='1',element_id='1',kind='CADLink',td=True,special='native',
                 source='N:/source/Plan.dwg',target=self.cad,loaded=True),
            dict(id='2',element_id='2',kind='RevitLink',td=True,special='native',
                 source='N:/source/Architecture.rvt',target=self.link,
                 loaded=True,package_loaded=False)]

    def test_final_transmit_preserves_package_paths_after_untransmitted_metadata_write(self):
        rows=self.native_rows()
        self.assertTrue(self.backend.apply_metadata(
            self.target,self.target,rows,relative=True,mark_transmitted=False))
        self.assertFalse(self.store.transmitted)
        self.assertEqual(self.store.desired['1'],(self.relative_cad,'Relative',True))
        self.assertEqual(self.store.last['1'][0],'N:/source/Plan.dwg')

        self.assertTrue(self.backend.mark_transmitted_package(self.target,rows))

        self.assertEqual(self.store.desired['1'],(self.relative_cad,'Relative',True))
        self.assertEqual(self.store.desired['2'],(self.relative_link,'Relative',False))
        self.assertEqual(self.store.desired['3'],('../Shared/Unknown.txt','Relative',False))
        self.assertTrue(self.store.transmitted)
        self.assertEqual(len(self.store.reads),2)
        self.assertIsNot(self.store.reads[0],self.store.reads[1])
        self.assertTrue(all(snapshot.disposed for snapshot in self.store.reads))

    def test_final_transmit_can_overlay_copied_native_paths_without_worker_write(self):
        self.assertTrue(self.backend.mark_transmitted_package(self.target,self.native_rows()))
        self.assertEqual(self.store.desired['1'],(self.relative_cad,'Relative',True))
        self.assertEqual(self.store.desired['2'],(self.relative_link,'Relative',False))

    def test_failed_manual_and_skipped_rows_keep_original_reference_and_status(self):
        for state in ('FAILED','ROLLED_BACK','ROLLBACK_FAILED','REMOVED_BY_CLEANUP',
                      'MANUAL_REPAIR_REQUIRED','PLUGIN_RECONNECT_REQUIRED'):
            row=self.native_rows()[0]
            row['repath']=state
            self.store.desired={};self.store.transmitted=False
            self.assertTrue(self.backend.apply_metadata(
                self.target,self.target,[row],relative=True,mark_transmitted=False))
            self.assertEqual(row['repath'],state)
            self.assertNotIn('1',self.store.desired)
            self.assertTrue(self.backend.mark_transmitted_package(self.target,[row]))
            self.assertEqual(self.store.desired['1'],('N:/source/Plan.dwg','Absolute',True))
            self.assertEqual(row['repath'],state)

        row=self.native_rows()[0]
        row['skip_repath']=True
        self.store.desired={};self.store.transmitted=False
        self.backend.apply_metadata(self.target,self.target,[row],mark_transmitted=False)
        self.assertNotIn('repath',row)
        self.assertNotIn('1',self.store.desired)
        self.backend.mark_transmitted_package(self.target,[row])
        self.assertEqual(self.store.desired['1'],('N:/source/Plan.dwg','Absolute',True))

    def test_unconverted_external_or_cloud_link_keeps_saved_reference(self):
        cloud=dict(project_guid='11111111-1111-1111-1111-111111111111',
                   model_guid='22222222-2222-2222-2222-222222222222',region='US')
        for updates in (dict(cloud_identity=cloud),dict(td=False,special='external')):
            row=self.native_rows()[1]
            row.update(updates)
            row.pop('package_loaded')
            self.store.desired={};self.store.transmitted=False
            self.backend.apply_metadata(self.target,self.target,[row],mark_transmitted=False)
            self.assertNotIn('2',self.store.desired)
            self.assertNotIn('repath',row)
            self.backend.mark_transmitted_package(self.target,[row])
            self.assertEqual(self.store.desired['2'],('N:/source/Architecture.rvt','Absolute',True))

    def test_converted_cloud_link_retains_successful_relative_local_reference(self):
        row=self.native_rows()[1]
        row.update(cloud_identity=dict(
            project_guid='11111111-1111-1111-1111-111111111111',
            model_guid='22222222-2222-2222-2222-222222222222',region='US'),
            repath='API_LOCAL_LINK_RELATIVE')
        self.backend.mark_transmitted_package(self.target,[row])
        self.assertEqual(self.store.desired['2'],(self.relative_link,'Relative',False))

    def test_worker_api_failure_survives_metadata_and_final_transmit(self):
        try:import System
        except ImportError:
            system=types.ModuleType('System');system.Int64=int;system.Int32=int
            sys.modules['System']=system
            self.addCleanup(sys.modules.pop,'System',None)
        row=self.native_rows()[0]
        def load_failed(path):raise RuntimeError('CAD reload rejected')
        closed=[]
        doc=Obj(GetElement=lambda ident:Obj(LoadFrom=load_failed),
                Save=lambda:None,Close=lambda save:closed.append(save) or True)
        self.backend.open_copy=lambda *args:doc
        self.backend._save_as_independent_package_central=lambda document,path:shutil.copyfile(self.stage,path)

        result=self.backend.finish_independent(
            self.stage,self.target,[row],dict(repath=True,cleanup=False,upgrade=False))

        self.assertEqual([issue['code'] for issue in result['issues']],['CAD_REPATH_FAILED'])
        self.assertEqual(row['repath'],'FAILED')
        self.assertEqual(closed,[False])
        self.assertNotIn('1',self.store.desired)
        self.assertTrue(self.store.transmitted)
        self.backend.mark_transmitted_package(self.target,[row])
        self.assertEqual(row['repath'],'FAILED')
        self.assertEqual(self.store.desired['1'],('N:/source/Plan.dwg','Absolute',True))

    def test_worker_closed_metadata_applies_paths_for_workshared_and_nonworkshared_copies(self):
        try:import System
        except ImportError:
            system=types.ModuleType('System');system.Int64=int;system.Int32=int
            sys.modules['System']=system
            self.addCleanup(sys.modules.pop,'System',None)
        for workshared in (True,False):
            self.backend.basic=lambda path:dict(version='2024',workshared=workshared)
            self.store.desired={};self.store.transmitted=False
            rows=self.native_rows();closed=[];saved=[]
            doc=Obj(GetElement=lambda ident:Obj(LoadFrom=lambda path:Obj(LoadResult='LinkLoaded')),
                    Save=lambda:saved.append(True),Close=lambda save:closed.append(save) or True)
            self.backend.open_copy=lambda *args:doc
            self.backend._save_as_independent_package_central=lambda document,path:shutil.copyfile(self.stage,path)
            def write_after_close(path,snapshot):
                self.assertEqual(closed,[False])
                self.store.write(path,snapshot)
            self.db.TransmissionData.WriteTransmissionData=write_after_close

            result=self.backend.finish_independent(
                self.stage,self.target,rows,dict(repath=True,cleanup=False,upgrade=False))

            self.assertEqual(result['issues'],[])
            self.assertTrue(self.store.transmitted)
            self.assertEqual(self.store.desired['1'],(self.relative_cad,'Relative',True))
            self.assertEqual(self.store.desired['2'],(self.relative_link,'Relative',False))
            self.assertEqual(rows[0]['repath'],'API_CAD_LINK')
            self.assertEqual(saved,[True])


if __name__=='__main__':unittest.main(verbosity=2)
