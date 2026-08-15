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

from families_downgrade_state import GEOMETRY_DWG
from families_downgrade_state import GEOMETRY_SAT
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
    """Page 0: export packages here, or rebuild families from packages."""

    def __init__(self, xaml_file_name, host_version="", export_available=True,
                 export_hint=""):
        self._is_ready = False
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = None
        self.mode = "export" if export_available else "rebuild"
        host_version = _safe_text(host_version)
        self.host_tb.Text = (
            "This is Revit {}. Packages written here can be rebuilt by Revit 2021 or "
            "later; families rebuilt here are Revit {} files.".format(host_version, host_version)
            if host_version else
            "Packages written here can be rebuilt by Revit 2021 or later.")
        if not export_available:
            self.export_rb.IsEnabled = False
            self.rebuild_rb.IsChecked = True
            hint = _safe_text(export_hint)
            if hint:
                self.export_hint_tb.Text = hint
        self._is_ready = True

    def _read_mode(self):
        rebuild = getattr(self, "rebuild_rb", None)
        return "rebuild" if rebuild is not None and _is_checked(rebuild) else "export"

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
                 split_types=False, geometry_format=GEOMETRY_SAT):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self._pick_folder = pick_folder
        self.result = None
        self.folder = _safe_text(folder)
        self.split_types = bool(split_types)
        self.geometry_format = geometry_format
        family_count = int(family_count or 0)
        self.count_tb.Text = "{} famil{} selected.".format(
            family_count, "y" if family_count == 1 else "ies")
        self.folder_box.Text = self.folder
        self.split_types_cb.IsChecked = self.split_types
        if geometry_format == GEOMETRY_DWG:
            self.format_dwg_rb.IsChecked = True
        else:
            self.format_sat_rb.IsChecked = True

    def _read(self):
        self.folder = _text_of(self.folder_box)
        self.split_types = _is_checked(self.split_types_cb)
        self.geometry_format = GEOMETRY_DWG if _is_checked(self.format_dwg_rb) else GEOMETRY_SAT

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
