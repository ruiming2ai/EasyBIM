# -*- coding: utf-8 -*-
"""Where Damper Check remembers its choices, and how the file is kept honest.

One local JSON file, ``%APPDATA%\\pyRevit\\pyRevit_EasyBIM_DamperCheck_settings.json``,
through pyRevit's ``get_universal_data_file`` rather than ``get_data_file`` -
the latter stamps the Revit version into the filename and would silo the
damper list per release.  Everything in it is by name: the ticked and
unticked ``Family : Type`` keys, the limit N, the scope, the class chips.
An ElementId never lands here because it means nothing in the next document.

The file I/O is ``easybim.local_settings`` (shared with Fire Damper Check);
this module owns only the schema: what the defaults are and how a file of
any age is read back into them.  No Revit imports.
"""

from __future__ import print_function

from easybim import local_settings
from easybim.local_settings import name_list as _name_list


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
    return local_settings.local_path(LOCAL_FILE_ID)


def load(path=None):
    """``(settings, note)`` - never raises."""
    return local_settings.load(LOCAL_FILE_ID, default_settings, normalize, path=path)


def save(settings, path=None):
    """``(ok, error_text)`` - never raises."""
    return local_settings.save(LOCAL_FILE_ID, settings, normalize, SCHEMA_VERSION, path=path)
