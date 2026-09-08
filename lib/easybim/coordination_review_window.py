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

from easybim import coordination_review_diff_revit
from easybim import coordination_review_issues_window
from easybim import coordination_review_model
from easybim import coordination_review_show


LOGGER = script.get_logger()
XAML_PATH = os.path.join(os.path.dirname(__file__), "ui", "coordination_review.xaml")
DEFAULT_STATUS_TEXT = (
    "Ready. Review the full issue list below, or click View Issues to see the "
    "differences EasyBIM found for that link."
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
        self._checked_links = coordination_review_model.build_checked_link_records(self.report)
        self._summary = coordination_review_model.summarize_checked_links(self.report)
        self._link_expansion_state = {}
        self._visible_link_keys = []

        self._populate_summary()
        self._refresh_content()

    def _populate_summary(self):
        self.doc_title_tb.Text = _safe_text(self.report.get("doc_title")) or "(Unknown)"
        self.monitored_links_tb.Text = str(_safe_int(self.report.get("monitored_link_count")) or 0)
        self.problem_links_tb.Text = str(_safe_int(self.report.get("problem_count")) or 0)
        self.differences_tb.Text = str(_safe_int(self.report.get("total_issues")) or 0)

    def _link_key(self, link_record):
        return _safe_text(link_record.get("filter_value"))

    def _build_link_header(self, link_record):
        header = DockPanel()
        header.LastChildFill = True

        view_btn = Button()
        view_btn.Content = "View Issues"
        view_btn.Width = 100
        view_btn.Height = 28
        view_btn.Margin = Thickness(12, 0, 0, 0)
        DockPanel.SetDock(view_btn, Dock.Right)
        view_btn.Click += self._make_view_issues_handler(link_record.get("link_id"))
        header.Children.Add(view_btn)

        kind = _safe_text(link_record.get("kind"))
        if kind == coordination_review_model.KIND_PROBLEM:
            badge = _build_badge(
                link_record.get("badge_text"),
                Brushes.MistyRose,
                Brushes.LightCoral,
                Brushes.Firebrick,
            )
        elif kind == coordination_review_model.KIND_ERROR:
            badge = _build_badge(
                link_record.get("badge_text"),
                Brushes.LemonChiffon,
                Brushes.BurlyWood,
                Brushes.SaddleBrown,
            )
        else:
            badge = _build_badge(
                link_record.get("badge_text"),
                Brushes.Honeydew,
                Brushes.DarkSeaGreen,
                Brushes.SeaGreen,
            )
        DockPanel.SetDock(badge, Dock.Right)
        header.Children.Add(badge)

        if link_record.get("flagged_by_revit"):
            flagged = _build_badge(
                "Revit flagged",
                Brushes.AliceBlue,
                Brushes.LightSteelBlue,
                Brushes.SteelBlue,
            )
            DockPanel.SetDock(flagged, Dock.Right)
            header.Children.Add(flagged)

        title = TextBlock()
        title.Text = _safe_text(link_record.get("name")) or "Unknown Link"
        title.FontSize = 15
        title.FontWeight = FontWeights.SemiBold
        title.TextWrapping = TextWrapping.Wrap
        header.Children.Add(title)
        return header

    def _make_link_expansion_handler(self, link_key, is_expanded):
        def _handler(sender, args):
            del sender, args
            self._link_expansion_state[link_key] = bool(is_expanded)

        return _handler

    def _make_view_issues_handler(self, link_id):
        def _handler(sender, args):
            del sender, args
            self._view_issues(link_id)

        return _handler

    def _create_group_row(self, group):
        container = Border()
        container.BorderBrush = Brushes.Gainsboro
        container.BorderThickness = Thickness(1)
        container.Background = Brushes.WhiteSmoke
        container.Padding = Thickness(10, 8, 10, 8)
        container.Margin = Thickness(0, 0, 0, 6)

        row_panel = DockPanel()
        row_panel.LastChildFill = True

        count = len(list(group.get("issues", []) or []))
        badge = _build_badge(
            "{} item(s)".format(count),
            Brushes.AliceBlue,
            Brushes.LightSteelBlue,
            Brushes.SteelBlue,
        )
        DockPanel.SetDock(badge, Dock.Right)
        row_panel.Children.Add(badge)

        label = TextBlock()
        label.Text = _safe_text(group.get("title")) or _safe_text(group.get("kind"))
        if group.get("estimated"):
            label.Text += "  (estimated)"
        label.TextWrapping = TextWrapping.Wrap
        row_panel.Children.Add(label)

        container.Child = row_panel
        return container

    def _create_link_expander(self, link_record):
        expander = Expander()
        expander.Margin = Thickness(0, 0, 0, 10)
        expander.Header = self._build_link_header(link_record)
        link_key = self._link_key(link_record)
        expander.IsExpanded = self._link_expansion_state.get(
            link_key,
            bool(link_record.get("is_expanded", False)),
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
        status = TextBlock()
        status.Text = _safe_text(link_record.get("status_text"))
        status.Foreground = Brushes.DimGray
        status.TextWrapping = TextWrapping.Wrap
        status.Margin = Thickness(0, 0, 0, 8)
        stack.Children.Add(status)

        for group in list(link_record.get("groups", []) or []):
            stack.Children.Add(self._create_group_row(group))

        body.Child = stack
        expander.Content = body
        return expander

    def _set_empty_state(self, is_visible, text):
        self.empty_tb.Visibility = Visibility.Visible if is_visible else Visibility.Collapsed
        self.content_sv.Visibility = Visibility.Collapsed if is_visible else Visibility.Visible
        self.empty_tb.Text = _safe_text(text)

    def _visible_links(self):
        return list(self._checked_links)

    def _refresh_content(self):
        self.content_sp.Children.Clear()
        visible_links = self._visible_links()
        self._visible_link_keys = []
        summary = dict(self._summary or {})

        if self.report.get("detection_error"):
            # The comparison could not run at all; the diagnosis says why.
            diagnosis = dict(self.report.get("diagnosis") or {})
            text = _safe_text(self.report.get("diagnosis_text"))
            headline = _safe_text(diagnosis.get("headline"))
            self._set_empty_state(True, text or "Coordination Review could not check this model.")
            self.status_tb.Text = headline or "Coordination Review could not check this model."
            return

        if not visible_links:
            detail = _safe_text(summary.get("detail"))
            headline = _safe_text(summary.get("headline"))
            self._set_empty_state(
                True, "{0}\n\n{1}".format(headline, detail).strip() or DEFAULT_STATUS_TEXT
            )
            self.status_tb.Text = _safe_text(summary.get("status")) or DEFAULT_STATUS_TEXT
            return

        self._set_empty_state(False, "")
        for link_record in visible_links:
            self._visible_link_keys.append(self._link_key(link_record))
            self.content_sp.Children.Add(self._create_link_expander(link_record))

        self.status_tb.Text = _safe_text(summary.get("status")) or DEFAULT_STATUS_TEXT

    def _view_issues(self, link_id):
        element_id_int = _safe_int(link_id)
        if element_id_int is None:
            self.status_tb.Text = "Could not resolve the selected link id."
            return

        # Resolve the UI document now, not at construction: at file open the
        # window is raised from the Idling delegate, whose application object
        # may be a UIControlledApplication without an ActiveUIDocument.
        self.uidoc = coordination_review_show.resolve_uidoc(self.uiapp, self.doc)
        if not self.uidoc or not self.doc:
            self.status_tb.Text = (
                "View Issues is unavailable because there is no active Revit UI document."
            )
            return

        active_doc = getattr(self.uidoc, "Document", None)
        if not _same_doc(active_doc, self.doc):
            self.status_tb.Text = (
                "View Issues is only available when the report document is the active "
                "Revit document."
            )
            return

        record = None
        for candidate in list(self._checked_links):
            if _safe_int(candidate.get("link_id")) == element_id_int:
                record = candidate
                break

        link_name = _safe_text((record or {}).get("name")) or "link {}".format(element_id_int)
        if record and _safe_text(record.get("error")):
            self.status_tb.Text = _safe_text(record.get("error"))
            return

        # This handler runs inside the pyRevit command / Idling API context
        # that opened the modal summary, so re-reading the documents here is
        # allowed; the detail window opens as a nested modal.
        def _compute():
            link_instance, error = coordination_review_show.resolve_link_instance(
                self.doc, element_id_int
            )
            if link_instance is None:
                return {"error": _safe_text(error) or "Could not resolve the link."}
            return coordination_review_diff_revit.build_link_issue_report(self.doc, link_instance)

        issue_report = dict((record or {}).get("report") or {})
        if not issue_report:
            self.status_tb.Text = "Reading elements that monitor {}...".format(link_name)
            try:
                issue_report = _compute()
            except Exception as ex:
                LOGGER.warning("Coordination Review issue report failed: %s", ex)
                self.status_tb.Text = "View Issues failed for {}: {}".format(
                    link_name, _safe_text(ex) or "Unknown error"
                )
                return

        if issue_report.get("error"):
            self.status_tb.Text = _safe_text(issue_report.get("error"))
            return

        self.status_tb.Text = "{} difference(s) for {} ({} monitored element(s)).".format(
            issue_report.get("issue_count", 0),
            link_name,
            issue_report.get("monitored_count", 0),
        )
        coordination_review_issues_window.show_issues_dialog(
            issue_report, doc=self.doc, uidoc=self.uidoc, recompute=_compute
        )

    def expand_all_click(self, sender, args):
        del sender, args
        for link_key in list(self._visible_link_keys):
            self._link_expansion_state[link_key] = True
        self._refresh_content()

    def collapse_all_click(self, sender, args):
        del sender, args
        for link_key in list(self._visible_link_keys):
            self._link_expansion_state[link_key] = False
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
