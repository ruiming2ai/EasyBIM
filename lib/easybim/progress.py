# -*- coding: utf-8 -*-
"""A progress bar that a command is allowed to run without.

pyRevit builds ``forms.ProgressBar`` by asking the host for its main window
rectangle, and that lookup is unguarded all the way down::

    forms.ProgressBar(...)  ->  TemplatePromptBar
        ->  revit.ui.get_window_rectangle()
            ->  get_mainwindow_hwnd()  ->  HOST_APP.proc_window
                ->  <uiapp>.MainWindowHandle

When any link in that chain is missing, a bare ``forms.ProgressBar(...)``
takes the whole command down before it has done anything - and the message
it dies with names a window handle, which tells the user nothing about the
work they asked for.

So the bar is treated as a convenience, not a dependency. ``ProgressSession``
opens one when it can and hands back a silent stand-in when it cannot, with a
note saying so. A run nobody can watch is worth far more than a run that will
not start.

The one thing genuinely lost in the fallback is Cancel, and callers are told
so in the note rather than left to wonder why the button is missing.

Import from pushbutton scripts as ``from easybim.progress import ...``; the
extension ``lib`` folder is always on pyRevit's ``sys.path``.
"""

# pylint: disable=import-error,broad-except

NO_BAR_NOTE = (
    "This Revit would not open a progress bar, so the run went ahead without one: "
    "there was nothing to watch and no Cancel button. The work itself was unaffected."
)


def _open_bar(title, cancellable):
    """``(bar, note)`` - a pyRevit progress bar, or ``(None, reason)``."""
    try:
        from pyrevit import forms

        return forms.ProgressBar(title=title, cancellable=cancellable), ""
    except Exception:
        return None, NO_BAR_NOTE


class ProgressSession(object):
    """A progress bar whose absence is survivable.

    Use it as a context manager. ``update()`` and ``cancelled`` are safe to
    call whether or not a bar exists, and stay safe if a bar that opened
    starts throwing part-way through a long run.

    ``note`` is empty when a real bar is on screen, and otherwise carries one
    sentence for the run's summary.
    """

    def __init__(self, title, cancellable=True, open_bar=None):
        self.title = title
        self.cancellable = bool(cancellable)
        self.note = ""
        self._bar = None
        self._open_bar = open_bar or _open_bar

    def __enter__(self):
        self._bar, self.note = self._open_bar(self.title, self.cancellable)
        if self._bar is not None:
            try:
                self._bar.__enter__()
            except Exception:
                # It constructed but would not start; carry on without it
                # rather than lose the run to its scaffolding.
                self._bar = None
                self.note = NO_BAR_NOTE
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        bar, self._bar = self._bar, None
        if bar is not None:
            try:
                bar.__exit__(exc_type, exc_value, exc_tb)
            except Exception:
                pass
        return False

    def update(self, done, total):
        """Move the bar along; a bar that refuses is dropped, not raised."""
        if self._bar is None:
            return
        try:
            self._bar.update_progress(int(done), max(int(total), 1))
        except Exception:
            self._bar = None
            self.note = NO_BAR_NOTE

    @property
    def cancelled(self):
        """True only when the user really pressed Cancel.

        With no bar there is no Cancel, so this is False - the batch runs to
        the end. Anything watching a long run for a way out needs its own
        limit as well; it cannot rely on this alone.
        """
        if self._bar is None:
            return False
        try:
            return bool(self._bar.cancelled)
        except Exception:
            self._bar = None
            self.note = NO_BAR_NOTE
            return False
