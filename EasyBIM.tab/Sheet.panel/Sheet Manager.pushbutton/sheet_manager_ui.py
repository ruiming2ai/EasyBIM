# -*- coding: utf-8 -*-
"""Sheet Manager main window and dynamic DataGrid construction.

Columns are generated at runtime (parameter and revision counts are unknown
at XAML time). Cell templates and per-column cell styles are produced with
XamlReader.Parse; parsed fragments never contain event attributes - all
interactivity is wired at grid level or on programmatically built headers.
"""

from __future__ import print_function

import time

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import FontWeights
from System.Windows import HorizontalAlignment
from System.Windows import RoutedEventHandler
from System.Windows import TextTrimming
from System.Windows import Thickness
from System.Windows import UIElement
from System.Windows.Input import MouseButtonEventHandler
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
from pyrevit import HOST_APP
from pyrevit import framework
from pyrevit import forms
from pyrevit import revit
from pyrevit import script

from easybim import excel_print_sets
from easybim import excel_workbook
from easybim import external_events
from easybim import link_reload
from easybim import print_sets
from easybim.compat import eid_to_int

import sheet_manager_dialogs as dialogs
import sheet_manager_revit as smrevit
import sheet_manager_state as state
import sheet_manager_xlsx as smxlsx


LOGGER = script.get_logger()

# Mirrored to pyRevit envvars: a persistent engine may be re-imported by a
# pyRevit reload while Revit keeps the live window (Clash Detection pattern).
ACTIVE_ENVVAR = "EASYBIM_SHEET_MANAGER_ACTIVE"
WINDOW_ENVVAR = "EASYBIM_SHEET_MANAGER_WINDOW"
_WINDOW = None


class DocumentGone(Exception):
    """The document this window was opened for is no longer valid."""


class WrongActiveDocument(Exception):
    """A write/selection was requested while another document is active."""

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
    '<DataTrigger Binding="{{Binding {attr}_state}}" Value="conflict">'
    '<Setter Property="Foreground" Value="#B25000"/>'
    '<Setter Property="Background" Value="#FFE8CC"/>'
    '<Setter Property="FontWeight" Value="SemiBold"/>'
    '<Setter Property="ToolTip"'
    ' Value="Sheet number conflicts with another sheet - finish the'
    ' swap or pick a unique number before Apply Changes."/>'
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


