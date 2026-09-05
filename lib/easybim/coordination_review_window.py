# -*- coding: utf-8 -*-
"""WPF Coordination Review dialog."""

import os

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import forms
from pyrevit import script
from System.Windows import CornerRadius, FontWeights, TextWrapping, Thickness, Visibility
from System.Windows.Controls import Border, Button, Dock, DockPanel, Expander, StackPanel, TextBlock
from System.Windows.Media import Brushes

from easybim import coordination_review_model
from easybim import coordination_review_show


LOGGER = script.get_logger()
XAML_PATH = os.path.join(os.path.dirname(__file__), "ui", "coordination_review.xaml")
DEFAULT_STATUS_TEXT = (
    "Ready. Review the full issue list below, or click Show to select a link "
    "and open Revit's Coordination Review for it."
)


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value):
    try:
        return int(value)
    except Exception:
        return None


def _doc_identity(doc):
    if doc is None:
        return ("", "")
    return (
        _safe_text(getattr(doc, "PathName", "")).lower(),
        _safe_text(getattr(doc, "Title", "")).lower(),
    )


def _same_doc(doc_a, doc_b):
    if doc_a is doc_b:
        return True
    return _doc_identity(doc_a) == _doc_identity(doc_b)


def _build_badge(text, background, border_brush, foreground):
    border = Border()
    border.Background = background
    border.BorderBrush = border_brush
    border.BorderThickness = Thickness(1)
    border.CornerRadius = CornerRadius(10)
    border.Padding = Thickness(8, 2, 8, 2)
    border.Margin = Thickness(8, 0, 0, 0)

    label = TextBlock()
    label.Text = _safe_text(text)
    label.FontSize = 11
    label.FontWeight = FontWeights.SemiBold
    label.Foreground = foreground
    border.Child = label
    return border


