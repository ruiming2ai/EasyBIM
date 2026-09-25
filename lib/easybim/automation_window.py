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
            self.run_workset_btn.Click += self._run_workset
            self.run_coordination_btn.Click += self._run_coordination
            self._running = False

        def _run_workset(self, sender, args):
            self._run("workset")

        def _run_coordination(self, sender, args):
            self._run("coordination_review")

        def _run(self, tool):
            if self._running:
                return
            from easybim.messages import run_automation
            self._running = True
            self.run_workset_btn.IsEnabled = False
            self.run_coordination_btn.IsEnabled = False
            self.save_btn.IsEnabled = False
            self.status_tb.Text = ""
            try:
                run_automation(tool, uiapp=uiapp)
            except ValueError as ex:
                self.status_tb.Text = str(ex)
            except Exception as ex:
                script.get_logger().warning("Manual automation failed: %s", ex)
                self.status_tb.Text = "Could not run this tool: {0}".format(ex)
            finally:
                self._running = False
                self.run_workset_btn.IsEnabled = True
                self.run_coordination_btn.IsEnabled = True
                self.save_btn.IsEnabled = True

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
