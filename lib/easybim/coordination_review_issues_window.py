# -*- coding: utf-8 -*-
"""WPF report of the Coordination Review differences EasyBIM computed for one link."""

import os

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import forms
from pyrevit import script
from System.Windows import (
    CornerRadius,
    FontWeights,
    TextWrapping,
    Thickness,
    VerticalAlignment,
    Visibility,
)
from System.Windows.Controls import Border, Button, Dock, DockPanel, Expander, StackPanel, TextBlock
from System.Windows.Media import Brushes

from easybim import coordination_review_show


LOGGER = script.get_logger()
XAML_PATH = os.path.join(os.path.dirname(__file__), "ui", "coordination_review_issues.xaml")


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


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


class CoordinationReviewIssuesWindow(forms.WPFWindow):
    def __init__(self, report, doc=None, uidoc=None, recompute=None):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.Topmost = True
        self.doc = doc
        self.uidoc = uidoc
        self._recompute = recompute
        self._expansion_state = {}
        self._visible_group_keys = []
        self.report = {}
        self._apply_report(report)

    # -- report -----------------------------------------------------------

    def _apply_report(self, report):
        self.report = dict(report or {})
        self._populate_summary()
        self._refresh_content()

    def _link_label(self):
        return _safe_text(self.report.get("link_name")) or "the link"

    def _populate_summary(self):
        self.link_name_tb.Text = _safe_text(self.report.get("link_name")) or "(Unknown link)"
        doc_title = _safe_text(self.report.get("doc_title"))
        self.doc_title_tb.Text = "Document: {}".format(doc_title) if doc_title else ""
        self.monitored_tb.Text = str(_safe_int(self.report.get("monitored_count")))
        self.issues_tb.Text = str(_safe_int(self.report.get("issue_count")))
        self.unchanged_tb.Text = str(_safe_int(self.report.get("ok_count")))

    # -- content ----------------------------------------------------------

    def _group_key(self, group):
        return _safe_text(group.get("kind"))

    def _build_group_header(self, group):
        header = DockPanel()
        header.LastChildFill = True

        if group.get("estimated"):
            estimated = _build_badge(
                "estimated",
                Brushes.LemonChiffon,
                Brushes.BurlyWood,
                Brushes.SaddleBrown,
            )
            DockPanel.SetDock(estimated, Dock.Right)
            header.Children.Add(estimated)

        badge = _build_badge(
            "{} item(s)".format(len(list(group.get("issues", []) or []))),
            Brushes.AliceBlue,
            Brushes.LightSteelBlue,
            Brushes.SteelBlue,
        )
        DockPanel.SetDock(badge, Dock.Right)
        header.Children.Add(badge)

        title = TextBlock()
        title.Text = _safe_text(group.get("title")) or _safe_text(group.get("kind"))
        title.FontSize = 15
        title.FontWeight = FontWeights.SemiBold
        header.Children.Add(title)
        return header

    def _make_expansion_handler(self, group_key, is_expanded):
        def _handler(sender, args):
            del sender, args
            self._expansion_state[group_key] = bool(is_expanded)

        return _handler

    def _make_show_handler(self, issue):
        def _handler(sender, args):
            del sender, args
            self._show_issue(issue)

        return _handler

    def _create_issue_row(self, issue):
        container = Border()
        container.BorderBrush = Brushes.Gainsboro
        container.BorderThickness = Thickness(1)
        container.Background = Brushes.WhiteSmoke
        container.Padding = Thickness(10, 8, 10, 8)
        container.Margin = Thickness(0, 0, 0, 6)

        row_panel = DockPanel()
        row_panel.LastChildFill = True

        if issue.get("host_id") is not None:
            show_btn = Button()
            show_btn.Content = "Show"
            show_btn.Width = 80
            show_btn.Height = 28
            show_btn.Margin = Thickness(10, 0, 0, 0)
            DockPanel.SetDock(show_btn, Dock.Right)
            show_btn.Click += self._make_show_handler(issue)
            row_panel.Children.Add(show_btn)

        label = TextBlock()
        label.Text = _safe_text(issue.get("message"))
        label.TextWrapping = TextWrapping.Wrap
        label.VerticalAlignment = VerticalAlignment.Center
        row_panel.Children.Add(label)

        container.Child = row_panel
        return container

    def _create_group_expander(self, group):
        expander = Expander()
        expander.Margin = Thickness(0, 0, 0, 10)
        expander.Header = self._build_group_header(group)
        group_key = self._group_key(group)
        expander.IsExpanded = self._expansion_state.get(group_key, True)
        expander.Expanded += self._make_expansion_handler(group_key, True)
        expander.Collapsed += self._make_expansion_handler(group_key, False)

        body = Border()
        body.BorderBrush = Brushes.Gainsboro
        body.BorderThickness = Thickness(1)
        body.Background = Brushes.White
        body.Padding = Thickness(10)
        body.Margin = Thickness(0, 6, 0, 0)

        stack = StackPanel()
        for issue in list(group.get("issues", []) or []):
            stack.Children.Add(self._create_issue_row(issue))
        body.Child = stack
        expander.Content = body
        return expander

    def _set_empty_state(self, is_visible, text):
        self.empty_tb.Visibility = Visibility.Visible if is_visible else Visibility.Collapsed
        self.content_sv.Visibility = Visibility.Collapsed if is_visible else Visibility.Visible
        self.empty_tb.Text = _safe_text(text)

    def _refresh_content(self):
        self.content_sp.Children.Clear()
        self._visible_group_keys = []

        error = _safe_text(self.report.get("error"))
        if error:
            self._set_empty_state(True, error)
            self.status_tb.Text = error
            return

        link_label = self._link_label()
        monitored = _safe_int(self.report.get("monitored_count"))
        if monitored <= 0:
            self._set_empty_state(
                True,
                "No element in this model monitors {}.\n\nCoordination Review only tracks "
                "Copy/Monitor relationships, so there is nothing to compare.".format(link_label),
            )
            self.status_tb.Text = "Nothing monitors {}.".format(link_label)
            return

        groups = list(self.report.get("groups", []) or [])
        summary = "Compared {} monitored element(s) with {}: {} issue(s), {} unchanged.".format(
            monitored,
            link_label,
            _safe_int(self.report.get("issue_count")),
            _safe_int(self.report.get("ok_count")),
        )
        offset_text = _safe_text(self.report.get("level_offset_text"))
        if offset_text:
            summary = "{} {}".format(summary, offset_text)

        if not groups:
            self._set_empty_state(
                True,
                "No differences found between the {} monitored element(s) and {}.".format(
                    monitored, link_label
                ),
            )
            self.status_tb.Text = summary
            return

        self._set_empty_state(False, "")
        for group in groups:
            self._visible_group_keys.append(self._group_key(group))
            self.content_sp.Children.Add(self._create_group_expander(group))
        self.status_tb.Text = summary

    # -- actions ----------------------------------------------------------

    def _show_issue(self, issue):
        host_id = issue.get("host_id")
        element = coordination_review_show.element_from_id(self.doc, host_id)
        if element is None:
            self.status_tb.Text = "Element {} is no longer available.".format(host_id)
            return
        result = coordination_review_show.select_element(self.uidoc, element)
        self.status_tb.Text = _safe_text(result.get("message")) or "Element {} selected.".format(host_id)

    def refresh_click(self, sender, args):
        del sender, args
        if not callable(self._recompute):
            return
        self.status_tb.Text = "Reading elements that monitor {}...".format(self._link_label())
        try:
            report = self._recompute()
        except Exception as ex:
            LOGGER.warning("Coordination Review issues refresh failed: %s", ex)
            self.status_tb.Text = "Refresh failed: {}".format(_safe_text(ex) or "Unknown error")
            return
        self._apply_report(report)

    def expand_all_click(self, sender, args):
        del sender, args
        for group_key in list(self._visible_group_keys):
            self._expansion_state[group_key] = True
        self._refresh_content()

    def collapse_all_click(self, sender, args):
        del sender, args
        for group_key in list(self._visible_group_keys):
            self._expansion_state[group_key] = False
        self._refresh_content()

    def close_click(self, sender, args):
        del sender, args
        self.Close()


def show_issues_dialog(report, doc=None, uidoc=None, recompute=None):
    try:
        window = CoordinationReviewIssuesWindow(report, doc=doc, uidoc=uidoc, recompute=recompute)
        window.ShowDialog()
        return True
    except Exception as ex:
        LOGGER.warning("Coordination Review issues window failed: %s", ex)
        return False
