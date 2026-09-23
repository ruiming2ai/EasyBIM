# -*- coding: utf-8 -*-
"""Portable file operations. Windows source names are never renamed."""
from __future__ import unicode_literals
import hashlib
import io
import ntpath
import os
import re
import tempfile
import uuid

try:
    text = unicode
    string_types = (basestring,)
except NameError:
    text = str
    string_types = (str,)

CATEGORIES = [('revit', 'Linked Revit models'), ('ifc', 'IFC / coordination sources'),
              ('cad', 'CAD links'), ('pdf', 'Linked PDFs'), ('images', 'Linked images'),
              ('pointcloud', 'Point clouds + Support folders'),
              ('navisworks', 'Navisworks files'), ('dwf', 'DWF markups'),
              ('keynotes', 'Keynotes / assembly codes'), ('decals', 'Decal image files'),
              ('analysis', 'Systems analysis reports'), ('other', 'Other external files')]


class Cancelled(Exception):
    pass


def defaults():
    return dict(include=dict((k, True) for k, label in CATEGORIES), deep=True,
                repath=True, upgrade=False, cleanup=False, discard_worksets=False,
                purge=False, views='all', view_types=[], per_model=True,
                reports=True, zip=False, mappings=[])


def category(path, kind=''):
    k = text(kind).lower()
    if 'decal' in k: return 'decals'
    if 'analysis' in k: return 'analysis'
    if 'keynote' in k or 'assemblycode' in k: return 'keynotes'
    ext = ntpath.splitext(path)[1].lower()
    for name, extensions in [('revit', '.rvt'), ('ifc', '.ifc .ifczip'),
                             ('cad', '.dwg .dxf .dgn .sat .3dm .skp'),
                             ('pdf', '.pdf'), ('images', '.png .jpg .jpeg .bmp .tif .tiff .gif'),
                             ('pointcloud', '.rcp .rcs .pcg .rcc .e57 .las .laz .pts .ptx'),
                             ('navisworks', '.nwd .nwc .nwf'), ('dwf', '.dwf .dwfx')]:
        if ext and ext in extensions.split(): return name
    return 'other'


def is_windows(path):
    return bool(ntpath.splitdrive(path)[0]) or '\\' in path


def absolute(path):
    if not path or '://' in path: return False
    if path.startswith(('\\\\.\\', '\\\\?\\')): return False
    if is_windows(path):
        drive, tail = ntpath.splitdrive(path)
        return bool(drive and tail.startswith(('\\', '/')))
    return os.path.isabs(path)


def canonical(path):
    path = text(path)
    return ntpath.normcase(ntpath.normpath(path)) if is_windows(path) else os.path.normpath(path)


def within(path, root):
    p, r = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def clean_parts(value):
    parts = value.replace('\\', '/').split('/')
    if any(p in ('.', '..') for p in parts):
        raise ValueError('Parent/dot traversal is not a valid mapped source: ' + value)
    return [p for p in parts if p]


def cache_source(path):
    # Internal collaboration caches are not independent, supported source models.
    parts=text(path or '').replace('\\','/').lower().split('/')
    return any(p in ('collaborationcache','paccache') for p in parts)


def resolve_source(source, owner='', mappings=None):
    """Map only an exact prefix, never a filename or a newer cloud model."""
    source = text(source or '').strip()
    if cache_source(source): return None
    normalized = source.replace('\\', '/').rstrip('/')
    candidates = []
    for prefix, folder in mappings or []:
        pre = text(prefix).replace('\\', '/').rstrip('/')
        if not pre: continue
        if normalized.lower() == pre.lower() or normalized.lower().startswith(pre.lower() + '/'):
            tail = normalized[len(pre):].lstrip('/')
            parts = clean_parts(tail)
            pm = ntpath if is_windows(folder) else os.path
            candidate = pm.normpath(pm.join(folder, *parts)) if parts else folder
            if not absolute(candidate): raise ValueError('Mapping destination must be absolute.')
            candidates.append((len(pre), candidate))
    if candidates:
        best = max(n for n, p in candidates)
        paths = set(canonical(p) for n, p in candidates if n == best)
        if len(paths) != 1: raise ValueError('Ambiguous source-prefix mappings: ' + source)
        selected=next(p for n, p in candidates if n == best)
        return None if cache_source(selected) else selected
    if absolute(source):
        pm = ntpath if is_windows(source) else os.path
        return pm.normpath(source)
    if '://' in source or ntpath.splitdrive(source)[0] or source.startswith('\\'):
        return None
    if source and absolute(owner):
        pm = ntpath if is_windows(owner) else os.path
        selected=pm.normpath(pm.join(pm.dirname(owner), source))
        return None if cache_source(selected) else selected
    return None


def mirror_path(source):
    if not absolute(source): raise ValueError('Not an absolute filesystem source: ' + source)
    normalized = ntpath.normpath(source) if is_windows(source) else os.path.normpath(source)
    slash = normalized.replace('\\', '/')
    marker = '/dc/accdocs/'
    i = slash.lower().find(marker)
    if i >= 0:
        parts = ['Sources', 'ACC'] + clean_parts(slash[i+len(marker):])
    elif slash.startswith('//'):
        parts = ['Sources', 'Network'] + clean_parts(slash[2:])
    elif ntpath.splitdrive(normalized)[0]:
        drive, tail = ntpath.splitdrive(normalized)
        parts = ['Sources', 'Drive_' + drive.rstrip(':')] + clean_parts(tail)
    else:
        parts = ['Sources', 'Local'] + clean_parts(slash)
    return '/'.join(parts)


class PathLengthError(ValueError):
    """A package path cannot safely be passed to legacy Windows/Revit APIs."""


