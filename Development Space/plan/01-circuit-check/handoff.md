# 01 — Circuit Check

The mechanical read of every panel schedule — overloads, oversized breakers, junk load names — run by a tool instead of an engineer with a highlighter.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 01 of 45 | Misc Tools — Circuiting pulldown | yes | S | 9/10 | 8/10 |

## Main purpose

Every electrical model ships with the same quiet defects: circuits loaded past
80% of their breaker, breaker ratings above the panel main, load names still
reading the auto-generated device dump, three-pole numbers on a board that
cannot supply them. Revit will not stop any of it, and today nobody finds these
until the panel schedules are printed and an engineer reads them line by line —
usually the night before issue. The checks are entirely mechanical; a person
should not be the one running them.

Circuit Check is the read-only sweep that runs them. It reads every circuit in
the document the way Circuit Schedule already does — one pass, plain dicts —
and judges each one against a small set of rules: load against rating, rating
against frame, rating against the panel main, poles against voltage, the load
name against a "still auto-generated" heuristic, and whether the circuit is on
a panel at all. Everything it flags carries the two numbers (or two strings)
that disagree, so the row is its own explanation. A circuit whose numbers
cannot be read is never silently passed — it lands in a named "could not
evaluate" bucket with the raw text shown.

It earns the top rank because it is the cheapest tool on the list relative to
what it catches. The collectors, the field table, and the select-and-zoom
helper already exist in `lib/easybim/circuit_schedule_revit`; the whole new
surface is a rule engine in pure state code and one window. And nothing else
occupies the ground: Update Circuit Rating writes ratings and reports zero-amp
circuits, Circuit Schedule shows topology — neither judges a circuit against
its own numbers. Native Revit has no circuit QA view at all, and the Dynamo
graphs that float around offices for this are per-office one-offs with no skip
accounting. Circuit Check closes the loop the zero-amp report opened: run,
fix, Refresh, empty report.

## Basic implementation ideas

- **Bundle & module layout** — `EasyBIM.tab/Misc Tools.panel/Circuiting.pulldown/Circuit Check.pushbutton/`
  with `script.py` (thin launcher, `__persistentengine__ = True` — the window
  is modeless), `bundle.yaml` (two-line title "Circuit\nCheck", narrative
  tooltip, `author: Ruiming Liu`), `CircuitCheckWindow.xaml`,
  `circuit_check_state.py` (the whole rule engine — zero Revit imports),
  `circuit_check_revit.py` (the one pass into dicts), `circuit_check_ui.py`,
  `circuit_check_xlsx.py` (flat-row export). Reuse from `lib/easybim`:
  `circuit_schedule_revit.collect_circuits` and `CIRCUIT_FIELDS` — a
  pushbutton *can* import lib, so this must not become a third copy of the
  collector (the existing duplication exists only because lib cannot import a
  pushbutton) — plus the select-and-zoom helper, `ExternalEventBridge`, and
  `compat`. Add `Circuit Check` to the pulldown's `bundle.yaml` layout list.
  Nothing new hoists yet; if the xlsx flat-row writer grows a second consumer
  (Parameter Check wants the same shape), that is the hoist moment.
- **Revit API route** — `FilteredElementCollector` `OfCategory(OST_ElectricalCircuit)`
  `WhereElementIsNotElementType()` via the lib collector. Two value channels,
  never mixed: numbers for judging come from `Parameter.AsDouble()` /
  `AsInteger()` in internal units (amps, VA, volts) and from
  `ElectricalSystem.ApparentCurrent` behind a `hasattr` probe; text for
  showing comes from `AsValueString()` exactly as `CIRCUIT_FIELDS` reads it.
  This sidesteps the locale-parsing trap entirely — no display string is ever
  parsed back into a float. Panel mains come off `system.BaseEquipment` →
  `get_Parameter(RBS_ELEC_PANEL_MAINS_PARAM)`, the BuiltInParameter resolved
  with the same `getattr(DB.BuiltInParameter, name, None)` guard
  `circuit_rating_revit` uses; a missing name fails that one rule into "could
  not evaluate", never the run. Poles from `RBS_ELEC_NUMBER_OF_POLES`
  (`AsInteger`), voltage from `RBS_ELEC_VOLTAGE` (`AsDouble`). No writes at
  all, so no Transaction of any kind — a pin the command-names test enforces.
  All model reads after the window opens (Run, Refresh, Show) ride
  `ExternalEventBridge`.
- **The scan/report cycle** — read-only, so the cycle is scan → judge →
  report. `circuit_check_revit.scan_model` returns one snapshot: a list of
  circuit dicts (ints and unicode only, raw doubles included) plus a panel
  dict for each `BaseEquipment` seen. `circuit_check_state.evaluate(snapshot,
  rules, threshold)` returns findings grouped by panel, each finding carrying
  rule id, the offending values in display text, and the circuit's identity as
  panel name + circuit number (ElementId only as a linkified key). The report
  is always re-read from the live model on Refresh, zero-amp style: it answers
  "what is left to fix", and an empty report says so.
