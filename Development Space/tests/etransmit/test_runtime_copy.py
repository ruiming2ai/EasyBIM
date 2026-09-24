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
        self.refs = refs
    def scan(self, source, stage, options):
        return dict(references=self.refs.get(source, []), issues=[], version='2024')
    def finish(self, stage, target, edges, options):
        return []


class RuntimeCopyTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ET_Runtime_')
        self.addCleanup(shutil.rmtree, self.root)

    def make_file(self, relative, payload):
        path = os.path.join(self.root, relative)
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, 'wb') as out:
            out.write(payload)
        return path

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
        with open(target, 'rb') as inp:
            self.assertEqual(inp.read(), b'abc')

    def test_local_host_discovers_and_copies_duplicate_named_pdfs(self):
        host = self.make_file('project/Host.rvt', b'abc')
        pdf_a = self.make_file('project/Arch/Details.pdf', b'%PDF-1.4\nA')
        pdf_b = self.make_file('project/Elec/Details.pdf', b'%PDF-1.4\nB')
        backend = InventoryBackend({host: [{'source': pdf_a, 'id': '1'}, {'source': pdf_b, 'id': '2'}]})
        result = e.transmit([host], os.path.join(self.root, 'package'), backend)
        self.assertEqual(result['status'], 'COLLECTED', repr(result['issues']))
        self.assertEqual(e.package_counts(result)['hosts_copied'], 1)
        self.assertEqual(e.package_counts(result)['files_copied'], 3)
        pdfs = [r for r in result['files'] if r['category'] == 'pdf']
        self.assertEqual(len(pdfs), 2)
        self.assertTrue(all(os.path.basename(r['target']) == 'Details.pdf' for r in pdfs))
        self.assertNotEqual(pdfs[0]['target'], pdfs[1]['target'])


if __name__ == '__main__':
    print('RUNTIME:', sys.version)
    unittest.main(verbosity=2)
