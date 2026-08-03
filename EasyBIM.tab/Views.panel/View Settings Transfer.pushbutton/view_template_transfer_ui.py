# -*- coding: utf-8 -*-
"""Main window for view / view template setting transfer."""

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import RoutedEventHandler
from System.Windows.Controls.Primitives import ButtonBase

from pyrevit import forms

import view_template_transfer_dialogs as dialogs
import view_template_transfer_revit as vtrevit
import view_template_transfer_state as state


TITLE = "View Settings Transfer"
XAML_FILE = "ViewTemplateTransferWindow.xaml"


class ViewTemplateTransferWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, doc):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.doc = doc
        self.is_ready = False
        self.templates = vtrevit.collect_view_templates(doc)
        self.views = vtrevit.collect_eligible_views(doc)
        self.target_rows = []
        self.target_groups = []
        self._selective_state = None
        self._payloads = {}

        if not state.has_transfer_candidates(len(self.views), len(self.templates)):
            forms.alert(
                "At least two views or view templates are required: "
                "one source and one target.",
                title=TITLE,
            )
            return

        self.source_view_cb.ItemsSource = self.views
        self.source_template_cb.ItemsSource = self.templates
        if self.views:
            self.source_view_cb.SelectedIndex = self._active_view_index()
        if self.templates:
            self.source_template_cb.SelectedIndex = 0

        self.source_view_rb.IsEnabled = bool(self.views)
        self.source_template_rb.IsEnabled = bool(self.templates)
        self.target_views_rb.IsEnabled = bool(self.views)
        self.target_templates_rb.IsEnabled = bool(self.templates)

        if self.views:
            self.source_view_rb.IsChecked = True
        else:
            self.source_template_rb.IsChecked = True
        if self.views:
            self.target_views_rb.IsChecked = True
        else:
            self.target_templates_rb.IsChecked = True

        # Target checkboxes carry no event attributes; refresh the count via
        # a tree-level click handler instead.
        self.target_tv.AddHandler(
            ButtonBase.ClickEvent,
            RoutedEventHandler(self.target_tree_click))

        self._sync_source_controls()
        self._bind_view_type_filters()
        self._rebuild_targets()
        self.status_tb.Text = (
            "Pick a source, check targets, then run Transfer All or "
            "Selective Transfer."
        )
        self.is_ready = True
        self._is_ready = True

    # ------------------------------------------------------------ helpers

    def _active_view_index(self):
        active_id = vtrevit.get_active_view_id_int(self.doc)
        if active_id is not None:
            for index, option in enumerate(self.views):
                if option.element_id_int == active_id:
                    return index
        return 0

    def _source_kind(self):
        if bool(self.source_view_rb.IsChecked):
            return state.SOURCE_KIND_VIEW
        return state.SOURCE_KIND_TEMPLATE

    def _target_kind(self):
        if bool(self.target_views_rb.IsChecked):
            return state.TARGET_KIND_VIEWS
        return state.TARGET_KIND_TEMPLATES

    def _selected_source_option(self):
        try:
            if self._source_kind() == state.SOURCE_KIND_VIEW:
                return self.source_view_cb.SelectedItem
            return self.source_template_cb.SelectedItem
        except Exception:
            return None

    def _current_target_options(self):
        if self._target_kind() == state.TARGET_KIND_VIEWS:
            return self.views
        return self.templates

    def _selected_view_type_filter(self):
        try:
            value = self.view_type_filter_cb.SelectedItem
        except Exception:
            value = None
        return state.safe_text(value) or state.ALL_VIEW_TYPES

    def _sync_source_controls(self):
        source_kind = self._source_kind()
        self.source_view_cb.IsEnabled = source_kind == state.SOURCE_KIND_VIEW
        self.source_template_cb.IsEnabled = (
            source_kind == state.SOURCE_KIND_TEMPLATE
        )

    def _bind_view_type_filters(self):
        values = state.get_view_type_filter_values(self._current_target_options())
        self.view_type_filter_cb.ItemsSource = values
        self.view_type_filter_cb.SelectedIndex = 0

    def _rebuild_targets(self):
        source_option = self._selected_source_option()
        self.target_rows = state.build_target_rows(
            self._current_target_options(),
            self._target_kind(),
            source_view_type_name=state.safe_text(
                getattr(source_option, "view_type_name", "")
            ),
            source_id_int=getattr(source_option, "element_id_int", None),
        )
        self._regroup_targets()

    def _regroup_targets(self):
        self.target_groups = state.group_target_templates_by_view_type(
            self.target_rows,
            selected_source_id_int=None,
            view_type_filter=self._selected_view_type_filter(),
        )
        self.target_tv.ItemsSource = None
        self.target_tv.ItemsSource = self.target_groups
        self._refresh_counts()

    def _refresh_counts(self):
        selected = len(
            [row for row in self.target_rows if bool(row.is_selected)]
        )
        if self._target_kind() == state.TARGET_KIND_VIEWS:
            noun = "target view(s)"
        else:
            noun = "target view template(s)"
        self.target_count_tb.Text = "{} of {} {} selected.".format(
            selected, len(self.target_rows), noun
        )

    def _reset_selective_state(self):
        self._selective_state = None
        self._payloads = {}

    def _selected_targets(self):
        return [
            row
            for row in self.target_rows
            if bool(row.is_selected) and row.is_checkable
        ]

    def _ensure_selective_state(self):
        source_option = self._selected_source_option()
        source_element = getattr(source_option, "element", None)
        if source_element is None:
            forms.alert("Select a source first.", title=TITLE)
            return None
        source_kind = self._source_kind()
        source_id = getattr(source_option, "element_id_int", None)
        if self._selective_state is not None and self._selective_state.matches_source(
            source_kind, source_id
        ):
            return self._selective_state
        try:
            if source_kind == state.SOURCE_KIND_VIEW:
                rows = vtrevit.build_selection_rows_for_view(self.doc, source_element)
            else:
                rows = vtrevit.build_selection_rows_for_template(source_element)
        except vtrevit.SourceNotSupportedError as ex:
            forms.alert(
                "This view cannot be used as a transfer source: {}".format(
                    state.safe_text(ex)
                ),
                title=TITLE,
            )
            return None
        if not rows:
            forms.alert(
                "The source does not expose any transferable settings.",
                title=TITLE,
            )
            return None
        self._selective_state = state.SelectiveState(source_kind, source_id, rows)
        self._payloads = {}
        return self._selective_state

    def _run_transfer(self, plan):
        source_option = self._selected_source_option()
        source_element = getattr(source_option, "element", None)
        if source_element is None:
            forms.alert("Select a source first.", title=TITLE)
            return
        targets = self._selected_targets()
        if not targets:
            forms.alert("Check at least one target.", title=TITLE)
            return
        summary = vtrevit.execute_transfer(
            self.doc,
            self._source_kind(),
            source_element,
            self._target_kind(),
            targets,
            plan,
            payload_cache=self._payloads,
        )
        forms.alert(summary.message(), title=TITLE)
        self.status_tb.Text = "Transferred to {} target(s).".format(
            summary.transferred_count
        )
        self._rebuild_targets()

    # ------------------------------------------------------------- events

    def source_mode_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._sync_source_controls()
        self._reset_selective_state()
        self._rebuild_targets()

    def source_view_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        if self._source_kind() != state.SOURCE_KIND_VIEW:
            return
        self._reset_selective_state()
        self._rebuild_targets()

    def source_template_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        if self._source_kind() != state.SOURCE_KIND_TEMPLATE:
            return
        self._reset_selective_state()
        self._rebuild_targets()

    def target_mode_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._bind_view_type_filters()
        self._rebuild_targets()

    def view_type_filter_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._regroup_targets()

    def target_tree_click(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._refresh_counts()

    def check_all_targets_click(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        state.set_visible_template_selection(
            self.target_rows,
            self._selected_view_type_filter(),
            True,
            skip_locked=True,
        )
        self._regroup_targets()

    def clear_targets_click(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        state.set_visible_template_selection(
            self.target_rows,
            self._selected_view_type_filter(),
            False,
        )
        self._regroup_targets()

    def selective_transfer_click(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        if not self._selected_targets():
            forms.alert("Check at least one target first.", title=TITLE)
            return
        selective_state = self._ensure_selective_state()
        if selective_state is None:
            return
        source_option = self._selected_source_option()
        dialog = dialogs.SelectiveTransferDialog(
            "SelectiveTransferDialog.xaml",
            self.doc,
            self._source_kind(),
            getattr(source_option, "element", None),
            state.safe_text(getattr(source_option, "name", "")),
            selective_state,
            self._payloads,
        )
        dialog.ShowDialog()
        if dialog.result is None:
            return
        self._run_transfer(dialog.result)

    def transfer_all_click(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._run_transfer(state.TransferPlan(transfer_all=True))

    def cancel_click(self, sender, args):
        del sender, args
        self.Close()


def show_window(doc):
    window = ViewTemplateTransferWindow(XAML_FILE, doc)
    if getattr(window, "is_ready", False):
        window.ShowDialog()
