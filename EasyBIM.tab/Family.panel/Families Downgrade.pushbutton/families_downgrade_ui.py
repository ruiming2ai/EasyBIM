# -*- coding: utf-8 -*-
"""WPF windows specific to the Families Downgrade command.

The family-selection pages are shared with Families Transfer and live in
``easybim.family_selection_ui``; here are the mode page, the export options
page and the rebuild page. No Revit API import belongs in this module: the
folder pickers are handed in as callables by ``script.py``.
"""

from pyrevit import forms

from easybim.family_selection_ui import _fill_checkbox_list
from easybim.family_selection_ui import _set_all_checked
from easybim.family_selection_ui import _sync_checkbox_options

from families_downgrade_state import find_packages


TITLE = "Families Downgrade"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _text_of(control):
    try:
        return _safe_text(control.Text).strip()
    except Exception:
        return ""


def _is_checked(control):
    try:
        return bool(control.IsChecked)
    except Exception:
        return False


def package_row_text(package):
    """One package row: family, category, the Revit it came from, its types."""
    label = _safe_text(getattr(package, "family_name", ""))
    details = []
    category = _safe_text(getattr(package, "category_label", ""))
    if category:
        details.append(category)
    version = _safe_text(getattr(package, "source_version", ""))
    if version:
        details.append("from Revit {}".format(version))
    type_count = int(getattr(package, "type_count", 0) or 0)
    if type_count:
        details.append("{} type{}".format(type_count, "" if type_count == 1 else "s"))
    if details:
        label = "{}  ({})".format(label, ", ".join(details))
    reason = _safe_text(getattr(package, "reason", ""))
    if reason and not bool(getattr(package, "is_usable", True)):
        label = "{}  - {}".format(label, reason)
    return label


def package_count_text(packages):
    packages = list(packages or [])
    usable = [p for p in packages if bool(getattr(p, "is_usable", False))]
    checked = [p for p in usable if bool(getattr(p, "is_selected", False))]
    if not packages:
        return "No packages loaded yet."
    text = "{} of {} package(s) selected.".format(len(checked), len(usable))
    broken = len(packages) - len(usable)
    if broken:
        text = "{} {} cannot be used.".format(text, broken)
    return text


class ModeWindow(forms.WPFWindow):
    """Page 0: downgrade in one go, or either half of it on its own."""

    def __init__(self, xaml_file_name, host_version="", export_available=True,
                 export_hint=""):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = None
        self.mode = "downgrade" if export_available else "rebuild"
        host_version = _safe_text(host_version)
        self.host_tb.Text = (
            "This is Revit {}. Families downgraded from here are written by the Revit you "
            "pick, not by this one.".format(host_version)
            if host_version else
            "Families downgraded from here are written by the Revit you pick.")
        if not export_available:
            self.downgrade_rb.IsEnabled = False
            self.export_rb.IsEnabled = False
            self.rebuild_rb.IsChecked = True
            hint = _safe_text(export_hint)
            if hint:
                self.export_hint_tb.Text = hint
        self._is_ready = True

    def _read_mode(self):
        for name, mode in (("rebuild_rb", "rebuild"), ("export_rb", "export")):
            control = getattr(self, name, None)
            if control is not None and _is_checked(control):
                return mode
        return "downgrade"

    def mode_changed(self, sender, args):
        del sender, args
        # The radios raise Checked while the XAML is still loading, before
        # __init__ has run; nothing to read yet.
        if not getattr(self, "_is_ready", False):
            return
        self.mode = self._read_mode()

    def next_click(self, sender, args):
        del sender, args
        self.mode = self._read_mode()
        self.result = self.mode
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = "cancel"
        self.Close()


