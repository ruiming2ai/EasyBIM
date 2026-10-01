# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, shutil, sys, tempfile, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'));sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import revit
class Obj(object):
    def __init__(self,**kw):self.__dict__.update(kw)
class RelativeRepath(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET219_rel_');self.addCleanup(shutil.rmtree,self.root);self.stage=os.path.join(self.root,'stage.rvt');self.host=os.path.join(self.root,'Host.rvt')
        with open(self.stage,'wb') as out:out.write(b'host')
        self.calls=[];ids=[Obj(Value=1),Obj(Value=2)]
        td=Obj(GetAllExternalFileReferenceIds=lambda:ids,SetDesiredReferenceData=lambda *a:self.calls.append(a),IsTransmitted=False)
        db=Obj(TransmissionData=Obj(ReadTransmissionData=lambda p:td,WriteTransmissionData=lambda *a:None),PathType=Obj(Relative='Relative',Absolute='Absolute'))
        self.b=revit.Backend(db,Obj(VersionNumber='2025'),self.root);self.b.mp=lambda p:p;self.b.basic=lambda p:dict(version='2025',workshared=False)
        self.b.open_copy=lambda *a:(_ for _ in ()).throw(AssertionError('metadata-only repath must not open Revit'))
    def rows(self):
        a=os.path.join(self.root,'Links','Revit','A','Architecture.rvt');b=os.path.join(self.root,'Links','Revit','B','Architecture.rvt')
        return [dict(id='1',element_id='1',kind='RevitLink',target=a,td=True,loaded=True,special='native',source='C:/A/Architecture.rvt'),dict(id='2',element_id='2',kind='RevitLink',target=b,td=True,loaded=True,special='native',source='D:/B/Architecture.rvt')]
    def test_same_name_links_keep_names_and_receive_distinct_relative_paths(self):
        rows=self.rows();self.b.apply_metadata(self.stage,self.host,rows,relative=True);self.assertEqual([os.path.basename(r['target']) for r in rows],['Architecture.rvt','Architecture.rvt'])
        values=[c[1] for c in self.calls];self.assertNotEqual(values[0],values[1]);self.assertTrue(values[0].replace('\\','/').endswith('Links/Revit/A/Architecture.rvt'));self.assertTrue(values[1].replace('\\','/').endswith('Links/Revit/B/Architecture.rvt'));self.assertEqual([c[2] for c in self.calls],['Relative','Relative'])
    def test_metadata_only_finish_does_not_open_or_save_document(self):
        rows=self.rows()[:1];result=self.b.finish(self.stage,self.host,rows,dict(repath=True));self.assertEqual(result['issues'],[]);self.assertTrue(result['metadata_repathed']);self.assertTrue(os.path.isfile(self.host));self.assertEqual(rows[0]['repath'],'TRANSMISSION_DATA')
if __name__=='__main__':unittest.main(verbosity=2)
