# EasyBIM e-transmit 2.1.9

## Purpose

2.1.9 makes live ACC source selection unambiguous, keeps ordinary Revit-link repathing closed-file and relative, combines unavoidable ACC link conversion with final verification in one copied-host open, and improves the existing Revit-integrated progress strip.

## Source policy

- Live ACC cloud-workshared models must be opened natively through Revit Home → Autodesk Docs before e-transmit.
- The e-transmit dialog no longer exposes APS sign-in, Add ACC published model, or published-version source selection. Published models can lag the live workshared state and are not substituted for the Revit document the user actually opened.
- EasyBIM describes the source as the live ACC model currently loaded by Revit; it does not claim that collaborators have not synchronized newer work. Use Reload Latest first when that state is required.
- Unsaved in-memory edits remain excluded. EasyBIM never saves, synchronizes or publishes the original working document.

## Repath and folder behavior

- Keep the existing Host.rvt + Links/<category>/... layout.
- Local/network/file-based Revit links are repathed with relative TransmissionData paths without opening the host for repath.
- Different sources with the same filename keep that filename and use distinct collision subfolders.
- Directly collected linked RVTs remain unchanged and are not recursively processed.

## True ACC External Resource links

TransmissionData cannot convert a true cloud external-resource link to a packaged local link. For that case only, the copied host is deferred until all package dependencies are in final locations.

The finalization sequence is:

1. Open the copied host once.
2. Convert the ACC/external link to the packaged local RVT.
3. SaveAs the final host path.
4. Reload the packaged RVT as a relative local resource and save.
5. Verify immediate link path and requested load state in the same open document.
6. Close once.

If this same-document verification succeeds, the engine marks the host OPENED_AND_REFERENCES_CHECKED and does not reopen it for a second verification pass. Failure/cancellation preserves or restores the collected host and reports the issue.

## Progress UI

The existing pyRevit ProgressBar remains inside Revit. It is moved down by one prompt-bar height so it does not cover the Revit title/document name. Phase text remains authoritative; color application is best-effort for older pyRevit/WPF builds.

- Blue: COLLECTING / REPATHING
- Purple: FINALIZING ACC LINKS / VERIFYING FINAL PACKAGE
- Green: READY
- Amber: READY — REVIEW ISSUES
- Red: INCOMPLETE
- Gray: CANCELLED

Ordinary final verification uses the explicit label PACKAGE BUILT — VERIFYING FINAL PACKAGE.

## Regression coverage

Portable/API-shaped tests cover:
- removal of published ACC source controls;
- source-mode guard against PUBLISHED_VERSION;
- closed-file relative paths and same-name collision subfolders;
- one-open true ACC conversion/save/verify behavior;
- relative local resource reload after SaveAs;
- skipped duplicate final verification;
- progress position, filename visibility, phase labels and colors;
- legacy local/cache, rollback, long-path, copy-integrity and report behavior.

Real Revit/ACC acceptance is still required because portable tests cannot prove Autodesk cloud cache behavior, true external-resource conversion, Revit window positioning on every DPI setup, or final link portability.
