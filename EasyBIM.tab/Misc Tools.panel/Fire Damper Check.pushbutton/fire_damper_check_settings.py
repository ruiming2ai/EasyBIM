# -*- coding: utf-8 -*-
"""Fire Damper Check's settings: the schema, and nothing else.

The file itself is ``easybim.local_settings`` (shared with Damper Check);
this module says what the defaults are and how a file of any age reads
back into them.  Everything is by name: the ticked fire damper types, and
per link title the rating parameter, the wall and floor types and the line
styles that count as rated - never an ElementId, because one means nothing
in the next document.  No Revit imports.
"""

from __future__ import print_function

from easybim import local_settings
from easybim.local_settings import name_list as _name_list


SCHEMA_VERSION = 1
LOCAL_FILE_ID = "EasyBIM_FireDamperCheck_settings"

SCOPES = ("model", "view")
DEFAULT_NEAR_MM = 600
DEFAULT_HOPS = 3
MAX_HOPS = 20
MAX_NEAR_MM = 100000
DEFAULT_RATING_PARAM = u"Fire Rating"
LINK_NAME_LISTS = ("wall_types", "unticked_wall_types", "floor_types", "unticked_floor_types",
                   "line_styles", "unticked_line_styles")


def _safe_text(value):
    if value is None:
        return u""
    try:
        return u"{0}".format(value)
    except Exception:
        return u""


def _clamped_int(raw, default, low, high):
    try:
        value = int(raw)
    except Exception:
        return default
    return max(low, min(high, value))


def default_band():
    return {"mode": "next_level", "height_mm": 3000, "min_storey_mm": 1500}


def default_link():
    entry = {"enabled": True, "rating_param": DEFAULT_RATING_PARAM}
    for name in LINK_NAME_LISTS:
        entry[name] = []
    return entry


def default_settings():
    return {
        "schema": SCHEMA_VERSION,
        "scope": "model",
        "include_floors": True,
        "damper_types": [],
        "unticked_damper_types": [],
        "near_mm": DEFAULT_NEAR_MM,
        "hops": DEFAULT_HOPS,
        "min_penetration_mm": 10,
        "line_thickness_mm": 200,
        "band": default_band(),
        "links": {},
    }


def normalize_link(raw):
    entry = default_link()
    if not isinstance(raw, dict):
        return entry
    entry["enabled"] = bool(raw.get("enabled", True))
    entry["rating_param"] = _safe_text(raw.get("rating_param", DEFAULT_RATING_PARAM)).strip()
    for name in LINK_NAME_LISTS:
        entry[name] = _name_list(raw.get(name))
    return entry


def normalize(raw):
    """Whatever was on disk -> a settings dict this build can use.

    Unknown keys are dropped, wrong types fall back to the default, and a
    newer schema is read as far as it is understood: the worst outcome of
    a stale file must be re-ticking a checklist.
    """
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings
    scope = _safe_text(raw.get("scope")).strip()
    settings["scope"] = scope if scope in SCOPES else "model"
    settings["include_floors"] = bool(raw.get("include_floors", True))
    settings["damper_types"] = _name_list(raw.get("damper_types"))
    settings["unticked_damper_types"] = _name_list(raw.get("unticked_damper_types"))
    settings["near_mm"] = _clamped_int(raw.get("near_mm", DEFAULT_NEAR_MM), DEFAULT_NEAR_MM, 1, MAX_NEAR_MM)
    settings["hops"] = _clamped_int(raw.get("hops", DEFAULT_HOPS), DEFAULT_HOPS, 0, MAX_HOPS)
    settings["min_penetration_mm"] = _clamped_int(raw.get("min_penetration_mm", 10), 10, 0, 1000)
    settings["line_thickness_mm"] = _clamped_int(raw.get("line_thickness_mm", 200), 200, 1, 5000)
    band = raw.get("band")
    if isinstance(band, dict):
        settings["band"] = {
            "mode": "fixed" if _safe_text(band.get("mode")) == "fixed" else "next_level",
            "height_mm": _clamped_int(band.get("height_mm", 3000), 3000, 300, 100000),
            "min_storey_mm": _clamped_int(band.get("min_storey_mm", 1500), 1500, 300, 100000),
        }
    links = raw.get("links")
    if isinstance(links, dict):
        settings["links"] = dict((_safe_text(key), normalize_link(value))
                                 for key, value in links.items() if _safe_text(key).strip())
    return settings


def local_path():
    return local_settings.local_path(LOCAL_FILE_ID)


def load(path=None):
    """``(settings, note)`` - never raises."""
    return local_settings.load(LOCAL_FILE_ID, default_settings, normalize, path=path)


def save(settings, path=None):
    """``(ok, error_text)`` - never raises."""
    return local_settings.save(LOCAL_FILE_ID, settings, normalize, SCHEMA_VERSION, path=path)
