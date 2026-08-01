# -*- coding: utf-8 -*-
"""Export selected schedules to Excel workbooks in displayed row order."""

# pylint: disable=import-error,invalid-name,broad-except
import os
import sys

import clr

clr.AddReference("RevitAPIUI")

from Autodesk.Revit.UI import TaskDialog

from pyrevit import coreutils
from pyrevit import forms
from pyrevit import revit
from pyrevit import script


SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

import schedule_export_xlsx
from schedule_export_revit import collect_schedule_options
from schedule_export_revit import get_display_ordered_elements
from schedule_export_state import ScheduleExportResult
from schedule_export_state import build_export_filename
from schedule_export_state import build_export_summary_text
from schedule_export_state import get_selected_options
from schedule_export_state import strip_rvt_suffix
from schedule_export_ui import ScheduleSelectionWindow


__title__ = "Export Schedules to Excel"

LOGGER = script.get_logger()


def _clean_filename(value):
    try:
        return coreutils.cleanup_filename(value, windows_safe=True)
    except Exception:
        return value


def _run():
    doc = revit.doc
    if doc is None:
        forms.alert(
            "Open a project document before running Export Schedules to Excel.",
            title=__title__,
        )
        return

    try:
        if bool(doc.IsFamilyDocument):
            forms.alert(
                "Run Export Schedules to Excel from a project document, "
                "not a family document.",
                title=__title__,
            )
            return
    except Exception:
        pass

    if not schedule_export_xlsx.XLSXWRITER_AVAILABLE:
        forms.alert(
            "The 'xlsxwriter' module is not available in this pyRevit "
            "installation. Update pyRevit and try again.",
            title=__title__,
        )
        return

    options = collect_schedule_options(doc)
    if not options:
        forms.alert(
            "No exportable schedules were found in this document.",
            title=__title__,
        )
        return

    window = ScheduleSelectionWindow("ScheduleSelectionWindow.xaml", options)
    window.ShowDialog()
    if window.result != "export":
        return

    selected_options = get_selected_options(options)
    if not selected_options:
        return

    folder_path = forms.pick_folder(
        title="Select destination folder for the Excel files"
    )
    if not folder_path:
        return

    model_name = strip_rvt_suffix(getattr(doc, "Title", ""))
    results = []
    for option in selected_options:
        try:
            elements, ordering_note = get_display_ordered_elements(
                doc, option.schedule
            )
            filename = build_export_filename(
                option.name, model_name, cleaner=_clean_filename
            )
            file_path = os.path.join(folder_path, filename)
            row_count = schedule_export_xlsx.export_schedule_to_xlsx(
                doc, option.schedule, elements, file_path
            )
            results.append(ScheduleExportResult(
                option.name,
                ok=True,
                path=file_path,
                row_count=row_count,
                ordering_note=ordering_note,
            ))
        except Exception as export_error:
            LOGGER.exception("Export failed for schedule: %s", option.name)
            results.append(ScheduleExportResult(
                option.name,
                ok=False,
                message=str(export_error),
            ))

    TaskDialog.Show(__title__, build_export_summary_text(results, folder_path))


try:
    _run()
except Exception as run_error:
    LOGGER.exception("Export Schedules to Excel failed.")
    forms.alert(
        "Export Schedules to Excel failed:\n{}".format(run_error),
        title=__title__,
    )
