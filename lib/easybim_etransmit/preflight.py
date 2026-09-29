# -*- coding: utf-8 -*-
"""Source save/reload choices. SaveAs is allowed only for explicit detached recovery."""
from __future__ import unicode_literals
import os
from . import files as f, performance
from .engine import issue


class PreflightError(RuntimeError):
    pass


def _native_rvt(path):
    from . import model_payload
    try:
        return model_payload.probe(path).get('container') == 'CFB'
    except Exception:
        return False


def _cloud_identity(doc):
    path = None
    try:
        path = doc.GetCloudModelPath()
        model = f.text(path.GetModelGUID()).lower().strip('{}')
        project = f.text(path.GetProjectGUID()).lower().strip('{}')
        region = f.text(getattr(path, 'Region', '') or '')
    except Exception:
        return {}
    if not model or not project:
        return {}
    return dict(model_guid=model, project_guid=project, region=region)


def _validate_local_primary(path, model_name, evidence):
    path = f.text(path or '').strip()
    if (not f.absolute(path) or f.cache_source(path)
            or f.is_desktop_connector_path(path)):
        raise PreflightError(
            'This detached model does not expose an exact saved RVT source.\n\n'
            'e-transmit cannot safely determine which RVT should be transmitted. '
            'Save the detached model first, then run e-transmit again.\n\n'
            'EasyBIM will not Save As, search by filename, or substitute another model.'
        )
    if not path.lower().endswith('.rvt') or not os.path.isfile(path):
        raise PreflightError(
            'The exact saved RVT source for {0} is unavailable:\n{1}\n\n'
            'Restore or save the exact source, then run e-transmit again. '
            'No similarly named file will be substituted.'.format(model_name, path)
        )
    if not _native_rvt(path):
        raise PreflightError(
            'The identified saved source for {0} is not a readable native RVT:\n{1}\n\n'
            'No substitute source was used.'.format(model_name, path)
        )
    return dict(path=path, evidence=evidence, mode='LOCAL_SAVED_RVT')


def _dispose(value):
    try:
        if value is not None and hasattr(value, 'Dispose'):
            value.Dispose()
    except Exception:
        pass


def _identity_path(value):
    return f.text(value or '').strip().replace('/', '\\').rstrip('\\').lower()


def _unique_detached_save_path(output, doc):
    if not output or not os.path.isdir(output):
        raise PreflightError('The eTransmit destination folder is unavailable.')
    title = f.text(getattr(doc, 'Title', '') or '').strip() or 'Detached Model'
    title = title.replace('/', '_').replace('\\', '_')
    if not title.lower().endswith('.rvt'):
        title += '.rvt'
    stem, ext = os.path.splitext(title)
    target = os.path.join(output, title)
    index = 2
    while os.path.exists(target):
        target = os.path.join(output, stem + '_' + f.text(index) + ext)
        index += 1
    try:
        f.validate_destination_path(target)
    except Exception as exc:
        raise PreflightError('The detached-model save path is not valid: '+f.text(exc))
    return target


def save_detached_current(doc, output, DB=None):
    """Explicitly SaveAs the current detached state into the chosen output folder."""
    if DB is None:
        raise PreflightError('Revit Save As services are unavailable.')
    if (getattr(doc, 'IsReadOnly', False) or getattr(doc, 'IsLinked', False)
            or getattr(doc, 'IsModifiable', False)):
        raise PreflightError(
            'The detached model cannot be saved in its current document state. '
            'No alternate Save As was attempted.'
        )

    target = _unique_detached_save_path(output, doc)
    save = worksharing = None
    try:
        save = DB.SaveAsOptions()
        save.OverwriteExistingFile = False
        try:
            save.MaximumBackups = 1
        except Exception:
            pass
        if bool(getattr(doc, 'IsWorkshared', False)):
            worksharing = DB.WorksharingSaveAsOptions()
            worksharing.SaveAsCentral = True
            save.SetWorksharingOptions(worksharing)
        performance.call('preflight', 'detached_save_as', target, doc.SaveAs, target, save)
    except Exception as exc:
        raise PreflightError(
            'Save As failed for the detached model: '+f.text(exc)+
            '. No Sync, Publish, alternate filename search, or source substitution was attempted.'
        )
    finally:
        _dispose(worksharing)
        _dispose(save)

    path_after = f.text(getattr(doc, 'PathName', '') or '')
    if _identity_path(path_after) != _identity_path(target):
        raise PreflightError(
            'Revit did not leave the open model at the new eTransmit save location. '
            'Transmission stopped so the working model state can be reviewed.'
        )
    _validate_local_primary(target, f.text(getattr(doc, 'Title', '') or ''), 'USER_SAVE_CURRENT')
    return target


