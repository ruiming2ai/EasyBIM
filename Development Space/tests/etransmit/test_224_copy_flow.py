# -*- coding: utf-8 -*-
"""Copy-first saved/cache repathing, with only Autodesk serialization replaced."""
from __future__ import unicode_literals
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'lib'))

from easybim_etransmit import engine, files as f, revit, session, worker
from test_212_cache import Obj, P, M
import test_212_session as cache_fixtures
from test_payload_acquisition import compound


class Reference(object):
    def __init__(self, path, kind, loaded, path_type='Absolute'):
        self.path = path
        self.ExternalFileReferenceType = kind
        self.loaded = loaded
        self.PathType = path_type

    def GetPath(self): return self.path
    def GetAbsolutePath(self): return self.path
    def GetLinkedFileStatus(self): return 'Loaded' if self.loaded else 'Unloaded'
    def Dispose(self): pass


class Transmission(object):
    def __init__(self, saved, desired=None, transmitted=False):
        self.saved = saved
        self.desired = desired or {}
        self.IsTransmitted = transmitted

    def GetAllExternalFileReferenceIds(self):
        return [Obj(Value=int(key)) for key in self.saved]

    def GetLastSavedReferenceData(self, ident): return self.saved[revit.eid(ident)]
    def GetDesiredReferenceData(self, ident): return self.desired.get(revit.eid(ident))

    def SetDesiredReferenceData(self, ident, path, path_type, loaded):
        key = revit.eid(ident)
        self.desired[key] = Reference(path, self.saved[key].ExternalFileReferenceType,
                                      bool(loaded), path_type)

    def Dispose(self): pass


class TransmissionFiles(object):
    """Persist TD in copied bytes so final delivery has independent readback."""
    marker = b'\nET_TEST_TRANSMISSION_DATA\n'

    def __init__(self, testcase, host_payload, references):
        self.testcase = testcase
        self.host_payload = host_payload
        self.references = references
        self.reads = []
        self.writes = []

    def read(self, path):
        self.reads.append(path)
        with open(path, 'rb') as stream: payload = stream.read()
        base, separator, encoded = payload.partition(self.marker)
        if separator:
            value = json.loads(encoded.decode('utf-8'))
            saved = dict((key, Reference(*args)) for key, args in value['saved'].items())
            desired = dict((key, Reference(*args)) for key, args in value['desired'].items())
            return Transmission(saved, desired, value['transmitted'])
        return Transmission(copy.deepcopy(self.references) if base == self.host_payload else {})

    def write(self, path, td):
        self.testcase.assertTrue(os.path.isfile(path), 'Host must exist before its first TD write')
        with open(path, 'rb') as stream: before = stream.read()
        self.writes.append(dict(path=path, before=before, desired=copy.deepcopy(td.desired)))
        def values(refs):
            return dict((key, [ref.path, ref.ExternalFileReferenceType,
                               ref.loaded, ref.PathType]) for key, ref in refs.items())
        value = dict(saved=values(td.saved), desired=values(td.desired),
                     transmitted=bool(td.IsTransmitted))
        with open(path, 'wb') as stream:
            stream.write(before.partition(self.marker)[0] + self.marker +
                         json.dumps(value, sort_keys=True).encode('utf-8'))

    def api(self):
        return Obj(ReadTransmissionData=self.read, WriteTransmissionData=self.write,
                   IsDocumentTransmitted=lambda path: self.read(path).IsTransmitted)


