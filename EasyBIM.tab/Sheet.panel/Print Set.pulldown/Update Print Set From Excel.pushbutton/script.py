# -*- coding: utf-8 -*-
"""Create or update an ordered native Revit print set from Excel rows."""

import clr

clr.AddReference("System.Windows.Forms")
import System.Windows.Forms as WinForms

from pyrevit import HOST_APP
from pyrevit import DB
from pyrevit import framework
from pyrevit.framework import Windows
from pyrevit import forms
from pyrevit import revit
from pyrevit import script

from easybim import excel_print_sets
from easybim import link_reload
from easybim import print_sets


LOGGER = script.get_logger()


class _LinkResultRow(object):
    __slots__ = ("name", "element_type", "status", "error")

    def __init__(self, name, element_type, status, error):
        self.name = name
        self.element_type = element_type
        self.status = status
        self.error = error


class _ReloadLinksResultsWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, items):
        forms.WPFWindow.__init__(self, xaml_file_name)

        rows = [_LinkResultRow(
            i.get("name", ""), i.get("element_type", ""),
            i.get("status", ""), i.get("error", ""))
            for i in (items or [])]

        reloaded = sum(1 for r in rows if r.status == "Reloaded")
        errors = sum(1 for r in rows if r.status == "Error")
        skipped = len(rows) - reloaded - errors
        parts = ["Reloaded: {0}".format(reloaded)]
        if errors:
            parts.append("Errors: {0}".format(errors))
        if skipped:
            parts.append("Skipped: {0}".format(skipped))
        self.summary_tb.Text = "  |  ".join(parts)

        self.results_dg.ItemsSource = rows

    def ok_clicked(self, sender, args):
        del sender, args
        self.Close()


class UpdatePrintSetFromExcelWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._selected_revision_ids = set()
        self._revision_rows = []
        self._print_set_names = []
        self._excel_path = None
        self._excel_warning = None
        self._session = None
        self._result = None

        self._setup_print_set_names()
        self._update_revision_filter_button_label()
        self._show_validation_page()
        self._refresh_validation()

    def _setup_print_set_names(self):
        self._print_set_names = print_sets.collect_print_set_names(
            revit.doc,
            DB,
            framework
        )
        self.printset_cb.ItemsSource = self._print_set_names
        if self._print_set_names:
            self.printset_cb.SelectedIndex = 0
        self.printset_cb.Text = ""

    def _collect_model_sheets(self):
        return list(
            DB.FilteredElementCollector(revit.doc)
              .OfClass(framework.get_type(DB.ViewSheet))
              .WhereElementIsNotElementType()
              .ToElements()
        )

    def _target_print_set_name(self):
        try:
            return excel_print_sets._safe_text(self.printset_cb.Text).strip()
        except Exception:
            return ""

    def _set_target_print_set_name(self, name):
        self.printset_cb.Text = name or ""

    def _show_validation_page(self):
        self.show_element(self.validation_page)
        self.hide_element(self.preview_page)

    def _show_preview_page(self):
        self.hide_element(self.validation_page)
        self.show_element(self.preview_page)

    def _set_validation_error(self, message):
        self.show_element(self.errormsg_block)
        self.errormsg_tb.Text = message
        self.disable_element(self.next_b)

    def _reset_validation_error(self):
        self.hide_element(self.errormsg_block)
        self.errormsg_tb.Text = ""

    def _set_preview_error(self, message):
        self.show_element(self.preview_errormsg_block)
        self.preview_errormsg_tb.Text = message
        self.disable_element(self.save_b)

    def _reset_preview_error(self):
        self.hide_element(self.preview_errormsg_block)
        self.preview_errormsg_tb.Text = ""

    def _update_revision_filter_button_label(self):
        if self._selected_revision_ids:
            self.filterrevs_b.Content = \
                "Filter By Revisions ({})".format(len(self._selected_revision_ids))
        else:
            self.filterrevs_b.Content = "Filter By Revisions"

    def _set_warning(self, message):
        if message:
            self.warning_tb.Text = message
            self.show_element(self.warning_tb)
        else:
            self.warning_tb.Text = ""
            self.hide_element(self.warning_tb)

    def _refresh_validation(self):
        self.number_discrepancies_lb.ItemsSource = []
        self.name_discrepancies_lb.ItemsSource = []
        self.disable_element(self.skipnumbers_b)
        self.disable_element(self.skipnames_b)
        self.disable_element(self.next_b)

        if not self._session:
            self.file_tb.Text = "No Excel file imported."
            self.summary_tb.Text = "Import an Excel file to start."
            self._set_warning(None)
            self._set_validation_error("Import an Excel file to continue.")
            return

        self._result = self._session.validate(self._selected_revision_ids)
        self.number_discrepancies_lb.ItemsSource = \
            self._result.number_discrepancies
        self.name_discrepancies_lb.ItemsSource = self._result.name_discrepancies

        if self._result.number_discrepancies:
            self.enable_element(self.skipnumbers_b)
        if self._result.name_discrepancies:
            self.enable_element(self.skipnames_b)

        summary = "{} visible Excel row(s), {} ready sheet row(s)".format(
            len(self._result.visible_rows),
            len(self._result.final_rows)
        )
        if self._selected_revision_ids:
            summary += " after revision filter"
        if self._result.skipped_row_ids:
            summary += ", {} skipped by user".format(
                len(self._result.skipped_row_ids)
            )
        self.summary_tb.Text = summary
        self._set_warning(self._excel_warning)

        if not self._target_print_set_name():
            self._set_validation_error("Enter a print set name to continue.")
        elif self._result.number_discrepancies or self._result.name_discrepancies:
            self._set_validation_error(
                "Resolve or skip the visible discrepancies to continue."
            )
        elif not self._result.final_rows:
            self._set_validation_error(
                "No matching sheets are available for this print set."
            )
        else:
            self._reset_validation_error()
            self.enable_element(self.next_b)

    def _refresh_preview(self):
        if not self._session:
            return

        self._result = self._session.validate(self._selected_revision_ids)
        self.final_sheets_lb.ItemsSource = self._result.final_rows
        self.preview_printsetname_tb.Text = self._target_print_set_name()

        printable_rows, skipped_count = print_sets.split_printable_rows(
            self._result.final_rows
        )
        summary = "{} sheet row(s), {} printable".format(
            len(self._result.final_rows),
            len(printable_rows)
        )
        if self._selected_revision_ids:
            summary += " after revision filter"
        if skipped_count:
            summary += ", {} skipped".format(skipped_count)
        self.preview_summary_tb.Text = summary

        if not self._target_print_set_name():
            self._set_preview_error("Enter a print set name to continue.")
        elif self._result.number_discrepancies or self._result.name_discrepancies:
            self._set_preview_error(
                "Go back and resolve or skip the visible discrepancies."
            )
        elif not printable_rows:
            self._set_preview_error(
                "No printable sheets are available for this print set."
            )
        else:
            self._reset_preview_error()
            self.enable_element(self.save_b)

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

    def _ask_for_print_set_name(self, current_name):
        form = WinForms.Form()
        form.Text = "Edit Print Set Name"
        form.Width = 520
        form.Height = 145
        form.StartPosition = WinForms.FormStartPosition.CenterScreen
        form.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
        form.AutoScaleMode = WinForms.AutoScaleMode.Dpi
        form.MinimizeBox = False
        form.MaximizeBox = False

        text_box = WinForms.TextBox()
        text_box.Left = 12
        text_box.Top = 14
        text_box.Width = 480
        text_box.Text = current_name or ""

        ok_b = WinForms.Button()
        ok_b.Text = "OK"
        ok_b.DialogResult = WinForms.DialogResult.OK
        ok_b.AutoSize = True
        ok_b.Left = 335
        ok_b.Top = 54

        cancel_b = WinForms.Button()
        cancel_b.Text = "Cancel"
        cancel_b.DialogResult = WinForms.DialogResult.Cancel
        cancel_b.AutoSize = True
        cancel_b.Left = 420
        cancel_b.Top = 54

        form.Controls.Add(text_box)
        form.Controls.Add(ok_b)
        form.Controls.Add(cancel_b)
        form.AcceptButton = ok_b
        form.CancelButton = cancel_b

        if form.ShowDialog() == WinForms.DialogResult.Cancel:
            return None
        return excel_print_sets._safe_text(text_box.Text).strip()

    def import_excel(self, sender, args):
        del sender, args
        excel_path = forms.pick_file(
            files_filter=(
                "Excel Workbooks (*.xlsx;*.xlsm)|*.xlsx;*.xlsm|"
                "All files (*.*)|*.*"
            ),
            restore_dir=True,
            multi_file=False
        )
        if not excel_path:
            return

        try:
            read_result = excel_print_sets.read_visible_excel_rows(excel_path)
        except excel_print_sets.UnsupportedExcelFile as import_err:
            forms.alert(str(import_err), title="Update Print Set From Excel")
            return
        except Exception as import_err:
            LOGGER.critical("Failed to import Excel print set: %s", import_err)
            forms.alert(
                "Failed to import the Excel file.",
                expanded=str(import_err),
                title="Update Print Set From Excel"
            )
            return

        self._excel_path = excel_path
        self._excel_warning = read_result.warning
        self._session = excel_print_sets.ExcelPrintSetSession(
            read_result.rows,
            self._collect_model_sheets()
        )
        self.file_tb.Text = excel_path
        self._set_target_print_set_name(
            excel_print_sets.default_print_set_name_from_path(excel_path)
        )
        self._show_validation_page()
        self._refresh_validation()

    def edit_print_set_name(self, sender, args):
        del sender, args
        new_name = self._ask_for_print_set_name(self._target_print_set_name())
        if new_name is None:
            return
        self._set_target_print_set_name(new_name)
        self._refresh_validation()

    def print_set_name_changed(self, sender, args):
        del sender, args
        self._refresh_validation()

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
        self._refresh_validation()
        if self.preview_page.Visibility == Windows.Visibility.Visible:
            self._refresh_preview()

    def skip_number_discrepancies(self, sender, args):
        del sender, args
        if self._session and self._result:
            self._session.skip_number_discrepancies(
                self._result.number_discrepancies
            )
        self._refresh_validation()

    def skip_name_discrepancies(self, sender, args):
        del sender, args
        if self._session and self._result:
            self._session.skip_name_discrepancies(
                self._result.name_discrepancies
            )
        self._refresh_validation()

    def next_page(self, sender, args):
        del sender, args
        self._refresh_validation()
        if not self._result or not self._result.can_continue:
            return
        if not self._result.final_rows:
            return
        self._show_preview_page()
        self._refresh_preview()

    def back_page(self, sender, args):
        del sender, args
        self._show_validation_page()
        self._refresh_validation()

    def save_print_set(self, sender, args):
        del sender, args
        self._refresh_preview()
        if not self._result:
            return

        printable_rows, skipped_count = print_sets.split_printable_rows(
            self._result.final_rows
        )
        if not printable_rows:
            forms.alert("No printable sheets are available for this print set.")
            return

        print_set_name = self._target_print_set_name()
        if not print_set_name:
            forms.alert("Enter a print set name to continue.")
            return

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
            LOGGER.critical("Failed to create print set from Excel: %s", save_err)
            forms.alert(
                "Failed to create or update the print set.",
                expanded=str(save_err),
                title="Update Print Set From Excel"
            )
            return

        summary = link_reload.ask_and_reload_loaded_links(
            revit.doc,
            title="Update Print Set From Excel"
        )
        if summary:
            items = summary.get("items")
            if items:
                _ReloadLinksResultsWindow(
                    "ReloadLinksResultsDialog.xaml", items).ShowDialog()

        message = [
            "Print set updated: {}".format(print_set_name),
            "Printable sheets included: {}".format(len(printable_rows)),
        ]
        if skipped_count:
            message.append("Skipped non-printable rows: {}".format(skipped_count))
        if self._result.skipped_row_ids:
            message.append(
                "Skipped Excel discrepancy rows: {}".format(
                    len(self._result.skipped_row_ids)
                )
            )
        forms.alert("\n".join(message), title="Update Print Set From Excel")
        self.Close()


forms.check_modeldoc(exitscript=True)
revit.selection.get_selection().clear()

if not print_sets.supports_ordered_print_sets(HOST_APP):
    forms.alert(
        "Update Print Set From Excel requires Revit 2023 or newer.",
        exitscript=True
    )

UpdatePrintSetFromExcelWindow("UpdatePrintSetFromExcel.xaml").ShowDialog()
