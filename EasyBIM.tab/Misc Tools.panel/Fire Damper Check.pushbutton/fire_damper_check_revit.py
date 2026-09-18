# -*- coding: utf-8 -*-
"""Revit API layer for Fire Damper Check - thin, because the two engines it
composes already live in lib: ``easybim.duct_network_revit`` reads the
ducts and the ticked dampers (with their location curves and connector
origins), ``easybim.link_crossings`` reads the rated barriers of the host
and every loaded link and finds where the ducts cross them.

Read-only.  No Transaction in this module and there never will be.  Only
ints, floats, booleans and unicode cross back into the state layer; the
live link contexts and the barrier index stay inside the scan closure the
launcher builds.  ``db`` is injectable for the desktop tests.
"""

from __future__ import print_function

import time

from easybim import duct_network_revit
from easybim import link_crossings


DUCT_CATEGORIES = ("OST_DuctCurves", "OST_FlexDuctCurves")


def collect_catalog(doc, db=None, clock=None, include_floors=True):
    """Everything the setup window lists: duct family types, and per link
    (the host first) the wall/floor types with their string parameters,
    the line styles in use and the levels."""
    clock = clock or time.time
    started = clock()
    families = duct_network_revit.collect_types(doc, db=db, clock=clock)
    contexts = link_crossings.build_contexts(doc, db=db)
    links = []
    for context in contexts:
        entry = context.describe()
        entry.update(link_crossings.collect_catalog(context, db=db, include_floors=include_floors))
        links.append(entry)
    return {
        "damper_types": families.get("types") or [],
        "view": families.get("view") or {},
        "links": links,
        "meta": {"seconds": clock() - started,
                 "probe_truncated": bool((families.get("meta") or {}).get("probe_truncated"))},
    }


def scan_network(doc, damper_types, scope=None, view_id=None, view_name=None, db=None, progress=None):
    """The duct network with curves and the dampers' connector origins."""
    options = {
        "with_curves": True,
        "origin_type_keys": list(damper_types or ()),
        "scope": scope or "model",
        "view_id": view_id,
        "view_name": view_name,
    }
    return duct_network_revit.scan(doc, options, db=db, progress=progress)


def duct_ids_in_view(doc, view_id, db=None):
    return duct_network_revit.element_ids_in_view(doc, view_id, DUCT_CATEGORIES, db=db)


def build_index(doc, choices, options, db=None):
    """``(contexts, index)`` - the live half; call ``describe`` for the plain half."""
    contexts = link_crossings.build_contexts(doc, db=db)
    index = link_crossings.build_index(contexts, choices, options, db=db)
    return contexts, index


def describe(contexts, index):
    description = link_crossings.describe_index(index)
    description["links"] = [context.describe() for context in contexts]
    return description


def find_crossings(index, segments, options=None, db=None, progress=None, clock=None):
    return link_crossings.find_crossings(index, segments, options=options, db=db,
                                         progress=progress, clock=clock)


def host_extent(segments):
    return link_crossings.host_extent(segments)


def show(uidoc, show_record, db=None):
    """Select the host elements (and the linked barrier where Revit lets
    us) and frame the crossing point."""
    return link_crossings.show_crossing(uidoc, show_record, db=db)
