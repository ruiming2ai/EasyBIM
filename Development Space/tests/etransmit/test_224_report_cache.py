# -*- coding: utf-8 -*-
"""Anonymized reproduction of a reported stale DIRECT / LinkedModels cache pair.

These tests exercise real cache copying/selection. BasicFileInfo is substituted
because portable CI does not have Revit; the payloads are synthetic CFB files.
"""
from __future__ import unicode_literals
import os
import unittest
import test_212_cache as fixtures
from test_payload_acquisition import compound
from easybim_etransmit import cache_sources as c, files as f


class ReportCacheTests(unittest.TestCase):
    setUp = getattr(fixtures.CacheTests.setUp, '__func__', fixtures.CacheTests.setUp)
    put = getattr(fixtures.CacheTests.put, '__func__', fixtures.CacheTests.put)
    store = getattr(fixtures.CacheTests.store, '__func__', fixtures.CacheTests.store)

    def pair(self, direct_saves=4869, linked_saves=4954, linked_account='USER',
             linked_folder='LinkedModels', linked_format='2024', linked_root=None):
        self.version = dict(guid=fixtures.V, saves=4954)
        self.direct = self.put(payload=compound(suffix='old-direct'))
        self.linked = self.put(base=linked_root, account=linked_account,
                               project=os.path.join(fixtures.P, linked_folder),
                               payload=compound(suffix='saved-linked'))
        self.infos = {
            f.digest(self.direct): dict(version=dict(guid=fixtures.W, saves=direct_saves),
                                       format='2024', workshared=True),
            f.digest(self.linked): dict(version=dict(guid=fixtures.M, saves=linked_saves),
                                       format=linked_format, workshared=True),
        }
        store = self.store(linked=True)
        if linked_root:
            store.roots.append(linked_root)
        store.read_info = lambda path: self.infos[f.digest(path)]
        return store

    def capture_expected(self, store):
        try:
            return store.capture(self.entry)
        except c.CacheError as exc:
            self.fail('Saved linked source should be acquired, not block repath: ' + exc.code)

    def assert_ambiguous(self, store):
        with self.assertRaises(c.CacheError) as caught:
            store.capture(self.entry)
        self.assertEqual(caught.exception.code, 'CACHE_CANDIDATES_AMBIGUOUS')
        self.assertFalse(any(name == 'snapshot.rvt'
                             for root, dirs, names in os.walk(self.out) for name in names))

    def test_report_pair_uses_linked_saved_edition_without_claiming_exact_revision(self):
        store = self.pair()
        before = [(f.digest(p), os.stat(p).st_mtime) for p in (self.direct, self.linked)]
        result = self.capture_expected(store)
        meta = result['metadata']
        self.assertEqual(meta['cache_path'], self.linked)
        self.assertEqual(f.digest(result['path']), f.digest(self.linked))
        self.assertEqual(meta['cache_selection_reason'], 'LINKED_MODELS_LOADED_SAVE_COUNT')
        self.assertEqual(meta['revision_check'], 'SAVED_CACHE_DIFFERS_FROM_LOADED')
        self.assertNotEqual(meta['cache_document_version']['guid'], self.version['guid'])
        self.assertEqual(meta['cache_document_version']['saves'], self.version['saves'])
        self.assertIn('not an exact revision match', meta['cache_evidence']['selection_note'])
        self.assertEqual([(f.digest(p), os.stat(p).st_mtime)
                          for p in (self.direct, self.linked)], before)

    def test_selection_does_not_follow_newest_timestamp(self):
        store = self.pair()
        os.utime(self.direct, (2000000000, 2000000000))
        os.utime(self.linked, (1500000000, 1500000000))
        self.assertEqual(self.capture_expected(store)['metadata']['cache_path'], self.linked)

    def test_role_recovery_is_order_independent(self):
        store = self.pair()
        original = c.find_candidates
        try:
            for paths in ([self.direct, self.linked], [self.linked, self.direct]):
                c.find_candidates = lambda *args: paths
                self.assertEqual(self.capture_expected(store)['metadata']['cache_path'], self.linked)
        finally:
            c.find_candidates = original

    def test_exact_direct_revision_still_wins(self):
        store = self.pair()
        self.infos[f.digest(self.direct)]['version'] = dict(self.version)
        meta = store.capture(self.entry)['metadata']
        self.assertEqual(meta['cache_path'], self.direct)
        self.assertEqual(meta['revision_check'], 'MATCHES_LOADED_SAVED_VERSION')

    def test_primary_host_cannot_use_linked_fallback(self):
        store = self.pair()
        self.entry.update(is_linked=False, is_modified=True)
        self.assert_ambiguous(store)

    def test_no_loaded_version_cannot_use_role_fallback(self):
        store = self.pair()
        self.entry['document_version'] = None
        self.assert_ambiguous(store)

    def test_equal_save_counts_with_different_guids_remain_ambiguous(self):
        self.assert_ambiguous(self.pair(direct_saves=4954))

    def test_newer_direct_edition_is_not_assumed_stale(self):
        self.assert_ambiguous(self.pair(direct_saves=4955))

    def test_linked_count_must_match_loaded_count(self):
        self.assert_ambiguous(self.pair(linked_saves=4953))

    def test_different_formats_are_not_resolved_by_counter(self):
        self.assert_ambiguous(self.pair(linked_format='2023'))

    def test_different_accounts_are_not_resolved_by_counter(self):
        self.assert_ambiguous(self.pair(linked_account='OTHER'))

    def test_different_roots_are_not_resolved_by_counter(self):
        other = os.path.join(self.root, 'other', 'CollaborationCache')
        self.assert_ambiguous(self.pair(linked_root=other))

    def test_unknown_directory_is_not_a_linked_role(self):
        self.assert_ambiguous(self.pair(linked_folder='Unknown'))

    def test_multiple_direct_candidates_remain_ambiguous(self):
        store = self.pair()
        self.put(filename='{' + fixtures.M + '}.rvt', payload=compound(suffix='old-direct'))
        self.assert_ambiguous(store)

    def test_multiple_linked_candidates_remain_ambiguous(self):
        store = self.pair()
        self.put(filename='{' + fixtures.M + '}.rvt',
                 project=os.path.join(fixtures.P, 'LinkedModels'),
                 payload=compound(suffix='saved-linked'))
        self.assert_ambiguous(store)

    def test_selected_snapshot_tampering_is_still_refused(self):
        store = self.pair()
        result = self.capture_expected(store)
        with open(result['path'], 'ab') as stream:
            stream.write(b'changed')
        with self.assertRaises(c.CacheError) as caught:
            store.capture(self.entry)
        self.assertEqual(caught.exception.code, 'CACHE_SNAPSHOT_CHANGED')


if __name__ == '__main__':
    unittest.main(verbosity=2)
