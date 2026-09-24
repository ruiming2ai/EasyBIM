# -*- coding: utf-8 -*-
"""Enable passive Coordination Review capture while a document opens."""

try:
    from easybim import coordination_review_passive

    # Pass this hook's live UIApplication: a lib module cannot see `__revit__`,
    # and `HOST_APP.uiapp` is not dependable this early, so a bare call can
    # find no event source and attach nothing.
    coordination_review_passive.register_passive_detector(__revit__, source="doc-opening")
except Exception:
    pass

# e-transmit source capture: Document.PathName becomes empty after a detached
# open, so remember the exact path exposed by DocumentOpeningEventArgs.
try:
    from easybim_etransmit import source_tracker
    _args = EXEC_PARAMS.event_args
    source_tracker.record_opening(getattr(_args, "PathName", "") if _args else "")
except Exception:
    pass
