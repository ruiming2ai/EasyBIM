# -*- coding: utf-8 -*-
"""Independent Copy Monitor. Executes only when the ribbon command is invoked."""
import imp
import os
import sys
from pyrevit import forms, revit, DB
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from easybim import independent_placement_revit as adapter
from easybim import copy_monitor_revit as monitor

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path: sys.path.insert(0, SCRIPT_DIR)
from copy_monitor_ui import ReviewWindow

__title__ = "Copy Monitor"


class FamilyFilter(ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, DB.FamilyInstance) and adapter.supported_category(element)
    def AllowReference(self, reference, point):
        return False


def pair_existing():
    uidoc, doc = revit.uidoc, revit.doc
    linked_ref = uidoc.Selection.PickObject(ObjectType.LinkedElement,
                                          "Select the linked MEP or Generic Model instance to monitor")
    link = doc.GetElement(linked_ref.ElementId)
    if not isinstance(link, DB.RevitLinkInstance) or link.GetLinkDocument() is None:
        raise ValueError("Select an instance in a loaded direct RVT link.")
    source = link.GetLinkDocument().GetElement(linked_ref.LinkedElementId)
    local_ref = uidoc.Selection.PickObject(ObjectType.Element, FamilyFilter(),
                                           "Select its corresponding instance in this project")
    destination = doc.GetElement(local_ref.ElementId)
    result = monitor.monitor_existing(doc, link, source, destination)
    if not result["ok"]:
        forms.alert(result["error"], title=__title__)
    else:
        reason = adapter.independent_reason(destination)
        message = "Monitoring relationship saved."
        if reason:
            message += "\n\nConversion required before automatic movement: " + reason
        forms.alert(message, title=__title__)


def copy_and_monitor():
    bundle = os.path.join(os.path.dirname(SCRIPT_DIR), "Batch Duplicate Host.pushbutton")
    if bundle not in sys.path: sys.path.insert(0, bundle)
    command = imp.load_source("easybim_copy_monitor_copy_wizard", os.path.join(bundle, "script.py"))
    command._run(default_copy_original=True, command_title=__title__)


def check():
    with forms.ProgressBar(title="Copy Monitor: checking ({value} of {max_value})", cancellable=True) as bar:
        def progress(index, total):
            bar.update_progress(index,total)
            return not bar.cancelled
        return monitor.check_changes(revit.doc,progress)


def review():
    scan = check()
    while True:
        window = ReviewWindow(scan)
        window.ShowDialog()
        if not window.action:
            return
        if window.action == "show":
            from easybim.coordination_review_show import select_element, frame_element_in_active_view
            element = window.selected_reports[0].get("_destination_element")
            if element is not None and element.IsValidObject:
                select_element(revit.uidoc,element)
                frame_element_in_active_view(revit.uidoc,element)
            else:
                forms.alert("The destination is unavailable.",title=__title__)
            continue
        if window.action == "refresh":
            scan = check()
            continue
        with forms.ProgressBar(title="Copy Monitor: applying ({value} of {max_value})",cancellable=True) as bar:
            def progress(index,total):
                bar.update_progress(index,total)
                return not bar.cancelled
            results = monitor.apply_action(revit.doc,window.selected_reports,window.action,progress)
        failures = [r["error"] for r in results if not r["ok"]]
        succeeded = sum(1 for r in results if r["ok"])
        message = "Applied: {}\nFailed or cancelled: {}".format(succeeded,len(failures))
        if failures:
            message += "\n\n" + "\n".join(failures[:20])
        forms.alert(message,title=__title__)
        scan = check()


def main():
    if revit.doc is None:
        forms.alert("Open a project document.",title=__title__)
        return
    try:
        adapter.check_version(revit.doc)
        action = forms.CommandSwitchWindow.show(
            ["Copy and Monitor","Monitor Existing","Check Changes"],
            message="Copy Monitor - checks and updates run only on command")
        if action == "Copy and Monitor": copy_and_monitor()
        elif action == "Monitor Existing": pair_existing()
        elif action == "Check Changes": review()
    except OperationCanceledException:
        return
    except Exception as error:
        forms.alert(adapter.text(error),title=__title__)


if __name__ == "__main__":
    main()
