# -*- coding: utf-8 -*-
"""WPF for Fire Damper Check: the per-link setup and the modeless report.

No Revit API lives here (test-pinned).  The report draws the dicts
``fire_damper_check_state`` produces; Show and Refresh are callables the
launcher hands in, run through the ``ExternalEventBridge`` it created while
it still had an API context.  The folding checklist, the report expanders,
the bridged window and the one-window registry are ``easybim.check_windows``,
shared with Damper Check.

The setup's left card is built in code because its shape is the model's:
one expander per link (this model first), each with an enable tick, the
rating-parameter ComboBox, and three folding checklists - wall types, floor
types, line styles - pre-ticked by the rules in the state module and
remembered by name per link.
"""

from __future__ import print_function

from pyrevit import forms
from pyrevit.framework import Windows

from easybim.check_windows import BridgedWindow
from easybim.check_windows import ChecklistPanel
from easybim.check_windows import DocumentGone
from easybim.check_windows import WindowRegistry
from easybim.check_windows import badge
from easybim.check_windows import brush
from easybim.check_windows import bucket_expander
from easybim.check_windows import card
from easybim.check_windows import dock_right
from easybim.check_windows import notes_expander
from easybim.check_windows import parse_positive_int
from easybim.check_windows import report_row
from easybim.check_windows import text_block
from easybim.check_windows import thickness

import fire_damper_check_state as state


TITLE = "Fire Damper Check"
SETUP_XAML = "FireDamperCheckSetupWindow.xaml"
RESULTS_XAML = "FireDamperCheckResultsWindow.xaml"

# Must equal script.py's ACTIVE_ENVVAR (test-pinned) and differ from
# Damper Check's: each tool keeps its own report open.
ACTIVE_ENVVAR = "EASYBIM_FIRE_DAMPER_CHECK_ACTIVE"
WINDOW_ENVVAR = "EASYBIM_FIRE_DAMPER_CHECK_WINDOW"

REGISTRY = WindowRegistry(ACTIVE_ENVVAR, WINDOW_ENVVAR)

# Kept as a name of this module for the launcher and the tests.
DocumentGone = DocumentGone


def _parse_near(text):
    return parse_positive_int(text, clamp=state.clamp_near)


def _parse_hops(text):
    raw = state.safe_text(text).strip()
    if not raw.isdigit():
        return None
    return state.clamp_hops(raw)


def _line_label(row):
    note = u"  ({0})".format(row.get("count", 0))
    reason = state.safe_text(row.get("reason"))
    if reason:
        note += u"  " + reason
    return state.safe_text(row.get("key")), note


def _type_label(row):
    main = state.safe_text(row.get("type_key"))
    note = u"  ({0})".format(row.get("count", 0))
    reason = state.safe_text(row.get("reason"))
    if reason:
        note += u"  " + reason
    return main, note


# ----------------------------------------------------------- setup window


