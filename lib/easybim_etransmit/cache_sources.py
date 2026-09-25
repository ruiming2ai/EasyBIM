# -*- coding: utf-8 -*-
"""Read-only, identity-bound acquisition of locally saved Revit cloud caches.

Only the explicitly identified project's/model's native RVT is copied. Nothing
is written to a cache; BasicFileInfo and Revit opening are used on copies only.
This is a recovery-style workflow, not an Autodesk cache-management API.
"""
from __future__ import unicode_literals
import hashlib
import io
import os
import re
import tempfile
import uuid
from . import files as f, longpaths as lp, model_payload


class CacheError(ValueError):
    def __init__(self, code, message, evidence=None):
        ValueError.__init__(self, message)
        self.code = code
        self.evidence = evidence or {}


def guid(value):
    try:
        result = uuid.UUID(f.text(value or '').strip().strip('{}'))
        return str(result) if result.int else ''
    except (ValueError, TypeError, AttributeError):
        return ''


def version(value):
    if not isinstance(value, dict) or not guid(value.get('guid')):
        return None
    try:
        count = int(value['saves'])
        if count < 0 or isinstance(value['saves'], bool):
            return None
        return dict(guid=guid(value['guid']), saves=count)
    except (KeyError, ValueError, TypeError):
        return None


def reference_identity(row):
    """Cloud identity survives even when an unloaded resource has no path."""
    metadata = dict((f.text(k).lower(), v) for k, v in row.get('resource_information', {}).items())
    identity = row.get('cloud_identity') or {}
    project = guid(identity.get('project_guid') or metadata.get('linkedmodelprojectid'))
    model = guid(identity.get('model_guid') or metadata.get('linkedmodelmodelid'))
    if not project or not model:
        return {}
    return dict(project_guid=project, model_guid=model,
                region=f.text(identity.get('region') or metadata.get('linkedmodelregion') or '').upper())


def same_identity(left, right):
    return bool(guid(left.get('project_guid')) and guid(left.get('model_guid')) and
                guid(left.get('project_guid')) == guid(right.get('project_guid')) and
                guid(left.get('model_guid')) == guid(right.get('model_guid')) and
                (not left.get('region') or not right.get('region') or
                 f.text(left['region']).upper() == f.text(right['region']).upper()))


def discover_roots(application, environ=None):
    """Read the current release's user Revit.ini; never change its settings."""
    env = os.environ if environ is None else environ
    release = f.text(getattr(application, 'VersionNumber', ''))
    if not re.match(r'^20[0-9]{2}$', release):
        return []
    folder = 'Autodesk Revit ' + release
    roots = []
    def add(path):
        if path and f.absolute(path):
            path = os.path.normpath(path)
            if f.canonical(path) not in [f.canonical(p) for p in roots]:
                roots.append(path)
    def from_base(base):
        base = f.text(base or '').strip().strip('"')
        base = re.sub(r'%([^%]+)%', lambda m: env.get(m.group(1), m.group(0)), base)
        if not f.absolute(base) or '://' in base or f.is_desktop_connector_path(base):
            return
        tail = os.path.basename(os.path.normpath(base)).lower()
        if tail == 'collaborationcache':
            add(base)
        elif tail == folder.lower():
            add(os.path.join(base, 'CollaborationCache'))
        else:
            add(os.path.join(base, folder, 'CollaborationCache'))
            # Some deployments store the current-release cache directly here.
            add(os.path.join(base, 'CollaborationCache'))
    appdata = env.get('APPDATA', '')
    if appdata:
        ini = os.path.join(appdata, 'Autodesk', 'Revit', folder, 'Revit.ini')
        try:
            with open(ini, 'rb') as inp:
                raw = inp.read(1024 * 1024)
            encoding = 'utf-16' if list(bytearray(raw[:2])) in ([255, 254], [254, 255]) else 'utf-8-sig'
            text = raw.decode(encoding, 'replace')
            section = ''
            for line in text.splitlines():
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    section = line[1:-1].lower()
                elif section == 'cloudmodelcache' and '=' in line:
                    key, value = line.split('=', 1)
                    if key.strip().lower() == 'cachelocation':
                        from_base(value)
        except (IOError, OSError):
            pass
    # Keep the default as a candidate because a changed INI location only takes
    # effect in the next Revit process. Conflicting copies are not selected by age.
    local = env.get('LOCALAPPDATA', '')
    if local:
        add(os.path.join(local, 'Autodesk', 'Revit', folder, 'CollaborationCache'))
    return roots


