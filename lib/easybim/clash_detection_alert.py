# -*- coding: utf-8 -*-
"""Non-modal "a clash just happened" window (Silent Mode off).

This is the repo's only modeless window - every other EasyBIM dialog is
``ShowDialog``.  It has to be: the whole point of the mode is that the user
keeps modelling, so a modal warning would defeat it.

The window is raised from the engine's Idling pass, never from
``DocumentChanged``, and it is reused: a second batch of clashes appends to
the open window rather than stacking a new one on screen.
"""

from __future__ import print_function

import os

try:
    from pyrevit import forms
except Exception:
    forms = None

try:
    from pyrevit import script
except Exception:
    script = None

from easybim.clash_detection_panel import ClashRow
from easybim.clash_detection_panel import checked_pair_keys
from easybim.clash_detection_panel import set_row_checked


ALERT_XAML = os.path.join(os.path.dirname(__file__), "ui", "clash_detection_alert.xaml")

#: The window is a notification, not a report - the panel holds the full
#: list.  Keeping it short also keeps it out of the user's way.
MAX_ROWS = 50

_WINDOW = None
_ROWS = None


def _log(message):
    try:
        if script is not None:
            script.get_logger().debug("[Clash Detection Alert] {0}".format(message))
    except Exception:
        pass


def _engine():
    from easybim import clash_detection_engine

    return clash_detection_engine


def _rows():
    global _ROWS
    if _ROWS is None:
        from System.Collections.ObjectModel import ObservableCollection

        _ROWS = ObservableCollection[object]()
    return _ROWS


def _rows_list():
    rows = _rows()
    try:
        return [rows[index] for index in range(rows.Count)]
    except Exception:
        return []


def _place_bottom_right(window):
    try:
        import clr

        clr.AddReference("AdWindows")
        import Autodesk.Windows as autodesk_windows

        from System.Windows.Interop import WindowInteropHelper

        WindowInteropHelper(window).Owner = (
            autodesk_windows.ComponentManager.ApplicationWindow
        )
    except Exception:
        pass
    try:
        from System.Windows import SystemParameters

        work_area = SystemParameters.WorkArea
        window.Left = max(0, work_area.Right - window.Width - 24)
        window.Top = max(0, work_area.Bottom - window.Height - 24)
    except Exception:
        pass


def _window_type():
    if forms is None:
        return None
    base_type = getattr(forms, "WPFWindow", None)
    if base_type is None:
        return None

    class ClashDetectionAlertWindow(base_type):
        def __init__(self):
            base_type.__init__(self, ALERT_XAML)
            self.Topmost = True
            self.RowsListView.ItemsSource = _rows()
            self.Closed += self._on_closed

        def _on_closed(self, sender, args):
            del sender, args
            _forget_window()

        def show_click(self, sender, args):
            del sender, args
            keys = checked_pair_keys(_rows_list())
            if keys:
                _engine().request_show(keys)

        def rows_double_click(self, sender, args):
            del args
            row = getattr(sender, "SelectedItem", None)
            pair_key = getattr(row, "pair_key", None)
            if pair_key:
                _engine().request_show(pair_key)

        def row_check_click(self, sender, args):
            del sender, args
            self._update_buttons()

        def select_all_click(self, sender, args):
            del sender, args
            for row in _rows_list():
                set_row_checked(row, True)
            self._update_buttons()

        def select_none_click(self, sender, args):
            del sender, args
            for row in _rows_list():
                set_row_checked(row, False)
            self._update_buttons()

        def pause_click(self, sender, args):
            del sender, args
            _engine().pause()
            self._update_buttons()

        def resume_click(self, sender, args):
            del sender, args
            _engine().resume()
            self._update_buttons()

        def silent_click(self, sender, args):
            del sender, args
            _engine().set_silent_mode(True)

        def stop_click(self, sender, args):
            del sender, args
            _engine().stop(reason="alert Stop Detection")

        def dismiss_click(self, sender, args):
            del sender, args
            self.Close()

        def _update_buttons(self):
            try:
                paused = _engine().is_paused()
            except Exception:
                paused = False
            try:
                self.PauseButton.IsEnabled = not paused
                self.ResumeButton.IsEnabled = paused
                self.ShowButton.IsEnabled = bool(checked_pair_keys(_rows_list()))
            except Exception:
                pass

    return ClashDetectionAlertWindow


def _forget_window():
    global _WINDOW
    _WINDOW = None
    try:
        _rows().Clear()
    except Exception:
        pass


def show(records, session=None):
    """Announce ``records``.  Reuses the open window when there is one."""
    global _WINDOW

    if not records:
        return False

    rows = _rows()
    try:
        for record in records:
            rows.Add(ClashRow(record))
        while rows.Count > MAX_ROWS:
            rows.RemoveAt(0)
    except Exception as ex:
        _log("Could not add rows: {0}".format(ex))
        return False

    if _WINDOW is None:
        window_type = _window_type()
        if window_type is None:
            return False
        try:
            _WINDOW = window_type()
            _place_bottom_right(_WINDOW)
            _WINDOW.Show()
        except Exception as ex:
            _WINDOW = None
            _log("Alert window failed: {0}".format(ex))
            return False
    else:
        try:
            _WINDOW.Activate()
        except Exception:
            pass

    try:
        count = rows.Count
        _WINDOW.HeaderText.Text = (
            "New clash detected" if count == 1 else "{0} new clashes detected".format(count)
        )
        if session is not None:
            _WINDOW.ScopeText.Text = session.scope_text()
        _WINDOW._update_buttons()
    except Exception:
        pass
    return True


def close():
    global _WINDOW
    window = _WINDOW
    _WINDOW = None
    if window is not None:
        try:
            window.Close()
        except Exception:
            pass
    try:
        _rows().Clear()
    except Exception:
        pass
