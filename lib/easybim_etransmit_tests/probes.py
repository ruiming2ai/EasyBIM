# -*- coding: utf-8 -*-
"""Native Revit probes. Never promoted as a production transmittal."""
from __future__ import unicode_literals
import copy
import os
import ntpath
from . import scenarios
from .base.revit import Backend, dispose, eid
from .base import files as f, cache_sources

class ProbeBackend(Backend):
    def __init__(self,DB,app,root,evidence):
        Backend.__init__(self,DB,app,root)
        self.evidence=evidence
        self.dirty_seen=False
        self.owned_documents=[]
    def phase(self,name,**values):
        self.evidence.phase=name;self.evidence.write('phase',**values)
    def open_copy(self,path,discard=False,**kwargs):
        self.guard(path)
        self.phase('open_copy',path=path,options=kwargs)
        doc=Backend.open_copy(self,path,discard,**kwargs)
        actual=f.text(getattr(doc,'PathName',''))
        # A detached document may have no PathName. It must still be a newly
        # returned task doc, never a linked doc or an existing open document.
        if bool(getattr(doc,'IsLinked',False)):
            raise RuntimeError('A linked read-only document was returned; refusing to use it.')
        # Revit can report a filename-only PathName for a detached working copy.
        # That value is observational, not a write destination. Track the task
        # document first so every rejection path can still close it safely.
        self.owned_documents.append(doc)
        self.evidence.track(doc)
        self.evidence.snapshot(doc,'after open')
        if actual and '://' in actual:
            raise RuntimeError('A task copy unexpectedly resolved to a source/cloud document: '+actual)
        if actual and (os.path.isabs(actual) or ntpath.isabs(actual)):
            self.guard(actual)
        elif actual:
            self.evidence.write('non_absolute_task_pathname',path=actual,
                                note='Observed only; all writes still use guarded absolute trial targets.')
        return doc
    def _save_as_independent_package_central(self,doc,target,clear_transmitted=False):
        self.phase('baseline_SaveAs',target=target)
        self.evidence.snapshot(doc,'before baseline SaveAs')
        try:return Backend._save_as_independent_package_central(self,doc,target,clear_transmitted)
        finally:self.evidence.snapshot(doc,'after baseline SaveAs')
    def apply_metadata(self,path,target,rows,**kwargs):
        self.phase('metadata',path=path,target=target,options=kwargs)
        written=Backend.apply_metadata(self,path,target,rows,**kwargs)
        self.evidence.write('metadata_result',written=written,
            activated_ids=[f.text(r.get('element_id')) for r in rows if r.get('repath') in ('STAGING_ABSOLUTE','TRANSMISSION_DATA')])
        return written
    def close_document(self,doc):
        self.phase('close_without_save')
        if not doc.Close(False):raise RuntimeError('Working document could not close; no output accepted.')
        if doc in self.owned_documents:self.owned_documents.remove(doc)
    def cleanup_documents(self):
        failures=[]
        for doc in list(self.owned_documents):
            try:
                if getattr(doc,'IsValidObject',True):self.close_document(doc)
            except Exception as exc:failures.append(f.text(exc))
        if failures:raise RuntimeError('Task documents remain open: '+'; '.join(failures))
    def observed_save_as(self,doc,target,clear_transmitted=False,strict=False):
        """Observe SaveAs; tolerant arm discards unsaved post-save changes on close.

        This is NOT a relaxation of production safety. These files stay inside
        a marked experiment, dirty state is retained, and no output is promoted.
        """
        self.guard(target);self.phase('first_SaveAs',target=target,strict=strict)
        self.evidence.snapshot(doc,'before SaveAs')
        save=self.DB.SaveAsOptions();ws=None
        try:
            save.OverwriteExistingFile=False;save.MaximumBackups=1
            if doc.IsWorkshared:
                ws=self.DB.WorksharingSaveAsOptions();ws.SaveAsCentral=True
                if clear_transmitted:ws.ClearTransmitted=True
                save.SetWorksharingOptions(ws)
            doc.SaveAs(target,save)
        finally:dispose(ws);dispose(save)
        state=self.evidence.snapshot(doc,'after SaveAs')
        self.dirty_seen=self.dirty_seen or bool(doc.IsModified)
        if f.canonical(doc.PathName)!=f.canonical(target):raise RuntimeError('SaveAs did not select the test path.')
        if strict and bool(doc.IsModified):raise RuntimeError('REPRODUCED_POST_SAVE_DIRTY_GUARD')
        # No automatic follow-up save of unknown callback edits. Close(False)
        # then normal reopen tests what is actually saved on disk.
        return state
    def normalize(self,stage,target,metadata_rows=None,strict=False):
        if metadata_rows:
            self.apply_metadata(stage,target,metadata_rows,relative=False)
        transmitted=self._package_is_transmitted(stage)
        doc=None
        try:
            # Same common closed-workset preparation in each paired arm.
            doc=self.open_copy(stage,close_worksets=True)
            self.observed_save_as(doc,target,transmitted,strict)
        finally:
            if doc is not None:self.close_document(doc)
        self.verify_independent_package(target,[],{'repath':False})
        doc=self.open_copy(target,detach=False,close_worksets=True)
        if bool(doc.IsDetached):
            self.close_document(doc)
            raise RuntimeError('Saved test host reopens detached. Rejected.')
        self.dirty_seen=self.dirty_seen or bool(doc.IsModified)
        return doc
    def ident(self,value):
        from System import Int64,Int32
        return self.DB.ElementId(Int64(int(value)) if int(self.app.VersionNumber)>=2024 else Int32(int(value)))
    def actual_reference(self,doc,row):
        try:
            ident=self.ident(row['element_id']);element=doc.GetElement(ident)
        except Exception as exc:return dict(present=None,element_id=f.text(row.get('element_id')),error=f.text(exc))
        if element is None:return dict(present=False,element_id=f.text(row['element_id']))
        result=dict(present=True,element_id=eid(element.Id),unique_id=f.text(element.UniqueId),
                    type=f.text(element.GetType().FullName))
        ref=mp=raw=None
        try:
            if row.get('special')=='image':
                result.update(path=f.text(element.Path),path_type=f.text(element.PathType),
                              absolute=f.resolve_source(f.text(element.Path),doc.PathName) or f.text(element.Path))
            else:
                ref=self.DB.ExternalFileUtils.GetExternalFileReference(doc,ident)
                if ref is not None:
                    mp=ref.GetAbsolutePath();raw=ref.GetPath()
                    result.update(absolute=self.visible(mp),path=self.visible(raw),path_type=f.text(ref.PathType))
                else:result['error']='No native reference; external resources recorded below.'
        except Exception as exc:result['error']=f.text(exc)
        finally:dispose(raw);dispose(mp);dispose(ref)
        # Native metadata may be unavailable for an external/cloud link.
        # Read its actual Revit resource identity independently of native data.
        if hasattr(element,'GetExternalResourceReferences'):
            try:
                resources=[]
                references=element.GetExternalResourceReferences()
                values=references.Values if hasattr(references,'Values') else references.values()
                for resource in values:
                    information=resource.GetReferenceInformation()
                    keys=information.Keys if hasattr(information,'Keys') else information.keys()
                    resources.append(dict(in_session=f.text(resource.InSessionPath),
                        server=f.text(getattr(resource,'ServerId','')),
                        version=f.text(getattr(resource,'Version','')),
                        information=dict((f.text(k),f.text(information[k])) for k in keys)))
                result['resources']=resources
            except Exception as exc:result['resources_error']=f.text(exc)
        return result
    def verify_identity(self,doc,row):
        before=self.actual_reference(doc,row)
        expected=row.get('native_source') or row.get('source') or ''
        actual=before.get('absolute') or ''
        if not before.get('present'):raise RuntimeError('Reference absent in saved host: '+f.text(row['element_id']))
        if expected and actual and scenarios.key_path(expected)!=scenarios.key_path(actual):
            raise RuntimeError('Saved/live original path mismatch; no guessed replacement for '+f.text(row['element_id']))
        if row.get('kind')=='CADLink' and (not actual or not before.get('type','').endswith('.CADLinkType')):
            raise RuntimeError('Saved CAD type/path identity is unverified.')
        return before
    def cad_repair(self,doc,row,relative=False):
        before=self.verify_identity(doc,row)
        self.guard(row['target'])
        if not os.path.isfile(row['target']):raise RuntimeError('Collected CAD target missing.')
        ident=self.ident(row['element_id']);element=doc.GetElement(ident)
        is_link=getattr(element,'IsLink',None)
        if callable(is_link):is_link=is_link()
        if is_link is False:raise RuntimeError('Imported CAD is not a linked CAD type.')
        result=resource=mp=None
        try:
            self.phase('CAD_LoadFrom',element_id=row['element_id'],target=row['target'],relative=relative)
            if relative:
                mp=self.mp(row['target'])
                resource=self.DB.ExternalResourceReference.CreateLocalResource(doc,
                    self.DB.ExternalResourceTypes.BuiltInExternalResourceTypes.CADLink,
                    mp,self.DB.PathType.Relative)
                result=element.LoadFrom(resource)
            else:result=element.LoadFrom(row['target'])
            status=f.text(result.LoadResult)
            returned=eid(result.ElementId)
            row['api_result']=dict(load_result=status,returned_element_id=returned)
            self.evidence.write('cad_load_result',element_id=row['element_id'],before=before,result=row['api_result'])
            if returned!=f.text(row['element_id']):
                raise RuntimeError('CAD_TARGET_COLLISION: returned another link type '+returned)
            if status not in ('LinkLoaded','LinkAlreadyLoaded'):
                raise RuntimeError('CAD reload result: '+status)
            row['repath']='API_CAD_LINK'
            row['after_api']=self.actual_reference(doc,row)
        finally:dispose(result);dispose(resource);dispose(mp)
    def save_repair_and_verify(self,doc,target,rows):
        self.phase('save_document_repairs')
        self.evidence.snapshot(doc,'before Save')
        doc.Save()
        self.dirty_seen=self.dirty_seen or bool(doc.IsModified)
        self.evidence.snapshot(doc,'after Save')
        self.close_document(doc)
        self.verify_independent_package(target,[],{'repath':False})
        doc=self.open_copy(target,detach=False,close_worksets=True)
        try:
            if doc.IsDetached:raise RuntimeError('Final test candidate reopens detached.')
            self.dirty_seen=self.dirty_seen or bool(doc.IsModified)
            okay=True;relative=True
            for row in rows:
                observed=self.actual_reference(doc,row);row['after_reopen']=observed
                match=bool(observed.get('present') and scenarios.key_path(observed.get('absolute'))==scenarios.key_path(row['target']))
                row['path_matches_after_reopen']=match
                okay=okay and match;relative=relative and observed.get('path_type')=='Relative'
                self.evidence.write('reference_after_normal_reopen',expected=row['target'],observed=observed,match=match)
            return scenarios.assess(True,okay,relative,self.dirty_seen,[])
        finally:self.close_document(doc)


