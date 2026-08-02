# -*- coding: utf-8 -*-
"""Revit-facing collectors and writers for Sheet Manager.

Everything that touches the Revit API lives here so the state module stays
importable on desktop Python.
"""

from __future__ import print_function

from pyrevit import DB
from pyrevit import HOST_APP
from pyrevit import framework
from pyrevit import revit
from pyrevit import script

from easybim import print_sets
from easybim import sheet_revisions
from easybim.compat import eid_to_int
from easybim.compat import element_id_factory
from easybim.compat import exception_text

import sheet_manager_state as state


LOGGER = script.get_logger()


def collect_sheets(doc):
    """All sheets in the model, placeholders included, sorted by number."""
    sheets = DB.FilteredElementCollector(doc)\
        .OfClass(framework.get_type(DB.ViewSheet))\
        .WhereElementIsNotElementType()\
        .ToElements()
    result = []
    for sheet in sheets:
        if getattr(sheet, "IsTemplate", False):
            continue
        result.append(sheet)
    result.sort(key=lambda sheet: (getattr(sheet, "SheetNumber", u"") or u""))
    return result


def collect_titleblock_map(doc):
    """Map sheet id (int) -> list of title block instances on that sheet."""
    tblocks = DB.FilteredElementCollector(doc)\
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)\
        .WhereElementIsNotElementType()\
        .ToElements()
    tb_map = {}
    for tblock in tblocks:
        try:
            owner_id = eid_to_int(tblock.OwnerViewId)
        except Exception:
            continue
        tb_map.setdefault(owner_id, []).append(tblock)
    return tb_map


def get_sheet_revision_info(sheet):
    """-> (all_revision_ids, cloud_driven_ids) as int sets.

    Cloud-driven = on the sheet via visible revision clouds; those cannot be
    removed through the additional-revisions list.
    """
    all_ids = set()
    additional_ids = set()
    try:
        for rev_id in sheet.GetAllRevisionIds():
            all_ids.add(eid_to_int(rev_id))
    except Exception:
        pass
    try:
        for rev_id in sheet.GetAdditionalRevisionIds():
            additional_ids.add(eid_to_int(rev_id))
    except Exception:
        pass
    return all_ids, all_ids - additional_ids


def build_rows(doc, row_factory):
    """Build grid rows for every sheet.

    ``row_factory(sheet_id, number, name, is_placeholder, tblock_count)``
    returns a state.SheetRowBase-compatible row. Returns (rows, tb_map,
    sheets_by_id) so later phases can resolve elements without recollecting.
    """
    tb_map = collect_titleblock_map(doc)
    rows = []
    sheets_by_id = {}
    for sheet in collect_sheets(doc):
        sheet_id = eid_to_int(sheet.Id)
        sheets_by_id[sheet_id] = sheet
        is_placeholder = False
        try:
            is_placeholder = bool(sheet.IsPlaceholder)
        except Exception:
            pass
        row = row_factory(
            sheet_id,
            getattr(sheet, "SheetNumber", u"") or u"",
            getattr(sheet, "Name", u"") or u"",
            is_placeholder,
            len(tb_map.get(sheet_id, [])),
        )
        all_ids, cloud_ids = get_sheet_revision_info(sheet)
        row.all_revision_ids = all_ids
        row.revisions_cloud = cloud_ids
        rows.append(row)
    return rows, tb_map, sheets_by_id


def attach_hidden_cloud_info(doc, rows, sheets_by_id):
    """Fill row.hidden_cloud_revs from the model's revision-cloud inventory."""
    sheets = [sheets_by_id[row.sheet_id] for row in rows
              if row.sheet_id in sheets_by_id]
    cloud_map = sheet_revisions.collect_sheet_cloud_map(
        doc, sheets, DB, framework, eid_to_int)
    for row in rows:
        revision_map = cloud_map.get(row.sheet_id) or {}
        row.hidden_cloud_revs = sheet_revisions.hidden_cloud_revision_ids(
            revision_map, row.all_revision_ids)
    return cloud_map


