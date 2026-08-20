# -*- coding: utf-8 -*-
"""Right-side dockable pane for Circuit Schedule.

Revit only accepts ``RegisterDockablePane`` while the application is
initialising, so :func:`register` runs from ``startup.py`` on every session.
Registration alone is free - the pane stays hidden and holds no state until
:func:`open_panel` is called by the pushbutton.  Where the host's pyRevit build
has no dockable-pane support the same content opens as a modeless window pinned
to the right of the Revit window, exactly as Clash Detection degrades.

The pane outlives the command that opened it, so Revit refuses selection calls
made from its own handlers.  Every model call therefore goes through an
``ExternalEventBridge`` created by ``script.py`` while it is still in a valid
API context.

The view, the scan and the bridge are all mirrored into pyRevit envvars: an
extension update re-imports this module while Revit keeps the pane instance
built from the *previous* module object, whose ``__init__`` - the only place
that binds it - never runs again.
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

from easybim import circuit_schedule_state as state


PANEL_TITLE = "EasyBIM Circuit Schedule"
PANEL_ID = "6f2b91d4-30ac-4f57-9c8e-51b7ad04e6c2"

REGISTERED_ENVVAR = "EASYBIM_CIRCUIT_SCHEDULE_REGISTERED"
VIEW_ENVVAR = "EASYBIM_CIRCUIT_SCHEDULE_VIEW"
SCAN_ENVVAR = "EASYBIM_CIRCUIT_SCHEDULE_SCAN"
BRIDGE_ENVVAR = "EASYBIM_CIRCUIT_SCHEDULE_BRIDGE"

UI_DIR = os.path.join(os.path.dirname(__file__), "ui")
PANEL_XAML = os.path.join(UI_DIR, "circuit_schedule_panel.xaml")
PANEL_WINDOW_XAML = os.path.join(UI_DIR, "circuit_schedule_panel_window.xaml")

#: Past this many hits, a filter stops opening ancestors.  A broad query would
#: otherwise realize a TreeViewItem for every row in the project at once.
EXPAND_LIMIT = 200

EMPTY_TEXT = u"This model has no electrical circuits."
NO_MATCH_TEXT = u"Nothing matches that search."
LOST_BRIDGE_TEXT = u"Lost the Revit connection - press Circuit Schedule again."

_PANEL_TYPE = None
_VIEW = None
_SCAN = None
_BRIDGE = None
_FALLBACK_WINDOW = None
_ROOTS = []
_INDEX = {}
_PARENTS = {}
_SELECTED = None
_QUERY = u""


def _log(message):
    try:
        if script is not None:
            script.get_logger().debug("[Circuit Schedule] {0}".format(message))
    except Exception:
        pass


def _get_envvar(name):
    try:
        return script.get_envvar(name)
    except Exception:
        return None


def _set_envvar(name, value):
    try:
        script.set_envvar(name, value)
    except Exception:
        pass


# -- rows -----------------------------------------------------------------


try:
    from easybim.wpf_notify import NotifyingObject as _NotifyBase

    _NOTIFY_SUPPORTED = True
except Exception:
    # A stand-in class, not ``object``: PanelNode also inherits state.Node, and
    # ``(object, Node)`` is not a resolvable base order.  This keeps the module
    # importable off Revit, where wpf_notify's ``import clr`` cannot run.
    class _NotifyBase(object):
        def raise_property_changed(self, property_name):
            pass

    _NOTIFY_SUPPORTED = False


class PanelNode(_NotifyBase, state.Node):
    """A tree row that tells WPF when it moves.

    Notification is not optional here: ``is_expanded`` is TwoWay-bound and
    written from code (a search opens the ancestors of every hit, Collapse All
    closes the lot), and ``is_visible`` and ``matched`` change on every
    keystroke in the search box.
    """

    def __init__(self, *args, **kwargs):
        if _NOTIFY_SUPPORTED:
            _NotifyBase.__init__(self)
        state.Node.__init__(self, *args, **kwargs)

    def notify(self, attr):
        if not _NOTIFY_SUPPORTED:
            return
        try:
            self.raise_property_changed(attr)
        except Exception:
            pass


# -- the live view --------------------------------------------------------


def _view():
    """The pane on screen, even when a previous module load built it."""
    if _VIEW is not None:
        return _VIEW
    return _get_envvar(VIEW_ENVVAR)


def _scan():
    if _SCAN is not None:
        return _SCAN
    return _get_envvar(SCAN_ENVVAR)


def _bridge():
    if _BRIDGE is not None:
        return _BRIDGE
    return _get_envvar(BRIDGE_ENVVAR)


def attach_bridge(bridge):
    """Hand the pane an ExternalEvent created in a valid API context."""
    global _BRIDGE
    _BRIDGE = bridge
    _set_envvar(BRIDGE_ENVVAR, bridge)


def has_ready_bridge():
    bridge = _bridge()
    try:
        return bool(bridge is not None and bridge.is_ready())
    except Exception:
        return False


def _set_text(name, text):
    view = _view()
    if view is None:
        return
    try:
        getattr(view, name).Text = text or u""
    except Exception:
        pass


def _set_visible(name, is_visible):
    view = _view()
    if view is None:
        return
    try:
        from System.Windows import Visibility

        getattr(view, name).Visibility = \
            Visibility.Visible if is_visible else Visibility.Collapsed
    except Exception:
        pass


def _bind_view(view):
    """Attach a freshly built pane or window to whatever is already loaded."""
    global _VIEW
    _VIEW = view
    _set_envvar(VIEW_ENVVAR, view)
    scan = _scan()
    if scan is not None:
        load_scan(scan)
    else:
        _repaint()


def _repaint():
    view = _view()
    if view is None:
        return
    try:
        view.TreeRoot.ItemsSource = None
        view.TreeRoot.ItemsSource = _ROOTS
    except Exception as ex:
        _log("Could not bind the tree: {0}".format(ex))
    _refresh_empty_state()
    _set_text("StatusText", _status_text())
    _update_buttons()


def _visible_count():
    return sum(1 for node in state.iter_nodes(_ROOTS) if node.is_visible)


def _refresh_empty_state():
    if not _ROOTS:
        _set_text("EmptyText", EMPTY_TEXT)
        _set_visible("EmptyText", True)
    elif not _visible_count():
        _set_text("EmptyText", NO_MATCH_TEXT)
        _set_visible("EmptyText", True)
    else:
        _set_visible("EmptyText", False)


def _status_text():
    scan = _scan()
    if scan is None:
        return u""
    text = state.summarize(scan, _ROOTS)
    title = scan.get("doc_title")
    if title:
        text = u"{0}  -  read from {1}".format(text, title)
    return text


def _update_buttons():
    view = _view()
    if view is None:
        return
    ready = has_ready_bridge()
    for name in ("ShowButton", "RefreshButton"):
        try:
            getattr(view, name).IsEnabled = ready
        except Exception:
            pass


# -- loading --------------------------------------------------------------


def load_scan(scan):
    """Rebuild the tree from a fresh model scan, keeping the user's place."""
    global _ROOTS, _INDEX, _PARENTS, _SELECTED, _SCAN

    _SCAN = scan
    _set_envvar(SCAN_ENVVAR, scan)
    saved = state.expansion_state(_ROOTS) if _ROOTS else None
    _ROOTS, _INDEX, _PARENTS = state.build_tree(scan, node_factory=PanelNode)
    _SELECTED = None
    if saved:
        state.restore_expansion(_ROOTS, saved)
    _apply_query(_QUERY)
    _repaint()
    _set_text("PathText", u"")


