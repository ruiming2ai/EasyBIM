# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os,sys,shutil,tempfile,unittest,zipfile,json
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f

class Batch(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_Batch_'); self.addCleanup(shutil.rmtree,self.root)
    def runner(self):
        try: from easybim_etransmit import batch
        except ImportError: self.fail('Batch runner with per-model archives is missing')
        return batch.run_batch
    def model(self,folder):
        d=os.path.join(self.root,folder);os.makedirs(d)
        p=os.path.join(d,'Host.rvt')
        with open(p,'wb') as out:out.write(folder.encode('utf-8'))
        return p
    def backend(self,root,model):
        class B(object):
            def scan(self,*a): return dict(references=[],issues=[],version='2024')
            def finish(self,stage,target,*a):shutil.copyfile(stage,target);return []
        return B()
    def test_separate_zip_for_each_same_named_host(self):
        run=self.runner(); a=self.model('A');b=self.model('B')
        opts=f.defaults();opts.update(zip_per_model=True,per_model=False)
        root=os.path.join(self.root,'out');r=run([a,b],root,self.backend,opts)
        self.assertEqual(len(r),2)
        with open(os.path.join(root,'batch.json')) as inp: archives=json.load(inp)
        self.assertEqual(len(archives['jobs']),2)
        for j in archives['jobs']:
            self.assertEqual(j['zip_status'],'VERIFIED')
            with zipfile.ZipFile(j['zip_path']) as z:
                self.assertIsNone(z.testzip())
                names=z.namelist();self.assertIn('START_HERE.txt',names)
                hosts=[n for n in names if n.endswith('Host.rvt')]
                self.assertEqual(len(hosts),1)
                self.assertFalse(any(n.endswith('.zip') for n in names))
    def test_failed_constructor_does_not_skip_next_model(self):
        run=self.runner();a=self.model('A');b=self.model('B')
        def factory(root,model):
            if model==a:raise RuntimeError('test constructor failure')
            return self.backend(root,model)
        r=run([a,b],os.path.join(self.root,'out'),factory,f.defaults())
        self.assertEqual(len(r),2);self.assertEqual(r[0]['status'],'FAILED')
        self.assertEqual(r[1]['status'],'COLLECTED')
    def test_cancel_before_next_job_recorded_in_batch(self):
        run=self.runner();a=self.model('A');b=self.model('B');started=[]
        def factory(root,model):started.append(model);return self.backend(root,model)
        r=run([a,b],os.path.join(self.root,'out'),factory,f.defaults(),cancelled=lambda:True)
        self.assertEqual(started,[]);self.assertEqual(r,[])
        with open(os.path.join(self.root,'out','batch.json')) as fobj:d=json.load(fobj)
        self.assertEqual(len(d['jobs']),2)
        self.assertTrue(all(j['status']=='NOT_STARTED_CANCELLED' for j in d['jobs']))

    def test_whole_batch_zip_failure_is_recorded_without_losing_results(self):
        from easybim_etransmit import batch,engine
        source=self.model('A');opts=f.defaults();opts['zip']=True
        original=engine.zip_package
        def fail(*args):raise IOError('archive permission denied')
        engine.zip_package=fail
        try:results=batch.run_batch([source],os.path.join(self.root,'out'),self.backend,opts)
        finally:engine.zip_package=original
        self.assertEqual(len(results),1)
        self.assertTrue(any(x['code']=='BATCH_ZIP_FAILED' for x in results[0]['issues']))

if __name__=='__main__':unittest.main(verbosity=2)
