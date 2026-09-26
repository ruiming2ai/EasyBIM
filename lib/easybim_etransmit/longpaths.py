# -*- coding: utf-8 -*-
"""Unicode Win32 file IO for long copied dependency paths (not a Revit API shim).

No registry or process-wide .NET switches are changed. Short paths continue to
use the ordinary file routines. Extended paths must never be passed to Revit.
"""
from __future__ import unicode_literals
import os
import sys
import ntpath
import ctypes as c
import hashlib
import uuid
from . import performance
try: text = unicode
except NameError: text = str


def extended(path):
    path = text(path)
    if path.startswith(('\\\\.\\', '\\\\?\\')):
        raise ValueError('Device paths are not accepted as source/destination inputs.')
    path = ntpath.normpath(path)
    drive, tail = ntpath.splitdrive(path)
    if not drive or not tail.startswith('\\'):
        raise ValueError('An absolute drive/UNC file path is required.')
    if path.startswith('\\\\'):
        return '\\\\?\\UNC\\' + path[2:]
    return '\\\\?\\' + path


_NATIVE = None


def managed():
    """Use pinned P/Invoke methods, which capture errors before the dynamic binder."""
    global _NATIVE
    if _NATIVE is None:
        import clr
        assembly = os.path.join(os.path.dirname(__file__), 'native', 'EasyBIM.FileIO.dll')
        if not os.path.isfile(assembly):
            raise IOError('Missing e-transmit native helper. Update the whole EasyBIM extension, including native/EasyBIM.FileIO.dll.')
        clr.AddReferenceToFileAndPath(assembly)
        from EasyBIM.FileIO import Native
        _NATIVE = Native
    return _NATIVE


def invoke(name, *args):
    try:
        return getattr(managed(), name)(*args)
    except Exception as exc:
        raise IOError(text(exc))


def api():
    # Lazy load keeps package imports portable and does not require pythonnet.
    k = c.WinDLL('kernel32', use_last_error=(sys.platform != 'cli'))
    k.GetFileAttributesW.argtypes = [c.c_wchar_p]; k.GetFileAttributesW.restype = c.c_uint32
    k.CreateDirectoryW.argtypes = [c.c_wchar_p, c.c_void_p]; k.CreateDirectoryW.restype = c.c_int
    k.DeleteFileW.argtypes = [c.c_wchar_p]; k.DeleteFileW.restype = c.c_int
    k.RemoveDirectoryW.argtypes = [c.c_wchar_p]; k.RemoveDirectoryW.restype = c.c_int
    k.MoveFileW.argtypes = [c.c_wchar_p, c.c_wchar_p]; k.MoveFileW.restype = c.c_int
    k.CreateFileW.argtypes = [c.c_wchar_p,c.c_uint32,c.c_uint32,c.c_void_p,c.c_uint32,c.c_uint32,c.c_void_p]
    k.CreateFileW.restype = c.c_void_p
    k.CloseHandle.argtypes = [c.c_void_p]; k.CloseHandle.restype = c.c_int
    k.ReadFile.argtypes = [c.c_void_p,c.c_void_p,c.c_uint32,c.POINTER(c.c_uint32),c.c_void_p]; k.ReadFile.restype = c.c_int
    k.WriteFile.argtypes = k.ReadFile.argtypes; k.WriteFile.restype = c.c_int
    k.FlushFileBuffers.argtypes = [c.c_void_p]; k.FlushFileBuffers.restype = c.c_int
    return k


def last_error():
    # Only CPython uses ctypes. IronPython errors are captured inside P/Invoke.
    return c.get_last_error()


def error(path):
    number = last_error()
    return IOError(number, 'Windows file operation failed ({0}): {1}'.format(number, path))


def attributes(path):
    if sys.platform == 'cli':
        value = int(invoke('Attributes', extended(path)))
        return None if value == -1 else value
    k = api(); value = int(k.GetFileAttributesW(extended(path))) & 0xffffffff
    if value == 0xffffffff:
        number = last_error()
        if number in (2, 3): return None
        raise error(path)
    return value


def exists(path): return attributes(path) is not None

def isfile(path):
    value = attributes(path)
    return value is not None and not (value & 16)


