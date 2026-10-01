# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io, json, os, sys, tempfile, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f, engine, model_payload, layout
from easybim_etransmit.revit import Backend as RevitBackend
from test_payload_acquisition import compound

class Backend(RevitBackend):
    """Only Autodesk API operations are substituted. File IO and engine are real."""
    def __init__(self,host,refs,output):
        RevitBackend.__init__(self,None,None,output)
        self.host=host;self.refs=refs;self.calls=[]
    def basic(self,path):return dict(version='2025',workshared=True,central=self.host)
    def scan(self,source,stage,options):
        self.calls.append(('scan',source))
        return dict(references=list(self.refs) if source==self.host else [],issues=[],version='2025',is_workshared=True)
    def finish(self,stage,target,rows,options):
        self.calls.append(('finish',target))
        # Only the isolated Revit serialization is substituted. The engine must
        # already have delivered every dependency to its final package path.
        assert options.get('independent_host')
        assert target == os.path.join(self.root, 'Host.rvt')
        assert all(f.file_exists(r['target']) for r in rows if r.get('target'))
        base=options.get('reference_target',target)
        self.paths=[os.path.relpath(r['target'],os.path.dirname(base)) for r in rows if r.get('target')]
        if options.get('repath'):
            for row in rows:
                if row.get('target'):row['verification']='SAVED_REFERENCE_CHECKED'
        with open(stage,'rb') as src,open(target,'wb') as dst:dst.write(src.read()+b'processed')
        return dict(issues=[],host_finalized=True,saved_references_checked=True,
                    verification_status='SAVED_REFERENCES_CHECKED',worker_repaired=True,
                    independent_package_central=True,verified_in_process=False)
    def verify_package(self,target,rows,options):
        self.calls.append(('verify',target))
        raise AssertionError('The parent must not reopen a worker-finalized host')
    def mark_transmitted_package(self,*args):
        self.calls.append(('mark_transmitted',))
        raise AssertionError('The parent must not mark an independent host transmitted')

