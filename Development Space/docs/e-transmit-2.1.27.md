# e-transmit 2.1.27 — preserve normal host delivery

## Root cause established from the two supplied reports and historical source

The older report identifies 2.1.6. Its successful host-copy path did not repath
that host: an unresolved Revit link caused HOST_PRESERVED_WITHOUT_LINK_REPATH,
leaving the saved/cache host unchanged. Historical implementation reviewed:
`d6e5825653bf6970a741bb19594607eeec816e98` (engine.py and revit.py).

The newer report identifies 2.1.26, not 2.1.25. Its delivered host was copied but
then modified by metadata-only repathing and marked TRANSMITTED. This is why
restoring file delivery alone did not restore the requested opening behavior.
Autodesk explicitly states that transmitted workshared files open detached.
Neither report establishes complete successful repathing of all dependencies.

## Corrected behavior

- Repath off: copy the saved host unchanged. Do not SaveAs, detach, or mark the
  delivered host transmitted. Unsaved edits are excluded.
- Repath on: use the existing separate Revit repair/save lifecycle, not
  metadata-only delivery. Successful repairs must have persisted references,
  a normal saved host, and a cleared transmission flag. The save boundary rejects
  a document still reporting IsDetached; the parent independently reads the final
  transmission state before accepting default-mode worker success.
- Default "Keep the original host if repairs fail": preserve/restore the verified
  acquisition bytes at the package host path if links are missing, inventory
  fails, a repair is cancelled, or the stopped worker fails. Clear all repaired
  reference claims on rollback. Report those unapplied repairs prominently.
- A worker whose exit is not confirmed remains quarantined. Never overwrite its
  active target or archive it. Keep the original recovery bytes and diagnostics.
- Unchecking the preservation option, or selecting Cleanup/Upgrade, retains
  strict independent-model completion and its existing recovery behavior.

Temporary repair documents may be opened detached to protect the working model;
they are not the exported result. They must be saved as normal package models
before acceptance. No source SaveAs, Sync, Publish, or new source mutations are
introduced. Existing explicit preflight save/reload decisions are unchanged.

The unchanged fallback deliberately preserves the input's existing state and
central association. An already-transmitted input stays already-transmitted
when copied unchanged; this must not be described as newly normalized. Never
synchronize an unchanged copy to the original central. Recipients are no longer
instructed to detach the exported host as a workaround for this tool's repathing.

## External-link inventory correction

Native TransmissionData omits externally managed references. An external Revit
link observed in a local open model is no longer silently discarded just because
it is absent from native saved metadata. Exact matching saved/live revision with
no unsaved edits can establish membership; otherwise the reference is retained
as unresolved with SAVED_EXTERNAL_LINK_UNVERIFIED. It is not guessed, silently
counted as zero, or bound to an unproven saved model. Ordinary saved file links
remain authoritative over unsaved retargets.

## Verification and remaining desktop acceptance

Automated regressions cover acquisition/delivery/rollback checksums, a missing
link, a failing worker, interrupted transmission writing, misleading worker
success, preserved source timestamps, genuine saved-path fixtures, external-link
membership, non-detached save rejection, reporting, and live-worker archive
blocking. Existing strict-mode tests remain. Autodesk serialization in portable
fixtures is not a real Revit integration test.

Desktop acceptance: confirm version 2.1.27; use a fresh output folder and leave
preservation checked with Cleanup/Upgrade off. Test both Repath off and Repath
on, a missing-link case, and a complete-link case. For unchanged outputs, compare
host SHA256 with the acquired saved bytes; for repaired outputs confirm normal
opening and saved reference paths in Revit. Move the whole package and recheck
relative links. Confirm source/cache contents and timestamps did not change.

Autodesk primary references:
- TransmissionData (including detached opening effects):
  https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/d78d1e9c-1cee-1336-88d5-b605dacd077d.htm
- WorksharingSaveAsOptions.ClearTransmitted:
  https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/0289f8a9-1156-868f-6fcc-5ccf7c38fb23.htm
- Document.IsDetached:
  https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/0792283e-f112-0a57-d0d9-e79e6b9ea5b9.htm