- **Edge cases & honest limits** — named buckets, never silence: *spares and
  spaces* (no elements, no load) skip the load-vs-rating and load-name rules
  as "spare — load checks not applied"; *zero or missing rating* routes to
  "could not evaluate" rather than reading as 0% loaded; *unassigned panel* is
  itself a finding, and disables the panel-main rule for that circuit with the
  reason stated; multi-slot numbers ("12,14,16") are kept verbatim as
  identity, never parsed. The tool judges **connected apparent load, not
  demand load** — the header says so — and it refuses to guess at wire
  sizing, AIC ratings, or code compliance: those need engineering judgment,
  and the tooltip names them as out of scope. Both heuristic rules ("poles
  inconsistent with voltage", "load name looks auto-generated") are labelled
  *heuristic* in the rule list and in every finding row.
- **Risks** — the classic trap is mixing `AsValueString` locales with float
  parsing; the two-channel rule above is the defense, and any circuit where
  the double read throws still lands visibly in "could not evaluate" with its
  display text. The auto-load-name detector will have false positives on
  projects whose real naming convention resembles Revit's dump — keep the rule
  uncheckable and labelled, never let it block anything. `ApparentCurrent`
  and the mains parameter vary across releases: capability-probe both, degrade
  the affected rule only. Models with thousands of circuits are fine (one
  collector pass), but the `BaseEquipment` hop must be memoised per panel, not
  taken per circuit.
- **Tests** — `test_circuit_check_state.py` pins the rule math at its
  boundaries (exactly 80% is not a finding, 80.1% is; zero rating routes to
  not-evaluable; spare handling; the name heuristic's known-good and
  known-bad strings). `test_circuit_check_command_names.py` pins bundle
  metadata, XAML↔handler wiring, icon sizes, the IronPython AST scan, and the
  forbidden-API pin that no `Transaction` is ever constructed in this bundle.
  `test_circuit_check_revit.py` drives the adapter against fakes shaped like
  both API generations — `ApparentCurrent` present and absent, mains
  parameter missing, `BaseEquipment` None — and asserts nothing but ints and
  unicode crosses back. `test_circuit_check_xlsx.py` pins the export rows and
  header order.

## UI description

**Main window** — one resizable modal-shaped modeless window (`ShowInTaskbar`
off, centered, grip-resizable), root `Grid Margin="14"`, rows Auto/*/Auto.
Header: "Circuit Check" SemiBold ~30px over a DimGray 13px subtitle naming the
document (`document_key`). Body, before a run: a **Checks card**
(`#D0D0D0` border, inner `#E0E0E0` list border) holding the six rule
checkboxes — each label in its own TextBlock, the two heuristics suffixed
"(heuristic)" — with the count line "6 checks selected, 0 unchecked.", Select
All / Select None, and a small numeric TextBox "Load threshold (%)" defaulting
to 80. Footer: status TextBlock left, then right-aligned 110×35 buttons —
**Run** (`IsDefault`), **Export** (disabled until a run, tooltip "Run a check
first."), **Close** (`IsCancel`).

After Run the body swaps to the **results view**: a read-only WPF table
grouped by panel under expanders (state preserved across Refresh), columns:
check name, panel, circuit number, load name, the two values that disagree,
and a **Show** button per row that selects and zooms through
`ExternalEventBridge`. A separate expander at the bottom, "Could not evaluate
(3)", lists each unreadable circuit with its raw display text and the reason.
The Run button relabels to **Refresh**. Status line examples:

> "214 circuits checked — 11 findings, 3 not evaluable. Nothing was changed."

> "Reading circuits… 180 of 214."

> "No findings. Every checked rule passes on all 214 circuits."

There is no confirmation window — the tool never writes, so the report is the
whole story. Export opens the standard save dialog and writes the visible rows
plus the not-evaluable bucket through `circuit_check_xlsx`.

### User operation flow

1. Ribbon: Misc Tools → Circuiting → Circuit Check. The Main window opens
   with all six rules ticked and the threshold at 80.
2. Untick anything not wanted (say, the load-name heuristic); the count line
   updates. Press **Run**.
3. The status line ticks progress while the bridge does the one pass; the
   results view fills, grouped by panel, worst counts first.
4. Click **Show** on a row — Revit selects and zooms to the circuit's
   elements. Fix the model (re-circuit, rename, resize the breaker).
5. Press **Refresh**. The pass re-reads the live model; fixed rows are gone,
   expander state and unticked rules survive.
6. A skipped item looks like: a row in the "Could not evaluate (3)" expander
   reading "LP-2 / 7 — rating reads '—', not a number", or a spare listed
   once as "spare — load checks not applied". Neither is counted as a
   finding.
7. **Export** writes the remaining rows to .xlsx for the markup set.
8. **Close** (or Esc) at any point — the cancel path and the happy path are
   the same door, because nothing was ever going to be written.

## See also

- Existing: **Update Circuit Rating** (writes ratings; its zero-amp report is
  the read-back pattern this generalises), **Circuit Schedule** (shares
  `collect_circuits`, `CIRCUIT_FIELDS`, and the show/zoom helper).
- Siblings: **06 Load Names** (fixes the auto-generated names this tool only
  flags), **34 Spare Capacity** and **18 Phase Balance** (panel-level
  arithmetic on the same snapshot), **35 Power Sweep** (the device-side
  converse: what is not circuited at all), **02 Parameter Check** (the same
  check-fix-Refresh loop generalised to any parameter).
