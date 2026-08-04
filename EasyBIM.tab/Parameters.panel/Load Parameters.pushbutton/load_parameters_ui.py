# -*- coding: utf-8 -*-
"""WPF windows for Load Parameters.

Kept free of Revit API imports: everything Revit-facing arrives through the
callables the launcher injects on the context object, which is what lets the
window classes be parsed and checked by the test suite off Revit.

The main window does not close when a load runs.  Every other multi-step tool
in this repo closes and reopens because it needs Revit's own pick UI, which a
modal dialog blocks; nothing here does, so the selection simply survives and
the second load is one click.
"""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

import os

from pyrevit import forms
from pyrevit import script
from pyrevit.framework import Windows

from System.Windows.Controls import CheckBox
from System.Windows.Controls import ComboBox
from System.Windows.Controls import DataGridCell
from System.Windows.Controls import DataGridLength
from System.Windows.Controls import DataGridTemplateColumn
from System.Windows.Controls import SelectionChangedEventHandler
from System.Windows.Controls.Primitives import ButtonBase
from System.Windows.Controls.Primitives import Selector
from System.Windows import HorizontalAlignment
from System.Windows import RoutedEventHandler
from System.Windows import Visibility
from System.Windows.Markup import XamlReader
from System.Windows.Media import VisualTreeHelper

import load_parameters_state as state


LOGGER = script.get_logger()

try:
    from easybim.wpf_notify import NotifyingObject as _RowBase
    _NOTIFY_SUPPORTED = True
except Exception:
    _RowBase = object
    _NOTIFY_SUPPORTED = False


_XNS = ('xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
        'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"')

_CHECK_CELL = (
    '<DataTemplate {ns}>'
    '<CheckBox HorizontalAlignment="Center" VerticalAlignment="Center"'
    ' IsChecked="{{Binding is_checked, Mode=TwoWay,'
    ' UpdateSourceTrigger=PropertyChanged}}"/>'
    '</DataTemplate>')

_TEXT_CELL = (
    '<DataTemplate {ns}>'
    '<TextBlock VerticalAlignment="Center" Margin="4,0,4,0"'
    ' Text="{{Binding {attr}}}" ToolTip="{{Binding {attr}}}"/>'
    '</DataTemplate>')

# The combo is always live rather than an editing template, so a value can be
# changed with one click.  DataGridComboBoxColumn is avoided on purpose: its
# ItemsSource cannot bind through the visual tree, because the column is not
# in it.
_GROUP_CELL = (
    '<DataTemplate {ns}>'
    '<ComboBox Margin="2,1,2,1" DisplayMemberPath="label"'
    ' ItemsSource="{{Binding group_choices}}"'
    ' SelectedItem="{{Binding group_choice, Mode=TwoWay,'
    ' UpdateSourceTrigger=PropertyChanged}}"/>'
    '</DataTemplate>')

_BINDING_CELL = (
    '<DataTemplate {ns}>'
    '<ComboBox Margin="2,1,2,1"'
    ' ItemsSource="{{Binding binding_choices}}"'
    ' SelectedItem="{{Binding binding_choice, Mode=TwoWay,'
    ' UpdateSourceTrigger=PropertyChanged}}"/>'
    '</DataTemplate>')

BINDING_TYPE = u"Type"
BINDING_INSTANCE = u"Instance"
BINDING_CHOICES = (BINDING_TYPE, BINDING_INSTANCE)


def _parse(fragment, **kwargs):
    return XamlReader.Parse(fragment.format(ns=_XNS, **kwargs))


def _notify(row, name):
    if not _NOTIFY_SUPPORTED:
        return
    try:
        row.raise_property_changed(name)
    except Exception:
        pass


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


# ---------------------------------------------------------------------------
# bound rows
# ---------------------------------------------------------------------------


