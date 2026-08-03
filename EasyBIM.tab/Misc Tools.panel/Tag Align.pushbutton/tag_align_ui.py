# -*- coding: utf-8 -*-
"""WPF windows for Tag Align.

None of these windows touch the Revit API.  Anything that needs the model -
zooming to a reference, re-selecting elements - arrives as a callback from
``script.py``, which keeps the windows testable and keeps every model call on
the command's own thread.

Picking elements needs Revit's own selection UI, which a modal dialog blocks.
So the windows follow the pattern ``Flip Multiple`` already uses: record a
``next_action``, close, let the caller do the picking, then reopen.
"""

# pylint: disable=import-error,invalid-name,broad-except
from pyrevit import forms
from pyrevit.framework import Windows

from tag_align_state import DROP_ALL
from tag_align_state import SCOPE_EXACT_TYPE
from tag_align_state import SCOPE_LABELS
from tag_align_state import SCOPE_PAIRED_FAMILY
from tag_align_state import SCOPE_SAME_CATEGORY
from tag_align_state import build_reference_row
from tag_align_state import build_report_text
from tag_align_state import build_running_text
from tag_align_state import checked_type_keys
from tag_align_state import describe_offset
from tag_align_state import narrower_scope
from tag_align_state import safe_text
from tag_align_state import unresolved_conflicts
from tag_align_state import validate_references


TITLE = "Tag Align"

ACTION_SELECT_ONE = "select_one"
ACTION_SELECT_MULTIPLE = "select_multiple"
ACTION_USE_SELECTION = "use_selection"
ACTION_ALIGN = "align"
ACTION_ALIGN_AND_TAG = "align_and_tag"
ACTION_CANCEL = "cancel"
ACTION_BACK = "back"
ACTION_OK = "ok"
ACTION_CLICK_MODE = "click_mode"
ACTION_BATCH_MODE = "batch_mode"
ACTION_SELECT = "select"
ACTION_DESELECT = "deselect"
ACTION_PROCESS = "process"
ACTION_RESELECT = "reselect"
ACTION_SWITCH_SCOPE = "switch_scope"

REFERENCE_COLUMNS = (
    ("tag_type", "Tag type", 150),
    ("host", "Element", 190),
    ("orientation", "Orientation", 120),
    ("offset", "Tag offset", 220),
    ("view", "Measured in", 130),
)


def _member(path, name):
    """Look up a WPF member, or ``None`` if this host does not expose it.

    Everything resolved here is cosmetic - weight, colour, trimming.  Making
    the lookup soft keeps a namespace surprise on some Revit build from taking
    a whole window down over a shade of grey.
    """
    owner = Windows
    for part in path:
        owner = getattr(owner, part, None)
        if owner is None:
            return None
    return getattr(owner, name, None)


SEMIBOLD = _member(("FontWeights",), "SemiBold")
DIM_GRAY = _member(("Media", "Brushes"), "DimGray")
LIGHT_GRAY = _member(("Media", "Brushes"), "LightGray")
WRAP = _member(("TextWrapping",), "Wrap")
ELLIPSIS = _member(("TextTrimming",), "CharacterEllipsis")
CENTER = _member(("VerticalAlignment",), "Center")
HORIZONTAL = _member(("Controls", "Orientation"), "Horizontal")
VISIBLE = _member(("Visibility",), "Visible")


def _text_block(text, width=None, bold=False, gray=False, wrap=False):
    block = Windows.Controls.TextBlock()
    block.Text = safe_text(text)
    if CENTER is not None:
        block.VerticalAlignment = CENTER
    block.Margin = Windows.Thickness(0, 0, 8, 0)
    if width:
        block.Width = float(width)
    if bold and SEMIBOLD is not None:
        block.FontWeight = SEMIBOLD
    if gray and DIM_GRAY is not None:
        block.Foreground = DIM_GRAY
    if wrap:
        if WRAP is not None:
            block.TextWrapping = WRAP
    elif ELLIPSIS is not None:
        block.TextTrimming = ELLIPSIS
    return block


