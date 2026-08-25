# 19 — Circuit Renumber

Scheme-driven renumbering of panel slots — odd/even sides, walk order,
grouped loads, spares at the bottom — with every hop planned before one runs.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 19 of 45 | Misc Tools — Circuiting pulldown | yes | M | 8/10 | 7/10 |

## Main purpose

Circuit numbers drift as the design changes: panels end with gaps,
receptacles scattered across slots in placement order, spares stranded in
the middle, and multi-pole circuits landing wherever there was room the day
they were drawn. Office standards — odd/even sides, loads grouped by room or
classification, spares gathered at the bottom — are real, but the only way
Revit lets you enforce one is dragging circuits in the panel schedule one
slot at a time, each drag recomputing the board under your cursor.

Circuit Renumber plans the whole permutation first. It reads each selected
panel's slot occupancy the way Phase Balance does — from `StartSlot`, poles,
and `CircuitType`, no schedule view needed to plan — and computes a legal
target slot per circuit under a named scheme: a 2- or 3-pole circuit must
land on consecutive slots in one column with the phase sequence the panel
provides, and the state layer owns that legality model. Because a slot you
want is usually occupied, the plan includes the move order explicitly: each
permutation cycle stages through a free slot span, and every hop appears in
the confirmation grid, so what runs is exactly what was shown. Apply commits
one undo step with one nested transaction per panel — a half-renumbered
board is worse than an untouched one, so a refused move rolls back its
whole panel alone, unlike Phase Balance's self-contained per-swap rollback.