def clr_id_list_factory():
    from System.Collections.Generic import List as ClrList

    def factory():
        return ClrList[DB.ElementId]()
    return factory


def select_elements(element_ids):
    """Set the Revit UI selection to the given ElementIds."""
    id_list = clr_id_list_factory()()
    for element_id in element_ids:
        id_list.Add(element_id)
    revit.uidoc.Selection.SetElementIds(id_list)


def write_parameter(element, param_name, value_text):
    """Write a staged text value to a parameter by StorageType."""
    param = element.LookupParameter(param_name)
    if param is None:
        raise ValueError("Parameter not found: {0}".format(param_name))
    if param.IsReadOnly:
        raise ValueError("Parameter is read-only: {0}".format(param_name))
    text = u"{0}".format(value_text if value_text is not None else u"")
    storage = param.StorageType
    if storage == DB.StorageType.String:
        param.Set(text)
        return
    if storage == DB.StorageType.Integer:
        lowered = text.strip().lower()
        if lowered in ("yes", "y", "true"):
            param.Set(1)
        elif lowered in ("no", "n", "false", ""):
            param.Set(0)
        else:
            param.Set(int(float(lowered)))
        return
    if storage == DB.StorageType.Double:
        # SetValueString honors the user's display units.
        try:
            if param.SetValueString(text):
                return
        except Exception:
            pass
        param.Set(float(text))
        return
    raise ValueError(
        "Unsupported storage type for {0}".format(param_name))


def _builtin_int(builtin_param):
    try:
        return int(builtin_param)
    except Exception:
        return None


_EXCLUDED_SHEET_PARAM_IDS = set(x for x in (
    _builtin_int(getattr(DB.BuiltInParameter, "SHEET_NUMBER", None)),
    _builtin_int(getattr(DB.BuiltInParameter, "SHEET_NAME", None)),
) if x is not None)


def excluded_sheet_param_ids():
    """Builtin param ids handled by the fixed Number/Name columns."""
    return set(_EXCLUDED_SHEET_PARAM_IDS)


def discover_instance_parameters(elements, exclude_id_values=None):
    """-> {name: {"storage", "read_only", "id_value"}} across the samples.

    ElementId-storage parameters are marked read-only (never staged)."""
    exclude = set(exclude_id_values or [])
    found = {}
    for element in elements:
        if element is None:
            continue
        try:
            params = element.Parameters
        except Exception:
            continue
        for param in params:
            try:
                name = param.Definition.Name
            except Exception:
                continue
            if not name:
                continue
            try:
                id_value = eid_to_int(param.Id)
            except Exception:
                id_value = None
            if id_value in exclude:
                continue
            storage = u"{0}".format(param.StorageType)
            read_only = bool(param.IsReadOnly) or storage == u"ElementId"
            if name in found:
                if not read_only and found[name]["read_only"]:
                    found[name]["read_only"] = False
                continue
            found[name] = {
                "storage": storage,
                "read_only": read_only,
                "id_value": id_value,
            }
    return found


def discover_sheet_parameters(sheets):
    samples = [sheet for sheet in sheets
               if not getattr(sheet, "IsPlaceholder", False)][:20] \
        or list(sheets)[:20]
    return discover_instance_parameters(
        samples, exclude_id_values=_EXCLUDED_SHEET_PARAM_IDS)


def discover_titleblock_parameters(tb_map):
    samples = []
    for tblocks in tb_map.values():
        if len(tblocks) == 1:
            samples.append(tblocks[0])
        if len(samples) >= 20:
            break
    if not samples:
        for tblocks in tb_map.values():
            samples.extend(tblocks[:1])
            if samples:
                break
    return discover_instance_parameters(samples)


