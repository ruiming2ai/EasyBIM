# -*- coding: utf-8 -*-
"""WPF windows for the My Ribbon command.

The manager window edits a working copy of the registry in place (move,
rename, remove, hide-tab) and closes with a ``result`` whenever the script
has to do host work - download, parse, import, export, apply - after which
the script reopens it.  Same close-and-reopen loop as Families Transfer.
"""

from pyrevit import forms
from pyrevit.framework import Windows

import my_ribbon_state as state


TITLE = "My Ribbon"

NEW_TAB_CHOICE = u"— New tab... —"
NEW_PANEL_CHOICE = u"— New panel... —"
EASYBIM_TAB = "EasyBIM"
ARROW = u"  ›  "

#: Tabs the Show/Hide window keeps ticked and disabled (the engine refuses
#: to hide them too): EasyBIM holds the My Ribbon button, Modify is Revit's
#: contextual editing tab.
FROZEN_TABS = ("easybim", "modify")
PYREVIT_TAB_NOTE = ("pyRevit's Reload, Update, Settings and Extensions live here. You can turn "
                    "it back on in My Ribbon at any time.")


def _safe_text(value):
    return state.safe_text(value)


def _search_text(textbox):
    try:
        return _safe_text(textbox.Text).strip().lower()
    except Exception:
        return ""


def _add_empty_text(panel, message):
    text = Windows.Controls.TextBlock()
    text.Text = message
    text.Foreground = Windows.Media.Brushes.DimGray
    text.Margin = Windows.Thickness(8, 8, 8, 8)
    text.TextWrapping = Windows.TextWrapping.Wrap
    panel.Children.Add(text)


def _text_block(text, bold=False, grey=False, size=None):
    block = Windows.Controls.TextBlock()
    block.Text = _safe_text(text)
    block.TextTrimming = Windows.TextTrimming.CharacterEllipsis
    if bold:
        block.FontWeight = Windows.FontWeights.SemiBold
    if grey:
        block.Foreground = Windows.Media.Brushes.DimGray
    if size:
        block.FontSize = size
    return block


def _one_line(text):
    return " ".join(_safe_text(text).replace("\\n", " ").split())


def _dest_label(dest):
    return u"{0}{1}{2}".format(dest.get("tab"), ARROW, dest.get("panel"))


def _small_icon(path, source=None):
    """A 16-px image for a picker row - from a file path or from a live
    ribbon item's ImageSource - or None when neither is usable."""
    try:
        bitmap = source
        if bitmap is None:
            if not path:
                return None
            import System
            bitmap = Windows.Media.Imaging.BitmapImage()
            bitmap.BeginInit()
            bitmap.UriSource = System.Uri(path)
            bitmap.DecodePixelWidth = 16
            bitmap.CacheOption = Windows.Media.Imaging.BitmapCacheOption.OnLoad
            bitmap.EndInit()
        image = Windows.Controls.Image()
        image.Source = bitmap
        image.Width = 16
        image.Height = 16
        image.Margin = Windows.Thickness(0, 0, 6, 0)
        return image
    except Exception:
        return None


# -- manager -------------------------------------------------------------------


