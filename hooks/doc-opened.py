# -*- coding: utf-8 -*-
# Runs when a document finishes opening. CPython & IronPython safe.
# We don't rely on EXEC_PARAMS; we try to grab the doc but will still show even if we can't.

from easybim.messages import run_start_message_on_file_open

# Anchor EasyBIM's Idling delegate here, where `__revit__` is a live
# UIApplication.  startup.py also installs it, but during application init the
# handles it can reach are unreliable, and a delegate subscribed there can die
# with the startup engine while the mirror still reports it.  Ensure first, so
# the file-open trigger this very run may mark is drained by a live delegate.
try:
    from easybim import idling
    idling.ensure_installed(__revit__)
except Exception:
    pass

# Try hook event args first (most reliable in hook context), then fall back
# to active UIDocument.
doc = None

try:
    args = EXEC_PARAMS.event_args
    doc = getattr(args, "Document", None) if args else None
except Exception:
    doc = None

if doc is None:
    try:
        uidoc = __revit__.ActiveUIDocument
        doc = uidoc.Document if uidoc else None
    except Exception:
        doc = None

run_start_message_on_file_open(doc=doc)
