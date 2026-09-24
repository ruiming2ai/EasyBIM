# -*- coding: utf-8 -*-
"""Run unchanged on CPython and IronPython 2.7; no mocked file IO or hashing."""
from __future__ import print_function
import io
import json
import os
import shutil
import sys
import tempfile
import traceback
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'lib'))
from easybim_etransmit import files as f, engine as e


class InventoryBackend(object):
    """Only the Revit API is replaced; acquisition/verification/reporting are real."""
    def __init__(self, refs):
        self.refs = dict((f.canonical(k), v) for k, v in refs.items())
    def scan(self, source, stage, options):
        return dict(references=self.refs.get(f.canonical(source), []), issues=[], version='2024')
    def finish(self, stage, target, edges, options):
        return []


class RuntimeCopyTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ET_Runtime_')
        self.addCleanup(shutil.rmtree, self.root)

    def make_file(self, relative, payload):
        path = os.path.normpath(os.path.join(self.root, relative))
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, 'wb') as out:
            out.write(payload)
        return path

    def read_bytes(self, path):
        with open(path, 'rb') as inp:
            return inp.read()

    def test_verified_local_copy_returns_metadata_and_valid_bytes(self):
        source = self.make_file('Original (1).rvt', b'abc')
        target = os.path.join(self.root, 'output', 'Original (1).rvt')
        try:
            result = f.copy_file(source, target)
        except Exception as exc:
            traceback.print_exc()
            if hasattr(exc, 'ToString'):
                print(exc.ToString())
            self.fail('Real runtime local copy failed: ' + str(exc))
        self.assertEqual(result['sha256'], 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
        self.assertEqual(result['size'], 3)
        self.assertEqual(self.read_bytes(target), b'abc')
        self.assertEqual(self.read_bytes(source), b'abc')

    def test_local_host_discovers_and_copies_duplicate_named_pdfs(self):
        host = self.make_file('project/Host.rvt', b'abc')
        pdf_a = self.make_file('project/Arch/Details.pdf', b'%PDF-1.4\nA')
        pdf_b = self.make_file('project/Elec/Details.pdf', b'%PDF-1.4\nB')
        backend = InventoryBackend({host: [{'source': pdf_a, 'id': '1'}, {'source': pdf_b, 'id': '2'}]})
        package = os.path.join(self.root, 'package')
        result = e.transmit([host], package, backend)
        self.assertEqual(result['status'], 'COLLECTED', repr(result['issues']))
        self.assertEqual(e.package_counts(result)['hosts_copied'], 1)
        self.assertEqual(e.package_counts(result)['files_copied'], 3)
        pdfs = [r for r in result['files'] if r['category'] == 'pdf']
        self.assertEqual(len(pdfs), 2)
        self.assertTrue(all(os.path.basename(r['target']) == 'Details.pdf' for r in pdfs))
        self.assertNotEqual(pdfs[0]['target'], pdfs[1]['target'])
        for rec in result['files']:
            self.assertEqual(self.read_bytes(rec['source']), self.read_bytes(rec['target']))
            self.assertEqual(rec['sha256'], rec['packaged_sha256'])
        with io.open(os.path.join(package, 'manifest.json'), encoding='utf-8') as inp:
            saved = json.load(inp)
        self.assertEqual(saved['status'], 'COLLECTED')
        self.assertEqual(len(saved['files']), 3)

    def test_empty_file_copy_keeps_verified_empty_digest(self):
        source = self.make_file('Empty.txt', b'')
        target = os.path.join(self.root, 'out', 'Empty.txt')
        result = f.copy_file(source, target)
        self.assertEqual(result['size'], 0)
        self.assertEqual(result['sha256'], 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
        self.assertEqual(self.read_bytes(target), b'')

    def test_multichunk_binary_copy_preserves_every_byte(self):
        payload = b'\x00\xff\r\nPDF-RVT-DWG\x80' * 180000
        source = self.make_file('Binary.rvt', payload)
        target = os.path.join(self.root, 'out', 'Binary.rvt')
        result = f.copy_file(source, target)
        self.assertEqual(result['size'], 2880000)
        self.assertEqual(result['sha256'], '39e2896d112632ba6b0e0430eec8b44806da234cfb0fa0915d5454ed6762524b')
        self.assertEqual(self.read_bytes(target), payload)
        self.assertEqual(self.read_bytes(source), payload)

    def test_copy_never_overwrites_existing_destination(self):
        source = self.make_file('Host.rvt', b'new data')
        target = self.make_file('out/Host.rvt', b'existing data')
        with self.assertRaises(IOError):
            f.copy_file(source, target)
        self.assertEqual(self.read_bytes(target), b'existing data')
        self.assertEqual(self.read_bytes(source), b'new data')

    def test_cancelled_copy_removes_partial_and_leaves_source_unchanged(self):
        payload = b'A' * (2 * 1024 * 1024)
        source = self.make_file('Host.rvt', payload)
        folder = os.path.join(self.root, 'out')
        target = os.path.join(folder, 'Host.rvt')
        stop = [False]
        def pulse(label, copied, total):
            if copied:
                stop[0] = True
        with self.assertRaises(f.Cancelled):
            f.copy_file(source, target, lambda: stop[0], pulse)
        self.assertFalse(os.path.exists(target))
        self.assertEqual(os.listdir(folder), [])
        self.assertEqual(self.read_bytes(source), payload)


if __name__ == '__main__':
    print('RUNTIME:', sys.version)
    unittest.main(verbosity=2)
