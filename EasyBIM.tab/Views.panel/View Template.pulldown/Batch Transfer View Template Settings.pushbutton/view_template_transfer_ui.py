# -*- coding: utf-8 -*-
"""WPF window for batch view-template setting transfer."""

from pyrevit import forms

from view_template_transfer_revit import build_template_parameter_rows
from view_template_transfer_revit import collect_view_templates
from view_template_transfer_revit import count_views_assigned_to_template
from view_template_transfer_revit import transfer_template_settings
from view_template_transfer_state import ALL_VIEW_TYPES
from view_template_transfer_state import get_selected_parameter_ids
from view_template_transfer_state import get_selected_template_options
from view_template_transfer_state import get_view_type_filter_values
from view_template_transfer_state import group_target_templates_by_view_type
from view_template_transfer_state import safe_text
from view_template_transfer_state import set_visible_template_selection


TITLE = "Batch Transfer View Template Settings"


class BatchTransferViewTemplateSettingsWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, doc):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.doc = doc
        self.is_ready = False
        self.templates = collect_view_templates(doc)
        self.parameter_rows = []
        self.target_templates = []
        self.target_groups = []

        if len(self.templates) < 2:
            forms.alert(
                "At least two view templates are required: one source and one target.",
                title=TITLE,
            )
            return

        self.source_template_cb.ItemsSource = self.templates
        self.source_template_cb.SelectedIndex = 0
        self._bind_view_type_filters()
        self._refresh_for_source()
        self.status_tb.Text = "Choose settings and target templates, then click Transfer."
        self.is_ready = True
        self._is_ready = True

    def _selected_source_option(self):
        try:
            return self.source_template_cb.SelectedItem
        except Exception:
            return None

    def _selected_source_template(self):
        option = self._selected_source_option()
        return getattr(option, "element", None)

    def _selected_source_id_int(self):
        option = self._selected_source_option()
        return getattr(option, "element_id_int", None)

    def _selected_view_type_filter(self):
        try:
            value = self.view_type_filter_cb.SelectedItem
        except Exception:
            value = None
        return safe_text(value) or ALL_VIEW_TYPES

    def _bind_view_type_filters(self):
        values = get_view_type_filter_values(self.templates)
        self.view_type_filter_cb.ItemsSource = values
        self.view_type_filter_cb.SelectedIndex = 0

    def _refresh_for_source(self):
        source_template = self._selected_source_template()
        if source_template is None:
            return

        self.parameter_rows = build_template_parameter_rows(source_template)
        self.settings_lv.ItemsSource = None
        self.settings_lv.ItemsSource = self.parameter_rows

        self.assigned_count_tb.Text = safe_text(
            count_views_assigned_to_template(self.doc, source_template)
        )
        self._refresh_targets()
        self._refresh_counts()

    def _refresh_targets(self):
        source_id_int = self._selected_source_id_int()
        self.target_templates = [
            option
            for option in self.templates
            if source_id_int is None or option.element_id_int != source_id_int
        ]
        self.target_groups = group_target_templates_by_view_type(
            self.target_templates,
            selected_source_id_int=None,
            view_type_filter=self._selected_view_type_filter(),
        )
        self.target_tv.ItemsSource = None
        self.target_tv.ItemsSource = self.target_groups

    def _refresh_counts(self):
        selected_settings = len(get_selected_parameter_ids(self.parameter_rows))
        total_settings = len(self.parameter_rows)
        selected_targets = len(get_selected_template_options(self.target_templates))
        total_targets = len(self.target_templates)
        self.settings_count_tb.Text = "{} of {} settings selected.".format(
            selected_settings,
            total_settings,
        )
        self.target_count_tb.Text = "{} of {} target templates selected.".format(
            selected_targets,
            total_targets,
        )

    def source_template_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._refresh_for_source()

    def view_type_filter_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._refresh_targets()
        self._refresh_counts()

    def check_all_settings_click(self, sender, args):
        del sender, args
        for row in self.parameter_rows:
            row.is_selected = True
        self.settings_lv.ItemsSource = None
        self.settings_lv.ItemsSource = self.parameter_rows
        self._refresh_counts()

    def clear_settings_click(self, sender, args):
        del sender, args
        for row in self.parameter_rows:
            row.is_selected = False
        self.settings_lv.ItemsSource = None
        self.settings_lv.ItemsSource = self.parameter_rows
        self._refresh_counts()

    def check_all_targets_click(self, sender, args):
        del sender, args
        set_visible_template_selection(
            self.target_templates,
            self._selected_view_type_filter(),
            True,
        )
        self._refresh_targets()
        self._refresh_counts()

    def clear_targets_click(self, sender, args):
        del sender, args
        set_visible_template_selection(
            self.target_templates,
            self._selected_view_type_filter(),
            False,
        )
        self._refresh_targets()
        self._refresh_counts()

    def transfer_click(self, sender, args):
        del sender, args
        selected_parameter_ids = get_selected_parameter_ids(self.parameter_rows)
        selected_targets = get_selected_template_options(self.target_templates)

        if not selected_parameter_ids:
            forms.alert("Select at least one setting to transfer.", title=TITLE)
            return
        if not selected_targets:
            forms.alert("Select at least one target view template.", title=TITLE)
            return

        source_template = self._selected_source_template()
        target_templates = [
            option.element
            for option in selected_targets
            if option.element is not None
        ]
        summary = transfer_template_settings(
            self.doc,
            source_template,
            target_templates,
            selected_parameter_ids,
        )
        forms.alert(summary.message(), title=TITLE)
        self._refresh_for_source()
        self._refresh_counts()

    def cancel_click(self, sender, args):
        del sender, args
        self.Close()
