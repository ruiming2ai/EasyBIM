# -*- coding: utf-8 -*-
"""Hide revision clouds by selected revision sequences."""

import clr

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
        "Hide Revision Clouds by Sequences",
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


def format_ids(ids):
    values = sorted(set([x for x in ids if x is not None]))
    return ", ".join(str(x) for x in values) if values else "N/A"


def pick_revisions_ui(all_revisions):
    infos = []
    for revision in all_revisions:
        try:
            infos.append(
                {
                    "id": eid_int(revision.Id),
                    "seq": rev_seq(revision),
                    "num": rev_num(revision),
                    "desc": rev_desc(revision),
                }
            )
        except Exception:
            pass
    infos.sort(key=lambda x: (x["seq"], x["num"]))

    f = WinForms.Form()
    f.Text = "Select Revisions"
    f.Width = 900
    f.Height = 560
    f.StartPosition = WinForms.FormStartPosition.CenterScreen
    f.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    f.AutoScaleMode = WinForms.AutoScaleMode.Dpi

    pnl = WinForms.Panel()
    pnl.Height = 56
    pnl.Dock = WinForms.DockStyle.Bottom

    btn_sel_all = WinForms.Button()
    btn_sel_all.Text = "Select All"
    btn_sel_all.AutoSize = True
    btn_sel_all.Left = 12
    btn_sel_all.Top = 12

    btn_clr_all = WinForms.Button()
    btn_clr_all.Text = "Clear All"
    btn_clr_all.AutoSize = True
    btn_clr_all.Top = 12

    ok = WinForms.Button()
    ok.Text = "Run"
    ok.DialogResult = WinForms.DialogResult.OK
    ok.AutoSize = True
    ok.Top = 12
    ok.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right

    cancel = WinForms.Button()
    cancel.Text = "Cancel"
    cancel.DialogResult = WinForms.DialogResult.Cancel
    cancel.AutoSize = True
    cancel.Top = 12
    cancel.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right

    def layout(sender=None, args=None):
        del sender, args
        cancel.Left = pnl.Width - cancel.Width - 12
        ok.Left = cancel.Left - ok.Width - 12
        gap = int(max(12, 0.5 * btn_sel_all.Width))
        btn_clr_all.Left = btn_sel_all.Left + btn_sel_all.Width + gap

    pnl.Resize += layout
    pnl.Controls.Add(btn_sel_all)
    pnl.Controls.Add(btn_clr_all)
    pnl.Controls.Add(ok)
    pnl.Controls.Add(cancel)

    lv = WinForms.ListView()
    lv.View = WinForms.View.Details
    lv.CheckBoxes = True
    lv.FullRowSelect = True
    lv.GridLines = True
    lv.Dock = WinForms.DockStyle.Fill

    lv.Columns.Add("Select", 80)
    lv.Columns.Add("Seq", 60)
    lv.Columns.Add("Number", 160)
    lv.Columns.Add("Description", 540)

    for item in infos:
        row = WinForms.ListViewItem("")
        row.SubItems.Add(str(item["seq"]))
        row.SubItems.Add(item["num"] or "")
        row.SubItems.Add(item["desc"] or "")
        row.Tag = item
        lv.Items.Add(row)

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

    f.Controls.Add(pnl)
    f.Controls.Add(lv)
    f.AcceptButton = ok
    f.CancelButton = cancel

    layout()
    if f.ShowDialog() == WinForms.DialogResult.Cancel:
        return None

    chosen = []
    for item in lv.Items:
        try:
            if item.Checked and item.Tag:
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
        return None

    return sorted(set([int(x["seq"]) for x in chosen if x is not None]))


all_revisions = FilteredElementCollector(doc).OfClass(Revision).ToElements()
seqs = pick_revisions_ui(all_revisions)
if not seqs:
    raise SystemExit

target_revisions = [r for r in all_revisions if rev_seq(r) in set(seqs)]
target_revision_ids = sorted(set([eid_int(r.Id) for r in target_revisions if eid_int(r.Id) is not None]))

