# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io, json, os, unittest
import xml.etree.ElementTree as ET
import test_211_ui_plugins as fixtures
from easybim_etransmit import layout
Obj=fixtures.Obj

class StructureUI(fixtures.UI):
    def test_all_dropdown_selections_are_transmitted_and_saved(self):
        for mode in ('categories','original','flat'):
            d=self.form();d.FileStructure=Obj(SelectedItem=Obj(Key=mode));d.SaveSettings.IsChecked=True
            d.settings=os.path.join(self.root,'settings.json');d.client_id='';d.callback_uri='http://localhost'
            d.transmit_click(None,None)
            self.assertEqual(d.result[2]['file_structure'],mode)
            with io.open(d.settings,encoding='utf-8') as inp:saved=json.load(inp)
            self.assertEqual(saved['file_structure'],mode)
    def test_old_combined_preference_cannot_disable_independent_packages(self):
        d=self.form();d.Separate.IsChecked=False;d.transmit_click(None,None)
        self.assertTrue(d.result[2]['per_model'])
    def test_loading_settings_defaults_and_valid_choices(self):
        original=os.environ.get('APPDATA');os.environ['APPDATA']=self.root
        self.addCleanup(self.restore_env,original)
        class Window(object):
            @staticmethod
            def __init__(window,xaml):
                for name in ('Categories','ViewMode','VersionLabel','Output','DeepScan','FileStructure','Repath',
                             'Reports','LoadUnloadedFiles','Zip','ZipPerModel','Purge','Models','Extras','Mappings'):
                    setattr(window,name,Obj())
        old_window=self.ui.forms.WPFWindow;old_db=self.ui.DB
        self.ui.forms.WPFWindow=Window;self.ui.DB=Obj(Document=Obj())
        try:
            path=os.path.join(self.root,'EasyBIM','e-transmit','settings.json');os.makedirs(os.path.dirname(path))
            for value in (None,'invalid','categories','original','flat'):
                with io.open(path,'w',encoding='utf-8') as out:out.write(json.dumps({} if value is None else {'file_structure':value}))
                d=self.ui.Dialog(Obj(Application=Obj(VersionNumber='2024',Documents=[]),ActiveUIDocument=None),'unused')
                self.assertEqual(d.structures[d.FileStructure.SelectedIndex].Key,layout.mode(value))
        finally:self.ui.forms.WPFWindow=old_window;self.ui.DB=old_db
    def restore_env(self,value):
        if value is None:os.environ.pop('APPDATA',None)
        else:os.environ['APPDATA']=value
    def test_separate_group_and_three_labels(self):
        path=os.path.join(fixtures.ROOT,'EasyBIM.tab','Links.panel','e-transmit.pushbutton','window.xaml')
        root=ET.parse(path).getroot();name='{http://schemas.microsoft.com/winfx/2006/xaml}Name'
        group=next(e for e in root.iter() if e.get('Header')=='File Structure Organization')
        self.assertTrue(any(e.get(name)=='FileStructure' for e in group.iter()))
        self.assertEqual([label for key,label in layout.MODES],['By category','Retain original folder structure','All files together'])

for name in fixtures.UI.__dict__:
    if name.startswith('test_') and name not in StructureUI.__dict__:setattr(StructureUI,name,None)
if __name__=='__main__':unittest.main(verbosity=2)
