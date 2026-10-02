# e-transmit 2.1.28 - repair available references without detached output

## Evidence and root cause

The latest supplied 2.1.27 report records a copied host and 55 copied files,
but no worker repair or saved-reference verification. Its unresolved external
architectural RVT triggered HOST_PRESERVED_WITHOUT_LINK_REPATH and
HOST_COPY_PRESERVED. PDF/image targets in Links were marked
NOT_APPLIED_HOST_UNCHANGED. Non-detached opening alone cannot update their paths:
the host was deliberately left byte-for-byte unchanged before repair began.

Two engine gates treated an unavailable RVT as a veto on every other reference.
The 2.1.26 report instead shows PDFs marked MANUAL_REPAIR_REQUIRED after
metadata-only processing; neither historical report proves successful PDF repair.

## Correction

With Repath and the recommended host-preservation option enabled, capable
backends now run an independent repair even when an RVT is unavailable:

1. Work only on the acquired scratch copy. Temporarily unload native RVT
   references with TransmissionData. For workshared hosts, open the scratch
   copy with user worksets closed to suppress normal cloud/external-link loading.
2. Enumerate real top-level Revit link types in that saved copy. Keep available
   references eligible for repair. Globally unload the unavailable ones with
   Unload(null), which does not save shared coordinates back to a source link.
   An explicitly Unloaded link does not need another unload call. A closed
   workset, local-user override, or NotFound status is not proof of global unload.
3. Save the scratch preparation, open for ordinary repair, and verify unavailable
   references remain Unloaded with worksets open. Repair available PDF/images,
   native CAD and other supported references to their collected package paths.
   Existing external RVT repair remains available for successfully collected RVTs.
4. Save a normal independent package host. Temporary transmitted metadata is
   materialized and cleared using the existing save lifecycle; it is never the
   final output. Read back final native relative paths.
5. Reopen the saved output without Detach, make no further changes or saves, and
   verify actual supported reference paths plus unavailable-link unloaded status.
   Reject detached or unexpectedly modified output. The engine requires this
   evidence before accepting partial repair success.

Unavailable references remain unresolved, not claimed to be packaged. If a
live-session RVT is absent from the saved host, no replacement is invented;
its report says NOT_PRESENT_IN_SAVED_HOST. Present unavailable RVTs keep their
original reference identity, are unloaded, and are reported separately from
successfully repaired PDF/image/CAD references. Their former requested load
state is retained in the report.

Repath off still copies unchanged saved bytes. Strict mode (preservation
unchecked), Cleanup and Upgrade keep their existing strict completion policy.
If preparation, repair, saving or verification fails, the original acquired host
is restored only after worker exit is confirmed. A still-running worker remains
quarantined. Rollback clears repaired-path claims. Source acquisition, preflight
consent, cache identity policy and Auto Update have not been modified.

## Reporting

START_HERE/REPORT and references.csv include per-reference repair status,
verification and actual saved path when read back. The host manifest records
normal_open_verified and unavailable_links_checked. File-copy checksums alone
are not repath verification. NEEDS_REVIEW remains appropriate for unavailable or
unsupported references even if other references were repaired successfully.

## Deliberate limits

- The missing RVT itself is not recovered from a guessed filename or cloud
  revision. This change fixes its veto over other collected dependencies.
- Non-workshared hosts with unavailable externally managed RVTs cannot use the
  closed-workset preparation. They fail safely to the unchanged host rather
  than silently load an unproven cloud substitute.
- Point-cloud paths with no exposed safe write API, private plugin associations,
  missing keynotes and external RCP scans remain explicit manual-repair items.
  No point-cloud type/instance is deleted or recreated; RCP contents are not edited.
- Revit may take additional time to open/save/reopen. No speedup is claimed.

## Verification

New file-backed regressions cover missing native and external RVTs, persisted
PDF/CAD paths, normal reopening, an already-unloaded link, failed unload, an
absent live-only link, source hashes/timestamps, misleading worker success,
reverted-on-close image paths, detached final output and an unavailable link
loading again. Existing tests were extended for actual saved path readback and
the additional normal-open verification. All Autodesk APIs in these tests are
fixtures, not licensed Revit integration tests.

Desktop acceptance: use version 2.1.28 and a fresh output folder, Repath on,
preservation checked, Cleanup/Upgrade off. In the missing-RVT case, confirm the
available PDF/CAD references point into Links while the unavailable RVT stays
unloaded; the report must distinguish both outcomes. Reopen normally, relocate
the complete package and verify relative links. Confirm original model/cache
contents and timestamps remain unchanged. Run a complete-link case and Repath
off case as well. This desktop acceptance cannot be certified by portable CI.

## Autodesk API references

- TransmissionData (local native references, materialization and transmitted behavior):
  https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/d78d1e9c-1cee-1336-88d5-b605dacd077d.htm
- LinkedFileStatus (Unloaded, InClosedWorkset, LocallyUnloaded, NotFound):
  https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/410b48f8-2a32-3d54-492c-c9a9fe4030ca.htm
- RevitLinkType.Unload:
  https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/83f4add7-1c0a-ddfa-b8ab-5be6df0f28a2.htm
- WorksharingSaveAsOptions.ClearTransmitted:
  https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/0289f8a9-1156-868f-6fcc-5ccf7c38fb23.htm
- ImageTypeOptions constructor:
  https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/7dda4131-548f-7c39-4dcd-ba9b85846018.htm
