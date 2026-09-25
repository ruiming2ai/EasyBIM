# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os,sys,unittest,tempfile,shutil
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import session as s,engine,files as f,batch
import test_211_host_safety as fixtures
Obj=fixtures.Obj

class ExtraHostChecks(fixtures.HostSafety):
    # Inherit the same setup; explicitly add only these scenarios to this suite.
    def test_processing_failure_restores_main_without_packaged_baseline(self):
        key=self.live();self.authorize(key)
        class B(s.SessionBackend):
            def finish(inner,stage,target,*args):
                with open(target,'wb') as out:out.write(b'bad processor result')
                raise RuntimeError('repath failed')
            def verify_package(inner,*args):self.fail('failed processing must not be opened')
        out=os.path.join(self.root,'out')
        result=engine.transmit([key],out,B(self.db,self.app,out,self.r),f.defaults())
        rec=result['files'][0]
        with open(rec['target'],'rb') as inp:self.assertEqual(inp.read(),self.current)
        self.assertEqual(f.digest(rec['target']),rec['current_state_sha256'])
        self.assertFalse(os.path.exists(os.path.join(out,'_HostState')))
        self.assertEqual(rec['current_state_integrity'],'VERIFIED')
        self.assertEqual(rec['processing_status'],'FAILED')
    def test_postsave_mutation_is_not_claimed_as_current_snapshot(self):
        old=self.doc.SaveAs
        def save(path,opts):old(path,opts);self.doc.IsModified=True
        self.doc.SaveAs=save;key=self.live();self.authorize(key)
        with self.assertRaises(s.SourceError):self.r.snapshot(key)
        self.assertTrue(os.path.isfile(self.calls[0]))
        self.assertFalse(self.r.get(key).get('snapshot_path'))
    def test_cancel_before_save_does_not_touch_document(self):
        key=self.live();self.authorize(key);self.r.cancelled=lambda:True
        with self.assertRaises(f.Cancelled):self.r.snapshot(key)
        self.assertEqual(self.calls,[])
    def test_schema_warnings_do_not_hide_host_error_in_dialog(self):
        result=dict(status='FAILED',requested_models=['h'],models=[],files=[],issues=[
            dict(code='PLUGIN_SOURCE_COVERAGE',severity='warning',source='h',message='optional'),
            dict(code='HOST_SNAPSHOT_FAILED',severity='error',source='h',message='required')])
        text=engine.completion_message([result],1)
        self.assertLess(text.index('HOST_SNAPSHOT_FAILED'),text.index('PLUGIN_SOURCE_COVERAGE'))
    def test_adc_link_is_skipped_even_if_link_document_not_cloud_workshared(self):
        child=Obj(Title='Arch',PathName=r'C:\DC\ACCDocs\Firm\Project\Shared\Arch.rvt',
                  IsLinked=True,IsModelInCloud=False,IsWorkshared=False)
        key=self.r.add_live(child)
        b=s.SessionBackend(self.db,self.app,self.root,self.r)
        self.assertTrue(b.skip_dependency(key,'host',{'skip_cloud_links':True}))
    def test_upfront_cloud_skip_does_not_bind_an_associated_download_graph(self):
        with open(os.path.join(ROOT,'lib/easybim_etransmit/ui.py')) as inp:code=inp.read()
        self.assertNotIn('registry.bind_graph(',code)
        self.assertIn('saved_state_only=True',code)

# Avoid duplicating inherited tests in discovery counts; borrow only setup/helpers.
for _name in list(fixtures.HostSafety.__dict__):
    if _name.startswith('test_') and _name not in ExtraHostChecks.__dict__:
        setattr(ExtraHostChecks,_name,None)

class PlannerChecks(unittest.TestCase):
    def test_windows_long_model_named_path_is_rejected_before_copy(self):
        key='open://entry/Host.rvt';name='A'*140+'.rvt'
        root='C:\\'+('long-output-'*4)
        errors=engine.preflight_paths([key],root,{'per_model':True},{},model_names={key:name})
        self.assertTrue(errors,'model-named duplicate length was not included in preflight')
    def test_zero_selected_models_has_no_phantom_job(self):
        self.assertEqual(batch.plan_jobs([],os.path.abspath('unused'),False),[])

if __name__=='__main__':unittest.main(verbosity=2)
