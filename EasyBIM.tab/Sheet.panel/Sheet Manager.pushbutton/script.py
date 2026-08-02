# -*- coding: utf-8 -*-
"""Sheet Manager - bulk sheet, revision, and parameter editor."""

from __future__ import print_function

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from pyrevit import forms
from pyrevit import revit

__title__ = "Sheet Manager"

forms.check_modeldoc(exitscript=True)
if getattr(revit.doc, "IsFamilyDocument", False):
    forms.alert(
        "Sheet Manager requires an open project document.",
        exitscript=True
    )
revit.selection.get_selection().clear()

import sheet_manager_ui

sheet_manager_ui.show_sheet_manager()
