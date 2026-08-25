# 34 — Spare Capacity

Every board's mains, demand, and open poles side by side — "is there room
on LP-2?" answered before the coordination call moves on.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 34 of 45 | Misc Tools — Circuiting pulldown | yes | S | 8/10 | 7/10 |

## Main purpose

The question every coordination call asks — "is there room on LP-2?" —
takes five clicks per panel to answer: open the schedule, read the mains,
read the demand total, count the open slots. Multiply by forty panels and
nobody actually checks; equipment gets circuited to whichever panel is
nearest until one trips over its main at commissioning. Native panel
schedules show one board at a time, so the model has the answer forty
times over and no place to read it once.

Spare Capacity is that one place, and it never writes. It rides the same
one-pass collector Circuit Schedule uses and shows, per board: mains
rating, total connected and estimated demand, poles consumed (circuits
plus spares and spaces), and the two derived numbers people actually want
— percent of main used by demand, and open poles remaining. The arithmetic
lives in pure state code over doubles read in internal units; any figure
that does not read lands under "not evaluable — shown as read" with its
raw display text, never a guessed zero. A user-set threshold (default 80%)
tints the boards worth worrying about, a "Group by feeder" toggle re-nests
the table using Circuit Schedule's existing tree engine so a sub-board
indents under its source, and every row has a Show button. Export rides
`excel_workbook`, boards identified by name, ElementId as a visible key
only.

