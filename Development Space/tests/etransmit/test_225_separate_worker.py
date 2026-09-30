# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))

from easybim_etransmit import worker
from easybim_etransmit.session import SessionBackend


class Obj(object):
    def __init__(self,**kw):self.__dict__.update(kw)


class FakeProcess(object):
    def __init__(self):
        self.HasExited=False
        self.disposed=False
    def WaitForExit(self,*args):
        self.HasExited=True
        return True
    def CloseMainWindow(self):
        self.HasExited=True
        return True
    def Kill(self):
        self.HasExited=True
    def Dispose(self):
        self.disposed=True


class WorkerRuntime(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_worker_test_')
        self.addCleanup(shutil.rmtree,self.root,ignore_errors=True)
        self.stage=os.path.join(self.root,'stage.rvt')
        self.target=os.path.join(self.root,'package','Host.rvt')
        os.makedirs(os.path.dirname(self.target))
        for path in (self.stage,self.target):
            with open(path,'wb') as out:out.write(b'rvt')

    def test_worker_mode_requires_explicit_process_flag_and_job(self):
        env={worker.MODE_ENV:'1',worker.JOB_ENV:'C:\\Temp\\job.json'}
        self.assertTrue(worker.is_worker_process(env))
        self.assertTrue(worker.has_pending_job(env))
        self.assertFalse(worker.is_worker_process({}))
        self.assertFalse(worker.has_pending_job({worker.MODE_ENV:'1'}))

    def test_parent_worker_job_merges_repaired_rows_without_reopen(self):
        rows=[dict(kind='Image',element_id='10',target=os.path.join(self.root,'PDF','A.pdf'))]
        app=Obj(VersionNumber='2026')
        process=FakeProcess()
        def start(exe,job_path):
            with io.open(job_path,'r',encoding='utf-8') as stream:
                job=json.loads(stream.read())
            returned=[dict(rows[0],repath='API_IMAGE_RELATIVE')]
            worker._write_json(job['result_path'],dict(
                status='SUCCEEDED',message='',process_id=42,
                processing_result=dict(issues=[],verified_in_process=False),
                rows=returned))
            return process
        result=worker.run_separate_revit(
            app,self.stage,self.target,rows,
            dict(repath=True,cleanup=False,upgrade=False),
            process_factory=start,executable='Revit.exe',
            sleeper=lambda seconds:None,clock=lambda:0)
        self.assertTrue(result['worker_repaired'])
        self.assertEqual(result['worker_process_id'],42)
        self.assertEqual(rows[0]['repath'],'API_IMAGE_RELATIVE')

    def test_live_session_backend_routes_repath_to_worker(self):
        source='open://host/Host.rvt'
        registry=Obj(get=lambda key:dict(mode='LIVE_DOCUMENT') if key==source else None)
        backend=SessionBackend(None,Obj(VersionNumber='2026'),self.root,registry)
        calls=[]
        old=worker.run_separate_revit
        worker.run_separate_revit=lambda *a,**k:calls.append((a,k)) or dict(
            issues=[],verified_in_process=False,worker_repaired=True)
        try:
            result=backend.finish(self.stage,self.target,[],
                                  dict(repath=True,_host_source=source))
        finally:
            worker.run_separate_revit=old
        self.assertTrue(result['worker_repaired'])
        self.assertEqual(len(calls),1)

    def test_saved_source_does_not_require_worker(self):
        source='C:\\Models\\Host.rvt'
        registry=Obj(get=lambda key:dict(mode='SAVED_FILE') if key==source else None)
        backend=SessionBackend(None,Obj(VersionNumber='2026'),self.root,registry)
        self.assertFalse(backend._requires_separate_worker(source,dict(repath=True)))

    def test_idling_worker_process_short_circuits_ordinary_consumers(self):
        path=os.path.join(ROOT,'lib','easybim','idling.py')
        with io.open(path,'r',encoding='utf-8') as stream:text=stream.read()
        worker_check=text.index('etransmit_worker.is_worker_process()')
        worker_run=text.index('etransmit_worker.run_pending')
        startup=text.index('_guarded("StartupJobs"',worker_check)
        self.assertLess(worker_check,worker_run)
        self.assertLess(worker_run,startup)
        self.assertIn('return\n    _guarded("StartupJobs"',text[worker_run:startup+40])


if __name__=='__main__':
    unittest.main(verbosity=2)
