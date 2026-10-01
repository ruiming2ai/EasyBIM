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

# Tab Color integration. The hook only marks the topology dirty. The existing
# raw Idling delegate performs one coalesced scan after Revit finishes the
# activation/UI work. Switches between already-open tabs are skipped by the
# Tab Color signature check.
try:
    from viewtabcolors import config as _tabcolor_config
    from easybim import idling as _tabcolor_idling

    if _tabcolor_config.load_profile().get("enabled", False):
        _tabcolor_idling.request_tab_color_refresh(
            reason="view-activated",
            force=False,
            verify=False,
        )
except Exception as _tabcolor_ex:
    try:
        _tabcolor_config.log(
            "EasyBIM view-activated queue failed: {0}".format(_tabcolor_ex)
        )
    except Exception:
        pass
