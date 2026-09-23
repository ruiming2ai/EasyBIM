# -*- coding: utf-8 -*-
"""Recursive package engine. The injected backend is the only Revit boundary."""
from __future__ import unicode_literals
import collections
import io
import json
import os
import ntpath
import shutil
import uuid
import zipfile
from . import files as f
from . import VERSION


def issue(code, source, message, severity='warning'):
    return dict(code=code, source=source, message=f.text(message), severity=severity)


def folder_files(folder):
    """Skip exposed symlinks and surface unreadable directories instead of hiding them."""
    def fail(exc): raise exc
    for root, dirs, names in os.walk(folder, onerror=fail, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
        for name in sorted(names):
            p = os.path.join(root, name)
            if not os.path.islink(p): yield p


def preflight_paths(models, root, options, extras=None):
    """Check known destinations BEFORE creating a report-only empty package.

    Nested references are still checked individually when discovered. This does
    not load a model or change the configured Desktop Connector source location.
    """
    errors = []
    groups = [[m] for m in models] if options.get('per_model') else [models]
    for index, group in enumerate(groups):
        package = f.package_root(root, index, options.get('per_model'))
        pm = ntpath if f.is_windows(package) else os.path
        for requested in list(group) + list(extras or []):
            try:
                source = f.resolve_source(requested, mappings=options.get('mappings'))
                if not source: continue  # Existing source-resolution validation reports this.
                relative = f.mirror_path(source)
                target = pm.join(package, *relative.split('/'))
                f.validate_destination_path(target)
            except f.PathLengthError as exc:
                errors.append(dict(code='DESTINATION_PATH_TOO_LONG', source=requested,
                                   target=target, message=f.text(exc)))
            except ValueError:
                continue  # Mapping errors are not mislabelled as path-length failures.
        stage = pm.join(package, '_work', '.et-00000000.rvt')
        try: f.validate_destination_path(stage)
        except f.PathLengthError as exc:
            errors.append(dict(code='DESTINATION_PATH_TOO_LONG', source='', target=stage, message=f.text(exc)))
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
    elif issues:
        state = 'completed with issues; review required'
    else:
        state = 'completed'
    lines = ['e-transmit ' + state + '.',
             'Host models copied: {0} / {1}'.format(hosts, requested),
             'Files copied: {0} | Issues: {1}'.format(files, len(issues))]
    if not hosts: lines.append('No host model was copied. This is NOT a completed transmittal.')
    for item in issues[:3]:
        lines.append('\n' + item['code'] + ': ' + item['message'])
    if len(issues) > 3: lines.append('\nSee START_HERE.txt for the remaining issues.')
    return '\n'.join(lines)


def transmit(models, root, backend, options=None, extras=None, cancelled=None, pulse=None):
    models = list(models)
    opts = options or f.defaults()
    if os.path.exists(root) and os.listdir(root):
        raise ValueError('Choose a new, empty package folder; existing files are never overwritten.')
    if not os.path.isdir(root): os.makedirs(root)
    result = dict(version=VERSION, status='RUNNING', root=root, models=[], files=[],
                  references=[], issues=[], options=opts, requested_models=list(models))
    work = os.path.join(root, '_work')
    os.makedirs(work)
    queue, records, edges = collections.deque(), {}, []
    origins = list(models)
    for source in origins: queue.append((source, '', True, None))
    for source in extras or []: queue.append((source, '', False, None))

    def add_issue(code, source, message, severity='warning'):
        result['issues'].append(issue(code, source, message, severity))

    try:
        while queue:
            f.check(cancelled)
            requested, owner, host, edge = queue.popleft()
            source, key, record = None, None, None
            try:
                source = f.resolve_source(requested, owner, opts.get('mappings'))
                if not source:
                    raise ValueError('No exact local/Connector path. Add an explicit source-prefix mapping; '
                                     'no live/latest or basename substitution is permitted.')
                if f.within(source, root):
                    raise ValueError('A source cannot be inside its output package.')
                cat = (edge or {}).get('category') or f.category(source, (edge or {}).get('kind', ''))
                if edge is not None: edge.update(local=source, category=cat)
                if not host and not opts['include'].get(cat, True):
                    if edge is not None: edge['status'] = 'EXCLUDED'
                    continue
                key = f.canonical(source)
                if host: result['models'].append(dict(requested=requested, source=source))
                if key in records:
                    if edge is not None: edge['status'] = records[key]['status']
                    continue
                if os.path.isdir(source):
                    if f.within(root,source):
                        raise ValueError('Output must be outside a recursively collected input folder.')
                    # Directories are explicit Add Folder or systems-report references only.
                    if not (edge is None or cat == 'analysis'):
                        raise ValueError('Expected a file, not a directory.')
                    if edge is not None: edge['status'] = 'DIRECTORY'
                    for child in folder_files(source):
                        queue.append((child, '', False, None))
                    continue
                relative = f.mirror_path(source)
                target = os.path.join(root, *relative.split('/'))
                record = dict(source=source, requested=requested, relative=relative,
                              target=target, category=cat, status='PENDING')
                records[key] = record
                result['files'].append(record)
                f.destination(root, relative)  # Validate before copying; failed paths remain in the report.
                if pulse: pulse('Copying ' + source, 0, 1)
                record.update(f.copy_file(source, target, cancelled, pulse))
                record['status'] = 'COPIED'
                if edge is not None: edge['status'] = 'COPIED'
                ext = os.path.splitext(source)[1].lower()
                if ext == '.rvt':
                    stage = f.temporary_path(work)
                    f.copy_file(target, stage, cancelled)
                    if pulse: pulse('Inspecting saved model ' + source, 0, 1)
                    record['inventory_status']='RUNNING'
                    scan = backend.scan(source, stage, opts)
                    record['inventory_status']='NEEDS_REVIEW' if scan.get('issues') else 'SCANNED'
                    record['revit_version'] = scan.get('version', '')
                    result['issues'].extend(scan.get('issues', []))
                    if f.signature(source) != (record['size'], record['source_mtime']):
                        record['inventory_status']='UNSTABLE'
                        raise IOError('Model changed during inspection; dependency inventory is not a stable snapshot.')
                    for ref in scan.get('references', []):
                        ref = dict(ref)
                        ref.update(owner=source, status='PENDING')
                        edges.append(ref)
                        queue.append((ref.get('source', ''), source, False, ref))
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
                if edge is not None: edge['status'] = 'UNRESOLVED'
                code = ('DESTINATION_PATH_TOO_LONG' if isinstance(exc, f.PathLengthError) else
                        ('UNRESOLVED_SOURCE' if not source else 'COLLECTION_FAILED'))
                if record and record.get('inventory_status') == 'RUNNING': record['inventory_status']='FAILED'
                add_issue(code, requested, exc, 'error')
                if key in records and records[key]['status'] == 'PENDING':
                    records[key]['status'] = 'FAILED'

        result['references'] = edges
        # Resolve targets only to successfully copied files. Never repath to a missing file.
        for ref in edges:
            rec = records.get(f.canonical(ref.get('local', '')))
            if rec and rec['status'] == 'COPIED' and ref['status'] != 'EXCLUDED':
                ref['target'] = rec['target']; ref['status'] = 'COPIED'
        if opts.get('repath') or opts.get('cleanup') or opts.get('upgrade'):
            for record in result['files']:
                f.check(cancelled)
                if record['status'] != 'COPIED' or not record['source'].lower().endswith('.rvt'): continue
                target = record['target']
                # Backend.finish receives the final target explicitly and stages absolute
                # references before opening. Internal paths need not repeat the source tree.
                stage = f.temporary_path(work)
                backup = f.temporary_path(work)
                f.copy_file(target, backup, cancelled)
                f.copy_file(target, stage, cancelled)
                model_edges = [r for r in edges if f.canonical(r['owner']) == f.canonical(record['source'])]
                try:
                    if pulse: pulse('Repath / cleanup ' + record['source'], 0, 1)
                    result['issues'].extend(backend.finish(stage, target, model_edges, opts) or [])
                    record['packaged_sha256'] = f.digest(target, cancelled)
                except f.Cancelled:
                    shutil.copyfile(backup, target)
                    for ref in model_edges: ref['repath']='ROLLED_BACK'
                    raise
                except Exception as exc:
                    shutil.copyfile(backup, target)
                    for ref in model_edges: ref['repath']='ROLLED_BACK'
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
        result['status'] = 'NEEDS_REVIEW' if any(x['severity'] != 'info' for x in result['issues']) else 'COLLECTED'
    except f.Cancelled:
        result['status'] = 'CANCELLED'
        add_issue('CANCELLED', '', 'Earlier completed copies remain. This is a partial transmittal.')
    except Exception as exc:
        result['status'] = 'FAILED'
        add_issue('PACKAGE_FAILED', '', exc, 'error')
    finally:
        result['references'] = edges
        try: shutil.rmtree(work)
        except Exception as exc: add_issue('STAGING_CLEANUP_FAILED', work, exc)
        # Hash the final bytes, not just the pre-repath source snapshot.
        for rec in result['files']:
            if rec['status'] == 'COPIED' and os.path.isfile(rec['target']):
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
             'Files copied: {0}'.format(counts['files_copied']), '', 'HOST MODELS:'] + hosts
    if not hosts: lines.append('No host model was copied. This is NOT a completed transmittal.')
    lines += ['', 'Keep the complete Sources folder hierarchy. Filenames have not been changed.',
              'Use the copied models only. Do not synchronize to the original central models.',
              'COLLECTED means no detected collection errors, not an in-Revit opening test.',
              'Review every warning before delivery. Reports contain original project paths.', '', 'ISSUES:']
    for i in result['issues']:
        lines.append('[{0}] {1}: {2} -- {3}'.format(i['severity'], i['code'], i['source'], i['message']))
    if not result['issues']: lines.append('No detected collection errors. Desktop opening test remains required.')
    with io.open(os.path.join(root, 'START_HERE.txt'), 'w', encoding='utf-8') as out:
        out.write('\n'.join(lines))
    if result['options'].get('reports', True):
        with io.open(os.path.join(root, 'REPORT.txt'), 'w', encoding='utf-8') as out: out.write('\n'.join(lines))
        f.write_csv(os.path.join(root, 'files.csv'), result['files'],
                    ['source','requested','relative','category','status','inventory_status','verification_status','size','sha256','packaged_sha256'])
        f.write_csv(os.path.join(root, 'references.csv'), result['references'],
                    ['owner','id','kind','source','local','target','loaded','status','repath','note'])
        f.write_csv(os.path.join(root, 'issues.csv'), result['issues'], ['severity','code','source','message'])


def zip_package(root, target, cancelled=None):
    if os.path.exists(target): raise IOError('ZIP already exists: ' + target)
    temp = target + '.partial-' + uuid.uuid4().hex
    try:
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path in folder_files(root):
                f.check(cancelled)
                archive.write(path, os.path.relpath(path, root))
        f.check(cancelled)
        f.publish(temp, target)
    finally:
        if os.path.exists(temp): os.remove(temp)
