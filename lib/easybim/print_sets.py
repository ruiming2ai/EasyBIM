# -*- coding: utf-8 -*-
"""Helpers for creating ordered native Revit print sets from Sheet Lists."""

import codecs
import os.path as op
import re


class UnsupportedRevitVersion(Exception):
    """Raised when ordered native print sets are not available."""


class SheetListOption(object):
    """Sheet List shown in the Update Print Set window."""

    def __init__(self, schedule, rows):
        self.schedule = schedule
        self.name = _safe_text(getattr(schedule, "Name", ""))
        self.rows = list(rows or [])


class SheetSetRow(object):
    """Sheet row shown in the Update Print Set preview."""

    def __init__(self, revit_sheet, index):
        self.revit_sheet = revit_sheet
        self.index = index
        self.number = _safe_text(getattr(revit_sheet, "SheetNumber", ""))
        self.name = _safe_text(getattr(revit_sheet, "Name", ""))
        self.printable = bool(getattr(revit_sheet, "CanBePrinted", False))
        self.status = "Printable" if self.printable else "Skipped"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except Exception:
        return fallback


def get_element_id_value(element_id):
    """Return an integer ElementId value across Revit versions and tests."""
    if element_id is None:
        return None
    try:
        from pyrevit.compat import get_elementid_value_func
        return get_elementid_value_func()(element_id)
    except Exception:
        pass
    for attr in ("IntegerValue", "Value"):
        try:
            value = getattr(element_id, attr)
            if value is not None:
                return int(value)
        except Exception:
            continue
    try:
        return int(element_id)
    except Exception:
        return None


def supports_ordered_print_sets(host_app):
    try:
        return bool(host_app.is_newer_than(2022))
    except Exception:
        return False


def get_schedule_export_encoding(host_app):
    if supports_host_newer_than(host_app, 2020):
        return "utf_8"
    return "utf_16_le"


def supports_host_newer_than(host_app, version):
    try:
        return bool(host_app.is_newer_than(version))
    except Exception:
        return False


def is_sheet_list_schedule(schedule, sheet_category_id):
    try:
        if getattr(schedule, "IsTemplate", False):
            return False
        return schedule.Definition.CategoryId == sheet_category_id
    except Exception:
        return False


def get_ordered_schedule_sheets(doc, schedule, DB, framework, script,
                                logger=None, host_app=None):
    """Return ViewSheet instances in the exported Sheet List row order."""
    sheets = list(
        DB.FilteredElementCollector(doc, schedule.Id)
          .OfClass(framework.get_type(DB.ViewSheet))
          .WhereElementIsNotElementType()
          .ToElements()
    )

    schedule_data = get_schedule_text_data(schedule, script, logger, host_app)
    if not schedule_data:
        return sheets

    ordered_sheets = []
    matched_sheet_ids = set()
    for line_no, data_line in enumerate(schedule_data):
        del line_no
        for sheet in sheets:
            sheet_id = get_element_id_value(getattr(sheet, "Id", None))
            if sheet_id in matched_sheet_ids:
                continue
            if schedule_line_matches_sheet(data_line, sheet):
                ordered_sheets.append(sheet)
                matched_sheet_ids.add(sheet_id)
                break

    for sheet in sheets:
        sheet_id = get_element_id_value(getattr(sheet, "Id", None))
        if sheet_id not in matched_sheet_ids:
            ordered_sheets.append(sheet)

    return ordered_sheets


def get_schedule_text_data(schedule, script, logger=None, host_app=None):
    schedule_data_file = script.get_instance_data_file(
        str(get_element_id_value(schedule.Id))
    )
    try:
        from pyrevit import DB
        export_options = DB.ViewScheduleExportOptions()
        from pyrevit import coreutils
        export_options.TextQualifier = coreutils.get_enum_none(
            DB.ExportTextQualifier
        )
        schedule.Export(
            op.dirname(schedule_data_file),
            op.basename(schedule_data_file),
            export_options
        )
    except Exception as export_err:
        if logger:
            logger.error("Error exporting sheet list: %s", export_err)
        return []

    try:
        with codecs.open(
            schedule_data_file,
            "r",
            get_schedule_export_encoding(host_app)
        ) as data_file:
            return [line.rstrip("\r\n") for line in data_file.readlines()]
    except Exception as open_err:
        if logger:
            logger.error(
                "Error opening sheet list export: %s | %s",
                schedule_data_file,
                open_err
            )
        return []


