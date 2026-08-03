# -*- coding: utf-8 -*-
"""Setup window for Clash Detection Mode.

Laid out like Revit's native Interference Check - two `Categories from`
dropdowns over two category lists - with the one thing the native dialog
lacks: the lists use ``SelectionMode="Extended"``, so a shift-click range or
a press-and-drag across rows can be ticked with a single checkbox click.

The same window doubles as **Edit Categories** for a session that is already
running, which is why it lives in ``lib`` rather than in the pushbutton
folder: the dockable panel needs to open it, and the panel cannot import
upward out of ``lib``.
"""
# pylint: disable=import-error,invalid-name,broad-except

from __future__ import print_function

import os

from pyrevit import forms
from pyrevit import script

from easybim import clash_detection_revit
from easybim import clash_detection_state as state


LOGGER = script.get_logger()
TITLE = "Clash Detection Mode"
XAML_FILE = os.path.join(
    os.path.dirname(__file__), "ui", "clash_detection_setup.xaml"
)
START_LABEL = "Start Ongoing Detection Mode"
APPLY_LABEL = "Apply Category Changes"

try:
    from easybim.wpf_notify import NotifyingObject as _RowBase

    _NOTIFY_SUPPORTED = True
except Exception:
    _RowBase = object
    _NOTIFY_SUPPORTED = False


class CategoryRow(_RowBase):
    """A category list row.  ``name``/``is_checked`` are what the XAML binds."""

    def __init__(self, option):
        if _NOTIFY_SUPPORTED:
            _RowBase.__init__(self)
        self.name = option.name
        self.category_id = option.category_id
        self.is_checked = bool(option.is_checked)


def _notify(row):
    if not _NOTIFY_SUPPORTED:
        return
    try:
        row.raise_property_changed("is_checked")
    except Exception:
        pass


