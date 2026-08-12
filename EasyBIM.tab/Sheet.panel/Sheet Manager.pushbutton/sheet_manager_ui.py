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
from pyrevit import HOST_APP
from pyrevit import framework
from pyrevit import forms
from pyrevit import revit
from pyrevit import script

from easybim import excel_print_sets
from easybim import excel_workbook
from easybim import link_reload
from easybim import print_sets
from easybim.compat import eid_to_int

import sheet_manager_dialogs as dialogs
import sheet_manager_revit as smrevit
import sheet_manager_state as state
import sheet_manager_xlsx as smxlsx


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
        self._source_order = None
        self._all_revision_ids = set()
        self._revision_filter_ids = set()
        self._param_rules = []
        self._sort_levels = []
        self._copy_content_ops = []
        self._sheet_param_info = None
        self._tb_param_info = None
        self._filter_extra_cache = {}

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
        self._all_revision_ids = set(
            revision.get("id") for revision in self._revision_rows)
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

    def _refresh_visible_rows(self):
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
        self._update_selectall_state()
        self._update_filter_labels()
        self._update_status()

    def _extra_filter_lookup(self, row, column_key):
        """Values for filter rules on params that are not table columns."""
        cache_key = (row.sheet_id, column_key)
        if cache_key in self._filter_extra_cache:
            return self._filter_extra_cache[cache_key]
        value = None
        if column_key.startswith("p:") or column_key.startswith("tb:"):
            param_name = column_key.split(":", 1)[1]
            if column_key.startswith("p:"):
                element = self._sheets_by_id.get(row.sheet_id)
            else:
                tblocks = self._tb_map.get(row.sheet_id) or []
                element = tblocks[0] if len(tblocks) == 1 else None
            value = smrevit.read_parameter_value(element, param_name)
        self._filter_extra_cache[cache_key] = value
        return value

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
        self._commit_pending_edit()
        doc = revit.doc
        schedules = smrevit.collect_sheet_list_schedules(doc)
        if not schedules:
            forms.alert("No Sheet List schedules were found in the model.",
                        title="Sheet Manager")
            return
        names = [getattr(s, "Name", u"") or u"" for s in schedules]
        dialog = dialogs.LoadFromSourceWindow(
            "LoadFromSourceDialog.xaml", "schedule", names)
        dialog.ShowDialog()
        if not dialog.result:
            return
        name, numbers_only = dialog.result
        schedule = schedules[names.index(name)]
        try:
            ordered = smrevit.get_ordered_sheets_for_schedule(doc, schedule)
        except Exception as err:
            forms.alert("Could not read the sheet list order.",
                        expanded=str(err), title="Sheet Manager")
            return
        self._source_order = [eid_to_int(sheet.Id) for sheet in ordered]
        self._source_label = u"Sheet List: {0}".format(name)
        if not numbers_only:
            try:
                self._load_schedule_columns(doc, schedule, ordered)
            except Exception as err:
                LOGGER.warning("Sheet list columns unavailable: %s", err)
                forms.alert(
                    "Loaded sheet order, but the sheet list's columns "
                    "could not be read; showing numbers & names only.",
                    expanded=str(err), title="Sheet Manager")
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
        names = print_sets.collect_print_set_names(revit.doc, DB, framework)
        if not names:
            forms.alert("No print sets were found in the model.",
                        title="Sheet Manager")
            return
        dialog = dialogs.LoadFromSourceWindow(
            "LoadFromSourceDialog.xaml", "printset", names)
        dialog.ShowDialog()
        if not dialog.result:
            return
        name = dialog.result[0]
        sheets = smrevit.get_print_set_sheets(revit.doc, name)
        if not sheets:
            forms.alert(
                u"Print set '{0}' contains no sheets.".format(name),
                title="Sheet Manager")
            return
        self._source_order = [eid_to_int(sheet.Id) for sheet in sheets]
        self._source_label = u"Print Set: {0}".format(name)
        self._refresh_visible_rows()

    def filter_by_revision(self, sender, args):
        del sender, args
        if not self._revision_rows:
            forms.alert("No revisions were found in the model.",
                        title="Sheet Manager")
            return
        dialog = dialogs.FilterByRevisionWindow(
            "FilterByRevisionDialog.xaml", self._revision_rows,
            self._revision_filter_ids)
        dialog.ShowDialog()
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
        self._ensure_param_info()
        dialog = dialogs.FilterByParameterWindow(
            "FilterByParameterDialog.xaml", self._filter_field_options(),
            self._param_rules, False)
        dialog.ShowDialog()
        if dialog.result is None:
            return
        rules, add_params = dialog.result
        self._param_rules = rules
        self._filter_extra_cache = {}
        if add_params:
            self._add_rule_param_columns(rules)
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
        dialog = dialogs.SortWindow(
            "SortDialog.xaml", field_options, self._sort_levels)
        dialog.ShowDialog()
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
        self._ensure_param_info()
        existing = set(column.param_name for column in self._columns
                       if column.kind == state.KIND_TB_PARAM)
        names = sorted(param_name for param_name in self._tb_param_info
                       if param_name not in existing)
        if not names:
            forms.alert("No more title block parameters to add.",
                        title="Sheet Manager")
            return
        chosen = forms.SelectFromList.show(
            names, multiselect=True, title="Add Title Block Parameter",
            button_name="Add Columns")
        if not chosen:
            return
        self._add_param_columns(state.KIND_TB_PARAM, chosen)

    def add_sheet_parameter(self, sender, args):
        del sender, args
        self._ensure_param_info()
        existing = set(column.param_name for column in self._columns
                       if column.kind == state.KIND_SHEET_PARAM)
        names = sorted(param_name for param_name in self._sheet_param_info
                       if param_name not in existing)
        if not names:
            forms.alert("No more sheet parameters to add.",
                        title="Sheet Manager")
            return
        chosen = forms.SelectFromList.show(
            names, multiselect=True, title="Add Sheet Parameter",
            button_name="Add Columns")
        if not chosen:
            return
        self._add_param_columns(state.KIND_SHEET_PARAM, chosen)

    def export_to_excel(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        if not smxlsx.XLSXWRITER_AVAILABLE:
            forms.alert(
                "The 'xlsxwriter' module is not available in this "
                "pyRevit installation.", title="Sheet Manager")
            return
        if not self._visible_rows:
            forms.alert("There are no rows to export.",
                        title="Sheet Manager")
            return
        doc_title = getattr(revit.doc, "Title", u"") or u""
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
            forms.alert("Export failed.", expanded=str(err),
                        title="Sheet Manager")
            return
        forms.alert(
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
            forms.alert("Could not read the workbook.",
                        expanded=str(err), title="Import from Excel")
            return
        export_data = sheets.get(smxlsx.EXPORT_SHEET_NAME)
        if export_data is None or not export_data.rows:
            forms.alert(
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
            forms.alert("No differences were found - nothing to stage.",
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
        forms.alert(u"\n".join(message),
                    expanded=u"\n".join(expanded_lines) or None,
                    title="Import from Excel")

    def copy_sheet_info(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        targets = [item for item in self.sheets_dg.SelectedItems
                   if isinstance(item, SheetRow)]
        if not targets:
            forms.alert(
                "Select the target sheets in the table first "
                "(shift-click or drag).", title="Copy Sheet Info")
            return
        source_options = [
            (row.sheet_id, u"{0} - {1}".format(row.number, row.name))
            for row in self._all_rows if not row.is_pending]
        dialog = dialogs.CopySheetInfoWindow(
            "CopySheetInfoDialog.xaml", source_options, len(targets))
        dialog.ShowDialog()
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
            forms.alert("Pick target sheets different from the source.",
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
        forms.alert("\n".join(message), title="Copy Sheet Info")
        self._update_status()

    def search_replace(self, sender, args):
        del sender, args
        self._commit_pending_edit()

        def plan_provider(find_text, replace_text, match_case):
            return state.plan_search_replace(
                self._visible_rows, self._columns, find_text,
                replace_text, match_case)

        dialog = dialogs.SearchReplaceWindow(
            "SearchReplaceDialog.xaml", plan_provider)
        dialog.ShowDialog()
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
            forms.alert(u"\n".join(message),
                        expanded=u"\n".join(skipped_invalid) or None,
                        title="Search & Replace")

    def save_print_set(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        if not print_sets.supports_ordered_print_sets(HOST_APP):
            forms.alert("Save Print Set requires Revit 2023 or newer.",
                        title="Sheet Manager")
            return
        checked = [row for row in self._visible_rows
                   if row.is_selected and not row.is_pending]
        if not checked:
            forms.alert(
                "Check at least one sheet row first (checkbox column).",
                title="Save Print Set")
            return
        names = print_sets.collect_print_set_names(revit.doc, DB, framework)
        dialog = dialogs.SavePrintSetWindow(
            "SavePrintSetDialog.xaml", names, len(checked))
        dialog.ShowDialog()
        if not dialog.result:
            return
        sheets = [self._sheets_by_id[row.sheet_id] for row in checked
                  if row.sheet_id in self._sheets_by_id]
        rows = print_sets.build_sheet_rows(sheets)
        printable, skipped = print_sets.split_printable_rows(rows)
        if not printable:
            forms.alert(
                "None of the checked sheets are printable "
                "(placeholder sheets cannot join a print set).",
                title="Save Print Set")
            return
        try:
            print_sets.save_ordered_print_set(
                revit.doc, dialog.result, printable, DB, framework,
                revit, HOST_APP)
        except print_sets.UnsupportedRevitVersion as version_err:
            forms.alert(str(version_err), title="Save Print Set")
            return
        except Exception as err:
            LOGGER.critical("Failed to save print set: %s", err)
            forms.alert("Failed to create or update the print set.",
                        expanded=str(err), title="Save Print Set")
            return
        message = ["Print set saved: {0}".format(dialog.result),
                   "Sheets included: {0}".format(len(printable))]
        if skipped:
            message.append(
                "Skipped non-printable rows: {0}".format(skipped))
        forms.alert("\n".join(message), title="Save Print Set")

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
        self._copy_content_ops = []
        self._filter_extra_cache = {}
        self._refresh_number_conflicts()
        self._refresh_visible_rows()

    def apply_changes(self, sender, args):
        del sender, args
        self._commit_pending_edit()
        changes = state.compute_staged_changes(self._all_rows, self._columns)
        changes.copy_content_ops = list(self._copy_content_ops)
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
            self._refresh_number_conflicts()
            forms.alert(
                "Sheet numbers must be unique and non-empty before "
                "applying. Conflicting number cells are highlighted "
                "orange - finish the swap or pick unique numbers for "
                "the listed sheets first.",
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
