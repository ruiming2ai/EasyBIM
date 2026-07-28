# -*- coding: utf-8 -*-
"""Remove selected revisions from selected sheets and auto-hide leftover revision clouds."""

import clr
import hashlib
import json
import os
import tempfile

clr.AddReference("System.Windows.Forms")
import System.Windows.Forms as WinForms

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (
    BuiltInParameter,
    ElementId,
    FilteredElementCollector,
    Revision,
    RevisionCloud,
    Transaction,
    View,
    ViewSheet,
)

clr.AddReference("System")
from System.Collections.Generic import List as ClrList


def get_doc():
    try:
        uidoc = __revit__.ActiveUIDocument
        return uidoc.Document if uidoc else None
    except Exception:
        return None


doc = get_doc()
if doc is None:
    WinForms.MessageBox.Show(
        "No active project document was found.",
        "Remove Revisions on Sheets",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Warning,
    )
    raise SystemExit


def eid_int(eid):
    if eid is None:
        return None
    try:
        return int(eid.IntegerValue)
    except Exception:
        pass
    try:
        return int(eid.Value)
    except Exception:
        pass
    try:
        return int(str(eid))
    except Exception:
        return None


def format_ids(ids):
    values = sorted(set([x for x in ids if x is not None]))
    return ", ".join(str(x) for x in values) if values else "N/A"


def doc_key():
    try:
        base = doc.PathName or doc.Title or "Untitled"
    except Exception:
        base = "UnknownDoc"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def prefs_path():
    return os.path.join(tempfile.gettempdir(), "rev_remove_prefs_{}.json".format(doc_key()))


def load_prev_selection():
    path = prefs_path()
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
            return set(int(x) for x in data.get("selected_revision_ids", []))
    except Exception:
        pass
    return set()


def save_prev_selection(selected_ids):
    path = prefs_path()
    try:
        with open(path, "w") as f:
            json.dump({"selected_revision_ids": [int(x) for x in selected_ids]}, f)
    except Exception:
        pass


def rev_seq(rev):
    try:
        return int(rev.SequenceNumber)
    except Exception:
        try:
            return int(eid_int(rev.Id) or 0)
        except Exception:
            return 0


def rev_num(rev):
    for attr in ("RevisionNumber", "Number"):
        try:
            value = getattr(rev, attr)
            if value:
                return str(value)
        except Exception:
            pass
    try:
        p = rev.get_Parameter(BuiltInParameter.REVISION_NUMBER)
        if p:
            value = p.AsString()
            if value:
                return value
    except Exception:
        pass
    return str(rev_seq(rev))


def rev_desc(rev):
    try:
        value = rev.Description
        if value:
            return value
    except Exception:
        pass
    try:
        p = rev.get_Parameter(BuiltInParameter.REVISION_DESCRIPTION)
        if p:
            value = p.AsString()
            if value:
                return value
    except Exception:
        pass
    return ""


def collect_revision_cloud_map():
    mapping = {}
    clouds = FilteredElementCollector(doc).OfClass(RevisionCloud).ToElements()
    for cloud in clouds:
        try:
            rid_i = eid_int(getattr(cloud, "RevisionId", None))
            cid_i = eid_int(cloud.Id)
            if rid_i is None or cid_i is None:
                continue
            mapping.setdefault(rid_i, []).append(cid_i)
        except Exception:
            continue
    for rid in mapping:
        mapping[rid] = sorted(set(mapping[rid]))
    return mapping


def collect_cloud_element_ids_for_revisions(revision_ids_int):
    target_ids = set([int(x) for x in revision_ids_int if x is not None])
    cloud_element_ids = []
    for cloud in FilteredElementCollector(doc).OfClass(RevisionCloud).ToElements():
        try:
            rid_i = eid_int(getattr(cloud, "RevisionId", None))
            if rid_i in target_ids:
                cloud_element_ids.append(cloud.Id)
        except Exception:
            continue
    cloud_ids_int = sorted(set([eid_int(x) for x in cloud_element_ids if eid_int(x) is not None]))
    return cloud_element_ids, cloud_ids_int


