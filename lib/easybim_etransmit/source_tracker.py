# -*- coding: utf-8 -*-
"""Remember the exact model source Revit was asked to open.

Detached documents intentionally expose an empty Document.PathName.  The
DocumentOpening event still exposes the requested path, so capture it there and
bind it to the opened document.  The current Revit journal is a read-only
fallback for models that were already open before EasyBIM was updated/reloaded.
"""
from __future__ import unicode_literals
import io
import json
import ntpath
import os
import re
import tempfile
import time

try:
    text = unicode
except NameError:
    text = str


def _state_path():
    base = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
    return os.path.join(base, 'EasyBIM', 'e-transmit', 'open_sources.json')


def _load(path):
    try:
        with io.open(path, 'r', encoding='utf-8') as inp:
            value = json.load(inp)
        if isinstance(value, dict):
            value.setdefault('pending', [])
            value.setdefault('documents', {})
            return value
    except (IOError, OSError, ValueError):
        pass
    return {'pending': [], 'documents': {}}


def _save(path, state):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    temp = path + '.tmp'
    with io.open(temp, 'w', encoding='utf-8') as out:
        out.write(text(json.dumps(state, ensure_ascii=False, indent=2)))
    if os.path.exists(path):
        os.remove(path)
    os.rename(temp, path)


def _leaf(value):
    value = text(value or '').strip().replace('\\', '/').rstrip('/')
    leaf = value.rsplit('/', 1)[-1]
    leaf = re.sub(r'^\{[0-9a-fA-F-]+\}', '', leaf)
    return leaf


def normalize_title(value):
    name = _leaf(value)
    if name.lower().endswith('.rvt'):
        name = name[:-4]
    # Revit appends "_detached" to an unsaved detached document title.
    name = re.sub(r'(?i)(?:[_\- ]detached)$', '', name)
    return name.strip().lower()


def document_key(doc):
    return normalize_title(getattr(doc, 'Title', ''))


def record_opening(path, state_path=None, now=None):
    path = text(path or '').strip()
    if not path:
        return
    state_path = state_path or _state_path()
    state = _load(state_path)
    state['pending'].append({'path': path, 'time': float(now if now is not None else time.time())})
    state['pending'] = state['pending'][-32:]
    _save(state_path, state)


def _best_pending(state, title):
    key = normalize_title(title)
    for index in range(len(state.get('pending', [])) - 1, -1, -1):
        item = state['pending'][index]
        if normalize_title(item.get('path', '')) == key:
            return index, item.get('path', '')
    return None, ''


def record_opened(doc, state_path=None, now=None):
    state_path = state_path or _state_path()
    state = _load(state_path)
    path = text(getattr(doc, 'PathName', '') or '').strip()
    index = None
    if not path:
        index, path = _best_pending(state, getattr(doc, 'Title', ''))
    if path:
        state['documents'][document_key(doc)] = {
            'path': path,
            'time': float(now if now is not None else time.time())
        }
        if index is not None:
            del state['pending'][index]
        _save(state_path, state)
    return path


def _journal_unescape(value):
    # Journal quoted strings may double backslashes; normalize only that escape.
    value = text(value or '').strip()
    if '\\\\' in value:
        value = value.replace('\\\\', '\\')
    return value


def _journal_candidates(journal_path):
    if not journal_path or not os.path.isfile(journal_path):
        return []
    try:
        size = os.path.getsize(journal_path)
        with open(journal_path, 'rb') as inp:
            if size > 8 * 1024 * 1024:
                inp.seek(size - 8 * 1024 * 1024)
            raw = inp.read()
        try:
            content = raw.decode('utf-8', 'replace')
        except AttributeError:
            content = raw
    except (IOError, OSError):
        return []

    hits = []
    # Ordinary File > Open, including local files opened Detached.
    for match in re.finditer(r'Jrn\.Data\s+"File Name"[\s\S]{0,700}?"IDOK"\s*,\s*"([^"]+)"',
                             content, re.I):
        hits.append((match.start(), _journal_unescape(match.group(1))))
    # Workshared local open records are more direct in some journal versions.
    for match in re.finditer(r'>Open:Local\s+"([^"]+)"', content, re.I):
        hits.append((match.start(), _journal_unescape(match.group(1))))
    # Cloud journals commonly contain central/local pairs.  The local value is
    # an exact cache file for that cloud identity; retain both as metadata.
    for match in re.finditer(r'central="([^"]+)"\s+local="([^"]+)"', content, re.I):
        hits.append((match.start(), _journal_unescape(match.group(1))))
    hits.sort(key=lambda pair: pair[0])
    return [value for _, value in hits]


def journal_source_for_document(doc, application=None):
    journal = text(getattr(application, 'RecordingJournalFilename', '') or '') if application else ''
    title = normalize_title(getattr(doc, 'Title', ''))
    candidates = _journal_candidates(journal)
    for candidate in reversed(candidates):
        if normalize_title(candidate) == title:
            return candidate
    return ''


def source_for_document(doc, application=None, state_path=None):
    """Return the exact opened source when Revit exposes or recorded it."""
    direct = text(getattr(doc, 'PathName', '') or '').strip()
    if direct:
        return direct
    state = _load(state_path or _state_path())
    stored = state.get('documents', {}).get(document_key(doc), {})
    if stored.get('path'):
        return text(stored['path'])
    journal = journal_source_for_document(doc, application)
    if journal:
        return journal
    return ''
