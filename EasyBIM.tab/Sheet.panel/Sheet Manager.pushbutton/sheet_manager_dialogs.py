# -*- coding: utf-8 -*-
"""Dialog windows for Sheet Manager (one class per bundle XAML)."""

from __future__ import print_function

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import Thickness
from System.Windows.Controls import ComboBox
from System.Windows.Controls import ComboBoxItem
from System.Windows.Controls import Orientation
from System.Windows.Controls import StackPanel
from System.Windows.Controls import TextBox

from pyrevit import forms

import sheet_manager_state as state


def _combo_key(combo):
    item = combo.SelectedItem
    if item is None:
        return None
    return getattr(item, "Tag", None)


def _add_combo_item(combo, label, key):
    item = ComboBoxItem()
    item.Content = label
    item.Tag = key
    combo.Items.Add(item)
    return item


def _select_combo_key(combo, key):
    for pos in range(combo.Items.Count):
        item = combo.Items[pos]
        if getattr(item, "Tag", None) == key:
            combo.SelectedIndex = pos
            return
    if combo.Items.Count:
        combo.SelectedIndex = 0


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


class _LinkResultRow(object):
    __slots__ = ("name", "element_type", "status", "error")

    def __init__(self, name, element_type, status, error):
        self.name = name
        self.element_type = element_type
        self.status = status
        self.error = error


class ReloadLinksResultsWindow(forms.WPFWindow):
    """Reload Links results: per-item status table."""

    def __init__(self, xaml_file_name, items):
        self._is_ready = False
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
        self._is_ready = True

    def ok_clicked(self, sender, args):
        del sender, args
        self.Close()


class LoadFromSourceWindow(forms.WPFWindow):
    """Shared picker for Load Sheet List / Load Print Set."""

    def __init__(self, xaml_file_name, mode, names):
        self._is_ready = False
        self.result = None
        self._mode = mode
        forms.WPFWindow.__init__(self, xaml_file_name)
        if mode == "schedule":
            self.Title = "Load Sheet List"
            self.prompt_tb.Text = "Sheet List:"
            self.show_element(self.numbersonly_cb)
        else:
            self.Title = "Load Print Set"
            self.prompt_tb.Text = "Print Set:"
            self.show_element(self.numbersonly_cb)
        for name in names:
            self.source_cb.Items.Add(name)
        if names:
            self.source_cb.SelectedIndex = 0
        self._is_ready = True

    def ok_clicked(self, sender, args):
        del sender, args
        if self.source_cb.SelectedItem is None:
            return
        self.result = (
            u"{0}".format(self.source_cb.SelectedItem),
            bool(self.numbersonly_cb.IsChecked),
        )
        self.Close()

    def cancel_clicked(self, sender, args):
        del sender, args
        self.Close()


class RevisionFilterRow(object):
    def __init__(self, revision, is_selected):
        self.revision_id = revision.get("id")
        self.sequence = revision.get("sequence")
        self.number = revision.get("number") or u""
        self.description = revision.get("description") or u""
        self.is_selected = bool(is_selected)


class FilterByRevisionWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, revision_rows, selected_ids):
        self._is_ready = False
        self.result = None
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._rows = []
        for revision in revision_rows:
            checked = (not selected_ids) or \
                (revision.get("id") in selected_ids)
            self._rows.append(RevisionFilterRow(revision, checked))
        self.revisions_dg.ItemsSource = self._rows
        self._is_ready = True

    def _set_all(self, value):
        self._rows = [RevisionFilterRow(
            {"id": row.revision_id, "sequence": row.sequence,
             "number": row.number, "description": row.description},
            value) for row in self._rows]
        self.revisions_dg.ItemsSource = None
        self.revisions_dg.ItemsSource = self._rows

    def select_all_clicked(self, sender, args):
        del sender, args
        self._set_all(True)

    def clear_all_clicked(self, sender, args):
        del sender, args
        self._set_all(False)

    def ok_clicked(self, sender, args):
        del sender, args
        self.result = set(row.revision_id for row in self._rows
                          if row.is_selected)
        self.Close()

    def cancel_clicked(self, sender, args):
        del sender, args
        self.Close()