def read_parameter_value(element, param_name):
    if element is None:
        return u""
    try:
        param = element.LookupParameter(param_name)
    except Exception:
        return u""
    if param is None:
        return u""
    try:
        if param.StorageType == DB.StorageType.String:
            return param.AsString() or u""
        value_string = param.AsValueString()
        if value_string is not None:
            return value_string
        if param.StorageType == DB.StorageType.Integer:
            return u"{0}".format(param.AsInteger())
        if param.StorageType == DB.StorageType.Double:
            return u"{0}".format(param.AsDouble())
    except Exception:
        pass
    return u""


def param_target_for_row(row, column, sheets_by_id, tb_map):
    if column.kind == state.KIND_SHEET_PARAM:
        return sheets_by_id.get(row.sheet_id)
    tblocks = tb_map.get(row.sheet_id) or []
    if len(tblocks) == 1:
        return tblocks[0]
    return None


def collect_sheet_list_schedules(doc):
    """Sheet-list ViewSchedules, cheaply (no text exports)."""
    sheet_category = revit.query.get_category(DB.BuiltInCategory.OST_Sheets)
    sheet_category_id = sheet_category.Id if sheet_category else None
    schedules = DB.FilteredElementCollector(doc)\
        .OfClass(framework.get_type(DB.ViewSchedule))\
        .WhereElementIsNotElementType()\
        .ToElements()
    result = [s for s in schedules
              if print_sets.is_sheet_list_schedule(s, sheet_category_id)]
    result.sort(key=lambda s: (getattr(s, "Name", u"") or u"").lower())
    return result


def get_ordered_sheets_for_schedule(doc, schedule):
    return print_sets.get_ordered_schedule_sheets(
        doc, schedule, DB, framework, script,
        logger=LOGGER, host_app=HOST_APP)


def map_schedule_fields(schedule):
    """Visible schedule fields in order.

    -> [{"heading", "param_id_value", "is_param"}]; is_param False for
    calculated/combined/percentage fields (those fall back to text columns).
    """
    result = []
    try:
        definition = schedule.Definition
        field_ids = list(definition.GetFieldOrder())
    except Exception:
        return result
    invalid_id = eid_to_int(DB.ElementId.InvalidElementId)
    for field_id in field_ids:
        try:
            field = definition.GetField(field_id)
            if getattr(field, "IsHidden", False):
                continue
            heading = getattr(field, "ColumnHeading", None) or u""
            if not heading:
                try:
                    heading = field.GetName() or u""
                except Exception:
                    heading = u""
            param_id_value = None
            try:
                param_id_value = eid_to_int(field.ParameterId)
            except Exception:
                param_id_value = None
            is_param = param_id_value is not None \
                and param_id_value != invalid_id \
                and not getattr(field, "IsCalculatedField", False) \
                and not getattr(field, "IsCombinedParameterField", False)
            result.append({
                "heading": heading,
                "param_id_value": param_id_value,
                "is_param": bool(is_param),
            })
        except Exception:
            continue
    return result


def read_schedule_text_cells(doc, schedule, sheets):
    """-> {sheet_id_int: [cell, ...]} from the schedule's text export."""
    del doc
    lines = print_sets.get_schedule_text_data(
        schedule, script, LOGGER, HOST_APP)
    per_sheet = {}
    if not lines:
        return per_sheet
    for sheet in sheets:
        for line in lines:
            if print_sets.schedule_line_matches_sheet(line, sheet):
                per_sheet[eid_to_int(sheet.Id)] = line.split(u"\t")
                break
    return per_sheet


def get_print_set_sheets(doc, print_set_name):
    """Sheets of a ViewSheetSet in its (ordered) view order."""
    sheet_set = print_sets._find_print_set_by_name(
        doc, DB, framework, print_set_name)
    if sheet_set is None:
        return []
    views = None
    try:
        ordered = getattr(sheet_set, "OrderedViewList", None)
        if ordered is not None:
            views = list(ordered)
    except Exception:
        views = None
    if not views:
        try:
            views = list(sheet_set.Views)
        except Exception:
            views = []
    return [view for view in views if isinstance(view, DB.ViewSheet)]


