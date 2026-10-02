# -*- coding: utf-8 -*-
"""Revit API boundary. All document writes are restricted to package copies."""
from __future__ import unicode_literals
import os
import ntpath
import shutil
from . import files as f
from .pathnames import relative as relative_path
from .engine import issue
from . import model_payload, cache_sources, performance


def eid(value):
    try: return f.text(value.Value)
    except AttributeError: return f.text(value.IntegerValue)


def dispose(value):
    if value is not None:
        try: value.Dispose()
        except Exception: pass


def load_intent(status):
    if status == 'Unloaded': return False
    if status in ('Loaded','NotFound','NotApplicable'): return True
    return None


def unconfigured_keynote(row):
    if row.get('kind') != 'KeynoteTable': return False
    fields=('source','saved_path','saved_absolute_path','native_source','in_session_path')
    if any(f.text(row.get(key) or '').strip() for key in fields): return False
    metadata=dict((f.text(k).lower(),v) for k,v in row.get('resource_information',{}).items())
    if any(f.text(metadata.get(key) or '').strip() for key in
           ('path','absolutepath','fullpath','filepath','savedpath')): return False
    # Only the built-in empty configuration has this explicit all-zero identity.
    return f.text(metadata.get('modelidentity','')).strip('{}').lower()=='00000000-0000-0000-0000-000000000000'


def package_load_state(row):
    return row.get('package_loaded',row.get('loaded'))


def metadata_target_allowed(row):
    if not row.get('target') or row.get('skip_repath'):
        return False
    if row.get('repath') in ('FAILED','ROLLED_BACK','ROLLBACK_FAILED',
                            'REMOVED_BY_CLEANUP','MANUAL_REPAIR_REQUIRED',
                            'PLUGIN_RECONNECT_REQUIRED'):
        return False
    if (cache_sources.reference_identity(row) or
            (row.get('special')=='external' and not row.get('td') and row.get('kind')=='RevitLink')):
        return row.get('repath') in ('API_LOCAL_LINK','API_LOCAL_LINK_RELATIVE')
    return True


