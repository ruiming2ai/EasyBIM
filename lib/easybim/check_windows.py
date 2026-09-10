# -*- coding: utf-8 -*-
"""WPF plumbing the checkers share: a folding checklist, report expanders,
a modeless window that reaches Revit through the ExternalEvent bridge, and
the one-window registry a persistent engine needs.

No Revit API here (pinned by the consumers' tests): the windows draw plain
dicts, and anything that must touch the model is a callable the launcher
hands in.  A modeless window's own handlers have no API context, so that
work rides ``easybim.external_events.ExternalEventBridge``; when no bridge
could be made the window opens modal and runs it in place.

Hoisted out of Damper Check when Fire Damper Check became the second
consumer.
"""

from __future__ import print_function

from pyrevit import forms
from pyrevit import script
from pyrevit.framework import Windows

from easybim.type_checklist import cap_items
from easybim.type_checklist import safe_text


LOGGER = script.get_logger()


class DocumentGone(Exception):
    """The document the report was opened for has been closed."""


# ------------------------------------------------------------ primitives


def brush(name):
    return getattr(Windows.Media.Brushes, name)


def thickness(*values):
    return Windows.Thickness(*values)


def text_block(content, size=None, semibold=False, foreground=None, wrap=True, margin=None):
    block = Windows.Controls.TextBlock()
    block.Text = safe_text(content)
    if size is not None:
        block.FontSize = size
    if semibold:
        block.FontWeight = Windows.FontWeights.SemiBold
    if foreground is not None:
        block.Foreground = foreground
    if wrap:
        block.TextWrapping = Windows.TextWrapping.Wrap
    if margin is not None:
        block.Margin = margin
    return block


def badge(text, background, border, foreground):
    holder = Windows.Controls.Border()
    holder.Background = background
    holder.BorderBrush = border
    holder.BorderThickness = thickness(1)
    holder.CornerRadius = Windows.CornerRadius(10)
    holder.Padding = thickness(8, 2, 8, 2)
    holder.Margin = thickness(8, 0, 0, 0)
    holder.Child = text_block(text, size=11, semibold=True, foreground=foreground, wrap=False)
    return holder


def card(child, background=None):
    holder = Windows.Controls.Border()
    holder.BorderBrush = brush("Gainsboro")
    holder.BorderThickness = thickness(1)
    holder.Background = background or brush("White")
    holder.Padding = thickness(10)
    holder.Margin = thickness(0, 6, 0, 0)
    holder.Child = child
    return holder


def parse_positive_int(text, clamp=None):
    """The typed number as an int >= 1, or None when it is not one yet."""
    raw = safe_text(text).strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < 1:
        return None
    return clamp(value) if clamp is not None else value


def expansion_handler(store, key, is_expanded):
    """A WPF handler that remembers an expander's state in ``store``."""
    def _handler(sender, args):
        del sender, args
        store[key] = bool(is_expanded)
    return _handler


def dock_right(panel, control):
    Windows.Controls.DockPanel.SetDock(control, Windows.Controls.Dock.Right)
    panel.Children.Add(control)


# ------------------------------------------------------------- checklist


def default_row_label(row):
    """``(main text, dim note)`` for a checklist row."""
    main = safe_text(row.get("type_key") if row.get("type_key") is not None else row.get("label"))
    note = u"  ({0})".format(row.get("count", 0))
    reason = safe_text(row.get("reason"))
    if reason:
        note += u"  " + reason
    return main, note


