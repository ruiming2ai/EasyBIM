# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import importlib, io, os, sys, types, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'));sys.path.insert(0,os.path.join(ROOT,'lib'))
class Obj(object):
    def __init__(self,**kw):self.__dict__.update(kw)
class ProgressUI(unittest.TestCase):
    def setUp(self):
        self.positions=[];owner=self
        class Bar(object):
            def __init__(self,**kw):self.cancelled=False;self.title=kw.get('title','');self.user_height=32;self.Top=0;self.pbar=Obj(Foreground=None);self.update_window()
            def update_window(self):owner.positions.append(True);self.Top=100
            def update_progress(self,*a):self.update_window()
            def __enter__(self):return self
            def __exit__(self,*a):pass
        fake=types.ModuleType('pyrevit');fake.forms=Obj(WPFWindow=object,ProgressBar=Bar,alert=lambda *a,**k:None);fake.script=Obj(get_logger=lambda:Obj(warning=lambda *a:None));fake.DB=Obj(Document=Obj())
        self.old=sys.modules.get('pyrevit');self.old_ui=sys.modules.pop('easybim_etransmit.ui',None);sys.modules['pyrevit']=fake;self.ui=importlib.import_module('easybim_etransmit.ui')
    def tearDown(self):
        sys.modules.pop('easybim_etransmit.ui',None)
        if self.old_ui is not None:sys.modules['easybim_etransmit.ui']=self.old_ui
        if self.old is None:sys.modules.pop('pyrevit',None)
        else:sys.modules['pyrevit']=self.old
    def test_phase_mapper_uses_approved_colors(self):
        cases=[('Copying Architecture.rvt','COLLECTING','#2F80ED'),('Repath / cleanup Host.rvt','REPATHING','#2F80ED'),('FINALIZING ACC LINKS | Host.rvt','FINALIZING ACC LINKS','#8E44AD'),('PACKAGE BUILT — VERIFYING FINAL PACKAGE | Host.rvt','VERIFYING FINAL PACKAGE','#8E44AD'),('READY','READY','#27AE60'),('READY — REVIEW ISSUES','READY — REVIEW ISSUES','#F2C94C'),('INCOMPLETE','INCOMPLETE','#EB5757'),('CANCELLED','CANCELLED','#828282')]
        for label,phase,color in cases:
            actual=self.ui.progress_phase(label);self.assertEqual(actual[0],phase);self.assertEqual(actual[1],color)
    def test_bar_positions_once_below_revit_title_and_keeps_filename_in_text(self):
        bar=self.ui.TransferProgressBar(title='e-transmit',cancellable=True,indeterminate=True);self.assertEqual(bar.Top,132);bar.set_phase('PACKAGE BUILT — VERIFYING FINAL PACKAGE | Snowdon Towers Sample Electrical.rvt');self.assertIn('Snowdon Towers Sample Electrical.rvt',bar.title);bar.update_progress(1,2);bar.update_progress(2,2);self.assertEqual(len(self.positions),1,'host geometry must be read once only')
    def test_engine_has_explicit_finalization_and_verification_labels(self):
        with io.open(os.path.join(ROOT,'lib','easybim_etransmit','engine.py'),encoding='utf-8') as inp:text=inp.read()
        self.assertIn('FINALIZING ACC LINKS | ',text);self.assertIn('PACKAGE BUILT — VERIFYING FINAL PACKAGE | ',text)
if __name__=='__main__':unittest.main(verbosity=2)
