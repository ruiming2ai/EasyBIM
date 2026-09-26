# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import copy, os, unittest
import test_212_session as fixtures
from test_212_cache import Obj
from test_payload_acquisition import compound
from easybim_etransmit import files as f, session as s, engine

class DiscoveryTiming(fixtures.SavedCacheSession):
    def local_setup(self):
        host=os.path.join(self.root,'Host.rvt');link=os.path.join(self.root,'Link.rvt')
        for p in (host,link):
            with open(p,'wb') as out:out.write(compound())
        self.doc.PathName=host;self.doc.IsModelInCloud=False;self.doc.IsWorkshared=False
        child=copy.copy(self.doc);child.Title='Link';child.PathName=link;child.IsLinked=True
        instance=Obj(GetLinkDocument=lambda:child,GetTypeId=lambda:Obj(IntegerValue=9))
        self.scanned=[]
        def scan(doc,source,info,result):
            self.scanned.append(doc)
            if doc is self.doc:result['references'].append(dict(id='9',element_id='9',source=link,kind='RevitLink',td=True,loaded=True))
        self.r.scanner.scan_open=scan
        self.r.scanner.elements=lambda doc,kind:[instance] if doc is self.doc and kind=='RevitLinkInstance' else []
        return host,link
    def test_link_identity_registration_does_not_scan_its_nested_contents(self):
        self.local_setup();self.r.add_live(self.doc)
        self.assertEqual(self.scanned,[self.doc])
    def test_initial_registry_discovery_is_included_in_report(self):
        self.local_setup();key=self.r.add_live(self.doc)
        out=os.path.join(self.root,'out');opts=f.defaults();opts['repath']=False
        result=engine.transmit([key],out,s.SessionBackend(self.db,self.app,out,self.r),opts)
        self.assertTrue(result.get('performance',{}).get('initial_discovery'))
        p=result['performance']
        self.assertGreaterEqual(p['measured_total_seconds'],p['elapsed_seconds'])

for name in fixtures.SavedCacheSession.__dict__:
    if name.startswith('test_') and name not in DiscoveryTiming.__dict__:setattr(DiscoveryTiming,name,None)
if __name__=='__main__':unittest.main()
