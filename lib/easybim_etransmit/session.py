# -*- coding: utf-8 -*-
"""Per-command live documents and explicit published-cloud sources.

Document handles never enter a manifest. Discovery never opens, saves, reloads,
closes or modifies the user's document. A primary-document SaveAs requires a
runtime callback granting consent; its recovery file is deliberately retained.
"""
from __future__ import unicode_literals
import copy
import os
import ntpath
import tempfile
import uuid
import time
from . import files as f, model_payload
from .revit import Backend, eid, dispose
from .engine import issue
from .cloud_sources import identifier


class SourceError(ValueError):
    def __init__(self,code,message):
        ValueError.__init__(self,message);self.code=code


def doc_version(doc):
    value=None
    try:
        value=doc.GetDocumentVersion(doc)  # Static Revit API requires the Document argument.
        return dict(guid=f.text(value.VersionGUID).lower(),saves=int(value.NumberOfSaves))
    except Exception:return None
    finally:dispose(value)


def cloud_identity(doc):
    path=None
    try:
        if not bool(getattr(doc,'IsModelInCloud',False)):return {}
        path=doc.GetCloudModelPath()
        return dict(model_guid=f.text(path.GetModelGUID()).lower(),
                    project_guid=f.text(path.GetProjectGUID()).lower(),
                    region=f.text(getattr(path,'Region','')))
    except Exception:return {}
    finally:dispose(path)


def file_version(DB,path):
    info=version=None
    try:
        info=DB.BasicFileInfo.Extract(path);version=info.GetDocumentVersion()
        return dict(guid=f.text(version.VersionGUID).lower(),saves=int(version.NumberOfSaves))
    finally:dispose(version);dispose(info)


