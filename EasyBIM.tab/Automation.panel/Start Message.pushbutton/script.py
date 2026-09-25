# -*- coding: utf-8 -*-
"""Configure automatic Workset and Coordination Review tools."""
from easybim.automation_window import show_automation_window

try:
    uiapp = __revit__
except NameError:
    uiapp = None

show_automation_window(uiapp=uiapp)