class SetupWindow(forms.WPFWindow):
    """Rated barriers per link, fire damper types, scope and the rule."""

    def __init__(self, catalog, settings, note=u""):
        # XAML attributes such as IsChecked="True" raise their handlers while
        # the window is still being parsed - before this body runs - so every
        # handler reads ``_ready`` through getattr and stands down until then.
        forms.WPFWindow.__init__(self, SETUP_XAML)
        self.result = None
        self._catalog = catalog or {}
        self._settings = dict(settings or {})
        self._links = []

        self._damper_rows = state.preselect_dampers(self._catalog.get("damper_types") or [], self._settings)
        self._dampers = ChecklistPanel(
            self.DamperPanel, self._damper_rows, state.group_damper_types,
            count_block=self.DamperCountText, count_text=state.damper_count_text,
            search_box=self.DamperSearchBox,
            empty_text=u"No family with duct connectors matches.",
            no_rows_text=u"This model has no family with duct connectors - nothing to tick.")

        view = self._catalog.get("view") or {}
        if not view.get("is_graphical"):
            self.ScopeViewRadio.IsEnabled = False
            self.ScopeViewRadio.ToolTip = (
                u"The active view cannot show ducts (a schedule, sheet or template) - "
                u"open a plan, section or 3D view to scope to it.")
        else:
            self.ScopeViewRadio.Content = u"Ducts in the active view ({0})".format(
                view.get("name") or u"active view")
        if self._settings.get("scope") == state.SCOPE_VIEW and view.get("is_graphical"):
            self.ScopeViewRadio.IsChecked = True
        else:
            self.ScopeModelRadio.IsChecked = True

        self.IncludeFloorsCheck.IsChecked = bool(self._settings.get("include_floors", True))
        self.NearBox.Text = u"{0}".format(state.clamp_near(self._settings.get("near_mm", state.DEFAULT_NEAR_MM)))
        self.HopsBox.Text = u"{0}".format(state.clamp_hops(self._settings.get("hops", state.DEFAULT_HOPS)))
        band = self._settings.get("band") or {}
        if band.get("mode") == "fixed":
            self.BandFixedRadio.IsChecked = True
        else:
            self.BandNextLevelRadio.IsChecked = True
        self.BandHeightBox.Text = u"{0}".format(int(band.get("height_mm", 3000) or 3000))

        if self._catalog.get("meta", {}).get("probe_truncated"):
            note = (note + u" " if note else u"") + (
                u"Listing other categories stopped at its time budget; "
                u"the four duct categories are complete.")
        if note:
            self.StatusText.Text = note
        self._ready = True
        self._dampers.render()
        self._build_links()

    def _is_ready(self):
        return bool(getattr(self, "_ready", False))

    # -- links -------------------------------------------------------------

    def _build_links(self):
        self.LinksPanel.Children.Clear()
        self._links = []
        links = self._catalog.get("links") or []
        if not links:
            self.LinksPanel.Children.Add(text_block(u"No model to read.", foreground=brush("DimGray"),
                                                    margin=thickness(4)))
            return
        for link in links:
            entry = self._link_entry(link)
            self._links.append(entry)
            self.LinksPanel.Children.Add(entry["expander"])

    def _link_entry(self, link):
        key = state.safe_text(link.get("key"))
        link_settings = state.link_settings_for(self._settings, key)
        entry = {"key": key, "title": state.safe_text(link.get("title")), "loaded": bool(link.get("loaded")),
                 "catalog": link, "settings": link_settings, "wall_panel": None, "floor_panel": None,
                 "line_panel": None, "wall_rows": [], "floor_rows": [], "line_rows": [],
                 "rating_names": [], "rating_combo": None, "enabled_box": None}

        expander = Windows.Controls.Expander()
        expander.Margin = thickness(0, 0, 0, 6)
        header = Windows.Controls.DockPanel()
        header.LastChildFill = True
        enabled = Windows.Controls.CheckBox()
        enabled.IsChecked = bool(link_settings.get("enabled", True)) and entry["loaded"]
        enabled.IsEnabled = entry["loaded"]
        enabled.VerticalAlignment = Windows.VerticalAlignment.Center
        enabled.Margin = thickness(2, 0, 8, 0)
        enabled.ToolTip = u"Untick to leave this model's barriers out of the check."
        header.Children.Add(enabled)
        entry["enabled_box"] = enabled
        status = state.safe_text(link.get("status"))
        if not entry["loaded"]:
            dock_right(header, badge(status or u"not loaded", brush("WhiteSmoke"), brush("Gainsboro"), brush("Gray")))
        else:
            count = len(link.get("types") or [])
            dock_right(header, badge(u"{0} type{1}".format(count, u"" if count == 1 else u"s"),
                                     brush("AliceBlue"), brush("LightSteelBlue"), brush("SteelBlue")))
        header.Children.Add(text_block(entry["title"], size=13, semibold=True, wrap=False))
        expander.Header = header
        expander.IsExpanded = entry["loaded"] and not link.get("is_host")
        entry["expander"] = expander

        body = Windows.Controls.StackPanel()
        if not entry["loaded"]:
            body.Children.Add(text_block(
                u"Not loaded ({0}). Reload it to read its walls; it is named in the report's notes.".format(
                    status or u"not loaded"), foreground=brush("DimGray")))
            expander.Content = card(body)
            return entry
        if status:
            body.Children.Add(text_block(status, size=11, foreground=brush("DimGray")))

        rating_row = Windows.Controls.StackPanel()
        rating_row.Orientation = Windows.Controls.Orientation.Horizontal
        rating_row.Margin = thickness(0, 0, 0, 6)
        rating_row.Children.Add(text_block(u"Rating parameter", wrap=False, margin=thickness(0, 0, 8, 0)))
        combo = Windows.Controls.ComboBox()
        combo.Width = 260
        options = state.rating_param_options(link)
        names = [row["name"] for row in options] + [u""]
        labels = [u"{0} ({1} type{2})".format(row["name"], row["count"], u"" if row["count"] == 1 else u"s")
                  if row["count"] else row["name"] for row in options] + [u"(none - use the ticks only)"]
        for label in labels:
            combo.Items.Add(label)
        chosen = state.safe_text(link_settings.get("rating_param")).strip()
        combo.SelectedIndex = names.index(chosen) if chosen in names else 0
        combo.Tag = key
        combo.SelectionChanged += self._rating_changed
        rating_row.Children.Add(combo)
        rating_row.Children.Add(text_block(u"non-empty means rated", size=11, foreground=brush("DimGray"),
                                           wrap=False, margin=thickness(8, 0, 0, 0)))
        body.Children.Add(rating_row)
        entry["rating_names"] = names
        entry["rating_combo"] = combo

        for kind, attr, title, empty in (("wall", "wall", u"Rated wall types", u"No wall types."),
                                         ("floor", "floor", u"Rated floor types", u"No floor types.")):
            body.Children.Add(text_block(title, semibold=True, margin=thickness(0, 4, 0, 2)))
            panel = Windows.Controls.StackPanel()
            body.Children.Add(panel)
            checklist = ChecklistPanel(panel, [], state.group_barrier_types, label_fn=_type_label,
                                       search_box=None, empty_text=empty, no_rows_text=empty)
            entry[attr + "_panel"] = checklist

        body.Children.Add(text_block(u"Drawn fire-rating lines (line styles)", semibold=True,
                                     margin=thickness(0, 4, 0, 2)))
        line_panel = Windows.Controls.StackPanel()
        body.Children.Add(line_panel)
        entry["line_panel"] = ChecklistPanel(
            line_panel, [], lambda rows: state.type_checklist.group_rows(rows, (u"Line styles",), (u"Line styles",)),
            label_fn=_line_label, search_box=None,
            empty_text=u"No lines in this model.", no_rows_text=u"No lines in this model.")
        expander.Content = card(body)
        self._fill_link_rows(entry)
        return entry

    def _fill_link_rows(self, entry):
        link = entry["catalog"]
        link_settings = entry["settings"]
        entry["wall_rows"] = state.preselect_barrier_types(link.get("types") or [], link_settings, "wall")
        entry["floor_rows"] = state.preselect_barrier_types(link.get("types") or [], link_settings, "floor")
        entry["line_rows"] = state.preselect_line_styles(link.get("line_styles") or [], link_settings)
        for attr, rows in (("wall_panel", entry["wall_rows"]), ("floor_panel", entry["floor_rows"]),
                           ("line_panel", entry["line_rows"])):
            panel = entry.get(attr)
            if panel is None:
                continue
            panel.rows = rows
            panel.render()

    def _rating_changed(self, sender, args):
        del args
        if not self._is_ready():
            return
        key = getattr(sender, "Tag", None)
        for entry in self._links:
            if entry["key"] != key or entry["rating_combo"] is None:
                continue
            index = int(sender.SelectedIndex) if sender.SelectedIndex is not None else 0
            names = entry["rating_names"]
            entry["settings"] = dict(entry["settings"], rating_param=names[index] if 0 <= index < len(names) else u"")
            self._fill_link_rows(entry)
            self.StatusText.Text = u"Rating parameter changed for '{0}' - its types were re-ticked from that parameter and the keywords.".format(
                entry["title"])

    # -- handlers ----------------------------------------------------------

    def damper_search_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._dampers.render()

    def damper_all_click(self, sender, args):
        del sender, args
        self._dampers.set_visible(True)

    def damper_none_click(self, sender, args):
        del sender, args
        self._dampers.set_visible(False)

    def scope_changed(self, sender, args):
        del sender, args

    def floors_changed(self, sender, args):
        del sender, args

    def band_changed(self, sender, args):
        del sender, args

    def near_changed(self, sender, args):
        del sender, args
        if not self._is_ready():
            return
        self.StatusText.Text = u"" if _parse_near(self.NearBox.Text) is not None else \
            u"The tolerance must be a whole number of millimetres, 1 or more."

    def hops_changed(self, sender, args):
        del sender, args
        if not self._is_ready():
            return
        self.StatusText.Text = u"" if _parse_hops(self.HopsBox.Text) is not None else \
            u"The connection limit must be a whole number, 0 or more."

    def _link_choices(self):
        choices = {}
        for entry in self._links:
            if not entry["loaded"]:
                continue
            index = 0
            if entry["rating_combo"] is not None and entry["rating_combo"].SelectedIndex is not None:
                index = int(entry["rating_combo"].SelectedIndex)
            names = entry["rating_names"]
            choices[entry["key"]] = {
                "enabled": bool(entry["enabled_box"].IsChecked),
                "rating_param": names[index] if 0 <= index < len(names) else u"",
                "wall_rows": entry["wall_rows"],
                "floor_rows": entry["floor_rows"],
                "line_rows": entry["line_rows"],
            }
        return choices

    def check_click(self, sender, args):
        del sender, args
        near = _parse_near(self.NearBox.Text)
        hops = _parse_hops(self.HopsBox.Text)
        height = parse_positive_int(self.BandHeightBox.Text)
        if near is None:
            self.StatusText.Text = u"The tolerance must be a whole number of millimetres, 1 or more."
            return
        if hops is None:
            self.StatusText.Text = u"The connection limit must be a whole number, 0 or more."
            return
        if bool(self.BandFixedRadio.IsChecked) and height is None:
            self.StatusText.Text = u"The fixed line height must be a whole number of millimetres."
            return
        if not any(row.get("is_checked") for row in self._damper_rows):
            self.StatusText.Text = (u"No family type is ticked as a fire damper - every crossing "
                                    u"would be reported. Tick the fire dampers first.")
            return
        choices = self._link_choices()
        anything = False
        for choice in choices.values():
            if not choice["enabled"]:
                continue
            if choice["rating_param"]:
                anything = True
            for rows in (choice["wall_rows"], choice["floor_rows"], choice["line_rows"]):
                if any(row.get("is_checked") for row in rows):
                    anything = True
        if not anything:
            self.StatusText.Text = (u"Nothing counts as a rated barrier yet - tick a wall type, a floor "
                                    u"type or a line style, or choose a rating parameter, in an enabled model.")
            return
        band = {"mode": "fixed" if bool(self.BandFixedRadio.IsChecked) else "next_level"}
        if height is not None:
            band["height_mm"] = height
        options = {
            "near_mm": near,
            "hops": hops,
            "scope": state.SCOPE_VIEW if bool(self.ScopeViewRadio.IsChecked) else state.SCOPE_MODEL,
            "include_floors": bool(self.IncludeFloorsCheck.IsChecked),
            "band": band,
        }
        self.result = state.apply_setup(self._settings, self._damper_rows, choices, options)
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


