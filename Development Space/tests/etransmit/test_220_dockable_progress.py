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

        self.registered=False
        self.panel_instance=None
        def is_registered(panel_type):
            return owner.registered
        def register(panel_type,default_visible=True):
            owner.calls.append(('register',panel_type.panel_id,default_visible))
            owner.registered=True
            owner.panel_instance=panel_type()
            return owner.panel_instance
        def open_panel(panel_id): owner.calls.append(('open',panel_id))
        def close_panel(panel_id): owner.calls.append(('close',panel_id))

        fake=types.ModuleType('pyrevit')
        fake.forms=Obj(WPFWindow=object,WPFPanel=WPFPanel,ProgressBar=ForbiddenProgressBar,
                       alert=lambda *a,**k:None,
                       is_registered_dockable_panel=is_registered,
                       register_dockable_panel=register,
                       open_dockable_panel=open_panel,
                       close_dockable_panel=close_panel)
        fake.script=Obj(get_logger=lambda:Obj(warning=lambda *a:None,info=lambda *a:None))
        fake.DB=Obj(Document=Obj())
        self.old=sys.modules.get('pyrevit')
        self.old_progress=sys.modules.pop('easybim.etransmit_progress_panel',None)
        sys.modules['pyrevit']=fake
        self.progress=importlib.import_module('easybim.etransmit_progress_panel')
        self.progress._top_dock_state=lambda:Obj(DockPosition='TOP',MinimumHeight=40,MinimumWidth=320)

    def tearDown(self):
        sys.modules.pop('easybim.etransmit_progress_panel',None)
        if self.old_progress is not None:sys.modules['easybim.etransmit_progress_panel']=self.old_progress
        if self.old is None:sys.modules.pop('pyrevit',None)
        else:sys.modules['pyrevit']=self.old

    def test_progress_surface_is_persistent_revit_dockable_panel(self):
        self.assertTrue(issubclass(self.progress.TransferProgressPanel,self.progress.forms.WPFPanel))
        self.assertEqual(self.progress.TransferProgressPanel.panel_title,'EasyBIM e-transmit')
        self.assertEqual(self.progress.TransferProgressPanel.panel_id,self.progress.PANEL_ID)
        self.assertTrue(self.progress.TransferProgressPanel.panel_source.endswith('etransmit_progress_panel.xaml'))

    def test_registration_happens_once_and_controller_only_opens_existing_pane(self):
        self.assertTrue(self.progress.register())
        self.assertEqual(len([c for c in self.calls if c[0]=='register']),1)
        with self.progress.ProgressController() as controller:
            self.assertTrue(controller.available)
            self.assertIs(controller.panel,self.panel_instance)
        self.assertEqual(len([c for c in self.calls if c[0]=='register']),1)
        self.assertIn(('open',self.progress.PANEL_ID),self.calls)
        self.assertIn(('close',self.progress.PANEL_ID),self.calls)

    def test_missing_registered_instance_degrades_to_noop_without_registration(self):
        self.progress._REGISTERED=True
        self.progress._PANEL_INSTANCE=None
        with self.progress.ProgressController() as controller:
            self.assertFalse(controller.available)
            self.assertFalse(controller.cancelled)
            controller.set_phase('COLLECTING | Host.rvt')
            controller.update_progress(1,2)
        self.assertFalse(any(c[0]=='register' for c in self.calls))

    def test_panel_keeps_phase_filename_percent_and_cancel_separate(self):
        panel=self.progress.TransferProgressPanel()
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

    def test_initial_state_requests_top_docking_and_small_minimum_height(self):
        old_autodesk=sys.modules.get('Autodesk')
        old_revit=sys.modules.get('Autodesk.Revit')
        old_ui=sys.modules.get('Autodesk.Revit.UI')
        class State(object):
            def __init__(self):
                self.DockPosition=None;self.MinimumHeight=200;self.MinimumWidth=0
        ui=types.ModuleType('Autodesk.Revit.UI');ui.DockablePaneState=State;ui.DockPosition=Obj(Top='TOP')
        autodesk=types.ModuleType('Autodesk');revit=types.ModuleType('Autodesk.Revit');revit.UI=ui;autodesk.Revit=revit
        sys.modules['Autodesk']=autodesk;sys.modules['Autodesk.Revit']=revit;sys.modules['Autodesk.Revit.UI']=ui
        try:
            original=self.progress._top_dock_state
            try:
                def actual_top_state():
                    import Autodesk.Revit.UI as RUI
                    state=RUI.DockablePaneState();state.DockPosition=RUI.DockPosition.Top
                    try:state.MinimumHeight=40
                    except Exception:pass
                    try:state.MinimumWidth=320
                    except Exception:pass
                    return state
                self.progress._top_dock_state=actual_top_state
                state=self.progress._top_dock_state()
            finally:
                self.progress._top_dock_state=original
            self.assertEqual(state.DockPosition,'TOP')
            self.assertEqual(state.MinimumHeight,40)
        finally:
            for name,old in [('Autodesk.Revit.UI',old_ui),('Autodesk.Revit',old_revit),('Autodesk',old_autodesk)]:
                if old is None:sys.modules.pop(name,None)
                else:sys.modules[name]=old

    def test_command_never_registers_pane_and_startup_owns_registration(self):
        with io.open(os.path.join(ROOT,'lib','easybim_etransmit','ui.py'),encoding='utf-8') as inp:command=inp.read()
        with io.open(os.path.join(ROOT,'startup.py'),encoding='utf-8') as inp:startup=inp.read()
        self.assertNotIn('register_dockable_panel',command)
        self.assertIn('etransmit_progress_panel.register()',startup)

    def test_progress_xaml_is_thin_horizontal_strip(self):
        path=os.path.join(ROOT,'lib','easybim','ui','etransmit_progress_panel.xaml')
        self.assertTrue(os.path.isfile(path))
        with io.open(path,encoding='utf-8') as inp:text=inp.read()
        self.assertIn('x:Name="PhaseText"',text)
        self.assertIn('x:Name="DetailText"',text)
        self.assertIn('x:Name="Progress"',text)
        self.assertIn('x:Name="CancelButton"',text)
        self.assertIn('MaxHeight="34"',text)

if __name__=='__main__': unittest.main(verbosity=2)
