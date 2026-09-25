# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, io, shutil, tempfile, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f
from test_payload_acquisition import compound
P='11111111-1111-4111-8111-111111111111'
M='22222222-2222-4222-8222-222222222222'
V='33333333-3333-4333-8333-333333333333'
W='44444444-4444-4444-8444-444444444444'
class Obj(object):
    def __init__(self,**kw):self.__dict__.update(kw)
def cache_module(test):
    try:from easybim_etransmit import cache_sources
    except ImportError:test.fail('Dedicated read-only cache acquisition is missing')
    return cache_sources

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_cache212_');self.addCleanup(shutil.rmtree,self.root)
        self.cache=os.path.join(self.root,'Autodesk Revit 2024','CollaborationCache')
        self.out=os.path.join(self.root,'isolated')
        self.identity=dict(project_guid=P,model_guid=M,region='US')
        self.version=dict(guid=V,saves=9)
    def put(self,base=None,filename=None,payload=None,project=P,account='USER'):
        folder=os.path.join(base or self.cache,account,project)
        if not os.path.isdir(folder):os.makedirs(folder)
        path=os.path.join(folder,filename or M+'.rvt')
        with open(path,'wb') as s:s.write(compound() if payload is None else payload)
        return path
    def store(self,linked=False,modified=False):
        c=cache_module(self)
        store=c.Store(Obj(),Obj(VersionNumber='2024'),self.out,roots=[self.cache])
        store.read_info=lambda path:dict(version=dict(self.version),format='2024',workshared=True)
        self.entry=dict(name='Original Model.rvt',cloud=self.identity,document_version=dict(self.version),
                        is_linked=linked,is_modified=modified)
        return store
    def test_exact_guid_project_lookup_excludes_central_pac_and_backups(self):
        c=cache_module(self);valid=self.put()
        self.put(filename='Display model name.rvt')
        self.put(project=W)
        self.put(filename=M+'.0001.rvt')
        for bad in ('CentralCache','PacCache',M+'_backup'):
            self.put(base=os.path.join(self.cache,'USER',P),account=bad,project='junk')
        self.assertEqual(c.find_candidates([self.cache],self.identity),[valid])
    def test_guid_case_and_braces_supported_not_substring_identity(self):
        c=cache_module(self);valid=self.put(filename='{'+M.upper()+'}.rvt',project='{'+P.upper()+'}')
        self.put(filename='prefix'+M+'.rvt');self.put(project='prefix'+P)
        self.assertEqual(c.find_candidates([self.cache],self.identity),[valid])
    def test_missing_or_zero_guid_never_becomes_filename_search(self):
        c=cache_module(self);self.put()
        for identity in ({},dict(project_guid=P,model_guid='00000000-0000-0000-0000-000000000000'),dict(project_guid='../escape',model_guid=M)):
            with self.assertRaises(c.CacheError):c.find_candidates([self.cache],identity)
    def test_unrelated_file_bytes_not_read(self):
        c=cache_module(self);self.put(filename='Other.rvt')
        store=self.store();store.read_info=lambda p:self.fail('unrelated model must not be inspected')
        with self.assertRaises(c.CacheError):store.capture(self.entry)
    def test_capture_keeps_original_bytes_and_returns_verified_native_snapshot(self):
        path=self.put();before=f.digest(path);stamp=os.stat(path).st_mtime
        store=self.store();result=store.capture(self.entry)
        self.assertEqual(f.digest(path),before);self.assertEqual(os.stat(path).st_mtime,stamp)
        self.assertEqual(f.digest(result['path']),before)
        self.assertFalse(f.cache_source(result['path']))
        self.assertEqual(result['metadata']['cache_path'],path)
        self.assertEqual(result['metadata']['revision_check'],'MATCHES_LOADED_SAVED_VERSION')
        self.assertEqual(result['metadata']['cache_document_version'],self.version)
    def test_mismatched_link_revision_is_exported_as_explicit_saved_edition(self):
        c=cache_module(self);self.put();store=self.store(linked=True)
        store.read_info=lambda p:dict(version=dict(guid=W,saves=99),format='2024',workshared=True)
        result=store.capture(self.entry)
        self.assertEqual(result['metadata']['revision_check'],'SAVED_CACHE_DIFFERS_FROM_LOADED')
        self.assertEqual(result['metadata']['cache_document_version'],dict(guid=W,saves=99))
    def test_save_count_is_checked_not_only_guid(self):
        c=cache_module(self);self.put();store=self.store(linked=True)
        store.read_info=lambda p:dict(version=dict(guid=V,saves=10),format='2024',workshared=True)
        result=store.capture(self.entry)
        self.assertEqual(result['metadata']['revision_check'],'SAVED_CACHE_DIFFERS_FROM_LOADED')
    def test_modified_primary_can_export_unique_saved_cache_without_saving_edits(self):
        self.put();store=self.store(modified=True)
        store.read_info=lambda p:dict(version=dict(guid=W,saves=8),format='2024',workshared=True)
        result=store.capture(self.entry)
        self.assertEqual(result['metadata']['revision_check'],'SAVED_CACHE_ONLY_UNSAVED_EXCLUDED')
        self.assertTrue(result['metadata']['unsaved_edits_excluded'])
    def test_unmodified_primary_mismatch_is_explicit_saved_edition(self):
        c=cache_module(self);self.put();store=self.store()
        store.read_info=lambda p:dict(version=dict(guid=W,saves=8),format='2024',workshared=True)
        result=store.capture(self.entry)
        self.assertEqual(result['metadata']['revision_check'],'SAVED_CACHE_DIFFERS_FROM_LOADED')
        self.assertEqual(result['metadata']['loaded_document_version'],self.version)
        self.assertEqual(result['metadata']['cache_document_version'],dict(guid=W,saves=8))
    def test_ambiguous_cache_different_bytes_never_selects_newest(self):
        c=cache_module(self);self.put(account='ONE');self.put(account='TWO',payload=compound(suffix='different'))
        store=self.store(modified=True)
        with self.assertRaises(c.CacheError) as ctx:store.capture(self.entry)
        self.assertEqual(ctx.exception.code,'CACHE_CANDIDATES_AMBIGUOUS')
    def test_identical_matching_cache_duplicates_are_recorded(self):
        self.put(account='ONE');self.put(account='TWO');store=self.store()
        result=store.capture(self.entry)
        self.assertEqual(result['metadata']['identical_candidates'],2)
    def test_corrupt_or_zip_payload_is_not_accepted_as_native_cache(self):
        c=cache_module(self);self.put(payload=b'PK\x03\x04fakezip');store=self.store()
        with self.assertRaises(c.CacheError):store.capture(self.entry)
    def test_missing_version_metadata_not_accepted(self):
        c=cache_module(self);self.put();store=self.store();store.read_info=lambda p:dict(version=None,format='2024')
        with self.assertRaises(c.CacheError):store.capture(self.entry)
    def test_source_change_during_copy_never_publishes_snapshot(self):
        c=cache_module(self);path=self.put();store=self.store();changed=[False]
        def pulse(*args):
            if not changed[0]:
                changed[0]=True
                with open(path,'ab') as s:s.write(b'changed during copy')
        with self.assertRaises(c.CacheError):store.capture(self.entry,pulse)
        self.assertFalse(any(n=='Original Model.rvt' for r,d,names in os.walk(self.out) for n in names))
    def test_cancellation_does_not_touch_original_or_leave_partial_candidates(self):
        c=cache_module(self);path=self.put();store=self.store();before=f.digest(path)
        store.cancelled=lambda:True
        with self.assertRaises(f.Cancelled):store.capture(self.entry)
        self.assertEqual(f.digest(path),before)
    def test_cannot_stage_inside_cache(self):
        c=cache_module(self);self.put();store=self.store();store.staging_root=os.path.join(self.cache,'export')
        with self.assertRaises(c.CacheError):store.capture(self.entry)
    def test_cannot_follow_symlink_to_other_file(self):
        c=cache_module(self)
        if not hasattr(os,'symlink') or os.name=='nt':self.skipTest('portable symlink fixture')
        target=self.put(base=os.path.join(self.root,'actual'))
        directory=os.path.join(self.cache,'USER',P);os.makedirs(directory)
        os.symlink(target,os.path.join(directory,M+'.rvt'))
        self.assertEqual(c.find_candidates([self.cache],self.identity),[])
    def test_cache_write_guard_is_not_globally_disabled(self):
        path=self.put()
        self.assertIsNone(f.resolve_source(path))
        with self.assertRaises((IOError,ValueError)):f.copy_file(path,os.path.join(self.root,'wrong.rvt'))