def hide_clouds_in_all_views(cloud_element_ids, revision_ids_int, cloud_ids_int):
    views = FilteredElementCollector(doc).OfClass(View).WhereElementIsNotElementType().ToElements()
    result = {
        "views_with_changes": 0,
        "hidden_instances": 0,
        "processed_view_ids": [],
        "failed_views": [],
        "revision_ids": sorted(set(revision_ids_int)),
        "cloud_ids": sorted(set(cloud_ids_int)),
    }

    if not cloud_element_ids:
        return result

    transaction = Transaction(doc, "Auto Hide Revision Clouds for Leftovers")
    transaction.Start()
    try:
        for view in views:
            try:
                if view.IsTemplate:
                    continue

                to_hide = []
                for cloud_id in cloud_element_ids:
                    cloud = doc.GetElement(cloud_id)
                    if cloud is None:
                        continue
                    try:
                        if cloud.CanBeHidden(view) and (not cloud.IsHidden(view)):
                            to_hide.append(cloud_id)
                    except Exception:
                        continue

                if to_hide:
                    hide_list = ClrList[ElementId]()
                    for cid in to_hide:
                        hide_list.Add(cid)
                    try:
                        view.HideElements(hide_list)
                        result["views_with_changes"] += 1
                        result["hidden_instances"] += hide_list.Count
                        result["processed_view_ids"].append(eid_int(view.Id))
                    except Exception as ex:
                        result["failed_views"].append(
                            {
                                "view_id": eid_int(view.Id),
                                "view_name": getattr(view, "Name", "") or "",
                                "revision_ids": sorted(set(revision_ids_int)),
                                "cloud_ids": sorted(set(cloud_ids_int)),
                                "error": str(ex),
                            }
                        )
            except Exception as ex:
                result["failed_views"].append(
                    {
                        "view_id": eid_int(view.Id),
                        "view_name": getattr(view, "Name", "") or "",
                        "revision_ids": sorted(set(revision_ids_int)),
                        "cloud_ids": sorted(set(cloud_ids_int)),
                        "error": str(ex),
                    }
                )
        transaction.Commit()
    except Exception as tx_error:
        try:
            transaction.RollBack()
        except Exception:
            pass
        result["failed_views"].append(
            {
                "view_id": "N/A",
                "view_name": "Transaction",
                "revision_ids": sorted(set(revision_ids_int)),
                "cloud_ids": sorted(set(cloud_ids_int)),
                "error": "Transaction error: {}".format(tx_error),
            }
        )

    return result


BUTTON_H = 24
BUTTON_W = 80