class MyRibbonWindow(forms.WPFWindow):
    """Sources on the left, the user's buttons on the right.

    ``working`` is edited in place.  ``result`` tells the script what to do
    next: ``"apply"``, ``"close"``, ``"add_source"``, ``("add_buttons",
    source_id)``, ``"import"`` or ``"export"``.
    """

    def __init__(self, xaml_file_name, working, saved, source_status=None,
                 placement_issues=None, ribbon_summary=None, open_folder=None,
                 pending_deletes=None, notice=None):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.working = working
        self.saved = saved
        self.source_status = dict(source_status or {})
        self.placement_issues = dict(placement_issues or {})
        self.ribbon_summary = list(ribbon_summary or [])
        self.open_folder = open_folder
        self.pending_deletes = pending_deletes if pending_deletes is not None else []
        self.result = None
        self._source_rows = []
        self._tree_items = {}
        self._selected_source_id = None
        self._selected_node = None
        self._populate_sources()
        self._populate_tree()
        self._refresh_status(notice)
        # Esc, the title-bar X and the Close button all end here; the prompt
        # about unapplied changes lives in one place so none of them can skip it.
        self.Closing += self._on_closing
        self._is_ready = True

    def _unapplied_changes(self):
        return state.count_changes(self.saved, self.working) + len(self.pending_deletes)

    def _on_closing(self, sender, args):
        del sender
        if self.result is not None:
            return
        changes = self._unapplied_changes()
        if changes and not forms.alert(
                "You have {0} change{1} that {2} not applied.\n\nClose and lose them?".format(
                    changes, "" if changes == 1 else "s", "is" if changes == 1 else "are"),
                title=TITLE, yes=True, no=True):
            args.Cancel = True
            return
        self.result = "close"

    # -- population --

    def _populate_sources(self):
        self.SourcesList.Items.Clear()
        self._source_rows = []
        for source in self.working.get("sources", []):
            item = Windows.Controls.ListBoxItem()
            panel = Windows.Controls.StackPanel()
            panel.Margin = Windows.Thickness(4, 4, 4, 4)
            panel.Children.Add(_text_block(source.get("label") or source.get("ext_name"), bold=True))
            detail = self._source_detail(source)
            panel.Children.Add(_text_block(detail, grey=True, size=11))
            if source.get("kind") != "dynamo":
                hide = Windows.Controls.CheckBox()
                if source.get("kind") == "ribbon":
                    hide.Content = "Hide this tab"
                else:
                    hide.Content = "Hide its own tab ({0})".format(
                        ", ".join(source.get("tab_names") or []) or "no tab found yet")
                hide.IsChecked = bool(source.get("hide_tab"))
                hide.Tag = source.get("id")
                hide.Margin = Windows.Thickness(0, 4, 0, 0)
                hide.FontSize = 11
                hide.Click += self.hide_tab_click
                panel.Children.Add(hide)
            item.Content = panel
            item.Tag = source.get("id")
            self.SourcesList.Items.Add(item)
            self._source_rows.append(item)
        count = len(self._source_rows)
        self.sources_count_tb.Text = "{0} source{1}.".format(count, "" if count == 1 else "s")
        if not count:
            item = Windows.Controls.ListBoxItem()
            item.Content = _text_block("No sources yet. Press Add source... to pick an installed "
                                       "extension, a tab already on the ribbon, or a Dynamo graph.",
                                       grey=True)
            item.IsEnabled = False
            self.SourcesList.Items.Add(item)

    def _source_detail(self, source):
        kind = source.get("kind")
        if kind == "git":
            origin = source.get("url") or ""
            if source.get("branch"):
                origin += " @ " + source["branch"]
        elif kind == "catalogue":
            origin = "pyRevit catalogue: " + _safe_text(source.get("name") or source.get("ext_name"))
        elif kind == "ribbon":
            origin = "tab on the ribbon: " + _safe_text(source.get("ext_name"))
        elif kind == "dynamo":
            origin = "Dynamo graph: " + _safe_text(source.get("path"))
        else:
            origin = "installed extension: " + _safe_text(source.get("ext_name"))
        status = self.source_status.get(source.get("id"), "")
        return "{0}  -  {1}".format(status, origin) if status else origin

    def _populate_tree(self):
        self.ButtonsTree.Items.Clear()
        self._tree_items = {}
        sources = dict((s.get("id"), s) for s in self.working.get("sources", []))
        for dest in self.working.get("destinations", []):
            rows = state.placements_in(self.working, dest.get("id"))
            node = Windows.Controls.TreeViewItem()
            header = u"{0}   ({1})".format(_dest_label(dest), len(rows))
            if dest.get("own_tab"):
                header += "   [my own tab]"
            node.Header = header
            node.Tag = ("dest", dest.get("id"))
            node.IsExpanded = True
            index = 0
            while index < len(rows):
                placement = rows[index]
                stack_id = state.safe_text(placement.get("stack"))
                if stack_id:
                    members = [placement]
                    scan = index + 1
                    while scan < len(rows) and \
                            state.safe_text(rows[scan].get("stack")) == stack_id:
                        members.append(rows[scan])
                        scan += 1
                    if len(members) >= 2:
                        stack_node = Windows.Controls.TreeViewItem()
                        stack_node.Header = u"Stacked ({0} small buttons)".format(len(members))
                        stack_node.Tag = ("stack", stack_id)
                        stack_node.IsExpanded = True
                        for member in members:
                            stack_node.Items.Add(self._placement_leaf(member, sources))
                        node.Items.Add(stack_node)
                        self._tree_items[stack_id] = stack_node
                        index = scan
                        continue
                node.Items.Add(self._placement_leaf(placement, sources))
                index += 1
            self.ButtonsTree.Items.Add(node)
            self._tree_items[dest.get("id")] = node
        total = state.summarize(self.working)["placements"]
        panels = len(self.working.get("destinations", []))
        self.buttons_count_tb.Text = "{0} button{1} on {2} panel{3}.".format(
            total, "" if total == 1 else "s", panels, "" if panels == 1 else "s")
        if not self.working.get("destinations"):
            node = Windows.Controls.TreeViewItem()
            node.Header = "Nothing placed yet. Select a source and press Add buttons from it..."
            node.IsEnabled = False
            self.ButtonsTree.Items.Add(node)
        self._selected_node = None
        self._refresh_buttons()

    def _placement_leaf(self, placement, sources):
        leaf = Windows.Controls.TreeViewItem()
        kind = placement.get("kind")
        if kind == "separator":
            text = u"———   separator"
        elif kind == "slideout":
            text = u"—▼—   slide-out (rows below open under the panel)"
        else:
            source = sources.get(placement.get("source"), {})
            text = u"{0}   - {1}".format(
                _one_line(placement.get("title")),
                source.get("label") or source.get("ext_name") or "?")
            if kind in ("pulldown", "splitbutton", "splitpushbutton"):
                text += "   [whole drop-down]"
            issue = self.placement_issues.get(placement.get("id"))
            if issue:
                text += "   ! " + issue
        leaf.Header = text
        leaf.Tag = ("placement", placement.get("id"))
        self._tree_items[placement.get("id")] = leaf
        return leaf

    def _refresh_status(self, notice=None):
        changes = state.count_changes(self.saved, self.working) + len(self.pending_deletes)
        text = state.status_line(self.working, changes)
        if notice:
            text = u"{0}   -   {1}".format(notice, text)
        self.status_tb.Text = text

    def _refresh_buttons(self):
        source = state.find_source_by_id(self.working, self._selected_source_id) \
            if self._selected_source_id is not None else None
        has_source = source is not None
        kind = source.get("kind") if has_source else None
        self.remove_source_btn.IsEnabled = has_source
        self.open_folder_btn.IsEnabled = has_source and self.open_folder is not None \
            and kind != "ribbon"
        self.locate_btn.Visibility = Windows.Visibility.Visible if kind == "dynamo" \
            else Windows.Visibility.Collapsed
        self.add_buttons_btn.IsEnabled = has_source
        node = self._selected_node
        is_placement = bool(node and node[0] == "placement")
        is_stack = bool(node and node[0] == "stack")
        is_dest = bool(node and node[0] == "dest")
        placement = state.find_placement_by_id(self.working, node[1]) if is_placement else None
        self.move_up_btn.IsEnabled = is_placement or is_stack
        self.move_down_btn.IsEnabled = is_placement or is_stack
        self.move_to_btn.IsEnabled = is_placement or is_stack
        self.rename_btn.IsEnabled = is_dest
        self.remove_placement_btn.IsEnabled = is_placement or is_dest
        self.remove_placement_btn.ToolTip = (
            "A stack is dissolved with Unstack; its buttons stay." if is_stack else None)
        stack_ok = False
        if is_placement:
            stack_ok, _ = state.can_group_with_next(self.working, node[1])
        self.stack_btn.IsEnabled = stack_ok
        self.unstack_btn.IsEnabled = is_stack or bool(placement and placement.get("stack"))
        dest_id = self._selected_dest_id()
        self.separator_btn.IsEnabled = dest_id is not None
        can_fold = dest_id is not None and not state.has_slideout(self.working, dest_id)
        self.slideout_btn.IsEnabled = can_fold
        self.slideout_btn.ToolTip = (
            "This panel already has a slide-out." if dest_id is not None and not can_fold
            else "Rows below the fold drop into the slide-out that opens under the panel.")

    def _rebuild(self, notice=None):
        self._is_ready = False
        try:
            self._populate_sources()
            self._populate_tree()
            self._refresh_status(notice)
        finally:
            self._is_ready = True

    # -- sources --

    def sources_selection_changed(self, sender, args):
        del sender, args
        item = self.SourcesList.SelectedItem
        self._selected_source_id = getattr(item, "Tag", None) if item is not None else None
        self._refresh_buttons()

    def hide_tab_click(self, sender, args):
        del args
        if not getattr(self, "_is_ready", False):
            return
        state.set_hide_tab(self.working, sender.Tag, bool(sender.IsChecked))
        source = state.find_source_by_id(self.working, sender.Tag)
        if source and bool(sender.IsChecked) and \
                any(state.normalize_label(t) in FROZEN_TABS for t in source.get("tab_names", [])):
            forms.alert("EasyBIM and Modify always stay visible; other tabs of this source will "
                        "be hidden.", title=TITLE)
        elif source and bool(sender.IsChecked) and \
                any(state.normalize_label(t) == "pyrevit" for t in source.get("tab_names", [])):
            forms.alert(PYREVIT_TAB_NOTE, title=TITLE, warn_icon=False)
        self._refresh_status()

    def add_source_click(self, sender, args):
        del sender, args
        self.result = "add_source"
        self.Close()

    def add_buttons_click(self, sender, args):
        del sender, args
        if self._selected_source_id is None:
            return
        self.result = ("add_buttons", self._selected_source_id)
        self.Close()

    def _placed_count_text(self, source):
        placements = [p for p in self.working.get("placements", [])
                      if p.get("source") == source.get("id")]
        if not placements:
            return ""
        return "\n\n{0} placed button{1} will be removed too.".format(
            len(placements), "" if len(placements) == 1 else "s")

    def remove_source_click(self, sender, args):
        del sender, args
        source = state.find_source_by_id(self.working, self._selected_source_id)
        if source is None:
            return
        message = "Remove {0} from My Ribbon?".format(source.get("label") or source.get("ext_name"))
        message += self._placed_count_text(source)
        if source.get("kind") == "dynamo":
            message += "\n\nThe graph file stays where it is; the button itself is deleted."
        elif source.get("kind") == "ribbon":
            message += "\n\nThe tab itself is not touched."
        else:
            message += "\n\nThe extension stays installed; pyRevit's Extensions window " \
                       "is where it can be uninstalled."
        if not forms.alert(message, title=TITLE, yes=True, no=True):
            return
        state.remove_source(self.working, source.get("id"))
        if source.get("kind") == "dynamo" and source.get("installed_by_my_ribbon"):
            # a Dynamo button is nothing but the bundle My Ribbon wrote
            self.pending_deletes.append(source)
        self._selected_source_id = None
        self._rebuild()

    def locate_click(self, sender, args):
        del sender, args
        source = state.find_source_by_id(self.working, self._selected_source_id)
        if source is None or source.get("kind") != "dynamo":
            return
        path = forms.pick_file(file_ext="dyn", title="Where is the graph now?")
        if not path:
            return
        source["path"] = path
        self._rebuild(notice="Graph path updated; press Apply.")

    def show_hide_tabs_click(self, sender, args):
        del sender, args
        window = TabVisibilityWindow("TabVisibilityWindow.xaml", self.ribbon_summary,
                                     state.hidden_tabs(self.working))
        window.ShowDialog()
        if window.result is None:
            return
        # tabs hidden in the registry but not on the ribbon right now (an
        # add-in that did not load today) were not listed: keep them hidden
        listed = set(state.normalize_label(t) for t in window.listed_titles)
        unseen = [n for n in state.hidden_tabs(self.working)
                  if state.normalize_label(n) not in listed]
        state.replace_hidden_tabs(self.working, list(window.result) + unseen)
        self._rebuild()

    def open_folder_click(self, sender, args):
        del sender, args
        source = state.find_source_by_id(self.working, self._selected_source_id)
        if source is None or self.open_folder is None:
            return
        try:
            if not self.open_folder(source):
                forms.alert("The folder for this source was not found.", title=TITLE)
        except Exception as ex:
            forms.alert("Could not open the folder:\n{0}".format(ex), title=TITLE)

    # -- destinations / placements --

    def tree_selection_changed(self, sender, args):
        del sender, args
        item = self.ButtonsTree.SelectedItem
        self._selected_node = getattr(item, "Tag", None) if item is not None else None
        self._refresh_buttons()

    def _selected_dest_id(self):
        """The destination the selected node belongs to, whatever its kind."""
        node = self._selected_node
        if not node:
            return None
        if node[0] == "dest":
            return node[1]
        if node[0] == "placement":
            placement = state.find_placement_by_id(self.working, node[1])
            return placement.get("dest") if placement else None
        if node[0] == "stack":
            for row in self.working.get("placements", []):
                if state.safe_text(row.get("stack")) == node[1]:
                    return row.get("dest")
        return None

    def _select_tree_item(self, item_id):
        item = self._tree_items.get(item_id)
        if item is not None:
            try:
                item.IsSelected = True
                item.BringIntoView()
            except Exception:
                pass

    def new_panel_click(self, sender, args):
        del sender, args
        self._create_destination(new_tab=False)

    def new_tab_click(self, sender, args):
        del sender, args
        self._create_destination(new_tab=True)

    def _create_destination(self, new_tab):
        window = DestinationWindow("DestinationWindow.xaml", self.ribbon_summary, self.working,
                                   mode="create", start_with_new_tab=new_tab)
        window.ShowDialog()
        if window.result and window.result != "back":
            tab, panel, own_tab = window.result
            dest = state.add_destination(self.working, tab, panel, own_tab)
            self._rebuild()
            self._select_tree_item(dest.get("id"))

    def move_up_click(self, sender, args):
        del sender, args
        self._move(-1)

    def move_down_click(self, sender, args):
        del sender, args
        self._move(1)

    def _move(self, delta):
        node = self._selected_node
        if not node or node[0] not in ("placement", "stack"):
            return
        if state.move_node(self.working, node[0], node[1], delta):
            self._rebuild()
            self._select_tree_item(node[1])

    def move_to_click(self, sender, args):
        del sender, args
        node = self._selected_node
        if not node or node[0] not in ("placement", "stack"):
            return
        current_dest = self._selected_dest_id()
        options = []
        for dest in self.working.get("destinations", []):
            if dest.get("id") != current_dest:
                options.append(_dest_label(dest))
        if not options:
            forms.alert("There is no other panel yet. Create one with New panel... or New tab...",
                        title=TITLE)
            return
        picked = forms.SelectFromList.show(options, title="Move to which panel?", button_name="Move",
                                           multiselect=False)
        if not picked:
            return
        for dest in self.working.get("destinations", []):
            if _dest_label(dest) == picked:
                if node[0] == "stack":
                    state.move_stack_to(self.working, node[1], dest.get("id"))
                else:
                    state.move_placement_to(self.working, node[1], dest.get("id"))
                break
        self._rebuild()
        self._select_tree_item(node[1])

    def stack_click(self, sender, args):
        del sender, args
        node = self._selected_node
        if not node or node[0] != "placement":
            return
        ok, reason = state.group_with_next(self.working, node[1])
        if not ok:
            forms.alert(reason, title=TITLE)
            return
        placement = state.find_placement_by_id(self.working, node[1])
        self._rebuild()
        self._select_tree_item(state.safe_text(placement.get("stack")) or node[1])

    def unstack_click(self, sender, args):
        del sender, args
        node = self._selected_node
        if not node or node[0] not in ("placement", "stack"):
            return
        if state.ungroup(self.working, node[1]):
            self._rebuild()
            if node[0] == "placement":
                self._select_tree_item(node[1])

    def separator_click(self, sender, args):
        del sender, args
        dest_id = self._selected_dest_id()
        if dest_id is None:
            return
        entry = state.add_separator(self.working, dest_id)
        self._rebuild()
        if entry is not None:
            self._select_tree_item(entry.get("id"))

    def slideout_click(self, sender, args):
        del sender, args
        dest_id = self._selected_dest_id()
        if dest_id is None:
            return
        entry = state.add_slideout(self.working, dest_id)
        if entry is None:
            forms.alert("This panel already has a slide-out.", title=TITLE)
            return
        self._rebuild()
        self._select_tree_item(entry.get("id"))

    def rename_click(self, sender, args):
        del sender, args
        node = self._selected_node
        if not node or node[0] != "dest":
            return
        dest = state.find_destination_by_id(self.working, node[1])
        if dest is None:
            return
        panel = forms.ask_for_string(default=dest.get("panel"), prompt="Panel name:",
                                     title="Rename panel")
        if panel is None:
            return
        tab = dest.get("tab")
        if dest.get("own_tab"):
            tab = forms.ask_for_string(default=dest.get("tab"), prompt="Tab name:",
                                       title="Rename tab") or dest.get("tab")
        state.rename_destination(self.working, node[1], tab=tab, panel=panel)
        self._rebuild()
        self._select_tree_item(node[1])

    def remove_placement_click(self, sender, args):
        del sender, args
        node = self._selected_node
        if not node:
            return
        if node[0] == "placement":
            state.remove_placement(self.working, node[1])
        elif node[0] == "dest":
            dest = state.find_destination_by_id(self.working, node[1])
            rows = state.placements_in(self.working, node[1])
            message = u"Remove the panel {0}?".format(_dest_label(dest))
            if rows:
                message += "\n\n{0} button{1} on it will be removed from My Ribbon.".format(
                    len(rows), "" if len(rows) == 1 else "s")
            if not forms.alert(message, title=TITLE, yes=True, no=True):
                return
            state.remove_destination(self.working, node[1])
        self._rebuild()

    # -- footer --

    def import_click(self, sender, args):
        del sender, args
        self.result = "import"
        self.Close()

    def export_click(self, sender, args):
        del sender, args
        self.result = "export"
        self.Close()

    def apply_click(self, sender, args):
        del sender, args
        self.result = "apply"
        self.Close()

    def close_click(self, sender, args):
        del sender, args
        # result stays None so _on_closing asks about unapplied changes
        self.Close()