def copy_sheet_detailing(doc, source_sheet, target_sheet, clr_factory):
    """Copy on-sheet detailing (lines, text, symbols...) between sheets.

    Excludes viewports, title blocks, schedule instances, and revision
    clouds (clouds would silently add revisions to the target sheet).
    Returns the copied element count.
    """
    elements = DB.FilteredElementCollector(doc, source_sheet.Id)\
        .WhereElementIsNotElementType()\
        .ToElements()
    tblock_cat = _builtin_int(DB.BuiltInCategory.OST_TitleBlocks)
    source_id = eid_to_int(source_sheet.Id)
    copy_ids = clr_factory()
    count = 0
    for element in elements:
        if isinstance(element, DB.Viewport):
            continue
        if isinstance(element, DB.ScheduleSheetInstance):
            continue
        if isinstance(element, DB.RevisionCloud):
            continue
        category = getattr(element, "Category", None)
        if category is None:
            continue
        try:
            if eid_to_int(category.Id) == tblock_cat:
                continue
        except Exception:
            pass
        try:
            if eid_to_int(element.OwnerViewId) != source_id:
                continue
        except Exception:
            continue
        copy_ids.Add(element.Id)
        count += 1
    if count:
        DB.ElementTransformUtils.CopyElements(
            source_sheet, copy_ids, target_sheet,
            DB.Transform.Identity, DB.CopyPasteOptions())
    return count


def duplicate_views_onto(doc, source_sheet, target_sheet):
    """Duplicate the source sheet's placed views onto the target sheet at
    the same viewport positions. Legends and schedules are placed directly
    (they can live on multiple sheets). -> (placed_count, skipped_names)."""
    placed = 0
    skipped = []
    try:
        viewport_ids = list(source_sheet.GetAllViewports())
    except Exception:
        viewport_ids = []
    for viewport_id in viewport_ids:
        viewport = doc.GetElement(viewport_id)
        if viewport is None:
            continue
        view = doc.GetElement(viewport.ViewId)
        if view is None:
            continue
        view_name = getattr(view, "Name", u"") or u"view"
        try:
            center = viewport.GetBoxCenter()
        except Exception:
            center = None
        target_view_id = None
        if getattr(view, "ViewType", None) == DB.ViewType.Legend:
            target_view_id = view.Id
        else:
            try:
                option = DB.ViewDuplicateOption.WithDetailing
                if not view.CanViewBeDuplicated(option):
                    option = DB.ViewDuplicateOption.Duplicate
                if view.CanViewBeDuplicated(option):
                    target_view_id = view.Duplicate(option)
            except Exception:
                target_view_id = None
        if target_view_id is None or center is None:
            skipped.append(view_name)
            continue
        try:
            if not DB.Viewport.CanAddViewToSheet(
                    doc, target_sheet.Id, target_view_id):
                skipped.append(view_name)
                continue
            new_viewport = DB.Viewport.Create(
                doc, target_sheet.Id, target_view_id, center)
            if new_viewport is None:
                skipped.append(view_name)
                continue
            try:
                type_id = viewport.GetTypeId()
                if type_id is not None:
                    new_viewport.ChangeTypeId(type_id)
            except Exception:
                pass
            placed += 1
        except Exception:
            skipped.append(view_name)
    try:
        schedule_instances = DB.FilteredElementCollector(
            doc, source_sheet.Id)\
            .OfClass(framework.get_type(DB.ScheduleSheetInstance))\
            .ToElements()
    except Exception:
        schedule_instances = []
    for instance in schedule_instances:
        try:
            if getattr(instance, "IsTitleblockRevisionSchedule", False):
                continue
            DB.ScheduleSheetInstance.Create(
                doc, target_sheet.Id, instance.ScheduleId, instance.Point)
            placed += 1
        except Exception:
            skipped.append(u"schedule instance")
    return placed, skipped


