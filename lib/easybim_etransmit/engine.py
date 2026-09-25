# -*- coding: utf-8 -*-
"""Recursive package engine. The injected backend is the only Revit boundary."""
from __future__ import unicode_literals
import collections
import hashlib
import io
import json
import os
import ntpath
import shutil
import tempfile
import sys
import traceback
import uuid
import zipfile
from . import files as f
from .pathnames import relative as relative_path
from . import VERSION


def issue(code, source, message, severity='warning'):
    row = dict(code=code, source=source, message=f.text(message), severity=severity)
    if isinstance(message, BaseException):
        row['exception_type'] = type(message).__name__
        if sys.exc_info()[1] is message:
            row['traceback'] = traceback.format_exc()
    return row


class StagingSourceError(ValueError):
    pass


def folder_files(folder):
    """Skip exposed symlinks and surface unreadable directories instead of hiding them."""
    if os.name == 'nt':
        from . import longpaths
        for path in longpaths.walk_files(folder): yield path
        return
    def fail(exc): raise exc
    for root, dirs, names in os.walk(folder, onerror=fail, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
        for name in sorted(names):
            p = os.path.join(root, name)
            if not os.path.islink(p): yield p


def preflight_paths(models, root, options, extras=None, model_names=None):
    """Use the same model-named layout as the real batch, before any save/copy."""
    from .batch import plan_jobs
    models=list(models);names=model_names or {};errors=[]
    separate=options.get('per_model') or options.get('zip_per_model') or any(f.text(m).startswith('open://') for m in models)
    try:jobs=plan_jobs(models,root,separate,names)
    except (ValueError,f.PathLengthError) as exc:
        return [dict(code='DESTINATION_PATH_TOO_LONG',source='',target=root,message=f.text(exc))]
    for job in jobs:
        package=job['root'];pm=ntpath if f.is_windows(package) else os.path
        for requested in job['models']+list(extras or []):
            target=package
            try:
                if f.text(requested).startswith('open://'):
                    name=names.get(requested,requested).replace('\\','/').rsplit('/',1)[-1]
                    if not name.lower().endswith('.rvt'):name+='.rvt'
                    target=pm.join(package,name);f.validate_destination_path(target)
                    target=pm.join(package,'_HostState','0'*20,name)
                    f.validate_copy_path(target)  # Raw baseline is never opened or modified.
                    if not options.get('saved_state_only',True):
                        target=pm.join(root+'_WorkingSnapshots','0'*20,name)
                        f.validate_destination_path(target)
                    continue
                source=f.resolve_source(requested,mappings=options.get('mappings'))
                if not source:continue
                relative=f.mirror_path(source);target=pm.join(package,*relative.split('/'))
                (f.validate_destination_path if target.lower().endswith('.rvt') else f.validate_copy_path)(target)
            except f.PathLengthError as exc:
                errors.append(dict(code='DESTINATION_PATH_TOO_LONG',source=requested,target=target,message=f.text(exc)))
            except ValueError:
                continue
    return errors


def package_counts(result):
    copied = dict((f.canonical(r['source']), r) for r in result['files'] if r['status'] == 'COPIED')
    hosts = sum(1 for model in result['models'] if f.canonical(model['source']) in copied)
    return dict(hosts_requested=len(result.get('requested_models', result['models'])),
                hosts_copied=hosts, files_copied=len(copied))


def completion_message(results, requested, cancelled=False):
    """Never describe a zero-host transmission as merely finished with issues."""
    hosts = sum(package_counts(r)['hosts_copied'] for r in results)
    files = sum(package_counts(r)['files_copied'] for r in results)
    issues = [i for r in results for i in r['issues'] if i['severity'] != 'info']
    if cancelled or any(r['status'] == 'CANCELLED' for r in results):
        state = 'cancelled (partial output)'
    elif hosts < requested or any(r['status'] == 'FAILED' for r in results):
        state = 'failed or incomplete'
    elif any(i['severity'] == 'error' for i in issues):
        state = 'incomplete; one or more files or model operations failed'
    elif issues:
        state = 'completed with issues; review required'
    else:
        state = 'completed'
    lines = ['e-transmit ' + state + '.',
             'Host models copied: {0} / {1}'.format(hosts, requested),
             'Files copied: {0} | Issues: {1}'.format(files, len(issues))]
    if not hosts: lines.append('No host model was copied. This is NOT a completed transmittal.')
    issues=sorted(issues,key=lambda i:(i['severity']!='error',not i['code'].startswith('HOST_')))
    retained=sum(1 for r in results for frow in r['files'] if (frow.get('current_state_integrity') or frow.get('saved_state_integrity'))=='VERIFIED')
    if retained:lines.append('Protected current-state host copies: {0}'.format(retained))
    for item in issues[:3]:
        lines.append('\n' + item['code'] + ': ' + item['message'])
    if len(issues) > 3: lines.append('\nSee START_HERE.txt for the remaining issues.')
    return '\n'.join(lines)


def portable_image_alias(root, row):
    """Return a short package-local alias only when Revit cannot safely address the mirror.

    The full source hierarchy remains untouched at mirror_target. The alias keeps
    the original basename and exists solely as the portable path stored by Revit.
    """
    if row.get('special') != 'image' or not row.get('target'):
        return None
    if f.path_units(row['target']) <= 240:
        return None
    target=row['target']
    pm=ntpath if f.is_windows(target) else os.path
    name=pm.basename(target)
    identity = '\n'.join([f.canonical(row.get('owner','')),
                           f.canonical(row.get('source') or target),
                           f.text(row.get('element_id') or row.get('id') or 'ref')])
    # Document-scoped element IDs alone collide in combined packages.
    ident = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]
    alias=os.path.join(root,'_Refs',ident,name)
    if f.path_units(alias) > 240:
        raise f.PathLengthError('Portable Revit reference path is still too long: '+alias)
    return alias


