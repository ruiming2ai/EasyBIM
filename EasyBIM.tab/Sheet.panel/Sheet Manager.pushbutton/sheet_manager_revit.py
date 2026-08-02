# -*- coding: utf-8 -*-
"""Revit-facing collectors and writers for Sheet Manager.

Everything that touches the Revit API lives here so the state module stays
importable on desktop Python.
"""

from __future__ import print_function

from pyrevit import DB
from pyrevit import framework

from easybim.compat import eid_to_int


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
