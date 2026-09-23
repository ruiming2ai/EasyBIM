"""Fake API lifecycle tests. Revit and IronPython desktop tests remain required."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'lib'))
from easybim_etransmit import revit as r, files as f, cleanup

class Id:
    def __init__(self,n): self.Value=n

class Tx:
    def __init__(self,*args): self.status='New'; self.rolled=False
    def Start(self): self.status='Started'
    def Commit(self): self.status='Committed'; return self.status
    def RollBack(self): self.rolled=True; self.status='RolledBack'
    def GetStatus(self): return self.status
    def Dispose(self): pass

class GenericList(list):
    @property
    def Count(self): return len(self)
    @classmethod
    def __class_getitem__(cls,key): return cls

class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'output';self.root.mkdir()
        self.stage=str(self.root/'stage.rvt');self.target=str(self.root/'final.rvt')
        Path(self.stage).write_bytes(b'saved')
        self.image_path=str(self.root/'details.pdf');Path(self.image_path).write_bytes(b'pdf')
        self.reloads=[];self.closed=[];self.saved=[]
        self.image=NS(ReloadFrom=lambda opt:self.reloads.append(vars(opt).copy()),Unload=lambda:None)
        self.doc=NS(IsWorkshared=False,GetElement=lambda ident:self.image,
                    Close=lambda save:self.closed.append(save) is None)
        def save_as(path,opts):
            self.assertTrue(f.within(path,str(self.root)))
            self.saved.append(path); Path(path).write_bytes(b'new saved model')
        self.doc.SaveAs=save_as;self.doc.Save=lambda:self.saved.append(self.target)
        self.db=NS(ImageTypeSource=NS(Link='Link'),ImageTypeOptions=lambda path,relative,source:NS(Path=path,Relative=relative),
                   SaveAsOptions=lambda:NS(),ElementId=lambda n:Id(n),Transaction=Tx,
                   TransactionStatus=NS(Started='Started',Committed='Committed'),
                   TransmissionData=NS(ReadTransmissionData=lambda p:None),
                   ModelPathUtils=NS(ConvertUserVisiblePathToModelPath=lambda p:p))
        self.backend=r.Backend(self.db,NS(VersionNumber='2026'),str(self.root))
        self.backend.basic=lambda path:dict(version='2026',workshared=False,central='')
        self.backend.open_copy=lambda *args:self.doc
        self.row=dict(id='200',element_id='200',special='image',kind='PDF',page=3,resolution=240,
                      target=self.image_path,source='original.pdf',loaded=True)
    def test_image_save_lifecycle_and_page_resolution(self):
        with patch.dict(sys.modules,{'System':NS(Int32=int,Int64=int)}):
            issues=self.backend.finish(self.stage,self.target,[self.row],f.defaults())
        self.assertEqual(self.closed,[False]);self.assertEqual(self.saved,[self.target,self.target])
        self.assertEqual([x['Relative'] for x in self.reloads],[False,True])
        self.assertTrue(all(x['Resolution']==240 and x['PageNumber']==3 for x in self.reloads))
        self.assertEqual(self.row['repath'],'API_IMAGE_RELATIVE');self.assertEqual(issues,[])
    def test_save_failure_closes_temporary_document(self):
        def fail(*args): raise IOError('save failed')
        self.doc.SaveAs=fail
        with patch.dict(sys.modules,{'System':NS(Int32=int,Int64=int)}):
            with self.assertRaises(IOError):self.backend.finish(self.stage,self.target,[self.row],f.defaults())
        self.assertEqual(self.closed,[False]);self.assertFalse(Path(self.target).exists())
    def test_repath_disabled_copies_saved_bytes_without_open(self):
        self.backend.open_copy=lambda *args:self.fail('Disabled cleanup/repath must not open document')
        opts=f.defaults();opts['repath']=False
        self.backend.finish(self.stage,self.target,[self.row],opts)
        self.assertEqual(Path(self.target).read_bytes(),b'saved')
    def test_purge_cascade_rolls_back_retained_view(self):
        view=NS(Id=Id(1),ViewType='FloorPlan',IsTemplate=False,GetPrimaryViewId=lambda:Id(-1))
        class Collector:
            def __init__(self,doc):self.kind=None
            def OfClass(self,kind):self.kind=kind;return self
            def __iter__(self):return iter([view] if self.kind=='View' else [])
            def Dispose(self):pass
        tx=Tx()
        db=NS(FilteredElementCollector=Collector,Viewport='Viewport',ScheduleSheetInstance='Schedule',View='View',
              ElementId=Id,Transaction=lambda *args:tx,TransactionStatus=NS(Started='Started',Committed='Committed'))
        doc=NS(GetUnusedElements=lambda cats:[Id(9)],Delete=lambda ids:[Id(9),Id(1)])
        modules={'System.Collections.Generic':NS(List=GenericList,HashSet=GenericList)}
        with patch.dict(sys.modules,modules):
            with self.assertRaisesRegex(RuntimeError,'cascade'): cleanup.run(doc,db,dict(views='all',purge=True))
        self.assertTrue(tx.rolled)

if __name__=='__main__':unittest.main()