class ClashDetectionSetupWindow(forms.WPFWindow):
    def __init__(self, doc, session=None):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self.doc = doc
        self.session = session
        self.is_editing = session is not None
        self.result = None
        self._is_ready = False
        self._category_cache = {}
        self._checked_by_model = {}
        self._left_rows = []
        self._right_rows = []

        self.model_options = clash_detection_revit.collect_model_options(doc)
        self.LeftModelCombo.ItemsSource = self.model_options
        self.RightModelCombo.ItemsSource = list(self.model_options)

        if self.is_editing:
            self._preselect_side("left", session.side_a)
            self._preselect_side("right", session.side_b)
        else:
            self.LeftModelCombo.SelectedIndex = 0
            self.RightModelCombo.SelectedIndex = 0

        self._is_ready = True
        self._load_side("left")
        self._load_side("right")
        self._refresh_session_panel()
        self._update_start_enabled()

    # -- running session ---------------------------------------------------

    def _refresh_session_panel(self):
        """Show the live controls whenever a session is running.

        This is the reliable way back to a panel that has been closed, and
        the only Stop that survives a pyRevit engine reload, so it appears
        even when this engine no longer holds the session itself.
        """
        from easybim import clash_detection_engine

        from System.Windows import Visibility

        running = clash_detection_engine.is_active()
        self.SessionPanel.Visibility = (
            Visibility.Visible if running else Visibility.Collapsed
        )
        if not running:
            return

        paused = clash_detection_engine.is_paused()
        session = clash_detection_engine.get_session()
        self.SessionStateText.Text = "Paused" if paused else "Running"
        if session is None:
            self.SessionStateText.Text = "Running (details lost on reload)"
            self.SessionDetailText.Text = (
                "Detection is still attached to Revit but this session's list "
                "was lost when pyRevit reloaded. Stop Detection to clear it."
            )
            self.SessionPauseButton.IsEnabled = False
            self.SessionResumeButton.IsEnabled = False
            self.OpenPanelButton.IsEnabled = False
        else:
            summary = session.summary()
            self.SessionDetailText.Text = "{0}  |  {1} live clash(es), {2} resolved".format(
                session.scope_text(), summary["active"], summary["resolved"]
            )
            self.SessionPauseButton.IsEnabled = not paused
            self.SessionResumeButton.IsEnabled = paused
            self.OpenPanelButton.IsEnabled = True

    def _preselect_side(self, side, side_selection):
        """Reopen on the model and categories the session is already using."""
        combo = self._combo_for(side)
        index = 0
        for position, option in enumerate(self.model_options):
            if option.model_ref == side_selection.model_ref:
                index = position
                break
        combo.SelectedIndex = index
        self._checked_by_model[
            "{0}::{1}".format(side, self.model_options[index].model_key)
        ] = set(side_selection.category_ids)
        self.SilentModeCheck.IsChecked = bool(self.session.silent_mode)

    # -- side plumbing ----------------------------------------------------

    def _list_for(self, side):
        return self.LeftCategoryList if side == "left" else self.RightCategoryList

    def _combo_for(self, side):
        return self.LeftModelCombo if side == "left" else self.RightModelCombo

    def _rows_for(self, side):
        return self._left_rows if side == "left" else self._right_rows

    def _set_rows(self, side, rows):
        if side == "left":
            self._left_rows = rows
        else:
            self._right_rows = rows

    def _cache_key(self, side):
        option = self._combo_for(side).SelectedItem
        return "{0}::{1}".format(side, option.model_key if option else "")

    def _categories_for(self, option):
        """Category probing is the slow part; do it once per document."""
        if option is None:
            return []
        if option.model_key not in self._category_cache:
            self._category_cache[option.model_key] = clash_detection_revit.collect_categories(
                option.document
            )
        return self._category_cache[option.model_key]

    def _load_side(self, side):
        option = self._combo_for(side).SelectedItem
        categories = self._categories_for(option)
        remembered = self._checked_by_model.get(self._cache_key(side), set())
        rows = []
        for category in categories:
            row = CategoryRow(category)
            row.is_checked = category.category_id in remembered
            rows.append(row)
        self._set_rows(side, rows)
        self._list_for(side).ItemsSource = rows
        if not rows:
            self.StatusText.Text = "{0} has no placed model elements to check.".format(
                option.display_name if option else "That model"
            )

    def _remember(self, side):
        self._checked_by_model[self._cache_key(side)] = state.checked_category_ids(
            self._rows_for(side)
        )

    # -- selection --------------------------------------------------------

    def _refresh_preserving_selection(self, list_box):
        """Fallback when INotifyPropertyChanged is unavailable on this host."""
        selected = list(list_box.SelectedItems)
        try:
            list_box.Items.Refresh()
        except Exception:
            items = list_box.ItemsSource
            list_box.ItemsSource = None
            list_box.ItemsSource = items
        try:
            list_box.SelectedItems.Clear()
            for row in selected:
                list_box.SelectedItems.Add(row)
        except Exception:
            pass

    def _bulk_set(self, list_box, rows, is_checked):
        changed = state.apply_check_to_rows(rows, is_checked)
        if changed:
            if _NOTIFY_SUPPORTED:
                for row in changed:
                    _notify(row)
            else:
                self._refresh_preserving_selection(list_box)
        self._update_start_enabled()

    def _apply_selection_command(self, side, command):
        list_box = self._list_for(side)
        rows = self._rows_for(side)
        before = [bool(row.is_checked) for row in rows]
        command(rows)
        if _NOTIFY_SUPPORTED:
            for index, row in enumerate(rows):
                if before[index] != bool(row.is_checked):
                    _notify(row)
        else:
            self._refresh_preserving_selection(list_box)
        self._update_start_enabled()

    def _check_click(self, side, sender):
        list_box = self._list_for(side)
        row = getattr(sender, "DataContext", None)
        if row is None:
            return
        is_checked = bool(sender.IsChecked)
        row.is_checked = is_checked
        selected = list(list_box.SelectedItems)
        # Only spread to the selection when the clicked row is part of it -
        # ticking an unselected row elsewhere must stay a single-row action.
        if len(selected) > 1 and row in selected:
            self._bulk_set(list_box, selected, is_checked)
        else:
            self._update_start_enabled()

    def _key_down(self, side, args):
        try:
            from System.Windows.Input import Key

            if args.Key != Key.Space:
                return
        except Exception:
            return
        list_box = self._list_for(side)
        selected = list(list_box.SelectedItems)
        if not selected:
            return
        self._bulk_set(list_box, selected, not bool(selected[0].is_checked))
        args.Handled = True

    def _update_start_enabled(self):
        left_ids = state.checked_category_ids(self._left_rows)
        right_ids = state.checked_category_ids(self._right_rows)
        can_start = state.can_start(left_ids, right_ids)
        self.StartButton.IsEnabled = can_start
        self.StartButton.Content = APPLY_LABEL if self.is_editing else START_LABEL
        self.StatusText.Text = (
            "{0} category(ies) vs {1} category(ies).".format(
                len(left_ids), len(right_ids)
            )
            if can_start
            else ""
        )

    # -- XAML handlers ----------------------------------------------------

    def left_model_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._load_side("left")
        self._update_start_enabled()

    def right_model_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._load_side("right")
        self._update_start_enabled()

    def left_category_check_click(self, sender, args):
        del args
        self._check_click("left", sender)

    def right_category_check_click(self, sender, args):
        del args
        self._check_click("right", sender)

    def left_category_key_down(self, sender, args):
        del sender
        self._key_down("left", args)

    def right_category_key_down(self, sender, args):
        del sender
        self._key_down("right", args)

    def left_all_click(self, sender, args):
        del sender, args
        self._apply_selection_command("left", state.select_all)

    def left_none_click(self, sender, args):
        del sender, args
        self._apply_selection_command("left", state.select_none)

    def left_invert_click(self, sender, args):
        del sender, args
        self._apply_selection_command("left", state.invert_selection)

    def right_all_click(self, sender, args):
        del sender, args
        self._apply_selection_command("right", state.select_all)

    def right_none_click(self, sender, args):
        del sender, args
        self._apply_selection_command("right", state.select_none)

    def right_invert_click(self, sender, args):
        del sender, args
        self._apply_selection_command("right", state.invert_selection)

    def start_click(self, sender, args):
        del sender, args
        self._remember("left")
        self._remember("right")
        left_ids = state.checked_category_ids(self._left_rows)
        right_ids = state.checked_category_ids(self._right_rows)
        if not state.can_start(left_ids, right_ids):
            return

        left_option = self.LeftModelCombo.SelectedItem
        right_option = self.RightModelCombo.SelectedItem
        self.result = (
            state.SideSelection(
                model_key=left_option.model_key,
                model_ref=left_option.model_ref,
                model_label=left_option.display_name,
                category_ids=left_ids,
                slot="a",
            ),
            state.SideSelection(
                model_key=right_option.model_key,
                model_ref=right_option.model_ref,
                model_label=right_option.display_name,
                category_ids=right_ids,
                slot="b",
            ),
            bool(self.SilentModeCheck.IsChecked),
        )
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()

    def open_panel_click(self, sender, args):
        del sender, args
        try:
            from easybim import clash_detection_engine
            from easybim import clash_detection_panel

            clash_detection_panel.open_panel(clash_detection_engine.get_session())
        except Exception as ex:
            LOGGER.debug("[Clash Detection] Open panel failed: %s", ex)

    def session_pause_click(self, sender, args):
        del sender, args
        from easybim import clash_detection_engine

        clash_detection_engine.pause()
        self._refresh_session_panel()

    def session_resume_click(self, sender, args):
        del sender, args
        from easybim import clash_detection_engine

        clash_detection_engine.resume()
        self._refresh_session_panel()

    def session_stop_click(self, sender, args):
        del sender, args
        from easybim import clash_detection_engine

        clash_detection_engine.stop(reason="main window Stop Detection")
        self.is_editing = False
        self.session = None
        self._refresh_session_panel()
        self._update_start_enabled()


