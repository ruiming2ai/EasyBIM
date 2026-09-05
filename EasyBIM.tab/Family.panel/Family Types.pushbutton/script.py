# -*- coding: utf-8 -*-
"""Family Types - the family's type table as an editable grid.

Two ways in. In the family editor the active document *is* the family, so the
edits go straight into it and the user saves the .rfa as usual. In a project
the user picks a loaded family, ``EditFamily`` opens it behind the window, and
Apply writes the changes and reloads the family - asking the same "Family
Already Exists" question a manual load asks.
"""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

import os
import sys

import clr

clr.AddReference("RevitAPIUI")

from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectType

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from pyrevit import forms
from pyrevit import revit
from pyrevit import script

from easybim.compat import safe_text
from easybim.family_load_options import FamilyLoadOptions
from easybim.family_load_options import build_overwrite_prompt
from easybim.family_selection_revit import application_from
from easybim.family_selection_revit import close_family_doc
from easybim.family_selection_revit import collect_families
from easybim.family_selection_revit import edit_family
from easybim.family_selection_revit import FamilySelectionFilter
from easybim.family_selection_revit import family_from_element
from easybim.family_selection_revit import is_family_document
from easybim.family_selection_revit import is_transferable_family

import family_types_revit as ftrevit
import family_types_ui as ftui


__title__ = "Family Types"

LOGGER = script.get_logger()


def _family_label(family):
    name = safe_text(getattr(family, "Name", u""))
    category = u""
    try:
        category = safe_text(family.FamilyCategory.Name)
    except Exception:
        category = u""
    return name, category or u"Other"


def _family_document_is_open(doc, family_name):
    """Is this family already being edited in its own window?

    ``EditFamily`` on such a family hands back the document the user is
    editing, and closing it would end their edit session - so when the answer
    is yes, this command leaves the document alone on the way out.
    """
    app = application_from(None, doc)
    if app is None:
        return False
    wanted = safe_text(family_name).strip().lower()
    if not wanted:
        return False
    try:
        documents = list(app.Documents)
    except Exception:
        return False
    for document in documents:
        if not is_family_document(document):
            continue
        title = safe_text(getattr(document, "Title", u""))
        if title.lower().endswith(u".rfa"):
            title = title[:-4]
        if title.strip().lower() == wanted:
            return True
    return False


def _family_options(doc):
    """Every editable loaded family, labelled for the picker."""
    options = []
    by_label = {}
    for family in collect_families(doc):
        if not is_transferable_family(family):
            continue
        name, category = _family_label(family)
        if not name:
            continue
        label = name
        # Two categories can hold a family of the same name; keep both
        # reachable by disambiguating the later one.
        if label in by_label:
            label = u"{0}  ({1})".format(name, category)
        by_label[label] = family
        options.append((label, category, family))

    return sorted(options, key=lambda item: (item[1].lower(),
                                               item[0].lower()))


def _is_cancelled_pick(error):
    if isinstance(error, OperationCanceledException):
        return True
    return "cancel" in safe_text(error).lower()


def _pick_family_in_model(doc, uidoc):
    """Click one eligible element and return its editable loadable family."""
    try:
        reference = uidoc.Selection.PickObject(
            ObjectType.Element,
            FamilySelectionFilter(),
            "Select one element to edit its family types",
        )
    except Exception as error:
        if _is_cancelled_pick(error):
            return None
        forms.alert("Could not select an element in the model.",
                    title=__title__, expanded=safe_text(error))
        return None

    if reference is None:
        return None
    try:
        element = doc.GetElement(reference.ElementId)
    except Exception:
        element = None
    return family_from_element(element)


def _pick_family(doc, uidoc):
    """Choose a loaded family or click one element in the active project."""
    options = _family_options(doc)

    if not options:
        forms.alert("This project has no editable loadable families.",
                    title=__title__)
        return None

    while True:
        window = ftui.FamilyPickerWindow(ftui.PICKER_XAML, options)
        window.ShowDialog()
        if window.result == "open":
            return window.selected_family
        if window.result != "model":
            return None

        family = _pick_family_in_model(doc, uidoc)
        if family is not None:
            return family


def _open_window(family_doc, family_name, context_text):
    columns, rows, notes = ftrevit.read_family_table(
        family_doc, row_factory=ftui.make_row)
    if not rows:
        forms.alert("This family has no family types to show.",
                    title=__title__)
        return None
    window = ftui.FamilyTypesWindow(
        ftui.MAIN_XAML, columns, rows, family_name, context_text, notes)
    window.ShowDialog()
    return window


def _report(result, family_name, loaded_into):
    lines = [u"{0}: {1}.".format(family_name, result.text())]
    if loaded_into:
        if result.loaded == u"overwritten":
            lines.append(u"Reloaded into {0}.".format(loaded_into))
        elif result.loaded == u"declined":
            lines.append(u"Not reloaded into {0} - the overwrite was "
                         u"declined, so the project still holds the old "
                         u"family.".format(loaded_into))
        elif result.loaded == u"failed":
            lines.append(u"Could not reload into {0}.".format(loaded_into))
    else:
        lines.append(u"Save the family to keep the changes.")
    forms.alert(u"\n".join(lines), title=__title__,
                expanded=u"\n".join(result.problems) or None,
                warn_icon=bool(result.problems))


def _run_family_document(family_doc, family_name, context_text,
                         project_doc=None):
    """Show the window and apply what it staged. -> True to run again."""
    window = _open_window(family_doc, family_name, context_text)
    if window is None:
        return False
    if window.result == "reload":
        return True
    if window.result != "apply":
        return False

    result = ftrevit.apply_changes(family_doc, window.staged_changes)
    loaded_into = u""
    if project_doc is not None:
        loaded_into = safe_text(getattr(project_doc, "Title", u""))
        options = FamilyLoadOptions(ask=build_overwrite_prompt())
        ftrevit.load_back_into_project(family_doc, project_doc, family_name,
                                       result, options)
    _report(result, family_name, loaded_into)
    return False


def main():
    # Not forms.check_modeldoc: that one rejects a family document, and the
    # family editor is half of what this button is for. Both kinds are fine;
    # only "no document at all" is not.
    doc = revit.doc
    if doc is None:
        forms.alert("Open a project or a family first.", title=__title__)
        return

    if getattr(doc, "IsFamilyDocument", False):
        family_name = ftrevit.family_display_name(doc)
        while _run_family_document(doc, family_name,
                                   u"editing this family document"):
            pass
        return

    family = _pick_family(doc, revit.uidoc)
    if family is None:
        return
    family_name = safe_text(getattr(family, "Name", u""))

    was_already_open = _family_document_is_open(doc, family_name)

    family_doc = None
    try:
        family_doc = edit_family(doc, family)
    except Exception as error:
        forms.alert("Could not open '{0}' for editing.".format(family_name),
                    title=__title__, expanded=str(error))
        return
    if family_doc is None:
        forms.alert("Could not open '{0}' for editing.".format(family_name),
                    title=__title__)
        return

    try:
        while _run_family_document(
                family_doc, family_name,
                u"loaded in {0}".format(safe_text(getattr(doc, "Title", u""))),
                project_doc=doc):
            pass
    finally:
        # Close only what this run opened. A family the user already had open
        # in the editor is theirs, and Close(False) would end that session.
        if not was_already_open:
            try:
                close_family_doc(family_doc)
            except Exception as error:
                LOGGER.debug("Could not close the family document: %s", error)


if __name__ == "__main__":
    main()