def no_reparse(path):
    if os.name == 'nt':
        lp.no_reparse(path)
    else:
        current = os.path.abspath(path)
        while True:
            if os.path.islink(current):
                raise CacheError('CACHE_REPARSE_REFUSED', 'Cache path traverses a symlink: ' + path)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent


def find_candidates(roots, identity, cancelled=None):
    project, model = guid(identity.get('project_guid')), guid(identity.get('model_guid'))
    if not project or not model:
        raise CacheError('CACHE_MODEL_IDENTITY_MISSING',
                         'Cloud project/model GUIDs are required; display-name searches are not used.')
    candidates, seen, visited = [], set(), 0
    for root in roots:
        f.check(cancelled)
        if os.path.basename(os.path.normpath(root)).lower() != 'collaborationcache':
            continue
        if not os.path.isdir(root):
            continue
        try:
            no_reparse(root)
        except (IOError, OSError, CacheError):
            continue
        def onerror(exc):
            raise CacheError('CACHE_ENUMERATION_FAILED', f.text(exc))
        for current, dirs, names in os.walk(root, topdown=True, onerror=onerror, followlinks=False):
            f.check(cancelled)
            visited += 1
            if visited > 20000:
                raise CacheError('CACHE_SEARCH_LIMIT', 'Cache enumeration exceeded the bounded directory limit.')
            relative = os.path.relpath(current, root)
            parts = [] if relative == '.' else relative.split(os.sep)
            if len(parts) > 8:
                dirs[:] = []
                continue
            kept = []
            for name in sorted(dirs):
                if name.lower() in ('centralcache', 'paccache') or name.lower().endswith('_backup'):
                    continue
                try:
                    no_reparse(os.path.join(current, name))
                    kept.append(name)
                except (IOError, OSError, CacheError):
                    pass
            dirs[:] = kept
            if project not in [guid(p) for p in parts]:
                continue
            for name in sorted(names):
                if not name.lower().endswith('.rvt') or guid(name[:-4]) != model:
                    continue
                path = os.path.join(current, name)
                try:
                    no_reparse(path)
                except (IOError, OSError, CacheError):
                    continue
                key = f.canonical(path)
                if key not in seen:
                    candidates.append(path)
                    seen.add(key)
    return candidates


def _stream(path, writing=False):
    return lp.Stream(path, writing) if os.name == 'nt' else open(path, 'wb' if writing else 'rb')


def _hash(path, cancelled=None):
    value = hashlib.sha256()
    with _stream(path) as inp:
        while True:
            f.check(cancelled)
            block = inp.read(1024 * 1024)
            if not block:
                break
            value.update(block)
    return value.hexdigest()  # Finalize only once, including under IronPython.


def _copy_snapshot(source, target, cancelled=None, pulse=None):
    """Narrow read-only cache copy; the general cache-write/copy guard stays on.

    Source handles allow Revit's normal IO rather than locking a live cache for a
    long copy. A second source hash plus two stat checks detect concurrent writes.
    """
    no_reparse(source); no_reparse(target)
    if f.file_exists(target):
        raise CacheError('CACHE_STAGING_EXISTS', 'Refusing to overwrite snapshot: ' + target)
    before = f.signature(source)
    total, digest = 0, hashlib.sha256()
    try:
        with _stream(source) as inp, _stream(target, True) as out:
            while True:
                f.check(cancelled)
                block = inp.read(1024 * 1024)
                if not block:
                    break
                out.write(block); digest.update(block); total += len(block)
                if pulse:
                    pulse('Read-only cache copy | ' + os.path.basename(source), total, before[0])
            out.flush()
        checksum = digest.hexdigest()
        if total != before[0] or f.signature(source) != before:
            raise CacheError('CACHE_CHANGED_DURING_COPY', 'Cache changed during copying; no snapshot was accepted.')
        if _hash(target, cancelled) != checksum or _hash(source, cancelled) != checksum:
            raise CacheError('CACHE_CHANGED_DURING_COPY', 'Cache/copy checksum changed; no snapshot was accepted.')
        if f.signature(source) != before:
            raise CacheError('CACHE_CHANGED_DURING_COPY', 'Cache changed during verification.')
        return dict(sha256=checksum, size=total, source_mtime=before[1])
    except Exception:
        # Target is an isolated staging file owned by this operation. Source is
        # never removed, overwritten, renamed, unlocked or repaired.
        if os.path.isfile(target):
            os.remove(target)
        raise


