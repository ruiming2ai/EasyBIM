# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, unittest
import test_211_ui_plugins as legacy
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
class CacheUI(legacy.UI):
    # Inherit setup only; new contract supersedes the old SaveAs confirmation.
    def test_accept_before_batch_grants_current_state_authorization(self):
        d=self.form();d.transmit_click(None,None)
        self.assertIsNotNone(d.result)
        self.assertTrue(d.result[2].get('saved_state_only'))
        self.assertFalse(d.snapshots_authorized)
        self.assertFalse(self.alerts,'Cache-only export must not ask to SaveAs an original')
    def test_decline_aborts_before_creating_outputs(self):
        self.answer=False;d=self.form();d.transmit_click(None,None)
        self.assertIsNotNone(d.result,'There is no save authorization to decline in cache mode')
        self.assertEqual(os.listdir(self.root),[])
    def test_run_no_longer_asks_per_host_after_batch_started(self):
        with open(os.path.join(ROOT,'lib/easybim_etransmit/ui.py')) as inp:code=inp.read()
        self.assertNotIn('registry.authorize_snapshots(',code)
        self.assertIn('saved_state_only=True',code)
        with open(os.path.join(ROOT,'EasyBIM.tab/Links.panel/e-transmit.pushbutton/window.xaml')) as inp:xaml=inp.read()
        self.assertIn('Unsaved edits are excluded',xaml)
class CacheWording(unittest.TestCase):
    def test_button_disclosure_does_not_promise_to_save_working_model(self):
        # Files are ordinary UTF-8; avoid requiring the real WPF host.
        paths=['EasyBIM.tab/Links.panel/e-transmit.pushbutton/bundle.yaml',
               'EasyBIM.tab/Links.panel/e-transmit.pushbutton/window.xaml']
        import io
        for relative in paths:
            with io.open(os.path.join(ROOT,relative),'r',encoding='utf-8') as inp:text=inp.read().lower()
            self.assertNotIn('always save the selected host',text)
            self.assertNotIn('save selected open hosts from their current state',text)
            self.assertNotIn('read-only cache copies may change',text)
            self.assertIn('unsaved edits',text)

if __name__=='__main__':unittest.main(verbosity=2)
