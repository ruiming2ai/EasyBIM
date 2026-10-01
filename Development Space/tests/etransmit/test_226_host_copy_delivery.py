# -*- coding: utf-8 -*-
"""A copied host is deliverable without an independent-model Revit worker.

The production acquisition, engine, filesystem, and metadata repath run here.
Only Autodesk's TransmissionData serialization is substituted; these are not
Revit load tests. A worker unavailable on the user's machine must not remove a
successfully acquired host in the default copy-first mode.
"""
from __future__ import unicode_literals
import io
import os
import zipfile
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'lib'))
from easybim_etransmit import engine, files as f, worker
import test_212_session as cache_fixtures
import test_224_copy_flow as flow


class HostCopyDelivery(unittest.TestCase):
    setUp = cache_fixtures.SavedCacheSession.__dict__['setUp']
    prepare = flow.LiveCacheNativeDelivery.__dict__['prepare']

    def setup_case(self, missing=False):
        key, output, backend, td, before = self.prepare(missing=missing)
        calls = []
        def unavailable(*args, **kwargs):
            calls.append(True)
            raise RuntimeError('Independent Revit worker unavailable')
        worker.run_separate_revit = unavailable
        return key, output, backend, td, before, calls

    def run_case(self, case, options):
        key, output, backend, td, before, calls = case
        result = engine.transmit([key], output, backend, options)
        if result.get('recovery_directory'):
            self.addCleanup(f.remove_tree_retry, result['recovery_directory'])
        host = next(row for row in result['files'] if row.get('is_primary_host'))
        return result, host

    def assert_copy_delivered(self, result, host, case):
        key, output, backend, td, before, calls = case
        self.assertTrue(os.path.isfile(host['target']),
                        'A copied host disappeared from its package: ' + repr(result['issues']))
        self.assertEqual(host['target'], os.path.join(output, 'Host.rvt'))
        self.assertEqual(host['status'], 'COPIED')
        self.assertEqual(engine.package_counts(result)['hosts_copied'], 1)
        self.assertFalse(host.get('host_finalized'), 'Copying must not claim independent serialization')
        self.assertEqual(calls, [], 'Default copy-first delivery must not depend on a Revit worker')
        self.assertEqual(dict((p, (f.digest(p), os.stat(p).st_mtime)) for p in before), before)

    def test_repath_off_delivers_cache_bytes_without_worker(self):
        case = self.setup_case()
        options = f.defaults(); options['repath'] = False
        result, host = self.run_case(case, options)
        self.assert_copy_delivered(result, host, case)
        self.assertEqual(f.digest(host['target']), f.digest(self.original))
        self.assertEqual(case[3].writes, [])
        self.assertEqual(host['opening_guidance'], 'DETACH_RECOMMENDED_FOR_WORKSHARED_COPY')

    def test_default_repath_delivers_host_and_persists_native_paths(self):
        case = self.setup_case()
        result, host = self.run_case(case, f.defaults())
        self.assert_copy_delivered(result, host, case)
        self.assertTrue(host.get('metadata_repathed'))
        self.assertEqual(host['model_verification'], 'TRANSMISSION_DATA_CHECKED')
        packaged = case[3].read(host['target'])
        self.assertTrue(packaged.IsTransmitted)
        self.assertEqual(host.get('transmission_status'), 'TRANSMITTED')
        self.assertEqual(host['opening_guidance'], 'OPEN_AS_TRANSMITTED_MODEL')
        for row in result['references']:
            self.assertEqual(row.get('repath'), 'TRANSMISSION_DATA')
            self.assertEqual(f.canonical(f.resolve_source(packaged.desired[row['id']].path, host['target'])),
                             f.canonical(row['target']))
        self.assertFalse(packaged.desired['1'].loaded)
        self.assertTrue(packaged.desired['2'].loaded)

    def test_missing_rvt_does_not_remove_host_or_block_native_cad_repath(self):
        case = self.setup_case(missing=True)
        result, host = self.run_case(case, f.defaults())
        self.assert_copy_delivered(result, host, case)
        cad = next(row for row in result['references'] if row['kind'] == 'CADLink')
        missing = next(row for row in result['references'] if row['kind'] == 'RevitLink')
        self.assertEqual(cad.get('repath'), 'TRANSMISSION_DATA')
        self.assertTrue(os.path.isfile(cad['target']))
        self.assertFalse(missing.get('target'))
        self.assertTrue(any(row['severity'] == 'error' for row in result['issues']))
        packaged = case[3].read(host['target'])
        self.assertNotIn('1', packaged.desired, 'Never invent a target for a missing link')
        self.assertIn('2', packaged.desired)

    def test_metadata_write_failure_restores_host_in_package(self):
        case = self.setup_case()
        def broken_write(path, metadata):
            with open(path, 'wb') as stream: stream.write(b'interrupted metadata write')
            raise IOError('Injected metadata failure')
        self.db.TransmissionData.WriteTransmissionData = broken_write
        result, host = self.run_case(case, f.defaults())
        self.assert_copy_delivered(result, host, case)
        self.assertEqual(f.digest(host['target']), f.digest(self.original))
        self.assertTrue(any(row['code'] == 'MODEL_PROCESSING_FAILED' for row in result['issues']))
        self.assertNotIn(host.get('model_verification'), ('TRANSMISSION_DATA_CHECKED', 'SAVED_REFERENCES_CHECKED'))

    def test_default_report_distinguishes_copy_from_independent_model(self):
        case = self.setup_case()
        options = f.defaults(); options['repath'] = False
        result, host = self.run_case(case, options)
        with io.open(os.path.join(case[1], 'START_HERE.txt'), encoding='utf-8') as stream:
            report = stream.read()
        self.assertIn('Copy-first', report)
        self.assertIn('Detach from Central', report)
        self.assertIn('not independent', report)

    def test_archive_contains_the_copied_host(self):
        case = self.setup_case()
        options = f.defaults(); options['repath'] = False
        result, host = self.run_case(case, options)
        self.assert_copy_delivered(result, host, case)
        target = case[1] + '.zip'
        engine.zip_package(case[1], target)
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read('Host.rvt'), self.payload)

    def test_pdf_manual_repair_does_not_start_worker_or_remove_host(self):
        case = self.setup_case()
        pdf = os.path.join(self.root, 'Drawing.pdf')
        with open(pdf, 'wb') as stream: stream.write(b'%PDF-fixture')
        self.r.get(case[0])['inventory']['references'].append(dict(
            id='3', element_id='3', kind='Image', special='image', source=pdf, td=False))
        result, host = self.run_case(case, f.defaults())
        self.assert_copy_delivered(result, host, case)
        ref = next(row for row in result['references'] if row['id'] == '3')
        self.assertTrue(os.path.isfile(ref['target']))
        self.assertEqual(ref.get('repath'), 'MANUAL_REPAIR_REQUIRED')

    def test_cleanup_still_requires_explicit_revit_processing(self):
        case = self.setup_case()
        options = f.defaults(); options['cleanup'] = True
        result, host = self.run_case(case, options)
        self.assertEqual(case[5], [True])
        self.assertTrue(os.path.isfile(host['recovery_path']))
        self.assertEqual(f.digest(host['recovery_path']), f.digest(self.original))

    def test_saved_local_host_delivers_without_worker(self):
        from easybim_etransmit.revit import Backend
        case = self.setup_case()
        local = os.path.join(self.root, 'Host.rvt')
        with open(local, 'wb') as stream: stream.write(self.payload)
        before = f.digest(local)
        backend = Backend(self.db, self.app, case[1])
        backend.open_copy = lambda *args: self.fail('Copy-only local host must not open Revit')
        options = f.defaults(); options['repath'] = False
        result = engine.transmit([local], case[1], backend, options)
        host = next(row for row in result['files'] if row.get('is_primary_host'))
        self.assertTrue(os.path.isfile(host['target']), repr(result['issues']))
        self.assertEqual(f.digest(host['target']), before)
        self.assertEqual(f.digest(local), before)
        self.assertEqual(case[5], [])
        self.assertEqual(engine.package_counts(result)['hosts_copied'], 1)

    def test_scan_failure_retains_acquired_host_in_copy_first_mode(self):
        case = self.setup_case()
        def scan_failure(*args):
            raise RuntimeError('Optional saved-reference inspection failed')
        case[2].inventory_before_copy = lambda *args: None
        case[2].scan = scan_failure
        result, host = self.run_case(case, f.defaults())
        self.assert_copy_delivered(result, host, case)
        self.assertEqual(f.digest(host['target']), f.digest(self.original))
        self.assertEqual(host['inventory_status'], 'FAILED')
        self.assertTrue(any(row['severity'] == 'error' for row in result['issues']))

    def test_explicit_full_repair_still_uses_worker_and_keeps_recovery(self):
        case = self.setup_case()
        options = f.defaults(); options['simple_repath'] = False
        result, host = self.run_case(case, options)
        self.assertEqual(case[5], [True])
        self.assertFalse(host.get('host_finalized'))
        self.assertTrue(os.path.isfile(host['recovery_path']))
        self.assertEqual(f.digest(host['recovery_path']), f.digest(self.original))


if __name__ == '__main__':
    unittest.main(verbosity=2)
