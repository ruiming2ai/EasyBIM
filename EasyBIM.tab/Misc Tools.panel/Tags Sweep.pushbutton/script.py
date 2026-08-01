# -*- coding: utf-8 -*-
"""List and delete orphaned tags (lost host)."""

# pylint: disable=import-error,invalid-name,broad-except
import clr

clr.AddReference("System.Windows.Forms")
import System.Windows.Forms as WinForms

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script
from pyrevit.compat import get_elementid_value_func

__title__ = "Tags Sweep"

logger = script.get_logger()
get_elementid_value = get_elementid_value_func()
SCOPE_ENTIRE = "Entire Project"
SCOPE_ACTIVE = "Active View Only"


from easybim.compat import eid_to_int as _eid_int


def _safe_get_attr(obj, name):
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _safe_view_name(doc, owner_view_id):
    if owner_view_id is None:
        return "<No Owner View>", None

    view_id_int = _eid_int(owner_view_id)
    if view_id_int is None or view_id_int < 0:
        return "<No Owner View>", view_id_int

    try:
        view = doc.GetElement(owner_view_id)
    except Exception:
        view = None

    if view is None:
        return "<Deleted View>", view_id_int

    try:
        name = view.Name
    except Exception:
        name = None

    if not name:
        name = "<Unnamed View>"

    return name, view_id_int


def _get_tag_class_defs():
    class_defs = []

    if _safe_get_attr(DB, "IndependentTag"):
        class_defs.append(("IndependentTag", DB.IndependentTag))

    if _safe_get_attr(DB, "RoomTag"):
        class_defs.append(("RoomTag", DB.RoomTag))
    elif _safe_get_attr(_safe_get_attr(DB, "Architecture"), "RoomTag"):
        class_defs.append(("RoomTag", DB.Architecture.RoomTag))

    if _safe_get_attr(DB, "AreaTag"):
        class_defs.append(("AreaTag", DB.AreaTag))
    elif _safe_get_attr(_safe_get_attr(DB, "Architecture"), "AreaTag"):
        class_defs.append(("AreaTag", DB.Architecture.AreaTag))

    if _safe_get_attr(DB, "SpaceTag"):
        class_defs.append(("SpaceTag", DB.SpaceTag))
    elif _safe_get_attr(_safe_get_attr(DB, "Mechanical"), "SpaceTag"):
        class_defs.append(("SpaceTag", DB.Mechanical.SpaceTag))

    return class_defs


def _collect_orphaned_tags(doc, scope_mode, active_view_id):
    rows = []

    for class_name, tag_class in _get_tag_class_defs():
        try:
            if scope_mode == SCOPE_ACTIVE and active_view_id:
                collector = DB.FilteredElementCollector(doc, active_view_id).OfClass(tag_class)
            else:
                collector = DB.FilteredElementCollector(doc).OfClass(tag_class)
            elements = collector.WhereElementIsNotElementType().ToElements()
        except Exception as ex:
            logger.debug("Tag class collect failed | class=%s | %s", class_name, ex)
            continue

        for tag in elements:
            try:
                if not bool(getattr(tag, "IsOrphaned", False)):
                    continue
            except Exception:
                continue

            try:
                owner_view_id = tag.OwnerViewId
            except Exception:
                owner_view_id = None

            view_name, view_id_int = _safe_view_name(doc, owner_view_id)
            elem_id_int = _eid_int(getattr(tag, "Id", None))

            tag_type_name = class_name
            try:
                runtime_type = tag.GetType()
                if runtime_type and runtime_type.Name:
                    tag_type_name = runtime_type.Name
            except Exception:
                pass

            rows.append(
                {
                    "elem_id_int": elem_id_int,
                    "tag_type_name": tag_type_name,
                    "view_name": view_name,
                    "view_id_int": view_id_int,
                    "elem_id": tag.Id,
                }
            )

    rows.sort(
        key=lambda x: (
            (x["view_name"] or "").lower(),
            (x["tag_type_name"] or "").lower(),
            x["elem_id_int"] if x["elem_id_int"] is not None else -1,
        )
    )
    return rows


def _set_status_text(label, rows, scope_mode):
    label.Text = "{} orphaned tag(s) found in {}.".format(len(rows), scope_mode)


