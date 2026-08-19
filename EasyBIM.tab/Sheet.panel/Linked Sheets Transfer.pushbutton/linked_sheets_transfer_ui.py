# -*- coding: utf-8 -*-
"""WPF windows for Linked Sheets Transfer.

Free of Revit API imports: everything Revit-facing arrives through the
callables the launcher hangs on the context object, which is what lets the
test suite parse and check these classes off Revit.

Grid rows are thin wrappers that write straight back onto the ``*_state``
objects, so the plan builder never has to know a window exists.  They derive
from ``NotifyingObject`` because a ``DataGrid`` whose ``ItemsSource`` is
reassigned to refresh loses the user's selection mid-gesture.
"""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

from pyrevit import forms
from pyrevit import script
from pyrevit.framework import Windows

import linked_sheets_transfer_state as state


LOGGER = script.get_logger()

try:
    from easybim.wpf_notify import NotifyingObject as _RowBase
    _NOTIFY_SUPPORTED = True
except Exception:
    _RowBase = object
    _NOTIFY_SUPPORTED = False


def _notify(row, name):
    if not _NOTIFY_SUPPORTED:
        return
    try:
        row.raise_property_changed(name)
    except Exception:
        pass


class _Row(_RowBase):
    def __init__(self):
        if _NOTIFY_SUPPORTED:
            _RowBase.__init__(self)


# ---------------------------------------------------------------------------
# bound rows
# ---------------------------------------------------------------------------

class OptionRow(_Row):
    """One checkbox in the options panel, defined once in the state module."""

    def __init__(self, key, label, checked, tooltip):
        _Row.__init__(self)
        self.key = key
        self.label = label
        self.tooltip = tooltip
        self._is_checked = bool(checked)

    def _get_is_checked(self):
        return self._is_checked

    def _set_is_checked(self, value):
        self._is_checked = bool(value)
        _notify(self, "is_checked")

    is_checked = property(_get_is_checked, _set_is_checked)


class SheetGridRow(_Row):
    """A ``state.SheetRow`` dressed for the grid, writing straight through."""

    def __init__(self, sheet_row):
        _Row.__init__(self)
        self.row = sheet_row

    @property
    def source_number(self):
        return self.row.source_number

    @property
    def source_name(self):
        return self.row.source_name

    @property
    def views_text(self):
        return self.row.views_text

    @property
    def status_text(self):
        return self.row.status_text

    @property
    def has_problem(self):
        return self.row.has_problem

    @property
    def search_blob(self):
        return self.row.search_blob

    def _get_is_checked(self):
        return self.row.is_checked

    def _set_is_checked(self, value):
        self.row.is_checked = bool(value)
        _notify(self, "is_checked")

    is_checked = property(_get_is_checked, _set_is_checked)

    def _get_new_number(self):
        return self.row.new_number

    def _set_new_number(self, value):
        self.row.new_number = state.safe_text(value)
        _notify(self, "new_number")

    new_number = property(_get_new_number, _set_new_number)

    def _get_new_name(self):
        return self.row.new_name

    def _set_new_name(self, value):
        self.row.new_name = state.safe_text(value)
        _notify(self, "new_name")

    new_name = property(_get_new_name, _set_new_name)

    def refresh(self):
        for name in ("status_text", "has_problem", "new_number", "new_name",
                     "is_checked"):
            _notify(self, name)


class UnmappedChoice(object):
    """The 'leave this level out' entry at the top of every level dropdown."""

    key = None
    elevation = 0.0
    label = u"(not mapped - views on this level are skipped)"
    name = label


class LevelGridRow(_Row):
    """One row of the level-mapping grid."""

    def __init__(self, level_row):
        _Row.__init__(self)
        self.row = level_row
        self.unmapped = UnmappedChoice()
        self.choices = [self.unmapped] + list(level_row.choices)

    @property
    def linked_name(self):
        return self.row.linked_name

    @property
    def delta_text(self):
        return self.row.delta_text

    @property
    def source_text(self):
        if self.row.ambiguous:
            return u"{0} (two match)".format(self.row.source)
        return self.row.source

    @property
    def has_problem(self):
        return (not self.row.is_mapped) or self.row.ambiguous

    def _get_host_choice(self):
        return self.row.host_choice or self.unmapped

    def _set_host_choice(self, value):
        if value is None or getattr(value, "key", None) is None:
            self.row.set_manual(None)
        else:
            self.row.set_manual(value)
        for name in ("host_choice", "source_text", "delta_text",
                     "has_problem"):
            _notify(self, name)

    host_choice = property(_get_host_choice, _set_host_choice)


