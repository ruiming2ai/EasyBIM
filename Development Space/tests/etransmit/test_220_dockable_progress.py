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
        self.warnings=[]
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
        fake.script=Obj(get_logger=lambda:Obj(warning=lambda *a:owner.warnings.append(a),info=lambda *a:None))
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

    def test_registered_panel_instance_survives_module_global_loss(self):
        self.assertTrue(self.progress.register())
        panel=self.panel_instance
        self.progress._PANEL_INSTANCE=None
        with self.progress.ProgressController() as controller:
            self.assertTrue(controller.available)
            self.assertIs(controller.panel,panel)
            self.assertEqual(controller.mode,'DOCKABLE_PANE')
        self.assertFalse(any(c[0]=='register' for c in self.calls[1:]))

    def test_missing_registered_instance_degrades_to_noop_without_registration(self):
        self.progress._REGISTERED=True
        self.progress._PANEL_INSTANCE=None
        with self.progress.ProgressController() as controller:
            self.assertFalse(controller.available)
            self.assertFalse(controller.cancelled)
            controller.set_phase('COLLECTING | Host.rvt')
            controller.update_progress(1,2)
        self.assertFalse(any(c[0]=='register' for c in self.calls))
        self.assertFalse(self.warnings)

    def test_missing_pane_uses_colored_top_overlay_fallback_without_warning_console(self):
        owner=self
        class Overlay(object):
            def __init__(self,*args,**kwargs):
                owner.calls.append(('overlay_create',kwargs.get('title')))
                self.cancelled=False;self.phases=[];self.values=[];self.closed=False
            def __enter__(self):
                owner.calls.append(('overlay_open',))
                return self
            def __exit__(self,*args):
                self.closed=True;owner.calls.append(('overlay_close',))
            def set_phase(self,label):
                self.phases.append(label);return owner.progress.progress_phase(label)[0]
            def update_progress(self,current,total):
                self.values.append((current,total))
        self.progress.TransferProgressBar=Overlay
        self.progress._REGISTERED=True
        self.progress._PANEL_INSTANCE=None
        with self.progress.ProgressController() as controller:
            self.assertTrue(controller.available)
            self.assertEqual(controller.mode,'TOP_OVERLAY')
            controller.set_phase('REPATHING | Host.rvt')
            controller.update_progress(2,4)
            overlay=controller.overlay
            self.assertEqual(overlay.values,[(2,4)])
            self.assertEqual(overlay.phases,['REPATHING | Host.rvt'])
        self.assertTrue(overlay.closed)
        self.assertFalse(self.warnings)

    def test_overlay_class_keeps_phase_color_mapping_transparency_and_ribbon_anchor(self):
        source_path=os.path.join(ROOT,'lib','easybim','etransmit_progress_panel.py')
        with io.open(source_path,encoding='utf-8') as inp:text=inp.read()
        self.assertIn('class TransferProgressBar(forms.ProgressBar)',text)
        self.assertIn('self.Opacity = 0.92',text)
        self.assertIn('_set_brush(self.pbar, color)',text)
        self.assertIn('ribbon_bottom = _ribbon_bottom_dip()',text)
        self.assertIn("self._etransmit_position_source = 'RIBBON_BOTTOM'",text)
        self.assertNotIn('script.get_logger().warning',text)

    def test_ribbon_bottom_helper_uses_wpf_device_transform(self):
        old_autodesk=sys.modules.get('Autodesk')
        old_windows=sys.modules.get('Autodesk.Windows')
        old_system=sys.modules.get('System')
        old_system_windows=sys.modules.get('System.Windows')

        class Point(object):
            def __init__(self,x,y):self.X=x;self.Y=y
        class Matrix(object):
            def Transform(self,point):return Point(point.X/2.0,point.Y/2.0)
        class Target(object):
            TransformFromDevice=Matrix()
        class Source(object):
            CompositionTarget=Target()
        class PresentationSource(object):
            @staticmethod
            def FromVisual(value):return Source()
        class Ribbon(object):
            ActualHeight=100.0
            def PointToScreen(self,point):return Point(point.X,400.0)
        aw=types.ModuleType('Autodesk.Windows')
        aw.ComponentManager=Obj(Ribbon=Ribbon())
        autodesk=types.ModuleType('Autodesk');autodesk.Windows=aw
        sw=types.ModuleType('System.Windows')
        sw.Point=Point;sw.PresentationSource=PresentationSource
        system=types.ModuleType('System');system.Windows=sw
        sys.modules['Autodesk']=autodesk;sys.modules['Autodesk.Windows']=aw
        sys.modules['System']=system;sys.modules['System.Windows']=sw
        try:
            self.assertEqual(self.progress._ribbon_bottom_dip(),200.0)
        finally:
            for name,old in [('System.Windows',old_system_windows),('System',old_system),
                             ('Autodesk.Windows',old_windows),('Autodesk',old_autodesk)]:
                if old is None:sys.modules.pop(name,None)
                else:sys.modules[name]=old

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
