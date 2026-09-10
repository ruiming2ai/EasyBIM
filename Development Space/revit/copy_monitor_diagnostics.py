# -*- coding: utf-8 -*-
"""Read-only pyRevit console diagnostic. Select candidate instances, then execute."""
from pyrevit import revit, DB
doc = revit.doc
for eid in revit.uidoc.Selection.GetElementIds():
    element = doc.GetElement(eid)
    if not isinstance(element, DB.FamilyInstance):
        print("{}: not a family instance".format(eid))
        continue
    family = element.Symbol.Family
    print({
        "revit": doc.Application.VersionNumber,
        "instance": element.UniqueId,
        "family": family.Name,
        "placement": str(family.FamilyPlacementType),
        "host": str(getattr(element.Host, "UniqueId", None)),
        "host_face": str(element.HostFace),
        "transform": str(element.GetTransform()),
        "location": str(element.Location),
        "pinned": element.Pinned,
    })
