# -*- coding: utf-8 -*-
"""WPF for Damper Check: the setup checklist and the modeless report.

No Revit API lives here (test-pinned).  The report draws the dicts
``damper_check_state`` produces, and the two things that must touch the
model - Show and Refresh - are callables the launcher hands in.  A modeless
window's own handlers have no API context, so both ride the
``ExternalEventBridge`` the launcher created while it still had one; when
no bridge could be made the window opens modal and runs them in place.

The checklist is built in code rather than bound to a ListBox because the
user asked for categories that fold: Duct Accessories open on top, every
other category collapsed beneath.  Ticks live on the row dicts, so a search
that rebuilds the panel never loses a choice.
"""

from __future__ import print_function

from pyrevit import forms
from pyrevit import script
from pyrevit.framework import Windows

import damper_check_state as state


TITLE = "Damper Check"
SETUP_XAML = "DamperCheckSetupWindow.xaml"
RESULTS_XAML = "DamperCheckResultsWindow.xaml"

# Must equal script.py's ACTIVE_ENVVAR (test-pinned): the launcher reads it
# to decide whether dropping stale modules is safe.
ACTIVE_ENVVAR = "EASYBIM_DAMPER_CHECK_ACTIVE"
WINDOW_ENVVAR = "EASYBIM_DAMPER_CHECK_WINDOW"

UNREADABLE_LIST_CAP = 50

#: ``(system class key, checkbox x:Name)``
CHIP_CONTROLS = (
    ("SupplyAir", "SupplyCheck"),
    ("ReturnAir", "ReturnCheck"),
    ("ExhaustAir", "ExhaustCheck"),
    ("OtherAir", "OtherCheck"),
    ("Unknown", "UnknownCheck"),
)

LOGGER = script.get_logger()
_WINDOW = None


class DocumentGone(Exception):
    """The document the report was opened for has been closed."""


# ------------------------------------------------------------ wpf helpers


def _brush(name):
    return getattr(Windows.Media.Brushes, name)


def _thickness(*values):
    return Windows.Thickness(*values)


def _text(content, size=None, semibold=False, brush=None, wrap=True, margin=None):
    block = Windows.Controls.TextBlock()
    block.Text = state.safe_text(content)
    if size is not None:
        block.FontSize = size
    if semibold:
        block.FontWeight = Windows.FontWeights.SemiBold
    if brush is not None:
        block.Foreground = brush
    if wrap:
        block.TextWrapping = Windows.TextWrapping.Wrap
    if margin is not None:
        block.Margin = margin
    return block


def _badge(text, background, border, foreground):
    holder = Windows.Controls.Border()
    holder.Background = background
    holder.BorderBrush = border
    holder.BorderThickness = _thickness(1)
    holder.CornerRadius = Windows.CornerRadius(10)
    holder.Padding = _thickness(8, 2, 8, 2)
    holder.Margin = _thickness(8, 0, 0, 0)
    holder.Child = _text(text, size=11, semibold=True, brush=foreground, wrap=False)
    return holder


def _card(child, background=None):
    holder = Windows.Controls.Border()
    holder.BorderBrush = _brush("Gainsboro")
    holder.BorderThickness = _thickness(1)
    holder.Background = background or _brush("White")
    holder.Padding = _thickness(10)
    holder.Margin = _thickness(0, 6, 0, 0)
    holder.Child = child
    return holder


def _parse_threshold(text):
    """The typed limit as an int, or None when it is not one yet."""
    raw = state.safe_text(text).strip()
    if not raw.isdigit():
        return None
    return state.clamp_threshold(raw)


# ----------------------------------------------------------- setup window