def _build_window(doc, active_view_id):
    form = WinForms.Form()
    form.Text = __title__
    form.Width = 980
    form.Height = 620
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    form.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    form.AutoScaleMode = WinForms.AutoScaleMode.Dpi
    form.MinimizeBox = False
    form.MaximizeBox = False

    top_panel = WinForms.Panel()
    top_panel.Height = 60
    top_panel.Dock = WinForms.DockStyle.Top

    scope_label = WinForms.Label()
    scope_label.Text = "Scope"
    scope_label.Left = 12
    scope_label.Top = 20
    scope_label.Width = 44

    scope_cb = WinForms.ComboBox()
    scope_cb.Left = 62
    scope_cb.Top = 16
    scope_cb.Width = 190
    scope_cb.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    scope_cb.Items.Add(SCOPE_ENTIRE)
    scope_cb.Items.Add(SCOPE_ACTIVE)
    scope_cb.SelectedIndex = 0

    status_lbl = WinForms.Label()
    status_lbl.Left = 270
    status_lbl.Top = 20
    status_lbl.Width = 680
    status_lbl.Height = 20

    top_panel.Controls.Add(scope_label)
    top_panel.Controls.Add(scope_cb)
    top_panel.Controls.Add(status_lbl)

    list_view = WinForms.ListView()
    list_view.View = WinForms.View.Details
    list_view.CheckBoxes = True
    list_view.FullRowSelect = True
    list_view.GridLines = True
    list_view.Dock = WinForms.DockStyle.Fill
    list_view.HideSelection = False

    list_view.Columns.Add("Select", 70)
    list_view.Columns.Add("Tag ID", 100)
    list_view.Columns.Add("Tag Type", 170)
    list_view.Columns.Add("View Name", 470)
    list_view.Columns.Add("View ID", 100)

    bottom_panel = WinForms.Panel()
    bottom_panel.Height = 56
    bottom_panel.Dock = WinForms.DockStyle.Bottom

    btn_select_all = WinForms.Button()
    btn_select_all.Text = "Select All"
    btn_select_all.AutoSize = True
    btn_select_all.Left = 12
    btn_select_all.Top = 12

    btn_select_none = WinForms.Button()
    btn_select_none.Text = "Select None"
    btn_select_none.AutoSize = True
    btn_select_none.Top = 12

    btn_delete = WinForms.Button()
    btn_delete.Text = "Delete"
    btn_delete.AutoSize = True
    btn_delete.Top = 12
    btn_delete.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right

    def _layout_buttons(sender=None, args=None):
        del sender, args
        gap = max(12, int(0.4 * btn_select_all.Width))
        btn_select_none.Left = btn_select_all.Left + btn_select_all.Width + gap
        btn_delete.Left = bottom_panel.Width - btn_delete.Width - 12

    bottom_panel.Resize += _layout_buttons
    bottom_panel.Controls.Add(btn_select_all)
    bottom_panel.Controls.Add(btn_select_none)
    bottom_panel.Controls.Add(btn_delete)

    def _populate():
        scope_mode = scope_cb.SelectedItem or SCOPE_ENTIRE
        rows = _collect_orphaned_tags(doc, scope_mode, active_view_id)

        list_view.BeginUpdate()
        list_view.Items.Clear()
        try:
            for row in rows:
                item = WinForms.ListViewItem("")
                item.SubItems.Add("" if row["elem_id_int"] is None else str(row["elem_id_int"]))
                item.SubItems.Add(row["tag_type_name"] or "")
                item.SubItems.Add(row["view_name"] or "")
                item.SubItems.Add("" if row["view_id_int"] is None else str(row["view_id_int"]))
                item.Tag = row
                item.Checked = True
                list_view.Items.Add(item)
        finally:
            list_view.EndUpdate()

        _set_status_text(status_lbl, rows, scope_mode)

    def _select_all(sender, args):
        del sender, args
        try:
            for item in list_view.Items:
                item.Checked = True
        except Exception:
            pass

    def _select_none(sender, args):
        del sender, args
        try:
            for item in list_view.Items:
                item.Checked = False
        except Exception:
            pass

    def _get_checked_rows():
        selected = []
        for item in list_view.Items:
            try:
                if item.Checked and item.Tag:
                    selected.append(item.Tag)
            except Exception:
                pass
        return selected

    def _delete_selected(sender, args):
        del sender, args
        chosen = _get_checked_rows()
        if not chosen:
            WinForms.MessageBox.Show(
                "No tags selected for delete.",
                __title__,
                WinForms.MessageBoxButtons.OK,
                WinForms.MessageBoxIcon.Information,
            )
            return

        result = WinForms.MessageBox.Show(
            "Delete {} orphaned tag(s)?".format(len(chosen)),
            __title__,
            WinForms.MessageBoxButtons.YesNo,
            WinForms.MessageBoxIcon.Warning,
        )
        if result != WinForms.DialogResult.Yes:
            return

        deleted = 0
        failed = 0
        seen_ids = set()

        with revit.Transaction("Delete Orphaned Tags"):
            for row in chosen:
                elem_id = row.get("elem_id")
                elem_id_int = _eid_int(elem_id)
                if elem_id_int is None or elem_id_int in seen_ids:
                    continue
                seen_ids.add(elem_id_int)

                try:
                    if doc.GetElement(elem_id) is None:
                        continue
                    doc.Delete(elem_id)
                    deleted += 1
                except Exception as ex:
                    failed += 1
                    logger.debug("Delete failed | id=%s | %s", elem_id_int, ex)

        WinForms.MessageBox.Show(
            "Delete complete.\nDeleted: {}\nFailed: {}".format(deleted, failed),
            __title__,
            WinForms.MessageBoxButtons.OK,
            WinForms.MessageBoxIcon.Information,
        )
        _populate()

    def _scope_changed(sender, args):
        del sender, args
        _populate()

    btn_select_all.Click += _select_all
    btn_select_none.Click += _select_none
    btn_delete.Click += _delete_selected
    scope_cb.SelectedIndexChanged += _scope_changed

    form.Controls.Add(bottom_panel)
    form.Controls.Add(list_view)
    form.Controls.Add(top_panel)

    _layout_buttons()
    _populate()
    return form


def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if not doc or not uidoc:
        forms.alert("No active project document.", title=__title__)
        return

    active_view_id = None
    try:
        active_view_id = uidoc.ActiveView.Id
    except Exception:
        active_view_id = None

    window = _build_window(doc, active_view_id)
    window.ShowDialog()


if __name__ == "__main__":
    run()
