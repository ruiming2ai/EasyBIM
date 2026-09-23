"""Desktop Connector shell and local staging regressions."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'lib'))

from easybim_etransmit import files as f, engine as e
from easybim_etransmit.revit import Backend as RevitBackend
from test_engine import Backend as FakeBackend


class DesktopConnectorRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_desktop_connector_path_is_identified_for_shell_access(self):
        helper = getattr(f, 'is_desktop_connector_path', None)
        self.assertTrue(callable(helper), 'Desktop Connector detection helper is missing')
        self.assertTrue(helper(r'C:\Users\tester\DC\ACCDocs\Account\Project\Project Files\Host.rvt'))
        self.assertTrue(helper(r'D:\Custom Workspace\ACCDocs\Account\Project\Host.rvt'))
        self.assertFalse(helper(r'C:\Projects\Local\Host.rvt'))

    def test_copy_file_routes_connector_source_through_shell_snapshot(self):
        helper = getattr(f, 'copy_file', None)
        shell_helper = getattr(f, 'shell_copy_to_local', None)
        self.assertTrue(callable(shell_helper), 'Windows Shell materializer is missing')
        source = r'C:\Users\tester\DC\ACCDocs\Account\Project\Project Files\Host.rvt'
        hydrated_dir = self.base / 'hydrated'
        hydrated_dir.mkdir()
        hydrated = hydrated_dir / 'Host.rvt'
        hydrated.write_bytes(b'valid downloaded revit bytes')
        target = self.base / 'Host.rvt'
        calls = []

        def fake_shell(src, cancelled=None):
            calls.append(src)
            return str(hydrated), None

        with patch.object(f, 'is_desktop_connector_path', return_value=True), \
             patch.object(f, 'shell_copy_to_local', fake_shell), \
             patch.object(f.os, 'name', 'nt'):
            result = helper(source, str(target))

        self.assertEqual(calls, [source])
        self.assertEqual(target.read_bytes(), hydrated.read_bytes())
        self.assertEqual(result.get('copy_method'), 'WINDOWS_SHELL')
        self.assertEqual(result.get('source_stability'), 'SHELL_SNAPSHOT')

    def test_shell_snapshot_does_not_restat_connector_after_copy(self):
        helper = getattr(f, 'source_snapshot_changed', None)
        self.assertTrue(callable(helper), 'source snapshot stability helper is missing')
        with patch.object(f, 'signature', side_effect=OSError('Desktop Connector direct stat is unsupported')) as stat:
            changed = helper(r'C:\Users\tester\DC\ACCDocs\A\Host.rvt',
                             {'copy_method': 'WINDOWS_SHELL',
                              'source_stability': 'SHELL_SNAPSHOT',
                              'size': 10, 'source_mtime': 1})
        self.assertFalse(changed)
        stat.assert_not_called()

    def test_engine_staging_is_outside_output_tree(self):
        source = self.base / 'Host.rvt'
        source.write_bytes(b'fake rvt')
        out = self.base / 'OneDrive Output' / '01'
        backend = FakeBackend()
        seen = []

        def set_staging_root(path):
            seen.append(path)
        backend.set_staging_root = set_staging_root

        result = e.transmit([str(source)], str(out), backend)
        self.assertIn(result['status'], ('COLLECTED', 'NEEDS_REVIEW'))
        self.assertEqual(len(seen), 1)
        self.assertFalse(f.within(seen[0], str(out)), seen[0])
        self.assertFalse((out / '_work').exists())
        self.assertFalse(os.path.exists(seen[0]), 'Local staging directory must be cleaned')

    def test_saved_reference_metadata_is_read_from_local_stage_not_connector(self):
        source = r'C:\Users\tester\DC\ACCDocs\Account\Project\Project Files\Host.rvt'
        stage = self.base / 'stage.rvt'
        stage.write_bytes(b'local snapshot')

        backend = object.__new__(RevitBackend)
        backend.root = str(self.base)
        backend.staging_root = str(self.base)
        backend.cancelled = None
        backend.DB = object()
        backend.app = object()
        backend.basic = lambda path: {'version': '2026', 'workshared': False, 'central': ''}
        calls = []
        backend.rows = lambda path, owner='': calls.append((path, owner)) or []

        result = backend.scan(source, str(stage), {'deep': False})
        self.assertEqual(result['version'], '2026')
        self.assertEqual(calls, [(str(stage), source)])

    def test_backend_guard_allows_only_package_or_registered_staging(self):
        helper = getattr(RevitBackend, 'set_staging_root', None)
        self.assertTrue(callable(helper), 'Backend staging-root registration is missing')
        package = self.base / 'package'
        staging = self.base / 'local_staging'
        outside = self.base / 'outside'
        package.mkdir(); staging.mkdir(); outside.mkdir()
        backend = object.__new__(RevitBackend)
        backend.root = str(package)
        backend.staging_root = None
        backend.set_staging_root(str(staging))
        backend.guard(str(staging / 'stage.rvt'))
        backend.guard(str(package / 'Host.rvt'))
        with self.assertRaises(ValueError):
            backend.guard(str(outside / 'bad.rvt'))


if __name__ == '__main__':
    unittest.main()
