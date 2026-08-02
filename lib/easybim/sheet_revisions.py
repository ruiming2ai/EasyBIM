# -*- coding: utf-8 -*-
"""Shared sheet-revision helpers for EasyBIM commands.

Extracted from the Revision Manager pushbuttons (Add/Remove Revisions on
Sheets, Hide/Unhide Revision Clouds by Sequences) so Sheet Manager can reuse
the same fallback chains. Pure-Python module: every Revit/.NET object arrives
as an argument (``DB``, ``framework``, ``eid_to_int``, factories), so the
desktop test suite can exercise the logic with fakes - same boundary rules as
``easybim.compat``.

``clr_list_factory`` must return an object with ``.Add(item)`` (a
``List[ElementId]`` in Revit; a small fake in tests).
"""

from __future__ import print_function


def get_locked_revision_ids(sheet, eid_to_int):
    """Cloud-driven revision ids (ints): on the sheet but not removable via
    the additional-revisions list."""
    all_ids = set()
    additional_ids = set()
    try:
        for rev_id in sheet.GetAllRevisionIds():
            value = eid_to_int(rev_id)
            if value is not None:
                all_ids.add(value)
    except Exception:
        pass
    try:
        for rev_id in sheet.GetAdditionalRevisionIds():
            value = eid_to_int(rev_id)
            if value is not None:
                additional_ids.add(value)
    except Exception:
        pass
    return all_ids - additional_ids


def ensure_revisions_on_sheet(sheet, rev_element_ids, eid_to_int,
                              clr_list_factory):
    """Add revisions to a sheet, preferring ``AddRevision`` and falling back
    to Get/SetAdditionalRevisionIds. Returns
    ``(added, skipped, failed, failed_rev_id_ints)``.

    Pattern-copied from Add Revisions on Sheets (behavior unchanged).
    """
    added = 0
    skipped = 0
    failed = 0
    failed_rev_ids = []

    try:
        existing_all = set(eid_to_int(x) for x in sheet.GetAllRevisionIds())
    except Exception:
        existing_all = set()

    can_add_method = hasattr(sheet, "AddRevision")
    get_add = getattr(sheet, "GetAdditionalRevisionIds", None)
    set_add = getattr(sheet, "SetAdditionalRevisionIds", None)

    additional_ids = None
    if get_add and set_add:
        try:
            additional_ids = clr_list_factory()
            for item in get_add():
                additional_ids.Add(item)
        except Exception:
            additional_ids = None

    for rid in rev_element_ids:
        rid_i = eid_to_int(rid)
        if rid_i in existing_all:
            skipped += 1
            continue

        if can_add_method:
            try:
                sheet.AddRevision(rid)
                added += 1
                existing_all.add(rid_i)
                continue
            except Exception:
                pass

        if additional_ids is not None and set_add is not None:
            try:
                already = False
                for item in additional_ids:
                    if eid_to_int(item) == rid_i:
                        already = True
                        break
                if not already:
                    additional_ids.Add(rid)
                    set_add(additional_ids)
                    added += 1
                    existing_all.add(rid_i)
                else:
                    skipped += 1
            except Exception:
                failed += 1
                if rid_i is not None:
                    failed_rev_ids.append(rid_i)
        else:
            failed += 1
            if rid_i is not None:
                failed_rev_ids.append(rid_i)

    return added, skipped, failed, sorted(set(failed_rev_ids))


def remove_revisions_from_sheet(sheet, remove_id_ints, eid_from_int,
                                eid_to_int, clr_list_factory):
    """Remove additional revisions by rebuilding the additional list, with a
    per-id ``RemoveRevision`` fallback. Returns
    ``(changed, removed_id_ints)``.

    Pattern-copied from Remove Revisions on Sheets (behavior unchanged).
    Cloud-driven revisions survive removal by design; callers detect them
    afterwards via ``GetAllRevisionIds``.
    """
    remove_set = set(remove_id_ints)
    has_get = hasattr(sheet, "GetAdditionalRevisionIds")
    has_set = hasattr(sheet, "SetAdditionalRevisionIds")

    additional_ids = []
    if has_get:
        try:
            additional_ids = list(sheet.GetAdditionalRevisionIds())
        except Exception:
            additional_ids = []
    else:
        try:
            additional_ids = list(sheet.GetAllRevisionIds())
        except Exception:
            additional_ids = []

    add_ints = [eid_to_int(aid) for aid in additional_ids
                if eid_to_int(aid) is not None]
    before_set = set(add_ints)
    after_ints = [rid for rid in add_ints if rid not in remove_set]
    changed = set(after_ints) != before_set
    removed = sorted(before_set - set(after_ints))

    if has_get and has_set:
        new_list = clr_list_factory()
        for rid in after_ints:
            new_list.Add(eid_from_int(rid))
        try:
            sheet.SetAdditionalRevisionIds(new_list)
        except Exception:
            for rid in removed:
                try:
                    sheet.RemoveRevision(eid_from_int(rid))
                except Exception:
                    pass
    else:
        for rid in removed:
            try:
                sheet.RemoveRevision(eid_from_int(rid))
            except Exception:
                pass

    return changed, removed