def _apply_query(query):
    """Filter the tree, and say how many rows answered."""
    global _QUERY
    _QUERY = query or u""
    if not _ROOTS:
        return
    count = state.apply_filter(_ROOTS, _QUERY)
    if not _QUERY:
        return
    if count > EXPAND_LIMIT:
        # Opening the ancestors of thousands of hits realizes a TreeViewItem
        # for most of the project.  Show the pruned tree closed instead.
        state.set_expanded(_ROOTS, False)


def _search_status(count):
    if not _QUERY:
        return _status_text()
    if not count:
        return u"No match"
    text = u"{0} match(es)".format(count)
    if count > EXPAND_LIMIT:
        text += u"  -  too many to open; expand what you need"
    return text


# -- actions --------------------------------------------------------------


def _selected_node():
    view = _view()
    if view is None:
        return None
    try:
        return view.TreeRoot.SelectedItem
    except Exception:
        return None


def _on_selection_changed():
    global _SELECTED
    _SELECTED = _selected_node()
    _set_text("PathText", state.breadcrumb(_SELECTED, _PARENTS))


def _on_search_changed():
    view = _view()
    if view is None:
        return
    try:
        query = view.SearchBox.Text
    except Exception:
        query = u""
    _apply_query(query)
    count = sum(1 for node in state.iter_nodes(_ROOTS) if node.matched)
    _refresh_empty_state()
    _set_text("StatusText", _search_status(count))


def _on_clear_search():
    view = _view()
    if view is None:
        return
    try:
        view.SearchBox.Text = u""       # raises TextChanged, which does the work
        view.SearchBox.Focus()
    except Exception:
        pass


