# -*- coding: utf-8 -*-
"""Selective Transfer and V/G edit dialogs.

VgEditDialog columns are generated at runtime from state.VG_GROUP_COLUMNS.
Cell templates are produced with XamlReader.Parse; parsed fragments never
contain event attributes - interactivity is wired at grid level.
"""

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import RoutedEventHandler
from System.Windows import Visibility
from System.Windows.Controls import Button
from System.Windows.Controls import CheckBox
from System.Windows.Controls import DataGridLength
from System.Windows.Controls import DataGridLengthUnitType
from System.Windows.Controls import DataGridTemplateColumn
from System.Windows.Controls import DataGridTextColumn
from System.Windows.Controls.Primitives import ButtonBase
from System.Windows.Data import Binding
from System.Windows.Data import BindingMode
from System.Windows.Markup import XamlReader

from pyrevit import forms

import view_template_transfer_revit as vtrevit
import view_template_transfer_state as state


TITLE = "View Template"

_XNS = ('xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
        'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"')

_TRANSFER_CHECK_TEMPLATE = (
    '<DataTemplate {ns}>'
    '<CheckBox HorizontalAlignment="Center" VerticalAlignment="Center"'
    ' IsChecked="{{Binding is_checked, Mode=TwoWay,'
    ' UpdateSourceTrigger=PropertyChanged}}"/>'
    '</DataTemplate>')

_FROZEN_CHECK_TEMPLATE = (
    '<DataTemplate {ns}>'
    '<CheckBox HorizontalAlignment="Center" VerticalAlignment="Center"'
    ' IsEnabled="False" Focusable="False"'
    ' IsChecked="{{Binding {attr}, Mode=OneWay}}"/>'
    '</DataTemplate>')

_FROZEN_CELL_STYLE = (
    '<Style {ns} TargetType="{{x:Type DataGridCell}}">'
    '<Setter Property="Background" Value="#F3F3F3"/>'
    '<Setter Property="Foreground" Value="#9B9B9B"/>'
    '</Style>')

_FROZEN_TEXT_STYLE = (
    '<Style {ns} TargetType="{{x:Type TextBlock}}">'
    '<Setter Property="Foreground" Value="#9B9B9B"/>'
    '<Setter Property="Margin" Value="6,0,4,0"/>'
    '<Setter Property="VerticalAlignment" Value="Center"/>'
    '</Style>')


def _parse_fragment(fragment, attr=""):
    return XamlReader.Parse(fragment.format(ns=_XNS, attr=attr))


class SelectiveTransferDialog(forms.WPFWindow):
    """Pick which source settings transfer. result is a TransferPlan or None."""

    def __init__(self, xaml_file_name, doc, source_kind, source_element,
                 source_name, selective_state, payload_cache):
        self._is_ready = False
        self.result = None
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.doc = doc
        self.source_kind = source_kind
        self.source_element = source_element
        self.source_name = state.safe_text(source_name)
        self.selective_state = selective_state
        self.payload_cache = payload_cache

        self.source_name_tb.Text = u"Source: {}".format(self.source_name)
        self.params_dg.AddHandler(
            ButtonBase.ClickEvent,
            RoutedEventHandler(self.grid_button_click))
        self._refresh_rows()
        self._is_ready = True

    def _refresh_rows(self):
        self.params_dg.ItemsSource = None
        self.params_dg.ItemsSource = self.selective_state.rows
        plan = state.build_transfer_plan(self.selective_state.rows)
        self.selective_status_tb.Text = "{} setting(s) selected.".format(
            plan.setting_count)

    def _ensure_group_items(self, row):
        if row.items is not None:
            return
        items, payloads = vtrevit.read_vg_group(
            self.doc, self.source_element, row.group_key)
        row.items = items
        self.payload_cache[row.group_key] = payloads
        self.selective_state.loaded_groups.add(row.group_key)

    def _open_edit_dialog(self, row):
        self._ensure_group_items(row)
        dialog = VgEditDialog(
            "VgEditDialog.xaml", row.group_key, self.source_name, row.items)
        dialog.ShowDialog()
        self._refresh_rows()

    def grid_button_click(self, sender, args):
        del sender
        if not getattr(self, "_is_ready", False):
            return
        source = args.OriginalSource
        row = getattr(source, "DataContext", None)
        if getattr(row, "parameter_id_int", None) is None:
            return
        if isinstance(source, CheckBox):
            if getattr(row, "group_key", None) is not None:
                self._ensure_group_items(row)
            state.apply_include_click(
                row, state.resolve_include_click(row, source.IsChecked))
            self._refresh_rows()
        elif isinstance(source, Button):
            if getattr(row, "group_key", None) is not None:
                self._open_edit_dialog(row)

    def transfer_click(self, sender, args):
        del sender, args
        plan = state.build_transfer_plan(self.selective_state.rows)
        if state.plan_is_empty(plan):
            forms.alert("Select at least one setting to transfer.", title=TITLE)
            return
        self.result = plan
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.Close()

    def window_closing(self, sender, args):
        del sender, args