# ---------------------------------------------------------------------------
# the main window
# ---------------------------------------------------------------------------

class LinkedSheetsTransferWindow(forms.WPFWindow):
    """Pick a link, pick sheets, map the levels, press Copy Sheets."""

    def __init__(self, xaml_file_name, context):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)

        self.context = context
        self.link_option = None
        self.link_data = None
        self.sheet_rows = []
        self.level_rows = []
        self._sheet_grid_rows = []
        self._visible_rows = []
        self._level_grid_rows = []

        self.option_rows = [
            OptionRow(key, label, default, tooltip)
            for key, label, default, tooltip in state.OPTION_DEFS]
        self.OptionList.ItemsSource = self.option_rows

        self.LinkCombo.ItemsSource = list(context.link_options)
        usable = [index for index, option in enumerate(context.link_options)
                  if option.is_usable]
        if usable:
            self.LinkCombo.SelectedIndex = usable[0]
        elif context.link_options:
            self.LinkCombo.SelectedIndex = 0

        self._is_ready = True
        self._load_selected_link()

    # -- options ----------------------------------------------------------

    @property
    def options(self):
        values = state.default_options()
        for row in self.option_rows:
            values[row.key] = bool(row.is_checked)
        return values

    # -- loading ----------------------------------------------------------

    def _load_selected_link(self):
        option = self.LinkCombo.SelectedItem
        self.link_option = option
        self.sheet_rows = []
        self.level_rows = []
        self._sheet_grid_rows = []
        self._level_grid_rows = []

        if option is None:
            self.LinkHintText.Text = (
                u"No loaded Revit links in this model.")
            self._refresh_sheets()
            self._refresh_levels()
            self._update_actions()
            return

        if not option.is_usable:
            self.LinkHintText.Text = (
                u"This link cannot be reproduced: {0}.".format(option.blocker))
            self._refresh_sheets()
            self._refresh_levels()
            self._update_actions()
            return

        self.LinkHintText.Text = u"Reading the link..."
        try:
            self.link_data = self.context.load_link(option)
        except Exception as load_error:
            self.link_data = None
            self.LinkHintText.Text = u"The link could not be read: {0}".format(
                load_error)
            self._refresh_sheets()
            self._refresh_levels()
            self._update_actions()
            return

        self.sheet_rows = self.link_data.sheet_rows
        self.level_rows = self.link_data.level_rows
        self._sheet_grid_rows = [SheetGridRow(row) for row in self.sheet_rows]

        used = set(state.all_level_keys(self.sheet_rows))
        self._level_grid_rows = [
            LevelGridRow(row) for row in self.level_rows
            if row.linked_key in used]

        self.LinkHintText.Text = u"{0} sheet(s) in the link.".format(
            len(self.sheet_rows))
        self._refresh_sheets()
        self._refresh_levels()
        self._recheck()

    def _refresh_sheets(self):
        self._visible_rows = state.filter_rows(self._sheet_grid_rows,
                                               self.SearchBox.Text)
        self.SheetGrid.ItemsSource = None
        self.SheetGrid.ItemsSource = self._visible_rows

    def _refresh_levels(self):
        self.LevelGrid.ItemsSource = None
        self.LevelGrid.ItemsSource = self._level_grid_rows

    def _recheck(self):
        """Re-run collision detection and repaint the affected rows."""
        facts = getattr(self.link_data, "host_facts", None)
        existing = facts.sheet_numbers if facts is not None else []
        state.detect_collisions(
            [row.row for row in self._sheet_grid_rows], existing)
        for row in self._sheet_grid_rows:
            row.refresh()
        self._update_actions()

    def _update_actions(self):
        reason = self._blocking_reason()
        self.CopyButton.IsEnabled = not reason
        self.CopyButton.ToolTip = reason or None
        if reason:
            self.StatusText.Text = reason
            return

        checked = state.checked_rows(
            [row.row for row in self._sheet_grid_rows])
        views = sum(len(row.supported_views) for row in checked)
        unmapped = len([row for row in self._level_grid_rows
                        if not row.row.is_mapped])
        message = u"{0} sheet(s), {1} plan view(s) ready.".format(
            len(checked), views)
        if unmapped:
            message += (u"  {0} level(s) are still unmapped; their views will "
                        u"be skipped.".format(unmapped))
        self.StatusText.Text = message

    def _blocking_reason(self):
        if self.link_option is None:
            return u"This model has no loaded Revit links."
        if not self.link_option.is_usable:
            return u"This link cannot be reproduced: {0}.".format(
                self.link_option.blocker)
        rows = [row.row for row in self._sheet_grid_rows]
        checked = state.checked_rows(rows)
        if not checked:
            return u"Tick at least one sheet."
        problems = [row for row in checked if row.has_problem]
        if problems:
            return (u"{0} sheet number(s) clash. Use the prefix or suffix "
                    u"box, or edit the New number column.".format(
                        len(problems)))
        return u""

    # -- handlers ---------------------------------------------------------

    def link_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._load_selected_link()

    def search_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._refresh_sheets()

    def select_all_click(self, sender, args):
        del sender, args
        state.apply_check_to_rows(
            [row.row for row in self._visible_rows], True)
        self._recheck()

    def select_none_click(self, sender, args):
        del sender, args
        state.apply_check_to_rows(
            [row.row for row in self._visible_rows], False)
        self._recheck()

    def header_check_click(self, sender, args):
        del args
        checked = bool(getattr(sender, "IsChecked", False))
        state.apply_check_to_rows(
            [row.row for row in self._visible_rows], checked)
        self._recheck()

    def sheet_check_click(self, sender, args):
        del sender, args
        self._recheck()

    def affix_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        state.apply_number_affixes(
            [row.row for row in self._sheet_grid_rows],
            self.PrefixBox.Text, self.SuffixBox.Text)
        self._recheck()

    def sheet_cell_edit_ending(self, sender, args):
        del sender
        # CellEditEnding fires before the binding commits, so the edited text
        # is read off the editor rather than off a row that has not caught up.
        try:
            row = args.Row.Item
            header = state.safe_text(args.Column.Header).lower()
            text = state.safe_text(args.EditingElement.Text)
            if header.startswith(u"new number"):
                row.new_number = text
            elif header.startswith(u"new name"):
                row.new_name = text
        except Exception:
            pass
        self._recheck()

    def level_choice_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._update_actions()

    def copy_click(self, sender, args):
        del sender, args
        if self._blocking_reason():
            return
        try:
            self.context.run(self)
        except Exception as run_error:
            LOGGER.exception("Linked Sheets Transfer failed.")
            forms.alert(u"Linked Sheets Transfer failed:\n{0}".format(run_error),
                        title=u"Linked Sheets Transfer")
        self._reload_after_run()

    def _reload_after_run(self):
        """A run creates sheets, so the collision set has moved on."""
        try:
            self.link_data = self.context.refresh_host_facts(self.link_data)
        except Exception:
            pass
        self._recheck()

    def cancel_click(self, sender, args):
        del sender, args
        self.Close()