def inventory_comparison(row,actual):
    """Compare cloud identities as identities, not opaque session/path strings."""
    source=row.get('native_source') or row.get('source') or ''
    result=dict(element_id=row.get('element_id'),kind=row.get('kind'),
                inventory_source=source,saved=actual,path_match=None)
    if actual.get('present') is None:
        result['conclusion']='UNVERIFIED_ELEMENT_READ'
    elif not actual.get('present'):
        result['conclusion']='ABSENT'
    else:
        identity=cache_sources.reference_identity(row)
        if identity:
            candidates=[cache_sources.reference_identity({'resource_information':r.get('information',{})})
                        for r in actual.get('resources',[])]
            candidates=[i for i in candidates if i]
            result.update(expected_cloud_identity=identity,saved_cloud_identities=candidates)
            result['conclusion']=('UNVERIFIED_RESOURCE_IDENTITY' if not candidates else
                'MATCH_CLOUD_IDENTITY' if any(cache_sources.same_identity(identity,i) for i in candidates)
                else 'MISMATCH_CLOUD_IDENTITY')
        elif source and actual.get('absolute') and '://' not in source:
            match=scenarios.key_path(source)==scenarios.key_path(actual['absolute'])
            result.update(path_match=match,conclusion='MATCH' if match else 'MISMATCH')
        else:result['conclusion']='UNVERIFIED_RESOURCE_IDENTITY'
    return result