class VgEditDialog(forms.WPFWindow):
    """Read-only view of one V/G group with per-item Transfer checkboxes."""

    def __init__(self, xaml_file_name, group_key, source_name, items):
        self._is_ready = False
        self.result = None
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.group_key = group_key
        self.items = list(items or [])
        self._snapshot = state.snapshot_checked_keys(self.items)

        group_title = state.VG_GROUP_TITLES.get(group_key, "V/G Overrides")
        self.Title = u"{} - {}".format(group_title, state.safe_text(source_name))

        if not self.items:
            self.empty_hint_tb.Text = state.vg_group_empty_hint(
                group_key,
                links_api_available=vtrevit.links_api_available(),
            )
            self.empty_hint_tb.Visibility = Visibility.Visible
            self.items_dg.Visibility = Visibility.Collapsed
            self.check_all_btn.IsEnabled = False
            self.clear_btn.IsEnabled = False
        else:
            self._build_columns(group_key)
            self.items_dg.ItemsSource = self.items
        self._is_ready = True

    def _build_columns(self, group_key):
        self.items_dg.Columns.Clear()

        transfer_column = DataGridTemplateColumn()
        transfer_column.Header = "Transfer"
        transfer_column.Width = DataGridLength(70.0)
        transfer_column.CanUserResize = False
        transfer_column.CellTemplate = _parse_fragment(_TRANSFER_CHECK_TEMPLATE)
        self.items_dg.Columns.Add(transfer_column)

        name_column = DataGridTextColumn()
        name_column.Header = "Name"
        name_binding = Binding("name")
        name_binding.Mode = BindingMode.OneWay
        name_column.Binding = name_binding
        name_column.IsReadOnly = True
        name_column.MinWidth = 180.0
        name_column.Width = DataGridLength(1.0, DataGridLengthUnitType.Star)
        self.items_dg.Columns.Add(name_column)

        for header, width, kind, attr in state.VG_GROUP_COLUMNS.get(group_key, ()):
            if kind == "check":
                column = DataGridTemplateColumn()
                column.CellTemplate = _parse_fragment(
                    _FROZEN_CHECK_TEMPLATE, attr)
            else:
                column = DataGridTextColumn()
                binding = Binding(attr)
                binding.Mode = BindingMode.OneWay
                column.Binding = binding
                column.IsReadOnly = True
                column.ElementStyle = _parse_fragment(_FROZEN_TEXT_STYLE)
            column.Header = header
            column.Width = DataGridLength(float(width))
            column.CellStyle = _parse_fragment(_FROZEN_CELL_STYLE)
            self.items_dg.Columns.Add(column)

    def _refresh_items(self):
        self.items_dg.ItemsSource = None
        self.items_dg.ItemsSource = self.items

    def check_all_click(self, sender, args):
        del sender, args
        for item in self.items:
            item.is_checked = True
        self._refresh_items()

    def clear_click(self, sender, args):
        del sender, args
        for item in self.items:
            item.is_checked = False
        self._refresh_items()

    def ok_click(self, sender, args):
        del sender, args
        self.result = True
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.Close()

    def window_closing(self, sender, args):
        del sender, args
        if self.result is None:
            state.restore_checked_keys(self.items, self._snapshot)
