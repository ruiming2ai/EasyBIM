# -*- coding: utf-8 -*-
"""Read-only diagnostic for Revit link display settings."""

from __future__ import print_function

import os
import sys
import time

from pyrevit import forms
from pyrevit import revit
from pyrevit import script


SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)


logger = script.get_logger()
TITLE = "Link Probe"

SETUP_MESSAGE = (
    "Link Probe reads your model and writes nothing. No transaction is "
    "opened, so it is safe to run on a live project.\n\n"
    "Before you continue, set one view up:\n\n"
    "1. Open a plan view that shows a loaded Revit link.\n"
    "2. In Visibility/Graphics, open that link's Display Settings and "
    "choose Custom.\n"
    "3. On the Model Categories tab, set the tab itself to Custom and "
    "untick one obvious category - Furniture is a good one.\n"
    "4. Close the dialogs, keeping that view active.\n\n"
    "You will be asked which category you unticked.\n\n"
    "The geometry pass can take a while on a large architectural link. "
    "It stops at 250,000 objects or 90 seconds and says so in the report.\n\n"
    "Continue?"
)


def main():
    forms.check_modeldoc(exitscript=True)

    import link_probe_report as report
    import link_probe_revit as probe

    doc = revit.doc
    blocker = probe.document_blocker(doc)
    if blocker:
        forms.alert(blocker, title=TITLE, exitscript=True)

    if not forms.alert(SETUP_MESSAGE, title=TITLE, yes=True, no=True):
        return

    view = doc.ActiveView
    if view is None:
        forms.alert("No active view.", title=TITLE, exitscript=True)

    timings = []
    errors = []

    def timed(label, func):
        started = time.time()
        try:
            return func()
        except Exception as ex:
            logger.exception("%s failed.", label)
            errors.append(u"{0}: {1}".format(label, ex))
            return None
        finally:
            timings.append((label, time.time() - started))

    options = timed("Collect links", lambda: probe.collect_link_options(doc)) or []
    if not options:
        forms.alert("No Revit links in this model.", title=TITLE, exitscript=True)

    payload = {
        "host": timed("Read host facts",
                      lambda: probe.read_host_facts(doc, doc.Application)) or {},
        "view": timed("Read view facts",
                      lambda: probe.read_view_facts(doc, view)) or {},
        "links": [option.as_payload() for option in options],
        "bindings": [],
        "geometry": {},
        "linked_view_scan": {},
        "timings": timings,
        "errors": errors,
    }

    def read_bindings():
        return [probe.read_link_basics(doc, view, option) for option in options]

    payload["bindings"] = timed("Read link display settings", read_bindings) or []

    loaded = [option for option in options if option.loaded]
    chosen = None
    if len(loaded) == 1:
        chosen = loaded[0]
    elif loaded:
        picked = forms.SelectFromList.show(
            [option.display for option in loaded],
            title="Which link did you customize?",
            button_name="Use this link",
            multiselect=False,
        )
        for option in loaded:
            if option.display == picked:
                chosen = option
                break

    if chosen is not None:
        names = probe.link_category_names(chosen)
        target = forms.SelectFromList.show(
            names,
            title="Which category did you untick inside the link?",
            button_name="That one",
            multiselect=False,
        ) if names else None

        if target:
            def run_experiment():
                with forms.ProgressBar(title="Link Probe: walking geometry...",
                                       indeterminate=True):
                    return probe.run_geometry_experiment(
                        doc, view, chosen, target)

            payload["geometry"] = timed("Geometry experiment",
                                        run_experiment) or {}

        linked_view_name = u""
        for binding in payload["bindings"]:
            if binding.get("link_label") == chosen.label:
                linked_view_name = binding.get("linked_view_name") or u""
                break
        if linked_view_name:
            payload["linked_view_scan"] = timed(
                "Read the linked view",
                lambda: probe.scan_linked_view(chosen, linked_view_name)) or {}

    output = script.get_output()
    output.set_title(TITLE)
    print(report.build_report(payload))


if __name__ == "__main__":
    main()