# -- where from? -----------------------------------------------------------------


class SourceSelectionWindow(forms.WPFWindow):
    """Two cards: what is already on this computer (pyRevit extensions, Revit's
    own tabs and other add-ins) and Dynamo graphs.  An extension this computer
    does not have yet is installed in pyRevit's own Extensions window first.

    ``result`` is ``("installed", ext)``, ``("ribbon", tab)``,
    ``("dynamo", paths)`` or None.
    """

    def __init__(self, xaml_file_name, installed, ribbon_tabs=None, linked_names=None):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.installed = list(installed or [])
        self.ribbon_tabs = list(ribbon_tabs or [])
        self.linked_names = set(state.normalize_label(n) for n in (linked_names or []))
        self.result = None
        self._populate_installed()
        self._is_ready = True

    def _group_header(self, text):
        item = Windows.Controls.ListBoxItem()
        item.Content = _text_block(text, bold=True, grey=True, size=11)
        item.IsEnabled = False
        item.Margin = Windows.Thickness(0, 6, 0, 2)
        self.InstalledList.Items.Add(item)

    def _populate_installed(self):
        self.InstalledList.Items.Clear()
        self._group_header("pyRevit extensions")
        for ext in self.installed:
            item = Windows.Controls.ListBoxItem()
            panel = Windows.Controls.StackPanel()
            title = _safe_text(ext.get("name"))
            if state.normalize_label(title) in self.linked_names:
                title += "   (already a source)"
            panel.Children.Add(_text_block(title, bold=True))
            buttons = len(ext.get("buttons") or [])
            detail = "tabs: {0}   -   {1} button{2}".format(
                ", ".join(ext.get("tab_names") or []) or "none",
                buttons, "" if buttons == 1 else "s")
            panel.Children.Add(_text_block(detail, grey=True, size=11))
            item.Content = panel
            item.Tag = {"kind": "installed", "ext": ext}
            self.InstalledList.Items.Add(item)
        if self.ribbon_tabs:
            self._group_header("Other tabs on the ribbon (Revit's own and other add-ins)")
        for tab in self.ribbon_tabs:
            item = Windows.Controls.ListBoxItem()
            panel = Windows.Controls.StackPanel()
            title = _safe_text(tab.get("title"))
            if state.normalize_label(title) in self.linked_names:
                title += "   (already a source)"
            panel.Children.Add(_text_block(title, bold=True))
            panels = [p.get("title") for p in tab.get("panels") or [] if p.get("title")]
            detail = "panels: {0}".format(", ".join(panels) or "none")
            panel.Children.Add(_text_block(detail, grey=True, size=11))
            item.Content = panel
            item.Tag = {"kind": "ribbon", "tab": tab}
            self.InstalledList.Items.Add(item)
        count = len(self.installed)
        self.installed_count_tb.Text = (
            "{0} pyRevit extension{1} (EasyBIM included) and {2} other tab{3}. Buttons of Revit's own "
            "tabs and other add-ins can be placed too; only Remove applies to them.".format(
                count, "" if count == 1 else "s", len(self.ribbon_tabs),
                "" if len(self.ribbon_tabs) == 1 else "s"))

    def installed_selection_changed(self, sender, args):
        del sender, args
        item = self.InstalledList.SelectedItem
        self.use_installed_btn.IsEnabled = item is not None and \
            isinstance(getattr(item, "Tag", None), dict)

    def installed_double_click(self, sender, args):
        del sender, args
        self.use_installed_click(None, None)

    def use_installed_click(self, sender, args):
        del sender, args
        item = self.InstalledList.SelectedItem
        if item is None or not isinstance(getattr(item, "Tag", None), dict):
            return
        tag = item.Tag
        if tag.get("kind") == "ribbon":
            self.result = ("ribbon", tag.get("tab"))
        else:
            self.result = ("installed", tag.get("ext"))
        self.Close()

    def add_dynamo_click(self, sender, args):
        del sender, args
        picked = forms.pick_file(file_ext="dyn", multi_file=True,
                                 title="Choose Dynamo graphs (.dyn)")
        if not picked:
            return
        if isinstance(picked, type(u"")) or isinstance(picked, str):
            picked = [picked]
        self.result = ("dynamo", list(picked))
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