def _row_panel(margin_top=0):
    panel = Windows.Controls.StackPanel()
    if HORIZONTAL is not None:
        panel.Orientation = HORIZONTAL
    panel.Margin = Windows.Thickness(0, float(margin_top), 0, 0)
    return panel


# ---------------------------------------------------------------------------
# main window
# ---------------------------------------------------------------------------


class TagAlignWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, session, format_length=None,
                 show_callback=None, preselected_count=0):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._loading = True
        self.session = session
        self.format_length = format_length
        self.show_callback = show_callback
        self.next_action = ACTION_CANCEL

        self.ScopeCategoryRadio.IsChecked = session.scope == SCOPE_SAME_CATEGORY
        self.ScopePairedRadio.IsChecked = session.scope == SCOPE_PAIRED_FAMILY
        self.ScopeExactRadio.IsChecked = session.scope == SCOPE_EXACT_TYPE
        self.ScaleWithViewCheck.IsChecked = bool(session.scale_with_view)
        self.CopyLeaderCheck.IsChecked = bool(session.copy_leader)
        self.ChangeTagTypeCheck.IsChecked = bool(session.change_tag_type)
        self.ConfirmBeforeProcessCheck.IsChecked = bool(session.confirm_before_process)
        self.IncludePinnedCheck.IsChecked = bool(session.include_pinned)
        self.CollapseFlipCheck.IsChecked = bool(session.collapse_flip)
        self.RotateFallbackCheck.IsChecked = bool(session.rotate_fallback)

        if preselected_count:
            self.UseSelectionButton.Content = "Use {0} selected tag(s)".format(preselected_count)
            if VISIBLE is not None:
                self.UseSelectionButton.Visibility = VISIBLE

        self._loading = False
        self.refresh()

    # -- rendering ---------------------------------------------------------

    def refresh(self):
        session = self.session
        has_references = session.has_references

        self.ReferenceSummaryText.Text = self._summary_text()
        self.ClearReferencesButton.IsEnabled = has_references
        self.ScopeBorder.IsEnabled = has_references
        self.OptionsBorder.IsEnabled = has_references
        self.AlignButton.IsEnabled = has_references
        self.AlignAndTagButton.IsEnabled = has_references

        # A single reference that rotates with the element covers every angle,
        # so the per-orientation fallback has nothing to fall back to.
        self.RotateFallbackCheck.IsEnabled = not session.uses_local_frame_everywhere

        self.HintText.Text = self._hint_text()
        self._render_reference_list()

    def _hint_text(self):
        """Validation runs here because it is pure Python.

        Scope and the view-scale option both change whether two references
        agree, so the warning has to move the moment the user clicks a radio -
        not later, when they press Align.
        """
        session = self.session
        if not session.has_references:
            return "Select a reference tag to enable Align and Align & Tag."

        validation = validate_references(
            session.references,
            scope=session.scope,
            scale_with_view=session.scale_with_view,
            collapse_flip=session.collapse_flip,
        )
        if validation.fatals:
            return validation.fatals[0].message
        if validation.conflicts:
            return "{0} reference conflict(s) under this setting - Align will ask you to resolve them.".format(
                len(validation.conflicts)
            )
        return ""

    def _summary_text(self):
        session = self.session
        if not session.has_references:
            return "No reference tag selected yet."

        parts = []
        tag_label = session.tag_type_label()
        if tag_label:
            parts.append("Tag type: {0}".format(tag_label))
        category = session.host_category_name()
        if category:
            parts.append("Category: {0}".format(category))
        if session.uses_local_frame_everywhere:
            parts.append("Offset rotates with the element (any orientation)")
        else:
            parts.append("Offset is matched per element orientation")
        parts.append("{0} reference tag(s)".format(len(session.references)))
        return "   |   ".join(parts)

    def _render_reference_list(self):
        self.ReferenceListPanel.Children.Clear()

        if not self.session.references:
            self.ReferenceListPanel.Children.Add(
                _text_block(
                    "Nothing here yet. Select One Reference Tag captures a single "
                    "placement; Select Multiple Reference Tags captures one per "
                    "element type and orientation.",
                    gray=True,
                    wrap=True,
                )
            )
            return

        header = _row_panel()
        header.Children.Add(_text_block("", width=62))
        for _, label, width in REFERENCE_COLUMNS:
            header.Children.Add(_text_block(label, width=width, bold=True))
        self.ReferenceListPanel.Children.Add(header)

        for rule in self.session.references:
            row_data = build_reference_row(rule, self.format_length)
            row = _row_panel(margin_top=6)

            show_button = Windows.Controls.Button()
            show_button.Content = "Show"
            show_button.Width = 54.0
            show_button.Height = 24.0
            show_button.Margin = Windows.Thickness(0, 0, 8, 0)
            show_button.Tag = rule
            show_button.Click += self._show_reference
            row.Children.Add(show_button)

            for key, _, width in REFERENCE_COLUMNS:
                block = _text_block(row_data.get(key, ""), width=width)
                block.ToolTip = safe_text(row_data.get(key, ""))
                row.Children.Add(block)

            self.ReferenceListPanel.Children.Add(row)

            notes = row_data.get("notes")
            if notes:
                note_block = _text_block(notes, gray=True, wrap=True)
                note_block.Margin = Windows.Thickness(62, 2, 0, 0)
                self.ReferenceListPanel.Children.Add(note_block)

    def _show_reference(self, sender, args):
        del args
        if self.show_callback is None:
            return
        rule = getattr(sender, "Tag", None)
        if rule is None:
            return
        try:
            self.show_callback([rule.tag_id, rule.host_id])
        except Exception as ex:
            self.HintText.Text = "Could not zoom to the reference: {0}".format(ex)

    # -- option plumbing ---------------------------------------------------

    def _capture_options(self):
        session = self.session
        if bool(self.ScopeExactRadio.IsChecked):
            session.scope = SCOPE_EXACT_TYPE
        elif bool(self.ScopePairedRadio.IsChecked):
            session.scope = SCOPE_PAIRED_FAMILY
        else:
            session.scope = SCOPE_SAME_CATEGORY
        session.scale_with_view = bool(self.ScaleWithViewCheck.IsChecked)
        session.copy_leader = bool(self.CopyLeaderCheck.IsChecked)
        session.change_tag_type = bool(self.ChangeTagTypeCheck.IsChecked)
        session.confirm_before_process = bool(self.ConfirmBeforeProcessCheck.IsChecked)
        session.include_pinned = bool(self.IncludePinnedCheck.IsChecked)
        session.collapse_flip = bool(self.CollapseFlipCheck.IsChecked)
        session.rotate_fallback = bool(self.RotateFallbackCheck.IsChecked)

    def scope_changed(self, sender, args):
        del sender, args
        if self._loading:
            return
        self._capture_options()
        self.refresh()

    def option_changed(self, sender, args):
        del sender, args
        if self._loading:
            return
        self._capture_options()
        self.refresh()

    # -- buttons -----------------------------------------------------------

    def _leave(self, action):
        self._capture_options()
        self.next_action = action
        self.Close()

    def select_one_click(self, sender, args):
        del sender, args
        self._leave(ACTION_SELECT_ONE)

    def select_multiple_click(self, sender, args):
        del sender, args
        self._leave(ACTION_SELECT_MULTIPLE)

    def use_selection_click(self, sender, args):
        del sender, args
        self._leave(ACTION_USE_SELECTION)

    def clear_references_click(self, sender, args):
        del sender, args
        self.session.reset_references()
        self.refresh()

    def align_click(self, sender, args):
        del sender, args
        self._leave(ACTION_ALIGN)

    def align_and_tag_click(self, sender, args):
        del sender, args
        self._leave(ACTION_ALIGN_AND_TAG)

    def cancel_click(self, sender, args):
        del sender, args
        self._leave(ACTION_CANCEL)