This is the curator-authored idea filling a gap the prior-art survey named
explicitly: configurable circuit renumbering is commercial-only today
(EVOLVE, Naviate); the free ecosystem has nothing that moves slots rather
than deleting and recreating circuits. Within EasyBIM the lane is clear —
Update Circuit Rating writes ratings, 06 Load Names writes words, 18 Phase
Balance moves circuits for load reasons; this renumbers to a standard. It
shares the slot-move core with Phase Balance, and whichever of the two
builds second hoists that core to `lib/easybim`. Impact is 7 rather than 8
only because the numbers themselves are cosmetic — but they are cosmetic on
every issued sheet, which is why the acknowledgement tick and the
before/after Excel record are mandatory, not decoration.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Circuiting.pulldown/Circuit Renumber.pushbutton/`
  added to the pulldown layout. `script.py` thin; the window is modal and
  short-lived (no Show buttons, no bridge, no persistent engine — the write
  is one command, like Load Names). `bundle.yaml` two-line title
  "Circuit\nRenumber", narrative tooltip naming the blast radius,
  `author: Ruiming Liu`. Split: `circuit_renumber_state.py` (scheme
  engine, slot legality model, cycle decomposition and hop planning, skip
  bucketing — pure Python), `circuit_renumber_revit.py` (snapshot, mapping
  probe, MoveSlotTo executor), `circuit_renumber_ui.py` +
  `CircuitRenumberWindow.xaml` and `CircuitRenumberReport.xaml`,
  `circuit_renumber_xlsx.py` (before/after export through
  `lib/easybim/excel_workbook`). The slot model and view-mapping probe are
  the `panel_slots` core shared with 18 Phase Balance — dict-in/dict-out,
  hoisted to `lib/easybim/panel_slots.py` by whichever tool builds second.
  Scheme presets persist by name through `script.get_universal_data_file`
  (roaming, Revit-version-independent — the Tag Align / My Ribbon
  precedent), portable across projects because nothing in a preset is an
  ElementId.
- **Revit API route** — Circuits via the lib `collect_circuits`
  (`OST_ElectricalCircuit`), cast to `Electrical.ElectricalSystem`, grouped
  by `BaseEquipment`; per circuit read `StartSlot` (`hasattr`-guarded —
  unreadable makes the panel report-only, stated), poles from
  `RBS_ELEC_NUMBER_OF_POLES`, `CircuitType` (spares and spaces are their
  own scheme lever, never silently moved), load-classification and load
  name as display text via the house `getattr(DB.BuiltInParameter, ...)`
  probe. The room/level walk order reuses the Load Names walk: each
  circuit's fed `Elements` once, `get_Space(phase)` first with `get_Room`
  fallback, an explicit phase ComboBox, and the deterministic majority-wins
  tie rule — if 06 builds first this imports its walk helper, otherwise 06
  hoists this one later. Execution mirrors Phase Balance exactly: find the
  panel's `PanelScheduleView` by `GetPanel()` (create one via
  `CreateInstanceView` as a disclosed plan step if absent), probe the slot
  ↔ (row, col) mapping with `GetCircuitByCell` and round-trip it against
  `StartSlot`, fail that panel closed to report-only if unverified, then
  run `MoveSlotTo` hop by hop. Locked slots probed via the
  `IsSlotLocked`-shaped accessors; unreadable lock state means the slot is
  never a source or a target. Transaction shape: one assimilated
  `TransactionGroup` per Apply, one nested `Transaction` per panel holding
  all of that panel's hops — a refused hop rolls back the panel alone;
  counters zero on rollback. Long batches write under a cancellable
  `forms.ProgressBar`, per panel.
- **The plan/apply cycle** — `build_plan` takes the snapshot plus the
  scheme (walk order: by room number / by level then room / by current
  order; odd/even side assignment; group by load classification; spares
  kept in place or gathered at the bottom) and emits, per panel: circuit
  identity (panel name + number — never ElementId), current slot, target
  slot, and the explicit hop list from cycle decomposition — each cycle
  staged through a free slot span legal for its largest circuit. A panel
  with no legal staging span skips that cycle's circuits by name ("no free
  slot span to stage through — free a slot and re-run"). One plan object
  feeds both the staged grid and the executor, so preview and write cannot
  drift. Apply is disabled — never hidden — until the acknowledgement tick
  "Circuit numbers on issued drawings will change." is set. The Report
  window re-reads the slot tables from the committed model and shows final
  numbering per panel, with **Export to Excel** writing the before/after
  record.
- **Edge cases & honest limits** — Named buckets: "already in place",
  "slot locked", "lock state unreadable — not moved", "pole/phase
  impossibility" (a 3-pole on a single-phase board, an odd-count target),
  "no free slot span to stage through", "spare/space — kept in place by
  scheme", "template mapping unverified — panel report-only", "slot
  numbers unreadable — panel report-only", "declined", "unchecked". The
  walk order's `{room}` reality is inherited from Load Names and stated in
  the subtitle: Spaces are the honest source; rooms live in the
  architectural link and are not reached in v1. The tool never renumbers
  across panels, never changes which panel feeds a circuit, and never
  touches wiring — it permutes slots on one board at a time, and says so.
- **Risks** — The template-coordinate wall is shared with Phase Balance:
  `MoveSlotTo` speaks per-template table coordinates, and the per-view
  probe with fail-closed-to-report-only is the only safe posture — share
  the probe, do not fork it. The multi-pole legality model is the heaviest
  correctness load: it depends on the panel's phase count and column
  layout, and a wrong model plans an illegal move the API will refuse
  mid-permutation — which is exactly why rollback is per panel, not per
  hop. Renumbering is high-blast-radius on issued sets (tags, schedules,
  keyed notes all carry the number): the acknowledgement tick and the
  Excel before/after export are load-bearing. Walk-order determinism must
  be pinned (stable sorts, the majority/tie rule) or two runs on the same
  model produce different boards. The element walk for room order is the
  slow path — one pass, cached in the plan, progress in the status line.
- **Tests** —
  - `test_circuit_renumber_state.py` pins the legality model (2- and
    3-pole spans per phase count and column layout), each scheme's target
    assignment, cycle decomposition and hop counts, the no-staging-span
    skip, walk-order determinism with ties, preset round-trip by name, and
    counters zeroing on a panel rollback.
  - `test_circuit_renumber_command_names.py` pins bundle metadata and
    pulldown layout, XAML↔handler wiring for both windows, 96×96 icons,
    the IronPython AST scan, and forbidden-API pins.
  - `test_circuit_renumber_revit.py` drives the adapter against fakes per
    API generation: `StartSlot` present/absent, mapping round-trip pass
    and fail, lock accessor missing, a mid-permutation `MoveSlotTo`
    refusal rolling back the whole panel, the create-view path, and the
    phase-indexed space/room walk.
  - `test_circuit_renumber_xlsx.py` pins the before/after export rows and
    header order.

## UI description

**Main window** — resizable modal, header "Circuit Renumber" over the
DimGray subtitle "Plans every hop before one runs. Spaces are read for walk
order; rooms in linked models are not visible here." Two cards side by
side. Left card, "Scheme": a ComboBox of saved presets with Save / Delete,
then the levers — walk order ComboBox (by room number / by level then room
/ by current order), phase ComboBox for the space lookup, checkboxes for
"odd/even sides", "group by load classification", and a spares ComboBox
(keep in place / gather at bottom). Right card, "Panels": checkbox list
with search and the count line "12 panels — 3 checked, 9 unchecked.",
Select All / Select None; report-only panels sit greyed with their reason.
Below both cards, the staged grid: Panel, Ckt, Load Name, Current #,
New # (red until Apply), Hops — skip rows greyed with their bucket named.
Footer: status left, the acknowledgement tick "Circuit numbers on issued
drawings will change.", then **Apply** (`IsDefault`, disabled until ticked,
reason in tooltip) and **Cancel** (`IsCancel`).

> "3 panels — 64 moves planned (9 hops through temp slots), 5 skipped."

> "RP-1 is report-only: template mapping unverified. Its rows are greyed, not staged."

**Report window** — read-only WPF table after commit, grouped per panel:
Ckt (identity by load name + old number), Final # read back from the model,
Result. Rolled-back panels listed whole, under their reason. Buttons:
**Export to Excel** (the before/after record), **Close**. Footer:

> "LP-2 renumbered — 24 circuits read back. RP-1 rolled back: template refused the move at slot 17. One undo step."

### User operation flow

1. Ribbon: Misc Tools → Circuiting → Circuit Renumber. The snapshot runs;
   the Panels card fills, report-only boards greyed with reasons.
2. Pick or build a scheme; pick the phase if the default is wrong. Check
   panels — search filters visibility without losing checks.
3. The staged grid fills red: every circuit's current and new number, with
   hop counts where a cycle stages through a free slot. Review; untick any
   panel or decline rows you disagree with ("skipped — declined").
4. Tick "Circuit numbers on issued drawings will change." Apply enables.
5. Apply runs per panel under the cancellable progress bar, one nested
   transaction each inside one assimilated group. Cancelling stops the
   remaining panels; what committed stands as one undo step and the report
   says "cancelled after 2 of 3 panels".
6. The Report window opens with final numbering re-read from the model. A
   skipped item looks like: "LP-2 / 7 — skipped: slot 9 locked"; a failed
   panel reads whole: "RP-1 — rolled back: template refused the move."
7. **Export to Excel** for the record — the before/after sheet is what the
   markup set and the tag-checking pass work from. Close; one Ctrl+Z in
   Revit reverts every panel the run touched.
8. Cancel path: **Cancel** (or Esc) before Apply closes the window with
   the model untouched — nothing is written during planning, ever.

## See also

- Existing: **Update Circuit Rating** (Circuiting sibling; note the
  contrast — its rollback is per circuit, this one is deliberately per
  panel), **Circuit Schedule** (`collect_circuits` and the panel + number
  identity rule).
- Plan siblings: **18 Phase Balance** — the shared `panel_slots` core and
  mapping probe; whichever builds second hoists it. **06 Load Names** —
  donor of the element walk, phase ComboBox, and majority/tie rule; words
  where this is numbers. **21 Circuit Excel** — the portable panel+number
  identity this tool must not silently break, and the round-trip that
  carries the new numbers to the engineer. **31 Detail Renumber** — the
  same two-pass-around-an-occupancy-constraint pattern applied to sheet
  viewports. **16 Panel Sheets** — shares the create-missing-
  `PanelScheduleView` step.
