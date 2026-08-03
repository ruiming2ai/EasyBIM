# -*- coding: utf-8 -*-
"""Minimal ``INotifyPropertyChanged`` support for IronPython view models.

The rest of EasyBIM refreshes WPF by reassigning ``ItemsSource`` (see
``view_template_transfer_ui``), which is fine for lists that only change on
a button press.  It is not fine for the Clash Detection category lists: the
whole point of the Extended-selection lists is to shift-select a range and
toggle it in one click, and reassigning ``ItemsSource`` throws that
selection away mid-gesture.

Implementing the interface in IronPython means providing ``add_*``/``remove_*``
methods for the event; WPF then subscribes to them like any .NET view model.
"""

from __future__ import print_function

import clr

clr.AddReference("WindowsBase")
clr.AddReference("PresentationFramework")

from System.ComponentModel import INotifyPropertyChanged
from System.ComponentModel import PropertyChangedEventArgs


class NotifyingObject(INotifyPropertyChanged):
    """Base for row objects bound with ``Mode=TwoWay``."""

    def __init__(self):
        self._property_changed_handlers = []

    # IronPython implements a .NET event with this pair of methods.
    def add_PropertyChanged(self, handler):
        self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        try:
            self._property_changed_handlers.remove(handler)
        except ValueError:
            pass

    def raise_property_changed(self, property_name):
        args = PropertyChangedEventArgs(property_name)
        for handler in list(self._property_changed_handlers):
            try:
                handler(self, args)
            except Exception:
                pass
