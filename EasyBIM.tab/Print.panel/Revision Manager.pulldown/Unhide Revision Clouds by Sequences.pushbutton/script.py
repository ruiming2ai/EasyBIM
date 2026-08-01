# -*- coding: utf-8 -*-
"""Unhide revision clouds by selected revision sequences."""

import clr

clr.AddReference("System.Windows.Forms")
import System.Windows.Forms as WinForms

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (
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
        "Unhide Revision Clouds by Sequences",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Warning,
    )
    raise SystemExit


from easybim.compat import eid_to_int as eid_int


def rev_seq(rev):
    try:
        return int(getattr(rev, "SequenceNumber", 0))
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
    return str(rev_seq(rev))


def rev_desc(rev):
    try:
        value = rev.Description
        if value:
            return value
    except Exception:
        pass
    return ""


def format_ids(ids):
    values = sorted(set([x for x in ids if x is not None]))
    return ", ".join(str(x) for x in values) if values else "N/A"


def pick_revision_sequences(all_revisions):
    rows = []
    for revision in all_revisions:
        try:
            rows.append(
                {
                    "seq": rev_seq(revision),
                    "num": rev_num(revision),
                    "desc": rev_desc(revision),
                    "obj": revision,
                }
            )
        except Exception:
            pass
    rows.sort(key=lambda x: (x["seq"], x["num"]))

    f = WinForms.Form()
    f.Text = "Select Revision Sequence(s)"
    f.Width = 900
    f.Height = 560
    f.StartPosition = WinForms.FormStartPosition.CenterScreen
    f.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    f.AutoScaleMode = WinForms.AutoScaleMode.Dpi
    f.MinimizeBox = False
    f.MaximizeBox = False
    f.ShowInTaskbar = True

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
        gap = max(12, int(0.5 * btn_sel_all.Width))
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

    for row in rows:
        item = WinForms.ListViewItem("")
        item.SubItems.Add(str(row["seq"]))
        item.SubItems.Add(row["num"] or "")
        item.SubItems.Add(row["desc"] or "")
        item.Tag = row
        lv.Items.Add(item)

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

    selected = set()
    for item in lv.Items:
        try:
            if item.Checked and item.Tag:
                selected.add(int(item.Tag["seq"]))
        except Exception:
            pass

    if not selected:
        WinForms.MessageBox.Show(
            "Please select at least one revision sequence.",
            "No Selection",
            WinForms.MessageBoxButtons.OK,
            WinForms.MessageBoxIcon.Information,
        )
        return None

    return selected


seqs = pick_revision_sequences(FilteredElementCollector(doc).OfClass(Revision).ToElements())
if not seqs:
    raise SystemExit

revisions = FilteredElementCollector(doc).OfClass(Revision).ToElements()
target_revisions = [r for r in revisions if rev_seq(r) in seqs]
target_revision_ids = sorted(set([eid_int(r.Id) for r in target_revisions if eid_int(r.Id) is not None]))

if not target_revisions:
    WinForms.MessageBox.Show(
        "No revisions found for selected sequences.\nRevision IDs: N/A\nCloud IDs: N/A",
        "Unhide Revision Clouds by Sequences",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Information,
    )
    raise SystemExit

target_rev_id_set = set([r.Id for r in target_revisions])
target_cloud_ids = []
for cloud in FilteredElementCollector(doc).OfClass(RevisionCloud).ToElements():
    try:
        rid = getattr(cloud, "RevisionId", None)
        if rid is not None and rid in target_rev_id_set:
            target_cloud_ids.append(cloud.Id)
    except Exception:
        continue
target_cloud_ids_int = sorted(set([eid_int(x) for x in target_cloud_ids if eid_int(x) is not None]))

if not target_cloud_ids:
    WinForms.MessageBox.Show(
        "No revision clouds found for selected sequences.\nRevision IDs: {}\nCloud IDs: N/A".format(
            format_ids(target_revision_ids)
        ),
        "Unhide Revision Clouds by Sequences",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Information,
    )
    raise SystemExit

views = FilteredElementCollector(doc).OfClass(View).WhereElementIsNotElementType().ToElements()
views_changed = 0
total_unhidden = 0
processed_view_ids = []
failed_views = []

# The same cloud elements are checked in every view; resolve them from the
# document once instead of views x clouds times.
target_clouds = []
for cloud_id in target_cloud_ids:
    element = doc.GetElement(cloud_id)
    if element is not None:
        target_clouds.append((cloud_id, element))

transaction = Transaction(doc, "Unhide Revision Clouds by Sequences")
transaction.Start()
try:
    for view in views:
        try:
            if view.IsTemplate:
                continue

            unhide_list = ClrList[ElementId]()
            for cloud_id, element in target_clouds:
                is_hidden = False
                try:
                    if hasattr(view, "IsElementHidden"):
                        is_hidden = view.IsElementHidden(cloud_id)
                    elif hasattr(element, "IsHidden"):
                        is_hidden = element.IsHidden(view)
                except Exception:
                    is_hidden = True

                if is_hidden:
                    unhide_list.Add(cloud_id)

            if unhide_list.Count > 0:
                try:
                    view.UnhideElements(unhide_list)
                    views_changed += 1
                    total_unhidden += unhide_list.Count
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
        "Unhide Revision Clouds by Sequences",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Error,
    )
    raise SystemExit

lines = []
lines.append("Done.")
lines.append("Selected sequences: {}".format(", ".join(str(x) for x in sorted(seqs))))
lines.append("Revision IDs: {}".format(format_ids(target_revision_ids)))
lines.append("Cloud IDs: {}".format(format_ids(target_cloud_ids_int)))
lines.append("Views changed: {}".format(views_changed))
lines.append("Instances unhidden: {}".format(total_unhidden))

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
    "Unhide Revision Clouds by Sequences",
    WinForms.MessageBoxButtons.OK,
    WinForms.MessageBoxIcon.Information,
)
