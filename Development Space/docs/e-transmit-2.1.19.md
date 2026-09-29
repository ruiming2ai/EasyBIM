# EasyBIM e-transmit 2.1.19 — placed-link filtering, batch preflight, and progress placement

## Scope

2.1.19 tightens the detached-model / unloaded-link workflow after real Revit
testing.

## Reload dialog shows only links actually placed in the host

Revit can retain `RevitLinkType` definitions after every placed instance has
been removed. Those stale or outdated types are not host dependencies.

The temporary-reload list now intersects `RevitLinkType` with the type ids of
actual `RevitLinkInstance` elements in the selected host document before a row
can be offered.

This prevents unused definitions such as old `_OUTDATED_...` link types from
appearing merely because they still exist in the RVT database.

The same placed-instance filter is also used when enriching unloaded link
metadata, so unused link types are not promoted into the host dependency
inventory.

## Detached source recovery message

If a manually selected RVT cannot be used, the retry message is intentionally
short:

> eTransmit cannot locate the detached model. Please load from selected file locations.

The choices remain **Load another model from file location…** and **Cancel**.

## Reload selection controls

The batch unloaded-link dialog now provides:

- **Select All**
- **Select None**
- **Reload selected and continue**
- **Continue without reloading**
- **Cancel**

ACC/cloud links that normally require a live Revit load remain selected by
default during detached-model file recovery. Direct file/server links remain
unselected by default when a saved path is already available.

## Batch preflight order

For a multi-model e-transmit, all decision-making now completes before package
processing starts:

1. The initial source selection scans the whole selected set for detached hosts.
2. Each unresolved detached host is resolved by the user before the main dialog
   is accepted.
3. One save decision is collected for selected modified live models.
4. All selected live hosts are registered and scanned.
5. One combined unloaded-link dialog is built across the complete batch, with
   the host shown for every row.
6. The user completes all reload selections.
7. Only after all decisions are complete does the progress phase begin.
8. Approved temporary reload/capture operations run.
9. The full model batch then runs without another detached-source or unloaded-link
   decision interrupting it.

## Detached Save As recovery

Both detached recovery paths now keep the live document as a dependency-discovery
context:

- **Save current detached model and transmit** uses the newly saved RVT as the
  transmitted host bytes but retains the live document for placed-link scanning.
- **Load model from file location…** uses the selected RVT as the transmitted
  host bytes and retains the open detached document for link discovery/reload.

This is needed for ACC/cloud links that may only expose a usable saved cache
revision after a temporary Revit load.

## Progress fallback placement

pyRevit's built-in prompt bar normally anchors to the top of the Revit window.
The EasyBIM fallback now explicitly loads `AdWindows`, reads the live Revit
`RibbonControl`, converts the ribbon-bottom screen coordinate with the same
DPI factor used by pyRevit, and repositions the strip immediately below the
full ribbon.

The fallback re-anchors after the WPF window is shown and after every pyRevit
window-position update, preventing a later pyRevit layout pass from moving it
back over the ribbon panels.

If ribbon geometry truly cannot be read, the old top-offset fallback remains as
a last resort.

## Safety boundaries

- The manually selected host RVT is never modified.
- ACC cache files are copied read-only before packaging.
- Temporary link reloads are restored to their original unloaded/local-unloaded
  state.
- No filename guessing or same-model identity blocker is reintroduced.
- No model package processing starts until all source/save/reload choices for the
  selected batch are complete.