class MetadataCopyFlow(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ET224_copy_')
        self.addCleanup(shutil.rmtree, self.root)
        self.stage = os.path.join(self.root, 'stage.rvt')
        self.target = os.path.join(self.root, 'package', 'Host.rvt')
        os.makedirs(os.path.dirname(self.target))
        self.payload = compound()
        with open(self.stage, 'wb') as stream: stream.write(self.payload)
        self.link = os.path.join(self.root, 'package', 'Links', 'Architecture.rvt')
        self.cad = os.path.join(self.root, 'package', 'CAD', 'Plan.dwg')
        self.rows = [dict(id='1', element_id='1', kind='RevitLink', td=True,
                          special='native', loaded=False, target=self.link,
                          source='N:/Project/Architecture.rvt'),
                     dict(id='2', element_id='2', kind='CADLink', td=False,
                          special='external', loaded=None, target=self.cad,
                          source='N:/Project/Plan.dwg')]
        self.td = TransmissionFiles(self, self.payload, {
            '1': Reference(self.rows[0]['source'], 'RevitLink', False),
            '2': Reference(self.rows[1]['source'], 'CADLink', True)})
        self.db = Obj(TransmissionData=self.td.api(),
                      ModelPathUtils=Obj(ConvertUserVisiblePathToModelPath=lambda path: path,
                                         ConvertModelPathToUserVisiblePath=lambda path: path),
                      PathType=Obj(Relative='Relative', Absolute='Absolute'))
        self.backend = revit.Backend(self.db, Obj(VersionNumber='2024'), self.root)
        self.backend.basic = lambda path: dict(version='2024', workshared=True, central='')
        self.backend.open_copy = lambda *args: self.fail('Native metadata repath must not open Revit')
        self.backend.finish_independent = lambda *args: self.fail('Native metadata repath must not SaveAs')
        self.opts = dict(repath=True, cleanup=False, upgrade=False, normalize_saved_cache=True)

    def test_copy_exists_before_metadata_write_and_stage_is_unchanged(self):
        result = self.backend.finish(self.stage, self.target, self.rows, self.opts)
        self.assertTrue(result['metadata_repathed'])
        self.assertEqual(result['issues'], [])
        self.assertEqual([write['path'] for write in self.td.writes], [self.target])
        self.assertEqual(self.td.writes[0]['before'], self.payload)
        with open(self.stage, 'rb') as stream: self.assertEqual(stream.read(), self.payload)
        self.assertTrue(all(row['repath'] == 'TRANSMISSION_DATA' for row in self.rows))
        td = self.td.read(self.target)
        self.assertEqual(td.desired['1'].PathType, 'Relative')
        self.assertFalse(td.desired['1'].loaded)
        self.assertTrue(td.desired['2'].loaded, 'CAD with unknown live load state uses saved TD intent')
        for row in self.rows:
            self.assertEqual(f.canonical(f.resolve_source(td.desired[row['id']].path, self.target)),
                             f.canonical(row['target']))

    def test_worker_mode_also_uses_native_copy_without_document_open(self):
        result = self.backend.finish(self.stage, self.target, self.rows,
                                     dict(self.opts, worker_mode=True))
        self.assertTrue(result['metadata_repathed'])
        self.assertEqual(self.td.writes[0]['path'], self.target)

    def test_reference_base_is_final_delivery_path(self):
        final = os.path.join(self.root, 'delivered', 'Host.rvt')
        result = self.backend.finish(self.stage, self.target, self.rows,
                                     dict(self.opts, reference_target=final))
        self.assertTrue(result['metadata_repathed'])
        td = self.td.read(self.target)
        for row in self.rows:
            self.assertEqual(f.canonical(f.resolve_source(td.desired[row['id']].path, final)),
                             f.canonical(row['target']))

    def test_classifier_accepts_native_cache_cad_and_rejects_document_repairs(self):
        self.assertTrue(self.backend.can_finish_metadata_copy(self.rows, self.opts))
        self.assertTrue(self.backend.can_deliver_direct(
            dict(copy_method='COLLABORATION_CACHE_READ_ONLY'), self.rows, self.opts))
        excluded = [dict(self.opts, cleanup=True), dict(self.opts, upgrade=True),
                    dict(self.opts, repath=False)]
        for options in excluded:
            self.assertFalse(self.backend.can_finish_metadata_copy(self.rows, options), options)
        rejected = [dict(id='3', kind='Image', special='image', target=self.cad),
                    dict(self.rows[0], td=False, special='external'),
                    dict(self.rows[0], cloud_identity=dict(project_guid=P, model_guid=M, region='US'))]
        for row in rejected:
            self.assertFalse(self.backend.can_finish_metadata_copy([row], self.opts), row)
        for row in rejected:
            self.assertTrue(self.backend.can_finish_metadata_copy([dict(row, skip_repath=True)], self.opts))
            row.pop('target')
            self.assertFalse(self.backend.can_finish_metadata_copy([row], self.opts))

    def test_readback_rejects_wrong_path_and_loaded_state(self):
        self.backend.finish(self.stage, self.target, self.rows, self.opts)
        self.assertEqual(self.backend.verify_metadata_package(self.target, self.rows, self.opts), [])
        for mutation in ('path', 'loaded'):
            td = self.td.read(self.target)
            if mutation == 'path': td.desired['1'].path = 'Elsewhere.rvt'
            else:
                td.desired['1'].path = os.path.relpath(self.link, os.path.dirname(self.target))
                td.desired['1'].loaded = True
            self.td.write(self.target, td)
            issues = self.backend.verify_metadata_package(self.target, self.rows, self.opts)
            self.assertEqual(len(issues), 1, repr(issues))
            self.assertEqual(issues[0]['code'], 'METADATA_VERIFICATION_FAILED')
            self.assertEqual(issues[0]['severity'], 'error')

    def test_readback_rejects_inactive_or_missing_desired_metadata(self):
        for mutation in ('inactive', 'missing'):
            self.backend.finish(self.stage, self.target, self.rows, self.opts)
            td = self.td.read(self.target)
            if mutation == 'inactive': td.IsTransmitted = False
            else: td.desired.pop('1')
            self.td.write(self.target, td)
            issues = self.backend.verify_metadata_package(self.target, self.rows, self.opts)
            self.assertTrue(issues, mutation)
            self.assertTrue(all(item['code'] == 'METADATA_VERIFICATION_FAILED' and
                                item['severity'] == 'error' for item in issues), repr(issues))

    def test_readback_rejects_matching_absolute_path(self):
        self.backend.finish(self.stage, self.target, self.rows, self.opts)
        td = self.td.read(self.target)
        td.desired['1'].path = self.link
        td.desired['1'].PathType = 'Absolute'
        self.td.write(self.target, td)
        issues = self.backend.verify_metadata_package(self.target, self.rows, self.opts)
        self.assertEqual(len(issues), 1, repr(issues))
        self.assertEqual(issues[0]['code'], 'METADATA_VERIFICATION_FAILED')
        self.assertEqual(issues[0]['severity'], 'error')

    def test_empty_reference_list_does_not_require_transmission_data(self):
        shutil.copyfile(self.stage, self.target)
        self.db.TransmissionData.ReadTransmissionData = lambda path: None
        self.assertEqual(self.backend.verify_metadata_package(self.target, [], self.opts), [])

    def test_native_metadata_flow_reports_plugin_workbook_reconnect_requirement(self):
        workbook = os.path.join(os.path.dirname(self.target), 'Schedule.xlsx')
        with open(workbook, 'wb') as stream: stream.write(b'copied workbook')
        row = dict(id='3', element_id='3', kind='Spreadsheet',
                   special='plugin_spreadsheet', target=workbook,
                   source='N:/Project/Schedule.xlsx')
        rows = self.rows + [row]
        result = self.backend.finish(self.stage, self.target, rows, self.opts)
        self.assertTrue(result['metadata_repathed'])
        self.assertEqual([item['code'] for item in result['issues']], ['PLUGIN_RECONNECT_REQUIRED'])
        self.assertEqual(row['repath'], 'PLUGIN_RECONNECT_REQUIRED')
        self.assertEqual(self.backend.verify_metadata_package(self.target, rows, self.opts), [])
        with open(workbook, 'rb') as stream: self.assertEqual(stream.read(), b'copied workbook')

    def test_simple_mode_repaths_dependency_copy_metadata_and_preserves_unsupported_references(self):
        pdf = os.path.join(os.path.dirname(self.target), 'Reference.pdf')
        external = os.path.join(os.path.dirname(self.target), 'Cloud.rvt')
        with open(pdf, 'wb') as stream: stream.write(b'%PDF copied reference')
        with open(external, 'wb') as stream: stream.write(compound(suffix='cloud link'))
        cloud_identity = dict(project_guid=P, model_guid=M, region='US')
        original_cloud = 'Autodesk Docs://Project/Cloud.rvt'
        rows = self.rows + [
            dict(id='3', element_id='3', kind='Image', special='image',
                 source='N:/Project/Reference.pdf', target=pdf, page=2, resolution=300),
            dict(id='4', element_id='4', kind='RevitLink', special='external',
                 source=original_cloud, target=external, td=False, loaded=True,
                 cloud_identity=copy.deepcopy(cloud_identity))]
        self.td.references['4'] = Reference(original_cloud, 'RevitLink', True)
        source = 'open://host/Host.rvt'
        registry = Obj(get=lambda key: dict(mode='LIVE_DOCUMENT') if key == source else None)
        backend = session.SessionBackend(self.db, Obj(VersionNumber='2024'), self.root, registry)
        backend.open_copy = lambda *args: self.fail('Simple mode must not open the mixed host')
        backend.finish_independent = lambda *args: self.fail('Simple mode must not SaveAs the mixed host')
        original = worker.run_separate_revit
        worker.run_separate_revit = lambda *args, **kwargs: self.fail('Simple mode must not launch a worker')
        self.addCleanup(setattr, worker, 'run_separate_revit', original)
        options = dict(self.opts, simple_repath=True, _host_source=source)
        result = backend.finish(self.stage, self.target, rows, options)
        self.assertTrue(result['metadata_repathed'])
        self.assertFalse(result.get('worker_repaired'))
        self.assertEqual(self.td.writes[0]['path'], self.target)
        self.assertEqual(self.td.writes[0]['before'], self.payload)
        with open(self.stage, 'rb') as stream: self.assertEqual(stream.read(), self.payload)
        self.assertEqual([row['repath'] for row in rows[:2]], ['TRANSMISSION_DATA'] * 2)
        self.assertEqual([row['repath'] for row in rows[2:]], ['MANUAL_REPAIR_REQUIRED'] * 2)
        self.assertEqual(len(result['issues']), 2, repr(result['issues']))
        self.assertTrue(all(item['severity'] == 'warning' for item in result['issues']))
        self.assertEqual(rows[3]['cloud_identity'], cloud_identity)
        self.assertEqual(rows[3]['source'], original_cloud)
        self.assertEqual((rows[2]['page'], rows[2]['resolution']), (2, 300))
        persisted = self.td.read(self.target)
        self.assertEqual(persisted.saved['4'].path, original_cloud)
        self.assertNotIn('4', persisted.desired)
        self.assertNotIn('3', persisted.desired)
        self.assertEqual(backend.verify_metadata_package(self.target, rows, options), [])
        self.assertTrue(all('CHECKED' in row.get('verification', '') for row in rows[:2]))

    def test_simple_mode_still_requires_no_cleanup_or_upgrade(self):
        row = dict(id='3', kind='Image', special='image', target=self.cad)
        options = dict(self.opts, simple_repath=True)
        self.assertTrue(self.backend.can_finish_metadata_copy([row], options))
        for flags in (dict(cleanup=True), dict(upgrade=True), dict(repath=False)):
            self.assertFalse(self.backend.can_finish_metadata_copy([row], dict(options, **flags)))

    def test_default_simple_scan_reads_saved_metadata_without_inspection_open(self):
        calls = []
        def forbidden(name):
            def operation(*args):
                calls.append(name)
                self.fail('Simple saved-host scan must not ' + name)
            return operation
        self.backend.prepare_scan = forbidden('prepare')
        self.backend.open_copy = forbidden('open')
        options = f.defaults()
        self.assertTrue(options['simple_repath'])
        result = self.backend.scan(self.stage, self.stage, options)
        self.assertEqual(calls, [])
        self.assertFalse(result['opened_in_revit'])
        self.assertFalse(result.get('open_failed'))
        self.assertEqual(dict((row['id'], row['source']) for row in result['references']),
                         dict((row['id'], row['source']) for row in self.rows))
        self.assertEqual(dict((row['id'], row['loaded']) for row in result['references']),
                         {'1': False, '2': True})
        self.assertEqual([item['code'] for item in result['issues']], ['METADATA_ONLY_SCAN'])
        self.assertEqual(result['issues'][0]['severity'], 'warning')
        self.assertIn('Image/PDF', result['issues'][0]['message'])
        self.assertIn('cloud', result['issues'][0]['message'])
        self.assertEqual(self.td.writes, [])
        with open(self.stage, 'rb') as stream: self.assertEqual(stream.read(), self.payload)

    def test_session_keeps_worker_for_cleanup_upgrade_image_and_cloud(self):
        source = 'open://host/Host.rvt'
        registry = Obj(get=lambda key: dict(mode='LIVE_DOCUMENT') if key == source else None)
        backend = session.SessionBackend(self.db, Obj(VersionNumber='2024'), self.root, registry)
        backend.finish_metadata_copy = lambda *args: self.fail('Document repair used metadata shortcut')
        calls = []
        original = worker.run_separate_revit
        worker.run_separate_revit = lambda *args, **kwargs: calls.append(args) or dict(worker_repaired=True)
        self.addCleanup(setattr, worker, 'run_separate_revit', original)
        options = dict(self.opts, simple_repath=False)
        cases = [(self.rows, dict(options, cleanup=True)),
                 (self.rows, dict(options, upgrade=True)),
                 ([dict(id='3', kind='Image', special='image', target=self.cad)], options),
                 ([dict(self.rows[0], cloud_identity=dict(project_guid=P, model_guid=M))], options)]
        for rows, options in cases:
            result = backend.finish(self.stage, self.target, rows, dict(options, _host_source=source))
            self.assertTrue(result['worker_repaired'])
        self.assertEqual(len(calls), len(cases))


class LiveCacheNativeDelivery(unittest.TestCase):
    setUp = cache_fixtures.SavedCacheSession.__dict__['setUp']

    def prepare(self, missing=False):
        link = os.path.join(self.root, 'Architecture.rvt')
        cad = os.path.join(self.root, 'Plan.dwg')
        if not missing:
            with open(link, 'wb') as stream: stream.write(compound(suffix='link'))
        with open(cad, 'wb') as stream: stream.write(b'linked CAD')
        refs = [dict(id='1', element_id='1', kind='RevitLink', source=link,
                     td=True, special='native', loaded=False),
                dict(id='2', element_id='2', kind='CADLink', source=cad,
                     td=False, special='external', loaded=None)]
        td = TransmissionFiles(self, self.payload, {
            '1': Reference(link, 'RevitLink', False),
            '2': Reference(cad, 'CADLink', True)})
        self.db.TransmissionData = td.api()
        self.db.ModelPathUtils = Obj(ConvertUserVisiblePathToModelPath=lambda path: path,
                                    ConvertModelPathToUserVisiblePath=lambda path: path)
        self.db.PathType = Obj(Relative='Relative', Absolute='Absolute')
        key = self.r.add_live(self.doc)
        self.r.get(key)['inventory']['references'] = refs
        output = os.path.join(self.root, 'out')
        backend = session.SessionBackend(self.db, self.app, output, self.r)
        backend.open_copy = lambda *args: self.fail('Live cache native host must not open Revit')
        backend.verify_package = lambda *args: self.fail('Metadata readback must not use document verification')
        backend.finalization_calls = []
        original = worker.run_separate_revit
        def finalize(application, stage, target, rows, options, **kwargs):
            self.assertTrue(options['independent_host'])
            backend.finalization_calls.append(dict(options=copy.deepcopy(options), target=target))
            shutil.copyfile(stage, target)
            saved = td.read(stage)
            for row in rows:
                self.assertTrue(f.within(row['target'], output))
                self.assertTrue(os.path.isfile(row['target']))
                ident = row['id']
                load = row.get('package_loaded')
                if load is None: load = saved.saved[ident].loaded
                saved.saved[ident] = Reference(os.path.relpath(row['target'], os.path.dirname(target)),
                                               row['kind'], load, 'Relative')
                row['repath'] = 'API_LOCAL_LINK_RELATIVE' if row['kind'] == 'RevitLink' else 'API_CAD_LINK'
                row['verification'] = 'SAVED_REFERENCE_PATH_AND_LOAD_CHECKED'
            saved.desired = {}
            saved.IsTransmitted = False
            td.write(target, saved)
            return dict(issues=[], host_finalized=True, saved_references_checked=True,
                        worker_repaired=True, verified_in_process=False,
                        verification_status='SAVED_REFERENCES_CHECKED',
                        independent_package_central=True, original_central_association_preserved=False)
        worker.run_separate_revit = finalize
        self.addCleanup(setattr, worker, 'run_separate_revit', original)
        before = dict((path, (f.digest(path), os.stat(path).st_mtime))
                      for path in (self.original, link, cad) if os.path.isfile(path))
        return key, output, backend, td, before

    def test_live_cache_finalizes_native_references_in_worker_and_preserves_source_session(self):
        key, output, backend, td, before = self.prepare()
        options = f.defaults()
        options.update(repath=True, cleanup=False, upgrade=False)
        result = engine.transmit([key], output, backend, options)
        host = next(row for row in result['files'] if row.get('is_primary_host'))
        self.assertEqual(host['processing_status'], 'PROCESSED', repr(result['issues']))
        self.assertTrue(host.get('host_finalized'), repr(host))
        self.assertTrue(host.get('worker_repaired'))
        self.assertEqual(len(backend.finalization_calls), 1)
        self.assertTrue(backend.finalization_calls[0]['options']['independent_host'])
        self.assertEqual(host['model_verification'], 'SAVED_REFERENCES_CHECKED')
        self.assertEqual(host['transmission_status'], 'NOT_TRANSMITTED')
        self.assertFalse(host.get('opened_in_revit'))
        self.assertEqual(host['copy_method'], 'COLLABORATION_CACHE_READ_ONLY')
        self.assertEqual(result['delivery_strategy'], 'DIRECT_DEPENDENCIES')
        self.assertEqual(engine.package_counts(result)['hosts_copied'], 1)
        self.assertFalse([item for item in result['issues'] if item['severity'] == 'error'], result['issues'])
        self.assertEqual(td.writes[0]['before'], self.payload)
        self.assertEqual(os.path.basename(td.writes[0]['path']), 'Host.rvt')
        self.assertNotIn(td.writes[0]['path'], before)
        self.assertEqual(dict((path, (f.digest(path), os.stat(path).st_mtime)) for path in before), before)
        packaged = td.read(host['target'])
        self.assertFalse(packaged.IsTransmitted)
        self.assertEqual(packaged.desired, {})
        for row in result['references']:
            self.assertIn(row.get('repath'), ('API_LOCAL_LINK_RELATIVE', 'API_CAD_LINK'), repr(row))
            self.assertIn('CHECKED', row.get('verification', ''), repr(row))
            desired = packaged.saved[row['id']]
            self.assertEqual(desired.PathType, 'Relative')
            self.assertEqual(f.canonical(f.resolve_source(desired.path, host['target'])),
                             f.canonical(row['target']))
        self.assertFalse(packaged.saved['1'].loaded)
        self.assertTrue(packaged.saved['2'].loaded)

    def test_missing_rvt_keeps_copied_cad_reference_unmodified_in_preserved_host(self):
        key, output, backend, td, before = self.prepare(missing=True)
        result = engine.transmit([key], output, backend, f.defaults())
        host = next(row for row in result['files'] if row.get('is_primary_host'))
        self.assertEqual(host['processing_status'], 'HOST_PRESERVED_LINKS_UNAVAILABLE', repr(result['issues']))
        self.assertEqual(host['status'], 'NOT_FINALIZED')
        self.assertFalse(host.get('metadata_repathed'))
        cad = next(row for row in result['references'] if row['kind'] == 'CADLink')
        self.assertTrue(os.path.isfile(cad['target']))
        self.assertEqual(f.digest(cad['target']), f.digest(cad['source']))
        self.assertFalse(os.path.isfile(host['target']))
        self.assertTrue(os.path.isfile(host['recovery_path']))
        self.assertFalse(f.within(host['recovery_path'], output))
        self.assertEqual(f.digest(host['recovery_path']), before[self.original][0])
        self.assertEqual(td.writes, [])
        self.assertEqual(backend.finalization_calls, [])
        if result.get('recovery_directory'): self.addCleanup(f.remove_tree_retry, result['recovery_directory'])
        self.assertEqual(dict((path, (f.digest(path), os.stat(path).st_mtime)) for path in before), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
