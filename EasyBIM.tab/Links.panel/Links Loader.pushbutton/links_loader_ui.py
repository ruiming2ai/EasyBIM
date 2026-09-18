# -*- coding: utf-8 -*-
"""WPF window classes for the Links Loader tool."""

from pyrevit import forms

from links_loader_state import status_label
from links_loader_state import count_updatable


class _LinkRow(object):
    """View-model row for the main window DataGrid."""

    def __init__(self, record):
        self.name = record.name
        self.element_type = record.element_type
        self.path = record.path
        self.status = "Loaded" if record.is_loaded else "Unloaded"


class _PlanRow(object):
    """View-model row for the import preview DataGrid."""

    def __init__(self, plan_item):
        self._item = plan_item
        self.name = plan_item.name
        self.old_path = plan_item.old_path
        self.new_path = plan_item.new_path
        self.status_label = status_label(plan_item.status)
        self.is_selected = plan_item.is_selected
        if plan_item.file_missing and plan_item.status == "update":
            self.status_label += " (file missing)"

    @property
    def plan_item(self):
        return self._item


class LinksLoaderWindow(forms.WPFWindow):

    def __init__(self, xaml_file_name, link_records, has_links):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = None

        count = len(link_records)
        self.subtitle_tb.Text = "{} link(s) found in the current document.".format(
            count
        )

        rows = [_LinkRow(r) for r in link_records]
        self.links_dg.ItemsSource = rows

        if not has_links:
            self.export_b.IsEnabled = False

    def export_click(self, sender, args):
        self.result = "export"
        self.Close()

    def import_click(self, sender, args):
        self.result = "import"
        self.Close()

    def close_click(self, sender, args):
        self.Close()


class ImportPreviewWindow(forms.WPFWindow):

    def __init__(self, xaml_file_name, plan_items, source_path):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = None
        self._plan_items = plan_items

        import os
        self.source_tb.Text = "From: {}".format(
            os.path.basename(source_path)
        )

        rows = [_PlanRow(item) for item in plan_items]
        self.plan_dg.ItemsSource = rows

        updatable = count_updatable(plan_items)
        total = len(plan_items)
        self.count_tb.Text = "{} of {} link(s) will be updated.".format(
            updatable, total
        )

        if not updatable:
            self.apply_b.IsEnabled = False

    @property
    def selected_items(self):
        items = []
        if self.plan_dg.ItemsSource is None:
            return items
        for row in self.plan_dg.ItemsSource:
            if row.is_selected:
                row.plan_item.is_selected = True
                items.append(row.plan_item)
            else:
                row.plan_item.is_selected = False
        return items

    def apply_click(self, sender, args):
        self.result = "apply"
        self.Close()

    def cancel_click(self, sender, args):
        self.Close()