def no_reparse(path):
    """Refuse traversal through directory junctions/symlinks on protected writes."""
    drive, tail = ntpath.splitdrive(ntpath.normpath(path))
    current = drive + '\\'
    for component in tail.strip('\\').split('\\'):
        if not component: continue
        current = ntpath.join(current, component)
        flags = attributes(current)
        if flags is None: return
        if flags & 1024: raise IOError('Reparse/symlink path requires an explicit real location: ' + current)


def makedirs(path):
    no_reparse(path)
    missing, current = [], path
    while attributes(current) is None:
        missing.append(current); parent = ntpath.dirname(current)
        if parent == current: raise IOError('Cannot find destination volume/share: ' + path)
        current = parent
    if not attributes(current) & 16: raise IOError('Destination parent is not a directory: ' + current)
    if sys.platform == 'cli':
        for current in reversed(missing): invoke('MakeDirectory', extended(current))
        return
    k = api()
    for current in reversed(missing):
        if not k.CreateDirectoryW(extended(current), None):
            if last_error() != 183: raise error(current)


def signature(path):
    if sys.platform == 'cli':
        stamp = invoke('Signature', extended(path))
        return (int(stamp.Size), (int(stamp.Written) - 116444736000000000) / 10000000.0)
    class Data(c.Structure):
        _pack_ = 4
        _fields_ = [('attrs',c.c_uint32),('created',c.c_uint64),('accessed',c.c_uint64),
                    ('written',c.c_uint64),('high',c.c_uint32),('low',c.c_uint32)]
    k = api(); k.GetFileAttributesExW.argtypes = [c.c_wchar_p,c.c_int,c.POINTER(Data)]
    k.GetFileAttributesExW.restype = c.c_int
    data = Data()
    if not k.GetFileAttributesExW(extended(path),0,c.byref(data)): raise error(path)
    return ((int(data.high) << 32) | int(data.low), (data.written - 116444736000000000) / 10000000.0)


class Stream(object):
    def __init__(self, path, writing=False):
        self.path, self.writing = path, writing
        self.dotnet = sys.platform == 'cli'
        if self.dotnet:
            self.handle = invoke('Open', extended(path), writing)
            return
        self.k = api()
        self.handle = self.k.CreateFileW(extended(path),0x40000000 if writing else 0x80000000,
                                       0 if writing else 7, None, 1 if writing else 3,128,None)
        if self.handle in (None, -1, c.c_void_p(-1).value): raise error(path)
    def read(self, count=1024*1024):
        if self.dotnet:
            from System import Array, Byte
            buf = Array.CreateInstance(Byte, count)
            done = self.handle.Read(buf, 0, count)
            return str(bytearray(buf)[:done])
        buf = c.create_string_buffer(count); done = c.c_uint32()
        if not self.k.ReadFile(self.handle,buf,count,c.byref(done),None): raise error(self.path)
        return buf.raw[:done.value]
    def write(self, value):
        if self.dotnet:
            from System import Array, Byte
            data = Array[Byte](bytearray(value))
            self.handle.Write(data, 0, len(data))
            return
        done = c.c_uint32(); buf = c.create_string_buffer(value, len(value))
        if not self.k.WriteFile(self.handle,buf,len(value),c.byref(done),None): raise error(self.path)
        if done.value != len(value): raise IOError('Incomplete write: ' + self.path)
    def flush(self):
        if self.dotnet:
            self.handle.Flush()
            return
        if not self.k.FlushFileBuffers(self.handle): raise error(self.path)
    def close(self):
        if self.handle is not None:
            if self.dotnet: self.handle.Dispose()
            else: self.k.CloseHandle(self.handle)
            self.handle = None
    def __enter__(self): return self
    def __exit__(self,*args): self.close()


def digest(path, cancelled=None):
    from .files import check
    value = hashlib.sha256(); size = 0
    with Stream(path) as inp:
        while True:
            check(cancelled); chunk = inp.read()
            if not chunk: break
            value.update(chunk); size += len(chunk)
    collector = performance.current()
    if collector and collector.stack: collector.stack[-1].meta['bytes'] = size
    return value.hexdigest()  # Finalize exactly once for IronPython.


def unlink(path):
    if sys.platform == 'cli':
        invoke('Delete', extended(path))
        return
    k = api()
    if not k.DeleteFileW(extended(path)):
        if last_error() not in (2, 3): raise error(path)


