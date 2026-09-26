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
from . import performance

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
              ('analysis', 'Systems analysis reports'), ('spreadsheets', 'Plugin spreadsheet sources (where readable)'), ('other', 'Other external files')]


class Cancelled(Exception):
    pass


def defaults():
    return dict(include=dict((k, True) for k, label in CATEGORIES), deep=True,
                repath=True, load_unloaded_files=True, file_structure='categories', upgrade=False, cleanup=False, discard_worksets=False,
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
                             ('spreadsheets', '.xls .xlsx .xlsm .xlsb .xlt .xltx .xltm .csv'),
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
    if os.name == 'nt' and (path_units(path) > 240 or path_units(root) > 240):
        from . import longpaths
        p, r = ntpath.normcase(ntpath.normpath(path)), ntpath.normcase(ntpath.normpath(root))
        if not (p == r or p.startswith(r.rstrip('\\') + '\\')): return False
        longpaths.no_reparse(path)
        return True
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


def _desktop_connector_workspace_locations():
    """Read Desktop Connector custom workspace roots without modifying settings.

    Desktop Connector Advanced Settings stores WorkspaceLocation per Autodesk
    documentation. Check user then machine scope; support Python 2/IronPython
    and Python 3 registry module names.
    """
    if os.name != 'nt':
        return []
    try:
        try:
            import winreg
        except ImportError:
            import _winreg as winreg
    except ImportError:
        return []

    key_path = r'SOFTWARE\Autodesk\Desktop Connector Advanced Settings'
    values = []
    seen = set()
    hives = []
    for name in ('HKEY_CURRENT_USER', 'HKEY_LOCAL_MACHINE'):
        hive = getattr(winreg, name, None)
        if hive is not None:
            hives.append(hive)

    access_modes = [getattr(winreg, 'KEY_READ', 0x20019)]
    wow64 = getattr(winreg, 'KEY_WOW64_64KEY', 0)
    if wow64:
        access_modes.insert(0, getattr(winreg, 'KEY_READ', 0x20019) | wow64)

    for hive in hives:
        for access in access_modes:
            key = None
            try:
                key = winreg.OpenKey(hive, key_path, 0, access)
                value, _kind = winreg.QueryValueEx(key, 'WorkspaceLocation')
                value = os.path.expandvars(text(value or '').strip().strip('"'))
                if value:
                    value = ntpath.normpath(value)
                    canonical_value = ntpath.normcase(value)
                    if canonical_value not in seen:
                        seen.add(canonical_value)
                        values.append(value)
                break
            except (IOError, OSError):
                pass
            finally:
                if key is not None:
                    try:
                        winreg.CloseKey(key)
                    except Exception:
                        pass
    return values


def default_connector_roots():
    """Known Desktop Connector ACCDocs roots; exact hierarchy matching only."""
    roots = []
    seen = set()

    def add(value):
        value = text(value or '').strip()
        if not value:
            return
        value = ntpath.normpath(value) if os.name == 'nt' else os.path.normpath(value)
        key = ntpath.normcase(value) if os.name == 'nt' else value
        if key not in seen:
            seen.add(key)
            roots.append(value)

    configured = os.environ.get('EASYBIM_DESKTOP_CONNECTOR_ROOTS', '')
    for value in configured.split(os.pathsep):
        add(value)

    pm = ntpath if os.name == 'nt' else os.path
    for workspace in _desktop_connector_workspace_locations():
        workspace = text(workspace).strip()
        if pm.basename(pm.normpath(workspace)).lower() == 'accdocs':
            add(workspace)
        else:
            add(pm.join(workspace, 'ACCDocs'))

    profile = os.environ.get('USERPROFILE') or os.path.expanduser('~')
    # Desktop Connector v16+ default, then legacy v15 location.
    add(ntpath.join(profile, 'DC', 'ACCDocs') if os.name == 'nt'
        else os.path.join(profile, 'DC', 'ACCDocs'))
    add(ntpath.join(profile, 'ACCDocs') if os.name == 'nt'
        else os.path.join(profile, 'ACCDocs'))
    return roots


def _connector_uri_parts(source):
    value = text(source or '').strip()
    match = re.match(r'^(Autodesk Docs|Autodesk Forma|Autodesk Construction Cloud|BIM 360|ACC)://(.+)$',
                     value, re.I)
    if not match:
        return None
    return clean_parts(match.group(2))


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
                if item.IsFolder:
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


def connector_file_exists(path):
    """Return whether one exact Desktop Connector item exists; never search by name."""
    if not is_desktop_connector_path(path):
        return False
    return _shell_file_exists(path)


@performance.timed('discovery', 'connector_lookup')
def resolve_connector_uri(source, roots=None):
    """Resolve one Autodesk display URI to one exact Connector hierarchy."""
    parts = _connector_uri_parts(source)
    if not parts:
        return None
    candidates = []
    seen = set()
    for root in roots or default_connector_roots():
        if not root:
            continue
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


@performance.timed('discovery', 'library_lookup')
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
        exact = os.path.normpath(os.path.join(root, *clean_parts(relative)))
        if os.path.isfile(exact):
            key = canonical(exact)
            if key not in seen:
                seen.add(key)
                matches.append(exact)
            continue
        for current, dirs, names in os.walk(root):
            dirs[:] = sorted(dirs)
            for name in names:
                if name.lower() == wanted:
                    candidate = os.path.join(current, name)
                    key = canonical(candidate)
                    if key not in seen:
                        seen.add(key)
                        matches.append(candidate)
            if len(matches) > 1:
                break
        if len(matches) > 1:
            break
    if len(matches) > 1:
        raise ValueError('Ambiguous Revit library resource: ' + value)
    return matches[0] if matches else None


@performance.timed('discovery', 'resolve_path')
def resolve_source(source, owner='', mappings=None):
    """Map exact identities only; never a filename-only or newest-model guess."""
    source = text(source or '').strip()
    if cache_source(source):
        return None
    normalized = source.replace('\\', '/').rstrip('/')
    candidates = []
    for prefix, folder in mappings or []:
        pre = text(prefix).replace('\\', '/').rstrip('/')
        if not pre:
            continue
        if normalized.lower() == pre.lower() or normalized.lower().startswith(pre.lower() + '/'):
            tail = normalized[len(pre):].lstrip('/')
            parts = clean_parts(tail)
            pm = ntpath if is_windows(folder) else os.path
            candidate = pm.normpath(pm.join(folder, *parts)) if parts else folder
            if not absolute(candidate):
                raise ValueError('Mapping destination must be absolute.')
            candidates.append((len(pre), candidate))
    if candidates:
        best = max(n for n, p in candidates)
        paths = set(canonical(p) for n, p in candidates if n == best)
        if len(paths) != 1:
            raise ValueError('Ambiguous source-prefix mappings: ' + source)
        selected = next(p for n, p in candidates if n == best)
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
        selected = pm.normpath(pm.join(pm.dirname(owner), source))
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


def snapshot_ready(path):
    if os.name == 'nt':
        from . import longpaths
        return longpaths.snapshot_ready(path)
    return os.path.isfile(path)


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
                if stable >= 4 and snapshot_ready(local):
                    return local
        except OSError:
            stable = 0
        time.sleep(0.25)
    raise ShellCopyError('Windows Shell copy timed out before the local snapshot became stable.')


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
            if ($stable -ge 4) {
                try {
                    $test = [System.IO.File]::Open($out, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
                    $test.Dispose()
                    exit 0
                } catch { $stable = 0 }
            }
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



@performance.timed('cleanup', 'remove_scratch')
def remove_tree_retry(path, attempts=10, delay=0.2):
    """Remove local scratch with short retries for antivirus/provider handles."""
    exists, remove = os.path.exists, shutil.rmtree
    if os.name == 'nt':
        from . import longpaths
        exists, remove = longpaths.exists, longpaths.remove_tree
    if not path or not exists(path): return
    last = None
    for attempt in range(attempts):
        try:
            remove(path)
            return
        except OSError as exc:
            last = exc
            time.sleep(delay * (attempt + 1))
    if exists(path):
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


def validate_copy_path(path):
    """Filesystem budget is separate from Revit's model/SaveAs path budget."""
    if os.name != 'nt' and not is_windows(path): return path
    value = ntpath.normpath(path)
    parts = ntpath.splitdrive(value)[1].strip('\\').split('\\')
    if path_units(value) > 32700 or any(path_units(part) > 255 for part in parts):
        raise PathLengthError('Windows dependency path/component exceeds the Unicode filesystem limit: ' + path)
    return path


def file_exists(path):
    if os.name == 'nt' and path_units(path) > 240:
        from . import longpaths
        return longpaths.isfile(path)
    return os.path.isfile(path)


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


def publish_report(source, target):
    """Refresh only our named reports in a newly allocated package/batch."""
    names = ('manifest.json', 'START_HERE.txt', 'REPORT.txt', 'files.csv',
             'references.csv', 'issues.csv', 'DIAGNOSTICS.txt', 'batch.json', 'BATCH_SUMMARY.txt', 'timings.csv', 'batch_timings.csv')
    if os.path.basename(target) not in names or os.path.basename(source) != os.path.basename(target):
        raise ValueError('Not a generated transmittal report: '+target)
    if file_exists(target):
        if os.name == 'nt':
            from . import longpaths
            longpaths.no_reparse(target);longpaths.unlink(target)
        else: os.remove(target)
    return copy_file(source, target)


def directory_has_entries(path):
    if os.name == 'nt' and path_units(path) > 240:
        from . import longpaths
        if not longpaths.exists(path): return False
        return bool(list(longpaths.children(path)))
    return os.path.exists(path) and bool(os.listdir(path))


def ensure_directory(path):
    if os.name == 'nt' and path_units(path) > 240:
        from . import longpaths
        longpaths.makedirs(path)
    elif not os.path.isdir(path): os.makedirs(path)


def destination(root, relative):
    if absolute(relative) or relative.startswith(('/', '\\')):
        raise ValueError('Package entry must be relative.')
    parts = clean_parts(relative)
    target = os.path.join(root, *parts)
    validate_copy_path(target)
    if not within(target, root): raise ValueError('Destination escapes package root.')
    return target


def check(cancelled=None):
    if cancelled and cancelled(): raise Cancelled('Cancelled by user.')


def signature(path):
    if os.name == 'nt' and path_units(path) > 240:
        from . import longpaths
        return longpaths.signature(path)
    s = os.stat(path)
    return (s.st_size, s.st_mtime)


@performance.timed(None, 'hash')
def digest(path, cancelled=None):
    if os.name == 'nt' and path_units(path) > 240:
        from . import longpaths
        return longpaths.digest(path, cancelled)
    h = hashlib.sha256(); size = 0
    with open(path, 'rb') as f:
        while True:
            check(cancelled)
            b = f.read(1024 * 1024)
            if not b: break
            h.update(b); size += len(b)
    collector = performance.current()
    if collector and collector.stack: collector.stack[-1].meta['bytes'] = size
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
    validate_copy_path(target)
    if cache_source(source): raise IOError('CollaborationCache/PacCache copies are not supported.')
    if os.name == 'nt' and (path_units(source) > 240 or path_units(target) > 240):
        from . import longpaths
        return longpaths.copy_file(source, target, cancelled, pulse, display_source)
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


@performance.timed(None, 'copy', target_index=1)
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
    if metadata is not None:
        metadata['verified_signature'] = signature(target)
    return metadata


def source_snapshot_changed(source, metadata):
    """Shell snapshots are immutable local acquisition points; never restat DC."""
    if metadata.get('source_stability') in ('SHELL_SNAPSHOT', 'ARCHIVE_SNAPSHOT', 'SESSION_SNAPSHOT', 'AUTHENTICATED_SNAPSHOT'):
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


@performance.timed(None, 'integrity_check')
def verified_hash(path, metadata, cancelled=None):
    """Reuse a verified checksum only for unchanged, task-owned output bytes."""
    expected = metadata.get('packaged_sha256') or metadata.get('sha256')
    stamp = signature(path)
    if expected and tuple(metadata.get('verified_signature') or ()) == stamp:
        return expected
    actual = digest(path, cancelled)
    if expected and actual != expected:
        raise IOError('Previously verified output changed: ' + path)
    metadata['verified_signature'] = stamp
    return actual


@performance.timed(None, 'relocate', target_index=1)
def relocate_verified(source, target, metadata, owned_root, cancelled=None, pulse=None):
    """Move only our scratch files; copy+verify across volumes. Never move originals."""
    check(cancelled)
    if not within(source, owned_root):
        raise ValueError('Only task-owned scratch files may be moved.')
    validate_copy_path(target)
    if file_exists(target): raise IOError('Refusing to overwrite: ' + target)
    expected = verified_hash(source, metadata, cancelled)
    ensure_directory(os.path.dirname(target))
    try:
        if os.name == 'nt' and (path_units(source)>240 or path_units(target)>240):
            from . import longpaths
            longpaths.no_reparse(source); longpaths.no_reparse(target)
            if sys.platform == 'cli':
                longpaths.invoke('Move', longpaths.extended(source), longpaths.extended(target))
            else:
                k = longpaths.api()
                if not k.MoveFileW(longpaths.extended(source),longpaths.extended(target)):
                    raise longpaths.error(target)
        else:
            publish(source, target)
        method = 'MOVE_VERIFIED_BYTES'
    except (IOError, OSError):
        # On a failed rename, do not overwrite any racing destination writer.
        if file_exists(target) or not file_exists(source): raise
        copied = copy_file(source, target, cancelled, pulse)
        if copied['sha256'] != expected:
            raise IOError('Delivered bytes differ from verified scratch file: ' + target)
        method = 'VERIFIED_COPY_FALLBACK'
        # Keep the owned source until routine scratch cleanup on copy fallback.
    return dict(sha256=expected, size=signature(target)[0],
                verified_signature=signature(target), delivery_method=method)
