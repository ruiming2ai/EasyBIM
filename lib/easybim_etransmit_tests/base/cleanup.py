# -*- coding: utf-8 -*-
"""Opt-in cleanup of a detached PACKAGE copy, never a user's open document."""
from __future__ import unicode_literals
from . import files as f

VIEW_TYPES = ['FloorPlan','CeilingPlan','EngineeringPlan','AreaPlan','Section','Elevation',
              'Detail','DraftingView','ThreeD','Schedule','Legend','Walkthrough','Rendering']
MODES = [('all','Keep all sheets and all views'),
         ('sheets','Keep sheets and only views on sheets'),
         ('sheets_selected','Keep sheets, placed views, and selected view types'),
         ('no_sheets_all','Remove all sheets; keep all views'),
         ('no_sheets_selected','Remove all sheets; keep selected view types')]


def view_deletions(rows, mode, selected):
    if mode not in [x[0] for x in MODES]: raise ValueError('Unknown view cleanup mode.')
    if mode.endswith('selected') and not selected: raise ValueError('Select view types to retain.')
    if mode == 'all': return set()
    by_id = dict((r['id'],r) for r in rows)
    keep, remove = set(), set()
    for row in rows:
        key, typ = row['id'], row['type']
        if row.get('template') or row.get('protected'):
            keep.add(key)
        elif typ == 'DrawingSheet':
            (remove if mode.startswith('no_sheets') else keep).add(key)
        elif mode == 'no_sheets_all' or (not mode.startswith('no_sheets') and row.get('placed')):
            keep.add(key)
        elif mode.endswith('selected') and typ in selected:
            keep.add(key)
        else: remove.add(key)
    # Keep primary views of retained dependent views, transitively.
    pending = list(keep)
    while pending:
        primary = by_id.get(pending.pop(),{}).get('primary')
        if primary in by_id and primary not in keep:
            keep.add(primary); pending.append(primary)
    return remove - keep


def run(doc, DB, options, cancelled=None):
    from System.Collections.Generic import List, HashSet
    from .revit import eid
    f.check(cancelled)
    mode = options.get('views','all')
    placed = set()
    for cls_name, prop in [('Viewport','ViewId'),('ScheduleSheetInstance','ScheduleId')]:
        col = DB.FilteredElementCollector(doc).OfClass(getattr(DB,cls_name))
        try:
            for item in col: placed.add(eid(getattr(item,prop)))
        finally: col.Dispose()
    col = DB.FilteredElementCollector(doc).OfClass(DB.View)
    try: views = list(col)
    finally: col.Dispose()
    rows=[]
    for view in views:
        typ=f.text(view.ViewType)
        try: primary=eid(view.GetPrimaryViewId())
        except Exception: primary=None
        rows.append(dict(id=eid(view.Id),type=typ,primary=primary,
                         placed=eid(view.Id) in placed,template=bool(view.IsTemplate),
                         protected=typ not in VIEW_TYPES+['DrawingSheet'] or
                         bool(getattr(view,'IsTitleblockRevisionSchedule',False))))
    remove=view_deletions(rows,mode,options.get('view_types',[]))
    keep=set(row['id'] for row in rows)-remove
    tx=DB.Transaction(doc,'e-transmit: cleanup package copy')
    tx.Start()
    try:
        if remove:
            ids=List[DB.ElementId]([v.Id for v in views if eid(v.Id) in remove])
            deleted=doc.Delete(ids)
            if any(eid(i) in keep for i in deleted):
                raise RuntimeError('View deletion would cascade into a retained view; cleanup rolled back.')
        if options.get('purge'):
            if not hasattr(doc,'GetUnusedElements'):
                raise RuntimeError('This Revit API does not expose GetUnusedElements. Disable Purge or use a newer Revit.')
            # Multiple passes remove nested unused definitions. No guessed rule GUIDs.
            for iteration in range(20):
                f.check(cancelled)
                unused=doc.GetUnusedElements(HashSet[DB.ElementId]())
                ids=List[DB.ElementId]([i for i in unused if eid(i) not in keep])
                if ids.Count == 0: break
                deleted=doc.Delete(ids)
                if any(eid(i) in keep for i in deleted):
                    raise RuntimeError('Purge would cascade into a retained view; cleanup rolled back.')
            else:
                raise RuntimeError('Purge did not converge after 20 passes; package cleanup rolled back.')
        if tx.Commit()!=DB.TransactionStatus.Committed:
            raise RuntimeError('Cleanup transaction was not committed.')
    except Exception:
        if tx.GetStatus()==DB.TransactionStatus.Started: tx.RollBack()
        raise
    finally: tx.Dispose()