def collect_sheet_cloud_map(doc, sheets, DB, framework, eid_to_int):
    """Inventory revision clouds relevant to each sheet.

    Returns ``{sheet_id_int: {revision_id_int: [entry, ...]}}`` where entry is
    a dict with ``cloud`` (element), ``cloud_id``, ``revision_id``,
    ``owner_view_id`` (all ints) and ``is_hidden`` (hidden in its owner view).
    A cloud is relevant to a sheet when its owner view is the sheet itself or
    a view placed on the sheet.
    """
    try:
        clouds = DB.FilteredElementCollector(doc)\
            .OfClass(framework.get_type(DB.RevisionCloud))\
            .WhereElementIsNotElementType()\
            .ToElements()
    except Exception:
        clouds = []

    clouds_by_view = {}
    for cloud in clouds:
        try:
            owner_id = getattr(cloud, "OwnerViewId", None)
            owner_int = eid_to_int(owner_id)
            revision_int = eid_to_int(getattr(cloud, "RevisionId", None))
            if owner_int is None or revision_int is None:
                continue
            owner_view = doc.GetElement(owner_id)
            is_hidden = False
            try:
                is_hidden = bool(cloud.IsHidden(owner_view))
            except Exception:
                is_hidden = False
            clouds_by_view.setdefault(owner_int, []).append({
                "cloud": cloud,
                "cloud_id": eid_to_int(cloud.Id),
                "revision_id": revision_int,
                "owner_view_id": owner_int,
                "is_hidden": is_hidden,
            })
        except Exception:
            continue

    sheet_map = {}
    for sheet in sheets:
        try:
            sheet_int = eid_to_int(sheet.Id)
        except Exception:
            continue
        if sheet_int is None:
            continue
        view_ints = set([sheet_int])
        try:
            for view_id in sheet.GetAllPlacedViews():
                view_int = eid_to_int(view_id)
                if view_int is not None:
                    view_ints.add(view_int)
        except Exception:
            pass
        revision_map = {}
        for view_int in view_ints:
            for entry in clouds_by_view.get(view_int, []):
                revision_map.setdefault(
                    entry["revision_id"], []).append(entry)
        if revision_map:
            sheet_map[sheet_int] = revision_map
    return sheet_map


def hidden_cloud_revision_ids(revision_map, on_sheet_ids):
    """Revisions whose clouds are all hidden (candidates for unhiding).

    ``revision_map``: one sheet's value from ``collect_sheet_cloud_map``.
    ``on_sheet_ids``: the sheet's current ``GetAllRevisionIds`` ints.
    """
    result = set()
    for revision_id, entries in revision_map.items():
        if revision_id in on_sheet_ids:
            continue
        if entries and all(entry.get("is_hidden") for entry in entries):
            result.add(revision_id)
    return result


def set_clouds_hidden_in_view(view, cloud_entries, hidden, clr_list_factory,
                              eid_from_int):
    """Hide or unhide the given clouds in one view.

    ``cloud_entries``: entry dicts from ``collect_sheet_cloud_map`` whose
    owner view is ``view``. Returns ``(changed_count, failed_cloud_ids)``.
    Uses the CanBeHidden/IsHidden guards from Hide Revision Clouds by
    Sequences.
    """
    to_change = []
    for entry in cloud_entries:
        cloud = entry.get("cloud")
        cloud_id = entry.get("cloud_id")
        if cloud is None or cloud_id is None:
            continue
        try:
            if hidden:
                if cloud.CanBeHidden(view) and (not cloud.IsHidden(view)):
                    to_change.append(cloud_id)
            else:
                if cloud.IsHidden(view):
                    to_change.append(cloud_id)
        except Exception:
            continue
    if not to_change:
        return 0, []
    id_list = clr_list_factory()
    for cloud_id in to_change:
        id_list.Add(eid_from_int(cloud_id))
    try:
        if hidden:
            view.HideElements(id_list)
        else:
            view.UnhideElements(id_list)
        return len(to_change), []
    except Exception:
        return 0, sorted(to_change)