class ChecklistPanel(object):
    """Grouped expanders of checkbox rows inside a StackPanel.

    Ticks live on the row dicts, so a search that rebuilds the panel never
    loses a choice; expansion state lives here so it survives a rebuild
    too.  ``group_rows(rows)`` decides the folding, ``count_text(rows)`` the
    line under the header.
    """

    def __init__(self, panel, rows, group_rows, count_block=None, count_text=None,
                 search_box=None, search_fields=("type_key", "category"),
                 label_fn=None, empty_text=u"", no_rows_text=u"", on_change=None):
        self.panel = panel
        self.rows = rows
        self._group_rows = group_rows
        self._count_block = count_block
        self._count_text = count_text
        self._search_box = search_box
        self._search_fields = tuple(search_fields)
        self._label_fn = label_fn or default_row_label
        self._empty_text = empty_text
        self._no_rows_text = no_rows_text
        self._on_change = on_change
        self.expanded = {}
        self._checkboxes = []

    def query(self):
        if self._search_box is None:
            return u""
        return safe_text(self._search_box.Text).strip().lower()

    def visible_rows(self):
        query = self.query()
        if not query:
            return list(self.rows)
        return [row for row in self.rows
                if any(query in safe_text(row.get(field)).lower() for field in self._search_fields)]

    def render(self):
        self.panel.Children.Clear()
        self._checkboxes = []
        visible = self.visible_rows()
        groups = self._group_rows(visible)
        if not groups:
            self.panel.Children.Add(text_block(
                self._empty_text if self.rows else self._no_rows_text,
                foreground=brush("DimGray"), margin=thickness(4)))
        for group in groups:
            self.panel.Children.Add(self._group_expander(group))
        self._update_count()

    def set_visible(self, is_checked):
        for row in self.visible_rows():
            row["is_checked"] = bool(is_checked)
        self.render()
        if self._on_change is not None:
            self._on_change()

    def _group_expander(self, group):
        category = group["category"]
        ticked = len([row for row in group["rows"] if row.get("is_checked")])
        expander = Windows.Controls.Expander()
        expander.Margin = thickness(0, 0, 0, 6)
        header = Windows.Controls.DockPanel()
        header.LastChildFill = True
        dock_right(header, badge(
            u"{0} ticked".format(ticked) if ticked else u"none ticked",
            brush("AliceBlue") if ticked else brush("WhiteSmoke"),
            brush("LightSteelBlue") if ticked else brush("Gainsboro"),
            brush("SteelBlue") if ticked else brush("Gray")))
        header.Children.Add(text_block(
            u"{0}  ({1} type{2} · {3} instance{4})".format(
                category, group["type_count"], u"" if group["type_count"] == 1 else u"s",
                group["instance_count"], u"" if group["instance_count"] == 1 else u"s"),
            size=13, semibold=True, wrap=False))
        expander.Header = header
        expander.IsExpanded = self.expanded.get(category, bool(group.get("is_expanded")))
        expander.Expanded += expansion_handler(self.expanded, category, True)
        expander.Collapsed += expansion_handler(self.expanded, category, False)

        stack = Windows.Controls.StackPanel()
        for row in group["rows"]:
            stack.Children.Add(self._row(row))
        expander.Content = card(stack)
        return expander

    def _row(self, row):
        line = Windows.Controls.StackPanel()
        line.Orientation = Windows.Controls.Orientation.Horizontal
        line.Margin = thickness(0, 2, 0, 2)
        checkbox = Windows.Controls.CheckBox()
        checkbox.IsChecked = bool(row.get("is_checked"))
        checkbox.Tag = row
        checkbox.VerticalAlignment = Windows.VerticalAlignment.Center
        checkbox.Margin = thickness(2, 0, 8, 0)
        checkbox.Checked += self._toggled
        checkbox.Unchecked += self._toggled
        line.Children.Add(checkbox)
        main, note = self._label_fn(row)
        line.Children.Add(text_block(main, wrap=False))
        if note:
            line.Children.Add(text_block(note, size=11, foreground=brush("DimGray"), wrap=False))
        self._checkboxes.append(checkbox)
        return line

    def _toggled(self, sender, args):
        del args
        row = getattr(sender, "Tag", None)
        if row is None:
            return
        row["is_checked"] = bool(sender.IsChecked)
        self._update_count()
        if self._on_change is not None:
            self._on_change()

    def _update_count(self):
        if self._count_block is not None and self._count_text is not None:
            self._count_block.Text = self._count_text(self.rows)