def pick_revisions_dialog(all_revisions):
    rows = []
    for revision in all_revisions:
        try:
            rows.append(
                {
                    "id": eid_int(revision.Id),
                    "seq": rev_seq(revision),
                    "num": rev_num(revision),
                    "desc": rev_desc(revision),
                    "obj": revision,
                }
            )
        except Exception:
            pass
    rows.sort(key=lambda x: (x["seq"], x["num"]))

    prev = load_prev_selection()

    f = WinForms.Form()
    f.Text = "Select Revisions (to REMOVE from selected sheets)"
    f.Width = 900
    f.Height = 560
    f.StartPosition = WinForms.FormStartPosition.CenterScreen
    f.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    f.AutoScaleMode = WinForms.AutoScaleMode.Dpi

    lv = WinForms.ListView()
    lv.View = WinForms.View.Details
    lv.CheckBoxes = True
    lv.FullRowSelect = True
    lv.GridLines = True
    lv.Left = 12
    lv.Top = 12
    lv.Width = 860
    lv.Height = 440
    lv.Anchor = (
        WinForms.AnchorStyles.Top
        | WinForms.AnchorStyles.Left
        | WinForms.AnchorStyles.Right
        | WinForms.AnchorStyles.Bottom
    )

    lv.Columns.Add("Select", 80)
    lv.Columns.Add("Seq", 60)
    lv.Columns.Add("Number", 160)
    lv.Columns.Add("Description", 540)

    for row in rows:
        item = WinForms.ListViewItem("")
        item.SubItems.Add(str(row["seq"]))
        item.SubItems.Add(row["num"])
        item.SubItems.Add(row["desc"])
        item.Tag = row
        try:
            if row["id"] in prev:
                item.Checked = True
        except Exception:
            pass
        lv.Items.Add(item)

    base_top = 468 - BUTTON_H

    btn_sel_all = WinForms.Button()
    btn_sel_all.Text = "Select All"
    btn_sel_all.AutoSize = True
    btn_sel_all.Top = base_top
    btn_sel_all.Left = 12
    btn_sel_all.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left

    btn_clr_all = WinForms.Button()
    btn_clr_all.Text = "Clear All"
    btn_clr_all.AutoSize = True
    btn_clr_all.Top = base_top
    btn_clr_all.Left = 110 + BUTTON_W
    btn_clr_all.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left

    def select_all(sender, args):
        del sender, args
        try:
            for item in lv.Items:
                item.Checked = True
        except Exception:
            pass

    def clear_all(sender, args):
        del sender, args
        try:
            for item in lv.Items:
                item.Checked = False
        except Exception:
            pass

    btn_sel_all.Click += select_all
    btn_clr_all.Click += clear_all

    ok = WinForms.Button()
    ok.Text = "Run"
    ok.DialogResult = WinForms.DialogResult.OK
    ok.AutoSize = True
    ok.Top = base_top
    ok.Left = 700
    ok.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right

    cancel = WinForms.Button()
    cancel.Text = "Cancel"
    cancel.DialogResult = WinForms.DialogResult.Cancel
    cancel.AutoSize = True
    cancel.Top = base_top
    cancel.Left = 790
    cancel.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right

    f.Controls.Add(lv)
    f.Controls.Add(btn_sel_all)
    f.Controls.Add(btn_clr_all)
    f.Controls.Add(ok)
    f.Controls.Add(cancel)
    f.AcceptButton = ok
    f.CancelButton = cancel

    if f.ShowDialog() == WinForms.DialogResult.Cancel:
        return None, None

    chosen = []
    for item in lv.Items:
        try:
            if item.Checked:
                chosen.append(item.Tag)
        except Exception:
            pass

    if not chosen:
        WinForms.MessageBox.Show(
            "Please select at least one revision.",
            "No Selection",
            WinForms.MessageBoxButtons.OK,
            WinForms.MessageBoxIcon.Information,
        )
        return None, None

    chosen_ids = set()
    chosen_numbers = []
    for item in chosen:
        if item["id"] is not None:
            chosen_ids.add(int(item["id"]))
            chosen_numbers.append(item["num"] or str(item["seq"]))

    save_prev_selection(chosen_ids)
    return chosen_ids, chosen_numbers