# -- which buttons? ---------------------------------------------------------------


class ButtonSelectionWindow(forms.WPFWindow):
    """Tab > Panel groups of check-boxes; a drop-down can be taken whole or
    by child.  ``result`` is ``"next"`` (see ``selected``), ``"back"`` or None."""

    def __init__(self, xaml_file_name, source_label, described, already_placed=None,
                 host_version=None, preselected_paths=None):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.described = described or {}
        self.already_placed = dict(already_placed or {})
        self.host_version = host_version
        self.result = None
        self.selected = []
        self._checkboxes = []      # (checkbox, button)
        self._children_of = {}     # pulldown path_key -> [child checkboxes]
        self._expanders = []
        self._expanded = {}
        self._checked = set(preselected_paths or [])
        self.header_tb.Text = "Which buttons from {0}?".format(source_label)
        self.Title = "My Ribbon - {0}".format(source_label)
        count = len([b for b in self.described.get("buttons", []) if state.is_placeable(b)])
        self.subtitle_tb.Text = "{0} button{1} in {2}. Tick the ones you want on your ribbon.".format(
            count, "" if count == 1 else "s",
            ", ".join(self.described.get("tab_names") or []) or "this extension")
        warnings = []
        if self.described.get("has_startup"):
            warnings.append("runs a startup script")
        if self.described.get("has_hooks"):
            warnings.append("has event hooks")
        if warnings:
            self.warning_tb.Text = ("This extension {0}. Because it is installed as a normal "
                                    "pyRevit extension, all of that keeps working - only its tab is "
                                    "hidden if you choose so.").format(" and ".join(warnings))
            self.warning_border.Visibility = Windows.Visibility.Visible
        self._populate()
        self._is_ready = True

    def _populate(self):
        self.ButtonListPanel.Children.Clear()
        self._checkboxes = []
        self._children_of = {}
        self._expanders = []
        needle = _search_text(self.SearchBox)
        shown = 0
        for tab in self.described.get("tabs", []):
            for panel in tab.get("panels", []):
                rows_panel = Windows.Controls.StackPanel()
                visible = 0
                for item in panel.get("items", []):
                    visible += self._add_row(rows_panel, item, needle, indent=0)
                if not visible:
                    continue
                shown += visible
                expander = Windows.Controls.Expander()
                key = u"{0}{1}{2}".format(tab.get("title"), ARROW, panel.get("title"))
                expander.Header = u"{0}   ({1})".format(_one_line(key), visible)
                expander.IsExpanded = self._expanded.get(key, True)
                expander.Tag = key
                expander.Margin = Windows.Thickness(8, 6, 8, 4)
                expander.Content = rows_panel
                self.ButtonListPanel.Children.Add(expander)
                self._expanders.append(expander)
        if not shown:
            _add_empty_text(self.ButtonListPanel, "No buttons match the search."
                            if needle else "This extension has no buttons that can be placed.")
        self._refresh_status()

    def _add_row(self, panel, item, needle, indent):
        title = _one_line(item.get("title"))
        tags = state.button_tags(item, self.host_version)
        placed_in = self.already_placed.get(state.path_key(item.get("path")))
        haystack = " ".join([title, _safe_text(item.get("tooltip")), " ".join(tags)]).lower()
        children = item.get("children") or []
        matches_self = not needle or needle in haystack
        child_count = 0
        child_panel = None
        child_boxes = []
        if children:
            child_panel = Windows.Controls.StackPanel()
            before = len(self._checkboxes)
            for child in children:
                child_count += self._add_row(child_panel, child, needle, indent + 1)
            child_boxes = [cb for cb, _ in self._checkboxes[before:]]
        if not matches_self and not child_count:
            return 0

        checkbox = Windows.Controls.CheckBox()
        content = Windows.Controls.StackPanel()
        content.Orientation = Windows.Controls.Orientation.Horizontal
        icon = _small_icon(item.get("icon"), item.get("icon_source"))
        if icon is not None:
            content.Children.Add(icon)
        content.Children.Add(_text_block(title))
        extras = list(tags)
        if placed_in:
            extras.append("already placed in " + placed_in)
        if extras:
            content.Children.Add(_text_block("   " + "; ".join(extras), grey=True, size=11))
        checkbox.Content = content
        checkbox.Tag = item
        checkbox.Margin = Windows.Thickness(18 + indent * 22, 4, 8, 4)
        if item.get("tooltip"):
            checkbox.ToolTip = item.get("tooltip")
        placeable = state.is_placeable(item)
        checkbox.IsEnabled = placeable and not placed_in
        checkbox.IsChecked = state.path_key(item.get("path")) in self._checked and checkbox.IsEnabled
        checkbox.Click += self.row_click
        panel.Children.Add(checkbox)
        self._checkboxes.append((checkbox, item))
        if child_panel is not None:
            panel.Children.Add(child_panel)
            self._children_of[state.path_key(item.get("path"))] = child_boxes
            self._apply_container_rule(checkbox)
        return 1 + child_count

    def _apply_container_rule(self, container_checkbox):
        """Taking the whole drop-down disables its children (they come along)."""
        key = state.path_key(container_checkbox.Tag.get("path"))
        whole = bool(container_checkbox.IsChecked)
        for child in self._children_of.get(key, []):
            if whole:
                child.IsChecked = False
                self._checked.discard(state.path_key(child.Tag.get("path")))
            child.IsEnabled = (not whole) and state.is_placeable(child.Tag) and \
                not self.already_placed.get(state.path_key(child.Tag.get("path")))

    def row_click(self, sender, args):
        del args
        item = sender.Tag
        key = state.path_key(item.get("path"))
        if sender.IsChecked:
            self._checked.add(key)
        else:
            self._checked.discard(key)
        if key in self._children_of:
            self._apply_container_rule(sender)
        self._refresh_status()

    def _refresh_status(self):
        count = len(self._checked)
        self.status_tb.Text = "{0} button{1} ticked.".format(count, "" if count == 1 else "s")
        self.next_btn.IsEnabled = count > 0

    def _sync_expanders(self):
        for expander in self._expanders:
            self._expanded[_safe_text(expander.Tag)] = bool(expander.IsExpanded)

    def search_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._sync_expanders()
        self._populate()

    def expand_all_click(self, sender, args):
        del sender, args
        for expander in self._expanders:
            expander.IsExpanded = True
            self._expanded[_safe_text(expander.Tag)] = True

    def collapse_all_click(self, sender, args):
        del sender, args
        for expander in self._expanders:
            expander.IsExpanded = False
            self._expanded[_safe_text(expander.Tag)] = False

    def select_all_click(self, sender, args):
        del sender, args
        for checkbox, item in self._checkboxes:
            if checkbox.IsEnabled:
                checkbox.IsChecked = True
                self._checked.add(state.path_key(item.get("path")))
        for checkbox, item in self._checkboxes:
            if state.path_key(item.get("path")) in self._children_of:
                self._apply_container_rule(checkbox)
        self._refresh_status()

    def select_none_click(self, sender, args):
        del sender, args
        for checkbox, item in self._checkboxes:
            checkbox.IsChecked = False
        self._checked = set()
        for checkbox, item in self._checkboxes:
            if state.path_key(item.get("path")) in self._children_of:
                self._apply_container_rule(checkbox)
        self._refresh_status()

    def _collect_selected(self):
        by_key = {}
        for button in self.described.get("buttons", []):
            by_key[state.path_key(button.get("path"))] = button
        selected = []
        for key in self._checked:
            button = by_key.get(key)
            if button is not None:
                selected.append(button)
        # keep the ribbon's own order
        order = dict((state.path_key(b.get("path")), i)
                     for i, b in enumerate(self.described.get("buttons", [])))
        selected.sort(key=lambda b: order.get(state.path_key(b.get("path")), 0))
        return selected

    def back_click(self, sender, args):
        del sender, args
        self.selected = self._collect_selected()
        self.result = "back"
        self.Close()

    def next_click(self, sender, args):
        del sender, args
        self.selected = self._collect_selected()
        if not self.selected:
            forms.alert("Tick at least one button.", title=TITLE)
            return
        self.result = "next"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


