# -*- coding: utf-8 -*-
"""Portable file operations. Windows source names are never renamed."""
from __future__ import unicode_literals
import base64
import hashlib
import io
import ntpath
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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


def default_connector_roots():
    """Known Desktop Connector workspace roots; exact hierarchy matching only."""
    roots = []
    configured = os.environ.get('EASYBIM_DESKTOP_CONNECTOR_ROOTS', '')
    for value in configured.split(os.pathsep):
        value = text(value).strip()
        if value and value not in roots:
            roots.append(value)
    profile = os.environ.get('USERPROFILE') or os.path.expanduser('~')
    for value in (os.path.join(profile, 'DC', 'ACCDocs'),
                  os.path.join(profile, 'ACCDocs')):
        if value not in roots:
            roots.append(value)
    return roots


def _connector_uri_parts(source):
    value = text(source or '').strip()
    match = re.match(r'^(Autodesk Docs|Autodesk Forma|Autodesk Construction Cloud|BIM 360|ACC)://(.+)    """Map only an exact prefix, never a filename or a newer cloud model."""
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
    connector = resolve_connector_uri(source)
    if connector:
        return connector
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


class ShellCopyError(IOError):
    """Windows Shell could not materialize/copy a Desktop Connector source."""


def is_desktop_connector_path(path):
    """Recognize current and legacy Desktop Connector workspace path shapes.

    Desktop Connector is a Windows Shell namespace.  Autodesk explicitly
    documents direct non-Shell access as unsupported, so these paths are
    materialized through Shell.Application before Python reads their bytes.
    """
    parts = [p.lower() for p in text(path or '').replace('\\', '/').split('/') if p]
    return ('accdocs' in parts or 'autodesk docs' in parts or 'bim 360' in parts)


def _ps_quote(value):
    return "'" + text(value).replace("'", "''") + "'"


def shell_copy_backend():
    """Prefer in-process COM inside pyRevit; CPython CI falls back to PowerShell."""
    try:
        import System
        if getattr(System, 'Type', None) is not None and getattr(System, 'Activator', None) is not None:
            return 'INPROCESS_COM'
    except Exception:
        pass
    return 'POWERSHELL'


def _wait_shell_copy(local, expected, cancelled=None):
    deadline = time.time() + (20 * 60)
    stable, last = 0, -1
    while time.time() < deadline:
        check(cancelled)
        try:
            if os.path.isfile(local):
                length = os.path.getsize(local)
                if ((expected and length == expected) or
                        (not expected and length == last and length > 0)):
                    stable += 1
                else:
                    stable = 0
                last = length
                if stable >= 4:
                    return local
        except OSError:
            stable = 0
        time.sleep(0.25)
    raise ShellCopyError('Windows Shell copy timed out before the local snapshot became stable.')


def _release_com(value):
    if value is None:
        return
    try:
        import System
        marshal = System.Runtime.InteropServices.Marshal
        if marshal.IsComObject(value):
            marshal.FinalReleaseComObject(value)
    except Exception:
        pass


def _shell_copy_inprocess(source, cancelled=None):
    """Use Shell.Application directly in the pyRevit process; no child process."""
    if os.name != 'nt':
        raise ShellCopyError('Windows Shell copy is available only on Windows.')
    check(cancelled)
    folder = tempfile.mkdtemp(prefix='EasyBIM_ET_DC_')
    name = ntpath.basename(source)
    local = os.path.join(folder, name)
    shell = src_ns = item = dst_ns = None
    try:
        import System
        prog = System.Type.GetTypeFromProgID('Shell.Application')
        if prog is None:
            raise ShellCopyError('Shell.Application COM registration was not found.')
        shell = System.Activator.CreateInstance(prog)
        src_dir = ntpath.dirname(source)
        src_ns = shell.NameSpace(src_dir)
        if src_ns is None:
            raise ShellCopyError('Windows Shell could not open the Desktop Connector source folder.')
        item = src_ns.ParseName(name)
        if item is None:
            raise ShellCopyError('Windows Shell could not resolve the selected Desktop Connector file.')
        dst_ns = shell.NameSpace(folder)
        if dst_ns is None:
            raise ShellCopyError('Windows Shell could not open the local staging folder.')
        try:
            expected = int(item.Size)
        except Exception:
            expected = 0
        # FOF_SILENT | NOCONFIRMATION | NOCONFIRMMKDIR | NOERRORUI | NO_UI.
        dst_ns.CopyHere(item, 9748)
        _wait_shell_copy(local, expected, cancelled)
        return local, folder
    except Exception:
        try: remove_tree_retry(folder)
        except Exception: pass
        raise
    finally:
        _release_com(dst_ns)
        _release_com(item)
        _release_com(src_ns)
        _release_com(shell)


def _shell_copy_powershell(source, cancelled=None):
    """CPython fallback for verification environments without .NET COM interop."""
    if os.name != 'nt':
        raise ShellCopyError('Windows Shell copy is available only on Windows.')
    check(cancelled)
    folder = tempfile.mkdtemp(prefix='EasyBIM_ET_DC_')
    name = ntpath.basename(source)
    local = os.path.join(folder, name)
    script = r"""
$ErrorActionPreference = 'Stop'
$src = %s
$dst = %s
$name = [System.IO.Path]::GetFileName($src)
$srcDir = [System.IO.Path]::GetDirectoryName($src)
$shell = New-Object -ComObject Shell.Application
$srcNs = $shell.NameSpace($srcDir)
if ($null -eq $srcNs) { throw 'Windows Shell could not open the Desktop Connector source folder.' }
$item = $srcNs.ParseName($name)
if ($null -eq $item) { throw 'Windows Shell could not resolve the selected Desktop Connector file.' }
$dstNs = $shell.NameSpace($dst)
if ($null -eq $dstNs) { throw 'Windows Shell could not open the local staging folder.' }
$expected = 0
try { $expected = [int64]$item.Size } catch { $expected = 0 }
$dstNs.CopyHere($item, 9748)
$out = Join-Path $dst $name
$deadline = (Get-Date).AddMinutes(20)
$stable = 0
$last = -1
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
    try {
        if (Test-Path -LiteralPath $out -PathType Leaf) {
            $length = [int64](Get-Item -LiteralPath $out).Length
            if (($expected -gt 0 -and $length -eq $expected) -or
                ($expected -le 0 -and $length -eq $last -and $length -gt 0)) {
                $stable++
            } else { $stable = 0 }
            $last = $length
            if ($stable -ge 4) { exit 0 }
        }
    } catch { $stable = 0 }
}
throw 'Windows Shell copy timed out before the local snapshot became stable.'
""" % (_ps_quote(source), _ps_quote(folder))
    encoded = base64.b64encode(script.encode('utf-16-le'))
    if not isinstance(encoded, str):
        encoded = encoded.decode('ascii')
    powershell = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                              'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    proc = None
    try:
        proc = subprocess.Popen([powershell, '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        while proc.poll() is None:
            try:
                check(cancelled)
            except Cancelled:
                try: proc.kill()
                except Exception: pass
                raise
            time.sleep(0.2)
        out, err = proc.communicate()
        if proc.returncode != 0 or not os.path.isfile(local):
            def readable(value):
                try: return value.decode('utf-8', 'replace')
                except AttributeError: return text(value or '')
            detail = readable(err).strip() or readable(out).strip()
            if not detail:
                detail = 'Windows Shell subprocess exited with code {0}.'.format(proc.returncode)
            raise ShellCopyError(detail)
        return local, folder
    except Exception:
        if proc is not None and proc.poll() is None:
            try: proc.kill()
            except Exception: pass
        try: remove_tree_retry(folder)
        except Exception: pass
        raise


def shell_copy_to_local(source, cancelled=None):
    """Materialize a Desktop Connector item via Windows Shell."""
    backend = shell_copy_backend()
    try:
        if backend == 'INPROCESS_COM':
            return _shell_copy_inprocess(source, cancelled)
        return _shell_copy_powershell(source, cancelled)
    except Cancelled:
        raise
    except Exception as exc:
        if isinstance(exc, ShellCopyError):
            detail = text(exc)
        else:
            detail = '{0}: {1}'.format(type(exc).__name__, text(exc))
        raise ShellCopyError(
            'Desktop Connector Windows Shell acquisition failed [{0}] for {1}: {2}'.format(
                backend, source, detail))



def remove_tree_retry(path, attempts=10, delay=0.2):
    """Remove local scratch with short retries for antivirus/provider handles."""
    if not path or not os.path.exists(path): return
    last = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            last = exc
            time.sleep(delay * (attempt + 1))
    if os.path.exists(path):
        raise last


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


def _copy_file_direct(source, target, cancelled=None, pulse=None, display_source=None):
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
    label = display_source or source
    try:
        with open(source, 'rb') as inp:
            inp.read(1)
            inp.seek(0)
            before = signature(source)
            check(cancelled)
            fd, temp = tempfile.mkstemp(prefix='.et-', suffix='.tmp', dir=folder)
            with os.fdopen(fd, 'wb') as out:
                while True:
                    check(cancelled)
                    block = inp.read(1024 * 1024)
                    if not block: break
                    out.write(block); h.update(block); size += len(block)
                    if pulse: pulse(label, size, before[0])
                out.flush()
        if signature(source) != before or size != before[0]:
            raise IOError('Source changed during collection; rerun after saving/sync completes: ' + label)
        # Finalize once: IronPython's Windows crypto provider can invalidate
        # the native handle when hexdigest() is called again on this object.
        # Cache the value for BOTH verification and the success metadata.
        expected = h.hexdigest()
        if digest(temp, cancelled) != expected: raise IOError('Copy checksum mismatch: ' + label)
        metadata = {'sha256': expected, 'size': size, 'source_mtime': before[1]}
        check(cancelled)
        publish(temp, target)
        return metadata
    finally:
        if temp and os.path.exists(temp): os.remove(temp)


def copy_file(source, target, cancelled=None, pulse=None):
    """Verified copy, using Windows Shell for Desktop Connector namespaces."""
    shell_folder = None
    shell_local = None
    metadata = None
    cleanup_warning = None
    try:
        if os.name == 'nt' and is_desktop_connector_path(source):
            backend_name = shell_copy_backend()
            shell_local, shell_folder = shell_copy_to_local(source, cancelled)
            metadata = _copy_file_direct(shell_local, target, cancelled, pulse, source)
            metadata['copy_method'] = ('WINDOWS_SHELL_COM' if backend_name == 'INPROCESS_COM'
                                       else 'WINDOWS_SHELL_POWERSHELL')
            metadata['shell_backend'] = backend_name
            metadata['source_stability'] = 'SHELL_SNAPSHOT'
        else:
            metadata = _copy_file_direct(source, target, cancelled, pulse, source)
            metadata['copy_method'] = 'DIRECT'
            metadata['source_stability'] = 'DIRECT'
    finally:
        if shell_folder:
            try: remove_tree_retry(shell_folder)
            except Exception as exc: cleanup_warning = text(exc)
    if metadata is not None and cleanup_warning:
        metadata['staging_cleanup_warning'] = cleanup_warning
    return metadata


def source_snapshot_changed(source, metadata):
    """Shell snapshots are immutable local acquisition points; never restat DC."""
    if metadata.get('source_stability') == 'SHELL_SNAPSHOT':
        return False
    return signature(source) != (metadata.get('size'), metadata.get('source_mtime'))


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
,
                     value, re.I)
    if not match:
        return None
    return clean_parts(match.group(2))


def _shell_folder_names(folder):
    """Enumerate a Desktop Connector folder through Windows Shell."""
    if os.name != 'nt':
        try:
            return sorted(name for name in os.listdir(folder)
                          if os.path.isdir(os.path.join(folder, name)))
        except OSError:
            return []
    shell = namespace = items = None
    names = []
    try:
        import System
        prog = System.Type.GetTypeFromProgID('Shell.Application')
        if prog is None:
            return []
        shell = System.Activator.CreateInstance(prog)
        namespace = shell.NameSpace(folder)
        if namespace is None:
            return []
        items = namespace.Items()
        for item in items:
            try:
                if bool(item.IsFolder):
                    names.append(text(item.Name))
            except Exception:
                pass
        return sorted(names)
    except Exception:
        return []
    finally:
        _release_com(items)
        _release_com(namespace)
        _release_com(shell)


def _shell_file_exists(path):
    if os.name != 'nt':
        return os.path.isfile(path)
    shell = namespace = item = None
    try:
        import System
        prog = System.Type.GetTypeFromProgID('Shell.Application')
        if prog is None:
            return False
        shell = System.Activator.CreateInstance(prog)
        namespace = shell.NameSpace(ntpath.dirname(path))
        if namespace is None:
            return False
        item = namespace.ParseName(ntpath.basename(path))
        return item is not None and not bool(getattr(item, 'IsFolder', False))
    except Exception:
        return False
    finally:
        _release_com(item)
        _release_com(namespace)
        _release_com(shell)


def resolve_connector_uri(source, roots=None):
    """Resolve an Autodesk cloud display path to one exact Connector hierarchy.

    This never searches by basename and never selects a newest/live sibling.
    If two connector accounts contain the same exact hierarchy, the result is
    deliberately ambiguous rather than guessed.
    """
    parts = _connector_uri_parts(source)
    if not parts:
        return None
    candidates = []
    seen = set()
    for root in roots or default_connector_roots():
        if not root:
            continue
        # Some display paths include account, some begin at project.
        trials = [os.path.join(root, *parts)]
        for account in _shell_folder_names(root):
            trials.append(os.path.join(root, account, *parts))
        for candidate in trials:
            key = canonical(candidate)
            if key in seen:
                continue
            seen.add(key)
            if _shell_file_exists(candidate):
                candidates.append(candidate)
    unique = dict((canonical(path), path) for path in candidates)
    if len(unique) > 1:
        raise ValueError('Ambiguous Desktop Connector hierarchy for: ' + text(source))
    return next(iter(unique.values())) if unique else None


def resolve_library_resource(source, roots):
    """Find the exact referenced library filename inside Revit library roots."""
    value = text(source or '').strip()
    if not value or '://' in value:
        return None
    if absolute(value) and os.path.isfile(value):
        return value
    relative = value.replace('\\', '/').lstrip('/')
    wanted = ntpath.basename(relative).lower()
    if not wanted:
        return None
    matches = []
    seen = set()
    for root in list(roots or []):
        root = text(root or '').strip()
        if not root or not os.path.isdir(root):
            continue
        # First honor an explicit relative subpath if one was saved.
        exact = os.path.normpath(os.path.join(root, *clean_parts(relative)))
        if os.path.isfile(exact):
            key = canonical(exact)
            if key not in seen:
                seen.add(key); matches.append(exact)
            continue
        # "At Library Locations" may save only the basename. Search exact
        # basename under configured Revit library roots, never a renamed file.
        for current, dirs, names in os.walk(root):
            dirs[:] = sorted(dirs)
            for name in names:
                if name.lower() == wanted:
                    path = os.path.join(current, name)
                    key = canonical(path)
                    if key not in seen:
                        seen.add(key); matches.append(path)
            if len(matches) > 1:
                break
        if len(matches) > 1:
            break
    if len(matches) > 1:
        raise ValueError('Ambiguous Revit library resource: ' + value)
    return matches[0] if matches else None


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


class ShellCopyError(IOError):
    """Windows Shell could not materialize/copy a Desktop Connector source."""


def is_desktop_connector_path(path):
    """Recognize current and legacy Desktop Connector workspace path shapes.

    Desktop Connector is a Windows Shell namespace.  Autodesk explicitly
    documents direct non-Shell access as unsupported, so these paths are
    materialized through Shell.Application before Python reads their bytes.
    """
    parts = [p.lower() for p in text(path or '').replace('\\', '/').split('/') if p]
    return ('accdocs' in parts or 'autodesk docs' in parts or 'bim 360' in parts)


def _ps_quote(value):
    return "'" + text(value).replace("'", "''") + "'"


def shell_copy_backend():
    """Prefer in-process COM inside pyRevit; CPython CI falls back to PowerShell."""
    try:
        import System
        if getattr(System, 'Type', None) is not None and getattr(System, 'Activator', None) is not None:
            return 'INPROCESS_COM'
    except Exception:
        pass
    return 'POWERSHELL'


def _wait_shell_copy(local, expected, cancelled=None):
    deadline = time.time() + (20 * 60)
    stable, last = 0, -1
    while time.time() < deadline:
        check(cancelled)
        try:
            if os.path.isfile(local):
                length = os.path.getsize(local)
                if ((expected and length == expected) or
                        (not expected and length == last and length > 0)):
                    stable += 1
                else:
                    stable = 0
                last = length
                if stable >= 4:
                    return local
        except OSError:
            stable = 0
        time.sleep(0.25)
    raise ShellCopyError('Windows Shell copy timed out before the local snapshot became stable.')


def _release_com(value):
    if value is None:
        return
    try:
        import System
        marshal = System.Runtime.InteropServices.Marshal
        if marshal.IsComObject(value):
            marshal.FinalReleaseComObject(value)
    except Exception:
        pass


def _shell_copy_inprocess(source, cancelled=None):
    """Use Shell.Application directly in the pyRevit process; no child process."""
    if os.name != 'nt':
        raise ShellCopyError('Windows Shell copy is available only on Windows.')
    check(cancelled)
    folder = tempfile.mkdtemp(prefix='EasyBIM_ET_DC_')
    name = ntpath.basename(source)
    local = os.path.join(folder, name)
    shell = src_ns = item = dst_ns = None
    try:
        import System
        prog = System.Type.GetTypeFromProgID('Shell.Application')
        if prog is None:
            raise ShellCopyError('Shell.Application COM registration was not found.')
        shell = System.Activator.CreateInstance(prog)
        src_dir = ntpath.dirname(source)
        src_ns = shell.NameSpace(src_dir)
        if src_ns is None:
            raise ShellCopyError('Windows Shell could not open the Desktop Connector source folder.')
        item = src_ns.ParseName(name)
        if item is None:
            raise ShellCopyError('Windows Shell could not resolve the selected Desktop Connector file.')
        dst_ns = shell.NameSpace(folder)
        if dst_ns is None:
            raise ShellCopyError('Windows Shell could not open the local staging folder.')
        try:
            expected = int(item.Size)
        except Exception:
            expected = 0
        # FOF_SILENT | NOCONFIRMATION | NOCONFIRMMKDIR | NOERRORUI | NO_UI.
        dst_ns.CopyHere(item, 9748)
        _wait_shell_copy(local, expected, cancelled)
        return local, folder
    except Exception:
        try: remove_tree_retry(folder)
        except Exception: pass
        raise
    finally:
        _release_com(dst_ns)
        _release_com(item)
        _release_com(src_ns)
        _release_com(shell)


def _shell_copy_powershell(source, cancelled=None):
    """CPython fallback for verification environments without .NET COM interop."""
    if os.name != 'nt':
        raise ShellCopyError('Windows Shell copy is available only on Windows.')
    check(cancelled)
    folder = tempfile.mkdtemp(prefix='EasyBIM_ET_DC_')
    name = ntpath.basename(source)
    local = os.path.join(folder, name)
    script = r"""
$ErrorActionPreference = 'Stop'
$src = %s
$dst = %s
$name = [System.IO.Path]::GetFileName($src)
$srcDir = [System.IO.Path]::GetDirectoryName($src)
$shell = New-Object -ComObject Shell.Application
$srcNs = $shell.NameSpace($srcDir)
if ($null -eq $srcNs) { throw 'Windows Shell could not open the Desktop Connector source folder.' }
$item = $srcNs.ParseName($name)
if ($null -eq $item) { throw 'Windows Shell could not resolve the selected Desktop Connector file.' }
$dstNs = $shell.NameSpace($dst)
if ($null -eq $dstNs) { throw 'Windows Shell could not open the local staging folder.' }
$expected = 0
try { $expected = [int64]$item.Size } catch { $expected = 0 }
$dstNs.CopyHere($item, 9748)
$out = Join-Path $dst $name
$deadline = (Get-Date).AddMinutes(20)
$stable = 0
$last = -1
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
    try {
        if (Test-Path -LiteralPath $out -PathType Leaf) {
            $length = [int64](Get-Item -LiteralPath $out).Length
            if (($expected -gt 0 -and $length -eq $expected) -or
                ($expected -le 0 -and $length -eq $last -and $length -gt 0)) {
                $stable++
            } else { $stable = 0 }
            $last = $length
            if ($stable -ge 4) { exit 0 }
        }
    } catch { $stable = 0 }
}
throw 'Windows Shell copy timed out before the local snapshot became stable.'
""" % (_ps_quote(source), _ps_quote(folder))
    encoded = base64.b64encode(script.encode('utf-16-le'))
    if not isinstance(encoded, str):
        encoded = encoded.decode('ascii')
    powershell = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                              'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    proc = None
    try:
        proc = subprocess.Popen([powershell, '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        while proc.poll() is None:
            try:
                check(cancelled)
            except Cancelled:
                try: proc.kill()
                except Exception: pass
                raise
            time.sleep(0.2)
        out, err = proc.communicate()
        if proc.returncode != 0 or not os.path.isfile(local):
            def readable(value):
                try: return value.decode('utf-8', 'replace')
                except AttributeError: return text(value or '')
            detail = readable(err).strip() or readable(out).strip()
            if not detail:
                detail = 'Windows Shell subprocess exited with code {0}.'.format(proc.returncode)
            raise ShellCopyError(detail)
        return local, folder
    except Exception:
        if proc is not None and proc.poll() is None:
            try: proc.kill()
            except Exception: pass
        try: remove_tree_retry(folder)
        except Exception: pass
        raise


def shell_copy_to_local(source, cancelled=None):
    """Materialize a Desktop Connector item via Windows Shell."""
    backend = shell_copy_backend()
    try:
        if backend == 'INPROCESS_COM':
            return _shell_copy_inprocess(source, cancelled)
        return _shell_copy_powershell(source, cancelled)
    except Cancelled:
        raise
    except Exception as exc:
        if isinstance(exc, ShellCopyError):
            detail = text(exc)
        else:
            detail = '{0}: {1}'.format(type(exc).__name__, text(exc))
        raise ShellCopyError(
            'Desktop Connector Windows Shell acquisition failed [{0}] for {1}: {2}'.format(
                backend, source, detail))



def remove_tree_retry(path, attempts=10, delay=0.2):
    """Remove local scratch with short retries for antivirus/provider handles."""
    if not path or not os.path.exists(path): return
    last = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            last = exc
            time.sleep(delay * (attempt + 1))
    if os.path.exists(path):
        raise last


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


def _copy_file_direct(source, target, cancelled=None, pulse=None, display_source=None):
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
    label = display_source or source
    try:
        with open(source, 'rb') as inp:
            inp.read(1)
            inp.seek(0)
            before = signature(source)
            check(cancelled)
            fd, temp = tempfile.mkstemp(prefix='.et-', suffix='.tmp', dir=folder)
            with os.fdopen(fd, 'wb') as out:
                while True:
                    check(cancelled)
                    block = inp.read(1024 * 1024)
                    if not block: break
                    out.write(block); h.update(block); size += len(block)
                    if pulse: pulse(label, size, before[0])
                out.flush()
        if signature(source) != before or size != before[0]:
            raise IOError('Source changed during collection; rerun after saving/sync completes: ' + label)
        # Finalize once: IronPython's Windows crypto provider can invalidate
        # the native handle when hexdigest() is called again on this object.
        # Cache the value for BOTH verification and the success metadata.
        expected = h.hexdigest()
        if digest(temp, cancelled) != expected: raise IOError('Copy checksum mismatch: ' + label)
        metadata = {'sha256': expected, 'size': size, 'source_mtime': before[1]}
        check(cancelled)
        publish(temp, target)
        return metadata
    finally:
        if temp and os.path.exists(temp): os.remove(temp)


def copy_file(source, target, cancelled=None, pulse=None):
    """Verified copy, using Windows Shell for Desktop Connector namespaces."""
    shell_folder = None
    shell_local = None
    metadata = None
    cleanup_warning = None
    try:
        if os.name == 'nt' and is_desktop_connector_path(source):
            backend_name = shell_copy_backend()
            shell_local, shell_folder = shell_copy_to_local(source, cancelled)
            metadata = _copy_file_direct(shell_local, target, cancelled, pulse, source)
            metadata['copy_method'] = ('WINDOWS_SHELL_COM' if backend_name == 'INPROCESS_COM'
                                       else 'WINDOWS_SHELL_POWERSHELL')
            metadata['shell_backend'] = backend_name
            metadata['source_stability'] = 'SHELL_SNAPSHOT'
        else:
            metadata = _copy_file_direct(source, target, cancelled, pulse, source)
            metadata['copy_method'] = 'DIRECT'
            metadata['source_stability'] = 'DIRECT'
    finally:
        if shell_folder:
            try: remove_tree_retry(shell_folder)
            except Exception as exc: cleanup_warning = text(exc)
    if metadata is not None and cleanup_warning:
        metadata['staging_cleanup_warning'] = cleanup_warning
    return metadata


def source_snapshot_changed(source, metadata):
    """Shell snapshots are immutable local acquisition points; never restat DC."""
    if metadata.get('source_stability') == 'SHELL_SNAPSHOT':
        return False
    return signature(source) != (metadata.get('size'), metadata.get('source_mtime'))


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
