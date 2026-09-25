# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io, json, os, shutil, sys, tempfile, unittest, zipfile
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f, layout, engine, batch

class Backend(object):
    def __init__(self, refs=None):self.refs=refs or {};self.calls=[]
    def set_staging_root(self,root):self.stage=root
    def scan(self,source,path,options):return dict(references=self.refs.get(source,[]),issues=[],version='2024')
    def finish(self,stage,target,rows,options):
        self.calls.append(('finish',target))
        # Model surrogate stores exactly the relative links that must survive a move.
        links=[dict(id=r.get('id'),path=os.path.relpath(r['target'],os.path.dirname(target))) for r in rows if r.get('target')]
        with io.open(target,'w',encoding='utf-8') as out:out.write(json.dumps(links))
        return []
    def verify_package(self,target,rows,options):
        self.calls.append(('verify',target))
        with io.open(target,encoding='utf-8') as inp:links=json.load(inp)
        for link in links:
            path=os.path.normpath(os.path.join(os.path.dirname(target),link['path']))
            if not f.file_exists(path):raise IOError('relative link missing: '+path)
        for row in rows:
            if row.get('target'):row['verification']='PATH_AND_LOAD_CHECKED' if row.get('kind')=='RevitLink' else 'PATH_CHECKED'
        return []

