# -*- coding: utf-8 -*-
"""Independent ordinary host delivery, with the isolated Revit API substituted."""
from __future__ import unicode_literals
import copy
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'lib'))
from easybim_etransmit import engine, files as f, session, worker
from test_212_cache import Obj
from test_payload_acquisition import compound


class FinalizingBoundary(object):
    independent_host_supported = True

    def __init__(self, testcase, host, references, output, outcome='success', workshared=True):
        self.testcase = testcase
        self.host = host
        self.references = references
        self.output = output
        self.outcome = outcome
        self.workshared = workshared
        self.calls = []

    def set_staging_root(self, path): self.staging_root = path
    def can_deliver_direct(self, *args): return True

    def scan(self, source, stage, options):
        if self.outcome == 'scan_error' and source == self.host:
            raise RuntimeError('Native scan failed before worksharing could be read')
        return dict(references=copy.deepcopy(self.references) if source == self.host else [],
                    issues=[], version='2024', opened_in_revit=False, is_workshared=self.workshared)

    def source_context(self, source):
        if self.outcome == 'outer_failure':
            raise RuntimeError('Fatal source context read before finalization scheduling')
        return {}

    def finish(self, stage, target, rows, options):
        self.calls.append(dict(stage=stage, target=target, rows=copy.deepcopy(rows),
                               options=dict(options)))
        self.testcase.assertTrue(options['independent_host'])
        self.testcase.assertTrue(f.within(target, self.output))
        for row in rows:
            self.testcase.assertTrue(os.path.isfile(row['target']))
            self.testcase.assertTrue(f.within(row['target'], self.output))
        self.testcase.assertEqual(f.digest(stage), f.digest(self.host))
        shutil.copyfile(stage, target)
        with open(target, 'ab') as stream: stream.write(b'ordinary independent package central')
        if self.outcome == 'exception': raise RuntimeError('Revit SaveAs failed')
        if self.outcome == 'cancel': raise f.Cancelled()
        if self.outcome == 'incomplete':
            return dict(issues=[], worker_repaired=True, verified_in_process=False)
        if self.outcome == 'error':
            return dict(issues=[engine.issue('HOST_SAVE_FAILED', self.host, 'Revit SaveAs failed', 'error')],
                        host_finalized=False, saved_references_checked=False)
        if options.get('repath'):
            for row in rows:
                row['repath'] = 'API_LOCAL_LINK_RELATIVE' if row['kind'] == 'RevitLink' else 'API_CAD_LINK'
                row['verification'] = 'SAVED_REFERENCE_PATH_AND_LOAD_CHECKED'
        return dict(issues=[], host_finalized=True, saved_references_checked=True,
                    verification_status='SAVED_REFERENCES_CHECKED',
                    worker_repaired=True, verified_in_process=False,
                    independent_package_central=self.workshared,
                    original_central_association_preserved=False)

    def mark_transmitted_package(self, *args):
        self.testcase.fail('Final ordinary host must never be marked transmitted')

    def verify_package(self, *args):
        self.testcase.fail('Parent must not reopen the host after isolated saved-reference verification')


