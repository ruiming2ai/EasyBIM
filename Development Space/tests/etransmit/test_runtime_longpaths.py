# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function
import os, sys, tempfile, shutil, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f
try: from easybim_etransmit import longpaths as lp
except ImportError: lp=None

class LongPaths(unittest.TestCase):
    def test_collection_accepts_long_pdf_without_relaxing_model_api_limit(self):
        path='C:\\ET\\'+'Folder\\'*35+'Original Details.pdf'
        self.assertGreater(len(path),260)
        self.assertTrue(callable(getattr(f,'validate_copy_path',None)))
        f.validate_copy_path(path)
        with self.assertRaises(ValueError): f.validate_destination_path(path[:-4]+'.rvt')
    def test_unc_extended_path_keeps_name_and_share(self):
        self.assertIsNotNone(lp)
        self.assertEqual(lp.extended(r'\\server\share\Project\Same.pdf'),r'\\?\UNC\server\share\Project\Same.pdf')
    def test_native_paths_reject_relative_and_devices(self):
        self.assertIsNotNone(lp)
        for path in ('relative.pdf',r'\\.\GLOBALROOT\bad',r'C:foo'):
            with self.assertRaises(ValueError):lp.extended(path)
    @unittest.skipUnless(os.name=='nt','Windows Win32 API integration')
    def test_real_ironpython_copy_and_checksum_past_260_characters(self):
        self.assertIsNotNone(lp)
        root=tempfile.mkdtemp(prefix='ET_Long_')
        short=os.path.join(root,'source.pdf')
        with open(short,'wb') as out:out.write(b'%PDF-'+b'payload'*200000)
        directory=os.path.join(root,'nested'*12,'references'*8,'archive'*9)
        target=os.path.join(directory,'Original Details.pdf')
        self.assertGreater(len(target),260)
        try:
            f.destination(root,os.path.relpath(target,root))
            metadata=f.copy_file(short,target)
            self.assertEqual(f.digest(short),metadata['sha256'])
            self.assertEqual(f.digest(target),metadata['sha256'])
            self.assertTrue(f.file_exists(target))
            with self.assertRaises(IOError):f.copy_file(short,target)
        finally:
            lp.remove_tree(root)
    def test_component_over_255_remains_invalid(self):
        self.assertTrue(callable(getattr(f,'validate_copy_path',None)))
        with self.assertRaises(ValueError):f.validate_copy_path('C:\\ET\\'+'x'*256+'.pdf')

if __name__=='__main__':
    print(sys.version)
    unittest.main(verbosity=2)