class ExportOptionsWindow(forms.WPFWindow):
    """The last export page: where the packages go, and how."""

    def __init__(self, xaml_file_name, family_count, pick_folder, folder="",
                 split_types=False):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._pick_folder = pick_folder
        self.result = None
        self.folder = _safe_text(folder)
        self.split_types = bool(split_types)
        family_count = int(family_count or 0)
        self.count_tb.Text = "{} famil{} selected.".format(
            family_count, "y" if family_count == 1 else "ies")
        self.folder_box.Text = self.folder
        self.split_types_cb.IsChecked = self.split_types

    def _read(self):
        self.folder = _text_of(self.folder_box)
        self.split_types = _is_checked(self.split_types_cb)

    def browse_click(self, sender, args):
        del sender, args
        if self._pick_folder is None:
            return
        picked = self._pick_folder("Select a folder for the downgrade packages.")
        if picked:
            self.folder_box.Text = _safe_text(picked)

    def back_click(self, sender, args):
        del sender, args
        self._read()
        self.result = "back"
        self.Close()

    def export_click(self, sender, args):
        del sender, args
        self._read()
        if not self.folder:
            forms.alert("Choose a folder for the packages first.", title=TITLE)
            return
        self.result = "export"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self._read()
        self.result = "cancel"
        self.Close()


class DowngradeOptionsWindow(forms.WPFWindow):
    """The one page of a downgrade: which Revit, and where the files go.

    The version list is handed in rather than read here, so this module keeps
    away from both Revit and the machine - the same split ``RebuildWindow``
    makes with its folder picker.
    """

    def __init__(self, xaml_file_name, family_count, pick_folder, choices,
                 output_folder="", target_version="", split_types=False, notes=None):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._pick_folder = pick_folder
        self.result = None
        self.choices = list(choices or [])
        self.output_folder = _safe_text(output_folder)
        self.target_version = _safe_text(target_version)
        self.split_types = bool(split_types)

        family_count = int(family_count or 0)
        self.count_tb.Text = "{} famil{} selected.".format(
            family_count, "y" if family_count == 1 else "ies")
        self.output_box.Text = self.output_folder
        self.split_types_cb.IsChecked = self.split_types
        self.status_tb.Text = "\n".join(_safe_text(note) for note in list(notes or []))

        for choice in self.choices:
            self.target_cb.Items.Add(choice.label)
        self.target_cb.IsEnabled = bool(self.choices)
        self.target_cb.SelectedIndex = self._initial_index()
        self._is_ready = True
        self._show_selected_note()

    def _initial_index(self):
        if not self.choices:
            return -1
        for index, choice in enumerate(self.choices):
            if self.target_version and choice.version == self.target_version:
                return index
        for index, choice in enumerate(self.choices):
            if choice.is_enabled:
                return index
        return 0

    @property
    def selected_choice(self):
        index = -1
        try:
            index = int(self.target_cb.SelectedIndex)
        except Exception:
            index = -1
        if 0 <= index < len(self.choices):
            return self.choices[index]
        return None

    def _show_selected_note(self):
        """A release this tool cannot rebuild for says so on the page, rather
        than being missing from a list the user cannot see the bottom of."""
        choice = self.selected_choice
        self.target_note_tb.Text = _safe_text(choice.reason) if choice is not None else ""

    def _read(self):
        self.output_folder = _text_of(self.output_box)
        self.split_types = _is_checked(self.split_types_cb)
        choice = self.selected_choice
        self.target_version = choice.version if choice is not None else ""

    def target_changed(self, sender, args):
        del sender, args
        # The combo raises this while the XAML is still loading.
        if not getattr(self, "_is_ready", False):
            return
        self._show_selected_note()

    def browse_click(self, sender, args):
        del sender, args
        if self._pick_folder is None:
            return
        picked = self._pick_folder("Select a folder for the downgraded family files.")
        if picked:
            self.output_box.Text = _safe_text(picked)

    def back_click(self, sender, args):
        del sender, args
        self._read()
        self.result = "back"
        self.Close()

    def downgrade_click(self, sender, args):
        del sender, args
        self._read()
        choice = self.selected_choice
        if choice is None:
            forms.alert("No Revit was found to rebuild the families in.", title=TITLE)
            return
        if not choice.is_enabled:
            forms.alert(choice.reason, title=TITLE)
            return
        if not self.output_folder:
            forms.alert("Choose a folder for the downgraded families first.", title=TITLE)
            return
        self.result = "downgrade"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self._read()
        self.result = "cancel"
        self.Close()


