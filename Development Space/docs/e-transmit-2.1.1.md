# e-transmit 2.1.1 — protect the current open host first

## What changed

The reported 2.1.0 dialog recorded `HOST_SNAPSHOT_DECLINED`, 1 of 2 hosts, and optional plugin coverage warnings. That message means one host was skipped at the save-consent gate; it is not evidence that Revit's native file writer failed. This repair moves consent before batch work and prioritizes the host over dependency discovery. ACC linked-model download work is deferred, not claimed fixed.

For each checked **open model**:

1. Show one explicit confirmation before starting the batch. It authorizes SaveAs of the currently open documents, including unsaved edits, and explains the working-path change. Declining leaves the selection dialog open and creates no partial batch.
2. Register the Document without scanning plugins or reopening models. Capture its original source/central path for reference resolution.
3. Save the actual Document to a new, non-overwriting RVT in the retained `<run>_WorkingSnapshots` folder. No published/ADC/saved host is substituted, even when the document currently reports no unsaved changes. No Sync or Publish is called.
4. Check the new native RVT and compare its saved DocumentVersion to the current clean Document. Copy it with checksums into the model's job folder and retain a second `HostSnapshot` copy before optional material discovery.
5. Read the host's and already-loaded linked models' materials. Missing materials, skipped cloud links, or optional plugin coverage failures do not delete an already captured host.

An actual native SaveAs failure remains an error with its original exception details. A file produced before an external post-save event throws is accepted only if it is a native RVT matching the clean current document revision. Any generated working file is retained even when it cannot be verified. No error is turned into a success merely because a file exists.

## Important working-document safety boundary

**Native Revit SaveAs can change the open working document's file location.**
The command does not overwrite the original local/central file and never calls Sync or Publish, but it cannot promise that the active Revit document remains attached to its old path. That consequence is disclosed before authorization and the resulting working path is recorded in the report. Review that path before continuing project work. The command never automatically closes, switches back, re-saves over the old source, or deletes the working snapshot. Returning to the original cloud project is an explicit user workflow, not an automatic merge of changes.

Loaded linked Documents are never saved or closed. An error reading third-party spreadsheet storage does not imply that the host model is corrupt.

## Default host-preservation options

**Preserve current open host unchanged (no repath/cleanup of this RVT)** is checked by default. The delivered primary host is the native current-state snapshot; the tool does not repath references, purge definitions, delete sheets/views, or recreate tags/link elements in it. The protected copy is checked again at the end. If the primary delivery copy unexpectedly differs, it is restored from the verified protected copy and the incident is reported.

**Skip ACC linked RVT downloads; keep existing link references in the host** is checked by default. This does not remove or recreate link elements. The linked RVT bytes may be absent; materials readable from loaded linked documents can still be collected. Local/saved workflows and explicit optional cloud download functionality remain available, but no cloud sign-in is needed to save the current open host.

A preserved host with missing linked RVTs is **not a self-contained transmittal**. Its report distinguishes `SNAPSHOT_ONLY_NOT_REOPENED` from full packaged-model opening/link verification. Nothing in this release certifies that unavailable links will display offline. Retained original references may still resolve to the original project if the delivered copy is opened in an environment with access.

For an open host, repath/cleanup/upgrade options do not change its protected copy. With preservation disabled explicitly, only the separate delivered copy can enter those processing steps. The untouched HostSnapshot remains available. If the backup could not be written, processing that would change the primary host is skipped and the valid primary/recovery copies remain reported.

## Model-named jobs and ZIPs

Instead of `01`, `02`, the job folders use Revit model names without the `.rvt` extension:

```
ET_<timestamp>/
  AEI-UCB_Morrison Hall_MEP_R24/
    Host/<identity>/AEI-UCB_Morrison Hall_MEP_R24.rvt
    HostSnapshot/<identity>/AEI-UCB_Morrison Hall_MEP_R24.rvt
    Sources/...
    START_HERE.txt
    manifest.json
  AEI-UCB_Lewis Hall_MEP_R24/
    ...
  AEI-UCB_Morrison Hall_MEP_R24.zip   # when separate ZIPs are enabled
  AEI-UCB_Lewis Hall_MEP_R24.zip
  batch.json
```

The original RVT and dependency filenames remain unchanged. Only duplicate job-folder names receive a suffix such as ` (2)`; case-insensitive folder/ZIP collisions and existing outputs are protected. Windows-invalid or overlong model names/paths stop preflight rather than silently shortening a filename. Use a short destination such as `C:\ET` when necessary. Native RVT path limits are checked separately from the long-path filesystem copier.

Per-model ZIPs include Host, HostSnapshot, Sources, any _Refs, and reports. Do not deliver a protected-host-only package as if all linked files were included. A ZIP checksum/CRC check is not a Revit opening test.

## Evidence and tests

The original failing behavior has regression tests for late/declined consent, host serialization before optional inventory, no saved/published fallback, immutable host survival after a missing dependency, native SaveAs and post-save exceptions, revision mismatch, cancellation before SaveAs, loaded-link save protection, original central-path retention, model/ZIP collisions, upfront path validation, and optional plugin-reader isolation.

Tests exercise file IO and the actual command/UI handlers with simulated Revit/WPF boundaries. Windows CI additionally runs the new tests under official IronPython 2.7. The CI tests do not run licensed Autodesk Revit or an authenticated company workspace. A real desktop test is still required; no actual cloud-model SaveAs is claimed to have been executed here.

## Installation and first check

Update the entire EasyBIM extension from main, restart Revit, and confirm version **2.1.1**. Select the open host row, keep the two preservation/skip-cloud checkboxes enabled, select an empty destination, and authorize the initial current-state SaveAs prompt. There is no later per-host consent prompt during file copying.

Check both `Host models copied` and `Protected current-state host snapshots`. Review the retained working-document path before continuing to edit. To demonstrate unsaved-state capture on your workstation, make a clearly identifiable harmless edit in a test model, run the command without publishing, and compare that edit in the saved Host/HostSnapshot copies. Revit-version and plugin-specific desktop validation is still necessary.

All changes are confined to the e-transmit button/package, its tests, documentation, and CI workflow. No unrelated EasyBIM buttons, shared hooks, startup scripts, or Tab Color files are changed.