def schedule_line_matches_sheet(data_line, sheet):
    sheet_number = _safe_text(getattr(sheet, "SheetNumber", ""))
    if not sheet_number:
        return False
    match_pattern = r"(^|.*\t){}(\t.*|$)".format(re.escape(sheet_number))
    return re.match(match_pattern, data_line) is not None


def build_sheet_rows(sheets):
    return [SheetSetRow(sheet, index + 1) for index, sheet in enumerate(sheets)]


def collect_sheet_list_options(doc, DB, framework, script, sheet_category_id,
                               logger=None, host_app=None):
    schedules = list(
        DB.FilteredElementCollector(doc)
          .OfClass(framework.get_type(DB.ViewSchedule))
          .WhereElementIsNotElementType()
          .ToElements()
    )

    options = []
    for schedule in schedules:
        if not is_sheet_list_schedule(schedule, sheet_category_id):
            continue
        sheets = get_ordered_schedule_sheets(
            doc,
            schedule,
            DB,
            framework,
            script,
            logger=logger,
            host_app=host_app
        )
        rows = build_sheet_rows(sheets)
        if rows:
            options.append(SheetListOption(schedule, rows))
    return options


def _get_revision_sequence(revision):
    try:
        return int(getattr(revision, "SequenceNumber"))
    except Exception:
        return _safe_int(get_element_id_value(getattr(revision, "Id", None)))


def _get_revision_number(revision):
    for attr in ("RevisionNumber", "Number"):
        try:
            value = getattr(revision, attr)
            if value:
                return _safe_text(value)
        except Exception:
            continue
    try:
        from pyrevit import DB
        for bip_name in (
            "REVISION_NUMBER",
            "PROJECT_REVISION_NUMBER",
            "PROJECT_REVISION_REVISION_NUMBER",
        ):
            try:
                bip = getattr(DB.BuiltInParameter, bip_name)
                param = revision.get_Parameter(bip)
                if param:
                    value = param.AsString()
                    if value:
                        return _safe_text(value)
            except Exception:
                continue
    except Exception:
        pass
    return _safe_text(_get_revision_sequence(revision))


def _get_revision_description(revision):
    try:
        value = revision.Description
        if value:
            return _safe_text(value)
    except Exception:
        pass
    try:
        from pyrevit import DB
        for bip_name in ("REVISION_DESCRIPTION", "PROJECT_REVISION_DESCRIPTION"):
            try:
                bip = getattr(DB.BuiltInParameter, bip_name)
                param = revision.get_Parameter(bip)
                if param:
                    value = param.AsString()
                    if value:
                        return _safe_text(value)
            except Exception:
                continue
    except Exception:
        pass
    return ""


def collect_revision_rows(doc, DB, framework):
    revisions = list(
        DB.FilteredElementCollector(doc)
          .OfClass(framework.get_type(DB.Revision))
          .ToElements()
    )

    rows = []
    for revision in revisions:
        rev_id = get_element_id_value(getattr(revision, "Id", None))
        if rev_id is None:
            continue
        rows.append({
            "id": int(rev_id),
            "sequence": _get_revision_sequence(revision),
            "number": _get_revision_number(revision),
            "description": _get_revision_description(revision),
        })
    rows.sort(key=lambda x: (x["sequence"], x["number"], x["id"]))
    return rows


def sheet_has_revision(sheet, selected_revision_ids):
    selected_ids = set([int(x) for x in selected_revision_ids or []])
    if not selected_ids:
        return True
    try:
        sheet_revision_ids = set()
        for rev_id in sheet.GetAllRevisionIds():
            rev_int = get_element_id_value(rev_id)
            if rev_int is not None:
                sheet_revision_ids.add(int(rev_int))
        return bool(sheet_revision_ids & selected_ids)
    except Exception:
        return False


