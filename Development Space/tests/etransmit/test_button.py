import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[3]
BUTTON=ROOT/'EasyBIM.tab/Links.panel/e-transmit.pushbutton'

class ButtonTests(unittest.TestCase):
    def test_zero_document_context(self):
        self.assertTrue((BUTTON/'bundle.yaml').exists(),'button bundle missing')
        self.assertIn('context: zero-doc',(BUTTON/'bundle.yaml').read_text())
    def test_xaml_parse_and_controls(self):
        self.assertTrue((BUTTON/'window.xaml').exists(),'WPF dialog missing')
        doc=ET.parse(BUTTON/'window.xaml')
        names={el.attrib.get('{http://schemas.microsoft.com/winfx/2006/xaml}Name') for el in doc.iter()}
        for name in ['Models','Categories','Output','DeepScan','Repath','Cleanup','Upgrade','ViewMode','SaveSettings','Mappings']:
            self.assertIn(name,names)
    def test_button_uses_own_module_only(self):
        self.assertTrue((BUTTON/'script.py').exists(),'button script missing')
        s=(BUTTON/'script.py').read_text()
        self.assertIn('easybim_etransmit',s)
        self.assertNotIn('revit.doc',s)
    def test_no_missing_event_handlers(self):
        self.assertTrue((BUTTON/'window.xaml').exists(),'WPF dialog missing')
        ui=ROOT/'lib/easybim_etransmit/ui.py'
        self.assertTrue(ui.exists(),'UI implementation missing')
        s=ui.read_text()
        for el in ET.parse(BUTTON/'window.xaml').iter():
            for key in ('Click','SelectionChanged','Checked','Unchecked'):
                if key in el.attrib: self.assertIn('def '+el.attrib[key]+'(',s)

if __name__=='__main__': unittest.main()
