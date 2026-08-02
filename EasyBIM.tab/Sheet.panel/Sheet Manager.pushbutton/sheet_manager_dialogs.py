# -*- coding: utf-8 -*-
"""Dialog windows for Sheet Manager (one class per bundle XAML)."""

from __future__ import print_function

from pyrevit import forms


class ApplyResultsWindow(forms.WPFWindow):
    """Apply Changes summary: counts + tabbed change/error grids."""

    def __init__(self, xaml_file_name, results):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)

        self.modified_tb.Text = \
            "Modified Sheets: {0}".format(results.modified_count())
        self.created_tb.Text = \
            "Created Sheets: {0}".format(results.created_count)
        self.params_tb.Text = \
            "Updated Parameters: {0}".format(
                results.updated_parameter_count)
        self.revadded_tb.Text = \
            "Revisions Added: {0}".format(results.revisions_added)
        self.revremoved_tb.Text = \
            "Revisions Removed: {0}".format(results.revisions_removed)

        self.params_tab.Header = "Parameter Changes ({0})".format(
            len(results.parameter_changes))
        self.revisions_tab.Header = "Revision Changes ({0})".format(
            len(results.revision_changes))
        self.sheets_tab.Header = "Sheet Changes ({0})".format(
            len(results.sheet_changes))
        self.errors_tab.Header = "Errors/Warnings ({0})".format(
            len(results.errors))

        self.params_dg.ItemsSource = list(results.parameter_changes)
        self.revisions_dg.ItemsSource = list(results.revision_changes)
        self.sheets_dg.ItemsSource = list(results.sheet_changes)
        self.errors_dg.ItemsSource = list(results.errors)
        self._is_ready = True

    def ok_clicked(self, sender, args):
        del sender, args
        self.Close()


def show_apply_results(results):
    ApplyResultsWindow("ApplyResultsDialog.xaml", results).ShowDialog()
