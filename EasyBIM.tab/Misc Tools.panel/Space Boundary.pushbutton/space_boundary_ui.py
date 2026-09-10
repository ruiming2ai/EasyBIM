# -*- coding: utf-8 -*-
"""WPF for Space Boundary: the setup, the dry run, and the difference report.

No Revit API lives here (test-pinned).  Every window draws dicts that
``space_boundary_state`` produced; anything that has to touch the model is a
callable the launcher hands in, run through the ``ExternalEventBridge`` it
created while it still had an API context.  The folding checklists, the report
expanders, the bridged window and the one-window registry are
``easybim.check_windows``, shared with the two checkers.

Three windows, because the tool does three separable things:

**The setup** carries the choices and nothing else.  Its two tabs are the two
jobs - draw the regions, or compare the ones already drawn.

**The dry run** is the house rule made visible: every pair that would be
drawn, every pair that would be skipped and exactly why, and an explicit tick
for anything surprising, before a single element is written.  Its ticks are
what the writer is given, so what was previewed is what runs.

**The report** is the review lane: what drifted, and the one or two things
that can honestly be done about each kind of drift.  A row that can only be
deleted never offers a redraw, because redrawing it would not fix anything.
"""

from __future__ import print_function

from pyrevit import forms
from pyrevit.framework import Windows

from easybim.check_windows import BridgedWindow
from easybim.check_windows import ChecklistPanel
from easybim.check_windows import DocumentGone
from easybim.check_windows import WindowRegistry
from easybim.check_windows import brush
from easybim.check_windows import bucket_expander
from easybim.check_windows import card
from easybim.check_windows import dock_right
from easybim.check_windows import expansion_handler
from easybim.check_windows import notes_expander
from easybim.check_windows import report_row
from easybim.check_windows import text_block
from easybim.check_windows import thickness

import space_boundary_state as state


TITLE = "Space Boundary"
SETUP_XAML = "SpaceBoundaryWindow.xaml"
PLAN_XAML = "SpaceBoundaryPlanWindow.xaml"
REPORT_XAML = "SpaceBoundaryReportWindow.xaml"

# Must equal script.py's ACTIVE_ENVVAR (test-pinned) and differ from the two
# checkers': each tool keeps its own report open.
ACTIVE_ENVVAR = "EASYBIM_SPACE_BOUNDARY_ACTIVE"
WINDOW_ENVVAR = "EASYBIM_SPACE_BOUNDARY_WINDOW"

REGISTRY = WindowRegistry(ACTIVE_ENVVAR, WINDOW_ENVVAR)

# Kept as a name of this module for the launcher and the tests.
DocumentGone = DocumentGone

KIND_CHOICES = ((state.KIND_ROOM, u"Rooms"), (state.KIND_SPACE, u"Spaces"))

#: Skips a person should read as information, not as something to fix.
QUIET_SKIP_TITLE = u"Not this view's rooms"


def _text(value):
    return state.safe_text(value)


def _source_label(row):
    main = _text(row.get("title"))
    if row.get("is_host"):
        main = u"{0} (this model)".format(main)
    note = u"  ({0:,})".format(state.to_int(row.get("count")))
    reason = _text(row.get("reason"))
    if reason:
        note += u"  " + reason
    return main, note


def _view_label(row):
    main = _text(row.get("name"))
    level = _text(row.get("level_name"))
    phase = _text(row.get("phase_name"))
    parts = [part for part in (level, phase) if part]
    note = u"  ({0})".format(u" · ".join(parts)) if parts else u""
    reason = _text(row.get("reason"))
    if reason:
        note += u"  " + reason
    return main, note


# ----------------------------------------------------------- setup window