# -- where to? --------------------------------------------------------------------


class DestinationWindow(forms.WPFWindow):
    """Tab + panel choice.  ``ribbon_summary`` is ``easybim.my_ribbon.
    list_ribbon()``; staged destinations from ``registry`` are offered too.
    ``result`` is ``(tab, panel, own_tab)``, ``"back"`` or None."""

    def __init__(self, xaml_file_name, ribbon_summary, registry, mode="add",
                 start_with_new_tab=False, count=0):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.ribbon_summary = list(ribbon_summary or [])
        self.registry = registry
        self.mode = mode
        self.result = None
        if mode == "create":
            self.header_tb.Text = "New tab" if start_with_new_tab else "New panel"
            self.subtitle_tb.Text = "Create the place first; add buttons to it afterwards."
            self.ok_btn.Content = "Create"
            self.back_btn.Visibility = Windows.Visibility.Collapsed
        elif count:
            self.header_tb.Text = "Where should {0} button{1} go?".format(
                count, "" if count == 1 else "s")
        self._tabs = self._tab_choices()
        for title in self._tabs:
            self.TabCombo.Items.Add(title)
        preferred = NEW_TAB_CHOICE if start_with_new_tab else EASYBIM_TAB
        index = self._tabs.index(preferred) if preferred in self._tabs else 0
        self.TabCombo.SelectedIndex = index
        self._fill_panels()
        self._is_ready = True
        self._refresh()

    def _own_tab_titles(self):
        titles = []
        for dest in self.registry.get("destinations", []):
            if dest.get("own_tab") and dest.get("tab") not in titles:
                titles.append(dest.get("tab"))
        for tab in self.ribbon_summary:
            if tab.get("is_ours") and tab.get("title") not in titles:
                titles.append(tab.get("title"))
        return titles

    def _tab_choices(self):
        choices = [EASYBIM_TAB]
        for title in self._own_tab_titles():
            if title not in choices:
                choices.append(title)
        choices.append(NEW_TAB_CHOICE)
        for tab in self.ribbon_summary:
            title = tab.get("title")
            if title and title not in choices and not tab.get("is_ours"):
                choices.append(title)
        return choices

    def _panels_for(self, tab_title):
        panels = []
        for tab in self.ribbon_summary:
            if state.normalize_label(tab.get("title")) == state.normalize_label(tab_title):
                for panel in tab.get("panels", []):
                    if panel.get("title") and panel.get("title") not in panels:
                        panels.append(panel.get("title"))
        for dest in self.registry.get("destinations", []):
            if state.normalize_label(dest.get("tab")) == state.normalize_label(tab_title) \
                    and dest.get("panel") not in panels:
                panels.append(dest.get("panel"))
        return panels

    def _selected_tab(self):
        return _safe_text(self.TabCombo.SelectedItem)

    def _fill_panels(self):
        self.PanelCombo.Items.Clear()
        tab = self._selected_tab()
        panels = [] if tab == NEW_TAB_CHOICE else self._panels_for(tab)
        for title in panels:
            self.PanelCombo.Items.Add(title)
        self.PanelCombo.Items.Add(NEW_PANEL_CHOICE)
        # A brand-new tab needs a new panel; an existing tab defaults to its
        # first panel of ours, else to a new one.
        default_index = self.PanelCombo.Items.Count - 1
        for index, title in enumerate(panels):
            if any(state.normalize_label(d.get("panel")) == state.normalize_label(title)
                   and state.normalize_label(d.get("tab")) == state.normalize_label(tab)
                   for d in self.registry.get("destinations", [])):
                default_index = index
                break
        self.PanelCombo.SelectedIndex = default_index

    def _refresh(self):
        if not getattr(self, "_is_ready", False):
            return
        tab = self._selected_tab()
        new_tab = tab == NEW_TAB_CHOICE
        shown = Windows.Visibility.Visible
        hidden = Windows.Visibility.Collapsed
        self.new_tab_label.Visibility = shown if new_tab else hidden
        self.NewTabBox.Visibility = self.new_tab_label.Visibility
        new_panel = _safe_text(self.PanelCombo.SelectedItem) == NEW_PANEL_CHOICE
        self.new_panel_label.Visibility = shown if new_panel else hidden
        self.NewPanelBox.Visibility = self.new_panel_label.Visibility
        own = new_tab or tab in self._own_tab_titles()
        if new_tab:
            hint = "A tab of your own. It is created the moment you press Apply and rebuilt " \
                   "every session from your settings."
        elif tab == EASYBIM_TAB:
            hint = "The EasyBIM tab. New panels are added at its end; existing panels get the " \
                   "buttons after their own."
        elif own:
            hint = "One of your own tabs."
        else:
            hint = "Another tab of the ribbon. Panels of other add-ins are left as they are; " \
                   "only your buttons are added."
        self.hint_tb.Text = hint
        tab_name, panel_name = self._names()
        ok = bool(tab_name and panel_name)
        self.ok_btn.IsEnabled = ok
        self.status_tb.Text = "" if ok else "Type a name for the new tab or panel."

    def _names(self):
        tab = self._selected_tab()
        if tab == NEW_TAB_CHOICE:
            tab = _safe_text(self.NewTabBox.Text).strip()
        panel = _safe_text(self.PanelCombo.SelectedItem)
        if panel == NEW_PANEL_CHOICE:
            panel = _safe_text(self.NewPanelBox.Text).strip()
        return tab, panel

    def tab_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._fill_panels()
        self._refresh()

    def panel_changed(self, sender, args):
        del sender, args
        self._refresh()

    def names_changed(self, sender, args):
        del sender, args
        self._refresh()

    def back_click(self, sender, args):
        del sender, args
        self.result = "back"
        self.Close()

    def ok_click(self, sender, args):
        del sender, args
        tab, panel = self._names()
        if not tab or not panel:
            forms.alert("Type a name for the new tab or panel.", title=TITLE)
            return
        own_tab = self._selected_tab() == NEW_TAB_CHOICE or tab in self._own_tab_titles()
        if state.normalize_label(tab) == "pyrevit":
            forms.alert("The pyRevit tab is left alone; choose another tab.", title=TITLE)
            return
        self.result = (tab, panel, own_tab)
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


