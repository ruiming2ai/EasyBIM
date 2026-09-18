# -*- coding: utf-8 -*-
"""JSON text that IronPython can actually produce.

CPython 2.7's ``json`` encoder - the one IronPython ships - looks at every
string and, when it is a ``str`` holding a byte above ``\\x7f``, calls
``s.decode("utf-8")`` on it before escaping.  Under IronPython ``str`` *is*
unicode, so a room called "Café" makes it decode a unicode string, which
fails with "'unknown' codec can't decode byte 0xe9".  Every ``json.dumps``
in the extension that could meet an accented name is exposed to it: a room,
a view, a link, a filled region type.

This encoder never hands a string to that path.  It escapes every character
above ``\\x7e`` as ``\\uXXXX`` itself, so the output is plain ASCII that both
runtimes' ``json.loads`` read back unchanged.  Formatting follows CPython 3's
``json.dumps``: ``", "`` and ``": "`` between items when there is no indent,
``","`` and ``": "`` when there is, keys sorted when asked - so a file written
here reads like one written by the standard library.
"""

from __future__ import print_function

import json
import math


def loads(text):
    """The standard decoder; ``\\uXXXX`` escapes read back on both runtimes."""
    return json.loads(text)


def dumps(value, indent=None, sort_keys=True, separators=None):
    """ASCII-only JSON for ``value`` - dicts, lists, tuples, strings, numbers,
    booleans and ``None``.  Anything else is a ``TypeError``, as it is for
    the standard library."""
    if separators is None:
        separators = (",", ": ") if indent is not None else (", ", ": ")
    item_separator, key_separator = separators
    pieces = []
    _write(value, pieces, indent, 0, sort_keys, item_separator, key_separator)
    return u"".join(pieces)


def _write(value, out, indent, level, sort_keys, item_separator, key_separator):
    if value is None:
        out.append(u"null")
    elif value is True:
        out.append(u"true")
    elif value is False:
        out.append(u"false")
    elif isinstance(value, _text_types()):
        out.append(_string(value))
    elif isinstance(value, _int_types()):
        out.append(u"{0}".format(int(value)))
    elif isinstance(value, float):
        out.append(_number(value))
    elif isinstance(value, dict):
        _write_dict(value, out, indent, level, sort_keys, item_separator, key_separator)
    elif isinstance(value, (list, tuple)):
        _write_list(value, out, indent, level, sort_keys, item_separator, key_separator)
    else:
        raise TypeError("{0!r} is not JSON serializable".format(value))


def _write_dict(value, out, indent, level, sort_keys, item_separator, key_separator):
    if not value:
        out.append(u"{}")
        return
    items = list(value.items())
    keys = []
    for key, item in items:
        if isinstance(key, _text_types()):
            text = key
        elif key is None:
            text = u"null"
        elif key is True:
            text = u"true"
        elif key is False:
            text = u"false"
        elif isinstance(key, _int_types() + (float,)):
            text = u"{0}".format(key)
        else:
            raise TypeError("keys must be str, int, float, bool or None, not {0}".format(
                type(key).__name__))
        keys.append((text, item))
    if sort_keys:
        keys.sort(key=lambda pair: pair[0])
    out.append(u"{")
    first = True
    for text, item in keys:
        if not first:
            out.append(item_separator)
        first = False
        _newline(out, indent, level + 1)
        out.append(_string(text))
        out.append(key_separator)
        _write(item, out, indent, level + 1, sort_keys, item_separator, key_separator)
    _newline(out, indent, level)
    out.append(u"}")


def _write_list(value, out, indent, level, sort_keys, item_separator, key_separator):
    if not value:
        out.append(u"[]")
        return
    out.append(u"[")
    first = True
    for item in value:
        if not first:
            out.append(item_separator)
        first = False
        _newline(out, indent, level + 1)
        _write(item, out, indent, level + 1, sort_keys, item_separator, key_separator)
    _newline(out, indent, level)
    out.append(u"]")


def _newline(out, indent, level):
    if indent is None:
        return
    if isinstance(indent, _int_types()):
        pad = u" " * int(indent)
    else:
        pad = u"{0}".format(indent)
    out.append(u"\n" + pad * level)


_ESCAPES = {
    u'"': u'\\"',
    u"\\": u"\\\\",
    u"\n": u"\\n",
    u"\r": u"\\r",
    u"\t": u"\\t",
    u"\b": u"\\b",
    u"\f": u"\\f",
}


def _string(value):
    text = u"{0}".format(value)
    out = [u'"']
    for char in text:
        escaped = _ESCAPES.get(char)
        if escaped is not None:
            out.append(escaped)
            continue
        code = ord(char)
        if code > 0xFFFF:
            # CPython 3 hands over one code point; IronPython would have handed
            # over two UTF-16 units.  Either way JSON wants the surrogate pair.
            code -= 0x10000
            out.append(u"\\u{0:04x}\\u{1:04x}".format(0xD800 + (code >> 10),
                                                      0xDC00 + (code & 0x3FF)))
        elif code < 0x20 or code > 0x7E:
            out.append(u"\\u{0:04x}".format(code))
        else:
            out.append(char)
    out.append(u'"')
    return u"".join(out)


def _number(value):
    if math.isnan(value) or math.isinf(value):
        raise ValueError("Out of range float values are not JSON compliant")
    text = repr(value)
    # IronPython and CPython 2 write 1e-07 as "1e-07"; both read it back.
    return u"{0}".format(text)


def _text_types():
    try:
        return (str, unicode)  # noqa: F821 - IronPython / Python 2
    except NameError:
        return (str,)


def _int_types():
    try:
        return (int, long)  # noqa: F821 - IronPython / Python 2
    except NameError:
        return (int,)