class SetupWindow(forms.WPFWindow):
    """The choices: what to convert, from where, into which views, measured how."""

    def __init__(self, catalog, settings, note=u"", picked=None):
        # XAML attributes such as IsChecked="True" raise their handlers while
        # the window is still being parsed - before this body runs - so every
        # handler reads ``_ready`` through getattr and stands down until then.
        forms.WPFWindow.__init__(self, SETUP_XAML)
        self.result = None
        self.settings = dict(settings or {})
        self.picked = dict(picked or {}) or None
        self._catalog = catalog or {}
        self._region_types = list(self._catalog.get("region_types") or [])

        self._source_rows = state.preselect_sources(
            self._catalog.get("sources") or [], self.settings)
        self._sources = ChecklistPanel(
            self.SourcePanel, self._source_rows, state.group_sources,
            count_block=self.SourceCountText, count_text=state.source_count_text,
            search_box=self.SourceSearchBox, search_fields=("key", "title", "category"),
            label_fn=_source_label,
            empty_text=u"No source matches.",
            no_rows_text=u"Nothing to read rooms from.",
            on_change=self._sources_changed)

        self._view_rows = state.preselect_views(self._catalog.get("views") or [], self.settings)
        self._views = ChecklistPanel(
            self.ViewsPanel, self._view_rows, state.group_views,
            count_block=self.ViewCountText, count_text=state.view_count_text,
            search_box=self.ViewSearchBox, search_fields=("name", "level_name", "category"),
            label_fn=_view_label,
            empty_text=u"No view matches.",
            no_rows_text=u"This model has no plan view that can hold a filled region.")

        self._fill_kinds()
        self._fill_boundary()
        self._fill_region_types()

        active = self._catalog.get("active_view") or {}
        if not active:
            self.ScopeActiveViewRadio.IsEnabled = False
            self.ScopeActiveViewRadio.ToolTip = (
                u"The active view cannot hold a filled region drawn from a room - open a floor, "
                u"ceiling, engineering or area plan.")
            self.ScopeSelectedViewsRadio.IsChecked = True
        else:
            self.ScopeActiveViewRadio.Content = u"The active view ({0})".format(
                active.get("name") or u"active view")
            if self.settings.get("scope") == state.SCOPE_SELECTED:
                self.ScopeSelectedViewsRadio.IsChecked = True
            else:
                self.ScopeActiveViewRadio.IsChecked = True

        if self.settings.get("subject") == state.SUBJECT_ONE and self.picked:
            self.SubjectOneRadio.IsChecked = True
        else:
            self.SubjectAllRadio.IsChecked = True
        self.ReplaceExistingCheck.IsChecked = bool(self.settings.get("replace_existing"))
        self.IncludeDesignOptionCheck.IsChecked = bool(
            self.settings.get("include_design_options"))

        self.CheckSummaryText.Text = _text(self._catalog.get("tracked_sentence"))
        if note:
            self.StatusText.Text = note
        if self._catalog.get("open_on_check"):
            self.Tabs.SelectedItem = self.CheckTab

        self._ready = True
        self._apply_kind()
        self._sources.render()
        self._views.render()
        self._refresh_scope()
        self._refresh_picked()

    # -- filling -----------------------------------------------------------

    def _fill_kinds(self):
        self.KindCombo.Items.Clear()
        wanted = self.settings.get("kind") or state.KIND_ROOM
        for index, (key, label) in enumerate(KIND_CHOICES):
            self.KindCombo.Items.Add(label)
            if key == wanted:
                self.KindCombo.SelectedIndex = index
        if self.KindCombo.SelectedIndex is None or self.KindCombo.SelectedIndex < 0:
            self.KindCombo.SelectedIndex = 0

    def _fill_boundary(self):
        self.BoundaryOverrideCombo.Items.Clear()
        wanted = self.settings.get("boundary_override") or "Finish"
        for index, name in enumerate(state.BOUNDARY_LOCATIONS):
            self.BoundaryOverrideCombo.Items.Add(state.BOUNDARY_SENTENCES.get(name, name))
            if name == wanted:
                self.BoundaryOverrideCombo.SelectedIndex = index
        if self.BoundaryOverrideCombo.SelectedIndex is None or \
                self.BoundaryOverrideCombo.SelectedIndex < 0:
            self.BoundaryOverrideCombo.SelectedIndex = 0
        if self.settings.get("boundary_source") == "override":
            self.BoundaryOverrideRadio.IsChecked = True
        else:
            self.BoundaryDocumentRadio.IsChecked = True
        self._refresh_boundary_text()

    def _fill_region_types(self):
        self.RegionTypeCombo.Items.Clear()
        wanted = _text(self.settings.get("region_type_name"))
        for index, row in enumerate(self._region_types):
            # Plain text rather than the row itself: a Python dict has no
            # ``name`` property for WPF to bind to, so a bound combo would
            # show the dict's repr on every line.
            self.RegionTypeCombo.Items.Add(_text(row.get("name")))
            if _text(row.get("name")) == wanted:
                self.RegionTypeCombo.SelectedIndex = index
        if self._region_types and (self.RegionTypeCombo.SelectedIndex is None or
                                   self.RegionTypeCombo.SelectedIndex < 0):
            self.RegionTypeCombo.SelectedIndex = 0
        if not self._region_types:
            self.RegionTypeCombo.IsEnabled = False
            self.RegionTypeCombo.ToolTip = state.CODE_SENTENCES["no_region_type"]

    # -- reactions ---------------------------------------------------------

    def _kind(self):
        index = self.KindCombo.SelectedIndex
        if index is None or index < 0 or index >= len(KIND_CHOICES):
            return state.KIND_ROOM
        return KIND_CHOICES[index][0]

    def _apply_kind(self):
        """Point every source's count at the kind, then re-run the rule."""
        kind = self._kind()
        field = "space_count" if kind == state.KIND_SPACE else "room_count"
        for row in self._source_rows:
            row["count"] = state.to_int(row.get(field))
        state.retick_sources(self._source_rows)
        total = sum(state.to_int(row.get("count")) for row in self._source_rows)
        noun = u"space" if kind == state.KIND_SPACE else u"room"
        if total:
            self.SourceNoteText.Text = u"{0:,} {1}(s) across the ticked and unticked " \
                                       u"sources.".format(total, noun)
        else:
            self.SourceNoteText.Text = u"Nothing in this model or its links holds a placed " \
                                       u"{0}.".format(noun)

    def kind_changed(self, sender, args):
        del sender, args
        if not self._is_ready():
            return
        self._apply_kind()
        self._sources.render()

    def _is_ready(self):
        return bool(getattr(self, "_ready", False))

    def _sources_changed(self):
        for row in self._source_rows:
            row["touched"] = True

    def _refresh_scope(self):
        selected = bool(self.ScopeSelectedViewsRadio.IsChecked)
        for control in (self.ViewsCard, self.ViewSearchBox, self.ViewAllButton,
                        self.ViewNoneButton):
            control.IsEnabled = selected

    def scope_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._refresh_scope()

    def _refresh_picked(self):
        one = bool(self.SubjectOneRadio.IsChecked)
        self.PickButton.IsEnabled = one
        if not one:
            self.PickedText.Text = u"every room or space in the ticked sources"
        elif self.picked:
            self.PickedText.Text = _text(self.picked.get("label")) or u"one picked"
        else:
            self.PickedText.Text = u"nothing picked yet"

    def subject_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._refresh_picked()

    def _refresh_boundary_text(self):
        resolved = self._catalog.get("boundary") or {}
        _name, _source, sentence = state.effective_location(
            self._read_boundary(), resolved.get("name"), resolved.get("source"),
            resolved.get("note"))
        self.BoundaryResolvedText.Text = sentence

    def _read_boundary(self):
        index = self.BoundaryOverrideCombo.SelectedIndex
        override = state.BOUNDARY_LOCATIONS[index] \
            if index is not None and 0 <= index < len(state.BOUNDARY_LOCATIONS) else "Finish"
        return {"boundary_source": "override" if bool(self.BoundaryOverrideRadio.IsChecked)
                else "document",
                "boundary_override": override}

    def boundary_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._refresh_boundary_text()

    def option_changed(self, sender, args):
        del sender, args

    def region_type_changed(self, sender, args):
        del sender, args

    def source_search_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._sources.render()

    def view_search_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._views.render()

    def source_all_click(self, sender, args):
        del sender, args
        self._sources.set_visible(True)

    def source_none_click(self, sender, args):
        del sender, args
        self._sources.set_visible(False)

    def view_all_click(self, sender, args):
        del sender, args
        self._views.set_visible(True)

    def view_none_click(self, sender, args):
        del sender, args
        self._views.set_visible(False)

    # -- leaving -----------------------------------------------------------

    def _region_type(self):
        index = self.RegionTypeCombo.SelectedIndex
        if index is None or index < 0 or index >= len(self._region_types):
            return {}
        return dict(self._region_types[index])

    def _collect(self):
        boundary = self._read_boundary()
        options = {
            "kind": self._kind(),
            "scope": state.SCOPE_SELECTED if bool(self.ScopeSelectedViewsRadio.IsChecked)
            else state.SCOPE_ACTIVE,
            "subject": state.SUBJECT_ONE if bool(self.SubjectOneRadio.IsChecked)
            else state.SUBJECT_ALL,
            "boundary_source": boundary["boundary_source"],
            "boundary_override": boundary["boundary_override"],
            "region_type_name": _text(self._region_type().get("name")),
            "replace_existing": bool(self.ReplaceExistingCheck.IsChecked),
            "include_design_options": bool(self.IncludeDesignOptionCheck.IsChecked),
        }
        return state.apply_setup(self.settings, self._source_rows, self._view_rows, options)

    def _complain(self, text):
        self.StatusText.Text = text
        return False

    def _ready_to_draw(self):
        if not state.ticked(self._source_rows):
            return self._complain(u"No source is ticked - tick this model or a link to read "
                                  u"rooms from.")
        if not self._region_types:
            return self._complain(state.CODE_SENTENCES["no_region_type"] +
                                  u" Load or create one, then try again.")
        if bool(self.ScopeSelectedViewsRadio.IsChecked) and not state.ticked(self._view_rows):
            return self._complain(u"No view is ticked - tick the views to draw in, or choose the "
                                  u"active view.")
        if bool(self.SubjectOneRadio.IsChecked) and not self.picked:
            return self._complain(u"Pick the room or space first, or switch back to converting "
                                  u"every one.")
        return True

    def preview_click(self, sender, args):
        del sender, args
        if not self._ready_to_draw():
            return
        self.settings = self._collect()
        self.result = "preview"
        self.Close()

    def check_click(self, sender, args):
        del sender, args
        self.settings = self._collect()
        self.result = "check"
        self.Close()

    def pick_click(self, sender, args):
        del sender, args
        # PickObject cannot run while a modal window is up, so the window
        # hands its choices back and script.py reopens it after the pick.
        self.settings = self._collect()
        self.result = "pick"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


