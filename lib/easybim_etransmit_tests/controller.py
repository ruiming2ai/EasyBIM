# -*- coding: utf-8 -*-
"""Test-only UI: reuse collected files or collect with the cloned e-transmit UI."""
from __future__ import unicode_literals
import copy
import datetime
import hashlib
import io
import json
import os
import traceback
from . import VERSION,BASE_COMMIT,scenarios
from .base import files as f
from .evidence import write_json


def load_package(path):
    """Only manifest-listed, package-contained files can become test inputs."""
    with io.open(path,'r',encoding='utf-8-sig') as inp:manifest=json.load(inp)
    root=os.path.dirname(os.path.abspath(path));records=manifest.get('files',[])
    hosts=[r for r in records if r.get('is_primary_host') and r.get('status')=='COPIED']
    if len(hosts)!=1:raise ValueError('Select a per-model manifest containing exactly one copied primary host.')
    record=hosts[0];host=scenarios.package_path(root,record.get('relative') or record.get('original_relative'))
    if not os.path.isfile(host):raise ValueError('Host is absent next to this manifest. Use the complete local export package, not reports alone.')
    expected=record.get('packaged_sha256') or record.get('sha256')
    if not expected or f.digest(host)!=expected:
        raise ValueError('Input host does not match the manifest checksum. Collect a fresh package; no older or same-name substitute is used.')
    mapping={}
    for item in records:
        if item.get('status')!='COPIED' or not item.get('target'):continue
        rel=item.get('relative') or item.get('original_relative')
        actual=scenarios.package_path(root,rel)
        mapping[scenarios.key_path(item['target'])]=(actual,item)
    rows=[]
    for original in manifest.get('references',[]):
        # A per-model package may contain nested inventories: only the primary
        # owner's elements are valid IDs in the selected host.
        if f.canonical(original.get('owner',''))!=f.canonical(record.get('source','')):continue
        row=copy.deepcopy(original)
        match=mapping.get(scenarios.key_path(row.get('target'))) if row.get('target') else None
        if match:
            actual,item=match
            if os.path.isfile(actual):
                expected=item.get('packaged_sha256') or item.get('sha256')
                if not expected or f.digest(actual)!=expected:
                    raise ValueError('Collected dependency checksum mismatch: '+actual)
                row['target']=actual
            else:
                row.pop('target',None);row['test_input_note']='File listed but absent from this package.'
        else:row.pop('target',None)
        rows.append(row)
    return dict(root=root,host=host,rows=rows,source_context=record.get('source_context',{}),manifest=manifest)


def new_root(parent,scenario):
    name='ET_TEST_'+scenario+'_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    root=os.path.join(parent,name);index=1
    while os.path.exists(root):index+=1;root=os.path.join(parent,name+'_'+str(index))
    os.makedirs(root)
    with io.open(os.path.join(root,'EXPERIMENT_ONLY.txt'),'w',encoding='utf-8') as out:
        out.write('EXPERIMENTAL - NOT FOR DELIVERY\n'+scenarios.SCENARIOS[scenario]['title']+'\n'
                  'Existing production tools, input model and original package must remain unchanged.\n'
                  'Failed candidate files are evidence only. Never sync any test copy to a source central.\n')
    return root


