# -*- coding: utf-8 -*-
"""Modal eTransmit UI; API operations remain on Revit's command thread."""
from __future__ import unicode_literals
import datetime
import io
import json
import os
import re
import traceback
from pyrevit import forms, script, DB
from . import VERSION, files as f
from . import cleanup, engine, batch, layout, preflight, performance
from .session import Registry, SessionBackend


from easybim import etransmit_progress_panel as progress_ui
from . import trace


def progress_phase(label):
    """Backward-compatible facade used by existing tests/callers."""
    return progress_ui.progress_phase(label)


DockableTransferProgress = progress_ui.ProgressController


class Choice(object):
    def __init__(self, name, key='', checked=True, source='', modified=False, document=None, mode='SAVED_FILE', cloud=None):
        self.Name, self.Key, self.Checked, self.Source, self.Modified = name,key,checked,source,modified
        self.Document=document;self.Mode=mode;self.CloudSelection=cloud


def validate_source_modes(models):
    """Reject legacy published-ACC rows; live ACC sources must be opened natively in Revit."""
    allowed=set(('LIVE_DOCUMENT','SAVED_FILE'))
    invalid=[getattr(row,'Mode','') for row in models if getattr(row,'Mode','') not in allowed]
    if invalid:
        raise ValueError('Unsupported source mode: '+', '.join(sorted(set(invalid)))+
                         '. Open live ACC models from Revit Home > Autodesk Docs before e-transmit.')
    return True


class UnloadedLinksDialog(forms.WPFWindow):
    def __init__(self, choices):
        forms.WPFWindow.__init__(self,os.path.join(os.path.dirname(__file__),'unloaded_links.xaml'))
        self.choices=choices;self.result=None
        self.Links.ItemsSource=choices
    def _set_all(self,value):
        try:self.Links.CommitEdit()
        except Exception:pass
        for row in self.choices:row.Checked=bool(value)
        try:self.Links.Items.Refresh()
        except Exception:
            self.Links.ItemsSource=None;self.Links.ItemsSource=self.choices
    def select_all(self,sender,args):
        self._set_all(True)
    def select_none(self,sender,args):
        self._set_all(False)
    def reload_selected(self,sender,args):
        self.Links.CommitEdit()
        self.result=[row for row in self.choices if row.Checked];self.Close()
    def continue_without_reload(self,sender,args):
        self.result=[];self.Close()
    def cancel_reload(self,sender,args):
        self.result=None;self.Close()


def source_save_choice(choices):
    # USER_BROWSE explicitly chose existing saved bytes. Keep the open detached
    # document only for link discovery/reload; do not offer to save its edits.
    modified=[r for r in choices if getattr(r,'Document',None) is not None
              and getattr(r,'DetachedRecovery','')!='USER_BROWSE'
              and bool(getattr(r.Document,'IsModified',False))]
    if not modified:return 'continue'
    answer=forms.alert('These selected models have unsaved changes:\n'+
        '\n'.join(f.text(r.Document.Title) for r in modified)+
        '\n\nContinue exports the last saved file/cache, not unsaved edits. Save means normal in-place Save only; no SaveAs, Sync or Publish.',
        title='e-transmit: unsaved changes',
        options=['Continue without saving','Save and continue','Cancel'])
    return {'Continue without saving':'continue','Save and continue':'save'}.get(answer,'cancel')


def _browse_detached_source():
    path = forms.pick_file(
        file_ext='rvt',
        multi_file=False,
        title='Load the existing model from file location'
    )
    if not path:
        return dict(action='CANCEL')
    return dict(action='BROWSE', path=f.text(path))