def transmit(models, root, backend, options=None, extras=None, cancelled=None, pulse=None):
    models = list(models)
    opts = options or f.defaults()
    if os.path.exists(root) and os.listdir(root):
        raise ValueError('Choose a new, empty package folder; existing files are never overwritten.')
    if not os.path.isdir(root): os.makedirs(root)
    result = dict(version=VERSION, status='RUNNING', root=root, models=[], files=[], aliases=[],
                  references=[], issues=[], options=opts, requested_models=list(models))
    # Revit inspection/processing scratch must not live in OneDrive or another
    # synchronized output tree.  Keep it in the local OS temp area and register
    # that one directory explicitly with the Revit backend write guard.
    work = tempfile.mkdtemp(prefix='EasyBIM_ET_')
    if hasattr(backend, 'set_staging_root'):
        backend.set_staging_root(work)
    queue, records, edges = collections.deque(), {}, []
    origins = list(models)
    for source in origins: queue.append((source, '', True, None))
    for source in extras or []: queue.append((source, '', False, None))

    def add_issue(code, source, message, severity='warning'):
        result['issues'].append(issue(code, source, message, severity))

    # Progress is advisory; a redraw failure must not be recorded as a failed
    # file copy. Preserve the cancellation signal, log the first UI failure,
    # and keep the verified file pipeline independent of the progress window.
    skipped_dependencies=set()
    progress_disabled = [False]
    def notify(label, current, total):
        if pulse is None or progress_disabled[0]:
            return
        try:
            pulse(label, current, total)
        except f.Cancelled:
            raise
        except Exception as exc:
            progress_disabled[0] = True
            add_issue('PROGRESS_UI_FAILED', label, exc)

    def copied_alias(edge):
        """Return an exact already-copied source for the same Revit element."""
        if edge is None or not edge.get('element_id'):
            return None, ''
        owner_key = f.canonical(edge.get('owner', ''))
        for previous in edges:
            if previous is edge:
                continue
            if f.canonical(previous.get('owner', '')) != owner_key:
                continue
            if previous.get('element_id') != edge.get('element_id'):
                continue
            candidate = previous.get('local') or previous.get('source') or ''
            if not candidate:
                continue
            if not f.is_desktop_connector_path(candidate) and f.within(candidate, work):
                continue
            rec = records.get(f.canonical(candidate))
            if rec and rec.get('status') == 'COPIED':
                return rec, candidate
        return None, ''

    def add_inventory(source, record, scan):
        record['inventory_status']='PARTIAL' if scan.get('open_failed') else ('NEEDS_REVIEW' if scan.get('issues') else 'SCANNED')
        record['revit_version']=scan.get('version','')
        record['opened_in_revit']=scan.get('opened_in_revit',False)
        record['inspection_status']=scan.get('inspection_status') or ('OPENED' if scan.get('opened_in_revit') else 'METADATA_ONLY')
        if scan.get('source_mode'): record['source_mode']=scan['source_mode']
        if 'plugin_coverage' in scan: record['plugin_coverage']=scan['plugin_coverage']
        result['issues'].extend(scan.get('issues',[]))
        for ref in scan.get('references',[]):
            ref=dict(ref);ref.update(owner=source,status='PENDING')
            edges.append(ref);queue.append((ref.get('source',''),source,False,ref))

    try:
        while queue:
            f.check(cancelled)
            requested, owner, host, edge = queue.popleft()
            source, key, record = None, None, None
            operation = 'resolve_source'
            try:
                skipper=getattr(backend,'skip_dependency',None)
                if not host and skipper and skipper(requested,owner,opts):
                    if edge is not None:
                        edge.update(status='SKIPPED_CLOUD_LINK',category='revit',skip_repath=True)
                    skipkey=f.canonical(requested)
                    if skipkey not in skipped_dependencies:
                        skipped_dependencies.add(skipkey)
                        add_issue('CLOUD_LINK_SKIPPED',requested,
                                  'Cloud-linked RVT acquisition is paused by the selected option. Its link element is retained; this does not prevent current-state host saving.')
                        scan=getattr(backend,'inventory_before_copy',lambda *a:None)(requested,opts)
                        if scan is not None:add_inventory(requested,{},scan)
                    continue
                resolver=getattr(backend,'resolve_acquired_source',None)
                resolved_request=resolver(requested,owner) if resolver else requested
                virtual=bool(getattr(backend,'is_virtual_source',lambda x:False)(resolved_request))
                source = resolved_request if virtual else f.resolve_source(resolved_request, owner, opts.get('mappings'))
                if not source and edge is not None and edge.get('optional_library'):
                    edge['status']='OPTIONAL_MISSING'
                    add_issue('OPTIONAL_LIBRARY_REFERENCE_MISSING',requested,'Exact optional Revit content-library resource is not installed.')
                    continue
                if not source:
                    alias_record, alias_source = copied_alias(edge)
                    if alias_record is not None:
                        edge['local'] = alias_source
                        edge['target'] = alias_record['target']
                        edge['status'] = 'DUPLICATE_ALIAS'
                        edge['skip_repath'] = True
                        continue
                    raise ValueError('No exact local/Connector path was exposed for this reference; '
                                     'no live/latest or basename substitution was performed.')
                if virtual:
                    source_in_output=False  # Only backend-registered identities reach this branch.
                elif f.is_desktop_connector_path(source):
                    # Desktop Connector is a Windows Shell namespace.  Do not
                    # call realpath/stat/isfile on it before Shell materializes
                    # the selected item.  A lexical containment check is enough
                    # here because the output itself is a normal filesystem path.
                    source_key = f.canonical(source).replace('\\', '/').rstrip('/')
                    root_key = f.canonical(root).replace('\\', '/').rstrip('/')
                    source_in_output = (source_key == root_key or
                                        source_key.startswith(root_key + '/'))
                else:
                    source_in_output = f.within(source, root)
                if source_in_output:
                    raise ValueError('A source cannot be inside its output package.')
                cat = (edge or {}).get('category') or f.category(source, (edge or {}).get('kind', ''))
                if edge is not None: edge.update(local=source, category=cat)
                if not host and not opts['include'].get(cat, True):
                    if edge is not None: edge['status'] = 'EXCLUDED'
                    continue
                # Session display paths can relocate into the inspection folder.
                # That is not evidence of an original link location. Never collect
                # our own scratch or guess its original root by the basename.
                if not virtual and not f.is_desktop_connector_path(source) and f.within(source, work):
                    # Deep inspection can expose the same Revit link twice:
                    # once from saved TransmissionData with its real source,
                    # and once from an in-session external-resource view whose
                    # display path points at our temporary inspection copy.
                    # If element identity proves it is the same already-copied
                    # reference, keep that exact source and ignore only the
                    # staging alias. Never search by basename.
                    alias_record, alias_source = copied_alias(edge)
                    if alias_record is not None:
                        edge['local'] = alias_source
                        edge['target'] = alias_record['target']
                        edge['status'] = 'DUPLICATE_ALIAS'
                        edge['skip_repath'] = True
                        continue
                    raise StagingSourceError(
                        'The reference points into the temporary inspection folder, not an original source. '
                        'Its saved source location could not be verified automatically. No live/latest or '
                        'same-name model was substituted.')
                key = f.canonical(source)
                if host: result['models'].append(dict(requested=requested, source=source))
                if key in records:
                    if edge is not None: edge['status'] = records[key]['status']
                    continue
                if not virtual and not f.is_desktop_connector_path(source) and os.path.isdir(source):
                    if f.within(root,source):
                        raise ValueError('Output must be outside a recursively collected input folder.')
                    # Directories are explicit Add Folder or systems-report references only.
                    if not (edge is None or cat == 'analysis'):
                        raise ValueError('Expected a file, not a directory.')
                    if edge is not None: edge['status'] = 'DIRECTORY'
                    for child in folder_files(source):
                        queue.append((child, '', False, None))
                    continue
                if (edge is not None and edge.get('optional_library') and
                        not f.is_desktop_connector_path(source) and not f.file_exists(source)):
                    edge['status'] = 'OPTIONAL_MISSING'
                    add_issue('OPTIONAL_LIBRARY_REFERENCE_MISSING', requested,
                              'Optional Revit content-library resource is not installed at this exact location. '
                              'The model was collected without substituting another filename.')
                    continue
                if (edge is not None and cat == 'analysis' and
                        not f.is_desktop_connector_path(source) and
                        not f.file_exists(source) and not os.path.isdir(source)):
                    edge['status'] = 'OPTIONAL_MISSING'
                    add_issue('ANALYSIS_RESOURCE_UNAVAILABLE', requested,
                              'Systems-analysis support resource is unavailable on this workstation; '
                              'the model and other dependencies continue to be collected.')
                    continue
                relative = backend.source_relative(source) if virtual else f.mirror_path(source)
                target = os.path.join(root, *relative.split('/'))
                record = dict(source=source, requested=requested, relative=relative,
                              target=target, category=cat, status='PENDING')
                records[key] = record
                result['files'].append(record)
                before=getattr(backend,'inventory_before_copy',lambda source,opts:None)(source,opts)
                if before is not None:
                    # Preserve live materials even when the host snapshot is
                    # refused/unavailable. Do not wait for host bytes to scan.
                    add_inventory(source,record,before)
                companions=getattr(backend,'additional_sources',None)
                if companions:
                    for companion in companions(source):
                        queue.append((companion,source,False,None))
                operation = 'validate_destination'
                f.destination(root, relative)  # Validate before copying; failed paths remain in the report.
                notify('Copying ' + source, 0, 1)
                operation = 'copy_file'
                acquire=getattr(backend,'acquire_file',None)
                metadata=acquire(source,target,owner,cancelled,notify) if acquire else f.copy_file(source,target,cancelled,notify)
                record.update(metadata)
                if record.get('staging_cleanup_warning'):
                    add_issue('SOURCE_STAGING_CLEANUP_FAILED', source,
                              record['staging_cleanup_warning'])
                record['status'] = 'COPIED'
                if edge is not None: edge['status'] = 'COPIED'
                late_inventory=getattr(backend,'inventory_after_copy',None)
                if late_inventory:
                    try:
                        late=late_inventory(source,opts)
                        if late:
                            result['issues'].extend(late.get('issues',[]))
                            if 'plugin_coverage' in late:record['plugin_coverage']=late['plugin_coverage']
                            for ref in late.get('references',[]):
                                ref=dict(ref);ref.update(owner=source,status='PENDING')
                                edges.append(ref);queue.append((ref.get('source',''),source,False,ref))
                    except f.Cancelled:raise
                    except Exception as exc:
                        add_issue('PLUGIN_SOURCE_COVERAGE',source,
                                  'Optional post-copy discovery failed ('+type(exc).__name__+'). Copied host retained.')
                ext = os.path.splitext(source)[1].lower()
                if ext == '.rvt':
                    if before is None:
                        operation = 'stage_model'
                        stage = f.temporary_path(work)
                        f.copy_file(target, stage, cancelled)
                        notify('Inspecting saved model ' + source, 0, 1)
                        record['inventory_status']='RUNNING'
                        operation = 'inspect_model'
                        scan = backend.scan(source, stage, opts)
                        add_inventory(source,record,scan)
                    if f.source_snapshot_changed(source, record):
                        record['inventory_status']='UNSTABLE'
                        raise IOError('Model changed during inspection; dependency inventory is not a stable snapshot.')
                elif ext == '.rcp':
                    support = os.path.splitext(source)[0] + ' Support'
                    if os.path.isdir(support):
                        for child in folder_files(support): queue.append((child, '', False, None))
                    add_issue('RCP_EXTERNAL_SCANS_UNVERIFIED', source,
                              'RCP and its adjacent "<name> Support" folder are collected. '
                              'Scans stored elsewhere and internal RCP paths require ReCap verification; '
                              'use Add Files/Folders for external scans. No RCP binary rewriting is attempted.')
                elif ext == '.nwf':
                    add_issue('NWF_DEPENDENCIES_UNVERIFIED', source,
                              'The NWF is copied, but its referenced models are not parsed. '
                              'Add those sources, or supply a published NWD.')
                elif ext in ('.dwg', '.dgn', '.dxf'):
                    add_issue('CAD_NESTED_DEPENDENCIES_UNVERIFIED', source,
                              'Direct Revit CAD link copied. CAD-internal Xrefs/fonts/images are not parsed; '
                              'add them or prepare the CAD dependency set with AutoCAD eTransmit.')
            except f.Cancelled:
                raise
            except Exception as exc:
                context=getattr(backend,'source_context',None)
                if record is not None and context:
                    record['source_context']=context(source)
                if edge is not None: edge['status'] = 'UNRESOLVED'
                code = ('STAGING_REFERENCE_UNRESOLVED' if isinstance(exc, StagingSourceError) else
                        ('DESTINATION_PATH_TOO_LONG' if isinstance(exc, f.PathLengthError) else
                         ('UNRESOLVED_SOURCE' if not source else 'COLLECTION_FAILED')))
                if record and record.get('inventory_status') == 'RUNNING': record['inventory_status']='FAILED'
                code=getattr(exc,'code',code)
                diagnostic = issue(code, requested, exc, 'error')
                diagnostic['operation'] = operation
                diagnostic['owner'] = owner
                result['issues'].append(diagnostic)
                if key in records and records[key]['status'] == 'PENDING':
                    records[key]['status'] = 'FAILED'

        result['references'] = edges
        context_reader=getattr(backend,'source_context',None)
        if context_reader:
            for record in result['files']:
                context=context_reader(record['source'])
                if context:record['source_context']=context
        # Resolve targets only to successfully copied files. Never repath to a missing file.
        for ref in edges:
            rec = records.get(f.canonical(ref.get('local', '')))
            if rec and rec['status'] == 'COPIED' and ref['status'] not in ('EXCLUDED', 'DUPLICATE_ALIAS', 'OPTIONAL_MISSING', 'SKIPPED_CLOUD_LINK'):
                ref['target'] = rec['target']; ref['status'] = 'COPIED'

        # Revit's image/PDF API still rejects some paths after Windows itself
        # successfully copied them past MAX_PATH. Preserve the complete mirror,
        # then create a second short package-local alias only for Revit storage.
        if opts.get('repath'):
            for ref in edges:
                if ref.get('status') != 'COPIED':
                    continue
                owner_record=records.get(f.canonical(ref.get('owner','')))
                if owner_record and (owner_record.get('current_state_backup') or owner_record.get('saved_state_backup')):
                    unavailable=any(f.canonical(r.get('owner',''))==f.canonical(ref.get('owner',''))
                        and (r.get('kind')=='RevitLink' or r.get('category')=='revit')
                        and not r.get('target') for r in edges)
                    if unavailable:
                        # No Revit reload will be attempted for this raw host.
                        # Its complete source mirror is sufficient; an unused
                        # operational alias must not add misleading path errors.
                        ref['repath']='DEFERRED_HOST_LINKS_UNAVAILABLE'
                        continue
                try:
                    alias=portable_image_alias(root,ref)
                    if not alias:
                        continue
                    mirror=ref['target']
                    parent=os.path.dirname(alias)
                    if not os.path.isdir(parent):
                        os.makedirs(parent)
                    f.copy_file(mirror,alias,cancelled,notify)
                    ref['mirror_target']=mirror
                    ref['target']=alias
                    ref['portable_alias']=True
                    result['aliases'].append(dict(source=ref.get('source',''),
                                                  mirror_target=mirror,target=alias,
                                                  element_id=ref.get('element_id',''),
                                                  reason='REVIT_PATH_LIMIT'))
                except f.Cancelled:
                    raise
                except Exception as exc:
                    add_issue('PORTABLE_ALIAS_FAILED',ref.get('source',''),exc,'error')
        if opts.get('repath') or opts.get('cleanup') or opts.get('upgrade'):
            for record in reversed(result['files']):
                f.check(cancelled)
                if record['status'] != 'COPIED' or not record['source'].lower().endswith('.rvt'): continue
                if record.get('inventory_status') in ('FAILED','RUNNING','UNSTABLE','PARTIAL'):
                    record['processing_status']='SKIPPED_INVENTORY_FAILURE'
                    continue
                if (record.get('current_state_backup') or record.get('saved_state_backup')):
                    missing=[r for r in edges if f.canonical(r.get('owner',''))==f.canonical(record['source'])
                             and (r.get('kind')=='RevitLink' or r.get('category')=='revit') and not r.get('target')]
                    if missing:
                        record['processing_status']='HOST_PRESERVED_LINKS_UNAVAILABLE'
                        record['model_verification']='DEFERRED'
                        add_issue('HOST_PRESERVED_WITHOUT_LINK_REPATH',record['source'],
                                  'The host snapshot and protected baseline are retained unchanged. Repath/cleanup and link-opening verification were deferred because linked RVT files are not packaged.')
                        continue
                target = record['target']
                # Backend.finish receives the final target explicitly and stages absolute
                # references before opening. Internal paths need not repeat the source tree.
                stage = f.temporary_path(work)
                backup = f.temporary_path(work)
                f.copy_file(target, backup, cancelled)
                f.copy_file(target, stage, cancelled)
                model_edges = [r for r in edges
                               if f.canonical(r['owner']) == f.canonical(record['source'])
                               and not r.get('skip_repath')]
                try:
                    notify('Repath / cleanup ' + record['source'], 0, 1)
                    processing_issues=backend.finish(stage, target, model_edges, opts) or []
                    result['issues'].extend(processing_issues)
                    record['processing_status']='NEEDS_REVIEW' if processing_issues else 'PROCESSED'
                    record['packaged_sha256'] = f.digest(target, cancelled)
                except f.Cancelled:
                    shutil.copyfile(backup, target)
                    for ref in model_edges: ref['repath']='ROLLED_BACK'
                    raise
                except Exception as exc:
                    shutil.copyfile(backup, target)
                    for ref in model_edges: ref['repath']='ROLLED_BACK'
                    record['processing_status']='FAILED'
                    add_issue('MODEL_PROCESSING_FAILED', record['source'],
                              'Unmodified collected copy retained: ' + f.text(exc), 'error')
                finally:
                    # Only files beginning with our newly allocated staging token belong to us.
                    prefix=os.path.splitext(os.path.basename(stage))[0]
                    for name in os.listdir(os.path.dirname(stage)):
                        if name.startswith(prefix):
                            path=os.path.join(os.path.dirname(stage),name)
                            if os.path.isdir(path): shutil.rmtree(path)
                            else: os.remove(path)
        verifier=getattr(backend,'verify_package',None)
        if verifier and opts.get('repath'):
            for record in reversed(result['files']):
                if record['status']!='COPIED' or not record['source'].lower().endswith('.rvt'): continue
                if record.get('inventory_status') in ('FAILED','UNSTABLE','RUNNING','PARTIAL'):
                    record['model_verification']='SKIPPED_INVENTORY_FAILURE'; continue
                if record.get('processing_status')=='FAILED':
                    record['model_verification']='SKIPPED_PROCESSING_FAILURE'; continue
                if record.get('processing_status')=='HOST_PRESERVED_LINKS_UNAVAILABLE':
                    record['model_verification']='DEFERRED';continue
                f.check(cancelled)
                notify('Verifying packaged model '+record['source'],0,1)
                model_edges=[r for r in edges if f.canonical(r['owner'])==f.canonical(record['source'])]
                problems=verifier(record['target'],model_edges,opts) or []
                result['issues'].extend(problems)
                record['model_verification'] = ('DEFERRED' if any(p['code']=='MODEL_VERIFICATION_DEFERRED' for p in problems)
                                               else ('FAILED' if any(p.get('severity')=='error' for p in problems)
                                                     else 'OPENED_AND_REFERENCES_CHECKED'))
        result['status'] = 'NEEDS_REVIEW' if any(x['severity'] != 'info' for x in result['issues']) else 'COLLECTED'
    except f.Cancelled:
        result['status'] = 'CANCELLED'
        add_issue('CANCELLED', '', 'Earlier completed copies remain. This is a partial transmittal.')
    except Exception as exc:
        result['status'] = 'FAILED'
        add_issue('PACKAGE_FAILED', '', exc, 'error')
    finally:
        result['references'] = edges
        try: f.remove_tree_retry(work)
        except Exception as exc: add_issue('STAGING_CLEANUP_FAILED', work, exc)
        # Hash the final bytes, not just the pre-repath source snapshot.
        for rec in result['files']:
            basis='saved_state' if rec.get('saved_state_backup') else 'current_state'
            if rec.get(basis+'_backup'):
                try:
                    if f.digest(rec[basis+'_backup'])!=rec[basis+'_sha256']:
                        raise IOError('Protected host baseline changed after collection.')
                    rec[basis+'_integrity']='VERIFIED'
                except Exception as exc:
                    rec[basis+'_integrity']='FAILED'
                    add_issue('HOST_BACKUP_VERIFICATION_FAILED',rec['source'],exc,'error')
            if rec['status'] == 'COPIED' and f.file_exists(rec['target']):
                try: rec['packaged_sha256'] = f.digest(rec['target'])
                except Exception as exc:
                    rec['verification_status']='FAILED'
                    add_issue('FINAL_VERIFICATION_FAILED',rec['source'],exc,'error')
        if result['status']=='COLLECTED' and any(i['severity']!='info' for i in result['issues']):
            result['status']='NEEDS_REVIEW'
        counts = package_counts(result)
        if result['status'] != 'CANCELLED' and counts['hosts_copied'] < counts['hosts_requested']:
            result['status'] = 'FAILED'
        write_reports(result)
    return result