class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET214_');self.addCleanup(f.remove_tree_retry,self.root)
        self.source=os.path.join(self.root,'src');os.makedirs(self.source)
        self.host=self.file('Host.rvt',b'original host');self.out=os.path.join(self.root,'out')
    def file(self,path,data=b'bytes'):
        path=os.path.join(self.source,*path.split('/'));f.ensure_directory(os.path.dirname(path))
        with open(path,'wb') as out:out.write(data)
        return path
    def run_export(self,refs=None,mode='categories',backend=None,extras=None,options=None,out=None,cancelled=None):
        opts=f.defaults();opts['file_structure']=mode
        if options:opts.update(options)
        b=backend or Backend({self.host:refs or []})
        result=engine.transmit([self.host],out or self.out,b,opts,extras,cancelled)
        if result.get('recovery_directory'):self.addCleanup(f.remove_tree_retry,result['recovery_directory'])
        return result,b
    def test_all_three_layouts_and_source_safety(self):
        pdf=self.file('Drawings/Detail.pdf')
        for mode in ('categories','original','flat'):
            result,b=self.run_export([dict(source=pdf,id='1')],mode,out=self.out+mode)
            self.assertEqual(result['status'],'COLLECTED',repr(result['issues']))
            row=next(r for r in result['files'] if r['source']==pdf)
            wanted={'categories':'Links/PDF/Detail.pdf','flat':'Links/Detail.pdf','original':layout.original(f.mirror_path(pdf))}[mode]
            self.assertEqual(row['relative'],wanted)
            self.assertEqual(result['files'][0]['relative'],'Host.rvt')
            self.assertEqual(result['references'][0]['target'],row['target'])
            self.assertFalse(os.path.exists(b.stage))
        with open(self.host,'rb') as inp:self.assertEqual(inp.read(),b'original host')
    def test_category_names(self):
        categories=['revit','cad','ifc','pdf','images','decals','pointcloud','navisworks','dwf','keynotes','analysis','spreadsheets','other']
        rows=[dict(source='/source/'+c+'.bin',category=c) for c in categories]
        layout.plan(rows,'categories')
        self.assertEqual([r['relative'].split('/')[1] for r in rows],['Revit','CAD','IFC','PDF','Images','Images','Point Cloud','Navisworks','DWF','Keynotes','Analysis','Spreadsheets','Other'])
    def test_identical_unrelated_files_do_not_merge(self):
        a=self.file('A/Detail.pdf');b=self.file('B/Detail.pdf')
        result,_=self.run_export([dict(source=a),dict(source=b)])
        rows=[r for r in result['files'] if r['category']=='pdf']
        self.assertEqual(len(rows),2)
        self.assertNotEqual(rows[0]['relative'],rows[1]['relative'])
        self.assertTrue(all(r['collision_separated'] for r in rows))
        self.assertTrue(all(r['relative'].endswith('/Detail.pdf') for r in rows))
    def test_layout_is_independent_of_discovery_order(self):
        sources=['/A/Details.pdf','/B/Details.pdf','/A/DETAILS.PDF']
        one=[dict(source=s,category='pdf') for s in sources]
        two=[dict(source=s,category='pdf') for s in reversed(sources)]
        layout.plan(one,'flat');layout.plan(two,'flat')
        self.assertEqual(dict((r['source'],r['relative']) for r in one),dict((r['source'],r['relative']) for r in two))
        self.assertEqual(len(set(layout.key(r['relative']) for r in one)),3)
    def test_repeat_and_mapped_alias_share_one_target(self):
        pdf=self.file('Details.pdf')
        result,_=self.run_export([dict(source=pdf,id='1'),dict(source='BIM 360://Project/Details.pdf',id='2')],options={'mappings':[('BIM 360://Project',self.source)]})
        self.assertEqual(len(result['files']),2)
        self.assertEqual(result['references'][0]['target'],result['references'][1]['target'])
    def test_verified_identity_aliases_share_one_file_and_scan(self):
        a=self.file('A/Link.rvt');b=self.file('B/Link.rvt')
        back=Backend({self.host:[dict(source=a,id='1'),dict(source=b,id='2')]})
        back.acquired_identity=lambda source,meta:'proven-edition' if source in (a,b) else source
        result,_=self.run_export(backend=back)
        self.assertEqual(len(result['files']),2)
        self.assertEqual(result['references'][0]['target'],result['references'][1]['target'])
        self.assertEqual(len([c for c in back.calls if c[0]=='finish']),2)
    def test_conflicting_bytes_for_identity_are_rejected(self):
        a=self.file('A/Link.rvt',b'a');b=self.file('B/Link.rvt',b'b')
        back=Backend({self.host:[dict(source=a),dict(source=b)]})
        back.acquired_identity=lambda source,meta:'same-edition' if source in (a,b) else source
        result,_=self.run_export(backend=back)
        self.assertTrue(any('Conflicting acquired bytes' in i['message'] for i in result['issues']))
        self.assertEqual(len([r for r in result['files'] if r['status']=='COPIED']),2)
    def test_nested_links_and_cycle_move_with_package(self):
        child=self.file('Arch.rvt');pdf=self.file('Sheet.pdf')
        back=Backend({self.host:[dict(source=child,id='1',kind='RevitLink')],child:[dict(source=pdf,id='2'),dict(source=self.host,id='3',kind='RevitLink')]})
        result,_=self.run_export(backend=back)
        self.assertEqual(result['status'],'COLLECTED',repr(result['issues']))
        moved=os.path.join(self.root,'moved');shutil.move(self.out,moved)
        for row in result['files']:
            if row['source'].endswith('.rvt'):back.verify_package(os.path.join(moved,*row['relative'].split('/')),[],{})
    def test_rcp_support_preserved_in_every_mode(self):
        rcp=self.file('Cloud/Survey.rcp');scan=self.file('Cloud/Survey Support/sub/scan.rcs')
        for mode in ('categories','flat','original'):
            result,_=self.run_export([dict(source=rcp)],mode,out=self.out+mode)
            by=dict((r['source'],r) for r in result['files'])
            self.assertEqual(os.path.relpath(by[scan]['target'],os.path.dirname(by[rcp]['target'])).replace('\\','/'),'Survey Support/sub/scan.rcs')
    def test_colliding_rcp_sets_keep_their_own_support(self):
        a=self.file('A/Survey.rcp');sa=self.file('A/Survey Support/scan.rcs')
        b=self.file('B/Survey.rcp');sb=self.file('B/Survey Support/scan.rcs')
        result,_=self.run_export([dict(source=a),dict(source=b)],'flat')
        by=dict((r['source'],r['target']) for r in result['files'])
        self.assertNotEqual(os.path.dirname(by[a]),os.path.dirname(by[b]))
        for rcp,scan in ((a,sa),(b,sb)):
            self.assertEqual(os.path.relpath(by[scan],os.path.dirname(by[rcp])).replace('\\','/'),'Survey Support/scan.rcs')
    def test_added_directory_and_direct_reference_do_not_duplicate(self):
        a=self.file('CADSet/model.dwg');self.file('CADSet/images/a.png')
        result,_=self.run_export([dict(source=a)],'flat',extras=[os.path.dirname(a)])
        self.assertEqual(len(result['files']),3)
        by=dict((r['source'],r['relative']) for r in result['files'])
        self.assertEqual(by[a],'Links/CADSet/model.dwg')
        self.assertTrue(any(r['relative']=='Links/CADSet/images/a.png' for r in result['files']))
    def test_nested_added_folders_and_rcp_overlap_are_deduplicated(self):
        a=self.file('Set/cloud/Survey.rcp');self.file('Set/cloud/Survey Support/a.rcs')
        result,_=self.run_export([dict(source=a)],'flat',extras=[os.path.join(self.source,'Set'),os.path.dirname(a)])
        self.assertEqual(len(result['files']),3)
        self.assertTrue(any(r['relative']=='Links/Set/cloud/Survey Support/a.rcs' for r in result['files']))
    def test_analysis_directory_retains_internal_structure(self):
        a=self.file('AnalysisSet/html/page.html');self.file('AnalysisSet/html/image.png')
        result,_=self.run_export([dict(source=os.path.join(self.source,'AnalysisSet'),kind='SystemsAnalysisReport')])
        self.assertTrue(any(r['relative']=='Links/Analysis/AnalysisSet/html/page.html' for r in result['files']))
    def test_no_old_trees_or_duplicates_in_zip(self):
        a=self.file('deep/a.pdf');result,_=self.run_export([dict(source=a),dict(source=a)])
        target=self.out+'.zip';engine.zip_package(self.out,target)
        with zipfile.ZipFile(target) as archive:
            names=archive.namelist()
            self.assertEqual(sum(name.endswith('a.pdf') for name in names),1)
            self.assertFalse(any(part in ('Sources','_Refs','_HostState') for name in names for part in name.split('/')))
    def test_final_verification_does_not_inherit_temporary_success(self):
        a=self.file('Link.rvt');back=Backend({self.host:[dict(source=a,kind='RevitLink',id='1')]})
        real=back.verify_package
        def verify(target,rows,opts):
            if f.within(target,self.out):raise IOError('Revit rejected final long path')
            return real(target,rows,opts)
        back.verify_package=verify
        result,_=self.run_export(backend=back)
        self.assertEqual(result['files'][0]['preparation_verification'],'OPENED_AND_REFERENCES_CHECKED')
        self.assertEqual(result['files'][0]['model_verification'],'FAILED')
        self.assertNotIn('verification',result['references'][0])
        self.assertEqual(result['counts']['revit_links_verified'],0)
        self.assertTrue(f.file_exists(result['files'][0]['target']))
    def test_failure_before_processing_keeps_acquired_host(self):
        back=Backend()
        back.finish=lambda *args:(_ for _ in ()).throw(RuntimeError('staging path rejected'))
        result,_=self.run_export(backend=back)
        self.assertEqual(f.digest(self.host),f.digest(result['files'][0]['target']))
        self.assertEqual(result['files'][0]['model_verification'],'SKIPPED_PROCESSING_FAILURE')
    def test_repath_off_has_no_verification_claim(self):
        result,b=self.run_export(options={'repath':False})
        self.assertEqual(b.calls,[])
        self.assertEqual(result['files'][0]['model_verification'],'NOT_ATTEMPTED')
    def test_final_delivery_failure_retains_recovery(self):
        original=f.copy_file
        def fail(source,target,*args,**kwargs):
            if f.within(target,self.out) and target.endswith('.rvt'):raise IOError('disk full')
            return original(source,target,*args,**kwargs)
        f.copy_file=fail
        try:result,b=self.run_export()
        finally:f.copy_file=original
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['files'][0]['status'],'DELIVERY_FAILED')
        self.assertTrue(os.path.isdir(result['recovery_directory']))
        self.assertTrue(os.path.exists(os.path.join(self.out,'manifest.json')))
    def test_partial_delivery_does_not_open_host_with_missing_link(self):
        link=self.file('Link.rvt');original=f.copy_file
        def fail(source,target,*args,**kwargs):
            if f.within(target,self.out) and target.endswith('Link.rvt'):raise IOError('link delivery failed')
            return original(source,target,*args,**kwargs)
        f.copy_file=fail
        try:result,b=self.run_export([dict(source=link,kind='RevitLink',id='1')])
        finally:f.copy_file=original
        self.assertEqual(result['files'][0]['model_verification'],'DEFERRED')
        self.assertFalse(any(call[0]=='verify' and f.within(call[1],self.out) for call in b.calls))
    def test_cancelled_processing_rolls_back_and_delivers_original(self):
        back=Backend()
        def cancel(stage,target,rows,options):
            with open(target,'wb') as out:out.write(b'damaged')
            raise f.Cancelled()
        back.finish=cancel
        result,_=self.run_export(backend=back)
        self.assertEqual(result['status'],'CANCELLED')
        self.assertEqual(f.digest(self.host),f.digest(result['files'][0]['target']))
        self.assertFalse(os.path.exists(back.stage))
    def test_two_hosts_get_independent_packages_even_old_combined_setting(self):
        second=self.file('Second.rvt');link=self.file('Link.rvt')
        opts=f.defaults();opts['per_model']=False
        results=batch.run_batch([self.host,second],self.out,lambda root,source:Backend({source:[dict(source=link)]}),opts)
        self.assertEqual(len(results),2)
        for result in results:
            self.assertTrue(os.path.exists(os.path.join(result['root'],'Links','Revit','Link.rvt')))
    def test_missing_invalid_settings_use_categories(self):
        self.assertEqual(f.defaults()['file_structure'],'categories')
        for value in (None,'','bogus',[],{},7):self.assertEqual(layout.mode(value),'categories')
    def test_large_layout_and_file_directory_conflicts(self):
        rows=[dict(source='/s/%05d.pdf'%i,category='pdf') for i in range(3000)]
        layout.plan(rows,'flat')
        self.assertEqual(len(set(r['relative'] for r in rows)),3000)
        rows=[dict(source='/a/Set',category='other'),dict(source='/b/Set/x.pdf',category='pdf')]
        layout.plan(rows,'flat',[dict(root='/b/Set',category='other')])
        self.assertFalse(rows[1]['relative'].startswith(rows[0]['relative']+'/'))
    def test_cancel_during_layout_preserves_acquired_files_without_duplicate_trees(self):
        pdf=self.file('Sheet.pdf');back=Backend({self.host:[dict(source=pdf)]});original=f.copy_file;state=[False]
        def cancel(source,target,*args,**kwargs):
            if f.within(target,os.path.join(back.stage,'p')) and not state[0]:
                state[0]=True;raise f.Cancelled()
            return original(source,target,*args,**kwargs)
        f.copy_file=cancel
        try:result,_=self.run_export(backend=back)
        finally:f.copy_file=original
        self.assertEqual(result['status'],'CANCELLED')
        self.assertEqual(result['counts']['files_copied'],2)
        self.assertEqual(f.digest(self.host),f.digest(os.path.join(self.out,'Host.rvt')))
        self.assertTrue(os.path.isfile(os.path.join(self.out,'Links','PDF','Sheet.pdf')))
        self.assertFalse(os.path.exists(back.stage))
    def test_cancel_during_delivery_keeps_completed_host_and_recovery(self):
        link=self.file('Link.rvt');state=[False];original=f.copy_file
        def cancel_after_host(source,target,*args,**kwargs):
            result=original(source,target,*args,**kwargs)
            if target==os.path.join(self.out,'Host.rvt'):state[0]=True
            return result
        f.copy_file=cancel_after_host
        try:result,b=self.run_export([dict(source=link,kind='RevitLink')],cancelled=lambda:state[0])
        finally:f.copy_file=original
        self.assertEqual(result['status'],'CANCELLED')
        self.assertEqual(result['files'][0]['delivery_status'],'CHECKSUM_VERIFIED')
        self.assertEqual(result['files'][1]['status'],'NOT_DELIVERED')
        self.assertTrue(os.path.isdir(result['recovery_directory']))
        self.assertNotIn('verification',result['references'][0])
    def test_failed_report_delivery_retains_recovery_location(self):
        original=engine.write_reports
        def fail(result):raise IOError('report destination denied')
        engine.write_reports=fail;back=Backend()
        try:
            with self.assertRaises(IOError):self.run_export(backend=back)
        finally:engine.write_reports=original
        self.addCleanup(f.remove_tree_retry,back.stage)
        self.assertTrue(os.path.isdir(back.stage))
    def test_whole_batch_zip_omits_generated_individual_zips(self):
        opts=f.defaults();opts.update(zip=True,zip_per_model=True)
        batch.run_batch([self.host],self.out,lambda *args:Backend(),opts)
        self.assertTrue(os.path.isfile(os.path.join(self.out,'Host.zip')))
        with zipfile.ZipFile(self.out+'.zip') as archive:
            self.assertIn('Host/Host.rvt',archive.namelist())
            self.assertNotIn('Host.zip',archive.namelist())
    def test_acquired_cloud_identity_requires_the_same_saved_revision(self):
        from easybim_etransmit.session import SessionBackend
        class Registry(object):
            def get(self,key):return entries.get(key)
        project='11111111-1111-4111-8111-111111111111';model='22222222-2222-4222-8222-222222222222'
        entries=dict(a=dict(mode='LIVE_DOCUMENT',cloud=dict(project_guid=project,model_guid=model,region='us'),saved_document_version=dict(guid='one',saves=7)),
                     b=dict(mode='CACHED_CLOUD_REFERENCE',cloud=dict(project_guid=project,model_guid=model,region='US'),saved_document_version=dict(guid='one',saves=7)))
        b=object.__new__(SessionBackend);b.registry=Registry()
        self.assertEqual(b.acquired_identity('a',{}),b.acquired_identity('b',{}))
        entries['b']['saved_document_version']=dict(guid='two',saves=7)
        self.assertNotEqual(b.acquired_identity('a',{}),b.acquired_identity('b',{}))
    def test_long_batch_root_supports_reports_and_zip_delivery(self):
        if os.name!='nt':self.skipTest('Windows native long-path batch')
        out=os.path.join(self.root,*(['BatchDestination0123456789']*11))
        opts=f.defaults();opts.update(zip=True,zip_per_model=True)
        results=batch.run_batch([self.host],out,lambda *args:Backend(),opts)
        self.assertTrue(f.file_exists(os.path.join(out,'batch.json')))
        self.assertTrue(f.file_exists(os.path.join(out,'Host.zip')))
        self.assertTrue(f.file_exists(out+'.zip'))
        self.assertFalse(any('ZIP_FAILED' in i['code'] for r in results for i in r['issues']))
    def test_long_delivery_root_keeps_files_and_reports(self):
        if os.name!='nt':self.skipTest('Windows native long-path delivery')
        out=os.path.join(self.root,*(['LongDeliveryFolder0123456789']*10))
        result,_=self.run_export(out=out)
        self.assertTrue(f.file_exists(os.path.join(out,'Host.rvt')))
        self.assertTrue(f.file_exists(os.path.join(out,'manifest.json')))
        self.assertEqual(result['files'][0]['delivery_status'],'CHECKSUM_VERIFIED')
        self.assertEqual(result['files'][0]['model_verification'],'FAILED')
        self.assertEqual(engine.preflight_paths([self.host],out,f.defaults()),[])

if __name__=='__main__':unittest.main(verbosity=2)
