# 44 — Fixture Units

Finds the exact joint where Revit's WSFU/DFU rollup stops adding up — and
checks every run against the sizing chart the spec actually uses.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 44 of 45 | Misc Tools / MEP Checks (new pulldown) | yes | M | 7/10 | 8/10 |

## Main purpose

Revit rolls fixture units up the pipe network — until one mis-set connector
or an open joint silently zeroes the accumulation, and the main gets sized
off a wrong total. The break is invisible: the Fixture Units cell on the
main just reads low, nothing is flagged anywhere, and there is no way to
see *where* the number stopped adding up. The second half of the failure is
that even a correct total proves nothing about the pipe: whether a 96-WSFU
run is allowed to be 2 inch depends on the chart in the project spec, and
nobody re-reads inch-by-inch after coordination moves the routing.

Fixture Units is a strictly read-only diagnostic for both halves. Setup is
picking which domestic water and sanitary system types to trace and loading
the office's FU-to-minimum-size chart from Excel. One pass snapshots the
pipes — diameter and Revit's own rolled-up fixture-unit value — the
fixtures' connector values, and the connector graph; pure state code then
runs its *own* traversal from fixtures toward the mains, summing per
segment, and compares its sum to Revit's native rollup at every joint. A
mismatch localizes the break to the first joint where the two diverge:
that joint is the finding, with both numbers on the row. The chart check
runs on the same snapshot, flagging every segment whose diameter sits
below the chart's minimum for its traced load.

The differentiation is the localization. Native Revit computes the rollup
but offers no view of where it breaks and no chart check; its sizing tools
work only on already-well-connected systems, which is precisely when you no
longer need them. The Dynamo FU scripts in circulation re-sum totals but
never diff against Revit's own accumulation joint by joint — and the diff
is the diagnostic that actually finds the broken connector. No EasyBIM tool
reads fixture units today. The tool never resizes a pipe: resizing cascades
through fittings and is a design decision, and the README will say exactly
that. The rank is held down only by audience — plumbing-heavy jobs need it
badly, others never open it.

## Basic implementation ideas

