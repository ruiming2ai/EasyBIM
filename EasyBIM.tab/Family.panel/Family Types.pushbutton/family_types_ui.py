# -*- coding: utf-8 -*-
"""The Family Types window and its Specify Types import dialog.

Columns are built at runtime because they are the family's parameters, so the
grid is assembled in code the way ``sheet_manager_ui`` assembles its revision
columns: a ``DataGridTextColumn`` per parameter bound to a generated attribute
name, with a cell style parsed from a XAML fragment that keys off the
``<attr>_state`` sibling.  The parsed fragments never carry event attributes -
every handler is wired in Python or named in the static XAML.

Revit is never touched from here; the window stages edits and the launcher
applies them.
"""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

import os

from pyrevit import forms
from pyrevit import script

from System import Double as ClrDouble
from System.Windows import HorizontalAlignment
from System.Windows import RoutedEventHandler
from System.Windows import Thickness
from System.Windows import UIElement
from System.Windows.Controls import Border
from System.Windows.Controls import Canvas
from System.Windows.Controls import CheckBox
from System.Windows.Controls import DataGridCell
from System.Windows.Controls import DataGridLength
from System.Windows.Controls import DataGridTemplateColumn
from System.Windows.Controls import DataGridTextColumn
from System.Windows.Controls import ScrollViewer
from System.Windows.Controls import ScrollChangedEventHandler
from System.Windows.Controls import TextBlock
from System.Windows.Controls.Primitives import ButtonBase
from System.Windows.Data import Binding
from System.Windows.Data import BindingMode
from System.Windows.Input import MouseButtonEventHandler
from System.Windows.Markup import XamlReader
from System.Windows import FontWeights
from System.Windows import TextTrimming
from System.Windows.Media import Brushes
from System.Windows.Media import VisualTreeHelper

from easybim import excel_workbook

import family_types_state as state
import family_types_xlsx as ftxlsx


TITLE = "Family Types"
LOGGER = script.get_logger()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_XAML = os.path.join(SCRIPT_DIR, "FamilyTypesWindow.xaml")
IMPORT_XAML = os.path.join(SCRIPT_DIR, "ImportTypesDialog.xaml")

_XNS = ('xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
        'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"')

# Staged edits read red, locked cells read grey, a duplicate or blank type
# name reads orange - the same three signals the Sheet Manager grid uses.
_CELL_STYLE = (
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
    '<DataTrigger Binding="{{Binding {attr}_state}}" Value="conflict">'
    '<Setter Property="Foreground" Value="#B25000"/>'
    '<Setter Property="Background" Value="#FFE8CC"/>'
    '<Setter Property="FontWeight" Value="SemiBold"/>'
    '<Setter Property="ToolTip" Value="A family type name must be filled in'
    ' and unique."/>'
    '</DataTrigger>'
    '<DataTrigger Binding="{{Binding is_marked_deleted}}" Value="True">'
    '<Setter Property="Foreground" Value="#9B9B9B"/>'
    '<Setter Property="Background" Value="#F3F3F3"/>'
    '<Setter Property="ToolTip" Value="Marked for deletion. Select the row'
    ' and click Delete Type again to keep it."/>'
    '</DataTrigger>'
    '</Style.Triggers>'
    '</Style>')


# A yes/no parameter is a checkbox, not typed text. The fragment carries NO
# event attribute - a loose fragment has no code-behind to bind one to, and
# the repo pins that rule with a test; the click is caught at grid level.
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
    '<DataTrigger Binding="{{Binding is_marked_deleted}}" Value="True">'
    '<Setter TargetName="cell_cb" Property="IsEnabled" Value="False"/>'
    '</DataTrigger>'
    '</DataTemplate.Triggers>'
    '</DataTemplate>')

_CHECK_CELL_STYLE = (
    '<Style {ns} TargetType="{{x:Type DataGridCell}}">'
    '<Style.Triggers>'
    '<DataTrigger Binding="{{Binding {attr}_state}}" Value="dirty">'
    '<Setter Property="Background" Value="#F8D7D7"/>'
    '</DataTrigger>'
    '</Style.Triggers>'
    '</Style>')

#: The two bands drawn above the column headers.
GROUP_TYPE = "Type Parameters"
GROUP_INSTANCE = "Instance Parameters"


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