def _adopt_detached_saved_source(row, path, evidence):
    """Use one saved RVT as a standalone source after explicit recovery."""
    row.ResolvedSource = path
    row.Source = path
    row.Mode = 'SAVED_FILE'
    row.DetachedRecovery = evidence
    row.Document = None
    row.Modified = False


def _bind_detached_browse_source(row, path):
    """Keep the open detached document for link discovery, but transmit saved bytes.

    The user's explicit RVT supplies the host file. The already-open detached
    document remains available only to discover/load Revit links; it is never
    saved by this recovery path.
    """
    row.ResolvedSource = path
    row.Source = path
    row.DetachedRecovery = 'USER_BROWSE'


def _recover_detached_source(row, doc, application, DB, output, detached_recovery, initial_error=''):
    feedback = f.text(initial_error or '')
    while True:
        resolution = detached_recovery(row, feedback) if detached_recovery else None
        if not resolution:
            raise f.Cancelled()
        action = f.text(resolution.get('action', '') or '').upper()
        feedback = ''

        if action == 'SAVE_CURRENT':
            path = save_detached_current(doc, output, DB)
            ready = _validate_local_primary(path, f.text(getattr(row, 'Name', '') or ''), 'USER_SAVE_CURRENT')
            _adopt_detached_saved_source(row, path, 'USER_SAVE_CURRENT')
            return ready

        if action == 'BROWSE':
            path = f.text(resolution.get('path', '') or '').strip()
            if not path:
                raise f.Cancelled()
            try:
                # The user's explicit file selection is authoritative. Validate
                # only that it is a readable native RVT; do not compare it to
                # the open detached document or block on fragile model identity.
                ready = _validate_local_primary(
                    path, f.text(getattr(row, 'Name', '') or ''), 'USER_BROWSE'
                )
            except PreflightError as exc:
                feedback = f.text(exc)
                continue
            _bind_detached_browse_source(row, ready['path'])
            return ready

        if action == 'CANCEL':
            raise f.Cancelled()
        raise PreflightError('Unknown detached-model recovery choice.')