def candidate_role(path, roots, identity):
    """Recognize only the explicit account/project cache layout, not substrings."""
    matches = []
    for root in roots:
        if not f.within(path, root):
            continue
        parts = os.path.relpath(path, root).split(os.sep)
        if (len(parts) in (3, 4) and guid(parts[1]) == guid(identity.get('project_guid'))
                and guid(os.path.splitext(parts[-1])[0]) == guid(identity.get('model_guid'))):
            role = 'DIRECT' if len(parts) == 3 else ('LINKED_MODELS' if parts[2].lower() == 'linkedmodels' else '')
            if role:
                matches.append(dict(role=role, scope=f.canonical(os.path.join(root, parts[0], parts[1]))))
    return matches[0] if len(matches) == 1 else dict(role='UNKNOWN', scope='')


def select_candidate(choices, evidence):
    """Resolve only a same-edition direct/LinkedModels pair within one authority."""
    ordered = sorted(choices, key=lambda item: (item[1]['cache_role'] != 'DIRECT',
                                               f.canonical(item[1]['cache_path']), item[1]['cache_path']))
    reason = 'IDENTICAL_BYTES' if len(choices) > 1 else 'UNIQUE_CANDIDATE'
    if len(set(item[1]['sha256'] for item in choices)) != 1:
        scopes = set(item[1]['cache_scope'] for item in choices)
        roles = set(item[1]['cache_role'] for item in choices)
        editions = set((item[1]['cache_document_version']['guid'],
                        item[1]['cache_document_version']['saves'], item[1]['cache_format']) for item in choices)
        paired = (len(scopes) == 1 and '' not in scopes and
                  roles == set(('DIRECT', 'LINKED_MODELS')) and len(editions) == 1)
        for role in roles:
            if len(set(item[1]['sha256'] for item in choices if item[1]['cache_role'] == role)) != 1:
                paired = False
        if not paired:
            raise CacheError('CACHE_CANDIDATES_AMBIGUOUS',
                             'Conflicting cache copies cannot be resolved by a same-edition direct/LinkedModels pair. No newest-timestamp selection was made.', evidence)
        reason = 'DIRECT_SAME_EDITION_PAIR'
    chosen = ordered[0]
    evidence.update(selection_reason=reason, selected_path=chosen[1]['cache_path'])
    for attempt in evidence['attempts']:
        if attempt.get('status') != 'REJECTED':
            attempt['selection'] = 'SELECTED' if attempt['path'] == chosen[1]['cache_path'] else 'ALTERNATE_NOT_SELECTED'
    return chosen


