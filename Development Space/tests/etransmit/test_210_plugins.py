# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os,sys,unittest,json
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f
class Obj(object):
    def __init__(self,**kw):self.__dict__.update(kw)
def mod(test):
    try:from easybim_etransmit import plugin_sources;return plugin_sources
    except ImportError:test.fail('plugin spreadsheet source discovery is missing')
class Paths(unittest.TestCase):
    def test_only_named_association_fields_not_notes(self):
        m=mod(self)
        data=dict(ExcelFilePath='C:/Project/Schedule.xlsx',Notes='Example C:/Bad.xlsx',Other='C:/Unrelated.xlsx')
        self.assertEqual(m.associations(data),[('ExcelFilePath','C:/Project/Schedule.xlsx')])
    def test_nested_json_xml_and_duplicate_fields(self):
        m=mod(self)
        rows=m.associations(dict(Data=json.dumps(dict(Links=[dict(WorkbookPath='Sheets/A.xlsm')])),
                                 Settings='<Settings><SourceFile>Sheets/B.xlsx</SourceFile></Settings>'))
        self.assertEqual(set(x[1] for x in rows),set(['Sheets/A.xlsm','Sheets/B.xlsx']))
    def test_xml_entities_and_oversize_storage_fail_closed(self):
        m=mod(self)
        with self.assertRaises(ValueError):m.associations(dict(Data='<!DOCTYPE x [<!ENTITY a SYSTEM "file:///secret">]><SourceFile>&a;</SourceFile>'))
        with self.assertRaises(ValueError):m.associations(dict(Data='x'*(2*1024*1024+1)))
    def test_field_provenance_and_no_raw_storage_in_record(self):
        m=mod(self)
        row=m.make_row('Ideate Sticky','schema','12','WorkbookPath','Sheets/B.xlsx','C:/Central/Host.rvt')
        self.assertEqual(row['category'],'spreadsheets');self.assertEqual(row['special'],'plugin_spreadsheet')
        self.assertEqual(row['source'],r'C:\Central\Sheets\B.xlsx')
        self.assertEqual(row['source_field'],'WorkbookPath')
        self.assertNotIn('raw_storage',row)
    def test_spreadsheets_category_default_included(self):
        self.assertIn('spreadsheets',f.defaults()['include'])
        self.assertEqual(f.category('C:/Schedule.xlsm'),'spreadsheets')
class SchemaDiscovery(unittest.TestCase):
    def db(self,schemas):
        return Obj(ExtensibleStorage=Obj(Schema=Obj(ListSchemas=lambda:schemas)))
    def test_read_denied_is_visible_without_attempt_to_read_entities(self):
        m=mod(self)
        schema=Obj(SchemaName='IdeateSticky',VendorId='IDEA',GUID='x',ReadAccessGranted=lambda:False,ListFields=lambda:[])
        rows,coverage=m.discover(self.db([schema]),Obj())
        self.assertEqual(rows,[]);self.assertEqual(coverage[0]['status'],'READ_DENIED')
    def test_empty_scan_is_not_a_claim_of_no_spreadsheets(self):
        m=mod(self)
        rows,coverage=m.discover(self.db([]),Obj())
        self.assertEqual(coverage[0]['status'],'NO_SUPPORTED_ASSOCIATION_FOUND')
    def test_cancellation_not_swallowed(self):
        m=mod(self)
        with self.assertRaises(f.Cancelled):m.discover(self.db([]),Obj(),lambda:True)
if __name__=='__main__':unittest.main(verbosity=2)
