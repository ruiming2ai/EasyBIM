# -*- coding: utf-8 -*-
"""Export schedules to Excel and import edited values back."""

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

from easybim import excel_workbook

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

import import_excel_state
import schedule_export_xlsx
from import_excel_revit import apply_import_plan
from import_excel_revit import build_import_plan
from import_excel_revit import collect_scope_element_ids
from import_excel_ui import ImportConflictWindow
from import_excel_ui import ImportPreviewWindow
from schedule_export_revit import collect_schedule_options
from schedule_export_revit import get_display_ordered_elements
from schedule_export_state import ScheduleExportResult
from schedule_export_state import build_export_filename
from schedule_export_state import build_export_summary_text
from schedule_export_state import get_selected_options
from schedule_export_state import strip_rvt_suffix
from schedule_export_ui import ScheduleSelectionWindow


__title__ = "Excel"

LOGGER = script.get_logger()

IMPORT_FILE_FILTER = (
    "Excel Workbooks (*.xlsx;*.xlsm)|*.xlsx;*.xlsm|All files (*.*)|*.*"
)


def _clean_filename(value):
    try:
        return coreutils.cleanup_filename(value, windows_safe=True)
    except Exception:
        return value


def _run_export(doc, selected_options):
    folder_path = forms.pick_folder(
        title="Select destination folder for the Excel files"
    )
    if not folder_path:
        return

    model_name = strip_rvt_suffix(getattr(doc, "Title", ""))
    extension = ".xlsm" if schedule_export_xlsx.vba_available() else ".xlsx"
    results = []
    for option in selected_options:
        try:
            elements, ordering_note = get_display_ordered_elements(
                doc, option.schedule
            )
            filename = build_export_filename(
                option.name, model_name, cleaner=_clean_filename,
                extension=extension,
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


def _get_header_row(sheet_rows):
    """Return (excel_row_number, values) for the sheet's header row.

    Normally row 1, but an editor that drops a fully empty first row makes
    the header land lower; the row number is returned so the data parser
    skips exactly that row rather than assuming row 1.
    """
    for excel_row, values in sheet_rows:
        if excel_row == 1:
            return 1, values
    if sheet_rows:
        return sheet_rows[0][0], sheet_rows[0][1]
    return 1, []


def _run_import(doc, option):
    file_path = forms.pick_file(
        files_filter=IMPORT_FILE_FILTER, restore_dir=True, multi_file=False
    )
    if not file_path:
        return

    try:
        sheets = excel_workbook.read_workbook_sheets(
            file_path, ["Export", "_metadata"]
        )
    except excel_workbook.UnsupportedWorkbook as read_error:
        forms.alert(
            "Could not read the workbook:\n{}".format(read_error),
            title=__title__,
        )
        return

    export_sheet = sheets.get("Export")
    if export_sheet is None or not export_sheet.rows:
        forms.alert(
            "The workbook has no 'Export' sheet. It does not look like an "
            "EasyBIM/pyRevit parameter export.",
            title=__title__,
        )
        return

    metadata_sheet = sheets.get("_metadata")
    metadata = import_excel_state.parse_metadata_rows(
        metadata_sheet.rows if metadata_sheet is not None else []
    )

    header_row_number, header_values = _get_header_row(export_sheet.rows)
    try:
        id_col_index, columns, ignored_headers = \
            import_excel_state.classify_columns(header_values, metadata)
    except ValueError as header_error:
        forms.alert(str(header_error), title=__title__)
        return
    if not columns:
        forms.alert(
            "No importable parameter columns were found.", title=__title__
        )
        return

    records, bad_id_rows = import_excel_state.parse_export_rows(
        export_sheet.rows, columns,
        id_col_index=id_col_index, header_row=header_row_number,
    )
    if not records:
        forms.alert(
            "No data rows with ElementIds were found.", title=__title__
        )
        return

    member_ids = collect_scope_element_ids(doc, option.schedule)
    records, out_of_scope_rows = import_excel_state.split_records_by_scope(
        records, member_ids
    )
    if not records:
        forms.alert(
            "None of the workbook rows belong to schedule '{}'. "
            "Nothing to import.".format(option.name),
            title=__title__,
        )
        return

    plan = build_import_plan(
        doc, records, columns,
        bad_id_rows=bad_id_rows,
        ignored_headers=ignored_headers,
        out_of_scope_rows=out_of_scope_rows,
        scope_name=option.name,
    )

    if plan.conflicts:
        conflict_window = ImportConflictWindow(
            "ImportConflictWindow.xaml",
            import_excel_state.build_conflict_report_lines(plan.conflicts),
        )
        conflict_window.ShowDialog()
        return

    preview_lines = import_excel_state.build_preview_lines(
        plan.matched_rows,
        plan.total_rows,
        len(plan.instance_writes),
        len(plan.type_writes),
        plan.type_write_row_count(),
        plan.skipped_lines(),
        instance_clear_count=plan.instance_clear_count(),
        type_clear_count=plan.type_clear_count(),
        unclearable_lines=plan.unclearable_lines(),
    )

    if not plan.instance_writes and not plan.type_writes:
        forms.alert(
            "Nothing to import.\n\n{}".format("\n".join(preview_lines)),
            title=__title__,
        )
        return

    preview_window = ImportPreviewWindow(
        "ImportPreviewWindow.xaml", file_path, preview_lines
    )
    preview_window.ShowDialog()
    if preview_window.result != "import":
        return

    result = apply_import_plan(doc, plan)
    TaskDialog.Show(__title__, import_excel_state.build_import_summary_text(
        result.written_instance,
        result.written_type,
        result.failed_lines,
        plan.skipped_lines(),
        file_path,
        cleared_count=result.cleared_count,
        clear_failure_lines=result.clear_failure_lines,
    ))


def _run():
    doc = revit.doc
    if doc is None:
        forms.alert(
            "Open a project document before running the Excel tool.",
            title=__title__,
        )
        return

    try:
        if bool(doc.IsFamilyDocument):
            forms.alert(
                "Run the Excel tool from a project document, "
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

    selected_options = get_selected_options(options)
    if window.result == "export":
        if selected_options:
            _run_export(doc, selected_options)
        return
    if window.result == "import":
        if len(selected_options) == 1:
            _run_import(doc, selected_options[0])
        return


try:
    _run()
except Exception as run_error:
    LOGGER.exception("Excel tool failed.")
    forms.alert(
        "Excel tool failed:\n{}".format(run_error),
        title=__title__,
    )