def filter_rows_by_revision(rows, selected_revision_ids):
    if not selected_revision_ids:
        return list(rows or [])
    return [
        row for row in rows or []
        if sheet_has_revision(row.revit_sheet, selected_revision_ids)
    ]


def split_printable_rows(rows):
    printable_rows = [row for row in rows or [] if getattr(row, "printable", False)]
    return printable_rows, len(list(rows or [])) - len(printable_rows)


def build_print_set_name(schedule_name, selected_revision_ids, revision_rows):
    selected_ids = set([int(x) for x in selected_revision_ids or []])
    if not selected_ids:
        return _safe_text(schedule_name)

    selected_revisions = [
        row for row in revision_rows or []
        if int(row.get("id", 0)) in selected_ids
    ]
    selected_revisions.sort(
        key=lambda x: (
            _safe_int(x.get("sequence")),
            _safe_text(x.get("number")),
            _safe_int(x.get("id")),
        )
    )

    labels = []
    for revision in selected_revisions:
        label = _safe_text(revision.get("number")) \
            or _safe_text(revision.get("sequence")) \
            or _safe_text(revision.get("id"))
        if label and label not in labels:
            labels.append(label)

    if not labels:
        labels = [_safe_text(x) for x in sorted(selected_ids)]

    return "{} - Rev {}".format(_safe_text(schedule_name), ", ".join(labels))


def _collect_print_sets(doc, DB, framework):
    return list(
        DB.FilteredElementCollector(doc)
          .OfClass(framework.get_type(DB.ViewSheetSet))
          .WhereElementIsNotElementType()
          .ToElements()
    )


def collect_print_set_names(doc, DB, framework):
    names = []
    for view_sheet_set in _collect_print_sets(doc, DB, framework):
        try:
            name = _safe_text(view_sheet_set.Name)
        except Exception:
            name = ""
        if name and name not in names:
            names.append(name)
    names.sort()
    return names


def _find_print_set_by_name(doc, DB, framework, print_set_name):
    for view_sheet_set in _collect_print_sets(doc, DB, framework):
        try:
            if view_sheet_set.Name == print_set_name:
                return view_sheet_set
        except Exception:
            continue
    return None


def save_ordered_print_set(doc, print_set_name, printable_rows, DB, framework,
                           revit, host_app):
    """Create or update a native ordered ViewSheetSet and make it current."""
    if not supports_ordered_print_sets(host_app):
        raise UnsupportedRevitVersion(
            "Ordered print sets require Revit 2023 or newer."
        )

    sheets = []
    for row in printable_rows or []:
        sheets.append(getattr(row, "revit_sheet", row))
    if not sheets:
        raise ValueError("No printable sheets were provided.")

    from System.Collections.Generic import List as ClrList

    ordered_views = ClrList[DB.View]()
    for sheet in sheets:
        ordered_views.Add(sheet)

    print_mgr = doc.PrintManager
    existing_set = _find_print_set_by_name(doc, DB, framework, print_set_name)

    if existing_set is not None:
        with revit.Transaction("Remove Existing Print Set", doc=doc):
            print_mgr.ViewSheetSetting.CurrentViewSheetSet = existing_set
            print_mgr.ViewSheetSetting.Delete()

    with revit.Transaction("Create Print Set from Sheet List", doc=doc):
        print_mgr.PrintRange = DB.PrintRange.Select
        view_sheet_setting = print_mgr.ViewSheetSetting
        view_sheet_setting.CurrentViewSheetSet.IsAutomatic = False
        view_sheet_setting.CurrentViewSheetSet.OrderedViewList = ordered_views
        view_sheet_setting.SaveAs(print_set_name)

    saved_set = _find_print_set_by_name(doc, DB, framework, print_set_name)
    if saved_set is not None:
        with revit.Transaction("Activate Print Set", doc=doc):
            print_mgr.PrintRange = DB.PrintRange.Select
            print_mgr.ViewSheetSetting.CurrentViewSheetSet = saved_set
        print_mgr.Apply()

    return saved_set