def write_reports(result):
    root = result['root']
    with io.open(os.path.join(root, 'manifest.json'), 'w', encoding='utf-8') as out:
        out.write(f.text(json.dumps(result, ensure_ascii=False, indent=2)))
    hosts = []
    by_source = dict((f.canonical(r['source']), r) for r in result['files'])
    for model in result['models']:
        r = by_source.get(f.canonical(model['source']))
        if r and r['status'] == 'COPIED': hosts.append(r['relative'])
    counts = package_counts(result)
    lines = ['EasyBIM e-transmit ' + VERSION, 'Status: ' + result['status'],
             'Host models copied: {0} / {1}'.format(counts['hosts_copied'], counts['hosts_requested']),
             'Files copied: {0}'.format(counts['files_copied']),
             'Portable Revit aliases: {0}'.format(len(result.get('aliases', []))),
             'Packaged RVTs opened and references checked: {0}'.format(sum(1 for r in result['files'] if r.get('model_verification')=='OPENED_AND_REFERENCES_CHECKED')), '', 'HOST MODELS:'] + hosts
    if not hosts: lines.append('No host model was copied. This is NOT a completed transmittal.')
    for record in result['files']:
        context=record.get('source_context',{})
        if context:
            lines.append('Source mode: '+context.get('mode','')+' | State: '+context.get('state_basis',''))
            if (record.get('current_state_backup') or record.get('saved_state_backup')):
                lines.append(('Protected saved-state host: ' if record.get('saved_state_backup') else 'Protected current-state host: ')+(record.get('saved_state_backup') or record['current_state_backup'])+' | '+(record.get('saved_state_integrity') or record.get('current_state_integrity','NOT_CHECKED')))
            if context.get('saved_state_only'):
                lines.append('Unsaved edits are excluded. Open/source models were not saved, synchronized, published, reloaded or relocated.')
                metadata=context.get('cache_metadata',{})
                if metadata.get('cache_path'):
                    lines.append('Read-only cached source: '+metadata['cache_path'])
                if metadata.get('cache_document_version'):
                    lines.append('Saved revision: '+f.text(metadata['cache_document_version'])+
                                 ' | Match: '+f.text(metadata.get('revision_check','')))
                if context.get('inventory_basis'):
                    lines.append('Dependency inventory basis: '+context['inventory_basis'])
            else:
                working=context.get('working_document_path_after') or context.get('snapshot_path') or context.get('snapshot_attempt_path')
                if working:lines.append('Retained working document / SaveAs recovery file: '+working+
                                        ' -- review the active Revit file before continuing. This recovery file is outside the transmitted package.')
    lines += ['', 'Keep the complete package together, including Sources, _Refs and _HostState when present. Filenames have not been changed.',
              'Use the copied models only. Do not synchronize to the original central models.',
              'Use the packaged-RVT verification count above to distinguish copied files from models actually reopened and checked in Revit.',
              'Review every warning before delivery. Reports contain original project paths.', '', 'ISSUES:']
    for i in result['issues']:
        lines.append('[{0}] {1}: {2} -- {3}'.format(i['severity'], i['code'], i['source'], i['message']))
    if not result['issues']: lines.append('No detected collection errors. Desktop opening test remains required.')
    with io.open(os.path.join(root, 'START_HERE.txt'), 'w', encoding='utf-8') as out:
        out.write('\n'.join(lines))
    if result['options'].get('reports', True):
        with io.open(os.path.join(root, 'REPORT.txt'),'w',encoding='utf-8') as out: out.write('\n'.join(lines))
        f.write_csv(os.path.join(root, 'files.csv'), result['files'],
                    ['source','requested','relative','category','status','inventory_status','inspection_status','processing_status','model_verification','acquisition_container','verification_status','size','sha256','packaged_sha256'])
        f.write_csv(os.path.join(root, 'references.csv'), result['references'],
                    ['owner','id','kind','source','local','target','loaded','status','repath','note'])
        f.write_csv(os.path.join(root, 'issues.csv'), result['issues'], ['severity','code','source','operation','exception_type','message'])
        diagnostics = [i for i in result['issues'] if i.get('traceback')]
        if diagnostics:
            with io.open(os.path.join(root, 'DIAGNOSTICS.txt'), 'w', encoding='utf-8') as out:
                for item in diagnostics:
                    out.write('{0} | {1} | {2}\n{3}\n'.format(
                        item['code'], item.get('operation', ''), item['source'], item['traceback']))


def zip_package(root, target, cancelled=None):
    if os.path.exists(target): raise IOError('ZIP already exists: ' + target)
    temp = target + '.partial-' + uuid.uuid4().hex
    try:
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path in folder_files(root):
                f.check(cancelled)
                arcname = relative_path(path, root)
                if os.name == 'nt' and f.path_units(path)>240:
                    stage_dir=tempfile.mkdtemp(prefix='ET_Zip_')
                    stage=os.path.join(stage_dir,'file.bin')
                    try:
                        f.copy_file(path,stage,cancelled)
                        archive.write(stage,arcname)
                    finally: f.remove_tree_retry(stage_dir)
                else: archive.write(path,arcname)
        # Read every compressed member before publishing the archive. ZipExtFile
        # verifies each CRC on EOF, including ZIP64 members. This is cancellable.
        with zipfile.ZipFile(temp, 'r') as check_archive:
            for member in check_archive.infolist():
                with check_archive.open(member) as inp:
                    while True:
                        f.check(cancelled)
                        if not inp.read(1024 * 1024): break
        f.check(cancelled)
        f.publish(temp, target)
    finally:
        if os.path.exists(temp): os.remove(temp)
