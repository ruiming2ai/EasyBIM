# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io, os, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
XAML=os.path.join(ROOT,'EasyBIM.tab','Links.panel','e-transmit.pushbutton','window.xaml')
UI=os.path.join(ROOT,'lib','easybim_etransmit','ui.py')
class LiveACCSourcePolicy(unittest.TestCase):
    def text(self,path):
        with io.open(path,'r',encoding='utf-8') as inp:return inp.read()
    def test_published_acc_picker_and_signin_controls_are_absent(self):
        xaml=self.text(XAML)
        for value in ('Add ACC published model...','Associate ACC download for open row...','APS setup / Sign in...','AuthStatus'):self.assertNotIn(value,xaml)
    def test_native_revit_acc_guidance_is_visible_and_mappings_remain(self):
        xaml=self.text(XAML);self.assertIn('Revit Home',xaml);self.assertIn('Autodesk Docs',xaml);self.assertIn('Mappings',xaml);self.assertIn('Add exact prefix mapping...',xaml)
    def test_ui_has_source_mode_guard_and_no_published_mode_execution_branch(self):
        ui=self.text(UI);self.assertIn('def validate_source_modes(',ui);self.assertIn("'LIVE_DOCUMENT'",ui);self.assertIn("'SAVED_FILE'",ui)
        self.assertNotIn("elif row.Mode=='PUBLISHED_VERSION'",ui);self.assertNotIn('def add_cloud_model(',ui);self.assertNotIn('def sign_in_cloud(',ui)
if __name__=='__main__':unittest.main(verbosity=2)
