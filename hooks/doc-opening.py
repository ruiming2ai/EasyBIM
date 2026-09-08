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
