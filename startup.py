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
    from easybim import temp_phase_close
    temp_phase_close.install_completion_handlers()
except Exception as ex:
    try:
        from easybim import temp_phase_close
        temp_phase_close.log_hook_exception("TempPhaseCompletionStartupException", ex)
    except Exception:
        pass

try:
    from easybim import auto_update

    if not auto_update.should_skip_startup(auto_update.get_startup_guard_state()):
        auto_update.mark_startup_attempted()
        auto_update.run_startup_auto_update()
except Exception:
    pass