class CoordinationReviewWindow(forms.WPFWindow):
    def __init__(self, report, doc=None, uiapp=None):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.Topmost = True
        self.report = dict(report or {})
        self.doc = doc
        self.uiapp = uiapp
        self.uidoc = getattr(uiapp, "ActiveUIDocument", None) if uiapp else None
        self._all_problem_links = coordination_review_model.build_problem_link_records(self.report)
        self._link_expansion_state = {}
        self._issue_expansion_state = {}
        self._visible_link_keys = []
        self._visible_issue_keys = []

        self._populate_summary()
        self._refresh_content()

    def _populate_summary(self):
        self.doc_title_tb.Text = _safe_text(self.report.get("doc_title")) or "(Unknown)"
        self.matching_warnings_tb.Text = str(_safe_int(self.report.get("total_matching_warnings")) or 0)
        self.problem_links_tb.Text = str(len(self._all_problem_links))
        self.assignments_tb.Text = str(_safe_int(self.report.get("total_link_assignments")) or 0)

    def _link_key(self, link_record):
        return _safe_text(link_record.get("filter_value"))

    def _issue_key(self, link_record, issue_record):
        return "{}::{}".format(self._link_key(link_record), _safe_text(issue_record.get("text")))

    def _build_link_header(self, link_record):
        header = DockPanel()
        header.LastChildFill = True

        badge = _build_badge(
            "{} warning(s)".format(link_record.get("visible_total", 0)),
            Brushes.AliceBlue,
            Brushes.LightSteelBlue,
            Brushes.SteelBlue,
        )
        DockPanel.SetDock(badge, Dock.Right)
        header.Children.Add(badge)

        title = TextBlock()
        title.Text = "{}".format(link_record.get("name", "Unknown Link"))
        title.FontSize = 15
        title.FontWeight = FontWeights.SemiBold
        header.Children.Add(title)
        return header

    def _build_issue_header(self, issue_record):
        header = StackPanel()

        title = TextBlock()
        title.Text = _safe_text(issue_record.get("text"))
        title.FontSize = 13
        title.FontWeight = FontWeights.SemiBold
        title.TextWrapping = TextWrapping.Wrap
        header.Children.Add(title)

        subtitle = TextBlock()
        subtitle.Text = "{} warning(s) | {} instance(s)".format(
            issue_record.get("count", 0),
            issue_record.get("total_instances", 0),
        )
        subtitle.Margin = Thickness(0, 4, 0, 0)
        subtitle.Foreground = Brushes.DimGray
        header.Children.Add(subtitle)
        return header

    def _make_link_expansion_handler(self, link_key, is_expanded):
        def _handler(sender, args):
            del sender, args
            self._link_expansion_state[link_key] = bool(is_expanded)

        return _handler

    def _make_issue_expansion_handler(self, issue_key, is_expanded):
        def _handler(sender, args):
            del sender, args
            self._issue_expansion_state[issue_key] = bool(is_expanded)

        return _handler

    def _make_show_handler(self, instance_id):
        def _handler(sender, args):
            del sender, args
            self._show_instance(instance_id)

        return _handler

    def _create_instance_row(self, instance_row):
        container = Border()
        container.BorderBrush = Brushes.Gainsboro
        container.BorderThickness = Thickness(1)
        container.Background = Brushes.WhiteSmoke
        container.Padding = Thickness(10, 8, 10, 8)
        container.Margin = Thickness(0, 0, 0, 6)

        row_panel = DockPanel()
        row_panel.LastChildFill = True

        show_btn = Button()
        show_btn.Content = _safe_text(instance_row.get("show_label")) or "Show"
        show_btn.Width = 80
        show_btn.Height = 28
        DockPanel.SetDock(show_btn, Dock.Right)
        show_btn.Click += self._make_show_handler(instance_row.get("instance_id"))
        row_panel.Children.Add(show_btn)

        label = TextBlock()
        label.Text = "Element Id {}".format(instance_row.get("label", ""))
        label.FontWeight = FontWeights.SemiBold
        row_panel.Children.Add(label)

        container.Child = row_panel
        return container

    def _create_issue_expander(self, link_record, issue_record):
        expander = Expander()
        expander.Margin = Thickness(0, 0, 0, 8)
        expander.Header = self._build_issue_header(issue_record)
        issue_key = self._issue_key(link_record, issue_record)
        expander.IsExpanded = self._issue_expansion_state.get(
            issue_key,
            bool(issue_record.get("is_expanded", True)),
        )
        expander.Expanded += self._make_issue_expansion_handler(issue_key, True)
        expander.Collapsed += self._make_issue_expansion_handler(issue_key, False)

        body = Border()
        body.BorderBrush = Brushes.Gainsboro
        body.BorderThickness = Thickness(1)
        body.Background = Brushes.White
        body.Padding = Thickness(10)
        body.Margin = Thickness(0, 6, 0, 0)

        stack = StackPanel()
        for instance_row in list(issue_record.get("instance_rows", []) or []):
            stack.Children.Add(self._create_instance_row(instance_row))

        overflow_label = _safe_text(issue_record.get("overflow_label"))
        if overflow_label:
            overflow_tb = TextBlock()
            overflow_tb.Text = "{} instance(s) were omitted from this view.".format(overflow_label)
            overflow_tb.Foreground = Brushes.DimGray
            overflow_tb.Margin = Thickness(0, 2, 0, 0)
            stack.Children.Add(overflow_tb)

        body.Child = stack
        expander.Content = body
        return expander

    def _create_link_expander(self, link_record):
        expander = Expander()
        expander.Margin = Thickness(0, 0, 0, 10)
        expander.Header = self._build_link_header(link_record)
        link_key = self._link_key(link_record)
        expander.IsExpanded = self._link_expansion_state.get(
            link_key,
            bool(link_record.get("is_expanded", True)),
        )
        expander.Expanded += self._make_link_expansion_handler(link_key, True)
        expander.Collapsed += self._make_link_expansion_handler(link_key, False)

        body = Border()
        body.BorderBrush = Brushes.Gainsboro
        body.BorderThickness = Thickness(1)
        body.Background = Brushes.White
        body.Padding = Thickness(10)
        body.Margin = Thickness(0, 6, 0, 0)

        stack = StackPanel()
        for issue_record in list(link_record.get("issues", []) or []):
            stack.Children.Add(self._create_issue_expander(link_record, issue_record))
        body.Child = stack
        expander.Content = body
        return expander

    def _set_empty_state(self, is_visible, text):
        self.empty_tb.Visibility = Visibility.Visible if is_visible else Visibility.Collapsed
        self.content_sv.Visibility = Visibility.Collapsed if is_visible else Visibility.Visible
        self.empty_tb.Text = _safe_text(text)

    def _visible_links(self):
        return list(self._all_problem_links)

    def _refresh_content(self):
        self.content_sp.Children.Clear()
        visible_links = self._visible_links()
        self._visible_link_keys = []
        self._visible_issue_keys = []

        if self.report.get("detection_error"):
            self._set_empty_state(True, "Detection Error")
            self.status_tb.Text = "Detection Error"
            return

        automation_error = self.report.get("automation_error")
        if automation_error:
            stage = _safe_text(automation_error.get("stage")) or "unknown"
            message = _safe_text(automation_error.get("message")) or "Coordination Review automation failed."
            self._set_empty_state(
                True,
                "Coordination Review automation failed at '{}'.\n\n{}".format(stage, message),
            )
            self.status_tb.Text = "Native Coordination Review extraction failed."
            return

        if not visible_links:
            self._set_empty_state(True, "No Coordination Review problems were found in this document.")
            self.status_tb.Text = DEFAULT_STATUS_TEXT
            return

        self._set_empty_state(False, "")
        for link_record in visible_links:
            self._visible_link_keys.append(self._link_key(link_record))
            for issue_record in list(link_record.get("issues", []) or []):
                self._visible_issue_keys.append(self._issue_key(link_record, issue_record))
            self.content_sp.Children.Add(self._create_link_expander(link_record))

        self.status_tb.Text = "Showing all issues across {} problem link(s).".format(len(visible_links))

    def _show_instance(self, instance_id):
        element_id_int = _safe_int(instance_id)
        if element_id_int is None:
            self.status_tb.Text = "Could not resolve the selected instance id."
            return

        if not self.uidoc or not self.doc:
            self.status_tb.Text = "Show is unavailable because there is no active Revit UI document."
            return

        active_doc = getattr(self.uidoc, "Document", None)
        if not _same_doc(active_doc, self.doc):
            self.status_tb.Text = "Show is only available when the report document is the active Revit document."
            return

        try:
            result = coordination_review_show.show_link_coordination_review(
                self.uiapp,
                self.uidoc,
                self.doc,
                element_id_int,
            )
        except Exception as ex:
            self.status_tb.Text = "Show failed for element {}: {}".format(
                element_id_int,
                _safe_text(ex) or "Unknown error",
            )
            return

        self.status_tb.Text = _safe_text(result.get("message")) or DEFAULT_STATUS_TEXT
        if result.get("ok"):
            # Revit only runs the posted Coordination Review command once this
            # modal window returns control to it, so close now.
            self.Close()

    def expand_all_click(self, sender, args):
        del sender, args
        for link_key in list(self._visible_link_keys):
            self._link_expansion_state[link_key] = True
        for issue_key in list(self._visible_issue_keys):
            self._issue_expansion_state[issue_key] = True
        self._refresh_content()

    def collapse_all_click(self, sender, args):
        del sender, args
        for link_key in list(self._visible_link_keys):
            self._link_expansion_state[link_key] = False
        for issue_key in list(self._visible_issue_keys):
            self._issue_expansion_state[issue_key] = False
        self._refresh_content()

    def close_click(self, sender, args):
        del sender, args
        self.Close()


def show_coordination_review_dialog(report, doc=None, uiapp=None):
    try:
        window = CoordinationReviewWindow(report, doc=doc, uiapp=uiapp)
        window.ShowDialog()
        return True
    except Exception as ex:
        LOGGER.warning("Coordination Review window failed: %s", ex)
        return False