# ---------------------------------------------------------------- report


def bucket_expander(bucket, expanded, build_row, cap=None):
    """One report expander: title, count badge, rows, and the hidden count."""
    key = bucket["key"]
    expander = Windows.Controls.Expander()
    expander.Margin = thickness(0, 0, 0, 10)
    header = Windows.Controls.DockPanel()
    header.LastChildFill = True
    problem = bool(bucket.get("is_problem"))
    count = len(bucket["items"])
    dock_right(header, badge(
        u"{0} {1}".format(count, bucket.get("noun") or (u"item" if count == 1 else u"items")),
        brush("MistyRose") if problem else brush("Honeydew"),
        brush("RosyBrown") if problem else brush("DarkSeaGreen"),
        brush("Firebrick") if problem else brush("DarkGreen")))
    header.Children.Add(text_block(bucket["title"], size=15, semibold=True, wrap=False))
    expander.Header = header
    expander.IsExpanded = expanded.get(key, problem)
    expander.Expanded += expansion_handler(expanded, key, True)
    expander.Collapsed += expansion_handler(expanded, key, False)

    stack = Windows.Controls.StackPanel()
    items, hidden = cap_items(bucket["items"], cap) if cap else cap_items(bucket["items"])
    for item in items:
        stack.Children.Add(build_row(item))
    if hidden:
        stack.Children.Add(text_block(u"… {0} more - search to narrow.".format(hidden),
                                      foreground=brush("DimGray"), margin=thickness(4)))
    expander.Content = card(stack)
    return expander


def report_row(item, on_show, busy=False, show_buttons=None, detail_suffix=u""):
    """A row with its Show button; the item rides on the button's Tag."""
    holder = Windows.Controls.Border()
    holder.BorderBrush = brush("Gainsboro")
    holder.BorderThickness = thickness(1)
    holder.Background = brush("WhiteSmoke")
    holder.Padding = thickness(10, 8, 10, 8)
    holder.Margin = thickness(0, 0, 0, 6)

    panel = Windows.Controls.DockPanel()
    panel.LastChildFill = True
    if on_show is not None:
        button = Windows.Controls.Button()
        button.Content = u"Show"
        button.Width = 80
        button.Height = 28
        button.Margin = thickness(10, 0, 0, 0)
        button.VerticalAlignment = Windows.VerticalAlignment.Center
        button.Tag = item
        button.ToolTip = u"Select the elements in the model and zoom to them."
        button.Click += on_show
        button.IsEnabled = not busy
        dock_right(panel, button)
        if show_buttons is not None:
            show_buttons.append(button)

    lines = Windows.Controls.StackPanel()
    lines.Children.Add(text_block(item.get("title"), semibold=True))
    detail = safe_text(item.get("detail")) + safe_text(detail_suffix)
    if detail:
        lines.Children.Add(text_block(detail, size=12, foreground=brush("DimGray"),
                                      margin=thickness(0, 2, 0, 0)))
    panel.Children.Add(lines)
    holder.Child = panel
    return holder


def notes_expander(notes, expanded, key="notes", title=u"Scan notes"):
    """The named skips and honest limits, folded, never a crash."""
    expander = Windows.Controls.Expander()
    expander.Margin = thickness(0, 0, 0, 10)
    expander.Header = text_block(u"{0} ({1})".format(title, len(notes)), size=15, semibold=True, wrap=False)
    expander.IsExpanded = expanded.get(key, False)
    expander.Expanded += expansion_handler(expanded, key, True)
    expander.Collapsed += expansion_handler(expanded, key, False)
    stack = Windows.Controls.StackPanel()
    for note in notes:
        stack.Children.Add(text_block(u"• " + safe_text(note), margin=thickness(0, 2, 0, 2)))
    expander.Content = card(stack, background=brush("WhiteSmoke"))
    return expander


# ------------------------------------------------------- bridged window