def pick_sheets_dialog(all_sheets):
    rows = []
    for sheet in all_sheets:
        try:
            number = (sheet.get_Parameter(BuiltInParameter.SHEET_NUMBER).AsString() or "").strip()
        except Exception:
            number = ""
        try:
            name = (sheet.get_Parameter(BuiltInParameter.SHEET_NAME).AsString() or "").strip()
        except Exception:
            name = ""
        rows.append({"id": eid_int(sheet.Id), "num": number, "name": name, "obj": sheet})
    rows.sort(key=lambda x: (x["num"], x["name"]))

    f = WinForms.Form()
    f.Text = "Select Sheets (remove chosen revisions from schedule)"
    f.Width = 900
    f.Height = 560
    f.StartPosition = WinForms.FormStartPosition.CenterScreen
    f.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    f.AutoScaleMode = WinForms.AutoScaleMode.Dpi

    lv = WinForms.ListView()
    lv.View = WinForms.View.Details
    lv.CheckBoxes = True
    lv.FullRowSelect = True
    lv.GridLines = True
    lv.Left = 12
    lv.Top = 12
    lv.Width = 860
    lv.Height = 440
    lv.Anchor = (
        WinForms.AnchorStyles.Top
        | WinForms.AnchorStyles.Left
        | WinForms.AnchorStyles.Right
        | WinForms.AnchorStyles.Bottom
    )

    lv.Columns.Add("Select", 80)
    lv.Columns.Add("Sheet Number", 180)
    lv.Columns.Add("Sheet Name", 580)

    for row in rows:
        item = WinForms.ListViewItem("")
        item.SubItems.Add(row["num"])
        item.SubItems.Add(row["name"])
        item.Tag = row
        lv.Items.Add(item)

    base_top = 468 - BUTTON_H

    btn_sel_all = WinForms.Button()
    btn_sel_all.Text = "Select All"
    btn_sel_all.AutoSize = True
    btn_sel_all.Top = base_top
    btn_sel_all.Left = 12
    btn_sel_all.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left

    btn_clr_all = WinForms.Button()
    btn_clr_all.Text = "Clear All"
    btn_clr_all.AutoSize = True
    btn_clr_all.Top = base_top
    btn_clr_all.Left = 110 + BUTTON_W
    btn_clr_all.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left

    def select_all(sender, args):
        del sender, args
        try:
            for item in lv.Items:
                item.Checked = True
        except Exception:
            pass

    def clear_all(sender, args):
        del sender, args
        try:
            for item in lv.Items:
                item.Checked = False
        except Exception:
            pass

    btn_sel_all.Click += select_all
    btn_clr_all.Click += clear_all

    ok = WinForms.Button()
    ok.Text = "Run"
    ok.DialogResult = WinForms.DialogResult.OK
    ok.AutoSize = True
    ok.Top = base_top
    ok.Left = 700
    ok.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right

    cancel = WinForms.Button()
    cancel.Text = "Cancel"
    cancel.DialogResult = WinForms.DialogResult.Cancel
    cancel.AutoSize = True
    cancel.Top = base_top
    cancel.Left = 790
    cancel.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right

    f.Controls.Add(lv)
    f.Controls.Add(btn_sel_all)
    f.Controls.Add(btn_clr_all)
    f.Controls.Add(ok)
    f.Controls.Add(cancel)
    f.AcceptButton = ok
    f.CancelButton = cancel

    if f.ShowDialog() == WinForms.DialogResult.Cancel:
        return None

    chosen = []
    for item in lv.Items:
        try:
            if item.Checked:
                chosen.append(item.Tag)
        except Exception:
            pass

    if not chosen:
        WinForms.MessageBox.Show(
            "Please select at least one sheet.",
            "No Selection",
            WinForms.MessageBoxButtons.OK,
            WinForms.MessageBoxIcon.Information,
        )
        return None

    return chosen


all_revisions = list(FilteredElementCollector(doc).OfClass(Revision).ToElements())
if not all_revisions:
    WinForms.MessageBox.Show(
        "No revisions found in the project.",
        "Remove Revisions on Sheets",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Information,
    )
    raise SystemExit

sel_rev_ids, sel_rev_numbers = pick_revisions_dialog(all_revisions)
if sel_rev_ids is None:
    raise SystemExit

all_sheets = list(FilteredElementCollector(doc).OfClass(ViewSheet).WhereElementIsNotElementType().ToElements())
if not all_sheets:
    WinForms.MessageBox.Show(
        "No sheets found in the project.",
        "Remove Revisions on Sheets",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Information,
    )
    raise SystemExit

chosen_sheets = pick_sheets_dialog(all_sheets)
if chosen_sheets is None:
    raise SystemExit

sel_rev_ids_int = sorted(set([int(x) for x in sel_rev_ids]))
sel_rev_id_set = set(sel_rev_ids_int)
revision_cloud_map = collect_revision_cloud_map()
revision_id_to_seq = {}
for revision in all_revisions:
    rid_i = eid_int(revision.Id)
    if rid_i is not None:
        revision_id_to_seq[rid_i] = rev_seq(revision)

changed_count = 0
unchanged_count = 0
failed_sheets_exceptions = []
processed_sheets = 0

