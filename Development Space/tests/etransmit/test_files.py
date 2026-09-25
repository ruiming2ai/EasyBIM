import importlib
import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'lib'))
try:
    c = importlib.import_module('easybim_etransmit.files')
except ImportError:
    c = None

class FileTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(c, 'e-transmit filesystem implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_duplicate_names_keep_distinct_parents(self):
        a = c.mirror_path(r'D:\Project\Arch\Details.pdf')
        b = c.mirror_path(r'D:\Project\Elec\Details.pdf')
        self.assertNotEqual(a, b)
        self.assertTrue(a.endswith('/Project/Arch/Details.pdf'))
        self.assertEqual(a.split('/')[-1], 'Details.pdf')

    def test_drive_roots_distinct(self):
        self.assertNotEqual(c.mirror_path(r'C:\P\a.pdf'), c.mirror_path(r'D:\P\a.pdf'))

    def test_network_root_preserved(self):
        self.assertEqual(c.mirror_path(r'\\server\share\P\a.dwg'), 'Sources/Network/server/share/P/a.dwg')

    def test_desktop_connector_hierarchy(self):
        self.assertEqual(c.mirror_path(r'C:\Users\User\DC\ACCDocs\Hub\Proj\Project Files\Shared\a.rvt'),
                         'Sources/ACC/Hub/Proj/Project Files/Shared/a.rvt')

    def test_consumed_not_changed(self):
        self.assertIn('/Consumed/', c.mirror_path(r'C:\Users\x\DC\ACCDocs\H\P\Project Files\Consumed\A.rvt'))

    def test_device_paths_rejected(self):
        for path in (r'\\.\PhysicalDrive0', 'C:relative.rvt', 'https://x/a.rvt'):
            with self.assertRaises(ValueError): c.mirror_path(path)

    def test_exact_mapping_only(self):
        folder = self.root / 'mapped'; folder.mkdir()
        f = folder / 'Consumed' / 'a.pdf'; f.parent.mkdir(); f.write_bytes(b'x')
        r = c.resolve_source('Autodesk Docs://Hub/P/Project Files/Consumed/a.pdf',
                             mappings=[('Autodesk Docs://Hub/P/Project Files', str(folder))])
        self.assertEqual(r, str(f))
        self.assertIsNone(c.resolve_source('Autodesk Docs://Hub/P2/Project Files/Consumed/a.pdf',
                                          mappings=[('Autodesk Docs://Hub/P', str(folder))]))

    def test_mapping_boundary_and_traversal(self):
        with self.assertRaises(ValueError):
            c.resolve_source('ACC://P/../secret.rvt', mappings=[('ACC://P', str(self.root))])

    def test_no_basename_search(self):
        (self.root / 'x.rvt').write_bytes(b'x')
        self.assertIsNone(c.resolve_source('BIM 360://Different/P/x.rvt'))

    def test_relative_requires_owner(self):
        self.assertIsNone(c.resolve_source('../ref.pdf'))
        self.assertEqual(c.resolve_source('../ref.pdf', str(self.root/'Models/a.rvt')), str(self.root/'ref.pdf'))

    def test_file_copy_verified(self):
        a = self.root/'a.pdf'; b = self.root/'out'/'a.pdf'
        a.write_bytes(b'original'*100)
        result = c.copy_file(str(a), str(b))
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(result['sha256'], c.digest(str(a)))

    def test_no_overwrite(self):
        a = self.root/'a'; b = self.root/'b'; a.write_bytes(b'a'); b.write_bytes(b'b')
        with self.assertRaises(IOError): c.copy_file(str(a), str(b))
        self.assertEqual(b.read_bytes(), b'b')

    def test_cancel_no_partial(self):
        a = self.root/'a'; a.write_bytes(b'a'*10000); b = self.root/'b'
        with self.assertRaises(c.Cancelled): c.copy_file(str(a), str(b), cancelled=lambda: True)
        self.assertFalse(b.exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['a'])

    def test_no_copy_same_source(self):
        a = self.root/'a'; a.write_bytes(b'a')
        with self.assertRaises(IOError): c.copy_file(str(a), str(a))

    def test_destination_containment(self):
        with self.assertRaises(ValueError): c.destination(str(self.root), '../escape.pdf')

    def test_csv_formula_escaped(self):
        self.assertTrue(next(csv.reader([c.csv_cell('=1+1')]))[0].startswith("'"))
        self.assertTrue(next(csv.reader([c.csv_cell('\t=1+1')]))[0].startswith("'"))

    def test_all_categories_default_on(self):
        self.assertTrue(all(c.defaults()['include'].values()))
        self.assertFalse(c.defaults()['cleanup'])
        self.assertFalse(c.defaults()['upgrade'])

    def test_classification(self):
        for ext, group in [('pdf','pdf'),('nwd','navisworks'),('rcp','pointcloud'),('rvt','revit'),('ifc','ifc')]:
            self.assertEqual(c.category('x.'+ext), group)

    def test_file_change_detected(self):
        a = self.root/'a'; a.write_bytes(b'a'*100)
        changed = []
        def pulse(*args):
            if not changed:
                changed.append(True); a.write_bytes(b'b'*200)
        with self.assertRaises(IOError): c.copy_file(str(a), str(self.root/'b'), pulse=pulse)
        self.assertFalse((self.root/'b').exists())

    def test_symlink_destination_refused(self):
        outside = self.root/'outside'; outside.mkdir()
        out = self.root/'out'; out.mkdir()
        try:
            (out/'linked').symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows account lacks the symlink creation privilege')
            raise
        with self.assertRaises(ValueError): c.destination(str(out), 'linked/evil.txt')

if __name__ == '__main__': unittest.main()
