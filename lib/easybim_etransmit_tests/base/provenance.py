# -*- coding: utf-8 -*-
"""Identify the loaded e-transmit installation without running Git or Revit."""
from __future__ import unicode_literals
import io
import os
import re

from . import VERSION


_COMMIT = re.compile(r'^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$')


def _read(path, limit=4096):
    try:
        with io.open(path, 'r', encoding='utf-8') as stream:
            value = stream.read(limit + 1)
        return value if len(value) <= limit else ''
    except (IOError, OSError, UnicodeError):
        return ''


def _commit(value):
    value = (value or '').strip()
    return value.lower() if _COMMIT.match(value) else ''


def _git_commit(root):
    """Read only local HEAD/ref metadata; ZIP installs simply have no hash."""
    git_dir = os.path.join(root, '.git')
    if os.path.isfile(git_dir):
        pointer = _read(git_dir).strip()
        if not pointer.startswith('gitdir:'):
            return ''
        location = pointer[len('gitdir:'):].strip()
        if not location:
            return ''
        git_dir = os.path.normpath(os.path.join(root, location))
    if not os.path.isdir(git_dir):
        return ''

    head = _read(os.path.join(git_dir, 'HEAD')).strip()
    direct = _commit(head)
    if direct:
        return direct
    if not head.startswith('ref:'):
        return ''
    ref = head[len('ref:'):].strip()
    parts = ref.split('/')
    if (not ref.startswith('refs/') or any(part in ('', '.', '..') for part in parts)
            or '\\' in ref or re.search(r'[\x00-\x20~^:?*\[]', ref)):
        return ''

    roots = [git_dir]
    common = _read(os.path.join(git_dir, 'commondir')).strip()
    if common:
        common_dir = os.path.normpath(os.path.join(git_dir, common))
        if common_dir not in roots:
            roots.append(common_dir)
    for metadata_root in roots:
        value = _commit(_read(os.path.join(metadata_root, *parts)))
        if value:
            return value
        packed = _read(os.path.join(metadata_root, 'packed-refs'), 2 * 1024 * 1024)
        for line in packed.splitlines():
            if line.startswith(('#', '^')):
                continue
            fields = line.split()
            if len(fields) == 2 and fields[1] == ref:
                return _commit(fields[0])
    return ''


def collect(button_path=None, module_path=None):
    """Return JSON-safe paths/version/hash for the code loaded in this process."""
    loaded_path = os.path.abspath(module_path or __file__)
    root = os.path.dirname(os.path.dirname(os.path.dirname(loaded_path)))
    button = button_path or os.path.join(
        root, 'EasyBIM.tab', 'Links.panel', 'e-transmit.pushbutton', 'script.py')
    return dict(version=VERSION, installation_root=root, module_path=loaded_path,
                button_path=os.path.abspath(button), git_commit=_git_commit(root))
