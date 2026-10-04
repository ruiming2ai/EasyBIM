# -*- coding: utf-8 -*-
"""Pure, testable planning for seven diagnostic groups (not seven proven causes)."""
from __future__ import unicode_literals
import copy
import ntpath
import os
from .base.files import text

SCENARIOS = {
 'A': dict(title='e-transmit A Save Guard (test)',question='Does a post-SaveAs dirty flag reject a normally saved file?',trials=('strict_guard','close_reopen_evidence')),
 'B': dict(title='e-transmit B Save Events (test)',question='What transactions and loaded add-ins accompany the save-state change?',trials=('save_event_trace',)),
 'C': dict(title='e-transmit C Save Sequence (test)',question='Does CAD TransmissionData preparation change the first-save outcome?',trials=('without_metadata','with_cad_metadata')),
 'D': dict(title='e-transmit D CAD Routing (test)',question='Does a selected CAD type persist its package path through each API overload?',trials=('cad_string','cad_local_relative')),
 'E': dict(title='e-transmit E CAD Duplicates (test)',question='Do duplicate CAD types collide at one target versus per-type copies?',trials=('shared_target','per_type_target')),
 'F': dict(title='e-transmit F Saved Inventory (test)',question='Do live/report references match actual elements and paths in the saved copy?',trials=('saved_inventory',)),
 'H': dict(title='e-transmit H Saved Copy Repath (test)',question='Can the corrected task-copy open reach one CAD repair and verify its saved path on normal reopening?',trials=('saved_copy_cad',)),
 'G': dict(title='e-transmit G Recovery Evidence (test)',question='At which stage does the full baseline fail, and what candidate did rollback hide?',trials=('baseline_with_evidence',)),
}

def trials(key):
    if key not in SCENARIOS:raise ValueError('Unknown experiment: '+str(key))
    return SCENARIOS[key]['trials']

def collection_options(options=None):
    from .base.files import defaults
    result=defaults();result.update(copy.deepcopy(options or {}))
    result.update(repath=False,simple_repath=True,cleanup=False,upgrade=False,
                  discard_worksets=False,purge=False,zip=False,zip_per_model=False,
                  saved_state_only=True,reports=True,per_model=True,verify_in_process=False)
    return result

def key_path(path):
    return ntpath.normcase(ntpath.normpath(text(path or ''))).replace('/','\\')

def duplicate_groups(rows):
    groups={}
    for row in rows:
        source=row.get('source') or row.get('native_source') or ''
        if row.get('kind')!='CADLink' or not row.get('target') or not source:continue
        bucket=groups.setdefault(key_path(source),{})
        bucket[str(row.get('element_id'))]=row
    return [list(sorted(g.values(),key=lambda r:str(r.get('element_id'))))
            for _,g in sorted(groups.items()) if len(g)>1]

def select_rows(rows,ids):
    wanted=set(str(i) for i in ids)
    selected=[r for r in rows if str(r.get('element_id')) in wanted]
    if set(str(r.get('element_id')) for r in selected)!=wanted:
        raise ValueError('Selected reference IDs are not present; no substitute was selected.')
    return selected

def fresh_rows(rows):
    result=copy.deepcopy(rows)
    for row in result:
        for key in ('repath','verification','verified_saved_path','preparation_verification',
                    'repath_source','actual_path','api_result'):
            row.pop(key,None)
    return result

def package_path(root,relative):
    value=text(relative or '')
    if not value or ntpath.isabs(value) or ntpath.splitdrive(value)[0]:
        raise ValueError('Package path must be relative: '+value)
    parts=value.replace('\\','/').split('/')
    if any(p in ('','..','.') for p in parts):raise ValueError('Unsafe package path: '+value)
    target=os.path.realpath(os.path.join(root,*parts));base=os.path.realpath(root)
    if not target.startswith(base+os.sep):raise ValueError('Package path escapes root.')
    return target

def assess(normal,paths,relative,dirty,errors):
    if errors:return 'FAILED'
    if not normal:return 'REJECTED_NOT_NORMAL'
    if not paths:return 'PATHS_NOT_VERIFIED'
    if dirty:return 'PATHS_MATCH_REVIEW_DIRTY_STATE'
    if not relative:return 'PATHS_MATCH_ABSOLUTE_REVIEW'
    return 'VERIFIED_TEST_ONLY'
