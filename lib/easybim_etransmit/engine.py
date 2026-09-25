# -*- coding: utf-8 -*-
"""Recursive package engine. The injected backend is the only Revit boundary."""
from __future__ import unicode_literals
import collections
import io
import json
import os
import ntpath
import shutil
import tempfile
import sys
import traceback
import zipfile
from . import files as f
from .pathnames import relative as relative_path
from . import VERSION, layout


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
    """Check filesystem delivery limits, not Revit's temporary processing limits."""
    from .batch import plan_jobs
    models=list(models); names=model_names or {}; errors=[]
    try: jobs=plan_jobs(models,root,True,names)
    except (ValueError,f.PathLengthError) as exc:
        return [dict(code='DESTINATION_PATH_TOO_LONG',source='',target=root,message=f.text(exc))]
    for job in jobs:
        candidates=[]
        for requested in job['models']+list(extras or []):
            source=f.resolve_source(requested,mappings=options.get('mappings')) if '://' not in requested else requested
            if not source: continue
            name=layout.basename(names.get(requested,source))
            if requested in job['models'] and not name.lower().endswith('.rvt'): name+='.rvt'
            candidates.append(dict(source=source, original_relative=(f.mirror_path(source) if f.absolute(source) else name),
                                   category=f.category(source), is_primary_host=requested in job['models']))
        layout.plan(candidates,options.get('file_structure'))
        for row in candidates:
            pm=ntpath if f.is_windows(job['root']) else os.path
            target=pm.join(job['root'],*row['relative'].split('/'))
            try: f.validate_copy_path(target)
            except f.PathLengthError as exc:
                errors.append(dict(code='DESTINATION_PATH_TOO_LONG',source=row['source'],target=target,message=f.text(exc)))
    return errors


def package_counts(result):
    copied = dict((f.canonical(r['source']), r) for r in result['files'] if r['status'] == 'COPIED')
    hosts = sum(1 for model in result['models'] if f.canonical(model['source']) in copied)
    links={}
    for ref in result.get('references',[]):
        if ref.get('kind')!='RevitLink' or ref.get('status') in ('EXCLUDED','DUPLICATE_ALIAS'):continue
        key=(f.canonical(ref.get('owner','')),ref.get('element_id') or ref.get('id') or ref.get('source',''))
        links[key]=ref
    return dict(hosts_requested=len(result.get('requested_models', result['models'])),
                hosts_copied=hosts, files_copied=len(copied),
                revit_links_requested=len(links),
                revit_links_copied=sum(1 for r in links.values() if r.get('target') and r.get('status')=='COPIED'),
                revit_links_verified=sum(1 for r in links.values() if r.get('verification')=='PATH_AND_LOAD_CHECKED'))


def link_discovery_status(result):
    hosts = [r for r in result.get('files', []) if r.get('is_primary_host')]
    missing = [r for r in hosts if not r.get('inventory_status') and r.get('status') != 'COPIED']
    if missing and len(missing) == len(hosts) and not result.get('references'):
        return 'NOT_PERFORMED: host acquisition prevented dependency inspection'
    if missing:
        return 'INCOMPLETE: host acquisition prevented some dependency inspection'
    return 'See per-model inventory status and inspection issues'


