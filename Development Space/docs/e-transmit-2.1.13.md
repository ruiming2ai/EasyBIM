# EasyBIM e-transmit 2.1.13 — detached-host stability and persistent progress UI

## Scope

2.1.13 stabilizes the saved-source workflow introduced in 2.1.11/2.1.12. It does
not add a new ACC browser, published-version fallback, deep inspection, automatic
SaveAs, Sync, Publish, or global link loading.

## Primary-host readiness

Before a run root or dependency package is created, every selected primary host
must have a provable saved source.

- Normal local hosts use their exact existing Document.PathName.
- Live ACC hosts must expose a valid Revit cloud project/model identity and
  continue to use the verified local CollaborationCache acquisition path.
- Pathless detached hosts use the exact source captured by EasyBIM's
  DocumentOpening hook for the current Revit session, with the current journal
  as read-only fallback.
- Persisted title-only source records from older Revit sessions are not trusted.
- A detached host with no provable exact RVT is blocked before dependency
  collection and the user is asked to save the detached model in native Revit.
- No filename search, newest-file selection, published-version substitution, or
  similarly-named RVT fallback is permitted.

Missing/stale dependency links remain a separate condition: once the primary host
is proven, available references may be collected while genuinely unavailable
references are reported as incomplete.

## Progress pane lifecycle

The e-transmit progress pane now lives in the persistent easybim package rather
than the command's hot-reloaded easybim_etransmit package.

- startup.py registers the pane once during Revit/pyRevit application startup.
- The e-transmit command only opens, resets, updates and closes the existing pane.
- Command code never calls register_dockable_panel.
- The requested top-docked state explicitly uses a small minimum height so the
  pane remains a thin strip.
- Missing/broken progress UI degrades to a no-op controller and logs
  PROGRESS_UI_UNAVAILABLE; transfer processing continues.

A Revit restart is required after installing/updating the extension for a newly
registered dockable pane to become available.

## Crash-boundary diagnostics

Lightweight ET_STEP journal comments identify the last completed startup stage.
After the run root exists, the same markers are append-written to ET_TRACE.txt
and each write is immediately closed/flushed, so the last boundary can survive a
hard Revit termination.

The 2.1.13 instrumentation does not claim that the Revit 2026 crash was caused by
the old runtime pane registration; it makes the next failure attributable.

## UI simplification

Removed from the normal e-transmit UI:

- Use saved copy for selected row...
- Skip unresolved cloud downloads...
- Exact source mappings / Add exact prefix mapping / Remove mapping

Old settings containing mappings or skip-cloud values are ignored. Low-level
mapping support may remain for compatibility, but the normal UI supplies an empty
mapping list.

## Preserved fast path

Repath OFF + Cleanup OFF + Upgrade OFF still performs saved-byte collection
without opening the packaged host. 2.1.12's closed-file TransmissionData
IsTransmitted operation remains independent of Repath and applies only to the
final primary workshared host.

## Real-Revit acceptance

Portable/API-shaped CI is necessary but not sufficient. Before merge/release,
smoke-test at least:

- Revit 2024 normal local + ACC live
- Revit 2025 Rev-25 detached-host case
- Revit 2026 Rev-26 crash case
- Repath OFF no extra host open
- Repath ON file references
- transmitted host marking
- unloaded-link temporary reload and restoration
- visible progress pane and Cancel
