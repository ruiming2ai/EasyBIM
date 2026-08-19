# -*- coding: utf-8 -*-
"""WPF windows specific to the Families Transfer command.

The source page, the link page and the family browser are shared with
Families Downgrade and live in ``easybim.family_selection_ui``.
"""

from pyrevit import forms
from pyrevit.framework import Windows

from easybim.family_selection_state import get_selected_document_keys
from easybim.family_selection_state import restore_document_selection
from easybim.family_selection_ui import _family_count_text


class TargetSelectionWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, documents, selected_document_keys=None):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.documents = list(documents or [])
        self.selected_document_keys = set(selected_document_keys or [])
        self.result = None
        self._controls = []

        restore_document_selection(self.documents, self.selected_document_keys)
        self._populate()

    def _populate(self):
        self.DocumentListPanel.Children.Clear()
        self._controls = []
        for document in self.documents:
            checkbox = Windows.Controls.CheckBox()
            checkbox.Content = document.display_name
            checkbox.IsChecked = bool(document.is_selected)
            checkbox.Tag = document
            checkbox.Margin = Windows.Thickness(8, 4, 8, 4)
            self.DocumentListPanel.Children.Add(checkbox)
            self._controls.append(checkbox)

        if self.documents:
            self.count_tb.Text = "{} open target file(s).".format(len(self.documents))
        else:
            self.count_tb.Text = "No other open project files were found. Export is still available."

    def _read_selected_document_keys(self):
        for checkbox in self._controls:
            option = getattr(checkbox, "Tag", None)
            if option is not None:
                option.is_selected = bool(getattr(checkbox, "IsChecked", False))
        return set(get_selected_document_keys(self.documents))

    def select_all_click(self, sender, args):
        del sender, args
        for checkbox in self._controls:
            checkbox.IsChecked = True

    def select_none_click(self, sender, args):
        del sender, args
        for checkbox in self._controls:
            checkbox.IsChecked = False

    def back_click(self, sender, args):
        del sender, args
        self.selected_document_keys = self._read_selected_document_keys()
        self.result = "back"
        self.Close()

    def next_click(self, sender, args):
        del sender, args
        self.selected_document_keys = self._read_selected_document_keys()
        self.result = "next"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = "cancel"
        self.Close()


class ActionWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, family_count, target_count):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = None
        self.summary_tb.Text = (
            "{}\n"
            "{} target file(s) checked for Transfer."
        ).format(_family_count_text(family_count), int(target_count or 0))

    def back_click(self, sender, args):
        del sender, args
        self.result = "back"
        self.Close()

    def export_click(self, sender, args):
        del sender, args
        self.result = "export"
        self.Close()

    def transfer_click(self, sender, args):
        del sender, args
        self.result = "transfer"
        self.Close()

    def transfer_close_all_rfa_click(self, sender, args):
        del sender, args
        self.result = "transfer_close_all_rfa"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = "cancel"
        self.Close()