- **Bundle & module layout** — joins the MEP Checks pulldown that **03
  Slope Check** creates (add `Fixture Units` to the pulldown's `layout:`;
  if this ships first it creates the pulldown to 03's spec):
  `EasyBIM.tab/Misc Tools.panel/MEP Checks.pulldown/Fixture Units.pushbutton/`
  with `script.py` (`__persistentengine__ = True` — the results window is
  modeless), `bundle.yaml` (two-line title "Fixture\nUnits", narrative
  tooltip stating read-only, `author: Ruiming Liu`), 96×96 icons,
  `FixtureUnitsSetupWindow.xaml`, `FixtureUnitsResultsWindow.xaml`,
  `fixture_units_state.py` (traversal, per-joint summation, divergence
  and chart rules — pure Python over plain dicts),
  `fixture_units_revit.py` (the pass), `fixture_units_ui.py`,
  `fixture_units_xlsx.py` (findings export, xlsxwriter guarded). The
  graph walk consumes `lib/easybim/connector_graph.py` — the walker 03
  Slope Check writes locally and the 03/04/15 hoist contract lifts to
  lib; by this tool's build order the lib copy should exist, and if it
  does not, this tool performs the hoist rather than forking a copy. The
  chart loads through `lib/easybim/excel_workbook.read_workbook_sheets`;
  chart identity is by sheet and column *names*, never positions —
  portable into the next office.
- **Revit API route** — setup list from `FilteredElementCollector`
  `OfClass(Plumbing.PipingSystemType)` grouped by `SystemClassification`
  (DomesticColdWater, DomesticHotWater, Sanitary pre-checked). The pass
  collects `Plumbing.Pipe` filtered to chosen types via
  `RBS_PIPING_SYSTEM_TYPE_PARAM`, reading `RBS_PIPE_DIAMETER_PARAM` and
  the native rollup `RBS_PIPE_FIXTURE_UNITS_PARAM` (`AsDouble`, internal
  units; display text from `AsValueString` for showing only), plus
  fixtures and fittings through `FamilyInstance.MEPModel.
  ConnectorManager`; edges from `Connector.AllRefs` filtered to physical
  End/Curve, the same false-joint filter the walker already carries.
  Fixture-side WSFU/DFU is probed in order: `Connector.
  GetMEPConnectorInfo()` with `GetConnectorParameterValue(
  ParameterUtils.GetParameterTypeId(RBS_PIPE_FIXTURE_UNITS_PARAM))` where
  that surface exists (hasattr-probed, roughly 2022+); where it does not,
  the traversal seeds from each leaf pipe's own native rollup instead —
  the pipe touching the fixture carries the fixture's contribution — and
  the report header states which seed path ran, because the zero-FU-
  fixture finding reads differently under each. No writes, no Transaction
  — pinned. Scan, Refresh, and Show ride `ExternalEventBridge` because
  the results window is modeless.
- **The scan/report cycle** — read-only: scan → trace → report.
  `fixture_units_revit.scan(doc, chosen_type_ids)` returns one snapshot —
  pipe dicts (id, system, diameter, native FU, material name), fixture
  dicts (per-connector FU where readable), adjacency, and scan metadata —
  ints, floats, unicode only. `fixture_units_state.evaluate(snapshot,
  chart, tolerance)` walks each branch from fixtures toward the mains
  with a visited set, `MAX_DEPTH`, and an element budget; loops (hot
  water recirculation) are cut with the named skip "loop detected —
  branch truncated at guard". Per joint it compares traced sum against
  the downstream native value and emits: **Break points** (first joint of
  divergence, both numbers shown), **Undersized** (segment diameter below
  the chart minimum for its traced FU, chart row cited), **Rollup
  absent** (native param empty — "system not well-connected here"), and
  **Zero-FU fixtures** (a fixture whose connectors carry no value — a
  finding, never folded into sums silently; under leaf-pipe seeding the
  row reads "leaf pipe reads zero — fixture value or joint" because the
  two cannot be told apart there). Refresh re-scans the live model;
  expander state, search, and the loaded chart survive.
- **Edge cases & honest limits** — named-skip buckets: *"loop detected —
  branch truncated at guard (n)"*; *"fitting has no readable connectors —
  graph breaks here"*; *"branch truncated at depth/budget"*; *"fixture-
  side values unreadable on this Revit version — traced from leaf
  pipes"* stated once in the header, not per row. Manifolded mains and
  recirc-heavy models yield a mostly-truncated report — the footer then
  says most branches were cut and *why*, and the tool presents that
  honestly rather than as "clean"; a truncated branch is never counted as
  passing. Chart semantics are the user's: WSFU flush-tank versus
  flush-valve columns, hot/cold splits, material rows all live in the
  Excel the office supplies, the tool checks against exactly the chart it
  was given, and the setup card shows what it matched so a half-matched
  chart is visible before the run. The tool refuses to resize, refuses to
  infer flow direction from flow settings (topology only), and states
  that a clean report is model QA, not an engineered sizing calc.
- **Risks** — the loop guard is the load-bearing wall: recirculation and
  manifolds defeat a naive tree walk, and the guard must convert them to
  named truncations without eating the legitimate branches beside them —
  synthetic loop graphs are the most heavily pinned state tests. The
  divergence rule needs a stated tolerance (float compare plus Revit's
  own rounding of the native param) or every joint "diverges" by 0.01.
  Content quality is the other wall: fixture connectors with zero FU are
  endemic in downloaded families, which is why zero is a finding kind of
  its own rather than noise in the sums. The
  `GetConnectorParameterValue` surface must be probed, never
  version-numbered, and the leaf-seed fallback must produce the same
  divergence findings — the fakes cover both paths. Performance: the
  walk runs on snapshot dicts with id-keyed lookups, never re-entering
  the API mid-trace; budgets from day one.
- **Tests** — `test_fixture_units_state.py` pins per-joint summation and
  divergence localization on synthetic graphs (the break lands on the
  first divergent joint, not the last), loop truncation, chart lookups
  at band boundaries (exactly 96 WSFU), tolerance edges, and both seed
  paths producing identical break findings.
  `test_fixture_units_command_names.py` pins the pulldown layout growth,
  bundle metadata, XAML↔handler wiring, icon sizes, the IronPython AST
  scan, and the no-Transaction pin. `test_fixture_units_revit.py` drives
  the adapter over fakes shaped like each API generation —
  `GetConnectorParameterValue` present and absent, throwing
  ConnectorManager, `AllRefs` noise — asserting plain-data snapshots and
  every failure landing in a named bucket. `test_fixture_units_xlsx.py`
  pins chart parsing by named columns and the findings export rows.

## UI description

**Setup window** — Interference-Check-shaped modal, `Grid Margin="14"`,
header "Fixture Units" over the DimGray subtitle "Read-only. Finds where
the rollup breaks; never resizes a pipe." Three cards. **Systems card**:
checkbox list of piping system types grouped by classification, domestic
cold/hot water and sanitary pre-checked, live-filter Search, count line
"4 of 12 system types selected.", Select All / Select None. **Chart
card**: Browse for the Excel, a first-20-rows read-only preview grid, and
the match line "Matched 3 classifications, 2 materials, 14 size bands." —
unmatched chart rows listed in DimGray so a bad header is caught before
the run. **Tolerance card**: one TextBox for the divergence tolerance in
FU, defaulting to 0.5. Footer: status left ("Branches the walk cannot
finish are truncated by name, never guessed across."), **Check**
(`IsDefault`, disabled until a chart loads — the chart check can be
skipped by an explicit "Trace only, no chart" checkbox instead of a
silent half-run), **Cancel** (`IsCancel`).

**Results window** — resizable modeless, staying open beside Revit. Body:
a read-only WPF table grouped by system, one expander per finding kind
with its count — "Break points — 3", "Undersized — 7", "Rollup absent —
4", "Zero-FU fixtures — 2" — and a **Named skips** expander last. Rows
carry the two numbers that disagree ("joint at id 512907 — traced 42.5,
Revit reads 12.0") or the chart citation ("2 in run at 96 WSFU — chart
minimum 2 1/2 in, row 'Copper / flush tank'"), each with a **Show**
button selecting and zooming via ExternalEventBridge. Footer: **Refresh**,
**Export**, **Close**, status left:

> "Traced 96 branches — 3 rollup breaks, 7 undersized segments, 2 branches truncated at loops (named). Nothing was changed."

> "Fixture-side values unreadable on this Revit version — traced from leaf pipes; zero-FU rows read accordingly."

> "All 96 branches reconcile with Revit's rollup and clear the chart. Nothing was changed."

### User operation flow

1. Ribbon: Misc Tools → MEP Checks → Fixture Units. The Setup window
   opens with domestic water and sanitary pre-checked.
2. Browse to the office chart; the preview and match line confirm the
   columns landed. Set the tolerance, press **Check**. **Cancel** here
   closes with nothing read beyond the setup lists.
3. The Results window opens as the trace runs, status ticking per
   system; break points fill first.
4. **Show** on "joint at id 512907 — traced 42.5, Revit reads 12.0" —
   Revit zooms to the fitting; the mis-set connector or open joint is
   right there. Fix it in the model.
5. A skipped item looks like: "HWR loop — branch truncated at guard" or
   "fitting id 400123 — no readable connectors — graph breaks here"
   under Named skips. Truncated is never counted as passing, and the
   footer restates the truncation count.
6. **Refresh** — the pass re-runs live; the repaired break disappears
   and its downstream undersized rows re-judge against the now-correct
   totals. Chart and expander state survive.
7. **Export** writes findings, skips, and the chart rows cited to .xlsx
   for the sizing markup.
8. **Close** or Esc at any time; both windows write nothing, ever, so
   the cancel path and the happy path are the same door.

## See also

- Existing: **Slope** (the write-side neighbor on gravity pipe; this
  tool's read-only temperament is 03's, not Slope's), **Circuit
  Schedule** (the modeless-results + ExternalEvent pattern).
- Siblings: **03 Slope Check** (creates the MEP Checks pulldown and
  donates the connector-graph walker), **04 Open Ends** (the open joints
  that cause half these rollup breaks — run it alongside), **15
  Connection Check** (the deep joint-rule sibling on the same walker;
  it named this tool the pulldown's next resident), **36 Air Balance**
  (the duct-side cousin: design values reconciled against totals).
