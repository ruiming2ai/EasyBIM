# -*- coding: utf-8 -*-
"""Per-command live documents and explicit published-cloud sources.

Document handles never enter a manifest. Discovery never opens, saves, reloads,
closes or modifies the user's document. A primary-document SaveAs requires a
runtime callback granting consent; the standard UI now uses saved-state-only
cache acquisition instead and never invokes that legacy SaveAs path.
"""
from __future__ import unicode_literals
import copy
import os
import ntpath
import tempfile
import uuid
import time
from . import files as f, model_payload, cache_sources
from .revit import Backend, eid, dispose
from .engine import issue
from .cloud_sources import identifier, validate_name


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
    def __init__(self,DB,application,recovery_root,cancelled=None,collect_plugins=True,
                 saved_state_only=True,cache_roots=None):
        self.DB=DB;self.app=application;self.recovery_root=recovery_root;self.cancelled=cancelled
        self.entries={};self._documents=[];self.graphs={};self.clients={};self._temp=None
        self.confirm_snapshot=None;self.collect_plugins=collect_plugins
        self.saved_state_only=bool(saved_state_only)
        self.cache_roots=cache_roots
        self._cache_store=None
        self._authorized_snapshots=set()
        self.scanner=Backend(DB,application,recovery_root,cancelled)
    def get(self,key):return self.entries.get(key)
    def owns(self,key):return key in self.entries
    def add_cached_reference(self,row):
        """Register an identified saved link without requiring a live Document."""
        identity=cache_sources.reference_identity(row)
        if not identity:return None
        for key,entry in self.entries.items():
            if entry['mode']=='CACHED_CLOUD_REFERENCE' and cache_sources.same_identity(identity,entry.get('cloud',{})):
                return key
        name=f.text(row.get('link_name') or '')
        if not name:
            for field in ('configured_source','in_session_path','saved_path','source'):
                value=f.text(row.get(field) or '')
                if value.lower().endswith('.rvt'):
                    name=value.replace('\\','/').rsplit('/',1)[-1];break
        if name and not name.lower().endswith('.rvt'):name+='.rvt'
        try:name=validate_name(name)
        except ValueError:
            raise SourceError('CACHE_LINK_NAME_UNAVAILABLE','Identified ACC link has no valid model filename; element '+f.text(row.get('element_id',''))+'.')
        key='cache://'+identifier(identity['region']+'/'+identity['project_guid']+'/'+identity['model_guid'])+'/'+name
        self.entries[key]=dict(source=key,mode='CACHED_CLOUD_REFERENCE',name=name,
            cloud=identity,document_version=None,is_linked=True,is_modified=False,
            original_path=f.text(row.get('source') or row.get('in_session_path') or ''),
            state_basis='SAVED_CLOUD_REFERENCE',children=[],snapshot_path=None)
        return key
    def add_live(self,doc,configured_source=None):
        for old,key in self._documents:
            if old is doc or old==doc:return key
        original=f.text(configured_source or getattr(doc,'PathName','') or '')
        name=ntpath.basename(original) if f.absolute(original) and not f.cache_source(original) else f.text(doc.Title)
        if not name.lower().endswith('.rvt'):name+='.rvt'
        if any(c in name for c in '<>:"/\\|?*'):
            raise SourceError('INVALID_OPEN_MODEL_NAME','The open document title is not a valid RVT filename.')
        key='open://'+uuid.uuid4().hex+'/'+name
        entry=dict(source=key,mode='LIVE_DOCUMENT',document=doc,name=name,original_path=original,
                   cloud=cloud_identity(doc),document_version=doc_version(doc),
                   state_basis='LIVE_INVENTORY_ONLY',children=[],snapshot_path=None,is_linked=bool(getattr(doc,'IsLinked',False)),
                   is_modified=bool(getattr(doc,'IsModified',False)))
        self.entries[key]=entry;self._documents.append((doc,key))
        result=dict(references=[],issues=[],version=f.text(getattr(self.app,'VersionNumber','')),
                    opened_in_revit=True,inspection_status='LIVE_DOCUMENT',source_mode='LIVE_DOCUMENT')
        entry['inventory']=result
        central=''
        if getattr(doc,'IsWorkshared',False) and not getattr(doc,'IsModelInCloud',False):
            try:central=self.scanner.visible(doc.GetWorksharingCentralModelPath())
            except Exception:pass
        info=dict(central=central,workshared=bool(getattr(doc,'IsWorkshared',False)))
        entry['plugin_base']=central or original;entry['plugins_scanned']=False
        try:
            self.scanner.scan_open(doc,original or key,info,result)
        except f.Cancelled:raise
        except ImportError:
            # An installation missing an owned module must not silently claim
            # spreadsheet coverage. General reference inventory still survives.
            result['issues'].append(issue('PLUGIN_SOURCE_COVERAGE',key,'Plugin discovery module unavailable.'))
        except Exception as exc:
            result['issues'].append(issue('LIVE_INVENTORY_FAILED',key,exc,'error'))
            result['open_failed']=True
        try:
            for instance in self.scanner.elements(doc,'RevitLinkInstance'):
                f.check(self.cancelled)
                child=instance.GetLinkDocument()
                if child is None:continue
                type_id=eid(instance.GetTypeId())
                matches=[r for r in result['references'] if r.get('element_id')==type_id and r.get('kind')=='RevitLink']
                configured=matches[0].get('source','') if matches else ''
                childkey=self.add_live(child,configured)
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
        return key
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
                'snapshot_path','working_document_path_after','bound_graph','snapshot_attempt_path',
                'snapshot_sha256','snapshot_validation','snapshot_error','snapshot_error_type',
                'snapshot_started_at','snapshot_completed_at','saved_document_version',
                'working_location_changed','snapshot_authorization','is_modified','cache_metadata',
                'cache_evidence','unsaved_edits_excluded','saved_state_only','inventory_basis')
        result=dict((k,entry[k]) for k in fields if k in entry)
        if entry['mode']=='PUBLISHED_VERSION':
            result.update(project_id=entry['graph'].project,host_version_id=entry['graph'].version,
                          item_id=entry['item']['itemId'],version_id=entry['item'].get('versionId'),
                          publish_status=entry['item'].get('publishStatus'))
        return result
    def relative(self,key):
        entry=self.entries[key]
        if entry['mode']=='PUBLISHED_VERSION':return entry['graph'].relative(entry['item'])
        if not entry.get('is_linked',False):return entry['name']
        original=entry.get('original_path','')
        if f.absolute(original) and not f.cache_source(original):return f.mirror_path(original)
        return 'Sources/Open_Models/'+identifier(key)+'/'+entry['name']
    def authorize_snapshots(self,keys):
        """Authorize ONLY the selected primary documents for this command.

        UI calls this after the up-front save confirmation, before the batch.
        Authorization is not saved in settings or inferred from an old report.
        """
        keys=list(keys)
        for key in keys:
            entry=self.entries.get(key)
            if not entry or entry.get('mode')!='LIVE_DOCUMENT' or entry.get('is_linked'):
                raise SourceError('INVALID_SNAPSHOT_AUTHORIZATION','Only selected open primary documents can be authorized for host SaveAs.')
        self._authorized_snapshots.update(keys)
    def snapshot(self,key,pulse=None):
        if self.saved_state_only:
            return self._snapshot_saved_state(key,pulse)
        return self._snapshot_live_state(key)

    def _snapshot_saved_state(self,key,pulse=None):
        """Copy a saved edition, NEVER save or relocate the open Document."""
        entry=self.entries[key];doc=entry.get('document')
        if entry.get('snapshot_path'):
            path=entry['snapshot_path']
            if f.digest(path,self.cancelled)!=entry['snapshot_sha256']:
                raise SourceError('CACHE_SNAPSHOT_CHANGED','The retained saved-state snapshot changed. It will not be reused.')
            return path
        before=doc_version(doc) if doc is not None else None
        if before!=entry.get('document_version'):
            raise SourceError('OPEN_DOCUMENT_VERSION_CHANGED','The open document revision changed after discovery. Start a fresh transmittal; no save was attempted.')
        entry['saved_state_only']=True
        entry['is_modified']=bool(getattr(doc,'IsModified',False))
        entry['unsaved_edits_excluded']=True
        if self._cache_store is None:
            self._cache_store=cache_sources.Store(self.DB,self.app,
                os.path.join(self.temp_root(),'cache'),roots=self.cache_roots,cancelled=self.cancelled)
        original=f.text(getattr(doc,'PathName','') or '')
        try:
            if entry.get('cloud') or bool(getattr(doc,'IsModelInCloud',False)):
                captured=self._cache_store.capture(entry,pulse)
                path=captured['path'];metadata=captured['metadata']
                entry['cache_metadata']=metadata
                entry['cache_evidence']=metadata.get('cache_evidence',{})
                entry['state_basis']='VERIFIED_LOCAL_CACHE_SAVED_STATE'
            else:
                physical=entry.get('original_path','')
                # A detached model has no PathName. Session/journal source
                # tracking can only supplement it when the saved revision can
                # be checked; it must not become a display-name file search.
                tracked=False
                if not f.absolute(physical):
                    from . import source_tracker
                    physical=source_tracker.source_for_document(doc,application=self.app)
                    tracked=True
                if not f.absolute(physical) or f.cache_source(physical) or f.is_desktop_connector_path(physical):
                    raise SourceError('SAVED_HOST_SOURCE_UNAVAILABLE','No exact saved local RVT was identified. No Save/SaveAs or published-version fallback was attempted.')
                folder=os.path.join(self.temp_root(),'saved-'+identifier(key))
                if not os.path.isdir(folder):os.makedirs(folder)
                path=os.path.join(folder,'snapshot.rvt')
                metadata=f.copy_file(physical,path,self.cancelled,pulse)
                if model_payload.probe(path).get('container')!='CFB':
                    raise SourceError('SAVED_HOST_NOT_NATIVE','The local source is not a native RVT.')
                info=self._cache_store.read_info(path)
                actual=cache_sources.version(info.get('version'));expected=cache_sources.version(before)
                if not actual or (actual!=expected and (entry.get('is_linked') or not entry['is_modified'] or tracked)):
                    raise SourceError('SAVED_HOST_VERSION_MISMATCH','The saved local file does not match the identified open model revision. No file was substituted.')
                metadata.update(cache_document_version=actual,copy_method='VERIFIED_LOCAL_FILE',
                                revision_check='MATCHES_LOADED_SAVED_VERSION' if actual==expected else 'SAVED_FILE_ONLY_UNSAVED_EXCLUDED',
                                unsaved_edits_excluded=entry['is_modified'])
                entry['state_basis']='VERIFIED_LOCAL_FILE_SAVED_STATE'
                entry['cache_metadata']=metadata
            if before!=doc_version(doc) or original!=f.text(getattr(doc,'PathName','') or ''):
                raise SourceError('OPEN_DOCUMENT_VERSION_CHANGED','The open document changed during acquisition. No output was accepted and no save was attempted.')
            entry['snapshot_path']=path
            entry['snapshot_sha256']=metadata['sha256']
            entry['saved_document_version']=metadata['cache_document_version']
            entry['snapshot_validation']='NATIVE_RVT_METADATA_AND_COPY_VERIFIED'
            entry['working_document_path_after']=original;entry['working_location_changed']=False
            entry['snapshot_completed_at']=time.time()
            return path
        except cache_sources.CacheError as exc:
            entry['cache_evidence']=exc.evidence
            raise SourceError(exc.code,f.text(exc))

    def _snapshot_live_state(self,key):
        entry=self.entries[key];doc=entry['document']
        if bool(getattr(doc,'IsLinked',False)):
            raise SourceError('LINK_DOCUMENT_READ_ONLY','A loaded linked document cannot be saved; acquire its exact original revision instead.')
        if entry.get('snapshot_path'):
            saved_version=entry.get('saved_document_version');current_version=doc_version(doc)
            if bool(getattr(doc,'IsModified',False)) or (saved_version and current_version and saved_version!=current_version):
                raise SourceError('HOST_SNAPSHOT_STALE','The open document changed after the captured snapshot. The old snapshot was retained but not reused as current state.')
            path=entry['snapshot_path']
            if not f.file_exists(path) or f.digest(path,self.cancelled)!=entry.get('snapshot_sha256'):
                raise SourceError('HOST_SNAPSHOT_CHANGED','The retained current-state snapshot changed after capture. It was not replaced by an older or published host.')
            return path
        if entry.get('snapshot_error'):
            raise SourceError('HOST_SNAPSHOT_FAILED',entry['snapshot_error'])
        f.check(self.cancelled)
        if getattr(doc,'IsReadOnly',False) or getattr(doc,'IsModifiable',False):
            raise SourceError('HOST_SNAPSHOT_UNAVAILABLE','Finish the current edit/transaction before saving a host copy; read-only documents cannot be serialized.')
        # Always serialize the Document. Even a matching saved DocumentVersion
        # is not used as an implicit substitute for the requested live state.
        target=os.path.join(self.recovery_root,identifier(key),entry['name'])
        if key not in self._authorized_snapshots:
            if not self.confirm_snapshot or not self.confirm_snapshot(doc,target):
                raise SourceError('HOST_SNAPSHOT_DECLINED','Current-state host snapshot was not authorized. No older or published host was substituted.')
            entry['snapshot_authorization']='PER_DOCUMENT_CONFIRMATION'
        else:entry['snapshot_authorization']='UPFRONT_BATCH_CONFIRMATION'
        f.validate_destination_path(target)
        if f.file_exists(target):
            raise SourceError('HOST_SNAPSHOT_DESTINATION_EXISTS','A working snapshot already exists at the new destination; it will not be overwritten.')
        folder=os.path.dirname(target)
        if not os.path.isdir(folder):os.makedirs(folder)
        save=ws=None
        entry['snapshot_attempt_path']=target
        entry['snapshot_started_at']=time.time()
        before=f.text(getattr(doc,'PathName','') or '')
        try:
            save=self.DB.SaveAsOptions()
            save.OverwriteExistingFile=False;save.MaximumBackups=1
            if getattr(doc,'IsWorkshared',False):
                ws=self.DB.WorksharingSaveAsOptions();ws.SaveAsCentral=True;save.SetWorksharingOptions(ws)
            # No Rename flag: it was removed from Revit and did not preserve
            # in-memory identity. Never save back to the old path to "restore" it.
            doc.SaveAs(target,save)
            entry['working_document_path_after']=f.text(getattr(doc,'PathName','') or '')
            entry['working_location_changed']=f.canonical(before)!=f.canonical(entry['working_document_path_after'])
            info=model_payload.probe(target)
            if info.get('container')!='CFB':
                raise ValueError('SaveAs did not produce a native RVT file.')
            # Check that this Revit API can read the emitted native file. An API
            # save is not allowed to silently deliver an archive/login page.
            native=self.DB.BasicFileInfo.Extract(target)
            try:
                version=f.text(native.Format)
                if version!=f.text(self.app.VersionNumber):
                    raise ValueError('Saved host format does not match the running Revit version.')
            finally:dispose(native)
            if bool(getattr(doc,'IsModified',False)):
                raise ValueError('A save-event callback modified the open document after SaveAs. The retained copy may not include those subsequent changes.')
            entry['saved_document_version']=doc_version(doc)
            entry['snapshot_sha256']=f.digest(target,self.cancelled)
            entry['snapshot_validation']='NATIVE_RVT_METADATA_READABLE'
            entry['snapshot_path']=target;entry['state_basis']='CURRENT_DOCUMENT_SAVEAS'
            entry['snapshot_completed_at']=time.time()
        except f.Cancelled:
            entry['snapshot_error']='Current-state snapshot operation was cancelled; any working file has been retained.'
            raise
        except Exception as exc:
            # Keep target even when a post-save add-in failed: it can be the
            # user's new working file. Do not auto-retry a state-changing SaveAs.
            entry['working_document_path_after']=f.text(getattr(doc,'PathName','') or '')
            entry['working_location_changed']=f.canonical(before)!=f.canonical(entry['working_document_path_after'])
            entry['snapshot_error_type']=type(exc).__name__
            entry['snapshot_error']='Current-state SaveAs/validation failed: '+f.text(exc)+'. Any working file remains at '+target
            raise SourceError('HOST_SNAPSHOT_FAILED',entry['snapshot_error'])
        finally:dispose(ws);dispose(save)
        return target
    def temp_root(self):
        if not self._temp:self._temp=tempfile.mkdtemp(prefix='ET_Sources_')
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
        self._documents[:]=[];self.entries.clear();self.graphs.clear();self.clients.clear();self._authorized_snapshots.clear()
        if self._temp:f.remove_tree_retry(self._temp);self._temp=None