class ParameterGridRow(_RowBase):
    """A ``state.ParameterRow`` dressed for the grid.

    The two editable columns write straight back onto the underlying row, so
    the plan builder never has to know the UI exists.
    """

    def __init__(self, row, group_choices):
        if _NOTIFY_SUPPORTED:
            _RowBase.__init__(self)
        self.row = row
        self.group_choices = group_choices
        self.binding_choices = list(BINDING_CHOICES)

    @property
    def name(self):
        return self.row.name

    @property
    def spec_label(self):
        return self.row.spec_label

    @property
    def source_label(self):
        return self.row.source_label

    def _get_is_checked(self):
        return self.row.is_checked

    def _set_is_checked(self, value):
        self.row.is_checked = bool(value)

    is_checked = property(_get_is_checked, _set_is_checked)

    def _get_group_choice(self):
        for choice in self.group_choices:
            if choice.group_id == self.row.group_id:
                return choice
        return None

    def _set_group_choice(self, value):
        if value is None:
            return
        self.row.group_id = value.group_id
        self.row.group_label = value.label

    group_choice = property(_get_group_choice, _set_group_choice)

    def _get_binding_choice(self):
        return BINDING_INSTANCE if self.row.is_instance else BINDING_TYPE

    def _set_binding_choice(self, value):
        self.row.is_instance = (value == BINDING_INSTANCE)

    binding_choice = property(_get_binding_choice, _set_binding_choice)

    def refresh(self):
        for name in ("is_checked", "group_choice", "binding_choice"):
            _notify(self, name)


class TargetListRow(_RowBase):
    """A family or category row bound into one of the target lists."""

    def __init__(self, row):
        if _NOTIFY_SUPPORTED:
            _RowBase.__init__(self)
        self.row = row

    @property
    def name(self):
        return self.row.name

    @property
    def note(self):
        return getattr(self.row, "note", u"")

    def _get_is_checked(self):
        return self.row.is_checked

    def _set_is_checked(self, value):
        self.row.is_checked = bool(value)

    is_checked = property(_get_is_checked, _set_is_checked)

    def refresh(self):
        _notify(self, "is_checked")


# ---------------------------------------------------------------------------
# main window
# ---------------------------------------------------------------------------


