# -*- coding: utf-8 -*-
"""Restore the active view's temporary phase and view settings.

This is the manual counterpart to the close-time recovery: it undoes the
temporary state immediately instead of waiting for the document to close.
"""

from __future__ import print_function

from easybim import temp_phase_view


try:
    _SCRIPT_PATH = __file__
except Exception:
    _SCRIPT_PATH = ""


temp_phase_view.log_command_context(_SCRIPT_PATH)
temp_phase_view.run_restore_active_view(command_script_path=_SCRIPT_PATH)
