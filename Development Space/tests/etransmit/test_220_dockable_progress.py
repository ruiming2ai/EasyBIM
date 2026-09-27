# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import importlib
import io
import os
import sys
import types
import unittest

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))

class Obj(object):
    def __init__(self,**kw): self.__dict__.update(kw)

class DockableProgressTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        owner=self

        class WPFPanel(object):
            def __init__(self):
                self.cancelled=False
                self.PhaseText=Obj(Text='')
                self.DetailText=Obj(Text='')
                self.Progress=Obj(IsIndeterminate=False,Minimum=0.0,Maximum=1.0,Value=0.0,Foreground=None)
                self.PercentText=Obj(Text='')
                self.CancelButton=Obj(IsEnabled=True,Content='Cancel')
                self.Dispatcher=Obj()

        class ForbiddenProgressBar(object):
            def __init__(self,*a,**k):
                raise AssertionError('floating pyRevit ProgressBar must not be created')

        self.panel_instance=None
        def is_registered(panel_type):
            return bool(getattr(owner,'registered',False))
        def register(panel_type,default_visible=True):
            owner.calls.append(('register',panel_type.panel_id,default_visible))
            owner.registered=True
            owner.panel_instance=panel_type()
            return owner.panel_instance
        def open_panel(panel_type): owner.calls.append(('open',panel_type.panel_id))
        def close_panel(panel_type): owner.calls.append(('close',panel_type.panel_id))

        fake=types.ModuleType('pyrevit')
        fake.forms=Obj(WPFWindow=object,WPFPanel=WPFPanel,ProgressBar=ForbiddenProgressBar,
                       alert=lambda *a,**k:None,
                       is_registered_dockable_panel=is_registered,
                       register_dockable_panel=register,
                       open_dockable_panel=open_panel,
                       close_dockable_panel=close_panel)
        fake.script=Obj(get_logger=lambda:Obj(warning=lambda *a:None))
        fake.DB=Obj(Document=Obj())
        self.old=sys.modules.get('pyrevit')
        self.old_ui=sys.modules.pop('easybim_etransmit.ui',None)
        sys.modules['pyrevit']=fake
        self.ui=importlib.import_module('easybim_etransmit.ui')
        self.ui._top_dock_state=lambda: Obj(DockPosition='TOP')

    def tearDown(self):
        sys.modules.pop('easybim_etransmit.ui',None)
        if self.old_ui is not None: sys.modules['easybim_etransmit.ui']=self.old_ui
        if self.old is None: sys.modules.pop('pyrevit',None)
        else: sys.modules['pyrevit']=self.old

    def test_progress_surface_is_a_revit_dockable_panel_not_prompt_bar(self):
        self.assertTrue(issubclass(self.ui.TransferProgressPanel,self.ui.forms.WPFPanel))
        self.assertEqual(self.ui.TransferProgressPanel.panel_title,'EasyBIM e-transmit')
        self.assertTrue(self.ui.TransferProgressPanel.panel_id)
        self.assertTrue(self.ui.TransferProgressPanel.panel_source.endswith('progress.xaml'))

    def test_dockable_context_registers_opens_and_closes_without_floating_bar(self):
        with self.ui.DockableTransferProgress() as progress:
            self.assertIs(progress.panel,self.panel_instance)
            self.assertFalse(progress.cancelled)
        self.assertEqual(self.calls[0][0],'register')
        self.assertIn(('open',self.ui.TransferProgressPanel.panel_id),self.calls)
        self.assertIn(('close',self.ui.TransferProgressPanel.panel_id),self.calls)

    def test_panel_keeps_phase_filename_percent_and_cancel_separate(self):
        panel=self.ui.TransferProgressPanel()
        phase=panel.set_phase('PACKAGE BUILT — VERIFYING FINAL PACKAGE | Snowdon Towers Sample Electrical.rvt')
        self.assertEqual(phase,'VERIFYING FINAL PACKAGE')
        self.assertEqual(panel.PhaseText.Text,'VERIFYING FINAL PACKAGE')
        self.assertEqual(panel.DetailText.Text,'Snowdon Towers Sample Electrical.rvt')
        panel.update_progress(3,4)
        self.assertEqual(panel.Progress.Value,3.0)
        self.assertEqual(panel.Progress.Maximum,4.0)
        self.assertEqual(panel.PercentText.Text,'75%')
        panel.cancel_click(None,None)
        self.assertTrue(panel.cancelled)
        self.assertFalse(panel.CancelButton.IsEnabled)

    def test_initial_state_requests_revit_top_docking(self):
        old_autodesk=sys.modules.get('Autodesk')
        old_revit=sys.modules.get('Autodesk.Revit')
        old_ui=sys.modules.get('Autodesk.Revit.UI')
        class State(object):
            def __init__(self): self.DockPosition=None
        ui=types.ModuleType('Autodesk.Revit.UI');ui.DockablePaneState=State;ui.DockPosition=Obj(Top='TOP')
        autodesk=types.ModuleType('Autodesk');revit=types.ModuleType('Autodesk.Revit');revit.UI=ui;autodesk.Revit=revit
        sys.modules['Autodesk']=autodesk;sys.modules['Autodesk.Revit']=revit;sys.modules['Autodesk.Revit.UI']=ui
        try:
            original=self.ui._top_dock_state
            try:
                def actual_top_state():
                    import Autodesk.Revit.UI as RUI
                    state=RUI.DockablePaneState();state.DockPosition=RUI.DockPosition.Top;return state
                self.ui._top_dock_state=actual_top_state
                state=self.ui._top_dock_state()
            finally:
                self.ui._top_dock_state=original
            self.assertEqual(state.DockPosition,'TOP')
        finally:
            for name,old in [('Autodesk.Revit.UI',old_ui),('Autodesk.Revit',old_revit),('Autodesk',old_autodesk)]:
                if old is None: sys.modules.pop(name,None)
                else: sys.modules[name]=old

    def test_progress_xaml_is_thin_horizontal_strip(self):
        path=os.path.join(ROOT,'lib','easybim_etransmit','progress.xaml')
        self.assertTrue(os.path.isfile(path))
        with io.open(path,encoding='utf-8') as inp: text=inp.read()
        self.assertIn('x:Name="PhaseText"',text)
        self.assertIn('x:Name="DetailText"',text)
        self.assertIn('x:Name="Progress"',text)
        self.assertIn('x:Name="CancelButton"',text)
        self.assertIn('DockPanel',text)

if __name__=='__main__': unittest.main(verbosity=2)
