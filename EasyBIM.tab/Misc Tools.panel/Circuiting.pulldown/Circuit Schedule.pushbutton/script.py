# -*- coding: utf-8 -*-
"""Open the Circuit Schedule browser on the model's electrical distribution."""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

from pyrevit import forms
from pyrevit import revit
from pyrevit import script

from easybim import circuit_schedule_panel as panel
from easybim import circuit_schedule_revit as crevit
from easybim import external_events

__title__ = "Circuit Schedule"

LOGGER = script.get_logger()


def _bridge():
    """Reuse the live ExternalEvent, or make one while we still can.

    The pane outlives this command, so Revit will refuse a selection made from
    its own handlers.  ``create()`` is only valid inside a command run - which
    is exactly where we are - so every click on the button is also the pane's
    chance to reconnect after a pyRevit reload disposed the last event.
    """
    if panel.has_ready_bridge():
        return
    bridge = external_events.ExternalEventBridge("EasyBIM Circuit Schedule")
    if bridge.create():
        panel.attach_bridge(bridge)
    else:
        # The tree is still worth reading; only Show and Refresh go quiet.
        LOGGER.debug("ExternalEvent unavailable; Circuit Schedule opens "
                     "read-only.")


def _run():
    doc = revit.doc
    _bridge()

    with forms.ProgressBar(title="Reading circuits...", indeterminate=True):
        scan = crevit.scan_model(doc)

    if not panel.open_panel(scan):
        forms.alert("Circuit Schedule could not open its panel.",
                    title=__title__)
        return

    if not scan.get("circuits"):
        forms.alert("This model has no electrical circuits.", title=__title__)


forms.check_modeldoc(exitscript=True)
if getattr(revit.doc, "IsFamilyDocument", False):
    forms.alert("Circuit Schedule requires an open project document.",
                exitscript=True)

try:
    _run()
except Exception as run_error:
    LOGGER.exception("Circuit Schedule failed.")
    forms.alert("Circuit Schedule failed:\n{0}".format(run_error),
                title=__title__)