class RebuildWindow(forms.WPFWindow):
    """The rebuild page: a package folder, its packages, an output folder."""

    def __init__(self, xaml_file_name, pick_folder, host_version="", package_folder="",
                 output_folder="", packages=None):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._pick_folder = pick_folder
        self.result = None
        self.package_folder = _safe_text(package_folder)
        self.output_folder = _safe_text(output_folder)
        self.packages = list(packages or [])
        if self.package_folder and not self.packages:
            # Coming back to this page, or reopening it, must not lose the
            # folder's contents: the list is only ever filled by a Browse.
            self.packages = find_packages(self.package_folder)
        self._controls = []
        host_version = _safe_text(host_version)
        self.host_tb.Text = (
            "Every package becomes a Revit {} family file. Rebuilt solids are static; the "
            "report next to the files says what each family lost.".format(host_version)
            if host_version else
            "Every package becomes a family file of this Revit. Rebuilt solids are static; "
            "the report next to the files says what each family lost.")
        self.package_folder_box.Text = self.package_folder
        self.output_folder_box.Text = self.output_folder
        self._populate()

    def _populate(self):
        self._controls = _fill_checkbox_list(
            self.PackageListPanel,
            self.packages,
            package_row_text,
            "Choose a folder holding *.downgrade packages."
            if not self.package_folder else "No downgrade packages were found in that folder.",
            enabled_for=lambda package: bool(getattr(package, "is_usable", False)),
        )
        self._refresh_count()

    def _refresh_count(self):
        self.count_tb.Text = package_count_text(self.packages)

    def _read(self):
        _sync_checkbox_options(self._controls)
        self.package_folder = _text_of(self.package_folder_box)
        self.output_folder = _text_of(self.output_folder_box)

    @property
    def selected_packages(self):
        return [package for package in self.packages
                if bool(getattr(package, "is_usable", False))
                and bool(getattr(package, "is_selected", False))]

    def browse_packages_click(self, sender, args):
        del sender, args
        if self._pick_folder is None:
            return
        picked = self._pick_folder("Select the folder holding the downgrade packages.",
                                   allow_new_folder=False)
        if not picked:
            return
        self.package_folder = _safe_text(picked)
        self.package_folder_box.Text = self.package_folder
        self.packages = find_packages(self.package_folder)
        if not _text_of(self.output_folder_box):
            self.output_folder_box.Text = self.package_folder
        self._populate()
        if not self.packages:
            forms.alert(
                "No downgrade packages were found in:\n{}\n\nA package is a folder holding "
                "manifest.json - the folders an export wrote, named *.downgrade. Choose the "
                "folder the export was written into, or the folder above it.".format(
                    self.package_folder),
                title=TITLE)

    def browse_output_click(self, sender, args):
        del sender, args
        if self._pick_folder is None:
            return
        picked = self._pick_folder("Select a folder for the rebuilt family files.")
        if picked:
            self.output_folder_box.Text = _safe_text(picked)

    def select_all_click(self, sender, args):
        del sender, args
        _set_all_checked(self._controls, True, only_enabled=True)
        _sync_checkbox_options(self._controls)
        self._refresh_count()

    def select_none_click(self, sender, args):
        del sender, args
        _set_all_checked(self._controls, False)
        _sync_checkbox_options(self._controls)
        self._refresh_count()

    def back_click(self, sender, args):
        del sender, args
        self._read()
        self.result = "back"
        self.Close()

    def rebuild_click(self, sender, args):
        del sender, args
        self._read()
        if not self.selected_packages:
            forms.alert("Tick at least one usable package first.", title=TITLE)
            return
        if not self.output_folder:
            forms.alert("Choose an output folder for the rebuilt families first.", title=TITLE)
            return
        self.result = "rebuild"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self._read()
        self.result = "cancel"
        self.Close()