remove_transaction = Transaction(doc, "Remove Revisions on Sheets")
remove_transaction.Start()
try:
    for sheet_entry in chosen_sheets:
        sheet = sheet_entry["obj"]
        processed_sheets += 1
        try:
            additional_ids = []
            has_get_additional = hasattr(sheet, "GetAdditionalRevisionIds")
            has_set_additional = hasattr(sheet, "SetAdditionalRevisionIds")

            if has_get_additional:
                try:
                    additional_ids = list(sheet.GetAdditionalRevisionIds())
                except Exception:
                    additional_ids = []
            else:
                try:
                    additional_ids = list(sheet.GetAllRevisionIds())
                except Exception:
                    additional_ids = []

            add_ints = [eid_int(aid) for aid in additional_ids if eid_int(aid) is not None]
            before_set = set(add_ints)
            after_ints = [rid for rid in add_ints if rid not in sel_rev_id_set]
            sheet_changed = set(after_ints) != before_set

            if has_set_additional and has_get_additional:
                new_list = ClrList[ElementId]()
                for rid in after_ints:
                    new_list.Add(ElementId(int(rid)))
                try:
                    sheet.SetAdditionalRevisionIds(new_list)
                except Exception:
                    for rid in (before_set - set(after_ints)):
                        try:
                            sheet.RemoveRevision(ElementId(int(rid)))
                        except Exception:
                            pass
            else:
                for rid in (before_set - set(after_ints)):
                    try:
                        sheet.RemoveRevision(ElementId(int(rid)))
                    except Exception:
                        pass

            if sheet_changed:
                changed_count += 1
            else:
                unchanged_count += 1
        except Exception as ex:
            cloud_ids = []
            for rid in sel_rev_ids_int:
                cloud_ids.extend(revision_cloud_map.get(rid, []))
            failed_sheets_exceptions.append(
                {
                    "sheet_num": sheet_entry["num"],
                    "sheet_name": sheet_entry["name"],
                    "revision_ids": sel_rev_ids_int,
                    "cloud_ids": sorted(set(cloud_ids)),
                    "error": str(ex),
                }
            )
    remove_transaction.Commit()
except Exception as tx_error:
    try:
        remove_transaction.RollBack()
    except Exception:
        pass
    WinForms.MessageBox.Show(
        "Unexpected transaction error:\n{}".format(tx_error),
        "Remove Revisions on Sheets",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Error,
    )
    raise SystemExit

try:
    doc.Regenerate()
except Exception:
    pass

leftover_before = []
for sheet_entry in chosen_sheets:
    sheet = sheet_entry["obj"]
    try:
        post_all = set([eid_int(x) for x in sheet.GetAllRevisionIds() if eid_int(x) is not None])
        leftover_ids = sorted(list(post_all & sel_rev_id_set))
    except Exception:
        leftover_ids = []

    if leftover_ids:
        leftover_before.append(
            {
                "sheet_num": sheet_entry["num"],
                "sheet_name": sheet_entry["name"],
                "revision_ids": leftover_ids,
                "sequence_ids": sorted(
                    set([revision_id_to_seq.get(rid) for rid in leftover_ids if revision_id_to_seq.get(rid) is not None])
                ),
            }
        )

auto_hide_result = None
auto_hide_cloud_ids_int = []
leftover_after = []
leftover_revision_ids_union = sorted(set([rid for row in leftover_before for rid in row["revision_ids"]]))
leftover_sequences_union = sorted(
    set([revision_id_to_seq.get(rid) for rid in leftover_revision_ids_union if revision_id_to_seq.get(rid) is not None])
)

if leftover_before:
    auto_hide_cloud_element_ids, auto_hide_cloud_ids_int = collect_cloud_element_ids_for_revisions(
        leftover_revision_ids_union
    )
    auto_hide_result = hide_clouds_in_all_views(
        auto_hide_cloud_element_ids, leftover_revision_ids_union, auto_hide_cloud_ids_int
    )

    try:
        doc.Regenerate()
    except Exception:
        pass

    for sheet_entry in chosen_sheets:
        sheet = sheet_entry["obj"]
        try:
            post_all_after = set([eid_int(x) for x in sheet.GetAllRevisionIds() if eid_int(x) is not None])
            leftover_ids_after = sorted(list(post_all_after & sel_rev_id_set))
        except Exception:
            leftover_ids_after = []

        if leftover_ids_after:
            leftover_after.append(
                {
                    "sheet_num": sheet_entry["num"],
                    "sheet_name": sheet_entry["name"],
                    "revision_ids": leftover_ids_after,
                    "sequence_ids": sorted(
                        set(
                            [
                                revision_id_to_seq.get(rid)
                                for rid in leftover_ids_after
                                if revision_id_to_seq.get(rid) is not None
                            ]
                        )
                    ),
                }
            )

