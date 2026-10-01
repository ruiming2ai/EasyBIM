# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import contextmanager

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import VERSION, batch, engine, files as f


@contextmanager
def replace(obj,name,side_effect=None):
    original=getattr(obj,name);calls=[]
    def replacement(*args,**kwargs):
        calls.append((args,kwargs))
        if side_effect is not None:return side_effect(*args,**kwargs)
    setattr(obj,name,replacement)
    try:yield calls
    finally:setattr(obj,name,original)


class UnfinishedArchive(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET225_archive_')
        self.addCleanup(shutil.rmtree,self.root)
        self.output=os.path.join(self.root,'batch')
        self.source=os.path.join(self.root,'Host.rvt')

    def result(self,source,package,options,worker=False,exists=True,target=None,
               status='NOT_FINALIZED',primary=True):
        f.ensure_directory(package)
        target=target or os.path.join(package,'Host.rvt')
        if exists:
            f.ensure_directory(os.path.dirname(target))
            with open(target,'wb') as out:out.write(b'unfinished host bytes')
        row=dict(source=source,relative='Host.rvt',category='revit',target=target,
                 status=status,is_primary_host=primary,worker_exit_unconfirmed=worker)
        issue=engine.issue('HOST_FINALIZATION_FAILED',source,'Original host repair failure.','error')
        result=dict(version=VERSION,root=package,status='FAILED',options=options,
                    requested_models=[source],models=[dict(source=source)],files=[row],
                    references=[],aliases=[],issues=[issue])
        engine.write_reports(result)
        return result

    def run_result(self,options,**state):
        def transmit(models,package,backend,opts,*args):
            return self.result(models[0],package,opts,**state)
        with replace(engine,'transmit',side_effect=transmit):
            return batch.run_batch([self.source],self.output,lambda *args:object(),options)

    def jobs(self):
        with io.open(os.path.join(self.output,'batch.json'),encoding='utf-8') as inp:
            return json.load(inp)['jobs']

    def test_live_worker_blocks_model_and_batch_archives_without_masking_failure(self):
        options=f.defaults();options.update(zip_per_model=True,zip=True)
        with replace(engine,'zip_package') as archive:
            results=self.run_result(options,worker=True)
        self.assertEqual(archive,[])
        self.assertEqual(results[0]['status'],'FAILED')
        self.assertEqual(results[0]['issues'][0]['code'],'HOST_FINALIZATION_FAILED')
        self.assertTrue(f.file_exists(results[0]['files'][0]['target']))
        self.assertEqual([issue['code'] for issue in results[0]['issues'][1:]],
                         ['MODEL_ZIP_SKIPPED_UNFINISHED','BATCH_ZIP_SKIPPED_UNFINISHED'])
        job=self.jobs()[0]
        self.assertEqual(job['status'],'FAILED')
        self.assertEqual(job['zip_status'],'SKIPPED_UNFINISHED_HOST')
        self.assertEqual(job['batch_zip_status'],'SKIPPED_UNFINISHED_HOST')
        self.assertNotIn('zip_sha256',job)
        self.assertNotIn('zip_path',job)
        self.assertFalse(os.path.exists(self.output+'.zip'))
        with io.open(os.path.join(job['root'],'manifest.json'),encoding='utf-8') as inp:
            manifest=json.load(inp)
        self.assertEqual(manifest['status'],'FAILED')
        self.assertTrue(any(issue['code']=='BATCH_ZIP_SKIPPED_UNFINISHED'
                            for issue in manifest['issues']))
        with io.open(os.path.join(self.output,'START_HERE.txt'),encoding='utf-8') as inp:
            self.assertIn('Batch ZIP: SKIPPED_UNFINISHED_HOST',inp.read())

    def test_remaining_unfinished_target_blocks_archive_after_worker_exit(self):
        options=f.defaults();options['zip_per_model']=True
        with replace(engine,'zip_package') as archive:
            results=self.run_result(options,worker=False)
        self.assertEqual(archive,[])
        self.assertEqual(self.jobs()[0]['zip_status'],'SKIPPED_UNFINISHED_HOST')
        self.assertEqual(results[0]['status'],'FAILED')
        self.assertEqual(results[0]['issues'][-1]['code'],'MODEL_ZIP_SKIPPED_UNFINISHED')

    def test_unconfirmed_worker_blocks_even_nonhost_missing_target(self):
        options=f.defaults();options.update(zip_per_model=True,zip=True)
        with replace(engine,'zip_package') as archive:
            self.run_result(options,worker=True,exists=False,status='COPIED',primary=False)
        self.assertEqual(archive,[])
        job=self.jobs()[0]
        self.assertEqual(job['zip_status'],'SKIPPED_UNFINISHED_HOST')
        self.assertEqual(job['batch_zip_status'],'SKIPPED_UNFINISHED_HOST')

    def test_outside_package_unfinished_target_is_checked_without_opening_archive(self):
        options=f.defaults();options['zip_per_model']=True
        target=os.path.join(self.root,'retained','Host.rvt')
        with replace(engine,'zip_package') as archive:
            self.run_result(options,target=target)
        self.assertEqual(archive,[])
        self.assertTrue(f.file_exists(target))
        self.assertEqual(self.jobs()[0]['zip_status'],'SKIPPED_UNFINISHED_HOST')

    def test_batch_archive_checks_later_result_and_reports_skip_to_every_package(self):
        sources=[os.path.join(self.root,'First.rvt'),os.path.join(self.root,'Second.rvt')]
        options=f.defaults();options['zip']=True
        def transmit(models,package,backend,opts,*args):
            result=self.result(models[0],package,opts,worker=models[0]==sources[1])
            if models[0]==sources[0]:
                result['status']='COLLECTED';result['files'][0]['status']='COPIED'
                result['issues']=[]
            return result
        with replace(engine,'transmit',side_effect=transmit),\
                replace(engine,'zip_package') as archive:
            results=batch.run_batch(sources,self.output,lambda *args:object(),options)
        self.assertEqual(archive,[])
        self.assertEqual(results[1]['status'],'FAILED')
        self.assertEqual(results[1]['issues'][0]['code'],'HOST_FINALIZATION_FAILED')
        self.assertTrue(all(job['batch_zip_status']=='SKIPPED_UNFINISHED_HOST' for job in self.jobs()))
        self.assertTrue(all(any(issue['code']=='BATCH_ZIP_SKIPPED_UNFINISHED' for issue in result['issues'])
                            for result in results))

    def test_removed_unfinished_target_can_archive_reports_without_publishing_host(self):
        options=f.defaults();options['zip_per_model']=True
        results=self.run_result(options,exists=False)
        job=self.jobs()[0]
        self.assertEqual(results[0]['status'],'FAILED')
        self.assertEqual(job['zip_status'],'VERIFIED')
        with zipfile.ZipFile(job['zip_path']) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('START_HERE.txt',archive.namelist())
            self.assertFalse(any(name.lower().endswith('.rvt') for name in archive.namelist()))
        self.assertFalse(any('ZIP_SKIPPED_UNFINISHED' in issue['code'] for issue in results[0]['issues']))


if __name__=='__main__':unittest.main(verbosity=2)
