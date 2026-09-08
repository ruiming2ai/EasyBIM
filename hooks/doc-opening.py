# -*- coding: utf-8 -*-
"""Enable passive Coordination Review capture while a document opens."""

try:
    from easybim import coordination_review_passive
    coordination_review_passive.register_passive_detector(source="doc-opening")
except Exception:
    pass
