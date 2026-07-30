# -*- coding: utf-8 -*-
"""Create or update an ordered native Revit print set from a Sheet List."""

import clr

clr.AddReference("System.Windows.Forms")
import System.Windows.Forms as WinForms

from pyrevit import HOST_APP
from pyrevit import DB
from pyrevit import framework
from pyrevit import forms
from pyrevit import revit
from pyrevit import script

from easybim import link_reload
from easybim import print_sets


LOGGER = script.get_logger()


class UpdatePrintSetFromScheduleWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._selected_revision_ids = set()
        self._revision_rows = []
        self._sheet_list_options = []

        self._setup_sheet_lists()
        self._update_revision_filter_button_label()
        self._refresh_preview()

    @property
    def selected_sheet_list(self):
        return self.schedules_cb.SelectedItem

    def _setup_sheet_lists(self):
        sheet_category_id = \
            revit.query.get_category(DB.BuiltInCategory.OST_Sheets).Id
        self._sheet_list_options = print_sets.collect_sheet_list_options(
            revit.doc,
            DB,
            framework,
            script,
            sheet_category_id,
            logger=LOGGER,
            host_app=HOST_APP
        )
        self.schedules_cb.ItemsSource = self._sheet_list_options
        if self._sheet_list_options:
            self.schedules_cb.SelectedIndex = 0
            self.enable_element(self.schedules_cb)
        else:
            self.disable_element(self.schedules_cb)
            self._set_error("No non-empty Sheet Lists were found in the active model.")

    def _active_rows(self):
        if not self.selected_sheet_list:
            return []
        return print_sets.filter_rows_by_revision(
            self.selected_sheet_list.rows,
            self._selected_revision_ids
        )

    def _target_print_set_name(self):
        if not self.selected_sheet_list:
            return ""
        return print_sets.build_print_set_name(
            self.selected_sheet_list.name,
            self._selected_revision_ids,
            self._revision_rows
        )

    def _reset_error(self):
        self.enable_element(self.save_b)
        self.hide_element(self.errormsg_block)
        self.errormsg_tb.Text = ""

    def _set_error(self, message):
        self.disable_element(self.save_b)
        self.show_element(self.errormsg_block)
        self.errormsg_tb.Text = message

    def _update_revision_filter_button_label(self):
        if self._selected_revision_ids:
            self.filterrevs_b.Content = \
                "Filter By Revisions ({})".format(len(self._selected_revision_ids))
        else:
            self.filterrevs_b.Content = "Filter By Revisions"

    def _refresh_preview(self):
        rows = self._active_rows()
        printable_rows, skipped_count = print_sets.split_printable_rows(rows)
        self.sheets_lb.ItemsSource = rows
        self.printsetname_tb.Text = self._target_print_set_name()

        summary = "{} sheet row(s), {} printable".format(
            len(rows),
            len(printable_rows)
        )
        if self._selected_revision_ids:
            summary += " after revision filter"
        if skipped_count:
            summary += ", {} skipped".format(skipped_count)
        self.summary_tb.Text = summary

        if not self._sheet_list_options:
            self._set_error("No non-empty Sheet Lists were found in the active model.")
        elif not self.selected_sheet_list:
            self._set_error("Select a Sheet List to continue.")
        elif not rows:
            self._set_error("No sheets match the selected revision filter.")
        elif not printable_rows:
            self._set_error("No printable sheets are available for this print set.")
        else:
            self._reset_error()

    def _pick_filter_revisions(self, revision_rows):
        form = WinForms.Form()
        form.Text = "Filter By Revisions"
        form.Width = 820
        form.Height = 520
        form.StartPosition = WinForms.FormStartPosition.CenterScreen
        form.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
        form.AutoScaleMode = WinForms.AutoScaleMode.Dpi
        form.MinimizeBox = False
        form.MaximizeBox = False

        revision_list = WinForms.ListView()
        revision_list.View = WinForms.View.Details
        revision_list.CheckBoxes = True
        revision_list.FullRowSelect = True
        revision_list.GridLines = True
        revision_list.Left = 12
        revision_list.Top = 12
        revision_list.Width = 780
        revision_list.Height = 415
        revision_list.Anchor = (
            WinForms.AnchorStyles.Top |
            WinForms.AnchorStyles.Left |
            WinForms.AnchorStyles.Right |
            WinForms.AnchorStyles.Bottom
        )

        revision_list.Columns.Add("Select", 80)
        revision_list.Columns.Add("Seq", 70)
        revision_list.Columns.Add("Number", 150)
        revision_list.Columns.Add("Description", 470)

        for revision in revision_rows:
            row = WinForms.ListViewItem("")
            row.SubItems.Add(str(revision.get("sequence", "")))
            row.SubItems.Add(revision.get("number", "") or "")
            row.SubItems.Add(revision.get("description", "") or "")
            row.Tag = revision
            if revision.get("id") in self._selected_revision_ids:
                row.Checked = True
            revision_list.Items.Add(row)

        select_all_b = WinForms.Button()
        select_all_b.Text = "Select All"
        select_all_b.AutoSize = True
        select_all_b.Left = 12
        select_all_b.Top = 438
        select_all_b.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left

        clear_all_b = WinForms.Button()
        clear_all_b.Text = "Clear All"
        clear_all_b.AutoSize = True
        clear_all_b.Left = 120
        clear_all_b.Top = 438
        clear_all_b.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Left

        save_b = WinForms.Button()
        save_b.Text = "Save"
        save_b.DialogResult = WinForms.DialogResult.OK
        save_b.AutoSize = True
        save_b.Left = 675
        save_b.Top = 438
        save_b.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right

        cancel_b = WinForms.Button()
        cancel_b.Text = "Cancel"
        cancel_b.DialogResult = WinForms.DialogResult.Cancel
        cancel_b.AutoSize = True
        cancel_b.Left = 740
        cancel_b.Top = 438
        cancel_b.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right

        def select_all(sender, args):
            del sender, args
            for item in revision_list.Items:
                item.Checked = True

        def clear_all(sender, args):
            del sender, args
            for item in revision_list.Items:
                item.Checked = False

        select_all_b.Click += select_all
        clear_all_b.Click += clear_all

        form.Controls.Add(revision_list)
        form.Controls.Add(select_all_b)
        form.Controls.Add(clear_all_b)
        form.Controls.Add(save_b)
        form.Controls.Add(cancel_b)
        form.AcceptButton = save_b
        form.CancelButton = cancel_b

        if form.ShowDialog() == WinForms.DialogResult.Cancel:
            return None

        selected_ids = set()
        for row in revision_list.Items:
            try:
                if row.Checked and row.Tag:
                    selected_ids.add(int(row.Tag.get("id")))
            except Exception:
                continue
        return selected_ids

    def schedule_changed(self, sender, args):
        del sender, args
        self._refresh_preview()

    def filter_by_revisions(self, sender, args):
        del sender, args
        self._revision_rows = print_sets.collect_revision_rows(
            revit.doc,
            DB,
            framework
        )
        if not self._revision_rows:
            forms.alert("No revisions were found in the active model.")
            return

        selected_revision_ids = self._pick_filter_revisions(self._revision_rows)
        if selected_revision_ids is None:
            return

        self._selected_revision_ids = set(selected_revision_ids)
        self._update_revision_filter_button_label()
        self._refresh_preview()

    def save_print_set(self, sender, args):
        del sender, args
        rows = self._active_rows()
        printable_rows, skipped_count = print_sets.split_printable_rows(rows)
        if not printable_rows:
            forms.alert("No printable sheets are available for this print set.")
            return

        print_set_name = self._target_print_set_name()
        try:
            print_sets.save_ordered_print_set(
                revit.doc,
                print_set_name,
                printable_rows,
                DB,
                framework,
                revit,
                HOST_APP
            )
        except print_sets.UnsupportedRevitVersion as version_err:
            forms.alert(str(version_err))
            return
        except Exception as save_err:
            LOGGER.critical("Failed to create print set: %s", save_err)
            forms.alert(
                "Failed to create or update the print set.",
                expanded=str(save_err)
            )
            return

        link_reload.ask_and_reload_loaded_links(
            revit.doc,
            title="Update Print Set From Schedule"
        )

        message = [
            "Print set updated: {}".format(print_set_name),
            "Printable sheets included: {}".format(len(printable_rows)),
        ]
        if skipped_count:
            message.append("Skipped non-printable rows: {}".format(skipped_count))
        forms.alert("\n".join(message), title="Update Print Set From Schedule")
        self.Close()


forms.check_modeldoc(exitscript=True)
revit.selection.get_selection().clear()

if not print_sets.supports_ordered_print_sets(HOST_APP):
    forms.alert(
        "Update Print Set From Schedule requires Revit 2023 or newer.",
        exitscript=True
    )

UpdatePrintSetFromScheduleWindow("UpdatePrintSetFromSchedule.xaml").ShowDialog()