# ---------------------------------------------------------------------------
# "can this tag be aligned to any orientation?"
# ---------------------------------------------------------------------------


class ReferenceOrientationWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, rule, format_length=None, initial_any=True):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = ACTION_CANCEL
        self.any_orientation = bool(initial_any)

        self.AnyOrientationRadio.IsChecked = bool(initial_any)
        self.SameOrientationRadio.IsChecked = not bool(initial_any)

        self.ReferenceText.Text = "{0} on {1} - {2}, {3}".format(
            safe_text(rule.tag_type_label),
            rule.host_label(),
            rule.orientation_label(),
            describe_offset(rule.offset_view, format_length, rule.view_scale),
        )

        notes = list(rule.notes or [])
        if not rule.has_direction:
            notes.append(
                "This element has no direction in the view, so both answers "
                "behave the same: the tag keeps its screen offset."
            )
        self.NoteText.Text = "\n".join(notes)

    def ok_click(self, sender, args):
        del sender, args
        self.any_orientation = bool(self.AnyOrientationRadio.IsChecked)
        self.result = ACTION_OK
        self.Close()

    def back_click(self, sender, args):
        del sender, args
        self.result = ACTION_BACK
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = ACTION_CANCEL
        self.Close()


# ---------------------------------------------------------------------------
# conflict resolution
# ---------------------------------------------------------------------------


class ReferenceConflictWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, validation, format_length=None,
                 scope=SCOPE_SAME_CATEGORY, show_callback=None):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.validation = validation
        self.format_length = format_length
        self.show_callback = show_callback
        self.result = ACTION_CANCEL
        self.narrowed_scope = narrower_scope(scope)

        if self.narrowed_scope is None:
            self.SwitchScopeButton.IsEnabled = False
            self.SwitchScopeButton.ToolTip = "Already matching on exact type."
        else:
            self.SwitchScopeButton.Content = "Narrow to: {0}".format(
                SCOPE_LABELS.get(self.narrowed_scope, self.narrowed_scope)
            )

        self._render()
        self._refresh_status()

    def _render(self):
        self.ConflictPanel.Children.Clear()

        for group_index, group in enumerate(self.validation.conflicts):
            border = Windows.Controls.Border()
            if LIGHT_GRAY is not None:
                border.BorderBrush = LIGHT_GRAY
            border.BorderThickness = Windows.Thickness(1)
            border.Padding = Windows.Thickness(10)
            border.Margin = Windows.Thickness(0, 0, 0, 10)

            stack = Windows.Controls.StackPanel()
            stack.Children.Add(_text_block(group.title, bold=True, wrap=True))
            stack.Children.Add(_text_block(group.key_label, gray=True, wrap=True))

            group_name = "TagAlignConflict{0}".format(group_index)
            for member_index, rule in enumerate(group.members):
                radio = Windows.Controls.RadioButton()
                radio.GroupName = group_name
                radio.Margin = Windows.Thickness(0, 8, 0, 0)
                radio.Tag = (group, member_index)
                radio.Checked += self._choice_changed

                content = Windows.Controls.StackPanel()
                content.Children.Add(
                    _text_block(
                        "{0} - {1}".format(rule.host_label(), rule.orientation_label()),
                        bold=True,
                        wrap=True,
                    )
                )
                content.Children.Add(
                    _text_block(
                        describe_offset(rule.offset_view, self.format_length, rule.view_scale),
                        gray=True,
                        wrap=True,
                    )
                )

                show_row = _row_panel(margin_top=2)
                show_button = Windows.Controls.Button()
                show_button.Content = "Show"
                show_button.Width = 54.0
                show_button.Height = 22.0
                show_button.Tag = rule
                show_button.Click += self._show_reference
                show_row.Children.Add(show_button)
                content.Children.Add(show_row)

                radio.Content = content
                stack.Children.Add(radio)

            drop_radio = Windows.Controls.RadioButton()
            drop_radio.GroupName = group_name
            drop_radio.Content = "Drop all of these references"
            drop_radio.Margin = Windows.Thickness(0, 10, 0, 0)
            drop_radio.Tag = (group, DROP_ALL)
            drop_radio.Checked += self._choice_changed
            stack.Children.Add(drop_radio)

            border.Child = stack
            self.ConflictPanel.Children.Add(border)

    def _show_reference(self, sender, args):
        del args
        if self.show_callback is None:
            return
        rule = getattr(sender, "Tag", None)
        if rule is None:
            return
        try:
            self.show_callback([rule.tag_id, rule.host_id])
        except Exception as ex:
            self.StatusText.Text = "Could not zoom to the reference: {0}".format(ex)

    def _choice_changed(self, sender, args):
        del args
        tag = getattr(sender, "Tag", None)
        if not tag:
            return
        group, member_index = tag
        group.chosen_index = member_index
        self._refresh_status()

    def _refresh_status(self):
        pending = unresolved_conflicts(self.validation)
        self.OkButton.IsEnabled = not pending
        if pending:
            self.StatusText.Text = "{0} conflict(s) still need a choice.".format(len(pending))
        else:
            self.StatusText.Text = "All conflicts resolved."

    def ok_click(self, sender, args):
        del sender, args
        if unresolved_conflicts(self.validation):
            self._refresh_status()
            return
        self.result = ACTION_OK
        self.Close()

    def switch_scope_click(self, sender, args):
        del sender, args
        self.result = ACTION_SWITCH_SCOPE
        self.Close()

    def reselect_click(self, sender, args):
        del sender, args
        self.result = ACTION_RESELECT
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = ACTION_CANCEL
        self.Close()