def collect(uiapp,xaml,scenario):
    """Use a private clone of the original dialog, with source-write paths disabled."""
    from pyrevit import forms,DB
    from .base.ui import Dialog
    from .base.session import Registry,SessionBackend
    from .base import batch
    class CollectDialog(Dialog):
        def __init__(self):
            Dialog.__init__(self,uiapp,xaml)
            self.Title=scenarios.SCENARIOS[scenario]['title']
            self.VersionLabel.Text='Experiment '+VERSION+' | Collection base 2.1.28'
            self.Repath.IsChecked=True;self.Repath.IsEnabled=False
            self.SimpleRepath.IsChecked=True;self.SimpleRepath.IsEnabled=False
            self.Reports.IsChecked=True;self.Reports.IsEnabled=False
            for name in ('Cleanup','Upgrade','DiscardWorksets','Purge','Zip','ZipPerModel','SaveSettings'):
                control=getattr(self,name);control.IsChecked=False;control.IsEnabled=False
        def transmit_click(self,sender,args):
            self.Models.CommitEdit()
            choices=[r for r in self.models if r.Checked]
            if len(choices)!=1:return forms.alert('Select one host so the experiment arms use the same saved input.')
            output=f.text(self.Output.Text).strip()
            if not output or not os.path.isdir(output):return forms.alert('Choose an existing output folder.')
            choice=choices[0]
            if choice.Mode not in ('LIVE_DOCUMENT','SAVED_FILE'):return forms.alert('Use an open model or saved RVT.')
            self.result=(choice,output);self.Close()
    dialog=CollectDialog();dialog.ShowDialog()
    if not dialog.result:return None,None
    choice,parent=dialog.result;root=new_root(parent,scenario)
    registry=Registry(DB,uiapp.Application,os.path.join(root,'ReadOnlySnapshots'),collect_plugins=False,saved_state_only=True)
    try:
        if choice.Mode=='LIVE_DOCUMENT':
            source=registry.add_live(choice.Document)
            name=registry.get(source)['name']
        else:source=choice.Source;name=choice.Name
        options=scenarios.collection_options()
        options['runtime_provenance']=dict(version=VERSION,collection_base='2.1.28',
            base_commit=BASE_COMMIT,module_path=os.path.abspath(__file__),
            button_path=os.path.join(os.path.dirname(xaml),'script.py'),
            installation_root=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..')))
        with forms.ProgressBar(title='Collecting saved test input - no source save',cancellable=True) as pb:
            def pulse(label,current,total):
                pb.title=label;pb.update_progress(current,max(total,1))
            results=batch.run_batch([source],os.path.join(root,'Collected'),
                lambda package,key:SessionBackend(DB,uiapp.Application,package,registry,lambda:pb.cancelled),
                options,[],lambda:pb.cancelled,pulse,model_names={source:name})
            if pb.cancelled:raise f.Cancelled()
        if not results:raise ValueError('No saved host was collected.')
        package=load_package(os.path.join(results[0]['root'],'manifest.json'))
        return package,root
    finally:registry.close()


def select_ids(package,scenario):
    from pyrevit import forms
    cad=[r for r in package['rows'] if r.get('kind')=='CADLink' and r.get('target') and r['target'].lower().endswith('.dwg')]
    if scenario in ('D','H'):
        if scenario=='H':cad=[r for r in cad if r.get('loaded') is not False and r.get('original_loaded') is not False]
        choices={f.text(r['element_id'])+' | '+os.path.basename(r['target']):r for r in cad}
        if not choices:raise ValueError('No collected linked DWG is available for this experiment.')
        picked=forms.SelectFromList.show(sorted(choices),title='Select one CAD type for H' if scenario=='H' else 'Select one CAD type for both routing arms',multiselect=False)
        return None if picked is None else [f.text(choices[picked]['element_id'])]
    if scenario=='E':
        groups=scenarios.duplicate_groups(cad)
        if not groups:raise ValueError('NOT APPLICABLE: no duplicate CAD types with the same exact source were found.')
        choices={', '.join(f.text(r['element_id']) for r in group)+' | '+os.path.basename(group[0]['target']):group for group in groups}
        picked=forms.SelectFromList.show(sorted(choices),title='Select duplicate CAD group',multiselect=False)
        return None if picked is None else [f.text(r['element_id']) for r in choices[picked]]
    return []


def run(uiapp,xaml,scenario):
    from pyrevit import forms,script
    from .base import worker
    scenarios.trials(scenario)
    if worker.is_worker_process():return
    title=scenarios.SCENARIOS[scenario]['title']
    root=None
    try:
        choice=forms.CommandSwitchWindow.show(['Use existing package (faster)','Collect a fresh package'],
            message=title+'\n'+scenarios.SCENARIOS[scenario]['question']+'\nAll candidates are experimental; production tools stay unchanged.')
        if choice is None:return
        if choice.startswith('Use existing'):
            path=forms.pick_file(file_ext='json',title='Choose the per-model manifest.json beside its RVT and Links folder')
            if not path:return
            package=load_package(path)
            parent=forms.pick_folder(title='Output folder for a NEW experiment (input package is never overwritten)')
            if not parent:return
            root=new_root(parent,scenario)
        else:
            package,root=collect(uiapp,xaml,scenario)
            if package is None:return
        selected=select_ids(package,scenario)
        if selected is None:return
        diag=os.path.join(root,'Diagnostics');f.ensure_directory(diag)
        options=dict(scenario=scenario,experiment_root=root,input_root=package['root'],
                     diagnostics_root=diag,selected_ids=selected,source_context=package['source_context'],
                     independent_host=False,worker_timeout_seconds=7200)
        if scenario=='B':
            label=forms.ask_for_string(default='Normal installed add-ins',prompt='Environment label for comparison. This button does not disable add-ins.',title=title)
            if label is None:return
            options['environment_label']=label
        write_json(os.path.join(root,'EXPERIMENT_PLAN.json'),dict(options=options,question=scenarios.SCENARIOS[scenario]['question'],
            baseline=BASE_COMMIT,version=VERSION,host=package['host'],arms=scenarios.trials(scenario)))
        before=f.digest(package['host'])
        with forms.ProgressBar(title=title,cancellable=True) as pb:
            def pulse(label,current,total):pb.title=title+' | '+label;pb.update_progress(current,max(1,total))
            result=worker.run_separate_revit(uiapp.Application,package['host'],os.path.join(root,'NeverDelivered.rvt'),
                package['rows'],options,cancelled=lambda:pb.cancelled,pulse=pulse)
        if before!=f.digest(package['host']):raise RuntimeError('Original package host changed unexpectedly.')
        lines=[r['trial']+': '+r['status'] for r in result.get('trial_results',[])]
        forms.alert(title+'\n\n'+'\n'.join(lines)+'\n\nOpen TEST_REPORT.txt. Failure can be the expected diagnostic result.\nOutput: '+root,
                    title='e-transmit experiments - NOT FOR DELIVERY')
        os.startfile(root)
    except f.Cancelled:
        forms.alert('Experiment cancelled. Input package was not intentionally modified. Retained evidence: '+f.text(root or ''),title=title)
    except Exception as exc:
        message=traceback.format_exc();script.get_logger().error(message)
        if root:
            write_json(os.path.join(root,'LAUNCH_ERROR.json'),dict(message=f.text(exc),traceback=message))
            with io.open(os.path.join(root,'TEST_REPORT.txt'),'a',encoding='utf-8') as out:
                out.write('\nEXPERIMENT STOPPED: '+f.text(exc)+'\nSee Diagnostics and LAUNCH_ERROR.json.\n')
        forms.alert('Experiment stopped: '+f.text(exc)+'\n\nEvidence: '+f.text(root or 'No experiment started'),title=title)
