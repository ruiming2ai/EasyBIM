# -*- coding: utf-8 -*-
"""Sheet Manager main window and dynamic DataGrid construction.

Columns are generated at runtime (parameter and revision counts are unknown
at XAML time). Cell templates and per-column cell styles are produced with
XamlReader.Parse; parsed fragments never contain event attributes - all
interactivity is wired at grid level or on programmatically built headers.
"""

from __future__ import print_function

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import FontWeights
from System.Windows import HorizontalAlignment
from System.Windows import RoutedEventHandler
from System.Windows import TextTrimming
from System.Windows import Thickness
from System.Windows.Controls import CheckBox
from System.Windows.Controls import DataGridCell
from System.Windows.Controls import DataGridLength
from System.Windows.Controls import DataGridTemplateColumn
from System.Windows.Controls import DataGridTextColumn
from System.Windows.Controls import StackPanel
from System.Windows.Controls import TextBlock
from System.Windows.Controls.Primitives import ButtonBase
from System.Windows.Data import Binding
from System.Windows.Data import BindingMode
from System.Windows.Markup import XamlReader
from System.Windows.Media import VisualTreeHelper

from pyrevit import DB
from pyrevit import framework
from pyrevit import forms
from pyrevit import revit
from pyrevit import script

from easybim import print_sets

import sheet_manager_revit as smrevit
import sheet_manager_state as state


LOGGER = script.get_logger()

_XNS = ('xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
        'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"')

_CHECKBOX_CELL_TEMPLATE = (
    '<DataTemplate {ns}>'
    '<CheckBox x:Name="cell_cb" HorizontalAlignment="Center"'
    ' VerticalAlignment="Center"'
    ' IsChecked="{{Binding {attr}, Mode=TwoWay,'
    ' UpdateSourceTrigger=PropertyChanged}}"/>'
    '<DataTemplate.Triggers>'
    '<DataTrigger Binding="{{Binding {attr}_state}}" Value="locked">'
    '<Setter TargetName="cell_cb" Property="IsEnabled" Value="False"/>'
    '</DataTrigger>'
    '</DataTemplate.Triggers>'
    '</DataTemplate>')

_TEXT_CELL_STYLE = (
    '<Style {ns} TargetType="{{x:Type DataGridCell}}">'
    '<Style.Triggers>'
    '<DataTrigger Binding="{{Binding {attr}_state}}" Value="dirty">'
    '<Setter Property="Foreground" Value="#C00000"/>'
    '<Setter Property="FontWeight" Value="SemiBold"/>'
    '</DataTrigger>'
    '<DataTrigger Binding="{{Binding {attr}_state}}" Value="locked">'
    '<Setter Property="Foreground" Value="#9B9B9B"/>'
    '<Setter Property="Background" Value="#F3F3F3"/>'
    '</DataTrigger>'
    '<DataTrigger Binding="{{Binding {attr}_state}}" Value="duplicated">'
    '<Setter Property="Foreground" Value="#C00000"/>'
    '<Setter Property="Background" Value="#F3F3F3"/>'
    '<Setter Property="ToolTip"'
    ' Value="This sheet has more than one title block."/>'
    '</DataTrigger>'
    '</Style.Triggers>'
    '</Style>')

_CHECK_CELL_STYLE = (
    '<Style {ns} TargetType="{{x:Type DataGridCell}}">'
    '<Style.Triggers>'
    '<DataTrigger Binding="{{Binding {attr}_state}}" Value="dirty">'
    '<Setter Property="Background" Value="#F8D7D7"/>'
    '</DataTrigger>'
    '<DataTrigger Binding="{{Binding {attr}_state}}" Value="cloud">'
    '<Setter Property="Background" Value="#EDF3FA"/>'
    '<Setter Property="ToolTip"'
    ' Value="On this sheet via revision cloud(s)."/>'
    '</DataTrigger>'
    '</Style.Triggers>'
    '</Style>')


def _parse_fragment(fragment, attr):
    return XamlReader.Parse(fragment.format(ns=_XNS, attr=attr))


def _find_parent_cell(element):
    current = element
    while current is not None:
        if isinstance(current, DataGridCell):
            return current
        try:
            current = VisualTreeHelper.GetParent(current)
        except Exception:
            return None
    return None


class SheetRow(forms.Reactive, state.SheetRowBase):
    """Grid row with WPF change notification.

    forms.Reactive implements INotifyPropertyChanged; attribute values are
    set directly and notifications raised manually via notify().
    """

    def __init__(self, sheet_id, number, name,
                 is_placeholder=False, tblock_count=1):
        state.SheetRowBase.__init__(
            self, sheet_id, number, name, is_placeholder, tblock_count)

    def notify(self, attr):
        try:
            self.OnPropertyChanged(attr)
        except Exception:
            pass


class SheetManagerWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._all_rows = []
        self._visible_rows = []
        self._columns = []
        self._spec_by_column = {}
        self._revision_rows = []
        self._tb_map = {}
        self._sheets_by_id = {}
        self._search_text = u""
        self._source_label = "All Sheets"
        self._selectall_cb = None

        self._load_model()
        self._build_columns_ui()
        self.sheets_dg.AddHandler(
            ButtonBase.ClickEvent,
            RoutedEventHandler(self.grid_checkbox_click))
        self._refresh_visible_rows()
        self._is_ready = True
        self._warn_multi_titleblocks()

    # ------------------------------------------------------------- model

    def _load_model(self):
        doc = revit.doc
        self._revision_rows = print_sets.collect_revision_rows(
            doc, DB, framework)
        rows, tb_map, sheets_by_id = smrevit.build_rows(doc, SheetRow)
        self._tb_map = tb_map
        self._sheets_by_id = sheets_by_id
        self._columns = state.fixed_columns() + \
            state.build_revision_columns(self._revision_rows)
        for row in rows:
            values = {}
            for column in self._columns:
                if column.kind == state.KIND_REVISION:
                    values[column.key] = \
                        column.revision_id in row.all_revision_ids
            state.populate_row(row, self._columns, values)
        self._all_rows = rows

    # ---------------------------------------------------------- columns

    def _build_columns_ui(self):
        self.sheets_dg.Columns.Clear()
        self._spec_by_column = {}
        for spec in self._columns:
            column = self._make_grid_column(spec)
            if column is None:
                continue
            self._spec_by_column[column] = spec
            self.sheets_dg.Columns.Add(column)

    def _make_grid_column(self, spec):
        if spec.kind == state.KIND_SELECT:
            column = DataGridTemplateColumn()
            column.CellTemplate = _parse_fragment(
                _CHECKBOX_CELL_TEMPLATE, spec.attr)
            column.Width = DataGridLength(float(spec.width))
            column.CanUserResize = False
            header_cb = CheckBox()
            header_cb.HorizontalAlignment = HorizontalAlignment.Center
            header_cb.ToolTip = "Check/uncheck all visible rows"
            header_cb.Click += self.selectall_header_clicked
            column.Header = header_cb
            self._selectall_cb = header_cb
            return column
        if spec.kind == state.KIND_REVISION:
            column = DataGridTemplateColumn()
            column.CellTemplate = _parse_fragment(
                _CHECKBOX_CELL_TEMPLATE, spec.attr)
            column.CellStyle = _parse_fragment(_CHECK_CELL_STYLE, spec.attr)
            column.Width = DataGridLength(float(spec.width))
            column.Header = self._make_revision_header(spec)
            return column
        column = DataGridTextColumn()
        column.Header = spec.header
        binding = Binding(spec.attr)
        binding.Mode = BindingMode.TwoWay
        column.Binding = binding
        column.CellStyle = _parse_fragment(_TEXT_CELL_STYLE, spec.attr)
        column.IsReadOnly = bool(
            spec.is_read_only
            or spec.kind in (state.KIND_INDEX, state.KIND_SCHEDULE_TEXT))
        column.Width = DataGridLength(float(spec.width))
        return column

    def _make_revision_header(self, spec):
        panel = StackPanel()
        title = TextBlock()
        title.Text = spec.header
        title.FontWeight = FontWeights.SemiBold
        title.HorizontalAlignment = HorizontalAlignment.Center
        panel.Children.Add(title)
        if spec.revision_label:
            subtitle = TextBlock()
            subtitle.Text = spec.revision_label
            subtitle.FontSize = 10.0
            subtitle.MaxWidth = 90.0
            subtitle.TextTrimming = TextTrimming.CharacterEllipsis
            subtitle.HorizontalAlignment = HorizontalAlignment.Center
            subtitle.ToolTip = spec.revision_label
            panel.Children.Add(subtitle)
        header_cb = CheckBox()
        header_cb.HorizontalAlignment = HorizontalAlignment.Center
        header_cb.Margin = Thickness(0, 2, 0, 0)
        header_cb.ToolTip = \
            "Check/uncheck this revision for all visible rows"
        header_cb.Tag = spec
        header_cb.Click += self.revision_header_clicked
        panel.Children.Add(header_cb)
        return panel

    # ----------------------------------------------------------- events

    def grid_checkbox_click(self, sender, args):
        del sender
        source = args.OriginalSource
        if not isinstance(source, CheckBox):
            return
        cell = _find_parent_cell(source)
        if cell is None:
            return
        spec = self._spec_by_column.get(cell.Column)
        if spec is None:
            return
        row = source.DataContext
        if not isinstance(row, SheetRow):
            return
        if spec.kind == state.KIND_REVISION:
            state.apply_revision_toggle(row, spec, source.IsChecked)
            self._update_status()
        elif spec.kind == state.KIND_SELECT:
            self._update_selectall_state()

    def selectall_header_clicked(self, sender, args):
        del args
        checked = bool(sender.IsChecked)
        for row in self._visible_rows:
            row.set_value("is_selected", checked)

    def revision_header_clicked(self, sender, args):
        del args
        spec = sender.Tag
        checked = bool(sender.IsChecked)
        for row in self._visible_rows:
            state.apply_revision_toggle(row, spec, checked)
        self._update_status()

    def grid_selection_changed(self, sender, args):
        del sender, args

    def search_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._search_text = self.search_tb.Text or u""
        self._refresh_visible_rows()

    # ---------------------------------------------------------- refresh

    def _refresh_visible_rows(self):
        visible = state.search_rows(
            self._all_rows, self._columns, self._search_text)
        state.renumber(visible)
        self._visible_rows = visible
        self.sheets_dg.ItemsSource = None
        self.sheets_dg.ItemsSource = visible
        self._update_selectall_state()
        self._update_status()

    def _update_status(self):
        staged = state.count_staged_cells(self._all_rows, self._columns)
        bits = ["{0} of {1} sheet(s) shown".format(
            len(self._visible_rows), len(self._all_rows))]
        if staged:
            bits.append("{0} staged change(s) pending Apply".format(staged))
        self.status_tb.Text = "  |  ".join(bits)
        self.source_tb.Text = "Source: {0}".format(self._source_label)

    def _update_selectall_state(self):
        if self._selectall_cb is None:
            return
        rows = self._visible_rows
        if not rows:
            self._selectall_cb.IsChecked = False
            return
        checked_count = len([row for row in rows if row.is_selected])
        if checked_count == 0:
            self._selectall_cb.IsChecked = False
        elif checked_count == len(rows):
            self._selectall_cb.IsChecked = True
        else:
            self._selectall_cb.IsChecked = None

    def _warn_multi_titleblocks(self):
        rows = state.multi_titleblock_rows(self._all_rows)
        if not rows:
            return
        lines = []
        for row in rows:
            lines.append(u"{0} - {1} ({2} title blocks)".format(
                row.number, row.name, row.tblock_count))
        forms.alert(
            "{0} sheet(s) have more than one title block instance.\n\n"
            "Title block parameter cells for these sheets will show as "
            "'duplicated' and cannot be edited here.".format(len(rows)),
            title="Sheet Manager",
            expanded=u"\n".join(lines))

    # ---------------------------------------------------- button stubs

    def _not_available(self, feature):
        forms.alert(
            "{0} is not available yet in this build.".format(feature),
            title="Sheet Manager")

    def load_all_sheets(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._source_label = "All Sheets"
        self.search_tb.Text = u""
        self._search_text = u""
        self._refresh_visible_rows()

    def load_sheet_list(self, sender, args):
        del sender, args
        self._not_available("Load Sheet List")

    def load_print_set(self, sender, args):
        del sender, args
        self._not_available("Load Print Set")

    def filter_by_revision(self, sender, args):
        del sender, args
        self._not_available("Filter By Revision")

    def filter_by_parameter(self, sender, args):
        del sender, args
        self._not_available("Filter By Parameter")

    def sort_clicked(self, sender, args):
        del sender, args
        self._not_available("Sort")

    def add_titleblock_parameter(self, sender, args):
        del sender, args
        self._not_available("Add Title Block Parameter")

    def add_sheet_parameter(self, sender, args):
        del sender, args
        self._not_available("Add Sheet Parameter")

    def export_to_excel(self, sender, args):
        del sender, args
        self._not_available("Export to Excel")

    def import_from_excel(self, sender, args):
        del sender, args
        self._not_available("Import from Excel")

    def copy_sheet_info(self, sender, args):
        del sender, args
        self._not_available("Copy Sheet Info")

    def search_replace(self, sender, args):
        del sender, args
        self._not_available("Search & Replace")

    def save_print_set(self, sender, args):
        del sender, args
        self._not_available("Save Print Set")

    def select_title_blocks(self, sender, args):
        del sender, args
        self._not_available("Select Title Blocks")

    def apply_changes(self, sender, args):
        del sender, args
        self._not_available("Apply Changes")


def show_sheet_manager():
    window = SheetManagerWindow("SheetManagerWindow.xaml")
    window.ShowDialog()