def cache_diagnostic_lines(evidence):
    if not evidence:
        return []
    lines = ['Loaded revision: '+f.text(evidence.get('expected_document_version')),
             'Cache selection: '+f.text(evidence.get('selection_reason', 'UNRESOLVED')),
             'Cache roots: '+f.text(evidence.get('roots', []))]
    for attempt in evidence.get('attempts', []):
        lines.append('Candidate: {0} | Role: {1} | {2} | {3} | Revision: {4} | SHA256: {5} | {6} {7}'.format(
            attempt.get('path', ''), attempt.get('cache_role', 'UNKNOWN'), attempt.get('status', ''),
            attempt.get('selection', ''), attempt.get('actual_document_version'), attempt.get('sha256', ''),
            attempt.get('code', ''), attempt.get('message', '')))
    return lines


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
    counts=[package_counts(r) for r in results]
    lines.append('Revit links requested / copied / verified: {0} / {1} / {2}'.format(
        *(sum(c[k] for c in counts) for k in ('revit_links_requested','revit_links_copied','revit_links_verified'))))
    if not hosts: lines.append('No host model was copied. This is NOT a completed transmittal.')
    for result in results:
        if link_discovery_status(result).startswith(('NOT_PERFORMED', 'INCOMPLETE')):
            lines.append('Revit link discovery: '+link_discovery_status(result))
    issues=sorted(issues,key=lambda i:(i['severity']!='error',not i['code'].startswith('HOST_')))
    retained=sum(1 for r in results for frow in r['files'] if (frow.get('current_state_integrity') or frow.get('saved_state_integrity'))=='VERIFIED')
    if retained:lines.append('Host acquisition checksums verified: {0}'.format(retained))
    for item in issues[:3]:
        lines.append('\n' + item['code'] + ': ' + item['message'])
    if len(issues) > 3: lines.append('\nSee START_HERE.txt for the remaining issues.')
    return '\n'.join(lines)



