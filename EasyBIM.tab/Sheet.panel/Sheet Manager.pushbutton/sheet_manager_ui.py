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
from System.Windows.Controls import DataGridEditAction
from System.Windows.Controls import DataGridEditingUnit
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

from easybim import link_reload
from easybim import print_sets

import sheet_manager_dialogs as dialogs
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
        try:
            smrevit.attach_hidden_cloud_info(doc, rows, sheets_by_id)
        except Exception as err:
            LOGGER.warning("Cloud inventory unavailable: %s", err)
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
            checked = bool(source.IsChecked)
            state.apply_revision_toggle(row, spec, checked)
            selected = [item for item in self.sheets_dg.SelectedItems
                        if isinstance(item, SheetRow)]
            if len(selected) > 1 and row in selected:
                state.propagate_revision_toggle(
                    selected, spec, checked, skip_row=row)
            self._update_status()
        elif spec.kind == state.KIND_SELECT:
            self._update_selectall_state()

    def grid_beginning_edit(self, sender, args):
        del sender
        spec = self._spec_by_column.get(args.Column)
        row = args.Row.Item if args.Row is not None else None
        if spec is None or not isinstance(row, SheetRow):
            args.Cancel = True
            return
        if not state.can_edit_cell(row, spec):
            args.Cancel = True

    def grid_cell_edit_ending(self, sender, args):
        del sender
        if not getattr(self, "_is_ready", False):
            return
        try:
            if args.EditAction != DataGridEditAction.Commit:
                return
        except Exception:
            pass
        spec = self._spec_by_column.get(args.Column)
        row = args.Row.Item if args.Row is not None else None
        if spec is None or not isinstance(row, SheetRow):
            return
        new_text = getattr(args.EditingElement, "Text", None)
        if new_text is None:
            return
        if spec.kind == state.KIND_NUMBER:
            error = self._validate_number_edit(row, new_text)
            if error:
                forms.alert(error, title="Sheet Manager")
                args.Cancel = True
                return
        state.apply_cell_edit(row, spec, new_text)
        if spec.kind not in (state.KIND_NUMBER, state.KIND_NAME):
            selected = [item for item in self.sheets_dg.SelectedItems
                        if isinstance(item, SheetRow)]
            if len(selected) > 1 and row in selected:
                state.propagate_edit(selected, spec, new_text, skip_row=row)
        self._update_status()

    def _validate_number_edit(self, row, new_text):
        number = u"{0}".format(new_text or u"").strip()
        if not number:
            return "Sheet Number cannot be empty."
        for other in self._all_rows:
            if other is row:
                continue
            if u"{0}".format(other.number or u"").strip() == number:
                return (u"Sheet Number '{0}' is already used by sheet "
                        u"'{1}'.".format(number, other.name))
        return None

    def _commit_pending_edit(self):
        try:
            self.sheets_dg.CommitEdit(DataGridEditingUnit.Row, True)
        except Exception:
            pass

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
        self._commit_pending_edit()
        rows = [item for item in self.sheets_dg.SelectedItems
                if isinstance(item, SheetRow)]
        if not rows:
            rows = [row for row in self._visible_rows if row.is_selected]
        if not rows:
            forms.alert("Select or check at least one sheet first.",
                        title="Sheet Manager")
            return
        tblock_ids = []
        for row in rows:
            for tblock in self._tb_map.get(row.sheet_id, []):
                tblock_ids.append(tblock.Id)
        if not tblock_ids:
            forms.alert("The selected sheets have no title blocks.",
                        title="Sheet Manager")
            return
        try:
            smrevit.select_elements(tblock_ids)
        except Exception as err:
            forms.alert("Could not set the Revit selection.",
                        expanded=str(err), title="Sheet Manager")
            return
        self.status_tb.Text = \
            "{0} title block(s) selected in Revit (visible after " \
            "closing this window).".format(len(tblock_ids))

    def _confirm_cloud_operations(self, changes):
        """Consolidated hide/unhide confirmations. None = cancel apply."""
        decisions = {"hide_approved": False, "unhide_mode": "skip"}
        if changes.cloud_hide_requests:
            lines = []
            for row, column, revision_id in changes.cloud_hide_requests:
                lines.append(u"{0} - {1}".format(row.number, column.header))
            choice = forms.alert(
                "{0} unchecked revision(s) are on their sheets via VISIBLE "
                "revision clouds and cannot be removed directly.\n\n"
                "Hide those clouds instead? (Hiding removes the revision "
                "from the sheet.)".format(len(lines)),
                title="Sheet Manager - Revision Clouds",
                options=["Hide the clouds",
                         "Keep those revisions (skip)"],
                expanded=u"\n".join(lines))
            if choice is None:
                return None
            decisions["hide_approved"] = choice == "Hide the clouds"
        if changes.cloud_unhide_candidates:
            lines = []
            for row, column, revision_id in changes.cloud_unhide_candidates:
                lines.append(u"{0} - {1}".format(row.number, column.header))
            choice = forms.alert(
                "{0} checked revision(s) have HIDDEN revision clouds on "
                "their sheets.\n\n"
                "Unhide those clouds, or add the revisions to the sheets "
                "manually (clouds stay hidden)?".format(len(lines)),
                title="Sheet Manager - Revision Clouds",
                options=["Unhide the clouds",
                         "Add as additional revisions",
                         "Skip these"],
                expanded=u"\n".join(lines))
            if choice is None:
                return None
            if choice == "Unhide the clouds":
                decisions["unhide_mode"] = "unhide"
            elif choice == "Add as additional revisions":
                decisions["unhide_mode"] = "add"
        return decisions

    def _post_apply_refresh(self, results):
        columns_by_attr = {}
        for column in self._columns:
            columns_by_attr[column.attr] = column
        for row, column, revision_id, direction in \
                getattr(results, "applied_cloud_ops", []):
            if direction == "hide":
                row.revisions_cloud.discard(revision_id)
                row.hidden_cloud_revs.add(revision_id)
            else:
                row.hidden_cloud_revs.discard(revision_id)
                row.revisions_cloud.add(revision_id)
        for row, attr in results.applied_cells:
            row.original[attr] = getattr(row, attr, None)
            column = columns_by_attr.get(attr)
            if column is not None:
                state.refresh_cell_state(row, column)
        self._refresh_visible_rows()

    def apply_changes(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        changes = state.compute_staged_changes(self._all_rows, self._columns)
        if changes.is_empty():
            forms.alert("No staged changes to apply.", title="Sheet Manager")
            return
        empty_rows, duplicate_groups = \
            state.find_number_problems(self._all_rows)
        if empty_rows or duplicate_groups:
            lines = []
            for row in empty_rows:
                lines.append(u"(empty) - {0}".format(row.name))
            for number, group in duplicate_groups:
                names = u", ".join(r.name for r in group)
                lines.append(u"{0} - {1}".format(number, names))
            forms.alert(
                "Sheet numbers must be unique and non-empty before "
                "applying. Fix the listed sheets first.",
                title="Sheet Manager", expanded=u"\n".join(lines))
            return
        decisions = self._confirm_cloud_operations(changes)
        if decisions is None:
            return
        try:
            results = smrevit.apply_staged_changes(
                revit.doc, changes, self._sheets_by_id, self._tb_map,
                decisions)
        except Exception as err:
            LOGGER.critical("Apply Changes failed: %s", err)
            forms.alert("Apply Changes failed.", expanded=str(err),
                        title="Sheet Manager")
            return
        self._post_apply_refresh(results)
        dialogs.show_apply_results(results)
        link_reload.ask_and_reload_loaded_links(
            revit.doc, title="Sheet Manager")


def show_sheet_manager():
    window = SheetManagerWindow("SheetManagerWindow.xaml")
    window.ShowDialog()
