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

from easybim_etransmit import preflight, engine, files as f, session
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
        result=preflight.primary_host_sources(
            [row],Obj(),DB=Obj(),output=self.root,
            detached_recovery=lambda r,feedback='':dict(action='BROWSE',path=self.host))
        self.assertEqual(result[0]['path'],self.host)
        self.assertEqual(result[0]['evidence'],'USER_BROWSE')
        self.assertEqual(row.Mode,'LIVE_DOCUMENT')
        self.assertIs(row.Document,doc)
        self.assertEqual(row.ResolvedSource,self.host)
        self.assertEqual(row.DetachedRecovery,'USER_BROWSE')

    def test_pathless_detached_host_can_save_current_state_and_keep_live_link_context(self):
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
        self.assertEqual(row.Mode,'LIVE_DOCUMENT')
        self.assertEqual(row.Source,self.host)
        self.assertEqual(row.ResolvedSource,self.host)
        self.assertIs(row.Document,doc)
        self.assertEqual(row.DetachedRecovery,'USER_SAVE_CURRENT')

    def test_pathless_detached_host_accepts_explicit_existing_rvt_without_identity_match(self):
        doc=Obj(PathName='',Title='Host_detached',IsModelInCloud=False,IsWorkshared=True)
        row=self.row(doc)
        from easybim_etransmit import source_tracker
        old_source=source_tracker.source_for_document_with_evidence
        source_tracker.source_for_document_with_evidence=lambda d,application=None:('','')
        try:
            result=preflight.primary_host_sources(
                [row],Obj(),DB=Obj(),output=self.root,
                detached_recovery=lambda r,feedback='':dict(action='BROWSE',path=self.host))
        finally:
            source_tracker.source_for_document_with_evidence=old_source
        self.assertEqual(result[0]['evidence'],'USER_BROWSE')
        self.assertEqual(row.Mode,'LIVE_DOCUMENT')
        self.assertIs(row.Document,doc)
        self.assertEqual(row.Source,self.host)
        self.assertEqual(row.ResolvedSource,self.host)
        self.assertEqual(row.DetachedRecovery,'USER_BROWSE')

    def test_explicit_browse_accepts_user_selected_native_rvt_without_model_comparison(self):
        alternate=os.path.join(self.root,'Alternate.rvt')
        with open(alternate,'wb') as out:out.write(compound(suffix='alternate'))
        doc=Obj(PathName='',Title='Host_detached',IsModelInCloud=False,IsWorkshared=True)
        row=self.row(doc)
        from easybim_etransmit import source_tracker
        old_source=source_tracker.source_for_document_with_evidence
        source_tracker.source_for_document_with_evidence=lambda d,application=None:('','')
        try:
            result=preflight.primary_host_sources(
                [row],Obj(),DB=Obj(),output=self.root,
                detached_recovery=lambda r,feedback='':dict(action='BROWSE',path=alternate))
        finally:
            source_tracker.source_for_document_with_evidence=old_source
        self.assertEqual(result[0]['path'],alternate)
        self.assertEqual(result[0]['evidence'],'USER_BROWSE')
        self.assertEqual(row.Source,alternate)
        self.assertEqual(row.Mode,'LIVE_DOCUMENT')
        self.assertIs(row.Document,doc)
        self.assertFalse(hasattr(preflight,'verify_detached_candidate'))

    def test_detached_merge_keeps_live_acc_link_absent_from_transmission_data(self):
        project='11111111-1111-1111-1111-111111111111'
        model='22222222-2222-2222-2222-222222222222'
        live=[dict(kind='RevitLink',element_id='7',id='7',source='open://child/ACC.rvt',
                   special='external',link_name='ACC Mechanical',
                   cloud_identity=dict(project_guid=project,model_guid=model,region='US'))]
        merged=session.merge_detached_revit_rows([],live)
        self.assertEqual(len(merged),1)
        self.assertEqual(merged[0]['element_id'],'7')
        self.assertEqual(merged[0]['source'],'open://child/ACC.rvt')
        self.assertEqual(merged[0]['source_evidence'],'DETACHED_LIVE_EXTERNAL_RESOURCE')

    def test_detached_merge_enriches_saved_link_with_live_cloud_identity(self):
        project='11111111-1111-1111-1111-111111111111'
        model='22222222-2222-2222-2222-222222222222'
        saved=[dict(kind='RevitLink',element_id='7',id='7',
                    source='Autodesk Docs://Project/Mechanical.rvt',td=True)]
        live=[dict(kind='RevitLink',element_id='7',id='7',source='open://child/Mechanical.rvt',
                   special='external',link_name='Mechanical',
                   cloud_identity=dict(project_guid=project,model_guid=model,region='US'))]
        merged=session.merge_detached_revit_rows(saved,live)
        self.assertEqual(len(merged),1)
        self.assertEqual(merged[0]['source'],'Autodesk Docs://Project/Mechanical.rvt')
        self.assertEqual(merged[0]['cloud_identity']['model_guid'],model)
        self.assertEqual(merged[0]['link_name'],'Mechanical')

    def test_detached_merge_does_not_invent_live_only_local_file_link(self):
        live=[dict(kind='RevitLink',element_id='8',id='8',
                   source=self.host,special='native',link_name='Local')]
        self.assertEqual(session.merge_detached_revit_rows([],live),[])

    def test_detached_browse_preselects_unloaded_acc_link_for_temporary_reload(self):
        project='11111111-1111-1111-1111-111111111111'
        model='22222222-2222-2222-2222-222222222222'
        row=dict(kind='RevitLink',element_id='7',id='7',source='',
                 link_name='ACC Mechanical',
                 cloud_identity=dict(project_guid=project,model_guid=model,region='US'))
        link=Obj(Id=Obj(IntegerValue=7),IsNestedLink=False,LocallyUnloaded=False)
        instance=Obj(GetTypeId=lambda:Obj(IntegerValue=7))
        doc=Obj()
        entry=dict(name='Host.rvt',document=doc,detached_recovery='USER_BROWSE',
                   inventory=dict(references=[row]))
        class LinkType(object):
            @staticmethod
            def IsLoaded(document,ident):return False
        registry=Obj(cancelled=None,
                     DB=Obj(RevitLinkType=LinkType),
                     scanner=Obj(elements=lambda document,name:
                         [instance] if name=='RevitLinkInstance' else [link]),
                     get=lambda key:entry)
        choices=preflight.unloaded_links(registry,['host'])
        self.assertEqual(len(choices),1)
        self.assertTrue(choices[0].Checked)
        self.assertIn('ACC/cloud link',choices[0].Availability)

    def test_detached_recovery_preselects_unloaded_link_without_readable_saved_rvt(self):
        row=dict(kind='RevitLink',element_id='9',id='9',source='',
                 link_name='Unresolved Link')
        link=Obj(Id=Obj(IntegerValue=9),IsNestedLink=False,LocallyUnloaded=False)
        instance=Obj(GetTypeId=lambda:Obj(IntegerValue=9))
        doc=Obj()
        entry=dict(name='Host.rvt',document=doc,detached_recovery='USER_BROWSE',
                   inventory=dict(references=[row]))
        class LinkType(object):
            @staticmethod
            def IsLoaded(document,ident):return False
        registry=Obj(cancelled=None,DB=Obj(RevitLinkType=LinkType),
                     scanner=Obj(elements=lambda document,name:
                         [instance] if name=='RevitLinkInstance' else [link]),
                     get=lambda key:entry)
        choices=preflight.unloaded_links(registry,['host'])
        self.assertEqual(len(choices),1)
        self.assertTrue(choices[0].Checked)
        self.assertIn('No directly readable saved RVT',choices[0].Availability)

    def test_detached_browse_does_not_preselect_unloaded_server_file_link(self):
        row=dict(kind='RevitLink',element_id='8',id='8',
                 source=self.host,link_name='Architecture')
        link=Obj(Id=Obj(IntegerValue=8),IsNestedLink=False,LocallyUnloaded=False)
        instance=Obj(GetTypeId=lambda:Obj(IntegerValue=8))
        doc=Obj()
        entry=dict(name='Host.rvt',document=doc,detached_recovery='USER_BROWSE',
                   inventory=dict(references=[row]))
        class LinkType(object):
            @staticmethod
            def IsLoaded(document,ident):return False
        registry=Obj(cancelled=None,
                     DB=Obj(RevitLinkType=LinkType),
                     scanner=Obj(elements=lambda document,name:
                         [instance] if name=='RevitLinkInstance' else [link]),
                     get=lambda key:entry)
        choices=preflight.unloaded_links(registry,['host'])
        self.assertEqual(len(choices),1)
        self.assertFalse(choices[0].Checked)
        self.assertIn('Saved file available',choices[0].Availability)

    def test_unloaded_reload_list_excludes_unplaced_outdated_link_types(self):
        placed_row=dict(kind='RevitLink',element_id='7',id='7',source=self.host,
                        link_name='Placed Mechanical')
        unused_row=dict(kind='RevitLink',element_id='99',id='99',source=self.host,
                        link_name='_OUTDATED_unused.rvt')
        placed_type=Obj(Id=Obj(IntegerValue=7),IsNestedLink=False,LocallyUnloaded=False)
        unused_type=Obj(Id=Obj(IntegerValue=99),IsNestedLink=False,LocallyUnloaded=False)
        instance=Obj(GetTypeId=lambda:Obj(IntegerValue=7))
        doc=Obj()
        entry=dict(name='Host.rvt',document=doc,inventory=dict(references=[placed_row,unused_row]))
        class LinkType(object):
            @staticmethod
            def IsLoaded(document,ident):return False
        def elements(document,name):
            return [instance] if name=='RevitLinkInstance' else [placed_type,unused_type]
        registry=Obj(cancelled=None,DB=Obj(RevitLinkType=LinkType),
                     scanner=Obj(elements=elements),get=lambda key:entry)
        choices=preflight.unloaded_links(registry,['host'])
        self.assertEqual([x.Name for x in choices],['Placed Mechanical'])

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
        self.assertNotIn('selected model does not match',text)
        self.assertIn('eTransmit cannot locate the detached model. Please load from selected file locations.',text)
        self.assertNotIn('selected file unavailable',text)
        self.assertIn('Save current changes and transmit',text)
        self.assertIn('Do not include current changes',text)
        self.assertNotIn('force_saved_host_inspection_sources',text)
        self.assertIn("getattr(row.Document, 'IsDetached', False)",text)
        self.assertIn("entry['detached_recovery']=row.DetachedRecovery",text)
        self.assertIn("DetachedRecovery','')!='USER_BROWSE'",text)

    def test_document_opening_hook_stamps_pending_source_with_current_journal(self):
        hook=os.path.join(ROOT,'hooks','doc-opening.py')
        with io.open(hook,encoding='utf-8') as inp:text=inp.read()
        self.assertIn("_app = getattr(__revit__, \"Application\", None)",text)
        self.assertIn('application=_app',text)

    def test_batch_preflight_collects_reload_decision_before_batch_processing(self):
        ui=os.path.join(ROOT,'lib','easybim_etransmit','ui.py')
        with io.open(ui,encoding='utf-8') as inp:text=inp.read()
        chooser=text.index('chooser=UnloadedLinksDialog(unloaded)')
        reload_plan=text.index('reload_selection=list(chooser.result)')
        batch=text.index("trace.write(uiapp,'ET_STEP_16_BATCH_START'")
        self.assertLess(chooser,reload_plan)
        self.assertLess(reload_plan,batch)
        self.assertIn('All decisions are complete here.',text)

    def test_reload_dialog_exposes_select_all_and_select_none(self):
        xaml=os.path.join(ROOT,'lib','easybim_etransmit','unloaded_links.xaml')
        ui=os.path.join(ROOT,'lib','easybim_etransmit','ui.py')
        with io.open(xaml,encoding='utf-8') as inp:x=inp.read()
        with io.open(ui,encoding='utf-8') as inp:u=inp.read()
        self.assertIn('Content="Select All"',x)
        self.assertIn('Content="Select None"',x)
        self.assertIn('Click="select_all"',x)
        self.assertIn('Click="select_none"',x)
        self.assertIn('def select_all(',u)
        self.assertIn('def select_none(',u)

    def test_command_contains_crash_boundaries_and_no_runtime_registration(self):
        ui=os.path.join(ROOT,'lib','easybim_etransmit','ui.py')
        with io.open(ui,encoding='utf-8') as inp:text=inp.read()
        for marker in ('ET_STEP_07_SAVE_PREFLIGHT_DONE','ET_STEP_08_PROGRESS_LOOKUP_START',
                       'ET_STEP_11_REGISTRY_READY','ET_STEP_13_LIVE_DOCUMENT_REGISTER_DONE',
                       'ET_STEP_16_BATCH_START','ET_STEP_17_BATCH_DONE'):
            self.assertIn(marker,text)
        self.assertNotIn('register_dockable_panel',text)

if __name__=='__main__':unittest.main(verbosity=2)
