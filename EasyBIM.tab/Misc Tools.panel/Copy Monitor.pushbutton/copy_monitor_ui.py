# -*- coding: utf-8 -*-
import os
from pyrevit import forms
from easybim import copy_monitor_review as view

class ReviewWindow(forms.WPFWindow):
    def __init__(self, scan):
        forms.WPFWindow.__init__(self, os.path.join(os.path.dirname(__file__), "ReviewWindow.xaml"))
        self.rows = view.build_rows(scan["reports"])
        self.visible = []
        self.action = None
        self.selected_reports = []
        self.SummaryText.Text = view.scan_summary(scan)
        self.FilterBox.ItemsSource = ["Pending", "All"] + sorted(set(r.Status for r in self.rows))
        self.FilterBox.SelectedIndex = 0

    def filter_changed(self, sender, args):
        self.visible = view.filter_rows(self.rows, str(self.FilterBox.SelectedItem))
        self.IssuesGrid.ItemsSource = self.visible

    def selection_changed(self, sender, args):
        row = self.IssuesGrid.SelectedItem
        self.DetailsText.Text = row.Details if row else ""

    def select_visible(self, sender, args):
        for row in self.visible: row.Checked = True
        self.IssuesGrid.Items.Refresh()

    def clear_selection(self, sender, args):
        for row in self.rows: row.Checked = False
        self.IssuesGrid.Items.Refresh()

    def choose(self, action):
        self.IssuesGrid.CommitEdit()
        # Actions apply only to the currently visible selection.
        chosen = [row for row in self.visible if row.Checked]
        if not chosen:
            forms.alert("Check one or more rows to apply this action.", title="Copy Monitor")
            return
        self.selected_reports = [row.report for row in chosen]
        self.action = action
        self.Close()

    def match_click(self, sender, args): self.choose("match")
    def relative_click(self, sender, args): self.choose("relative")
    def accept_click(self, sender, args): self.choose("accept")
    def postpone_click(self, sender, args): self.choose("postpone")
    def stop_click(self, sender, args): self.choose("stop")

    def show_click(self, sender, args):
        row = self.IssuesGrid.SelectedItem
        if row:
            self.selected_reports = [row.report]
            self.action = "show"
            self.Close()

    def refresh_click(self, sender, args):
        self.action = "refresh"
        self.Close()

    def close_click(self, sender, args):
        self.Close()