# ---------------------------------------------------------------------------
# "align existing tags as well?"
# ---------------------------------------------------------------------------


class AlignTagOptionsWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, initial_align_existing=True):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = ACTION_CANCEL
        self.align_existing = bool(initial_align_existing)
        self.AlignExistingRadio.IsChecked = bool(initial_align_existing)
        self.TagOnlyRadio.IsChecked = not bool(initial_align_existing)

    def ok_click(self, sender, args):
        del sender, args
        self.align_existing = bool(self.AlignExistingRadio.IsChecked)
        self.result = ACTION_OK
        self.Close()

    def back_click(self, sender, args):
        del sender, args
        self.result = ACTION_BACK
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = ACTION_CANCEL
        self.Close()


# ---------------------------------------------------------------------------
# mode picker
# ---------------------------------------------------------------------------


class ModeWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, session):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.session = session
        self.result = ACTION_CANCEL

        if session.create_tags:
            self.TitleText.Text = "Align & Tag - how do you want to pick the elements?"
            if session.align_existing:
                self.SubtitleText.Text = (
                    "Untagged elements get a new tag; tags that already exist are aligned."
                )
            else:
                self.SubtitleText.Text = (
                    "Untagged elements get a new tag; elements that already have one are left alone."
                )
        else:
            self.TitleText.Text = "Align - how do you want to pick the elements?"
            self.SubtitleText.Text = (
                "Existing tags are moved to the reference position. Untagged elements "
                "are reported, not tagged."
            )

        self.ReferenceText.Text = "Reference: {0} on {1}".format(
            session.tag_type_label() or "<tag>",
            session.host_category_name() or "<category>",
        )
        self.refresh()

    def refresh(self):
        summary = self.session.running
        touched = summary.considered or summary.changed or summary.skipped
        self.ReportButton.IsEnabled = bool(touched)
        if touched:
            self.RunningText.Text = build_running_text(summary, self.session.create_tags)
        else:
            self.RunningText.Text = "Nothing processed yet."

    def click_mode_click(self, sender, args):
        del sender, args
        self.result = ACTION_CLICK_MODE
        self.Close()

    def batch_mode_click(self, sender, args):
        del sender, args
        self.result = ACTION_BATCH_MODE
        self.Close()

    def report_click(self, sender, args):
        del sender, args
        self.result = "report"
        self.Close()

    def back_click(self, sender, args):
        del sender, args
        self.result = ACTION_BACK
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = ACTION_CANCEL
        self.Close()


