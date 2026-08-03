# -*- coding: utf-8 -*-
"""Transfer view or view template settings to views and view templates."""

import os
import sys

from pyrevit import forms
from pyrevit import revit
from pyrevit import script


SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)


logger = script.get_logger()
TITLE = "View Template"


def main():
    forms.check_modeldoc(exitscript=True)
    if getattr(revit.doc, "IsFamilyDocument", False):
        forms.alert(
            "View Template requires an open project document.",
            title=TITLE,
            exitscript=True,
        )

    import view_template_transfer_ui

    try:
        view_template_transfer_ui.show_window(revit.doc)
    except Exception as ex:
        logger.exception("View Template transfer failed.")
        forms.alert(str(ex), title=TITLE)


if __name__ == "__main__":
    main()
