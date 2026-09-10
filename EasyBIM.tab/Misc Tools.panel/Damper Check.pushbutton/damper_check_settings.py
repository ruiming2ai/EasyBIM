# -*- coding: utf-8 -*-
"""Where Damper Check remembers its choices, and how the file is kept honest.

One local JSON file, ``%APPDATA%\\pyRevit\\pyRevit_EasyBIM_DamperCheck_settings.json``,
through pyRevit's ``get_universal_data_file`` rather than ``get_data_file`` -
the latter stamps the Revit version into the filename and would silo the
damper list per release.  Everything in it is by name: the ticked and
unticked ``Family : Type`` keys, the limit N, the scope, the class chips.
An ElementId never lands here because it means nothing in the next document.

Reading never raises - a truncated or hand-edited file yields the defaults
plus a note the window can show - and writing goes to a ``.tmp`` beside the
target and swaps, so a crash mid-write cannot leave a half file behind.
No Revit imports: the desktop tests drive it against a temp path.
"""

from __future__ import print_function

import io
import json
import os


SCHEMA_VERSION = 1
LOCAL_FILE_ID = "EasyBIM_DamperCheck_settings"

SYSTEM_CLASS_KEYS = ("SupplyAir", "ReturnAir", "ExhaustAir", "OtherAir", "Unknown")
SCOPES = ("model", "view")
DEFAULT_THRESHOLD = 1
MAX_THRESHOLD = 9999


def _safe_text(value):
    if value is None:
        return u""
    try:
        return u"{0}".format(value)
    except Exception:
        return u""


def default_settings():
    return {
        "schema": SCHEMA_VERSION,
        "threshold": DEFAULT_THRESHOLD,
        "scope": "model",
        "system_classes": list(SYSTEM_CLASS_KEYS),
        "damper_types": [],
        "unticked_types": [],
    }


def _name_list(value):
    names = []
    seen = set()
    for entry in value if isinstance(value, (list, tuple)) else []:
        text = _safe_text(entry).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        names.append(text)
    return sorted(names)


def normalize(raw):
    """Whatever was on disk -> a settings dict this build can use.

    Unknown keys are dropped, wrong types fall back to the default, and a
    newer schema is read as far as it is understood rather than refused: the
    worst outcome of a stale file must be re-ticking a checklist.
    """
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings

    try:
        threshold = int(raw.get("threshold", DEFAULT_THRESHOLD))
    except Exception:
        threshold = DEFAULT_THRESHOLD
    settings["threshold"] = max(1, min(MAX_THRESHOLD, threshold))

    scope = _safe_text(raw.get("scope")).strip()
    settings["scope"] = scope if scope in SCOPES else "model"

    classes = raw.get("system_classes")
    if isinstance(classes, (list, tuple)):
        chosen = [key for key in SYSTEM_CLASS_KEYS
                  if key in set(_safe_text(item).strip() for item in classes)]
        settings["system_classes"] = chosen
    settings["damper_types"] = _name_list(raw.get("damper_types"))
    settings["unticked_types"] = _name_list(raw.get("unticked_types"))
    return settings


def local_path():
    """pyRevit's roaming per-user data file; imported lazily so this module
    loads (and is tested) without pyRevit on the path."""
    from pyrevit import script

    return script.get_universal_data_file(LOCAL_FILE_ID, "json")


def load(path=None):
    """``(settings, note)`` - never raises; ``note`` names a file that could
    not be read so the window can say so instead of pretending."""
    if path is None:
        try:
            path = local_path()
        except Exception:
            return default_settings(), u""
    if not path or not os.path.isfile(path):
        return default_settings(), u""
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            raw = json.loads(handle.read() or "{}")
    except Exception as ex:
        return default_settings(), u"Settings file could not be read ({0}); defaults used.".format(ex)
    return normalize(raw), u""


def save(settings, path=None):
    """``(ok, error_text)`` - never raises."""
    if path is None:
        try:
            path = local_path()
        except Exception as ex:
            return False, u"No settings path is available: {0}".format(ex)
    if not path:
        return False, u"No settings path is set."

    payload = normalize(settings)
    payload["schema"] = SCHEMA_VERSION
    folder = os.path.dirname(path)
    try:
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
    except Exception as ex:
        return False, u"Could not create {0}: {1}".format(folder, ex)

    text = json.dumps(payload, indent=2, sort_keys=True)
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
