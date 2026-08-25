# 18 — Phase Balance

Every panel's A/B/C balance in one table, and a slot-swap planner that fixes
the worst one without deleting a single circuit.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 18 of 45 | Misc Tools — Circuiting pulldown | yes | M | 7/10 | 8/10 |

## Main purpose

A panel that drifts to 60/25/15 across A, B, and C gets flagged by the
reviewing engineer every single time, and rebalancing it in Revit means
dragging circuits between slots in the panel schedule one at a time while the
totals recompute under your cursor. Worse, *finding* which panels are out of
balance means opening every schedule and reading three numbers per board —
so on most jobs nobody looks until the review comment arrives.

Phase Balance is two honest halves. The report half reads every panel's own
per-phase current and load — the numbers its panel schedule already shows —
and lines all the boards up in one table with an imbalance percent, worst
first; panels whose phase parameters are missing or blank are "not
evaluable", shown as read, never guessed. The rebalance half is deliberately
narrower: for one panel at a time it proposes pairwise slot swaps between
same-pole-count circuits — moves that cannot change anyone's pole span —
shows projected totals under the standard slot-to-phase convention, and
executes only through `PanelScheduleView.MoveSlotTo`, one nested transaction
per swap inside one assimilated group, so a swap the template refuses rolls
back alone and is reported by circuit number. No circuit is ever deleted or
re-created; wiring, load names, and ratings survive untouched.