class BridgedWindow(forms.WPFWindow):
    """A results window that reaches Revit through the bridge.

    XAML attributes such as ``IsChecked="True"`` raise their handlers while
    the window is still being parsed - before a subclass body runs - so
    every handler should read ``_is_ready()`` and stand down until then.
    Subclasses set ``self._ready = True`` at the end of their ``__init__``.
    The XAML must carry ``StatusText``; ``RefreshButton``, ``EmptyText`` and
    ``ContentScroll`` are used when present.
    """

    def __init__(self, xaml_name, bridge=None, uiapp=None, title=u""):
        forms.WPFWindow.__init__(self, xaml_name)
        self._bridge = bridge
        self._uiapp = uiapp
        self._title = title or u"EasyBIM"
        self._busy = False
        self._closing = False
        self._expanded = {}
        self._show_buttons = []
        if self._bridge is not None:
            self._bridge.on_error = self._on_bridge_error
            self._bridge.on_idle = self._on_bridge_idle

    def _is_ready(self):
        return bool(getattr(self, "_ready", False))

    def _make_expansion_handler(self, key, is_expanded):
        return expansion_handler(self._expanded, key, is_expanded)

    def _set_empty(self, is_visible, text):
        empty = getattr(self, "EmptyText", None)
        scroll = getattr(self, "ContentScroll", None)
        if empty is not None:
            empty.Visibility = Windows.Visibility.Visible if is_visible else Windows.Visibility.Collapsed
            empty.Text = safe_text(text)
        if scroll is not None:
            scroll.Visibility = Windows.Visibility.Collapsed if is_visible else Windows.Visibility.Visible

    def _update_status(self):
        """Overridden by the window; the idle status line."""
        if self._bridge is None:
            self.StatusText.Text = (u"Opened without an ExternalEvent, so this window is modal; "
                                    u"Show and Refresh still work.")
        else:
            self.StatusText.Text = u"Nothing was changed."

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
                        title=self._title)
            return False
        return True

    def _set_busy(self, busy, label=u""):
        self._busy = bool(busy)
        buttons = list(self._show_buttons)
        refresh = getattr(self, "RefreshButton", None)
        if refresh is not None:
            buttons.append(refresh)
        for button in buttons:
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
                        u"{0} will close.".format(self._title), title=self._title)
            self._closing = True
            try:
                self.Close()
            except Exception:
                pass
            return
        LOGGER.warning("%s %s failed: %s", self._title, label, error)
        self.StatusText.Text = u"{0} failed: {1}".format(label, safe_text(error) or u"unknown error")

    def _dispose_bridge(self):
        self._closing = True
        if self._bridge is not None:
            try:
                self._bridge.dispose()
            except Exception:
                pass


# --------------------------------------------------------------- registry


class WindowRegistry(object):
    """One open report per tool, findable across a persistent engine.

    The window rides a pyRevit envvar so a relaunch after an engine recycle
    still finds it; the active flag is what the launcher reads to decide
    whether dropping stale modules is safe.
    """

    def __init__(self, active_envvar, window_envvar):
        self.active_envvar = active_envvar
        self.window_envvar = window_envvar
        self._window = None

    def live(self):
        window = self._window
        if window is None:
            try:
                window = script.get_envvar(self.window_envvar)
            except Exception:
                window = None
        try:
            if window is not None and window.IsVisible:
                return window
        except Exception:
            pass
        return None

    def close_open(self):
        """A new check replaces the previous report; True if one closed."""
        window = self.live()
        if window is None:
            self.forget()
            return False
        try:
            window.Close()
        except Exception:
            pass
        self.forget()
        return True

    def remember(self, window):
        self._window = window
        try:
            script.set_envvar(self.window_envvar, window)
            script.set_envvar(self.active_envvar, True)
        except Exception:
            pass

    def forget(self, window=None):
        if window is not None and self._window is not None and self._window is not window:
            return  # a different (newer) window owns the registry
        self._window = None
        try:
            script.set_envvar(self.window_envvar, None)
            script.set_envvar(self.active_envvar, False)
        except Exception:
            pass
