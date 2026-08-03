# -*- coding: utf-8 -*-
"""Restore every temporary phase and view setting in the active document.

Covers views tracked by the Temp Phase button plus any other view left with
Temporary View Properties enabled, matching what close recovery would do.
"""

from __future__ import print_function

from easybim import temp_phase_view


try:
    _SCRIPT_PATH = __file__
except Exception:
    _SCRIPT_PATH = ""


temp_phase_view.log_command_context(_SCRIPT_PATH)
temp_phase_view.run_restore_all_views(command_script_path=_SCRIPT_PATH)
