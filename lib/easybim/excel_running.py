# -*- coding: utf-8 -*-
"""Is a workbook open in a running Excel, and does it have unsaved changes?

Best effort and never raises: callers get True / False / None (unknown).
Excel's owner file (``~$name``) is checked first so the common case - the
workbook is not open - costs no COM call. The running Excel is reached
late-bound (Type.InvokeMember), which works on .NET Framework (Revit 2024 and
older) and .NET 8 alike; only the way to get the running instance differs.
"""

import os


def owner_lock_paths(workbook_path):
    """Candidate Excel owner files for ``workbook_path``.

    Excel writes ``~$`` + the file name, but replaces the first two
    characters of longer names instead of prefixing them.
    """
    folder, name = os.path.split(u"{0}".format(workbook_path or u""))
    if not name:
        return []
    candidates = [os.path.join(folder, u"~$" + name)]
    if len(name) > 2:
        candidates.append(os.path.join(folder, u"~$" + name[2:]))
    return candidates


def is_open_in_excel(workbook_path):
    for path in owner_lock_paths(workbook_path):
        try:
            if os.path.exists(path):
                return True
        except Exception:
            pass
    return False


def _same_path(path_a, path_b):
    def norm(path):
        return os.path.normcase(os.path.abspath(u"{0}".format(path or u"")))
    try:
        return norm(path_a) == norm(path_b)
    except Exception:
        return False


def _com_get(target, member, *args):
    from System import Array, Object
    from System.Reflection import BindingFlags
    flags = BindingFlags.GetProperty | BindingFlags.InvokeMethod
    return target.GetType().InvokeMember(
        member, flags, None, target, Array[Object](list(args)))


def _active_excel():
    from System.Runtime.InteropServices import Marshal
    getter = getattr(Marshal, "GetActiveObject", None)
    if getter is not None:
        return getter("Excel.Application")
    # .NET 8 dropped Marshal.GetActiveObject: ask OLE directly.
    import ctypes
    from System import IntPtr

    class _GUID(ctypes.Structure):
        _fields_ = [("data1", ctypes.c_ulong), ("data2", ctypes.c_ushort),
                    ("data3", ctypes.c_ushort),
                    ("data4", ctypes.c_ubyte * 8)]

    # oledll raises OSError on a failing HRESULT (no Excel / not running).
    clsid = _GUID()
    ctypes.oledll.ole32.CLSIDFromProgID(
        u"Excel.Application", ctypes.byref(clsid))
    unknown = ctypes.c_void_p()
    ctypes.oledll.oleaut32.GetActiveObject(
        ctypes.byref(clsid), None, ctypes.byref(unknown))
    if not unknown.value:
        return None
    pointer = IntPtr(unknown.value)
    try:
        return Marshal.GetObjectForIUnknown(pointer)
    finally:
        Marshal.Release(pointer)


def workbook_has_unsaved_changes(workbook_path):
    """True/False when a running Excel has the workbook open, else None."""
    if os.name != "nt":
        return None
    try:
        excel = _active_excel()
        if excel is None:
            return None
        workbooks = _com_get(excel, "Workbooks")
        count = int(_com_get(workbooks, "Count"))
        for index in range(1, count + 1):
            workbook = _com_get(workbooks, "Item", index)
            if _same_path(_com_get(workbook, "FullName"), workbook_path):
                return not bool(_com_get(workbook, "Saved"))
        return None
    except Exception:
        return None