def _group_by_sheet(items):
    grouped = {}
    for item in items:
        grouped.setdefault(item[0].sheet_id, []).append(item)
    return grouped


def _on_off(value):
    return "On" if value else "Off"


def apply_staged_changes(doc, changes, sheets_by_id, tb_map,
                         cloud_decisions):
    """Run all staged changes inside one assimilated TransactionGroup.

    ``cloud_decisions``: {"hide_approved": bool,
                          "unhide_mode": "unhide" | "add" | "skip"}.
    Returns a state.ApplyResults; per-item failures never abort a phase.
    """
    results = state.ApplyResults()
    eid_from_int = element_id_factory(DB.ElementId)
    clr_factory = clr_id_list_factory()

    revision_adds = list(changes.revision_adds)
    cloud_hides = []
    if changes.cloud_hide_requests:
        if cloud_decisions.get("hide_approved"):
            cloud_hides = list(changes.cloud_hide_requests)
        else:
            for row, column, revision_id in changes.cloud_hide_requests:
                results.add_error(
                    row.number, column.header,
                    "Skipped: revision is on the sheet via visible "
                    "cloud(s); hiding was declined.",
                    status="skipped")
    cloud_unhides = []
    unhide_mode = cloud_decisions.get("unhide_mode", "skip")
    if changes.cloud_unhide_candidates:
        if unhide_mode == "unhide":
            cloud_unhides = list(changes.cloud_unhide_candidates)
        elif unhide_mode == "add":
            revision_adds.extend(changes.cloud_unhide_candidates)
        else:
            for row, column, revision_id in changes.cloud_unhide_candidates:
                results.add_error(
                    row.number, column.header,
                    "Skipped: hidden cloud(s) exist for this revision; "
                    "unhiding was declined.",
                    status="skipped")

    removal_targets = {}
    for row, column, revision_id in changes.revision_removes:
        removal_targets.setdefault(row.sheet_id, set()).add(revision_id)
    for row, column, revision_id in cloud_hides:
        removal_targets.setdefault(row.sheet_id, set()).add(revision_id)

    with revit.TransactionGroup("Sheet Manager - Apply Changes", doc=doc):
        _apply_renumbers(doc, changes, sheets_by_id, results)
        _apply_creates(doc, changes, sheets_by_id, tb_map, results,
                       eid_from_int)
        _apply_names_and_params(doc, changes, sheets_by_id, tb_map, results)
        _apply_copy_content(doc, changes.copy_content_ops, sheets_by_id,
                            results, clr_factory)
        _apply_revisions(doc, revision_adds, changes.revision_removes,
                         sheets_by_id, results, eid_from_int, clr_factory)
        _apply_clouds(doc, cloud_hides, cloud_unhides, sheets_by_id,
                      results, eid_from_int, clr_factory)

    _leftover_check(removal_targets, sheets_by_id, results)
    return results


def _apply_renumbers(doc, changes, sheets_by_id, results):
    if not changes.renames:
        return
    existing_numbers = []
    for sheet in sheets_by_id.values():
        try:
            existing_numbers.append(sheet.SheetNumber)
        except Exception:
            continue
    rename_keys = [(row.sheet_id, old, new)
                   for row, old, new in changes.renames]
    phase_temp, phase_final = state.plan_number_assignments(
        rename_keys, existing_numbers)
    rename_by_id = {}
    for row, old, new in changes.renames:
        rename_by_id[row.sheet_id] = (row, old, new)
    failed_ids = set()
    with revit.Transaction("Sheet Manager - Renumber Sheets", doc=doc):
        for sheet_id, temp_number in phase_temp:
            sheet = sheets_by_id.get(sheet_id)
            row, old, new = rename_by_id[sheet_id]
            if sheet is None:
                failed_ids.add(sheet_id)
                results.add_error(old, "Sheet Number",
                                  "Sheet not found in model.")
                continue
            try:
                sheet.SheetNumber = temp_number
            except Exception as err:
                failed_ids.add(sheet_id)
                results.add_error(old, "Sheet Number", exception_text(err))
        for sheet_id, final_number in phase_final:
            if sheet_id in failed_ids:
                continue
            sheet = sheets_by_id.get(sheet_id)
            row, old, new = rename_by_id[sheet_id]
            try:
                sheet.SheetNumber = final_number
                results.parameter_changes.append(state.ResultItem(
                    new, "Sheet Number", old, new, "applied"))
                results.applied_cells.append((row, "number"))
                results.updated_parameter_count += 1
                results.modified_sheet_ids.add(sheet_id)
            except Exception as err:
                results.add_error(old, "Sheet Number", exception_text(err))
                try:
                    sheet.SheetNumber = old
                except Exception:
                    pass