def _on_set_expanded(value):
    state.set_expanded(_ROOTS, value)


def _on_show(node=None):
    """Select and frame the row's elements in the active Revit view."""
    node = node or _SELECTED or _selected_node()
    element_ids = list(getattr(node, "element_ids", None) or [])
    if not element_ids:
        return
    bridge = _bridge()
    if bridge is None:
        _set_text("StatusText", LOST_BRIDGE_TEXT)
        return

    scan = _scan() or {}
    doc_key = scan.get("doc_key")

    def work(uiapp):
        from easybim import circuit_schedule_revit as crevit

        uidoc = getattr(uiapp, "ActiveUIDocument", None)
        if uidoc is None:
            return u"No project document is open."
        if doc_key and crevit.document_key(uidoc.Document) != doc_key:
            return _stale_text(scan)
        crevit.show_elements(uidoc, element_ids)
        return u""

    _run(bridge, work, "Circuit Schedule Show")


def _on_refresh():
    bridge = _bridge()
    if bridge is None:
        _set_text("StatusText", LOST_BRIDGE_TEXT)
        return

    def work(uiapp):
        from easybim import circuit_schedule_revit as crevit

        uidoc = getattr(uiapp, "ActiveUIDocument", None)
        if uidoc is None:
            return None
        return crevit.scan_model(uidoc.Document)

    def done(result):
        if result is None:
            _set_text("StatusText", u"No project document is open.")
            return
        load_scan(result)

    _run(bridge, work, "Circuit Schedule Refresh", on_done=done)


def _stale_text(scan):
    return u"This tree was read from {0}. Press Refresh to read the open " \
           u"model.".format(scan.get("doc_title") or u"another model")


def _run(bridge, work, label, on_done=None):
    """Queue Revit work; ``on_done`` runs on the same thread inside Execute."""
    def finish(result):
        if on_done is not None:
            on_done(result)
        elif isinstance(result, type(u"")) and result:
            _set_text("StatusText", result)

    try:
        accepted = bridge.run(work, on_done=finish, label=label)
    except Exception as ex:
        _log("{0} failed: {1}".format(label, ex))
        accepted = False
    if not accepted:
        _set_text("StatusText", LOST_BRIDGE_TEXT)
        _update_buttons()


# -- view types -----------------------------------------------------------


class _Handlers(object):
    """The handler set both views share, so the pair cannot drift."""

    def search_changed(self, sender, args):
        del sender, args
        _on_search_changed()

    def search_key_down(self, sender, args):
        del sender
        try:
            from System.Windows.Input import Key

            if args.Key == Key.Escape:
                _on_clear_search()
            elif args.Key == Key.Enter:
                _on_show(_first_match())
        except Exception:
            pass

    def clear_search_click(self, sender, args):
        del sender, args
        _on_clear_search()

    def tree_selection_changed(self, sender, args):
        del sender, args
        _on_selection_changed()

    def tree_double_click(self, sender, args):
        del sender, args
        _on_show()

    def expand_all_click(self, sender, args):
        del sender, args
        _on_set_expanded(True)

    def collapse_all_click(self, sender, args):
        del sender, args
        _on_set_expanded(False)

    def show_click(self, sender, args):
        del sender, args
        _on_show()

    def refresh_click(self, sender, args):
        del sender, args
        _on_refresh()


def _first_match():
    for node in _pre_order(_ROOTS):
        if node.matched:
            return node
    return None


def _pre_order(roots):
    for node in roots or []:
        yield node
        for child in _pre_order(node.children):
            yield child


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

    class CircuitSchedulePanel(base_type, _Handlers):
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

    _PANEL_TYPE = CircuitSchedulePanel
    return _PANEL_TYPE


def _fallback_window_type():
    if forms is None:
        return None
    base_type = getattr(forms, "WPFWindow", None)
    if base_type is None:
        return None

    class CircuitScheduleWindow(base_type, _Handlers):
        def __init__(self):
            base_type.__init__(self, PANEL_WINDOW_XAML)
            _bind_view(self)

    return CircuitScheduleWindow


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
    _set_envvar(REGISTERED_ENVVAR, True)
    return True


def _is_registered():
    return bool(_get_envvar(REGISTERED_ENVVAR))


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


def open_panel(scan=None):
    """Show the pane and load it with a fresh scan."""
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
    if scan is not None:
        load_scan(scan)
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
    # The view mirror is deliberately left alone: Revit keeps the dockable pane
    # instance alive when it is closed, and its __init__ - the only place that
    # binds it - never runs again.  Every write to it is already guarded.
