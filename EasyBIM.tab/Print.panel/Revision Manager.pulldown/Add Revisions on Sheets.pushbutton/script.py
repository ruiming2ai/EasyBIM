# -*- coding: utf-8 -*-
"""Add selected revisions to selected sheets (pyRevit version)."""

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
        "Add Revisions on Sheets",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Warning,
    )
    raise SystemExit


from easybim.compat import eid_to_int as eid_int
from easybim.compat import element_id_factory

eid_from_int = element_id_factory(ElementId)


def doc_key():
    try:
        base = doc.PathName or doc.Title or "Untitled"
    except Exception:
        base = "UnknownDoc"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def prefs_path():
    return os.path.join(tempfile.gettempdir(), "rev_apply_prefs_{}.json".format(doc_key()))


def load_prev_revision_ids():
    try:
        path = prefs_path()
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
            return set(int(x) for x in data.get("selected_revision_ids", []))
    except Exception:
        pass
    return set()


def save_prev_revision_ids(selected_ids_iter):
    try:
        path = prefs_path()
        data = {"selected_revision_ids": [int(x) for x in selected_ids_iter if x is not None]}
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def rev_seq(rev):
    try:
        return int(getattr(rev, "SequenceNumber"))
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
    for bip_name in ("REVISION_NUMBER", "PROJECT_REVISION_NUMBER", "PROJECT_REVISION_REVISION_NUMBER"):
        try:
            bip = getattr(BuiltInParameter, bip_name)
            p = rev.get_Parameter(bip)
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
    for bip_name in ("REVISION_DESCRIPTION", "PROJECT_REVISION_DESCRIPTION"):
        try:
            bip = getattr(BuiltInParameter, bip_name)
            p = rev.get_Parameter(bip)
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


def shift_buttons_up_by_one_height(listview, buttons, base_top=468):
    btn_h = buttons[0].Height or 24
    new_top = max(12, base_top - btn_h)
    for button in buttons:
        button.Top = new_top
    listview.Height = max(200, new_top - listview.Top - 8)


def pick_revisions():
    all_revisions = FilteredElementCollector(doc).OfClass(Revision).ToElements()
    infos = []
    for revision in all_revisions:
        try:
            infos.append(
                {
                    "id": eid_int(revision.Id),
                    "sequence": rev_seq(revision),
                    "number": rev_num(revision),
                    "description": rev_desc(revision),
                    "obj": revision,
                }
            )
        except Exception:
            pass

    infos.sort(key=lambda x: (x["sequence"], x["number"]))
    prev_checked = load_prev_revision_ids()

    f = WinForms.Form()
    f.Text = "Select Revisions"
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

    for item in infos:
        row = WinForms.ListViewItem("")
        row.SubItems.Add(str(item["sequence"]))
        row.SubItems.Add(item["number"] or "")
        row.SubItems.Add(item["description"] or "")
        row.Tag = item
        try:
            if item["id"] in prev_checked:
                row.Checked = True
        except Exception:
            pass
        lv.Items.Add(row)

    btn_sel_all = WinForms.Button()
    btn_sel_all.Text = "Select All"
    btn_sel_all.AutoSize = True
    btn_sel_all.Left = 12

    btn_clr_all = WinForms.Button()
    btn_clr_all.Text = "Clear All"
    btn_clr_all.AutoSize = True
    btn_clr_all.Left = btn_sel_all.Right + 42

    def select_all(sender, args):
        del sender, args
        try:
            for it in lv.Items:
                it.Checked = True
        except Exception:
            pass

    def clear_all(sender, args):
        del sender, args
        try:
            for it in lv.Items:
                it.Checked = False
        except Exception:
            pass

    btn_sel_all.Click += select_all
    btn_clr_all.Click += clear_all

    ok = WinForms.Button()
    ok.Text = "Run"
    ok.DialogResult = WinForms.DialogResult.OK
    ok.AutoSize = True
    ok.Left = 700

    cancel = WinForms.Button()
    cancel.Text = "Cancel"
    cancel.DialogResult = WinForms.DialogResult.Cancel
    cancel.AutoSize = True
    cancel.Left = 790

    for button in (btn_sel_all, btn_clr_all, ok, cancel):
        button.Top = 468
    shift_buttons_up_by_one_height(lv, (btn_sel_all, btn_clr_all, ok, cancel), base_top=468)

    btn_sel_all.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left
    btn_clr_all.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left
    ok.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right
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
            chosen_numbers.append(item["number"] or str(item["sequence"]))

    save_prev_revision_ids(chosen_ids)
    return chosen_ids, chosen_numbers


def pick_sheets():
    sheets = FilteredElementCollector(doc).OfClass(ViewSheet).WhereElementIsNotElementType().ToElements()
    entries = []
    for sheet in sheets:
        try:
            number = getattr(sheet, "SheetNumber", None) or ""
        except Exception:
            number = ""
        try:
            name = getattr(sheet, "Name", None) or ""
        except Exception:
            name = ""
        entries.append({"id": eid_int(sheet.Id), "number": number, "name": name, "obj": sheet})
    entries.sort(key=lambda x: (x["number"], x["name"]))

    f = WinForms.Form()
    f.Text = "Select Sheets"
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
    lv.Columns.Add("Sheet Name", 600)

    for entry in entries:
        row = WinForms.ListViewItem("")
        row.SubItems.Add(entry["number"])
        row.SubItems.Add(entry["name"])
        row.Tag = entry
        lv.Items.Add(row)

    btn_sel_all = WinForms.Button()
    btn_sel_all.Text = "Select All"
    btn_sel_all.AutoSize = True
    btn_sel_all.Left = 12

    btn_clr_all = WinForms.Button()
    btn_clr_all.Text = "Clear All"
    btn_clr_all.AutoSize = True
    btn_clr_all.Left = btn_sel_all.Right + 42

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
    ok.Left = 700

    cancel = WinForms.Button()
    cancel.Text = "Cancel"
    cancel.DialogResult = WinForms.DialogResult.Cancel
    cancel.AutoSize = True
    cancel.Left = 790

    for button in (btn_sel_all, btn_clr_all, ok, cancel):
        button.Top = 468
    shift_buttons_up_by_one_height(lv, (btn_sel_all, btn_clr_all, ok, cancel), base_top=468)

    btn_sel_all.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left
    btn_clr_all.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left
    ok.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right
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

    return [x["obj"] for x in chosen]


