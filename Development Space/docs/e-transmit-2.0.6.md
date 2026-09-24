# e-transmit 2.0.6: progress context and dependency paths

## Observed failure and reproduction

The 2.0.5 report shows a copied host followed by MainWindowHandle errors for
PDFs, images, other dependencies, and model processing. The old engine invokes
its UI progress callback immediately before each copy, during copy chunks,
and before model finishing; an exception in that callback was incorrectly
reported as a failed file operation.

The upstream pyRevit ProgressBar calls update_window on redraw, which resolves
the current HOST_APP window. A reported pyRevit document-hook issue describes
that context becoming unavailable after background model opening:
https://github.com/pyrevitlabs/pyRevit/issues/1851
https://docs.pyrevitlabs.io/reference/pyrevit/forms/_ipy/

The regression simulates that context loss after host inspection while running
the production UI runner, collector, hashing and reporting. Before repair it
reproduces the host-only result and MainWindowHandle failures; after repair it
collects the host, PDF and image. UI and Revit API objects are simulated, not a
live Revit session. No company model or authenticated ACC workspace was tested.

## Changes

- A tool-local ProgressBar subclass captures initial window geometry and avoids
  looking up mutable pyRevit host context on subsequent redraws. Progress values
  and the Cancel button still use the normal progress control. The progress bar
  stays at its initial position during the run; it does not follow window moves.
- A redraw failure is recorded once as PROGRESS_UI_FAILED, not as a failed copy.
  The file pipeline continues; cancellation signals are not swallowed. If this
  fallback is used, the progress display may stop updating.
- Convert PointCloudType.GetPath ModelPath with ModelPathUtils instead of using
  the object's debug string. A null, non-file engine path is reported explicitly.
- Resolve saved relative workshared links against the original saved central
  path when it is a filesystem path, consistent with Revit ModelPath semantics:
  https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/40a84c72-e4b8-72ac-2f71-3216c66a11b3.htm
- Reject a dependency that resolves into this run's inspection scratch folder.
  Such a display path is not evidence of the original source location. It is
  reported as STAGING_REFERENCE_UNRESOLVED; no same-name or live model is chosen.
- Collection errors retain operation, exception type and traceback in the
  manifest; detailed reports also write DIAGNOSTICS.txt when a trace is present.
- The completion dialog says incomplete when actual file/model errors remain.

The SHA-256 fix from 2.0.5, all-file defaults, source hierarchy and filenames,
Shared/Consumed locations, overwrite protection and original-model protection
are retained. No hooks, startup scripts, other buttons or panel layouts change.
Imported content extraction remains out of scope. Missing network paths and
unverifiable cloud references still need their exact intended source locations.

## Tests and desktop acceptance

The portable suite includes nine new regressions in test_runtime_progress.py.
The existing Windows IronPython job runs these as well as the six real-copy
regressions from 2.0.5. File IO, hashes and reports are real; WPF/Revit behavior
is simulated. This is not certification of real model opening or ACC downloads.

Update the complete extension, confirm version 2.0.6, then test the same saved
host into a new output folder with deep inspection and repath enabled, upgrade
and cleanup disabled. Confirm that accessible linked files appear after host
inspection, check every unresolved path, and open the relocated packaged model
before delivery. Never synchronize package copies to the original central.

For a new failure, preserve REPORT.txt, manifest.json and DIAGNOSTICS.txt (if
present). Those reports include original project paths; review before sharing.
