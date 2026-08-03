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


def _handle_already_running(engine):
    answer = forms.alert(
        "Clash Detection Mode is already running.",
        title=TITLE,
        options=["Show Panel", "Restart With New Categories", "Stop Detection"],
    )
    if not answer:
        return True
    if answer == "Stop Detection":
        engine.stop(reason="ribbon button")
        return True
    if answer == "Show Panel":
        from easybim import clash_detection_panel

        clash_detection_panel.open_panel(engine.get_session())
        return True
    engine.stop(reason="restart with new categories")
    return False


def main():
    forms.check_modeldoc(exitscript=True)
    if getattr(revit.doc, "IsFamilyDocument", False):
        forms.alert(
            "Clash Detection Mode requires an open project document.",
            title=TITLE,
            exitscript=True,
        )

    from easybim import clash_detection_engine

    if clash_detection_engine.is_active():
        if _handle_already_running(clash_detection_engine):
            return

    import clash_detection_ui

    try:
        clash_detection_ui.show_setup_window(revit.doc, uiapp=_uiapp())
    except Exception as ex:
        logger.exception("Clash Detection Mode failed.")
        forms.alert("Clash Detection Mode failed:\n{0}".format(ex), title=TITLE)


if __name__ == "__main__":
    main()
