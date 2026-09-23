import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'lib'))
from easybim_etransmit import files as f
try:
    e = importlib.import_module('easybim_etransmit.engine')
except ImportError:
    e = None

class Backend:
    def __init__(self, refs=None, finish_fail=False):
        self.refs = refs or {}; self.scanned=[]; self.finished=[]; self.finish_fail=finish_fail
    def scan(self, source, stage, options):
        assert source != stage
        self.scanned.append(source)
        return {'references': self.refs.get(source, []), 'issues': [], 'version': '2026'}
    def finish(self, stage, target, edges, options):
        self.finished.append((stage, target, edges))
        if self.finish_fail: raise RuntimeError('save failed')
        return []

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(e, 'package engine is missing')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name); self.src=self.base/'src'; self.src.mkdir()
        self.out=self.base/'out'; self.opts=f.defaults(); self.opts['per_model']=False
    def file(self, name, content=b'example'):
        p=self.src/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(content); return str(p)
    def test_nested_files_and_dedupe(self):
        root=self.file('host.rvt'); child=self.file('model/link.rvt'); pdf=self.file('refs/a.pdf')
        b=Backend({root:[{'source':child, 'id':'1'}, {'source':child,'id':'2'}], child:[{'source':pdf,'id':'3'}]})
        r=e.transmit([root],str(self.out),b,self.opts)
        self.assertEqual(len(r['files']),3)
        self.assertEqual(b.scanned,[root,child])
        self.assertEqual(Path(root).read_bytes(),b'example')
        self.assertEqual(len(r['references']),3)
    def test_cycle_terminates(self):
        a=self.file('a.rvt'); b=self.file('b.rvt')
        r=e.transmit([a],str(self.out),Backend({a:[{'source':b}],b:[{'source':a}]}),self.opts)
        self.assertEqual(len(r['files']),2)
    def test_missing_reference_reported(self):
        a=self.file('a.rvt')
        r=e.transmit([a],str(self.out),Backend({a:[{'source':str(self.src/'gone.pdf')}]}),self.opts)
        self.assertEqual(r['status'],'NEEDS_REVIEW')
        self.assertTrue(any('gone.pdf' in i['source'] for i in r['issues']))
    def test_cloud_no_latest_substitution(self):
        a=self.file('a.rvt'); self.file('live/a.rvt')
        r=e.transmit([a],str(self.out),Backend({a:[{'source':'BIM 360://P/Consumed/a.rvt'}]}),self.opts)
        self.assertEqual(len(r['files']),1)
        self.assertTrue(any(i['code']=='UNRESOLVED_SOURCE' for i in r['issues']))
    def test_source_filenames_unchanged(self):
        a=self.file('a.rvt'); x=self.file('A/Details.pdf'); y=self.file('B/Details.pdf')
        r=e.transmit([a],str(self.out),Backend({a:[{'source':x},{'source':y}]}),self.opts)
        paths=[p['relative'] for p in r['files'] if p['source'].endswith('.pdf')]
        self.assertEqual(len(paths),2)
        self.assertTrue(all(p.endswith('/Details.pdf') for p in paths))
    def test_rcp_support_folder_included(self):
        a=self.file('a.rvt'); rcp=self.file('Cloud/Survey.rcp'); scan=self.file('Cloud/Survey Support/scan.rcs')
        r=e.transmit([a],str(self.out),Backend({a:[{'source':rcp}]}),self.opts)
        self.assertTrue(any(x['source']==scan for x in r['files']))
        self.assertTrue(any(x['code']=='RCP_EXTERNAL_SCANS_UNVERIFIED' for x in r['issues']))
    def test_category_exclusion(self):
        a=self.file('a.rvt'); pdf=self.file('a.pdf'); self.opts['include']['pdf']=False
        r=e.transmit([a],str(self.out),Backend({a:[{'source':pdf}]}),self.opts)
        self.assertEqual(len(r['files']),1)
        self.assertEqual(r['references'][0]['status'],'EXCLUDED')
    def test_finish_failure_retains_unmodified_copy(self):
        a=self.file('a.rvt')
        r=e.transmit([a],str(self.out),Backend(finish_fail=True),self.opts)
        self.assertEqual(r['status'],'NEEDS_REVIEW')
        self.assertEqual(Path(r['files'][0]['target']).read_bytes(),b'example')
    def test_report_and_manifest_written(self):
        a=self.file('a.rvt'); r=e.transmit([a],str(self.out),Backend(),self.opts)
        self.assertEqual(json.loads((self.out/'manifest.json').read_text())['status'],r['status'])
        self.assertTrue((self.out/'START_HERE.txt').exists())
        self.assertTrue((self.out/'references.csv').exists())
        self.assertFalse((self.out/'_work').exists())
    def test_existing_package_not_overwritten(self):
        a=self.file('a.rvt'); self.out.mkdir(); (self.out/'a.txt').write_text('x')
        with self.assertRaises(ValueError): e.transmit([a],str(self.out),Backend(),self.opts)
    def test_never_collect_output_as_extra(self):
        a=self.file('a.rvt'); self.out.mkdir(); inside=self.out/'x.pdf'; inside.write_bytes(b'x')
        with self.assertRaises(ValueError): e.transmit([a],str(self.out),Backend(),self.opts,extras=[str(inside)])
    def test_cancel_writes_partial_report(self):
        a=self.file('a.rvt')
        r=e.transmit([a],str(self.out),Backend(),self.opts,cancelled=lambda:True)
        self.assertEqual(r['status'],'CANCELLED')
        self.assertTrue((self.out/'manifest.json').exists())
    def test_nwf_dependency_not_claimed_complete(self):
        a=self.file('a.rvt'); n=self.file('refs/x.nwf')
        r=e.transmit([a],str(self.out),Backend({a:[{'source':n}]}),self.opts)
        self.assertTrue(any(x['code']=='NWF_DEPENDENCIES_UNVERIFIED' for x in r['issues']))
    def test_no_source_files_mutated(self):
        a=self.file('a.rvt'); pdf=self.file('a.pdf')
        before={p:f.digest(p) for p in [a,pdf]}
        e.transmit([a],str(self.out),Backend({a:[{'source':pdf}]}),self.opts)
        self.assertEqual(before,{p:f.digest(p) for p in [a,pdf]})

if __name__ == '__main__': unittest.main()
