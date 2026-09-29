# -*- coding: utf-8 -*-
"""Persistent e-transmit dockable progress pane.

This module intentionally lives outside easybim_etransmit because the
e-transmit command hot-reloads that package on every launch. Revit owns a
DockablePane for the lifetime of the application session, so its provider/type
must remain stable for that same lifetime.
"""
from __future__ import unicode_literals
import os

from pyrevit import forms


PANEL_ID = '8e4dc5df-aedf-45e7-8ef4-68d4a4c4e210'
PANEL_TITLE = 'EasyBIM e-transmit'
PANEL_XAML = os.path.join(os.path.dirname(__file__), 'ui', 'etransmit_progress_panel.xaml')
PHASE_COLORS = dict(
    collecting='#2F80ED',
    repathing='#2F80ED',
    verifying='#8E44AD',
    ready='#27AE60',
    review='#F2C94C',
    incomplete='#EB5757',
    cancelled='#828282',
)

_PANEL_INSTANCE = None
_REGISTERED = False


def _log(message):
    """Progress UI is optional; never open pyRevit output for its failures."""
    del message


def _text(value):
    try:
        return unicode(value)
    except NameError:
        return str(value)


def progress_phase(label):
    value = _text(label or '')
    upper = value.upper()
    if upper.startswith('READY — REVIEW ISSUES') or upper.startswith('READY - REVIEW ISSUES'):
        return 'READY — REVIEW ISSUES', PHASE_COLORS['review'], value
    if upper.startswith('READY'):
        return 'READY', PHASE_COLORS['ready'], value
    if upper.startswith('INCOMPLETE') or upper.startswith('FAILED'):
        return 'INCOMPLETE', PHASE_COLORS['incomplete'], value
    if upper.startswith('CANCELLED') or upper.startswith('CANCELED'):
        return 'CANCELLED', PHASE_COLORS['cancelled'], value
    if 'FINALIZING ACC LINKS' in upper:
        return 'FINALIZING ACC LINKS', PHASE_COLORS['verifying'], value
    if 'VERIFYING FINAL PACKAGE' in upper:
        return 'VERIFYING FINAL PACKAGE', PHASE_COLORS['verifying'], value
    if 'REPATH' in upper or 'CLEANUP' in upper:
        return 'REPATHING', PHASE_COLORS['repathing'], value
    return 'COLLECTING', PHASE_COLORS['collecting'], value


def _phase_detail(label, phase):
    value = _text(label or '')
    if '|' in value:
        return value.split('|', 1)[1].strip()
    upper = value.upper()
    for prefix in ('PACKAGE BUILT — ', 'PACKAGE BUILT - '):
        if upper.startswith(prefix):
            value = value[len(prefix):].strip()
            break
    if phase in ('READY', 'READY — REVIEW ISSUES', 'INCOMPLETE', 'CANCELLED'):
        return ''
    if upper.startswith(phase):
        value = value[len(phase):].lstrip(' :-—|')
    return value


def _set_brush(control, color):
    try:
        from System.Windows.Media import BrushConverter
        control.Foreground = BrushConverter().ConvertFromString(color)
    except Exception:
        pass


def _pump_dispatcher(control):
    try:
        from System import Action
        from System.Windows.Threading import DispatcherPriority
        control.Dispatcher.Invoke(DispatcherPriority.Background, Action(lambda: None))
    except Exception:
        pass


def _top_dock_state():
    import Autodesk.Revit.UI as RUI
    state = RUI.DockablePaneState()
    state.DockPosition = RUI.DockPosition.Top
    try:
        state.MinimumHeight = 40
    except Exception:
        pass
    try:
        state.MinimumWidth = 320
    except Exception:
        pass
    return state


class TransferProgressPanel(forms.WPFPanel):
    panel_id = PANEL_ID
    panel_title = PANEL_TITLE
    panel_source = PANEL_XAML
    initial_state = None

    def __init__(self):
        global _PANEL_INSTANCE
        forms.WPFPanel.__init__(self)
        _PANEL_INSTANCE = self
        self.reset()

    def reset(self):
        self.cancelled = False
        try:
            self.CancelButton.IsEnabled = True
            self.CancelButton.Content = 'Cancel'
            self.PhaseText.Text = 'COLLECTING'
            self.DetailText.Text = 'Preparing e-transmit'
            self.Progress.IsIndeterminate = True
            self.Progress.Minimum = 0.0
            self.Progress.Maximum = 1.0
            self.Progress.Value = 0.0
            self.PercentText.Text = ''
            _set_brush(self.Progress, PHASE_COLORS['collecting'])
        except Exception:
            pass
        _pump_dispatcher(self)

    def cancel_click(self, sender, args):
        del sender, args
        self.cancelled = True
        try:
            self.CancelButton.IsEnabled = False
            self.CancelButton.Content = 'Cancelling…'
        except Exception:
            pass

    def set_phase(self, label):
        phase, color, _value = progress_phase(label)
        detail = _phase_detail(label, phase)
        try:
            self.PhaseText.Text = phase
            self.DetailText.Text = detail
            _set_brush(self.Progress, color)
        except Exception:
            pass
        _pump_dispatcher(self)
        return phase

    def update_progress(self, current, total):
        try:
            total = max(1.0, float(total))
            current = max(0.0, min(float(current), total))
            self.Progress.IsIndeterminate = False
            self.Progress.Minimum = 0.0
            self.Progress.Maximum = total
            self.Progress.Value = current
            self.PercentText.Text = '{0}%'.format(int(round((current / total) * 100.0)))
        except Exception:
            pass
        _pump_dispatcher(self)