class Registry(object):
    def __init__(self,DB,application,recovery_root,cancelled=None,collect_plugins=True):
        self.DB=DB;self.app=application;self.recovery_root=recovery_root;self.cancelled=cancelled
        self.entries={};self._documents=[];self.graphs={};self.clients={};self._temp=None
        self.confirm_snapshot=None;self.collect_plugins=collect_plugins
        self.scanner=Backend(DB,application,recovery_root,cancelled)
    def get(self,key):return self.entries.get(key)
    def owns(self,key):return key in self.entries
    def register_live(self,doc,configured_source=None):
        for old,key in self._documents:
            if old is doc or old==doc:return key
        original=f.text(configured_source or getattr(doc,'PathName','') or '')
        name=f.text(doc.Title)
        if bool(getattr(doc,'IsLinked',False)) and f.absolute(original) and not f.cache_source(original):
            name=ntpath.basename(original)
        if not name.lower().endswith('.rvt'):name+='.rvt'
        if any(c in name for c in '<>:"/\\|?*'):
            raise SourceError('INVALID_OPEN_MODEL_NAME','The open document title is not a valid RVT filename.')
        key='open://'+uuid.uuid4().hex+'/'+name
        central='';central_path=None
        if getattr(doc,'IsWorkshared',False):
            try:
                central_path=doc.GetWorksharingCentralModelPath()
                central=self.scanner.visible(central_path)
            except Exception:pass
            finally:dispose(central_path)
        entry=dict(source=key,mode='LIVE_DOCUMENT',document=doc,name=name,original_path=original,
                   cloud=cloud_identity(doc),document_version=doc_version(doc),reference_central=central,
                   state_basis='LIVE_INVENTORY_ONLY',children=[],snapshot_path=None,is_linked=bool(getattr(doc,'IsLinked',False)))
        self.entries[key]=entry;self._documents.append((doc,key))
        return key

    def add_live(self,doc,configured_source=None):
        key=self.register_live(doc,configured_source)
        self.inventory(key)
        return key

    def inventory(self,key):
        entry=self.entries[key]
        if 'inventory' in entry:return entry['inventory']
        doc=entry['document'];original=entry.get('original_path','')
        result=dict(references=[],issues=[],version=f.text(getattr(self.app,'VersionNumber','')),
                    opened_in_revit=True,inspection_status='LIVE_DOCUMENT',source_mode='LIVE_DOCUMENT')
        entry['inventory']=result
        central=entry.get('reference_central','')
        info=dict(central=central,workshared=bool(getattr(doc,'IsWorkshared',False)))
        try:
            self.scanner.scan_open(doc,original or key,info,result)
        except f.Cancelled:raise
        except Exception as exc:
            result['issues'].append(issue('LIVE_INVENTORY_FAILED',key,exc,'error'))
            result['open_failed']=True
        if self.collect_plugins:
            try:self.scanner.scan_plugins(doc,central or original,result)
            except f.Cancelled:raise
            except Exception as exc:
                # Optional vendor coverage is not a failure to serialize the RVT.
                result['issues'].append(issue('PLUGIN_SOURCE_COVERAGE',key,exc))
        try:
            for instance in self.scanner.elements(doc,'RevitLinkInstance'):
                f.check(self.cancelled)
                child=instance.GetLinkDocument()
                if child is None:continue
                type_id=eid(instance.GetTypeId())
                matches=[r for r in result['references'] if r.get('element_id')==type_id and r.get('kind')=='RevitLink']
                configured=matches[0].get('source','') if matches else ''
                childkey=self.add_live(child,configured)
                if entry.get('bound_graph'):self.entries[childkey]['bound_graph']=entry['bound_graph']
                if childkey not in entry['children']:entry['children'].append(childkey)
                if not matches:
                    row=dict(id=type_id,element_id=type_id,kind='RevitLink',special='external',td=False,loaded=True)
                    result['references'].append(row);matches=[row]
                for row in matches:
                    row['configured_source']=row.get('source','');row['source']=childkey
                    row['source_evidence']='LIVE_LINK_DOCUMENT_IDENTITY'
                    row['cloud_identity']=dict(self.entries[childkey]['cloud'])
        except f.Cancelled:raise
        except Exception as exc:
            result['issues'].append(issue('LIVE_LINK_TRAVERSAL_FAILED',key,exc,'error'))
        return result
    def add_graph(self,graph,client):
        self.graphs[graph.key]=graph;self.clients[graph.key]=client
        for item in graph.entries:
            self.entries[item['source']]=dict(source=item['source'],mode='PUBLISHED_VERSION',
                name=item['modelName'],graph=graph,item=item,state_basis='EXPLICIT_PUBLISHED_VERSION')
        return graph.host['source']
    def bind_graph(self,live_key,graph,client):
        """A live document may use this graph ONLY for revision-checked links."""
        expected=self.entries[live_key].get('cloud',{}).get('model_guid','')
        if not expected:
            raise SourceError('CLOUD_HOST_IDENTITY_UNAVAILABLE','This open host does not expose a cloud model GUID. Use explicit published-version mode instead of binding by filename.')
        metadata=client.version(graph.project,graph.version)
        actual=f.text(metadata.get('attributes',{}).get('extension',{}).get('data',{}).get('modelGuid','')).lower().strip('{}')
        if actual!=expected.lower().strip('{}'):
            raise SourceError('CLOUD_HOST_IDENTITY_MISMATCH','The selected published host is not the cloud model currently open. No download graph was bound.')
        self.add_graph(graph,client)
        seen=set();pending=[live_key]
        while pending:
            key=pending.pop()
            if key in seen:continue
            seen.add(key);entry=self.entries[key];entry['bound_graph']=graph.key
            pending.extend(entry.get('children',[]))
    def public(self,key):
        entry=self.entries[key]
        fields=('source','mode','name','original_path','cloud','document_version','state_basis',
                'snapshot_path','working_document_path_after','bound_graph','snapshot_verification',
                'snapshot_sha256','snapshot_captured_at','snapshot_warnings','snapshot_requested_path','reference_central')
        result=dict((k,entry[k]) for k in fields if k in entry)
        if entry['mode']=='PUBLISHED_VERSION':
            result.update(project_id=entry['graph'].project,host_version_id=entry['graph'].version,
                          item_id=entry['item']['itemId'],version_id=entry['item'].get('versionId'),
                          publish_status=entry['item'].get('publishStatus'))
        return result
    def relative(self,key):
        entry=self.entries[key]
        if entry['mode']=='PUBLISHED_VERSION':return entry['graph'].relative(entry['item'])
        if not entry.get('is_linked',False):
            return 'Host/'+identifier(key)[:8]+'/'+entry['name']
        original=entry.get('original_path','')
        if f.absolute(original) and not f.cache_source(original):return f.mirror_path(original)
        return 'Sources/Open_Models/'+identifier(key)+'/'+entry['name']
    def authorize_snapshot(self,key):
        """Per-command consent from the initial Transmit confirmation, never settings."""
        entry=self.entries[key]
        if entry.get('is_linked'):
            raise SourceError('LINK_DOCUMENT_READ_ONLY','A linked Document must not be saved by this command.')
        entry['snapshot_authorized']=True

    def snapshot(self,key):
        entry=self.entries[key];doc=entry['document']
        f.check(self.cancelled)
        if bool(getattr(doc,'IsLinked',False)):
            raise SourceError('LINK_DOCUMENT_READ_ONLY','A loaded linked document cannot be saved; acquire its exact original revision instead.')
        if entry.get('snapshot_path'):return entry['snapshot_path']
        if getattr(doc,'IsReadOnly',False) or getattr(doc,'IsModifiable',False):
            raise SourceError('HOST_SNAPSHOT_UNAVAILABLE','Exit the current edit mode or transaction before saving the current host. No old file was substituted.')
        # Always serialize the actual Document. Neither an ADC download nor a
        # published/saved host is a substitute for the requested open state.
        target=os.path.join(self.recovery_root,identifier(key)[:8],entry['name'])
        allowed=entry.get('snapshot_authorized',False)
        if not allowed and self.confirm_snapshot:allowed=bool(self.confirm_snapshot(doc,target))
        if not allowed:
            raise SourceError('HOST_SNAPSHOT_DECLINED','Current-state host snapshot was not authorized; no older host was substituted.')
        f.validate_destination_path(target)
        original=entry.get('original_path','')
        if f.absolute(original) and f.canonical(target)==f.canonical(original):
            raise SourceError('HOST_SOURCE_OVERWRITE_BLOCKED','Snapshot destination must differ from the working source.')
        if f.file_exists(target):
            raise SourceError('HOST_SNAPSHOT_EXISTS','A prior snapshot exists; start a new run. Existing snapshots are never overwritten.')
        folder=os.path.dirname(target)
        if not os.path.isdir(folder):os.makedirs(folder)
        entry['snapshot_requested_path']=target
        entry['snapshot_warnings']=[]
        save=self.DB.SaveAsOptions();ws=None;save_error=None
        try:
            save.OverwriteExistingFile=False;save.MaximumBackups=1
            if getattr(doc,'IsWorkshared',False):
                ws=self.DB.WorksharingSaveAsOptions();ws.SaveAsCentral=True;save.SetWorksharingOptions(ws)
            f.check(self.cancelled)
            doc.SaveAs(target,save)
        except f.Cancelled:raise
        except Exception as exc:
            save_error=exc
        finally:
            entry['working_document_path_after']=f.text(getattr(doc,'PathName',''))
            dispose(ws);dispose(save)
        # Preserve any bytes even after failure: an external SavedAs event may
        # throw after Revit wrote a complete file and moved the working path.
        try:
            if not f.file_exists(target):raise ValueError('No RVT was written at the requested snapshot path.')
            info=model_payload.probe(target)
            if info.get('container')!='CFB':raise ValueError('SaveAs did not produce a native RVT.')
            if getattr(doc,'IsModified',False):
                raise ValueError('The document still has unsaved changes after SaveAs; snapshot currency is not proven.')
            expected=doc_version(doc)
            actual=file_version(self.DB,target)
            if not expected or actual!=expected:
                raise ValueError('Saved RVT revision does not match the current DocumentVersion.')
            entry['snapshot_verification']='REVISION_CHECKED'
            entry['snapshot_sha256']=f.digest(target,self.cancelled)
        except f.Cancelled:raise
        except Exception as exc:
            detail=(type(save_error).__name__+': '+f.text(save_error)+'; ') if save_error else ''
            raise SourceError('HOST_SNAPSHOT_FAILED',detail+f.text(exc)+
                              ' Any generated recovery file is retained at '+target+'.')
        if save_error:
            entry['snapshot_warnings'].append(issue('HOST_SAVE_EVENT_RECOVERED',target,
                'SaveAs raised '+type(save_error).__name__+': '+f.text(save_error)+
                '. The native file matches the clean current document revision and was retained.'))
        entry['snapshot_path']=target;entry['state_basis']='CURRENT_DOCUMENT_SAVEAS'
        entry['document_version']=expected
        entry['snapshot_captured_at']=time.strftime('%Y-%m-%dT%H:%M:%S')
        return target
    def temp_root(self):
        if not self._temp:self._temp=tempfile.mkdtemp(prefix='ET_APS_')
        return self._temp
    def cloud_file(self,key,pulse=None):
        entry=self.entries[key]
        if entry.get('download_path'):return entry['download_path']
        graph=entry['graph'];client=self.clients[graph.key];item=entry['item']
        if client is None:raise SourceError('CLOUD_SIGN_IN_REQUIRED','Sign in to the registered Autodesk app to acquire this published version.')
        if time.time()-graph.fetched_at>45*60:
            graph.refresh_urls(client.linked_files(graph.project,graph.version))
        folder=os.path.join(self.temp_root(),identifier(key))
        if not os.path.isdir(folder):os.makedirs(folder)
        path=os.path.join(folder,entry['name'])
        client.download(item['signedUrl'],path,item['size'],pulse)
        payload=model_payload.prepare(path,os.path.join(folder,'native'),expected_name=entry['name'],cancelled=self.cancelled)
        if payload.get('host_member'):
            raise SourceError('CLOUD_PAYLOAD_UNEXPECTED','The version-specific RCM download returned a composite instead of one native RVT.')
        entry['download_path']=payload['path'];return payload['path']
    def match_live_cloud(self,key):
        entry=self.entries[key];model_guid=entry.get('cloud',{}).get('model_guid')
        graph=self.graphs.get(entry.get('bound_graph'))
        if not graph or not model_guid:return None
        client=self.clients[graph.key];matches=[]
        for item in graph.entries:
            if 'model_guid' not in item:
                if item.get('versionId'):metadata=client.version(graph.project,item['versionId'])
                else:metadata=client.item_tip(graph.project,item['itemId'])
                item['model_guid']=f.text(metadata.get('attributes',{}).get('extension',{}).get('data',{}).get('modelGuid','')).lower()
            if item['model_guid']==model_guid:matches.append(item)
        if len(matches)>1:raise SourceError('CLOUD_IDENTITY_AMBIGUOUS','The chosen published graph contains multiple items for the loaded cloud model identity.')
        return matches[0]['source'] if matches else None
    def close(self):
        self._documents[:]=[];self.entries.clear();self.graphs.clear();self.clients.clear()
        if self._temp:f.remove_tree_retry(self._temp);self._temp=None