def transmit(models, root, backend, options=None, extras=None, cancelled=None, pulse=None):
    models = list(models)
    opts = dict(options or f.defaults())
    opts['file_structure'] = layout.mode(opts.get('file_structure'))
    if f.directory_has_entries(root):
        raise ValueError('Choose a new, empty package folder; existing files are never overwritten.')
    f.ensure_directory(root)
    result = dict(version=VERSION, status='RUNNING', root=root, models=[], files=[], aliases=[],
                  references=[], issues=[], options=opts, requested_models=list(models))
    retained_recovery=[]
    # Revit inspection/processing scratch must not live in OneDrive or another
    # synchronized output tree.  Keep it in the local OS temp area and register
    # that one directory explicitly with the Revit backend write guard.
    work = tempfile.mkdtemp(prefix='ET_')
    prepared = os.path.join(work, 'p')
    bundles = []
    identities = {}
    organized = [False]
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
            if ref.get('kind')=='RevitLink':
                ref['original_loaded']=ref.get('loaded')
                ref['package_loaded']=(True if opts.get('repath') and opts.get('load_unloaded_files',True)
                                       and ref.get('loaded') is False else ref.get('loaded'))
            edges.append(ref);queue.append((ref.get('source',''),source,False,ref))

    def organize(recover=False):
        if organized[0]: return
        contextual = {}
        for ref in edges:
            rec=records.get(f.canonical(ref.get('local','')))
            if rec and ref.get('category') in ('analysis','keynotes','decals'):
                contextual.setdefault(rec['source'],set()).add(ref['category'])
        for rec in result['files']:
            if rec['source'] in contextual:rec['category']=sorted(contextual[rec['source']])[0]
        layout.plan(result['files'], opts['file_structure'], bundles)
        for record in result['files']:
            if record['status'] == 'COPIED' and not recover:
                try:
                    target = f.destination(prepared, record['relative'])
                    if f.canonical(record['target']) != f.canonical(target):
                        f.copy_file(record['target'], target, cancelled, notify)
                    record['target'] = target
                except f.Cancelled: raise
                except Exception as exc:
                    record['processing_status'] = 'LAYOUT_FAILED'
                    add_issue('PACKAGE_LAYOUT_FAILED', record['source'], exc, 'error')
        for model in result['models']:
            rec = records.get(f.canonical(model['source']))
            if rec: model['source'] = rec['source']
        for ref in edges:
            owner = records.get(f.canonical(ref.get('owner', '')))
            if owner: ref['owner'] = owner['source']
        organized[0] = True

    def deliver():
        # Cancellation preserves already acquired files. A new cancellation during
        # delivery stops additional copies and retains the prepared recovery tree.
        was_cancelled = result['status'] == 'CANCELLED'
        delivery_cancel = None if was_cancelled else cancelled
        try: organize(recover=True)
        except Exception as exc:
            result['recovery_directory'] = work
            add_issue('PACKAGE_LAYOUT_FAILED', '', exc, 'error')
            for record in result['files']:
                record['target']=None
                if record['status']=='COPIED':record['status']='NOT_DELIVERED'
            for ref in edges:
                ref.pop('target',None);ref.pop('verification',None)
            return
        stage_paths = {}
        for record in result['files']:
            old = record['target']
            target = os.path.join(root, *record['relative'].split('/'))
            stage_paths[old] = target
            record['preparation_verification'] = record.pop('model_verification', 'NOT_ATTEMPTED')
            record['model_verification'] = 'NOT_ATTEMPTED'
            record['target'] = target
            if record['status'] != 'COPIED': continue
            record['acquisition_status'] = 'COPIED'
            try:
                if not was_cancelled: notify('Delivering '+record['source'],0,1)
                expected = f.digest(old,delivery_cancel)
                metadata = f.copy_file(old, f.destination(root, record['relative']), delivery_cancel, None if was_cancelled else notify)
                if metadata['sha256'] != expected: raise IOError('Delivered file differs from the prepared copy.')
                record['packaged_sha256'] = expected
                record['delivery_status'] = 'CHECKSUM_VERIFIED'
            except f.Cancelled:
                result['status'] = 'CANCELLED'
                result['recovery_directory'] = work
                record['status'] = 'NOT_DELIVERED'; record['delivery_status'] = 'CANCELLED'
            except Exception as exc:
                result['recovery_directory'] = work
                record['status'] = 'DELIVERY_FAILED'; record['delivery_status'] = 'FAILED'
                add_issue('PACKAGE_DELIVERY_FAILED', record['source'], exc, 'error')
        for ref in edges:
            ref['preparation_verification'] = ref.pop('verification', 'NOT_ATTEMPTED')
            rec = records.get(f.canonical(ref.get('local', '')))
            ref.pop('target', None)
            if rec and rec['status'] == 'COPIED' and ref['status'] not in ('EXCLUDED', 'OPTIONAL_MISSING', 'SKIPPED_CLOUD_LINK'):
                ref['target'] = rec['target']
            elif rec and ref['status'] in ('COPIED', 'DUPLICATE_ALIAS'):
                ref['status'] = 'NOT_DELIVERED'
        # Preparation messages must not advertise removed package paths.
        for diagnostic in result['issues']:
            for field in ('source', 'message', 'traceback'):
                if isinstance(diagnostic.get(field), f.string_types):
                    for old, new in sorted(stage_paths.items(), key=lambda item: -len(item[0])):
                        diagnostic[field] = diagnostic[field].replace(old, new)
        verifier = getattr(backend, 'verify_package', None)
        for record in reversed(result['files']):
            if record['status'] != 'COPIED' or not record['source'].lower().endswith('.rvt'): continue
            if not opts.get('repath') or not verifier:
                record['model_verification'] = 'NOT_ATTEMPTED'; continue
            if result['status'] == 'CANCELLED' or (cancelled and cancelled()):
                record['model_verification'] = 'DEFERRED'; continue
            if record.get('processing_status') in ('FAILED', 'ROLLBACK_FAILED', 'LAYOUT_FAILED'):
                record['model_verification'] = 'SKIPPED_PROCESSING_FAILURE'; continue
            if record.get('inventory_status') in ('FAILED', 'UNSTABLE', 'RUNNING', 'PARTIAL'):
                record['model_verification'] = 'SKIPPED_INVENTORY_FAILURE'; continue
            if record.get('processing_status') == 'HOST_PRESERVED_LINKS_UNAVAILABLE':
                record['model_verification'] = 'DEFERRED'; continue
            rows = [r for r in edges if f.canonical(r['owner']) == f.canonical(record['source'])]
            if any((r.get('kind')=='RevitLink' or r.get('category')=='revit') and not r.get('target') for r in rows):
                record['model_verification']='DEFERRED'
                add_issue('MODEL_VERIFICATION_DEFERRED', record['source'], 'A linked model was not delivered; final opening is deferred to prevent source/cloud fallback.', 'error')
                continue
            try:
                notify('Checking delivered model '+record['source'],0,1)
                f.validate_destination_path(record['target'])
                problems = verifier(record['target'], rows, opts) or []
                result['issues'].extend(problems)
                record['model_verification'] = ('DEFERRED' if any(p['code'] == 'MODEL_VERIFICATION_DEFERRED' for p in problems)
                    else 'FAILED' if any(p.get('severity') == 'error' for p in problems) else 'OPENED_AND_REFERENCES_CHECKED')
                if record['model_verification'] != 'OPENED_AND_REFERENCES_CHECKED':
                    add_issue('FINAL_LOCATION_VERIFICATION_FAILED', record['source'],
                        'Files are retained. Review the Revit errors; if the destination path is too long, move the complete package to a shorter location.', 'error')
            except f.Cancelled:
                result['status'] = 'CANCELLED'; record['model_verification'] = 'DEFERRED'
            except Exception as exc:
                record['model_verification'] = 'FAILED'
                add_issue('FINAL_LOCATION_VERIFICATION_FAILED', record['source'],
                    'Files are retained. Move the complete package to a shorter location if Revit rejects this path: ' + f.text(exc), 'error')

    try:
        while queue:
            f.check(cancelled)
            requested, owner, host, edge = queue.popleft()
            source, key, record = None, None, None
            operation = 'resolve_source'
            try:
                if edge is not None and edge.get('unconfigured'):
                    edge['status']='UNCONFIGURED'
                    continue
                if edge is not None and edge.get('kind')=='RevitLink' and not opts['include'].get('revit',True):
                    edge['status']='EXCLUDED'
                    continue
                if edge is not None and edge.get('resolution_failed'):
                    edge['status']='UNRESOLVED'
                    continue  # The inventory already contains a specific identity/name diagnostic.
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
                    if host:records[key]['is_primary_host']=True
                    if edge is not None: edge['status'] = records[key]['status']
                    continue
                if not virtual and not f.is_desktop_connector_path(source) and os.path.isdir(source):
                    if f.within(root,source):
                        raise ValueError('Output must be outside a recursively collected input folder.')
                    # Directories are explicit Add Folder or systems-report references only.
                    if not (edge is None or cat == 'analysis'):
                        raise ValueError('Expected a file, not a directory.')
                    if edge is not None: edge['status'] = 'DIRECTORY'
                    bundles.append(dict(root=source, category=cat if cat == 'analysis' else 'other'))
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
                target = os.path.join(work, 'a', layout.suffix(key), layout.basename(relative))
                record = dict(source=source, requested=requested, relative=relative,
                              target=target, category=cat, status='PENDING',is_primary_host=host,
                              original_relative=relative)
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
                f.validate_copy_path(target)  # Acquire once at a short path before allocating delivery names.
                notify('Copying ' + source, 0, 1)
                operation = 'copy_file'
                acquire=getattr(backend,'acquire_file',None)
                metadata=acquire(source,target,owner,cancelled,notify) if acquire else f.copy_file(source,target,cancelled,notify)
                record.update(metadata)
                if record.get('staging_cleanup_warning'):
                    add_issue('SOURCE_STAGING_CLEANUP_FAILED', source,
                              record['staging_cleanup_warning'])
                record['status'] = 'COPIED'
                identity_reader = getattr(backend, 'acquired_identity', None)
                identity = identity_reader(source, metadata) if identity_reader else f.canonical(source)
                record['layout_identity'] = identity
                previous = identities.get(identity)
                if previous is not None and previous.get('sha256') == record.get('sha256'):
                    previous.setdefault('source_aliases', []).append(source)
                    previous['is_primary_host'] = previous.get('is_primary_host') or host
                    records[key] = previous
                    result['files'].remove(record)
                    if edge is not None: edge['status'] = 'COPIED'
                    continue
                if previous is not None:
                    record['status'] = 'FAILED'
                    record['layout_identity'] = identity + '/conflict/' + key
                    raise ValueError('Conflicting acquired bytes for the same source identity; no version was substituted.')
                identities[identity] = record
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
                        bundles.append(dict(root=os.path.dirname(source), entry=source, support=support, category='pointcloud'))
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
                if edge is not None:
                    for field in ('element_id','kind','link_name','cloud_identity','resource_information'):
                        if field in edge:diagnostic[field]=edge[field]
                if record and record.get('source_context',{}).get('cache_evidence'):
                    diagnostic['cache_evidence']=record['source_context']['cache_evidence']
                result['issues'].append(diagnostic)
                if key in records and records[key]['status'] == 'PENDING':
                    records[key]['status'] = 'FAILED'

        organize()
        result['references'] = edges
        context_reader=getattr(backend,'source_context',None)
        if context_reader:
            for record in result['files']:
                context=context_reader(record['source'])
                if context:record['source_context']=context
        # Resolve targets only to successfully copied files. Never repath to a missing file.
        for ref in edges:
            rec = records.get(f.canonical(ref.get('local', '')))
            if rec and rec['status'] == 'COPIED' and ref['status'] not in ('EXCLUDED', 'OPTIONAL_MISSING', 'SKIPPED_CLOUD_LINK'):
                ref['target'] = rec['target']
                if ref['status'] != 'DUPLICATE_ALIAS': ref['status'] = 'COPIED'

        if opts.get('repath') or opts.get('cleanup') or opts.get('upgrade'):
            for record in reversed(result['files']):
                f.check(cancelled)
                if record['status'] != 'COPIED' or not record['source'].lower().endswith('.rvt'): continue
                if record.get('processing_status') == 'LAYOUT_FAILED': continue
                if record.get('inventory_status') in ('FAILED','RUNNING','UNSTABLE','PARTIAL'):
                    record['processing_status']='SKIPPED_INVENTORY_FAILURE'
                    continue
                if record.get('is_primary_host'):
                    missing=[r for r in edges if f.canonical(r.get('owner',''))==f.canonical(record['source'])
                             and (r.get('kind')=='RevitLink' or r.get('category')=='revit') and not r.get('target')]
                    if missing:
                        record['processing_status']='HOST_PRESERVED_LINKS_UNAVAILABLE'
                        record['model_verification']='DEFERRED'
                        add_issue('HOST_PRESERVED_WITHOUT_LINK_REPATH',record['source'],
                                  'The collected host is retained unchanged. Repath/cleanup and link-opening verification were deferred because linked RVT files are not packaged.')
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
                    processing_options=dict(opts)
                    processing_options['normalize_saved_cache']=record.get('copy_method')=='COLLABORATION_CACHE_READ_ONLY'
                    f.validate_destination_path(target)
                    processing_issues=backend.finish(stage, target, model_edges, processing_options) or []
                    result['issues'].extend(processing_issues)
                    record['processing_status']='NEEDS_REVIEW' if processing_issues else 'PROCESSED'
                    record['packaged_sha256'] = f.digest(target, cancelled)
                except f.Cancelled:
                    try:
                        shutil.copyfile(backup,target)
                        record['processing_status']='ROLLED_BACK'
                    except Exception as exc:
                        record['processing_status']='ROLLBACK_FAILED'
                        record['recovery_path']=backup;retained_recovery.append(backup)
                        add_issue('HOST_ROLLBACK_FAILED',record['source'],
                                  'Unmodified collected copy retained outside the package at '+backup+': '+f.text(exc),'error')
                    for ref in model_edges: ref['repath']=record['processing_status']
                    raise
                except Exception as exc:
                    record['processing_status']='FAILED'
                    try:
                        shutil.copyfile(backup,target)
                        for ref in model_edges:ref['repath']='ROLLED_BACK'
                    except Exception as rollback_exc:
                        record['processing_status']='ROLLBACK_FAILED'
                        record['recovery_path']=backup;retained_recovery.append(backup)
                        for ref in model_edges:ref['repath']='ROLLBACK_FAILED'
                        add_issue('HOST_ROLLBACK_FAILED',record['source'],
                                  'Unmodified collected copy retained outside the package at '+backup+': '+f.text(rollback_exc),'error')
                    add_issue('MODEL_PROCESSING_FAILED', record['source'],
                              'Model processing failed; see processing/recovery status: ' + f.text(exc), 'error')
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
                if record.get('processing_status') in ('FAILED','ROLLBACK_FAILED'):
                    record['model_verification']='SKIPPED_PROCESSING_FAILURE'; continue
                if record.get('processing_status')=='HOST_PRESERVED_LINKS_UNAVAILABLE':
                    record['model_verification']='DEFERRED';continue
                f.check(cancelled)
                notify('Checking prepared model '+record['source'],0,1)
                model_edges=[r for r in edges if f.canonical(r['owner'])==f.canonical(record['source'])]
                try: problems=verifier(record['target'],model_edges,opts) or []
                except f.Cancelled: raise
                except Exception as exc: problems=[issue('MODEL_OPEN_VERIFICATION_FAILED', record['source'], exc, 'error')]
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
        deliver()
        # Hash the final bytes, not just the pre-repath source snapshot.
        for rec in result['files']:
            if rec['status'] == 'COPIED' and f.file_exists(rec['target']):
                try:
                    rec['packaged_sha256'] = f.digest(rec['target'])
                    if rec.get('is_primary_host') and rec.get('processing_status') not in ('PROCESSED','NEEDS_REVIEW'):
                        if rec['packaged_sha256']!=rec['sha256']:
                            raise IOError('Unprocessed/restored host differs from the acquired copy.')
                except Exception as exc:
                    rec['verification_status']='FAILED'
                    add_issue('FINAL_VERIFICATION_FAILED',rec['source'],exc,'error')
        if result['status']=='COLLECTED' and any(i['severity']!='info' for i in result['issues']):
            result['status']='NEEDS_REVIEW'
        counts = package_counts(result)
        if result['status'] != 'CANCELLED' and counts['hosts_copied'] < counts['hosts_requested']:
            result['status'] = 'FAILED'
        try:
            write_reports(result)
        except Exception as exc:
            result['recovery_directory'] = work
            raise IOError('Reports could not be delivered. Prepared files retained at '+work+': '+f.text(exc))
        finally:
            if not retained_recovery and not result.get('recovery_directory'):
                try: f.remove_tree_retry(work)
                except Exception as exc:
                    add_issue('STAGING_CLEANUP_FAILED', work, exc)
                    write_reports(result)
    return result