def _default_titleblock_type_id(doc, tb_map, eid_from_int):
    """Most-used title block type in the model (fallback: first type,
    then no title block)."""
    counts = {}
    for tblocks in tb_map.values():
        for tblock in tblocks:
            try:
                type_int = eid_to_int(tblock.GetTypeId())
            except Exception:
                continue
            if type_int is None:
                continue
            counts[type_int] = counts.get(type_int, 0) + 1
    if counts:
        best = sorted(counts.items(),
                      key=lambda pair: (-pair[1], pair[0]))[0][0]
        try:
            return eid_from_int(best)
        except Exception:
            pass
    try:
        first_type = DB.FilteredElementCollector(doc)\
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)\
            .WhereElementIsElementType()\
            .FirstElement()
        if first_type is not None:
            return first_type.Id
    except Exception:
        pass
    return DB.ElementId.InvalidElementId


def _apply_creates(doc, changes, sheets_by_id, tb_map, results,
                   eid_from_int):
    if not changes.pending_sheets:
        return
    tb_type_id = _default_titleblock_type_id(doc, tb_map, eid_from_int)
    with revit.Transaction("Sheet Manager - Create Sheets", doc=doc):
        for row in changes.pending_sheets:
            try:
                sheet = DB.ViewSheet.Create(doc, tb_type_id)
                sheet.SheetNumber = row.number
                if row.name:
                    sheet.Name = row.name
                row.sheet_id = eid_to_int(sheet.Id)
                row.is_pending = False
                sheets_by_id[row.sheet_id] = sheet
                results.created_count += 1
                results.modified_sheet_ids.add(row.sheet_id)
                results.sheet_changes.append(state.ResultItem(
                    row.number, "Create sheet", u"", row.name, "applied"))
                results.applied_cells.append((row, "number"))
                results.applied_cells.append((row, "name"))
            except Exception as err:
                results.add_error(row.number, "Create sheet",
                                  exception_text(err))


def _apply_names_and_params(doc, changes, sheets_by_id, tb_map, results):
    if not (changes.name_edits or changes.param_edits):
        return
    with revit.Transaction("Sheet Manager - Update Parameters", doc=doc):
        for row, old, new in changes.name_edits:
            sheet = sheets_by_id.get(row.sheet_id)
            if sheet is None:
                results.add_error(row.number, "Sheet Name",
                                  "Sheet not found in model.")
                continue
            try:
                sheet.Name = new
                results.parameter_changes.append(state.ResultItem(
                    row.number, "Sheet Name", old, new, "applied"))
                results.applied_cells.append((row, "name"))
                results.updated_parameter_count += 1
                results.modified_sheet_ids.add(row.sheet_id)
            except Exception as err:
                results.add_error(row.number, "Sheet Name",
                                  exception_text(err))
        for row, column, old, new in changes.param_edits:
            if column.kind == state.KIND_SHEET_PARAM:
                target = sheets_by_id.get(row.sheet_id)
            else:
                tblocks = tb_map.get(row.sheet_id) or []
                target = tblocks[0] if len(tblocks) == 1 else None
            if target is None:
                results.add_error(
                    row.number, column.header,
                    "No single target element for this parameter.")
                continue
            try:
                write_parameter(target, column.param_name, new)
                results.parameter_changes.append(state.ResultItem(
                    row.number, column.header, old, new, "applied"))
                results.applied_cells.append((row, column.attr))
                results.updated_parameter_count += 1
                results.modified_sheet_ids.add(row.sheet_id)
            except Exception as err:
                results.add_error(row.number, column.header,
                                  exception_text(err))


