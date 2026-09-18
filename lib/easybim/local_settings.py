# -*- coding: utf-8 -*-
"""One local JSON file per tool, kept honest - shared by Damper Check and
Fire Damper Check.

The file lives in pyRevit's roaming per-user data folder through
``get_universal_data_file`` rather than ``get_data_file``: the latter stamps
the Revit version into the filename and would silo a user's choices per
release.  Reading never raises - a truncated or hand-edited file yields the
tool's defaults plus a note the window can show - and writing goes to a
``.tmp`` beside the target and swaps, so a crash mid-write cannot leave a
half file behind.  Each tool owns its schema through the ``defaults`` and
``normalize`` callables it passes in.

No Revit imports; pyRevit is reached lazily inside ``local_path`` only.
"""

from __future__ import print_function

import io
import json
import os

try:
    from easybim.json_text import dumps as _json_dumps
except Exception:
    # Loaded standalone by a desktop test, without the package.  Inside Revit
    # the package is always there, and json_text is not optional: the
    # standard encoder cannot write a view or link name with an accent.
    _json_dumps = json.dumps


def safe_text(value):
    if value is None:
        return u""
    try:
        return u"{0}".format(value)
    except Exception:
        return u""


def name_list(value):
    """A sorted, de-duplicated list of non-empty names; anything else -> []."""
    names = []
    seen = set()
    for entry in value if isinstance(value, (list, tuple)) else []:
        text = safe_text(entry).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        names.append(text)
    return sorted(names)


def local_path(file_id):
    """pyRevit's roaming per-user data file for ``file_id``."""
    from pyrevit import script

    return script.get_universal_data_file(file_id, "json")


def load(file_id, defaults, normalize, path=None):
    """``(settings, note)`` - never raises; ``note`` names a file that could
    not be read so the window can say so instead of pretending."""
    if path is None:
        try:
            path = local_path(file_id)
        except Exception:
            return defaults(), u""
    if not path or not os.path.isfile(path):
        return defaults(), u""
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            raw = json.loads(handle.read() or "{}")
    except Exception as ex:
        return defaults(), u"Settings file could not be read ({0}); defaults used.".format(ex)
    return normalize(raw), u""


def save(file_id, settings, normalize, schema_version, path=None):
    """``(ok, error_text)`` - never raises."""
    if path is None:
        try:
            path = local_path(file_id)
        except Exception as ex:
            return False, u"No settings path is available: {0}".format(ex)
    if not path:
        return False, u"No settings path is set."

    payload = normalize(settings)
    payload["schema"] = schema_version
    folder = os.path.dirname(path)
    try:
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
    except Exception as ex:
        return False, u"Could not create {0}: {1}".format(folder, ex)

    text = _json_dumps(payload, indent=2, sort_keys=True)
    temporary = path + ".tmp"
    try:
        with io.open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text if isinstance(text, type(u"")) else text.decode("utf-8"))
        if os.path.isfile(path):
            os.remove(path)
        os.rename(temporary, path)
    except Exception as ex:
        try:
            if os.path.isfile(temporary):
                os.remove(temporary)
        except Exception:
            pass
        return False, u"Could not write {0}: {1}".format(path, ex)
    return True, u""
