# -*- coding: utf-8 -*-
"""WPF window for the Excel (export/import schedules) command."""

# pylint: disable=import-error,invalid-name,broad-except
import os
import sys

from pyrevit import forms
from pyrevit.framework import Windows

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from schedule_export_state import filter_schedule_options
from schedule_export_state import get_selected_options
from schedule_export_state import schedule_display_text


TITLE = "Excel"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _search_text(control):
    try:
        return _safe_text(control.Text)
    except Exception:
        return ""


def _add_empty_text(panel, message):
    textblock = Windows.Controls.TextBlock()
    textblock.Text = message
    textblock.Margin = Windows.Thickness(8, 4, 8, 4)
    panel.Children.Add(textblock)


class ScheduleSelectionWindow(forms.WPFWindow):
    """Checkbox multi-select window for exportable schedules."""

    def __init__(self, xaml_file_name, schedule_options):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.options = list(schedule_options or [])
        self.result = None
        self._controls = []

        self._populate()
        self._update_buttons()
        self._is_ready = True

    def _populate(self):
        self.ScheduleListPanel.Children.Clear()
        self._controls = []
        visible_options = filter_schedule_options(
            self.options,
            _search_text(self.ScheduleSearchBox),
        )
        for option in visible_options:
            checkbox = Windows.Controls.CheckBox()
            checkbox.Content = schedule_display_text(option)
            checkbox.IsChecked = bool(option.is_selected)
            checkbox.Tag = option
            checkbox.Margin = Windows.Thickness(8, 4, 8, 4)
            checkbox.Checked += self.selection_changed
            checkbox.Unchecked += self.selection_changed
            self.ScheduleListPanel.Children.Add(checkbox)
            self._controls.append(checkbox)

        if not visible_options:
            if self.options:
                _add_empty_text(
                    self.ScheduleListPanel, "No schedules match the search."
                )
            else:
                _add_empty_text(
                    self.ScheduleListPanel,
                    "No exportable schedules were found.",
                )

        self._update_count_text(len(visible_options))

    def _update_count_text(self, visible_count):
        selected_count = len(get_selected_options(self.options))
        if visible_count != len(self.options):
            self.count_tb.Text = (
                "{} schedule(s), {} shown, {} selected."
            ).format(len(self.options), visible_count, selected_count)
        else:
            self.count_tb.Text = "{} schedule(s), {} selected.".format(
                len(self.options), selected_count
            )

    def _sync_controls(self):
        for checkbox in self._controls:
            option = getattr(checkbox, "Tag", None)
            if option is not None:
                option.is_selected = bool(getattr(checkbox, "IsChecked", False))

    def _update_buttons(self):
        # Import needs exactly ONE selected schedule; selections hidden by
        # the search filter still count.
        try:
            self.ImportButton.IsEnabled = \
                len(get_selected_options(self.options)) == 1
        except Exception:
            pass

    def selection_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._sync_controls()
        self._update_buttons()
        self._update_count_text(len(self._controls))

    def schedule_search_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._sync_controls()
        self._populate()
        self._update_buttons()

    def select_all_click(self, sender, args):
        del sender, args
        for checkbox in self._controls:
            checkbox.IsChecked = True
        self._sync_controls()
        self._update_buttons()
        self._update_count_text(len(self._controls))

    def select_none_click(self, sender, args):
        del sender, args
        for checkbox in self._controls:
            checkbox.IsChecked = False
        self._sync_controls()
        self._update_buttons()
        self._update_count_text(len(self._controls))

    def export_click(self, sender, args):
        del sender, args
        self._sync_controls()
        if not get_selected_options(self.options):
            forms.alert("Select at least one schedule to export.", title=TITLE)
            return
        self.result = "export"
        self.Close()

    def import_click(self, sender, args):
        del sender, args
        self._sync_controls()
        if len(get_selected_options(self.options)) != 1:
            forms.alert(
                "Select exactly one schedule to import into.", title=TITLE
            )
            return
        self.result = "import"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = "cancel"
        self.Close()
