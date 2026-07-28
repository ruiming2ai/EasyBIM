# -*- coding: utf-8 -*-
"""Process EasyBIM startup queue, temp-phase runtime, and close stop."""

from easybim.messages import process_startup_jobs

try:
    from easybim import temp_phase_view
except Exception:
    temp_phase_view = None

try:
    from easybim import close_stop
except Exception:
    close_stop = None

try:
    _EVENT_ARGS = EXEC_PARAMS.event_args
except Exception:
    _EVENT_ARGS = None

try:
    process_startup_jobs()
except Exception:
    # Never hard-fail Revit idling because of startup automation.
    pass
