# -*- coding: utf-8 -*-
"""The Update Circuit Rating window.

Presentation only: the model is scanned once before the window opens and the
decisions are made in ``circuit_rating_state``, so changing the source
parameter or a target here never touches the document.
"""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

from pyrevit import forms
from pyrevit.framework import Windows

import circuit_rating_state as state


TITLE = "Update Circuit Rating"


def _combo_key(combo):
    item = combo.SelectedItem
    if item is None:
        return None
    return getattr(item, "Tag", None)


def _add_combo_item(combo, label, key):
    item = Windows.Controls.ComboBoxItem()
    item.Content = label
    item.Tag = key
    combo.Items.Add(item)
    return item


class TargetOption(object):
    """One tickable target circuit parameter.

    Plain object: the list is built once and only WPF ever writes
    ``is_selected``, so there is nothing to notify back.
    """

    def __init__(self, option):
        self.key = option.get("key")
        self.label = option.get("label") or u""
        self.is_selected = bool(option.get("is_default"))


class CircuitRow(forms.Reactive, state.CircuitRowBase):
    """Grid row with WPF change notification.

    All/None writes ``is_selected`` from code, so the row has to notify;
    reassigning ``ItemsSource`` instead would drop the grid's scroll position
    on every click.
    """

    def __init__(self, circuit_id, panel, circuit_number, load_name,
                 donor_label, source_value):
        state.CircuitRowBase.__init__(
            self, circuit_id, panel, circuit_number, load_name,
            donor_label, source_value)

    def notify(self, attr):
        try:
            self.OnPropertyChanged(attr)
        except Exception:
            pass


class UpdateCircuitRatingWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, circuits_data, source_options,
                 target_options):
        self._is_ready = False
        self.result = None
        forms.WPFWindow.__init__(self, xaml_file_name)

        self._circuits_data = circuits_data or []
        self._rows = []
        self._skipped = 0

        self._target_rows = [TargetOption(option)
                             for option in target_options or []]
        self.TargetParamList.ItemsSource = self._target_rows
        # A checkbox inside an ItemTemplate has no handler of its own, so the
        # click is caught once at the list level (the Sheet Manager idiom).
        self.TargetParamList.AddHandler(
            Windows.Controls.Primitives.ButtonBase.ClickEvent,
            Windows.RoutedEventHandler(self.target_changed))

        for option in source_options or []:
            _add_combo_item(
                self.SourceParamCombo, option.get("label"), option.get("key"))
        if source_options:
            self.SourceParamCombo.SelectedIndex = 0

        self._is_ready = True
        self._rebuild(reset_selection=True)

    # ------------------------------------------------------------- internals

    def _checked_targets(self):
        return [{"key": row.key, "label": row.label}
                for row in self._target_rows if row.is_selected]

    def _capture_selection(self):
        return set(row.circuit_id for row in self._rows if row.is_selected)

    def _rebuild(self, reset_selection=False):
        # A new source parameter lists a different set of circuits, so the
        # tick state is meaningless and everything starts checked again.
        selected_ids = None if reset_selection else self._capture_selection()
        records = state.build_records(
            self._circuits_data, _combo_key(self.SourceParamCombo))
        self._rows, self._skipped = state.build_rows(
            records,
            self._checked_targets(),
            row_factory=CircuitRow,
            selected_ids=selected_ids,
        )
        self.CircuitsGrid.ItemsSource = None
        self.CircuitsGrid.ItemsSource = self._rows
        self._refresh_counts()

    def _refresh_counts(self):
        self.CountText.Text = \
            u"{0} circuit(s) listed, {1} skipped (no circuited element " \
            u"carries a value).".format(len(self._rows), self._skipped)

    def _set_all(self, value):
        for row in self._rows:
            row.is_selected = value
            row.notify("is_selected")

    # -------------------------------------------------------------- handlers

    def source_param_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._rebuild(reset_selection=True)

    def target_changed(self, sender, args):
        del sender, args
        if not self._is_ready:
            return
        self._rebuild()

    def all_clicked(self, sender, args):
        del sender, args
        self._set_all(True)

    def none_clicked(self, sender, args):
        del sender, args
        self._set_all(False)

    def update_clicked(self, sender, args):
        del sender, args
        targets = self._checked_targets()
        if not targets:
            forms.alert("Tick at least one target circuit parameter.",
                        title=TITLE)
            return
        plan = state.build_plan(self._rows, targets)
        if not plan:
            forms.alert("Tick at least one circuit to update.", title=TITLE)
            return
        self.result = {
            "plan": plan,
            "target_labels": dict(
                (target["key"], target["label"]) for target in targets),
        }
        self.Close()

    def cancel_clicked(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


class ZeroRatingReportWindow(forms.WPFWindow):
    """What is still at zero amps once the run is over."""

    def __init__(self, xaml_file_name, summary_text, rows):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.SummaryText.Text = summary_text or u""
        rows = list(rows or [])
        if rows:
            self.ZeroCountText.Text = \
                u"{0} circuit(s) are at zero amp after updating:".format(
                    len(rows))
        else:
            self.ZeroCountText.Text = \
                u"No circuit is left at zero amp."
        self.ZeroCircuitsGrid.ItemsSource = rows
        self._is_ready = True

    def close_clicked(self, sender, args):
        del sender, args
        self.Close()


def show_zero_rating_report(summary_text, rows):
    ZeroRatingReportWindow(
        "ZeroRatingReportWindow.xaml", summary_text, rows).ShowDialog()


def show_update_circuit_rating(circuits_data, source_options, target_options):
    window = UpdateCircuitRatingWindow(
        "UpdateCircuitRatingWindow.xaml",
        circuits_data,
        source_options,
        target_options,
    )
    window.ShowDialog()
    return window.result