def show_setup_window(doc, uiapp=None, session=None):
    """Run the main window.

    This is the tool's one window, opened by the ribbon button whether or not
    a session is running: when one is, it carries the live controls (Open
    Panel, Pause, Resume, Stop) so a closed panel is always recoverable.

    With ``session`` given the category lists start on the running selection
    and applying them keeps the clashes that are still in scope and still
    clashing, rather than starting the list over.
    """
    from easybim import clash_detection_engine

    if session is None and clash_detection_engine.has_live_session():
        session = clash_detection_engine.get_session()

    window = ClashDetectionSetupWindow(doc, session=session)
    if not window.model_options:
        forms.alert("No model to check.", title=TITLE)
        return False
    window.ShowDialog()
    if not window.result:
        return False

    side_a, side_b, silent_mode = window.result
    links = clash_detection_revit.link_instances_by_ref(window.model_options)

    if session is not None and clash_detection_engine.is_active():
        applied, message = clash_detection_engine.set_categories(
            side_a, side_b, link_instances_by_ref=links
        )
        if not applied:
            forms.alert(message, title=TITLE)
            return False
        clash_detection_engine.set_silent_mode(silent_mode)
        return True

    started, message = clash_detection_engine.start(
        uiapp,
        doc,
        side_a,
        side_b,
        silent_mode=silent_mode,
        link_instances_by_ref=links,
    )
    if not started:
        forms.alert(message, title=TITLE)
        return False
    return True


def request_edit_categories():
    """Open Edit Categories for the running session (used by the panel)."""
    from easybim import clash_detection_engine

    session = clash_detection_engine.get_session()
    doc = clash_detection_engine.get_document()
    if session is None or doc is None:
        return False
    return show_setup_window(doc, session=session)
