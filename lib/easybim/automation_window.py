# -*- coding: utf-8 -*-
"""Automation preference window; no document or model transaction required."""
import os

from easybim import automation_settings


def show_automation_window(uiapp=None):
    from pyrevit import forms, script

    class AutomationWindow(forms.WPFWindow):
        def __init__(self):
            forms.WPFWindow.__init__(self, os.path.join(
                os.path.dirname(__file__), "ui", "automation.xaml"))
            settings, error = automation_settings.load_settings()
            self.workset_cb.IsChecked = settings["workset_enabled"]
            self.coordination_cb.IsChecked = settings["coordination_review_enabled"]
            self.status_tb.Text = error
            self.save_btn.Click += self._save

        def _save(self, sender, args):
            settings = {
                "workset_enabled": bool(self.workset_cb.IsChecked),
                "coordination_review_enabled": bool(self.coordination_cb.IsChecked),
            }
            ok, error = automation_settings.save_settings(settings)
            if not ok:
                self.status_tb.Text = error
                return
            try:
                automation_settings.apply_listener_settings(settings, uiapp)
            except Exception as ex:
                script.get_logger().warning("Automation listener update failed: %s", ex)
                self.status_tb.Text = "Settings saved. Restart Revit to finish updating the warning listener."
                return
            self.Close()

    AutomationWindow().ShowDialog()
