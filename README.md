# PyRevit_EasyBIM
PyRevit Tools created by Ruiming Liu

## Temp Phase & View (Revit 2015-2027)

Temp Phase & View is a Python-only pyRevit workflow, grouped as one ribbon
pulldown with three commands:

- **Temp Phase** — apply the selected phase to the active view and record its
  original state in the EasyBIM session.
- **Restore** — undo the temporary phase and view settings for the active view
  immediately, without waiting for the file to close.
- **Restore All Views** — do the same for every view in the document, covering
  both tracked views and any other view left with Temporary View Properties
  enabled.

Both restore commands run the same transaction close recovery uses, so the
results are identical; they only change *when* the cleanup happens. Once a
document has nothing left to restore, its close-recovery arming is dropped so
later closes skip the view scan entirely. As with close recovery, the restored
state is only in the open session until the model is saved or synchronized.

When a file close is requested, EasyBIM cancels the close, restores tracked
phases, and clears every tracked or discoverable Temporary View Property in one
transaction. Close-Stop is armed per document only after the button applies a
temporary phase, so files where the button has not been used close normally.

After restoration, the Close-Stop dialog explains that the cleanup is currently
only in the open session. Choose one of the following actions:

- **Save and Close** — save the restored project, then close it
  only after Revit reports that the save completed successfully. Save As cases
  are handled through the same completion flow.
- **Synchronize and Close** — available for workshared files;
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
staging, build step, or cache clearing is required. The standalone C# fallback
add-in and its source have been removed from the repository; the Python command
and hooks are the only Temp Phase implementation.

Diagnostics are emitted only through pyRevit's standard debug logging (enable
pyRevit debug mode to see markers such as `DocClosingCancelSucceeded` or
`TempPhaseRestoreCommitted`). The legacy per-event file log at
`%APPDATA%\EasyBIM\Temp Phase\logs\events.log` is no longer written; the
folder can be deleted on machines where it exists.