def _same_doc(doc_a, doc_b):
    if doc_a is None or doc_b is None:
        return False
    try:
        return bool(doc_a.Equals(doc_b))
    except Exception:
        pass
    try:
        return (getattr(doc_a, "Title", None) == getattr(doc_b, "Title", None)
                and getattr(doc_a, "PathName", None)
                == getattr(doc_b, "PathName", None))
    except Exception:
        return False


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
    def __init__(self, xaml_file_name, bridge=None, uiapp=None):
        self._is_ready = False
        try:
            # Escape must not close a modeless editor holding staged edits;
            # the DataGrid then handles Escape as cancel-edit natively.
            forms.WPFWindow.__init__(self, xaml_file_name, handle_esc=False)
        except TypeError:
            forms.WPFWindow.__init__(self, xaml_file_name)
        self._bridge = bridge
        self._uiapp = uiapp
        self._doc = revit.doc  # bound in API context at launch
        self._doc_title = getattr(self._doc, "Title", u"") or u""
        self._busy = False
        self._closing = False
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
        self._source_order = None
        self._all_revision_ids = set()
        self._revision_filter_ids = set()
        self._param_rules = []
        self._sort_levels = []
        self._copy_content_ops = []
        self._sheet_param_info = None
        self._tb_param_info = None
        self._filter_extra_cache = {}
        self._last_sync = time.time()
        self._editing_row = None
        self._revit_buttons = [
            self.loadsheetlist_b, self.loadprintset_b, self.filterparam_b,
            self.addtbparam_b, self.addsheetparam_b, self.saveprintset_b,
            self.selecttblocks_b, self.apply_b, self.refresh_b,
            self.pdfexport_b, self.print_b,
        ]
        if self._bridge is not None:
            self._bridge.on_error = self._on_bridge_error
            self._bridge.on_idle = self._on_bridge_idle
            self.Activated += self._on_activated
        self.Closing += self._on_closing
        self.Closed += self._on_closed

        self._load_model()
        self._build_columns_ui()
        self.sheets_dg.AddHandler(
            ButtonBase.ClickEvent,
            RoutedEventHandler(self.grid_checkbox_click))
        # Clicking a checkbox in a FullRow-selection grid would collapse a
        # drag/shift multi-selection to that one row BEFORE Click fires;
        # intercept the mouse-down on checkboxes inside highlighted rows
        # so the highlight (and therefore the propagation) survives.
        self.sheets_dg.AddHandler(
            UIElement.PreviewMouseLeftButtonDownEvent,
            MouseButtonEventHandler(self.grid_preview_mouse_down),
            True)
        self._refresh_visible_rows()
        self._is_ready = True
        self._warn_multi_titleblocks()

    # ------------------------------------------------------------- model

    # ------------------------------------------------- revit bridge plumbing

    def _run_in_revit(self, label, work, on_done=None, quiet=False):
        """Run ``work(uiapp)`` then ``on_done(result)`` in Revit API context.

        Both run inside the ExternalEvent's Execute (Revit's main thread,
        which is also this window's thread). Without a bridge (modal
        fallback) they run synchronously - we already are in context.
        """
        if self._bridge is None:
            try:
                result = work(self._uiapp)
            except Exception as err:
                self._on_bridge_error(label, err)
                return False
            if on_done is not None:
                on_done(result)
            return True
        if not quiet:
            self._set_busy(True, label)
        if not self._bridge.run(work, on_done=on_done, label=label):
            self._set_busy(False)
            self._alert(
                "Revit could not take the request right now (it may be "
                "inside another command). Finish what Revit is doing and "
                "try again.", title="Sheet Manager")
            return False
        return True

    def _set_busy(self, busy, label=""):
        self._busy = bool(busy)
        for button in self._revit_buttons:
            try:
                button.IsEnabled = not self._busy
            except Exception:
                pass
        if self._busy:
            self.status_tb.Text = "Waiting for Revit - {0}...".format(label)
        else:
            self._update_status()

    def _on_bridge_idle(self):
        if not self._closing:
            self._set_busy(False)

    def _require_doc(self, uiapp, must_be_active):
        """Guard bridged work: bound doc still valid (and active for
        writes/selection). Returns the UIDocument (may be None for reads)."""
        try:
            valid = bool(getattr(self._doc, "IsValidObject", True))
        except Exception:
            valid = False
        if not valid:
            raise DocumentGone()
        uidoc = getattr(uiapp, "ActiveUIDocument", None) if uiapp else None
        if uidoc is None:
            try:
                uidoc = revit.uidoc
            except Exception:
                uidoc = None
        if must_be_active:
            active = getattr(uidoc, "Document", None)
            if active is None or not _same_doc(active, self._doc):
                raise WrongActiveDocument(self._doc_title)
        return uidoc

    def _on_bridge_error(self, label, error):
        if isinstance(error, DocumentGone):
            self._alert(
                "The document this Sheet Manager was opened for has been "
                "closed. Sheet Manager will close.", title="Sheet Manager")
            self._closing = True
            try:
                self.Close()
            except Exception:
                pass
            return
        if isinstance(error, WrongActiveDocument):
            self._alert(
                u"Switch back to '{0}' in Revit, then try again.".format(
                    self._doc_title), title="Sheet Manager")
            return
        LOGGER.debug("%s failed: %s", label, error)
        self._alert("{0} failed.".format(label), expanded=str(error),
                    title="Sheet Manager")

    def _alert(self, *args, **kwargs):
        """forms.alert with this window disabled while the TaskDialog is up
        (a nested message pump could otherwise re-enter a click handler)."""
        try:
            self.IsEnabled = False
        except Exception:
            pass
        try:
            return forms.alert(*args, **kwargs)
        finally:
            try:
                self.IsEnabled = True
            except Exception:
                pass

    def _show_dialog(self, dialog):
        """ShowDialog with this window as owner so it stays on top of us."""
        try:
            dialog.Owner = self
        except Exception:
            pass
        dialog.ShowDialog()
        return dialog

    def _on_closing(self, sender, args):
        """A modeless window is easy to close by accident: confirm when
        staged edits or Revit work would be lost."""
        del sender
        if self._closing:
            return
        self._commit_pending_edit()
        staged = state.count_staged_cells(self._all_rows, self._columns)
        pending_ops = len(self._copy_content_ops)
        busy = self._bridge is not None and self._bridge.is_busy()
        if not (staged or pending_ops or busy):
            return
        if busy:
            message = ("Sheet Manager is still waiting for Revit to finish "
                       "a request.\n\nClose anyway?")
        else:
            message = ("You have {0} staged change(s) that have not been "
                       "applied.\n\nClose Sheet Manager anyway?".format(
                           staged + pending_ops))
        choice = self._alert(
            message, title="Sheet Manager",
            options=["Close and discard", "Keep window open"])
        if choice != "Close and discard":
            args.Cancel = True

    def _on_closed(self, sender, args):
        del sender, args
        self._closing = True
        if self._bridge is not None:
            try:
                self._bridge.dispose()
            except Exception:
                pass
        _forget_window(self)

    # ------------------------------------------------------------- model

    def _load_model(self):
        doc = self._doc
        self._revision_rows = print_sets.collect_revision_rows(
            doc, DB, framework)
        self._all_revision_ids = set(
            revision.get("id") for revision in self._revision_rows)
        rows, tb_map, sheets_by_id = smrevit.build_rows(doc, SheetRow)
        self._tb_map = tb_map
        self._sheets_by_id = sheets_by_id
        try:
            smrevit.attach_hidden_cloud_info(doc, rows, sheets_by_id)
        except Exception as err:
            LOGGER.debug("Cloud inventory unavailable: %s", err)
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

    def _resolve_grid_checkbox(self, source):
        """-> (checkbox, spec, row) for a click on a grid checkbox, or
        None when the source is not one of our cell checkboxes."""
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
        if not isinstance(row, SheetRow):
            return None
        return checkbox, spec, row

    def _apply_checkbox_change(self, spec, row, checked, selected):
        """Stage a checkbox value on ``row`` and, when the row is part of
        a multi-row highlight, on every highlighted row."""
        multi = len(selected) > 1 and row in selected
        if spec.kind == state.KIND_REVISION:
            state.apply_revision_toggle(row, spec, checked)
            if multi:
                state.propagate_revision_toggle(
                    selected, spec, checked, skip_row=row)
            self._update_status()
        elif spec.kind == state.KIND_SELECT:
            row.set_value("is_selected", checked)
            if multi:
                # One click marks every highlighted (drag/shift-selected)
                # row, matching how the revision checkboxes propagate.
                for other in selected:
                    if other is not row:
                        other.set_value("is_selected", checked)
            self._update_selectall_state()

    def grid_preview_mouse_down(self, sender, args):
        """Keep a multi-row highlight alive when a checkbox inside one of
        the highlighted rows is clicked (the DataGrid would otherwise
        collapse the selection to that row before Click fires)."""
        del sender
        resolved = self._resolve_grid_checkbox(args.OriginalSource)
        if resolved is None:
            return
        if self._busy:
            args.Handled = True
            return
        checkbox, spec, row = resolved
        if spec.kind not in (state.KIND_SELECT, state.KIND_REVISION):
            return
        if not checkbox.IsEnabled:
            return
        selected = [item for item in self.sheets_dg.SelectedItems
                    if isinstance(item, SheetRow)]
        if len(selected) < 2 or row not in selected:
            return  # single row: let the normal Click path handle it
        checked = not bool(checkbox.IsChecked)
        self._apply_checkbox_change(spec, row, checked, selected)
        args.Handled = True

    def grid_checkbox_click(self, sender, args):
        del sender
        if self._busy:
            return
        resolved = self._resolve_grid_checkbox(args.OriginalSource)
        if resolved is None:
            return
        checkbox, spec, row = resolved
        selected = [item for item in self.sheets_dg.SelectedItems
                    if isinstance(item, SheetRow)]
        self._apply_checkbox_change(
            spec, row, bool(checkbox.IsChecked), selected)

    def grid_beginning_edit(self, sender, args):
        del sender
        if self._busy:
            args.Cancel = True
            return
        spec = self._spec_by_column.get(args.Column)
        row = args.Row.Item if args.Row is not None else None
        if spec is None or not isinstance(row, SheetRow):
            args.Cancel = True
            return
        if not state.can_edit_cell(row, spec):
            args.Cancel = True
            return
        self._editing_row = row  # a focus-return sync skips this row

    def grid_cell_edit_ending(self, sender, args):
        del sender
        self._editing_row = None
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
                self._alert(error, title="Sheet Manager")
                args.Cancel = True
                return
        state.apply_cell_edit(row, spec, new_text)
        if spec.kind == state.KIND_NUMBER:
            self._refresh_number_conflicts()
        if spec.kind not in (state.KIND_NUMBER, state.KIND_NAME):
            selected = [item for item in self.sheets_dg.SelectedItems
                        if isinstance(item, SheetRow)]
            if len(selected) > 1 and row in selected:
                state.propagate_edit(selected, spec, new_text, skip_row=row)
        self._update_status()

    def _validate_number_edit(self, row, new_text):
        """Intrinsic validity only. Collisions with other rows are ALLOWED
        while staging (that is how swaps start) - they render as orange
        conflicts and block Apply Changes instead."""
        del row
        number = u"{0}".format(new_text or u"").strip()
        if not number:
            return "Sheet Number cannot be empty."
        bad_chars = state.invalid_number_chars(number)
        if bad_chars:
            return (u"Sheet Number cannot include prohibited "
                    u"characters: {0}".format(u" ".join(bad_chars)))
        return None

    def _number_column(self):
        for column in self._columns:
            if column.kind == state.KIND_NUMBER:
                return column
        return None

    def _refresh_number_conflicts(self):
        column = self._number_column()
        if column is None:
            return 0
        return state.refresh_number_conflicts(self._all_rows, column)

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

    def _refresh_visible_rows(self, preserve_selection=False):
        selected_ids = None
        if preserve_selection:
            selected_ids = set(
                id(item) for item in self.sheets_dg.SelectedItems
                if isinstance(item, SheetRow))
        rows = self._all_rows
        if self._source_order is not None:
            by_id = {}
            for row in rows:
                by_id[row.sheet_id] = row
            scoped = [by_id[sheet_id] for sheet_id in self._source_order
                      if sheet_id in by_id]
            scoped += [row for row in rows if row.is_pending]
            rows = scoped
        rows = state.search_rows(rows, self._columns, self._search_text)
        rows = state.filter_rows_by_revisions(
            rows, self._columns, self._revision_filter_ids,
            self._all_revision_ids)
        rows = state.filter_rows_by_rules(
            rows, state.columns_by_key(self._columns), self._param_rules,
            extra_lookup=self._extra_filter_lookup)
        if self._sort_levels:
            rows = state.sort_rows(
                rows, state.columns_by_key(self._columns),
                self._sort_levels)
        state.renumber(rows)
        self._visible_rows = rows
        self.sheets_dg.ItemsSource = None
        self.sheets_dg.ItemsSource = rows
        if selected_ids:
            try:
                for row in rows:
                    if id(row) in selected_ids:
                        self.sheets_dg.SelectedItems.Add(row)
            except Exception:
                pass
        self._update_selectall_state()
        self._update_filter_labels()
        self._update_status()

    # ------------------------------------------------------- freshness

    SYNC_MIN_INTERVAL_S = 2.0

    def _on_activated(self, sender, args):
        """Light sync when the user clicks back into the window: sheet
        numbers/names/additional revisions/title block counts, new and
        deleted sheets. Throttled; skipped while Revit work is pending."""
        del sender, args
        if not getattr(self, "_is_ready", False) or self._closing:
            return
        if self._bridge is None or self._bridge.is_busy():
            return
        if time.time() - self._last_sync < self.SYNC_MIN_INTERVAL_S:
            return
        self._last_sync = time.time()
        self._run_in_revit("Sync with model", self._sync_work,
                           self._sync_done, quiet=True)

    def _sync_work(self, uiapp):
        started = time.time()
        self._require_doc(uiapp, must_be_active=False)
        snapshot = smrevit.read_light_snapshot(
            self._doc, set(self._sheets_by_id))
        new_rows = []
        for sheet in snapshot["new_sheets"]:
            new_rows.append(smrevit.build_row_for_sheet(
                sheet, SheetRow, snapshot["tb_map"]))
        if new_rows:
            new_by_id = {}
            for row in new_rows:
                new_by_id[row.sheet_id] = \
                    snapshot["sheets"][row.sheet_id]["sheet"]
            try:
                smrevit.attach_hidden_cloud_info(
                    self._doc, new_rows, new_by_id)
            except Exception:
                pass
            for row in new_rows:
                values = {}
                for column in self._columns:
                    if column.kind == state.KIND_REVISION:
                        values[column.key] = \
                            column.revision_id in row.all_revision_ids
                state.populate_row(row, self._columns, values)
            param_values = smrevit.read_param_values(
                new_rows, self._columns, new_by_id, snapshot["tb_map"])
            for row in new_rows:
                for column in self._columns:
                    key = (row.sheet_id, column.key)
                    if key in param_values:
                        state.populate_new_column(
                            row, column, param_values[key])
        snapshot["new_rows"] = new_rows
        snapshot["elapsed"] = time.time() - started
        return snapshot

    def _sync_done(self, snapshot):
        LOGGER.debug("Sheet Manager sync: %.0f ms",
                     snapshot.get("elapsed", 0) * 1000.0)
        self._tb_map = snapshot["tb_map"]
        membership_changed = False
        for row in self._all_rows:
            if row.is_pending or row is self._editing_row:
                continue
            info = snapshot["sheets"].get(row.sheet_id)
            if info is None:
                if not row.is_missing:
                    state.mark_row_missing(row, self._columns)
                    membership_changed = True
                self._sheets_by_id.pop(row.sheet_id, None)
                continue
            if row.is_missing:
                state.unmark_row_missing(row, self._columns)
                membership_changed = True
            self._sheets_by_id[row.sheet_id] = info["sheet"]
            row.tblock_count = info["tblock_count"]
            row.all_revision_ids = row.revisions_cloud | info["additional_ids"]
            values = {"number": info["number"], "name": info["name"]}
            for column in self._columns:
                if column.kind == state.KIND_REVISION:
                    values[column.key] = \
                        column.revision_id in row.all_revision_ids
            state.merge_row_values(row, self._columns, values)
        for row in snapshot["new_rows"]:
            self._all_rows.append(row)
            self._sheets_by_id[row.sheet_id] = \
                snapshot["sheets"][row.sheet_id]["sheet"]
            membership_changed = True
        self._refresh_number_conflicts()
        if membership_changed:
            self._refresh_visible_rows(preserve_selection=True)
        else:
            self._update_status()
        revision_count = snapshot.get("revision_count")
        if revision_count is not None \
                and revision_count != len(self._revision_rows):
            self.status_tb.Text += \
                "  |  Revisions changed in the model - click Refresh"

    def refresh_clicked(self, sender, args):
        """Full reload merged into the grid: revisions (incl. new columns),
        clouds, title blocks, every parameter column. Staged edits kept."""
        del sender, args
        self._commit_pending_edit()
        self._run_in_revit("Refresh", self._refresh_work, self._refresh_done)

    def _refresh_work(self, uiapp):
        self._require_doc(uiapp, must_be_active=False)
        doc = self._doc
        revision_rows = print_sets.collect_revision_rows(doc, DB, framework)
        rows, tb_map, sheets_by_id = smrevit.build_rows(doc, SheetRow)
        try:
            smrevit.attach_hidden_cloud_info(doc, rows, sheets_by_id)
        except Exception as err:
            LOGGER.debug("Cloud inventory unavailable: %s", err)
        param_columns = [column for column in self._columns
                         if column.kind in (state.KIND_SHEET_PARAM,
                                            state.KIND_TB_PARAM)]
        param_values = smrevit.read_param_values(
            rows, param_columns, sheets_by_id, tb_map)
        return {
            "revision_rows": revision_rows,
            "rows": rows,
            "tb_map": tb_map,
            "sheets_by_id": sheets_by_id,
            "param_values": param_values,
        }

    def _refresh_done(self, payload):
        fresh_rows = payload["rows"]
        self._tb_map = payload["tb_map"]
        self._sheets_by_id = payload["sheets_by_id"]
        # Revision columns follow the model (new/deleted revisions).
        self._revision_rows = payload["revision_rows"]
        self._all_revision_ids = set(
            revision.get("id") for revision in self._revision_rows)
        non_revision = [column for column in self._columns
                        if column.kind != state.KIND_REVISION]
        self._columns = non_revision + \
            state.build_revision_columns(self._revision_rows)
        existing = {}
        for row in self._all_rows:
            if not row.is_pending:
                existing[row.sheet_id] = row
        merged = []
        param_values = payload["param_values"]
        for fresh in fresh_rows:
            row = existing.pop(fresh.sheet_id, None)
            values = {"number": fresh.number, "name": fresh.name}
            for column in self._columns:
                if column.kind == state.KIND_REVISION:
                    values[column.key] = \
                        column.revision_id in fresh.all_revision_ids
                elif column.kind in (state.KIND_SHEET_PARAM,
                                     state.KIND_TB_PARAM):
                    key = (fresh.sheet_id, column.key)
                    if key in param_values:
                        values[column.key] = param_values[key]
            if row is None:
                row = fresh
                state.populate_row(row, self._columns, values)
                for column in self._columns:
                    if column.kind in (state.KIND_SHEET_PARAM,
                                       state.KIND_TB_PARAM,
                                       state.KIND_SCHEDULE_TEXT) \
                            and not hasattr(row, column.attr):
                        state.populate_new_column(
                            row, column, values.get(column.key, u""))
            else:
                if row.is_missing:
                    state.unmark_row_missing(row, self._columns)
                row.tblock_count = fresh.tblock_count
                row.is_placeholder = fresh.is_placeholder
                row.all_revision_ids = fresh.all_revision_ids
                row.revisions_cloud = fresh.revisions_cloud
                row.hidden_cloud_revs = fresh.hidden_cloud_revs
                for column in self._columns:
                    if not hasattr(row, column.attr):
                        # a revision column added by the model
                        state.populate_new_column(
                            row, column, values.get(column.key, False))
                state.merge_row_values(row, self._columns, values)
            merged.append(row)
        # existing rows not in the model any more are purged (Refresh is
        # the explicit "make the grid match the model" action)
        merged += [row for row in self._all_rows if row.is_pending]
        self._all_rows = merged
        self._filter_extra_cache = {}
        self._prefetch_filter_values()
        self._build_columns_ui()
        self._refresh_number_conflicts()
        self._refresh_visible_rows(preserve_selection=True)
        self._last_sync = time.time()

    def _extra_filter_lookup(self, row, column_key):
        """Values for filter rules on params that are not table columns.

        Pure dict lookup: the cache is filled by ``_prefetch_filter_values``
        inside a bridged (API-context) call, so filtering while typing never
        touches Revit."""
        return self._filter_extra_cache.get((row.sheet_id, column_key))

    def _prefetch_filter_values(self, rules=None, rows=None):
        """Read (in API context) every rule parameter that is not a table
        column, for the given rows (default all)."""
        rules = self._param_rules if rules is None else rules
        column_keys = set(column.key for column in self._columns)
        wanted = []
        for column_key, _, _ in rules:
            if column_key in column_keys or column_key in wanted:
                continue
            if column_key.startswith("p:") or column_key.startswith("tb:"):
                wanted.append(column_key)
        if not wanted:
            return
        for row in (rows if rows is not None else self._all_rows):
            if row.is_pending:
                continue
            for column_key in wanted:
                param_name = column_key.split(":", 1)[1]
                if column_key.startswith("p:"):
                    element = self._sheets_by_id.get(row.sheet_id)
                else:
                    tblocks = self._tb_map.get(row.sheet_id) or []
                    element = tblocks[0] if len(tblocks) == 1 else None
                self._filter_extra_cache[(row.sheet_id, column_key)] = \
                    smrevit.read_parameter_value(element, param_name)

    def _update_filter_labels(self):
        if self._revision_filter_ids:
            self.filterrev_b.Content = "Filter By Revision ({0})".format(
                len(self._revision_filter_ids))
        else:
            self.filterrev_b.Content = "Filter By Revision"
        if self._param_rules:
            self.filterparam_b.Content = "Filter By Parameter ({0})".format(
                len(self._param_rules))
        else:
            self.filterparam_b.Content = "Filter By Parameter"
        if self._sort_levels:
            self.sort_b.Content = "Sort ({0})".format(len(self._sort_levels))
        else:
            self.sort_b.Content = "Sort"

    def _update_status(self):
        staged = state.count_staged_cells(self._all_rows, self._columns)
        bits = ["{0} of {1} sheet(s) shown".format(
            len(self._visible_rows), len(self._all_rows))]
        if staged:
            bits.append("{0} staged change(s) pending Apply".format(staged))
        if self._copy_content_ops:
            bits.append("{0} copy operation(s) pending Apply".format(
                len(self._copy_content_ops)))
        conflicts = len(state.conflicted_number_rows(self._all_rows))
        if conflicts:
            bits.append("{0} sheet-number conflict(s) - resolve before "
                        "Apply".format(conflicts))
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
        self._alert(
            "{0} sheet(s) have more than one title block instance.\n\n"
            "Title block parameter cells for these sheets will show as "
            "'duplicated' and cannot be edited here.".format(len(rows)),
            title="Sheet Manager",
            expanded=u"\n".join(lines))

    # ---------------------------------------------------- button stubs

    def _not_available(self, feature):
        self._alert(
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
        self._commit_pending_edit()
        self._run_in_revit("Load Sheet List", self._load_sheet_list_work)

    def _load_sheet_list_work(self, uiapp):
        """Whole flow in API context: schedules -> dialog -> ordering
        (ViewSchedule.Export) -> optional columns -> grid refresh."""
        self._require_doc(uiapp, must_be_active=False)
        doc = self._doc
        schedules = smrevit.collect_sheet_list_schedules(doc)
        if not schedules:
            self._alert("No Sheet List schedules were found in the model.",
                        title="Sheet Manager")
            return
        names = [getattr(s, "Name", u"") or u"" for s in schedules]
        dialog = self._show_dialog(dialogs.LoadFromSourceWindow(
            "LoadFromSourceDialog.xaml", "schedule", names))
        if not dialog.result:
            return
        name, numbers_only = dialog.result
        schedule = schedules[names.index(name)]
        try:
            ordered = smrevit.get_ordered_sheets_for_schedule(doc, schedule)
        except Exception as err:
            self._alert("Could not read the sheet list order.",
                        expanded=str(err), title="Sheet Manager")
            return
        self._source_order = [eid_to_int(sheet.Id) for sheet in ordered]
        self._source_label = u"Sheet List: {0}".format(name)
        if not numbers_only:
            try:
                self._load_schedule_columns(doc, schedule, ordered)
            except Exception as err:
                LOGGER.debug("Sheet list columns unavailable: %s", err)
                self._alert(
                    "Loaded sheet order, but the sheet list's columns "
                    "could not be read; showing numbers & names only.",
                    expanded=str(err), title="Sheet Manager")
        self._prefetch_filter_values()
        self._refresh_visible_rows()

    def _load_schedule_columns(self, doc, schedule, ordered_sheets):
        fields = smrevit.map_schedule_fields(schedule)
        if not fields:
            return
        self._ensure_param_info()
        id_to_name = {}
        for param_name, info in self._sheet_param_info.items():
            if info.get("id_value") is not None:
                id_to_name[info["id_value"]] = param_name
        excluded_ids = smrevit.excluded_sheet_param_ids()
        existing_keys = set(column.key for column in self._columns)
        param_counter = state.next_attr_index(self._columns, "p")
        text_counter = state.next_attr_index(self._columns, "s")
        new_param_specs = []
        text_specs = []
        for pos, field in enumerate(fields):
            param_id_value = field.get("param_id_value")
            if param_id_value in excluded_ids:
                continue
            if field.get("is_param") and param_id_value in id_to_name:
                param_name = id_to_name[param_id_value]
                key = u"p:{0}".format(param_name)
                if key in existing_keys:
                    continue
                info = self._sheet_param_info.get(param_name, {})
                spec = state.ColumnSpec(
                    key, state.KIND_SHEET_PARAM, param_name,
                    "p{0}".format(param_counter),
                    param_name=param_name, param_id_value=param_id_value,
                    storage_type=info.get("storage", u""),
                    is_read_only=bool(info.get("read_only")),
                    source="schedule", width=130)
                param_counter += 1
                existing_keys.add(key)
                new_param_specs.append(spec)
            else:
                heading = field.get("heading") or \
                    u"Column {0}".format(pos + 1)
                key = u"sched:{0}".format(heading)
                if key in existing_keys:
                    continue
                spec = state.ColumnSpec(
                    key, state.KIND_SCHEDULE_TEXT, heading,
                    "s{0}".format(text_counter),
                    is_read_only=True, source="schedule", width=120)
                text_counter += 1
                existing_keys.add(key)
                text_specs.append((pos, spec))
        for spec in new_param_specs:
            for row in self._all_rows:
                element = smrevit.param_target_for_row(
                    row, spec, self._sheets_by_id, self._tb_map)
                state.populate_new_column(
                    row, spec,
                    smrevit.read_parameter_value(element, spec.param_name))
        if text_specs:
            cells_by_sheet = smrevit.read_schedule_text_cells(
                doc, schedule, ordered_sheets)
            for pos, spec in text_specs:
                for row in self._all_rows:
                    cells = cells_by_sheet.get(row.sheet_id)
                    value = u""
                    if cells and pos < len(cells):
                        value = cells[pos]
                    state.populate_new_column(row, spec, value)
        new_specs = new_param_specs + [spec for _, spec in text_specs]
        if new_specs:
            self._insert_columns_before_revisions(new_specs)
            self._build_columns_ui()

    def load_print_set(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        self._run_in_revit("Load Print Set", self._load_print_set_work)

    def _load_print_set_work(self, uiapp):
        self._require_doc(uiapp, must_be_active=False)
        names = print_sets.collect_print_set_names(self._doc, DB, framework)
        if not names:
            self._alert("No print sets were found in the model.",
                        title="Sheet Manager")
            return
        dialog = self._show_dialog(dialogs.LoadFromSourceWindow(
            "LoadFromSourceDialog.xaml", "printset", names))
        if not dialog.result:
            return
        name = dialog.result[0]
        sheets = smrevit.get_print_set_sheets(self._doc, name)
        if not sheets:
            self._alert(
                u"Print set '{0}' contains no sheets.".format(name),
                title="Sheet Manager")
            return
        self._source_order = [eid_to_int(sheet.Id) for sheet in sheets]
        self._source_label = u"Print Set: {0}".format(name)
        self._refresh_visible_rows()

    def filter_by_revision(self, sender, args):
        del sender, args
        if not self._revision_rows:
            self._alert("No revisions were found in the model.",
                        title="Sheet Manager")
            return
        dialog = self._show_dialog(dialogs.FilterByRevisionWindow(
            "FilterByRevisionDialog.xaml", self._revision_rows,
            self._revision_filter_ids))
        if dialog.result is None:
            return
        if dialog.result >= self._all_revision_ids:
            self._revision_filter_ids = set()
        else:
            self._revision_filter_ids = set(dialog.result)
        self._refresh_visible_rows()

    def _ensure_param_info(self):
        if self._sheet_param_info is None:
            self._sheet_param_info = smrevit.discover_sheet_parameters(
                list(self._sheets_by_id.values()))
        if self._tb_param_info is None:
            self._tb_param_info = smrevit.discover_titleblock_parameters(
                self._tb_map)

    def _filter_field_options(self):
        options = [("number", "Sheet Number"), ("name", "Sheet Name")]
        existing_keys = set()
        for column in self._columns:
            if column.kind in (state.KIND_SHEET_PARAM,
                               state.KIND_TB_PARAM,
                               state.KIND_SCHEDULE_TEXT):
                options.append((column.key, column.header))
                existing_keys.add(column.key)
        for param_name in sorted(self._sheet_param_info or {}):
            key = u"p:{0}".format(param_name)
            if key not in existing_keys:
                options.append((key, param_name))
        for param_name in sorted(self._tb_param_info or {}):
            key = u"tb:{0}".format(param_name)
            if key not in existing_keys:
                options.append(
                    (key, state.TB_HEADER_PREFIX + param_name))
        return options

    def filter_by_parameter(self, sender, args):
        del sender, args
        self._run_in_revit("Filter By Parameter",
                           self._filter_by_parameter_work)

    def _filter_by_parameter_work(self, uiapp):
        self._require_doc(uiapp, must_be_active=False)
        self._ensure_param_info()
        dialog = self._show_dialog(dialogs.FilterByParameterWindow(
            "FilterByParameterDialog.xaml", self._filter_field_options(),
            self._param_rules, False))
        if dialog.result is None:
            return
        rules, add_params = dialog.result
        self._param_rules = rules
        self._filter_extra_cache = {}
        if add_params:
            self._add_rule_param_columns(rules)
        self._prefetch_filter_values(rules)
        self._refresh_visible_rows()

    def _add_rule_param_columns(self, rules):
        existing_keys = set(column.key for column in self._columns)
        sheet_names = []
        tb_names = []
        for column_key, _, _ in rules:
            if column_key in existing_keys:
                continue
            if column_key.startswith(u"p:"):
                sheet_names.append(column_key.split(u":", 1)[1])
            elif column_key.startswith(u"tb:"):
                tb_names.append(column_key.split(u":", 1)[1])
        if sheet_names:
            self._add_param_columns(state.KIND_SHEET_PARAM, sheet_names)
        if tb_names:
            self._add_param_columns(state.KIND_TB_PARAM, tb_names)

    def sort_clicked(self, sender, args):
        del sender, args
        field_options = [(column.key, column.header)
                         for column in self._columns
                         if column.is_text_value]
        dialog = self._show_dialog(dialogs.SortWindow(
            "SortDialog.xaml", field_options, self._sort_levels))
        if dialog.result is None:
            return
        self._sort_levels = dialog.result
        self._refresh_visible_rows()

    def _add_param_columns(self, kind, names):
        self._ensure_param_info()
        if kind == state.KIND_SHEET_PARAM:
            info_map = self._sheet_param_info
            key_prefix = u"p:"
        else:
            info_map = self._tb_param_info
            key_prefix = u"tb:"
        existing_keys = set(column.key for column in self._columns)
        counter = state.next_attr_index(self._columns, "p")
        new_specs = []
        for param_name in names:
            key = key_prefix + param_name
            if key in existing_keys:
                continue
            info = info_map.get(param_name, {})
            header = param_name if kind == state.KIND_SHEET_PARAM \
                else state.TB_HEADER_PREFIX + param_name
            spec = state.ColumnSpec(
                key, kind, header, "p{0}".format(counter),
                param_name=param_name,
                param_id_value=info.get("id_value"),
                storage_type=info.get("storage", u""),
                is_read_only=bool(info.get("read_only", True)),
                source="user", width=130)
            counter += 1
            existing_keys.add(key)
            for row in self._all_rows:
                element = smrevit.param_target_for_row(
                    row, spec, self._sheets_by_id, self._tb_map)
                state.populate_new_column(
                    row, spec,
                    smrevit.read_parameter_value(element, param_name))
            new_specs.append(spec)
        if new_specs:
            self._insert_columns_before_revisions(new_specs)
            self._build_columns_ui()
            self._refresh_visible_rows()

    def _insert_columns_before_revisions(self, new_specs):
        insert_at = len(self._columns)
        for pos, column in enumerate(self._columns):
            if column.kind == state.KIND_REVISION:
                insert_at = pos
                break
        self._columns = self._columns[:insert_at] + list(new_specs) + \
            self._columns[insert_at:]

    def add_titleblock_parameter(self, sender, args):
        del sender, args
        self._run_in_revit(
            "Add Title Block Parameter",
            lambda uiapp: self._add_parameter_work(
                uiapp, state.KIND_TB_PARAM))

    def add_sheet_parameter(self, sender, args):
        del sender, args
        self._run_in_revit(
            "Add Sheet Parameter",
            lambda uiapp: self._add_parameter_work(
                uiapp, state.KIND_SHEET_PARAM))

    def _add_parameter_work(self, uiapp, kind):
        self._require_doc(uiapp, must_be_active=False)
        self._ensure_param_info()
        if kind == state.KIND_TB_PARAM:
            info_map = self._tb_param_info
            label = "Add Title Block Parameter"
            noun = "title block parameters"
        else:
            info_map = self._sheet_param_info
            label = "Add Sheet Parameter"
            noun = "sheet parameters"
        existing = set(column.param_name for column in self._columns
                       if column.kind == kind)
        names = sorted(param_name for param_name in info_map
                       if param_name not in existing)
        if not names:
            self._alert("No more {0} to add.".format(noun),
                        title="Sheet Manager")
            return
        chosen = forms.SelectFromList.show(
            names, multiselect=True, title=label,
            button_name="Add Columns")
        if not chosen:
            return
        self._add_param_columns(kind, chosen)

    def export_to_excel(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        if not smxlsx.XLSXWRITER_AVAILABLE:
            self._alert(
                "The 'xlsxwriter' module is not available in this "
                "pyRevit installation.", title="Sheet Manager")
            return
        if not self._visible_rows:
            self._alert("There are no rows to export.",
                        title="Sheet Manager")
            return
        doc_title = self._doc_title
        default_name = smxlsx.build_export_filename(doc_title)
        file_path = forms.save_file(file_ext="xlsx",
                                    default_name=default_name)
        if not file_path:
            return
        import time
        try:
            count = smxlsx.export_table_to_xlsx(
                doc_title, self._columns, self._visible_rows, file_path,
                time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as err:
            self._alert("Export failed.", expanded=str(err),
                        title="Sheet Manager")
            return
        self._alert(
            "Exported {0} sheet row(s) (all visible columns, staged "
            "values) to:\n{1}".format(count, file_path),
            title="Export to Excel")

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
                file_path, [smxlsx.EXPORT_SHEET_NAME,
                            smxlsx.METADATA_SHEET_NAME])
        except excel_workbook.UnsupportedWorkbook as err:
            self._alert("Could not read the workbook.",
                        expanded=str(err), title="Import from Excel")
            return
        export_data = sheets.get(smxlsx.EXPORT_SHEET_NAME)
        if export_data is None or not export_data.rows:
            self._alert(
                "Worksheet '{0}' was not found or is empty. Export from "
                "Sheet Manager first, edit, then import that "
                "workbook.".format(smxlsx.EXPORT_SHEET_NAME),
                title="Import from Excel")
            return
        metadata = sheets.get(smxlsx.METADATA_SHEET_NAME)
        plan = smxlsx.plan_import(
            export_data.rows, metadata.rows if metadata else [],
            self._all_rows, self._columns,
            normalize=excel_print_sets.normalize_key)
        if plan.is_empty() and not plan.skipped_rows:
            self._alert("No differences were found - nothing to stage.",
                        title="Import from Excel")
            return
        staged = 0
        invalid_numbers = []
        for row, column, value in plan.cell_edits:
            if column.kind == state.KIND_REVISION:
                if state.apply_revision_toggle(row, column, value):
                    staged += 1
                continue
            if column.kind == state.KIND_NUMBER:
                error = self._validate_number_edit(row, value)
                if error:
                    invalid_numbers.append(
                        u"{0} -> {1}: {2}".format(row.number, value,
                                                  error))
                    continue
            if state.apply_cell_edit(row, column, value):
                staged += 1
        created = 0
        for item in plan.creatable:
            row = SheetRow(None, item["number"], item["name"], False, 0)
            values = {}
            for column in self._columns:
                if column.kind == state.KIND_REVISION:
                    values[column.key] = \
                        column.revision_id in item["revisions"]
            state.populate_row(row, self._columns, values)
            column_map = state.columns_by_key(self._columns)
            for key, text in item["values"].items():
                column = column_map.get(key)
                if column is not None:
                    state.apply_cell_edit(row, column, text)
            state.mark_pending_row_dirty(row, self._columns)
            self._all_rows.append(row)
            created += 1
        conflicts = self._refresh_number_conflicts()
        self._refresh_visible_rows()
        message = [
            "Matched sheets: {0}".format(plan.matched_count),
            "Staged edits (red): {0}".format(staged),
            "New sheets staged for creation: {0}".format(created),
        ]
        if conflicts:
            message.append(
                "Sheet-number conflicts to resolve (orange): "
                "{0}".format(conflicts))
        if plan.skipped_readonly:
            message.append("Skipped read-only cells: {0}".format(
                plan.skipped_readonly))
        if plan.unmatched_columns:
            message.append("Ignored unknown columns: {0}".format(
                len(plan.unmatched_columns)))
        expanded_lines = []
        if invalid_numbers:
            message.append("Skipped invalid sheet numbers: {0}".format(
                len(invalid_numbers)))
            expanded_lines += [u"Invalid number: {0}".format(text)
                               for text in invalid_numbers]
        if plan.skipped_rows:
            message.append(
                "SKIPPED ROWS (red flag): {0} - see details.".format(
                    len(plan.skipped_rows)))
            expanded_lines += [u"{0}: {1}".format(label, reason)
                               for label, reason in plan.skipped_rows]
        message.append(
            "\nNothing is written until you click Apply Changes.")
        self._alert(u"\n".join(message),
                    expanded=u"\n".join(expanded_lines) or None,
                    title="Import from Excel")

    def copy_sheet_info(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        targets = [item for item in self.sheets_dg.SelectedItems
                   if isinstance(item, SheetRow)]
        if not targets:
            self._alert(
                "Select the target sheets in the table first "
                "(shift-click or drag).", title="Copy Sheet Info")
            return
        source_options = [
            (row.sheet_id, u"{0} - {1}".format(row.number, row.name))
            for row in self._all_rows if not row.is_pending]
        dialog = self._show_dialog(dialogs.CopySheetInfoWindow(
            "CopySheetInfoDialog.xaml", source_options, len(targets)))
        if dialog.result is None:
            return
        source_id, dup_sheet, dup_tb, dup_detailing, dup_views = \
            dialog.result
        source_row = None
        for row in self._all_rows:
            if row.sheet_id == source_id:
                source_row = row
                break
        if source_row is None:
            return
        targets = [row for row in targets if row is not source_row]
        if not targets:
            self._alert("Pick target sheets different from the source.",
                        title="Copy Sheet Info")
            return
        staged = 0
        param_columns_missing = []
        if dup_sheet:
            columns = [column for column in self._columns
                       if column.kind == state.KIND_SHEET_PARAM
                       and not column.is_read_only]
            if not columns:
                param_columns_missing.append("sheet parameter")
            for column in columns:
                value = getattr(source_row, column.attr, u"")
                staged += len(state.propagate_edit(targets, column, value))
        if dup_tb:
            columns = [column for column in self._columns
                       if column.kind == state.KIND_TB_PARAM
                       and not column.is_read_only]
            if not columns:
                param_columns_missing.append("title block parameter")
            for column in columns:
                value = getattr(source_row, column.attr, u"")
                staged += len(state.propagate_edit(targets, column, value))
        if dup_detailing or dup_views:
            request = state.CopySheetRequest(
                source_id, source_row.number, dup_sheet, dup_tb,
                dup_detailing, dup_views,
                [row.sheet_id for row in targets if not row.is_pending])
            self._copy_content_ops.append(request)
        message = ["Copy Sheet Info from {0}:".format(source_row.number)]
        if dup_sheet or dup_tb:
            message.append(
                "{0} parameter value(s) staged (shown red).".format(staged))
        if param_columns_missing:
            message.append(
                "Note: no editable {0} columns are in the table - add "
                "columns first to copy those values.".format(
                    " / ".join(param_columns_missing)))
        if dup_detailing or dup_views:
            message.append(
                "Detailing/views will be copied when you Apply Changes.")
        self._alert("\n".join(message), title="Copy Sheet Info")
        self._update_status()

    def search_replace(self, sender, args):
        del sender, args
        self._commit_pending_edit()

        def plan_provider(find_text, replace_text, match_case):
            return state.plan_search_replace(
                self._visible_rows, self._columns, find_text,
                replace_text, match_case)

        dialog = self._show_dialog(dialogs.SearchReplaceWindow(
            "SearchReplaceDialog.xaml", plan_provider))
        if not dialog.result:
            return
        applied = 0
        skipped_invalid = []
        for row, column, old_text, new_text in dialog.result:
            if column.kind == state.KIND_NUMBER:
                error = self._validate_number_edit(row, new_text)
                if error:
                    skipped_invalid.append(
                        u"{0} -> {1}: {2}".format(
                            old_text, new_text, error))
                    continue
            if state.apply_cell_edit(row, column, new_text):
                applied += 1
        conflicts = self._refresh_number_conflicts()
        self._update_status()
        if conflicts or skipped_invalid:
            message = ["{0} replacement(s) staged.".format(applied)]
            if conflicts:
                message.append(
                    "{0} sheet-number conflict(s) are highlighted "
                    "orange - resolve them (finish the swap or pick "
                    "unique numbers) before Apply Changes.".format(
                        conflicts))
            if skipped_invalid:
                message.append(
                    "Skipped {0} invalid sheet-number "
                    "replacement(s).".format(len(skipped_invalid)))
            self._alert(u"\n".join(message),
                        expanded=u"\n".join(skipped_invalid) or None,
                        title="Search & Replace")

    def save_print_set(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        if not print_sets.supports_ordered_print_sets(HOST_APP):
            self._alert("Save Print Set requires Revit 2023 or newer.",
                        title="Sheet Manager")
            return
        checked = [row for row in self._visible_rows
                   if row.is_selected and not row.is_pending]
        if not checked:
            self._alert(
                "Check at least one sheet row first (checkbox column).",
                title="Save Print Set")
            return
        self._run_in_revit(
            "Save Print Set",
            lambda uiapp: self._save_print_set_work(uiapp, checked))

    def _save_print_set_work(self, uiapp, checked):
        self._require_doc(uiapp, must_be_active=True)
        names = print_sets.collect_print_set_names(self._doc, DB, framework)
        dialog = self._show_dialog(dialogs.SavePrintSetWindow(
            "SavePrintSetDialog.xaml", names, len(checked)))
        if not dialog.result:
            return
        sheets = [self._sheets_by_id[row.sheet_id] for row in checked
                  if row.sheet_id in self._sheets_by_id]
        rows = print_sets.build_sheet_rows(sheets)
        printable, skipped = print_sets.split_printable_rows(rows)
        if not printable:
            self._alert(
                "None of the checked sheets are printable "
                "(placeholder sheets cannot join a print set).",
                title="Save Print Set")
            return
        try:
            print_sets.save_ordered_print_set(
                self._doc, dialog.result, printable, DB, framework,
                revit, HOST_APP)
        except print_sets.UnsupportedRevitVersion as version_err:
            self._alert(str(version_err), title="Save Print Set")
            return
        except Exception as err:
            LOGGER.debug("Failed to save print set: %s", err)
            self._alert("Failed to create or update the print set.",
                        expanded=str(err), title="Save Print Set")
            return
        message = ["Print set saved: {0}".format(dialog.result),
                   "Sheets included: {0}".format(len(printable))]
        if skipped:
            message.append(
                "Skipped non-printable rows: {0}".format(skipped))
        self._alert("\n".join(message), title="Save Print Set")

    def _target_rows_for_selection(self):
        """Checked rows first (the persistent marking), else the rows
        currently highlighted in the grid."""
        checked = [row for row in self._visible_rows if row.is_selected]
        if checked:
            return checked
        return [item for item in self.sheets_dg.SelectedItems
                if isinstance(item, SheetRow)]

    def select_title_blocks(self, sender, args):
        """Silently select the title blocks of the checked/highlighted
        sheets in Revit; the window stays open (modeless)."""
        del sender, args
        self._commit_pending_edit()
        rows = self._target_rows_for_selection()
        if not rows:
            self._alert("Check or highlight at least one sheet first.",
                        title="Sheet Manager")
            return
        sheet_ids = [row.sheet_id for row in rows
                     if not row.is_pending and not row.is_missing
                     and row.sheet_id is not None]
        sheet_count = len(sheet_ids)

        def work(uiapp):
            uidoc = self._require_doc(uiapp, must_be_active=True)
            # Fresh collector rather than the cached _tb_map: title blocks
            # may have been added/removed while the window was open.
            tblock_ids = smrevit.collect_titleblock_ids(
                self._doc, sheet_ids)
            if tblock_ids:
                smrevit.select_elements(tblock_ids, uidoc)
            return len(tblock_ids)

        def done(count):
            if count:
                self.status_tb.Text = (
                    "{0} title block(s) on {1} sheet(s) selected in "
                    "Revit.".format(count, sheet_count))
            else:
                self.status_tb.Text = \
                    "The selected sheets have no title blocks."

        self._run_in_revit("Select Title Blocks", work, done)

    def _confirm_cloud_operations(self, changes):
        """Consolidated hide/unhide confirmations. None = cancel apply."""
        decisions = {"hide_approved": False, "unhide_mode": "skip"}
        if changes.cloud_hide_requests:
            lines = []
            for row, column, revision_id in changes.cloud_hide_requests:
                lines.append(u"{0} - {1}".format(row.number, column.header))
            choice = self._alert(
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
            choice = self._alert(
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

    def _post_apply_refresh(self, results, stale=None):
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
        # Stale cells: baseline moves to the model value; the staged value
        # stays displayed (red) or reverts to normal when it now matches.
        for row, column, attr, model_value in (stale or []):
            if model_value is state.MISSING:
                if not row.is_missing:
                    state.mark_row_missing(row, self._columns)
                continue
            column = column or columns_by_attr.get(attr)
            if column is None:
                continue
            state.merge_row_values(row, [column], {column.key: model_value})
        self._copy_content_ops = []
        self._filter_extra_cache = {}
        self._prefetch_filter_values()
        self._refresh_number_conflicts()
        self._refresh_visible_rows(preserve_selection=True)
        self._last_sync = time.time()

    def apply_changes(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        changes = state.compute_staged_changes(self._all_rows, self._columns)
        changes.copy_content_ops = list(self._copy_content_ops)
        if changes.is_empty():
            self._alert("No staged changes to apply.", title="Sheet Manager")
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
            self._refresh_number_conflicts()
            self._alert(
                "Sheet numbers must be unique and non-empty before "
                "applying. Conflicting number cells are highlighted "
                "orange - finish the swap or pick unique numbers for "
                "the listed sheets first.",
                title="Sheet Manager", expanded=u"\n".join(lines))
            return
        decisions = self._confirm_cloud_operations(changes)
        if decisions is None:
            return

        def work(uiapp):
            self._require_doc(uiapp, must_be_active=True)
            # Re-read every staged cell in the SAME Execute as the write:
            # a cell changed in the model since load is skipped (reported,
            # kept red against the new baseline), never overwritten blind.
            current = smrevit.read_staged_cell_values(
                self._doc, changes, self._sheets_by_id, self._tb_map)
            clean, stale = state.partition_stale_changes(changes, current)
            results = smrevit.apply_staged_changes(
                self._doc, clean, self._sheets_by_id, self._tb_map,
                decisions)
            state.record_stale_changes(results, stale)
            return results, stale

        def done(payload):
            results, stale = payload
            self._post_apply_refresh(results, stale)
            self._show_dialog(dialogs.ApplyResultsWindow(
                "ApplyResultsDialog.xaml", results))
            # Still inside Execute: the per-link reload transactions need
            # the API context too.
            link_reload.ask_and_reload_loaded_links(
                self._doc, title="Sheet Manager",
                confirm_func=self._confirm_link_reload)

        self._run_in_revit("Apply Changes", work, done)

    def _confirm_link_reload(self, title):
        choice = self._alert(
            link_reload.RELOAD_PROMPT, title=title,
            options=["Reload links", "Skip"])
        return choice == "Reload links"

    def _reload_and_post_command(self, action_title, command_member_name):
        def work(uiapp):
            should_reload = self._confirm_link_reload(action_title)
            if should_reload:
                result = link_reload.reload_loaded_manage_links(self._doc)
                items = (result or {}).get("items")
                if items:
                    self._show_dialog(dialogs.ReloadLinksResultsWindow(
                        "ReloadLinksResultsDialog.xaml", items))
            try:
                from Autodesk.Revit.UI import PostableCommand
                command = getattr(PostableCommand, command_member_name, None)
            except Exception:
                command = None
            if command is None:
                return
            try:
                from Autodesk.Revit.UI import RevitCommandId
                lookup = getattr(RevitCommandId,
                                 "LookupPostableCommandId", None)
                if not callable(lookup):
                    return
                command_id = lookup(command)
                can_post = getattr(uiapp, "CanPostCommand", None)
                if callable(can_post) and can_post(command_id):
                    uiapp.PostCommand(command_id)
            except Exception:
                pass

        self._run_in_revit(action_title, work)

    def pdf_export(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        self._reload_and_post_command("PDF Export", "ExportPDF")

    def print_sheets(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        self._reload_and_post_command("Print", "Print")


# ------------------------------------------------------------ launcher

def _live_window():
    window = _WINDOW
    if window is None:
        try:
            window = script.get_envvar(WINDOW_ENVVAR)
        except Exception:
            window = None
    try:
        if window is not None and window.IsVisible:
            return window
    except Exception:
        pass
    return None


def activate_open_window():
    """Single instance: bring an already-open Sheet Manager to the front.
    Returns True when one was found (the caller must not open another)."""
    window = _live_window()
    if window is None:
        _forget_window()  # stale envvar after a crash -> allow a new one
        return False
    try:
        from System.Windows import WindowState
        if window.WindowState == WindowState.Minimized:
            window.WindowState = WindowState.Normal
        window.Activate()
    except Exception:
        pass
    return True


def _remember_window(window):
    global _WINDOW
    _WINDOW = window
    try:
        script.set_envvar(WINDOW_ENVVAR, window)
        script.set_envvar(ACTIVE_ENVVAR, True)
    except Exception:
        pass


def _forget_window(window=None):
    global _WINDOW
    if window is not None and _WINDOW is not None and _WINDOW is not window:
        return  # a different (newer) window owns the registry
    _WINDOW = None
    try:
        script.set_envvar(WINDOW_ENVVAR, None)
        script.set_envvar(ACTIVE_ENVVAR, False)
    except Exception:
        pass


def _own_by_revit(window):
    """Keep the modeless window above Revit's main window (repo pattern)."""
    try:
        clr.AddReference("AdWindows")
        import Autodesk.Windows as autodesk_windows
        from System.Windows.Interop import WindowInteropHelper
        WindowInteropHelper(window).Owner = \
            autodesk_windows.ComponentManager.ApplicationWindow
    except Exception:
        pass


def show_sheet_manager(uiapp=None):
    """Open Sheet Manager modeless; falls back to modal when the
    ExternalEvent cannot be created (tool degrades, never dies)."""
    bridge = external_events.ExternalEventBridge("EasyBIM Sheet Manager")
    ready = bridge.create()  # in-context: we are inside the command run
    if not ready:
        LOGGER.debug("ExternalEvent unavailable; running Sheet Manager "
                     "modally.")
        window = SheetManagerWindow("SheetManagerWindow.xaml",
                                    bridge=None, uiapp=uiapp)
        window.ShowDialog()
        return window
    window = SheetManagerWindow("SheetManagerWindow.xaml",
                                bridge=bridge, uiapp=uiapp)
    _remember_window(window)
    _own_by_revit(window)
    window.Show()
    return window
