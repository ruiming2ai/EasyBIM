# -*- coding: utf-8 -*-
"""Read-only RVT/container inspection and scoped composite-download acquisition.

No geometry, model identities or proprietary streams are rewritten here. Revit
is still the authority on model readability. CFB validation only identifies the
container and the small BasicFileInfo stream before the Revit API is called.
"""
from __future__ import unicode_literals
import os
import ntpath
import re
import stat
import struct
import uuid
import zipfile
from . import files as f

CFB = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
END, FREE = 0xfffffffe, 0xffffffff
MAX_META = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024 * 1024
MAX_MEMBERS = 10000


class PayloadError(ValueError):
    pass


def _read_basic(path):
    """Bounded MS-CFB reader: directory + BasicFileInfo only, no RVT modifications."""
    with open(path, 'rb') as inp:
        inp.seek(0, 2); size = inp.tell(); inp.seek(0)
        head = inp.read(512)
        if len(head) != 512 or head[:8] != CFB:
            raise PayloadError('Not a complete CFB/RVT container.')
        major, order, shift, mini_shift = struct.unpack_from('<4H', head, 26)
        if order != 65534 or (major, shift) not in ((3, 9), (4, 12)) or mini_shift != 6:
            raise PayloadError('Unsupported CFB header; no model processing attempted.')
        sector = 1 << shift
        count = size // sector - 1
        if count < 1 or size % sector:
            raise PayloadError('Truncated or unaligned CFB/RVT container.')
        nfat, first_dir = struct.unpack_from('<II', head, 44)
        cutoff, first_mini, nmini, first_difat, ndifat = struct.unpack_from('<5I', head, 56)
        if nfat > count or ndifat > count or nmini > count or cutoff != 4096:
            raise PayloadError('Invalid CFB allocation sizes.')
        def block(ident):
            if ident < 0 or ident >= count:
                raise PayloadError('CFB sector is outside the acquired file.')
            inp.seek((ident + 1) * sector)
            value = inp.read(sector)
            if len(value) != sector: raise PayloadError('Incomplete CFB sector.')
            return value
        fat_ids = [i for i in struct.unpack_from('<109I', head, 76) if i != FREE]
        seen, ident = set(), first_difat
        for _ in range(ndifat):
            if ident in seen: raise PayloadError('Cyclic CFB DIFAT.')
            seen.add(ident)
            ints = struct.unpack('<{0}I'.format(sector // 4), block(ident))
            fat_ids.extend(i for i in ints[:-1] if i != FREE); ident = ints[-1]
        if len(fat_ids) < nfat or nfat * sector > MAX_META:
            raise PayloadError('Missing or excessive CFB allocation table.')
        fat = []
        for ident in fat_ids[:nfat]:
            fat.extend(struct.unpack('<{0}I'.format(sector // 4), block(ident)))
        def chain(start, table, reader, unit, limit=MAX_META):
            parts, visited, ident = [], set(), start
            while ident != END:
                if ident in visited or ident >= len(table) or ident == FREE:
                    raise PayloadError('Invalid or cyclic CFB allocation chain.')
                if (len(parts) + 1) * unit > limit:
                    raise PayloadError('CFB metadata exceeds the safe reading limit.')
                visited.add(ident); parts.append(reader(ident)); ident = table[ident]
            return b''.join(parts)
        directory = chain(first_dir, fat, block, sector)
        root, basic = None, None
        for offset in range(0, len(directory), 128):
            entry = directory[offset:offset+128]
            if len(entry) != 128: break
            length, kind = struct.unpack_from('<HB', entry, 64)
            if kind not in (2, 5): continue
            if length < 2 or length > 64 or length % 2: raise PayloadError('Invalid CFB directory name.')
            name = entry[:length-2].decode('utf-16-le')
            start, length_bytes = struct.unpack_from('<IQ', entry, 116)
            if major == 3: length_bytes &= 0xffffffff
            if kind == 5: root = (start, length_bytes)
            if kind == 2 and name == 'BasicFileInfo':
                if basic is not None: raise PayloadError('Ambiguous CFB BasicFileInfo stream.')
                basic = (start, length_bytes)
        if root is None or basic is None:
            raise PayloadError('CFB file does not contain Revit BasicFileInfo.')
        if not basic[1] or basic[1] > MAX_META:
            raise PayloadError('Invalid BasicFileInfo size.')
        if basic[1] < cutoff:
            mini_fat_raw = chain(first_mini, fat, block, sector)
            mini_fat = struct.unpack('<{0}I'.format(len(mini_fat_raw)//4), mini_fat_raw)
            mini_stream = chain(root[0], fat, block, sector)[:root[1]]
            def small(i):
                value = mini_stream[i*64:(i+1)*64]
                if len(value) != 64: raise PayloadError('Truncated CFB mini stream.')
                return value
            data = chain(basic[0], mini_fat, small, 64)[:basic[1]]
        else:
            data = chain(basic[0], fat, block, sector)[:basic[1]]
        if len(data) != basic[1]: raise PayloadError('Incomplete BasicFileInfo stream.')
    # Revit BasicFileInfo is UTF-16 text preceded by small binary fields in
    # some versions. Try both alignments; never infer a year from a filename.
    texts = [data[j:len(data)-(len(data)-j)%2].decode('utf-16-le', 'replace') for j in (0, 1)]
    matches = [(value, re.search(r'Format:\s*(20\d{2})(?!\d)', value)) for value in texts]
    found = [(value, match) for value, match in matches if match]
    if len(found) != 1: raise PayloadError('Unable to identify the saved Revit format from BasicFileInfo.')
    value, match = found[0]
    central = re.search(r'Central Model Path:[ \t]*([^\r\n\x00]*)', value)
    ws = re.search(r'Worksharing:[ \t]*(Central|Local|None|Not Enabled|Disabled)', value, re.I)
    return dict(container='CFB', version=match.group(1),
                workshared=bool(ws and ws.group(1).lower() in ('central', 'local')),
                worksharing_known=bool(ws),
                central=central.group(1).strip() if central else '',
                validation='CONTAINER_METADATA_ONLY')


def probe(path):
    try:
        with open(path, 'rb') as inp: prefix = inp.read(8)
        if prefix == CFB: return _read_basic(path)
        if prefix[:4] in (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'):
            return dict(container='ZIP', validation='ARCHIVE_NOT_YET_EXTRACTED')
        raise PayloadError('The acquired .rvt is neither a native RVT container nor a ZIP composite download.')
    except PayloadError: raise
    except Exception as exc:
        raise PayloadError('Could not inspect the acquired model container: ' + f.text(exc))


def _member_name(name):
    normalized = name.replace('\\', '/')
    pieces = normalized.rstrip('/').split('/')
    if (not normalized or normalized.startswith('/') or ntpath.splitdrive(normalized)[0] or
            any(p in ('', '.', '..') or ':' in p or '\x00' in p or p.rstrip(' .') != p for p in pieces)):
        raise PayloadError('Unsafe archive member path; extraction refused.')
    for part in pieces:
        if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part, re.I):
            raise PayloadError('Reserved Windows filename in archive.')
    return '/'.join(pieces)


def prepare(source, scratch, expected_name=None, cancelled=None):
    """Return a validated native file plus a scoped list of exact archive members."""
    f.check(cancelled)
    info = probe(source)
    if info['container'] == 'CFB':
        return dict(path=source, info=info, members=[], host_member=None)
    if os.path.exists(scratch):
        raise PayloadError('Archive extraction requires a fresh private directory.')
    expected = (expected_name or ntpath.basename(source)).lower()
    try:
        with zipfile.ZipFile(source, 'r') as archive:
            entries = archive.infolist()
            if len(entries) > MAX_MEMBERS: raise PayloadError('Composite download has too many members.')
            names, files, total = set(), [], 0
            for entry in entries:
                f.check(cancelled)
                name = _member_name(entry.filename)
                key = name.lower()
                if key in names: raise PayloadError('Case-colliding archive members; extraction refused.')
                names.add(key)
                mode = (entry.external_attr >> 16) & 0xffff
                if stat.S_ISLNK(mode) or entry.flag_bits & 1:
                    raise PayloadError('Symlink or encrypted archive member; extraction refused.')
                if entry.filename.endswith(('/', '\\')): continue
                total += entry.file_size
                if total > MAX_ARCHIVE_BYTES: raise PayloadError('Composite download exceeds extraction limit.')
                files.append((entry, name))
            hosts = [name for entry, name in files if name.rsplit('/', 1)[-1].lower() == expected]
            if len(hosts) != 1:
                raise PayloadError('Composite host is missing or ambiguous. Exactly one member must match the selected model filename.')
            # Reject file/directory conflicts up front, before any extraction.
            file_names = set(name.lower() for _, name in files)
            for _, name in files:
                parts = name.split('/')
                if any('/'.join(parts[:i]).lower() in file_names for i in range(1, len(parts))):
                    raise PayloadError('Conflicting file/directory archive paths.')
            os.makedirs(scratch)
            members = []
            for entry, name in files:
                f.check(cancelled)
                local = os.path.join(scratch, *name.split('/'))
                parent = os.path.dirname(local)
                if not os.path.isdir(parent): os.makedirs(parent)
                size = 0
                with archive.open(entry) as inp, open(local, 'wb') as out:
                    while True:
                        f.check(cancelled); chunk = inp.read(1024 * 1024)
                        if not chunk: break
                        out.write(chunk); size += len(chunk)
                        if size > entry.file_size: raise PayloadError('Archive entry exceeds declared size.')
                if size != entry.file_size: raise PayloadError('Incomplete archive member.')
                members.append(dict(member=name, path=local))
            host = next(item for item in members if item['member'] == hosts[0])
            native = probe(host['path'])
            if native['container'] != 'CFB': raise PayloadError('Composite host is not a native RVT.')
            return dict(path=host['path'], info=native, members=members, host_member=hosts[0])
    except f.Cancelled:
        if os.path.exists(scratch): f.remove_tree_retry(scratch)
        raise
    except Exception as exc:
        if os.path.exists(scratch): f.remove_tree_retry(scratch)
        if isinstance(exc, PayloadError): raise
        raise PayloadError('Composite download extraction/CRC verification failed: ' + f.text(exc))


class Store(object):
    """Keep exact member provenance scoped to its originating composite model."""
    def __init__(self, root):
        self.root = root
        if not os.path.isdir(root): os.makedirs(root)
        self.contexts = {}
        self.physical = {}
        self.stages = {}

    def resolve(self, source, owner=''):
        """Only map known extracted paths/relative members; never basename search."""
        exact=self.physical.get(f.canonical(source))
        if exact: return exact
        context=self.contexts.get(f.canonical(owner),{})
        stage=self.stages.get(f.canonical(owner))
        if stage and f.absolute(source):
            pm=ntpath if f.is_windows(source) else os.path
            parent=pm.dirname(stage)
            key=f.canonical(source).replace('\\','/')
            prefix=f.canonical(parent).replace('\\','/').rstrip('/')+'/'
            if key.startswith(prefix):
                rel=pm.relpath(source,parent)
                logical=pm.normpath(pm.join(pm.dirname(owner),rel))
                # Composite downloads have exact member provenance.
                if context and f.canonical(logical) in context:
                    return logical
                # Host-only cloud siblings are NOT inferred. Resolve through
                # live identities or an explicit version-specific APS graph.
        return source

    def bind_stage(self, stage, owner):
        self.stages[f.canonical(owner)]=stage

    def copy(self, source, target, owner='', cancelled=None, pulse=None):
        f.check(cancelled)
        owner_context = self.contexts.get(f.canonical(owner), {})
        member = owner_context.get(f.canonical(source))
        if member:
            model_info=probe(member['path']) if target.lower().endswith('.rvt') else None
            meta = f.copy_file(member['path'], target, cancelled, pulse)
            if model_info: meta.update(model_info=model_info)
            meta.update(acquisition_container='ZIP', archive_member=member['member'],
                        source_stability='ARCHIVE_SNAPSHOT', archive_source=member['archive_source'])
            self.contexts[f.canonical(source)] = owner_context
            return meta
        if not source.lower().endswith('.rvt'):
            return f.copy_file(source, target, cancelled, pulse)
        token = uuid.uuid4().hex[:12]
        acquired = os.path.join(self.root, token + '.rvt')
        meta = f.copy_file(source, acquired, cancelled, pulse)
        package = prepare(acquired, os.path.join(self.root, token),
                          expected_name=ntpath.basename(source), cancelled=cancelled)
        native_meta = f.copy_file(package['path'], target, cancelled, pulse)
        meta.update(model_info=package['info'], acquisition_sha256=meta['sha256'],
                    acquisition_size=meta['size'], acquisition_container='ZIP' if package['host_member'] else 'CFB')
        if package['host_member']:
            meta.update(sha256=native_meta['sha256'], size=native_meta['size'],
                        source_stability='ARCHIVE_SNAPSHOT', archive_member=package['host_member'])
            pm = ntpath if f.is_windows(source) else os.path
            host_dir = ntpath.dirname(package['host_member'].replace('/', '\\'))
            context = {}
            for item in package['members']:
                tail = ntpath.relpath(item['member'].replace('/', '\\'), host_dir or '.')
                logical = pm.normpath(pm.join(pm.dirname(source), *tail.split('\\')))
                record = dict(item, archive_source=source)
                context[f.canonical(logical)] = record
                self.physical[f.canonical(item['path'])] = logical
            self.contexts[f.canonical(source)] = context
        return meta
