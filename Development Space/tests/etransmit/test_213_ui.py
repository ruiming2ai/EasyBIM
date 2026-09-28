# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io, json, os, unittest
import xml.etree.ElementTree as ET
import test_211_ui_plugins as fixtures


class LoadUnloadedOption(fixtures.UI):
    def test_obsolete_load_preference_cannot_force_output_links_loaded(self):
        d=self.form();d.LoadUnloadedFiles.IsChecked=True;d.transmit_click(None,None)
        self.assertFalse(d.result[2].get('load_unloaded_files',False))

    def test_obsolete_preferences_are_not_saved(self):
        d=self.form();d.SaveSettings.IsChecked=True
        d.settings=os.path.join(self.root,'settings.json')
        d.transmit_click(None,None)
        with io.open(d.settings,encoding='utf-8') as inp:saved=json.load(inp)
        self.assertNotIn('load_unloaded_files',saved)
        self.assertNotIn('deep',saved)

    def test_global_checkbox_replaced_by_per_link_popup(self):
        path=os.path.join(fixtures.ROOT,'EasyBIM.tab','Links.panel','e-transmit.pushbutton','window.xaml')
        root=ET.parse(path).getroot();name='{http://schemas.microsoft.com/winfx/2006/xaml}Name'
        self.assertFalse(any(e.get(name)=='LoadUnloadedFiles' for e in root.iter()))
        popup=os.path.join(fixtures.ROOT,'lib','easybim_etransmit','unloaded_links.xaml')
        with io.open(popup,encoding='utf-8') as inp:text=inp.read()
        self.assertIn('Reload selected and continue',text)
        self.assertIn('Continue without reloading',text)
        self.assertIn('Undo history',text)


for name in fixtures.UI.__dict__:
    if name.startswith('test_') and name not in LoadUnloadedOption.__dict__:setattr(LoadUnloadedOption,name,None)

if __name__=='__main__':unittest.main(verbosity=2)
