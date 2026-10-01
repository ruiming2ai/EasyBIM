# EasyBIM e-transmit 2.1.24 — copy first and preserve final repaths

The simple repath workflow now copies the identified saved host/cache RVT and
dependencies, then updates supported file-based CAD and Revit links through
closed-file `TransmissionData`. It does not open the host in a worker, SaveAs it,
or create a new package central. Exact saved/cache acquisition rules remain in
place; this changes processing after collection, not which edition is copied.

## Why 2.1.23 could lose a repath

The worker's `finish_independent` step wrote desired relative references with
`IsTransmitted = false`. The parent's later mark-transmitted step rebuilt desired
references from older last-saved data, overwriting those paths. In non-workshared
models, leaving the flag false also meant Revit ignored the desired references.

The final desired-reference write now activates transmission data. Parent
finalization overlays the final packaged targets and requested load intent before
writing, preserves failed/skipped row results, and accepts
`API_LOCAL_LINK_RELATIVE` for successful ACC-to-local conversion.

[Autodesk's TransmissionData documentation](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/d78d1e9c-1cee-1336-88d5-b605dacd077d.htm)
requires `IsTransmitted = true` for desired paths/load states to apply. This flag
can cause a workshared copy to open detached from its original central. The
simple repath workflow still avoids creating a new central during export.

## When document processing is still needed

Linked PDFs/images and true ACC External Resource links still require Revit API
repair in the disposable worker. Cleanup and upgrade remain opt-in and retain
their document-processing workflow. Worker repairs are followed by the same final
packaged-target overlay so transmission marking does not discard their results.

## Validation limit

Closed-file readback reports `TRANSMISSION_DATA_CHECKED`: it checks saved desired
path/load metadata, not whether Revit will load each reference. Desktop acceptance
has not been performed. Relocate and open the package in Revit, then inspect
Manage Links, load states, and link placement before treating it as portable.
