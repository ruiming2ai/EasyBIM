# -*- coding: utf-8 -*-
"""Process startup work and deferred Temp Phase close recovery."""

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None

try:
    from easybim import auto_update
except Exception:
    auto_update = None


try:
    # The startup auto-update is deferred here so its git/network work runs
    # after Revit is interactive instead of blocking the startup thread.
    if auto_update is not None and auto_update.has_pending_startup_auto_update():
        auto_update.run_pending_startup_auto_update()
except Exception:
    pass

# Temp Phase close recovery moved to the one-time .NET Idling delegate in
# easybim.idling, installed from startup.py.  A hook script is re-read and
# recompiled by pyRevit on every Idling event, which Revit raises
# continuously; the remaining consumers here are being moved the same way.