def run_trial(backend,stage,target,rows,trial,options):
    """A single arm: each call uses its own initial saved bytes and copies."""
    doc=None;result=dict(trial=trial,status='STARTED',rows=[])
    if trial=='baseline_with_evidence':
        baseline=dict(options,repath=True,simple_repath=False,worker_mode=True,
                      independent_host=True,allow_partial_repath=True,
                      cleanup=False,upgrade=False,verify_in_process=False)
        raw=backend.finish_independent(stage,target,rows,baseline)
        result.update(status='BASELINE_COMPLETED_TEST_ONLY',processing_result=raw,rows=rows)
        return result
    if trial=='proven_repath':
        supported=[r for r in rows if r.get('target') and not r.get('skip_repath') and
                   ((r.get('kind')=='RevitLink') or
                    (r.get('kind')=='CADLink' and os.path.splitext(r.get('target',''))[1].lower()=='.dwg') or
                    r.get('special')=='image')]
        if not supported:
            result['status']='NOT_APPLICABLE_NO_SUPPORTED_REFERENCES'
            return result
        initial_dirty=False;final_dirty=False;reopen_dirty=False;failures=[]
        try:
            doc=backend.open_copy(stage,close_worksets=False)
            backend.observed_save_as(doc,target,backend._package_is_transmitted(stage),strict=False)
            initial_dirty=bool(getattr(doc,'IsModified',False))
            backend.dirty_seen=backend.dirty_seen or initial_dirty

            revit_rows=[r for r in supported if r.get('kind')=='RevitLink']
            if revit_rows:
                revit_issues,_=backend._repath_external_revit_links_relative(doc,revit_rows)
                failures.extend(f.text(x.get('message','')) for x in revit_issues)

            cad_rows=[r for r in supported if r.get('kind')=='CADLink']
            seen_targets={}
            for row in cad_rows:
                key=scenarios.key_path(row['target'])
                if key in seen_targets:
                    folder=os.path.join(os.path.dirname(target),'Links','CAD','Type-'+f.text(row['element_id']))
                    f.ensure_directory(folder)
                    destination=os.path.join(folder,ntpath.basename(row['target']))
                    backend.guard(destination)
                    f.copy_file(row['target'],destination)
                    row['target']=destination
                    key=scenarios.key_path(destination)
                seen_targets[key]=f.text(row['element_id'])
                try:
                    backend.cad_repair(doc,row,relative=True)
                except Exception as exc:
                    row['repath']='FAILED';row['error']=f.text(exc);failures.append(f.text(exc))
                    backend.evidence.write('cad_error',element_id=row['element_id'],message=f.text(exc))

            image_rows=[r for r in supported if r.get('special')=='image']
            if image_rows:
                image_issues,_=backend.repath_images(doc,image_rows)
                failures.extend(f.text(x.get('message','')) for x in image_issues)

            backend.phase('save_repaired_candidate',target=target)
            backend.evidence.snapshot(doc,'before repaired Save')
            doc.Save()
            final_dirty=bool(getattr(doc,'IsModified',False))
            backend.dirty_seen=backend.dirty_seen or final_dirty
            backend.evidence.snapshot(doc,'after repaired Save')
            backend.close_document(doc);doc=None

            backend.verify_independent_package(target,[],{'repath':False})
            if backend._package_is_transmitted(target):
                raise RuntimeError('Final test candidate remains transmitted.')
            doc=backend.open_copy(target,detach=False,close_worksets=True)
            if bool(getattr(doc,'IsDetached',False)):
                raise RuntimeError('Final test candidate reopens detached.')
            reopen_dirty=bool(getattr(doc,'IsModified',False))
            backend.dirty_seen=backend.dirty_seen or reopen_dirty

            checked=[];all_match=True
            for row in supported:
                observed=backend.actual_reference(doc,row)
                match=bool(observed.get('present') and
                           scenarios.key_path(observed.get('absolute'))==scenarios.key_path(row['target']))
                row['after_reopen']=observed;row['path_matches_after_reopen']=match
                all_match=all_match and match
                checked.append(dict(element_id=row.get('element_id'),kind=row.get('kind'),
                                    expected=row.get('target'),observed=observed,match=match))
                backend.evidence.write('reference_after_normal_reopen',
                    element_id=row.get('element_id'),expected=row.get('target'),observed=observed,match=match)

            status='REPATH_VERIFIED_TEST_ONLY' if all_match and not failures else 'REPATH_PARTIAL_TEST_ONLY'
            result.update(status=status,normal_open_verified=True,initial_save_dirty=initial_dirty,
                          final_save_dirty=final_dirty,reopen_dirty=reopen_dirty,
                          dirty_callback_evidence=bool(initial_dirty or final_dirty or reopen_dirty),
                          errors=failures,rows=supported,checked=checked)
            return result
        finally:
            if doc is not None and doc in backend.owned_documents:
                backend.close_document(doc)
    cad=[r for r in rows if r.get('kind')=='CADLink' and r.get('target') and r['target'].lower().endswith('.dwg')]
    if trial in ('cad_string','cad_local_relative','shared_target','per_type_target'):
        selected=scenarios.select_rows(cad,options.get('selected_ids',[]))
        if not selected:result['status']='NOT_APPLICABLE_NO_CAD_SELECTION';return result
    else:selected=[]
    if trial in ('shared_target','per_type_target'):
        if len(selected)<2:raise ValueError('Duplicate test requires at least two CAD types.')
        sources=set(scenarios.key_path(r.get('native_source') or r.get('source')) for r in selected)
        if len(sources)!=1 or len(set(f.digest(r['target']) for r in selected))!=1:
            raise ValueError('Duplicate arms require the same original source and identical collected bytes.')
        if trial=='shared_target':
            for row in selected:row['target']=selected[0]['target']
    metadata=cad if trial=='with_cad_metadata' else None
    try:
        doc=(backend.open_copy(stage,close_worksets=True) if trial=='saved_inventory' else
             backend.normalize(stage,target,metadata,strict=(trial=='strict_guard')))
        if trial in ('strict_guard','close_reopen_evidence','save_event_trace','without_metadata','with_cad_metadata'):
            result['metadata_activated_ids']=[f.text(r.get('element_id')) for r in cad if r.get('repath')=='STAGING_ABSOLUTE']
            result.update(status='NORMAL_REOPEN_REVIEW_DIRTY_STATE' if backend.dirty_seen else 'NORMAL_REOPEN_TEST_ONLY',
                          normal_open_verified=True,dirty_seen=backend.dirty_seen,
                          rows=[dict(element_id=r['element_id'],expected=r['target'],observed=backend.actual_reference(doc,r)) for r in cad])
        elif trial=='saved_inventory':
            comparisons=[inventory_comparison(row,backend.actual_reference(doc,row)) for row in rows]
            saved_ids=set(eid(e.Id) for e in backend.elements(doc,'CADLinkType'))
            known_ids=set(f.text(r.get('element_id')) for r in rows if r.get('kind')=='CADLink')
            result.update(status='INVENTORY_COMPARISON_COMPLETE',rows=comparisons,
                          saved_only_cad_ids=sorted(saved_ids-known_ids),
                          action='Saved input inspected before SaveAs. No repair or source substitution.')
        else:
            # Worksets were closed to avoid accidental cloud fallback during
            # initial SaveAs. Open CAD-containing worksets in the task model
            # before invoking API repairs; this is explicit and logged.
            if doc.IsWorkshared:
                from System.Collections.Generic import List
                ids={}
                for row in selected:
                    element=doc.GetElement(backend.ident(row['element_id']))
                    if element is not None:
                        ids[int(element.WorksetId.IntegerValue)]=element.WorksetId
                backend.close_document(doc);doc=None
                opts=backend.DB.OpenOptions();config=mp=None
                try:
                    config=backend.DB.WorksetConfiguration(backend.DB.WorksetConfigurationOption.CloseAllWorksets)
                    config.Open(List[backend.DB.WorksetId](list(ids.values())))
                    opts.SetOpenWorksetsConfiguration(config)
                    backend.phase('reopen_selected_CAD_worksets',worksets=sorted(ids))
                    mp=backend.mp(target)
                    doc=backend.app.OpenDocumentFile(mp,opts)
                    backend.owned_documents.append(doc)
                    backend.evidence.track(doc)
                    backend.guard(doc.PathName)
                    if doc.IsDetached:raise RuntimeError('CAD repair requires a normal saved candidate.')
                finally:dispose(mp);dispose(config);dispose(opts)
            failures=[]
            for row in selected:
                if trial=='per_type_target':
                    folder=os.path.join(os.path.dirname(target),'Links','CAD','Type-'+str(int(row['element_id'])))
                    f.ensure_directory(folder)
                    destination=os.path.join(folder,ntpath.basename(row['target']))
                    backend.guard(destination)
                    f.copy_file(row['target'],destination)
                    row['target']=destination
                try:backend.cad_repair(doc,row,relative=trial=='cad_local_relative')
                except Exception as exc:
                    row['error']=f.text(exc);failures.append(f.text(exc))
                    backend.evidence.write('cad_error',element_id=row['element_id'],message=f.text(exc))
            status=backend.save_repair_and_verify(doc,target,selected);doc=None
            result.update(status='CAD_ERRORS_RECORDED' if failures else status,errors=failures,rows=selected,
                          dirty_seen=backend.dirty_seen)
    finally:
        if doc is not None and doc in backend.owned_documents:backend.close_document(doc)
    return result