# ------------------------------------------------------------ plan window


class PlanWindow(forms.WPFWindow):
    """The dry run: every pair that would be drawn, and every one that would not.

    Its ticks are what the writer is handed, so what a person read here is
    exactly what runs.  Anything surprising - a large batch, or replacing
    regions somebody may have edited - needs an explicit tick first.
    """

    def __init__(self, plan, config):
        forms.WPFWindow.__init__(self, PLAN_XAML)
        self.result = None
        self.checked_keys = None
        self._plan = plan or {}
        self._config = config or {}
        self._expanded = {}
        self._acks = {}
        self._items = [dict(item, is_checked=True) for item in self._plan.get("items") or []]

        self.SummaryText.Text = state.plan_summary(self._plan)
        refusal = _text(self._plan.get("refusal"))
        if refusal:
            self.RefusalCard.Visibility = Windows.Visibility.Visible
            self.RefusalText.Text = refusal
            self.DrawButton.IsEnabled = False
        self._build_acks()
        self._ready = True
        self._render()

    def _is_ready(self):
        return bool(getattr(self, "_ready", False))

    def _build_acks(self):
        asks = self._plan.get("acknowledgements") or []
        if not asks:
            return
        self.AckCard.Visibility = Windows.Visibility.Visible
        self.AckPanel.Children.Clear()
        for ask in asks:
            checkbox = Windows.Controls.CheckBox()
            checkbox.Content = _text(ask.get("text"))
            checkbox.Margin = thickness(0, 3, 0, 3)
            checkbox.Tag = _text(ask.get("key"))
            checkbox.Checked += self._ack_toggled
            checkbox.Unchecked += self._ack_toggled
            self._acks[_text(ask.get("key"))] = False
            self.AckPanel.Children.Add(checkbox)

    def _ack_toggled(self, sender, args):
        del args
        self._acks[_text(getattr(sender, "Tag", u""))] = bool(sender.IsChecked)
        if self._is_ready():
            self._update_status()

    # -- rendering ---------------------------------------------------------

    def _visible(self):
        query = _text(self.SearchBox.Text).strip().lower()
        if not query:
            return list(self._items), list(self._plan.get("skips") or [])
        items = [item for item in self._items if query in _text(item.get("title")).lower()]
        skips = [skip for skip in self._plan.get("skips") or []
                 if query in _text(skip.get("title")).lower()
                 or query in _text(skip.get("reason")).lower()]
        return items, skips

    def _render(self):
        items, skips = self._visible()
        self.ContentPanel.Children.Clear()

        if not items and not skips:
            self._set_empty(True, u"Nothing matches that search."
                            if _text(self.SearchBox.Text).strip()
                            else u"There is nothing to draw and nothing was skipped.")
        else:
            self._set_empty(False, u"")
            for group in self._group_items(items):
                self.ContentPanel.Children.Add(self._item_expander(group))
            for group in self._group_skips(skips):
                self.ContentPanel.Children.Add(self._skip_expander(group))

        notes = state.scan_notes(self._plan, self._config)
        self.ContentPanel.Children.Add(notes_expander(notes, self._expanded))
        self._update_counts()
        self._update_status()

    def _set_empty(self, is_visible, text):
        self.EmptyText.Visibility = Windows.Visibility.Visible if is_visible \
            else Windows.Visibility.Collapsed
        self.EmptyText.Text = _text(text)
        self.ContentScroll.Visibility = Windows.Visibility.Collapsed if is_visible \
            else Windows.Visibility.Visible

    def _group_items(self, items):
        groups = {}
        order = []
        for item in items:
            name = _text(item.get("view_name")) or u"(view)"
            if name not in groups:
                groups[name] = []
                order.append(name)
            groups[name].append(item)
        return [{"key": u"view:" + name, "title": name, "items": groups[name]}
                for name in order]

    def _group_skips(self, skips):
        groups = {}
        order = []
        for skip in skips:
            code = _text(skip.get("code"))
            if code not in groups:
                groups[code] = {"quiet": bool(skip.get("quiet")), "rows": []}
                order.append(code)
            groups[code]["rows"].append(skip)
        result = []
        for code in order:
            rows = groups[code]["rows"]
            title = _text(rows[0].get("reason")) or code
            if groups[code]["quiet"] and code in ("wrong_level", "wrong_phase", "view_no_level"):
                title = u"{0} - {1}".format(QUIET_SKIP_TITLE, title)
            result.append({"key": u"skip:" + code, "title": title, "rows": rows,
                           "quiet": groups[code]["quiet"]})
        return result

    def _item_expander(self, group):
        expander = Windows.Controls.Expander()
        expander.Margin = thickness(0, 0, 0, 8)
        header = Windows.Controls.DockPanel()
        header.LastChildFill = True
        count = len(group["items"])
        ticked = len([item for item in group["items"] if item.get("is_checked")])
        dock_right(header, text_block(u"{0} of {1} ticked".format(ticked, count), size=11,
                                      foreground=brush("DimGray"), wrap=False))
        header.Children.Add(text_block(group["title"], size=14, semibold=True, wrap=False))
        expander.Header = header
        expander.IsExpanded = self._expanded.get(group["key"], count <= 40)
        expander.Expanded += expansion_handler(self._expanded, group["key"], True)
        expander.Collapsed += expansion_handler(self._expanded, group["key"], False)

        stack = Windows.Controls.StackPanel()
        for item in group["items"][:state.ROW_CAP]:
            stack.Children.Add(self._item_row(item))
        hidden = max(0, count - state.ROW_CAP)
        if hidden:
            stack.Children.Add(text_block(
                u"… {0:,} more - search to narrow. All of them are ticked.".format(hidden),
                foreground=brush("DimGray"), margin=thickness(4)))
        expander.Content = card(stack)
        return expander

    def _item_row(self, item):
        line = Windows.Controls.StackPanel()
        line.Orientation = Windows.Controls.Orientation.Horizontal
        line.Margin = thickness(0, 2, 0, 2)
        checkbox = Windows.Controls.CheckBox()
        checkbox.IsChecked = bool(item.get("is_checked"))
        checkbox.Tag = item
        checkbox.VerticalAlignment = Windows.VerticalAlignment.Center
        checkbox.Margin = thickness(2, 0, 8, 0)
        checkbox.Checked += self._item_toggled
        checkbox.Unchecked += self._item_toggled
        line.Children.Add(checkbox)
        line.Children.Add(text_block(_text(item.get("title")), wrap=False))
        notes = []
        if item.get("action") == "replace":
            notes.append(u"replaces an existing region")
        if item.get("in_group"):
            notes.append(u"the room is in a group; the region cannot join it")
        if notes:
            line.Children.Add(text_block(u"  " + u"; ".join(notes), size=11,
                                         foreground=brush("DimGray"), wrap=False))
        return line

    def _item_toggled(self, sender, args):
        del args
        item = getattr(sender, "Tag", None)
        if item is None:
            return
        item["is_checked"] = bool(sender.IsChecked)
        self._update_counts()
        self._update_status()

    def _skip_expander(self, group):
        bucket = {"key": group["key"], "title": group["title"],
                  "is_problem": not group["quiet"],
                  "noun": u"room" if len(group["rows"]) == 1 else u"rooms",
                  "items": group["rows"]}
        return bucket_expander(bucket, self._expanded, self._skip_row)

    def _skip_row(self, skip):
        holder = Windows.Controls.Border()
        holder.Padding = thickness(2, 3, 2, 3)
        holder.Child = text_block(_text(skip.get("title")), wrap=False)
        return holder

    def _update_counts(self):
        ticked = len([item for item in self._items if item.get("is_checked")])
        self.CountText.Text = u"{0:,} of {1:,} ticked".format(ticked, len(self._items))

    def _blocking(self):
        if _text(self._plan.get("refusal")):
            return _text(self._plan.get("refusal"))
        missing = [key for key, done in self._acks.items() if not done]
        if missing:
            return u"Tick the box above to confirm before drawing."
        if not any(item.get("is_checked") for item in self._items):
            return u"Nothing is ticked, so there is nothing to draw."
        return u""

    def _update_status(self):
        blocking = self._blocking()
        self.DrawButton.IsEnabled = not blocking
        self.StatusText.Text = blocking or (
            u"The whole run is one undo step. The relationship is written into each region, "
            u"inside this model.")

    # -- handlers ----------------------------------------------------------

    def search_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._render()

    def select_all_click(self, sender, args):
        del sender, args
        visible, _skips = self._visible()
        for item in visible:
            item["is_checked"] = True
        self._render()

    def select_none_click(self, sender, args):
        del sender, args
        visible, _skips = self._visible()
        for item in visible:
            item["is_checked"] = False
        self._render()

    def expand_all_click(self, sender, args):
        del sender, args
        self._set_all(True)

    def collapse_all_click(self, sender, args):
        del sender, args
        self._set_all(False)

    def _set_all(self, is_expanded):
        items, skips = self._visible()
        for group in self._group_items(items):
            self._expanded[group["key"]] = is_expanded
        for group in self._group_skips(skips):
            self._expanded[group["key"]] = is_expanded
        self._expanded["notes"] = is_expanded
        self._render()

    def draw_click(self, sender, args):
        del sender, args
        if self._blocking():
            return
        self.checked_keys = [item["key"] for item in self._items if item.get("is_checked")]
        self.result = "draw"
        self.Close()

    def back_click(self, sender, args):
        del sender, args
        self.result = "back"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


