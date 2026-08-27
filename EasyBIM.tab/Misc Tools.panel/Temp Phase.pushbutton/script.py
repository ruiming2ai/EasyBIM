# -*- coding: utf-8 -*-
"""Set a temporary phase on the active view and restore original phases on close or on demand."""

from __future__ import print_function

from easybim import temp_phase_view


try:
    _SCRIPT_PATH = __file__
except Exception:
    _SCRIPT_PATH = ""


temp_phase_view.log_command_context(_SCRIPT_PATH)
temp_phase_view.run_pushbutton(command_script_path=_SCRIPT_PATH)