Circuit Schedule answers "what feeds what"; this answers "how full is it",
which the tree deliberately does not compute. It is the read-only
companion the zero-amp report established the pattern for, and the
S-effort makes it nearly free: the collector, the feeder edges, the tree
engine, and the select-and-zoom helper all exist in `lib/easybim` already
— the whole new surface is one snapshot extension, one state module, and
one window. Usefulness 8 because the question recurs weekly on any
electrical job; impact 7 because the tool prevents the overload surprise
but leaves the fix to the engineer.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Circuiting.pulldown/Spare
  Capacity.pushbutton/`, added to the pulldown's `layout:`. `script.py`
  thin with `__persistentengine__ = True` — the window is modeless, and
  Run/Refresh/Show ride `ExternalEventBridge`. `bundle.yaml` (two-line
  title "Spare\nCapacity", narrative tooltip naming what is read and what
  is refused, `author: Ruiming Liu`), 96×96 icons. Split:
  `spare_capacity_state.py` (derived figures, bucket routing, feeder
  derivation, threshold logic — zero Revit imports),
  `spare_capacity_revit.py` (the board pass), `spare_capacity_ui.py`,
  `SpareCapacityWindow.xaml`, `spare_capacity_xlsx.py`. Reuse from
  `lib/easybim`: `circuit_schedule_revit.collect_circuits` and
  `scan_model` (the membership and feeder edges), the generic half of
  `circuit_schedule_state` — `Node`, `build_tree`-style nesting, `tokens`,
  search, expansion state — for the feeder grouping, `show_elements`,
  `ExternalEventBridge`, `compat`. The board-membership probe ("owns a
  `PanelScheduleView`") is the declared hoist candidate: 16 Panel Sheets
  wants the same inventory, and whichever builds second lifts it to lib.
- **Revit API route** — Boards are the union of two memberships: every
  `BaseEquipment` of a collected circuit, plus every equipment returned by
  `FilteredElementCollector` `OfClass(Electrical.PanelScheduleView)` →
  `GetPanel()` — the second test is what catches the freshly placed empty
  board. Per board, two value channels, never mixed (the 01 Circuit Check
  rule): judging reads `AsDouble()`/`AsInteger()` in internal units,
  display shows `AsValueString()`. Figures per board, every
  BuiltInParameter resolved through the house `getattr(DB.BuiltInParameter,
  name, None)` probe: mains from `RBS_ELEC_PANEL_MAINS_PARAM`, MCB rating
  from `RBS_ELEC_PANEL_MCB_RATING_PARAM`, the VA totals from
  `RBS_ELEC_PANEL_TOTALLOAD_PARAM` / `RBS_ELEC_PANEL_TOTALESTLOAD_PARAM`,
  the current-shaped totals where the release carries them (the probe list
  is data, not branches — a missing member degrades that one figure), and
  max single-pole breakers from `RBS_ELEC_PANEL_MAX_POLE_BREAKERS_PARAM`.
  Per circuit the tool reads its own numeric sidecar — poles `AsInteger`,
  `CircuitType` (Circuit / Spare / Space) behind a `hasattr` probe —
  rather than widening the lib snapshot, whose fields stay display-only by
  design. Fed From derives from the snapshot alone: the circuit whose
  `element_ids` contains this board names the feeder panel, with
  `RBS_ELEC_PANEL_SUPPLY_FROM_PARAM` display text as fallback. Percent of
  main divides demand current by mains only when both read as doubles in
  amps; the tool refuses to derive amps from VA totals — that is voltage
  and phase math it does not do, and the cell says why instead. No writes,
  so no Transaction of any kind — a pin the command-names test enforces.
- **The scan/report cycle** — read-only: scan → derive → report.
  `spare_capacity_revit.scan_model` returns one snapshot — board dicts
  (name, id, figures as raw doubles plus display text) and circuit dicts
  (board id, poles, circuit type) — nothing but ints, floats, and unicode.
  `spare_capacity_state.evaluate(snapshot, threshold)` computes per board:
  poles consumed, open poles, percent of main, the feeder edge, and a
  bucket for every figure that did not read. Refresh re-reads the live
  model, zero-amp style: the table answers "how full is it now", and a
  board fixed since the last run simply reads better.
- **Edge cases & honest limits** — buckets are per figure, not per board:
  a panel with unreadable demand still shows its true pole count, and the
  unreadable cell renders an em-dash with the raw text in a tooltip and a
  row in the "Not evaluable" expander. Named cases: *"max breakers not
  set on the family — open poles not computed"* (common on switchboards);
  *"no demand current on this release — % of main not computed"*;
  *"empty board — no circuits"* (listed, not hidden — it is the emptiest
  panel of all); spares and spaces consume poles and are counted as such;
  multi-section boards read as their single equipment instance, stated.
  The tool shows connected and demand exactly as the model computed them —
  it applies no diversity, no continuous-load factors, and no code rules
  (NEC 220 is engineering judgment, and the tooltip names it out of
  scope). It never suggests which panel to use; it shows the numbers and
  leaves the choice where it belongs.
- **Risks** — Panel total parameters vary by Revit release and by whether
  load classifications are assigned: resolution by name with per-figure
  degradation is the whole defense, and a missing parameter must render as
  blank-with-reason, never zero. The brainstorm's locale-parsing trap
  (percent math on display text) is closed by adopting Circuit Check's
  two-channel rule outright — no display string is ever parsed back into a
  float, so there is no parser to share, which is better than sharing one.
  A demand total that equals connected usually means no classifications
  are assigned; the tool shows it without judging it — resist the urge to
  turn that into a finding here (01 Circuit Check is where rules live).
  The `BaseEquipment` and `PanelScheduleView` hops must be memoised per
  board, not taken per circuit; with that, thousands of circuits stay a
  one-pass, sub-second scan.
- **Tests** — `test_spare_capacity_state.py` pins pole arithmetic with
  spares, spaces, and multi-pole circuits, the percent boundary at the
  threshold (exactly 80 tints, 79.9 does not), not-evaluable routing when
  a double is None, the VA-refusal rule, feeder derivation with a cycle in
  the edges (depth-guarded), and empty-board inclusion. —
  `test_spare_capacity_command_names.py` pins bundle metadata and the
  grown pulldown layout, XAML↔handler wiring, icon sizes, the IronPython
  AST scan, and the no-Transaction pin. — `test_spare_capacity_revit.py`
  drives the adapter against fakes per API generation — mains parameter
  missing, current totals absent, `PanelScheduleView` collector empty,
  `CircuitType` raising, poles `AsInteger` returning None — asserting
  plain data only. — `test_spare_capacity_xlsx.py` pins export rows and
  header order.

## UI description

**Main window** — one resizable modeless window (`ShowInTaskbar` off,
centered, grip-resizable), root `Grid Margin="14"`, rows Auto/*/Auto.
Header "Spare Capacity" SemiBold ~30px over a DimGray subtitle naming the
document and the promise: "Read-only. Demand is shown as the model
computed it." A slim options row under the header: numeric TextBox
"Threshold (%)" defaulting to 80, a "Group by feeder" CheckBox, and a
small "Search" label with the live-filter TextBox (board names by
substring, ids by token — searches flip visibility, selection survives).
The body star row (MinHeight set) holds the read-only table: Panel, Fed
From, Mains, Connected, Demand, % Used, Open Poles, and a **Show** button
per row (select + zoom via ExternalEventBridge). Rows at or over the
threshold tint; unreadable cells render an em-dash with the raw text in
the tooltip. "Group by feeder" re-nests rows under their source using the
tree engine, expander state preserved across Refresh. A "Not evaluable
(3)" expander at the bottom lists each unreadable figure with its board,
raw display text, and reason. Footer: status TextBlock left, then
right-aligned 110×35 buttons — **Refresh** (`IsDefault`), **Export**
(disabled until a run, tooltip "Nothing to export yet."), **Close**
(`IsCancel`).

> "42 boards read — 5 at or over 80% demand, 3 figures not evaluable. Nothing was changed."

> "LP-2 — 84% of main by demand, 6 open poles."

> "DP-1: max breakers not set on the family — open poles not computed."

There is no confirmation window and no report window — the tool never
writes, so the table is the whole story. Export writes the visible rows
plus the not-evaluable bucket through the standard save dialog.

### User operation flow

1. Ribbon: Misc Tools → Circuiting → Spare Capacity. The window opens and
   the first scan runs, status ticking while the bridge does the pass.
2. The table fills, worst boards tinted. Sort by % Used or Open Poles;
   type "LP" in Search to narrow the conversation to lighting panels.
3. Toggle **Group by feeder** — sub-boards indent under their source, and
   the distribution shape of the spare capacity is visible at a glance.
4. Click **Show** on LP-2 — Revit selects and zooms to the board. Decide,
   circuit the equipment (elsewhere), come back.
5. Press **Refresh**. The live model is re-read; the numbers move, the
   expander and grouping state survive.
6. A skipped item looks like: an em-dash cell whose tooltip reads "Demand
   reads '—', not a number", echoed as a row in "Not evaluable (3)".
   Nothing unreadable is ever averaged in as zero.
7. **Export** writes the workbook for the coordination minutes — boards
   by name, ElementId as a visible key.
8. **Close** (or Esc) at any point — the cancel path and the happy path
   are the same door, because nothing was ever going to be written.

## See also

- Existing: **Circuit Schedule** (the snapshot, the feeder edges, the
  tree engine, and the show/zoom helper this composes), **Update Circuit
  Rating** (the zero-amp report that set the read-only-companion
  pattern).
- Siblings: **01 Circuit Check** — rule-level QA over the same snapshot;
  it judges circuits, this measures boards. **18 Phase Balance** — the
  next question after "is there room" is "on which phase"; same panel
  arithmetic, write-side. **35 Power Sweep** — the device-side converse:
  what is not circuited anywhere yet, and will soon be asking for these
  open poles. **16 Panel Sheets** — shares the board-inventory probe
  (`PanelScheduleView` → `GetPanel()`); second consumer hoists it.
