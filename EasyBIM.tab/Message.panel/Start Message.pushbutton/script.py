#! python3
# -*- coding: utf-8 -*-


from easybim.messages import run_start_message_workflow

# Try to grab the active document; the button should still work without it.
try:
    uidoc = __revit__.ActiveUIDocument  # provided by pyRevit
    doc = uidoc.Document if uidoc else None
except Exception:
    doc = None

# For a button, it's usually friendlier to force-show the dialog even if we
# are on a family doc or have no active doc (so the user always gets feedback).
# We also request to open the Worksets dialog after the alert.
show_start_message(
    doc=doc,
    force=True,
    open_worksets_after=True,
    run_coord_report_after=True,
)
