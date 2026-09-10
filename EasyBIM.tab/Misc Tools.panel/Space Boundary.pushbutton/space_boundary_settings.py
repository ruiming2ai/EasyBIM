# -*- coding: utf-8 -*-
"""Space Boundary's settings: the schema, and nothing else.

The file itself is ``easybim.local_settings``; this module owns only what the
defaults are and how a file of any age reads back into them.  Everything
travels by name - the filled region type, the views, the sources - because a
name still means something in the next model and an ElementId does not.

The relationship between a region and its room is a different thing entirely
and does not live here: it is written into the model, on each region, by
``space_boundary_storage``.  This file is only what the setup window was last
set to.  No Revit imports.
"""

from __future__ import print_function

from easybim import local_settings
from easybim.local_settings import name_list as _name_list

import space_boundary_state as state


SCHEMA_VERSION = 1
LOCAL_FILE_ID = "EasyBIM_SpaceBoundary_settings"

MIN_GRID_MM = 1
MAX_GRID_MM = 25


def _safe_text(value):
    if value is None:
        return u""
    try:
        return u"{0}".format(value)
    except Exception:
        return u""


def _one_of(value, allowed, fallback):
    text = _safe_text(value).strip()
    return text if text in allowed else fallback


def default_settings():
    return {
        "schema": SCHEMA_VERSION,
        "kind": state.KIND_ROOM,
        "scope": state.SCOPE_ACTIVE,
        "subject": state.SUBJECT_ALL,
        "boundary_source": "document",
        "boundary_override": "Finish",
        "region_type_name": u"",
        "source_keys": [],
        "unticked_source_keys": [],
        "view_names": [],
        "unticked_view_names": [],
        "replace_existing": False,
        "include_design_options": False,
        "grid_mm": state.GRID_MM,
    }


def normalize(raw):
    """Whatever was on disk -> a settings dict this build can use.

    Unknown keys are dropped and a wrong type falls back to the default: the
    worst outcome of a stale file must be re-ticking the setup, never a
    broken command.
    """
    settings = default_settings()
    if not isinstance(raw, dict):
        return settings
    settings["kind"] = _one_of(raw.get("kind"), (state.KIND_ROOM, state.KIND_SPACE),
                               state.KIND_ROOM)
    settings["scope"] = _one_of(raw.get("scope"), (state.SCOPE_ACTIVE, state.SCOPE_SELECTED),
                                state.SCOPE_ACTIVE)
    settings["subject"] = _one_of(raw.get("subject"), (state.SUBJECT_ALL, state.SUBJECT_ONE),
                                  state.SUBJECT_ALL)
    settings["boundary_source"] = _one_of(raw.get("boundary_source"), ("document", "override"),
                                          "document")
    settings["boundary_override"] = _one_of(raw.get("boundary_override"),
                                            state.BOUNDARY_LOCATIONS, "Finish")
    settings["region_type_name"] = _safe_text(raw.get("region_type_name")).strip()
    settings["source_keys"] = _name_list(raw.get("source_keys"))
    settings["unticked_source_keys"] = _name_list(raw.get("unticked_source_keys"))
    settings["view_names"] = _name_list(raw.get("view_names"))
    settings["unticked_view_names"] = _name_list(raw.get("unticked_view_names"))
    settings["replace_existing"] = bool(raw.get("replace_existing"))
    settings["include_design_options"] = bool(raw.get("include_design_options"))
    try:
        grid = int(raw.get("grid_mm", state.GRID_MM))
    except Exception:
        grid = int(state.GRID_MM)
    settings["grid_mm"] = max(MIN_GRID_MM, min(MAX_GRID_MM, grid))
    return settings


def local_path():
    return local_settings.local_path(LOCAL_FILE_ID)


def load(path=None):
    """``(settings, note)`` - never raises."""
    return local_settings.load(LOCAL_FILE_ID, default_settings, normalize, path=path)


def save(settings, path=None):
    """``(ok, error_text)`` - never raises."""
    return local_settings.save(LOCAL_FILE_ID, settings, normalize, SCHEMA_VERSION, path=path)
