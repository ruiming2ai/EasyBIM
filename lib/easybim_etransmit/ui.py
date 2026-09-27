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
from . import cleanup, engine, batch, layout
from .session import Registry, SessionBackend


PHASE_COLORS=dict(collecting='#2F80ED',repathing='#2F80ED',verifying='#8E44AD',
                  ready='#27AE60',review='#F2C94C',incomplete='#EB5757',cancelled='#828282')

def progress_phase(label):
    value=f.text(label or '')
    upper=value.upper()
    if upper.startswith('READY — REVIEW ISSUES') or upper.startswith('READY - REVIEW ISSUES'):
        return 'READY — REVIEW ISSUES',PHASE_COLORS['review'],value
    if upper.startswith('READY'):
        return 'READY',PHASE_COLORS['ready'],value
    if upper.startswith('INCOMPLETE') or upper.startswith('FAILED'):
        return 'INCOMPLETE',PHASE_COLORS['incomplete'],value
    if upper.startswith('CANCELLED') or upper.startswith('CANCELED'):
        return 'CANCELLED',PHASE_COLORS['cancelled'],value
    if 'FINALIZING ACC LINKS' in upper:
        return 'FINALIZING ACC LINKS',PHASE_COLORS['verifying'],value
    if 'VERIFYING FINAL PACKAGE' in upper:
        return 'VERIFYING FINAL PACKAGE',PHASE_COLORS['verifying'],value
    if 'REPATH' in upper or 'CLEANUP' in upper:
        return 'REPATHING',PHASE_COLORS['repathing'],value
    return 'COLLECTING',PHASE_COLORS['collecting'],value


class TransferProgressBar(forms.ProgressBar):
    """Keep one pyRevit prompt bar, positioned below Revit's title area."""
    def update_window(self):
        if getattr(self, '_etransmit_positioned', False):
            return
        forms.ProgressBar.update_window(self)
        self.Top=getattr(self,'Top',0)+float(getattr(self,'user_height',32) or 32)
        self._etransmit_positioned = True

    def set_phase(self,label):
        phase,color,detail=progress_phase(label)
        if phase in ('READY','READY — REVIEW ISSUES','INCOMPLETE','CANCELLED'):
            self.title=phase
        else:
            self.title=phase+' | '+detail
        try:
            from System.Windows.Media import BrushConverter
            self.pbar.Foreground=BrushConverter().ConvertFromString(color)
        except Exception:
            pass
        return phase


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


