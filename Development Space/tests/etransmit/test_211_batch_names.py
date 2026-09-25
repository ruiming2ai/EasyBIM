# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os,sys,tempfile,shutil,unittest,json,zipfile
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import batch,files as f
class Names(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_Names_211_');self.addCleanup(shutil.rmtree,self.root)
    def source(self,folder,name):
        d=os.path.join(self.root,folder);os.makedirs(d);p=os.path.join(d,name)
        with open(p,'wb') as out:out.write(b'model')
        return p
    def backend(self,*args):
        class B(object):
            def scan(self,*args):return dict(references=[],issues=[],version='2024')
            def finish(self,stage,target,*args):shutil.copyfile(stage,target);return []
        return B()
    def test_jobs_and_zips_use_names_not_numbers(self):
        a=self.source('A','MEP - Lewis.rvt');b=self.source('B','MEP - Morrison.rvt')
        opts=f.defaults();opts['zip_per_model']=True
        root=os.path.join(self.root,'out');results=batch.run_batch([a,b],root,self.backend,opts)
        self.assertEqual([os.path.basename(r['root']) for r in results],['MEP - Lewis','MEP - Morrison'])
        with open(os.path.join(root,'batch.json')) as inp:jobs=json.load(inp)['jobs']
        self.assertEqual([os.path.basename(j['zip_path']) for j in jobs],['MEP - Lewis.zip','MEP - Morrison.zip'])
    def test_duplicate_names_have_distinct_folders_but_unchanged_rvt_name(self):
        a=self.source('A','Host.rvt');b=self.source('B','Host.rvt')
        r=batch.run_batch([a,b],os.path.join(self.root,'out'),self.backend,f.defaults())
        self.assertEqual([os.path.basename(x['root']) for x in r],['Host','Host (2)'])
        self.assertEqual([os.path.basename(x['files'][0]['target']) for x in r],['Host.rvt','Host.rvt'])
    def test_virtual_source_uses_model_name_not_display_mode_or_id(self):
        helper=getattr(batch,'plan_jobs',None);self.assertTrue(callable(helper),'named job planner missing')
        key='open://session-token/Old.rvt'
        jobs=helper([key],self.root,True,{key:'Current Name.rvt'})
        self.assertEqual(os.path.basename(jobs[0]['root']),'Current Name')
    def test_path_injection_and_reserved_folder_name_are_neutralized(self):
        helper=getattr(batch,'plan_jobs',None);self.assertTrue(callable(helper))
        for name in ['../NUL.rvt','CON.rvt','Name:bad?.rvt','..']:
            jobs=helper(['source'],self.root,True,{'source':name})
            folder=os.path.basename(jobs[0]['root'])
            self.assertTrue(f.within(jobs[0]['root'],self.root))
            self.assertNotIn(folder.upper(),['CON','NUL','..',''])
            self.assertFalse(any(x in folder for x in '<>:"/\\|?*'))
    def test_existing_job_never_overwritten(self):
        a=self.source('A','Host.rvt');root=os.path.join(self.root,'out');os.makedirs(os.path.join(root,'Host'))
        sentinel=os.path.join(root,'Host','keep.txt')
        with open(sentinel,'wb') as out:out.write(b'original')
        results=batch.run_batch([a],root,self.backend,f.defaults())
        with open(sentinel,'rb') as inp:self.assertEqual(inp.read(),b'original')
        self.assertNotEqual(results[0]['root'],os.path.join(root,'Host'))

if __name__=='__main__':unittest.main(verbosity=2)
