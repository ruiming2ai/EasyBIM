# 14 — Clash Sweep

Gives Clash Detection Mode the day-one backlog and statuses that survive to
next week's coordination meeting.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 14 of 45 | Misc Tools (inside Clash Detection Mode) | yes | M | 8/10 | 8/10 |

## Main purpose

Clash Detection Mode is forward-only by design: only elements that change
after Start ever enter its queue, so the four hundred clashes already sitting
in the model on day one are exactly the ones it will never mention. Teams run
Navisworks or native Interference Check for the backlog and the live mode for
the delta — two lists that share no identity — and neither carries a status
that survives to next week's meeting, so every session re-litigates clashes
someone already approved.

Clash Sweep extends the existing mode with exactly the two things it lacks:
an initial whole-model pass, and triage that persists. The sweep feeds the
session's existing queue from a one-time collector pass over both sides
instead of from `DocumentChanged`, drains through the same bounded Idling
slices, and lands its results in the same dockable panel — one list, one
`pair_key` identity, each row tagged *pre-existing* or *live*. Triage gives
every pair a status (Active / Reviewed / Approved, plus a note) and round-trips
it through an Excel workbook keyed portably — model, category, family and
type names, level, nearest-grid fingerprint — with ElementIds carried only as
visible keys, so last week's approvals survive a workshared week in which
every ElementId changed hands. Nothing is ever written to the model.

The prior-art survey is blunt about where the value sits: lightweight
persistent clash tracking inside Revit — status, re-check history, no
Navisworks seat — is served by nobody free. Native Interference Check has no
statuses and no link-pairing UX; the free pyRevit ecosystem has one-shot
lists. The sweep itself is the enabler, but **the status round-trip is the
differentiator**, and the design below treats it as the headline. Effort is M
because the pair engine, panel, and bounded-tick discipline already exist and
are reused verbatim; the rank sits just below the new-tool leaders only
because it improves an existing surface rather than opening a new one.

## Basic implementation ideas

- **Bundle & module layout** — the one idea on this list that adds no
  pushbutton: entry points are the existing Clash Detection Mode setup window
  and dockable panel, both grown in place. New lib modules (everything here
  already lives in `lib/easybim/` because the panel registers from startup):
  `clash_sweep_state.py` (pure — candidate estimator, seed/refill
  bookkeeping, fingerprint build and match, status transitions) and
  `clash_status_xlsx.py` (export/import rows). The flat-table xlsx writer
  currently lives pushbutton-local in Sheet Manager's `sheet_manager_xlsx.py`
  (`export_table_to_xlsx`); this is its second consumer and the panel cannot
  import a pushbutton, so the writer hoists to lib and Sheet Manager repoints
  — the read side already exists as `excel_workbook.read_workbook_sheets`.
  Grown in place: `clash_detection_state.py` (`ClashRecord` already stamps
  `status = STATUS_ACTIVE`; add `STATUS_REVIEWED`/`STATUS_APPROVED`, a note
  field, and an `origin` tag), `clash_detection_engine.py` (seed pass +
  drain), `clash_detection_setup.py` (Sweep button + pre-flight line),
  `clash_detection_panel.py` (status column, origin column, Import/Export).
- **Revit API route** — the seed pass is one `FilteredElementCollector` per
  side context — host document and each checked link's `GetLinkDocument()` —
  `WherePasses` the side's existing `ElementMulticategoryFilter`,
  `WhereElementIsNotElementType()`, ids only. Those ids enter the session as
  ordinary work items tagged pre-existing and drain through the existing
  Idling delegate under the same bounds (`MAX_ELEMENTS_PER_TICK` = 25,
  `BUDGET_SEC` = 0.04 s), so a 100k-element sweep is minutes of background
  slices and Revit stays usable. The pair test is unchanged: `BBOX_PAD`
  padded `Outline` + `BoundingBoxIntersectsFilter` prefilter, then
  `ElementIntersectsElementFilter` (same document) or
  `ElementIntersectsSolidFilter` on the transformed solid (link), `pair_key`
  dedupe so a pair found by the sweep and again by a live edit records once.
  One queue discipline, not two: live `DocumentChanged` items drain first,
  sweep backlog after, so the resident mode's latency promise holds; and
  because `MAX_QUEUE` (20,000) may be smaller than the model, the seeder
  holds the full id list in `clash_sweep_state` and refills the queue as it
  drains — nothing is ever dropped, and the remaining-id bookkeeping mirrors
  into the pyRevit envvar store beside the session so an engine recycle
  cannot silently truncate a sweep. No Transaction anywhere (pinned); the
  mode stays read-only.
- **The scan/report cycle** — read-only, so the cycle is estimate → sweep →
  triage → round-trip. Before anything starts,
  `clash_sweep_state.estimate(side_counts, occupancy)` prices the job from
  per-side category counts hashed into a coarse overlap grid (~10 ft cells,
  sum of per-cell a×b products); the setup window shows the number, and past
  a hard ceiling the sweep refuses with it — "Everything vs Everything is a
  Navisworks job", fail closed rather than trying. Results stream into the
  panel as they are found, `MAX_PAIRS` (2,000) note honoured. Re-running the
  sweep re-seeds; a recorded pair still clashing keeps its status, one no
  longer intersecting resolves and counts as resolved — statuses survive
  re-check, which is the whole point. Export writes one row per pair through
  `clash_status_xlsx`; import matches rows by fingerprint and ends in a load
  report where every unmatched row is named, never silently dropped.