class SetupWindow(forms.WPFWindow):
    """Scope, class chips, the damper checklist and the limit N."""

    def __init__(self, catalog, settings, note=u""):
        # XAML attributes such as IsChecked="True" raise their handlers while
        # the window is still being parsed - before this body runs - so every
        # handler reads ``_ready`` through getattr and stands down until then.
        forms.WPFWindow.__init__(self, SETUP_XAML)
        self.result = None
        self._catalog = catalog or {}
        self._settings = dict(settings or {})
        self._rows = state.preselect_types(self._catalog.get("types") or [], self._settings)
        self._expanded = {}
        self._checkboxes = []

        view = self._catalog.get("view") or {}
        if not view.get("is_graphical"):
            self.ScopeViewRadio.IsEnabled = False
            self.ScopeViewRadio.ToolTip = (
                u"The active view cannot show ducts (a schedule, sheet or template) - "
                u"open a plan, section or 3D view to scope to it.")
        else:
            self.ScopeViewRadio.Content = u"Air terminals in the active view ({0})".format(
                view.get("name") or u"active view")
        if self._settings.get("scope") == state.SCOPE_VIEW and view.get("is_graphical"):
            self.ScopeViewRadio.IsChecked = True
        else:
            self.ScopeModelRadio.IsChecked = True

        chosen = set(self._settings.get("system_classes") or state.SYSTEM_CLASS_KEYS)
        for key, control_name in CHIP_CONTROLS:
            getattr(self, control_name).IsChecked = key in chosen

        self.ThresholdBox.Text = u"{0}".format(
            state.clamp_threshold(self._settings.get("threshold", state.DEFAULT_THRESHOLD)))

        if self._catalog.get("meta", {}).get("probe_truncated"):
            note = (note + u" " if note else u"") + (
                u"Listing other categories stopped at its time budget; "
                u"the four duct categories are complete.")
        if note:
            self.StatusText.Text = note
        self._ready = True
        self._populate_types()

    # -- checklist ---------------------------------------------------------

    def _visible_rows(self):
        query = state.safe_text(self.TypeSearchBox.Text).strip().lower()
        if not query:
            return list(self._rows)
        return [row for row in self._rows
                if query in state.safe_text(row.get("type_key")).lower()
                or query in state.safe_text(row.get("category")).lower()]

    def _populate_types(self):
        self.TypePanel.Children.Clear()
        self._checkboxes = []
        visible = self._visible_rows()
        groups = state.group_types(visible)
        if not groups:
            self.TypePanel.Children.Add(_text(
                u"No family with duct connectors matches." if self._rows
                else u"This model has no family with duct connectors - nothing to tick.",
                brush=_brush("DimGray"), margin=_thickness(4)))
        for group in groups:
            self.TypePanel.Children.Add(self._group_expander(group))
        self._update_count()

    def _group_expander(self, group):
        category = group["category"]
        ticked = len([row for row in group["rows"] if row.get("is_checked")])
        expander = Windows.Controls.Expander()
        expander.Margin = _thickness(0, 0, 0, 6)
        header = Windows.Controls.DockPanel()
        header.LastChildFill = True
        badge = _badge(u"{0} ticked".format(ticked) if ticked else u"none ticked",
                       _brush("AliceBlue") if ticked else _brush("WhiteSmoke"),
                       _brush("LightSteelBlue") if ticked else _brush("Gainsboro"),
                       _brush("SteelBlue") if ticked else _brush("Gray"))
        Windows.Controls.DockPanel.SetDock(badge, Windows.Controls.Dock.Right)
        header.Children.Add(badge)
        header.Children.Add(_text(
            u"{0}  ({1} type{2} · {3} instance{4})".format(
                category, group["type_count"], u"" if group["type_count"] == 1 else u"s",
                group["instance_count"], u"" if group["instance_count"] == 1 else u"s"),
            size=13, semibold=True, wrap=False))
        expander.Header = header
        expander.IsExpanded = self._expanded.get(category, bool(group.get("is_expanded")))
        expander.Expanded += self._make_expansion_handler(category, True)
        expander.Collapsed += self._make_expansion_handler(category, False)

        stack = Windows.Controls.StackPanel()
        for row in group["rows"]:
            stack.Children.Add(self._type_row(row))
        expander.Content = _card(stack)
        return expander

    def _type_row(self, row):
        line = Windows.Controls.StackPanel()
        line.Orientation = Windows.Controls.Orientation.Horizontal
        line.Margin = _thickness(0, 2, 0, 2)
        checkbox = Windows.Controls.CheckBox()
        checkbox.IsChecked = bool(row.get("is_checked"))
        checkbox.Tag = row
        checkbox.VerticalAlignment = Windows.VerticalAlignment.Center
        checkbox.Margin = _thickness(2, 0, 8, 0)
        checkbox.Checked += self._type_toggled
        checkbox.Unchecked += self._type_toggled
        line.Children.Add(checkbox)
        line.Children.Add(_text(row.get("type_key"), wrap=False))
        note = u"  ({0})".format(row.get("count", 0))
        reason = state.safe_text(row.get("reason"))
        if reason:
            note += u"  " + reason
        line.Children.Add(_text(note, size=11, brush=_brush("DimGray"), wrap=False))
        self._checkboxes.append(checkbox)
        return line

    def _make_expansion_handler(self, category, is_expanded):
        def _handler(sender, args):
            del sender, args
            self._expanded[category] = bool(is_expanded)
        return _handler

    def _type_toggled(self, sender, args):
        del args
        row = getattr(sender, "Tag", None)
        if row is None:
            return
        row["is_checked"] = bool(sender.IsChecked)
        self._update_count()

    def _update_count(self):
        self.TypeCountText.Text = state.selection_count_text(self._rows)

    def _set_visible(self, is_checked):
        for row in self._visible_rows():
            row["is_checked"] = bool(is_checked)
        self._populate_types()

    # -- handlers ----------------------------------------------------------

    def _is_ready(self):
        return bool(getattr(self, "_ready", False))

    def type_search_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._populate_types()

    def type_all_click(self, sender, args):
        del sender, args
        self._set_visible(True)

    def type_none_click(self, sender, args):
        del sender, args
        self._set_visible(False)

    def scope_changed(self, sender, args):
        del sender, args

    def chip_changed(self, sender, args):
        del sender, args
        if not self._is_ready():
            return
        note = state.excluded_note({"system_classes": self._chosen_classes()})
        self.StatusText.Text = note or u"All duct system classes are in scope."

    def threshold_changed(self, sender, args):
        del sender, args
        if not self._is_ready():
            return
        if _parse_threshold(self.ThresholdBox.Text) is None:
            self.StatusText.Text = u"The limit must be a whole number of 1 or more."
        else:
            self.StatusText.Text = u""

    def _chosen_classes(self):
        return [key for key, control_name in CHIP_CONTROLS
                if bool(getattr(self, control_name).IsChecked)]

    def check_click(self, sender, args):
        del sender, args
        threshold = _parse_threshold(self.ThresholdBox.Text)
        if threshold is None:
            self.StatusText.Text = u"The limit must be a whole number of 1 or more."
            return
        classes = self._chosen_classes()
        if not classes:
            self.StatusText.Text = u"Tick at least one duct system class."
            return
        if not any(row.get("is_checked") for row in self._rows):
            self.StatusText.Text = (u"No family type is ticked as a damper - every terminal "
                                    u"would be reported. Tick the dampers first.")
            return
        scope = state.SCOPE_VIEW if bool(self.ScopeViewRadio.IsChecked) else state.SCOPE_MODEL
        self.result = state.apply_setup(self._settings, self._rows, threshold=threshold,
                                        scope=scope, system_classes=classes)
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


