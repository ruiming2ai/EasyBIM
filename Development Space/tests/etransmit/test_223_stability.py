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

    def test_filename_only_detached_path_enters_recovery_instead_of_hard_stopping(self):
        doc=Obj(PathName='Host_detached.rvt',Title='Host_detached',
                IsModelInCloud=False,IsWorkshared=True,IsDetached=True)
        row=self.row(doc)
        old_verify=preflight.verify_detached_candidate
        preflight.verify_detached_candidate=lambda d,p,application=None,DB=None:'PROJECT_INFORMATION_UNIQUE_ID'
        try:
            result=preflight.primary_host_sources(
                [row],Obj(),DB=Obj(),output=self.root,
                detached_recovery=lambda r,feedback='':dict(action='BROWSE',path=self.host))
        finally:
            preflight.verify_detached_candidate=old_verify
        self.assertEqual(result[0]['path'],self.host)
        self.assertEqual(row.Mode,'SAVED_FILE')
        self.assertIsNone(row.Document)

    def test_pathless_detached_host_can_save_current_state_and_continue_as_saved_file(self):
        doc=Obj(PathName='',Title='Host_detached',IsModelInCloud=False,IsWorkshared=True)
        row=self.row(doc)
        from easybim_etransmit import source_tracker
        old_source=source_tracker.source_for_document_with_evidence
        old_save=preflight.save_detached_current
        source_tracker.source_for_document_with_evidence=lambda d,application=None:('','')
        preflight.save_detached_current=lambda d,output,DB=None:self.host
        try:
            result=preflight.primary_host_sources(
                [row],Obj(),DB=Obj(),output=self.root,
                detached_recovery=lambda r,feedback='':dict(action='SAVE_CURRENT'))
        finally:
            source_tracker.source_for_document_with_evidence=old_source
            preflight.save_detached_current=old_save
        self.assertEqual(result[0]['path'],self.host)
        self.assertEqual(result[0]['evidence'],'USER_SAVE_CURRENT')
        self.assertEqual(row.Mode,'SAVED_FILE')
        self.assertEqual(row.Source,self.host)
        self.assertIsNone(row.Document)
        self.assertEqual(row.DetachedRecovery,'USER_SAVE_CURRENT')

    def test_pathless_detached_host_can_browse_verified_existing_rvt(self):
        doc=Obj(PathName='',Title='Host_detached',IsModelInCloud=False,IsWorkshared=True)
        row=self.row(doc)
        from easybim_etransmit import source_tracker
        old_source=source_tracker.source_for_document_with_evidence
        old_verify=preflight.verify_detached_candidate
        source_tracker.source_for_document_with_evidence=lambda d,application=None:('','')
        preflight.verify_detached_candidate=lambda d,p,application=None,DB=None:'PROJECT_INFORMATION_UNIQUE_ID'
        try:
            result=preflight.primary_host_sources(
                [row],Obj(),DB=Obj(),output=self.root,
                detached_recovery=lambda r,feedback='':dict(action='BROWSE',path=self.host))
        finally:
            source_tracker.source_for_document_with_evidence=old_source
            preflight.verify_detached_candidate=old_verify
        self.assertEqual(result[0]['evidence'],'USER_BROWSE_PROJECT_INFORMATION_UNIQUE_ID')
        self.assertEqual(row.Mode,'SAVED_FILE')
        self.assertIsNone(row.Document)
        self.assertEqual(row.Source,self.host)

    def test_wrong_browsed_rvt_is_rejected_and_recovery_can_browse_again(self):
        wrong=os.path.join(self.root,'Wrong.rvt')
        with open(wrong,'wb') as out:out.write(compound(suffix='wrong'))
        doc=Obj(PathName='',Title='Host_detached',IsModelInCloud=False,IsWorkshared=True)
        row=self.row(doc)
        from easybim_etransmit import source_tracker
        old_source=source_tracker.source_for_document_with_evidence
        old_verify=preflight.verify_detached_candidate
        source_tracker.source_for_document_with_evidence=lambda d,application=None:('','')
        calls=[]
        def verify(d,p,application=None,DB=None):
            if p==wrong:
                raise preflight.PreflightError('The selected RVT does not appear to be the same model.')
            return 'PROJECT_INFORMATION_UNIQUE_ID'
        def recover(r,feedback=''):
            calls.append(feedback)
            return dict(action='BROWSE',path=wrong if len(calls)==1 else self.host)
        preflight.verify_detached_candidate=verify
        try:
            result=preflight.primary_host_sources(
                [row],Obj(),DB=Obj(),output=self.root,detached_recovery=recover)
        finally:
            source_tracker.source_for_document_with_evidence=old_source
            preflight.verify_detached_candidate=old_verify
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0],'')
        self.assertIn('same model',calls[1])
        self.assertEqual(result[0]['path'],self.host)

    def test_browse_identity_accepts_matching_worksharing_central_without_opening_candidate(self):
        central='Autodesk Docs://Project X/Host.rvt'
        class BasicInfo(object):
            CentralPath=central
            Format='2026'
            IsWorkshared=True
            def Dispose(self):pass
        class BasicFileInfo(object):
            @staticmethod
            def Extract(path):return BasicInfo()
        class ModelPathUtils(object):
            @staticmethod
            def ConvertModelPathToUserVisiblePath(value):return central
        DB=Obj(BasicFileInfo=BasicFileInfo,ModelPathUtils=ModelPathUtils)
        doc=Obj(Title='Host_detached',IsWorkshared=True,
                GetWorksharingCentralModelPath=lambda:'model-path',
                ProjectInformation=Obj(UniqueId='live-project-id'))
        self.assertEqual(
            preflight.verify_detached_candidate(doc,self.host,application=None,DB=DB),
            'CENTRAL_IDENTITY')

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

    def test_detached_recovery_dialog_uses_approved_wording_and_choices(self):
        ui=os.path.join(ROOT,'lib','easybim_etransmit','ui.py')
        with io.open(ui,encoding='utf-8') as inp:text=inp.read()
        self.assertIn("eTransmit couldn't locate the detached model.",text)
        self.assertIn('So please make the following decisions to transmit:',text)
        self.assertIn('Option 1 — Save current detached model and transmit',text)
        self.assertIn('Option 2 — Load model from file location…',text)
        self.assertIn('Load another model from file location…',text)
        self.assertNotIn('Browse another RVT',text)
        self.assertIn('Save current changes and transmit',text)
        self.assertIn('Do not include current changes',text)
        self.assertIn('selected model does not match',text)
        self.assertIn("getattr(row.Document, 'IsDetached', False)",text)

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