summary_lines = []
summary_lines.append("Completed remove operation.")
summary_lines.append("Selected revision numbers: {}".format(", ".join(sel_rev_numbers) if sel_rev_numbers else "N/A"))
summary_lines.append("Selected revision IDs: {}".format(format_ids(sel_rev_ids_int)))
summary_lines.append("Sheets processed: {}".format(processed_sheets))
summary_lines.append("Sheets changed: {}".format(changed_count))
summary_lines.append("Sheets unchanged: {}".format(unchanged_count))

if failed_sheets_exceptions:
    summary_lines.append("")
    summary_lines.append("Failed sheets (unexpected errors):")
    for failed in failed_sheets_exceptions:
        summary_lines.append(
            "- Sheet: {} {}".format(failed.get("sheet_num", "") or "(no number)", failed.get("sheet_name", "") or "")
        )
        summary_lines.append("  Revision IDs: {}".format(format_ids(failed.get("revision_ids", []))))
        summary_lines.append("  Cloud IDs: {}".format(format_ids(failed.get("cloud_ids", []))))
        summary_lines.append("  Error: {}".format(failed.get("error", "Unknown error")))

if leftover_before:
    summary_lines.append("")
    summary_lines.append("Failed/Leftover section (persisted):")
    summary_lines.append("Leftovers detected before auto-hide:")
    for row in leftover_before:
        summary_lines.append(
            "- Sheet: {} {}".format(row.get("sheet_num", "") or "(no number)", row.get("sheet_name", "") or "")
        )
        summary_lines.append("  Revision IDs: {}".format(format_ids(row.get("revision_ids", []))))
        summary_lines.append("  Revision Sequences: {}".format(format_ids(row.get("sequence_ids", []))))

    summary_lines.append("")
    summary_lines.append("Auto-hide action:")
    summary_lines.append("Revision IDs auto-targeted: {}".format(format_ids(leftover_revision_ids_union)))
    summary_lines.append("Revision sequences auto-targeted: {}".format(format_ids(leftover_sequences_union)))
    summary_lines.append("Cloud IDs targeted for hide: {}".format(format_ids(auto_hide_cloud_ids_int)))
    if auto_hide_result:
        summary_lines.append("Views changed during auto-hide: {}".format(auto_hide_result["views_with_changes"]))
        summary_lines.append("Cloud instances hidden: {}".format(auto_hide_result["hidden_instances"]))

        if auto_hide_result["failed_views"]:
            summary_lines.append("Auto-hide view failures:")
            for failed in auto_hide_result["failed_views"]:
                summary_lines.append(
                    "- View: {} ({})".format(
                        failed.get("view_name", "") or "Unnamed View", failed.get("view_id", "N/A")
                    )
                )
                summary_lines.append("  Revision IDs: {}".format(format_ids(failed.get("revision_ids", []))))
                summary_lines.append("  Cloud IDs: {}".format(format_ids(failed.get("cloud_ids", []))))
                summary_lines.append("  Error: {}".format(failed.get("error", "Unknown error")))

    summary_lines.append("")
    if leftover_after:
        summary_lines.append("Leftovers remaining after auto-hide (true failures):")
        for row in leftover_after:
            row_cloud_ids = []
            for rid in row.get("revision_ids", []):
                row_cloud_ids.extend(revision_cloud_map.get(rid, []))
            summary_lines.append(
                "- Sheet: {} {}".format(row.get("sheet_num", "") or "(no number)", row.get("sheet_name", "") or "")
            )
            summary_lines.append("  Revision IDs: {}".format(format_ids(row.get("revision_ids", []))))
            summary_lines.append("  Revision Sequences: {}".format(format_ids(row.get("sequence_ids", []))))
            summary_lines.append("  Cloud IDs: {}".format(format_ids(row_cloud_ids)))
    else:
        summary_lines.append("After auto-hide, no leftovers remain on selected sheets.")

WinForms.MessageBox.Show(
    "\n".join(summary_lines),
    "Remove Revisions on Sheets",
    WinForms.MessageBoxButtons.OK,
    WinForms.MessageBoxIcon.Information,
)
