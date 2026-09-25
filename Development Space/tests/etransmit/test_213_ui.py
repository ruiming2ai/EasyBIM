# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io, json, os, unittest
import xml.etree.ElementTree as ET
import test_211_ui_plugins as fixtures


class LoadUnloadedOption(fixtures.UI):
    def test_checkbox_option_defaults_on_and_is_transmitted(self):
        d=self.form();d.transmit_click(None,None)
        self.assertTrue(d.result[2]['load_unloaded_files'])

    def test_unchecked_preference_is_saved(self):
        d=self.form();d.LoadUnloadedFiles.IsChecked=False;d.SaveSettings.IsChecked=True
        d.settings=os.path.join(self.root,'settings.json');d.client_id='';d.callback_uri='http://localhost'
        d.transmit_click(None,None)
        self.assertFalse(d.result[2]['load_unloaded_files'])
        with io.open(d.settings,encoding='utf-8') as inp:saved=json.load(inp)
        self.assertFalse(saved['load_unloaded_files'])

    def test_checkbox_default_and_repath_enable_binding(self):
        path=os.path.join(fixtures.ROOT,'EasyBIM.tab','Links.panel','e-transmit.pushbutton','window.xaml')
        root=ET.parse(path).getroot()
        name='{http://schemas.microsoft.com/winfx/2006/xaml}Name'
        box=next(e for e in root.iter() if e.get(name)=='LoadUnloadedFiles')
        self.assertEqual(box.get('Content'),'Load Unloaded Files')
        self.assertEqual(box.get('IsChecked'),'True')
        self.assertEqual(box.get('IsEnabled'),'{Binding IsChecked, ElementName=Repath}')


for name in fixtures.UI.__dict__:
    if name.startswith('test_') and name not in LoadUnloadedOption.__dict__:setattr(LoadUnloadedOption,name,None)

if __name__=='__main__':unittest.main(verbosity=2)