# --------------------------------------------------------- results window


class ResultsWindow(BridgedWindow):
    """The report: one expander per bucket, Show per row, live tolerance."""

    def __init__(self, analysis, config, settings, bridge=None, uiapp=None,
                 rescan=None, show=None, save_settings=None):
        # See SetupWindow: handlers can fire mid-parse, before ``_ready`` exists.
        BridgedWindow.__init__(self, RESULTS_XAML, bridge=bridge, uiapp=uiapp, title=TITLE)
        self._analysis = analysis or {}
        self._config = config or {}
        self._settings = dict(settings or {})
        self._rescan = rescan
        self._show = show
        self._save_settings = save_settings
        self._near = state.clamp_near(self._config.get("near_mm", state.DEFAULT_NEAR_MM))
        self._hops = state.clamp_hops(self._config.get("hops", state.DEFAULT_HOPS))
        self._updating = False
        self.Closed += self._on_closed

        self._updating = True
        self.NearBox.Text = u"{0}".format(self._near)
        self.HopsBox.Text = u"{0}".format(self._hops)
        self._updating = False
        self._ready = True
        self._render()

    # -- rendering ---------------------------------------------------------

    def _render(self):
        report = state.classify(self._analysis, self._near, self._hops)
        query = state.safe_text(self.SearchBox.Text)
        shown = state.filter_report(report, query) if query.strip() else report

        self.ContentPanel.Children.Clear()
        self._show_buttons = []
        buckets = [bucket for bucket in shown["buckets"] if bucket["items"]]
        if not buckets:
            if query.strip():
                message = u"Nothing matches '{0}'.".format(query.strip())
            elif report["crossing_count"] == 0:
                message = (u"No duct crosses a rated barrier. If that is a surprise, check the ticked "
                           u"wall types and line styles, and that the link is loaded.")
            else:
                message = u"All {0} crossings carry a fire damper.".format(report["crossing_count"])
            self._set_empty(True, message)
        else:
            self._set_empty(False, u"")
            for bucket in buckets:
                count = len(bucket["items"])
                self.ContentPanel.Children.Add(bucket_expander(
                    dict(bucket, noun=u"crossing" if count == 1 else u"crossings"), self._expanded, self._row))
        notes = state.scan_notes(self._analysis, self._config, self._analysis.get("links") or [])
        self.ContentPanel.Children.Add(notes_expander(notes, self._expanded))

        self.SummaryText.Text = state.summary_line(self._analysis, report)
        if not self._busy:
            self._update_status()

    def _row(self, item):
        return report_row(item, self._show_click, self._busy, self._show_buttons)

    def _update_status(self):
        if self._bridge is None:
            BridgedWindow._update_status(self)
        else:
            self.StatusText.Text = u"Within {0} mm, at most {1} connection(s). Nothing was changed.".format(
                self._near, self._hops)

    # -- tolerance ---------------------------------------------------------

    def _set_near(self, value):
        self._near = state.clamp_near(value)
        self._updating = True
        try:
            self.NearBox.Text = u"{0}".format(self._near)
        finally:
            self._updating = False
        self._settings["near_mm"] = self._near
        self._render()

    def near_changed(self, sender, args):
        del sender, args
        if not self._is_ready() or self._updating:
            return
        value = _parse_near(self.NearBox.Text)
        if value is None:
            self.StatusText.Text = u"The tolerance must be a whole number of millimetres, 1 or more."
            return
        self._near = value
        self._settings["near_mm"] = value
        self._render()

    def near_down_click(self, sender, args):
        del sender, args
        self._set_near(self._near - 100)

    def near_up_click(self, sender, args):
        del sender, args
        self._set_near(self._near + 100)

    def hops_changed(self, sender, args):
        del sender, args
        if not self._is_ready() or self._updating:
            return
        value = _parse_hops(self.HopsBox.Text)
        if value is None:
            self.StatusText.Text = u"The connection limit must be a whole number, 0 or more."
            return
        self._hops = value
        self._settings["hops"] = value
        self._render()

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

    # -- actions -------------------------------------------------------------

    def _show_click(self, sender, args):
        del args
        item = getattr(sender, "Tag", None)
        if item is None or self._show is None:
            return
        show = item.get("show") or {}
        if not show.get("host_ids") and show.get("link_element_id") is None and not show.get("point"):
            self.StatusText.Text = u"Nothing to show for this row."
            return

        def _work(uiapp):
            return self._show(uiapp, show)

        def _done(shown):
            if shown:
                self.StatusText.Text = u"Selected {0} element(s) and framed the crossing.".format(
                    len(show.get("host_ids") or []) + (1 if show.get("link_element_id") is not None else 0))
            else:
                self.StatusText.Text = (u"Framed the crossing; the elements could not be selected "
                                        u"(hidden in this view, or the link cannot be selected on this Revit).")

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
        if self._save_settings is not None:
            try:
                self._save_settings(self._settings)
            except Exception:
                pass
        REGISTRY.forget(self)
        self._dispose_bridge()


# ---------------------------------------------------------------- launcher


def close_open_window():
    """A new check replaces the previous report; returns True if one closed."""
    return REGISTRY.close_open()


def show_results(analysis, config, settings, bridge=None, uiapp=None, rescan=None,
                 show=None, save_settings=None):
    """Open the report modeless; modal when no ExternalEvent could be made."""
    REGISTRY.close_open()
    window = ResultsWindow(analysis, config, settings, bridge=bridge, uiapp=uiapp,
                           rescan=rescan, show=show, save_settings=save_settings)
    if bridge is None:
        window.ShowDialog()
        return window
    REGISTRY.remember(window)
    window.Show()
    return window