class LoadParametersWindow(forms.WPFWindow):
    """The one window.  Both load actions run from here and it stays open."""

    def __init__(self, xaml_file_name, context):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._is_ready = False
        self._suspend = False
        self.context = context

        self.parameter_rows = list(context.parameter_rows)
        self.family_rows = []
        self.category_rows = list(context.category_rows)
        self.group_choices = list(context.group_choices)

        self._parameter_views = []
        self._family_views = []
        self._category_views = []
        self._spec_by_column = {}

        self._build_columns()
        # AddHandler demands the delegate type the event declares: ClickEvent
        # is a RoutedEventHandler, SelectionChangedEvent is not.
        self.ParametersGrid.AddHandler(
            ButtonBase.ClickEvent, RoutedEventHandler(self.grid_click))
        self.ParametersGrid.AddHandler(
            Selector.SelectionChangedEvent,
            SelectionChangedEventHandler(self.grid_combo_changed))

        self.BulkGroupCombo.ItemsSource = self.group_choices

        self._refresh_parameters()
        self._refresh_families()
        self._refresh_categories()
        self._is_ready = True
        self._update_state()

    # -- construction -----------------------------------------------------

    def _build_columns(self):
        grid = self.ParametersGrid
        grid.Columns.Add(self._template_column(u"", _CHECK_CELL, 40,
                                               resizable=False))
        grid.Columns.Add(self._template_column(u"Parameter", _TEXT_CELL, 220,
                                               attr="name"))
        grid.Columns.Add(self._template_column(u"Data type", _TEXT_CELL, 130,
                                               attr="spec_label"))
        grid.Columns.Add(self._template_column(u"From", _TEXT_CELL, 160,
                                               attr="source_label"))
        grid.Columns.Add(self._template_column(u"Group", _GROUP_CELL, 190))
        grid.Columns.Add(self._template_column(u"Type / Instance",
                                               _BINDING_CELL, 120))

    def _template_column(self, header, fragment, width, attr=None,
                         resizable=True):
        column = DataGridTemplateColumn()
        if attr is None:
            column.CellTemplate = _parse(fragment)
        else:
            column.CellTemplate = _parse(fragment, attr=attr)
        column.Width = DataGridLength(float(width))
        column.CanUserResize = resizable
        if header:
            column.Header = header
        else:
            header_box = CheckBox()
            header_box.HorizontalAlignment = HorizontalAlignment.Center
            header_box.ToolTip = "Check or uncheck every visible parameter"
            header_box.Click += self.header_check_click
            column.Header = header_box
            self._header_check = header_box
        return column

    # -- refresh ----------------------------------------------------------

    def _refresh_parameters(self):
        rows = state.filter_rows(self.parameter_rows,
                                 self.ParameterSearchBox.Text)
        self._parameter_views = [ParameterGridRow(row, self.group_choices)
                                 for row in rows]
        self.ParametersGrid.ItemsSource = self._parameter_views

    def _refresh_families(self):
        rows = state.filter_rows(self.family_rows, self.FamilySearchBox.Text)
        self._family_views = [TargetListRow(row) for row in rows]
        self.FamilyList.ItemsSource = self._family_views

    def _refresh_categories(self):
        rows = state.filter_rows(self.category_rows,
                                 self.CategorySearchBox.Text)
        self._category_views = [TargetListRow(row) for row in rows]
        self.CategoryList.ItemsSource = self._category_views

    def _underlying(self, views):
        return [view.row for view in views]

    def _update_state(self):
        if not self._is_ready:
            return
        params = state.checked_rows(self.parameter_rows)
        families = [row for row in self.family_rows if row.is_checked]
        categories = [row for row in self.category_rows if row.is_checked]

        self.ParameterCountText.Text = u"{0} of {1} checked".format(
            len(params), len(self.parameter_rows))
        self.FamilyCountText.Text = u"{0} of {1} checked".format(
            len(families), len(self.family_rows))
        self.CategoryCountText.Text = u"{0} of {1} checked".format(
            len(categories), len(self.category_rows))

        self.LoadFamiliesButton.IsEnabled = bool(params and families)
        self.LoadProjectButton.IsEnabled = bool(params and categories)

        selected = self._selected_parameter_views()
        if len(selected) > 1:
            self.BulkTargetText.Text = u"Set for {0} selected rows:".format(
                len(selected))
        elif selected:
            self.BulkTargetText.Text = u"Set for the selected row:"
        else:
            self.BulkTargetText.Text = u"Select rows to edit together"

    def _selected_parameter_views(self):
        try:
            return [item for item in self.ParametersGrid.SelectedItems
                    if isinstance(item, ParameterGridRow)]
        except Exception:
            return []

    def _selected_list_rows(self, list_box):
        try:
            return [item for item in list_box.SelectedItems
                    if isinstance(item, TargetListRow)]
        except Exception:
            return []

    # -- shared list behaviour --------------------------------------------

    def _bulk_set(self, views, is_checked):
        """One checkbox click spreads across the whole shift-selected range."""
        rows = [view.row for view in views]
        changed = set(id(row) for row in state.apply_check_to_rows(
            rows, is_checked))
        for view in views:
            if id(view.row) in changed:
                view.refresh()
        self._update_state()

    def _apply_command(self, views, command):
        rows = [view.row for view in views]
        before = [bool(row.is_checked) for row in rows]
        command(rows)
        for index, view in enumerate(views):
            if before[index] != bool(view.row.is_checked):
                view.refresh()
        self._update_state()

    def _check_click(self, list_box, sender):
        view = getattr(sender, "DataContext", None)
        if view is None:
            return
        is_checked = bool(sender.IsChecked)
        view.row.is_checked = is_checked
        selected = self._selected_list_rows(list_box)
        # Ticking a row outside the selection must stay a single-row action.
        if len(selected) > 1 and view in selected:
            self._bulk_set(selected, is_checked)
        else:
            self._update_state()

    def _key_down(self, list_box, args):
        try:
            from System.Windows.Input import Key

            if args.Key != Key.Space:
                return
        except Exception:
            return
        selected = self._selected_list_rows(list_box)
        if not selected:
            return
        self._bulk_set(selected, not bool(selected[0].row.is_checked))
        args.Handled = True

    # -- parameter grid handlers ------------------------------------------

    def grid_click(self, sender, args):
        """One routed handler for every checkbox in the grid.

        Parsed cell templates cannot carry event attributes, so the clicked
        control is found by walking up the visual tree instead.
        """
        del sender
        source = args.OriginalSource
        if not isinstance(source, CheckBox):
            return
        if _find_parent_cell(source) is None:
            return
        view = getattr(source, "DataContext", None)
        if not isinstance(view, ParameterGridRow):
            return
        is_checked = bool(source.IsChecked)
        view.row.is_checked = is_checked
        selected = self._selected_parameter_views()
        if len(selected) > 1 and view in selected:
            self._bulk_set(selected, is_checked)
        else:
            self._update_state()

    def grid_combo_changed(self, sender, args):
        """Editing one row's combo applies it to the rest of the selection."""
        del sender
        if not self._is_ready or self._suspend:
            return
        source = args.OriginalSource
        if not isinstance(source, ComboBox):
            return
        cell = _find_parent_cell(source)
        if cell is None:
            return
        view = getattr(source, "DataContext", None)
        if not isinstance(view, ParameterGridRow):
            return
        selected = self._selected_parameter_views()
        if len(selected) <= 1 or view not in selected:
            self._update_state()
            return

        value = source.SelectedItem
        self._suspend = True
        try:
            for other in selected:
                if other is view:
                    continue
                if isinstance(value, state.GroupChoice):
                    other.row.group_id = value.group_id
                    other.row.group_label = value.label
                else:
                    other.row.is_instance = (value == BINDING_INSTANCE)
                other.refresh()
        finally:
            self._suspend = False
        self._update_state()

    def header_check_click(self, sender, args):
        del args
        self._bulk_set(self._parameter_views, bool(sender.IsChecked))

    def parameters_selection_changed(self, sender, args):
        del sender, args
        self._update_state()

    def parameter_search_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._refresh_parameters()
        self._update_state()

    def parameters_all_click(self, sender, args):
        del sender, args
        self._apply_command(self._parameter_views, state.select_all)

    def parameters_none_click(self, sender, args):
        del sender, args
        self._apply_command(self._parameter_views, state.select_none)

    def parameters_invert_click(self, sender, args):
        del sender, args
        self._apply_command(self._parameter_views, state.invert_selection)

    def bulk_group_changed(self, sender, args):
        del args
        if not self._is_ready or self._suspend:
            return
        choice = sender.SelectedItem
        if choice is None:
            return
        views = self._selected_parameter_views() or self._parameter_views
        self._suspend = True
        try:
            for view in views:
                view.row.group_id = choice.group_id
                view.row.group_label = choice.label
                view.refresh()
        finally:
            self._suspend = False
        self.StatusText.Text = u"Group set to {0} on {1} row(s).".format(
            choice.label, len(views))

    def bulk_type_click(self, sender, args):
        del sender, args
        self._set_binding(False)

    def bulk_instance_click(self, sender, args):
        del sender, args
        self._set_binding(True)

    def _set_binding(self, is_instance):
        views = self._selected_parameter_views() or self._parameter_views
        self._suspend = True
        try:
            for view in views:
                view.row.is_instance = is_instance
                view.refresh()
        finally:
            self._suspend = False
        self.StatusText.Text = u"{0} set on {1} row(s).".format(
            BINDING_INSTANCE if is_instance else BINDING_TYPE, len(views))

    def add_from_file_click(self, sender, args):
        del sender, args
        picked = self.context.pick_from_shared_file(self)
        if not picked:
            return
        before = len(self.parameter_rows)
        self.parameter_rows = state.sort_parameter_rows(
            state.merge_rows(self.parameter_rows, picked))
        self._refresh_parameters()
        self._update_state()
        added = len(self.parameter_rows) - before
        if added:
            self.StatusText.Text = \
                u"{0} parameter(s) added to the list.".format(added)
        else:
            self.StatusText.Text = \
                u"Those parameters are already in the list."

    # -- family handlers ---------------------------------------------------

    def family_search_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._refresh_families()
        self._update_state()

    def family_check_click(self, sender, args):
        del args
        self._check_click(self.FamilyList, sender)
        self._sync_categories()

    def family_key_down(self, sender, args):
        del sender
        self._key_down(self.FamilyList, args)
        self._sync_categories()

    def families_all_click(self, sender, args):
        del sender, args
        self._apply_command(self._family_views, state.select_all)
        self._sync_categories()

    def families_none_click(self, sender, args):
        del sender, args
        self._apply_command(self._family_views, state.select_none)

    def families_invert_click(self, sender, args):
        del sender, args
        self._apply_command(self._family_views, state.invert_selection)
        self._sync_categories()

    def _sync_categories(self):
        """Tick the categories of checked families - additively.

        Only ever adds: re-applying on every click would make an unticked
        category impossible to keep unticked.
        """
        wanted = state.categories_for_families(self.family_rows,
                                               self.category_rows)
        changed = state.apply_check_to_rows(wanted, True)
        if not changed:
            return
        changed_ids = set(id(row) for row in changed)
        for view in self._category_views:
            if id(view.row) in changed_ids:
                view.refresh()
        self._update_state()

    def add_project_families_click(self, sender, args):
        del sender, args
        rows = self.context.collect_project_families()
        before = len(self.family_rows)
        self.family_rows = state.sort_family_rows(
            state.merge_rows(self.family_rows, rows))
        self._refresh_families()
        self._update_state()
        self.StatusText.Text = u"{0} project family(ies) added.".format(
            len(self.family_rows) - before)

    def add_folder_families_click(self, sender, args):
        del sender, args
        rows, folder = self.context.pick_folder_families(self)
        if not folder:
            return
        before = len(self.family_rows)
        self.family_rows = state.sort_family_rows(
            state.merge_rows(self.family_rows, rows))
        self._refresh_families()
        self._update_state()
        added = len(self.family_rows) - before
        if added:
            self.StatusText.Text = u"{0} family file(s) found in {1}.".format(
                added, folder)
        else:
            self.StatusText.Text = u"No new family files found in {0}.".format(
                folder)

    def remove_families_click(self, sender, args):
        del sender, args
        keep = [row for row in self.family_rows if not row.is_checked]
        removed = len(self.family_rows) - len(keep)
        self.family_rows = keep
        self._refresh_families()
        self._update_state()
        self.StatusText.Text = u"{0} family(ies) removed from the list.".format(
            removed)

    def output_toggle_click(self, sender, args):
        del args
        enabled = bool(sender.IsChecked)
        self.OutputFolderText.IsEnabled = enabled
        self.BrowseOutputButton.IsEnabled = enabled
        if not enabled:
            self.OutputFolderText.Text = u""

    def browse_output_click(self, sender, args):
        del sender, args
        folder = self.context.pick_output_folder()
        if folder:
            self.OutputFolderText.Text = folder

    # -- category handlers -------------------------------------------------

    def category_search_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._refresh_categories()
        self._update_state()

    def category_check_click(self, sender, args):
        del args
        self._check_click(self.CategoryList, sender)

    def category_key_down(self, sender, args):
        del sender
        self._key_down(self.CategoryList, args)

    def categories_all_click(self, sender, args):
        del sender, args
        self._apply_command(self._category_views, state.select_all)

    def categories_none_click(self, sender, args):
        del sender, args
        self._apply_command(self._category_views, state.select_none)

    def categories_invert_click(self, sender, args):
        del sender, args
        self._apply_command(self._category_views, state.invert_selection)

    # -- run ---------------------------------------------------------------

    def mode_changed(self, sender, args):
        del sender, args
        self._update_state()

    @property
    def mode(self):
        if bool(self.ModeUpdateRadio.IsChecked):
            return state.MODE_UPDATE
        return state.MODE_SKIP

    @property
    def output_folder(self):
        if not bool(self.OutputFolderCheck.IsChecked):
            return u""
        return self.OutputFolderText.Text.strip()

    def load_families_click(self, sender, args):
        del sender, args
        self._run(state.ACTION_FAMILIES)

    def load_project_click(self, sender, args):
        del sender, args
        self._run(state.ACTION_PROJECT)

    def _run(self, action):
        try:
            summary = self.context.run(self, action)
        except Exception as error:
            LOGGER.exception("Load Parameters run failed.")
            forms.alert(u"Load Parameters failed.",
                        expanded=state.safe_text(error),
                        title="Load Parameters")
            return
        if summary is None:
            return
        # Rows may have been marked unusable during the run (checkout, files
        # that turned out to be read-only), so the lists are re-rendered.
        self._refresh_families()
        self._update_state()
        self.StatusText.Text = state.build_headline(summary, action)

    def close_click(self, sender, args):
        del sender, args
        self.Close()


