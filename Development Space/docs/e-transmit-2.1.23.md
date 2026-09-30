# EasyBIM e-transmit 2.1.23 — PDF link repair, noninteractive batch worker, package-central output

## Why PDFs showed "Not Found"

The repaired Hertz Hall RVT showed linked PDFs with Saved Path values such as:

    Links\PDF\HERTZ MECHANICAL PIPING PLAN - GROUND LEVEL.pdf

while Manage Links reported **Not Found**.

For Revit ImageType/PDF links, a relative path in a workshared file is resolved
relative to the workshared **central model location**, not merely the physical
folder containing the RVT. Revit's API also allows an absolute local file path
to be supplied to ImageTypeOptions while `useRelativePath=True`; Revit then
stores the corresponding relative reference.

2.1.23 therefore:

1. gives `ImageType.ReloadFrom` the exact absolute packaged PDF/image file;
2. asks Revit itself to store the link relatively;
3. calls `ImageType.CanReload()` immediately in the same open worker document;
4. if Revit still cannot resolve that relative path, retries that one reference
   using the absolute packaged path and reports
   `IMAGE_RELATIVE_FALLBACK_ABSOLUTE` instead of leaving a broken Not Found
   reference.

Imported PDFs/images are embedded and are not repathed.

## Package central rather than re-transmitted host

The early e-transmit implementation saved a detached/preserved-workset copy as a
new central before writing relative links. That state is useful because relative
PDF/image paths in a workshared project have a stable central-file base.

The separate worker now deliberately restores that behavior:

1. open the task-owned stage RVT detached/preserve-worksets;
2. save it as a **new package-owned central** at the exported host path;
3. repath Revit/CAD/PDF/image references against package files;
4. save and close;
5. do **not** mark that successfully worker-repaired host transmitted again.

There is no durable "saved detached document" state that is equivalent to an
unsaved detached session while preserving worksets. A preserved-workset SaveAs
becomes a workshared file. 2.1.23 keeps it as a package-owned central so relative
linked images/PDFs continue to resolve. Recipients can open it normally or
create a detached/local working copy as required.

The package report records transmission status `PACKAGE_CENTRAL`.

## One Revit worker for the whole batch

The parent no longer starts one Revit.exe for every host. A WorkerSession starts
Revit once on the first native repair job and keeps that process alive.

For each model, sequentially:

    open one package stage RVT
    repair references
    SaveAs/save package central
    close document
    wait for next job

The same worker process handles subsequent hosts. It exits when the parent
Registry closes at the end of the e-transmit command. Only one model is open in
the worker at a time.

If that Revit worker exits unexpectedly between jobs, the next job can start a
replacement process. A failure during a job retains worker diagnostics and
flows through the existing host rollback path.

## Noninteractive worker

The disposable worker is intended to complete without a person watching it.

At EasyBIM startup in worker mode, it registers:

- `DialogBoxShowing`: automatically returns an affirmative/continue result
  (OK, Yes, Retry, then Revit command-link choices) when Revit exposes the
  modal through the API. Every auto-handled dialog is recorded.
- `FailuresProcessing`: deletes warnings. Unresolved transaction errors are
  rolled back instead of waiting on the normal Revit failure dialog.

The normal user's Revit session does **not** get these aggressive handlers.
They exist only in the disposable worker process and only package copies are
modified there.

OS/licensing/sign-in windows that appear before pyRevit/EasyBIM loads are outside
Revit's DialogBoxShowing API and cannot be guaranteed suppressible by this
mechanism. Package repair uses already-acquired local/cache files to minimize
those cases.

## CAD and Revit links

Existing native repair remains:

- Revit links: `RevitLinkType.LoadFrom`
- linked DWG: `CADLinkType.LoadFrom` fallback when TransmissionData did not
  already repath it
- supported closed-file references: TransmissionData desired paths

A CAD failure is isolated and reported without undoing successful PDF/image or
other CAD repairs.

## Verification policy

Worker-repaired models are not reopened merely to compare the paths that were
just written. `WORKER_SAVE_COMPLETED` means all repair operations reached the
worker save/close boundary. Image/PDF relative-path validity is checked in the
same worker document before saving, using `CanReload()`.

## Safety boundaries

- The user's working document is never SaveAs'd or closed by the worker.
- The worker opens only EasyBIM-owned stage/package copies.
- Source files and ACC cache files are not modified.
- One model at a time is open in the worker.
- Failed repair jobs use the existing unmodified package-host rollback path.