# ---------------------------------------------------------------------------
# batch bar
# ---------------------------------------------------------------------------


class BatchWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, session, entries=None, status_text=""):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.session = session
        self.entries = list(entries or [])
        self.result = ACTION_CANCEL

        if status_text:
            self.StatusText.Text = status_text
        self.refresh()

    def refresh(self):
        count = len(self.entries)
        self.CountText.Text = "{0} element(s) selected.".format(count)

        for control in (self.FilterButton, self.DeselectButton, self.ClearButton,
                        self.ProcessButton):
            control.IsEnabled = count > 0

        summary = self.session.running
        self.ReportButton.IsEnabled = bool(
            summary.considered or summary.changed or summary.skipped
        )

        self._render_breakdown()

    def _render_breakdown(self):
        self.BreakdownPanel.Children.Clear()
        if not self.entries:
            self.BreakdownPanel.Children.Add(
                _text_block("Nothing selected yet.", gray=True, wrap=True)
            )
            return

        counts = {}
        order = []
        for entry in self.entries:
            key = "{0}  >  {1} : {2}".format(
                safe_text(entry.get("category")) or "<no category>",
                safe_text(entry.get("family")) or "<no family>",
                safe_text(entry.get("type")) or "<no type>",
            )
            if key not in counts:
                counts[key] = 0
                order.append(key)
            counts[key] += 1

        for key in sorted(order, key=lambda name: name.lower()):
            self.BreakdownPanel.Children.Add(
                _text_block("{0}  -  {1}".format(key, counts[key]), wrap=True)
            )

    def filter_click(self, sender, args):
        del sender, args
        self.result = "filter"
        self.Close()

    def select_click(self, sender, args):
        del sender, args
        self.result = ACTION_SELECT
        self.Close()

    def deselect_click(self, sender, args):
        del sender, args
        self.result = ACTION_DESELECT
        self.Close()

    def clear_click(self, sender, args):
        del sender, args
        self.entries = []
        self.result = "clear"
        self.Close()

    def process_click(self, sender, args):
        del sender, args
        self.result = ACTION_PROCESS
        self.Close()

    def report_click(self, sender, args):
        del sender, args
        self.result = "report"
        self.Close()

    def back_click(self, sender, args):
        del sender, args
        self.result = ACTION_BACK
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = ACTION_CANCEL
        self.Close()


# ---------------------------------------------------------------------------
# selection filter
# ---------------------------------------------------------------------------