class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET218_');self.addCleanup(f.remove_tree_retry,self.root)
        self.host=self.put('src/Host.rvt',compound(suffix='host'))
        self.link=self.put('src/Architecture.rvt',compound(suffix='link'))
        self.out=os.path.join(self.root,'out')
    def put(self,relative,data=b'data'):
        p=os.path.join(self.root,*relative.split('/'));f.ensure_directory(os.path.dirname(p))
        with open(p,'wb') as out:out.write(data)
        return p
    def run_package(self,mode='categories',refs=None,repath=True,backend=None):
        refs=refs if refs is not None else [dict(id='1',element_id='1',kind='RevitLink',source=self.link,td=True,loaded=True,special='native')]
        b=backend or Backend(self.host,refs,self.out)
        opts=dict(f.defaults(), simple_repath=False);opts.update(repath=repath,file_structure=mode)
        result=engine.transmit([self.host],self.out,b,opts)
        if result.get('recovery_directory'):self.addCleanup(f.remove_tree_retry,result['recovery_directory'])
        return result,b
    def test_linked_files_are_not_processed_or_reopened_even_with_repath_on(self):
        result,b=self.run_package()
        self.assertEqual(len([c for c in b.calls if c[0]=='finish']),1)
        self.assertEqual([c[1] for c in b.calls if c[0]=='finish'],[os.path.join(self.out,'Host.rvt')])
        self.assertFalse([c for c in b.calls if c[0] in ('verify','mark_transmitted')])
        host=next(r for r in result['files'] if r.get('is_primary_host'))
        self.assertEqual(host['model_verification'],'SAVED_REFERENCES_CHECKED')
        self.assertEqual(host['transmission_status'],'NOT_TRANSMITTED')
        self.assertEqual(host['opening_guidance'],'OPEN_NORMALLY_INDEPENDENT_PACKAGE')
        row=next(r for r in result['files'] if r['source']==self.link)
        self.assertEqual(f.digest(row['target']),f.digest(self.link))
        self.assertEqual(row['processing_status'],'UNCHANGED_DEPENDENCY')
        self.assertNotEqual(row['model_verification'],'OPENED_AND_REFERENCES_CHECKED')
    def test_native_rvt_acquisition_has_one_verified_copy(self):
        calls=[];original=f.copy_file
        def copy(*args,**kw):calls.append(args[:2]);return original(*args,**kw)
        f.copy_file=copy
        try:
            meta=model_payload.Store(os.path.join(self.root,'stage')).copy(self.link,os.path.join(self.root,'copied.rvt'))
        finally:f.copy_file=original
        self.assertEqual(len(calls),1)
        self.assertEqual(meta['acquisition_container'],'CFB')
        self.assertEqual(meta['sha256'],f.digest(self.link))
    def test_direct_dependencies_copy_once_to_final_layout_in_all_modes(self):
        for mode in ('categories','original','flat'):
            self.out=os.path.join(self.root,'out-'+mode);calls=[];original=f.copy_file
            def copy(*args,**kw):calls.append(args[:2]);return original(*args,**kw)
            f.copy_file=copy
            try:result,b=self.run_package(mode)
            finally:f.copy_file=original
            row=next(r for r in result['files'] if r['source']==self.link)
            self.assertEqual([t for s,t in calls if s==self.link],[row['target']])
            self.assertEqual(row.get('delivery_method'),'DIRECT_VERIFIED_COPY')
            self.assertTrue(all(not p.startswith('..') for p in b.paths))
    def test_same_name_sources_get_distinct_direct_destinations(self):
        other=self.put('other/Architecture.rvt',compound(suffix='different'))
        refs=[dict(id=str(i),element_id=str(i),kind='RevitLink',source=p,td=True,loaded=True,special='native') for i,p in enumerate((self.link,other))]
        result,b=self.run_package(refs=refs)
        rows=[r for r in result['files'] if not r.get('is_primary_host')]
        self.assertEqual(len(rows),2)
        self.assertNotEqual(rows[0]['target'],rows[1]['target'])
        self.assertTrue(all(f.digest(r['source'])==f.digest(r['target']) for r in rows))
    def test_performance_report_has_real_operation_rows_and_no_double_counted_phases(self):
        result,b=self.run_package()
        self.assertIn('performance',result)
        p=result['performance']
        self.assertGreaterEqual(p['elapsed_seconds'],0)
        self.assertTrue(p['monotonic'])
        self.assertTrue(p['operations'])
        self.assertTrue(all(r['seconds']>=0 and r['self_seconds']>=0 for r in p['operations']))
        self.assertLessEqual(sum(r['seconds'] for r in p['phases']),p['elapsed_seconds']+0.001)
        self.assertTrue(any(r['operation']=='copy' and r['bytes']>0 for r in p['operations']))
        self.assertTrue(any(r['operation']=='hash' for r in p['operations']))
        self.assertTrue(os.path.isfile(os.path.join(self.out,'timings.csv')))
        with io.open(os.path.join(self.out,'REPORT.txt'),encoding='utf-8') as inp:self.assertIn('PERFORMANCE SUMMARY',inp.read())
        with io.open(os.path.join(self.out,'manifest.json'),encoding='utf-8') as inp:self.assertEqual(json.load(inp)['performance'],p)
    def test_failed_file_is_timed_and_never_marked_delivered(self):
        original=f.copy_file
        def fail(source,target,*a,**kw):
            if source==self.link:raise IOError('test disk full')
            return original(source,target,*a,**kw)
        f.copy_file=fail
        try:result,b=self.run_package()
        finally:f.copy_file=original
        row=next(r for r in result['files'] if r['source']==self.link)
        self.assertNotEqual(row['status'],'COPIED')
        self.assertFalse([c for c in b.calls if c[0] in ('finish','verify','mark_transmitted')])
        host=next(r for r in result['files'] if r.get('is_primary_host'))
        self.assertEqual(host['status'],'NOT_FINALIZED')
        self.assertEqual(f.digest(host['recovery_path']),f.digest(self.host))
        self.assertFalse(f.within(host['recovery_path'],result['root']))
        self.assertFalse(f.file_exists(host['target']))
        self.assertIn('performance',result)
        self.assertTrue(any(r['status']=='FAILED' for r in result['performance']['operations']))
    def test_repath_off_finalizes_workshared_host_and_preserves_dependency_bytes(self):
        before=f.digest(self.host)
        result,b=self.run_package(repath=False)
        self.assertEqual([c[0] for c in b.calls if c[0]!='scan'],['finish'])
        host=next(r for r in result['files'] if r.get('is_primary_host'))
        self.assertEqual(host['model_verification'],'SAVED_REFERENCES_CHECKED')
        self.assertEqual(host['transmission_status'],'NOT_TRANSMITTED')
        self.assertNotEqual(f.digest(host['target']),before)
        self.assertEqual(f.digest(self.host),before)
        self.assertTrue(all(f.digest(r['source'])==f.digest(r['target']) for r in result['files'] if not r.get('is_primary_host')))
    def test_rcp_support_files_keep_structure_with_direct_delivery(self):
        rcp=self.put('src/Survey.rcp');scan=self.put('src/Survey Support/sub/A.rcs',b'scan')
        result,b=self.run_package(refs=[dict(id='pc',source=rcp,kind='PointCloud')],repath=False)
        rows=dict((r['source'],r) for r in result['files'])
        self.assertEqual(os.path.relpath(rows[scan]['target'],os.path.dirname(rows[rcp]['target'])).replace('\\','/'),'Survey Support/sub/A.rcs')
        self.assertEqual(rows[scan].get('delivery_method'),'DIRECT_VERIFIED_COPY')
    def test_batch_preserves_package_profile_and_records_zip_separately(self):
        from easybim_etransmit import batch
        options=dict(f.defaults(), simple_repath=False);options.update(repath=False,zip_per_model=True)
        results=batch.run_batch([self.host],self.out,lambda out,source:Backend(source,[],out),options)
        result=results[0]
        self.assertTrue(result['performance'].get('finalized'))
        with io.open(os.path.join(self.out,'batch.json'),encoding='utf-8') as inp:data=json.load(inp)
        self.assertTrue(any(r['operation']=='zip_archive' for r in data['performance']['operations']))
        self.assertFalse(any(r['operation']=='zip_archive' for r in result['performance']['operations']))
        self.assertTrue(os.path.isfile(os.path.join(self.out,'batch_timings.csv')))
        with io.open(os.path.join(result['root'],'manifest.json'),encoding='utf-8') as inp:
            self.assertEqual(json.load(inp)['performance'],result['performance'])
    def test_api_host_fallback_retains_short_layout_and_unchanged_links(self):
        refs=[dict(id='1',source=self.link,kind='RevitLink',td=True,loaded=True,special='native')]
        b=Backend(self.host,refs,self.out)
        b.can_deliver_direct=lambda *args:False
        result,b=self.run_package(backend=b)
        self.assertEqual(result['delivery_strategy'],'SHORT_PATH_PREPARATION')
        row=next(r for r in result['files'] if r['source']==self.link)
        self.assertEqual(row['sha256'],row['packaged_sha256'])
        self.assertEqual([c[1] for c in b.calls if c[0]=='finish'],[os.path.join(self.out,'Host.rvt')])
        self.assertFalse([c for c in b.calls if c[0] in ('verify','mark_transmitted')])
    def test_instrumentation_names_are_reserved_in_flat_layout(self):
        rows=[dict(source='/x/timings.csv',category='other',original_relative='timings.csv')]
        layout.plan(rows,'flat')
        self.assertNotEqual(rows[0]['relative'],'timings.csv')

if __name__=='__main__':unittest.main(verbosity=2)
