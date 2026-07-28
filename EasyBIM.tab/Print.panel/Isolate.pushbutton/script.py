# -*- coding: utf-8 -*-
"""Hide all categories in all views/sheets except user-selected categories."""

import clr

clr.AddReference("System.Windows.Forms")
import System.Windows.Forms as WinForms

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import ElementId, FilteredElementCollector, Transaction, View


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
        "Isolate",
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


def format_ids(values):
    ids = sorted(set([x for x in values if x is not None]))
    return ", ".join(str(x) for x in ids) if ids else "N/A"


def collect_all_categories():
    rows = []
    cats = doc.Settings.Categories
    for cat in cats:
        try:
            cid = eid_int(cat.Id)
            if cid is None:
                continue
            name = cat.Name or "(Unnamed Category)"
            ctype = str(cat.CategoryType)
            label = "{} [{} | ID:{}]".format(name, ctype, cid)
            rows.append({"id": cid, "name": name, "type": ctype, "label": label})
        except Exception:
            continue
    rows.sort(key=lambda x: (x["name"].lower(), x["type"], x["id"]))
    return rows


def pick_categories_to_keep(all_categories):
    f = WinForms.Form()
    f.Text = "Isolate Categories (All Views & Sheets)"
    f.Width = 980
    f.Height = 620
    f.StartPosition = WinForms.FormStartPosition.CenterScreen
    f.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    f.AutoScaleMode = WinForms.AutoScaleMode.Dpi
    f.MinimizeBox = False
    f.MaximizeBox = False

    info = WinForms.Label()
    info.Text = (
        "Pick categories to KEEP visible.\n"
        "All categories not in this list will be hidden in every view and sheet."
    )
    info.Left = 12
    info.Top = 12
    info.Width = 940
    info.Height = 38

    cb = WinForms.ComboBox()
    cb.Left = 12
    cb.Top = 58
    cb.Width = 840
    cb.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    for item in all_categories:
        cb.Items.Add(item["label"])
    if cb.Items.Count > 0:
        cb.SelectedIndex = 0

    btn_add = WinForms.Button()
    btn_add.Text = "+ Add"
    btn_add.Left = 860
    btn_add.Top = 56
    btn_add.Width = 90
    btn_add.Height = 28

    lb = WinForms.ListBox()
    lb.Left = 12
    lb.Top = 96
    lb.Width = 938
    lb.Height = 430
    lb.HorizontalScrollbar = True

    btn_remove = WinForms.Button()
    btn_remove.Text = "Remove Selected"
    btn_remove.Left = 12
    btn_remove.Top = 536
    btn_remove.Width = 150
    btn_remove.Height = 28

    btn_clear = WinForms.Button()
    btn_clear.Text = "Clear"
    btn_clear.Left = 170
    btn_clear.Top = 536
    btn_clear.Width = 80
    btn_clear.Height = 28

    ok = WinForms.Button()
    ok.Text = "Run"
    ok.DialogResult = WinForms.DialogResult.OK
    ok.Left = 770
    ok.Top = 536
    ok.Width = 85
    ok.Height = 28

    cancel = WinForms.Button()
    cancel.Text = "Cancel"
    cancel.DialogResult = WinForms.DialogResult.Cancel
    cancel.Left = 865
    cancel.Top = 536
    cancel.Width = 85
    cancel.Height = 28

    selected_ids = []
    selected_map = {}

    def add_current(sender=None, args=None):
        del sender, args
        idx = cb.SelectedIndex
        if idx < 0 or idx >= len(all_categories):
            return
        row = all_categories[idx]
        cid = row["id"]
        if cid in selected_map:
            return
        selected_map[cid] = row["label"]
        selected_ids.append(cid)
        lb.Items.Add(row["label"])

    def remove_selected(sender=None, args=None):
        del sender, args
        idx = lb.SelectedIndex
        if idx < 0:
            return
        label = lb.Items[idx]
        target_id = None
        for cid in selected_ids:
            if selected_map.get(cid) == label:
                target_id = cid
                break
        if target_id is not None:
            selected_ids[:] = [x for x in selected_ids if x != target_id]
            try:
                del selected_map[target_id]
            except Exception:
                pass
        lb.Items.RemoveAt(idx)

    def clear_all(sender=None, args=None):
        del sender, args
        selected_ids[:] = []
        selected_map.clear()
        lb.Items.Clear()

    btn_add.Click += add_current
    btn_remove.Click += remove_selected
    btn_clear.Click += clear_all
    cb.DoubleClick += add_current

    f.Controls.Add(info)
    f.Controls.Add(cb)
    f.Controls.Add(btn_add)
    f.Controls.Add(lb)
    f.Controls.Add(btn_remove)
    f.Controls.Add(btn_clear)
    f.Controls.Add(ok)
    f.Controls.Add(cancel)
    f.AcceptButton = ok
    f.CancelButton = cancel

    res = f.ShowDialog()
    if res == WinForms.DialogResult.Cancel:
        return None

    if not selected_ids:
        result = WinForms.MessageBox.Show(
            "No categories were selected to keep visible.\n"
            "This will attempt to hide every hideable category in all views and sheets.\n\n"
            "Continue?",
            "Isolate",
            WinForms.MessageBoxButtons.YesNo,
            WinForms.MessageBoxIcon.Warning,
        )
        if result != WinForms.DialogResult.Yes:
            return None

    return sorted(set(selected_ids))