def copy_file(source, target, cancelled=None, pulse=None, label=None):
    from . import files as f
    f.check(cancelled)
    no_reparse(source); no_reparse(target)
    if exists(target): raise IOError('Refusing to overwrite: ' + target)
    if not isfile(source): raise IOError('Source missing or not a file: ' + source)
    folder = ntpath.dirname(target); makedirs(folder)
    temp = ntpath.join(folder, '.et-' + uuid.uuid4().hex[:8] + '.tmp')
    before = f.signature(source)
    total, sha = 0, hashlib.sha256()
    try:
        with Stream(source) as inp, Stream(temp, True) as out:
            while True:
                f.check(cancelled); chunk = inp.read()
                if not chunk: break
                out.write(chunk); sha.update(chunk); total += len(chunk)
                if pulse: pulse(label or source,total,before[0])
            out.flush()
        expected = sha.hexdigest()
        if total != before[0] or f.signature(source) != before:
            raise IOError('Source changed during collection: ' + source)
        if f.digest(temp, cancelled) != expected: raise IOError('Copy checksum mismatch: ' + source)
        f.check(cancelled)
        if sys.platform == 'cli': invoke('Move', extended(temp), extended(target))
        else:
            k = api()
            if not k.MoveFileW(extended(temp),extended(target)): raise error(target)
        return dict(size=total, source_mtime=before[1], sha256=expected, filesystem_io='WIN32_UNICODE')
    finally:
        if exists(temp): unlink(temp)


def children(path):
    """Enumerate directory entries through Unicode Win32 APIs, without following links."""
    if sys.platform == 'cli':
        for entry in invoke('Entries', extended(path)):
            yield text(entry.Name), int(entry.Attributes)
        return
    class FindData(c.Structure):
        _pack_ = 4
        _fields_ = [('attributes',c.c_uint32),('times',c.c_uint32*6),
                    ('sizehigh',c.c_uint32),('sizelow',c.c_uint32),
                    ('reserved',c.c_uint32*2),('name',c.c_wchar*260),('alternate',c.c_wchar*14)]
    k=api()
    k.FindFirstFileW.argtypes=[c.c_wchar_p,c.POINTER(FindData)]; k.FindFirstFileW.restype=c.c_void_p
    k.FindNextFileW.argtypes=[c.c_void_p,c.POINTER(FindData)]; k.FindNextFileW.restype=c.c_int
    k.FindClose.argtypes=[c.c_void_p]; k.FindClose.restype=c.c_int
    data=FindData()
    handle=k.FindFirstFileW(extended(ntpath.join(path,'*')),c.byref(data))
    if handle in (None,-1,c.c_void_p(-1).value):
        if last_error()==2: return
        raise error(path)
    try:
        while True:
            name=text(data.name)
            if name not in ('.','..'): yield name,int(data.attributes)
            if not k.FindNextFileW(handle,c.byref(data)):
                if last_error()!=18: raise error(path)
                break
    finally:k.FindClose(handle)


def walk_files(path):
    for name,flags in sorted(children(path)):
        if flags & 1024: continue
        child=ntpath.join(path,name)
        if flags & 16:
            for value in walk_files(child):yield value
        else:yield child


def remove_tree(path):
    """Explicit test/scratch cleanup; do not follow reparse points."""
    def remove_directory(value):
        if sys.platform == 'cli': invoke('RemoveDirectory', extended(value))
        else:
            k = api()
            if not k.RemoveDirectoryW(extended(value)): raise error(value)
    for name,flags in list(children(path)):
        child = ntpath.join(path, name)
        if flags & 16:
            if flags & 1024:
                remove_directory(child)
            else: remove_tree(child)
        else: unlink(child)
    remove_directory(path)


def snapshot_ready(path):
    """An exclusive read handle must be obtainable after the shell writer closes."""
    if sys.platform == 'cli': return bool(invoke('CanReadExclusively', extended(path)))
    k=api()
    handle=k.CreateFileW(extended(path),0x80000000,0,None,3,128,None)
    if handle in (None,-1,c.c_void_p(-1).value): return False
    k.CloseHandle(handle)
    return True