# ---------------------------------------------------------------------------
# shared parameter file picker
# ---------------------------------------------------------------------------


class SharedParameterFileWindow(forms.WPFWindow):
    """Tick definitions out of a .txt, grouped as the file groups them."""

    def __init__(self, xaml_file_name, file_path, groups):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._is_ready = False
        self.groups = list(groups or [])
        self.result = None
        self._controls = []
        self._expanded = {}

        self.FileText.Text = os.path.basename(state.safe_text(file_path))
        self._populate()
        self._is_ready = True
        self._update_count()

    def _all_rows(self):
        rows = []
        for _, group_rows in self.groups:
            rows.extend(group_rows)
        return rows

    def _sync(self):
        """Write checkbox state back before rebuilding.

        Without this the search box silently wipes every tick made so far.
        """
        for box in self._controls:
            row = getattr(box, "Tag", None)
            if row is not None:
                row.is_checked = bool(box.IsChecked)

    def _populate(self):
        self._sync()
        self._controls = []
        self.GroupPanel.Children.Clear()

        needle = state.safe_text(self.SearchBox.Text).strip().lower()
        for group_name, rows in self.groups:
            visible = [row for row in rows
                       if not needle or needle in row.display().lower()]
            if not visible:
                continue

            panel = Windows.Controls.StackPanel()
            for row in visible:
                box = Windows.Controls.CheckBox()
                box.Content = row.display()
                box.IsChecked = bool(row.is_checked)
                box.Tag = row
                box.Margin = Windows.Thickness(18, 3, 8, 3)
                if not row.supported:
                    box.IsEnabled = False
                    box.ToolTip = row.unsupported_reason
                panel.Children.Add(box)
                self._controls.append(box)

            expander = Windows.Controls.Expander()
            expander.Header = u"{0} ({1})".format(group_name, len(visible))
            expander.IsExpanded = self._expanded.get(group_name, True)
            expander.Content = panel
            self.GroupPanel.Children.Add(expander)

    def _update_count(self):
        if not self._is_ready:
            return
        self._sync()
        checked = len([row for row in self._all_rows() if row.is_checked])
        self.CountText.Text = u"{0} checked".format(checked)
        self.AddButton.IsEnabled = bool(checked)

    def search_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._populate()
        self._update_count()

    def select_all_click(self, sender, args):
        del sender, args
        for box in self._controls:
            if box.IsEnabled:
                box.IsChecked = True
        self._update_count()

    def select_none_click(self, sender, args):
        del sender, args
        for box in self._controls:
            box.IsChecked = False
        self._update_count()

    def add_click(self, sender, args):
        del sender, args
        self._sync()
        self.result = [row for row in self._all_rows()
                       if row.is_checked and row.supported]
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


