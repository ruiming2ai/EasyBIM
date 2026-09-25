# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os,sys,unittest,xml.etree.ElementTree as ET
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
class Obj(object):
    def __init__(self,**kw):self.__dict__.update(kw)
class Picker(unittest.TestCase):
    def module(self):
        try:from easybim_etransmit import cloud_picker;return cloud_picker
        except ImportError:self.fail('Published version picker is missing')
    def test_explicit_version_selection_not_latest_download(self):
        m=self.module();calls=[];v='urn:adsk.wipprod:fs.file:vf.H?version=2'
        c=Obj(hubs=lambda:[dict(id='hub',attributes=dict(name='Firm'))],
              projects=lambda h:[dict(id='b.p',attributes=dict(name='Project'))],
              top_folders=lambda h,p:[dict(type='folders',id='folder',attributes=dict(name='Project Files'))],
              contents=lambda p,f:[dict(type='items',id='host',attributes=dict(displayName='Host.rvt'))],
              versions=lambda p,i:[dict(id=v,attributes=dict(versionNumber=2)),dict(id=v.replace('2','3'),attributes=dict(versionNumber=3))],
              linked_files=lambda p,x:calls.append(x) or dict(hostFile=dict(modelName='Host.rvt',itemId='host',versionId=x,size=1,signedUrl='https://s3.amazonaws.com/h'),linkedFiles=dict(results=[])))
        def select(options,title):
            return next(x for x in options if x.value!='..')
        result=m.choose(c,select)
        self.assertEqual(calls,[v]);self.assertEqual(result['graph'].version,v)
        self.assertEqual(result['folder_parts'],['Firm','Project','Project Files'])
    def test_cancel_does_not_start_downloads(self):
        m=self.module();c=Obj(hubs=lambda:[dict(id='h',attributes=dict(name='Firm'))])
        self.assertIsNone(m.choose(c,lambda *a:None))
class UIContract(unittest.TestCase):
    def read(self,name):
        with open(os.path.join(ROOT,name),'r') as inp:return inp.read()
    def test_controls_and_callbacks_are_present(self):
        text=self.read('EasyBIM.tab/Links.panel/e-transmit.pushbutton/window.xaml');tree=ET.fromstring(text)
        names=set(n.attrib.get('{http://schemas.microsoft.com/winfx/2006/xaml}Name') for n in tree.iter())
        self.assertIn('ZipPerModel',names)
        ui=self.read('lib/easybim_etransmit/ui.py')
        for callback in ['sign_in_cloud','add_cloud_model','associate_cloud_graph']:
            self.assertIn('Click="'+callback+'"',text);self.assertIn('def '+callback+'(',ui)
        self.assertIn('Binding="{Binding Mode}"',text)
    def test_live_document_not_reduced_to_source_path(self):
        ui=self.read('lib/easybim_etransmit/ui.py')
        self.assertIn('document=doc',ui)
        self.assertIn("row.Mode=='LIVE_DOCUMENT'",ui)
        self.assertIn('registry.register_live(',ui)
        self.assertIn('batch.run_batch(',ui)
        self.assertNotIn('Only the saved versions will be packaged',ui)
    def test_settings_do_not_store_auth_secrets(self):
        ui=self.read('lib/easybim_etransmit/ui.py')
        self.assertIn('self.tokens.close()',ui)
        self.assertNotIn("saved['token']",ui);self.assertNotIn("saved['refresh_token']",ui)
if __name__=='__main__':unittest.main(verbosity=2)
