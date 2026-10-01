# EasyBIM e-transmit 2.1.23 — independent-central-first repair and unattended Revit

## Why the PDFs could show a correct-looking path but still be Not Found

Real Revit testing showed linked PDFs saved as paths such as:

`Links\PDF\HERTZ MECHANICAL PIPING PLAN - GROUND LEVEL.pdf`

while Manage Links still reported **Not Found**.

For Revit image/PDF links, Autodesk's `ImageTypeOptions` relative-path behavior
is tied to the saved project location; for workshared projects, relative image
paths are resolved from the central model location. Therefore merely writing a
relative string into a detached/transmitted workshared copy is not sufficient.

## Independent package identity first

The live-host repair worker now follows the same fundamental lifecycle that the
early e-transmit implementation used successfully:

1. Open only the EasyBIM-owned stage copy, detached from its source central.
2. **Before repathing references**, SaveAs the repair document to the final
   package RVT path.
3. If the model is workshared, SaveAs uses
   `WorksharingSaveAsOptions.SaveAsCentral = true`. This makes the package RVT
   an independent package central/project identity while it is being repaired.
4. Repath Revit links, linked DWGs, PDFs and linked images against dependency
   files already copied into that package.
5. Save and close the package RVT.
6. Normalize supported closed-file TransmissionData references while keeping
   `TransmissionData.IsTransmitted = false`.
7. Only after all repair work has completed does the parent process mark the
   closed package RVT transmitted.

So the repair phase is intentionally **non-transmitted first → repair/save →
transmitted last**.

## PDF and image repair

For linked PDFs/images, EasyBIM no longer hands Revit a manually precomputed
relative filename as the file it should open.

Instead:

- `ImageTypeOptions` receives the **absolute packaged PDF/image file path**;
- `useRelativePath=True` tells Revit to persist the link relatively;
- page number and resolution are preserved;
- `ImageTypeOptions.IsValid(document)` is checked when available;
- `ImageType.ReloadFrom(options)` reloads from the actual packaged file;
- the transaction must commit before the row is reported as repathed.

This lets Revit itself compute the saved relative path from the already-created
package central/project location.

Imported PDFs/images are embedded content and are not repathed; only linked
ImageTypes use this workflow.

## Unattended worker dialogs and warnings

The disposable repair Revit is now explicitly non-interactive.

At startup it subscribes to:

- `DialogBoxShowing` — known/common task dialogs, message boxes and ordinary
  Revit dialogs are dismissed using a deterministic worker-only result policy;
- `FailuresProcessing` — warnings are deleted automatically; unresolved
  transaction errors are silently rolled back instead of opening a modal
  failure dialog.

Every suppressed dialog/failure is returned to the parent and summarized in
the package report.

The policy is deliberately scoped to the disposable worker process. The user's
normal Revit session is not given global dialog suppression.

## Why a separate process still exists

The independent-central-first idea is implemented inside the worker rather than
by SaveAs-ing the user's currently open model.

A Revit `Document.SaveAs` changes the working document's location/identity.
Using the user's open detached/local/ACC document directly would therefore
relocate or modify their working session. The separate process keeps that source
untouched while still reproducing the successful early e-transmit sequence on
the task-owned package copy.

## Final state

After worker Save/Close succeeds, the parent performs the existing closed-file
mark-transmitted operation. There is no reopen-to-compare cycle for a
worker-repaired host.

The report records:

- `independent_package_central = true`
- `worker_repaired = true`
- `model_verification = WORKER_SAVE_COMPLETED`
- suppressed worker dialog/failure counts when applicable.

## Batch optimization

This release focuses on correctness and unattended processing. It does not yet
change the worker lifetime to one persistent Revit process for an entire
multi-host batch. That remains a compatible follow-up optimization once this
central-first repair lifecycle is confirmed in real Revit.