def ensure_revisions_on_sheet(sheet, rev_element_ids):
    added = 0
    skipped = 0
    failed = 0
    failed_rev_ids = []

    try:
        existing_all = set(eid_int(x) for x in sheet.GetAllRevisionIds())
    except Exception:
        existing_all = set()

    can_add_method = hasattr(sheet, "AddRevision")
    get_add = getattr(sheet, "GetAdditionalRevisionIds", None)
    set_add = getattr(sheet, "SetAdditionalRevisionIds", None)

    additional_ids = None
    if get_add and set_add:
        try:
            additional_ids = ClrList[ElementId]()
            for item in get_add():
                additional_ids.Add(item)
        except Exception:
            additional_ids = None

    for rid in rev_element_ids:
        rid_i = eid_int(rid)
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
                    if eid_int(item) == rid_i:
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


def format_ids(ids):
    values = sorted(set([x for x in ids if x is not None]))
    return ", ".join(str(x) for x in values) if values else "N/A"


rev_ids_ints, rev_numbers = pick_revisions()
if rev_ids_ints is None:
    raise SystemExit

selected_rev_eids = [eid_from_int(x) for x in rev_ids_ints]
chosen_sheets = pick_sheets()
if chosen_sheets is None:
    raise SystemExit

revision_cloud_map = collect_revision_cloud_map()

total_added = 0
total_skipped = 0
total_failed = 0
affected = 0
failure_entries = []

transaction = Transaction(doc, "Add Revisions on Sheets")
transaction.Start()
try:
    for sheet in chosen_sheets:
        try:
            added, skipped, failed, failed_rev_ids = ensure_revisions_on_sheet(sheet, selected_rev_eids)
            total_added += added
            total_skipped += skipped
            total_failed += failed
            if added > 0:
                affected += 1
            if failed_rev_ids:
                cloud_ids = []
                for rid in failed_rev_ids:
                    cloud_ids.extend(revision_cloud_map.get(rid, []))
                failure_entries.append(
                    {
                        "sheet_number": getattr(sheet, "SheetNumber", "") or "",
                        "sheet_name": getattr(sheet, "Name", "") or "",
                        "revision_ids": failed_rev_ids,
                        "cloud_ids": sorted(set(cloud_ids)),
                    }
                )
        except Exception as ex:
            sheet_number = getattr(sheet, "SheetNumber", "") or ""
            sheet_name = getattr(sheet, "Name", "") or ""
            failed_rev_ids = sorted(set(int(x) for x in rev_ids_ints))
            cloud_ids = []
            for rid in failed_rev_ids:
                cloud_ids.extend(revision_cloud_map.get(rid, []))
            failure_entries.append(
                {
                    "sheet_number": sheet_number,
                    "sheet_name": sheet_name,
                    "revision_ids": failed_rev_ids,
                    "cloud_ids": sorted(set(cloud_ids)),
                    "error": str(ex),
                }
            )
            total_failed += len(failed_rev_ids)
    transaction.Commit()
except Exception as tx_error:
    try:
        transaction.RollBack()
    except Exception:
        pass
    WinForms.MessageBox.Show(
        "Unexpected transaction error:\n{}".format(tx_error),
        "Add Revisions on Sheets",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Error,
    )
    raise SystemExit

lines = []
lines.append("Applied revisions to sheets.")
lines.append("Selected revision numbers: {}".format(", ".join(rev_numbers) if rev_numbers else "N/A"))
lines.append("Selected revision IDs: {}".format(format_ids(rev_ids_ints)))
lines.append("Sheets affected: {}".format(affected))
lines.append("Revisions added: {}".format(total_added))
lines.append("Already present / skipped: {}".format(total_skipped))
lines.append("Add failures (failed attempts): {}".format(total_failed))

if failure_entries:
    lines.append("")
    lines.append("Failure details (with IDs):")
    for entry in failure_entries:
        sheet_label = "{} {}".format(entry.get("sheet_number", ""), entry.get("sheet_name", "")).strip()
        if not sheet_label:
            sheet_label = "(unknown sheet)"
        lines.append("- Sheet: {}".format(sheet_label))
        lines.append("  Revision IDs: {}".format(format_ids(entry.get("revision_ids", []))))
        lines.append("  Cloud IDs: {}".format(format_ids(entry.get("cloud_ids", []))))
        if entry.get("error"):
            lines.append("  Error: {}".format(entry.get("error")))

WinForms.MessageBox.Show(
    "\n".join(lines),
    "Add Revisions on Sheets",
    WinForms.MessageBoxButtons.OK,
    WinForms.MessageBoxIcon.Information,
)
