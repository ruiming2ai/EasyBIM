# -*- coding: utf-8 -*-
# Runs when a document finishes opening. CPython & IronPython safe.
# We don't rely on EXEC_PARAMS; we try to grab the doc but will still show even if we can't.

from easybim.messages import run_start_message_on_file_open

# Try hook event args first (most reliable in hook context), then fall back
# to active UIDocument.
doc = None

# Try to get the current document.  In Rocket Mode, EXEC_PARAMS is unreliable,
# so we grab the active UIDocument from __revit__.  If this fails, we'll
# still show the message using force=True.
try:
    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document if uidoc else None
except Exception:
    doc = None

# Show the start message once per document open.  If we couldn't acquire a
# document (doc is None), force the message so users still see it.  Pass
# open_worksets_after=True to open the Worksets dialog after the alert.
show_start_message(doc=doc, force=(doc is None), open_worksets_after=True)
