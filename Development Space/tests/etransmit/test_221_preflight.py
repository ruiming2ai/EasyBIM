# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os,sys,unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'));sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import preflight as p, files as f
class Obj(object):
    def __init__(self,**kw):self.__dict__.update(kw)
class SaveChoices(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        self.doc=Obj(PathName='Host.rvt',Title='Host',IsModified=True,IsReadOnly=False,IsLinked=False,IsModifiable=False,IsModelInCloud=False,IsWorkshared=True)
        def save():self.calls.append('save');self.doc.IsModified=False
        self.doc.Save=save
        self.rows=[Obj(Mode='LIVE_DOCUMENT',Document=self.doc)]
    def test_continue_does_not_save(self):
        p.save_selected(self.rows,'continue');self.assertEqual(self.calls,[])
    def test_cancel_does_not_save(self):
        with self.assertRaises(f.Cancelled):p.save_selected(self.rows,'cancel')
        self.assertEqual(self.calls,[])
    def test_save_is_in_place_once_even_duplicate_rows(self):
        result=p.save_selected(self.rows*2,'save');self.assertEqual(self.calls,['save']);self.assertEqual(self.doc.PathName,'Host.rvt');self.assertEqual(result[0]['action'],'SAVED_IN_PLACE');self.assertEqual(result[0]['source'],'Host.rvt')
    def test_save_failure_never_falls_back_to_save_as_sync_or_publish(self):
        def fail():raise IOError('save blocked')
        self.doc.Save=fail
        for name in ('SaveAs','SaveCloudModel','SynchronizeWithCentral'):setattr(self.doc,name,lambda *a:self.fail('unsafe fallback'))
        with self.assertRaises(p.PreflightError):p.save_selected(self.rows,'save')
    def test_unsaved_new_document_is_not_saved_as_implicitly(self):
        self.doc.PathName=''
        with self.assertRaises(p.PreflightError):p.save_selected(self.rows,'save')
        self.assertEqual(self.calls,[])
    def test_save_that_changes_document_path_aborts(self):
        self.doc.Save=lambda:setattr(self.doc,'PathName','Wrong.rvt')
        with self.assertRaises(p.PreflightError):p.save_selected(self.rows,'save')
    def test_save_that_remains_modified_aborts(self):
        self.doc.Save=lambda:self.calls.append('save')
        with self.assertRaises(p.PreflightError):p.save_selected(self.rows,'save')
        self.assertEqual(self.calls,['save'])

class ReloadChoices(unittest.TestCase):
    def setUp(self):
        self.log=[];self.entries={'host':dict(source='host',name='Host.rvt',preflight_events=[],document=Obj(IsModified=False,IsReadOnly=False,IsModifiable=False),inventory=dict(references=[],issues=[]))}
        self.registry=Obj(get=self.entries.get,cancelled=None)
        self.registry.snapshot=lambda key,pulse=None:self.log.append('snapshot:'+key) or 'snapshot.rvt'
        self.registry.capture_reloaded_link=lambda choice:self.log.append('capture:'+choice.element_id)
        self.loaded=False;self.local=False
        def reload():self.log.append('reload');self.loaded=True;return Obj(LoadResult='LinkLoaded')
        def unload(arg):self.log.append('unload');self.loaded=False
        def revert():self.log.append('revert');self.local=False;self.loaded=True;return 'Loaded'
        def unloadlocal(arg):self.log.append('unload_local');self.local=True;self.loaded=False;return True
        self.link=Obj(Reload=reload,Unload=unload,RevertLocalUnloadStatus=revert,UnloadLocally=unloadlocal,IsNotLoadedIntoMultipleOpenDocuments=lambda:True)
        self.choice=Obj(owner='host',element_id='42',link=self.link,local_override=False,original_loaded=False,Name='Arch.rvt',is_loaded=lambda:self.loaded,is_local=lambda:self.local)
    def test_snapshot_before_selected_reload_and_restore_after_capture(self):
        with p.TemporaryReloads(self.registry,[self.choice]) as context:context.acquire()
        self.assertEqual(self.log,['snapshot:host','reload','capture:42','unload']);self.assertFalse(self.loaded)
    def test_no_selection_does_not_reload_or_snapshot(self):
        with p.TemporaryReloads(self.registry,[]) as context:context.acquire()
        self.assertEqual(self.log,[])
    def test_failed_partial_reload_is_restored(self):
        def fail():self.loaded=True;self.log.append('failed_reload');raise IOError('partial load')
        self.link.Reload=fail
        with p.TemporaryReloads(self.registry,[self.choice]) as context:context.acquire()
        self.assertFalse(self.loaded);self.assertIn('unload',self.log)
        self.assertTrue(self.entries['host']['inventory']['issues'])
    def test_cancel_during_capture_still_restores(self):
        def fail(c):raise f.Cancelled()
        self.registry.capture_reloaded_link=fail
        with self.assertRaises(f.Cancelled):
            with p.TemporaryReloads(self.registry,[self.choice]) as context:context.acquire()
        self.assertFalse(self.loaded);self.assertIn('unload',self.log)
    def test_original_local_override_is_preserved(self):
        self.local=True;self.choice.local_override=True
        with p.TemporaryReloads(self.registry,[self.choice]) as context:context.acquire()
        self.assertTrue(self.local);self.assertFalse(self.loaded)
        self.assertEqual(self.log,['snapshot:host','revert','capture:42','unload_local'])
        self.assertNotIn('unload',self.log)
    def test_changed_local_override_is_not_modified_after_selection(self):
        self.local=True  # Originally global-unloaded; another action changed the override.
        with p.TemporaryReloads(self.registry,[self.choice]) as context:context.acquire()
        self.assertEqual(self.log,['snapshot:host'])
        self.assertTrue(self.local)
        self.assertTrue(any(i['code']=='SOURCE_LINK_STATE_CHANGED' for i in self.entries['host']['inventory']['issues']))
    def test_restore_failure_is_not_silently_swallowed(self):
        self.link.Unload=lambda arg:None
        with self.assertRaises(p.PreflightError):
            with p.TemporaryReloads(self.registry,[self.choice]) as context:context.acquire()
        self.assertTrue(any(i['code']=='SOURCE_LINK_RESTORE_FAILED' for i in self.entries['host']['inventory']['issues']))
    def test_snapshot_failure_happens_before_any_source_mutation(self):
        def fail(key,pulse=None):raise IOError('no saved source')
        self.registry.snapshot=fail
        with self.assertRaises(IOError):
            with p.TemporaryReloads(self.registry,[self.choice]) as context:context.acquire()
        self.assertEqual(self.log,[])

import copy
import test_212_session as fixtures
from test_212_cache import W,P
from test_payload_acquisition import compound
from easybim_etransmit import engine,session
class ReloadIntegration(fixtures.SavedCacheSession):
    def test_real_registry_acquires_unloaded_cloud_link_then_export_stays_unloaded(self):
        original_name='Architecture.rvt';folder=os.path.join(self.cache,'USER',P,'LinkedModels');os.makedirs(folder)
        linkedfile=os.path.join(folder,W+'.rvt')
        with open(linkedfile,'wb') as out:out.write(compound(suffix='architecture'))
        child=copy.copy(self.doc);child.Title='Architecture';child.IsLinked=True
        child.GetCloudModelPath=lambda:Obj(GetModelGUID=lambda:W,GetProjectGUID=lambda:P,Region='US')
        child.PathName='Autodesk Docs://Project/Architecture.rvt'
        ident=Obj(IntegerValue=42);loaded=[False];history=[]
        link=Obj(Id=ident,Name=original_name,IsNestedLink=False,LocallyUnloaded=False,
                 IsNotLoadedIntoMultipleOpenDocuments=lambda:True)
        def reload():loaded[0]=True;history.append('reload');return Obj(LoadResult='LinkLoaded')
        def unload(_):loaded[0]=False;history.append('unload')
        link.Reload=reload;link.Unload=unload
        self.db.RevitLinkType=Obj(IsLoaded=lambda doc,i:loaded[0])
        instance=Obj(GetTypeId=lambda:ident,GetLinkDocument=lambda:child if loaded[0] else None)
        self.r.scanner.elements=lambda doc,kind:([link] if kind=='RevitLinkType' else [instance] if kind=='RevitLinkInstance' else []) if doc is self.doc else []
        def scan(doc,src,info,result):
            if doc is self.doc:result['references'].append(dict(id='42',element_id='42',kind='RevitLink',loaded=False,source='',td=False,special='external',cloud_identity=dict(project_guid=P,model_guid=W,region='US')))
        self.r.scanner.scan_open=scan
        key=self.r.add_live(self.doc);choices=p.unloaded_links(self.r,[key])
        self.assertEqual(len(choices),1);self.assertFalse(choices[0].Checked)
        with p.TemporaryReloads(self.r,choices) as transfer:transfer.acquire()
        self.assertFalse(loaded[0]);self.assertEqual(history,['reload','unload'])
        self.assertTrue(self.r.get(key).get('snapshot_path'))
        opts=f.defaults();opts['repath']=False
        b=session.SessionBackend(self.db,self.app,self.root,self.r)
        b.open_copy=lambda *a:self.fail('collect-only host must not open')
        result=engine.transmit([key],os.path.join(self.root,'out'),b,opts)
        self.assertEqual(result['counts']['revit_links_copied'],1,repr(result['issues']))
        self.assertFalse(result['references'][0]['package_loaded'])
        target=next(r['target'] for r in result['files'] if not r.get('is_primary_host'))
        self.assertEqual(f.digest(target),f.digest(linkedfile))
        self.assertEqual(f.digest(result['files'][0]['target']),f.digest(self.original))
for name in fixtures.SavedCacheSession.__dict__:
    if name.startswith('test_') and name not in ReloadIntegration.__dict__:setattr(ReloadIntegration,name,None)

if __name__=='__main__':unittest.main()
