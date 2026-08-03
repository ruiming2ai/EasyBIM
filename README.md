# PyRevit_EasyBIM
PyRevit Tools created by Ruiming Liu

## Temp Phase & View (Revit 2015-2027)

Temp Phase & View is a Python-only pyRevit workflow driven by one ribbon
button. Clicking it opens a window that carries every option:

- **Apply** — apply the phase selected in the list to the active view and
  record its original state in the EasyBIM session.
- **Restore** — undo the temporary phase and view settings for the active view
  immediately, without waiting for the file to close.
- **Restore All Views** — do the same for every view in the document, covering
  both tracked views and any other view left with Temporary View Properties
  enabled.
- **Cancel** — close the window without changing anything.

Both restore options run the same transaction close recovery uses, so the
results are identical; they only change *when* the cleanup happens. Once a
document has nothing left to restore, its close-recovery arming is dropped so
later closes skip the view scan entirely. As with close recovery, the restored
state is only in the open session until the model is saved or synchronized.

The window still opens on a view that cannot take a temporary phase — the
phase list and Apply are disabled, so the restore options stay reachable.

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
document open. The Python `doc-closing` and `doc-closed` hooks, together with a
single `Idling` delegate installed at startup (`lib/easybim/idling.py`),
coordinate the per-document trigger/state and prevent duplicate close reposts.
Using Temp Phase in one file does not arm close recovery for other open files.

Normal users only need to update EasyBIM from GitHub and reload pyRevit. No DLL
staging, build step, or cache clearing is required. The standalone C# fallback
add-in and its source have been removed from the repository; the Python command
and hooks are the only Temp Phase implementation.

Clash Detection Mode is the one command that runs in a persistent pyRevit
engine, because it owns live Revit event handlers that must outlive the click
that created them. A persistent engine keeps its loaded modules across a
pyRevit reload, so the command drops its own modules on each launch when no
detection session is running - that is what lets an update take effect on the
next click instead of only after a Revit restart. Its main window shows a
`Build <timestamp>` stamp read from the files on disk: if that does not move
after an update, Revit is still loading the old files from somewhere else.

Diagnostics are emitted only through pyRevit's standard debug logging (enable
pyRevit debug mode to see markers such as `DocClosingCancelSucceeded` or
`TempPhaseRestoreCommitted`). The legacy per-event file log at
`%APPDATA%\EasyBIM\Temp Phase\logs\events.log` is no longer written; the
folder can be deleted on machines where it exists.

## Tag Align (Misc Tools)

Tag Align copies one tag's placement onto other elements. Pick a reference tag,
then either align the tags that already exist or tag and align in one pass.

- **Select One Reference Tag** asks whether the tag can be aligned to any
  orientation. Answer yes and the offset rotates with the element, so a tag two
  feet above a horizontal pipe sits two feet off the side of a vertical one.
  Answer no and only elements at that same orientation are touched; the rest are
  named in the report so you know to add a second reference for them.
- **Select Multiple Reference Tags** matches per element type and orientation -
  one reference for horizontal walls, another for vertical, and so on.

All reference tags must share one tag family type and one element category, and
a different category is never matched. Two scopes are offered: exact family and
type, or "different types, but only paired family" (the default). Anything that
would put two different tags in the same place is reported as a conflict, with a
window to pick the winner per group, before a single tag moves.

Elements are picked either one at a time - each click is processed immediately
and Esc ends the loop - or as a batch, where a small bar offers Filter, Select,
Deselect and Process while Revit's normal window and crossing selection does the
picking. A batch is one transaction, so one undo puts it all back.

The offset is measured from the element rather than from the sheet, so it
survives rotation, and it is stored with its source view scale, so a reference
measured at 1:100 reproduces the same printed distance at 1:50. Room, area and
space tags are not supported yet and are rejected when picked as a reference.