class Backend(object):
    mark_transmitted_rows_supported = True
    independent_host_supported = True
    partial_repath_supported = True

    def __init__(self, DB, application, package_root, cancelled=None):
        self.DB, self.app, self.root, self.cancelled = DB, application, package_root, cancelled
        self.staging_root = None
        self.payloads = None

    def set_staging_root(self, path):
        self.staging_root = path
        self.payloads = model_payload.Store(os.path.join(path, 'acquired'))

    def defer_file_copy(self, source, owner=''):
        return (not f.is_desktop_connector_path(source) and not f.cache_source(source)
                and f.file_exists(source) and not (self.payloads and
                self.payloads.contexts.get(f.canonical(owner))))

    def can_deliver_direct(self, record, rows, options):
        # Metadata-only hosts do not need physical siblings at their working path.
        if self.requires_final_host_open(record,rows,options):return True
        if options.get('cleanup') or options.get('upgrade'): return False
        if not options.get('repath'): return True
        return self.can_finish_metadata_copy(rows,options)

    def can_finish_metadata_copy(self, rows, options):
        """Native references can be repathed on the saved/cache bytes alone."""
        if not options.get('repath') or options.get('cleanup') or options.get('upgrade'):
            return False
        if options.get('simple_repath'):
            return True
        return not any(not r.get('skip_repath') and
                       (r.get('special')=='image' or
                        (r.get('kind')=='RevitLink' and
                         (not r.get('td') or cache_sources.reference_identity(r))))
                       for r in rows)

    def requires_final_host_open(self, record, rows, options):
        """Document API repairs wait until dependencies reach final locations."""
        if record.get('is_primary_host') and options.get('independent_host'):
            return bool(record.get('is_workshared') or options.get('repath') or
                        options.get('cleanup') or options.get('upgrade'))
        if not record.get('is_primary_host') or not options.get('repath'): return False
        if self.can_finish_metadata_copy(rows,options):return False
        return any(r.get('target') and not r.get('skip_repath') and
                   (r.get('special')=='image' or (r.get('kind')=='RevitLink' and
                   (not r.get('td') or cache_sources.reference_identity(r)))) for r in rows)

    def acquire_file(self, source, target, owner='', cancelled=None, pulse=None):
        self.guard(target)
        return self.payloads.copy(source, target, owner, cancelled, pulse)

    def resolve_acquired_source(self, source, owner=''):
        return self.payloads.resolve(source, owner) if self.payloads else source

    def guard(self, path):
        allowed = f.within(path, self.root)
        if not allowed and self.staging_root:
            allowed = f.within(path, self.staging_root)
        if not allowed:
            raise ValueError('Model write outside package/staging refused: ' + path)

    def mp(self, path): return self.DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    def visible(self, path):
        return f.text(self.DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(path)) if path else ''

    def basic(self, path):
        try:
            info = self.DB.BasicFileInfo.Extract(path)
            try:
                return dict(version=f.text(info.Format), workshared=bool(info.IsWorkshared),
                            central=f.text(getattr(info, 'CentralPath', '')),
                            is_central=bool(getattr(info,'IsCentral',False)))
            finally: dispose(info)
        except Exception as exc:
            # A valid CFB metadata stream can still be inspected when the API's
            # BasicFileInfo extractor refuses it. This does NOT certify Revit
            # readability; OpenDocumentFile must succeed separately.
            info = model_payload.probe(path)
            if info['container'] != 'CFB' or not info.get('worksharing_known'):
                raise model_payload.PayloadError('Archive/unknown payload reached the Revit API boundary.')
            info['metadata_warning'] = 'BasicFileInfo API failed; read-only container metadata used: ' + f.text(exc)
            return info

    def reference_row(self, ref, ident, owner, td=False):
        model_path = ref.GetPath()
        identity = {}
        try:
            # Preserve identity before converting a cloud ModelPath to a display
            # string. This is essential when inventorying an older saved snapshot.
            try:
                model_guid = cache_sources.guid(model_path.GetModelGUID())
                project_guid = cache_sources.guid(model_path.GetProjectGUID())
                if model_guid and project_guid:
                    identity = dict(model_guid=model_guid, project_guid=project_guid,
                                    region=f.text(getattr(model_path, 'Region', '')))
            except Exception:
                pass  # Local FilePaths do not expose cloud identity.
            raw = self.visible(model_path)
        finally:
            dispose(model_path)
        kind = f.text(ref.ExternalFileReferenceType)
        path_type = f.text(getattr(ref, 'PathType', ''))
        try: saved_absolute = self.visible(ref.GetAbsolutePath())
        except Exception: saved_absolute = ''
        if path_type == 'Content':
            # Revit content paths are relative to LIBRARIES, not the host RVT.
            source = saved_absolute
            if not source:
                source = f.resolve_library_resource(raw, self.library_paths()) or ''
        elif f.absolute(raw) or '://' in raw:
            source = raw
        else:
            source = f.resolve_source(raw, owner) or saved_absolute or raw
        row = dict(id=eid(ident), element_id=eid(ident), source=source,
                   saved_path=raw, saved_absolute_path=saved_absolute, path_type=path_type,
                   kind=kind, loaded=load_intent(f.text(ref.GetLinkedFileStatus())),
                   optional_library=(kind=='AssemblyCodeTable' and path_type=='Content'),
                   td=td, special='native')
        if identity:
            row['cloud_identity'] = identity
        return row

    @performance.timed('discovery', 'saved_reference_read', file_index=1)
    def rows(self, path, owner=''):
        td=self.DB.TransmissionData.ReadTransmissionData(self.mp(path))
        rows=[]
        try:
            if td is None: return rows
            for ident in td.GetAllExternalFileReferenceIds():
                f.check(self.cancelled)
                ref=td.GetDesiredReferenceData(ident) if td.IsTransmitted else None
                if ref is None: ref=td.GetLastSavedReferenceData(ident)
                rows.append(self.reference_row(ref, ident, owner, True))
        finally: dispose(td)
        return rows

    def elements(self, doc, name):
        cls=getattr(self.DB,name,None)
        if cls is None: return []
        collector=self.DB.FilteredElementCollector(doc).OfClass(cls)
        try: return list(collector)
        finally: dispose(collector)

    @performance.timed(None, 'revit_open', file_index=1)
    def open_copy(self, path, discard=False, close_worksets=False, detach=True):
        self.guard(path)
        info=self.basic(path)
        current = f.text(getattr(self.app, 'VersionNumber', ''))
        if current.isdigit() and info['version'].isdigit() and int(info['version']) > int(current):
            raise model_payload.PayloadError('Model saved in Revit '+info['version']+' cannot be opened by Revit '+current+'.')
        opts=self.DB.OpenOptions()
        worksets=None
        try:
            if info['workshared']:
                if detach:
                    opts.DetachFromCentralOption=(self.DB.DetachFromCentralOption.DetachAndDiscardWorksets
                                                  if discard else self.DB.DetachFromCentralOption.DetachAndPreserveWorksets)
                worksets=self.DB.WorksetConfiguration(
                    self.DB.WorksetConfigurationOption.CloseAllWorksets if close_worksets else
                    self.DB.WorksetConfigurationOption.OpenAllWorksets)
                opts.SetOpenWorksetsConfiguration(worksets)
            return self.app.OpenDocumentFile(self.mp(path),opts)
        finally:
            dispose(worksets); dispose(opts)

    def prepare_scan(self, stage, rows):
        """Unload file-based RVT links in the disposable inspection copy."""
        self.guard(stage)
        td=self.DB.TransmissionData.ReadTransmissionData(self.mp(stage))
        try:
            if td is None: return
            ids=dict((eid(i),i) for i in td.GetAllExternalFileReferenceIds())
            for row in rows:
                if row['kind']=='RevitLink' and f.absolute(row['source']) and row['id'] in ids:
                    td.SetDesiredReferenceData(ids[row['id']],self.mp(row['source']),self.DB.PathType.Absolute,False)
            td.IsTransmitted=True
            self.DB.TransmissionData.WriteTransmissionData(self.mp(stage),td)
        finally: dispose(td)

    @performance.timed('discovery', 'model_inspection', file_index=1)
    def scan(self, source, stage, options):
        self.guard(stage)
        info=self.basic(stage)
        current=f.text(getattr(self.app,'VersionNumber',''))
        if current.isdigit() and info['version'].isdigit() and int(info['version'])>int(current):
            raise model_payload.PayloadError('Saved Revit '+info['version']+' requires that version or newer; running '+current+'.')
        result=dict(references=[],issues=[],version=info['version'],opened_in_revit=False,is_workshared=info['workshared'])
        if getattr(self, 'payloads', None): self.payloads.bind_stage(stage, source)
        if info.get('metadata_warning'):
            result['issues'].append(issue('BASIC_METADATA_FALLBACK',source,info['metadata_warning']))
        base=info.get('central') if info.get('workshared') and f.absolute(info.get('central', '')) else source
        try: result['references']=self.rows(stage, base)
        except Exception as exc: result['issues'].append(issue('SAVED_REFERENCE_SCAN_FAILED',source,exc,'error'))
        simple_only=options.get('simple_repath') and not (options.get('cleanup') or options.get('upgrade'))
        if simple_only or not f.host_processing_allowed(options) or not options.get('deep',True):
            result['issues'].append(issue('METADATA_ONLY_SCAN',source,
                                         'Image/PDF, cloud, point-cloud and some other dependencies can be absent from saved reference metadata.'))
            return result
        doc=None
        try:
            self.prepare_scan(stage,result['references'])
            doc=self.open_copy(stage)
            result['opened_in_revit']=True
            self.scan_open(doc,source,info,result)
            if options.get("include",{}).get("spreadsheets",True):
                self.scan_plugins(doc,base,result)
        except f.Cancelled: raise
        except Exception as exc:
            result['open_failed']=True
            result['issues'].append(issue('DEEP_SCAN_FAILED',source,
                                         'Saved metadata was retained, but deeper dependency inspection failed: '+f.text(exc),'error'))
        finally:
            if doc is not None:
                if not performance.call('discovery','revit_close',stage,doc.Close,False): raise RuntimeError('Temporary inspection document could not be closed.')
        return result

    def scan_plugins(self, doc, base, result):
        from . import plugin_sources
        rows,coverage=plugin_sources.discover(self.DB,doc,self.cancelled,base)
        result['references'].extend(rows);result['plugin_coverage']=coverage
        for item in coverage:
            if item['status'] in ('READ_DENIED','UNSUPPORTED','ERROR'):
                result['issues'].append(issue('PLUGIN_SOURCE_COVERAGE',base,
                    item['provider']+': '+item['status']+' - '+item.get('message','')))

    def library_paths(self):
        """Configured Revit content/library roots, without inventing defaults."""
        try:
            paths = self.app.GetLibraryPaths()
        except Exception:
            return []
        try:
            if hasattr(paths, 'Values'):
                values = list(paths.Values)
            elif hasattr(paths, 'values'):
                values = list(paths.values())
            else:
                values = []
            return [f.text(value) for value in values if f.text(value)]
        except Exception:
            return []

    def resource_source(self, display, metadata, kind):
        """Named path fields are evidence; arbitrary identity/version strings are not."""
        candidates=[]
        lowered=dict((f.text(k).lower(),v) for k,v in metadata.items())
        for key in ('absolutepath','fullpath','filepath','path','savedpath'):
            value=f.text(lowered.get(key,'') or '').strip()
            if value: candidates.append(value)
        if display: candidates.append(f.text(display))
        for value in candidates:
            if f.absolute(value) and self.staging_root and f.within(value,self.staging_root): continue
            if f.absolute(value) or '://' in value: return value
            if lowered.get('pathtype','') == 'Relative' and value:
                return value
        if kind in ('AssemblyCodeTable','KeynoteTable'):
            for value in candidates:
                resolved=f.resolve_library_resource(value,self.library_paths())
                if resolved: return resolved
        return f.text(display or '')

    @performance.timed('discovery', 'live_reference_read', file_index=1)
    def scan_open(self, doc, source, info, result):
        refs, issues=result['references'],result['issues']
        by_id=dict((r['id'],r) for r in refs)
        base=info['central'] if info['workshared'] and f.absolute(info['central']) else source
        imported=set(eid(i.GetTypeId()) for i in self.elements(doc,'ImportInstance') if not i.IsLinked)
        seen=set(by_id)
        embedded_images=set()
        def add(row):
            if row['id'] in by_id:
                old=by_id[row['id']]
                row['td']=old.get('td',False)
                # A second API representation may supply the missing original
                # path. Never throw that information away because the ID was seen.
                if row.get('special') != 'external' or not row.get('source'):
                    if old.get('source'): row['source']=old['source']
                if row.get('special')=='external':
                    row['native_source']=old.get('source','')
                    if old.get('special')=='image': row['special']='image'
                    if old.get('special')=='pointcloud': row['special']='pointcloud'
                if row.get('special')!='image' and old.get('loaded') is not None: row['loaded']=old.get('loaded')
                old.update(row)
            else:
                refs.append(row); by_id[row['id']]=row
            seen.add(row['id'])
        for image in self.elements(doc,'ImageType'):
            f.check(self.cancelled)
            try:
                key=eid(image.Id); seen.add(key)
                if f.text(image.Source)!='Link':
                    embedded_images.add(key)
                    refs[:]=[r for r in refs if r['id']!=key]
                    by_id.pop(key,None)
                    continue  # Embedded/imported content is explicitly out of scope.
                raw=f.text(image.Path)
                add(dict(id=key,element_id=key,source=f.resolve_source(raw,base) or raw,
                         kind='PDF' if raw.lower().endswith('.pdf') else 'Image',
                         special='image',page=int(image.PageNumber),resolution=float(image.Resolution),
                         loaded=f.text(image.Status)!='Unloaded',td=False))
            except f.Cancelled: raise
            except Exception as exc:
                issues.append(issue('IMAGE_REFERENCE_UNRESOLVED',source+' #'+f.text(getattr(image,'Id','?')),exc))
        for point in self.elements(doc,'PointCloudType'):
            f.check(self.cancelled)
            try:
                key=eid(point.Id)
                model_path=point.GetPath()
                if model_path is None:
                    # Non-file engines return null. Never invent a file named None.
                    refs[:]=[r for r in refs if r['id']!=key]
                    by_id.pop(key,None); seen.add(key)
                    issues.append(issue('POINT_CLOUD_NOT_FILE_BASED',source+' #'+key,
                                        'Point-cloud engine has no file source to collect.'))
                    continue
                try:
                    raw=f.text(model_path) if isinstance(model_path,f.string_types) else self.visible(model_path)
                finally:
                    if not isinstance(model_path,f.string_types): dispose(model_path)
                point_base=f.text(getattr(self.app,'PointCloudsRootPath',''))
                resolved=f.resolve_source(raw)
                if not resolved and point_base:
                    resolved=f.resolve_source(raw,os.path.join(point_base,'_base.rvt'))
                add(dict(id=key,element_id=key,source=resolved or raw,kind='PointCloud',
                         special='pointcloud',loaded=None,td=False))
            except f.Cancelled: raise
            except Exception as exc:
                issues.append(issue('POINT_CLOUD_REFERENCE_UNRESOLVED',source+' #'+f.text(getattr(point,'Id','?')),exc))
        native_ids=[]
        try: native_ids=list(self.DB.ExternalFileUtils.GetAllExternalFileReferences(doc))
        except Exception as exc: issues.append(issue('FILE_REFERENCE_SCAN_FAILED',source,exc,'error'))
        for ident in native_ids:
            key=eid(ident)
            if key in seen or key in imported: continue
            try:
                ref=self.DB.ExternalFileUtils.GetExternalFileReference(doc,ident)
                add(self.reference_row(ref, ident, base))
            except Exception as exc: issues.append(issue('FILE_REFERENCE_UNRESOLVED',source+' #'+key,exc))
        # The built-in external-resource API includes decals, report paths, keynotes and IFC.
        builtin=getattr(self.DB.ExternalResourceTypes,'BuiltInExternalResourceTypes',None)
        types=[]
        for name in ('RevitLink','CADLink','IFCLink','Image','KeynoteTable','AssemblyCodeTable',
                     'DecalImage','SystemsAnalysisReport','MaterialTexture','TopographyLink'):
            try: types.append((getattr(builtin,name),name))
            except AttributeError: pass
        external_ids=[]
        try: external_ids=list(self.DB.ExternalResourceUtils.GetAllExternalResourceReferences(doc))
        except Exception as exc: issues.append(issue('EXTERNAL_RESOURCE_SCAN_FAILED',source,exc,'error'))
        # Unloaded cloud links may expose identity only on their link type.
        known_ids=set(eid(i) for i in external_ids)
        for link_type in self.elements(doc,'RevitLinkType'):
            if not bool(getattr(link_type,'IsNestedLink',False)) and eid(link_type.Id) not in known_ids:
                external_ids.append(link_type.Id);known_ids.add(eid(link_type.Id))
        for ident in external_ids:
            f.check(self.cancelled)
            try:
                key=eid(ident)
                if key in imported or key in embedded_images: continue
                element=doc.GetElement(ident)
                if element is None or bool(getattr(element,'IsNestedLink',False)): continue
                resources=element.GetExternalResourceReferences()
                keys=list(resources.Keys) if hasattr(resources,'Keys') else list(resources.keys())
                for index,resource_type in enumerate(keys):
                    resource=resources[resource_type]
                    kind=next((name for typ,name in types if typ==resource_type),'ExternalResource')
                    display=f.text(resource.InSessionPath)
                    meta=resource.GetReferenceInformation()
                    meta_keys=list(meta.Keys) if hasattr(meta,'Keys') else list(meta.keys())
                    metadata=dict((f.text(k),f.text(meta[k])) for k in meta_keys)
                    resolved_source=self.resource_source(display,metadata,kind)
                    # Prefer an exact file/URI exposed by the server metadata or
                    # configured Revit library roots.  For server-managed paths
                    # this remains an identity, not permission to substitute a
                    # different live/latest model.
                    resolved_source=f.resolve_source(resolved_source,base) or resolved_source
                    row_id=key if key in by_id and index==0 else key+':'+f.text(index)
                    loaded=None
                    special='external'
                    page=1
                    resolution=72.0
                    if kind=='RevitLink':
                        try: loaded=bool(self.DB.RevitLinkType.IsLoaded(doc,ident))
                        except Exception: pass
                    elif kind=='Image':
                        if hasattr(element, 'Source') and f.text(element.Source) != 'Link':
                            continue  # Do not externalize an embedded image/PDF.
                        # Some saved image/PDF links are exposed only through
                        # ExternalResourceUtils, not the ImageType collector.
                        # They are still ImageType elements and can be repathed.
                        special='image'
                        try: page=int(getattr(element,'PageNumber',1) or 1)
                        except Exception: page=1
                        try: resolution=float(getattr(element,'Resolution',72.0) or 72.0)
                        except Exception: resolution=72.0
                        try: loaded=f.text(getattr(element,'Status',''))!='Unloaded'
                        except Exception: loaded=None
                    row=dict(id=row_id,element_id=key,source=resolved_source,kind=kind,
                             special=special,loaded=loaded,td=False,server=f.text(resource.ServerId),
                             resource_version=f.text(resource.Version),resource_information=metadata,
                             in_session_path=display,
                             optional_library=(kind=='AssemblyCodeTable'),
                             note='External-resource identity recorded; exact source resolution preserves the configured resource.')
                    if kind=='RevitLink':
                        try: row['link_name']=f.text(element.Name)
                        except Exception:
                            try: row['link_name']=f.text(self.DB.Element.Name.GetValue(element))
                            except Exception: pass
                        identity=cache_sources.reference_identity(row)
                        if identity:row['cloud_identity']=identity
                    if f.category(resolved_source)=='spreadsheets':
                        row.update(category='spreadsheets',special='plugin_spreadsheet',provider=kind,source_evidence='EXTERNAL_RESOURCE_PATH')
                    if special=='image':
                        row.update(page=page,resolution=resolution)
                    add(row)
                    if resource.Version and (f.is_desktop_connector_path(resolved_source) or '://' in f.text(resolved_source)):
                        issues.append(issue('EXTERNAL_RESOURCE_VERSION_UNVERIFIED',resolved_source,
                                            'Recorded server version '+f.text(resource.Version)+'. Desktop Connector files are copied '
                                            'at the selected location; historical version identity cannot be certified.'))
            except f.Cancelled: raise
            except Exception as exc:
                issues.append(issue('EXTERNAL_RESOURCE_UNRESOLVED',source+' #'+f.text(getattr(ident,'Id','?')),exc))
        for row in refs:
            if unconfigured_keynote(row):
                row['unconfigured']=True
                row['note']='No keynote file is configured.'
        # Some Revit versions do not expose Navisworks coordination paths publicly.
        coordination=getattr(self.DB.BuiltInCategory,'OST_Coordination_Model',None)
        if coordination is not None:
            col=self.DB.FilteredElementCollector(doc).OfCategory(coordination)
            try:
                for element in col:
                    key=eid(element.Id)
                    if key not in seen:
                        issues.append(issue('COORDINATION_PATH_NOT_EXPOSED',source+' #'+key,
                                            'Navisworks coordination element found, but no source path was exposed. '
                                            'Add its original NWC/NWD manually; no geometry export is substituted.'))
            finally: dispose(col)

    def image_options(self, row, relative, model_path=None):
        """Build options from the real packaged file, not a guessed relative file.

        Revit accepts an absolute local source together with
        useRelativePath=True. That lets ReloadFrom validate/open the actual file
        while Revit computes the stored relative path from the already-saved
        package model/central location. This is materially safer for workshared
        hosts than handing ReloadFrom a precomputed relative string.
        """
        del model_path
        self.guard(row['target'])
        opts=self.DB.ImageTypeOptions(row['target'],bool(relative),self.DB.ImageTypeSource.Link)
        try:
            if row['target'].lower().endswith('.pdf'): opts.PageNumber=row['page']
            opts.Resolution=row['resolution']
            return opts
        except Exception:
            dispose(opts); raise

    def mark_transmitted_package(self, path, rows=None):
        """Mark a closed packaged workshared RVT transmitted without opening it.

        Preserve TransmissionData-backed reference paths/load states explicitly,
        because Revit applies desired reference data when a transmitted file opens.
        External-server references (including true ACC resources) are not contained
        in TransmissionData and are therefore left untouched.
        """
        self.guard(path)
        if not self.basic(path).get('workshared'):
            return None
        model_path=self.mp(path);td=None
        intended={}
        packaged={}
        for row in rows or []:
            key=f.text(row.get('element_id',row.get('id','')))
            if metadata_target_allowed(row):
                self.guard(row['target'])
                packaged[key]=row
            state=package_load_state(row)
            if state is not None:
                intended[key]=bool(state)
        try:
            td=self.DB.TransmissionData.ReadTransmissionData(model_path)
            if td is None:return False
            already=bool(td.IsTransmitted)
            for ident in td.GetAllExternalFileReferenceIds():
                f.check(self.cancelled)
                ref=ref_path=None
                try:
                    ref=td.GetDesiredReferenceData(ident) if already else None
                    if ref is None:ref=td.GetLastSavedReferenceData(ident)
                    if ref is None:continue
                    status_text=f.text(ref.GetLinkedFileStatus())
                    ident_key=(f.text(ident) if isinstance(ident,f.string_types) else eid(ident))
                    should_load=intended.get(ident_key)
                    if should_load is None:
                        should_load=load_intent(status_text)
                    if should_load is None:should_load=(status_text!='Unloaded')
                    row=packaged.get(ident_key)
                    if row is not None:
                        # Desired data may have been written while IsTransmitted
                        # was false. Never replace the packaged target with the
                        # older saved path during the final transmit step.
                        ref_path=self.mp(relative_path(row['target'],os.path.dirname(path)))
                        path_type=self.DB.PathType.Relative
                    else:
                        ref_path=ref.GetPath();path_type=ref.PathType
                    td.SetDesiredReferenceData(ident,ref_path,path_type,bool(should_load))
                finally:
                    dispose(ref_path);dispose(ref)
            td.IsTransmitted=True
            self.DB.TransmissionData.WriteTransmissionData(model_path,td)
        finally:
            dispose(td);dispose(model_path)
        check=getattr(self.DB.TransmissionData,'IsDocumentTransmitted',None)
        model_path=self.mp(path);verify=None
        try:
            if check is not None:
                try:return bool(check(model_path))
                except Exception:pass
            verify=self.DB.TransmissionData.ReadTransmissionData(model_path)
            return bool(verify is not None and verify.IsTransmitted)
        finally:
            dispose(verify);dispose(model_path)

    def _transmission_load_state(self, td, ident, row):
        """Preserve load intent when the live inventory has no load flag.

        CAD/PDF/other TransmissionData references do not always expose a useful
        loaded value through the same API surface as RevitLinkType. Re-read the
        exact saved TransmissionData record instead of skipping repath.
        """
        state=package_load_state(row)
        if state is not None:
            return bool(state)
        ref=None
        try:
            ref=td.GetDesiredReferenceData(ident) if bool(td.IsTransmitted) else None
            if ref is None:
                ref=td.GetLastSavedReferenceData(ident)
            if ref is None:
                return None
            status_text=f.text(ref.GetLinkedFileStatus())
            state=load_intent(status_text)
            if state is None and status_text:
                state=(status_text!='Unloaded')
            return state
        except Exception:
            return None
        finally:
            dispose(ref)


    def apply_metadata(self, path, target, rows, relative=True, mark_transmitted=True):
        self.guard(path); self.guard(target)
        for row in rows:
            if row.get('target'): self.guard(row['target'])
        td=self.DB.TransmissionData.ReadTransmissionData(self.mp(path))
        if td is None: return False
        try:
            ids=dict((eid(i),i) for i in td.GetAllExternalFileReferenceIds())
            for row in rows:
                ident_key=f.text(row.get('element_id',row.get('id','')))
                if not metadata_target_allowed(row) or ident_key not in ids: continue
                # Cloud references need the resource-server conversion first,
                # even if a native API representation also supplied a TD id.
                desired_load=self._transmission_load_state(td,ids[ident_key],row)
                if desired_load is None:
                    continue
                value=relative_path(row['target'],os.path.dirname(target)) if relative else row['target']
                typ=self.DB.PathType.Relative if relative else self.DB.PathType.Absolute
                td.SetDesiredReferenceData(ids[ident_key],self.mp(value),typ,bool(desired_load))
                if package_load_state(row) is None:
                    row['package_loaded']=bool(desired_load)
                if not f.text(row.get('repath','')).startswith('API_'):
                    row['repath']='TRANSMISSION_DATA' if relative else 'STAGING_ABSOLUTE'
            td.IsTransmitted=bool(mark_transmitted)
            self.DB.TransmissionData.WriteTransmissionData(self.mp(path),td)
            return True
        finally: dispose(td)

    def finish_metadata_copy(self, stage, target, rows, options):
        """Copy saved bytes first; write native paths without a Revit host open."""
        self.guard(stage);self.guard(target)
        reference_target=options.get('reference_target') or target
        self.guard(reference_target)
        if f.canonical(stage)!=f.canonical(target):
            shutil.copyfile(stage,target)
        transmitted=self.apply_metadata(target,reference_target,rows,relative=True)
        issues=[]
        if not transmitted and self.basic(target).get('workshared'):
            issues.append(issue('WORKSHARING_COPY_NOT_DETACHED',target,
                                'Saved/cache bytes were copied, but transmission metadata is unavailable. '
                                'Open the package copy with Detach from Central.','error'))
        for row in rows:
            if not row.get('target') or row.get('skip_repath'):continue
            if row.get('special')=='plugin_spreadsheet':
                row['repath']='PLUGIN_RECONNECT_REQUIRED'
                issues.append(issue('PLUGIN_RECONNECT_REQUIRED',row.get('source',''),
                                    'Workbook copied without executing Excel. The owning plugin must reconnect its private association.'))
                continue
            if not row.get('repath'):
                row['repath']='MANUAL_REPAIR_REQUIRED'
                issues.append(issue('REPATH_NOT_AVAILABLE',row.get('source',''),
                                    'File copied, but this reference cannot be changed through saved TransmissionData. '
                                    'Repair it in the packaged model, or disable Simple copy and repath to use Revit document repairs.'))
        return dict(issues=issues,verified_in_process=False,metadata_repathed=True,
                    transmission_status='TRANSMITTED' if transmitted else 'NOT_MARKED')

    def verify_metadata_package(self, target, rows, options):
        """Check persisted desired paths without claiming a Revit load test."""
        self.guard(target)
        issues=[];model_path=self.mp(target);td=None
        try:
            td=self.DB.TransmissionData.ReadTransmissionData(model_path)
            if not any(metadata_target_allowed(r) for r in rows):return []
            if td is None or not bool(td.IsTransmitted):
                return [issue('METADATA_VERIFICATION_FAILED',target,
                              'Packaged reference metadata is missing or inactive.','error')]
            ids=dict((eid(i),i) for i in td.GetAllExternalFileReferenceIds())
            for row in rows:
                f.check(self.cancelled)
                if not metadata_target_allowed(row):continue
                ref=ref_path=None
                try:
                    ident=ids.get(f.text(row.get('element_id',row.get('id',''))))
                    ref=td.GetDesiredReferenceData(ident) if ident is not None else None
                    if ref is None:raise RuntimeError('Packaged desired reference data is missing.')
                    ref_path=ref.GetPath();value=self.visible(ref_path)
                    if f.text(ref.PathType)!='Relative':
                        raise RuntimeError('Packaged desired reference path is not relative.')
                    paths=ntpath if f.is_windows(target) else os.path
                    value=paths.join(paths.dirname(target),value)
                    if f.canonical(value)!=f.canonical(row['target']):
                        raise RuntimeError('Desired reference still points elsewhere: '+value)
                    state=package_load_state(row)
                    if state is not None and load_intent(f.text(ref.GetLinkedFileStatus()))!=bool(state):
                        raise RuntimeError('Desired reference load state differs from the requested state.')
                    row['verification']='TRANSMISSION_DATA_CHECKED'
                except f.Cancelled:raise
                except Exception as exc:
                    issues.append(issue('METADATA_VERIFICATION_FAILED',row.get('source',''),exc,'error'))
                finally:dispose(ref_path);dispose(ref)
        finally:dispose(td);dispose(model_path)
        return issues

    def _verify_document(self, doc, target, rows, options):
        issues=[]
        from System import Int64,Int32
        for row in rows:
            if not row.get('target') or row.get('skip_repath'): continue
            kind=row.get('kind')
            if kind not in ('RevitLink','CADLink') and row.get('special')!='image': continue
            row.pop('verification',None)
            row.pop('verified_saved_path',None)
            f.check(self.cancelled)
            try:
                number=int(row['element_id'])
                ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
                element=doc.GetElement(ident)
                if element is None:
                    if options.get('cleanup'):
                        row['repath']='REMOVED_BY_CLEANUP';continue
                    raise RuntimeError('Original reference element is missing from the packaged model.')
                if kind=='RevitLink':
                    reference=element.GetExternalFileReference()
                    actual_path=None
                    try:
                        actual_path=reference.GetAbsolutePath()
                        actual=self.visible(actual_path)
                    finally:
                        dispose(actual_path)
                    expected_load=package_load_state(row)
                    if expected_load is not None and bool(self.DB.RevitLinkType.IsLoaded(doc,ident))!=bool(expected_load):
                        raise RuntimeError('Packaged Revit link load state does not match the requested state.')
                    if row.get('repath')=='API_LOCAL_LINK_RELATIVE' and f.text(getattr(reference,'PathType',''))!='Relative':
                        raise RuntimeError('Packaged Revit link was not stored as a relative reference.')
                elif kind=='CADLink':
                    reference=self.DB.ExternalFileUtils.GetExternalFileReference(doc,ident)
                    actual_path=None
                    try:
                        actual_path=reference.GetAbsolutePath()
                        actual=self.visible(actual_path)
                    finally:
                        dispose(actual_path)
                else:
                    actual=f.resolve_source(f.text(element.Path),target) or f.text(element.Path)
                    if row.get('repath')=='API_IMAGE_RELATIVE':
                        if f.text(getattr(element,'PathType',''))!='Relative':
                            raise RuntimeError('Packaged image/PDF was not stored as a relative reference.')
                        status=f.text(getattr(element,'Status',''))
                        if status not in ('Loaded','Unloaded'):
                            raise RuntimeError('Packaged image/PDF did not load successfully: '+status)
                        expected_load=package_load_state(row)
                        if expected_load is not None and (status=='Loaded')!=bool(expected_load):
                            raise RuntimeError('Packaged image/PDF load state does not match the requested state.')
                if f.canonical(actual)!=f.canonical(row['target']):
                    raise RuntimeError('Packaged reference still points elsewhere: '+actual)
                saved_path=None
                if kind in ('RevitLink','CADLink'):
                    try:
                        saved_path=reference.GetPath()
                        row['verified_saved_path']=self.visible(saved_path)
                    finally:dispose(saved_path)
                else:row['verified_saved_path']=f.text(element.Path)
                row['verification']='PATH_AND_LOAD_CHECKED' if kind=='RevitLink' else 'PATH_CHECKED'
            except Exception as exc:
                issues.append(issue('LINK_VERIFICATION_FAILED',row.get('source',''),exc,'error'))
        return issues

    def repath_cad_links(self, doc, rows):
        """Point linked DWG CADLinkTypes at packaged files in the open copy."""
        from System import Int64, Int32
        issues=[]
        for row in rows:
            f.check(self.cancelled)
            result=None
            try:
                number=int(row['element_id'])
                ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
                link=doc.GetElement(ident)
                if link is None:
                    raise RuntimeError('CAD link type is missing from the package model.')
                result=link.LoadFrom(row['target'])
                load_result=f.text(getattr(result,'LoadResult',''))
                if load_result and load_result not in ('LinkLoaded','LinkAlreadyLoaded'):
                    raise RuntimeError('CAD link reload failed: '+load_result)
                row['repath']='API_CAD_LINK'
            except f.Cancelled:
                raise
            except Exception as exc:
                row['repath']='FAILED'
                issues.append(issue('CAD_REPATH_FAILED',row.get('source',''),exc,'error'))
            finally:
                dispose(result)
        return issues

    def repath_images(self, doc, rows):
        """Reload linked PDFs/images from the packaged absolute file.

        Revit receives the absolute source for validation, but
        ImageTypeOptions(useRelativePath=True) tells it to persist a relative
        path against the new package central/project location.
        """
        from System import Int64, Int32
        issues=[]
        changed=False
        for row in rows:
            f.check(self.cancelled)
            tx=self.DB.Transaction(doc,'e-transmit: package image/PDF link')
            opts=None
            try:
                number=int(row['element_id'])
                ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
                image=doc.GetElement(ident)
                if image is None:
                    row['repath']='REMOVED_BY_CLEANUP'
                    continue
                opts=self.image_options(row,True,getattr(doc,'PathName',''))
                validator=getattr(opts,'IsValid',None)
                if validator is not None and not bool(validator(doc)):
                    raise RuntimeError('Revit rejected the packaged PDF/image source: '+row['target'])
                tx.Start()
                image.ReloadFrom(opts)
                if package_load_state(row) is False:
                    image.Unload()
                if tx.Commit()!=self.DB.TransactionStatus.Committed:
                    raise RuntimeError('PDF/image link reload transaction did not commit.')
                row['repath']='API_IMAGE_RELATIVE'
                row['repath_source']='PACKAGED_ABSOLUTE_FILE'
                changed=True
            except f.Cancelled:
                raise
            except Exception as exc:
                try:
                    if tx.GetStatus()==self.DB.TransactionStatus.Started: tx.RollBack()
                except Exception:
                    pass
                row['repath']='FAILED'
                issues.append(issue('IMAGE_REPATH_FAILED',row.get('source',''),exc,'error'))
            finally:
                dispose(tx);dispose(opts)
        return issues,changed

    def _save_as_independent_package_central(self, doc, target, clear_transmitted=False):
        """Save the detached/task copy as its own package central first."""
        save=self.DB.SaveAsOptions();ws=None
        try:
            save.OverwriteExistingFile=True;save.MaximumBackups=1
            if bool(getattr(doc,'IsWorkshared',False)):
                ws=self.DB.WorksharingSaveAsOptions()
                ws.SaveAsCentral=True
                if clear_transmitted:ws.ClearTransmitted=True
                save.SetWorksharingOptions(ws)
            performance.call('repath','revit_save_independent_central',target,
                             doc.SaveAs,target,save)
        finally:
            dispose(ws);dispose(save)
        actual=f.text(getattr(doc,'PathName','') or '')
        if actual and f.canonical(actual)!=f.canonical(target):
            raise RuntimeError('Revit did not switch the repair document to the package path.')
        if bool(getattr(doc,'IsModified',False)):
            raise RuntimeError('A save callback modified the package document after SaveAs.')
        if bool(getattr(doc,'IsDetached',False)):
            raise RuntimeError('The saved package document is still detached; it cannot be delivered as a normal host.')
        return True

    def _repath_external_revit_links_relative(self, doc, rows):
        from System import Int64, Int32
        issues=[]
        changed=False
        for row in rows:
            f.check(self.cancelled)
            resource=result=model_path=None
            try:
                number=int(row['element_id'])
                ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
                link=doc.GetElement(ident)
                if link is None:
                    raise RuntimeError('Revit link type is missing from the package model.')
                model_path=self.mp(row['target'])
                resource=self.DB.ExternalResourceReference.CreateLocalResource(
                    doc,self.DB.ExternalResourceTypes.BuiltInExternalResourceTypes.RevitLink,
                    model_path,self.DB.PathType.Relative)
                result=link.LoadFrom(resource,None)
                if f.text(result.LoadResult) not in ('LinkLoaded','LinkAlreadyLoaded'):
                    raise RuntimeError('Packaged Revit link reload failed: '+f.text(result.LoadResult))
                if package_load_state(row) is False:
                    link.Unload(None)
                row['repath']='API_LOCAL_LINK_RELATIVE'
                changed=True
            except f.Cancelled:
                raise
            except Exception as exc:
                row['repath']='FAILED'
                issues.append(issue('REVIT_LINK_REPATH_FAILED',row.get('source',''),exc,'error'))
            finally:
                dispose(result);dispose(resource);dispose(model_path)
        return issues,changed

    def _prepare_partial_repath(self, stage, rows):
        """Persist unavailable RVTs as unloaded in scratch, never in the source.

        Closed worksets prevent external/cloud links loading during this first
        open. Unload(None) then persists a real global unload (not just a closed
        workset or a user's local override). The ordinary repair opens all
        worksets again and only reloads references with delivered targets.
        """
        self.guard(stage)
        missing=[r for r in rows if r.get('kind')=='RevitLink' and
                 not r.get('target') and not r.get('skip_repath')]
        info=self.basic(stage)
        if not info.get('workshared') and any(not r.get('td') or
                cache_sources.reference_identity(r) for r in missing):
            raise RuntimeError('Unavailable external Revit links cannot be isolated before opening '
                               'this non-workshared host. Original host retained; no links were guessed.')
        model_path=self.mp(stage);td=None
        try:
            td=self.DB.TransmissionData.ReadTransmissionData(model_path)
            wrote=False
            if td is not None:
                for ident in td.GetAllExternalFileReferenceIds():
                    ref=None;ref_path=None
                    try:
                        ref=td.GetDesiredReferenceData(ident) if td.IsTransmitted else None
                        if ref is None:ref=td.GetLastSavedReferenceData(ident)
                        if ref is None or f.text(ref.ExternalFileReferenceType)!='RevitLink':continue
                        ref_path=ref.GetPath()
                        td.SetDesiredReferenceData(ident,ref_path,ref.PathType,False)
                        wrote=True
                    finally:dispose(ref_path);dispose(ref)
                if wrote:
                    td.IsTransmitted=True
                    self.DB.TransmissionData.WriteTransmissionData(model_path,td)
        finally:dispose(td);dispose(model_path)
        opened_transmitted=self._package_is_transmitted(stage)
        available=set(f.text(r.get('element_id',r.get('id',''))) for r in rows
                      if r.get('kind')=='RevitLink' and r.get('target') and not r.get('skip_repath'))
        unloaded=[];issues=[];doc=None
        try:
            doc=self.open_copy(stage,close_worksets=True)
            links=dict((eid(link.Id),link) for link in self.elements(doc,'RevitLinkType')
                       if not bool(getattr(link,'IsNestedLink',False)))
            for key,link in links.items():
                if key in available:continue
                f.check(self.cancelled)
                # Do not save shared coordinates back to any source link.
                # IsLoaded=False in a closed workset does not establish a persisted
                # unload. An explicit global Unloaded status does; avoid an
                # unnecessary cloud-server operation in that already-safe case.
                if f.text(link.GetLinkedFileStatus())!='Unloaded':
                    link.Unload(None)
                if bool(self.DB.RevitLinkType.IsLoaded(doc,link.Id)):
                    raise RuntimeError('An unavailable Revit link remained loaded: '+key)
                unloaded.append(key)
                issues.append(issue('UNAVAILABLE_RVT_LEFT_UNLOADED',stage+' #'+key,
                    'No packaged RVT is available. The reference is retained unloaded; '
                    'available PDF/image/CAD references are repaired independently.'))
            for row in missing:
                key=f.text(row.get('element_id',row.get('id','')))
                row['requested_package_loaded']=package_load_state(row)
                row['saved_membership_verification']='PRESENT' if key in links else 'NOT_PRESENT'
                row['repath']='NOT_REPATHED_UNAVAILABLE' if key in links else 'NOT_PRESENT_IN_SAVED_HOST'
                if key in links:row['package_loaded']=False
                else:
                    issues.append(issue('REFERENCE_NOT_IN_SAVED_HOST',stage+' #'+key,
                        'The live-session Revit link is absent from the saved host. No replacement link was created.'))
            self._save_as_independent_package_central(doc,stage,opened_transmitted)
        finally:
            if doc is not None and not doc.Close(False):
                raise RuntimeError('Partial-repath preparation document could not be closed.')
        return unloaded,issues

    def _verify_unavailable_links(self, doc, rows, unloaded_ids, mark_verified=False):
        if unloaded_ids:
            from System import Int64,Int32
        for key in unloaded_ids:
            number=int(key)
            ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
            element=doc.GetElement(ident)
            if (element is None or bool(self.DB.RevitLinkType.IsLoaded(doc,ident)) or
                    f.text(element.GetLinkedFileStatus())!='Unloaded'):
                raise RuntimeError('An unavailable Revit reference is not persistently unloaded on reopen: '+key)
        if mark_verified:
            for row in rows:
                key=f.text(row.get('element_id',row.get('id','')))
                if key in unloaded_ids:row['verification']='UNAVAILABLE_LINK_UNLOADED_CHECKED'
                elif row.get('saved_membership_verification')=='NOT_PRESENT':
                    row['verification']='NOT_PRESENT_IN_SAVED_HOST'

    def _verify_normal_reopen(self, target, rows, options, unloaded_ids):
        """Read the final saved file, with no detach flag and no further saves."""
        doc=None
        try:
            doc=self.open_copy(target,detach=False)
            if bool(getattr(doc,'IsDetached',False)):
                raise RuntimeError('The final saved host still opens detached.')
            if f.canonical(f.text(getattr(doc,'PathName','')))!=f.canonical(target):
                raise RuntimeError('Final verification did not open the package host.')
            if bool(getattr(doc,'IsModified',False)):
                raise RuntimeError('The package changed during its final read-only verification open.')
            checked=[r for r in rows if metadata_target_allowed(r) and
                     (r.get('repath')=='TRANSMISSION_DATA' or
                      f.text(r.get('repath','')).startswith('API_'))]
            problems=self._verify_document(doc,target,checked,options)
            if problems:
                raise RuntimeError('Saved reference paths failed normal reopen: '+
                                   '; '.join(f.text(p.get('message','')) for p in problems))
            for row in checked:
                if row.get('verification') in ('PATH_CHECKED','PATH_AND_LOAD_CHECKED'):
                    row['verification']='SAVED_REFERENCE_CHECKED'
                    # Actual saved path was read by _verify_document, not inferred from the target.
            self._verify_unavailable_links(doc,rows,unloaded_ids,mark_verified=True)
        finally:
            if doc is not None and not doc.Close(False):
                raise RuntimeError('Normal package verification document could not be closed.')
        return True

    def finish_independent(self, stage, target, rows, options):
        """Materialize package references and deliver a normal, saved central.

        Transmitted metadata is temporary input to a Revit open, never the final
        deliverable. The second save consumes relative native paths after the
        package central location has been established.
        """
        self.guard(stage);self.guard(target)
        issues=[]
        info=self.basic(stage)
        for row in rows:
            if row.get('target'):self.guard(row['target'])
        current=f.text(getattr(self.app,'VersionNumber','') or '')
        if info.get('version') and current and info['version']!=current and not options.get('upgrade'):
            raise RuntimeError('Saved format '+info['version']+' requires an authorized upgrade to Revit '
                               +current+' before this host can be finalized.')

        unloaded_ids=[]
        if options.get('allow_partial_repath') and options.get('repath') and any(
                r.get('kind')=='RevitLink' and not r.get('target') and not r.get('skip_repath') for r in rows):
            unloaded_ids,partial_issues=self._prepare_partial_repath(stage,rows)
            issues.extend(partial_issues)
        if options.get('repath'):
            self.apply_metadata(stage,target,rows,relative=False)
        opened_transmitted=self._package_is_transmitted(stage)

        special=[r for r in rows if r.get('target') and not r.get('skip_repath')
                 and r.get('special')=='image']
        external=[r for r in rows if r.get('target') and r.get('kind')=='RevitLink'
                  and not r.get('skip_repath')
                  and (not r.get('td') or cache_sources.reference_identity(r))]
        cad=[r for r in rows if r.get('target') and r.get('kind')=='CADLink'
             and not r.get('skip_repath') and not r.get('repath')
             and os.path.splitext(r.get('target',''))[1].lower()=='.dwg']

        doc=None
        changed=False
        discard=bool(options.get('cleanup') and options.get('discard_worksets'))
        try:
            doc=self.open_copy(stage,discard)
            self._verify_unavailable_links(doc,rows,unloaded_ids)
            self._save_as_independent_package_central(doc,target,opened_transmitted)

            if options.get('repath'):
                revit_issues,revit_changed=self._repath_external_revit_links_relative(doc,external)
                issues.extend(revit_issues);changed=changed or revit_changed

                cad_issues=self.repath_cad_links(doc,cad)
                issues.extend(cad_issues)
                changed=changed or any(r.get('repath')=='API_CAD_LINK' for r in cad)

                image_issues,image_changed=self.repath_images(doc,special)
                issues.extend(image_issues);changed=changed or image_changed

            if options.get('cleanup'):
                from .cleanup import run
                run(doc,self.DB,options,self.cancelled)
                changed=True

            # Save the independent package model after all document-API repairs.
            if changed or options.get('upgrade') or options.get('normalize_saved_cache'):
                performance.call('repath','revit_save_repaired_central',target,doc.Save)
                if bool(getattr(doc,'IsModified',False)):
                    raise RuntimeError('A save callback modified the repaired package document after Save.')
            if options.get('repath'):
                api_rows=[r for r in rows if metadata_target_allowed(r) and
                          f.text(r.get('repath','')).startswith('API_')]
                problems=self._verify_document(doc,target,api_rows,options) if api_rows else []
                if problems:
                    raise RuntimeError('Document reference repairs did not persist at the package path: '+
                                       '; '.join(f.text(p.get('message','')) for p in problems))
        finally:
            if doc is not None:
                if not performance.call('repath','revit_close',target,doc.Close,False):
                    raise RuntimeError('Independent package repair document could not be closed.')

        # Desired paths become real element data only when Revit opens them.
        # Establishing the package central first gives relative paths a known
        # base, including for native formats without a document reload API.
        if options.get('repath'):
            relative_written=self.apply_metadata(target,target,rows,relative=True)
            if relative_written:
                relative_transmitted=self._package_is_transmitted(target)
                if not relative_transmitted:
                    raise RuntimeError('The relative package references were not activated for materialization.')
                doc=None
                try:
                    doc=self.open_copy(target,False)
                    self._verify_unavailable_links(doc,rows,unloaded_ids)
                    repair_rows=[r for r in rows if metadata_target_allowed(r) and
                                 (r.get('repath')=='TRANSMISSION_DATA' or
                                  f.text(r.get('repath','')).startswith('API_'))]
                    problems=self._verify_document(doc,target,repair_rows,options) if repair_rows else []
                    if problems:
                        issues.extend(problems)
                        raise RuntimeError('Final package references did not match their packaged files: '+
                                           '; '.join(f.text(p.get('message','')) for p in problems))
                    self._save_as_independent_package_central(doc,target,relative_transmitted)
                finally:
                    if doc is not None:
                        if not performance.call('repath','revit_close_materialized',target,doc.Close,False):
                            raise RuntimeError('Materialized package document could not be closed.')
            for row in rows:
                if not row.get('target') or row.get('skip_repath') or row.get('repath'):continue
                plugin=row.get('special')=='plugin_spreadsheet'
                row['repath']='PLUGIN_RECONNECT_REQUIRED' if plugin else 'MANUAL_REPAIR_REQUIRED'
                issues.append(issue('PLUGIN_RECONNECT_REQUIRED' if plugin else 'REPATH_NOT_AVAILABLE',
                                    row.get('source',''),
                                    'File copied, but its reference could not be saved to the packaged location.'))

        self.verify_independent_package(target,rows,options,
                                        expected_workshared=bool(info.get('workshared') and not discard))
        errors=[p for p in issues if p.get('severity')=='error']
        if errors:
            raise RuntimeError('The package host could not be finalized: '+
                               '; '.join(f.text(p.get('message','')) for p in errors))

        normal_open_verified=False
        if options.get('repath'):
            normal_open_verified=self._verify_normal_reopen(target,rows,options,unloaded_ids)
        return dict(issues=issues,verified_in_process=False,
                    normal_open_verified=normal_open_verified,
                    unavailable_links_checked=bool(normal_open_verified),
                    independent_package_central=True,host_finalized=True,
                    saved_references_checked=True,verification_status='SAVED_REFERENCES_CHECKED')

    def _package_is_transmitted(self, path):
        model_path=self.mp(path);td=None
        try:
            td=self.DB.TransmissionData.ReadTransmissionData(model_path)
            if td is not None:return bool(td.IsTransmitted)
            check=getattr(self.DB.TransmissionData,'IsDocumentTransmitted',None)
            return bool(check(model_path)) if check is not None else False
        finally:dispose(td);dispose(model_path)

    def verify_independent_package(self, target, rows, options, expected_workshared=None):
        """Verify real saved references and normal opening state, without writes."""
        self.guard(target)
        info=self.basic(target)
        if expected_workshared is not None and bool(info.get('workshared'))!=bool(expected_workshared):
            raise RuntimeError('The finalized package changed the host worksharing state.')
        if info.get('workshared'):
            if not info.get('is_central') or f.canonical(info.get('central',''))!=f.canonical(target):
                raise RuntimeError('The exported host is not a saved central at its package location.')
        if self._package_is_transmitted(target):
            raise RuntimeError('The finalized package host is still marked transmitted.')
        if not options.get('repath'):return []
        model_path=self.mp(target);td=None
        try:
            td=self.DB.TransmissionData.ReadTransmissionData(model_path)
            ids=dict((eid(i),i) for i in td.GetAllExternalFileReferenceIds()) if td is not None else {}
            for row in rows:
                if not metadata_target_allowed(row):continue
                key=f.text(row.get('element_id',row.get('id','')))
                ident=ids.get(key)
                if ident is None:
                    if row.get('repath') in ('TRANSMISSION_DATA','STAGING_ABSOLUTE'):
                        if options.get('cleanup'):
                            row['repath']='REMOVED_BY_CLEANUP';continue
                        raise RuntimeError('A native packaged reference is absent from the saved host: '+key)
                    if row.get('repath')=='API_CAD_LINK':
                        # LoadFrom(String) keeps the previous path type. Its
                        # early absolute-path check cannot prove portability;
                        # only final native saved metadata can prove Relative.
                        raise RuntimeError('The packaged CAD reference has no saved metadata to verify its relative path: '+key)
                    continue
                ref=ref_path=None
                try:
                    ref=td.GetLastSavedReferenceData(ident)
                    if ref is None:raise RuntimeError('A packaged reference has no saved data: '+key)
                    if f.text(ref.PathType)!='Relative':
                        raise RuntimeError('A saved package reference is not relative: '+key)
                    ref_path=ref.GetPath();value=self.visible(ref_path)
                    paths=ntpath if f.is_windows(target) else os.path
                    absolute=paths.join(paths.dirname(target),value)
                    if f.canonical(absolute)!=f.canonical(row['target']):
                        raise RuntimeError('Saved reference still points elsewhere: '+absolute)
                    state=package_load_state(row)
                    if state is not None and load_intent(f.text(ref.GetLinkedFileStatus()))!=bool(state):
                        raise RuntimeError('A saved package reference has the wrong load state: '+key)
                    row['verification']='SAVED_REFERENCE_CHECKED'
                    row['verified_saved_path']=value
                finally:dispose(ref_path);dispose(ref)
        finally:dispose(td);dispose(model_path)
        return []

    @performance.timed('repath', 'revit_process', file_index=1)
    def finish(self, stage, target, rows, options):
        if options.get('independent_host'):
            return self.finish_independent(stage,target,rows,options)
        if self.can_finish_metadata_copy(rows,options):
            return self.finish_metadata_copy(stage,target,rows,options)
        if options.get('worker_mode'):
            return self.finish_independent(stage,target,rows,options)
        self.guard(stage); self.guard(target)
        issues=[]
        info=self.basic(stage)
        for row in rows:
            if row.get('target'): self.guard(row['target'])
        reference_target=options.get('reference_target') or target
        self.guard(reference_target)
        transmitted=self.apply_metadata(stage,reference_target,rows) if options.get('repath') else False
        special=[r for r in rows if r.get('target') and r.get('special')=='image' and not r.get('repath')]
        external=[r for r in rows if r.get('target') and r.get('kind')=='RevitLink'
                  and (not r.get('td') or cache_sources.reference_identity(r))]
        # Revit's CADLinkType.LoadFrom(String) supports linked DWG. Other
        # CAD formats remain on their TransmissionData/API-specific path.
        cad=[r for r in rows if r.get('target') and r.get('kind')=='CADLink'
             and os.path.splitext(r.get('target',''))[1].lower()=='.dwg'
             and not r.get('repath')]
        needs_document=options.get('cleanup') or options.get('upgrade') or options.get('normalize_saved_cache') or (options.get('repath') and (special or external or cad))
        if needs_document and reference_target != target:
            raise ValueError('Direct layout is only valid for metadata-only host processing.')
        if needs_document and info['version']!=f.text(self.app.VersionNumber) and not options.get('upgrade'):
            issues.append(issue('UPGRADE_CONSENT_REQUIRED',target,
                                'Saved format '+info['version']+' retained. Additional API repathing needs a save in Revit '
                                +f.text(self.app.VersionNumber)+'; enable Upgrade to authorize it.'))
            needs_document=False
        if not needs_document:
            shutil.copyfile(stage,target)
            if info['workshared'] and not transmitted:
                issues.append(issue('WORKSHARING_COPY_NOT_DETACHED',target,
                                    'The saved copy could not be marked transmitted. Open it with Detach from Central; '
                                    'never synchronize it to the source central.', 'error'))
        else:
            doc=None
            try:
                if options.get('repath'): self.apply_metadata(stage,target,rows,relative=False)
                doc=self.open_copy(stage,bool(options.get('cleanup') and options.get('discard_worksets')))
                if options.get('repath'):
                    from System import Int64, Int32
                    for row in external:
                        f.check(self.cancelled)
                        number=int(row['element_id'])
                        ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
                        link=doc.GetElement(ident)
                        resource=result=model_path=None
                        try:
                            model_path=self.mp(row['target'])
                            if cache_sources.reference_identity(row) or not bool(getattr(link,'IsFromLocalPath',True)):
                                resource=self.DB.ExternalResourceReference.CreateLocalResource(doc,
                                    self.DB.ExternalResourceTypes.BuiltInExternalResourceTypes.RevitLink,
                                    model_path,self.DB.PathType.Absolute)
                                result=link.LoadFrom(resource,None)
                            else:
                                result=link.LoadFrom(model_path,None)
                            if f.text(result.LoadResult) not in ('LinkLoaded','LinkAlreadyLoaded'):
                                raise RuntimeError('Revit link reload failed: '+f.text(result.LoadResult))
                        finally:dispose(result);dispose(resource);dispose(model_path)
                        if package_load_state(row) is False: link.Unload(None)
                        row['repath']='API_LOCAL_LINK'
                    issues.extend(self.repath_cad_links(doc,cad))
                if options.get('cleanup'):
                    from .cleanup import run
                    run(doc,self.DB,options,self.cancelled)
                save=self.DB.SaveAsOptions(); ws=None
                try:
                    save.OverwriteExistingFile=True; save.MaximumBackups=1
                    if doc.IsWorkshared:
                        ws=self.DB.WorksharingSaveAsOptions(); ws.SaveAsCentral=True
                        save.SetWorksharingOptions(ws)
                    performance.call('repath','revit_save_as',target,doc.SaveAs,target,save)
                finally: dispose(ws); dispose(save)
                needs_resave=False
                if options.get('repath') and external:
                    # SaveAs establishes the final package base. Reload cloud/external
                    # links once more as RELATIVE local resources so this same open
                    # document can be the final verification pass.
                    for row in external:
                        f.check(self.cancelled)
                        number=int(row['element_id'])
                        ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
                        link=doc.GetElement(ident);resource=result=model_path=None
                        try:
                            model_path=self.mp(row['target'])
                            resource=self.DB.ExternalResourceReference.CreateLocalResource(doc,
                                self.DB.ExternalResourceTypes.BuiltInExternalResourceTypes.RevitLink,
                                model_path,self.DB.PathType.Relative)
                            result=link.LoadFrom(resource,None)
                            if f.text(result.LoadResult) not in ('LinkLoaded','LinkAlreadyLoaded'):
                                raise RuntimeError('Relative packaged Revit link reload failed: '+f.text(result.LoadResult))
                            if package_load_state(row) is False: link.Unload(None)
                            row['repath']='API_LOCAL_LINK_RELATIVE';needs_resave=True
                        finally:dispose(result);dispose(resource);dispose(model_path)
                if options.get('repath') and special:
                    # SaveAs has established the correct final base. Revit may
                    # accept a short relative path when the absolute dependency
                    # path exceeds MAX_PATH. Keep each reload isolated.
                    for row in special:
                        f.check(self.cancelled)
                        tx=self.DB.Transaction(doc,'e-transmit: relative image path')
                        opts=None
                        try:
                            number=int(row['element_id'])
                            ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
                            image=doc.GetElement(ident)
                            if image is None:
                                row['repath']='REMOVED_BY_CLEANUP'; continue
                            opts=self.image_options(row,True,target)
                            tx.Start()
                            image.ReloadFrom(opts)
                            if row.get('loaded') is False: image.Unload()
                            if tx.Commit()!=self.DB.TransactionStatus.Committed:
                                raise RuntimeError('Relative image reload not committed.')
                            row['repath']='API_IMAGE_RELATIVE';needs_resave=True
                        except f.Cancelled: raise
                        except Exception as exc:
                            if tx.GetStatus()==self.DB.TransactionStatus.Started: tx.RollBack()
                            row['repath']='FAILED'
                            issues.append(issue('IMAGE_REPATH_FAILED',row.get('source',''),exc,'error'))
                        finally:
                            dispose(tx); dispose(opts)
                if needs_resave:
                    performance.call('repath','revit_save',target,doc.Save)
                verified_in_process=False
                if options.get('verify_in_process'):
                    verify_issues=self._verify_document(doc,target,rows,options)
                    issues.extend(verify_issues)
                    verified_in_process=not any(x.get('severity')=='error' for x in verify_issues)
            finally:
                if doc is not None:
                    if not performance.call('repath','revit_close',target,doc.Close,False): raise RuntimeError('Temporary output model could not be closed.')
            if options.get('repath'): self.apply_metadata(target,target,rows)
        if options.get('repath'):
            for row in rows:
                if row.get('target') and row.get('special')=='plugin_spreadsheet':
                    row['repath']='PLUGIN_RECONNECT_REQUIRED'
                    issues.append(issue('PLUGIN_RECONNECT_REQUIRED',row.get('source',''),
                                        'Workbook copied without executing Excel. The owning plugin must reconnect its private association.'))
                    continue
                if row.get('target') and not row.get('repath'):
                    row['repath']='MANUAL_REPAIR_REQUIRED'
                    issues.append(issue('REPATH_NOT_AVAILABLE',row.get('source',''),
                                        'Source copied, but this reference cannot be repathed safely by the exposed API. '
                                        'Repair in the packaged model and verify after moving the package.'))
        if options.get('verify_in_process'):
            return dict(issues=issues,verified_in_process=bool(locals().get('verified_in_process',False)))
        return issues

    @performance.timed('verification', 'host_verify', file_index=1)
    def verify_package(self, target, rows, options):
        """Open the output read-only in intent, close without saving; verify paths/loads.

        This is executed by the user's Revit process, not by portable CI. Do not
        open a host with unresolved RVTs and silently fall back to cloud links.
        """
        self.guard(target)
        missing=[r for r in rows if r.get('kind')=='RevitLink' and not r.get('target')
                 and r.get('status')!='EXCLUDED']
        if missing:
            return [issue('MODEL_VERIFICATION_DEFERRED',target,
                          'A linked RVT is unresolved. Final opening was not attempted to avoid source/cloud fallback.','error')]
        doc=None
        issues=[]
        try:
            doc=self.open_copy(target)
            issues.extend(self._verify_document(doc,target,rows,options))
        except f.Cancelled: raise
        except Exception as exc:
            issues.append(issue('MODEL_OPEN_VERIFICATION_FAILED',target,exc,'error'))
        finally:
            if doc is not None and not performance.call('verification','revit_close',target,doc.Close,False):
                issues.append(issue('MODEL_CLOSE_FAILED',target,'Verification document could not be closed.','error'))
        return issues
