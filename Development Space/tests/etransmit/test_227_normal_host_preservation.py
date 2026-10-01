# -*- coding: utf-8 -*-
"""Non-transmitted deliveries, including failed repairs and incomplete inventory.

Real filesystem acquisition/delivery/rollback; Autodesk serialization is a fixture,
not a Revit integration test. No client model names or report data are embedded.
"""
from __future__ import unicode_literals
import copy
import io
import os
import shutil
import unittest
import test_226_host_copy_delivery as fixtures
from test_224_copy_flow import Reference
from easybim_etransmit import batch, engine, files as f, session, worker
from test_212_cache import Obj, P, M


class NormalHostPreservation(unittest.TestCase):
    setUp = fixtures.HostCopyDelivery.__dict__['setUp']
    prepare = fixtures.HostCopyDelivery.__dict__['prepare']
    setup_case = fixtures.HostCopyDelivery.__dict__['setup_case']
    run_case = fixtures.HostCopyDelivery.__dict__['run_case']

    def assert_original(self, result, host, case):
        self.assertEqual(host['status'], 'COPIED', repr(result['issues']))
        self.assertTrue(os.path.isfile(host['target']), repr(result['issues']))
        self.assertEqual(f.digest(host['target']), f.digest(self.original))
        self.assertFalse(case[3].read(host['target']).IsTransmitted)
        self.assertFalse(host.get('host_finalized'))
        self.assertFalse(host.get('saved_references_checked'))
        self.assertFalse(host.get('metadata_repathed'))
        self.assertEqual(engine.package_counts(result)['hosts_copied'], 1)
        for row in result['references']:
            self.assertNotIn(row.get('repath'), ('TRANSMISSION_DATA','API_LOCAL_LINK_RELATIVE'))
            self.assertFalse(row.get('verification'))
        self.assertEqual(dict((p, (f.digest(p), os.stat(p).st_mtime)) for p in case[4]), case[4])

    def test_missing_link_matches_216_unchanged_host_not_226_transmitted_copy(self):
        case = self.setup_case(missing=True)
        result, host = self.run_case(case, f.defaults())
        self.assert_original(result, host, case)
        self.assertEqual(case[5], [])
        cad = next(r for r in result['references'] if r['kind']=='CADLink')
        self.assertTrue(os.path.isfile(cad['target']))
        self.assertEqual(case[3].writes, [], 'No metadata write on the preserved host')

    def test_failed_worker_restores_original_in_delivery_not_just_external_recovery(self):
        case = self.setup_case()
        result, host = self.run_case(case, f.defaults())
        self.assertEqual(case[5], [True], 'Repath must attempt normal serialization, not TD-only delivery')
        self.assert_original(result, host, case)
        self.assertTrue(any(i['code']=='MODEL_PROCESSING_FAILED' for i in result['issues']))

    def test_partial_transmission_write_is_rolled_back_without_a_detached_delivery(self):
        case = self.setup_case()
        def fail(app, stage, target, rows, options, **kw):
            case[5].append(True)
            td=case[3].read(target);td.IsTransmitted=True;case[3].write(target,td)
            for row in rows:row.update(repath='TRANSMISSION_DATA',verification='SAVED_REFERENCE_CHECKED')
            raise RuntimeError('Interrupted native save')
        worker.run_separate_revit=fail
        result, host = self.run_case(case, f.defaults())
        self.assertEqual(case[5], [True])
        self.assert_original(result, host, case)

    def test_worker_claim_cannot_hide_transmitted_output(self):
        case = self.setup_case()
        def dishonest(app, stage, target, rows, options, **kw):
            td=case[3].read(target);td.IsTransmitted=True;case[3].write(target,td)
            return dict(issues=[],host_finalized=True,saved_references_checked=True,worker_repaired=True)
        worker.run_separate_revit=dishonest
        result, host = self.run_case(case, f.defaults())
        self.assert_original(result, host, case)
        self.assertTrue(any(i['code']=='MODEL_PROCESSING_FAILED' for i in result['issues']))

    def test_successful_normal_save_keeps_real_saved_paths_and_clears_transmitted(self):
        case = self.setup_case()
        def save(app, stage, target, rows, options, **kw):
            case[5].append(True)
            self.assertTrue(options['independent_host'])
            self.assertEqual(f.digest(target), f.digest(self.original))
            td=case[3].read(stage)
            for row in rows:
                if row.get('target'):
                    td.saved[row['id']]=Reference(os.path.relpath(row['target'],os.path.dirname(target)),
                                                  row['kind'], row.get('package_loaded') is not False,'Relative')
                    row.update(repath='API_LOCAL_LINK_RELATIVE',verification='SAVED_REFERENCE_CHECKED')
            td.desired={};td.IsTransmitted=False;case[3].write(target,td)
            return dict(issues=[],host_finalized=True,saved_references_checked=True,
                        worker_repaired=True,independent_package_central=True)
        worker.run_separate_revit=save
        result, host = self.run_case(case, f.defaults())
        self.assertEqual(case[5], [True])
        self.assertEqual(host['status'],'COPIED',repr(result['issues']))
        self.assertTrue(host['host_finalized'])
        self.assertEqual(host['transmission_status'],'NOT_TRANSMITTED')
        self.assertFalse(case[3].read(host['target']).IsTransmitted)
        self.assertEqual(host['opening_guidance'],'OPEN_NORMALLY_INDEPENDENT_PACKAGE')

    def test_copy_only_does_not_detach_save_or_mark_transmitted(self):
        case=self.setup_case();opts=f.defaults();opts['repath']=False
        result,host=self.run_case(case,opts)
        self.assert_original(result,host,case)
        self.assertEqual(case[5],[])
        self.assertEqual(case[3].writes,[])
        self.assertEqual(host['opening_guidance'],'SOURCE_OPENING_STATE_PRESERVED')
        with io.open(os.path.join(case[1],'START_HERE.txt'),encoding='utf-8') as inp:report=inp.read()
        self.assertNotIn('Open workshared copies with Detach from Central',report)
        self.assertIn('unchanged',report.lower())

    def test_unconfirmed_worker_never_overwrites_live_candidate_or_archives_it(self):
        case=self.setup_case()
        class Running(RuntimeError):worker_may_be_running=True
        def running(app,stage,target,rows,options,**kw):
            case[5].append(True)
            with open(target,'wb') as out:out.write(b'worker still owns target')
            raise Running('Process exit unconfirmed')
        worker.run_separate_revit=running
        result,host=self.run_case(case,f.defaults())
        self.assertEqual(case[5],[True])
        self.assertTrue(host['worker_exit_unconfirmed'])
        self.assertEqual(host['status'],'NOT_FINALIZED')
        self.assertEqual(f.digest(host['recovery_path']),f.digest(self.original))
        with open(host['target'],'rb') as inp:self.assertEqual(inp.read(),b'worker still owns target')
        self.assertTrue(batch.unfinished_archive_files([result]))