def _apply_copy_content(doc, ops, sheets_by_id, results, clr_factory):
    if not ops:
        return
    with revit.Transaction("Sheet Manager - Copy Sheet Content", doc=doc):
        for op in ops:
            source = sheets_by_id.get(op.source_sheet_id)
            if source is None:
                results.add_error(op.source_number, "Copy Sheet Info",
                                  "Source sheet not found in model.")
                continue
            for target_id in op.target_sheet_ids:
                target = sheets_by_id.get(target_id)
                if target is None:
                    results.add_error(op.source_number, "Copy Sheet Info",
                                      "Target sheet not found in model.")
                    continue
                target_number = getattr(target, "SheetNumber", u"") or u""
                if op.dup_detailing:
                    try:
                        count = copy_sheet_detailing(
                            doc, source, target, clr_factory)
                        results.sheet_changes.append(state.ResultItem(
                            target_number, "Copy sheet detailing", u"",
                            u"{0} element(s) from {1}".format(
                                count, op.source_number),
                            "applied" if count else "nothing to copy"))
                        if count:
                            results.modified_sheet_ids.add(target_id)
                    except Exception as err:
                        results.add_error(target_number,
                                          "Copy sheet detailing",
                                          exception_text(err))
                if op.dup_with_views:
                    try:
                        placed, skipped = duplicate_views_onto(
                            doc, source, target)
                        detail = u"{0} view(s) from {1}".format(
                            placed, op.source_number)
                        if skipped:
                            detail += u"; skipped: {0}".format(
                                u", ".join(skipped[:8]))
                        results.sheet_changes.append(state.ResultItem(
                            target_number, "Duplicate views", u"", detail,
                            "applied" if placed else "nothing placed"))
                        if placed:
                            results.modified_sheet_ids.add(target_id)
                    except Exception as err:
                        results.add_error(target_number, "Duplicate views",
                                          exception_text(err))


def _apply_revisions(doc, adds, removes, sheets_by_id, results,
                     eid_from_int, clr_factory):
    if not (adds or removes):
        return
    adds_by_sheet = _group_by_sheet(adds)
    removes_by_sheet = _group_by_sheet(removes)
    with revit.Transaction("Sheet Manager - Update Revisions", doc=doc):
        for sheet_id, items in adds_by_sheet.items():
            sheet = sheets_by_id.get(sheet_id)
            if sheet is None:
                for row, column, revision_id in items:
                    results.add_error(row.number, column.header,
                                      "Sheet not found in model.")
                continue
            rev_eids = [eid_from_int(revision_id)
                        for _, _, revision_id in items]
            added, skipped, failed, failed_ids = \
                sheet_revisions.ensure_revisions_on_sheet(
                    sheet, rev_eids, eid_to_int, clr_factory)
            results.revisions_added += added
            failed_set = set(failed_ids)
            for row, column, revision_id in items:
                if revision_id in failed_set:
                    results.add_error(row.number, column.header,
                                      "Could not add revision to sheet.")
                    continue
                results.revision_changes.append(state.ResultItem(
                    row.number, column.header, _on_off(False),
                    _on_off(True), "applied"))
                results.applied_cells.append((row, column.attr))
                results.modified_sheet_ids.add(sheet_id)
        for sheet_id, items in removes_by_sheet.items():
            sheet = sheets_by_id.get(sheet_id)
            if sheet is None:
                for row, column, revision_id in items:
                    results.add_error(row.number, column.header,
                                      "Sheet not found in model.")
                continue
            remove_ints = [revision_id for _, _, revision_id in items]
            changed, removed = sheet_revisions.remove_revisions_from_sheet(
                sheet, remove_ints, eid_from_int, eid_to_int, clr_factory)
            removed_set = set(removed)
            results.revisions_removed += len(removed_set)
            for row, column, revision_id in items:
                if revision_id in removed_set:
                    results.revision_changes.append(state.ResultItem(
                        row.number, column.header, _on_off(True),
                        _on_off(False), "applied"))
                    results.applied_cells.append((row, column.attr))
                    results.modified_sheet_ids.add(sheet_id)
                else:
                    results.add_error(
                        row.number, column.header,
                        "Revision was not in the sheet's additional "
                        "revisions.", status="skipped")


