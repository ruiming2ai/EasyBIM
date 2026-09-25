# -*- coding: utf-8 -*-
"""One deterministic delivery location per source; no filesystem mutations."""
from __future__ import unicode_literals
import hashlib
import ntpath
import re
from . import files as f

MODES = [('categories', 'By category'), ('original', 'Retain original folder structure'),
         ('flat', 'All files together')]
FOLDERS = dict(revit='Revit', cad='CAD', ifc='IFC', pdf='PDF', images='Images',
               decals='Images', pointcloud='Point Cloud', navisworks='Navisworks',
               dwf='DWF', keynotes='Keynotes', analysis='Analysis',
               spreadsheets='Spreadsheets', other='Other')


def mode(value):
    return value if isinstance(value, f.string_types) and value in dict(MODES) else 'categories'


def key(path):
    return ntpath.normpath(f.text(path)).replace('\\', '/').lower()


def basename(path):
    return f.text(path).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


def label(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', basename(value)).strip().rstrip('. ')
    value = value[:32] or 'Source'
    if value.split('.')[0].upper() in ('CON', 'PRN', 'AUX', 'NUL', 'CLOCK$') or re.match(r'^(COM|LPT)[1-9](\.|$)', value, re.I):
        value = '_' + value
    return value


def suffix(identity):
    return hashlib.sha256(f.text(identity).encode('utf-8')).hexdigest()[:12]


def original(relative):
    parts = f.clean_parts(relative)
    if parts and parts[0] in ('Sources', 'Links'): parts.pop(0)
    return '/'.join(['Links'] + parts)


def plan(records, selected, bundles=None):
    """Allocate whole directory/RCP units before checking file/directory collisions.

    An explicitly added outer directory wins over its nested directories/RCPs.
    This retains required internal paths without duplicating individual files.
    """
    selected = mode(selected)
    bundles = sorted(bundles or [], key=lambda b: (len(key(b['root'])), key(b['root']), b.get('entry', '')))
    units = {}
    for rec in records:
        source = rec['source']; name = basename(rec.get('original_relative') or source)
        unit_id = rec.get('layout_identity') or f.canonical(source)
        member = name; group = None
        if not rec.get('is_primary_host'):
            for bundle in bundles:
                root = key(bundle['root']).rstrip('/')
                inside = key(source).startswith(root + '/')
                if bundle.get('entry'):
                    inside = key(source) == key(bundle['entry']) or key(source).startswith(key(bundle['support']).rstrip('/') + '/')
                if inside:
                    group = bundle; break
        if group:
            unit_id = 'folder:' + f.canonical(group.get('entry') or group['root'])
            member = f.text(source).replace('\\', '/')[len(f.text(group['root']).replace('\\', '/').rstrip('/')) + 1:]
        if rec.get('is_primary_host'):
            base = ''; member = name
        elif selected == 'original':
            full = original(rec.get('original_relative') or f.mirror_path(source))
            base = full[:-len(member)].rstrip('/')
        else:
            category = group.get('category', 'other') if group else rec.get('category', 'other')
            base = 'Links' + ('/' + FOLDERS.get(category, 'Other') if selected == 'categories' else '')
            if group and not group.get('entry'): base += '/' + basename(group['root'])
        unit = units.setdefault(unit_id, dict(base=base, members=[], identity=unit_id,
                                             parent=(group['root'] if group else ntpath.dirname(source)), split=False))
        unit['members'].append((rec, member))
    # Reserve report names and treat file-vs-directory collisions like same names.
    reserved = ['manifest.json', 'START_HERE.txt', 'REPORT.txt', 'files.csv',
                'references.csv', 'issues.csv', 'DIAGNOSTICS.txt', 'Links']
    for attempt in range(3):
        occupied = []; conflicts = set()
        for ident, unit in sorted(units.items()):
            base = unit['base']
            if unit['split']:
                base += ('/' if base else '') + label(unit['parent']) + '-' + suffix(ident)
            for rec, member in unit['members']:
                path = '/'.join(p for p in (base, member) if p)
                if key(path) in [key(p) for p in reserved]: conflicts.add(ident)
                occupied.append((key(path), ident))
                rec['relative'] = path
                rec['collision_separated'] = unit['split']
        occupied.sort()
        # Sorted neighbours reveal duplicate file names and file/parent conflicts.
        for (left, owner), (right, ident) in zip(occupied, occupied[1:]):
            if left == right or right.startswith(left + '/'):
                conflicts.update((owner, ident))
        if not conflicts: return records
        for ident in conflicts: units[ident]['split'] = True
    raise ValueError('Unable to allocate unique package paths without renaming files.')
