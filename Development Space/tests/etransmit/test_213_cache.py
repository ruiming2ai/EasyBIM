# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, unittest
import test_212_cache as fixtures
from test_212_cache import P, M, V, W
from test_payload_acquisition import compound
from easybim_etransmit import cache_sources as c


class SavedLinkCache(fixtures.CacheTests):
    def test_morrison_linkedmodels_same_save_count_different_guid(self):
        path=self.put(project=os.path.join(P,'LinkedModels'))
        store=self.store(linked=True)
        self.entry['document_version']=dict(guid=V,saves=70)
        store.read_info=lambda p:dict(version=dict(guid=W,saves=70),format='2024')
        meta=store.capture(self.entry)['metadata']
        self.assertEqual(meta['cache_path'],path)
        self.assertEqual(meta['revision_check'],'SAVED_CACHE_DIFFERS_FROM_LOADED')
        self.assertEqual(meta['loaded_document_version'],dict(guid=V,saves=70))
        self.assertEqual(meta['cache_document_version'],dict(guid=W,saves=70))

    def test_unloaded_link_has_no_loaded_revision(self):
        self.put();store=self.store(linked=True);self.entry['document_version']=None
        meta=store.capture(self.entry)['metadata']
        self.assertEqual(meta['revision_check'],'SAVED_CACHE_NO_LOADED_REVISION')
        self.assertEqual(meta['cache_document_version'],self.version)

    def test_exact_revision_wins_over_different_saved_edition(self):
        self.put(account='A',payload=compound(suffix='older'))
        exact=self.put(account='B',payload=compound(suffix='exact'))
        store=self.store(linked=True)
        def info(path):
            with open(path,'rb') as inp:data=inp.read()
            return dict(version=dict(guid=V if data==compound(suffix='exact') else W,saves=9),format='2024')
        store.read_info=info
        meta=store.capture(self.entry)['metadata']
        self.assertEqual(meta['cache_path'],exact)
        self.assertEqual(meta['revision_check'],'MATCHES_LOADED_SAVED_VERSION')

    def test_two_different_saved_editions_are_not_selected_by_timestamp(self):
        self.put(account='A');self.put(account='B',payload=compound(suffix='other'))
        store=self.store(linked=True)
        store.read_info=lambda p:dict(version=dict(guid=W,saves=70),format='2024')
        with self.assertRaises(c.CacheError) as ctx:store.capture(self.entry)
        self.assertEqual(ctx.exception.code,'CACHE_CANDIDATES_AMBIGUOUS')
        self.assertEqual(len(ctx.exception.evidence['attempts']),2)

    def test_no_loaded_revision_does_not_bypass_native_metadata_checks(self):
        self.put();store=self.store(linked=True);self.entry['document_version']=None
        store.read_info=lambda p:dict(version=None,format='2024')
        with self.assertRaises(c.CacheError):store.capture(self.entry)

    def test_saved_fallback_does_not_accept_newer_revit_format(self):
        self.put();store=self.store(linked=True)
        store.read_info=lambda p:dict(version=dict(guid=W,saves=70),format='2025')
        with self.assertRaises(c.CacheError) as ctx:store.capture(self.entry)
        self.assertEqual(ctx.exception.evidence['attempts'][0]['code'],'CACHE_FORMAT_UNSUPPORTED')

    def test_resource_identity_normalizes_guids_and_keeps_region(self):
        row=dict(resource_information=dict(LinkedModelProjectId='{'+P+'}',LinkedModelModelId=M.upper(),LinkedModelRegion='us'))
        self.assertEqual(c.reference_identity(row),dict(project_guid=P,model_guid=M,region='US'))
        self.assertFalse(c.same_identity(c.reference_identity(row),dict(project_guid=P,model_guid=M,region='EMEA')))


for name in fixtures.CacheTests.__dict__:
    if name.startswith('test_') and name not in SavedLinkCache.__dict__:setattr(SavedLinkCache,name,None)

if __name__=='__main__':unittest.main(verbosity=2)
