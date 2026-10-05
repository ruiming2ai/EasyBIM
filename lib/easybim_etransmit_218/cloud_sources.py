# -*- coding: utf-8 -*-
"""An exact, explicitly selected published host and its authoritative RCM graph.

Names are resolved only within this API response for the chosen host version,
never by searching a cloud project for a similarly named model.
"""
from __future__ import unicode_literals
import hashlib
import ntpath
import time
from . import files as f,aps


def identifier(value):return hashlib.sha256(f.text(value).encode('utf-8')).hexdigest()[:20]


def validate_name(name):
    name=f.text(name)
    if (not name or name!=ntpath.basename(name) or '/' in name or '\\' in name or ':' in name
            or name!=name.strip() or not name.lower().endswith('.rvt')
            or any(ord(c)<32 for c in name) or any(c in name for c in '<>"|?*')):
        raise ValueError('Invalid RVT filename in the authoritative cloud manifest.')
    return name


class Graph(object):
    def __init__(self,project,version,response,folder_parts=None):
        host=dict(response.get('hostFile') or {})
        if host.get('versionId')!=version:raise ValueError('Published host version does not match the requested version.')
        self.fetched_at=time.time();self.project=project;self.version=version;self.folder_parts=list(folder_parts or [])
        self.key=identifier(project+'\n'+version);self.entries=[];self.by_name={};self.by_source={}
        records=[host]+list(response.get('linkedFiles',{}).get('results',[]))
        by_identity={}
        for index,record in enumerate(records):
            item=dict(record);name=validate_name(item.get('modelName',''))
            if not item.get('itemId'):raise ValueError('Cloud file has no stable item identity.')
            aps.validate_download_url(item.get('signedUrl',''))
            size=int(item.get('size',-1))
            if size<0:raise ValueError('Cloud file has no valid download size.')
            identity=item['itemId']+'\n'+f.text(item.get('versionId',''))
            if identity in by_identity:
                prior=by_identity[identity]
                if prior['modelName']!=name or int(prior['size'])!=size:
                    raise ValueError('Cloud manifest contains contradictory duplicate identities.')
                continue
            item['source']='aps://'+self.key+'/'+identifier(identity)+'/'+name
            item['is_host']=index==0;item['graph_key']=self.key
            by_identity[identity]=item;self.entries.append(item)
            self.by_source[item['source']]=item
            self.by_name.setdefault(name.lower(),[]).append(item)
        self.host=self.entries[0]
    def __repr__(self):return '<Published Revit graph '+self.key+'>'
    def match_staged_name(self,name):
        hits=self.by_name.get(f.text(name).lower(),[])
        if len(hits)>1:raise ValueError('Multiple cloud identities share this filename; name-only association is unsafe.')
        return hits[0] if hits else None
    def public_manifest(self):
        return dict(project_id=self.project,published_host_version=self.version,
                    source_mode='EXPLICIT_PUBLISHED_VERSION',graph_key=self.key,
                    files=[dict((k,x[k]) for k in ('modelName','itemId','versionId','size','publishStatus','source','is_host') if k in x) for x in self.entries],
                    coverage='Returned downloadable RVTs; omissions are reconciled against the Revit reference inventory.')
    def refresh_urls(self,response):
        fresh=Graph(self.project,self.version,response,self.folder_parts)
        if set(fresh.by_source)!=set(self.by_source):
            raise ValueError('The refreshed published graph has changed membership or permissions; no alternate version was chosen.')
        for key,item in self.by_source.items():
            candidate=fresh.by_source[key]
            if (item['modelName'],int(item['size']))!=(candidate['modelName'],int(candidate['size'])):
                raise ValueError('Refreshed graph metadata differs for the pinned snapshot.')
        for key,item in self.by_source.items():item['signedUrl']=fresh.by_source[key]['signedUrl']
        self.fetched_at=fresh.fetched_at
    def relative(self,item):
        # The RCM API does not promise a filesystem hierarchy for unpublished
        # child snapshots. Give each exact graph/identity its own namespace.
        name=validate_name(item['modelName'])
        return 'Sources/ACC_Versions/'+self.key+'/'+identifier(item['itemId'])+'/'+name
