# -*- coding: utf-8 -*-
"""Run the frozen EasyBIM E-Transmit 2.1.8 runtime only when this button is clicked."""
import os
import sys
import traceback
from pyrevit import forms, script

# This is a normal .pushbutton, not a smartbutton or hook. Nothing here runs at
# Revit/pyRevit startup; execution begins only after the user clicks this button.
for name in list(sys.modules):
    if name == 'easybim_etransmit_218' or name.startswith('easybim_etransmit_218.'):
        del sys.modules[name]

try:
    from easybim_etransmit_218.ui import run
    run(__revit__, os.path.join(os.path.dirname(__file__), 'window.xaml'))
except Exception as exc:
    script.get_logger().error(traceback.format_exc())
    forms.alert(
        'E-Transmit 2.1.8 test stopped: {0}\n\n'
        'This button runs only the isolated historical 2.1.8 test runtime. '
        'Production E-Transmit is separate.'.format(exc),
        title='EasyBIM E-Transmit 2.1.8 (test)')
