# -*- coding: utf-8 -*-
"""Status window for a running Clash Detection Mode session.

The panel can be docked away and the alert dismissed, so pressing the ribbon
button while the mode runs has to answer "is this on, and what is it doing?".
This is that answer, plus every control the mode has in one place.
"""
# pylint: disable=import-error,invalid-name,broad-except

from __future__ import print_function

import os
import time

from pyrevit import forms
from pyrevit import script

from easybim import clash_detection_state as state


LOGGER = script.get_logger()
TITLE = "Clash Detection Mode"
XAML_FILE = os.path.join(
    os.path.dirname(__file__), "ui", "clash_detection_status.xaml"
)


def _elapsed_text(started_at):
    if not started_at:
        return ""
    seconds = int(max(0, time.time() - started_at))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return "Running for {0}h {1}m".format(hours, minutes)
    if minutes:
        return "Running for {0}m {1}s".format(minutes, seconds)
    return "Running for {0}s".format(seconds)


def _category_names(session):
    """Both sides' category counts, since the ids alone say nothing useful."""
    return "{0} category(ies)  vs  {1} category(ies)".format(
        len(session.side_a.category_ids),
        len(session.side_b.category_ids),
    )


class ClashDetectionStatusWindow(forms.WPFWindow):
    def __init__(self, engine):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self.engine = engine
        self._refresh()

    def _refresh(self):
        session = self.engine.get_session()
        if session is None or not self.engine.is_active():
            self.StateText.Text = "Not running"
            self.ElapsedText.Text = ""
            self.ScopeText.Text = ""
            self.CategoriesText.Text = ""
            self.NotesText.Text = ""
            for control in (
                self.ShowPanelButton,
                self.PauseButton,
                self.ResumeButton,
                self.EditCategoriesButton,
                self.StopButton,
            ):
                control.IsEnabled = False
            return

        paused = self.engine.is_paused()
        self.StateText.Text = "Paused" if paused else "Running"
        self.ElapsedText.Text = (
            "Not watching for changes while paused."
            if paused
            else _elapsed_text(session.started_at)
        )
        self.ScopeText.Text = session.scope_text()
        self.CategoriesText.Text = _category_names(session)

        summary = session.summary()
        self.ActiveCountText.Text = str(summary["active"])
        self.ResolvedCountText.Text = str(summary["resolved"])
        self.QueuedCountText.Text = str(
            summary["queued"] + summary["rechecking"]
        )
        self.NotesText.Text = "\n".join(summary["notes"])

        # Exactly one of the two is available at any moment, so the window
        # never offers an action that would do nothing.
        self.PauseButton.IsEnabled = not paused
        self.ResumeButton.IsEnabled = paused
        self.ShowPanelButton.IsEnabled = True
        self.EditCategoriesButton.IsEnabled = True
        self.StopButton.IsEnabled = True

    # -- XAML handlers ----------------------------------------------------

    def show_panel_click(self, sender, args):
        del sender, args
        try:
            from easybim import clash_detection_panel

            clash_detection_panel.open_panel(self.engine.get_session())
        except Exception as ex:
            LOGGER.debug("[Clash Detection Status] Show panel failed: %s", ex)

    def pause_click(self, sender, args):
        del sender, args
        self.engine.pause()
        self._refresh()

    def resume_click(self, sender, args):
        del sender, args
        self.engine.resume()
        self._refresh()

    def edit_categories_click(self, sender, args):
        del sender, args
        self.Close()
        try:
            from easybim import clash_detection_setup

            clash_detection_setup.request_edit_categories()
        except Exception as ex:
            forms.alert("Could not edit categories:\n{0}".format(ex), title=TITLE)

    def stop_click(self, sender, args):
        del sender, args
        self.engine.stop(reason="status window")
        self.Close()

    def close_click(self, sender, args):
        del sender, args
        self.Close()


def show_status_window():
    """Open the status window for the running session."""
    from easybim import clash_detection_engine

    if not clash_detection_engine.is_active():
        return False
    try:
        ClashDetectionStatusWindow(clash_detection_engine).ShowDialog()
        return True
    except Exception as ex:
        LOGGER.warning("Clash Detection status window failed: %s", ex)
        forms.alert(
            "Clash Detection Mode is running ({0}).".format(
                state.safe_text(clash_detection_engine.get_session().status_text())
            ),
            title=TITLE,
        )
        return False
