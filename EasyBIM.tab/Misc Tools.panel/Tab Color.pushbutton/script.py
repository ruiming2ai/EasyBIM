# -*- coding: utf-8 -*-
"""Configure, import, export, and explicitly enable Tab Color."""

from __future__ import absolute_import

import copy
import os
import sys


BUNDLE_DIR = os.path.dirname(__file__)
EXTENSION_DIR = os.path.abspath(os.path.join(BUNDLE_DIR, "..", "..", ".."))
LIB_DIR = os.path.join(EXTENSION_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from pyrevit import forms

from viewtabcolors import config
from viewtabcolors import defaults
from viewtabcolors import runtime


class RuleRow(forms.Reactive):
    def __init__(self, rule):
        super(RuleRow, self).__init__()
        self.Id = rule["id"]
        self._enabled = bool(rule["enabled"])
        self._color = rule["color"]
        self.Label = rule["label"]
        self.ViewTypes = ", ".join(rule["view_types"])

    @forms.reactive
    def Enabled(self):
        return self._enabled

    @Enabled.setter
    def Enabled(self, value):
        self._enabled = bool(value)

    @forms.reactive
    def Color(self):
        return self._color

    @Color.setter
    def Color(self, value):
        self._color = value

    def write_to(self, rule):
        rule["enabled"] = bool(self.Enabled)
        rule["color"] = config.normalize_color(self.Color)


class SettingsWindow(forms.WPFWindow):
    def __init__(self):
        self.profile = config.load_profile()
        forms.WPFWindow.__init__(self, os.path.join(BUNDLE_DIR, "ui.xaml"))

        self.import_button.Click += self.import_click
        self.export_button.Click += self.export_click
        self.defaults_button.Click += self.defaults_click
        self.cancel_button.Click += self.cancel_click
        self.save_button.Click += self.save_click

        self._load_profile_into_ui(self.profile)
        self._update_status()

    def _load_profile_into_ui(self, profile):
        self.profile = copy.deepcopy(profile)
        self.rows = [RuleRow(rule) for rule in self.profile["rules"]]
        self.master_enabled.IsChecked = bool(self.profile.get("enabled", False))
        self.rules_grid.ItemsSource = self.rows
        self.rules_grid.Items.Refresh()

    def _read_ui_profile(self):
        try:
            self.rules_grid.CommitEdit()
            self.rules_grid.CommitEdit()
        except Exception:
            pass
        updated = copy.deepcopy(self.profile)
        updated["enabled"] = bool(self.master_enabled.IsChecked)
        by_id = dict((row.Id, row) for row in self.rows)
        for rule in updated["rules"]:
            by_id[rule["id"]].write_to(rule)
        return config.validate_profile(updated)

    def _update_status(self):
        status = runtime.get_status()
        state = status.get("state", "unknown")
        if state == "running":
            self.runtime_status.Text = "Running · {0} view(s) · {1} ambiguous".format(
                status.get("open_views", 0), status.get("ambiguous_captions", 0)
            )
        else:
            self.runtime_status.Text = status.get("message", state)
        self.message_text.Text = (
            "Tab Color is off until you check Enable and click Save & Apply. "
            "Settings are stored per Windows user; nothing is written into "
            "the Revit model."
        )

    def color_button_click(self, sender, event_args):
        del event_args
        row = sender.DataContext
        try:
            import clr

            clr.AddReference("System.Drawing")
            clr.AddReference("System.Windows.Forms")
            from System.Drawing import Color
            from System.Windows.Forms import ColorDialog, DialogResult

            hex_value = str(row.Color).lstrip("#")
            if len(hex_value) == 8:
                hex_value = hex_value[-6:]
            dialog = ColorDialog()
            dialog.FullOpen = True
            dialog.Color = Color.FromArgb(
                int(hex_value[0:2], 16),
                int(hex_value[2:4], 16),
                int(hex_value[4:6], 16),
            )
            if dialog.ShowDialog() == DialogResult.OK:
                row.Color = config.rgb_to_hex(
                    dialog.Color.R, dialog.Color.G, dialog.Color.B
                )
                self.rules_grid.Items.Refresh()
        except Exception as ex:
            forms.alert("Could not open the color picker:\n\n{0}".format(ex))

    def import_click(self, sender, event_args):
        del sender, event_args
        path = forms.pick_file(
            file_ext="json", title="Import Tab Color settings"
        )
        if not path:
            return
        try:
            imported = config.read_profile_file(path)
            self._load_profile_into_ui(imported)
            self.message_text.Text = (
                "Imported '{0}'. Click Save & Apply to make it active.".format(
                    os.path.basename(path)
                )
            )
        except Exception as ex:
            forms.alert(
                "The settings file was not imported:\n\n{0}".format(ex),
                title="Tab Color",
                warn_icon=True,
            )

    def export_click(self, sender, event_args):
        del sender, event_args
        try:
            current = self._read_ui_profile()
        except Exception as ex:
            forms.alert(
                "Fix the settings before exporting:\n\n{0}".format(ex),
                title="Tab Color",
                warn_icon=True,
            )
            return
        path = forms.save_file(
            file_ext="json",
            default_name="TabColor.settings.json",
            title="Export Tab Color settings",
        )
        if not path:
            return
        try:
            config.export_profile(current, path)
            self.message_text.Text = "Exported settings to '{0}'.".format(path)
        except Exception as ex:
            forms.alert(
                "The settings file was not exported:\n\n{0}".format(ex),
                title="Tab Color",
                warn_icon=True,
            )

    def defaults_click(self, sender, event_args):
        del sender, event_args
        self._load_profile_into_ui(defaults.make_default_profile())
        self.message_text.Text = (
            "Default colors are loaded. Click Save & Apply to make them active."
        )

    def cancel_click(self, sender, event_args):
        del sender, event_args
        self.Close()

    def save_click(self, sender, event_args):
        del sender, event_args
        try:
            current = self._read_ui_profile()
            self.profile = config.save_profile(current)
            status = runtime.apply(__revit__, force=True)
            if status.get("state") in ("error", "unsupported"):
                forms.alert(
                    status.get("message", "The colors could not be applied."),
                    title="Tab Color",
                    warn_icon=True,
                )
                return
            self.Close()
        except Exception as ex:
            forms.alert(
                "Settings were not saved:\n\n{0}".format(ex),
                title="Tab Color",
                warn_icon=True,
            )


SettingsWindow().ShowDialog()
