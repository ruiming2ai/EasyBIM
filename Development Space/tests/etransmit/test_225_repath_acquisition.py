# -*- coding: utf-8 -*-
"""Real engine + Registry + cache acquisition regression, without a Revit runtime.

The final Revit write boundary records its inputs, not a fabricated successful
repath. These tests prove collection no longer prevents finalization from being
called; separate API-shaped tests cover the per-reference Revit operations.
"""
from __future__ import unicode_literals
import copy
import os
import shutil
import unittest
import test_212_session as fixtures
from test_212_cache import Obj, P, M, V, W
from test_213_session import cloud_row
from test_payload_acquisition import compound
from easybim_etransmit import cache_sources as c, engine, files as f, session

N = '55555555-5555-4555-8555-555555555555'
Q = '66666666-6666-4666-8666-666666666666'
R = '77777777-7777-4777-8777-777777777777'


class RecordingRevitBoundary(session.SessionBackend):
    """Only native RVT metadata reading/writing is replaced, not acquisition."""
    mark_transmitted_package = None
    verify_package = None

    def __init__(self, db, app, root, registry):
        session.SessionBackend.__init__(self, db, app, root, registry)
        self.finalizations = []

    def rows(self, path, owner=''):
        # True ACC links in this fixture are absent from closed-file metadata.
        return []

    def finish(self, stage, target, rows, options):
        # Engine must deliver the dependencies BEFORE requesting native repair.
        self.finalizations.append(dict(target=target, rows=copy.deepcopy(rows),
                                       options=dict(options)))
        shutil.copyfile(stage, target)
        return dict(issues=[], verified_in_process=False, worker_repaired=True,
                    host_finalized=True, saved_references_checked=True,
                    verification_status='SAVED_REFERENCES_CHECKED',
                    independent_package_central=True)


