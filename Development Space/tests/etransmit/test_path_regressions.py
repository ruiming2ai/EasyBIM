"""Regression coverage for long output roots and download-on-read sources."""
import builtins
import importlib
import ntpath
import types
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'lib'))
from easybim_etransmit import files as f, engine as e
from test_engine import Backend


class PathRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def helper(self, module, name):
        self.assertTrue(callable(getattr(module, name, None)), 'Missing ' + name)
        return getattr(module, name)

    def test_generated_package_root_does_not_repeat_model_name(self):
        package_root = self.helper(f, 'package_root')
        root = r'C:\Users\tester\OneDrive - Consulting Company\Documents\ET_20260923_163032'
        self.assertEqual(package_root(root, 0, True), ntpath.join(root, '01'))
        self.assertEqual(package_root(root, 1, True), ntpath.join(root, '02'))
        self.assertEqual(package_root(root, 0, False), root)

    def test_run_root_is_short_and_does_not_overwrite(self):
        new_run_root = self.helper(f, 'new_run_root')
        expected = self.base / 'ET_20260923_163032'
        self.assertEqual(new_run_root(str(self.base), '20260923_163032'), str(expected))
        expected.mkdir()
        self.assertEqual(new_run_root(str(self.base), '20260923_163032'), str(expected) + '_2')

    def test_selected_acc_host_path_is_preflighted_before_any_copy(self):
        preflight = self.helper(e, 'preflight_paths')
        root = r'C:\Users\tester\OneDrive - Consulting Engineers Corporation\Documents\Long project destination\ET_20260923_163032'
        source = (r'C:\Users\tester\DC\ACCDocs\Consulting Engineers Corporation\University Project\Project Files'
                  r'\Mechanical Electrical Plumbing\Shared\Architecture\Long Building Name Architectural Model.rvt')
        errors = preflight([source], root, f.defaults())
        self.assertTrue(errors)
        self.assertEqual(errors[0]['code'], 'DESTINATION_PATH_TOO_LONG')
        self.assertIn('characters', errors[0]['message'])
        self.assertIn('Long Building Name Architectural Model.rvt', errors[0]['target'])
        self.assertEqual(preflight([source], r'C:\ET\ET_20260923_163032', f.defaults()), [])

    def test_preflight_keeps_consumed_and_filename_exact(self):
        preflight = self.helper(e, 'preflight_paths')
        opts = f.defaults()
        opts['mappings'] = [('BIM 360://P/Project Files', r'C:\Users\tester\DC\ACCDocs\A\P\Project Files')]
        self.assertEqual(preflight(['BIM 360://P/Project Files/Consumed/Architecture/Model.rvt'],
                                   r'C:\ET\ET_20260923_163032', opts), [])
        self.assertIn('/Consumed/Architecture/Model.rvt',
                      f.mirror_path(f.resolve_source('BIM 360://P/Project Files/Consumed/Architecture/Model.rvt',
                                                     mappings=opts['mappings'])))

    def test_windows_path_check_reserves_revit_backups(self):
        validate = self.helper(f, 'validate_destination_path')
        path = 'C:\\P\\' + 'a' * 245 + '.rvt'
        self.assertEqual(len(path), 254)
        validate(path)
        with self.assertRaises(ValueError): validate(path[:-4] + 'a.rvt')

    def test_windows_path_check_counts_utf16_units(self):
        validate = self.helper(f, 'validate_destination_path')
        path = 'C:\\P\\' + '\U0001f4c1' * 130 + '.pdf'
        with self.assertRaises(ValueError): validate(path)

    def test_source_path_does_not_change_when_output_is_shortened(self):
        a = r'C:\Users\tester\DC\ACCDocs\Account\Project\Project Files\Shared\A\Details.pdf'
        b = r'C:\Users\tester\DC\ACCDocs\Account\Project\Project Files\Consumed\B\Details.pdf'
        self.assertEqual(f.mirror_path(a), 'Sources/ACC/Account/Project/Project Files/Shared/A/Details.pdf')
        self.assertEqual(f.mirror_path(b), 'Sources/ACC/Account/Project/Project Files/Consumed/B/Details.pdf')

    def test_temporary_copy_name_does_not_append_to_original_basename(self):
        src = self.base / 'source.pdf'; src.write_bytes(b'data')
        target = self.base / ('x' * 185 + '.pdf')
        publish = f.publish
        writes = []
        def inspect(temp, target):
            writes.append(temp)
            return publish(temp, target)
        with patch.object(f, 'publish', inspect):
            f.copy_file(str(src), str(target))
        self.assertEqual(target.read_bytes(), b'data')
        self.assertEqual(len(writes), 1)
        self.assertTrue(all(len(os.path.basename(p)) <= 20 for p in writes), writes)
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), sorted([src.name, target.name]))

    def test_hydration_on_first_read_is_not_a_mid_copy_source_change(self):
        src = self.base / 'model.rvt'; src.write_bytes(b'downloaded model')
        target = self.base / 'copy.rvt'
        real_open = builtins.open
        class HydratingReader:
            def __init__(self, stream): self.stream = stream; self.done = False
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def read(self, count=-1):
                if not self.done:
                    self.done = True
                    st = src.stat(); os.utime(src, (st.st_atime, st.st_mtime + 10))
                return self.stream.read(count)
            def seek(self, *args): return self.stream.seek(*args)
        def open_source(path, mode='r', *args, **kwargs):
            stream = real_open(path, mode, *args, **kwargs)
            return HydratingReader(stream) if os.fspath(path) == str(src) and mode == 'rb' else stream
        with patch.object(f, 'open', open_source, create=True):
            result = f.copy_file(str(src), str(target))
        self.assertEqual(target.read_bytes(), src.read_bytes())
        self.assertEqual(result['source_mtime'], src.stat().st_mtime)

    def test_missing_host_is_failed_not_finished_with_warning(self):
        out = self.base / 'out'
        result = e.transmit([str(self.base / 'missing.rvt')], str(out), Backend())
        self.assertEqual(result['status'], 'FAILED')
        summary = self.helper(e, 'package_counts')(result)
        self.assertEqual(summary['hosts_copied'], 0)
        self.assertEqual(summary['hosts_requested'], 1)
        report = (out / 'START_HERE.txt').read_text()
        self.assertIn('0 / 1', report)
        self.assertIn('No host model was copied', report)

    def test_inspection_uses_short_internal_rvt_name(self):
        source = self.base / ('Host_' + 'x' * 25 + '.rvt'); source.write_bytes(b'fake rvt')
        stages = []
        class RecordingBackend(Backend):
            def scan(self, source, stage, options):
                stages.append(stage)
                return super().scan(source, stage, options)
        r = e.transmit([str(source)], str(self.base / 'out'), RecordingBackend())
        self.assertEqual(r['status'], 'COLLECTED')
        self.assertTrue(all(len(os.path.basename(p)) <= 20 for p in stages), stages)
        self.assertTrue(r['files'][0]['target'].endswith(source.name))

    def test_rejected_destination_is_in_manifest_and_not_a_copied_host(self):
        source = self.base / 'Host.rvt'; source.write_bytes(b'rvt')
        out = self.base / 'out'
        with patch.object(f, 'destination', side_effect=f.PathLengthError('Destination length exceeds budget')):
            r = e.transmit([str(source)], str(out), Backend())
        self.assertEqual(r['status'], 'FAILED')
        self.assertEqual(r['files'][0]['status'], 'FAILED')
        self.assertEqual(r['issues'][0]['code'], 'DESTINATION_PATH_TOO_LONG')
        self.assertEqual(e.package_counts(r)['hosts_copied'], 0)
        self.assertFalse((out / 'Sources').exists())

    def test_internal_processing_paths_are_in_local_temp_not_output(self):
        source = self.base / 'Host.rvt'; source.write_bytes(b'rvt')
        out = self.base / 'out'; backend = Backend()
        seen = []
        backend.set_staging_root = lambda path: seen.append(path)
        r = e.transmit([str(source)], str(out), backend)
        self.assertEqual(r['status'], 'COLLECTED')
        self.assertEqual(len(seen), 1)
        self.assertEqual(Path(backend.finished[0][0]).parent, Path(seen[0]))
        self.assertFalse(f.within(seen[0], str(out)))
        self.assertFalse((out / '_work').exists())
        self.assertFalse(os.path.exists(seen[0]))

    def test_preflight_prompt_leaves_dialog_open_without_creating_package(self):
        # Execute the real event handler with WPF-shaped objects, not a Revit session.
        pyrevit = types.ModuleType('pyrevit')
        prompts = []
        pyrevit.forms = types.SimpleNamespace(WPFWindow=object, ProgressBar=object,
            alert=lambda message, **kwargs: prompts.append(message) or True)
        pyrevit.script = object(); pyrevit.DB = object()
        sys.modules.pop('easybim_etransmit.ui', None)
        with patch.dict(sys.modules, {'pyrevit': pyrevit}):
            ui = importlib.import_module('easybim_etransmit.ui')
        self.addCleanup(sys.modules.pop, 'easybim_etransmit.ui', None)
        dialog = object.__new__(ui.Dialog)
        flag = lambda value: types.SimpleNamespace(IsChecked=value)
        dialog.Models = types.SimpleNamespace(CommitEdit=lambda: None)
        dialog.models = [ui.Choice('Host', source=str(self.base / 'Host.rvt'))]
        dialog.categories = [ui.Choice(label, key) for key, label in f.CATEGORIES]
        dialog.Output = types.SimpleNamespace(Text=str(self.base))
        dialog.DeepScan = flag(True); dialog.Repath = flag(True)
        dialog.Cleanup = flag(False); dialog.Upgrade = flag(False)
        dialog.DiscardWorksets = flag(False); dialog.Purge = flag(False)
        dialog.Separate = flag(True); dialog.Reports = flag(True); dialog.Zip = flag(False)
        dialog.ViewMode = types.SimpleNamespace(SelectedItem=types.SimpleNamespace(Key='all'))
        dialog.view_types=[]; dialog.mappings=[]; dialog.extras=[]; dialog.result=None
        chosen=[]; closed=[]
        dialog.browse_output=lambda *args: chosen.append(True)
        dialog.Close=lambda: closed.append(True)
        with patch.object(e, 'preflight_paths', return_value=[{'message':'Destination is too long.'}]), \
             patch.object(e, 'transmit') as transmit:
            dialog.transmit_click(None, None)
        self.assertEqual(chosen, [True]); self.assertEqual(closed, [])
        self.assertIsNone(dialog.result)
        self.assertTrue(any('No files were copied' in message for message in prompts))
        transmit.assert_not_called()
        self.assertEqual(list(self.base.iterdir()), [])

    def test_status_summary_cannot_say_finished_when_host_is_missing(self):
        summarize = self.helper(e, 'completion_message')
        r = {'status':'FAILED','root':str(self.base), 'requested_models':['missing.rvt'],
             'models':[], 'files':[], 'issues':[e.issue('COLLECTION_FAILED','missing.rvt','Missing','error')]}
        message = summarize([r], 1)
        self.assertIn('failed', message.lower())
        self.assertIn('0 / 1', message)
        self.assertNotIn('finished', message.lower())


if __name__ == '__main__': unittest.main()
