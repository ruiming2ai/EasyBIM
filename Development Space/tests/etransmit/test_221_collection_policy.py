# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import copy, io, os, unittest
import test_212_session as fixtures
from test_212_cache import Obj
from test_213_session import cloud_row
from easybim_etransmit import session as s, revit, files as f, engine

class CollectionPolicy(fixtures.SavedCacheSession):
    def test_modified_acc_uses_live_inventory_without_opening(self):
        self.doc.IsModified=True
        key=self.r.add_live(self.doc)
        self.r.get(key)['inventory']['references']=[dict(id='7',source='Drawing.pdf',kind='Image')]
        self.r.get(key)['cache_metadata']={'revision_check':'SAVED_CACHE_DIFFERS_FROM_LOADED'}
        b=s.SessionBackend(self.db,self.app,self.root,self.r)
        result=b.inventory_before_copy(key,dict(repath=False,cleanup=False,upgrade=False))
        self.assertIsNotNone(result)
        self.assertEqual(result['references'][0]['source'],'Drawing.pdf')
        self.assertFalse(result['opened_in_revit'])
    def test_closed_collect_only_never_opens_even_with_legacy_deep_true(self):
        stage=os.path.join(self.root,'stage.rvt')
        b=revit.Backend(self.db,self.app,self.root)
        b.basic=lambda p:dict(version='2024',workshared=False,central='')
        b.rows=lambda *a:[]
        b.open_copy=lambda *a:self.fail('collect-only must not open host')
        b.prepare_scan=lambda *a:self.fail('collect-only must not stage inspection')
        result=b.scan(stage,stage,dict(repath=False,cleanup=False,upgrade=False,deep=True))
        self.assertFalse(result['opened_in_revit'])
        self.assertFalse(result.get('open_failed',False))
    def test_unloaded_cloud_name_is_kept_from_link_type(self):
        ident=Obj(IntegerValue=42)
        link=Obj(Id=ident,Name='Architecture.rvt',IsNestedLink=False,LocallyUnloaded=False)
        row=cloud_row(ident='42',name='');row.pop('link_name')
        self.r.scanner.scan_open=lambda d,src,info,result:result['references'].append(row)
        self.r.scanner.elements=lambda doc,kind:[link] if kind=='RevitLinkType' else []
        key=self.r.add_live(self.doc)
        self.assertEqual(self.r.get(key)['inventory']['references'][0].get('link_name'),'Architecture.rvt')
        b=s.SessionBackend(self.db,self.app,self.root,self.r)
        result=b.inventory_before_copy(key,f.defaults())
        self.assertTrue(result['references'][0]['source'].startswith('cache://'))
    def test_repath_off_cleanup_or_upgrade_still_can_open(self):
        self.assertFalse(f.host_processing_allowed(dict(repath=False,cleanup=False,upgrade=False)))
        self.assertTrue(f.host_processing_allowed(dict(repath=False,cleanup=True,upgrade=False)))
        self.assertTrue(f.host_processing_allowed(dict(repath=False,cleanup=False,upgrade=True)))
    def test_old_load_unloaded_preference_cannot_force_package_loaded(self):
        path=os.path.join(self.root,'Link.rvt')
        with open(path,'wb') as out:out.write(self.payload)
        key=self.r.add_live(self.doc)
        self.r.get(key)['inventory']['references']=[dict(id='1',element_id='1',kind='RevitLink',source=path,loaded=False,td=True)]
        b=s.SessionBackend(self.db,self.app,self.root,self.r)
        b.requires_final_host_open=lambda *a:False
        b.finish=lambda *a:[];b.verify_package=lambda *a:[]
        opts=f.defaults();opts.update(repath=True,load_unloaded_files=True)
        result=engine.transmit([key],os.path.join(self.root,'out'),b,opts)
        self.assertFalse(result['references'][0]['package_loaded'])

for name in fixtures.SavedCacheSession.__dict__:
    if name.startswith('test_') and name not in CollectionPolicy.__dict__:setattr(CollectionPolicy,name,None)

class DialogContracts(unittest.TestCase):
    def test_obsolete_controls_removed(self):
        root=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
        with io.open(os.path.join(root,'EasyBIM.tab','Links.panel','e-transmit.pushbutton','window.xaml'),encoding='utf-8') as inp:text=inp.read()
        self.assertNotIn('x:Name="DeepScan"',text)
        self.assertNotIn('x:Name="LoadUnloadedFiles"',text)
        for name in ('Repath','Cleanup','Upgrade'):self.assertIn('x:Name="'+name+'"',text)


class CleanupPermission(fixtures.SavedCacheSession):
    def test_cleanup_and_upgrade_without_repath_process_host_even_if_link_is_missing(self):
        for option in ('cleanup','upgrade'):
            key=self.r.add_live(self.doc)
            self.r.get(key)['inventory']['references']=[dict(id='missing',element_id='1',kind='RevitLink',source=os.path.join(self.root,'Missing.rvt'),loaded=False)]
            b=s.SessionBackend(self.db,self.app,self.root,self.r);calls=[]
            def finish(stage,target,rows,opts):
                calls.append(dict(opts));return []
            b.finish=finish
            b.verify_package=lambda *a:self.fail('repath off must not open for link verification')
            opts=f.defaults();opts.update(repath=False,cleanup=False,upgrade=False);opts[option]=True
            result=engine.transmit([key],os.path.join(self.root,option),b,opts)
            self.assertEqual(len(calls),1,repr(result['issues']))
            self.assertFalse(calls[0]['repath'])
            self.assertEqual(result['files'][0]['processing_status'],'PROCESSED')
    def test_copy_report_explains_detach_without_claiming_standalone(self):
        key=self.r.add_live(self.doc);opts=f.defaults();opts.update(repath=False)
        b=s.SessionBackend(self.db,self.app,self.root,self.r)
        result=engine.transmit([key],os.path.join(self.root,'report'),b,opts)
        with io.open(os.path.join(result['root'],'START_HERE.txt'),encoding='utf-8') as inp:text=inp.read()
        self.assertIn('Detach from Central',text)
        self.assertIn('references were not changed',text)
        self.assertEqual(result['files'][0]['opening_guidance'],'DETACH_RECOMMENDED_FOR_WORKSHARED_COPY')
        self.assertNotEqual(result['files'][0]['original_central_association_preserved'],False)

for name in fixtures.SavedCacheSession.__dict__:
    if name.startswith('test_') and name not in CleanupPermission.__dict__:setattr(CleanupPermission,name,None)

if __name__=='__main__':unittest.main()
