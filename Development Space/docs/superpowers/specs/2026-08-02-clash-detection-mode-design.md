# Clash Detection Mode Design

## Summary

A live, forward-only interference check. The user picks two category sets in
a window shaped like Revit's native Interference Check, presses **Start
Ongoing Detection Mode**, and from that point on EasyBIM reports clashes that
their own edits create. Clashes that already existed are never reported and
the whole project is never scanned.

Two delivery modes: a pop-up alert window when a clash appears, or **Silent
Mode**, which suppresses the pop-up and keeps a dockable panel on the right
updated live instead.

## Goals

- Report a clash within a fraction of a second of the edit that created it.
- Never report a clash that existed before the mode started.
- Start instantly - no baseline pass over the project.
- Cost nothing measurable while the mode is off.
- Stay responsive while the mode is on, whatever the user does.
- Write nothing to disk, so there is no cache to grow and none to delete.
- Support host-vs-host, host-vs-link and link-vs-link category pairings.
- Let the user select many categories at once (shift-click, drag, spacebar).

## Non-Goals

- Do not replace the native Interference Check for whole-project auditing.
- Do not report clashes inside a linked model's own document.
- Do not persist results across Revit sessions.
- Do not modify the model. The mode is read-only; it opens no transaction.

## Why an empty query result is not "no clash"

The first release lost any clash created by a *move* or a *copy*, and the
cause is worth recording because it is easy to reintroduce.

`_find_clashes` returned a bare list. An empty list meant three different
things - genuinely no clash, no bounding box available, or the query threw -
and the resolution pass treated all three as proof the user had resolved every
clash on that element. `remove_pair` also strips the record from the
not-yet-announced buffer, so a pair found moments earlier in the same pass
vanished before reaching the panel.

That produced a create-versus-edit asymmetry. A create makes one work item, so
nothing can erase the pair. A move or a paste routinely touches several
elements at once - multi-select, joined walls, connected MEP, a pasted group -
so element 1 recorded the clash and element 4 deleted it.

Three rules now hold, each with a test:

1. `_find_clashes` returns `(ids, completed)`, and resolution runs only when
   every target-side query completed.
2. A pair recorded during the current pass is never eligible for removal in
   that pass (`ClashSession.was_recorded_this_pass`).
3. A missing outline drops the bounding-box quick filter rather than the
   query. A bad outline may cost speed; it can never invent a "no clash".

The related silent drop: Revit reports a *container* - a group or assembly -
rather than its members, so a moved or pasted group landed on a category
neither side watched and disappeared. Containers now expand via
`GetMemberIds()`, and members that are themselves containers expand on their
own turn through the queue.

## User Behavior

The ribbon button (`Misc Tools` panel) opens the setup window. Both sides
have a `Categories from` dropdown listing the current project plus every
loaded link, over a list of the model categories that actually have placed
instances in that document. Lists use `SelectionMode="Extended"`, so a
shift-click range or a press-and-drag can be ticked with one checkbox click;
spacebar toggles the selection; `All` / `None` / `Invert` act on the list.

`Start Ongoing Detection Mode` replaces the native `OK`. `Silent Mode and
Update Clash on Dynamic Panel` is on by default.

While running, pressing the ribbon button opens the **status window**: running
or paused, elapsed time, what is being watched, and live counts, with *Show
Panel*, *Pause*, *Resume*, *Edit Categories*, *Stop Detection*. That, plus a
dot on the ribbon icon - green running, amber paused - is how the mode stays
discoverable once the panel is docked away and the alert dismissed. The dot is
drawn over whatever image the button currently has, so it is correct in both
Revit themes without shipping extra icons.

**Pause** stops watching and keeps the list; edits made while paused are never
queued, so paused costs exactly what off costs. **Resume** re-checks the
recorded pairs rather than replaying those edits: anything fixed during the
pause drops off, anything still clashing stays. Resume is disabled until the
mode is actually paused, and Pause is disabled while it already is.

**Edit Categories** reopens the setup window on the current selection - it is
available while paused - and applies the change without tearing down the
session. Pairs outside the new scope are dropped; the rest are re-tested and
kept only if still clashing.

Resume and Edit Categories are the same operation underneath,
`_revalidate_pairs`, drained through the normal per-tick budget so re-checking
the whole list never stalls Revit.

Rows carry a checkbox and a single **Show** at the foot of the list selects and
frames every ticked clash at once; double-clicking a row shows just that pair.
`Stop Detection` and `Pause` / `Resume` appear on the panel, the alert window
and the status window. Closing the monitored document stops the mode
automatically.

