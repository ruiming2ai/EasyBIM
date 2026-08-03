# -*- coding: utf-8 -*-
"""EasyBIM's single Revit Idling subscription.

Revit raises Idling continuously, and pyRevit runs a hook *script* on every
dispatch - `File.Exists`, a shebang probe, a full file read, an IronPython
compile, a fresh scope and a ``ScriptRuntime`` per event, with no compiled-code
cache anywhere in the runtime.  One .NET delegate installed at startup replaces
all of that with a direct delegate call, so this module owns that subscription
and fans it out to the consumers that used to live in ``hooks/app-idling.py``.

Two obligations come with a raw delegate that pyRevit handles for its own
hooks, and both are met here:

* pyRevit detaches its hooks across a reload but never a raw delegate.  The
  delegate is therefore mirrored into a pyRevit envvar together with its event
  source: a recycled engine loses this module's globals while Revit keeps the
  live delegate, so without the mirror every reload would stack another
  subscription.  Same approach as ``clash_detection_engine`` and
  ``coordination_review_passive``.
* A hook script had pyRevit's executor to catch anything that escaped.  A
  delegate has nothing between it and Revit's event dispatcher, so every
  consumer runs inside its own guard.

Consumers must never read ``EXEC_PARAMS``: outside a script run it returns
*stale* data (pyRevit never disposes ``ScriptRuntimeConfigs``), so the sender
handed to the delegate is passed through explicitly instead.
"""

from __future__ import print_function


try:
    from pyrevit import script
except Exception:
    script = None

try:
    from pyrevit import HOST_APP
except Exception:
    HOST_APP = None

try:
    from easybim import temp_phase_close
except Exception:
    temp_phase_close = None

try:
    from easybim import messages
except Exception:
    messages = None

try:
    from easybim import auto_update
except Exception:
    auto_update = None


#: Mirrors the live delegate and its event source across a pyRevit reload.
HANDLER_ENVVAR = "EASYBIM_IDLING_HANDLER"

_HANDLER = None
_HANDLER_UIAPP = None

#: Consumers open modal dialogs, and Revit can keep pumping Idling behind a
#: modal.  pyRevit envvars hand back live mutable dicts, so a re-entrant pass
#: would see not-yet-marked work and act on it a second time.
_IN_PASS = [False]


class _NullLogger(object):
    def debug(self, *args, **kwargs):
        return None


try:
    LOGGER = script.get_logger() if script is not None else _NullLogger()
except Exception:
    LOGGER = _NullLogger()


def _log(message):
    try:
        LOGGER.debug("[EasyBIM Idling] {0}".format(message))
    except Exception:
        pass


def _exception_text(exception):
    if exception is None:
        return ""
    try:
        return "{0}: {1}".format(exception.__class__.__name__, exception)
    except Exception:
        return "unprintable exception"


# -- envvar mirror --------------------------------------------------------


def _get_envvar(name, default=None):
    if script is None:
        return default
    try:
        value = script.get_envvar(name)
    except Exception:
        return default
    return default if value is None else value


def _set_envvar(name, value):
    if script is None:
        return
    try:
        script.set_envvar(name, value)
    except Exception:
        pass


# -- event source ---------------------------------------------------------


def _get_uiapp(uiapp=None):
    if uiapp is not None:
        return uiapp
    try:
        return HOST_APP.uiapp
    except Exception:
        return None


def _idling_source(uiapp=None):
    uiapp = _get_uiapp(uiapp)
    if uiapp is None:
        return None
    try:
        getattr(uiapp, "Idling")
        return uiapp
    except Exception:
        return None


def _make_idling_handler():
    """Typed CLR delegate, degrading to the bare callable on odd hosts."""
    try:
        from System import EventHandler

        try:
            from Autodesk.Revit.UI.Events import IdlingEventArgs
        except Exception:
            from Autodesk.Revit.UI import IdlingEventArgs
        return EventHandler[IdlingEventArgs](_on_idling)
    except Exception:
        return _on_idling


# -- consumers ------------------------------------------------------------