class TypeRow(forms.Reactive, state.TypeRowBase):
    """Grid row with WPF change notification.

    forms.Reactive implements INotifyPropertyChanged; the state layer sets
    attributes directly and calls notify(), so a staged edit repaints without
    the grid's ItemsSource being reassigned (which would lose the scroll
    position and the selection).
    """

    def __init__(self, type_name, is_new=False, is_default_row=False):
        state.TypeRowBase.__init__(self, type_name, is_new=is_new,
                                   is_default_row=is_default_row)
        self.family_type = None

    def notify(self, attr):
        try:
            self.OnPropertyChanged(attr)
        except Exception:
            pass


def make_row(type_name, is_default_row=False):
    return TypeRow(type_name, is_default_row=is_default_row)


class ImportEntryRow(forms.Reactive):
    """One tickable row of the Specify Types dialog.

    Plain attributes plus an explicit notify(), the idiom the Update Circuit
    Rating grid uses: All / None / New Only write ``is_selected`` from code,
    so the row has to raise PropertyChanged; reassigning ItemsSource instead
    would reset the scroll position on every click.
    """

    def __init__(self, entry):
        self.entry = entry
        self.key = entry.key
        self.type_name = entry.type_name
        self.status = entry.status
        self.change_text = entry.change_text
        self.can_select = entry.status != state.IMPORT_MISSING \
            and bool(entry.edits)
        self.is_selected = bool(entry.is_selected) and self.can_select

    def notify(self, attr):
        try:
            self.OnPropertyChanged(attr)
        except Exception:
            pass

    def set_selected(self, value):
        self.is_selected = bool(value) and self.can_select
        self.notify("is_selected")