# ---------------------------------------------------------- report window


class ReportWindow(BridgedWindow):
    """The difference report: what drifted, and what can honestly be done."""

    def __init__(self, report, config, bridge=None, uiapp=None, recheck=None, show=None,
                 redraw=None, accept=None, delete=None, create=None, ignore=None):
        # See SetupWindow: handlers can fire mid-parse, before ``_ready`` exists.
        BridgedWindow.__init__(self, REPORT_XAML, bridge=bridge, uiapp=uiapp, title=TITLE)
        self._report = report or {}
        self._config = config or {}
        self._recheck = recheck
        self._show = show
        self._redraw = redraw
        self._accept = accept
        self._delete = delete
        self._create = create
        self._ignore = ignore
        self.Closed += self._on_closed
        self._ready = True
        self._render()

    # -- rendering ---------------------------------------------------------

    def _render(self):
        query = _text(self.SearchBox.Text)
        buckets = self._filtered(query)
        self.ContentPanel.Children.Clear()
        self._show_buttons = []

        shown = [bucket for bucket in buckets if bucket["items"]]
        if not shown:
            if query.strip():
                message = u"Nothing matches '{0}'.".format(query.strip())
            elif not state.to_int(self._report.get("tracked")):
                message = (u"No filled region in this model carries a room relationship yet. "
                           u"Draw some on the Create tab, and they will be tracked from then on.")
            else:
                message = u"Every tracked region is in step with the room it stands for."
            self._set_empty(True, message)
        else:
            self._set_empty(False, u"")
            for bucket in shown:
                self.ContentPanel.Children.Add(
                    bucket_expander(bucket, self._expanded, self._row))

        notes = state.scan_notes(self._report, self._config)
        self.ContentPanel.Children.Add(notes_expander(notes, self._expanded))
        self.SummaryText.Text = state.drift_summary(self._report)
        if not self._busy:
            self._update_status()

    def _filtered(self, query):
        buckets = self._report.get("buckets") or []
        if not query.strip():
            return buckets
        needle = query.strip().lower()
        result = []
        for bucket in buckets:
            rows = [item for item in bucket["items"]
                    if needle in _text(item.get("title")).lower()
                    or needle in _text(item.get("detail")).lower()]
            result.append(dict(bucket, items=rows))
        return result

    def _row(self, item):
        return report_row(item, self._show_click, self._busy, self._show_buttons,
                          extra_buttons=self._row_buttons(item))

    def _row_buttons(self, item):
        """Only what would honestly help this row.

        A region whose record names another view, or whose room is gone, is
        never offered a redraw: there is nothing to redraw it from, and an
        offer that cannot work is worse than no offer.
        """
        buttons = []
        if item.get("is_ignored"):
            if self._ignore is not None:
                buttons.append((u"Restore", u"Put this row back in the group it belongs to.",
                                self._restore_click, 84))
            return tuple(buttons)
        if item.get("can_redraw") and self._redraw is not None:
            buttons.append((u"Redraw", u"Delete this region and draw it again from the room as "
                                       u"it is now. Any hand edits to it are lost.",
                            self._redraw_click, 84))
        if item.get("can_accept") and self._accept is not None:
            buttons.append((u"Accept", u"Take the region as it now is. Future edits to it are "
                                       u"still reported.", self._accept_click, 84))
        if item.get("can_create") and self._create is not None:
            buttons.append((u"Create", u"Draw a region for this room in this view.",
                            self._create_click, 84))
        if item.get("can_delete") and self._delete is not None:
            buttons.append((u"Delete", u"Delete this region and its record.",
                            self._delete_click, 84))
        if self._ignore is not None and item.get("region_uid"):
            buttons.append((u"Ignore", u"Set this row aside. The decision is stored in this "
                                       u"model, so it comes back next time and reaches the team "
                                       u"after a Sync to Central.", self._ignore_click, 84))
        return tuple(buttons)

    def _update_status(self):
        if self._bridge is None:
            BridgedWindow._update_status(self)
        else:
            self.StatusText.Text = (u"Reading only. Nothing changes until you use one of the "
                                    u"buttons on a row.")

    # -- handlers ----------------------------------------------------------

    def search_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._render()

    def expand_all_click(self, sender, args):
        del sender, args
        self._set_all(True)

    def collapse_all_click(self, sender, args):
        del sender, args
        self._set_all(False)

    def _set_all(self, is_expanded):
        for key, _title, _problem in state.DRIFT_BUCKETS:
            self._expanded[key] = is_expanded
        self._expanded["notes"] = is_expanded
        self._render()

    def _show_click(self, sender, args):
        del args
        item = getattr(sender, "Tag", None)
        if item is None or self._show is None:
            return
        if item.get("region_id") is None:
            self.StatusText.Text = u"There is no region to show for this row yet."
            return

        def _work(uiapp):
            return self._show(uiapp, item)

        def _done(shown):
            self.StatusText.Text = u"Selected the region." if shown else \
                u"The region could not be selected (hidden in this view, or already gone)."

        self._run_in_revit(u"Show", _work, _done, quiet=True)

    def _redraw_click(self, sender, args):
        del args
        self._act(getattr(sender, "Tag", None), self._redraw, u"Redraw", u"Redrawn.")

    def _accept_click(self, sender, args):
        del args
        self._act(getattr(sender, "Tag", None), self._accept, u"Accept",
                  u"Accepted as drawn, and saved in the model.")

    def _delete_click(self, sender, args):
        del args
        self._act(getattr(sender, "Tag", None), self._delete, u"Delete", u"Deleted.")

    def _create_click(self, sender, args):
        del args
        self._act(getattr(sender, "Tag", None), self._create, u"Create", u"Drawn.")

    def _ignore_click(self, sender, args):
        del args
        self._set_ignored(getattr(sender, "Tag", None), True)

    def _restore_click(self, sender, args):
        del args
        self._set_ignored(getattr(sender, "Tag", None), False)

    def _act(self, item, action, label, done_text):
        """Do the thing, re-read, and say what happened - in that order."""
        if item is None or action is None:
            return

        def _work(uiapp):
            return action(uiapp, item)

        def _done(result):
            result = result or {}
            if result.get("report") is not None:
                self._report = result["report"]
            self._render()
            self.StatusText.Text = _text(result.get("message")) or (
                done_text if result.get("ok", True)
                else u"{0} did not happen.".format(label))

        self._run_in_revit(label, _work, _done)

    def _set_ignored(self, item, on):
        """Write the decision into the model, then move the row - in that
        order, so a row never reads as set aside when nothing was stored."""
        if item is None or self._ignore is None:
            return
        key = _text(item.get("region_uid"))
        if not key:
            self.StatusText.Text = u"This row has no region to set aside."
            return

        def _work(uiapp):
            return self._ignore(uiapp, key, on)

        def _done(result):
            result = result or {}
            if result.get("report") is not None:
                self._report = result["report"]
            self._render()
            if result.get("ok"):
                self.StatusText.Text = (u"Set aside, and saved in the model." if on
                                        else u"Restored, and saved in the model.")
            else:
                self.StatusText.Text = u"Not saved in the model: {0}".format(
                    _text(result.get("message")) or u"unknown error")

        self._run_in_revit(u"Ignore" if on else u"Restore", _work, _done, quiet=True)

    def refresh_click(self, sender, args):
        del sender, args
        if self._recheck is None:
            return
        self._run_in_revit(u"Refresh", self._recheck, self._refresh_done)

    def _refresh_done(self, report):
        if report:
            self._report = report
        self._render()

    def close_click(self, sender, args):
        del sender, args
        self.Close()

    def _on_closed(self, sender, args):
        del sender, args
        REGISTRY.forget(self)
        self._dispose_bridge()


# ---------------------------------------------------------------- launcher


def close_open_window():
    """A new check replaces the previous report; returns True if one closed."""
    return REGISTRY.close_open()


def show_setup(catalog, settings, note=u"", picked=None):
    """``(result, settings, picked)`` - the setup, run modally."""
    window = SetupWindow(catalog, settings, note=note, picked=picked)
    window.ShowDialog()
    return window.result, window.settings, window.picked


def show_plan(plan, config):
    """``(result, checked_keys)`` - the dry run, run modally."""
    window = PlanWindow(plan, config)
    window.ShowDialog()
    return window.result, window.checked_keys


def show_report(report, config, bridge=None, uiapp=None, recheck=None, show=None, redraw=None,
                accept=None, delete=None, create=None, ignore=None):
    """Open the report modeless; modal when no ExternalEvent could be made."""
    REGISTRY.close_open()
    window = ReportWindow(report, config, bridge=bridge, uiapp=uiapp, recheck=recheck,
                          show=show, redraw=redraw, accept=accept, delete=delete,
                          create=create, ignore=ignore)
    if bridge is None:
        window.ShowDialog()
        return window
    REGISTRY.remember(window)
    window.Show()
    return window