class TransferProgressBar(forms.ProgressBar):
    """Fallback top overlay used when the persistent Revit pane is unavailable."""
    def update_window(self):
        if getattr(self, '_etransmit_positioned', False):
            return
        forms.ProgressBar.update_window(self)
        try:
            self.Top = getattr(self, 'Top', 0) + float(getattr(self, 'user_height', 32) or 32)
        except Exception:
            pass
        try:
            self.Opacity = 0.92
        except Exception:
            pass
        self._etransmit_positioned = True

    def set_phase(self, label):
        phase, color, value = progress_phase(label)
        detail = _phase_detail(value, phase)
        self.title = phase if not detail else phase + ' | ' + detail
        try:
            _set_brush(self.pbar, color)
        except Exception:
            pass
        return phase


def _is_registered():
    if _REGISTERED:
        return True
    try:
        return bool(forms.is_registered_dockable_panel(TransferProgressPanel))
    except Exception:
        return False


def register():
    """Register once during EasyBIM/Revit startup; never from the command."""
    global _REGISTERED, _PANEL_INSTANCE
    if _is_registered():
        _REGISTERED = True
        return True
    register_fn = getattr(forms, 'register_dockable_panel', None)
    if register_fn is None:
        _log('e-transmit progress pane registration API is unavailable.')
        return False
    try:
        TransferProgressPanel.initial_state = _top_dock_state()
        panel = register_fn(TransferProgressPanel, default_visible=False)
        if panel is not None:
            _PANEL_INSTANCE = panel
        _REGISTERED = True
        return True
    except Exception as exc:
        _log('e-transmit progress pane registration failed: {0}'.format(exc))
        return False


def open_panel():
    """Open the already-registered pane. Never attempts registration here."""
    if not _is_registered():
        return None
    panel = _PANEL_INSTANCE
    if panel is None:
        return None
    try:
        panel.reset()
        forms.open_dockable_panel(PANEL_ID)
        return panel
    except Exception as exc:
        _log('PROGRESS_UI_UNAVAILABLE: {0}'.format(exc))
        return None


def close_panel():
    if not _is_registered():
        return
    try:
        forms.close_dockable_panel(PANEL_ID)
    except Exception:
        pass


class ProgressController(object):
    """Fail-safe command-facing progress API with a silent overlay fallback."""
    def __init__(self):
        self.panel = None
        self.overlay = None
        self._overlay_attempted = False

    def _open_overlay(self):
        if self.overlay is not None:
            return self.overlay
        if self._overlay_attempted:
            return None
        self._overlay_attempted = True
        candidate = None
        try:
            candidate = TransferProgressBar(title='e-transmit', cancellable=True, indeterminate=True)
            entered = candidate.__enter__()
            self.overlay = entered if entered is not None else candidate
            return self.overlay
        except Exception:
            try:
                if candidate is not None:
                    candidate.__exit__(None, None, None)
            except Exception:
                pass
            self.overlay = None
            return None

    def _close_overlay(self):
        overlay = self.overlay
        self.overlay = None
        if overlay is None:
            return
        try:
            overlay.__exit__(None, None, None)
        except Exception:
            pass

    def _drop_panel(self):
        if self.panel is not None:
            try:
                close_panel()
            except Exception:
                pass
        self.panel = None

    def __enter__(self):
        self.panel = open_panel()
        if self.panel is None:
            self._open_overlay()
        return self

    def __exit__(self, *args):
        if self.panel is not None:
            close_panel()
            self.panel = None
        self._close_overlay()

    @property
    def available(self):
        return self.panel is not None or self.overlay is not None

    @property
    def mode(self):
        if self.panel is not None:
            return 'DOCKABLE_PANE'
        if self.overlay is not None:
            return 'TOP_OVERLAY'
        return 'NONE'

    @property
    def cancelled(self):
        try:
            if self.panel is not None:
                return bool(self.panel.cancelled)
            if self.overlay is not None:
                return bool(self.overlay.cancelled)
            return False
        except Exception:
            return False

    def set_phase(self, label):
        if self.panel is not None:
            try:
                return self.panel.set_phase(label)
            except Exception:
                self._drop_panel()
                self._open_overlay()
        if self.overlay is not None:
            try:
                return self.overlay.set_phase(label)
            except Exception:
                self._close_overlay()
        return progress_phase(label)[0]

    def update_progress(self, current, total):
        if self.panel is not None:
            try:
                self.panel.update_progress(current, total)
                return
            except Exception:
                self._drop_panel()
                self._open_overlay()
        if self.overlay is not None:
            try:
                self.overlay.update_progress(current, total)
            except Exception:
                self._close_overlay()
