# -*- coding: utf-8 -*-
"""Clash Detection Mode - live, forward-only interference checking."""
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


__title__ = "Clash Detection Mode"

# The engine keeps live Revit event delegates alive long after this command
# returns.  Without a persistent engine pyRevit can recycle the engine that
# owns those Python callbacks, and the mode would silently stop detecting.
__persistentengine__ = True

TITLE = "Clash Detection Mode"
logger = script.get_logger()


CLASH_MODULE_PREFIX = "easybim.clash_detection"
ACTIVE_ENVVAR = "EASYBIM_CLASH_DETECTION_ACTIVE"


def _drop_stale_modules():
    """Let an extension update take effect without restarting Revit.

    ``__persistentengine__`` keeps this engine's ``sys.modules`` alive across
    a pyRevit reload - that is what stops the live Revit event delegates from
    dying with the engine.  The cost is that updated ``easybim.clash_detection*``
    modules would never be re-imported, so pulling a new version and reloading
    pyRevit (the flow the README documents) would keep running the old code.

    Dropping them here is only safe while nothing is live, so the active flag
    is read straight from the pyRevit envvar store: importing the engine
    module to ask it would be importing the very code we may need to replace.
    """
    try:
        if script.get_envvar(ACTIVE_ENVVAR):
            return False
    except Exception:
        return False

    dropped = []
    for name in list(sys.modules):
        if name.startswith(CLASH_MODULE_PREFIX) or name == "easybim.wpf_notify":
            try:
                del sys.modules[name]
                dropped.append(name)
            except Exception:
                pass
    if dropped:
        logger.debug("Reloaded %s clash module(s) after an update.", len(dropped))
    return bool(dropped)


def _uiapp():
    if HOST_APP is not None:
        try:
            return HOST_APP.uiapp
        except Exception:
            pass
    try:
        return __revit__  # noqa: F821 - pyRevit injects this
    except Exception:
        return None


def main():
    forms.check_modeldoc(exitscript=True)
    if getattr(revit.doc, "IsFamilyDocument", False):
        forms.alert(
            "Clash Detection Mode requires an open project document.",
            title=TITLE,
            exitscript=True,
        )

    _drop_stale_modules()

    try:
        # One window, always.  When a session is running it carries the live
        # controls, which is what makes a closed panel recoverable and gives
        # Stop Detection a home that cannot disappear.
        from easybim import clash_detection_setup

        clash_detection_setup.show_setup_window(revit.doc, uiapp=_uiapp())
    except Exception as ex:
        logger.exception("Clash Detection Mode failed.")
        forms.alert("Clash Detection Mode failed:\n{0}".format(ex), title=TITLE)


if __name__ == "__main__":
    main()
