# -*- coding: utf-8 -*-
# Runs when a document finishes opening. CPython & IronPython safe.
# We don't rely on EXEC_PARAMS; we try to grab the doc but will still show even if we can't.

from rltools.messages import run_start_message_workflow

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

def _is_project_doc(candidate):
    if candidate is None:
        return False
    try:
        if hasattr(candidate, "IsValidObject") and not candidate.IsValidObject:
            return False
    except Exception:
        return False
    if getattr(candidate, "IsFamilyDocument", False):
        return False
    if hasattr(candidate, "IsLinked") and candidate.IsLinked:
        return False
    return True


if _is_project_doc(doc):
    run_start_message_workflow(doc=doc, force=False)