class FilterByParameterWindow(forms.WPFWindow):
    """Native-schedule-style AND rules; 8 rows built programmatically."""

    RULE_COUNT = 8

    def __init__(self, xaml_file_name, field_options, current_rules,
                 add_params_checked):
        self._is_ready = False
        self.result = None
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._rule_rows = []
        current_rules = list(current_rules or [])
        for pos in range(self.RULE_COUNT):
            rule = current_rules[pos] if pos < len(current_rules) else None
            self._rule_rows.append(self._build_rule_row(field_options, rule))
        self.addparams_cb.IsChecked = bool(add_params_checked)
        self._is_ready = True

    def _build_rule_row(self, field_options, rule):
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal
        panel.Margin = Thickness(0, 3, 0, 3)

        field_cb = ComboBox()
        field_cb.Width = 240.0
        _add_combo_item(field_cb, "(none)", None)
        for key, label in field_options:
            _add_combo_item(field_cb, label, key)
        op_cb = ComboBox()
        op_cb.Width = 170.0
        op_cb.Margin = Thickness(8, 0, 0, 0)
        for op_key, op_label in state.FILTER_OPS:
            _add_combo_item(op_cb, op_label, op_key)
        value_tb = TextBox()
        value_tb.Width = 220.0
        value_tb.Margin = Thickness(8, 0, 0, 0)

        def op_changed(sender, args):
            del sender, args
            op_key = _combo_key(op_cb)
            value_tb.IsEnabled = op_key not in state.FILTER_OPS_NO_VALUE
        op_cb.SelectionChanged += op_changed

        if rule is not None:
            _select_combo_key(field_cb, rule[0])
            _select_combo_key(op_cb, rule[1])
            value_tb.Text = u"{0}".format(rule[2] or u"")
        else:
            field_cb.SelectedIndex = 0
            op_cb.SelectedIndex = 0

        panel.Children.Add(field_cb)
        panel.Children.Add(op_cb)
        panel.Children.Add(value_tb)
        self.rules_sp.Children.Add(panel)
        return (field_cb, op_cb, value_tb)

    def clear_clicked(self, sender, args):
        del sender, args
        for field_cb, op_cb, value_tb in self._rule_rows:
            field_cb.SelectedIndex = 0
            op_cb.SelectedIndex = 0
            value_tb.Text = u""

    def ok_clicked(self, sender, args):
        del sender, args
        rules = []
        for field_cb, op_cb, value_tb in self._rule_rows:
            field_key = _combo_key(field_cb)
            op_key = _combo_key(op_cb)
            if field_key is None or op_key is None:
                continue
            rules.append((field_key, op_key, value_tb.Text or u""))
        self.result = (rules, bool(self.addparams_cb.IsChecked))
        self.Close()

    def cancel_clicked(self, sender, args):
        del sender, args
        self.Close()


class SortWindow(forms.WPFWindow):
    LEVEL_COUNT = 4

    def __init__(self, xaml_file_name, field_options, current_levels):
        self._is_ready = False
        self.result = None
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._level_rows = []
        current_levels = list(current_levels or [])
        for pos in range(self.LEVEL_COUNT):
            level = current_levels[pos] if pos < len(current_levels) else None
            self._level_rows.append(
                self._build_level_row(field_options, level, pos))
        self._is_ready = True

    def _build_level_row(self, field_options, level, pos):
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal
        panel.Margin = Thickness(0, 3, 0, 3)
        field_cb = ComboBox()
        field_cb.Width = 280.0
        label = "Sort by:" if pos == 0 else "Then by:"
        _add_combo_item(field_cb, "(none)", None)
        for key, option_label in field_options:
            _add_combo_item(field_cb, option_label, key)
        direction_cb = ComboBox()
        direction_cb.Width = 150.0
        direction_cb.Margin = Thickness(8, 0, 0, 0)
        _add_combo_item(direction_cb, "Ascending", True)
        _add_combo_item(direction_cb, "Descending", False)
        direction_cb.SelectedIndex = 0
        if level is not None:
            _select_combo_key(field_cb, level[0])
            _select_combo_key(direction_cb, bool(level[1]))
        else:
            field_cb.SelectedIndex = 0
        from System.Windows.Controls import TextBlock as WpfTextBlock
        caption = WpfTextBlock()
        caption.Text = label
        caption.Width = 60.0
        caption.Margin = Thickness(0, 4, 8, 0)
        panel.Children.Add(caption)
        panel.Children.Add(field_cb)
        panel.Children.Add(direction_cb)
        self.levels_sp.Children.Add(panel)
        return (field_cb, direction_cb)

    def clear_clicked(self, sender, args):
        del sender, args
        for field_cb, direction_cb in self._level_rows:
            field_cb.SelectedIndex = 0
            direction_cb.SelectedIndex = 0

    def ok_clicked(self, sender, args):
        del sender, args
        levels = []
        for field_cb, direction_cb in self._level_rows:
            field_key = _combo_key(field_cb)
            if field_key is None:
                continue
            ascending = _combo_key(direction_cb)
            levels.append((field_key, bool(ascending)))
        self.result = levels
        self.Close()

    def cancel_clicked(self, sender, args):
        del sender, args
        self.Close()


class SavePrintSetWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, existing_names, sheet_count):
        self._is_ready = False
        self.result = None
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.summary_tb.Text = \
            "{0} checked sheet(s) will be saved in display order.".format(
                sheet_count)
        for name in existing_names:
            self.name_cb.Items.Add(name)
        self._is_ready = True

    def ok_clicked(self, sender, args):
        del sender, args
        name = u"{0}".format(self.name_cb.Text or u"").strip()
        if not name:
            forms.alert("Enter or pick a print set name.",
                        title="Save Print Set")
            return
        self.result = name
        self.Close()

    def cancel_clicked(self, sender, args):
        del sender, args
        self.Close()


class CopySheetInfoWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, source_options, target_count):
        self._is_ready = False
        self.result = None
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.targets_tb.Text = \
            "Copies info onto the {0} selected sheet(s).".format(target_count)
        for key, label in source_options:
            _add_combo_item(self.source_cb, label, key)
        if source_options:
            self.source_cb.SelectedIndex = 0
        self._is_ready = True

    def ok_clicked(self, sender, args):
        del sender, args
        source_key = _combo_key(self.source_cb)
        if source_key is None:
            return
        flags = (bool(self.sheetinfo_cb.IsChecked),
                 bool(self.tbinfo_cb.IsChecked),
                 bool(self.detailing_cb.IsChecked),
                 bool(self.withviews_cb.IsChecked))
        if not any(flags):
            forms.alert("Pick at least one duplicate option.",
                        title="Copy Sheet Info")
            return
        self.result = (source_key,) + flags
        self.Close()

    def cancel_clicked(self, sender, args):
        del sender, args
        self.Close()


class SearchReplacePreviewRow(object):
    def __init__(self, sheet, column, old_value, new_value):
        self.sheet = sheet
        self.column = column
        self.old_value = old_value
        self.new_value = new_value


class SearchReplaceWindow(forms.WPFWindow):
    """plan_provider(find, replace, match_case) -> [(row, column, old, new)]"""

    def __init__(self, xaml_file_name, plan_provider):
        self._is_ready = False
        self.result = None
        self._plan_provider = plan_provider
        self._plan = []
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._is_ready = True

    def preview_clicked(self, sender, args):
        del sender, args
        find_text = self.find_tb.Text or u""
        if not find_text:
            forms.alert("Enter a search text first.",
                        title="Search & Replace")
            return
        self._plan = self._plan_provider(
            find_text, self.replace_tb.Text or u"",
            bool(self.matchcase_cb.IsChecked))
        preview = [SearchReplacePreviewRow(
            row.number, column.header, old, new)
            for row, column, old, new in self._plan]
        self.preview_dg.ItemsSource = None
        self.preview_dg.ItemsSource = preview
        self.count_tb.Text = "{0} replacement(s) found".format(len(preview))
        self.ok_b.IsEnabled = bool(preview)

    def ok_clicked(self, sender, args):
        del sender, args
        if not self._plan:
            return
        self.result = list(self._plan)
        self.Close()

    def cancel_clicked(self, sender, args):
        del sender, args
        self.Close()
