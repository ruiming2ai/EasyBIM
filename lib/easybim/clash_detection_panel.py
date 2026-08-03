# -*- coding: utf-8 -*-
"""Right-side dockable panel for Clash Detection Mode.

Revit only accepts ``RegisterDockablePane`` while the application is
initialising, so :func:`register` is called from ``startup.py`` on every
session.  Registration alone is free - the pane stays hidden and holds no
state until :func:`open_panel` is called by the engine.

If the host's pyRevit build has no dockable-panel support, the same content
opens as a modeless window pinned to the right of the Revit window instead
of failing outright.

Rows are immutable value objects held in an ``ObservableCollection``: a
clash description never changes, rows only appear when a clash is created
and disappear when it is resolved.  That means the collection's own change
notification is enough and no per-row binding plumbing is needed here.
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


PANEL_TITLE = "EasyBIM Clash Detection"
PANEL_ID = "1a7c3f42-9d16-4b8e-8f52-6c0d3a9e77b4"
REGISTERED_ENVVAR = "EASYBIM_CLASH_PANEL_REGISTERED"

UI_DIR = os.path.join(os.path.dirname(__file__), "ui")
PANEL_XAML = os.path.join(UI_DIR, "clash_detection_panel.xaml")
PANEL_WINDOW_XAML = os.path.join(UI_DIR, "clash_detection_panel_window.xaml")

_PANEL_TYPE = None
_VIEW = None
_FALLBACK_WINDOW = None
_ROWS = None
_ROW_KEYS = []
_LAST_STATUS = ""
_LAST_SCOPE = ""


def _log(message):
    try:
        if script is not None:
            script.get_logger().debug("[Clash Detection Panel] {0}".format(message))
    except Exception:
        pass


# -- rows -----------------------------------------------------------------


class ClashRow(object):
    """One line in the panel.  Plain object: WPF only reads these."""

    def __init__(self, record):
        self.pair_key = record.key
        self.sequence = record.sequence
        self.element_a_title = record.element_a.display_label
        self.element_a_detail = record.element_a.detail_label
        self.element_b_title = record.element_b.display_label
        self.element_b_detail = record.element_b.detail_label


def _observable_collection():
    from System.Collections.ObjectModel import ObservableCollection

    return ObservableCollection[object]()


def _rows():
    global _ROWS
    if _ROWS is None:
        _ROWS = _observable_collection()
    return _ROWS


def _scope_text(session):
    if session is None:
        return ""
    return "{0}  vs  {1}".format(
        session.side_a.model_label or "Current Project",
        session.side_b.model_label or "Current Project",
    )


# -- view wiring ----------------------------------------------------------


def _bind_view(view):
    """Attach a freshly constructed panel/window to the live row collection."""
    global _VIEW
    _VIEW = view
    try:
        view.RowsListView.ItemsSource = _rows()
    except Exception as ex:
        _log("Could not bind rows: {0}".format(ex))
    _apply_text(_LAST_STATUS, _LAST_SCOPE)
    _apply_empty_state()


def _apply_text(status_text, scope_text):
    view = _VIEW
    if view is None:
        return
    try:
        view.StatusText.Text = status_text or ""
    except Exception:
        pass
    try:
        view.ScopeText.Text = scope_text or ""
    except Exception:
        pass


def _apply_empty_state():
    view = _VIEW
    if view is None:
        return
    try:
        from System.Windows import Visibility

        is_empty = _rows().Count == 0
        view.EmptyText.Visibility = Visibility.Visible if is_empty else Visibility.Collapsed
    except Exception:
        pass


def _on_show_click(sender):
    row = getattr(sender, "DataContext", None)
    pair_key = getattr(row, "pair_key", None)
    if not pair_key:
        return
    from easybim import clash_detection_engine

    clash_detection_engine.request_show(pair_key)


def _on_stop_click():
    from easybim import clash_detection_engine

    clash_detection_engine.stop(reason="panel Stop Detection")


def _panel_type():
    """Build the ``forms.WPFPanel`` subclass once, if pyRevit supports it."""
    global _PANEL_TYPE
    if _PANEL_TYPE is not None:
        return _PANEL_TYPE
    if forms is None:
        return None
    base_type = getattr(forms, "WPFPanel", None)
    if base_type is None:
        return None

    class ClashDetectionPanel(base_type):
        panel_title = PANEL_TITLE
        panel_id = PANEL_ID
        panel_source = PANEL_XAML

        def __init__(self):
            base_type.__init__(self)
            _bind_view(self)

        def setup_dockablepane(self, data):
            """Ask Revit to dock on the right the first time it is shown."""
            try:
                from Autodesk.Revit.UI import DockablePaneState
                from Autodesk.Revit.UI import DockPosition

                pane_state = DockablePaneState()
                pane_state.DockPosition = DockPosition.Right
                data.InitialState = pane_state
            except Exception:
                pass

        def show_click(self, sender, args):
            del args
            _on_show_click(sender)

        def stop_click(self, sender, args):
            del sender, args
            _on_stop_click()

    _PANEL_TYPE = ClashDetectionPanel
    return _PANEL_TYPE


def _fallback_window_type():
    if forms is None:
        return None
    base_type = getattr(forms, "WPFWindow", None)
    if base_type is None:
        return None

    class ClashDetectionPanelWindow(base_type):
        def __init__(self):
            base_type.__init__(self, PANEL_WINDOW_XAML)
            self.Topmost = True
            _bind_view(self)

        def show_click(self, sender, args):
            del args
            _on_show_click(sender)

        def stop_click(self, sender, args):
            del sender, args
            _on_stop_click()

    return ClashDetectionPanelWindow


# -- registration ---------------------------------------------------------


def register():
    """Register the dockable pane.  Must run during Revit app init."""
    panel_type = _panel_type()
    if panel_type is None:
        return False
    register_fn = getattr(forms, "register_dockable_panel", None)
    if register_fn is None:
        return False
    try:
        register_fn(panel_type)
    except Exception as ex:
        _log("Dockable panel registration failed: {0}".format(ex))
        return False
    try:
        script.set_envvar(REGISTERED_ENVVAR, True)
    except Exception:
        pass
    return True


def _is_registered():
    try:
        return bool(script.get_envvar(REGISTERED_ENVVAR))
    except Exception:
        return False


# -- open / close ---------------------------------------------------------


def _dock_right(window):
    """Pin the fallback window to the right edge of the Revit window."""
    try:
        import clr

        clr.AddReference("AdWindows")
        import Autodesk.Windows as autodesk_windows

        handle = autodesk_windows.ComponentManager.ApplicationWindow
        from System.Windows.Interop import WindowInteropHelper

        WindowInteropHelper(window).Owner = handle
    except Exception:
        pass
    try:
        from System.Windows import SystemParameters

        window.Left = max(0, SystemParameters.WorkArea.Right - window.Width - 12)
        window.Top = SystemParameters.WorkArea.Top + 120
    except Exception:
        pass


def _open_fallback_window():
    global _FALLBACK_WINDOW
    if _FALLBACK_WINDOW is not None:
        try:
            _FALLBACK_WINDOW.Activate()
            return True
        except Exception:
            _FALLBACK_WINDOW = None
    window_type = _fallback_window_type()
    if window_type is None:
        return False
    try:
        window = window_type()
        _dock_right(window)
        window.Show()
        _FALLBACK_WINDOW = window
        return True
    except Exception as ex:
        _log("Fallback window failed: {0}".format(ex))
        return False


def open_panel(session):
    """Show the panel and load it with the current session."""
    opened = False
    if _is_registered():
        open_fn = getattr(forms, "open_dockable_panel", None)
        if open_fn is not None:
            try:
                open_fn(PANEL_ID)
                opened = True
            except Exception as ex:
                _log("open_dockable_panel failed: {0}".format(ex))
    if not opened:
        opened = _open_fallback_window()
    refresh(session)
    return opened


def close_panel():
    global _FALLBACK_WINDOW
    if _is_registered():
        close_fn = getattr(forms, "close_dockable_panel", None)
        if close_fn is not None:
            try:
                close_fn(PANEL_ID)
            except Exception:
                pass
    if _FALLBACK_WINDOW is not None:
        try:
            _FALLBACK_WINDOW.Close()
        except Exception:
            pass
        _FALLBACK_WINDOW = None
    _clear_rows()
    _apply_text("", "")
    _apply_empty_state()


# -- refresh --------------------------------------------------------------


def _clear_rows():
    global _ROW_KEYS
    try:
        _rows().Clear()
    except Exception:
        pass
    _ROW_KEYS = []


def _apply_rows(records):
    """Reconcile the collection in place so scroll position survives."""
    global _ROW_KEYS

    rows = _rows()
    wanted_keys = [record.key for record in records]
    if wanted_keys == _ROW_KEYS:
        return

    wanted = set(wanted_keys)
    for index in range(rows.Count - 1, -1, -1):
        try:
            if rows[index].pair_key not in wanted:
                rows.RemoveAt(index)
        except Exception:
            pass

    existing = set()
    try:
        for index in range(rows.Count):
            existing.add(rows[index].pair_key)
    except Exception:
        pass

    for record in records:
        if record.key not in existing:
            rows.Add(ClashRow(record))

    _ROW_KEYS = wanted_keys


def refresh(session):
    """Push the session's current state into the panel (UI thread safe)."""
    global _LAST_STATUS, _LAST_SCOPE

    if session is None:
        _LAST_STATUS = ""
        _LAST_SCOPE = ""
        _clear_rows()
        _apply_text("", "")
        _apply_empty_state()
        return

    records = session.records()
    status_text = session.status_text()
    scope_text = _scope_text(session)
    _LAST_STATUS = status_text
    _LAST_SCOPE = scope_text

    def _update():
        _apply_rows(records)
        _apply_text(status_text, scope_text)
        _apply_empty_state()

    view = _VIEW
    dispatcher = getattr(view, "Dispatcher", None) if view is not None else None
    if dispatcher is not None:
        try:
            if not dispatcher.CheckAccess():
                dispatcher.Invoke(_update)
                return
        except Exception:
            pass
    _update()