class SessionBackend(Backend):
    def __init__(self,DB,application,package_root,registry,cancelled=None):
        Backend.__init__(self,DB,application,package_root,cancelled);self.registry=registry
    def open_copy(self,path,discard=False):
        document=Backend.open_copy(self,path,discard)
        # Do not close a returned original here: even an unexpected Revit result
        # must not let a caller's processing/finally block touch a working model.
        for original, key in self.registry._documents:
            if document is original or document==original:
                raise SourceError('ORIGINAL_DOCUMENT_GUARD',
                    'Revit returned a working document for an export-copy open. No SaveAs, repath or Close was performed on it.')
        if bool(getattr(document,'IsLinked',False)):
            raise SourceError('ORIGINAL_DOCUMENT_GUARD','A linked working document cannot be processed as an export copy.')
        return document

    def is_virtual_source(self,source):return self.registry.owns(source)
    def source_context(self,source):return self.registry.public(source) if self.registry.owns(source) else {}
    def source_relative(self,source):return self.registry.relative(source) if self.registry.owns(source) else f.mirror_path(source)
    def additional_sources(self,source):
        entry=self.registry.get(source)
        if entry and entry['mode']=='PUBLISHED_VERSION' and entry['item'].get('is_host'):
            return [r['source'] for r in entry['graph'].entries if r['source']!=source]
        return []
    def skip_dependency(self,source,owner,options):
        if self.registry.saved_state_only and self.registry.owns(source):
            return False  # Cache reads are not published/cloud downloads.
        if not options.get('skip_cloud_links',False):return False
        entry=self.registry.get(source)
        if entry:
            original=f.text(entry.get('original_path',''))
            remote_path=f.is_desktop_connector_path(original) or original.lower().startswith(
                ('autodesk docs://','bim 360://','acc://','cld://','cld:'))
            return bool(entry.get('is_linked') and (entry.get('cloud') or remote_path or
                        getattr(entry.get('document'),'IsModelInCloud',False)))
        value=f.text(source or '')
        return value.lower().endswith('.rvt') and (f.is_desktop_connector_path(value) or
            value.lower().startswith(('autodesk docs://','bim 360://','acc://','cld://','cld:')))
    def inventory_before_copy(self,source,options):
        if self.registry.saved_state_only:
            return None  # Keep the host before discovering optional materials.
        entry=self.registry.get(source)
        return copy.deepcopy(entry['inventory']) if entry and entry['mode']=='LIVE_DOCUMENT' else None
    def inventory_after_copy(self,source,options):
        # Optional vendor inspection cannot prevent the initial host copy.
        entry=self.registry.get(source)
        if not entry or entry['mode']!='LIVE_DOCUMENT' or not self.registry.collect_plugins or (self.registry.saved_state_only and not self._can_use_live_inventory(entry)):
            return None
        if entry.get('plugins_scanned'):
            return copy.deepcopy(entry.get('plugin_inventory'))
        result=dict(references=[],issues=[],plugin_coverage=[])
        entry['plugins_scanned']=True
        try:
            self.registry.scanner.scan_plugins(entry['document'],entry.get('plugin_base',''),result)
        except f.Cancelled:raise
        except Exception as exc:
            result['issues'].append(issue('PLUGIN_SOURCE_COVERAGE',source,
                'Optional workbook discovery did not complete ('+type(exc).__name__+'). Host snapshot was already retained.'))
        entry['plugin_inventory']=copy.deepcopy(result)
        return result
    def resolve_acquired_source(self,source,owner=''):
        if self.registry.owns(source):return source
        entry=self.registry.get(owner)
        if self.registry.saved_state_only and entry and entry['mode'] in ('LIVE_DOCUMENT','CACHED_CLOUD_REFERENCE'):
            value=f.text(source or '')
            if value.lower().endswith('.rvt') and (f.is_desktop_connector_path(value) or '://' in value):
                raise SourceError('CACHE_LINK_IDENTITY_UNRESOLVED','This cloud link was not matched to an identified loaded document/cache. No published model was downloaded instead.')
        if entry and entry['mode']=='PUBLISHED_VERSION':
            if f.absolute(source) and self.staging_root and f.within(source,self.staging_root):
                member=entry['graph'].match_staged_name(ntpath.basename(source))
                if member:return member['source']
                raise SourceError('CLOUD_LINK_OMITTED','The referenced RVT is absent from the selected published host download inventory. Check link permissions and published-version identity; no same-named replacement was chosen.')
        return Backend.resolve_acquired_source(self,source,owner)
    def acquired_identity(self,source,metadata):
        """Merge only the same proven saved edition, never equal unrelated bytes."""
        from . import layout
        entry=self.registry.get(source)
        if not entry:return f.canonical(source)
        if entry['mode']=='PUBLISHED_VERSION':
            item=entry['item']
            return 'published:'+f.text(entry['graph'].project)+'/'+item['itemId']+'/'+f.text(item.get('versionId',''))
        cloud=cache_sources.reference_identity(dict(cloud_identity=entry.get('cloud') or {}))
        if cloud and entry.get('saved_document_version'):
            import json
            return 'cache:'+json.dumps(cloud,sort_keys=True)+'/'+json.dumps(entry['saved_document_version'],sort_keys=True)
        original=entry.get('original_path','')
        if self.registry.saved_state_only and f.absolute(original) and not f.cache_source(original):
            return f.canonical(original)
        return source
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
        if self.registry.saved_state_only:
            physical=self.registry.snapshot(source,pulse)
            meta=f.copy_file(physical,target,cancelled,pulse)
            expected=entry['snapshot_sha256']
            if meta.get('sha256')!=expected:
                raise SourceError('CACHE_SNAPSHOT_COPY_MISMATCH','The package copy did not match the validated saved-state snapshot.')
            if not entry.get('is_linked'):
                meta.update(saved_state_sha256=expected,saved_state_integrity='VERIFIED')
            meta.update(copy_method=entry['cache_metadata']['copy_method'],source_stability='SESSION_SNAPSHOT',
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
            expected=entry['snapshot_sha256']
            if meta.get('sha256')!=expected:
                raise SourceError('HOST_SNAPSHOT_COPY_MISMATCH','The host copy did not match the captured current state; the retained working snapshot was not changed.')
            meta.update(current_state_sha256=expected,
                        current_state_integrity='VERIFIED')
        meta.update(source_stability='SESSION_SNAPSHOT',source_context=self.registry.public(source))
        return meta
    def _can_use_live_inventory(self,entry):
        return (entry.get('document') is not None and not entry.get('is_modified') and
                entry.get('cache_metadata',{}).get('revision_check')=='MATCHES_LOADED_SAVED_VERSION')

    def scan(self,source,stage,options):
        entry=self.registry.get(source)
        if self.registry.saved_state_only and entry and entry['mode'] in ('LIVE_DOCUMENT','CACHED_CLOUD_REFERENCE'):
            if self._can_use_live_inventory(entry):
                result=copy.deepcopy(entry['inventory'])
                result['opened_in_revit']=False
                result['inspection_status']='LOADED_SAVED_REVISION_INVENTORY'
                entry['inventory_basis']='UNMODIFIED_LOADED_DOCUMENT_MATCHED_TO_SAVED_REVISION'
            else:
                # Unsaved reference additions/deletions are not authoritative
                # for the exported saved file. Inspect only its disposable copy.
                result=Backend.scan(self,source,stage,options)
                entry['inventory_basis']='SAVED_SNAPSHOT_INSPECTION'
                if entry.get('is_modified'):
                    result['issues'].append(issue('UNSAVED_EDITS_EXCLUDED',source,
                        'The exported host is the locally saved file/cache, not the unsaved open state. No Save, Sync or Publish was performed.'))
            if options.get('include',{}).get('revit',True):self._bind_saved_links(entry,result)
            metadata=entry.get('cache_metadata',{})
            revision=metadata.get('revision_check','')
            if revision in ('SAVED_CACHE_DIFFERS_FROM_LOADED','SAVED_CACHE_NO_LOADED_REVISION'):
                result['issues'].append(issue(revision,source,
                    'Exported saved cache revision '+f.text(metadata.get('cache_document_version'))+
                    '; loaded revision '+f.text(metadata.get('loaded_document_version'))+
                    '. Dependencies were inspected from the saved copy.',
                    'warning' if revision=='SAVED_CACHE_DIFFERS_FROM_LOADED' else 'info'))
            result['source_mode']='SAVED_LOCAL_STATE'
            return result
        before=self.inventory_before_copy(source,options)
        if before is not None:return before
        return Backend.scan(self,source,stage,options)

    def _bind_saved_links(self,entry,result):
        # Bind snapshot references only with persisted cloud identity evidence.
        by_id=dict((r.get('element_id'),r) for r in entry.get('inventory',{}).get('references',[]) if r.get('kind')=='RevitLink')
        for row in result.get('references',[]):
            if row.get('kind')!='RevitLink':continue
            if self.registry.owns(row.get('source','')):continue
            identity=cache_sources.reference_identity(row)
            if identity:
                row['cloud_identity']=identity
                try:
                    old=by_id.get(row.get('element_id'),{})
                    child=self.registry.get(old.get('source',''))
                    key=(old['source'] if child and cache_sources.same_identity(identity,child.get('cloud',{}))
                         else self.registry.add_cached_reference(row))
                except SourceError as exc:
                    diagnostic=issue(exc.code,entry.get('source',''),exc,'error')
                    diagnostic.update(element_id=row.get('element_id'),cloud_identity=identity)
                    result.setdefault('issues',[]).append(diagnostic)
                    row['resolution_failed']=True
                    continue
                row['configured_source']=row.get('source','')
                row['source']=key;row['source_evidence']='SAVED_CLOUD_IDENTITY_CACHE_SOURCE'
                continue
            old=by_id.get(row.get('element_id'),{})
            child=self.registry.get(old.get('source',''))
            if not child:continue
            proven=(not child.get('cloud') and f.absolute(row.get('source','')) and
                    f.canonical(row['source'])==f.canonical(child.get('original_path','')))
            if proven:
                row['configured_source']=row.get('source','')
                row['source']=old['source'];row['source_evidence']='SAVED_LINK_IDENTITY_MATCHED_TO_LOADED_DOCUMENT'
