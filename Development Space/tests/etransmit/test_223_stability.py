# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io
import os
import shutil
import sys
import tempfile
import unittest

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))

from easybim_etransmit import preflight, engine, files as f
from test_payload_acquisition import compound

class Obj(object):
    def __init__(self,**kw): self.__dict__.update(kw)

class PrimaryHostReadiness(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_223_')
        self.addCleanup(shutil.rmtree,self.root)
        self.host=os.path.join(self.root,'Host.rvt')
        with open(self.host,'wb') as out:out.write(compound(suffix='host'))

    def row(self,doc,mode='LIVE_DOCUMENT',source=''):
        return Obj(Name='Host_detached',Mode=mode,Source=source,Document=doc)

    def test_normal_saved_local_live_host_is_proven(self):
        doc=Obj(PathName=self.host,Title='Host',IsModelInCloud=False)
        row=self.row(doc)
        result=preflight.primary_host_sources([row],Obj())
        self.assertEqual(result[0]['path'],self.host)
        self.assertEqual(result[0]['evidence'],'DOCUMENT_PATH')
        self.assertEqual(row.ResolvedSource,self.host)

    def test_detached_host_uses_exact_current_session_opening_source(self):
        doc=Obj(PathName='',Title='Host_detached',IsModelInCloud=False)
        row=self.row(doc)
        from easybim_etransmit import source_tracker
        old=source_tracker.source_for_document_with_evidence
        source_tracker.source_for_document_with_evidence=lambda d,application=None:(self.host,'OPEN_EVENT')
        try:
            result=preflight.primary_host_sources([row],Obj())
        finally:
            source_tracker.source_for_document_with_evidence=old
        self.assertEqual(result[0]['path'],self.host)
        self.assertEqual(result[0]['evidence'],'OPEN_EVENT')
        self.assertEqual(row.ResolvedSource,self.host)

    def test_pathless_detached_host_blocks_before_collection(self):
        doc=Obj(PathName='',Title='Host_detached',IsModelInCloud=False)
        row=self.row(doc)
        from easybim_etransmit import source_tracker
        old=source_tracker.source_for_document_with_evidence
        source_tracker.source_for_document_with_evidence=lambda d,application=None:('','')
        try:
            with self.assertRaises(preflight.PreflightError) as caught:
                preflight.primary_host_sources([row],Obj())
        finally:
            source_tracker.source_for_document_with_evidence=old
        self.assertIn('Save the detached model first',str(caught.exception))

    def test_missing_exact_local_source_is_never_substituted(self):
        missing=os.path.join(self.root,'Missing.rvt')
        doc=Obj(PathName=missing,Title='Host',IsModelInCloud=False)
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.primary_host_sources([self.row(doc)],Obj())
        self.assertIn(missing,str(caught.exception))
        self.assertIn('No similarly named file',str(caught.exception))

    def test_live_acc_requires_cloud_identity_but_not_local_path(self):
        cloud=Obj(GetModelGUID=lambda:'model-guid',GetProjectGUID=lambda:'project-guid',Region='US')
        doc=Obj(PathName='',Title='Cloud Host',IsModelInCloud=True,GetCloudModelPath=lambda:cloud)
        row=self.row(doc)
        result=preflight.primary_host_sources([row],Obj())
        self.assertEqual(result[0]['mode'],'ACC_LIVE')
        self.assertEqual(result[0]['cloud']['model_guid'],'model-guid')
        self.assertEqual(row.ResolvedSource,'')

    def test_saved_file_selection_is_exact_and_native(self):
        row=Obj(Name='Host',Mode='SAVED_FILE',Source=self.host,Document=None)
        result=preflight.primary_host_sources([row],Obj())
        self.assertEqual(result[0]['evidence'],'SAVED_FILE_SELECTION')
        self.assertEqual(row.ResolvedSource,self.host)

    def test_browsed_saved_host_is_delivered_collect_only_without_opening_host(self):
        row=Obj(Name='Host_detached',Mode='SAVED_FILE',Source=self.host,Document=None)
        preflight.primary_host_sources([row],Obj())
        scans=[]
        testcase=self
        class Backend(object):
            def set_staging_root(self,path): self.staging_root=path
            def scan(self,source,stage,options):
                scans.append(dict(options))
                testcase.assertFalse(f.host_processing_allowed(options),
                                     'collect-only browse must not authorize a Revit host open')
                return dict(references=[],issues=[],version='2025',opened_in_revit=False,
                            is_workshared=False,inspection_status='METADATA_ONLY')
            def finish(self,*args):
                testcase.fail('collect-only browse must not process/open the packaged host')
        opts=f.defaults();opts.update(repath=False,cleanup=False,upgrade=False)
        output=os.path.join(self.root,'browse_collect_only')
        result=engine.transmit([row.ResolvedSource],output,Backend(),opts)
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1,repr(result['issues']))
        self.assertEqual(len(scans),1)
        hosts=[record for record in result['files'] if record.get('is_primary_host')]
        self.assertEqual(len(hosts),1)
        self.assertTrue(os.path.isfile(hosts[0]['target']))
        self.assertEqual(f.digest(hosts[0]['target']),f.digest(self.host))
        self.assertFalse(hosts[0].get('opened_in_revit'))


class UIContracts(unittest.TestCase):
    def test_obsolete_source_controls_and_options_are_absent(self):
        xaml=os.path.join(ROOT,'EasyBIM.tab','Links.panel','e-transmit.pushbutton','window.xaml')
        ui=os.path.join(ROOT,'lib','easybim_etransmit','ui.py')
        session=os.path.join(ROOT,'lib','easybim_etransmit','session.py')
        with io.open(xaml,encoding='utf-8') as inp:x=inp.read()
        with io.open(ui,encoding='utf-8') as inp:u=inp.read()
        with io.open(session,encoding='utf-8') as inp:s=inp.read()
        for value in ('Use saved copy for selected row...','Skip unresolved cloud downloads',
                      'Exact source mappings','Add exact prefix mapping...','Remove mapping'):
            self.assertNotIn(value,x)
        self.assertNotIn("opts['skip_cloud_links']",u)
        self.assertNotIn('skip_cloud_links',s)
        self.assertNotIn('self.mappings',u)
        self.assertIn("opts['mappings']=[]",u)

    def test_document_opening_hook_stamps_pending_source_with_current_journal(self):
        hook=os.path.join(ROOT,'hooks','doc-opening.py')
        with io.open(hook,encoding='utf-8') as inp:text=inp.read()
        self.assertIn("_app = getattr(__revit__, \"Application\", None)",text)
        self.assertIn('application=_app',text)

    def test_command_contains_crash_boundaries_and_no_runtime_registration(self):
        ui=os.path.join(ROOT,'lib','easybim_etransmit','ui.py')
        with io.open(ui,encoding='utf-8') as inp:text=inp.read()
        for marker in ('ET_STEP_07_SAVE_PREFLIGHT_DONE','ET_STEP_08_PROGRESS_LOOKUP_START',
                       'ET_STEP_11_REGISTRY_READY','ET_STEP_13_LIVE_DOCUMENT_REGISTER_DONE',
                       'ET_STEP_16_BATCH_START','ET_STEP_17_BATCH_DONE'):
            self.assertIn(marker,text)
        self.assertNotIn('register_dockable_panel',text)

if __name__=='__main__':unittest.main(verbosity=2)
