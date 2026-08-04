# -*- coding: utf-8 -*-
"""Transfer the highest equipment amp rating onto its circuit's ratings."""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

import os
import sys

from pyrevit import forms
from pyrevit import revit
from pyrevit import script

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

import circuit_rating_revit as crevit
import circuit_rating_state as state
import circuit_rating_ui as ui

__title__ = "Update Circuit Rating"

LOGGER = script.get_logger()


def _run():
    doc = revit.doc

    with forms.ProgressBar(title="Reading circuits...", indeterminate=True):
        circuits_data, source_options, target_options = crevit.scan_model(doc)

    if not circuits_data:
        forms.alert("This model has no electrical circuits.", title=__title__)
        return
    if not source_options:
        forms.alert(
            "No usable parameter was found on any circuited element.\n\n"
            "The rating has to come from a numeric parameter carried by the "
            "elements on the circuit.",
            title=__title__)
        return
    if not target_options:
        forms.alert(
            "No writable numeric parameter was found on the circuits.",
            title=__title__)
        return

    result = ui.show_update_circuit_rating(
        circuits_data, source_options, target_options)
    if not result:
        return

    plan = result["plan"]
    target_labels = result["target_labels"]
    results = crevit.apply_ratings(doc, plan, target_labels)
    summary = state.summarize(plan, results)

    message = "Updated {0} of {1} circuit(s).".format(
        summary["updated"], summary["attempted"])
    if summary["failed"]:
        message += "\n{0} circuit(s) could not be written.".format(
            summary["failed"])
    forms.alert(message, expanded=summary["detail"], title=__title__)


forms.check_modeldoc(exitscript=True)
if getattr(revit.doc, "IsFamilyDocument", False):
    forms.alert("Update Circuit Rating requires an open project document.",
                exitscript=True)

try:
    _run()
except Exception as run_error:
    LOGGER.exception("Update Circuit Rating failed.")
    forms.alert("Update Circuit Rating failed:\n{0}".format(run_error),
                title=__title__)
