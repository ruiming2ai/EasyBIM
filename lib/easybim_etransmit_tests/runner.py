# -*- coding: utf-8 -*-
"""Run distinct experiments on fresh file-backed copies. Preserve every failure."""
from __future__ import unicode_literals
import copy
import hashlib
import io
import json
import os
import shutil
import traceback
from . import VERSION, BASE_COMMIT, scenarios
from .base import files as f
from .evidence import Evidence,write_json
from .probes import ProbeBackend,run_trial


def copy_arm(source_root,host,rows,arm_root):
    """Clone exact collected targets; preserve relative layout and basename."""
    if os.path.exists(arm_root):raise ValueError('Experiment arm already exists; never overwrite it.')
    os.makedirs(arm_root)
    target=os.path.join(arm_root,os.path.basename(host))
    stage=os.path.join(arm_root,'Working','input.rvt');f.ensure_directory(os.path.dirname(stage))
    f.copy_file(host,stage)
    fresh=scenarios.fresh_rows(rows);copied={}
    for row in fresh:
        old=row.get('target')
        if not old:continue
        relative=os.path.relpath(old,source_root)
        new=scenarios.package_path(arm_root,relative)
        if f.canonical(old)==f.canonical(host):
            row.pop('target',None);row['test_note']='Self-reference is not repathed';continue
        if not os.path.isfile(old):
            row.pop('target',None);row['test_note']='Collected target absent';continue
        if f.canonical(new) not in copied:
            f.ensure_directory(os.path.dirname(new));f.copy_file(old,new);copied[f.canonical(new)]=True
        row['target']=new
    return stage,target,fresh


def execute(job,uiapp):
    from pyrevit import DB
    options=job['options'];scenario=options['scenario'];scenarios.trials(scenario)
    root=options['experiment_root'];source_root=options['input_root'];host=job['stage']
    if not os.path.isfile(os.path.join(root,'EXPERIMENT_ONLY.txt')):
        raise ValueError('Experiment root marker is missing.')
    if not os.path.isfile(host):raise ValueError('Saved input host is missing.')
    from .base.revit import Backend
    info=Backend(DB,uiapp.Application,source_root).basic(host)
    if f.text(info.get('version'))!=f.text(uiapp.Application.VersionNumber):
        raise ValueError('Run the test in the same Revit version as the saved input; no automatic upgrade.')
    original_hash=f.digest(host)
    evidence=Evidence(os.path.join(root,'Diagnostics'));backend=None
    summary=dict(scenario=scenario,title=scenarios.SCENARIOS[scenario]['title'],
        question=scenarios.SCENARIOS[scenario]['question'],version=VERSION,base_commit=BASE_COMMIT,
        input_sha256=original_hash,trial_results=[],production_tools_changed=False,
        all_outputs='EXPERIMENTAL, NOT FOR DELIVERY',source_context=options.get('source_context',{}),
        environment_label=options.get('environment_label','Normal installed environment'))
    write_json(os.path.join(root,'TEST_REPORT.json'),summary)
    try:
        evidence.subscribe(uiapp,root)
        summary['event_capture']=dict(subscribed=[name for app,name,handler in evidence.subscriptions],
            note='Missing subscriptions are unavailable, not proof that no event occurred.')
        if scenario=='B':evidence.census(uiapp)
        # No file is hard-linked. Each arm starts with its own byte copy of the
        # SAME saved host and the same already-collected file targets.
        for trial in scenarios.trials(scenario):
            trial_root=os.path.join(root,'Trials',trial)
            backend=ProbeBackend(DB,uiapp.Application,trial_root,evidence)
            result=dict(trial=trial,status='STARTED',root=trial_root)
            try:
                arm_rows=copy.deepcopy(job.get('rows',[]))
                # Only G needs the complete dependency set. Other tests isolate
                # a narrow question without copying every RVT/point-cloud again.
                selected=set(f.text(x) for x in options.get('selected_ids',[]))
                for row in arm_rows:
                    needed=(scenario=='G' or (scenario=='C' and row.get('kind')=='CADLink') or
                            (scenario in ('D','E') and f.text(row.get('element_id')) in selected))
                    if not needed:row.pop('target',None)
                stage,target,rows=copy_arm(source_root,host,arm_rows,trial_root)
                if f.digest(stage)!=original_hash:raise RuntimeError('Trial input checksum mismatch.')
                backend.set_staging_root(os.path.join(trial_root,'Working'))
                write_json(os.path.join(trial_root,'INPUT_REFERENCES.json'),rows)
                evidence.write('trial_start',trial=trial,input_sha256=original_hash)
                result.update(run_trial(backend,stage,target,rows,trial,options))
            except Exception as exc:
                result.update(status='FAILED',failed_stage=evidence.phase,message=f.text(exc),traceback=traceback.format_exc())
                evidence.write('trial_failed',trial=trial,message=f.text(exc),traceback=traceback.format_exc())
            finally:
                try:backend.cleanup_documents()
                except Exception as exc:
                    result.update(status='FAILED_DOCUMENT_CLOSE',message=f.text(exc))
                result['dirty_seen']=backend.dirty_seen
                if os.path.isdir(trial_root):write_json(os.path.join(trial_root,'RESULT.json'),result)
                summary['trial_results'].append(result)
                write_json(os.path.join(root,'TEST_REPORT.json'),summary)
            if result['status']=='FAILED_DOCUMENT_CLOSE':break
        summary['input_unchanged']=f.digest(host)==original_hash
        if not summary['input_unchanged']:raise RuntimeError('Original test input changed unexpectedly.')
        summary['status']='EXPERIMENTS_COMPLETED' if len(summary['trial_results'])==len(scenarios.trials(scenario)) else 'EXPERIMENTS_INCOMPLETE'
        return summary
    finally:
        evidence.close()
        summary['input_unchanged']=f.digest(host)==original_hash
        write_json(os.path.join(root,'TEST_REPORT.json'),summary)
        lines=[summary['title'],summary['question'],'','EXPERIMENT ONLY - NOT A PRODUCTION TRANSMITTAL',
               'Original saved input unchanged: '+str(summary['input_unchanged']),
               'Base production code: '+BASE_COMMIT,
               '']
        for item in summary['trial_results']:
            lines.extend([item['trial']+': '+item['status'],'  Stage: '+item.get('failed_stage','completed'),
                          '  '+item.get('message','')])
        lines.extend(['','Read TEST_REPORT.json, Trials/<arm>/RESULT.json and Diagnostics/events.jsonl.',
                      'Dirty flags are evidence, not proof of a callback or a valid repair.',
                      'A failed/not-normal candidate remains diagnostic only. No source/central synchronization.',
                      'Tests A-F use closed-workset save probes; G reproduces the full baseline lifecycle.',
                      'B observes installed add-ins; it does not disable them or create a clean profile.'])
        with io.open(os.path.join(root,'TEST_REPORT.txt'),'w',encoding='utf-8') as out:out.write('\n'.join(lines))