def path_units(path):
    # Windows budgets are UTF-16 code units, not Unicode code points.
    return len(text(path).encode('utf-16-le')) // 2


def validate_destination_path(path):
    """Keep file IO, short sibling temporaries and RVT backup names below MAX_PATH.

    This deliberately does not enable registry settings or pass extended paths
    to Revit. Extended-path filesystem support does not prove Revit portability.
    """
    if os.name != 'nt' and not is_windows(path): return path
    value = ntpath.normpath(path)
    directory = ntpath.dirname(value)
    # MAX_PATH includes the terminator. RVT SaveAs can append '.0001'.
    file_limit = 254 if value.lower().endswith('.rvt') else 259
    # mkstemp('.et-', '.tmp') uses 16 characters including its random component.
    directory_limit = min(247, 259 - 1 - 16)
    length, folder_length = path_units(value), path_units(directory)
    if length > file_limit or folder_length > directory_limit:
        raise PathLengthError(
            'Destination path is too long: {0} characters (file budget {1}); '
            'folder {2} characters (budget {3}, including temporary-file space).\n'
            'Destination: {4}\n'
            'Choose a shorter output folder, for example C:\\ET. '
            'Original filenames and source subfolders will not be shortened.'.format(
                length, file_limit, folder_length, directory_limit, value))
    return path


def new_run_root(output, stamp):
    """Short tool-generated wrapper, never a shortened source filename."""
    pm = ntpath if is_windows(output) else os.path
    name = 'ET_' + stamp
    root = pm.join(output, name)
    n = 2
    while os.path.exists(root):
        root = pm.join(output, name + '_' + text(n)); n += 1
    return root


def package_root(root, index, separate=True):
    # The complete model basename already occurs inside Sources. Repeating it
    # as an outer folder caused otherwise-valid Desktop Connector paths to fail.
    pm = ntpath if is_windows(root) else os.path
    return pm.join(root, '{0:02d}'.format(index + 1)) if separate else root


def temporary_path(folder, suffix='.rvt'):
    """Short internal name; copy_file publishes it without overwriting a race."""
    for attempt in range(128):
        value = os.path.join(folder, '.et-' + uuid.uuid4().hex[:8] + suffix)
        if not os.path.exists(value):
            validate_destination_path(value)
            return value
    raise IOError('Unable to allocate an unused temporary filename.')


def destination(root, relative):
    if absolute(relative) or relative.startswith(('/', '\\')):
        raise ValueError('Package entry must be relative.')
    parts = clean_parts(relative)
    target = os.path.join(root, *parts)
    if not within(target, root): raise ValueError('Destination escapes package root.')
    validate_destination_path(target)
    return target


def check(cancelled=None):
    if cancelled and cancelled(): raise Cancelled('Cancelled by user.')


def signature(path):
    s = os.stat(path)
    return (s.st_size, s.st_mtime)


def digest(path, cancelled=None):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            check(cancelled)
            b = f.read(1024 * 1024)
            if not b: break
            h.update(b)
    return h.hexdigest()


def publish(temp, target):
    """Publish without overwriting an existing file, including a racing writer."""
    if os.name == 'nt':
        os.rename(temp, target)  # Windows rename does not replace an existing path.
    else:
        os.link(temp, target)
        os.remove(temp)


def copy_file(source, target, cancelled=None, pulse=None):
    check(cancelled)
    validate_destination_path(target)
    if os.path.exists(target): raise IOError('Refusing to overwrite: ' + target)
    if cache_source(source): raise IOError('CollaborationCache/PacCache copies are not supported.')
    if not os.path.isfile(source): raise IOError('Source missing or not a file: ' + source)
    if os.path.islink(source): raise IOError('Symlink sources require an explicit real file path: ' + source)
    folder = os.path.dirname(target)
    if not os.path.isdir(folder): os.makedirs(folder)
    temp = None
    h, size = hashlib.sha256(), 0
    try:
        with open(source, 'rb') as inp:
            # A first read may hydrate a Desktop Connector placeholder and set
            # its metadata. Establish the snapshot AFTER that read, then rewind.
            # No refresh, cloud lookup or substitution of a different source.
            inp.read(1)
            inp.seek(0)
            before = signature(source)
            check(cancelled)
            # Do not append a long suffix to the ORIGINAL filename. A source
            # basename can already use nearly the whole Windows path budget.
            fd, temp = tempfile.mkstemp(prefix='.et-', suffix='.tmp', dir=folder)
            with os.fdopen(fd, 'wb') as out:
                while True:
                    check(cancelled)
                    block = inp.read(1024 * 1024)
                    if not block: break
                    out.write(block); h.update(block); size += len(block)
                    if pulse: pulse(source, size, before[0])
                out.flush()
        if signature(source) != before or size != before[0]:
            raise IOError('Source changed during collection; rerun after saving/sync completes: ' + source)
        if digest(temp, cancelled) != h.hexdigest(): raise IOError('Copy checksum mismatch: ' + source)
        check(cancelled)
        publish(temp, target)
        return {'sha256': h.hexdigest(), 'size': size, 'source_mtime': before[1]}
    finally:
        if temp and os.path.exists(temp): os.remove(temp)


def csv_cell(value):
    s = text(value if value is not None else '')
    if s.lstrip().startswith(('=', '+', '-', '@')) or s.startswith(('\t', '\r', '\n')):
        s = "'" + s
    return '"' + s.replace('"', '""') + '"'


def write_csv(path, rows, columns):
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write(','.join(csv_cell(x) for x in columns) + '\r\n')
        for row in rows:
            f.write(','.join(csv_cell(row.get(x, '')) for x in columns) + '\r\n')
