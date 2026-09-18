# -*- coding: utf-8 -*-
"""WPF for Damper Check: the setup checklist and the modeless report.

No Revit API lives here (test-pinned).  The report draws the dicts
``damper_check_state`` produces, and the two things that must touch the
model - Show and Refresh - are callables the launcher hands in, run through
the ``ExternalEventBridge`` the launcher created while it still had an API
context.  The folding checklist, the report expanders, the bridged window
and the one-window registry are ``easybim.check_windows``, shared with Fire
Damper Check; this module is what is Damper Check's own: the chips, the
limit N, and the meaning of each bucket.
"""

from __future__ import print_function

from pyrevit import forms
from pyrevit.framework import Windows

from easybim.check_windows import BridgedWindow
from easybim.check_windows import ChecklistPanel
from easybim.check_windows import DocumentGone
from easybim.check_windows import WindowRegistry
from easybim.check_windows import bucket_expander
from easybim.check_windows import notes_expander
from easybim.check_windows import parse_positive_int
from easybim.check_windows import report_row

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

REGISTRY = WindowRegistry(ACTIVE_ENVVAR, WINDOW_ENVVAR)

# Kept as a name of this module for the launcher and the tests.
DocumentGone = DocumentGone


def _parse_threshold(text):
    """The typed limit as an int, or None when it is not one yet."""
    return parse_positive_int(text, clamp=state.clamp_threshold)


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
        self._types = ChecklistPanel(
            self.TypePanel, self._rows, state.group_types,
            count_block=self.TypeCountText, count_text=state.selection_count_text,
            search_box=self.TypeSearchBox,
            empty_text=u"No family with duct connectors matches.",
            no_rows_text=u"This model has no family with duct connectors - nothing to tick.")

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
        self._types.render()

    def _is_ready(self):
        return bool(getattr(self, "_ready", False))

    # -- handlers ----------------------------------------------------------

    def type_search_changed(self, sender, args):
        del sender, args
        if self._is_ready():
            self._types.render()

    def type_all_click(self, sender, args):
        del sender, args
        self._types.set_visible(True)

    def type_none_click(self, sender, args):
        del sender, args
        self._types.set_visible(False)

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


class ResultsWindow(BridgedWindow):
    """The report: one expander per bucket, Show per row, live N."""

    def __init__(self, analysis, config, settings, bridge=None, uiapp=None,
                 rescan=None, show=None, save_settings=None, ignore=None):
        # See SetupWindow: handlers can fire mid-parse, before ``_ready`` exists.
        BridgedWindow.__init__(self, RESULTS_XAML, bridge=bridge, uiapp=uiapp, title=TITLE)
        self._analysis = analysis or {}
        self._ignore = ignore
        self._ignored = set(state.safe_text(key) for key in (analysis or {}).get("ignored") or [])
        self._config = config or {}
        self._settings = dict(settings or {})
        self._rescan = rescan
        self._show = show
        self._save_settings = save_settings
        self._threshold = state.clamp_threshold(self._config.get("threshold", state.DEFAULT_THRESHOLD))
        self._updating_threshold = False
        self.Closed += self._on_closed

        self._updating_threshold = True
        self.ThresholdBox.Text = u"{0}".format(self._threshold)
        self._updating_threshold = False
        self._ready = True
        self._render()

    # -- rendering ---------------------------------------------------------

    def _render(self):
        report = state.classify(self._analysis, self._threshold, self._ignored)
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
                self.ContentPanel.Children.Add(bucket_expander(
                    dict(bucket, noun=u"terminal" if len(bucket["items"]) == 1 else u"terminals"),
                    self._expanded, self._row))
        self.ContentPanel.Children.Add(notes_expander(self._notes(report), self._expanded))

        self.SummaryText.Text = state.summary_line(self._analysis, report)
        if not self._busy:
            self._update_status()

    def _row(self, item):
        suffix = u"  (network has loops - counts approximate)" if item.get("approx") else u""
        return report_row(item, self._show_click, self._busy, self._show_buttons,
                          detail_suffix=suffix, extra_buttons=self._row_buttons(item))

    def _row_buttons(self, item):
        """Ignore on a live row; Restore on one already set aside."""
        if self._ignore is None:
            return ()
        if item.get("is_ignored"):
            return ((u"Restore", u"Put this finding back in the group it belongs to.",
                     self._restore_click, 84),)
        return ((u"Ignore", u"Set this finding aside. The decision is stored in this model, so it "
                            u"comes back next time and reaches the team after a Sync to Central.",
                 self._ignore_click, 84),)

    def _notes(self, report):
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
        set_aside = report.get("ignored_count") or 0
        if set_aside:
            notes.append(u"{0} finding(s) set aside on review. The list is stored in this model, so it "
                         u"comes back next time and reaches the team after a Sync to Central.".format(set_aside))
        stale = list(report.get("stale_ignored") or [])
        if stale:
            notes.append(u"{0} set-aside record(s) in this model match no finding now - fixed, deleted, "
                         u"or out of the current scope.".format(len(stale)))
        notes.append(u"Linked models are not traversed. The scan itself changes nothing; only the "
                     u"findings you set aside are written to the model.")
        return notes

    def _update_status(self):
        if self._bridge is None:
            BridgedWindow._update_status(self)
        else:
            self.StatusText.Text = u"Limit {0}. The scan changes nothing in the model.".format(
                self._threshold)

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

    def _ignore_click(self, sender, args):
        del args
        self._set_ignored(getattr(sender, "Tag", None), True)

    def _restore_click(self, sender, args):
        del args
        self._set_ignored(getattr(sender, "Tag", None), False)

    def _set_ignored(self, item, on):
        """Write the decision into the model, then move the row - in that
        order, so a row never reads as set aside when nothing was stored."""
        if item is None or self._ignore is None:
            return
        key = state.safe_text(item.get("key"))
        if not key:
            self.StatusText.Text = u"This row has no stable key, so it cannot be set aside."
            return

        def _work(uiapp):
            return self._ignore(uiapp, key, on)

        def _done(result):
            ok, keys, reason = result
            if keys is not None:
                self._ignored = set(state.safe_text(value) for value in keys)
            self._render()
            if ok:
                self.StatusText.Text = (u"Set aside, and saved in the model."
                                        if on else u"Restored, and saved in the model.")
            else:
                self.StatusText.Text = u"Not saved in the model: {0}".format(
                    reason or u"unknown error")

        self._run_in_revit(u"Ignore" if on else u"Restore", _work, _done, quiet=True)

    def refresh_click(self, sender, args):
        del sender, args
        if self._rescan is None:
            return
        self._run_in_revit(u"Refresh", self._rescan, self._refresh_done)

    def _refresh_done(self, analysis):
        if analysis:
            self._analysis = analysis
            if analysis.get("ignored") is not None:
                self._ignored = set(state.safe_text(key) for key in analysis["ignored"])
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
                 show=None, save_settings=None, ignore=None):
    """Open the report modeless; modal when no ExternalEvent could be made."""
    REGISTRY.close_open()
    window = ResultsWindow(analysis, config, settings, bridge=bridge, uiapp=uiapp,
                           rescan=rescan, show=show, save_settings=save_settings, ignore=ignore)
    if bridge is None:
        window.ShowDialog()
        return window
    REGISTRY.remember(window)
    window.Show()
    return window
