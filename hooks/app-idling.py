# -*- coding: utf-8 -*-
"""Process RL Tools startup queue on each Revit idling event."""

from rltools.messages import process_startup_jobs

try:
    process_startup_jobs()
except Exception:
    # Never hard-fail Revit idling because of startup automation.
    pass
