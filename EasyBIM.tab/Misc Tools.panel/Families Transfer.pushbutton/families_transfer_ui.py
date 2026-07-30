# -*- coding: utf-8 -*-
"""WPF windows for the Families Transfer command."""

from pyrevit import forms
from pyrevit.framework import Windows

from families_transfer_state import filter_family_options
from families_transfer_state import filter_open_family_document_options
from families_transfer_state import get_selected_document_keys
from families_transfer_state import get_selected_family_keys
from families_transfer_state import get_selected_open_family_document_keys
from families_transfer_state import group_family_options_by_category
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


def _option_text(name, category_name):
    category_name = _safe_text(category_name)
    if category_name:
        return "{}  ({})".format(_safe_text(name), category_name)
    return _safe_text(name)


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


class SourceSelectionWindow(forms.WPFWindow):
    def __init__(
        self,
        xaml_file_name,
        selected_families,
        open_family_documents,
        selected_document_keys=None,
        status_text="",
    ):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.selected_families = list(selected_families or [])
        self.open_family_documents = list(open_family_documents or [])
        self.selected_family_keys = set(get_selected_family_keys(self.selected_families))
        self.selected_document_keys = set(selected_document_keys or [])
        self.result = None
        self._selected_family_controls = []
        self._open_rfa_controls = []

        restore_open_family_document_selection(
            self.open_family_documents,
            self.selected_document_keys,
        )

        self.selected_count_tb.Text = _family_count_text(len(self.selected_families))
        self.status_tb.Text = _safe_text(status_text)
        self._populate_selected_families()
        self._populate_open_family_documents()

    def _populate_selected_families(self):
        self.SelectedSourceListPanel.Children.Clear()
        self._selected_family_controls = []
        visible_families = filter_family_options(
            self.selected_families,
            _search_text(self.SelectedSourceSearchBox),
        )
        for family in visible_families:
            checkbox = Windows.Controls.CheckBox()
            checkbox.Content = _option_text(
                getattr(family, "name", ""),
                getattr(family, "category_name", ""),
            )
            checkbox.IsChecked = bool(family.is_selected)
            checkbox.Tag = family
            checkbox.Margin = Windows.Thickness(8, 4, 8, 4)
            self.SelectedSourceListPanel.Children.Add(checkbox)
            self._selected_family_controls.append(checkbox)

        if not visible_families:
            if self.selected_families:
                _add_empty_text(self.SelectedSourceListPanel, "No selected families match the search.")
            else:
                _add_empty_text(self.SelectedSourceListPanel, "No active-project families selected.")

    def _sync_selected_family_controls(self):
        for checkbox in self._selected_family_controls:
            option = getattr(checkbox, "Tag", None)
            if option is not None:
                option.is_selected = bool(getattr(checkbox, "IsChecked", False))

    def _sync_open_rfa_controls(self):
        for checkbox in self._open_rfa_controls:
            option = getattr(checkbox, "Tag", None)
            if option is not None:
                option.is_selected = bool(getattr(checkbox, "IsChecked", False))

    def _populate_open_family_documents(self):
        self.OpenFamilyListPanel.Children.Clear()
        self._open_rfa_controls = []
        visible_documents = filter_open_family_document_options(
            self.open_family_documents,
            _search_text(self.OpenFamilySearchBox),
        )
        for document in visible_documents:
            checkbox = Windows.Controls.CheckBox()
            checkbox.Content = _option_text(
                getattr(document, "display_name", ""),
                getattr(document, "category_name", ""),
            )
            checkbox.IsChecked = bool(document.is_selected)
            checkbox.Tag = document
            checkbox.Margin = Windows.Thickness(8, 4, 8, 4)
            self.OpenFamilyListPanel.Children.Add(checkbox)
            self._open_rfa_controls.append(checkbox)

        if not visible_documents:
            if self.open_family_documents:
                _add_empty_text(self.OpenFamilyListPanel, "No opened .rfa files match the search.")
            else:
                _add_empty_text(self.OpenFamilyListPanel, "No opened .rfa family files were found.")

        checked_count = len(get_selected_open_family_document_keys(self.open_family_documents))
        if self.open_family_documents:
            self.open_rfa_count_tb.Text = "{} opened .rfa file(s), {} checked.".format(
                len(self.open_family_documents),
                checked_count,
            )
        else:
            self.open_rfa_count_tb.Text = "No opened .rfa family files were found."

    def _read_selected_document_keys(self):
        self._sync_open_rfa_controls()
        self.selected_document_keys = set(
            get_selected_open_family_document_keys(self.open_family_documents)
        )
        return self.selected_document_keys

    def _read_selected_family_keys(self):
        self._sync_selected_family_controls()
        self.selected_family_keys = set(get_selected_family_keys(self.selected_families))
        self.selected_count_tb.Text = _family_count_text(len(self.selected_family_keys))
        return self.selected_family_keys

    def selected_source_search_changed(self, sender, args):
        del sender, args
        self._read_selected_family_keys()
        self._populate_selected_families()

    def open_family_search_changed(self, sender, args):
        del sender, args
        self._sync_open_rfa_controls()
        self._populate_open_family_documents()

    def open_family_select_all_click(self, sender, args):
        del sender, args
        for checkbox in self._open_rfa_controls:
            checkbox.IsChecked = True
        self._read_selected_document_keys()
        self._populate_open_family_documents()

    def open_family_select_none_click(self, sender, args):
        del sender, args
        for checkbox in self._open_rfa_controls:
            checkbox.IsChecked = False
        self._read_selected_document_keys()
        self._populate_open_family_documents()

    def select_click(self, sender, args):
        del sender, args
        self._read_selected_family_keys()
        self._read_selected_document_keys()
        self.result = "select"
        self.Close()

    def next_click(self, sender, args):
        del sender, args
        self._read_selected_family_keys()
        self._read_selected_document_keys()
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
        self._category_expanders = []
        self._expanded_categories = {}

        restore_family_selection(self.families, self.selected_family_keys)
        self._populate()

    def _sync_family_controls(self):
        for checkbox in self._controls:
            option = getattr(checkbox, "Tag", None)
            if option is not None:
                option.is_selected = bool(getattr(checkbox, "IsChecked", False))

    def _sync_category_expanders(self):
        for expander in self._category_expanders:
            category_name = getattr(expander, "Tag", "")
            if category_name:
                self._expanded_categories[category_name] = bool(getattr(expander, "IsExpanded", True))

    def _populate(self):
        self.FamilyListPanel.Children.Clear()
        self._controls = []
        self._category_expanders = []
        visible_families = filter_family_options(
            self.families,
            _search_text(self.FamilySearchBox),
        )
        for group in group_family_options_by_category(visible_families):
            expander = Windows.Controls.Expander()
            expander.Header = "{} ({})".format(group.category_name, len(group.families))
            expander.IsExpanded = self._expanded_categories.get(group.category_name, True)
            expander.Tag = group.category_name
            expander.Margin = Windows.Thickness(4, 6, 4, 6)

            category_panel = Windows.Controls.StackPanel()

            for family in group.families:
                checkbox = Windows.Controls.CheckBox()
                checkbox.Content = family.name
                checkbox.IsChecked = bool(family.is_selected)
                checkbox.Tag = family
                checkbox.Margin = Windows.Thickness(18, 4, 8, 4)
                category_panel.Children.Add(checkbox)
                self._controls.append(checkbox)

            expander.Content = category_panel
            self.FamilyListPanel.Children.Add(expander)
            self._category_expanders.append(expander)

        if not visible_families:
            _add_empty_text(self.FamilyListPanel, "No transferable families match the search.")

        if visible_families and len(visible_families) != len(self.families):
            self.count_tb.Text = "{} transferable families, {} shown.".format(
                len(self.families),
                len(visible_families),
            )
        else:
            self.count_tb.Text = "{} transferable families.".format(len(self.families))

    def _read_selected_family_keys(self):
        self._sync_family_controls()
        return set(get_selected_family_keys(self.families))

    def family_search_changed(self, sender, args):
        del sender, args
        self._sync_family_controls()
        self._sync_category_expanders()
        self._populate()

    def select_all_click(self, sender, args):
        del sender, args
        for checkbox in self._controls:
            checkbox.IsChecked = True
        self._read_selected_family_keys()
        self._sync_category_expanders()
        self._populate()

    def select_none_click(self, sender, args):
        del sender, args
        for checkbox in self._controls:
            checkbox.IsChecked = False
        self._read_selected_family_keys()
        self._sync_category_expanders()
        self._populate()

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
