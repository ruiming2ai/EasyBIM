# -*- coding: utf-8 -*-
"""Transmit saved models and dependencies without manually opening each model."""
import os
import sys
import traceback
from pyrevit import forms, script

# Refresh only this tool's modules after EasyBIM updates; other tools are untouched.
for name in list(sys.modules):
    if name == 'easybim_etransmit' or name.startswith('easybim_etransmit.'):
        del sys.modules[name]
try:
    from easybim_etransmit.ui import run
    run(__revit__, os.path.join(os.path.dirname(__file__), 'window.xaml'))
except Exception as exc:
    script.get_logger().error(traceback.format_exc())
    forms.alert('e-transmit stopped: {0}\n\nReview the pyRevit output and any package reports. '
                'Original source models were not saved or synchronized.'.format(exc), title='EasyBIM e-transmit')
