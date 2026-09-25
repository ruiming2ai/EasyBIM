# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io, os, unittest
import test_212_cache as cache_fixtures
import test_212_session as session_fixtures
import test_213_session as link_fixtures
from easybim_etransmit import cache_sources as c, session as s, files as f, engine
from test_payload_acquisition import compound

LOADED='60d1db2f-e83f-47cf-bd85-bebe4ed04391'
SAVED='94a34fa7-0fad-4319-b92a-fa10582453bc'

def method(value):return getattr(value,'__func__',value)

class UnmodifiedCacheHost(unittest.TestCase):
    setUp=method(cache_fixtures.CacheTests.setUp)
    put=method(cache_fixtures.CacheTests.put)
    store=method(cache_fixtures.CacheTests.store)

    def test_greek_theater_one_candidate_different_guid_equal_save_count(self):
        original=self.put();store=self.store()
        self.entry['document_version']=dict(guid=LOADED,saves=87)
        store.read_info=lambda path:dict(version=dict(guid=SAVED,saves=87),format='2024')
        result=store.capture(self.entry)
        self.assertEqual(f.digest(result['path']),f.digest(original))
        self.assertEqual(result['metadata']['revision_check'],'SAVED_CACHE_DIFFERS_FROM_LOADED')
        self.assertEqual(result['metadata']['loaded_document_version'],dict(guid=LOADED,saves=87))
        self.assertEqual(result['metadata']['cache_document_version'],dict(guid=SAVED,saves=87))
        self.assertFalse(self.entry['is_modified'])

    def test_host_exact_match_still_wins_over_saved_fallback(self):
        direct=self.put();exact=self.put(account='SECOND',payload=compound(suffix='exact'))
        store=self.store()
        store.read_info=lambda path:dict(version=self.version if f.digest(path)==f.digest(exact) else dict(guid=SAVED,saves=87),format='2024')
        result=store.capture(self.entry)
        self.assertEqual(result['metadata']['cache_path'],exact)
        self.assertEqual(result['metadata']['revision_check'],'MATCHES_LOADED_SAVED_VERSION')

    def test_host_different_saved_editions_remain_ambiguous(self):
        direct=self.put();self.put(project=os.path.join(cache_fixtures.P,'LinkedModels'),payload=compound(suffix='other'))
        store=self.store()
        store.read_info=lambda path:dict(version=dict(guid=SAVED,saves=86 if f.digest(path)==f.digest(direct) else 87),format='2024')
        with self.assertRaises(c.CacheError) as caught:store.capture(self.entry)
        self.assertEqual(caught.exception.code,'CACHE_CANDIDATES_AMBIGUOUS')

class UnmodifiedHostExport(unittest.TestCase):
    setUp=method(session_fixtures.SavedCacheSession.setUp)
    put_link=method(link_fixtures.SavedCloudLinks.put_link)
    saved_scans=method(link_fixtures.SavedCloudLinks.saved_scans)

    def test_actual_registry_path_collects_saved_dependencies_for_unmodified_host(self):
        self.version.VersionGUID=LOADED;self.version.NumberOfSaves=87
        link=self.put_link();before=f.digest(self.original);link_before=f.digest(link)
        key=self.r.add_live(self.doc)
        self.r.get(key)['inventory']['references']=[dict(source='Wrong-live-only.pdf',id='wrong',kind='Image')]
        self.r._cache_store=c.Store(self.db,self.app,os.path.join(self.r.temp_root(),'cache'),roots=[self.cache])
        self.r._cache_store.read_info=lambda path:dict(version=dict(guid=SAVED,saves=87),format='2024')
        self.saved_scans({'Host.rvt':[link_fixtures.cloud_row()]})
        out=os.path.join(self.root,'out');back=s.SessionBackend(self.db,self.app,out,self.r)
        opts=f.defaults();opts['repath']=False
        result=engine.transmit([key],out,back,opts)
        self.assertEqual(result['counts']['hosts_copied'],1,repr(result['issues']))
        self.assertEqual(result['counts']['revit_links_copied'],1)
        self.assertTrue(os.path.isfile(os.path.join(out,'Links','Revit','Architecture.rvt')))
        self.assertEqual(len(result['files']),2)
        self.assertEqual(result['files'][0]['source_context']['inventory_basis'],'SAVED_SNAPSHOT_INSPECTION')
        warning=next(i for i in result['issues'] if i['code']=='SAVED_CACHE_DIFFERS_FROM_LOADED')
        self.assertEqual(warning['severity'],'warning')
        self.assertIn(LOADED,warning['message']);self.assertIn(SAVED,warning['message'])
        self.assertFalse(self.doc.IsModified)
        self.assertEqual(self.doc.PathName,'Autodesk Docs://Project/Host.rvt')
        self.assertEqual(f.digest(self.original),before);self.assertEqual(f.digest(link),link_before)
        self.assertEqual(result['counts']['revit_links_verified'],0)
        self.assertNotIn('NOT_PERFORMED',result['link_discovery_status'])
        with io.open(os.path.join(out,'REPORT.txt'),encoding='utf-8') as inp:report=inp.read()
        self.assertIn('SAVED_CACHE_DIFFERS_FROM_LOADED',report)
        self.assertIn(LOADED,report);self.assertIn(SAVED,report)

if __name__=='__main__':unittest.main()