def _guarded(label, func, *args):
    """Run one consumer so it can break neither the others nor Revit.

    ``except Exception`` deliberately is not enough here: ``SystemExit`` is not
    an ``Exception`` subclass, and ``pyrevit.forms.alert(exitscript=True)``
    raises it.  A hook script had pyRevit's executor to absorb that; a raw
    delegate would let it reach Revit's event dispatcher.
    """
    try:
        func(*args)
    except SystemExit:
        _log("{0} requested an interpreter exit; ignored".format(label))
    except Exception as ex:
        _log("{0} failed: {1}".format(label, _exception_text(ex)))


def _run_temp_phase_close(sender):
    if temp_phase_close is None:
        return
    # handle_idling discards the event args, so none are forwarded.
    temp_phase_close.handle_app_idling(uiapp=sender, event_args=None)


def _run_startup_jobs(sender):
    if messages is None:
        return
    if messages.has_pending_startup_jobs():
        # Pass the sender explicitly; the hook used to call this with no
        # argument and lean on the ``__revit__`` builtin, which pyRevit does
        # not inject into library modules.
        messages.process_startup_jobs(sender)


def _run_auto_update(sender):
    """Run the deferred startup auto-update, detached from Idling first.

    The update can end in ``sessionmgr.reload_pyrevit()``, which disposes the
    script engine that owns this delegate.  Revit would keep the delegate
    subscribed and invoke a dead object on every later tick, so the
    subscription is dropped before the update runs and restored afterwards if
    this engine is still alive.  ``startup.py`` re-installs on the way back up
    after a real reload.  The update is one-shot per session (envvar flag plus
    a process mutex), so a tick lost to the detach costs nothing.
    """
    if auto_update is None or not auto_update.has_pending_startup_auto_update():
        return
    uninstall()
    try:
        auto_update.run_pending_startup_auto_update()
    finally:
        install(sender)


def _run_consumers(sender):
    _guarded("StartupJobs", _run_startup_jobs, sender)
    _guarded("TempPhaseCloseRecovery", _run_temp_phase_close, sender)
    _guarded("StartupAutoUpdate", _run_auto_update, sender)


def _on_idling(sender, args):
    del args
    # Cheap guard, deliberately outside the try: a re-entrant tick must cost
    # nothing beyond one flag read.
    if _IN_PASS[0]:
        return
    _IN_PASS[0] = True
    try:
        _run_consumers(sender)
    finally:
        _IN_PASS[0] = False


# -- subscription ---------------------------------------------------------


def _detach(uiapp, handler):
    if uiapp is None or handler is None:
        return
    try:
        uiapp.Idling -= handler
    except Exception:
        pass


def _detach_stale():
    """Detach a delegate left behind by a previous pyRevit engine.

    Module globals do not survive a pyRevit reload, so without the envvar
    mirror a reload would silently stack a second subscription.
    """
    stored = _get_envvar(HANDLER_ENVVAR, None)
    if not isinstance(stored, dict):
        return
    _detach(stored.get("uiapp"), stored.get("handler"))
    _set_envvar(HANDLER_ENVVAR, None)


def install(uiapp=None):
    """Subscribe the one Idling delegate.  Never fatal - returns a bool."""
    global _HANDLER, _HANDLER_UIAPP

    source = _idling_source(uiapp)
    if source is None:
        _log("Install skipped: no UIApplication exposing Idling.")
        return False

    _detach_stale()

    handler = _make_idling_handler()
    try:
        source.Idling += handler
    except Exception as ex:
        _log("Idling subscription failed: {0}".format(_exception_text(ex)))
        return False

    _HANDLER = handler
    _HANDLER_UIAPP = source
    _set_envvar(HANDLER_ENVVAR, {"handler": handler, "uiapp": source})
    _log("Idling delegate installed.")
    return True


def uninstall():
    """Detach via module globals and via the envvar mirror; both are safe."""
    global _HANDLER, _HANDLER_UIAPP

    _detach(_HANDLER_UIAPP, _HANDLER)
    _detach_stale()
    _HANDLER = None
    _HANDLER_UIAPP = None
    _log("Idling delegate uninstalled.")
    return True


def is_installed():
    """True when this session has a live Idling delegate attached."""
    if _HANDLER is not None:
        return True
    stored = _get_envvar(HANDLER_ENVVAR, None)
    return bool(isinstance(stored, dict) and stored.get("handler") is not None)