def detached_source_recovery(row, output, feedback=''):
    """User-directed recovery for a pathless detached LIVE_DOCUMENT."""
    if feedback:
        answer = forms.alert(
            'eTransmit cannot locate the detached model. Please load from selected file locations.',
            title='eTransmit — detached model not located',
            options=['Load another model from file location…', 'Cancel']
        )
        if answer != 'Load another model from file location…':
            return dict(action='CANCEL')
        return _browse_detached_source()

    message = (
        "eTransmit couldn't locate the detached model.\n\n"
        "So please make the following decisions to transmit:\n\n"
        "Model: " + f.text(getattr(row, 'Name', '') or '')
    )
    answer = forms.alert(
        message,
        title='eTransmit — detached model not located',
        options=[
            'Option 1 — Save current detached model and transmit',
            'Option 2 — Load model from file location…',
            'Cancel'
        ]
    )

    if answer == 'Option 2 — Load model from file location…':
        return _browse_detached_source()
    if answer != 'Option 1 — Save current detached model and transmit':
        return dict(action='CANCEL')

    change_choice = forms.alert(
        'The new RVT will be saved automatically in the eTransmit destination:\n'
        + f.text(output) +
        '\n\nThis Save As changes the open Revit document to that new file. '
        'Choose whether the current in-memory changes should be included.',
        title='eTransmit — save detached model',
        options=[
            'Save current changes and transmit',
            'Do not include current changes',
            'Cancel'
        ]
    )
    if change_choice == 'Save current changes and transmit':
        return dict(action='SAVE_CURRENT')
    if change_choice == 'Do not include current changes':
        # Revit SaveAs always writes the current in-memory state. To exclude
        # current changes, the user must identify the existing saved RVT.
        return _browse_detached_source()
    return dict(action='CANCEL')


