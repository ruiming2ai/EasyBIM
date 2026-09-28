# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io
import os
import sys
import tempfile
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
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=self.tmp.name;self.target=os.path.join(self.root,'Host.rvt')
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


class EngineTransmittedDelivery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=self.tmp.name;self.src=os.path.join(self.root,'Host.rvt');self.out=os.path.join(self.root,'out')
        with io.open(self.src,'wb') as out:out.write(b'host')
        self.calls=[]
        parent=self
        class Backend(object):
            def scan(self,source,stage,options): return dict(references=[],issues=[],version='2024',is_workshared=True)
            def mark_transmitted_package(self,target): parent.calls.append(target);return True
        self.backend=Backend();self.opts=f.defaults();self.opts.update(repath=False,cleanup=False,upgrade=False,per_model=False)

    def test_collect_only_marks_only_final_primary_host_and_reports_transmitted_opening(self):
        result=engine.transmit([self.src],self.out,self.backend,self.opts)
        host=result['files'][0]
        self.assertEqual(self.calls,[host['target']])
        self.assertEqual(host.get('transmission_status'),'TRANSMITTED')
        self.assertEqual(host.get('opening_guidance'),'OPEN_AS_TRANSMITTED_MODEL')
        with io.open(os.path.join(self.out,'START_HERE.txt'),encoding='utf-8') as inp:text=inp.read()
        self.assertIn('Transmission status: TRANSMITTED',text)
        self.assertIn('opens detached',text.lower())

    def test_mark_failure_keeps_detach_guidance(self):
        self.backend.mark_transmitted_package=lambda target:False
        result=engine.transmit([self.src],self.out,self.backend,self.opts)
        host=result['files'][0]
        self.assertEqual(host.get('transmission_status'),'TRANSMIT_UNAVAILABLE')
        self.assertEqual(host.get('opening_guidance'),'DETACH_RECOMMENDED_FOR_WORKSHARED_COPY')
        self.assertTrue(any(i['code']=='WORKSHARING_COPY_NOT_TRANSMITTED' for i in result['issues']))

    def test_expected_transmission_metadata_change_is_not_reported_as_host_corruption(self):
        original=f.digest(self.src)
        def mark(target):
            with io.open(target,'ab') as out: out.write(b'-transmitted-metadata')
            return True
        self.backend.mark_transmitted_package=mark
        result=engine.transmit([self.src],self.out,self.backend,self.opts)
        host=result['files'][0]
        self.assertEqual(f.digest(self.src),original)
        self.assertNotEqual(host['packaged_sha256'],host['sha256'])
        self.assertFalse(any(i['code']=='FINAL_VERIFICATION_FAILED' for i in result['issues']))


if __name__=='__main__': unittest.main(verbosity=2)