class NormalHostDelivery(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ET225_normal_')
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source = os.path.join(self.root, 'source')
        os.makedirs(self.source)
        self.host = self.put('Host.rvt', compound(suffix='host'))
        self.link = self.put('Architecture.rvt', compound(suffix='link'))
        self.cad = self.put('Plan.dwg', b'linked CAD')
        self.output = os.path.join(self.root, 'package')
        self.references = [dict(id='1', element_id='1', kind='RevitLink', source=self.link,
                                td=True, special='native', loaded=False),
                           dict(id='2', element_id='2', kind='CADLink', source=self.cad,
                                td=True, special='native', loaded=True)]
        self.before = dict((path, (f.digest(path), os.stat(path).st_mtime))
                           for path in (self.host, self.link, self.cad))

    def put(self, name, payload):
        path = os.path.join(self.source, name)
        with open(path, 'wb') as stream: stream.write(payload)
        return path

    def transmit(self, outcome='success', repath=True, references=None, cancelled=None, pulse=None, workshared=True):
        backend = FinalizingBoundary(self, self.host,
                                      self.references if references is None else references,
                                      self.output, outcome, workshared)
        options = dict(f.defaults(), simple_repath=False)
        options.update(repath=repath, cleanup=False, upgrade=False)
        result = engine.transmit([self.host], self.output, backend, options, cancelled=cancelled, pulse=pulse)
        recovery = result.get('recovery_directory')
        if recovery: self.addCleanup(f.remove_tree_retry, recovery)
        host = next(row for row in result['files'] if row.get('is_primary_host'))
        return result, host, backend

    def assert_sources_preserved(self):
        self.assertEqual(dict((path, (f.digest(path), os.stat(path).st_mtime))
                              for path in self.before), self.before)

    def test_primary_host_finalizes_after_all_dependencies_are_delivered(self):
        result, host, backend = self.transmit()
        self.assertEqual(len(backend.calls), 1, repr(result['issues']))
        self.assertTrue(backend.calls[0]['options']['independent_host'])
        self.assertEqual(backend.calls[0]['target'], host['target'])
        self.assertEqual(host['status'], 'COPIED', repr(result['issues']))
        self.assertTrue(host['host_finalized'])
        self.assertTrue(host['independent_package_central'])
        self.assertEqual(host['model_verification'], 'SAVED_REFERENCES_CHECKED')
        self.assertEqual(host['transmission_status'], 'NOT_TRANSMITTED')
        self.assertEqual(host['opening_guidance'], 'OPEN_NORMALLY_INDEPENDENT_PACKAGE')
        self.assertFalse(host['original_central_association_preserved'])
        self.assertNotEqual(f.digest(host['target']), f.digest(self.host))
        for row in result['files']:
            if not row.get('is_primary_host'):
                self.assertEqual(f.digest(row['target']), f.digest(row['source']))
        self.assertEqual(engine.package_counts(result)['hosts_copied'], 1)
        self.assert_sources_preserved()

    def test_repath_off_still_saves_workshared_host_as_an_independent_normal_model(self):
        result, host, backend = self.transmit(repath=False)
        self.assertEqual(len(backend.calls), 1, repr(result['issues']))
        self.assertFalse(backend.calls[0]['options']['repath'])
        self.assertTrue(backend.calls[0]['options']['independent_host'])
        self.assertEqual(host['status'], 'COPIED')
        self.assertTrue(host['host_finalized'])
        self.assertEqual(host['transmission_status'], 'NOT_TRANSMITTED')
        self.assertNotEqual(f.digest(host['target']), f.digest(self.host))
        self.assert_sources_preserved()

    def test_nonworkshared_host_with_repath_off_still_requires_normal_finalization(self):
        with open(self.host, 'rb') as stream: payload = stream.read()
        payload = payload.replace('Worksharing: Central'.encode('utf-16-le'),
                                  'Worksharing: None   '.encode('utf-16-le'))
        with open(self.host, 'wb') as stream: stream.write(payload)
        self.before[self.host] = (f.digest(self.host), os.stat(self.host).st_mtime)
        result, host, backend = self.transmit(repath=False, workshared=False)
        self.assertEqual(len(backend.calls), 1, repr(result['issues']))
        self.assertTrue(backend.calls[0]['options']['independent_host'])
        self.assertFalse(backend.calls[0]['options']['repath'])
        self.assertFalse(host['is_workshared'])
        self.assertTrue(host['host_finalized'])
        self.assertFalse(host['independent_package_central'])
        self.assertEqual(host['status'], 'COPIED')
        self.assertEqual(host['transmission_status'], 'NOT_TRANSMITTED')
        self.assertEqual(host['model_verification'], 'SAVED_REFERENCES_CHECKED')
        self.assert_sources_preserved()

    def assert_unscheduled_host_recovered(self, outcome):
        result, host, backend = self.transmit(outcome=outcome, references=[])
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(backend.calls, [])
        self.assertFalse(host.get('finalize_after_delivery'))
        self.assertEqual(host['status'], 'NOT_FINALIZED', repr(host))
        self.assertFalse(host.get('host_finalized'))
        self.assertEqual(host['model_verification'], 'DEFERRED')
        self.assertFalse(os.path.isfile(host['target']))
        self.assertTrue(os.path.isfile(host['recovery_path']))
        self.assertFalse(f.within(host['recovery_path'], self.output))
        self.assertEqual(f.digest(host['recovery_path']), self.before[self.host][0])
        self.assertEqual(engine.package_counts(result)['hosts_copied'], 0)
        self.assert_sources_preserved()
        return result, host

    def test_scan_failure_with_unknown_worksharing_recovers_acquired_bytes(self):
        result, host = self.assert_unscheduled_host_recovered('scan_error')
        self.assertEqual(host['inventory_status'], 'FAILED')
        self.assertFalse(host.get('is_workshared'))
        self.assertTrue(any(item.get('operation') == 'inspect_model' for item in result['issues']))

    def test_fatal_failure_before_scheduling_cannot_publish_unfinalized_host(self):
        result, host = self.assert_unscheduled_host_recovered('outer_failure')
        self.assertTrue(any(item['code'] == 'HOST_FINALIZATION_NOT_COMPLETED' for item in result['issues']))

    def assert_original_recovered(self, outcome):
        result, host, backend = self.transmit(outcome)
        self.assertEqual(len(backend.calls), 1, repr(result['issues']))
        self.assertEqual(host['status'], 'NOT_FINALIZED', repr(host))
        self.assertFalse(host.get('host_finalized'))
        self.assertFalse(os.path.isfile(host['target']))
        self.assertTrue(os.path.isfile(host['recovery_path']))
        self.assertFalse(f.within(host['recovery_path'], self.output))
        self.assertEqual(f.digest(host['recovery_path']), self.before[self.host][0])
        self.assertEqual(engine.package_counts(result)['hosts_copied'], 0)
        self.assertEqual(result['status'], 'CANCELLED' if outcome == 'cancel' else 'FAILED')
        self.assert_sources_preserved()

    def test_failed_worker_result_retains_original_outside_package(self):
        self.assert_original_recovered('error')

    def test_worker_exception_retains_original_outside_package(self):
        self.assert_original_recovered('exception')

    def test_cancelled_worker_retains_original_outside_package(self):
        self.assert_original_recovered('cancel')

    def test_cancellation_before_worker_start_keeps_acquired_original_for_recovery(self):
        cancelled = [False]
        def pulse(label, current, total):
            if label.startswith('SAVING INDEPENDENT PACKAGE HOST | '):
                cancelled[0] = True
                raise f.Cancelled()
        result, host, backend = self.transmit(cancelled=lambda: cancelled[0], pulse=pulse)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(backend.calls, [])
        self.assertEqual(host['status'], 'NOT_FINALIZED')
        self.assertFalse(os.path.isfile(host['target']))
        self.assertTrue(os.path.isfile(host['recovery_path']))
        self.assertEqual(f.digest(host['recovery_path']), self.before[self.host][0])
        self.assertFalse(f.within(host['recovery_path'], self.output))
        self.assertEqual(engine.package_counts(result)['hosts_copied'], 0)
        self.assert_sources_preserved()

    def test_success_without_finalized_contract_is_not_a_completed_host(self):
        self.assert_original_recovered('incomplete')

    def test_missing_rvt_retains_original_recovery_without_finalizing_host(self):
        missing = os.path.join(self.source, 'Missing.rvt')
        refs = [dict(self.references[0], source=missing), self.references[1]]
        result, host, backend = self.transmit(references=refs)
        self.assertEqual(backend.calls, [])
        self.assertEqual(host['status'], 'NOT_FINALIZED', repr(host))
        self.assertEqual(f.digest(host['recovery_path']), self.before[self.host][0])
        self.assertFalse(f.within(host['recovery_path'], self.output))
        self.assertFalse(os.path.isfile(host['target']))
        cad = next(row for row in result['files'] if row['source'] == self.cad)
        self.assertEqual(cad['status'], 'COPIED')
        self.assertEqual(f.digest(cad['target']), self.before[self.cad][0])
        self.assertEqual(engine.package_counts(result)['hosts_copied'], 0)
        self.assert_sources_preserved()


class SavedAndLiveSessionFinalization(unittest.TestCase):
    def test_independent_host_forces_worker_for_saved_and_live_sources_with_simple_mode(self):
        root = tempfile.mkdtemp(prefix='ET225_session_')
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        calls = []
        original = worker.run_separate_revit
        def finalize(*args, **kwargs):
            calls.append((args, kwargs))
            return dict(issues=[], host_finalized=True, saved_references_checked=True,
                        independent_package_central=True, verification_status='SAVED_REFERENCES_CHECKED')
        worker.run_separate_revit = finalize
        self.addCleanup(setattr, worker, 'run_separate_revit', original)
        for mode, source in (('SAVED_FILE', os.path.join(root, 'Host.rvt')),
                             ('LIVE_DOCUMENT', 'open://host/Host.rvt')):
            registry = Obj(get=lambda key, m=mode, s=source: dict(mode=m) if key == s else None)
            backend = session.SessionBackend(None, Obj(VersionNumber='2024'), root, registry)
            backend.finish_metadata_copy = lambda *args: self.fail('Primary host must not bypass independent SaveAs')
            options = dict(repath=False, simple_repath=True, independent_host=True,
                           normalize_saved_cache=True, _host_source=source)
            result = backend.finish(os.path.join(root, 'stage.rvt'), os.path.join(root, 'final.rvt'), [], options)
            self.assertTrue(result['host_finalized'])
            self.assertTrue(calls[-1][0][4]['independent_host'])
            self.assertEqual(calls[-1][0][4]['_host_source'], source)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