all_categories = collect_all_categories()
if not all_categories:
    WinForms.MessageBox.Show(
        "No categories were found in this document.",
        "Isolate",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Information,
    )
    raise SystemExit

selected_keep_ids = pick_categories_to_keep(all_categories)
if selected_keep_ids is None:
    raise SystemExit

all_category_ids = sorted(set([x["id"] for x in all_categories]))
hide_ids = sorted(set([x for x in all_category_ids if x not in set(selected_keep_ids)]))

views = FilteredElementCollector(doc).OfClass(View).WhereElementIsNotElementType().ToElements()
if not views:
    WinForms.MessageBox.Show(
        "No views/sheets were found in this document.",
        "Isolate",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Information,
    )
    raise SystemExit

views_processed = 0
views_changed = 0
hide_ops = 0
cannot_hide_ids = set()
failed_ops = []

transaction = Transaction(doc, "Isolate Categories in All Views and Sheets")
transaction.Start()
try:
    for view in views:
        view_changed = False
        views_processed += 1

        for cid_int in hide_ids:
            cid = ElementId(int(cid_int))
            try:
                can_hide = True
                if hasattr(view, "CanCategoryBeHidden"):
                    try:
                        can_hide = view.CanCategoryBeHidden(cid)
                    except Exception:
                        can_hide = True
                if not can_hide:
                    cannot_hide_ids.add(cid_int)
                    continue

                already_hidden = False
                try:
                    already_hidden = view.GetCategoryHidden(cid)
                except Exception:
                    already_hidden = False

                if not already_hidden:
                    view.SetCategoryHidden(cid, True)
                    hide_ops += 1
                    view_changed = True
            except Exception as ex:
                failed_ops.append(
                    {
                        "view_id": eid_int(view.Id),
                        "view_name": getattr(view, "Name", "") or "",
                        "category_id": cid_int,
                        "action": "hide",
                        "error": str(ex),
                    }
                )

        if view_changed:
            views_changed += 1

    transaction.Commit()
except Exception as tx_error:
    try:
        transaction.RollBack()
    except Exception:
        pass
    WinForms.MessageBox.Show(
        "Unexpected transaction error:\n{}".format(tx_error),
        "Isolate",
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.Error,
    )
    raise SystemExit

lines = []
lines.append("Isolate completed for all views and sheets.")
lines.append("Views processed: {}".format(views_processed))
lines.append("Views changed: {}".format(views_changed))
lines.append("Categories selected as HIDE EXCEPTIONS (left unchanged): {}".format(len(selected_keep_ids)))
lines.append("Category IDs left unchanged: {}".format(format_ids(selected_keep_ids)))
lines.append("Categories targeted to HIDE: {}".format(len(hide_ids)))
lines.append("Category IDs targeted to hide: {}".format(format_ids(hide_ids)))
lines.append("Hide operations executed: {}".format(hide_ops))

if cannot_hide_ids:
    lines.append("")
    lines.append("Categories that cannot be hidden in one or more views:")
    lines.append("Category IDs: {}".format(format_ids(cannot_hide_ids)))

if failed_ops:
    lines.append("")
    lines.append("Failed operations (sample up to 80 rows):")
    limit = 80
    for row in failed_ops[:limit]:
        view_label = "{} ({})".format(row.get("view_name", "") or "Unnamed View", row.get("view_id", "N/A"))
        lines.append(
            "- Action: {} | View: {} | Category ID: {} | Error: {}".format(
                row.get("action", "n/a"), view_label, row.get("category_id", "N/A"), row.get("error", "Unknown")
            )
        )
    if len(failed_ops) > limit:
        lines.append("... {} more failure rows omitted.".format(len(failed_ops) - limit))

WinForms.MessageBox.Show(
    "\n".join(lines),
    "Isolate",
    WinForms.MessageBoxButtons.OK,
    WinForms.MessageBoxIcon.Information,
)
