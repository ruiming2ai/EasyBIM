# -*- coding: utf-8 -*-
"""Regressions for zero-step source discovery and remaining 2.0.6 warnings."""
from __future__ import print_function
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'lib'))

from easybim_etransmit import files as f, engine as e


class Obj(object):
    def __init__(self, **values):
        self.__dict__.update(values)


def tracker_module(testcase):
    try:
        from easybim_etransmit import source_tracker
        return source_tracker
    except ImportError:
        testcase.fail('easybim_etransmit.source_tracker is required for zero-step detached/cloud source discovery')


class SourceAutomationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ET_AutoSource_')
        self.addCleanup(shutil.rmtree, self.root)
        self.state = os.path.join(self.root, 'open_sources.json')

    def make(self, relative, payload=b'x'):
        path = os.path.normpath(os.path.join(self.root, relative))
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, 'wb') as out:
            out.write(payload)
        return path

    def test_detached_open_reuses_exact_document_opening_path(self):
        tracker = tracker_module(self)
        source = self.make('Downloads/Host.rvt', b'rvt')
        tracker.record_opening(source, state_path=self.state, now=100.0)
        doc = Obj(Title='Host_detached', PathName='', IsDetached=True,
                  IsModelInCloud=False, IsWorkshared=True)
        tracker.record_opened(doc, state_path=self.state, now=101.0)
        self.assertEqual(tracker.source_for_document(doc, state_path=self.state), source)

    def test_journal_fallback_recovers_local_file_for_already_open_detached_model(self):
        tracker = tracker_module(self)
        source = os.path.join(self.root, 'Downloads', 'Host.rvt')
        journal = os.path.join(self.root, 'journal.0001.txt')
        with io.open(journal, 'w', encoding='utf-8') as out:
            out.write(u'Jrn.Command "Internal" , "Open an existing project , ID_REVIT_FILE_OPEN"\n')
            out.write(u'Jrn.Data "File Name"  _\n')
            out.write(u'         , "IDOK", "' + source.replace('\\', '\\\\') + u'"\n')
        doc = Obj(Title='Host_detached', PathName='', IsDetached=True,
                  IsModelInCloud=False, IsWorkshared=True)
        app = Obj(RecordingJournalFilename=journal)
        self.assertEqual(tracker.source_for_document(doc, application=app, state_path=self.state), source)

    def test_default_connector_roots_include_custom_workspace(self):
        helper = getattr(f, '_desktop_connector_workspace_locations', None)
        self.assertTrue(callable(helper), 'Desktop Connector custom workspace discovery is missing')
        old = f._desktop_connector_workspace_locations
        custom = os.path.join(self.root, 'CustomDC')
        f._desktop_connector_workspace_locations = lambda: [custom]
        try:
            roots = f.default_connector_roots()
        finally:
            f._desktop_connector_workspace_locations = old
        self.assertIn(os.path.join(custom, 'ACCDocs'), roots)

    def test_connector_uri_uses_exact_project_hierarchy_without_manual_prefix_mapping(self):
        resolver = getattr(f, 'resolve_connector_uri', None)
        self.assertTrue(callable(resolver), 'automatic Desktop Connector URI resolver is missing')
        root = os.path.join(self.root, 'ACCDocs')
        wanted = self.make(os.path.join('ACCDocs', 'Account A', 'Project X', 'Project Files',
                                        'Shared', 'Architecture', 'A.rvt'), b'rvt')
        uri = 'Autodesk Docs://Project X/Project Files/Shared/Architecture/A.rvt'
        old_names, old_exists = f._shell_folder_names, f._shell_file_exists
        f._shell_folder_names = lambda folder: sorted(os.listdir(folder))
        f._shell_file_exists = lambda path: os.path.isfile(path)
        try:
            self.assertEqual(resolver(uri, [root]), wanted)
        finally:
            f._shell_folder_names, f._shell_file_exists = old_names, old_exists

    def test_connector_uri_never_guesses_when_exact_hierarchy_is_ambiguous(self):
        resolver = getattr(f, 'resolve_connector_uri', None)
        self.assertTrue(callable(resolver), 'automatic Desktop Connector URI resolver is missing')
        root = os.path.join(self.root, 'ACCDocs')
        self.make(os.path.join('ACCDocs', 'Account A', 'Project X', 'Project Files',
                               'Shared', 'Architecture', 'A.rvt'), b'a')
        self.make(os.path.join('ACCDocs', 'Account B', 'Project X', 'Project Files',
                               'Shared', 'Architecture', 'A.rvt'), b'b')
        uri = 'Autodesk Docs://Project X/Project Files/Shared/Architecture/A.rvt'
        old_names, old_exists = f._shell_folder_names, f._shell_file_exists
        f._shell_folder_names = lambda folder: sorted(os.listdir(folder))
        f._shell_file_exists = lambda path: os.path.isfile(path)
        try:
            with self.assertRaises(ValueError):
                resolver(uri, [root])
        finally:
            f._shell_folder_names, f._shell_file_exists = old_names, old_exists

    def test_uniformat_at_library_location_resolves_from_revit_library_root(self):
        resolver = getattr(f, 'resolve_library_resource', None)
        self.assertTrue(callable(resolver), 'Revit library resource resolver is missing')
        lib = os.path.join(self.root, 'Libraries')
        expected = self.make(os.path.join('Libraries', 'English-Imperial', 'US',
                                          'UniformatClassifications.txt'), b'uniformat')
        self.assertEqual(resolver('UniformatClassifications.txt', [lib]), expected)

    def test_uniformat_library_resolution_never_substitutes_a_different_filename(self):
        resolver = getattr(f, 'resolve_library_resource', None)
        self.assertTrue(callable(resolver), 'Revit library resource resolver is missing')
        lib = os.path.join(self.root, 'Libraries')
        self.make(os.path.join('Libraries', 'English-Imperial', 'US',
                               'AssemblyCode_Sample.txt'), b'newer-name')
        self.assertIsNone(resolver('UniformatClassifications.txt', [lib]))

    def test_same_element_staging_alias_does_not_become_a_second_failed_reference(self):
        host = self.make('Host.rvt', b'host')
        linked = self.make('Refs/Arch.rvt', b'arch')
        scratch_root = [None]

        class Backend(object):
            def set_staging_root(self, value):
                scratch_root[0] = value
            def scan(self, source, stage, options):
                if f.canonical(source) != f.canonical(host):
                    return dict(references=[], issues=[], version='2024')
                staged_alias = os.path.join(scratch_root[0], 'Arch.rvt')
                with open(staged_alias, 'wb') as out:
                    out.write(b'alias')
                return dict(references=[
                    dict(id='100', element_id='100', source=linked, kind='RevitLink', td=True),
                    dict(id='100:0', element_id='100', source=staged_alias, kind='RevitLink', td=False)
                ], issues=[], version='2024')
            def finish(self, *args):
                return []

        opts = f.defaults()
        opts['repath'] = False
        result = e.transmit([host], os.path.join(self.root, 'out'), Backend(), opts)
        self.assertEqual(e.package_counts(result)['files_copied'], 2, repr(result['issues']))
        self.assertFalse(any(x['code'] == 'STAGING_REFERENCE_UNRESOLVED' for x in result['issues']))
        aliases = [x for x in result['references'] if x.get('id') == '100:0']
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0].get('status'), 'DUPLICATE_ALIAS')

    def test_same_element_blank_external_alias_uses_saved_reference(self):
        host = self.make('HostBlank.rvt', b'host')
        linked = self.make('Refs/ArchBlank.rvt', b'arch')

        class Backend(object):
            def set_staging_root(self, value):
                pass
            def scan(self, source, stage, options):
                if f.canonical(source) != f.canonical(host):
                    return dict(references=[], issues=[], version='2024')
                return dict(references=[
                    dict(id='200', element_id='200', source=linked, kind='RevitLink', td=True),
                    dict(id='200:0', element_id='200', source='', kind='RevitLink', td=False,
                         special='external')
                ], issues=[], version='2024')
            def finish(self, *args):
                return []

        opts = f.defaults()
        opts['repath'] = False
        result = e.transmit([host], os.path.join(self.root, 'out_blank'), Backend(), opts)
        self.assertEqual(e.package_counts(result)['files_copied'], 2, repr(result['issues']))
        self.assertFalse(any(x['code'] == 'UNRESOLVED_SOURCE' for x in result['issues']))
        aliases = [x for x in result['references'] if x.get('id') == '200:0']
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0].get('status'), 'DUPLICATE_ALIAS')
        self.assertEqual(aliases[0].get('target'),
                         [x for x in result['references'] if x.get('id') == '200'][0].get('target'))

    def test_missing_optional_assembly_code_is_warning_not_file_failure(self):
        host = self.make('Host.rvt', b'host')

        class Backend(object):
            def set_staging_root(self, value):
                pass
            def scan(self, source, stage, options):
                return dict(references=[
                    dict(id='10', element_id='10', source='UniformatClassifications.txt',
                         kind='AssemblyCodeTable', optional_library=True)
                ], issues=[], version='2024')
            def finish(self, *args):
                return []

        opts = f.defaults()
        opts['repath'] = False
        result = e.transmit([host], os.path.join(self.root, 'out2'), Backend(), opts)
        self.assertEqual(e.package_counts(result)['hosts_copied'], 1)
        self.assertFalse(any(x['code'] == 'COLLECTION_FAILED' for x in result['issues']))
        optional = [x for x in result['issues'] if x['code'] == 'OPTIONAL_LIBRARY_REFERENCE_MISSING']
        self.assertEqual(len(optional), 1)
        self.assertEqual(optional[0]['severity'], 'warning')
        self.assertIn('completed with issues', e.completion_message([result], 1))


if __name__ == '__main__':
    print('RUNTIME:', sys.version)
    unittest.main(verbosity=2)