class ExternalLinkInventory(unittest.TestCase):
    setUp = fixtures.HostCopyDelivery.__dict__['setUp']

    def test_modified_local_host_does_not_drop_live_external_link_or_treat_it_as_saved(self):
        local=os.path.join(self.root,'Local.rvt')
        with open(local,'wb') as out:out.write(self.payload)
        self.doc.PathName=local;self.doc.IsModelInCloud=False;self.doc.IsModified=True
        key=self.r.add_live(self.doc)
        live=dict(id='77',element_id='77',kind='RevitLink',special='external',source='',
                  cloud_identity=dict(project_guid=P,model_guid=M,region='US'),link_name='Architecture.rvt',
                  loaded=False,td=False)
        self.r.get(key)['inventory']['references']=[live]
        backend=session.SessionBackend(self.db,self.app,self.root,self.r)
        backend.rows=lambda *a:[]
        inventory=backend.inventory_before_copy(key,f.defaults())
        self.assertEqual(len(inventory['references']),1,'External link disappeared from inventory')
        row=inventory['references'][0]
        self.assertTrue(row.get('resolution_failed'),'Unknown saved membership must not cause a guessed cache binding')
        self.assertTrue(row.get('saved_membership_unverified'))
        self.assertEqual(row['element_id'],'77')
        self.assertFalse(row.get('source','').startswith('cache://'))
        self.assertTrue(any(i['code']=='SAVED_EXTERNAL_LINK_UNVERIFIED' for i in inventory['issues']))
        self.assertNotIn('resolution_failed',live,'Never mutate source inventory')

    def test_exact_saved_revision_can_supply_live_external_resource_membership(self):
        local=os.path.join(self.root,'Local.rvt')
        with open(local,'wb') as out:out.write(self.payload)
        self.doc.PathName=local;self.doc.IsModelInCloud=False;self.doc.IsModified=False
        key=self.r.add_live(self.doc)
        entry=self.r.get(key)
        entry['document_version']=dict(guid='matching-version',saves=8)
        self.db.BasicFileInfo=Obj(Extract=lambda path:Obj(GetDocumentVersion=lambda:
                                   Obj(VersionGUID='matching-version',NumberOfSaves=8)))
        entry['inventory']['references']=[dict(id='77',element_id='77',kind='RevitLink',
            special='external',source='',cloud_identity=dict(project_guid=P,model_guid=M,region='US'),
            link_name='Architecture.rvt',loaded=False,td=False)]
        backend=session.SessionBackend(self.db,self.app,self.root,self.r);backend.rows=lambda *a:[]
        rows=backend.inventory_before_copy(key,f.defaults())['references']
        self.assertEqual(len(rows),1)
        self.assertFalse(rows[0].get('resolution_failed'))
        self.assertTrue(rows[0]['source'].startswith('cache://'))

    def test_saved_file_link_remains_authoritative_over_unsaved_live_retarget(self):
        local=os.path.join(self.root,'Local.rvt')
        with open(local,'wb') as out:out.write(self.payload)
        self.doc.PathName=local;self.doc.IsModelInCloud=False
        key=self.r.add_live(self.doc)
        self.r.get(key)['inventory']['references']=[dict(id='1',element_id='1',kind='RevitLink',
          source='cloud://changed',special='external',td=False)]
        backend=session.SessionBackend(self.db,self.app,self.root,self.r)
        backend.rows=lambda *a:[dict(id='1',element_id='1',kind='RevitLink',source='/saved.rvt',td=True)]
        rows=backend.inventory_before_copy(key,f.defaults())['references']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['source'],'/saved.rvt')


class FinalSavedState(unittest.TestCase):
    def test_saveas_must_not_leave_repair_document_detached(self):
        from easybim_etransmit.revit import Backend
        db=Obj(SaveAsOptions=lambda:Obj(SetWorksharingOptions=lambda value:None),
               WorksharingSaveAsOptions=lambda:Obj())
        doc=Obj(IsWorkshared=True,IsModified=False,IsDetached=True,PathName='/package/Host.rvt',
                SaveAs=lambda path,options:None)
        backend=Backend(db,Obj(),'/package')
        with self.assertRaises(RuntimeError):
            backend._save_as_independent_package_central(doc,'/package/Host.rvt',True)

    def test_dialog_does_not_tell_recipient_to_detach_the_export(self):
        path=os.path.join(os.path.dirname(__file__),'..','..','..','EasyBIM.tab',
                          'Links.panel','e-transmit.pushbutton','window.xaml')
        with io.open(path,encoding='utf-8') as inp:text=inp.read()
        self.assertNotIn('Open workshared copies with Detach from Central',text)
        self.assertIn('Keep the original host if repairs fail',text)


if __name__=='__main__':unittest.main(verbosity=2)
