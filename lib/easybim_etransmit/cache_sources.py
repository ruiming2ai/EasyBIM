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
from . import files as f, model_payload


class CacheError(ValueError):
    def __init__(self, code, message, evidence=None):
        ValueError.__init__(self, message)
        self.code = code
        self.evidence = evidence or {}


def guid(value):
    try:
        parsed = uuid.UUID(f.text(value or '').strip().strip('{}'))
        return str(parsed) if parsed.int else ''
    except (ValueError, AttributeError, TypeError):
        return ''


def version(value):
    if not isinstance(value, dict):
        return None
    token = guid(value.get('guid'))
    try:
        count = int(value.get('saves'))
    except (TypeError, ValueError):
        return None
    return dict(guid=token, saves=count) if token and count >= 0 else None


def _expand(value, environ):
    def replace(match):
        key = match.group(1)
        return environ.get(key, environ.get(key.upper(), match.group(0)))
    return re.sub(r'%([^%]+)%', replace, value).strip().strip('"')


def discover_roots(application, environ=None):
    """Use current-release defaults and documented Revit.ini CacheLocation.

    Configured and default roots are both considered because CacheLocation takes
    effect on the next Revit launch. Candidate revision checks decide validity.
    """
    environ = os.environ if environ is None else environ
    year = f.text(getattr(application, 'VersionNumber', ''))
    if not re.match(r'^20\d\d$', year):
        return []
    roots, seen = [], set()
    def add(path):
        if not path:
            return
        key = f.canonical(path)
        if key not in seen:
            seen.add(key); roots.append(os.path.normpath(path))
    roaming = environ.get('APPDATA', '')
    ini = os.path.join(roaming, 'Autodesk', 'Revit', 'Autodesk Revit '+year, 'Revit.ini') if roaming else ''
    if ini and os.path.isfile(ini):
        try:
            with open(ini, 'rb') as stream:
                raw = stream.read(1024*1024)
            encoding = 'utf-16' if list(bytearray(raw[:2])) in ([255, 254], [254, 255]) else 'utf-8-sig'
            content = raw.decode(encoding)
            section = ''
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith((';', '#')):
                    continue
                if line.startswith('[') and line.endswith(']'):
                    section = line[1:-1].strip().lower(); continue
                if section == 'cloudmodelcache' and '=' in line:
                    key, value = line.split('=', 1)
                    if key.strip().lower() != 'cachelocation':
                        continue
                    base = _expand(value.strip(), environ)
                    if not f.absolute(base):
                        continue
                    leaf = os.path.basename(os.path.normpath(base)).lower()
                    if leaf == 'collaborationcache':
                        add(base)
                    elif leaf == year:
                        add(os.path.join(base, 'CollaborationCache'))
                    else:
                        add(os.path.join(base, year, 'CollaborationCache'))
                        add(os.path.join(base, 'CollaborationCache'))
        except (IOError, OSError, UnicodeError):
            pass
    local = environ.get('LOCALAPPDATA', '')
    if local:
        add(os.path.join(local, 'Autodesk', 'Revit', 'Autodesk Revit '+year, 'CollaborationCache'))
    return roots


def _no_reparse(path):
    if os.name == 'nt':
        from . import longpaths
        longpaths.no_reparse(path)
    elif os.path.islink(path) or os.path.realpath(path) != os.path.abspath(path):
        raise CacheError('CACHE_REPARSE_PATH', 'Cache paths through symbolic links are not accepted.')


def _is_inside(path, root):
    key = f.canonical(path).replace('\\','/').rstrip('/')
    parent = f.canonical(root).replace('\\','/').rstrip('/')
    return key == parent or key.startswith(parent+'/')


