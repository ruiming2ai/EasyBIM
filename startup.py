# -*- coding: utf-8 -*-
"""EasyBIM extension startup automation."""


try:
    from easybim import coordination_review_passive
    coordination_review_passive.register_passive_detector()
except Exception:
    pass


try:
    from easybim import modify_ribbon
    modify_ribbon.register_modify_shortcuts()
except Exception:
    pass


try:
    from easybim import view_template_ribbon
    view_template_ribbon.apply_native_view_template_icon()
except Exception:
    pass

try:
    from easybim import clash_detection_panel

    # Revit only accepts RegisterDockablePane during application init, so the
    # pane must be registered every session even when the mode is never used.
    # Registration alone costs nothing: the pane stays hidden and holds no
    # state until Clash Detection Mode opens it.
    clash_detection_panel.register()
except Exception:
    pass


try:
    from easybim import temp_phase_close
    temp_phase_close.install_completion_handlers()
except Exception as ex:
    try:
        from easybim import temp_phase_close
        temp_phase_close.log_hook_exception("TempPhaseCompletionStartupException", ex)
    except Exception:
        pass


try:
    from easybim import idling

    # One .NET Idling delegate for all of EasyBIM's deferred work.  A pyRevit
    # hook script would instead be read and recompiled on every Idling event,
    # which Revit raises continuously for the whole session.
    idling.install()
except Exception as ex:
    try:
        from easybim import temp_phase_close
        temp_phase_close.log_hook_exception("IdlingInstallStartupException", ex)
    except Exception:
        pass

try:
    from easybim import my_ribbon

    # Deferred: the user's My Ribbon placements share live buttons of other
    # extensions, and those extensions may load after EasyBIM.  The first
    # Idling tick is the first moment every tab is on the ribbon.
    my_ribbon.queue_startup_apply()
except Exception:
    pass

try:
    from easybim import auto_update

    # Deferred: the first Idling tick runs the guarded update, so git and
    # network work never block the Revit startup thread.
    auto_update.queue_startup_auto_update()
except Exception:
    pass