class Store(object):
    def __init__(self, DB, application, staging_root, roots=None, cancelled=None):
        self.DB, self.app, self.staging_root = DB, application, staging_root
        self.roots = list(roots) if roots is not None else discover_roots(application)
        self.cancelled = cancelled
        self._snapshots = {}

    def read_info(self, path):
        # All callers supply an isolated copy, NEVER the CollaborationCache RVT.
        basic = docver = None
        try:
            basic = self.DB.BasicFileInfo.Extract(path)
            docver = basic.GetDocumentVersion()
            return dict(version=dict(guid=f.text(docver.VersionGUID), saves=int(docver.NumberOfSaves)),
                        format=f.text(basic.Format), workshared=bool(basic.IsWorkshared))
        finally:
            for item in (docver, basic):
                if item is not None:
                    try: item.Dispose()
                    except Exception: pass

    def capture(self, entry, pulse=None):
        """Return one native stable snapshot with cloud and revision evidence."""
        f.check(self.cancelled)
        identity = entry.get('cloud') or {}
        expected = version(entry.get('document_version'))
        primary_modified = bool(entry.get('is_modified') and not entry.get('is_linked'))
        saved_link = bool(entry.get('is_linked'))
        evidence = dict(roots=list(self.roots), identity=dict(identity),
                        expected_document_version=expected, attempts=[])
        if not expected and not (primary_modified or saved_link):
            raise CacheError('CACHE_VERSION_UNAVAILABLE', 'The loaded saved DocumentVersion is unavailable.', evidence)
        if f.cache_source(self.staging_root) or any(f.within(self.staging_root, r) for r in self.roots):
            raise CacheError('CACHE_DESTINATION_REFUSED', 'Snapshot destination must be outside every cache.', evidence)
        no_reparse(self.staging_root)
        paths = find_candidates(self.roots, identity, self.cancelled)
        if not paths:
            raise CacheError('CACHE_MODEL_NOT_FOUND', 'No native local cache candidate matched the exact cloud project/model GUIDs.', evidence)
        if not os.path.isdir(self.staging_root):
            os.makedirs(self.staging_root)
        work = tempfile.mkdtemp(prefix='candidate-', dir=self.staging_root)
        accepted = []
        success = False
        try:
            for index, path in enumerate(paths):
                f.check(self.cancelled)
                target = os.path.join(work, str(index) + '.rvt')
                role = candidate_role(path, self.roots, identity)
                attempt = dict(path=path, status='READING', cache_role=role['role'], cache_scope=role['scope'])
                evidence['attempts'].append(attempt)
                try:
                    meta = _copy_snapshot(path, target, self.cancelled, pulse)
                    if model_payload.probe(target).get('container') != 'CFB':
                        raise CacheError('CACHE_NOT_NATIVE_RVT', 'The cache candidate is not a complete native RVT.')
                    info = self.read_info(target)
                    actual = version(info.get('version'))
                    attempt['actual_document_version'] = actual
                    attempt['format'] = f.text(info.get('format', ''))
                    if not actual:
                        raise CacheError('CACHE_VERSION_UNAVAILABLE', 'The copied RVT has no valid document revision.')
                    fmt, release = f.text(info.get('format', '')), f.text(getattr(self.app, 'VersionNumber', ''))
                    if not fmt.isdigit() or (release.isdigit() and int(fmt) > int(release)):
                        raise CacheError('CACHE_FORMAT_UNSUPPORTED', 'Copied cache format is unsupported by this Revit process.')
                    equal = actual == expected
                    if not equal and not (primary_modified or saved_link):
                        raise CacheError('CACHE_VERSION_MISMATCH', 'Copied cache does not match both the loaded revision GUID and save count.')
                    revision_check = ('MATCHES_LOADED_SAVED_VERSION' if equal else
                                      ('SAVED_CACHE_DIFFERS_FROM_LOADED' if expected else 'SAVED_CACHE_NO_LOADED_REVISION')
                                      if saved_link else 'SAVED_CACHE_ONLY_UNSAVED_EXCLUDED')
                    attempt.update(status='MATCH' if equal else 'SAVED_ONLY', actual_document_version=actual,
                                   sha256=meta['sha256'])
                    meta.update(cache_path=path, cache_role=role['role'], cache_scope=role['scope'], cache_document_version=actual, loaded_document_version=expected,
                                cache_model_identity=dict(identity), cache_format=fmt,
                                revision_check=revision_check,
                                unsaved_edits_excluded=bool(entry.get('is_modified')),
                                source_stability='VERIFIED_CACHE_SNAPSHOT', copy_method='COLLABORATION_CACHE_READ_ONLY')
                    accepted.append((target, meta, equal))
                except f.Cancelled:
                    raise
                except Exception as exc:
                    attempt.update(status='REJECTED', code=getattr(exc, 'code', 'CACHE_COPY_UNREADABLE'), message=f.text(exc))
                    if os.path.isfile(target): os.remove(target)
            if not accepted:
                code = 'CACHE_VERSION_MISMATCH' if any(a.get('code') == 'CACHE_VERSION_MISMATCH' for a in evidence['attempts']) else 'CACHE_SNAPSHOT_UNAVAILABLE'
                raise CacheError(code, 'No cache candidate passed native-file, stability and revision checks. See cache_evidence in manifest.json.', evidence)
            exact = [item for item in accepted if item[2]]
            choices = exact or accepted
            chosen, metadata, _ = select_candidate(choices, evidence)
            metadata['identical_candidates'] = sum(1 for item in choices if item[1]['sha256'] == metadata['sha256'])
            metadata['cache_evidence'] = evidence
            metadata['cache_selection_reason'] = evidence['selection_reason']
            reuse_key = (f.canonical(metadata['cache_path']), metadata['sha256'],
                         metadata['cache_document_version']['guid'], metadata['cache_document_version']['saves'])
            previous = self._snapshots.get(reuse_key)
            if previous:
                if not os.path.isfile(previous) or f.digest(previous, self.cancelled) != metadata['sha256']:
                    raise CacheError('CACHE_SNAPSHOT_CHANGED', 'Previously selected isolated snapshot changed or disappeared.', evidence)
                metadata['snapshot_reused'] = True
                return dict(path=previous, metadata=metadata)
            final = os.path.join(work, 'snapshot.rvt')
            os.rename(chosen, final)
            for other, _, _ in accepted:
                if other != chosen and os.path.isfile(other): os.remove(other)
            self._snapshots[reuse_key] = final
            success = True
            return dict(path=final, metadata=metadata)
        finally:
            if not success:
                f.remove_tree_retry(work)
