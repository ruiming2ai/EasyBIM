# -*- coding: utf-8 -*-
"""Revit API boundary. All document writes are restricted to package copies."""
from __future__ import unicode_literals
import os
import shutil
from . import files as f
from .engine import issue


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

    def guard(self, path):
        if not f.within(path,self.root): raise ValueError('Model write outside package refused: ' + path)

    def mp(self, path): return self.DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
    def visible(self, path):
        return f.text(self.DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(path)) if path else ''

    def basic(self, path):
        info=self.DB.BasicFileInfo.Extract(path)
        try:
            return dict(version=f.text(info.Format),workshared=bool(info.IsWorkshared),
                        central=f.text(getattr(info,'CentralPath','')))
        finally: dispose(info)

    def rows(self, path):
        td=self.DB.TransmissionData.ReadTransmissionData(self.mp(path))
        rows=[]
        try:
            if td is None: return rows
            for ident in td.GetAllExternalFileReferenceIds():
                f.check(self.cancelled)
                ref=td.GetDesiredReferenceData(ident) if td.IsTransmitted else None
                if ref is None: ref=td.GetLastSavedReferenceData(ident)
                raw=self.visible(ref.GetPath())
                try: source=self.visible(ref.GetAbsolutePath())
                except Exception: source=raw if f.absolute(raw) or '://' in raw else ''
                state=f.text(ref.GetLinkedFileStatus())
                rows.append(dict(id=eid(ident),element_id=eid(ident),source=source,
                                 saved_path=raw,kind=f.text(ref.ExternalFileReferenceType),
                                 loaded=load_intent(state),td=True,special='native'))
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
        result=dict(references=[],issues=[],version=info['version'])
        try: result['references']=self.rows(source)
        except Exception as exc: result['issues'].append(issue('SAVED_REFERENCE_SCAN_FAILED',source,exc,'error'))
        if not options.get('deep',True):
            result['issues'].append(issue('METADATA_ONLY_SCAN',source,
                                         'Image/PDF, cloud, point-cloud and some other dependencies can be absent from saved reference metadata.'))
            return result
        doc=None
        try:
            self.prepare_scan(stage,result['references'])
            doc=self.open_copy(stage)
            self.scan_open(doc,source,info,result)
        except f.Cancelled: raise
        except Exception as exc:
            result['issues'].append(issue('DEEP_SCAN_FAILED',source,
                                         'Saved metadata was retained, but deeper dependency inspection failed: '+f.text(exc),'error'))
        finally:
            if doc is not None:
                if not doc.Close(False): raise RuntimeError('Temporary inspection document could not be closed.')
        return result

    def scan_open(self, doc, source, info, result):
        refs, issues=result['references'],result['issues']
        by_id=dict((r['id'],r) for r in refs)
        base=info['central'] if info['workshared'] and f.absolute(info['central']) else source
        imported=set(eid(i.GetTypeId()) for i in self.elements(doc,'ImportInstance') if not i.IsLinked)
        seen=set(by_id)
        def add(row):
            if row['id'] in by_id:
                old=by_id[row['id']]
                row['td']=old.get('td',False)
                if old.get('source'): row['source']=old['source']
                if row.get('special')!='image': row['loaded']=old.get('loaded')
                old.update(row)
            else:
                refs.append(row); by_id[row['id']]=row
            seen.add(row['id'])
        for image in self.elements(doc,'ImageType'):
            f.check(self.cancelled)
            try:
                key=eid(image.Id); seen.add(key)
                if f.text(image.Source)!='Link':
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
                key=eid(point.Id); raw=f.text(point.GetPath())
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
                raw=self.visible(ref.GetPath())
                add(dict(id=key,element_id=key,source=f.resolve_source(raw,base) or raw,
                         kind=f.text(ref.ExternalFileReferenceType),loaded=load_intent(f.text(ref.GetLinkedFileStatus())),
                         special='native',td=False))
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
                if key in seen or key in imported: continue
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
                    # The display path is not a resource identity. Cloud/server paths require
                    # an explicit prefix mapping. No name search, live model fetch or cache scraping.
                    row=dict(id=key+':'+f.text(index),element_id=key,source=display,kind=kind,
                             special='external',loaded=None,td=False,server=f.text(resource.ServerId),
                             resource_version=f.text(resource.Version),resource_information=metadata,
                             note='External-resource identity recorded; local/prefix-mapped copy is not a cloud version download.')
                    add(row)
                    if resource.Version:
                        issues.append(issue('EXTERNAL_RESOURCE_VERSION_UNVERIFIED',display,
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

    def image_options(self, row, relative):
        self.guard(row['target'])
        opts=self.DB.ImageTypeOptions(row['target'],relative,self.DB.ImageTypeSource.Link)
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
                if not row.get('target') or row['id'] not in ids or row.get('loaded') is None: continue
                value=os.path.relpath(row['target'],os.path.dirname(target)) if relative else row['target']
                typ=self.DB.PathType.Relative if relative else self.DB.PathType.Absolute
                td.SetDesiredReferenceData(ids[row['id']],self.mp(value),typ,bool(row['loaded']))
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
        external=[r for r in rows if r.get('target') and r.get('special')=='external' and r.get('kind')=='RevitLink']
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
                        row['repath']='API_LOCAL_LINK'
                    for row in special:
                        f.check(self.cancelled)
                        number=int(row['element_id'])
                        ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
                        image=doc.GetElement(ident)
                        # First save establishes the package central-path base; use absolute paths
                        # during the first reload, then a second relative-path reload after SaveAs.
                        opts=self.image_options(row,False)
                        tx=self.DB.Transaction(doc,'e-transmit: relink package image')
                        tx.Start()
                        try:
                            image.ReloadFrom(opts)
                            if row.get('loaded') is False: image.Unload()
                            if tx.Commit()!=self.DB.TransactionStatus.Committed: raise RuntimeError('Image reload not committed.')
                        except Exception:
                            if tx.GetStatus()==self.DB.TransactionStatus.Started: tx.RollBack()
                            raise
                        finally: dispose(tx); dispose(opts)
                        row['repath']='API_IMAGE_ABSOLUTE'
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
                    # After SaveAs the central/project base is the final output folder.
                    tx=self.DB.Transaction(doc,'e-transmit: relative image paths')
                    tx.Start()
                    try:
                        for row in special:
                            number=int(row['element_id'])
                            ident=self.DB.ElementId(Int64(number) if int(self.app.VersionNumber)>=2024 else Int32(number))
                            image=doc.GetElement(ident)
                            if image is None:
                                row['repath']='REMOVED_BY_CLEANUP'; continue
                            opts=self.image_options(row,True)
                            try:
                                image.ReloadFrom(opts)
                                if row.get('loaded') is False: image.Unload()
                                row['repath']='API_IMAGE_RELATIVE'
                            finally: dispose(opts)
                        if tx.Commit()!=self.DB.TransactionStatus.Committed: raise RuntimeError('Relative path transaction not committed.')
                    except Exception:
                        if tx.GetStatus()==self.DB.TransactionStatus.Started: tx.RollBack()
                        raise
                    finally: dispose(tx)
                    doc.Save()
            finally:
                if doc is not None:
                    if not doc.Close(False): raise RuntimeError('Temporary output model could not be closed.')
            if options.get('repath'): self.apply_metadata(target,target,rows)
        if options.get('repath'):
            for row in rows:
                if row.get('target') and not row.get('repath'):
                    row['repath']='MANUAL_REPAIR_REQUIRED'
                    issues.append(issue('REPATH_NOT_AVAILABLE',row.get('source',''),
                                        'Source copied, but this reference cannot be repathed safely by the exposed API. '
                                        'Repair in the packaged model and verify after moving the package.'))
        return issues