# -- import preview ---------------------------------------------------------------


class ImportPreviewWindow(forms.WPFWindow):
    """``previews`` maps ``"merge"``/``"replace"`` to a list of sentences.
    ``result`` is the chosen mode or None."""

    def __init__(self, xaml_file_name, file_path, previews):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.previews = dict(previews or {})
        self.result = None
        self.file_tb.Text = _safe_text(file_path)
        self._is_ready = True
        self._show()

    def _mode(self):
        return "replace" if bool(self.replace_radio.IsChecked) else "merge"

    def _show(self):
        self.preview_tb.Text = "\n".join(self.previews.get(self._mode(), []))

    def mode_changed(self, sender, args):
        del sender, args
        if not getattr(self, "_is_ready", False):
            return
        self._show()

    def import_click(self, sender, args):
        del sender, args
        self.result = self._mode()
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


class _ImportResultRow(object):
    """One grid line.  The names are what ImportResultsDialog.xaml binds to."""

    __slots__ = ("name", "kind", "status", "detail")

    def __init__(self, name, kind, status, detail):
        self.name = name
        self.kind = kind
        self.status = status
        self.detail = detail


class ImportResultsWindow(forms.WPFWindow):
    """What an import installed and changed: one line per extension and setting."""

    def __init__(self, xaml_file_name, rows, summary):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.results_dg.ItemsSource = [
            _ImportResultRow(_safe_text(r.get("name")), _safe_text(r.get("kind")),
                             _safe_text(r.get("status")), _safe_text(r.get("detail")))
            for r in (rows or [])]
        self.summary_tb.Text = "  |  ".join(summary or [])
        self._is_ready = True

    def ok_clicked(self, sender, args):
        del sender, args
        self.Close()


