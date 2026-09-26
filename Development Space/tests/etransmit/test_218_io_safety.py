# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import errno, os, sys, tempfile, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..",".."))
sys.path.insert(0,os.path.join(ROOT,"lib"))
from easybim_etransmit import files as f, performance as p, model_payload
from test_payload_acquisition import compound

class IOSafety(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET218_IO_');self.addCleanup(f.remove_tree_retry,self.root)
        self.src=os.path.join(self.root,'original.bin')
        with open(self.src,'wb') as out:out.write(b'abcdef')
        self.work=os.path.join(self.root,'work');os.makedirs(self.work)
        self.staged=os.path.join(self.work,'copy.bin');self.meta=f.copy_file(self.src,self.staged)
        self.final=os.path.join(self.root,'delivered','copy.bin')
    def test_timing_reports_use_safe_report_publication(self):
        for name in ('timings.csv','batch_timings.csv'):
            source=os.path.join(self.work,name)
            target=os.path.join(self.root,name)
            with open(source,'wb') as out:out.write(b'phase,seconds\ncopy,1.0\n')
            f.publish_report(source,target)
            f.publish_report(source,target)
            self.assertEqual(f.digest(source),f.digest(target))
    def test_never_moves_original_sources(self):
        with self.assertRaises(ValueError):f.relocate_verified(self.src,self.final,self.meta,self.work)
        self.assertTrue(os.path.isfile(self.src))
    def test_same_volume_relocation_does_not_reread_verified_bytes(self):
        old=f.digest
        def fail(*a):self.fail('verified same-volume move must not hash again')
        f.digest=fail
        try:r=f.relocate_verified(self.staged,self.final,self.meta,self.work)
        finally:f.digest=old
        self.assertEqual(r['delivery_method'],'MOVE_VERIFIED_BYTES')
        self.assertFalse(os.path.exists(self.staged));self.assertEqual(f.digest(self.src),f.digest(self.final))
    def test_cross_volume_fallback_copies_and_verifies_without_destroying_source(self):
        old=f.publish
        def publish(source,target):
            if source==self.staged:raise OSError(errno.EXDEV,'different volume')
            return old(source,target)
        f.publish=publish
        try:r=f.relocate_verified(self.staged,self.final,self.meta,self.work)
        finally:f.publish=old
        self.assertEqual(r['delivery_method'],'VERIFIED_COPY_FALLBACK')
        self.assertEqual(f.digest(self.final),self.meta['sha256']);self.assertTrue(os.path.isfile(self.src))
    def test_tampered_staged_file_cannot_inherit_checksum(self):
        with open(self.staged,'ab') as out:out.write(b'tamper')
        with self.assertRaises(IOError):f.relocate_verified(self.staged,self.final,self.meta,self.work)
        self.assertFalse(os.path.exists(self.final))
    def test_existing_destination_is_never_replaced(self):
        f.ensure_directory(os.path.dirname(self.final))
        with open(self.final,'wb') as out:out.write(b'keep')
        with self.assertRaises(IOError):f.relocate_verified(self.staged,self.final,self.meta,self.work)
        with open(self.final,'rb') as inp:self.assertEqual(inp.read(),b'keep')
    def test_cancelled_move_keeps_scratch_file(self):
        with self.assertRaises(f.Cancelled):f.relocate_verified(self.staged,self.final,self.meta,self.work,lambda:True)
        self.assertTrue(os.path.isfile(self.staged));self.assertFalse(os.path.exists(self.final))
    def test_nested_timing_excludes_child_time_from_parent_phase(self):
        tick=[0.0]
        with p.Collector(clock=lambda:tick[0]) as timer:
            with p.span('parent','outer'):
                tick[0]=1.0
                with p.span('child','inner'):tick[0]=4.0
                tick[0]=5.0
        snap=timer.snapshot();phases=dict((r['phase'],r['seconds']) for r in snap['phases'])
        self.assertEqual(phases,dict(parent=2.0,child=3.0));self.assertEqual(snap['elapsed_seconds'],5.0)
        self.assertIsNone(p.current())
    def test_cancellation_is_recorded_and_does_not_leak_timer(self):
        with p.Collector() as timer:
            with self.assertRaises(f.Cancelled):
                with p.span('copy','cancel'):raise f.Cancelled()
        self.assertEqual(timer.snapshot()['operations'][0]['status'],'CANCELLED');self.assertIsNone(p.current())
    def test_windows_long_source_keeps_short_acquisition_fallback(self):
        src=os.path.join(self.root,'source.rvt');target=os.path.join(self.root,'out.rvt')
        with open(src,'wb') as out:out.write(compound())
        real_os=model_payload.os;real_units=f.path_units;real_probe=model_payload.probe
        class WindowsOS(object):
            name='nt'
            def __getattr__(self,name):return getattr(real_os,name)
        def probe(path):
            if path==src:raise IOError('IronPython cannot directly open a long source path')
            return real_probe(path)
        model_payload.os=WindowsOS()
        f.path_units=lambda path:300 if path==src else real_units(path)
        model_payload.probe=probe
        try:
            meta=model_payload.Store(self.work).copy(src,target)
        finally:
            model_payload.os=real_os;f.path_units=real_units;model_payload.probe=real_probe
        self.assertEqual(meta['sha256'],f.digest(src))
        self.assertEqual(f.digest(src),f.digest(target))
    def test_changing_native_source_during_probe_cannot_publish_mismatched_snapshot(self):
        src=os.path.join(self.root,'source.rvt');target=os.path.join(self.root,'out.rvt')
        with open(src,'wb') as out:out.write(compound())
        old=model_payload.probe
        def probe(path):
            value=old(path)
            if path==src:
                with open(src,'ab') as out:out.write(b'changed')
            return value
        model_payload.probe=probe
        try:
            with self.assertRaises(IOError):model_payload.Store(self.work).copy(src,target)
        finally:model_payload.probe=old
        self.assertFalse(os.path.exists(target));self.assertTrue(os.path.exists(src))

if __name__=='__main__':unittest.main()
