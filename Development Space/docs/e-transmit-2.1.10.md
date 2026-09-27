# EasyBIM e-transmit 2.1.10

## Progress UI change

2.1.10 replaces the floating pyRevit prompt-bar progress overlay with a thin Revit dockable pane.

- The pane uses Revit's dockable-pane system with an initial **Top** dock position. Revit allocates layout space for it instead of overlaying the application title, ribbon, document tabs, or model canvas.
- The pane is shown only while e-transmit runs and hidden afterward.
- Phase, current filename/detail, progress percentage and Cancel are separate controls so long filenames remain readable.
- Existing phase colors are retained: blue collecting/repathing, purple ACC finalization/final verification, green ready, amber review, red incomplete, gray cancelled.
- WPF dispatcher refreshes keep the pane responsive to progress updates and the Cancel button between chunked/observable operations.
- If a registered pane cannot be rebound after a full scripting-engine reload, e-transmit does not fall back to an overlaying prompt bar.

## Scope

This release changes only the e-transmit progress surface and its UI regressions. Collection, relative repathing, live-ACC source policy, one-open ACC finalization, hashing, delivery and verification behavior remain as in 2.1.9.

A dockable pane prevents UI overlap; it does not make Revit API work concurrent. Revit can still be occupied while document open/save/verification calls execute.

## Verification

Portable/API-shaped regression coverage includes the dockable-pane contract, top docking state, phase/detail separation, percentage updates, Cancel state, show/hide lifecycle, and survival of document-hook context changes. Real Revit acceptance is still required for pane sizing/docking under the user's Revit/pyRevit build and DPI configuration.