It earns rank 18 because the ground is commercial-only — EVOLVE Electrical
and Design Master sell load balancing; the Dynamo attempts that float around
reassign circuits by deleting and recreating them, which destroys wiring —
and because within EasyBIM the survey half is nearly free: the panels fall
out of the same `collect_circuits` snapshot Circuit Schedule and 01 Circuit
Check already read, and Circuit Check deliberately stops at per-circuit
arithmetic. Usefulness is 7 rather than 8 only because rebalancing is a
milestone task, not a daily one. It shares its slot-move core with 19
Circuit Renumber — whichever of the two builds second hoists that core to
`lib/easybim`.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Circuiting.pulldown/Phase Balance.pushbutton/`
  added to the pulldown's layout list. `script.py` thin, with
  `__persistentengine__ = True` — the window is modeless (Show buttons and
  Apply both ride `ExternalEventBridge`). `bundle.yaml` two-line title
  "Phase\nBalance", narrative tooltip stating both halves and the
  convention caveat, `author: Ruiming Liu`. Split: `phase_balance_state.py`
  (imbalance math, the swap proposer, projected totals, skip bucketing —
  pure Python), `phase_balance_revit.py` (panel/circuit snapshot, the
  per-view slot-mapping probe, the MoveSlotTo executor),
  `phase_balance_ui.py` + `PhaseBalanceWindow.xaml` and
  `PhaseBalanceReport.xaml`. The slot model (slot ↔ circuit ↔ poles ↔
  phase) and the view-mapping probe are written dict-in/dict-out as a
  candidate `lib/easybim/panel_slots.py` — 19 Circuit Renumber is the
  declared second consumer, and whichever builds second performs the hoist.
- **Revit API route** — Snapshot: `collect_circuits` from
  `lib/easybim/circuit_schedule_revit` (`OST_ElectricalCircuit`), each cast
  to `Electrical.ElectricalSystem`; group by `BaseEquipment` id — a panel
  is any equipment some circuit calls home. Per panel, per-phase values
  come off the ElectricalEquipment's built-in parameters resolved with the
  house `getattr(DB.BuiltInParameter, name, None)` probe over a candidate
  name list (`RBS_ELEC_PANEL_CURRENT_PHASEA_PARAM` and its B/C and load
  siblings — the names shift across releases, which is exactly why the
  probe list is data, not code). Two channels, never mixed: `AsDouble()` in
  internal units feeds the imbalance math, `AsValueString()` feeds the
  table. Per circuit read poles, `CircuitType` (spares and spaces are named
  skips before planning starts), and `StartSlot` behind `hasattr` — where
  `StartSlot` is unreadable the panel is report-only, stated. Planning
  needs no schedule view: the slot model builds from `StartSlot` + poles.
  Execution does: find the panel's `PanelScheduleView` by `GetPanel()`, or
  create one with `PanelScheduleView.CreateInstanceView` as the first
  nested transaction, disclosed in the plan ("panel has no schedule view —
  one will be created"). Then the mapping probe walks the view's body cells
  with `GetCircuitByCell`, builds slot ↔ (row, col), and round-trips it
  against `StartSlot`; a template the probe cannot verify fails that panel
  closed to report-only — no swap runs on a mapping that is guessed.
  Locked slots are probed through the `IsSlotLocked`-shaped accessors with
  `hasattr`; where lock state is unreadable the slot is not moved, named.
  A swap of two occupied slots is three `MoveSlotTo` hops through a free
  slot; all three live inside that swap's one nested `Transaction`, inside
  one assimilated `TransactionGroup` per Apply — a refused hop rolls back
  its whole swap, never the batch, and counters zero on rollback.
- **The plan/apply cycle** — `build_plan` runs per selected panel: it
  computes current per-phase totals, then greedily proposes same-pole-count
  pairwise swaps that reduce `100 × (max_phase − avg) / avg` (the formula
  is printed in the header so the number is never a mystery), stopping at
  a target threshold or when no swap helps. Each swap is one plan row —
  "swap 7 with 12 — moves 1.2 kVA from A to B" — individually declinable
  ("skipped — declined, never failed"), with before/after projected totals
  labelled "projected (standard slot convention)", because slot-to-phase is
  standard but not universal and the projection says so. The staged rows
  render red until Apply; Apply stays disabled until the acknowledgement
  tick "Circuit numbers on this panel will change." is set, since a slot
  move renumbers the circuit and that number is on issued paper. After
  commit the Report window re-reads the panel's per-phase parameters from
  the model — Revit's own recomputed totals, not the projection — and
  lists every swap as committed or rolled back by circuit number.
- **Edge cases & honest limits** — Named buckets: "not evaluable — phase
  parameters missing/blank", "single-phase panel — A/B only" (two-column
  math, C shown as "—"), "not applicable — one phase", "spare", "space",
  "slot locked", "lock state unreadable — not moved", "template mapping
  unverified — report only", "slot numbers unreadable — report only",
  "panel full — no free slot to stage a swap through", "declined". The
  report half never depends on the slot-to-phase convention — it reads the
  panel's own numbers; only the *projection* uses the convention, and it is
  labelled. The tool does not rebalance across panels, does not touch
  feeders, and does not judge demand factors — it moves branch circuits
  between slots of one board, and the tooltip says exactly that.
- **Risks** — `MoveSlotTo` works in table coordinates that differ per
  `PanelScheduleTemplate`; the per-view probe with the `StartSlot`
  round-trip is the defense, and fail-closed-to-report-only is the rule
  when it cannot verify. The per-phase parameter names drift across
  releases — the candidate-list probe plus the "not evaluable" bucket keep
  a missing name from reading as zero. The greedy swap proposer must be
  deterministic (stable ordering, pinned tie-breaks) or two runs propose
  different plans on the same model. Creating a schedule view as a side
  effect surprises users if undisclosed — it is a plan row, never silent.
  Division by a zero average (an empty panel) must route to "not
  evaluable", not a crash or an infinite percent.
- **Tests** —
  - `test_phase_balance_state.py` pins the imbalance formula at boundaries
    (zero average, single-phase, exactly-at-threshold), swap-proposer
    determinism and pole-count pairing, projected-total arithmetic, the
    decline path, and every named bucket's classification.
  - `test_phase_balance_command_names.py` pins bundle metadata and pulldown
    layout, XAML↔handler wiring for both windows, 96×96 icon pairs,
    `__persistentengine__`, the IronPython AST scan, forbidden-API pins.
  - `test_phase_balance_revit.py` drives the adapter against fakes per API
    generation: phase parameters present/absent/blank, `StartSlot` missing,
    a mapping probe whose round-trip fails, the lock accessor missing, a
    `MoveSlotTo` refusal rolling back one swap and zeroing its counter, and
    the create-view-then-probe path.

## UI description

**Main window** — modeless, resizable, header "Phase Balance" over the
DimGray subtitle "Read from each panel's own totals. Projections assume the
standard slot-to-phase convention and say so." Left card, "Panels": the
all-panels table — Panel, A, B, C (display text), Imbalance % with the worst
rows tinted, and a **Show** button per row — over a count line
("38 panels — 6 above 20%.") and a **Refresh** button. Not-evaluable and
single-phase rows sit greyed in place with their reason, never hidden.
Selecting a row loads the right card, "Rebalance {panel}": the slot map
drawn as the familiar two-column strip (multi-pole spans drawn joined,
spares/spaces/locked slots greyed with their bucket), the proposed swaps
staged in red as checkbox rows — "swap 7 with 12 — moves 1.2 kVA from A to
B" — and a header line with before → projected totals. Footer: status left,
the acknowledgement tick "Circuit numbers on this panel will change.", then
**Apply** (`IsDefault`, disabled until ticked, reason in tooltip) and
**Close** (`IsCancel`).

> "38 panels read — 6 above 20% imbalance, 4 not evaluable, 2 single-phase."

> "LP-2: 3 swaps staged, projected imbalance 4% (standard slot convention). 2 slots locked — not moved."

**Report window** — read-only WPF table after commit: Swap, Circuits (by
panel + number), Result — committed or "rolled back: template refused the
move" — and the panel's per-phase totals re-read from the model. Footer:

> "LP-2: 2 swaps committed, 1 rolled back. Re-read from the model: A 41.2 A, B 39.8 A, C 40.5 A — imbalance 6%."

### User operation flow

1. Ribbon: Misc Tools → Circuiting → Phase Balance. The snapshot pass runs
   through the bridge; the Panels table fills, worst imbalance first.
2. Press **Show** on a suspect row to see the board in the model; pick the
   row to plan. The right card draws its slot map and stages the proposed
   swaps in red.
3. Untick any swap you disagree with — it lands under "skipped — declined,
   never failed". Watch the projected totals update.
4. Tick "Circuit numbers on this panel will change." **Apply** enables.
5. Apply commits one TransactionGroup (one undo step): create the schedule
   view if the plan disclosed it, verify the slot mapping, run each swap as
   three hops in its own nested transaction. A refused swap rolls back
   alone and moves to the skip ledger.
6. The Report window opens with phase totals re-read from the committed
   model. A skipped item looks like: "swap 3/5 — skipped: slot 17 locked";
   a failed one like: "swap 7/12 — rolled back: template refused the move."
7. Close the report; the Panels table refreshes with the new numbers. Work
   the next board, or press Ctrl+Z once in Revit to revert the whole panel.
8. Cancel path: **Close** (or Esc) before Apply — nothing was written; a
   panel that failed its mapping probe was report-only the whole time and
   its Apply never enabled (reason in the tooltip).

## See also

- Existing: **Update Circuit Rating** (the per-item nested-rollback shape,
  applied here per swap), **Circuit Schedule** (the `collect_circuits`
  snapshot and the panel-grouping this table is built from).
- Plan siblings: **19 Circuit Renumber** — the same `panel_slots` core
  (slot model, mapping probe, MoveSlotTo executor); whichever builds second
  hoists it to `lib/easybim`. **01 Circuit Check** — per-circuit QA on the
  same snapshot; it flags, this rebalances. **34 Spare Capacity** — the
  panel-survey sibling (fill and open slots where this reads phases).
  **16 Panel Sheets** — the other tool that creates missing
  `PanelScheduleView`s; share the create-view step's shape.
