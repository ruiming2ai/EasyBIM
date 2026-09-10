# -*- coding: utf-8 -*-
"""Fire Damper Check - every duct crossing a rated barrier without a fire damper (read-only)."""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from pyrevit import forms
from pyrevit import revit
from pyrevit import script

try:
    from pyrevit import HOST_APP
except Exception:
    HOST_APP = None

__title__ = "Fire Damper Check"

# The report window outlives this run and its Show/Refresh buttons reach
# Revit through an ExternalEvent whose handler is Python. A recycled engine
# would kill both, so the button keeps its engine (the Sheet Manager rule).
__persistentengine__ = True

# Must equal fire_damper_check_ui.ACTIVE_ENVVAR (test-pinned).
ACTIVE_ENVVAR = "EASYBIM_FIRE_DAMPER_CHECK_ACTIVE"

#: Which list in the model's shared record belongs to this tool.
TOOL_KEY = "fire_damper_check"

STALE_MODULES = (
    "fire_damper_check_ui",
    "fire_damper_check_revit",
    "fire_damper_check_state",
    "fire_damper_check_settings",
    "easybim.link_crossings",
    "easybim.duct_network_revit",
    "easybim.duct_network_state",
    "easybim.type_checklist",
    "easybim.local_settings",
    "easybim.check_windows",
    "easybim.model_store",
    "easybim.external_events",
)

logger = script.get_logger()


def _drop_stale_modules():
    """Let an extension update take effect without restarting Revit.

    ``__persistentengine__`` keeps this engine's ``sys.modules`` alive across
    a pyRevit reload, so updated modules would never be re-imported.
    Dropping them is only safe while no report is open, so the flag is read
    from the pyRevit envvar store rather than from the module we may be
    about to replace (the Sheet Manager / Clash Detection approach).
    """
    try:
        if script.get_envvar(ACTIVE_ENVVAR):
            return False
    except Exception:
        return False
    dropped = 0
    for name in list(sys.modules):
        if name in STALE_MODULES:
            sys.modules.pop(name, None)
            dropped += 1
    if dropped:
        logger.debug("Reloaded %s Fire Damper Check module(s).", dropped)
    return bool(dropped)


def _uiapp():
    if HOST_APP is not None:
        try:
            uiapp = HOST_APP.uiapp
            if uiapp is not None:
                return uiapp
        except Exception:
            pass
    try:
        return __revit__  # noqa: F821 - pyRevit runtime global
    except Exception:
        return None


def _make_scan(doc, catalog, settings, config, frevit, fstate, model_store):
    """The two-phase pass as one callable: ducts, then crossings, then verdicts."""
    view = catalog.get("view") or {}
    link_keys = [link.get("key") for link in catalog.get("links") or []]
    choices = fstate.choices_by_key(settings, link_keys)

    def _scan(progress=None):
        snapshot = frevit.scan_network(
            doc, config["damper_types"], scope=config["scope"],
            view_id=view.get("id"), view_name=view.get("name"), progress=progress)
        scope_ids = None
        if config["scope"] == fstate.SCOPE_VIEW:
            scope_ids = frevit.duct_ids_in_view(doc, view.get("id"))
        segments_info = fstate.segments_from_snapshot(snapshot, config, scope_ids=scope_ids)
        options = {
            "include_floors": config["include_floors"],
            "host_extent": frevit.host_extent(segments_info["segments"]),
            "band_mode": config["band_mode"],
            "band_height_ft": config["band_height_ft"],
            "min_storey_ft": config["min_storey_ft"],
            "line_thickness_ft": config["line_thickness_ft"],
            "min_penetration_ft": config["min_penetration_ft"],
        }
        contexts, index = frevit.build_index(doc, choices, options)
        crossings = frevit.find_crossings(index, segments_info["segments"], options, progress=progress)
        description = frevit.describe(contexts, index)
        analysis = fstate.analyze(snapshot, crossings, description, config, segments_info=segments_info)
        analysis["links"] = description.get("links") or []
        # What the reviewer set aside last time, read back out of the model.
        analysis["ignored"] = sorted(model_store.read(doc, TOOL_KEY))
        return analysis

    return _scan


def main():
    forms.check_modeldoc(exitscript=True)
    if getattr(revit.doc, "IsFamilyDocument", False):
        forms.alert(
            "Fire Damper Check requires an open project document.",
            title=__title__,
            exitscript=True,
        )
    _drop_stale_modules()

    import fire_damper_check_revit as frevit
    import fire_damper_check_settings as fsettings
    import fire_damper_check_state as fstate
    import fire_damper_check_ui as fui
    from easybim import external_events
    from easybim import model_store
    from easybim.progress import ProgressSession

    doc = revit.doc
    # A new check replaces the previous report rather than stacking windows.
    fui.close_open_window()

    settings, note = fsettings.load()
    with forms.ProgressBar(title="Listing links, walls and duct families...", indeterminate=True):
        catalog = frevit.collect_catalog(doc)

    setup = fui.SetupWindow(catalog, settings, note=note)
    setup.ShowDialog()
    if not setup.result:
        return
    settings = setup.result
    ok, error = fsettings.save(settings)
    if not ok:
        logger.warning("Fire Damper Check settings not saved: %s", error)

    config = fstate.config_from_settings(settings)
    scan = _make_scan(doc, catalog, settings, config, frevit, fstate, model_store)

    with ProgressSession("Reading ducts and crossing the rated barriers...", cancellable=True) as progress:
        def _tick(done, total):
            progress.update(done, total)
            return not progress.cancelled

        analysis = scan(progress=_tick)

    # Created here, inside the command run, while an API context still exists.
    bridge = external_events.ExternalEventBridge("EasyBIM Fire Damper Check")
    ready = bridge.create()

    def rescan(uiapp):
        del uiapp
        if not getattr(doc, "IsValidObject", True):
            raise fui.DocumentGone()
        return scan()

    def ignore(uiapp, key, on):
        """Set one finding aside, or put it back. The record lives in the
        model, so this is the one write either checker makes."""
        del uiapp
        if not getattr(doc, "IsValidObject", True):
            raise fui.DocumentGone()
        return model_store.set_ignored(doc, TOOL_KEY, key, on)

    def show(uiapp, show_record):
        uidoc = getattr(uiapp, "ActiveUIDocument", None) if uiapp is not None else None
        if uidoc is None:
            uidoc = revit.uidoc
        return frevit.show(uidoc, show_record)

    fui.show_results(
        analysis,
        config,
        settings,
        bridge=bridge if ready else None,
        uiapp=_uiapp(),
        rescan=rescan,
        show=show,
        save_settings=fsettings.save,
        ignore=ignore,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as run_error:
        logger.exception("Fire Damper Check failed.")
        forms.alert("Fire Damper Check failed:\n{0}".format(run_error), title=__title__)