def primary_host_sources(choices, application=None, DB=None, output='', detached_recovery=None):
    """Prove every primary host source before any dependency collection starts.

    Stores ResolvedSource on each UI row so the later Registry receives the
    already-proven opening source for pathless detached documents.
    """
    from . import source_tracker
    results = []
    for row in choices:
        mode = f.text(getattr(row, 'Mode', '') or '')
        name = f.text(getattr(row, 'Name', '') or '')
        if mode == 'SAVED_FILE':
            try:
                path = f.resolve_source(getattr(row, 'Source', ''), mappings=[])
            except ValueError:
                path = None
            ready = _validate_local_primary(path, name, 'SAVED_FILE_SELECTION')
            row.ResolvedSource = ready['path']
            results.append(ready)
            continue
        if mode != 'LIVE_DOCUMENT':
            raise PreflightError('Unsupported source mode for '+name+': '+mode)

        doc = getattr(row, 'Document', None)
        if doc is None:
            raise PreflightError('Open-document source is no longer available for '+name+'.')
        if bool(getattr(doc, 'IsModelInCloud', False)):
            identity = _cloud_identity(doc)
            if not identity:
                raise PreflightError(
                    'The live ACC model identity could not be verified for '+name+'. '
                    'Reopen the model from Revit Home → Autodesk Docs, then retry.'
                )
            row.ResolvedSource = ''
            results.append(dict(path='', evidence='LIVE_ACC_IDENTITY',
                                mode='ACC_LIVE', cloud=identity))
            continue

        direct = f.text(getattr(doc, 'PathName', '') or '').strip()
        if direct:
            path, evidence = direct, 'DOCUMENT_PATH'
            try:
                ready = _validate_local_primary(path, name, evidence)
                row.ResolvedSource = ready['path']
                results.append(ready)
                continue
            except PreflightError as exc:
                if detached_recovery is None or not bool(getattr(doc, 'IsDetached', False)):
                    raise
                ready = _recover_detached_source(
                    row, doc, application, DB, output, detached_recovery, f.text(exc))
                results.append(ready)
                continue

        path, evidence = source_tracker.source_for_document_with_evidence(
            doc, application=application)
        if path:
            try:
                ready = _validate_local_primary(path, name, evidence or 'UNVERIFIED')
                row.ResolvedSource = ready['path']
                results.append(ready)
                continue
            except PreflightError as exc:
                if detached_recovery is None:
                    raise
                ready = _recover_detached_source(
                    row, doc, application, DB, output, detached_recovery, f.text(exc))
                results.append(ready)
                continue

        if detached_recovery is not None:
            ready = _recover_detached_source(
                row, doc, application, DB, output, detached_recovery)
            results.append(ready)
            continue

        ready = _validate_local_primary(path, name, evidence or 'UNVERIFIED')
        row.ResolvedSource = ready['path']
        results.append(ready)
    return results


def save_selected(choices, decision):
    """Apply one explicit in-place save decision to distinct selected open docs."""
    if decision == 'cancel':
        raise f.Cancelled()
    if decision not in ('continue', 'save'):
        raise PreflightError('Unknown source-save choice; nothing was saved.')
    events=[];seen=[]
    for choice in choices:
        doc=getattr(choice,'Document',None)
        if doc is None or any(doc is old or doc==old for old in seen):continue
        seen.append(doc)
        if not bool(getattr(doc,'IsModified',False)):continue
        event=dict(model=f.text(getattr(doc,'Title','')),source=f.text(getattr(doc,'PathName','') or ''),action='CONTINUED_WITHOUT_SAVE')
        if decision == 'save':
            if (getattr(doc,'IsReadOnly',False) or getattr(doc,'IsLinked',False)
                    or getattr(doc,'IsModifiable',False)):
                raise PreflightError('Cannot save '+event['model']+' in the current document state. No fallback save was attempted.')
            before=f.text(getattr(doc,'PathName','') or '')
            if not before:
                raise PreflightError('Save '+event['model']+' using native Revit first. This tool will not use Save As.')
            try:
                performance.call('preflight','source_save',before,doc.Save)
            except Exception as exc:
                raise PreflightError('Save failed for '+event['model']+': '+f.text(exc)+'. No SaveAs, Sync or Publish fallback was attempted. Earlier explicitly requested saves may already have completed.')
            if before!=f.text(getattr(doc,'PathName','') or ''):
                raise PreflightError('The source location changed during saving. Export stopped; review the working model.')
            if bool(getattr(doc,'IsModified',False)):
                raise PreflightError('The model is still modified after Save. Export stopped because Save and continue did not produce a clean saved state.')
            event['action']='SAVED_IN_PLACE'
        events.append(event)
    return events


def link_type_name(DB,link):
    """Read the actual type name, including IronPython's inherited Name property."""
    getters=(lambda:link.Name,lambda:DB.Element.Name.GetValue(link),
             lambda:link.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString())
    for getter in getters:
        try:
            name=f.text(getter() or '').strip()
            if name:return name
        except Exception:pass
    return ''


def placed_revit_link_type_ids(registry, doc):
    """Return direct RevitLinkType ids that actually have placed host instances.

    Revit can retain unused/outdated RevitLinkType definitions after every
    instance has been removed. Those types are not host dependencies and must
    never appear in the temporary-reload decision list.
    """
    from .revit import eid
    try:
        instances=registry.scanner.elements(doc,'RevitLinkInstance')
    except Exception:
        return None
    placed=set()
    for instance in instances:
        try:
            placed.add(eid(instance.GetTypeId()))
        except Exception:
            pass
    return placed


