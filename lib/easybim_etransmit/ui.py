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
from . import cleanup, engine, batch, aps, oauth, cloud_picker
from .session import Registry, SessionBackend


class TransferProgressBar(forms.ProgressBar):
    """Keep the initial geometry while document hooks change pyRevit context.

    ProgressBar redraws normally (including Cancel), but its update_window
    normally looks up HOST_APP.uiapp.MainWindowHandle on every redraw. A
    background document event can leave that mutable global without a UI app.
    Do not monkeypatch pyRevit or disable any document hooks to work around it.
    """
    def update_window(self):
        if getattr(self, '_etransmit_positioned', False):
            return
        forms.ProgressBar.update_window(self)
        self._etransmit_positioned = True


class Choice(object):
    def __init__(self, name, key='', checked=True, source='', modified=False, document=None, mode='SAVED_FILE', cloud=None):
        self.Name, self.Key, self.Checked, self.Source, self.Modified = name,key,checked,source,modified
        self.Document=document;self.Mode=mode;self.CloudSelection=cloud


class Dialog(forms.WPFWindow):
    def __init__(self, uiapp, xaml):
        forms.WPFWindow.__init__(self,xaml)
        self.tokens=None;self.client=None;self.client_id='';self.callback_uri='http://127.0.0.1:8767/callback'
        self.uiapp=uiapp; self.result=None; self.models=[]; self.extras=[]; self.mappings=[]; self.view_types=[]
        self.categories=[Choice(label,key) for key,label in f.CATEGORIES]
        self.Categories.ItemsSource=self.categories
        self.modes=[Choice(label,key) for key,label in cleanup.MODES]
        self.ViewMode.ItemsSource=self.modes; self.ViewMode.SelectedIndex=0
        self.VersionLabel.Text='Version '+VERSION+' | Revit '+f.text(uiapp.Application.VersionNumber)
        self.settings=os.path.join(os.environ.get('APPDATA',os.path.expanduser('~')),'EasyBIM','e-transmit','settings.json')
        try:
            with io.open(self.settings,encoding='utf-8') as inp: saved=json.load(inp)
            self.Output.Text=saved.get('output','')
            self.mappings=[Choice(a+'  ->  '+b,source=b,key=a) for a,b in saved.get('mappings',[])]
            self.DeepScan.IsChecked=saved.get('deep',True); self.Separate.IsChecked=saved.get('per_model',True)
            self.Repath.IsChecked=saved.get('repath',True); self.Reports.IsChecked=saved.get('reports',True)
            self.Zip.IsChecked=saved.get('zip',False)
            self.ZipPerModel.IsChecked=saved.get('zip_per_model',False)
            self.client_id=saved.get('aps_client_id','');self.callback_uri=saved.get('aps_callback_uri',self.callback_uri)
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

    def release_credentials(self):
        if self.tokens is not None:self.tokens.close()
        self.tokens=None;self.client=None

    def sign_in_cloud(self,sender=None,args=None):
        client_id=forms.ask_for_string(default=self.client_id,
            prompt='APS native-app CLIENT ID (not a secret). The app must be provisioned for this ACC account with Data Management and Forma/ACC APIs.',
            title='Optional Autodesk read-only sign-in')
        if not client_id:return False
        callback=forms.ask_for_string(default=self.callback_uri,
            prompt='The exact loopback callback registered for the native app.',title='APS callback')
        if not callback:return False
        try:
            self.release_credentials()
            with TransferProgressBar(title='Autodesk sign-in',cancellable=True,indeterminate=True) as pb:
                self.tokens=oauth.sign_in(client_id,callback,lambda:pb.cancelled,
                    lambda label,cur,total:pb.update_progress(cur,total))
            self.client_id=client_id;self.callback_uri=callback
            self.client=aps.Client(self.tokens)
            self.AuthStatus.Text='Signed in for this command only | read scope: data:read'
            return True
        except f.Cancelled:return False
        except Exception as exc:
            forms.alert(f.text(exc),title='Autodesk sign-in');return False

    def choose_cloud(self):
        if self.client is None and not self.sign_in_cloud():return None
        def choose(items,title):
            if not items:
                forms.alert('No accessible matching items were returned. Check project/download permissions.',title=title)
                return None
            return forms.SelectFromList.show(items,name_attr='Name',title=title,multiselect=False)
        try:return cloud_picker.choose(self.client,choose)
        except Exception as exc:
            forms.alert(f.text(exc),title='Published ACC version');return None

    def add_cloud_model(self,sender,args):
        selected=self.choose_cloud()
        if not selected:return
        graph=selected['graph']
        if not forms.alert('Transmit this PUBLISHED version, not unsaved/live model state?\n\n'+selected['display']+
                '\n\nAutodesk returned '+str(len(graph.entries)-1)+' linked file(s). Missing permissions can omit links; the model inventory will be checked separately.',yes=True,no=True):return
        self.models.append(Choice(graph.host['modelName'],source=selected['display'],mode='PUBLISHED_VERSION',cloud=selected))
        self.refresh()

    def associate_cloud_graph(self,sender,args):
        row=self.Models.SelectedItem
        if row is None or row.Mode!='LIVE_DOCUMENT':
            return forms.alert('Select an open-model row first. Published-mode models already include their authoritative download graph.')
        selected=self.choose_cloud()
        if selected:
            row.CloudSelection=selected
            self.AuthStatus.Text='Associated download graph with '+row.Name+'; loaded link revisions must match.'

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
        self.Models.CommitEdit()
        models=[x for x in self.models if x.Checked]
        if not models: return forms.alert('Select at least one source model.')
        output=f.text(self.Output.Text).strip()
        if not os.path.isdir(output): return forms.alert('Select an existing output directory.')
        opts=f.defaults(); opts['include']=dict((x.Key,bool(x.Checked)) for x in self.categories)
        opts.update(deep=bool(self.DeepScan.IsChecked),repath=bool(self.Repath.IsChecked),
                    cleanup=bool(self.Cleanup.IsChecked),upgrade=bool(self.Upgrade.IsChecked or self.Cleanup.IsChecked),
                    discard_worksets=bool(self.DiscardWorksets.IsChecked),purge=bool(self.Purge.IsChecked),
                    views=self.ViewMode.SelectedItem.Key,view_types=self.view_types,per_model=bool(self.Separate.IsChecked),
                    reports=bool(self.Reports.IsChecked),zip=bool(self.Zip.IsChecked),zip_per_model=bool(self.ZipPerModel.IsChecked),mappings=[(x.Key,x.Source) for x in self.mappings])
        if opts['zip_per_model']:opts['per_model']=True
        unresolved=[]
        for row in models:
            if row.Mode=='LIVE_DOCUMENT':continue
            if row.Mode=='PUBLISHED_VERSION':continue
            try: path=f.resolve_source(row.Source,mappings=opts['mappings'])
            except ValueError: path=None
            if not path: unresolved.append(row.Name+' : '+row.Source)
        if unresolved:
            return forms.alert('These saved-file selections do not resolve to an exact source. Open-model selections do not require a saved path.\n\n'+'\n'.join(unresolved))
        root=f.new_run_root(output,datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
        long_paths=engine.preflight_paths([x.Source for x in models if x.Mode=='SAVED_FILE'],root,opts,self.extras)
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
        if any(x.Mode=='LIVE_DOCUMENT' for x in models):notes.append('Open documents supply their current dependency inventory. If host serialization needs SaveAs, a separate confirmation will show the recovery location. No automatic Sync or Publish occurs.')
        if opts['upgrade']: notes.append('Package copies will be saved in Revit '+f.text(self.uiapp.Application.VersionNumber)+'. They cannot be opened in an older Revit.')
        if opts['cleanup']: notes.append('Cleanup may delete views/definitions or discard worksets IN COPIES ONLY. Retain your original models.')
        if notes and not forms.alert('\n\n'.join(notes)+'\n\nContinue?',yes=True,no=True): return
        if self.SaveSettings.IsChecked:
            saved=dict((k,opts[k]) for k in ('deep','repath','per_model','reports','zip','zip_per_model','mappings'))
            saved['output']=output
            saved['aps_client_id']=self.client_id;saved['aps_callback_uri']=self.callback_uri
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
                pb.title='e-transmit | '+f.text(label);pb.update_progress(current,max(1,total))
            registry=Registry(DB,uiapp.Application,root+'_WorkingSnapshots',cancel,
                              opts['include'].get('spreadsheets',True))
            def confirm(doc,path):
                return forms.alert('Include the CURRENT state of '+f.text(doc.Title)+'?\n\n'
                    'This requires SaveAs to:\n'+path+'\n\n'
                    'Revit may change the working document to this new file. The recovery file is retained outside the transmittal. '
                    'No Sync or Publish is performed. Afterward, review the working file before continuing project work.\n\n'
                    'Yes: authorize SaveAs. No: collect dependencies only and mark the host incomplete; do not substitute an older host.',
                    yes=True,no=True,title='Confirm current-state snapshot')
            registry.confirm_snapshot=confirm
            sources=[]
            for row in choices:
                if cancel():break
                if row.Mode=='LIVE_DOCUMENT':
                    key=registry.add_live(row.Document)
                    if row.CloudSelection:
                        selected=row.CloudSelection
                        try:registry.bind_graph(key,selected['graph'],selected['client'])
                        except Exception as exc:
                            registry.get(key)['inventory']['issues'].append(engine.issue(
                                getattr(exc,'code','CLOUD_GRAPH_BIND_FAILED'),key,exc,'error'))
                    sources.append(key)
                elif row.Mode=='PUBLISHED_VERSION':
                    selected=row.CloudSelection
                    sources.append(registry.add_graph(selected['graph'],selected['client']))
                else:sources.append(row.Source)
            for client in registry.clients.values():client.cancelled=cancel
            if sources:
                results=batch.run_batch(sources,root,
                    lambda package,source:SessionBackend(DB,uiapp.Application,package,registry,cancel),
                    opts,extras,cancel,pulse)
            was_cancelled=cancel()
        if not results:return forms.alert('Transmission cancelled before any models were processed.')
        message=engine.completion_message(results,len(choices),cancelled=was_cancelled)
        forms.alert(message+'\n\nOutput: '+root+'\n\nKeep Sources and _Refs together. Read each START_HERE.txt and batch.json before delivery.',title='EasyBIM e-transmit')
        os.startfile(root)
    finally:
        dialog.release_credentials()
        if registry is not None:
            try:registry.close()
            except Exception:script.get_logger().warning('Temporary downloaded snapshots could not all be removed. Working SaveAs recovery files are intentionally retained.')