def _apply_clouds(doc, hides, unhides, sheets_by_id, results,
                  eid_from_int, clr_factory):
    if not (hides or unhides):
        return
    involved = {}
    for row, column, revision_id in hides + unhides:
        sheet = sheets_by_id.get(row.sheet_id)
        if sheet is not None:
            involved[row.sheet_id] = sheet
    cloud_map = sheet_revisions.collect_sheet_cloud_map(
        doc, list(involved.values()), DB, framework, eid_to_int)
    results.applied_cloud_ops = []
    with revit.Transaction("Sheet Manager - Update Revision Clouds",
                           doc=doc):
        for direction, items in (("hide", hides), ("unhide", unhides)):
            hide = direction == "hide"
            for row, column, revision_id in items:
                entries = (cloud_map.get(row.sheet_id) or {})\
                    .get(revision_id) or []
                if not entries:
                    results.add_error(
                        row.number, column.header,
                        "No revision clouds found to {0}.".format(direction),
                        status="skipped")
                    continue
                by_view = {}
                for entry in entries:
                    by_view.setdefault(
                        entry["owner_view_id"], []).append(entry)
                total_changed = 0
                failures = []
                for view_int, view_entries in by_view.items():
                    view = doc.GetElement(eid_from_int(view_int))
                    if view is None:
                        continue
                    changed, failed_ids = \
                        sheet_revisions.set_clouds_hidden_in_view(
                            view, view_entries, hide, clr_factory,
                            eid_from_int)
                    total_changed += changed
                    failures.extend(failed_ids)
                if total_changed and not failures:
                    status = "applied"
                elif total_changed:
                    status = "partial"
                else:
                    status = "failed"
                label = column.header + " (clouds)"
                results.revision_changes.append(state.ResultItem(
                    row.number, label, _on_off(hide), _on_off(not hide),
                    status))
                if total_changed:
                    if hide:
                        results.revisions_removed += 1
                    else:
                        results.revisions_added += 1
                    results.applied_cells.append((row, column.attr))
                    results.applied_cloud_ops.append(
                        (row, column, revision_id, direction))
                    results.modified_sheet_ids.add(row.sheet_id)
                if failures:
                    results.add_error(
                        row.number, label,
                        "Could not {0} cloud id(s): {1}".format(
                            direction,
                            ", ".join(str(x) for x in failures)))


def _leftover_check(removal_targets, sheets_by_id, results):
    for sheet_id, revision_ids in removal_targets.items():
        sheet = sheets_by_id.get(sheet_id)
        if sheet is None:
            continue
        try:
            post_all = set()
            for rev_id in sheet.GetAllRevisionIds():
                value = eid_to_int(rev_id)
                if value is not None:
                    post_all.add(value)
        except Exception:
            continue
        number = getattr(sheet, "SheetNumber", u"") or u""
        for revision_id in sorted(post_all & revision_ids):
            results.add_error(
                number, "Revision id {0}".format(revision_id),
                "Still on the sheet after removal (likely driven by "
                "visible revision clouds).", status="warning")
