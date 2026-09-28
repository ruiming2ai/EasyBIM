# -*- coding: utf-8 -*-
"""Explicit source save/reload choices. Never SaveAs, Sync, Publish or close a source."""
from __future__ import unicode_literals
from . import files as f, performance
from .engine import issue


class PreflightError(RuntimeError):
    pass


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


def enrich_link_types(registry,entry):
    """Preserve direct unloaded type names even when no child Document is loaded."""
    from .revit import eid
    from . import cache_sources
    doc=entry.get('document')
    if doc is None:return
    rows=entry['inventory']['references']
    try:types=registry.scanner.elements(doc,'RevitLinkType')
    except Exception:return
    for link in types:
        f.check(registry.cancelled)
        if bool(getattr(link,'IsNestedLink',False)):continue
        ident=eid(link.Id)
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
    def __init__(self,registry,owner,link,row):
        self.registry=registry;self.owner=owner;self.link=link
        self.element_id=row['element_id'];self.original_loaded=False
        self.local_override=bool(getattr(link,'LocallyUnloaded',False))
        self.Name=row.get('link_name') or 'Revit link '+self.element_id
        self.Host=registry.get(owner)['name'];self.Checked=False
        source=row.get('source','')
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
        for link in registry.scanner.elements(doc,'RevitLinkType'):
            if bool(getattr(link,'IsNestedLink',False)):continue
            ident=eid(link.Id)
            row=next((r for r in rows if r.get('kind')=='RevitLink' and r.get('element_id')==ident),None)
            if row is None:continue
            try:loaded=bool(registry.DB.RevitLinkType.IsLoaded(doc,link.Id))
            except Exception:loaded=row.get('loaded')
            if loaded is False:choices.append(ReloadChoice(registry,key,link,row))
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