- **Edge cases & honest limits** — fingerprint identity has a real collision
  case: two identical fittings clashing the same beam type on the same level
  near the same grid intersection. A row whose fingerprint matches two live
  pairs degrades to *"matched ambiguously — listed"*, applied to neither;
  guessing is worse than asking. Models without grids fall back to a
  rounded-coordinate fingerprint and the export header says so — weaker
  identity, stated. Unloaded links, elements with no bounding box, and
  elements in non-primary design options are named buckets in the sweep
  summary, not silent absences. Statuses live in the session and the
  workbook only — the panel subtitle says "Statuses are not written to the
  model"; Stop with unexported non-Active statuses raises the native-style
  TaskDialog with a verification checkbox before they are lost. What the
  tool refuses: sweeping unbounded past the ceiling, and inventing a match
  for an ambiguous fingerprint.
- **Risks** — the estimator must be honest *before* work starts or users
  will launch sweeps the ceiling should have refused; keeping it cheap
  (counts × grid occupancy, no geometry) while erring high is the tricky
  bit — an over-estimate that refuses is recoverable, an under-estimate that
  hangs is not. Sweeping while the live mode also queues edits is one queue
  discipline or none: the drain-order rule and the shared `pair_key` dedupe
  are what prevent double records and starvation, and both need explicit
  tests. The envvar-mirrored seeder state must survive an engine recycle the
  way the session already does, or a recycle mid-sweep reports "done" at
  60%. Fingerprint drift (family renamed between export and import) lands
  those rows in unmatched — correct, but the load report must make the
  rename cause guessable by showing the row's names.
- **Tests** — `test_clash_sweep_state.py` pins the estimator (counts,
  occupancy, ceiling refusal), seed/refill discipline (queue never exceeds
  `MAX_QUEUE`, nothing dropped, live-first ordering), status transitions
  surviving re-check and resolution, and fingerprint build/match/ambiguity
  including the no-grids fallback. `test_clash_sweep_command_names.py` pins
  the grown setup/panel XAML↔handler wiring, the new buttons and columns,
  the IronPython AST scan over the new lib modules, the no-Transaction pin —
  and that the whole existing `test_clash_detection_*` suite still passes
  unmodified. `test_clash_sweep_revit.py` drives the seed pass over fakes
  shaped like each API generation — link contexts with transforms,
  multicategory-filter fallback, no-bbox elements, recycle-then-resume from
  mirrored state. `test_clash_status_xlsx.py` pins export column order and
  the import load-report buckets (matched / unmatched / ambiguous).

## UI description

**Setup window** (existing, grown) — the Interference-Check-shaped window
gains one button beside Start: **Sweep Model Now**, disabled — never hidden —
with the reason in its tooltip until both sides validate, and a DimGray
pre-flight line beneath the side cards that updates as categories are
checked:

> "≈ 41,000 candidate pairs — estimated 2–4 min in the background."

> "≈ 8.4 M candidate pairs — beyond the sweep ceiling. Narrow the categories; Everything vs Everything is a Navisworks job."

**Clash panel** (existing dockable pane, grown; modeless right-edge fallback
unchanged) — each clash row gains an origin tag column (*live* /
*pre-existing*) and a status ComboBox (Active / Reviewed / Approved) with the
note in the row's detail line; the toolbar gains **Export Statuses** and
**Import Statuses**. Footer status during a sweep, ticking per Idling slice:

> "Sweep: 12,400 of 38,000 checked — 214 clashes so far. Revit stays usable."

> "Sweep complete: 38,000 checked, 291 pre-existing clashes recorded, 2 links skipped (unloaded — named)."

**Load report window** — read-only WPF table after an import, never a stack
of message boxes: matched rows with the status they received, then the named
buckets. Footer:

> "Matched 96 — 4 unmatched (renamed since export?), 2 ambiguous (applied to neither, listed)."

**Stop prompt** — the existing Stop path grows a native-style TaskDialog
with a verification checkbox when non-Active statuses would be lost:
"14 reviewed/approved statuses are not exported. Stop anyway?"

### User operation flow

1. Ribbon: Misc Tools → Clash Detection Mode. Set up both sides as today;
   the pre-flight line prices the sweep as categories are checked.
2. Press **Sweep Model Now** (with or without also pressing Start for the
   live mode). The panel opens and fills as slices drain; Revit stays
   responsive throughout.
3. Triage in the panel: set Approved on the sprinkler-vs-beam pairs the
   structural engineer accepted, Reviewed on the ones assigned out, notes as
   needed. Live clashes from ongoing edits interleave, tagged *live*.
4. **Export Statuses** before sync — one workbook, name-keyed, ids only as
   visible keys.
5. Next session (new ElementIds, new teammate): sweep again, **Import
   Statuses**. The load report names every row it could not place; a skipped
   item looks like "L3 duct 600×400 vs W21×44 — matched ambiguously (2
   candidates) — applied to neither" or "unmatched — no live pair carries
   this fingerprint".
6. Re-checks resolve pairs that no longer intersect; their rows leave the
   list and the resolved count ticks — approvals on still-live pairs
   survive.
7. Cancel path: Pause or Stop at any time — the sweep stops at the next
   slice boundary and the summary names what was not visited. Stop warns
   once about unexported statuses; nothing was ever written to the model,
   so there is nothing to undo.

## See also

- Existing: **Clash Detection Mode** (this is its missing half — same
  engine, same panel, same bounded ticks; the design context records it as
  "live, forward-only" and this removes the second word), **Sheet Manager**
  (donor of the flat-table xlsx writer this hoists to lib).
- Siblings: **13 Sleeve Place** (turns the structural crossings this finds
  into sleeves before they become clashes), **08 Warnings Watch** (the same
  persist-across-sessions instinct applied to Revit warnings; the two tools'
  portable-identity fingerprints should rhyme), **04 Open Ends** (the other
  read-only coordination sweep).
