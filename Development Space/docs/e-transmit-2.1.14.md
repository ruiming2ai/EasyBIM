# EasyBIM e-transmit 2.1.14 — detached source recovery and silent progress fallback

## Scope

2.1.14 closes two gaps found after the 2.1.13 stabilization: exact-source
recovery for already-open detached local models, and progress UI failure that
could open the pyRevit output console. It also locks the Browse models saved-RVT
fast path with an end-to-end regression.

The safety contract is unchanged: e-transmit never silently SaveAs, Sync,
Publish, move, or close the user's working source document.

## Detached local host recovery

Revit gives a detached document an empty `Document.PathName`, so e-transmit must
recover the exact saved RVT without guessing.

- `DocumentOpening` pending records are now stamped with the current Revit
  journal and only a pending record from that journal may bind to the opened
  document.
- The read-only journal fallback now parses Revit's actual multiline
  `Jrn.Data  _` / `"File Name"` format instead of only the simplified one-line
  form used by the earlier regression test.
- Relative file-dialog paths recorded by Revit are resolved deterministically
  against `Application.RecordingJournalFilename`'s directory. This is an exact
  path reconstruction, not a filename search.
- `>Open:Local` and cloud central/local journal candidates use the same exact
  normalization.
- Same-named sources that remain ambiguous are still refused. There is no
  newest-file, basename, published-version, or similarly-named-file fallback.

Once proven, the saved bytes are copied to task-owned staging/package locations.
The open working document's path is not changed.

## Browse models saved-RVT fast path

An exact RVT chosen with **Browse models...** remains a `SAVED_FILE` source.
A new regression drives preflight through packaging with Repath, Cleanup, and
Upgrade all OFF and verifies:

- the primary host is present in the final package;
- its checksum matches the selected saved RVT;
- the collect-only path does not authorize or invoke a Revit host open.

Repath/Cleanup/Upgrade behavior is otherwise unchanged and may process only
package/staging copies when those options require Revit document APIs.

## Progress UI

The startup-registered dockable progress pane remains the first choice. If it is
not registered, cannot be rebound/opened, or fails during an update:

1. e-transmit silently falls back to the previous top pyRevit progress overlay;
2. the overlay keeps phase colors, Cancel, and a slightly transparent `0.92`
   opacity;
3. if the overlay is also unavailable, progress becomes a no-op and transfer
   processing continues.

Progress UI failures no longer call pyRevit warning logging, so they do not open
an output-console warning window. `ET_STEP_09` tracing records the active mode as
`DOCKABLE_PANE`, `TOP_OVERLAY`, or `NONE` for post-crash diagnosis.

## Preserved decisions

- Live ACC hosts still must come from native Revit Home > Autodesk Docs.
- No published ACC/browser substitute was added.
- Repath OFF + Cleanup OFF + Upgrade OFF remains the no-host-open collect-only
  path.
- Direct linked RVTs remain copy-only; nested linked-model recursion is not
  added.
- Obsolete controls removed in 2.1.13 remain removed.
- Only the final primary workshared host is marked transmitted.

## Validation boundary

Portable CPython and IronPython-shaped regression suites exercise the source
tracker, package path, and progress controller. They do not replace the required
real-Revit smoke tests for Revit 2024, 2025, and 2026, including the Rev-25 and
Rev-26 cases that motivated this work.
