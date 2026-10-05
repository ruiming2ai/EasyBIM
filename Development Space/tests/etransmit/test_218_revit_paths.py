# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, tempfile, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f
from easybim_etransmit.revit import Backend
from test_212_cache import Obj

class FinalReferenceBase(unittest.TestCase):
    def test_metadata_processing_uses_final_host_base_without_opening_revit(self):
        root=tempfile.mkdtemp(prefix='ET218_Path_');self.addCleanup(f.remove_tree_retry,root)
        stage=os.path.join(root,'stage.rvt');prepared=os.path.join(root,'prepared.rvt')
        with open(stage,'wb') as out:out.write(b'original')
        final=os.path.join(root,'deliver','Host.rvt');link=os.path.join(root,'deliver','Links','Arch.rvt')
        writes=[]
        ident=Obj(IntegerValue=100)
        td=Obj(IsTransmitted=False,GetAllExternalFileReferenceIds=lambda:[ident],
               SetDesiredReferenceData=lambda *a:writes.append(a),Dispose=lambda:None)
        db=Obj(ModelPathUtils=Obj(ConvertUserVisiblePathToModelPath=lambda p:p),
               TransmissionData=Obj(ReadTransmissionData=lambda p:td,WriteTransmissionData=lambda *a:None),
               PathType=Obj(Relative='Relative',Absolute='Absolute'))
        backend=Backend(db,Obj(VersionNumber='2025'),root)
        backend.basic=lambda p:dict(version='2025',workshared=False,central='')
        backend.open_copy=lambda *a:self.fail('metadata-only repath must not open Revit')
        rows=[dict(id='100',element_id='100',kind='RevitLink',td=True,loaded=True,special='native',target=link)]
        opts=f.defaults();opts['reference_target']=final
        self.assertTrue(backend.can_deliver_direct(dict(copy_method='DIRECT'),rows,opts))
        backend.finish(stage,prepared,rows,opts)
        self.assertEqual(writes[0][1],os.path.join('Links','Arch.rvt'))
        with open(prepared,'rb') as inp:self.assertEqual(inp.read(),b'original')
    def test_api_image_and_cache_hosts_keep_short_path_preparation(self):
        backend=Backend(None,None,'unused');opts=f.defaults()
        self.assertFalse(backend.can_deliver_direct({},[dict(special='image')],opts))
        self.assertFalse(backend.can_deliver_direct(dict(copy_method='COLLABORATION_CACHE_READ_ONLY'),[],opts))

if __name__=='__main__':unittest.main()
