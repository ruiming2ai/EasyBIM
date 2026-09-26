# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io, os, sys, unittest
import test_212_cache as fixtures
import test_214_layout as layouts
from test_payload_acquisition import compound
from easybim_etransmit import cache_sources as c, files as f, engine

class PairedCacheTests(unittest.TestCase):
    setUp=getattr(fixtures.CacheTests.setUp, '__func__', fixtures.CacheTests.setUp)
    put=getattr(fixtures.CacheTests.put, '__func__', fixtures.CacheTests.put)
    store=getattr(fixtures.CacheTests.store, '__func__', fixtures.CacheTests.store)

    def pair(self):
        direct=self.put()
        linked=self.put(project=os.path.join(fixtures.P,'LinkedModels'),payload=compound(suffix='linked'))
        return direct,linked

    def test_uploaded_modified_morrison_host_and_repeated_link(self):
        direct,linked=self.pair();store=self.store(modified=True)
        self.entry['document_version']=dict(guid='0a5e7d00-ddf2-4377-b244-d54e1754beae',saves=250)
        store.read_info=lambda path:dict(version=dict(guid='337646cd-1395-4c60-a8ee-f3974048aa53',saves=250),format='2024')
        before=[f.digest(p) for p in (direct,linked)]
        result=store.capture(self.entry)
        self.assertEqual(result['metadata']['cache_path'],direct)
        self.assertEqual(result['metadata']['cache_selection_reason'],'DIRECT_SAME_EDITION_PAIR')
        self.assertEqual(result['metadata']['identical_candidates'],1)
        self.assertEqual(f.digest(result['path']),before[0])
        self.entry.update(is_linked=True,is_modified=False,document_version=None)
        again=store.capture(self.entry)
        self.assertEqual(again['path'],result['path'])
        self.assertTrue(again['metadata']['snapshot_reused'])
        self.assertEqual([f.digest(p) for p in (direct,linked)],before)
        self.assertEqual(len(os.listdir(self.out)),1)

    def test_order_does_not_change_choice(self):
        direct,linked=self.pair();store=self.store(linked=True)
        real=c.find_candidates
        try:
            for paths in ([direct,linked],[linked,direct]):
                c.find_candidates=lambda *args:paths
                self.assertEqual(store.capture(self.entry)['metadata']['cache_path'],direct)
        finally:c.find_candidates=real

    def test_exact_loaded_revision_wins_over_direct_role(self):
        direct,linked=self.pair();store=self.store(linked=True)
        store.read_info=lambda p:dict(version=self.version if f.digest(p)==f.digest(linked) else dict(guid=fixtures.W,saves=9),format='2024')
        self.assertEqual(store.capture(self.entry)['metadata']['cache_path'],linked)

    def test_different_revisions_still_ambiguous_without_exact(self):
        direct,linked=self.pair();store=self.store(linked=True);self.entry['document_version']=None
        store.read_info=lambda p:dict(version=self.version if f.digest(p)==f.digest(linked) else dict(guid=fixtures.W,saves=9),format='2024')
        with self.assertRaises(c.CacheError) as caught:store.capture(self.entry)
        self.assertEqual(caught.exception.code,'CACHE_CANDIDATES_AMBIGUOUS')

    def test_different_formats_still_ambiguous(self):
        direct,linked=self.pair();store=self.store(linked=True)
        store.read_info=lambda p:dict(version=self.version,format='2023' if f.digest(p)==f.digest(linked) else '2024')
        with self.assertRaises(c.CacheError):store.capture(self.entry)

    def test_other_account_cannot_be_resolved_by_pair(self):
        self.pair();self.put(account='OTHER');store=self.store(linked=True)
        with self.assertRaises(c.CacheError):store.capture(self.entry)

    def test_other_root_cannot_be_resolved_by_pair(self):
        self.pair();other=os.path.join(self.root,'other','CollaborationCache');self.put(base=other)
        store=self.store(linked=True);store.roots.append(other)
        with self.assertRaises(c.CacheError):store.capture(self.entry)

    def test_unknown_layout_is_not_a_linked_role(self):
        self.put();self.put(project=os.path.join(fixtures.P,'other','LinkedModels'),payload=compound(suffix='other'))
        store=self.store(linked=True)
        with self.assertRaises(c.CacheError):store.capture(self.entry)

    def test_conflicting_same_role_is_rejected(self):
        self.pair();self.put(filename='{'+fixtures.M+'}.rvt',payload=compound(suffix='direct conflict'))
        store=self.store(linked=True)
        with self.assertRaises(c.CacheError):store.capture(self.entry)

    def test_linked_only_is_usable(self):
        linked=self.put(project=os.path.join(fixtures.P,'LinkedModels'))
        store=self.store(linked=True)
        self.assertEqual(store.capture(self.entry)['metadata']['cache_path'],linked)

    def test_tampered_retained_snapshot_is_not_reused(self):
        self.pair();store=self.store(linked=True);result=store.capture(self.entry)
        with open(result['path'],'ab') as out:out.write(b'changed')
        with self.assertRaises(c.CacheError) as caught:store.capture(self.entry)
        self.assertEqual(caught.exception.code,'CACHE_SNAPSHOT_CHANGED')

