# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, tempfile, shutil, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import engine as e, files as f
from easybim_etransmit.revit import Backend

class Obj(object):
    def __init__(self,**kw): self.__dict__.update(kw)

class Safety(unittest.TestCase):
    def test_collision_namespace_uses_source_identity(self):
        from easybim_etransmit import layout
        rows=[dict(source='/A/drawing.pdf',category='pdf'),dict(source='/B/drawing.pdf',category='pdf')]
        layout.plan(rows,'categories')
        self.assertNotEqual(rows[0]['relative'],rows[1]['relative'])
        self.assertEqual(os.path.basename(rows[0]['relative']),'drawing.pdf')
        again=[dict(row) for row in reversed(rows)]
        layout.plan(again,'categories')
        self.assertEqual(rows[0]['relative'],again[1]['relative'])

    def test_imported_image_not_reintroduced_by_external_resources(self):
        ident=Obj(Value=42)
        resource=Obj(InSessionPath='/sources/embedded.pdf',ServerId='server',Version='',
                     GetReferenceInformation=lambda:{'Path':'/sources/embedded.pdf'})
        image=Obj(Id=ident,Source='Import',IsNestedLink=False,
                  GetExternalResourceReferences=lambda:{'IMAGE':resource})
        db=Obj(ExternalFileUtils=Obj(GetAllExternalFileReferences=lambda d:[]),
               ExternalResourceUtils=Obj(GetAllExternalResourceReferences=lambda d:[ident]),
               ExternalResourceTypes=Obj(BuiltInExternalResourceTypes=Obj(Image='IMAGE')),
               BuiltInCategory=Obj())
        b=Backend(db,Obj(),'/out')
        b.elements=lambda d,n:[image] if n=='ImageType' else []
        result=dict(references=[],issues=[])
        b.scan_open(Obj(GetElement=lambda i:image),'/host.rvt',dict(central='',workshared=False),result)
        self.assertEqual(result['references'],[])

    def test_deferred_is_not_failed_open(self):
        root=tempfile.mkdtemp(); self.addCleanup(shutil.rmtree,root)
        host=os.path.join(root,'Host.rvt')
        with open(host,'wb') as out: out.write(b'fake')
        class B(object):
            def scan(self,*a): return dict(references=[],issues=[],version='2024')
            def finish(self,stage,target,*a): shutil.copyfile(stage,target); return []
            def verify_package(self,*a):
                return [e.issue('MODEL_VERIFICATION_DEFERRED','Host','Missing link','error')]
        result=e.transmit([host],os.path.join(root,'out'),B())
        self.assertEqual(result['files'][0]['model_verification'],'DEFERRED')

if __name__=='__main__': unittest.main(verbosity=2)
