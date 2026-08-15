# -*- coding: utf-8 -*-
"""One ExternalEvent + FIFO queue so a modeless window can run Revit API work.

Revit refuses transactions, selection changes and (officially) any API call
made from a modeless window's own event handlers - the command that opened
the window has long since returned.  The window queues a callable and Raises
one ``ExternalEvent``; Revit later calls ``Execute(uiapp)`` on its main thread
- the same thread every pyRevit WPF window lives on - inside a valid API
context.  Both the work callable and its ``on_done`` run inside ``Execute``,
so no Dispatcher marshalling is needed and either may show modal dialogs.

``create()`` must be called while still in a valid API context (the command
run that opens the window).  This is the repo's first ExternalEvent; the same
rules as ``idling.py`` apply inside ``Execute``: guard ``SystemExit`` as well
as ``Exception``, and never read ``EXEC_PARAMS``.

Revit/.NET imports are guarded and the event factory is injectable so the
desktop test suite can drive the queue with fakes.
"""

from __future__ import print_function

import collections

try:
    from Autodesk.Revit.UI import ExternalEvent
    from Autodesk.Revit.UI import IExternalEventHandler
except Exception:  # off-Revit (unit tests)
    ExternalEvent = None
    IExternalEventHandler = None


_HandlerBase = IExternalEventHandler \
    if IExternalEventHandler is not None else object

_Request = collections.namedtuple("_Request", "func on_done label")

#: ``ExternalEvent.Raise`` results that mean "queued"; anything else
#: (Denied, TimedOut) means Revit did not take the request.
OK_RAISE_RESULTS = ("Accepted", "Pending")


class _QueueHandler(_HandlerBase):
    """IExternalEventHandler that drains the bridge queue.

    Revit requires BOTH members; ``GetName`` shows up in journal files.
    """

    def __init__(self, bridge):
        self._bridge = bridge

    def Execute(self, uiapp):
        self._bridge._execute(uiapp)

    def GetName(self):
        return self._bridge.name


def _default_create_event(handler):
    if ExternalEvent is None:
        raise RuntimeError("Autodesk.Revit.UI.ExternalEvent is unavailable")
    return ExternalEvent.Create(handler)


class ExternalEventBridge(object):
    """Queue Revit work from a modeless window and run it in API context.

    ``on_error(label, exception)`` is called for failures in ``create``,
    ``run`` and inside ``Execute``; ``on_idle()`` after every drain (the
    window uses it to leave its busy state).
    """

    def __init__(self, name, create_event=None, on_error=None,
                 on_idle=None):
        self.name = name
        self.on_error = on_error
        self.on_idle = on_idle
        self._create_event = create_event or _default_create_event
        self._queue = collections.deque()
        self._event = None
        self._handler = None
        self._running = False
        self._disposed = False

    # ---------------------------------------------------------- lifecycle

    def create(self):
        """Register the ExternalEvent. Valid API context only. -> bool."""
        if self._event is not None:
            return True
        try:
            handler = _QueueHandler(self)
            self._event = self._create_event(handler)
            self._handler = handler
        except Exception as ex:
            self._handler = None
            self._event = None
            self._report("create", ex)
        return self._event is not None

    def is_ready(self):
        return self._event is not None and not self._disposed

    def is_busy(self):
        return self._running or bool(self._queue)

    def pending(self):
        return len(self._queue)

    def dispose(self):
        self._disposed = True
        self._queue.clear()
        event, self._event = self._event, None
        if event is not None:
            try:
                event.Dispose()
            except Exception:
                pass

    # ------------------------------------------------------------- queue

    def run(self, func, on_done=None, label=""):
        """Queue ``func(uiapp)`` (then ``on_done(result)``) and Raise.

        Returns False - and leaves nothing queued - when Revit did not
        accept the request (Denied / TimedOut) or the bridge is gone.
        """
        if not self.is_ready():
            return False
        request = _Request(func, on_done, label)
        self._queue.append(request)
        try:
            result = self._event.Raise()
            text = u"{0}".format(result)  # the enum prints as its name
        except Exception as ex:
            self._forget(request)
            self._report(label, ex)
            return False
        if text not in OK_RAISE_RESULTS:
            self._forget(request)
            return False
        return True

    # --------------------------------------------------------- internals

    def _forget(self, request):
        try:
            self._queue.remove(request)
        except ValueError:
            pass

    def _report(self, label, exception):
        if self.on_error is None:
            return
        try:
            self.on_error(label, exception)
        except Exception:
            pass

    def _execute(self, uiapp):
        if self._running or self._disposed:
            return
        self._running = True
        try:
            # Requests queued by an on_done callback drain in this pass too.
            while self._queue:
                request = self._queue.popleft()
                try:
                    result = request.func(uiapp)
                except SystemExit:  # forms.alert(exitscript=True)
                    continue
                except Exception as ex:
                    self._report(request.label, ex)
                    continue
                if request.on_done is None:
                    continue
                try:
                    request.on_done(result)
                except SystemExit:
                    continue
                except Exception as ex:
                    self._report(request.label + " (finish)", ex)
        finally:
            self._running = False
            if self.on_idle is not None:
                try:
                    self.on_idle()
                except Exception:
                    pass
