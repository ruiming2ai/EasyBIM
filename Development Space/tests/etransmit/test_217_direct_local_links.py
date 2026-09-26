# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import copy, os, unittest

import test_212_session as fixtures
from test_212_cache import Obj, V, W
from test_payload_acquisition import compound
from easybim_etransmit import cache_sources as c, session as s, files as f, engine


class DirectLocalRevitLinks(fixtures.SavedCacheSession):
    def local_files(self):
        host=os.path.join(self.root,'Host.rvt')
        link=os.path.join(self.root,'Architecture.rvt')
        with open(host,'wb') as out:out.write(compound(suffix='host'))
        with open(link,'wb') as out:out.write(compound(suffix='link'))
        self.doc.Title='Host'
        self.doc.PathName=host
        self.doc.IsModelInCloud=False
        self.doc.IsLinked=False
        self.doc.IsModified=False
        self.doc.IsWorkshared=False
        return host,link

    def test_loaded_local_link_keeps_saved_file_path_instead_of_virtual_document_key(self):
        host,link=self.local_files()
        child=copy.copy(self.doc)
        child.Title='Architecture'
        child.PathName=link
        child.IsLinked=True
        child.IsModelInCloud=False
        instance=Obj(GetLinkDocument=lambda:child,
                     GetTypeId=lambda:Obj(IntegerValue=42))
        def scan_open(doc,source,info,result):
            if doc is self.doc:
                result['references'].append(dict(
                    id='42',element_id='42',kind='RevitLink',
                    source=link,loaded=True,td=True))
        self.r.scanner.scan_open=scan_open
        self.r.scanner.elements=lambda doc,name:(
            [instance] if doc is self.doc and name=='RevitLinkInstance' else [])
        key=self.r.add_live(self.doc)
        row=self.r.get(key)['inventory']['references'][0]
        self.assertEqual(row['source'],link)
        self.assertEqual(row['source_evidence'],'LIVE_LINK_SAVED_FILE_PATH')

    def test_open_local_host_copies_known_link_without_temporary_rvt_inspection(self):
        host,link=self.local_files()
        def scan_open(doc,source,info,result):
            result['references'].append(dict(
                id='42',element_id='42',kind='RevitLink',
                source=link,loaded=True,td=True))
        self.r.scanner.scan_open=scan_open
        self.r.scanner.elements=lambda *args:[]
        key=self.r.add_live(self.doc)
        out=os.path.join(self.root,'out')
        backend=s.SessionBackend(self.db,self.app,out,self.r)
        scan_calls=[]
        def unexpected_scan(source,stage,options):
            scan_calls.append(source)
            return dict(references=[],issues=[],version='2024')
        backend.scan=unexpected_scan
        options=f.defaults();options['repath']=False
        result=engine.transmit([key],out,backend,options)
        self.assertEqual(scan_calls,[])
        self.assertEqual(engine.package_counts(result)['revit_links_copied'],1,repr(result['issues']))
        copied=next(row for row in result['files'] if row.get('source')==link)
        self.assertEqual(f.digest(copied['target']),f.digest(link))

    def test_local_saved_file_revision_difference_does_not_block_copy(self):
        host,link=self.local_files()
        key=self.r.add_live(self.doc)
        self.r._cache_store=c.Store(
            self.db,self.app,os.path.join(self.r.temp_root(),'cache-local'),
            roots=[self.cache])
        self.r._cache_store.read_info=lambda path:dict(
            version=dict(guid=W,saves=8),format='2024')
        snapshot=self.r.snapshot(key)
        self.assertEqual(f.digest(snapshot),f.digest(host))
        metadata=self.r.get(key)['cache_metadata']
        self.assertEqual(metadata['revision_check'],'SAVED_FILE_COPIED')
        self.assertEqual(metadata['cache_document_version'],dict(guid=W,saves=8))


for name in fixtures.SavedCacheSession.__dict__:
    if name.startswith('test_') and name not in DirectLocalRevitLinks.__dict__:
        setattr(DirectLocalRevitLinks,name,None)


if __name__=='__main__':
    unittest.main(verbosity=2)
