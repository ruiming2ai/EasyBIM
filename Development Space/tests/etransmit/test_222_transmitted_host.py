# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io
import os
import sys
import tempfile
import shutil
import unittest

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import engine, files as f, revit


class Obj(object):
    def __init__(self,**kw): self.__dict__.update(kw)


class Ref(object):
    def __init__(self,path,path_type,status):
        self._path=path;self.PathType=path_type;self._status=status
    def GetPath(self): return self._path
    def GetLinkedFileStatus(self): return self._status


class TD(object):
    def __init__(self):
        self.IsTransmitted=False;self.desired=[];self.disposed=False
        self.refs={
            'loaded':Ref('N:/Project/Loaded.rvt','Absolute','Loaded'),
            'unloaded':Ref('../Links/Unloaded.rvt','Relative','Unloaded')
        }
    def GetAllExternalFileReferenceIds(self): return ['loaded','unloaded']
    def GetLastSavedReferenceData(self,ident): return self.refs[ident]
    def GetDesiredReferenceData(self,ident): return None
    def SetDesiredReferenceData(self,*args): self.desired.append(args)
    def Dispose(self): self.disposed=True


class TransmittedBoundary(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_222_');self.addCleanup(shutil.rmtree,self.root);self.target=os.path.join(self.root,'Host.rvt')
        with io.open(self.target,'wb') as out: out.write(b'host-bytes')
        self.td=TD();self.read=[];self.writes=[];self.transmitted=set()
        def read(path): self.read.append(path);return self.td
        def write(path,td): self.writes.append(path);self.transmitted.add(path)
        def check(path): return path in self.transmitted and bool(self.td.IsTransmitted)
        self.db=Obj(
            ModelPathUtils=Obj(ConvertUserVisiblePathToModelPath=lambda p:p,
                               ConvertModelPathToUserVisiblePath=lambda p:p),
            TransmissionData=Obj(ReadTransmissionData=read,WriteTransmissionData=write,
                                 IsDocumentTransmitted=check),
            PathType=Obj(Absolute='Absolute',Relative='Relative'))
        self.b=revit.Backend(self.db,Obj(VersionNumber='2024'),self.root)
        self.b.basic=lambda p:dict(version='2024',workshared=True,central='Autodesk Docs://Project/Host.rvt')

    def test_closed_package_host_is_marked_transmitted_preserving_file_references(self):
        self.b.open_copy=lambda *a: self.fail('marking transmitted must not open the host')
        self.assertTrue(self.b.mark_transmitted_package(self.target))
        self.assertEqual(self.read,[self.target])
        self.assertEqual(self.writes,[self.target])
        self.assertTrue(self.td.IsTransmitted);self.assertTrue(self.td.disposed)
        self.assertEqual(self.td.desired,[
            ('loaded','N:/Project/Loaded.rvt','Absolute',True),
            ('unloaded','../Links/Unloaded.rvt','Relative',False)])

    def test_missing_transmission_data_returns_false_without_changing_file(self):
        self.db.TransmissionData.ReadTransmissionData=lambda p:None
        before=f.digest(self.target)
        self.assertFalse(self.b.mark_transmitted_package(self.target))
        self.assertEqual(f.digest(self.target),before);self.assertEqual(self.writes,[])

    def test_transmitted_verification_must_pass(self):
        self.db.TransmissionData.IsDocumentTransmitted=lambda p:False
        self.assertFalse(self.b.mark_transmitted_package(self.target))

    def test_nonworkshared_host_is_not_marked(self):
        self.b.basic=lambda p:dict(version='2024',workshared=False,central='')
        self.assertIsNone(self.b.mark_transmitted_package(self.target))
        self.assertEqual(self.read,[]);self.assertEqual(self.writes,[])


class EngineIndependentDelivery(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_222_');self.addCleanup(shutil.rmtree,self.root);self.src=os.path.join(self.root,'Host.rvt');self.out=os.path.join(self.root,'out')
        with io.open(self.src,'wb') as out:out.write(b'host')
        self.calls=[]
        parent=self
        class Backend(object):
            independent_host_supported=True
            def scan(self,source,stage,options): return dict(references=[],issues=[],version='2024',is_workshared=True)
            def finish(self,stage,target,rows,options):
                parent.assertTrue(options.get('independent_host'))
                parent.assertEqual(rows,[])
                parent.assertTrue(os.path.isfile(stage))
                parent.assertTrue(os.path.isfile(target))
                parent.calls.append(target)
                return dict(issues=[],host_finalized=True,saved_references_checked=True,
                            independent_package_central=True,verified_in_process=False)
            def mark_transmitted_package(self,*args): parent.fail('primary hosts must remain ordinary independent models')
            def verify_package(self,*args): parent.fail('finalized hosts must not be opened again by the parent')
        self.backend=Backend();self.opts=f.defaults();self.opts.update(repath=False,cleanup=False,upgrade=False,per_model=False)

    def test_collect_only_finalizes_primary_host_and_reports_normal_independent_opening(self):
        result=engine.transmit([self.src],self.out,self.backend,self.opts)
        host=result['files'][0]
        self.assertEqual(self.calls,[host['target']])
        self.assertTrue(host.get('host_finalized'))
        self.assertTrue(host.get('saved_references_checked'))
        self.assertEqual(host.get('transmission_status'),'NOT_TRANSMITTED')
        self.assertEqual(host.get('model_verification'),'SAVED_REFERENCES_CHECKED')
        self.assertEqual(host.get('opening_guidance'),'OPEN_NORMALLY_INDEPENDENT_PACKAGE')
        self.assertIs(host.get('original_central_association_preserved'),False)
        with io.open(os.path.join(self.out,'START_HERE.txt'),encoding='utf-8') as inp:text=inp.read()
        self.assertIn('Transmission status: NOT_TRANSMITTED',text)
        self.assertNotIn('opens detached',text.lower())

    def test_finalization_failure_keeps_original_in_recovery_and_removes_delivery(self):
        def fail(*args):raise RuntimeError('package central cannot be saved')
        self.backend.finish=fail
        result=engine.transmit([self.src],self.out,self.backend,self.opts)
        host=result['files'][0]
        self.assertFalse(host.get('host_finalized'))
        self.assertEqual(host.get('transmission_status'),'NOT_FINALIZED')
        self.assertEqual(host.get('model_verification'),'FAILED')
        self.assertFalse(os.path.isfile(host['target']))
        self.assertTrue(os.path.isfile(host['recovery_path']))
        self.assertEqual(f.digest(host['recovery_path']),f.digest(self.src))
        self.assertTrue(any(i['code']=='MODEL_PROCESSING_FAILED' for i in result['issues']))

    def test_expected_independent_host_save_is_not_reported_as_host_corruption(self):
        original=f.digest(self.src)
        finish=self.backend.finish
        def save_independent(stage,target,rows,options):
            with io.open(target,'ab') as out: out.write(b'-independent-central')
            return finish(stage,target,rows,options)
        self.backend.finish=save_independent
        result=engine.transmit([self.src],self.out,self.backend,self.opts)
        host=result['files'][0]
        self.assertEqual(f.digest(self.src),original)
        self.assertNotEqual(host['packaged_sha256'],host['sha256'])
        self.assertFalse(any(i['code']=='FINAL_VERIFICATION_FAILED' for i in result['issues']))


if __name__=='__main__': unittest.main(verbosity=2)