class ImportTypesDialog(forms.WPFWindow):
    """Revit's type-catalogue "Specify Types" question, for a workbook."""

    def __init__(self, xaml_file_name, plan, file_path):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.plan = plan
        self.result = None
        self.delete_missing = False
        self._rows = [ImportEntryRow(entry) for entry in plan.entries]
        self.file_tb.Text = file_path
        self.entries_dg.ItemsSource = self._rows

        warnings = []
        if plan.unmatched_columns:
            warnings.append(
                u"{0} column(s) in the file do not match a parameter in this "
                u"family and are ignored: {1}".format(
                    len(plan.unmatched_columns),
                    u", ".join(plan.unmatched_columns[:6])))
        if plan.skipped_locked:
            warnings.append(
                u"{0} cell(s) target read-only parameters (formula-driven, "
                u"reporting, or element references) and are ignored.".format(
                    plan.skipped_locked))
        if warnings:
            self.warn_tb.Text = u"\n".join(warnings)
            self.show_element(self.warn_tb)
        self._refresh_count()

    def _refresh_count(self):
        chosen = len([row for row in self._rows if row.is_selected])
        missing = len(self.plan.missing())
        text = u"{0} of {1} type(s) ticked".format(
            chosen, len([row for row in self._rows if row.can_select]))
        if missing:
            text += u"; {0} in the family are not in the file".format(missing)
        self.count_tb.Text = text

    def _set_all(self, wanted, only_new=False):
        for row in self._rows:
            if not row.can_select:
                continue
            if only_new:
                row.set_selected(row.status == state.IMPORT_NEW)
            else:
                row.set_selected(wanted)
        self._refresh_count()

    def select_all_clicked(self, sender, args):
        del sender, args
        self._set_all(True)

    def select_none_clicked(self, sender, args):
        del sender, args
        self._set_all(False)

    def select_new_clicked(self, sender, args):
        del sender, args
        self._set_all(True, only_new=True)

    def import_clicked(self, sender, args):
        del sender, args
        self.result = [row.key for row in self._rows if row.is_selected]
        self.delete_missing = bool(self.deletemissing_cb.IsChecked)
        self.Close()

    def cancel_clicked(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


class FamilyTypesWindow(forms.WPFWindow):
    """The type table. Stages every change; the launcher writes them."""

    def __init__(self, xaml_file_name, columns, rows, family_name,
                 context_text, notes=None):
        self._is_ready = False
        self.result = None
        forms.WPFWindow.__init__(self, xaml_file_name)

        self._columns = columns
        self._all_rows = list(rows)
        self._visible_rows = list(rows)
        self._family_name = family_name
        self._hidden_empty = False
        self._search_text = u""
        self._selection_snapshot = (None, [])
        self._band_signature = None
        self._band_cheap_signature = None
        self._scroll_offset = 0.0

        # Checkbox clicks are caught at grid level: the cell templates are
        # loose XAML fragments and cannot carry a handler of their own.
        # AddHandler demands the delegate type the event declares.
        self.types_dg.AddHandler(
            ButtonBase.ClickEvent,
            RoutedEventHandler(self.grid_checkbox_click))
        self.types_dg.AddHandler(
            UIElement.PreviewMouseLeftButtonDownEvent,
            MouseButtonEventHandler(self.grid_preview_mouse_down),
            True)
        try:
            self.types_dg.AddHandler(
                ScrollViewer.ScrollChangedEvent,
                ScrollChangedEventHandler(self.grid_scrolled))
            self.types_dg.LayoutUpdated += self.grid_layout_updated
        except Exception as err:
            LOGGER.debug("Group band not wired: %s", err)

        self.family_tb.Text = u"{0}  -  {1}".format(family_name, context_text)
        self._build_columns_ui()
        self._refresh_grid()
        self._refresh_status()
        self._is_ready = True
        for note in notes or []:
            LOGGER.debug(note)
        if notes:
            self.status_tb.Text = notes[0]

    # ---------------------------------------------------------- columns

    def _build_columns_ui(self):
        # The band spans these columns, so it has to be recomputed with them.
        self._band_signature = None
        self._band_cheap_signature = None
        self.types_dg.Columns.Clear()
        self._spec_by_column = {}
        for spec in self._columns:
            if getattr(spec, "is_hidden", False):
                continue
            column = self._make_grid_column(spec)
            self._spec_by_column[column] = spec
            self.types_dg.Columns.Add(column)

    def _make_grid_column(self, spec):
        if getattr(spec, "is_yes_no", False):
            column = DataGridTemplateColumn()
            column.Header = spec.header
            column.CellTemplate = _parse_fragment(
                _CHECKBOX_CELL_TEMPLATE, spec.attr)
            column.CellStyle = _parse_fragment(_CHECK_CELL_STYLE, spec.attr)
            column.Width = DataGridLength(float(min(spec.width, 90)))
            return column
        column = DataGridTextColumn()
        column.Header = spec.header
        binding = Binding(spec.attr)
        binding.Mode = BindingMode.TwoWay
        column.Binding = binding
        column.CellStyle = _parse_fragment(_CELL_STYLE, spec.attr)
        column.IsReadOnly = bool(spec.is_read_only)
        column.Width = DataGridLength(float(spec.width))
        return column

    # ------------------------------------------------- cell multi-select

    def _selected_cell_rows(self, spec):
        """Rows whose cell in ``spec``'s column is part of the selection.

        ``SelectionUnit="CellOrRowHeader"`` means the selection is a set of
        cells, so a bulk edit is "every selected cell in this column" rather
        than "every selected row". Grid order is preserved.
        """
        wanted = []
        try:
            cells = list(self.types_dg.SelectedCells)
        except Exception:
            return wanted
        seen = []
        for cell in cells:
            try:
                if self._spec_by_column.get(cell.Column) is not spec:
                    continue
                item = cell.Item
            except Exception:
                continue
            if item is None or not isinstance(item, TypeRow):
                continue
            if any(item is other for other in seen):
                continue
            seen.append(item)
        for row in self._all_rows:
            if any(row is other for other in seen):
                wanted.append(row)
        return wanted

    def _snapshot_selection(self, spec):
        """Remember the selected cells before WPF can collapse them.

        Load-bearing: clicking into a cell - a checkbox especially - collapses
        a drag/shift selection on mouse-down, BEFORE the edit or the click is
        reported. Sheet Manager hits the same wall and answers it the same
        way, with a tunnelling preview handler.
        """
        self._selection_snapshot = (spec, self._selected_cell_rows(spec))
        return self._selection_snapshot[1]

    def _snapshot_for(self, spec):
        snapshot_spec, rows = self._selection_snapshot
        if snapshot_spec is not spec:
            return []
        return rows

    def grid_preview_mouse_down(self, sender, args):
        del sender
        resolved = self._resolve_grid_checkbox(
            getattr(args, "OriginalSource", None))
        if resolved is None:
            return
        checkbox, spec, row = resolved
        selected = self._selected_cell_rows(spec)
        if len(selected) < 2 or not any(row is other for other in selected):
            # One cell: let WPF handle it and the Click handler stage it.
            return
        # Several cells in this column are highlighted; WPF is about to throw
        # that away. Do the whole toggle here and stop the event.
        self._selection_snapshot = (spec, selected)
        checked = not bool(checkbox.IsChecked)
        state.apply_cell_edit(row, spec, checked)
        state.propagate_cell_edit(selected, spec, checked, skip_row=row)
        self._refresh_status()
        args.Handled = True

    def grid_checkbox_click(self, sender, args):
        del sender
        resolved = self._resolve_grid_checkbox(
            getattr(args, "OriginalSource", None))
        if resolved is None:
            return
        checkbox, spec, row = resolved
        checked = bool(checkbox.IsChecked)
        state.apply_cell_edit(row, spec, checked)
        selected = self._snapshot_for(spec)
        if len(selected) > 1 and any(row is other for other in selected):
            state.propagate_cell_edit(selected, spec, checked, skip_row=row)
        self._refresh_status()

    def _resolve_grid_checkbox(self, source):
        """-> (checkbox, spec, row) for one of our cell checkboxes, or None."""
        checkbox = source
        while checkbox is not None and not isinstance(checkbox, CheckBox):
            if isinstance(checkbox, DataGridCell):
                return None
            try:
                checkbox = VisualTreeHelper.GetParent(checkbox)
            except Exception:
                return None
        if checkbox is None:
            return None
        cell = _find_parent_cell(checkbox)
        if cell is None:
            return None
        spec = self._spec_by_column.get(cell.Column)
        if spec is None:
            return None
        row = checkbox.DataContext
        if not isinstance(row, TypeRow):
            return None
        return checkbox, spec, row

    # ------------------------------------------------------------- grid

    def _refresh_grid(self):
        self._visible_rows = state.search_rows(
            self._all_rows, self._columns, self._search_text)
        self.types_dg.ItemsSource = None
        self.types_dg.ItemsSource = self._visible_rows

    def _refresh_status(self):
        problems = state.find_name_problems(self._all_rows)
        changes = state.compute_staged_changes(self._all_rows, self._columns)
        text = u"{0} type(s), {1} parameter(s) - {2}".format(
            state.surviving_type_count(self._all_rows),
            len(state.param_columns(self._columns)),
            changes.summary_text())
        if problems:
            text = u"{0}  |  {1}".format(problems[0], text)
        self.status_tb.Text = text
        return problems

    def _selected_rows(self):
        """Rows touched by the selection, in grid order.

        The grid selects cells, not rows, so a row counts as selected when any
        of its cells is - which is more forgiving than the old full-row rule,
        and still exactly what clicking a row header gives.
        """
        rows = []
        try:
            cells = list(self.types_dg.SelectedCells)
        except Exception:
            cells = []
        picked = []
        for cell in cells:
            try:
                item = cell.Item
            except Exception:
                continue
            if isinstance(item, TypeRow) \
                    and not any(item is other for other in picked):
                picked.append(item)
        for row in self._all_rows:
            if any(row is other for other in picked):
                rows.append(row)
        return rows

    def _alert(self, message, expanded=None, warn_icon=True):
        forms.alert(message, title=TITLE, expanded=expanded,
                    warn_icon=warn_icon)

    def _commit_pending_edit(self):
        """Push a cell still in edit mode into the row before acting on it."""
        try:
            self.types_dg.CommitEdit()
            self.types_dg.CommitEdit()
        except Exception:
            pass

    # ----------------------------------------------------------- events

    def search_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._search_text = self.search_tb.Text or u""
        self._refresh_grid()

    def grid_beginning_edit(self, sender, args):
        del sender
        row = getattr(args, "Row", None)
        item = getattr(row, "Item", None) if row is not None else None
        spec = self._spec_by_column.get(getattr(args, "Column", None))
        if item is None or spec is None:
            return
        if not state.can_edit_cell(item, spec):
            args.Cancel = True
            return
        # Taken now, because committing the edit collapses the selection.
        self._snapshot_selection(spec)

    def grid_cell_edit_ending(self, sender, args):
        del sender
        row = getattr(args, "Row", None)
        item = getattr(row, "Item", None) if row is not None else None
        spec = self._spec_by_column.get(getattr(args, "Column", None))
        element = getattr(args, "EditingElement", None)
        if item is None or spec is None or element is None:
            return
        text = getattr(element, "Text", None)
        if text is None:
            return
        state.apply_cell_edit(item, spec, text)
        selected = self._snapshot_for(spec)
        if len(selected) > 1 and any(item is other for other in selected):
            changed = state.propagate_cell_edit(
                selected, spec, text, skip_row=item)
            if changed:
                self.status_tb.Text = u"Set {0} cell(s) in {1}.".format(
                    len(changed) + 1, spec.param_name or spec.header)
                self._refresh_status()
                return
        self._refresh_status()

    def _add_row_from(self, name, source=None):
        """Add a staged new type, seeded from an existing one.

        Seeding is not a convenience: ``FamilyManager.NewType`` copies the
        current type's values into the type it creates, so a row shown blank
        here would come out of Revit carrying whatever the current type had.
        Starting from a real type's values keeps the grid and the family
        saying the same thing.
        """
        row = TypeRow(self._unique_name(name), is_new=True)
        values = {}
        if source is not None:
            values = dict(
                (column.key, getattr(source, column.attr, u""))
                for column in state.param_columns(self._columns))
        state.populate_row(row, self._columns, values)
        state.mark_row_new(row, self._columns)
        self._all_rows.append(row)
        return row

    def _first_surviving_row(self):
        for row in self._all_rows:
            if not row.is_marked_deleted and not row.is_new:
                return row
        return None

    def new_type_clicked(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        self._add_row_from(u"New Type", self._first_surviving_row())
        self._refresh_grid()
        self._refresh_status()

    def duplicate_type_clicked(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        selected = self._selected_rows()
        if not selected:
            self._alert("Select a family type to duplicate.")
            return
        for source in selected:
            self._add_row_from(source.type_name, source)
        self._refresh_grid()
        self._refresh_status()

    def delete_type_clicked(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        selected = self._selected_rows()
        if not selected:
            self._alert("Select the family type(s) to delete.")
            return
        refusals = []
        for row in selected:
            if row.is_new:
                # A row that was never written has nothing to delete in
                # Revit; dropping it from the grid is the whole operation.
                self._all_rows.remove(row)
                continue
            ok, message = state.toggle_delete(row, self._all_rows)
            if not ok:
                refusals.append(message)
        self._refresh_grid()
        self._refresh_status()
        if refusals:
            self._alert(refusals[0])

    def fill_down_clicked(self, sender, args):
        """Push the topmost selected cell's value down the rest of the column.

        The same thing typing into one cell of a multi-cell selection does -
        offered as a button because that gesture is not discoverable.
        """
        del sender, args
        self._commit_pending_edit()
        spec, rows = self._pick_fill_column()
        if spec is None:
            self._alert(
                "Select two or more cells in one column first, then Fill "
                "Down copies the top cell's value to the rest.")
            return
        source = rows[0]
        value = getattr(source, spec.attr, u"")
        changed = state.propagate_cell_edit(rows, spec, value,
                                            skip_row=source)
        self._refresh_status()
        if not changed:
            self._alert(
                "Nothing to fill - the other selected cells are read-only "
                "or already hold that value.", warn_icon=False)
            return
        self.status_tb.Text = u"Filled {0} cell(s) in {1}.".format(
            len(changed), spec.param_name or spec.header)

    def _pick_fill_column(self):
        """-> (spec, rows) for a selection inside one column, else (None, [])."""
        try:
            cells = list(self.types_dg.SelectedCells)
        except Exception:
            return None, []
        specs = []
        for cell in cells:
            try:
                spec = self._spec_by_column.get(cell.Column)
            except Exception:
                continue
            if spec is None or spec.kind == state.KIND_TYPE_NAME:
                continue
            if not any(spec is other for other in specs):
                specs.append(spec)
        if len(specs) != 1:
            return None, []
        rows = self._selected_cell_rows(specs[0])
        if len(rows) < 2:
            return None, []
        return specs[0], rows

    # -------------------------------------------------------- group band

    def grid_scrolled(self, sender, args):
        del sender
        # The DataGrid's ScrollViewer is inside its template; the event that
        # reports the scroll also carries the offset, so there is nothing to
        # dig out of the visual tree.
        try:
            self._scroll_offset = float(args.HorizontalOffset or 0.0)
        except Exception:
            pass
        self._sync_group_band()

    def grid_layout_updated(self, sender, args):
        del sender, args
        # This fires on every render pass, so do the cheapest possible check
        # before walking the columns. Its job is only to catch the first
        # layout after a rebuild, when column widths are still 0; scrolling
        # and column drags both come through ScrollChanged instead.
        try:
            cheap = (float(self.types_dg.ActualWidth or 0.0),
                     int(self.types_dg.Columns.Count))
        except Exception:
            return
        if cheap == getattr(self, "_band_cheap_signature", None):
            return
        self._band_cheap_signature = cheap
        self._sync_group_band()

    def _sync_group_band(self):
        """Draw the TYPE / INSTANCE bands over their columns.

        There is no such thing as a merged DataGrid header, so the band is
        drawn on a Canvas above the grid and kept in step with the columns by
        hand.  Every step is guarded and the whole band hides itself on any
        failure: a band that cannot line up must never take the grid with it.
        """
        try:
            self._draw_group_band()
        except Exception as err:
            LOGGER.debug("Group band hidden: %s", err)
            try:
                self.groupband_cv.Children.Clear()
            except Exception:
                pass

    def _band_segments(self):
        """-> [(label, left, width)] in grid coordinates, before scrolling."""
        offset = 0.0
        spans = []
        for column in self.types_dg.Columns:
            spec = self._spec_by_column.get(column)
            width = float(column.ActualWidth or 0.0)
            if spec is None or width <= 0.0:
                offset += width
                continue
            if spec.kind == state.KIND_TYPE_NAME:
                offset += width
                continue
            label = GROUP_INSTANCE if spec.is_instance else GROUP_TYPE
            if spans and spans[-1][0] == label:
                spans[-1][2] += width
            else:
                spans.append([label, offset, width])
            offset += width
        return spans

    def _horizontal_offset(self):
        return float(getattr(self, "_scroll_offset", 0.0) or 0.0)

    def _draw_group_band(self):
        canvas = self.groupband_cv
        spans = self._band_segments()
        # The first column is frozen, so it never scrolls out from under the
        # band; everything after it moves by the scroll offset.
        frozen = 0.0
        for column in self.types_dg.Columns:
            spec = self._spec_by_column.get(column)
            if spec is not None and spec.kind == state.KIND_TYPE_NAME:
                frozen = float(column.ActualWidth or 0.0)
                break
        shift = self._horizontal_offset()
        signature = (tuple(tuple(span) for span in spans), shift, frozen,
                     float(canvas.ActualWidth or 0.0))
        if signature == self._band_signature:
            return
        self._band_signature = signature

        canvas.Children.Clear()
        viewport = float(canvas.ActualWidth or 0.0)
        for label, left, width in spans:
            start = left - shift
            end = start + width
            # Clip to the scrollable region so a band never slides under the
            # frozen type-name column.
            start = max(start, frozen)
            end = min(end, viewport) if viewport > 0 else end
            if end - start < 2.0:
                continue
            border = Border()
            border.Width = ClrDouble(end - start)
            border.Height = ClrDouble(float(canvas.Height or 20.0))
            border.Background = Brushes.WhiteSmoke
            border.BorderBrush = Brushes.Silver
            border.BorderThickness = Thickness(1, 1, 1, 0)
            text = TextBlock()
            text.Text = label
            text.FontSize = 11.0
            text.FontWeight = FontWeights.SemiBold
            text.Foreground = Brushes.DimGray
            text.HorizontalAlignment = HorizontalAlignment.Center
            text.Margin = Thickness(4, 2, 4, 0)
            text.TextTrimming = TextTrimming.CharacterEllipsis
            border.Child = text
            Canvas.SetLeft(border, ClrDouble(start))
            Canvas.SetTop(border, ClrDouble(0.0))
            canvas.Children.Add(border)

    def hide_empty_clicked(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        self._hidden_empty = not self._hidden_empty
        for column in state.param_columns(self._columns):
            column.is_hidden = self._hidden_empty and not any(
                getattr(row, column.attr, u"") for row in self._all_rows)
        self.hideempty_b.Content = "Show All Columns" if self._hidden_empty \
            else "Hide Empty Columns"
        self._build_columns_ui()
        self._refresh_grid()

    def reload_clicked(self, sender, args):
        del sender, args
        changes = state.compute_staged_changes(self._all_rows, self._columns)
        if not changes.is_empty():
            if not forms.alert(
                    "Re-read the family and discard every staged change?",
                    title=TITLE, yes=True, no=True):
                return
        self.result = "reload"
        self.Close()

    def _unique_name(self, base):
        taken = set(state.normalize_name(row.type_name)
                    for row in self._all_rows)
        if state.normalize_name(base) not in taken:
            return base
        index = 2
        while state.normalize_name(u"{0} {1}".format(base, index)) in taken:
            index += 1
        return u"{0} {1}".format(base, index)

    # ------------------------------------------------------------ excel

    def export_to_excel(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        if not ftxlsx.XLSXWRITER_AVAILABLE:
            self._alert("The 'xlsxwriter' module is not available in this "
                        "pyRevit installation.")
            return
        if not self._all_rows:
            self._alert("There are no family types to export.")
            return
        file_path = forms.save_file(
            file_ext="xlsx",
            default_name=ftxlsx.build_export_filename(self._family_name))
        if not file_path:
            return
        import time
        try:
            count = ftxlsx.export_table_to_xlsx(
                self._family_name, self._family_name, self._columns,
                self._all_rows, file_path,
                time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as err:
            self._alert("Export failed.", expanded=str(err))
            return
        forms.alert(
            "Exported {0} family type(s) with staged values to:\n{1}\n\n"
            "Each parameter's header cell carries its name on the first "
            "line and its ElementId on the second - that id is what an "
            "import matches on.".format(count, file_path),
            title="Export to Excel", warn_icon=False)

    def import_from_excel(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        file_path = forms.pick_file(
            files_filter="Excel Workbooks (*.xlsx;*.xlsm)|*.xlsx;*.xlsm"
                         "|All files (*.*)|*.*")
        if not file_path:
            return
        try:
            sheets = excel_workbook.read_workbook_sheets(
                file_path, [ftxlsx.EXPORT_SHEET_NAME,
                            ftxlsx.METADATA_SHEET_NAME])
        except excel_workbook.UnsupportedWorkbook as err:
            self._alert("Could not read the workbook.", expanded=str(err))
            return
        except Exception as err:
            self._alert("Could not read the workbook.", expanded=str(err))
            return

        export_sheet = sheets.get(ftxlsx.EXPORT_SHEET_NAME)
        if export_sheet is None:
            self._alert(
                "This workbook has no '{0}' sheet, so it was not written by "
                "Family Types.".format(ftxlsx.EXPORT_SHEET_NAME))
            return
        metadata_sheet = sheets.get(ftxlsx.METADATA_SHEET_NAME)
        plan = state.plan_import(
            export_sheet.rows,
            metadata_sheet.rows if metadata_sheet is not None else [],
            self._all_rows, self._columns)
        if not plan.entries:
            self._alert("The workbook has no family type rows.")
            return

        dialog = ImportTypesDialog(IMPORT_XAML, plan, file_path)
        dialog.ShowDialog()
        if dialog.result is None:
            return
        result = state.apply_import_selection(
            plan, dialog.result, self._all_rows, self._columns,
            lambda name: TypeRow(name, is_new=True),
            delete_missing=dialog.delete_missing)
        self._refresh_grid()
        self._refresh_status()
        message = "Staged from Excel: {0}.\n\nNothing has been written to " \
                  "the family yet - review the red cells and click Apply " \
                  "Changes.".format(result.text())
        if result.refused_deletes:
            message += "\n\nKept (a family must have at least one type): " \
                       "{0}".format(", ".join(result.refused_deletes))
        forms.alert(message, title="Import from Excel", warn_icon=False)

    # ------------------------------------------------------------ close

    def apply_changes(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        problems = self._refresh_status()
        if problems:
            self._alert(
                "Fix the family type names before applying:",
                expanded="\n".join(problems))
            return
        changes = state.compute_staged_changes(self._all_rows, self._columns)
        if changes.is_empty():
            self._alert("There is nothing to apply.", warn_icon=False)
            return
        if changes.deletes:
            names = ", ".join(row.type_name for row in changes.deletes)
            if not forms.alert(
                    "Delete {0} family type(s)?\n\n{1}\n\nAny instance "
                    "placed with a deleted type is deleted with it."
                    .format(len(changes.deletes), names),
                    title=TITLE, yes=True, no=True):
                return
        self.result = "apply"
        self.staged_changes = changes
        self.Close()

    def cancel_clicked(self, sender, args):
        del sender, args
        changes = state.compute_staged_changes(self._all_rows, self._columns)
        if not changes.is_empty():
            if not forms.alert(
                    "Close without applying {0}?".format(
                        changes.summary_text()),
                    title=TITLE, yes=True, no=True):
                return
        self.result = None
        self.Close()