# --------------------------------------------------------- results window


class ResultsWindow(forms.WPFWindow):
    """The report: one expander per bucket, Show per row, live N."""

    def __init__(self, analysis, config, settings, bridge=None, uiapp=None,
                 rescan=None, show=None, save_settings=None):
        # See SetupWindow: handlers can fire mid-parse, before ``_ready`` exists.
        forms.WPFWindow.__init__(self, RESULTS_XAML)
        self._analysis = analysis or {}
        self._config = config or {}
        self._settings = dict(settings or {})
        self._bridge = bridge
        self._uiapp = uiapp
        self._rescan = rescan
        self._show = show
        self._save_settings = save_settings
        self._threshold = state.clamp_threshold(self._config.get("threshold", state.DEFAULT_THRESHOLD))
        self._expanded = {}
        self._show_buttons = []
        self._busy = False
        self._closing = False
        self._updating_threshold = False

        if self._bridge is not None:
            self._bridge.on_error = self._on_bridge_error
            self._bridge.on_idle = self._on_bridge_idle
        self.Closed += self._on_closed

        self._updating_threshold = True
        self.ThresholdBox.Text = u"{0}".format(self._threshold)
        self._updating_threshold = False
        self._ready = True
        self._render()

    def _is_ready(self):
        return bool(getattr(self, "_ready", False))

    # -- rendering ---------------------------------------------------------

    def _render(self):
        report = state.classify(self._analysis, self._threshold)
        query = state.safe_text(self.SearchBox.Text)
        shown = state.filter_report(report, query) if query.strip() else report

        self.ContentPanel.Children.Clear()
        self._show_buttons = []
        buckets = [bucket for bucket in shown["buckets"] if bucket["items"]]
        if not buckets:
            if query.strip():
                message = u"Nothing matches '{0}'.".format(query.strip())
            elif report["terminal_count"] == 0:
                message = u"No air terminal is in scope. Widen the scope or the class chips and Refresh."
            else:
                message = u"All {0} terminals in scope are isolated.".format(report["terminal_count"])
            self._set_empty(True, message)
        else:
            self._set_empty(False, u"")
            for bucket in buckets:
                self.ContentPanel.Children.Add(self._bucket_expander(bucket))
        self.ContentPanel.Children.Add(self._notes_expander())

        self.SummaryText.Text = state.summary_line(self._analysis, report)
        if not self._busy:
            self._update_status()

    def _set_empty(self, is_visible, text):
        self.EmptyText.Visibility = Windows.Visibility.Visible if is_visible else Windows.Visibility.Collapsed
        self.ContentScroll.Visibility = Windows.Visibility.Collapsed if is_visible else Windows.Visibility.Visible
        self.EmptyText.Text = state.safe_text(text)

    def _bucket_expander(self, bucket):
        key = bucket["key"]
        expander = Windows.Controls.Expander()
        expander.Margin = _thickness(0, 0, 0, 10)
        header = Windows.Controls.DockPanel()
        header.LastChildFill = True
        problem = bool(bucket.get("is_problem"))
        badge = _badge(u"{0} terminal{1}".format(len(bucket["items"]), u"" if len(bucket["items"]) == 1 else u"s"),
                       _brush("MistyRose") if problem else _brush("Honeydew"),
                       _brush("RosyBrown") if problem else _brush("DarkSeaGreen"),
                       _brush("Firebrick") if problem else _brush("DarkGreen"))
        Windows.Controls.DockPanel.SetDock(badge, Windows.Controls.Dock.Right)
        header.Children.Add(badge)
        header.Children.Add(_text(bucket["title"], size=15, semibold=True, wrap=False))
        expander.Header = header
        expander.IsExpanded = self._expanded.get(key, problem)
        expander.Expanded += self._make_expansion_handler(key, True)
        expander.Collapsed += self._make_expansion_handler(key, False)

        stack = Windows.Controls.StackPanel()
        items, hidden = state.cap_items(bucket["items"])
        for item in items:
            stack.Children.Add(self._row(item))
        if hidden:
            stack.Children.Add(_text(u"… {0} more - search to narrow.".format(hidden),
                                     brush=_brush("DimGray"), margin=_thickness(4)))
        expander.Content = _card(stack)
        return expander

    def _row(self, item):
        holder = Windows.Controls.Border()
        holder.BorderBrush = _brush("Gainsboro")
        holder.BorderThickness = _thickness(1)
        holder.Background = _brush("WhiteSmoke")
        holder.Padding = _thickness(10, 8, 10, 8)
        holder.Margin = _thickness(0, 0, 0, 6)

        panel = Windows.Controls.DockPanel()
        panel.LastChildFill = True
        button = Windows.Controls.Button()
        button.Content = u"Show"
        button.Width = 80
        button.Height = 28
        button.Margin = _thickness(10, 0, 0, 0)
        button.VerticalAlignment = Windows.VerticalAlignment.Center
        button.Tag = item
        button.ToolTip = u"Select the run in the model and zoom to it."
        button.Click += self._show_click
        button.IsEnabled = not self._busy
        Windows.Controls.DockPanel.SetDock(button, Windows.Controls.Dock.Right)
        panel.Children.Add(button)
        self._show_buttons.append(button)

        lines = Windows.Controls.StackPanel()
        lines.Children.Add(_text(item.get("title"), semibold=True))
        detail = state.safe_text(item.get("detail"))
        if item.get("approx"):
            detail += u"  (network has loops - counts approximate)"
        if detail:
            lines.Children.Add(_text(detail, size=12, brush=_brush("DimGray"), margin=_thickness(0, 2, 0, 0)))
        panel.Children.Add(lines)
        holder.Child = panel
        return holder

    def _notes_expander(self):
        """Scan notes: named skips and the honest limits, never a crash."""
        analysis = self._analysis
        skips = analysis.get("skips") or {}
        stats = analysis.get("stats") or {}
        meta = analysis.get("meta") or {}
        notes = []
        unreadable = list(skips.get("unreadable_ids") or [])
        if unreadable:
            listed = u", ".join(u"{0}".format(value) for value in unreadable[:UNREADABLE_LIST_CAP])
            more = u" … and {0} more".format(len(unreadable) - UNREADABLE_LIST_CAP) \
                if len(unreadable) > UNREADABLE_LIST_CAP else u""
            notes.append(u"Connectors unreadable on {0} element(s) - not judged: {1}{2}".format(
                len(unreadable), listed, more))
        if skips.get("out_of_scope_terminals"):
            notes.append(u"{0} terminal(s) outside the active view were traced but not reported.".format(
                skips["out_of_scope_terminals"]))
        if skips.get("class_filtered_terminals"):
            notes.append(u"{0} terminal(s) left out by the class chips.".format(
                skips["class_filtered_terminals"]))
        excluded = state.excluded_note(self._config)
        if excluded:
            notes.append(excluded)
        for group in analysis.get("groups") or []:
            if group.get("has_loops"):
                notes.append(u"Network rooted at {0} (id {1}) has loops - terminal counts are approximate.".format(
                    group.get("root_label"), group.get("root_id")))
            if group.get("truncated"):
                notes.append(u"Network rooted at {0} (id {1}) was cut by the node guard.".format(
                    group.get("root_label"), group.get("root_id")))
            if group.get("root_kind") == state.ROOT_OPEN_END:
                notes.append(u"Network rooted at open duct {0} (id {1}) reaches no equipment - "
                             u"a branch not connected to its main, or a system left unassigned.".format(
                                 group.get("root_label"), group.get("root_id")))
        if stats.get("idle_dampers"):
            notes.append(u"{0} ticked damper(s) isolate no terminal (dead legs or spare taps).".format(
                stats["idle_dampers"]))
        if stats.get("pulled_in"):
            notes.append(u"{0} element(s) from other categories joined the network through their connectors.".format(
                stats["pulled_in"]))
        if meta.get("truncated"):
            notes.append(u"The scan stopped early: {0}. Elements not read are not judged.".format(
                meta.get("truncated_reason") or u"budget reached"))
        if meta.get("scope_note"):
            notes.append(state.safe_text(meta.get("scope_note")))
        notes.append(u"Linked models are not traversed. Nothing in the model is changed.")

        expander = Windows.Controls.Expander()
        expander.Margin = _thickness(0, 0, 0, 10)
        expander.Header = _text(u"Scan notes ({0})".format(len(notes)), size=15, semibold=True, wrap=False)
        expander.IsExpanded = self._expanded.get("notes", False)
        expander.Expanded += self._make_expansion_handler("notes", True)
        expander.Collapsed += self._make_expansion_handler("notes", False)
        stack = Windows.Controls.StackPanel()
        for note in notes:
            stack.Children.Add(_text(u"• " + note, margin=_thickness(0, 2, 0, 2)))
        expander.Content = _card(stack, background=_brush("WhiteSmoke"))
        return expander

    def _make_expansion_handler(self, key, is_expanded):
        def _handler(sender, args):
            del sender, args
            self._expanded[key] = bool(is_expanded)
        return _handler

    def _update_status(self):
        if self._bridge is None:
            self.StatusText.Text = (u"Opened without an ExternalEvent, so this window is modal; "
                                    u"Show and Refresh still work.")
        else:
            self.StatusText.Text = u"Limit {0}. Nothing was changed.".format(self._threshold)

    # -- threshold -----------------------------------------------------------

    def _set_threshold(self, value):
        value = state.clamp_threshold(value)
        self._threshold = value
        self._updating_threshold = True
        try:
            self.ThresholdBox.Text = u"{0}".format(value)
        finally:
            self._updating_threshold = False
        self._settings["threshold"] = value
        self._render()

    def threshold_changed(self, sender, args):
        del sender, args
        if not self._is_ready() or self._updating_threshold:
            return
        value = _parse_threshold(self.ThresholdBox.Text)
        if value is None:
            self.StatusText.Text = u"The limit must be a whole number of 1 or more."
            return
        self._threshold = value
        self._settings["threshold"] = value
        self._render()

    def threshold_down_click(self, sender, args):
        del sender, args
        self._set_threshold(self._threshold - 1)

    def threshold_up_click(self, sender, args):
        del sender, args
        self._set_threshold(self._threshold + 1)

    # -- search / expanders ------------------------------------------------

    def search_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._render()

    def expand_all_click(self, sender, args):
        del sender, args
        for key, _title, _problem in state.BUCKETS:
            self._expanded[key] = True
        self._expanded["notes"] = True
        self._render()

    def collapse_all_click(self, sender, args):
        del sender, args
        for key, _title, _problem in state.BUCKETS:
            self._expanded[key] = False
        self._expanded["notes"] = False
        self._render()

    # -- revit bridge plumbing -----------------------------------------------

    def _run_in_revit(self, label, work, on_done=None, quiet=False):
        """Run ``work(uiapp)`` then ``on_done(result)`` in API context.

        Both run inside the ExternalEvent's Execute (Revit's main thread,
        which is also this window's thread).  Without a bridge (modal
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
            forms.alert(u"Revit could not take the request right now (it may be inside "
                        u"another command). Finish what Revit is doing and try again.",
                        title=TITLE)
            return False
        return True

    def _set_busy(self, busy, label=u""):
        self._busy = bool(busy)
        for button in (self.RefreshButton,) + tuple(self._show_buttons):
            try:
                button.IsEnabled = not self._busy
            except Exception:
                pass
        if self._busy:
            self.StatusText.Text = u"Waiting for Revit - {0}...".format(label)
        else:
            self._update_status()

    def _on_bridge_idle(self):
        if not self._closing:
            self._set_busy(False)

    def _on_bridge_error(self, label, error):
        if isinstance(error, DocumentGone):
            forms.alert(u"The document this report was opened for has been closed. "
                        u"Damper Check will close.", title=TITLE)
            self._closing = True
            try:
                self.Close()
            except Exception:
                pass
            return
        LOGGER.warning("Damper Check %s failed: %s", label, error)
        self.StatusText.Text = u"{0} failed: {1}".format(label, state.safe_text(error) or u"unknown error")

    # -- actions -------------------------------------------------------------

    def _show_click(self, sender, args):
        del args
        item = getattr(sender, "Tag", None)
        if item is None or self._show is None:
            return
        ids = list(item.get("show_ids") or [])
        if not ids:
            self.StatusText.Text = u"Nothing to show for this row."
            return

        def _work(uiapp):
            return self._show(uiapp, ids)

        def _done(shown):
            if shown:
                self.StatusText.Text = u"Selected {0} element(s) of the run.".format(len(ids))
            else:
                self.StatusText.Text = (u"Could not select the run - the elements may be hidden "
                                        u"in the active view.")

        self._run_in_revit(u"Show", _work, _done, quiet=True)

    def refresh_click(self, sender, args):
        del sender, args
        if self._rescan is None:
            return
        self._run_in_revit(u"Refresh", self._rescan, self._refresh_done)

    def _refresh_done(self, analysis):
        if analysis:
            self._analysis = analysis
        self._render()

    def close_click(self, sender, args):
        del sender, args
        self.Close()

    def _on_closed(self, sender, args):
        del sender, args
        self._closing = True
        if self._save_settings is not None:
            try:
                self._save_settings(self._settings)
            except Exception:
                pass
        _forget_window(self)
        if self._bridge is not None:
            try:
                self._bridge.dispose()
            except Exception:
                pass


# ---------------------------------------------------------------- launcher


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


def close_open_window():
    """A new check replaces the previous report; returns True if one closed."""
    window = _live_window()
    if window is None:
        _forget_window()
        return False
    try:
        window.Close()
    except Exception:
        pass
    _forget_window()
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


def show_results(analysis, config, settings, bridge=None, uiapp=None, rescan=None,
                 show=None, save_settings=None):
    """Open the report modeless; modal when no ExternalEvent could be made."""
    close_open_window()
    window = ResultsWindow(analysis, config, settings, bridge=bridge, uiapp=uiapp,
                           rescan=rescan, show=show, save_settings=save_settings)
    if bridge is None:
        window.ShowDialog()
        return window
    _remember_window(window)
    window.Show()
    return window
