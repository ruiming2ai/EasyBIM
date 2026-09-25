# -*- coding: utf-8 -*-
"""Revit API boundary. All document writes are restricted to package copies."""
from __future__ import unicode_literals
import os
import ntpath
import shutil
from . import files as f
from .pathnames import relative as relative_path
from .engine import issue
from . import model_payload


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


class Backend(object):
    def __init__(self, DB, application, package_root, cancelled=None):
        self.DB, self.app, self.root, self.cancelled = DB, application, package_root, cancelled
        self.staging_root = None
        self.payloads = None

    def set_staging_root(self, path):
        self.staging_root = path
        self.payloads = model_payload.Store(os.path.join(path, 'acquired'))

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
                            central=f.text(getattr(info, 'CentralPath', '')))
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
        raw = self.visible(ref.GetPath())
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
        return dict(id=eid(ident), element_id=eid(ident), source=source,
                    saved_path=raw, saved_absolute_path=saved_absolute, path_type=path_type,
                    kind=kind, loaded=load_intent(f.text(ref.GetLinkedFileStatus())),
                    optional_library=(kind=='AssemblyCodeTable' and path_type=='Content'),
                    td=td, special='native')

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

    def open_copy(self, path, discard=False):
        self.guard(path)
        info=self.basic(path)
        current = f.text(getattr(self.app, 'VersionNumber', ''))
        if current.isdigit() and info['version'].isdigit() and int(info['version']) > int(current):
            raise model_payload.PayloadError('Model saved in Revit '+info['version']+' cannot be opened by Revit '+current+'.')
        opts=self.DB.OpenOptions()
        worksets=None
        try:
            if info['workshared']:
                opts.DetachFromCentralOption=(self.DB.DetachFromCentralOption.DetachAndDiscardWorksets
                                              if discard else self.DB.DetachFromCentralOption.DetachAndPreserveWorksets)
                worksets=self.DB.WorksetConfiguration(self.DB.WorksetConfigurationOption.OpenAllWorksets)
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

    def scan(self, source, stage, options):
        self.guard(stage)
        info=self.basic(stage)
        current=f.text(getattr(self.app,'VersionNumber',''))
        if current.isdigit() and info['version'].isdigit() and int(info['version'])>int(current):
            raise model_payload.PayloadError('Saved Revit '+info['version']+' requires that version or newer; running '+current+'.')
        result=dict(references=[],issues=[],version=info['version'],opened_in_revit=False)
        if getattr(self, 'payloads', None): self.payloads.bind_stage(stage, source)
        if info.get('metadata_warning'):
            result['issues'].append(issue('BASIC_METADATA_FALLBACK',source,info['metadata_warning']))
        base=info.get('central') if info.get('workshared') and f.absolute(info.get('central', '')) else source
        try: result['references']=self.rows(stage, base)
        except Exception as exc: result['issues'].append(issue('SAVED_REFERENCE_SCAN_FAILED',source,exc,'error'))
        if not options.get('deep',True):
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
                if not doc.Close(False): raise RuntimeError('Temporary inspection document could not be closed.')
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
        self.guard(row['target'])
        path=row['target']
        if relative and model_path:
            pm=ntpath if f.is_windows(model_path) else os.path
            path=relative_path(path,pm.dirname(model_path))
        opts=self.DB.ImageTypeOptions(path,relative,self.DB.ImageTypeSource.Link)
        try:
            if row['target'].lower().endswith('.pdf'): opts.PageNumber=row['page']
            opts.Resolution=row['resolution']
            return opts
        except Exception:
            dispose(opts); raise

    def apply_metadata(self, path, target, rows, relative=True):
        self.guard(path); self.guard(target)
        for row in rows:
            if row.get('target'): self.guard(row['target'])
        td=self.DB.TransmissionData.ReadTransmissionData(self.mp(path))
        if td is None: return False
        try:
            ids=dict((eid(i),i) for i in td.GetAllExternalFileReferenceIds())
            for row in rows:
                ident_key=row.get('element_id',row['id'])
                if not row.get('target') or ident_key not in ids or row.get('loaded') is None: continue
                value=relative_path(row['target'],os.path.dirname(target)) if relative else row['target']
                typ=self.DB.PathType.Relative if relative else self.DB.PathType.Absolute
                td.SetDesiredReferenceData(ids[ident_key],self.mp(value),typ,bool(row['loaded']))
                row['repath']='TRANSMISSION_DATA' if relative else 'STAGING_ABSOLUTE'
            td.IsTransmitted=True
            self.DB.TransmissionData.WriteTransmissionData(self.mp(path),td)
            return True
        finally: dispose(td)

    def finish(self, stage, target, rows, options):
        self.guard(stage); self.guard(target)
        issues=[]
        info=self.basic(stage)
        for row in rows:
            if row.get('target'): self.guard(row['target'])
        transmitted=self.apply_metadata(stage,target,rows) if options.get('repath') else False
        special=[r for r in rows if r.get('target') and r.get('special')=='image' and not r.get('repath')]
        external=[r for r in rows if r.get('target') and not r.get('td') and r.get('kind')=='RevitLink']
        needs_document=options.get('cleanup') or options.get('upgrade') or (options.get('repath') and (special or external))
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
                        result=link.LoadFrom(self.mp(row['target']),None)
                        if f.text(result.LoadResult) not in ('LinkLoaded','LinkAlreadyLoaded'):
                            raise RuntimeError('Revit link reload failed: '+f.text(result.LoadResult))
                        dispose(result)
                        if row.get('loaded') is False: link.Unload(None)
                        row['repath']='API_LOCAL_LINK'
                if options.get('cleanup'):
                    from .cleanup import run
                    run(doc,self.DB,options,self.cancelled)
                save=self.DB.SaveAsOptions(); ws=None
                try:
                    save.OverwriteExistingFile=True; save.MaximumBackups=1
                    if doc.IsWorkshared:
                        ws=self.DB.WorksharingSaveAsOptions(); ws.SaveAsCentral=True
                        save.SetWorksharingOptions(ws)
                    doc.SaveAs(target,save)
                finally: dispose(ws); dispose(save)
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
                            row['repath']='API_IMAGE_RELATIVE'
                        except f.Cancelled: raise
                        except Exception as exc:
                            if tx.GetStatus()==self.DB.TransactionStatus.Started: tx.RollBack()
                            row['repath']='FAILED'
                            issues.append(issue('IMAGE_REPATH_FAILED',row.get('source',''),exc,'error'))
                        finally:
                            dispose(tx); dispose(opts)
                    doc.Save()
            finally:
                if doc is not None:
                    if not doc.Close(False): raise RuntimeError('Temporary output model could not be closed.')
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
        return issues

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
            from System import Int64,Int32
            for row in rows:
                if not row.get('target') or row.get('skip_repath'): continue
                kind=row.get('kind')
                if kind!='RevitLink' and row.get('special')!='image': continue
                f.check(self.cancelled)
                try:
                    number=int(row['element_id'])
                    ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
                    element=doc.GetElement(ident)
                    if element is None:
                        if options.get('cleanup'): continue
                        raise RuntimeError('Original reference element is missing from the packaged model.')
                    if kind=='RevitLink':
                        reference=element.GetExternalFileReference()
                        actual=self.visible(reference.GetAbsolutePath())
                        if row.get('loaded') is True and not self.DB.RevitLinkType.IsLoaded(doc,ident):
                            raise RuntimeError('Packaged Revit link did not load.')
                    else:
                        actual=f.resolve_source(f.text(element.Path),target) or f.text(element.Path)
                    if f.canonical(actual)!=f.canonical(row['target']):
                        raise RuntimeError('Packaged reference still points elsewhere: '+actual)
                    row['verification']='PATH_AND_LOAD_CHECKED' if kind=='RevitLink' else 'PATH_CHECKED'
                except Exception as exc:
                    issues.append(issue('LINK_VERIFICATION_FAILED',row.get('source',''),exc,'error'))
        except f.Cancelled: raise
        except Exception as exc:
            issues.append(issue('MODEL_OPEN_VERIFICATION_FAILED',target,exc,'error'))
        finally:
            if doc is not None and not doc.Close(False):
                issues.append(issue('MODEL_CLOSE_FAILED',target,'Verification document could not be closed.','error'))
        return issues
