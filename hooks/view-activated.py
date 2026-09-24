# -*- coding: utf-8 -*-
# Fires when a view becomes active - in particular right after an opened
# document activates, which is the moment the removed Start Message alert's
# OK click used to land.  Runs the deferred file-open startup work (Active
# Workset picker + Coordination Review report) that hooks/doc-opened.py
# noted, and keeps the Idling delegate anchored from a context where
# `__revit__` is a live UIApplication.  Both calls are cheap no-ops when
# there is nothing pending, so ordinary view switching costs a few envvar
# reads.

try:
    from easybim import idling
    idling.ensure_installed(__revit__)
except Exception:
    pass

try:
    from easybim.messages import run_pending_file_open_startup
    run_pending_file_open_startup(uiapp=__revit__)
except Exception:
    pass

# Tab Color integration. Independent of the existing view-activation workflow.
try:
    from viewtabcolors import config as _tabcolor_config
    from viewtabcolors import runtime as _tabcolor_runtime

    if _tabcolor_config.load_profile().get("enabled", False):
        try:
            _tabcolor_event_args = __eventargs__
        except Exception:
            try:
                _tabcolor_event_args = EXEC_PARAMS.event_args
            except Exception:
                _tabcolor_event_args = None
        _tabcolor_runtime.apply_from_event(__revit__, _tabcolor_event_args)
except Exception as _tabcolor_ex:
    try:
        _tabcolor_config.log(
            "EasyBIM view-activated hook failed: {0}".format(_tabcolor_ex)
        )
    except Exception:
        pass
