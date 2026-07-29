# -*- coding: utf-8 -*-
"""WPF windows for the Families Transfer command."""

from pyrevit import forms
from pyrevit.framework import Windows

from families_transfer_state import get_selected_document_keys
from families_transfer_state import get_selected_family_keys
from families_transfer_state import get_selected_open_family_document_keys
from families_transfer_state import restore_document_selection
from families_transfer_state import restore_family_selection
from families_transfer_state import restore_open_family_document_selection


TITLE = "Families Transfer"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _family_count_text(count):
    count = int(count or 0)
    if count == 1:
        return "1 family selected."
    return "{} families selected.".format(count)


class SourceSelectionWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, selected_count, status_text=""):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = None
        self.selected_count_tb.Text = _family_count_text(selected_count)
        self.status_tb.Text = _safe_text(status_text)

    def select_click(self, sender, args):
        del sender, args
        self.result = "select"
        self.Close()

    def next_click(self, sender, args):
        del sender, args
        self.result = "next"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = "cancel"
        self.Close()


class FamilySelectionWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, families, selected_family_keys=None):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.families = list(families or [])
        self.selected_family_keys = set(selected_family_keys or [])
        self.result = None
        self._controls = []

        restore_family_selection(self.families, self.selected_family_keys)
        self._populate()

    def _populate(self):
        self.FamilyListPanel.Children.Clear()
        self._controls = []
        for family in self.families:
            checkbox = Windows.Controls.CheckBox()
            checkbox.Content = family.name
            checkbox.IsChecked = bool(family.is_selected)
            checkbox.Tag = family
            checkbox.Margin = Windows.Thickness(8, 4, 8, 4)
            self.FamilyListPanel.Children.Add(checkbox)
            self._controls.append(checkbox)

        self.count_tb.Text = "{} transferable family/families.".format(len(self.families))

    def _read_selected_family_keys(self):
        for checkbox in self._controls:
            option = getattr(checkbox, "Tag", None)
            if option is not None:
                option.is_selected = bool(getattr(checkbox, "IsChecked", False))
        return set(get_selected_family_keys(self.families))

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
        self.selected_family_keys = self._read_selected_family_keys()
        self.result = "back"
        self.Close()

    def next_click(self, sender, args):
        del sender, args
        selected = self._read_selected_family_keys()
        if not selected:
            forms.alert("Select at least one family.", title=TITLE)
            return
        self.selected_family_keys = selected
        self.result = "next"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = "cancel"
        self.Close()


class OpenFamilyDocumentsWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, documents, selected_document_keys=None):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.documents = list(documents or [])
        self.selected_document_keys = set(selected_document_keys or [])
        self.result = None
        self._controls = []

        restore_open_family_document_selection(self.documents, self.selected_document_keys)
        self._populate()

    def _populate(self):
        self.OpenFamilyListPanel.Children.Clear()
        self._controls = []
        for document in self.documents:
            checkbox = Windows.Controls.CheckBox()
            checkbox.Content = document.display_name
            checkbox.IsChecked = bool(document.is_selected)
            checkbox.Tag = document
            checkbox.Margin = Windows.Thickness(8, 4, 8, 4)
            self.OpenFamilyListPanel.Children.Add(checkbox)
            self._controls.append(checkbox)

        if self.documents:
            self.count_tb.Text = "{} opened .rfa file(s).".format(len(self.documents))
        else:
            self.count_tb.Text = "No opened .rfa family files were found."

    def _read_selected_document_keys(self):
        for checkbox in self._controls:
            option = getattr(checkbox, "Tag", None)
            if option is not None:
                option.is_selected = bool(getattr(checkbox, "IsChecked", False))
        return set(get_selected_open_family_document_keys(self.documents))

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