class Dialog(forms.WPFWindow):
    def __init__(self, uiapp, xaml):
        forms.WPFWindow.__init__(self,xaml)
        self.snapshots_authorized=False
        self.uiapp=uiapp; self.result=None; self.models=[]; self.extras=[]; self.view_types=[]
        self.categories=[Choice(label,key) for key,label in f.CATEGORIES]
        self.Categories.ItemsSource=self.categories
        self.structures=[Choice(label,key) for key,label in layout.MODES]
        self.FileStructure.ItemsSource=self.structures; self.FileStructure.SelectedIndex=0
        self.modes=[Choice(label,key) for key,label in cleanup.MODES]
        self.ViewMode.ItemsSource=self.modes; self.ViewMode.SelectedIndex=0
        self.VersionLabel.Text='Version '+VERSION+' | Revit '+f.text(uiapp.Application.VersionNumber)
        self.settings=os.path.join(os.environ.get('APPDATA',os.path.expanduser('~')),'EasyBIM','e-transmit','settings.json')
        try:
            with io.open(self.settings,encoding='utf-8') as inp: saved=json.load(inp)
            self.Output.Text=saved.get('output','')
            self.Repath.IsChecked=saved.get('repath',True); self.Reports.IsChecked=saved.get('reports',True)
            self.FileStructure.SelectedIndex=[x.Key for x in self.structures].index(layout.mode(saved.get('file_structure')))
            self.Zip.IsChecked=saved.get('zip',False)
            self.ZipPerModel.IsChecked=saved.get('zip_per_model',False)
        except (IOError,ValueError): pass
        active=uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
        for doc in uiapp.Application.Documents:
            if doc.IsLinked or doc.IsFamilyDocument: continue
            source=f.text(doc.PathName or '')
            self.models.append(Choice(f.text(doc.Title),checked=doc==active,source=f.text(source),modified=bool(doc.IsModified),document=doc,mode='LIVE_DOCUMENT'))
        self.models.sort(key=lambda x:(not x.Checked,x.Name.lower()))
        if self.models and not any(x.Checked for x in self.models): self.models[0].Checked=True
        self.Purge.IsEnabled=hasattr(DB.Document,'GetUnusedElements')
        self.refresh()

    def refresh(self):
        self.Models.ItemsSource=None; self.Models.ItemsSource=self.models
        self.Extras.ItemsSource=None; self.Extras.ItemsSource=self.extras

    def append_models(self, paths):
        seen=set(f.canonical(m.Source) for m in self.models if m.Mode=='SAVED_FILE')
        for path in paths:
            if f.canonical(path) not in seen:
                self.models.append(Choice(os.path.basename(path),source=path)); seen.add(f.canonical(path))
        self.refresh()

    def browse_models(self,sender,args):
        paths=forms.pick_file(file_ext='rvt',multi_file=True,title='Select saved Revit source models')
        if paths: self.append_models(paths if not isinstance(paths,f.string_types) else [paths])

    def browse_model_folder(self,sender,args):
        folder=forms.pick_folder(title='Select folder of saved RVT models (includes subfolders)')
        if folder:
            paths=[p for p in engine.folder_files(folder) if p.lower().endswith('.rvt') and
                   not re.search(r'\.\d{4}\.rvt$',p,re.I)]
            self.append_models(paths)

    def remove_model(self,sender,args):
        row=self.Models.SelectedItem
        if row in self.models: self.models.remove(row); self.refresh()

    def browse_output(self,sender,args):
        folder=forms.pick_folder(title='Choose destination (a new transmittal folder will be created)')
        if folder: self.Output.Text=folder

    def add_files(self,sender,args):
        paths=forms.pick_file(multi_file=True,title='Add original dependency files')
        if paths:
            for path in paths if not isinstance(paths,f.string_types) else [paths]:
                if path not in self.extras: self.extras.append(path)
            self.refresh()

    def add_folder(self,sender,args):
        folder=forms.pick_folder(title='Add a dependency folder and all its contents')
        if folder and folder not in self.extras: self.extras.append(folder); self.refresh()

    def remove_extra(self,sender,args):
        row=self.Extras.SelectedItem
        if row in self.extras: self.extras.remove(row); self.refresh()

    def cleanup_toggle(self,sender,args): self.CleanupOptions.IsEnabled=bool(self.Cleanup.IsChecked)

    def select_view_types(self,sender,args):
        selected=forms.SelectFromList.show(cleanup.VIEW_TYPES,title='View types to retain',multiselect=True)
        if selected is not None:
            self.view_types=list(selected); self.ViewTypesLabel.Text=', '.join(self.view_types) or 'None'

    def cancel_click(self,sender,args): self.Close()

    def transmit_click(self,sender,args):
        self.snapshots_authorized=False;self.result=None
        self.Models.CommitEdit()
        models=[x for x in self.models if x.Checked]
        if not models: return forms.alert('Select at least one source model.')
        try: validate_source_modes(models)
        except ValueError as exc: return forms.alert(f.text(exc),title='e-transmit source')
        output=f.text(self.Output.Text).strip()
        if not os.path.isdir(output): return forms.alert('Select an existing output directory.')
        uiapp=getattr(self,'uiapp',None)
        application=getattr(uiapp,'Application',None)
        trace.write(uiapp,'ET_STEP_02_HOST_PREFLIGHT_START')
        try:
            needs_detached_recovery = any(
                getattr(row, 'Mode', '') == 'LIVE_DOCUMENT'
                and getattr(row, 'Document', None) is not None
                and bool(getattr(row.Document, 'IsDetached', False))
                and not bool(getattr(row.Document, 'IsModelInCloud', False))
                for row in models
            )
            if needs_detached_recovery:
                preflight.primary_host_sources(
                    models, application, DB=DB, output=output,
                    detached_recovery=lambda row, feedback='': detached_source_recovery(
                        row, output, feedback)
                )
            else:
                # Preserve the established two-argument path for ordinary
                # local/cloud sources and existing integration shims.
                preflight.primary_host_sources(models, application)
        except f.Cancelled:
            trace.write(uiapp,'ET_STEP_02_HOST_PREFLIGHT_CANCELLED')
            return
        except preflight.PreflightError as exc:
            trace.write(uiapp,'ET_STEP_02_HOST_PREFLIGHT_BLOCKED',detail=f.text(exc))
            return forms.alert(f.text(exc),title='e-transmit host source unavailable')
        trace.write(uiapp,'ET_STEP_03_HOST_PREFLIGHT_READY')
        opts=f.defaults(); opts['include']=dict((x.Key,bool(x.Checked)) for x in self.categories)
        opts['file_structure']=layout.mode(self.FileStructure.SelectedItem.Key if self.FileStructure.SelectedItem else None)
        opts.update(repath=bool(self.Repath.IsChecked),
                    cleanup=bool(self.Cleanup.IsChecked),upgrade=bool(self.Upgrade.IsChecked or self.Cleanup.IsChecked),
                    discard_worksets=bool(self.DiscardWorksets.IsChecked),purge=bool(self.Purge.IsChecked),
                    views=self.ViewMode.SelectedItem.Key,view_types=self.view_types,per_model=True,
                    reports=bool(self.Reports.IsChecked),zip=bool(self.Zip.IsChecked),zip_per_model=bool(self.ZipPerModel.IsChecked))
        opts['mappings']=[]
        opts['saved_state_only']=True
        if opts['zip_per_model'] or any(x.Mode=='LIVE_DOCUMENT' for x in models):opts['per_model']=True
        root=f.new_run_root(output,datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
        planned=[];planned_names={}
        for index,row in enumerate(models):
            source=('open://selection-'+str(index)+'/'+row.Name if row.Mode=='LIVE_DOCUMENT' else getattr(row,'ResolvedSource',None) or row.Source)
            planned.append(source);planned_names[source]=row.Name
        long_paths=engine.preflight_paths(planned,root,opts,self.extras,planned_names)
        if long_paths:
            message=long_paths[0]['message']+'\n\nNo files were copied and no transmittal was created. '
            message+='Choose a shorter output folder now?'
            if forms.alert(message,yes=True,no=True,title='e-transmit: output path too long'):
                self.browse_output(sender,args)
            return
        if opts['cleanup']:
            try: cleanup.view_deletions([],opts['views'],opts['view_types'])
            except ValueError as exc: return forms.alert(f.text(exc))
        notes=[]
        # Source-save choice is made once, before collecting the selected documents.
        if opts['upgrade']: notes.append('Package copies will be saved in Revit '+f.text(self.uiapp.Application.VersionNumber)+'. They cannot be opened in an older Revit.')
        if opts['cleanup']: notes.append('Cleanup may delete views/definitions or discard worksets IN COPIES ONLY. Retain your original models.')
        if notes and not forms.alert('\n\n'.join(notes)+'\n\nContinue?',yes=True,no=True,title='Process package copies / Transmit'): return
        if self.SaveSettings.IsChecked:
            saved=dict((k,opts[k]) for k in ('repath','file_structure','per_model','reports','zip','zip_per_model'))
            saved['output']=output
            folder=os.path.dirname(self.settings)
            try:
                if not os.path.isdir(folder): os.makedirs(folder)
                with io.open(self.settings,'w',encoding='utf-8') as out: out.write(f.text(json.dumps(saved,indent=2)))
            except IOError as exc: forms.alert('Settings could not be saved: '+f.text(exc))
        self.result=(models,root,opts,list(self.extras)); self.Close()


def run(uiapp,xaml):
    trace.write(uiapp,'ET_STEP_01_COMMAND_START')
    dialog=Dialog(uiapp,xaml);registry=None;results=[];was_cancelled=False;root=None
    try:
        dialog.ShowDialog()
        if not dialog.result:return
        choices,root,opts,extras=dialog.result
        trace.write(uiapp,'ET_STEP_04_DIALOG_ACCEPTED',root)

        # Batch preflight: collect every user decision before any model package
        # starts. Detached-source recovery already completed for all selected
        # rows in Dialog.transmit_click(). Now resolve save and reload choices
        # across the complete batch.
        trace.write(uiapp,'ET_STEP_05_SAVE_PROMPT_START',root)
        save_decision=source_save_choice(choices)
        trace.write(uiapp,'ET_STEP_06_SAVE_PROMPT_DONE',root,save_decision)
        with performance.Collector() as save_timing:
            save_events=preflight.save_selected(choices,save_decision)
        trace.write(uiapp,'ET_STEP_07_SAVE_PREFLIGHT_DONE',root)

        cancel_provider=[lambda:False]
        def cancel():return bool(cancel_provider[0]())

        trace.write(uiapp,'ET_STEP_10_REGISTRY_START',root)
        registry=Registry(DB,uiapp.Application,root+'_ReadOnlySnapshots',cancel,
                          opts['include'].get('spreadsheets',True),saved_state_only=True)
        trace.write(uiapp,'ET_STEP_11_REGISTRY_READY',root)
        sources=[];model_names={};live_keys=[]
        for row in choices:
            if row.Mode=='LIVE_DOCUMENT':
                trace.write(uiapp,'ET_STEP_12_LIVE_DOCUMENT_REGISTER_START',root,row.Name)
                key=registry.add_live(row.Document,configured_source=getattr(row,'ResolvedSource','') or None)
                entry=registry.get(key)
                if getattr(row,'DetachedRecovery',''):
                    entry['detached_recovery']=row.DetachedRecovery
                sources.append(key);live_keys.append(key);model_names[key]=entry['name']
                trace.write(uiapp,'ET_STEP_13_LIVE_DOCUMENT_REGISTER_DONE',root,row.Name)
            else:
                source=getattr(row,'ResolvedSource',None) or row.Source
                sources.append(source);model_names[source]=getattr(row,'Name',source)

        reload_selection=[]
        if live_keys:
            registry.get(live_keys[0]).setdefault('preflight_timings',[]).append(save_timing.snapshot())
            for key in live_keys:
                entry=registry.get(key)
                entry.setdefault('preflight_events',[]).extend(event for event in save_events
                    if event.get('model')==f.text(entry['document'].Title) and event.get('source')==f.text(entry['document'].PathName or ''))
            trace.write(uiapp,'ET_STEP_14_UNLOADED_LINK_PREFLIGHT_START',root)
            unloaded=preflight.unloaded_links(registry,live_keys) if opts['include'].get('revit',True) else []
            if unloaded:
                chooser=UnloadedLinksDialog(unloaded);chooser.ShowDialog()
                if chooser.result is None:raise f.Cancelled()
                reload_selection=list(chooser.result)
            trace.write(uiapp,'ET_STEP_15_UNLOADED_LINK_PREFLIGHT_DONE',root)

        # All decisions are complete here. From this point forward the command
        # can process the complete batch without stopping for another source or
        # unloaded-link choice.
        trace.write(uiapp,'ET_STEP_08_PROGRESS_LOOKUP_START',root)
        with DockableTransferProgress() as pb:
            trace.write(uiapp,'ET_STEP_09_PROGRESS_VISIBLE' if pb.available else 'ET_STEP_09_PROGRESS_UNAVAILABLE',
                        root,getattr(pb,'mode','NONE'))
            cancel_provider[0]=lambda:pb.cancelled
            def pulse(label,current,total):
                pb.set_phase(label);pb.update_progress(current,max(1,total))

            if reload_selection:
                with performance.Collector() as reload_timing:
                    with preflight.TemporaryReloads(registry,reload_selection,pulse) as reloads:
                        reloads.acquire()
                if live_keys:
                    registry.get(live_keys[0]).setdefault('preflight_timings',[]).append(reload_timing.snapshot())

            if sources and not cancel():
                trace.write(uiapp,'ET_STEP_16_BATCH_START',root)
                results=batch.run_batch(sources,root,
                    lambda package,source:SessionBackend(DB,uiapp.Application,package,registry,cancel),
                    opts,extras,cancel,pulse,model_names=model_names)
                trace.write(uiapp,'ET_STEP_17_BATCH_DONE',root)
            was_cancelled=cancel()
            if was_cancelled:
                pb.set_phase('CANCELLED')
            elif any(r.get('status')=='FAILED' or any(i.get('severity')=='error' for i in r.get('issues',[])) for r in results):
                pb.set_phase('INCOMPLETE')
            elif any(r.get('status')=='NEEDS_REVIEW' or any(i.get('severity')!='info' for i in r.get('issues',[])) for r in results):
                pb.set_phase('READY — REVIEW ISSUES')
            else:
                pb.set_phase('READY')
            pb.update_progress(1,1)
        trace.write(uiapp,'ET_STEP_18_COMMAND_COMPLETE',root)
        if not results:return forms.alert('Transmission cancelled before any models were processed.')
        message=engine.completion_message(results,len(choices),cancelled=was_cancelled)
        forms.alert(message+'\n\nOutput: '+root+'\n\nKeep the full model-named job folders, including their Links folders together. Read each START_HERE.txt and batch.json before delivery.',title='EasyBIM e-transmit')
        os.startfile(root)
    except f.Cancelled:
        trace.write(uiapp,'ET_CANCELLED',root)
        forms.alert('Transmission cancelled. Any explicitly requested source saves already completed are retained.',title='EasyBIM e-transmit')
    except preflight.PreflightError as exc:
        trace.write(uiapp,'ET_PREFLIGHT_STOPPED',root,f.text(exc))
        forms.alert(f.text(exc),title='e-transmit preflight stopped')
    finally:
        if registry is not None:
            try:registry.close()
            except Exception:script.get_logger().warning('Temporary read-only source snapshots could not all be removed; no working model was closed or relocated.')
