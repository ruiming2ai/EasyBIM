# -*- coding: utf-8 -*-
"""Host-context regressions; real file IO on CPython/IronPython, simulated UI/API."""
from __future__ import print_function
import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'lib'))
from easybim_etransmit import files as f, engine as e
from easybim_etransmit.revit import Backend


class Obj(object):
    def __init__(self, **values):
        self.__dict__.update(values)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ET_UI_')
        self.addCleanup(shutil.rmtree, self.root)
        self.host = self.make('Host.rvt', b'host')
        self.pdf = self.make('Refs/Details.pdf', b'%PDF-1.4 test')
        self.image = self.make('Refs/Image.png', b'image')
        self.output = os.path.join(self.root, 'out')
        self.state = Obj(uiapp=Obj(MainWindowHandle=123), calls=0, positions=0)
        state = self.state
        refs = [dict(source=self.pdf, id='1'), dict(source=self.image, id='2')]
        def scan(*args):
            state.uiapp = None  # models opened/closed invoke document event hooks
            return dict(references=refs, issues=[])
        self.backend = Obj(scan=scan, finish=lambda *a: [])

    def make(self, relative, payload):
        path = os.path.normpath(os.path.join(self.root, relative))
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, 'wb') as out:
            out.write(payload)
        return path

    def test_progress_callback_failure_does_not_skip_dependencies_or_processing(self):
        def pulse(*args):
            self.state.calls += 1
            return self.state.uiapp.MainWindowHandle
        result = e.transmit([self.host], self.output, self.backend, pulse=pulse)
        self.assertEqual(e.package_counts(result)['files_copied'], 3, repr(result['issues']))
        problems = [x for x in result['issues'] if x['code'] == 'PROGRESS_UI_FAILED']
        self.assertEqual(len(problems), 1)
        self.assertIn('MainWindowHandle', problems[0]['message'])
        self.assertIn('traceback', problems[0])
        self.assertFalse(any(x['code'] == 'MODEL_PROCESSING_FAILED' for x in result['issues']))

    def test_real_missing_file_still_reports_error_and_trace(self):
        self.backend.scan = lambda *a: dict(references=[dict(id='9', source=self.pdf + '.missing')], issues=[])
        result = e.transmit([self.host], self.output, self.backend)
        errors = [i for i in result['issues'] if i['code'] == 'COLLECTION_FAILED']
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['operation'], 'copy_file')
        self.assertIn('traceback', errors[0])
        self.assertTrue(os.path.isfile(os.path.join(self.output, 'DIAGNOSTICS.txt')))
        self.assertIn('incomplete', e.completion_message([result], 1))

    def test_midcopy_ui_error_does_not_discard_verified_file(self):
        def pulse(label, copied, total):
            if copied:
                raise AttributeError('UI redraw failed')
        result = e.transmit([self.host], self.output, self.backend, pulse=pulse)
        self.assertEqual(e.package_counts(result)['files_copied'], 3)
        self.assertEqual(len([i for i in result['issues'] if i['code'] == 'PROGRESS_UI_FAILED']), 1)

    def test_cancel_from_progress_is_not_swallowed(self):
        def pulse(*args):
            raise f.Cancelled('cancel button')
        result = e.transmit([self.host], self.output, self.backend, pulse=pulse)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertFalse(any(x['code'] == 'PROGRESS_UI_FAILED' for x in result['issues']))

    def test_ui_run_keeps_progress_alive_when_background_open_loses_host_context(self):
        state = self.state
        class Bar(object):
            instances = []
            def __init__(self, **kwargs):
                self.cancelled = False
                self.title = kwargs.get('title')
                self.update_window()
                Bar.instances.append(self)
            def update_window(self):
                state.positions += 1
                return state.uiapp.MainWindowHandle
            def update_progress(self, *args):
                state.calls += 1
                self.update_window()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        alerts = []
        fake = types.ModuleType('pyrevit')
        fake.forms = Obj(WPFWindow=object, ProgressBar=Bar,
                         alert=lambda msg, **kw: alerts.append(msg))
        fake.script = Obj()
        fake.DB = Obj()
        old_pyrevit = sys.modules.get('pyrevit')
        old_ui = sys.modules.pop('easybim_etransmit.ui', None)
        old_start = getattr(os, 'startfile', None)
        sys.modules['pyrevit'] = fake
        os.startfile = lambda path: None
        try:
            ui = importlib.import_module('easybim_etransmit.ui')
            options = f.defaults(); options['per_model'] = False
            ui.Dialog = lambda *a: Obj(result=([Obj(Source=self.host,Mode='SAVED_FILE')], self.output, options, []), ShowDialog=lambda: None,release_credentials=lambda:None)
            ui.SessionBackend = lambda *a: self.backend
            ui.run(Obj(Application=Obj()), 'unused.xaml')
            with io.open(os.path.join(self.output, os.path.splitext(os.path.basename(self.host))[0], 'manifest.json'), encoding='utf-8') as inp:
                result = json.load(inp)
            self.assertEqual(e.package_counts(result)['files_copied'], 3, repr(result['issues']))
            self.assertEqual(result['status'], 'COLLECTED', repr(result['issues']))
            self.assertGreater(state.calls, 3)
            self.assertEqual(state.positions, 1, 'Do not re-read mutable host context after background opening')
            self.assertEqual(len(Bar.instances), 1)
        finally:
            sys.modules.pop('easybim_etransmit.ui', None)
            if old_ui is not None: sys.modules['easybim_etransmit.ui'] = old_ui
            if old_pyrevit is None: sys.modules.pop('pyrevit', None)
            else: sys.modules['pyrevit'] = old_pyrevit
            if old_start is None: del os.startfile
            else: os.startfile = old_start

    def test_engine_never_collects_its_inspection_scratch_as_an_original_link(self):
        def set_staging(path):
            self.backend.work = path
        def scan(*args):
            scratch = os.path.join(self.backend.work, 'Arch.rvt')
            with open(scratch, 'wb') as out: out.write(b'not original')
            return dict(references=[dict(id='99', source=scratch)], issues=[])
        self.backend.set_staging_root = set_staging
        self.backend.scan = scan
        result = e.transmit([self.host], self.output, self.backend)
        self.assertEqual(e.package_counts(result)['files_copied'], 1)
        self.assertTrue(any(x['code'] == 'STAGING_REFERENCE_UNRESOLVED' for x in result['issues']))
        self.assertFalse(any(x['relative'].endswith('/Arch.rvt') for x in result['files']))


