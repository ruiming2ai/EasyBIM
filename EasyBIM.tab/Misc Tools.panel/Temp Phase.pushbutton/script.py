# -*- coding: utf-8 -*-
"""Temp Phase command entrypoint.

The button and its session tracking are entirely Python-based.  Close
recovery is handled by the companion Python hooks, so this command does not
load a controller DLL or depend on pyRevit's C# command compiler.
"""

from __future__ import print_function

from easybim import temp_phase_view


try:
    _SCRIPT_PATH = __file__
except Exception:
    _SCRIPT_PATH = ""


temp_phase_view.log_command_context(_SCRIPT_PATH)
temp_phase_view.run_pushbutton(command_script_path=_SCRIPT_PATH)