class SelectionFilterWindow(forms.WPFWindow):
    """Revit's Filter dialog, one level deeper: category, family and type."""

    def __init__(self, xaml_file_name, tree):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.tree = list(tree or [])
        self.result = ACTION_CANCEL
        self.allowed_type_keys = set()
        self._type_boxes = []
        self._suspend = False

        self._populate()
        self._refresh_count()

    def _populate(self):
        self.FilterTree.Items.Clear()
        self._type_boxes = []

        for category_node in self.tree:
            category_item = Windows.Controls.TreeViewItem()
            category_item.IsExpanded = True
            category_box = self._make_box(category_node)
            category_item.Header = category_box

            child_boxes = []
            for family_node in category_node.children:
                family_item = Windows.Controls.TreeViewItem()
                family_item.IsExpanded = False
                family_box = self._make_box(family_node)
                family_item.Header = family_box

                type_boxes = []
                for type_node in family_node.children:
                    type_item = Windows.Controls.TreeViewItem()
                    type_box = self._make_box(type_node)
                    type_item.Header = type_box
                    family_item.Items.Add(type_item)
                    type_boxes.append(type_box)
                    self._type_boxes.append(type_box)

                family_box.Tag = (family_node, type_boxes)
                child_boxes.extend(type_boxes)
                category_item.Items.Add(family_item)

            category_box.Tag = (category_node, child_boxes)
            self.FilterTree.Items.Add(category_item)

    def _make_box(self, node):
        box = Windows.Controls.CheckBox()
        box.Content = node.display()
        box.IsChecked = bool(node.is_checked)
        box.Margin = Windows.Thickness(2, 2, 2, 2)
        box.Tag = (node, [])
        box.Checked += self._box_toggled
        box.Unchecked += self._box_toggled
        return box

    def _box_toggled(self, sender, args):
        del args
        if self._suspend:
            return
        tag = getattr(sender, "Tag", None)
        if not tag:
            return
        node, children = tag
        node.is_checked = bool(sender.IsChecked)

        if children:
            self._suspend = True
            try:
                for child in children:
                    child.IsChecked = node.is_checked
                    child_tag = getattr(child, "Tag", None)
                    if child_tag:
                        child_node, grand_children = child_tag
                        child_node.is_checked = node.is_checked
                        for grand_child in grand_children:
                            grand_child.IsChecked = node.is_checked
                            grand_tag = getattr(grand_child, "Tag", None)
                            if grand_tag:
                                grand_tag[0].is_checked = node.is_checked
            finally:
                self._suspend = False

        self._refresh_count()

    def _set_all(self, value):
        self._suspend = True
        try:
            for item in self.FilterTree.Items:
                self._apply_recursive(item, value)
        finally:
            self._suspend = False
        self._refresh_count()

    def _apply_recursive(self, item, value):
        box = getattr(item, "Header", None)
        if box is not None and hasattr(box, "IsChecked"):
            box.IsChecked = value
            tag = getattr(box, "Tag", None)
            if tag:
                tag[0].is_checked = value
        for child in item.Items:
            self._apply_recursive(child, value)

    def _refresh_count(self):
        kept = 0
        for category_node in self.tree:
            for family_node in category_node.children:
                for type_node in family_node.children:
                    if type_node.is_checked:
                        kept += type_node.count
        self.CountText.Text = "{0} element(s) will stay selected.".format(kept)
        self.OkButton.IsEnabled = kept > 0

    def check_all_click(self, sender, args):
        del sender, args
        self._set_all(True)

    def check_none_click(self, sender, args):
        del sender, args
        self._set_all(False)

    def ok_click(self, sender, args):
        del sender, args
        self.allowed_type_keys = checked_type_keys(self.tree)
        self.result = ACTION_OK
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = ACTION_CANCEL
        self.Close()


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


class ReportWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, summary, create_tags=False, select_callback=None):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.summary = summary
        self.select_callback = select_callback

        changed = summary.changed
        if summary.cancelled:
            self.HeadlineText.Text = "Cancelled - nothing was kept."
        elif changed:
            self.HeadlineText.Text = "{0} tag(s) updated.".format(changed)
        else:
            self.HeadlineText.Text = "Nothing to change."

        self.BodyText.Text = build_report_text(summary, create_tags)

        skipped = list(summary.skipped or []) + list(summary.failed or [])
        if skipped and select_callback is not None:
            self.SelectSkippedButton.IsEnabled = True
            self.SelectSkippedButton.ToolTip = "Select the {0} element(s) that were not changed.".format(
                len(skipped)
            )

    def select_skipped_click(self, sender, args):
        del sender, args
        if self.select_callback is None:
            return
        try:
            self.select_callback(self.summary)
            self.Close()
        except Exception as ex:
            self.HeadlineText.Text = "Could not select: {0}".format(ex)

    def close_click(self, sender, args):
        del sender, args
        self.Close()
