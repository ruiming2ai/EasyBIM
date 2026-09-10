# -*- coding: utf-8 -*-
"""Damper Check - every HVAC end branch that has no isolation damper (read-only)."""

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

__title__ = "Damper Check"

# The report window outlives this run and its Show/Refresh buttons reach
# Revit through an ExternalEvent whose handler is Python. A recycled engine
# would kill both, so the button keeps its engine (the Sheet Manager rule).
__persistentengine__ = True

# Must equal damper_check_ui.ACTIVE_ENVVAR (test-pinned).
ACTIVE_ENVVAR = "EASYBIM_DAMPER_CHECK_ACTIVE"
STALE_MODULES = (
    "damper_check_ui",
    "damper_check_state",
    "damper_check_settings",
    "easybim.duct_network_revit",
    "easybim.duct_network_state",
    "easybim.type_checklist",
    "easybim.local_settings",
    "easybim.check_windows",
    "easybim.external_events",
)

logger = script.get_logger()


def _drop_stale_modules():
    """Let an extension update take effect without restarting Revit.

    ``__persistentengine__`` keeps this engine's ``sys.modules`` alive across
    a pyRevit reload, so updated Damper Check modules would never be
    re-imported. Dropping them is only safe while no report is open, so the
    flag is read from the pyRevit envvar store rather than from the module
    we may be about to replace (the Sheet Manager / Clash Detection approach).
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
        logger.debug("Reloaded %s Damper Check module(s).", dropped)
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


def _scan(doc, options, config, drevit, dstate, progress_session):
    """The one read pass under a cancellable progress bar, then the walk."""
    with progress_session("Reading duct connectors...", cancellable=True) as progress:
        def _tick(done, total):
            progress.update(done, total)
            return not progress.cancelled

        snapshot = drevit.scan(doc, options, progress=_tick)
    return dstate.analyze(snapshot, config)


def main():
    forms.check_modeldoc(exitscript=True)
    if getattr(revit.doc, "IsFamilyDocument", False):
        forms.alert(
            "Damper Check requires an open project document.",
            title=__title__,
            exitscript=True,
        )
    _drop_stale_modules()

    import damper_check_settings as dsettings
    import damper_check_state as dstate
    import damper_check_ui as dui
    from easybim import duct_network_revit as drevit
    from easybim import external_events
    from easybim.progress import ProgressSession

    doc = revit.doc
    # A new check replaces the previous report rather than stacking windows.
    dui.close_open_window()

    settings, note = dsettings.load()
    with forms.ProgressBar(title="Listing duct families...", indeterminate=True):
        catalog = drevit.collect_types(doc)

    setup = dui.SetupWindow(catalog, settings, note=note)
    setup.ShowDialog()
    if not setup.result:
        return
    settings = setup.result
    ok, error = dsettings.save(settings)
    if not ok:
        logger.warning("Damper Check settings not saved: %s", error)

    config = dstate.config_from_settings(settings)
    view = catalog.get("view") or {}
    options = {
        "scope": config["scope"],
        "view_id": view.get("id"),
        "view_name": view.get("name"),
    }

    analysis = _scan(doc, options, config, drevit, dstate, ProgressSession)

    # Created here, inside the command run, while an API context still exists.
    bridge = external_events.ExternalEventBridge("EasyBIM Damper Check")
    ready = bridge.create()

    def rescan(uiapp):
        del uiapp
        if not getattr(doc, "IsValidObject", True):
            raise dui.DocumentGone()
        return dstate.analyze(drevit.scan(doc, options), config)

    def show(uiapp, element_ids):
        uidoc = getattr(uiapp, "ActiveUIDocument", None) if uiapp is not None else None
        if uidoc is None:
            uidoc = revit.uidoc
        return drevit.show_elements(uidoc, element_ids)

    dui.show_results(
        analysis,
        config,
        settings,
        bridge=bridge if ready else None,
        uiapp=_uiapp(),
        rescan=rescan,
        show=show,
        save_settings=dsettings.save,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as run_error:
        logger.exception("Damper Check failed.")
        forms.alert("Damper Check failed:\n{0}".format(run_error), title=__title__)