class ReportRepathAcquisition(unittest.TestCase):
    setUp = getattr(fixtures.SavedCacheSession.setUp, '__func__', fixtures.SavedCacheSession.setUp)

    def write(self, path, payload):
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, 'wb') as stream:
            stream.write(payload)
        return path

    def prepare(self, detached=False, conflict=False, missing=False):
        rows = []
        infos = {f.digest(self.original): dict(version=dict(guid=V, saves=9),
                                              format='2024', workshared=True)}
        linked_root = os.path.join(self.cache, 'USER', P, 'LinkedModels')
        self.sources = [self.original]
        for index, model in enumerate((W, N, Q, R)):
            name = 'Dependency{0}.rvt'.format(index)
            linked = os.path.join(linked_root, model + '.rvt')
            if not (missing and index == 0):
                self.write(linked, compound(suffix=name))
                infos[f.digest(linked)] = dict(version=dict(guid=M, saves=4954),
                                               format='2024', workshared=True)
                self.sources.append(linked)
            if index == 0 and not missing:
                direct = self.write(os.path.join(self.cache, 'USER', P, model + '.rvt'),
                                    compound(suffix='stale-direct'))
                infos[f.digest(direct)] = dict(version=dict(guid=Q, saves=4954 if conflict else 4869),
                                               format='2024', workshared=True)
                self.sources.append(direct)
                self.selected_link = linked
            row = cloud_row(model, name, str(100 + index), loaded=index != 2)
            if index != 2:
                # Linked Document metadata exists for loaded links, not unloaded.
                child = copy.copy(self.doc)
                child.Title = name
                child.PathName = 'Autodesk Docs://Project/' + name
                child.IsLinked = True
                child.GetCloudModelPath = lambda m=model: Obj(
                    GetModelGUID=lambda: m, GetProjectGUID=lambda: P, Region='US')
                child.GetDocumentVersion = lambda doc: Obj(VersionGUID=V, NumberOfSaves=4954)
                row['source'] = self.r.add_live(child, inspect=False)
            rows.append(row)

        for index, (name, kind) in enumerate((('Plan.dwg', 'CADLink'),
                                               ('Detail.dwg', 'CADLink'),
                                               ('Reference.pdf', 'Image'))):
            path = self.write(os.path.join(self.root, 'inputs', name), name.encode('ascii'))
            self.sources.append(path)
            row = dict(id=str(200 + index), element_id=str(200 + index), source=path,
                       kind=kind, loaded=True, td=False,
                       special='image' if kind == 'Image' else 'external')
            if kind == 'Image':
                row.update(page=1, resolution=300)
            rows.append(row)

        configured = None
        if detached:
            configured = self.write(os.path.join(self.root, 'Detached.rvt'), self.payload)
            self.sources.append(configured)
            self.doc.IsDetached = True
            self.doc.IsModelInCloud = False
            self.doc.PathName = ''
        host = self.r.add_live(self.doc, configured_source=configured)
        entry = self.r.get(host)
        entry['inventory']['references'] = rows
        if detached:
            entry['detached_recovery'] = 'USER_BROWSE'
        store = c.Store(self.db, self.app, os.path.join(self.r.temp_root(), 'cache'), roots=[self.cache])
        store.read_info = lambda path: infos[f.digest(path)]
        self.r._cache_store = store
        output = os.path.join(self.root, 'out')
        backend = RecordingRevitBoundary(self.db, self.app, output, self.r)
        opts = f.defaults()
        opts.update(repath=True, simple_repath=False, cleanup=False, upgrade=False)
        self.before = dict((p, (f.digest(p), os.stat(p).st_mtime)) for p in self.sources)
        result = engine.transmit([host], output, backend, opts)
        if result.get('recovery_directory'):
            self.addCleanup(f.remove_tree_retry, result['recovery_directory'])
        return result, backend

    def assert_finalization_reached(self, detached):
        result, backend = self.prepare(detached=detached)
        counts = engine.package_counts(result)
        self.assertEqual(counts['revit_links_copied'], 4, repr(result['issues']))
        self.assertEqual(len(backend.finalizations), 1, repr(result['issues']))
        call = backend.finalizations[0]
        self.assertTrue(call['options']['repath'])
        self.assertTrue(call['options']['independent_host'])
        self.assertFalse(call['options']['verify_in_process'])
        self.assertEqual(len(call['rows']), 7)
        for row in call['rows']:
            self.assertTrue(os.path.isfile(row['target']))
            self.assertTrue(f.within(row['target'], result['root']))
        self.assertEqual([r['package_loaded'] for r in call['rows'] if r['kind'] == 'RevitLink'],
                         [True, True, False, True])
        self.assertEqual(sum(r['kind'] == 'CADLink' for r in call['rows']), 2)
        self.assertEqual(sum(r.get('special') == 'image' for r in call['rows']), 1)
        link = next(r for r in result['files'] if r['source'].endswith('/Dependency0.rvt'))
        self.assertEqual(f.digest(link['target']), f.digest(self.selected_link))
        self.assertEqual(link['source_context']['cache_metadata']['revision_check'],
                         'SAVED_CACHE_DIFFERS_FROM_LOADED')
        self.assertFalse(any(i['code'] == 'HOST_PRESERVED_WITHOUT_LINK_REPATH' for i in result['issues']))
        host=next(r for r in result['files'] if r.get('is_primary_host'))
        self.assertEqual(host.get('model_verification'),'SAVED_REFERENCES_CHECKED')
        self.assertTrue(host.get('worker_repaired'))
        self.assertTrue(host.get('host_finalized'))
        self.assertEqual(host['transmission_status'],'NOT_TRANSMITTED')
        self.assertEqual(host['opening_guidance'],'OPEN_NORMALLY_INDEPENDENT_PACKAGE')
        self.assertEqual(engine.package_counts(result)['hosts_copied'],1)
        self.assertEqual(dict((p, (f.digest(p), os.stat(p).st_mtime)) for p in self.sources), self.before)

    def test_acc_live_report_pair_reaches_repath_with_all_dependencies(self):
        self.assert_finalization_reached(False)

    def test_detached_report_pair_reaches_repath_with_all_dependencies(self):
        self.assert_finalization_reached(True)

    def assert_host_still_protected(self, **kwargs):
        result, backend = self.prepare(**kwargs)
        self.assertEqual(backend.finalizations, [])
        host = next(r for r in result['files'] if r.get('is_primary_host'))
        self.assertEqual(host['processing_status'], 'HOST_PRESERVED_LINKS_UNAVAILABLE')
        self.assertEqual(host['model_verification'], 'DEFERRED')
        self.assertEqual(host['status'], 'NOT_FINALIZED')
        self.assertEqual(host['transmission_status'], 'NOT_FINALIZED')
        self.assertFalse(host['host_finalized'])
        self.assertFalse(os.path.isfile(host['target']))
        self.assertFalse(f.within(host['recovery_path'],result['root']))
        self.assertEqual(f.digest(host['recovery_path']), f.digest(self.original))
        self.assertEqual(engine.package_counts(result)['hosts_copied'],0)
        self.assertEqual(engine.package_counts(result)['revit_links_copied'], 3)
        self.assertEqual(dict((p, (f.digest(p), os.stat(p).st_mtime)) for p in self.sources), self.before)

    def test_genuinely_ambiguous_pair_still_blocks_native_host_open(self):
        self.assert_host_still_protected(conflict=True)

    def test_missing_link_still_blocks_native_host_open(self):
        self.assert_host_still_protected(missing=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
