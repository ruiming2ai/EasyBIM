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

    def test_worker_version_mismatch_is_rejected_before_model_access(self):
        app=Obj(VersionNumber='2026')
        job=worker._job_payload(self.stage,self.target,[],dict(independent_host=True),app,self.root)
        self.assertEqual(job['tool_version'],worker.VERSION)
        job['tool_version']='obsolete-installed-version'
        job['stage']=os.path.join(self.root,'missing-stage.rvt')
        with self.assertRaises(worker.WorkerError) as caught:
            worker._run_job(job,None)
        self.assertIn('EasyBIM version mismatch',str(caught.exception))
        self.assertIn('obsolete-installed-version',str(caught.exception))
        self.assertIn(worker.VERSION,str(caught.exception))
        with open(self.target,'rb') as stream:self.assertEqual(stream.read(),b'rvt')

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

    def assert_rejected_worker_keeps_diagnostics(self, outcome, process=None):
        rows=[dict(kind='RevitLink',element_id='10',source='Original.rvt')]
        process=process or FakeProcess()
        captured=[]
        def start(executable,job_path):
            captured.append(job_path)
            with io.open(job_path,'r',encoding='utf-8') as stream:job=json.loads(stream.read())
            self.addCleanup(shutil.rmtree,os.path.dirname(job_path),ignore_errors=True)
            result=dict(status='SUCCEEDED',processing_result=dict(
                issues=[],host_finalized=True,saved_references_checked=True),
                rows=[dict(rows[0],repath='API_LOCAL_LINK_RELATIVE')])
            if outcome=='failure':result.update(status='FAILED',message='Native host SaveAs failed')
            elif outcome=='incomplete':result['processing_result'].pop('saved_references_checked')
            worker._write_json(job['result_path'],result)
            return process
        with self.assertRaises(worker.WorkerError) as caught:
            worker.run_separate_revit(
                Obj(VersionNumber='2026'),self.stage,self.target,rows,
                dict(repath=True,independent_host=True),process_factory=start,
                executable='Revit.exe',sleeper=lambda seconds:None,clock=lambda:0)
        self.assertEqual(len(captured),1)
        job_dir=os.path.dirname(captured[0])
        self.assertTrue(os.path.isfile(captured[0]))
        self.assertTrue(os.path.isfile(os.path.join(job_dir,'result.json')))
        self.assertNotIn('repath',rows[0],'Rejected results must not mutate the parent reference rows')
        self.assertTrue(process.disposed)
        return caught.exception

    def test_failed_worker_preserves_job_result_and_parent_rows(self):
        error=self.assert_rejected_worker_keeps_diagnostics('failure')
        self.assertIn('Native host SaveAs failed',str(error))

    def test_success_missing_saved_reference_contract_preserves_diagnostics(self):
        self.assert_rejected_worker_keeps_diagnostics('incomplete')

    def test_unconfirmed_worker_exit_cannot_be_accepted_as_success(self):
        class UnstoppableProcess(FakeProcess):
            def WaitForExit(self,*args):return False
            def CloseMainWindow(self):return False
            def Kill(self):raise RuntimeError('process cannot be stopped')
        error=self.assert_rejected_worker_keeps_diagnostics('success',UnstoppableProcess())
        self.assertTrue(error.worker_may_be_running)

    def test_unattended_dialog_policy_avoids_save_and_prefers_continue(self):
        self.assertEqual(worker._dialog_result_candidates('','Save changes to this project?')[0],7)
        self.assertEqual(worker._dialog_result_candidates('TaskDialog_Upgrade','Upgrade model and continue?')[0],1001)
        generic=worker._dialog_result_candidates('TaskDialog_Test','Unexpected warning')
        self.assertIn(1,generic)
        self.assertIn(2,generic)

    def test_worker_result_records_suppressed_ui_for_report(self):
        worker._SUPPRESSED_DIALOGS[:]=[dict(dialog_id='x',result_code=1,dismissed=True)]
        worker._SUPPRESSED_FAILURES[:]=[dict(severity='Warning',action='DELETED')]
        result=worker._worker_result({},'SUCCEEDED',processing_result={},rows=[])
        self.assertEqual(len(result['suppressed_dialogs']),1)
        self.assertEqual(len(result['suppressed_failures']),1)
        worker._SUPPRESSED_DIALOGS[:]=[]
        worker._SUPPRESSED_FAILURES[:]=[]

    def test_live_session_backend_routes_image_repath_to_worker(self):
        source='open://host/Host.rvt'
        registry=Obj(get=lambda key:dict(mode='LIVE_DOCUMENT') if key==source else None)
        backend=SessionBackend(None,Obj(VersionNumber='2026'),self.root,registry)
        calls=[]
        old=worker.run_separate_revit
        worker.run_separate_revit=lambda *a,**k:calls.append((a,k)) or dict(
            issues=[],verified_in_process=False,worker_repaired=True)
        try:
            result=backend.finish(self.stage,self.target,[dict(special='image',target='A.pdf')],
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
