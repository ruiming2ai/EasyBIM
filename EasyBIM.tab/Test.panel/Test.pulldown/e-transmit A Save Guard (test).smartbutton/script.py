# -*- coding: utf-8 -*-
"""Private e-transmit experiment; cloned button assets, isolated runtime."""
import os

def __selfinit__(script_cmp, ui_button, application):
    # Revit startup imports smart buttons; never launch a test on import.
    if os.environ.get('EASYBIM_ET_TEST_WORKER_MODE') == '1':
        import sys
        library=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..','..','lib'))
        if library not in sys.path:sys.path.insert(0,library)
        from easybim_etransmit_tests import bootstrap
        # HOST_APP.uiapp passed by pyRevit may be None during startup. The
        # injected __revit__ can instead be UIControlledApplication.
        if application is None:
            try: application = __revit__
            except NameError: pass
        bootstrap.install(application)
    return True

if __name__ == '__main__':
    from easybim_etransmit_tests.controller import run
    run(__revit__, os.path.join(os.path.dirname(__file__), 'window.xaml'), 'A')
