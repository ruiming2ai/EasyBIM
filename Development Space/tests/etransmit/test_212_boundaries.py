# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, unittest
from test_212_cache import Obj,P,M
import test_212_session as fixtures
from easybim_etransmit import session as s, files as f, engine

class SavedBoundary(fixtures.SavedCacheSession):
    def test_report_does_not_claim_a_working_SaveAs_or_unsaved_snapshot(self):
        key=self.r.add_live(self.doc);out=os.path.join(self.root,'report')
        opts=f.defaults();opts['repath']=False
        result=engine.transmit([key],out,s.SessionBackend(self.db,self.app,out,self.r),opts)
        with open(os.path.join(out,'START_HERE.txt')) as inp:text=inp.read()
        self.assertIn('Unsaved edits are excluded',text)
        self.assertIn('Host acquisition checksum',text)
        self.assertNotIn('_HostState',text)
        self.assertNotIn('Retained working document / SaveAs',text)
    def test_revit_open_must_never_return_a_working_document_for_processing(self):
        self.r.add_live(self.doc);b=s.SessionBackend(self.db,self.app,self.root,self.r)
        old=s.Backend.open_copy;s.Backend.open_copy=lambda *a,**k:self.doc
        try:
            with self.assertRaises(s.SourceError):b.open_copy(os.path.join(self.root,'owned.rvt'))
        finally:s.Backend.open_copy=old
    def test_saved_native_link_retains_cloud_identity_before_path_string_conversion(self):
        b=s.SessionBackend(self.db,self.app,self.root,self.r)
        mp=Obj(GetModelGUID=lambda:M,GetProjectGUID=lambda:P,Region='US',Dispose=lambda:None)
        b.visible=lambda p:'Autodesk Docs://Project/Arch.rvt'
        ref=Obj(GetPath=lambda:mp,GetAbsolutePath=lambda:mp,ExternalFileReferenceType='RevitLink',
                PathType='Cloud',GetLinkedFileStatus=lambda:'Loaded')
        row=b.reference_row(ref,Obj(Value=1),'owner')
        self.assertEqual(row.get('cloud_identity'),dict(model_guid=M,project_guid=P,region='US'))
    def test_saved_link_does_not_bind_to_different_unsaved_cloud_retarget(self):
        key=self.r.add_live(self.doc)
        entry=self.r.get(key);entry['inventory']['references']=[dict(element_id='1',source=key,kind='RevitLink')]
        row=dict(element_id='1',kind='RevitLink',source='Autodesk Docs://Project/Original.rvt',
                 cloud_identity=dict(model_guid='55555555-5555-4555-8555-555555555555',project_guid=P))
        b=s.SessionBackend(self.db,self.app,self.root,self.r)
        b._bind_saved_links(entry,dict(references=[row]))
        self.assertNotEqual(row['source'],key)
        self.assertEqual(self.r.get(row['source'])['cloud']['model_guid'],'55555555-5555-4555-8555-555555555555')
    def test_normalization_failure_restores_cached_host_without_extra_baseline(self):
        key=self.r.add_live(self.doc)
        class B(s.SessionBackend):
            def finish(inner,stage,target,*args):
                with open(target,'wb') as o:o.write(b'bad processing bytes')
                raise RuntimeError('failed to normalize copied RVT')
            def verify_package(inner,*a):self.fail('failed normalization must not pass verification')
        out=os.path.join(self.root,'normfail')
        result=engine.transmit([key],out,B(self.db,self.app,out,self.r),f.defaults())
        host=result['files'][0]
        self.assertEqual(host['processing_status'],'FAILED')
        self.assertEqual(f.digest(host['target']),f.digest(self.original))
        self.assertFalse(os.path.exists(os.path.join(out,'_HostState')))

for name in fixtures.SavedCacheSession.__dict__:
    if name.startswith('test_') and name not in SavedBoundary.__dict__:setattr(SavedBoundary,name,None)

if __name__=='__main__':unittest.main(verbosity=2)
