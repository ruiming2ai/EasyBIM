# -*- coding: utf-8 -*-
"""RL Tools extension startup automation."""


try:
    from rltools import auto_update

    if not auto_update.should_skip_startup(auto_update.get_startup_guard_state()):
        auto_update.mark_startup_attempted()
        auto_update.run_startup_auto_update()
except Exception:
    pass
