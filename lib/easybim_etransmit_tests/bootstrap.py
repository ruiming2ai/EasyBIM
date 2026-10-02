# -*- coding: utf-8 -*-
"""Smart-button initialization: lab-only raw Idling delegate. Normal session no-op."""
from __future__ import unicode_literals
import os
_SOURCE=None
_HANDLER=None

def install(application,env=None):
    global _SOURCE,_HANDLER
    env=os.environ if env is None else env
    if env.get('EASYBIM_ET_TEST_WORKER_MODE')!='1':return False
    if _HANDLER is not None:return True
    if application is None:return False
    from System import AppDomain
    if AppDomain.CurrentDomain.GetData('EasyBIM.ETTests.IdlingOwner') is not None:return True
    from .base import worker
    worker.install_unattended_handlers(application)
    from System import EventHandler
    from Autodesk.Revit.UI.Events import IdlingEventArgs
    def on_idle(sender,args):
        worker.run_pending(sender)
    _HANDLER=EventHandler[IdlingEventArgs](on_idle)
    application.Idling += _HANDLER
    _SOURCE=application
    AppDomain.CurrentDomain.SetData('EasyBIM.ETTests.IdlingOwner',_HANDLER)
    job=worker._read_json(worker.pending_job_path())
    worker._write_json(job['status_path'],dict(phase='lab bootstrap ready'))
    return True