def enrich_link_types(registry,entry):
    """Preserve direct unloaded type names even when no child Document is loaded."""
    from .revit import eid
    from . import cache_sources
    doc=entry.get('document')
    if doc is None:return
    rows=entry['inventory']['references']
    placed=placed_revit_link_type_ids(registry,doc)
    try:types=registry.scanner.elements(doc,'RevitLinkType')
    except Exception:return
    for link in types:
        f.check(registry.cancelled)
        if bool(getattr(link,'IsNestedLink',False)):continue
        ident=eid(link.Id)
        if placed is not None and ident not in placed:continue
        matches=[r for r in rows if r.get('kind')=='RevitLink' and r.get('element_id')==ident]
        if not matches:
            row=None
            try:
                ref=link.GetExternalFileReference()
                row=registry.scanner.reference_row(ref,link.Id,entry.get('original_path',''))
            except Exception:pass
            if row is None:
                try:
                    resources=link.GetExternalResourceReferences()
                    values=list(resources.Values) if hasattr(resources,'Values') else list(resources.values())
                    for resource in values:
                        data=resource.GetReferenceInformation()
                        info=dict((f.text(k),f.text(data[k])) for k in data.Keys) if hasattr(data,'Keys') else dict(data)
                        candidate=dict(resource_information=info)
                        identity=cache_sources.reference_identity(candidate)
                        if identity:
                            row=dict(id=ident,element_id=ident,kind='RevitLink',td=False,
                                     special='external',source='',cloud_identity=identity,resource_information=info)
                            break
                except Exception:pass
            if row is None:continue
            rows.append(row);matches=[row]
        name=link_type_name(registry.DB,link)
        for row in matches:
            if name:row['link_name']=name
            try:row['loaded']=bool(registry.DB.RevitLinkType.IsLoaded(doc,link.Id))
            except Exception:pass
            row['local_unload_override']=bool(getattr(link,'LocallyUnloaded',False))


class ReloadChoice(object):
    def __init__(self,registry,owner,link,row,checked=False):
        from . import cache_sources
        self.registry=registry;self.owner=owner;self.link=link
        self.element_id=row['element_id'];self.original_loaded=False
        self.local_override=bool(getattr(link,'LocallyUnloaded',False))
        self.Name=row.get('link_name') or 'Revit link '+self.element_id
        self.Host=registry.get(owner)['name'];self.Checked=bool(checked)
        source=row.get('source','')
        cloud=bool(cache_sources.reference_identity(row))
        if cloud and self.Checked:
            self.Availability='ACC/cloud link — reload selected by default to acquire its saved cache'
        else:
            self.Availability=('Saved file available; reload is optional' if f.absolute(source) and f.file_exists(source)
                               else 'Identified source/cache will be checked; select only to request a reload')
        self.State='Unloaded for me' if self.local_override else 'Unloaded'
    def is_loaded(self):
        doc=self.registry.get(self.owner)['document']
        return bool(self.registry.DB.RevitLinkType.IsLoaded(doc,self.link.Id))
    def is_local(self):return bool(getattr(self.link,'LocallyUnloaded',False))


def unloaded_links(registry,keys):
    from .revit import eid
    choices=[]
    for key in keys:
        entry=registry.get(key);doc=entry.get('document')
        if doc is None:continue
        rows=entry['inventory']['references']
        placed=placed_revit_link_type_ids(registry,doc)
        for link in registry.scanner.elements(doc,'RevitLinkType'):
            if bool(getattr(link,'IsNestedLink',False)):continue
            ident=eid(link.Id)
            if placed is not None and ident not in placed:continue
            row=next((r for r in rows if r.get('kind')=='RevitLink' and r.get('element_id')==ident),None)
            if row is None:continue
            try:loaded=bool(registry.DB.RevitLinkType.IsLoaded(doc,link.Id))
            except Exception:loaded=row.get('loaded')
            if loaded is False:
                from . import cache_sources
                default_reload=(entry.get('detached_recovery')=='USER_BROWSE'
                                and bool(cache_sources.reference_identity(row)))
                choices.append(ReloadChoice(registry,key,link,row,default_reload))
    return choices


