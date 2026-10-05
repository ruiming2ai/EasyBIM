# -*- coding: utf-8 -*-
"""Wake an inactive Revit session without running Revit or Git on a timer thread.

The timer ONLY signals ExternalEvent. Revit supplies the live UIApplication to
Execute on its API thread when it is safe. One runtime is mirrored per process;
reload replaces it, and ApplicationClosing stops it. Natural Idling remains a
fallback if a host refuses ExternalEvent creation during initial startup.
"""
import threading
import time

RUNTIME_ENVVAR = "EASYBIM_UPDATE_WAKEUP"
INTERVAL_MS = 10000
VERSION = 1
_LAST_INSTALL_ATTEMPT = [0.0]


def _get_runtime():
    try:
        from pyrevit import script
        return script.get_envvar(RUNTIME_ENVVAR)
    except Exception:
        return None


def _set_runtime(value):
    try:
        from pyrevit import script
        script.set_envvar(RUNTIME_ENVVAR, value)
        return True
    except Exception:
        return False


def _create_event(runtime):
    from Autodesk.Revit.UI import ExternalEvent, IExternalEventHandler

    class UpdateEventHandler(IExternalEventHandler):
        def Execute(self, uiapp):
            runtime.execute(uiapp)

        def GetName(self):
            return "EasyBIM installed-revision check"

    runtime.handler = UpdateEventHandler()
    return ExternalEvent.Create(runtime.handler)


def _create_timer(callback):
    from System import Int32
    from System.Threading import Timer, TimerCallback
    # Explicit Int32 avoids ambiguous native Timer overloads in IronPython.
    return Timer(TimerCallback(callback), None, Int32(INTERVAL_MS), Int32(INTERVAL_MS))


def _subscribe_closing(uiapp, callback):
    try:
        from System import EventHandler
        from Autodesk.Revit.UI.Events import ApplicationClosingEventArgs
        handler = EventHandler[ApplicationClosingEventArgs](callback)
        uiapp.ApplicationClosing += handler
        return uiapp, handler
    except Exception:
        return None, None


class _Wakeup(object):
    def __init__(self, callback):
        self.version = VERSION
        self.callback = callback
        self.lock = threading.RLock()
        self.stopped = False
        self.executing = False
        self.pending = False
        self.event = self.timer = self.handler = None
        self.closing_source = self.closing_handler = None
        self.raised = self.executions = 0
        self.last_error = ""

    def start(self, uiapp):
        # Called only from Revit startup/Idling/hook API context, never timer.
        self.event = _create_event(self)
        self.closing_source, self.closing_handler = _subscribe_closing(uiapp, self._closing)
        self.timer = _create_timer(self.tick)

    def tick(self, state=None):
        # Do not read Revit objects, call consumers, access Git, or display UI.
        # The lock also prevents Raise racing Dispose during a reload.
        with self.lock:
            if self.stopped or self.executing or self.pending:
                return
            self.pending = True
            try:
                request = str(self.event.Raise())
                if request not in ("Accepted", "Pending"):
                    self.pending = False
                    self.last_error = "ExternalEvent request was not accepted"
                else:
                    self.raised += 1
                    self.last_error = ""
            except Exception as error:
                self.pending = False
                self.last_error = "ExternalEvent raise: " + type(error).__name__

    def execute(self, uiapp):
        with self.lock:
            self.pending = False
            if self.stopped or self.executing:
                return
            self.executing = True
            self.executions += 1
        try:
            self.callback(uiapp)
        except (Exception, SystemExit) as error:
            # A raw CLR callback must never leak an interpreter exit into Revit.
            with self.lock:
                self.last_error = "Update callback: " + type(error).__name__
        finally:
            with self.lock:
                self.executing = False
                if self.stopped:
                    self._dispose_event()

    def _closing(self, sender, args):
        self.stop()

    def _dispose_event(self):
        if self.event is not None:
            event, self.event = self.event, None
            try:
                event.Dispose()
            except Exception:
                pass

    def stop(self):
        # UI-thread lifecycle operation. Timer.Dispose is nonblocking; waiting
        # for its worker here could deadlock against a callback holding our lock.
        with self.lock:
            if self.stopped:
                return
            self.stopped = True
            self.pending = False
            if self.timer is not None:
                try:
                    self.timer.Dispose()
                except Exception:
                    pass
                self.timer = None
            if self.closing_source is not None and self.closing_handler is not None:
                try:
                    self.closing_source.ApplicationClosing -= self.closing_handler
                except Exception:
                    pass
            self.closing_source = self.closing_handler = None
            # Reload can call stop from inside this ExternalEvent's Execute.
            # Do not dispose the currently executing event until Execute exits.
            if not self.executing:
                self._dispose_event()

    def snapshot(self):
        with self.lock:
            return {"active": not self.stopped, "pending": self.pending,
                    "raised": self.raised, "executions": self.executions,
                    "last_error": self.last_error}


def uninstall():
    old = _get_runtime()
    if old is not None:
        try:
            old.stop()
        except Exception:
            pass
    # Only this lifecycle owner clears the mirror. An old Execute finishing
    # after a reload must not clear the replacement runtime's mirror.
    _set_runtime(None)


def install(uiapp, callback):
    """Replace the previous engine's scheduler; fail safely to natural Idling."""
    uninstall()
    _LAST_INSTALL_ATTEMPT[0] = time.time()
    runtime = _Wakeup(callback)
    try:
        runtime.start(uiapp)
        if not _set_runtime(runtime):
            raise RuntimeError("could not retain scheduler across engines")
        return True
    except Exception:
        runtime.stop()
        return False


def ensure_installed(uiapp, callback):
    old = _get_runtime()
    if (old is not None and getattr(old, "version", None) == VERSION
            and not getattr(old, "stopped", True)):
        return True
    now = time.time()
    if 0 <= now - _LAST_INSTALL_ATTEMPT[0] < INTERVAL_MS / 1000.0:
        return False
    return install(uiapp, callback)


def diagnostics():
    runtime = _get_runtime()
    if runtime is None:
        return {"active": False, "last_error": "Scheduler unavailable; natural Idling is the fallback"}
    try:
        return runtime.snapshot()
    except Exception:
        return {"active": False, "last_error": "Scheduler state unavailable"}