def find_candidates(roots, identity, cancelled=None):
    project, model = guid(identity.get('project_guid')), guid(identity.get('model_guid'))
    if not project or not model:
        raise CacheError('CACHE_IDENTITY_UNAVAILABLE', 'Exact cloud project/model GUIDs were not exposed; no filename search was attempted.')
    found, seen, visited = [], set(), 0
    excluded = ('centralcache', 'paccache', 'backup', 'backups')
    for root in roots:
        f.check(cancelled)
        try:
            _no_reparse(root)
        except (CacheError, IOError, OSError):
            continue
        if not os.path.isdir(root):
            continue
        for current, dirs, names in os.walk(root, followlinks=False):
            f.check(cancelled); visited += 1
            if visited > 20000:
                raise CacheError('CACHE_DISCOVERY_LIMIT', 'Cache directory scan exceeded its bounded limit; no candidate was guessed.')
            depth = len(os.path.relpath(current, root).replace('\\','/').split('/'))
            keep = []
            for name in sorted(dirs):
                if name.lower() in excluded or name.lower().endswith('_backup') or depth >= 8:
                    continue
                try:
                    _no_reparse(os.path.join(current, name)); keep.append(name)
                except (CacheError, IOError, OSError):
                    pass
            dirs[:] = keep
            components = os.path.relpath(current, root).replace('\\','/').split('/')
            if project not in [guid(part) for part in components]:
                continue
            for name in sorted(names):
                stem, ext = os.path.splitext(name)
                if ext.lower() != '.rvt' or guid(stem) != model:
                    continue
                path = os.path.join(current, name)
                try:
                    _no_reparse(path)
                except (CacheError, IOError, OSError):
                    continue
                key = f.canonical(path)
                if key not in seen:
                    seen.add(key); found.append(path)
    return found


def _stream(path, writing=False):
    if os.name == 'nt':
        from . import longpaths
        return longpaths.Stream(path, writing)
    return open(path, 'wb' if writing else 'rb')


def _digest_readonly(path, cancelled=None):
    hashed = hashlib.sha256()
    with _stream(path) as inp:
        while True:
            f.check(cancelled)
            block = inp.read(1024*1024)
            if not block: break
            hashed.update(block)
    return hashed.hexdigest()  # IronPython: finalize exactly once.


def _copy_snapshot(source, target, cancelled=None, pulse=None):
    """A narrow exception to the generic cache-copy ban, never cache writes."""
    _no_reparse(source); _no_reparse(os.path.dirname(target))
    if f.cache_source(target) or f.file_exists(target):
        raise CacheError('CACHE_WRITE_REFUSED', 'Cache snapshots must use a new isolated destination outside all caches.')
    before = f.signature(source)
    digest, size = hashlib.sha256(), 0
    try:
        with _stream(source) as inp, _stream(target, True) as out:
            while True:
                f.check(cancelled)
                block = inp.read(1024*1024)
                if not block: break
                out.write(block); digest.update(block); size += len(block)
                if pulse: pulse(source, size, before[0])
            out.flush()
        fingerprint = digest.hexdigest()
        if before != f.signature(source) or size != before[0]:
            raise CacheError('CACHE_CHANGED_DURING_COPY', 'The cached model changed during copying. The snapshot was rejected, not retried as newest.')
        if _digest_readonly(target, cancelled) != fingerprint:
            raise CacheError('CACHE_COPY_CHECKSUM_FAILED', 'The isolated snapshot checksum did not match the copied bytes.')
        # Stat alone does not prove consistency. A second full read checks that
        # the completed source still equals the snapshot (also catches same-size writes).
        if _digest_readonly(source, cancelled) != fingerprint or before != f.signature(source):
            raise CacheError('CACHE_CHANGED_DURING_COPY', 'The cached model changed or was incomplete while the snapshot was validated.')
        return dict(sha256=fingerprint, size=size, source_mtime=before[1])
    except Exception:
        if f.file_exists(target): os.remove(target)
        raise