class SessionBackend(Backend):
    def __init__(self,DB,application,package_root,registry,cancelled=None):
        Backend.__init__(self,DB,application,package_root,cancelled);self.registry=registry
    def is_virtual_source(self,source):return self.registry.owns(source)
    def source_context(self,source):return self.registry.public(source) if self.registry.owns(source) else {}
    def source_relative(self,source):return self.registry.relative(source) if self.registry.owns(source) else f.mirror_path(source)
    def additional_sources(self,source):
        entry=self.registry.get(source)
        if entry and entry['mode']=='PUBLISHED_VERSION' and entry['item'].get('is_host'):
            return [r['source'] for r in entry['graph'].entries if r['source']!=source]
        return []
    def skip_dependency_copy(self,source,options):
        entry=self.registry.get(source)
        if not options.get('skip_cloud_links') or not entry or not entry.get('is_linked'):return None
        original=entry.get('original_path','')
        if (getattr(entry['document'],'IsModelInCloud',False) or entry.get('cloud') or
                f.is_desktop_connector_path(original) or '://' in original):
            return 'ACC linked RVT copying was skipped by the selected option. Its existing reference is retained; loaded-link materials can still be collected.'
        return None

    def host_copy_first(self,source):
        entry=self.registry.get(source)
        return bool(entry and entry['mode']=='LIVE_DOCUMENT' and not entry.get('is_linked'))
    def inventory_before_copy(self,source,options):
        entry=self.registry.get(source)
        return copy.deepcopy(self.registry.inventory(source)) if entry and entry['mode']=='LIVE_DOCUMENT' else None
    def resolve_acquired_source(self,source,owner=''):
        if self.registry.owns(source):return source
        entry=self.registry.get(owner)
        if entry and entry['mode']=='PUBLISHED_VERSION':
            if f.absolute(source) and self.staging_root and f.within(source,self.staging_root):
                member=entry['graph'].match_staged_name(ntpath.basename(source))
                if member:return member['source']
                raise SourceError('CLOUD_LINK_OMITTED','The referenced RVT is absent from the selected published host download inventory. Check link permissions and published-version identity; no same-named replacement was chosen.')
        return Backend.resolve_acquired_source(self,source,owner)
    def acquire_file(self,source,target,owner='',cancelled=None,pulse=None):
        entry=self.registry.get(source)
        if not entry:return Backend.acquire_file(self,source,target,owner,cancelled,pulse)
        self.guard(target)
        if entry['mode']=='PUBLISHED_VERSION':
            physical=self.registry.cloud_file(source,pulse)
            meta=f.copy_file(physical,target,cancelled,pulse)
            meta.update(source_stability='AUTHENTICATED_SNAPSHOT',copy_method='APS_SIGNED_DOWNLOAD',
                        source_context=self.registry.public(source))
            return meta
        if entry.get('is_linked',False):
            expected=entry.get('document_version')
            if not expected:raise SourceError('LIVE_LINK_VERSION_UNVERIFIED','Loaded link revision was not exposed; refusing an unverified substitute.')
            cloud_key=self.registry.match_live_cloud(source)
            if cloud_key:physical=self.registry.cloud_file(cloud_key,pulse)
            else:
                physical=f.resolve_source(entry.get('original_path',''))
                if not physical or f.cache_source(physical):
                    raise SourceError('LIVE_CLOUD_LINK_UNRESOLVED','Loaded link identity was captured, but no exact readable revision was available. Associate a published host version through Autodesk sign-in; downloads must match the loaded link revision.')
            # Materialize first, then validate the actual downloaded native RVT.
            meta=self.payloads.copy(physical,target,owner,cancelled,pulse)
            try:
                actual=file_version(self.DB,target)
                if expected!=actual:raise SourceError('LIVE_LINK_VERSION_MISMATCH','The acquired linked RVT is not the edition loaded by the open host. No version was silently substituted.')
            except Exception:
                if f.file_exists(target):os.remove(target)
                raise
            entry['state_basis']='LOADED_LINK_REVISION_CHECKED'
        else:
            physical=self.registry.snapshot(source)
            meta=self.payloads.copy(physical,target,owner,cancelled,pulse)
            # Immutable native snapshot is independent of all repath/cleanup work.
            protected=os.path.join(self.root,'HostSnapshot',identifier(source)[:8],entry['name'])
            meta.update(is_live_host=True,host_snapshot_sha256=entry['snapshot_sha256'],
                        host_snapshot_status='UNAVAILABLE')
            try:
                f.copy_file(target,protected,cancelled,pulse)
                if f.digest(protected,cancelled)!=entry['snapshot_sha256']:
                    raise IOError('The protected copy did not match the current-state snapshot.')
                meta.update(host_snapshot_path=protected,host_snapshot_status='VERIFIED')
            except f.Cancelled:raise
            except Exception as exc:
                # Do not mislabel the already-verified primary copy as missing
                # when only its redundant backup could not be written.
                entry['snapshot_warnings'].append(issue('HOST_SNAPSHOT_BACKUP_FAILED',protected,exc,'error'))
        meta.update(source_stability='SESSION_SNAPSHOT',source_context=self.registry.public(source))
        return meta
    def scan(self,source,stage,options):
        before=self.inventory_before_copy(source,options)
        if before is not None:return before
        return Backend.scan(self,source,stage,options)