# -- credentials -------------------------------------------------------------------


class CredentialsWindow(forms.WPFWindow):
    """``result`` is ``(user, token)`` or None."""

    def __init__(self, xaml_file_name, message):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = None
        self.message_tb.Text = _safe_text(message)

    def ok_click(self, sender, args):
        del sender, args
        user = _safe_text(self.UserBox.Text).strip()
        token = _safe_text(self.TokenBox.Password)
        if not user or not token:
            forms.alert("Both a user name and a token are needed.", title=TITLE)
            return
        self.result = (user, token)
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


# -- show / hide tabs -----------------------------------------------------------


class TabVisibilityWindow(forms.WPFWindow):
    """Every ribbon tab with a checkbox (ticked = shown).  EasyBIM and Modify
    are ticked and frozen; pyRevit carries a note; tabs that are invisible for
    some other reason (contextual, hidden by another add-in) are shown
    unticked and disabled.  ``result`` is the list of tab titles to hide, or
    None on Cancel."""

    def __init__(self, xaml_file_name, ribbon_summary, hidden_names):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = None
        self._rows = []
        self.listed_titles = []
        hidden = set(state.normalize_label(n) for n in (hidden_names or []))
        for tab in ribbon_summary or []:
            title = _safe_text(tab.get("title"))
            if not title or tab.get("is_contextual"):
                continue
            key = state.normalize_label(title)
            if key == state.normalize_label(state.DYNAMO_LIBRARY_TAB):
                # My Ribbon's own library tab is never meant to be seen
                continue
            checkbox = Windows.Controls.CheckBox()
            checkbox.Margin = Windows.Thickness(8, 5, 8, 5)
            checkbox.Tag = title
            note = ""
            frozen = key in FROZEN_TABS
            ours_hidden = key in hidden
            if frozen:
                checkbox.IsChecked = True
                checkbox.IsEnabled = False
                note = "always shown" + (" - My Ribbon lives here" if key == "easybim"
                                         else " - Revit's editing tab")
            elif not ours_hidden and tab.get("is_visible") is False:
                checkbox.IsChecked = False
                checkbox.IsEnabled = False
                note = "hidden by Revit or another add-in"
            else:
                checkbox.IsChecked = not ours_hidden
                if key == "pyrevit":
                    note = PYREVIT_TAB_NOTE
                elif tab.get("is_ours"):
                    note = "one of your own tabs"
            content = Windows.Controls.StackPanel()
            content.Orientation = Windows.Controls.Orientation.Horizontal
            content.Children.Add(_text_block(title))
            if note:
                content.Children.Add(_text_block("   " + note, grey=True, size=11))
            checkbox.Content = content
            checkbox.Click += self.row_click
            self.TabListPanel.Children.Add(checkbox)
            self._rows.append(checkbox)
            self.listed_titles.append(title)
        if not self._rows:
            _add_empty_text(self.TabListPanel, "No tabs were found on the ribbon.")
        self._is_ready = True
        self._refresh()

    def _hidden_titles(self):
        return [_safe_text(row.Tag) for row in self._rows if not bool(row.IsChecked)
                and row.IsEnabled]

    def _refresh(self):
        hidden = self._hidden_titles()
        self.count_tb.Text = "{0} of {1} tabs hidden.".format(len(hidden), len(self._rows))
        self.status_tb.Text = "Hidden: " + ", ".join(hidden) if hidden else "Every tab is shown."

    def row_click(self, sender, args):
        del sender, args
        self._refresh()

    def select_all_click(self, sender, args):
        del sender, args
        for row in self._rows:
            if row.IsEnabled:
                row.IsChecked = True
        self._refresh()

    def select_none_click(self, sender, args):
        del sender, args
        for row in self._rows:
            if row.IsEnabled:
                row.IsChecked = False
        self._refresh()

    def confirm_click(self, sender, args):
        del sender, args
        hidden = self._hidden_titles()
        if any(state.normalize_label(t) == "pyrevit" for t in hidden):
            if not forms.alert("Hide the pyRevit tab?\n\n" + PYREVIT_TAB_NOTE,
                               title=TITLE, yes=True, no=True):
                return
        self.result = hidden
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()