class PathTests(unittest.TestCase):
    def setUp(self):
        self.db = Obj(ModelPathUtils=Obj(ConvertModelPathToUserVisiblePath=lambda p: p.path),
                      ExternalFileUtils=Obj(GetAllExternalFileReferences=lambda d: []),
                      ExternalResourceTypes=Obj(BuiltInExternalResourceTypes=Obj()),
                      ExternalResourceUtils=Obj(GetAllExternalResourceReferences=lambda d: []),
                      BuiltInCategory=Obj())
        self.backend = Backend(self.db, Obj(PointCloudsRootPath=r'C:\Clouds'), r'C:\Output')
        self.backend.set_staging_root(r'C:\Temp\EasyBIM_ET_TEST')
        self.backend.elements = lambda doc, kind: []
        self.info = dict(central='', workshared=False)

    def test_pointcloud_modelpath_is_converted_not_stringified(self):
        class ModelPath(object):
            path = r'C:\Survey\Main.rcp'
            def __str__(self):
                return '<Autodesk.Revit.DB.FilePath object>'
        point = Obj(Id=Obj(Value=10), GetPath=lambda: ModelPath())
        self.backend.elements = lambda d, k: [point] if k == 'PointCloudType' else []
        result = dict(references=[], issues=[])
        self.backend.scan_open(Obj(), r'C:\Project\Host.rvt', self.info, result)
        self.assertEqual(result['references'][0]['source'], r'C:\Survey\Main.rcp')
        self.assertFalse(result['issues'])

    def test_nonfile_pointcloud_is_reported_without_inventing_a_filename(self):
        point = Obj(Id=Obj(Value=10), GetPath=lambda: None)
        self.backend.elements = lambda d, k: [point] if k == 'PointCloudType' else []
        result = dict(references=[], issues=[])
        self.backend.scan_open(Obj(), r'C:\Project\Host.rvt', self.info, result)
        self.assertFalse(result['references'])
        self.assertTrue(any(i['code'] == 'POINT_CLOUD_NOT_FILE_BASED' for i in result['issues']))

    def test_saved_relative_references_use_original_central_not_local_or_scratch(self):
        seen = []
        self.backend.guard = lambda *a: None
        self.backend.basic = lambda *a: dict(version='2024', workshared=True, central=r'N:\Project\Host.rvt')
        self.backend.rows = lambda path, owner='': seen.append((path, owner)) or []
        self.backend.scan(r'C:\Downloads\Host.rvt', r'C:\Temp\EasyBIM_ET_TEST\stage.rvt', {'deep': False})
        self.assertEqual(seen[0][1], r'N:\Project\Host.rvt')


if __name__ == '__main__':
    print('RUNTIME:', sys.version)
    unittest.main(verbosity=2)
