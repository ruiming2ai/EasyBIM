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
from . import cleanup, engine
from .revit import Backend


class Choice(object):
    def __init__(self, name, key='', checked=True, source='', modified=False):
        self.Name, self.Key, self.Checked, self.Source, self.Modified = name,key,checked,source,modified


class Dialog(forms.WPFWindow):
    def __init__(self, uiapp, xaml):
        forms.WPFWindow.__init__(self,xaml)
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
        except (IOError,ValueError): pass
        active=uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
        for doc in uiapp.Application.Documents:
            if doc.IsLinked or doc.IsFamilyDocument: continue
            source=f.text(doc.PathName)
            self.models.append(Choice(f.text(doc.Title),checked=doc==active,source=source,modified=bool(doc.IsModified)))
        self.models.sort(key=lambda x:(not x.Checked,x.Name.lower()))
        if self.models and not any(x.Checked for x in self.models): self.models[0].Checked=True
        self.Purge.IsEnabled=hasattr(DB.Document,'GetUnusedElements')
        self.refresh()

    def refresh(self):
        self.Models.ItemsSource=None; self.Models.ItemsSource=self.models
        self.Extras.ItemsSource=None; self.Extras.ItemsSource=self.extras
        self.Mappings.ItemsSource=None; self.Mappings.ItemsSource=self.mappings

    def append_models(self, paths):
        seen=set(f.canonical(m.Source) for m in self.models)
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
            row.Source=path; row.Checked=True; self.refresh()

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
                    reports=bool(self.Reports.IsChecked),zip=bool(self.Zip.IsChecked),mappings=[(x.Key,x.Source) for x in self.mappings])
        unresolved=[]
        for row in models:
            try: path=f.resolve_source(row.Source,mappings=opts['mappings'])
            except ValueError: path=None
            if not path: unresolved.append(row.Name+' : '+row.Source)
        if unresolved:
            return forms.alert('Choose an exact saved source using "Use saved copy" or add a prefix mapping.\n\n'+'\n'.join(unresolved))
        root=f.new_run_root(output,datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
        long_paths=engine.preflight_paths([x.Source for x in models],root,opts,self.extras)
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
        if any(x.Modified for x in models): notes.append('Open models have unsaved changes. Only the saved versions will be packaged; no source Save/Sync occurs.')
        if opts['upgrade']: notes.append('Package copies will be saved in Revit '+f.text(self.uiapp.Application.VersionNumber)+'. They cannot be opened in an older Revit.')
        if opts['cleanup']: notes.append('Cleanup may delete views/definitions or discard worksets IN COPIES ONLY. Retain your original models.')
        if notes and not forms.alert('\n\n'.join(notes)+'\n\nContinue?',yes=True,no=True): return
        if self.SaveSettings.IsChecked:
            saved=dict((k,opts[k]) for k in ('deep','repath','per_model','reports','zip','mappings'))
            saved['output']=output
            folder=os.path.dirname(self.settings)
            try:
                if not os.path.isdir(folder): os.makedirs(folder)
                with io.open(self.settings,'w',encoding='utf-8') as out: out.write(f.text(json.dumps(saved,indent=2)))
            except IOError as exc: forms.alert('Settings could not be saved: '+f.text(exc))
        self.result=([x.Source for x in models],root,opts,list(self.extras)); self.Close()


def run(uiapp,xaml):
    dialog=Dialog(uiapp,xaml); dialog.ShowDialog()
    if not dialog.result: return
    models,root,opts,extras=dialog.result
    results=[]
    with forms.ProgressBar(title='e-transmit',cancellable=True,indeterminate=True) as pb:
        def cancel(): return pb.cancelled
        def pulse(label,current,total):
            pb.title='e-transmit | '+f.text(label)
            pb.update_progress(current,max(1,total))
        groups=[[m] for m in models] if opts['per_model'] else [models]
        for index,group in enumerate(groups):
            if cancel(): break
            package=f.package_root(root,index,opts['per_model'])
            backend=Backend(DB,uiapp.Application,package,cancel)
            results.append(engine.transmit(group,package,backend,opts,extras,cancel,pulse))
        if results and opts['per_model']:
            with io.open(os.path.join(root,'START_HERE.txt'),'w',encoding='utf-8') as out:
                out.write('EasyBIM e-transmit '+VERSION+'\n\n'+'\n'.join(r['status']+' : '+os.path.relpath(r['root'],root) for r in results))
                if cancel(): out.write('\nCANCELLED: not all selected models were processed.')
        if results and opts['zip'] and not cancel(): engine.zip_package(root,root+'.zip',cancel)
    if not results: return forms.alert('Transmission cancelled before any models were processed.')
    message=engine.completion_message(results,len(models),cancelled=cancel())
    forms.alert(message+'\n\nOutput: '+root+'\n\nRead START_HERE.txt and verify copied models before delivery.',
                title='EasyBIM e-transmit')
    os.startfile(root)