# -- new Dynamo button -------------------------------------------------------------


class DynamoButtonWindow(forms.WPFWindow):
    """Title and icon for one picked graph.  ``result`` is
    ``{"title", "icon"}`` (icon = PNG path or None), ``"skip"`` or None."""

    def __init__(self, xaml_file_name, path, facts, default_title, remaining=0):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.path = path
        self.result = None
        self.icon_path = None
        self.path_tb.Text = _safe_text(path)
        self.TitleBox.Text = _safe_text(default_title)
        tags = state.dynamo_tags(facts or {})
        fmt = (facts or {}).get("format")
        lines = []
        if fmt == "2.x":
            lines.append("Dynamo 2.x graph" + (' "{0}"'.format(facts.get("name")) if facts.get("name") else ""))
        elif fmt == "1.x":
            lines.append("Dynamo 1.x graph" + (' "{0}"'.format(facts.get("name")) if facts.get("name") else ""))
        for tag in tags:
            if not tag.startswith("Dynamo 1.x"):
                lines.append(tag[0].upper() + tag[1:])
        if not lines:
            lines.append("Nothing special was read from the file.")
        self.facts_tb.Text = "\n".join(lines)
        if remaining:
            self.status_tb.Text = "{0} more graph{1} to go.".format(remaining, "" if remaining == 1 else "s")
        self._is_ready = True
        self._refresh()

    def _refresh(self):
        if not getattr(self, "_is_ready", False):
            return
        custom = bool(self.custom_icon_radio.IsChecked)
        self.choose_icon_btn.IsEnabled = custom
        self.icon_path_tb.Text = _safe_text(self.icon_path) if custom else ""
        title_ok = bool(_safe_text(self.TitleBox.Text).strip())
        icon_ok = (not custom) or bool(self.icon_path)
        self.ok_btn.IsEnabled = title_ok and icon_ok
        if not title_ok:
            self.status_tb.Text = "Type a title for the button."
        elif not icon_ok:
            self.status_tb.Text = "Choose a PNG file, or keep Dynamo's own look."

    def title_changed(self, sender, args):
        del sender, args
        self._refresh()

    def icon_choice_changed(self, sender, args):
        del sender, args
        self._refresh()

    def choose_icon_click(self, sender, args):
        del sender, args
        path = forms.pick_file(file_ext="png", title="Choose an icon (PNG, ideally 96 x 96)")
        if path:
            self.icon_path = path
            self.custom_icon_radio.IsChecked = True
        self._refresh()

    def ok_click(self, sender, args):
        del sender, args
        title = _safe_text(self.TitleBox.Text).strip()
        if not title:
            forms.alert("Type a title for the button.", title=TITLE)
            return
        custom = bool(self.custom_icon_radio.IsChecked)
        if custom and not self.icon_path:
            forms.alert("Choose a PNG file, or keep Dynamo's own look.", title=TITLE)
            return
        self.result = {"title": title, "icon": self.icon_path if custom else None}
        self.Close()

    def skip_click(self, sender, args):
        del sender, args
        self.result = "skip"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = None
        self.Close()

