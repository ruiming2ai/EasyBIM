# EasyBIM e-transmit 2.1.22 — separate Revit repair worker

## Root cause from the 2026-09-30 reports

The latest Hertz Hall report showed that package dependencies were copied, but
the host repair was rolled back because Revit rejected opening the task-owned
workshared copy while the source local/central identity was already open in the
same Revit process:

`Cannot open the local model and the central model in the same Revit session.`

The manifest consequently recorded linked PDF rows as `repath: ROLLED_BACK`.
This is an execution-architecture failure, not a path-mapping failure.

## New repair architecture

When the source host is a live Revit document and e-transmit needs to modify the
package copy (Repath, Cleanup, Upgrade, or saved-cache normalization), the parent
session no longer opens that copy.

Instead:

1. EasyBIM finishes collecting the host and dependency files.
2. The parent writes a one-shot JSON repair job in %TEMP%.
3. EasyBIM starts the same installed `Revit.exe` version as a second process.
4. The worker process inherits only the job-file location through environment
   variables.
5. On its first usable Revit Idling tick, the worker opens the EasyBIM-owned
   stage RVT with the normal detach/preserve-worksets rules.
6. It applies the existing package-reference repair operations:
   - Revit links: `RevitLinkType.LoadFrom`
   - linked PDFs/images: `ImageType.ReloadFrom`
   - linked DWGs: `CADLinkType.LoadFrom` fallback when closed-file
     TransmissionData did not already repair them
   - supported TransmissionData references: packaged relative paths
7. It saves the package host and closes it.
8. It writes a result JSON and requests Revit exit.
9. The parent merges the repaired row state and continues package delivery.

The source document in the user's Revit process is never opened, saved, closed,
relocated, or replaced by this worker.

## No verification reopen

A successful worker repair is accepted when the Revit API repair calls,
transactions, Save/SaveAs, and close all succeed. The parent does **not** reopen
that host merely to compare paths after the worker has already written them.

Reports use `WORKER_SAVE_COMPLETED` for this state and explicitly say that no
verification reopen was performed.

The existing verification API remains available for non-worker/diagnostic paths,
but it is not part of the live-host worker repair path.

## CAD repair fallback

Some live CAD references are exposed through the document inventory but are not
represented by the saved TransmissionData row used for metadata-only repath.
For those rows, 2.1.22 calls `CADLinkType.LoadFrom(packaged_absolute_path)`
inside the worker before saving. The final TransmissionData pass can then
normalize the persisted package path where Revit exposes that reference.

## Worker startup isolation

The disposable Revit process uses EasyBIM's existing startup Idling delegate.
When the worker environment flag is present, the delegate runs only the
e-transmit worker and skips ordinary startup jobs, My Ribbon work, and EasyBIM
auto-update. This prevents an extension reload or unrelated startup task from
interrupting the repair.

## Failure behavior

If the second Revit process cannot start, exits early, times out, or reports a
repair exception, the parent treats host processing as failed and restores the
unmodified collected backup using the existing rollback behavior. The one-shot
worker job folder is retained on failure for diagnosis.

The default worker timeout is two hours to allow large Revit models to open and
save without an artificial short limit.

## Runtime

The parent pyRevit command remains IronPython/Revit API code. This change solves
the Revit document-identity conflict by moving the package write into a separate
Revit process; changing Python engines would not solve that Revit API rule.