class TemporaryReloads(object):
    """Snapshot first; acquire checked links; restore each before processing outputs."""
    def __init__(self,registry,selected,pulse=None):
        self.registry=registry;self.selected=list(selected);self.pulse=pulse;self.touched=[]
    def __enter__(self):return self
    def __exit__(self,*args):self.restore()
    def _event(self,choice,action):
        self.registry.get(choice.owner).setdefault('preflight_events',[]).append(
            dict(action=action,element_id=choice.element_id,link=choice.Name))
    def _problem(self,choice,code,exc):
        self.registry.get(choice.owner)['inventory'].setdefault('issues',[]).append(
            issue(code,choice.owner,choice.Name+': '+f.text(exc),'error'))
    def acquire(self):
        owners=[]
        for choice in self.selected:
            if choice.owner not in owners:owners.append(choice.owner)
        for owner in owners:
            f.check(self.registry.cancelled)
            self.registry.snapshot(owner,self.pulse)
        for choice in self.selected:
            f.check(self.registry.cancelled)
            doc=self.registry.get(choice.owner)['document']
            if getattr(doc,'IsReadOnly',False) or getattr(doc,'IsModifiable',False):
                self._problem(choice,'SOURCE_LINK_RELOAD_FAILED','Document is read-only or has an open transaction.');continue
            if choice.is_loaded() or choice.is_local()!=choice.local_override:
                self._problem(choice,'SOURCE_LINK_STATE_CHANGED','Link load state changed after selection; no source change attempted.');continue
            result=None
            try:
                checker=getattr(choice.link,'IsNotLoadedIntoMultipleOpenDocuments',None)
                if checker and not checker():
                    raise PreflightError('Link is shared by multiple open documents; close the other host before requesting reload.')
                self.touched.append(choice)
                if self.pulse:self.pulse('Reloading selected link | '+choice.Name,0,1)
                self._event(choice,'TEMPORARY_RELOAD_REQUESTED')
                if choice.local_override:
                    performance.call('preflight','link_revert_local_unload',choice.Name,choice.link.RevertLocalUnloadStatus)
                else:
                    result=performance.call('preflight','link_reload',choice.Name,choice.link.Reload)
                    if f.text(getattr(result,'LoadResult','')) not in ('LinkLoaded','LinkAlreadyLoaded'):
                        raise PreflightError('Reload did not return a loaded result.')
                if not choice.is_loaded():raise PreflightError('Link is still unloaded; workshared load state was not overridden.')
                self.registry.capture_reloaded_link(choice)
                self._event(choice,'RELOADED_SAVED_FILE_ACQUIRED')
            except f.Cancelled:raise
            except Exception as exc:self._problem(choice,'SOURCE_LINK_RELOAD_FAILED',exc)
            finally:
                try:
                    if result is not None and hasattr(result,'Dispose'):result.Dispose()
                finally:self.restore()
    def restore(self):
        failed=[]
        for choice in list(reversed(self.touched)):
            try:
                if choice.local_override:
                    if not choice.is_local():
                        performance.call('preflight','link_restore_local_unload',choice.Name,choice.link.UnloadLocally,None)
                    if not choice.is_local() or choice.is_loaded():raise PreflightError('Local unloaded state was not restored.')
                else:
                    if choice.is_loaded():
                        performance.call('preflight','link_restore_unload',choice.Name,choice.link.Unload,None)
                    if choice.is_loaded():raise PreflightError('Original unloaded state was not restored.')
                self._event(choice,'ORIGINAL_UNLOADED_STATE_RESTORED')
            except Exception as exc:
                failed.append(choice.Name);self._problem(choice,'SOURCE_LINK_RESTORE_FAILED',exc)
            finally:self.touched.remove(choice)
        if failed:
            raise PreflightError('Could not restore unloaded links: '+', '.join(failed)+'. Review Manage Links now; no automatic Save or Sync was performed.')