class RootTests(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_roots212_');self.addCleanup(shutil.rmtree,self.root)
    def test_current_release_default_root_only(self):
        c=cache_module(self);env=dict(LOCALAPPDATA=self.root,APPDATA=self.root)
        roots=c.discover_roots(Obj(VersionNumber='2024'),env)
        self.assertIn(os.path.join(self.root,'Autodesk','Revit','Autodesk Revit 2024','CollaborationCache'),roots)
        self.assertFalse(any('2023' in x for x in roots))
    def test_utf16_ini_custom_location_read_without_changing_ini(self):
        c=cache_module(self);folder=os.path.join(self.root,'Autodesk','Revit','Autodesk Revit 2024');os.makedirs(folder)
        ini=os.path.join(folder,'Revit.ini');custom=os.path.join(self.root,'Cache Base')
        data=('[CloudModelCache]\r\nCacheLocation='+custom+'\r\n').encode('utf-16')
        with open(ini,'wb') as s:s.write(data)
        roots=c.discover_roots(Obj(VersionNumber='2024'),dict(APPDATA=self.root,LOCALAPPDATA=self.root))
        self.assertIn(os.path.join(custom,'Autodesk Revit 2024','CollaborationCache'),roots)
        with open(ini,'rb') as s:self.assertEqual(s.read(),data)
    def test_custom_year_folder_does_not_append_year_twice(self):
        c=cache_module(self);folder=os.path.join(self.root,'Autodesk','Revit','Autodesk Revit 2025');os.makedirs(folder)
        custom=os.path.join(self.root,'FastDisk','Autodesk Revit 2025')
        with io.open(os.path.join(folder,'Revit.ini'),'w',encoding='utf-8') as s:s.write('[CloudModelCache]\nCacheLocation='+custom)
        roots=c.discover_roots(Obj(VersionNumber='2025'),dict(APPDATA=self.root,LOCALAPPDATA=self.root))
        self.assertIn(os.path.join(custom,'CollaborationCache'),roots)
        self.assertFalse(any('Autodesk Revit 2025'+os.sep+'Autodesk Revit 2025' in p for p in roots))

if __name__=='__main__':unittest.main(verbosity=2)