# ---------------------------------------------------------------------------
# confirm and report
# ---------------------------------------------------------------------------

class ConfirmWindow(forms.WPFWindow):
    """The dry run.  Nothing is written until this window says so."""

    def __init__(self, xaml_file_name, headline, body,
                 needs_model_change_ack=False, needs_double_draw_ack=False):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = False
        self._needs_model_change = bool(needs_model_change_ack)
        self._needs_double_draw = bool(needs_double_draw_ack)

        self.HeadlineText.Text = state.safe_text(headline)
        self.BodyText.Text = state.safe_text(body)

        if self._needs_model_change:
            self.ModelChangeCheck.Visibility = Windows.Visibility.Visible
        if self._needs_double_draw:
            self.DoubleDrawCheck.Visibility = Windows.Visibility.Visible
        self._update_proceed()

    def _update_proceed(self):
        blockers = []
        if self._needs_model_change and not bool(
                self.ModelChangeCheck.IsChecked):
            blockers.append(u"the copied families warning")
        if self._needs_double_draw and not bool(
                self.DoubleDrawCheck.IsChecked):
            blockers.append(u"the double-drawing warning")
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
    """What actually happened, including how well every viewport landed.

    The sheet is not opened from here: Revit refuses to change the active view
    while a modal dialog is up, so the button only records the request and the
    launcher acts on it once this window has closed.
    """

    def __init__(self, xaml_file_name, headline, body, can_open_sheet=False):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.HeadlineText.Text = state.safe_text(headline)
        self.BodyText.Text = state.safe_text(body)
        self.open_requested = False
        if not can_open_sheet:
            self.OpenSheetButton.IsEnabled = False
            self.OpenSheetButton.ToolTip = u"No sheet was created."

    def open_sheet_click(self, sender, args):
        del sender, args
        self.open_requested = True
        self.Close()

    def close_click(self, sender, args):
        del sender, args
        self.Close()
