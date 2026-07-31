# PyRevit_EasyBIM
PyRevit Tools created by Ruiming Liu

## Temp Phase recovery (Revit 2025-2026)

Temp Phase is a Python-only pyRevit workflow. The button applies the selected
phase and records the original view state in the EasyBIM session. When a file
close is requested, EasyBIM cancels the close, restores tracked phases, and
clears every tracked or discoverable Temporary View Property in one transaction.
Close-Stop is armed per document only after the button successfully applies a
temporary phase; files where the button has not been used close normally.

After restoration, the Close-Stop dialog explains that the cleanup is currently
only in the open session. Choose one of the following actions:

- **Save Restored File and Close** — save the restored project, then close it
  only after Revit reports that the save completed successfully. Save As cases
  are handled through the same completion flow.
- **Synchronize Restored File and Close** — available for workshared files;
  opens Revit's normal Synchronize with Central/options command and closes only
  after `DocumentSynchronizedWithCentral` reports success. Synchronizing is
  required when the restored state must be reflected in the central model.
- **Keep File Open** — leave the restored document open. It remains restored in
  the current session, but it is not permanent until the file is saved (and, for
  a local workshared file, synchronized with central).

Save or synchronization cancellation, failure, unavailable commands, Revit
shutdown, and unsupported/non-cancellable close contexts leave the restored
document open. The Python `doc-closing`, `app-idling`, and `doc-closed` hooks
coordinate the per-document trigger/state and prevent duplicate close reposts.
Using Temp Phase in one file does not arm close recovery for other open files.

Normal users only need to update EasyBIM from GitHub and reload pyRevit. No DLL
staging, build step, or cache clearing is required. The standalone C# add-in
and its source remain installed as a fallback until Revit 2025 and 2026 runtime
parity is proven; the C# projects under `src/TempPhase` are not loaded by the
normal pyRevit command or hooks.

Diagnostics are written to
`%APPDATA%\EasyBIM\Temp Phase\logs\events.log`. Useful markers include
`PythonTempPhaseDocumentArmed`, `DocClosingSkippedUnarmedDocument`,
`DocClosingArmedDocument`, `PythonTempPhaseDocumentTriggerCleared`,
`DocClosingIdentityRecorded`, `DocClosedIdentityResolved`,
`TempPhaseArmStaleRemoved`,
`DocClosingCancelSucceeded`, `TempPhaseRestoreCommitted`,
`TempPhaseSaveCloseSelected`, `TempPhaseSyncCloseSelected`,
`TempPhaseCommitCompleted`, `TempPhaseCommitFailed`,
`TempPhaseCommitCloseReposted`, and `TempPhaseCloseKeptOpen`.