class Store(object):
    def __init__(self, DB, application, staging_root, roots=None, cancelled=None, read_info=None):
        self.DB, self.app, self.root = DB, application, staging_root
        self.roots = list(discover_roots(application) if roots is None else roots)
        self.cancelled = cancelled
        self.read_info = read_info or self._read_info
        if f.cache_source(staging_root) or any(_is_inside(staging_root, x) for x in self.roots):
            raise CacheError('CACHE_WRITE_REFUSED', 'Cache acquisition cannot write or create temporary files inside a cache.')

    def _read_info(self, path):
        basic = edition = None
        try:
            basic = self.DB.BasicFileInfo.Extract(path)
            edition = basic.GetDocumentVersion()
            return dict(version=dict(guid=f.text(edition.VersionGUID).lower(), saves=int(edition.NumberOfSaves)),
                        format=f.text(basic.Format), workshared=bool(basic.IsWorkshared))
        finally:
            for value in (edition, basic):
                if value is not None:
                    try: value.Dispose()
                    except Exception: pass

    def capture(self, entry, pulse=None):
        identity = entry.get('cloud') or {}
        expected = version(entry.get('document_version'))
        evidence = dict(roots=list(self.roots), project_guid=identity.get('project_guid',''),
                        model_guid=identity.get('model_guid',''), expected_version=expected, candidates=[])
        primary_modified = bool(entry.get('is_modified')) and not entry.get('is_linked')
        if not expected and not primary_modified:
            raise CacheError('CACHE_VERSION_UNAVAILABLE', 'The loaded document has no verifiable saved revision; no substitute was selected.', evidence)
        try:
            candidates = find_candidates(self.roots, identity, self.cancelled)
        except CacheError as exc:
            raise CacheError(exc.code, f.text(exc), evidence)
        if not candidates:
            raise CacheError('CACHE_MODEL_NOT_FOUND', 'No native CollaborationCache RVT matched this exact project/model identity. The open model and cache were not changed.', evidence)
        if not os.path.isdir(self.root): os.makedirs(self.root)
        accepted = []
        for source in candidates:
            f.check(self.cancelled)
            fd, staged = tempfile.mkstemp(prefix='cache-', suffix='.rvt', dir=self.root)
            os.close(fd); os.remove(staged)
            record = dict(path=source, status='CHECKING'); evidence['candidates'].append(record)
            try:
                meta = _copy_snapshot(source, staged, self.cancelled, pulse)
                payload = model_payload.probe(staged)
                if payload.get('container') != 'CFB':
                    raise CacheError('CACHE_NOT_NATIVE_RVT', 'The cache candidate was not a native RVT; it was not opened in Revit.')
                info = self.read_info(staged)
                actual = version(info.get('version'))
                record.update(document_version=actual, format=info.get('format',''))
                if not actual:
                    raise CacheError('CACHE_METADATA_UNREADABLE', 'The copied cache has no verifiable native document revision.')
                if actual != expected and not primary_modified:
                    raise CacheError('CACHE_REVISION_MISMATCH', 'The cached edition differs from the edition loaded by Revit. No newer/older cache was substituted.')
                meta.update(copy_method='COLLABORATION_CACHE_READ_ONLY', source_stability='VERIFIED_CACHE_SNAPSHOT',
                            cache_path=source, cache_document_version=actual, loaded_document_version=expected,
                            cache_project_guid=guid(identity.get('project_guid')), cache_model_guid=guid(identity.get('model_guid')),
                            unsaved_edits_excluded=bool(entry.get('is_modified')), native_format=info.get('format',''),
                            revision_check='MATCHES_LOADED_SAVED_VERSION' if actual == expected else 'SAVED_CACHE_ONLY_UNSAVED_EXCLUDED')
                record.update(status='VERIFIED', sha256=meta['sha256'], document_version=actual)
                accepted.append(dict(path=staged, metadata=meta))
            except f.Cancelled:
                if f.file_exists(staged): os.remove(staged)
                raise
            except Exception as exc:
                record.update(status='REJECTED', code=getattr(exc,'code','CACHE_READ_FAILED'),
                              error_type=type(exc).__name__, message=f.text(exc))
                if f.file_exists(staged): os.remove(staged)
        exact = [x for x in accepted if x['metadata']['cache_document_version'] == expected]
        choices = exact or accepted
        if not choices:
            codes = set(x.get('code') for x in evidence['candidates'])
            code = next(iter(codes)) if len(codes) == 1 else 'CACHE_NO_VALID_SNAPSHOT'
            raise CacheError(code or 'CACHE_NO_VALID_SNAPSHOT', 'No stable readable cache copy satisfied the identity/revision checks. See cache_evidence for candidate rejection details.', evidence)
        if len(set(x['metadata']['sha256'] for x in choices)) != 1:
            for x in accepted:
                if f.file_exists(x['path']): os.remove(x['path'])
            raise CacheError('CACHE_AMBIGUOUS', 'Multiple different cache copies fit this identity; the newest timestamp was not used to guess.', evidence)
        selected = choices[0]
        for other in accepted:
            if other is not selected and f.file_exists(other['path']): os.remove(other['path'])
        selected['metadata']['cache_evidence'] = evidence
        return selected
