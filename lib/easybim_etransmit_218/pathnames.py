# -*- coding: utf-8 -*-
"""Lexical relative paths: IronPython ntpath.relpath calls legacy GetFullPath.

Both Windows inputs must already be absolute. This routine does not access the
filesystem and is not a replacement for destination containment checks.
"""
from __future__ import unicode_literals
import ntpath
import os


def relative(path, start):
    path = ntpath.normpath(path)
    start = ntpath.normpath(start)
    drive, tail = ntpath.splitdrive(path)
    base_drive, base_tail = ntpath.splitdrive(start)
    if not drive and not base_drive:
        # Native POSIX paths used in portable tests.
        return os.path.relpath(path.replace(chr(92), '/'), start.replace(chr(92), '/'))
    if (not drive or not base_drive or not tail.startswith(chr(92)) or
            not base_tail.startswith(chr(92))):
        raise ValueError('Relative-path calculation requires absolute Windows inputs.')
    if ntpath.normcase(drive) != ntpath.normcase(base_drive):
        raise ValueError('Cannot create a relative path between different volumes/shares.')
    parts = [p for p in tail.split(chr(92)) if p]
    base = [p for p in base_tail.split(chr(92)) if p]
    common = 0
    for left, right in zip(parts, base):
        if ntpath.normcase(left) != ntpath.normcase(right):
            break
        common += 1
    result = ['..'] * (len(base) - common) + parts[common:]
    return ntpath.join(*result) if result else '.'
