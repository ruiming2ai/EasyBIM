# -*- coding: utf-8 -*-
"""Sheet Manager - bulk sheet, revision, and parameter editor (modeless)."""

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

__title__ = "Sheet Manager"

# The window outlives this run and its buttons reach Revit through an
# ExternalEvent whose handler is Python. A recycled engine would kill both
# (AGENTS decision 2026-08-02: any event-owning button needs this flag).
__persistentengine__ = True

# Must equal sheet_manager_ui.ACTIVE_ENVVAR (test-pinned).
ACTIVE_ENVVAR = "EASYBIM_SHEET_MANAGER_ACTIVE"
STALE_MODULES = (
    "sheet_manager_ui",
    "sheet_manager_revit",
    "sheet_manager_state",
    "sheet_manager_dialogs",
    "sheet_manager_xlsx",
    "easybim.external_events",
)

logger = script.get_logger()


def _drop_stale_modules():
    """Let an extension update take effect without restarting Revit.

    ``__persistentengine__`` keeps this engine's ``sys.modules`` alive across
    a pyRevit reload, so updated Sheet Manager modules would never be
    re-imported. Dropping them is only safe while no window is open, so the
    flag is read from the pyRevit envvar store rather than from the module
    we may be about to replace (same approach as Clash Detection Mode).
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
        logger.debug("Reloaded %s Sheet Manager module(s).", dropped)
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


def main():
    forms.check_modeldoc(exitscript=True)
    if getattr(revit.doc, "IsFamilyDocument", False):
        forms.alert(
            "Sheet Manager requires an open project document.",
            title=__title__,
            exitscript=True
        )
    _drop_stale_modules()

    import sheet_manager_ui

    if sheet_manager_ui.activate_open_window():
        return
    revit.selection.get_selection().clear()
    sheet_manager_ui.show_sheet_manager(uiapp=_uiapp())


if __name__ == "__main__":
    main()