def write_reports(result):
    # Reports also use verified long-path delivery under IronPython/Windows.
    if os.name == 'nt' and f.path_units(result['root']) > 200:
        scratch = tempfile.mkdtemp(prefix='ET_Report_')
        try:
            _write_reports_at(result, scratch)
            for path in folder_files(scratch):
                f.publish_report(path, os.path.join(result['root'], os.path.basename(path)))
        finally: f.remove_tree_retry(scratch)
    else: _write_reports_at(result, result['root'])


def _write_reports_at(result, root):
    result['counts']=package_counts(result)
    result['link_discovery_status']=link_discovery_status(result)
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
             'Revit links requested / copied / verified: {0} / {1} / {2}'.format(
                 counts['revit_links_requested'],counts['revit_links_copied'],counts['revit_links_verified']),
             'Revit link discovery: '+result['link_discovery_status'],
             'File structure: '+layout.mode(result['options'].get('file_structure')),
             'Packaged RVTs opened and references checked: {0}'.format(sum(1 for r in result['files'] if r.get('model_verification')=='OPENED_AND_REFERENCES_CHECKED')), '', 'HOST MODELS:'] + hosts
    if not hosts: lines.append('No host model was copied. This is NOT a completed transmittal.')
    if result.get('recovery_directory'): lines.append('Undelivered/recovery files retained at: '+result['recovery_directory'])
    for record in result['files']:
        if record.get('recovery_path'):
            lines.append('Processing rollback failed. Unmodified copy retained outside the package: '+record['recovery_path'])
        context=record.get('source_context',{})
        if context:
            lines.append('Source mode: '+context.get('mode','')+' | State: '+context.get('state_basis',''))
            if record.get('is_primary_host'):
                lines.append('Host acquisition checksum: '+record.get('sha256','NOT_ACQUIRED'))
                lines.extend(cache_diagnostic_lines(context.get('cache_evidence', {})))
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
    lines += ['', 'REVIT LINKS:']
    for ref in result.get('references',[]):
        if ref.get('kind')!='RevitLink':continue
        record=by_source.get(f.canonical(ref.get('local') or ref.get('source','')),{})
        context=record.get('source_context',{})
        evidence=context.get('cache_evidence',{})
        lines.append('Element {0} | {1} | {2} | Original loaded: {3} | Package loaded: {4}'.format(
            ref.get('element_id','?'),ref.get('link_name') or context.get('name') or ref.get('source',''),
            ref.get('status',''),ref.get('original_loaded',ref.get('loaded')),ref.get('package_loaded',ref.get('loaded'))))
        lines.append('  ACC identity: '+f.text(ref.get('cloud_identity') or context.get('cloud') or ref.get('resource_information',{})))
        lines.extend('  '+line for line in cache_diagnostic_lines(evidence))
    lines += ['', 'Keep the complete package together, including its Links folder. Filenames have not been changed.',
              'Use the copied models only. Do not synchronize to the original central models.',
              'Use the packaged-RVT verification count above to distinguish copied files from models actually reopened and checked in Revit.',
              'Review every warning before delivery. Reports contain original project paths.', '', 'ISSUES:']
    for i in result['issues']:
        label=i['source'] or i.get('owner','')
        if i.get('element_id'):label+=' #'+f.text(i['element_id'])+' '+i.get('kind','')
        lines.append('[{0}] {1}: {2} -- {3}'.format(i['severity'], i['code'], label, i['message']))
    if not result['issues']: lines.append('No detected collection errors. Desktop opening test remains required.')
    with io.open(os.path.join(root, 'START_HERE.txt'), 'w', encoding='utf-8') as out:
        out.write('\n'.join(lines))
    if result['options'].get('reports', True):
        with io.open(os.path.join(root, 'REPORT.txt'),'w',encoding='utf-8') as out: out.write('\n'.join(lines))
        f.write_csv(os.path.join(root, 'files.csv'), result['files'],
                    ['source','requested','relative','category','status','inventory_status','inspection_status','processing_status','preparation_verification','delivery_status','model_verification','collision_separated','acquisition_container','verification_status','size','sha256','packaged_sha256'])
        f.write_csv(os.path.join(root, 'references.csv'), result['references'],
                    ['owner','id','element_id','kind','link_name','source','local','target','loaded','original_loaded','package_loaded','status','repath','preparation_verification','verification','cloud_identity','note'])
        f.write_csv(os.path.join(root, 'issues.csv'), result['issues'], ['severity','code','source','owner','element_id','kind','operation','exception_type','message'])
        diagnostics = [i for i in result['issues'] if i.get('traceback')]
        cache_details = []
        for record in result['files']:
            evidence = record.get('source_context', {}).get('cache_evidence', {})
            if evidence:
                cache_details.append('Source: '+record['source'])
                cache_details.extend(cache_diagnostic_lines(evidence))
        if diagnostics or cache_details:
            with io.open(os.path.join(root, 'DIAGNOSTICS.txt'), 'w', encoding='utf-8') as out:
                out.write('Revit link discovery: '+result['link_discovery_status']+'\n'+'\n'.join(cache_details)+'\n')
                for item in diagnostics:
                    out.write('{0} | {1} | {2}\n{3}\n'.format(
                        item['code'], item.get('operation', ''), item['source'], item['traceback']))


def zip_package(root, target, cancelled=None, exclude_paths=None):
    if f.file_exists(target): raise IOError('ZIP already exists: ' + target)
    scratch = tempfile.mkdtemp(prefix='ET_Zip_')
    temp = os.path.join(scratch, 'package.zip')
    excluded = set(f.canonical(p) for p in (exclude_paths or []))
    try:
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path in folder_files(root):
                if f.canonical(path) in excluded: continue
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
        if f.file_exists(target): raise IOError('ZIP already exists: ' + target)
        f.copy_file(temp, target, cancelled)
    finally:
        f.remove_tree_retry(scratch)
