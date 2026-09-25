# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os,sys,unittest,tempfile,shutil,types,importlib
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import plugin_sources
class Obj(object):
    def __init__(self,**kw):self.__dict__.update(kw)
class UI(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_UI_211_');self.addCleanup(shutil.rmtree,self.root)
        self.old_pyrevit=sys.modules.get('pyrevit');self.old_ui=sys.modules.pop('easybim_etransmit.ui',None)
        self.answer=True;self.alerts=[]
        def alert(msg,**kw):self.alerts.append(msg);return self.answer
        fake=types.ModuleType('pyrevit');fake.forms=Obj(WPFWindow=object,ProgressBar=object,alert=alert)
        fake.DB=Obj();fake.script=Obj();sys.modules['pyrevit']=fake
        self.ui=importlib.import_module('easybim_etransmit.ui')
        self.addCleanup(self.cleanup)
    def cleanup(self):
        sys.modules.pop('easybim_etransmit.ui',None)
        if self.old_ui is not None:sys.modules['easybim_etransmit.ui']=self.old_ui
        if self.old_pyrevit is None:sys.modules.pop('pyrevit',None)
        else:sys.modules['pyrevit']=self.old_pyrevit
    def form(self):
        d=self.ui.Dialog.__new__(self.ui.Dialog)
        d.result=None;d.snapshots_authorized=False;d.Models=Obj(CommitEdit=lambda:None)
        d.models=[self.ui.Choice('Open Host',document=Obj(Title='Open Host'),mode='LIVE_DOCUMENT')]
        d.Output=Obj(Text=self.root);d.categories=[];d.extras=[];d.mappings=[];d.view_types=[]
        for name in ['DeepScan','Repath','LoadUnloadedFiles','Reports','Separate','SkipCloudLinks']:setattr(d,name,Obj(IsChecked=True))
        for name in ['Cleanup','Upgrade','DiscardWorksets','Purge','Zip','ZipPerModel','SaveSettings']:setattr(d,name,Obj(IsChecked=False))
        d.FileStructure=Obj(SelectedItem=Obj(Key='categories'))
        d.ViewMode=Obj(SelectedItem=Obj(Key='all'));d.uiapp=Obj(Application=Obj(VersionNumber='2024'));d.Close=lambda:None
        return d
    def test_accept_before_batch_grants_current_state_authorization(self):
        d=self.form();d.transmit_click(None,None)
        self.assertIsNotNone(d.result)
        self.assertFalse(d.snapshots_authorized,'cache-only mode must not authorize source saves')
        self.assertTrue(d.result[2]['saved_state_only'])
        self.assertTrue(d.result[2]['skip_cloud_links'])
        self.assertEqual(self.alerts,[])
    def test_decline_aborts_before_creating_outputs(self):
        self.answer=False;d=self.form();d.transmit_click(None,None)
        self.assertIsNotNone(d.result);self.assertFalse(d.snapshots_authorized);self.assertEqual(os.listdir(self.root),[])
    def test_run_no_longer_asks_per_host_after_batch_started(self):
        with open(os.path.join(ROOT,'lib/easybim_etransmit/ui.py')) as inp:code=inp.read()
        self.assertNotIn('registry.confirm_snapshot=confirm',code)
        self.assertNotIn('registry.authorize_snapshots(',code)
        self.assertIn('saved_state_only=True',code)
        self.assertNotIn("saved['snapshots_authorized']",code)
        with open(os.path.join(ROOT,'EasyBIM.tab/Links.panel/e-transmit.pushbutton/window.xaml')) as inp:xaml=inp.read()
        self.assertIn('x:Name="SkipCloudLinks"',xaml)
class Schemas(unittest.TestCase):
    def test_read_access_checked_before_protected_fields(self):
        def protected():self.fail('ListFields called before read access was granted')
        schema=Obj(SchemaName='FamilyBrowserStatus',VendorId='CTC',GUID='schema',
                   ReadAccessGranted=lambda:False,ListFields=protected)
        db=Obj(ExtensibleStorage=Obj(Schema=Obj(ListSchemas=lambda:[schema])))
        rows,coverage=plugin_sources.discover(db,Obj())
        self.assertEqual(rows,[]);self.assertEqual(coverage[0]['status'],'READ_DENIED')
    def test_invalid_schema_is_not_inspected_as_a_workbook(self):
        def invalid():self.fail('must not inspect invalid schema')
        schema=Obj(SchemaName='CTC_Status',VendorId='CTC',GUID='schema',IsValidObject=False,ListFields=invalid)
        db=Obj(ExtensibleStorage=Obj(Schema=Obj(ListSchemas=lambda:[schema])))
        rows,coverage=plugin_sources.discover(db,Obj())
        self.assertEqual(rows,[]);self.assertEqual(coverage[0]['status'],'UNAVAILABLE_SCHEMA')
if __name__=='__main__':unittest.main(verbosity=2)
