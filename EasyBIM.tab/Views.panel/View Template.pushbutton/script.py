# -*- coding: utf-8 -*-
"""Batch transfer selected settings between view templates."""

import os
import sys

from pyrevit import forms
from pyrevit import revit
from pyrevit import script


SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from view_template_transfer_ui import BatchTransferViewTemplateSettingsWindow


logger = script.get_logger()
XAML_FILE = "BatchTransferViewTemplateSettings.xaml"
TITLE = "Batch Transfer View Template Settings"


def main():
    doc = revit.doc
    if doc is None:
        forms.alert("Open a Revit project before running this command.", title=TITLE)
        return

    try:
        window = BatchTransferViewTemplateSettingsWindow(XAML_FILE, doc)
        if getattr(window, "is_ready", False):
            window.ShowDialog()
    except Exception as ex:
        logger.exception("Batch Transfer View Template Settings failed.")
        forms.alert(str(ex), title=TITLE)


if __name__ == "__main__":
    main()