# ---------------------------------------------------------------------------
# confirm and report
# ---------------------------------------------------------------------------


class ConfirmWindow(forms.WPFWindow):
    """The dry run.  Nothing is written until this window says so."""

    def __init__(self, xaml_file_name, headline, body, needs_upgrade_ack=False,
                 needs_binding_ack=False, note=u""):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = False
        self._needs_upgrade = bool(needs_upgrade_ack)
        self._needs_binding = bool(needs_binding_ack)

        self.HeadlineText.Text = state.safe_text(headline)
        self.BodyText.Text = state.safe_text(body)
        self.DialogNoteText.Text = state.safe_text(note)

        if self._needs_upgrade:
            self.UpgradeCheck.Visibility = Visibility.Visible
        if self._needs_binding:
            self.BindingCheck.Visibility = Visibility.Visible
        self._update_proceed()

    def _update_proceed(self):
        blockers = []
        if self._needs_upgrade and not bool(self.UpgradeCheck.IsChecked):
            blockers.append(u"the upgrade warning")
        if self._needs_binding and not bool(self.BindingCheck.IsChecked):
            blockers.append(u"the Instance/Type warning")
        if blockers:
            self.ProceedButton.IsEnabled = False
            self.ProceedButton.ToolTip = u"Acknowledge {0} first.".format(
                u" and ".join(blockers))
        else:
            self.ProceedButton.IsEnabled = True
            self.ProceedButton.ToolTip = None

    def acknowledge_changed(self, sender, args):
        del sender, args
        self._update_proceed()

    def proceed_click(self, sender, args):
        del sender, args
        self.result = True
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = False
        self.Close()


class ReportWindow(forms.WPFWindow):
    """What actually happened."""

    def __init__(self, xaml_file_name, headline, body):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.HeadlineText.Text = state.safe_text(headline)
        self.BodyText.Text = state.safe_text(body)

    def close_click(self, sender, args):
        del sender, args
        self.Close()