class Dialog(forms.WPFWindow):
    def __init__(self, uiapp, xaml):
        forms.WPFWindow.__init__(self,xaml)
        self.snapshots_authorized=False
        self.uiapp=uiapp; self.result=None; self.models=[]; self.extras=[]; self.mappings=[]; self.view_types=[]
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
            self.mappings=[Choice(a+'  ->  '+b,source=b,key=a) for a,b in saved.get('mappings',[])]
            self.DeepScan.IsChecked=saved.get('deep',True)
            self.Repath.IsChecked=saved.get('repath',True); self.Reports.IsChecked=saved.get('reports',True)
            self.LoadUnloadedFiles.IsChecked=saved.get('load_unloaded_files',True)
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
        self.Mappings.ItemsSource=None; self.Mappings.ItemsSource=self.mappings

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

    def replace_source(self,sender,args):
        row=self.Models.SelectedItem
        if row is None: return forms.alert('Select a model row first.')
        path=forms.pick_file(file_ext='rvt',title='Select the intended saved copy; do not substitute WIP for Shared/Consumed')
        if path:
            row.Source=path;row.Name=os.path.basename(path);row.Mode='SAVED_FILE';row.Document=None
            row.CloudSelection=None;row.Checked=True; self.refresh()

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

    def add_mapping(self,sender,args):
        prefix=forms.ask_for_string(prompt='Exact configured source prefix. Include the correct Shared/Consumed hierarchy. '
                                   'No filename searching is performed.',title='Source prefix')
        if not prefix: return
        folder=forms.pick_folder(title='Choose the SAME source hierarchy in Desktop Connector or a local download')
        if folder:
            self.mappings.append(Choice(prefix+'  ->  '+folder,key=prefix,source=folder)); self.refresh()

    def remove_mapping(self,sender,args):
        row=self.Mappings.SelectedItem
        if row in self.mappings: self.mappings.remove(row); self.refresh()

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
        opts=f.defaults(); opts['include']=dict((x.Key,bool(x.Checked)) for x in self.categories)
        opts['file_structure']=layout.mode(self.FileStructure.SelectedItem.Key if self.FileStructure.SelectedItem else None)
        opts.update(deep=bool(self.DeepScan.IsChecked),repath=bool(self.Repath.IsChecked),
                    load_unloaded_files=bool(self.LoadUnloadedFiles.IsChecked),
                    cleanup=bool(self.Cleanup.IsChecked),upgrade=bool(self.Upgrade.IsChecked or self.Cleanup.IsChecked),
                    discard_worksets=bool(self.DiscardWorksets.IsChecked),purge=bool(self.Purge.IsChecked),
                    views=self.ViewMode.SelectedItem.Key,view_types=self.view_types,per_model=True,
                    reports=bool(self.Reports.IsChecked),zip=bool(self.Zip.IsChecked),zip_per_model=bool(self.ZipPerModel.IsChecked),mappings=[(x.Key,x.Source) for x in self.mappings])
        opts['skip_cloud_links']=bool(self.SkipCloudLinks.IsChecked)
        opts['saved_state_only']=True
        if opts['zip_per_model'] or any(x.Mode=='LIVE_DOCUMENT' for x in models):opts['per_model']=True
        unresolved=[]
        for row in models:
            if row.Mode=='LIVE_DOCUMENT':continue
            try: path=f.resolve_source(row.Source,mappings=opts['mappings'])
            except ValueError: path=None
            if not path: unresolved.append(row.Name+' : '+row.Source)
        if unresolved:
            return forms.alert('These saved-file selections do not resolve to an exact source. Open-model selections do not require a saved path.\n\n'+'\n'.join(unresolved))
        root=f.new_run_root(output,datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
        planned=[];planned_names={}
        for index,row in enumerate(models):
            source=('open://selection-'+str(index)+'/'+row.Name if row.Mode=='LIVE_DOCUMENT' else row.Source)
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
        # The visible UI disclosure states saved/cache state only. No source save consent or SaveAs path is used.
        if opts['upgrade']: notes.append('Package copies will be saved in Revit '+f.text(self.uiapp.Application.VersionNumber)+'. They cannot be opened in an older Revit.')
        if opts['cleanup']: notes.append('Cleanup may delete views/definitions or discard worksets IN COPIES ONLY. Retain your original models.')
        if notes and not forms.alert('\n\n'.join(notes)+'\n\nContinue?',yes=True,no=True,title='Process package copies / Transmit'): return
        if self.SaveSettings.IsChecked:
            saved=dict((k,opts[k]) for k in ('deep','repath','load_unloaded_files','file_structure','per_model','reports','zip','zip_per_model','mappings'))
            saved['output']=output
            folder=os.path.dirname(self.settings)
            try:
                if not os.path.isdir(folder): os.makedirs(folder)
                with io.open(self.settings,'w',encoding='utf-8') as out: out.write(f.text(json.dumps(saved,indent=2)))
            except IOError as exc: forms.alert('Settings could not be saved: '+f.text(exc))
        self.result=(models,root,opts,list(self.extras)); self.Close()


def run(uiapp,xaml):
    dialog=Dialog(uiapp,xaml);registry=None;results=[];was_cancelled=False
    try:
        dialog.ShowDialog()
        if not dialog.result:return
        choices,root,opts,extras=dialog.result
        with TransferProgressBar(title='e-transmit',cancellable=True,indeterminate=True) as pb:
            def cancel():return pb.cancelled
            def pulse(label,current,total):
                pb.set_phase(label);pb.update_progress(current,max(1,total))
            registry=Registry(DB,uiapp.Application,root+'_ReadOnlySnapshots',cancel,
                              opts['include'].get('spreadsheets',True),saved_state_only=True)
            sources=[];model_names={};live_keys=[]
            for row in choices:
                if cancel():break
                if row.Mode=='LIVE_DOCUMENT':
                    key=registry.add_live(row.Document)
                    sources.append(key);live_keys.append(key);model_names[key]=registry.get(key)['name']
                else:
                    sources.append(row.Source);model_names[row.Source]=getattr(row,'Name',row.Source)
            if sources:
                results=batch.run_batch(sources,root,
                    lambda package,source:SessionBackend(DB,uiapp.Application,package,registry,cancel),
                    opts,extras,cancel,pulse,model_names=model_names)
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
        if not results:return forms.alert('Transmission cancelled before any models were processed.')
        message=engine.completion_message(results,len(choices),cancelled=was_cancelled)
        forms.alert(message+'\n\nOutput: '+root+'\n\nKeep the full model-named job folders, including their Links folders together. Read each START_HERE.txt and batch.json before delivery.',title='EasyBIM e-transmit')
        os.startfile(root)
    finally:
        if registry is not None:
            try:registry.close()
            except Exception:script.get_logger().warning('Temporary read-only source snapshots could not all be removed; the open working models were not saved or closed.')