if not target_revisions:
    WinForms.MessageBox.Show(
        "No revisions found for sequence(s): {}\nRevision IDs: N/A\nCloud IDs: N/A".format(seqs),
        "Hide Revision Clouds by Sequences",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Information,
    )
    raise SystemExit

target_cloud_ids = []
for cloud in FilteredElementCollector(doc).OfClass(RevisionCloud).ToElements():
    try:
        rid_i = eid_int(getattr(cloud, "RevisionId", None))
        if rid_i in set(target_revision_ids):
            target_cloud_ids.append(cloud.Id)
    except Exception:
        continue
target_cloud_ids_int = sorted(set([eid_int(x) for x in target_cloud_ids if eid_int(x) is not None]))

if not target_cloud_ids:
    WinForms.MessageBox.Show(
        "No revision clouds found for sequence(s): {}\nRevision IDs: {}\nCloud IDs: N/A".format(
            seqs, format_ids(target_revision_ids)
        ),
        "Hide Revision Clouds by Sequences",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Information,
    )
    raise SystemExit

views = FilteredElementCollector(doc).OfClass(View).WhereElementIsNotElementType().ToElements()
views_with_changes = 0
total_hidden_instances = 0
processed_view_ids = []
failed_views = []

transaction = Transaction(doc, "Hide Revision Clouds by Sequences")
transaction.Start()
try:
    for view in views:
        try:
            if view.IsTemplate:
                continue

            to_hide = []
            for cloud_id in target_cloud_ids:
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
                    views_with_changes += 1
                    total_hidden_instances += hide_list.Count
                    processed_view_ids.append(eid_int(view.Id))
                except Exception as ex:
                    failed_views.append(
                        {
                            "view_id": eid_int(view.Id),
                            "view_name": getattr(view, "Name", "") or "",
                            "revision_ids": target_revision_ids,
                            "cloud_ids": target_cloud_ids_int,
                            "error": str(ex),
                        }
                    )
        except Exception as ex:
            failed_views.append(
                {
                    "view_id": eid_int(view.Id),
                    "view_name": getattr(view, "Name", "") or "",
                    "revision_ids": target_revision_ids,
                    "cloud_ids": target_cloud_ids_int,
                    "error": str(ex),
                }
            )
    transaction.Commit()
except Exception as tx_error:
    try:
        transaction.RollBack()
    except Exception:
        pass
    WinForms.MessageBox.Show(
        "Unexpected transaction error:\n{}\nRevision IDs: {}\nCloud IDs: {}".format(
            tx_error, format_ids(target_revision_ids), format_ids(target_cloud_ids_int)
        ),
        "Hide Revision Clouds by Sequences",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Error,
    )
    raise SystemExit

lines = []
lines.append("Done.")
lines.append("Selected sequences: {}".format(", ".join(str(x) for x in seqs)))
lines.append("Revision IDs: {}".format(format_ids(target_revision_ids)))
lines.append("Cloud IDs: {}".format(format_ids(target_cloud_ids_int)))
lines.append("Views changed: {}".format(views_with_changes))
lines.append("Instances hidden: {}".format(total_hidden_instances))

if failed_views:
    lines.append("")
    lines.append("Failure details (with IDs):")
    for failed in failed_views:
        view_label = "{} ({})".format(failed.get("view_name", "") or "Unnamed View", failed.get("view_id", "N/A"))
        lines.append("- View: {}".format(view_label))
        lines.append("  Revision IDs: {}".format(format_ids(failed.get("revision_ids", []))))
        lines.append("  Cloud IDs: {}".format(format_ids(failed.get("cloud_ids", []))))
        lines.append("  Error: {}".format(failed.get("error", "Unknown error")))

WinForms.MessageBox.Show(
    "\n".join(lines),
    "Hide Revision Clouds by Sequences",
    WinForms.MessageBoxButtons.OK,
    WinForms.MessageBoxIcon.Information,
)