## Detection Flow

Two events, attached on Start and detached on Stop.

`Application.DocumentChanged` records which element ids the transaction
added, modified and deleted, and stamps the clock. Set unions only - no
document reads, no collectors, no geometry. Everything else is deferred,
matching the queue-then-drain pattern `temp_phase_close` already uses.

`UIApplication.Idling` does the work, gated in order: inactive -> return;
nothing queued -> return; last change less than `DEBOUNCE_SEC` (0.35s) ago ->
return, which coalesces a burst of edits into one pass. Then it processes at
most `MAX_ELEMENTS_PER_TICK` (25) items or `BUDGET_SEC` (0.04s) of wall
clock, whichever comes first, leaving the rest queued. Idling is also a valid
API context, so deferred `Show` requests and the alert window are raised
here rather than from a WPF click or from `DocumentChanged`.

Per element, the query orders filters by cost: the side's cached
`ElementMulticategoryFilter` and a padded `BoundingBoxIntersectsFilter` (both
quick filters served by Revit's spatial index) cut the candidate set down
before the exact filter runs. Same-model checks use
`ElementIntersectsElementFilter`; cross-model checks extract the element's
solids (recursing into `GeometryInstance`), transform them into the target
model's coordinates via `target.GetTotalTransform().Inverse * source`, and
use `ElementIntersectsSolidFilter`.

Because only elements changed since Start are ever tested, pre-existing
clashes are invisible by construction. A clash between a changed element and
an untouched one *is* reported - the user's edit created it.

`DocumentChanged` never fires for a link's own document, so link-side
geometry only re-enters play when the `RevitLinkInstance` moves. That drops
every pair referencing the link, refreshes its cached transform, and
re-queues the opposite side's elements inside the link's new bounding box -
bounded by that footprint, never by project size.

## Data Model

Everything lives in memory: module globals in `clash_detection_engine`,
mirrored into pyRevit envvars only so a pyRevit engine reload can find and
detach a stale delegate.

- Element identity: `"<model_ref>#<element_id>"`, where `model_ref` is
  `host` or `link:<instance id>`.
- Pair identity: the two element keys sorted and joined, so `A vs B` and
  `B vs A` are one record and a clash is never reported twice.
- A record holds both elements' category, family, type, id and model label.

Resolution is driven off the change set, so it costs O(changed), never
O(recorded): a deleted element drops its pairs; a modified element is
re-queried, and any previously recorded partner missing from the fresh
result set is dropped. A dropped pair can be detected again later, so
"move it away, move it back" reports a second time.

## Bounds

`MAX_QUEUE` 20000 (drop-oldest, counted and surfaced in the panel),
`MAX_PAIRS` 2000 (recording stops with a note), alert window capped at 50
rows. Nothing accumulates without a ceiling, and `stop()` clears all of it.
The queue is sized so a large paste is checked end to end rather than
half-dropped: a work item is a small tuple, and at 25 items per tick a full
queue still drains in seconds.

## Error Handling

Every Revit call is wrapped: elements without solid geometry make the exact
filter throw and are skipped; an unloaded link fails Start with a message
rather than half-arming; if Revit does not expose the events, Start fails
cleanly and nothing is left subscribed. If the host's pyRevit build has no
dockable-panel support, the same content opens as a modeless window pinned
to the right instead of failing.

## Testing

Pure state (`test_clash_detection_state.py`): pair-key normalization and
dedupe, resolve-on-delete and resolve-on-modify, re-detection after
resolution, queue cap drop-oldest, pair cap, debounce, per-tick budget,
target-side resolution for host/link/same-category pairings, bulk toggle,
and `clear()`.

Contract (`test_clash_detection_command_names.py`): bundle metadata, all
four XAML files parse, `x:Name`s and handlers resolve, both lists are
`Extended`, the start button replaces `OK`, the panel and its fallback
window expose identical contracts, and every `{Binding}` matches an
attribute the code actually sets.

Governance (`test_clash_detection_no_local_files.py`): no clash module can
open a file or import a filesystem writer; startup registers the pane;
doc-closing stops the mode; both events are detached; `_subscribe` detaches
before attaching; `_on_document_changed` contains no geometry or collector
call; `_on_idling` short-circuits on its first statement.

Manual Revit validation is listed in the implementation plan; the load-
bearing checks are that a new clash appears within a fraction of a second,
that pre-existing clashes never appear, that moving an element clear removes
its row, and that a 500-element move keeps Revit responsive.
