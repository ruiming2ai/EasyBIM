# -*- coding: utf-8 -*-
"""WPF windows for Batch Duplicate Host."""

from pyrevit import forms
from pyrevit.framework import Windows

from batch_duplicate_host_state import restore_category_selection
from batch_duplicate_host_state import restore_type_selection
from batch_duplicate_host_state import sort_family_groups


TITLE = "Batch Duplicate Host"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


class SourceSelectionWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.should_select = False

    def select_click(self, sender, args):
        del sender, args
        self.should_select = True
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.should_select = False
        self.Close()


class CategorySelectionWindow(forms.WPFWindow):
    def __init__(
        self,
        xaml_file_name,
        target_documents,
        category_loader,
        initial_document_key=None,
        initial_selected_category_ids=None,
    ):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.target_documents = list(target_documents or [])
        self.category_loader = category_loader
        self.initial_document_key = initial_document_key
        self.initial_selected_category_ids = set(initial_selected_category_ids or [])
        self.selected_target_document = None
        self.selected_category_ids = set()
        self.was_back = False
        self.result = None
        self._active_document_key = None
        self._selection_cache = {}
        self._category_controls = []

        self.DocumentComboBox.ItemsSource = self.target_documents
        if self.target_documents:
            self.DocumentComboBox.SelectedItem = self._find_initial_document()

    def _find_initial_document(self):
        for option in self.target_documents:
            if option.document_key == self.initial_document_key:
                return option
        return self.target_documents[0]

    def _read_selected_category_ids(self):
        selected_ids = set()
        for checkbox in self._category_controls:
            if bool(getattr(checkbox, "IsChecked", False)):
                option = getattr(checkbox, "Tag", None)
                if option:
                    selected_ids.add(option.category_id)
        return selected_ids

    def _load_categories_for_document(self, option):
        categories = list(self.category_loader(option) or [])
        selected_ids = self._selection_cache.get(option.document_key)
        if selected_ids is None:
            if option.document_key == self.initial_document_key:
                selected_ids = self.initial_selected_category_ids
            else:
                selected_ids = set()

        restore_category_selection(categories, selected_ids)
        self._category_controls = []
        self.CategoryListPanel.Children.Clear()

        for category in categories:
            checkbox = Windows.Controls.CheckBox()
            checkbox.Content = category.name
            checkbox.IsChecked = category.is_selected
            checkbox.Tag = category
            checkbox.Margin = Windows.Thickness(16, 4, 4, 4)
            self.CategoryListPanel.Children.Add(checkbox)
            self._category_controls.append(checkbox)

        self._active_document_key = option.document_key

    def document_changed(self, sender, args):
        del sender, args
        if self._active_document_key is not None:
            self._selection_cache[self._active_document_key] = self._read_selected_category_ids()

        option = getattr(self.DocumentComboBox, "SelectedItem", None)
        if option is None:
            self.CategoryListPanel.Children.Clear()
            self._category_controls = []
            self._active_document_key = None
            return

        self._load_categories_for_document(option)

    def select_all_click(self, sender, args):
        del sender, args
        for checkbox in self._category_controls:
            checkbox.IsChecked = True

    def select_none_click(self, sender, args):
        del sender, args
        for checkbox in self._category_controls:
            checkbox.IsChecked = False

    def ok_click(self, sender, args):
        del sender, args
        option = getattr(self.DocumentComboBox, "SelectedItem", None)
        if option is None:
            forms.alert("Select a target document.", title=TITLE)
            return

        selected_ids = self._read_selected_category_ids()
        if not selected_ids:
            forms.alert("Select at least one category.", title=TITLE)
            return

        self.selected_target_document = option
        self.selected_category_ids = selected_ids
        self.result = "ok"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = "cancel"
        self.Close()

    def back_click(self, sender, args):
        del sender, args
        self.was_back = True
        self.result = "back"
        self.Close()


class FamilyTypeSelectionWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, family_groups, initial_selected_type_ids=None):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.family_groups = sort_family_groups(list(family_groups or []))
        self.initial_selected_type_ids = set(initial_selected_type_ids or [])
        self.selected_type_ids = set()
        self.was_back = False
        self.result = None
        self._type_controls = []

        restore_type_selection(self.family_groups, self.initial_selected_type_ids)
        self._populate_tree()

    def _populate_tree(self):
        self.FamilyTypeTree.Items.Clear()
        self._type_controls = []

        for group in self.family_groups:
            group_item = Windows.Controls.TreeViewItem()
            group_item.Header = group.name
            group_item.IsExpanded = True

            for family_type in group.types:
                type_item = Windows.Controls.TreeViewItem()
                checkbox = Windows.Controls.CheckBox()
                checkbox.Content = family_type.type_name
                checkbox.IsChecked = family_type.is_selected
                checkbox.Tag = family_type
                checkbox.Margin = Windows.Thickness(4, 2, 2, 2)
                type_item.Header = checkbox
                group_item.Items.Add(type_item)
                self._type_controls.append(checkbox)

            self.FamilyTypeTree.Items.Add(group_item)

    def _read_selected_type_ids(self):
        selected_ids = set()
        for checkbox in self._type_controls:
            if bool(getattr(checkbox, "IsChecked", False)):
                family_type = getattr(checkbox, "Tag", None)
                if family_type:
                    selected_ids.add(family_type.type_id)
        return selected_ids

    def select_all_click(self, sender, args):
        del sender, args
        for checkbox in self._type_controls:
            checkbox.IsChecked = True

    def select_none_click(self, sender, args):
        del sender, args
        for checkbox in self._type_controls:
            checkbox.IsChecked = False

    def ok_click(self, sender, args):
        del sender, args
        selected_ids = self._read_selected_type_ids()
        if not selected_ids:
            forms.alert("Select at least one family type.", title=TITLE)
            return

        self.selected_type_ids = selected_ids
        self.result = "ok"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = "cancel"
        self.Close()

    def back_click(self, sender, args):
        del sender, args
        self.was_back = True
        self.result = "back"
        self.Close()


class OffsetWindow(forms.WPFWindow):
    def __init__(
        self,
        xaml_file_name,
        unit_text,
        length_parser,
        initial_x_offset_text="0",
        initial_y_offset_text="0",
        initial_z_offset_text="0",
        initial_align_orientation=True,
    ):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.length_parser = length_parser
        self.UnitTextBlock.Text = _safe_text(unit_text)
        self.XOffsetTextBox.Text = _safe_text(initial_x_offset_text) or "0"
        self.YOffsetTextBox.Text = _safe_text(initial_y_offset_text) or "0"
        self.ZOffsetTextBox.Text = _safe_text(initial_z_offset_text) or "0"
        self.AlignOrientationCheckBox.IsChecked = bool(initial_align_orientation)
        self.x_offset = 0.0
        self.y_offset = 0.0
        self.z_offset = 0.0
        self.align_orientation = bool(initial_align_orientation)
        self.was_back = False
        self.result = None

    @property
    def x_offset_text(self):
        return _safe_text(self.XOffsetTextBox.Text)

    @property
    def y_offset_text(self):
        return _safe_text(self.YOffsetTextBox.Text)

    @property
    def z_offset_text(self):
        return _safe_text(self.ZOffsetTextBox.Text)

    def run_click(self, sender, args):
        del sender, args
        success, x_value, x_error = self.length_parser("Hand / Local X", self.x_offset_text)
        if not success:
            forms.alert(x_error, title=TITLE)
            return

        success, y_value, y_error = self.length_parser("Facing / Local Y", self.y_offset_text)
        if not success:
            forms.alert(y_error, title=TITLE)
            return

        success, z_value, z_error = self.length_parser("Up / Local Z", self.z_offset_text)
        if not success:
            forms.alert(z_error, title=TITLE)
            return

        self.x_offset = float(x_value)
        self.y_offset = float(y_value)
        self.z_offset = float(z_value)
        self.align_orientation = bool(self.AlignOrientationCheckBox.IsChecked)
        self.result = "ok"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = "cancel"
        self.Close()

    def back_click(self, sender, args):
        del sender, args
        self.was_back = True
        self.result = "back"
        self.Close()