class CachePackageTests(unittest.TestCase):
    setUp=getattr(layouts.LayoutTests.setUp, '__func__', layouts.LayoutTests.setUp)
    file=getattr(layouts.LayoutTests.file, '__func__', layouts.LayoutTests.file)
    run_export=getattr(layouts.LayoutTests.run_export, '__func__', layouts.LayoutTests.run_export)

    def test_failed_host_explains_unperformed_discovery_and_candidates(self):
        back=layouts.Backend()
        evidence=dict(expected_document_version=dict(guid=fixtures.V,saves=250),attempts=[dict(path='cache/model.rvt',status='REJECTED',cache_role='DIRECT',code='CACHE_VERSION_MISMATCH')])
        back.source_context=lambda source:dict(cache_evidence=evidence)
        def fail(*args):raise c.CacheError('CACHE_CANDIDATES_AMBIGUOUS','cache conflict',evidence)
        back.acquire_file=fail
        result,_=self.run_export(backend=back)
        self.assertTrue(result['link_discovery_status'].startswith('NOT_PERFORMED'))
        self.assertIn('NOT_PERFORMED',engine.completion_message([result],1))
        for name in ('REPORT.txt','DIAGNOSTICS.txt'):
            with io.open(os.path.join(self.out,name),encoding='utf-8') as inp:text=inp.read()
            self.assertIn('NOT_PERFORMED',text)
            self.assertIn('Role: DIRECT',text)
            self.assertIn('CACHE_VERSION_MISMATCH',text)

    def test_paired_snapshot_host_repeated_link_is_copied_once_without_nested_scan(self):
        case=PairedCacheTests('test_linked_only_is_usable');case.setUp()
        try:
            case.pair();store=case.store(modified=True)
            alias=self.file('Alias/Host.rvt');child=self.file('Arch.rvt')
            for mode in ('categories','original','flat'):
                back=layouts.Backend({self.host:[dict(source=child,id='1',kind='RevitLink'),dict(source=child,id='2',kind='RevitLink')],child:[dict(source=alias,id='3',kind='RevitLink')]})
                def acquire(source,target,*args):
                    if source in (self.host,alias):
                        entry=dict(case.entry,is_linked=source==alias)
                        snapshot=store.capture(entry)
                        meta=f.copy_file(snapshot['path'],target)
                        meta['source_stability']='SESSION_SNAPSHOT'
                        return meta
                    return f.copy_file(source,target)
                back.acquire_file=acquire
                back.acquired_identity=lambda source,meta:'saved-host-edition' if source in (self.host,alias) else source
                result,_=self.run_export(backend=back,mode=mode,out=self.out+mode)
                self.assertEqual(result['status'],'COLLECTED',repr(result['issues']))
                self.assertEqual(len(result['files']),2)
                self.assertEqual(len(result['references']),2)
                self.assertTrue(os.path.isdir(os.path.join(self.out+mode,'Links')))
                self.assertEqual(result['references'][0]['target'],result['references'][1]['target'])
                self.assertFalse(any(r.get('source')==alias for r in result['files']))
                child_record=next(r for r in result['files'] if r.get('source')==child)
                self.assertEqual(child_record['inventory_status'],'NOT_INSPECTED_LINK_FILE')
                for record in result['files']:
                    self.assertEqual(f.digest(record['target']),record['packaged_sha256'])
        finally:case.doCleanups()

if __name__=='__main__':unittest.main()
