# 22 — Voltage Drop

Per-circuit voltage drop computed from the model's own length, load, and wire size — with a named not-calculable bucket instead of guessed numbers.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 22 of 45 | Misc Tools — Circuiting pulldown | yes | M | 8/10 | 8/10 |

## Main purpose

Voltage drop is checked in a spreadsheet the model never sees: someone
exports circuit lengths and loads, retypes wire sizes, and by the second
addendum the spreadsheet describes a model that no longer exists. Meanwhile
Revit carries everything the calculation needs — a per-circuit length
estimate, voltage, apparent current, poles, and the wire size string — and
computes nothing with it.

Voltage Drop is the calculator that closes that gap, with the whole formula
in the pure layer. The adapter reads each ElectricalSystem's numbers in one
pass; the state module parses the "3-#12, 1-#12 GND"-shaped wire size text
down to a conductor size, looks up resistance in a bundled copper/aluminum
table, and computes percent drop single- and three-phase, judged against
separate branch and feeder thresholds. The one design rule that makes it
trustworthy: anything unparseable — a blank wire size, an exotic notation, a
zero length — becomes a named "not calculable" row carrying the raw text
verbatim, never a guessed number. Results are primarily a report; optionally
the computed percentage can be written into one user-picked circuit
parameter so schedules and tags can carry it, behind a full staged plan.

Nothing occupies this ground. Update Circuit Rating copies existing numbers
between parameters and computes nothing. Revit's wire sizing settings size
conductors but never surface a per-circuit voltage-drop check you can read,
sort, or export. The Dynamo graphs offices pass around hardcode one wire
notation and die silently on every other — the named not-calculable bucket
is precisely the part they lack, and it is the EasyBIM half of the idea. The
rank sits mid-pack only because the tool is honest about what it is: a
screening check over Revit's estimated lengths, not a design calculation,
and every surface of it says so.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Circuiting.pulldown/Voltage Drop.pushbutton/`
  with thin `script.py`, `bundle.yaml` (two-line title "Voltage\nDrop",
  narrative tooltip that names the screening-only stance, `author: Ruiming
  Liu`), 96×96 icons. Four-layer split: `voltage_drop_state.py` (wire-string
  parser, resistance table, formula, threshold classification — pure and
  desktop-tested against hand calcs), `voltage_drop_revit.py` (one pass into
  dicts, plus the optional writer), `voltage_drop_ui.py`,
  `voltage_drop_xlsx.py` (flat-row export of results and the bucket), with
  `VoltageDropWindow.xaml` and `VoltageDropReport.xaml`. Add `Voltage Drop`
  to the Circuiting pulldown's `layout:` list. This tool and 21 Circuit
  Excel are joint second consumers of `circuit_rating_revit`'s
  target-discovery and unit helpers (`_collect_target_options`,
  `_get_param_unit_id`, `_to_internal`) — the first of the two to build
  hoists them into `lib/easybim/circuit_params.py`; the other imports. Same
  deal for `collect_circuits`, whose hoist 06 Load Names has already
  claimed.
- **Revit API route** — circuits via the hoisted `collect_circuits`
  (`OST_ElectricalCircuit`), cast to `Electrical.ElectricalSystem`. Per
  circuit, numbers come from internal units only: length from
  `RBS_ELEC_CIRCUIT_LENGTH_PARAM` (`AsDouble`, internal feet — Revit's own
  most-remote-device estimate, and the window header says exactly that),
  voltage from `RBS_ELEC_VOLTAGE` (`AsDouble`), poles from
  `RBS_ELEC_NUMBER_OF_POLES` (`AsInteger`), current from
  `ElectricalSystem.ApparentCurrent` behind a `hasattr` probe with the
  apparent-current BuiltInParameter as fallback — every BIP name resolved
  through the `getattr(DB.BuiltInParameter, name, None)` guard, a missing
  name routing that circuit to "not calculable", never failing the run.
  Wire size text is the one deliberate `AsValueString` read
  (`RBS_ELEC_CIRCUIT_WIRE_SIZE_PARAM`) — it is free text and is treated as
  such. Branch vs feeder is classified by what the circuit feeds: any
  connected electrical equipment means feeder (the `_is_electrical_equipment`
  test the rating tool owns), and the classification shows per row. The
  read pass needs no Transaction; the optional write is one assimilated
  `TransactionGroup` with a nested `Transaction` per circuit, counters
  zeroed on rollback. Modal, short-lived: no ExternalEvent, no Idling. The
  pure layer owns the formula, stated plainly: the parser normalizes one
  grammar — optional "N sets" prefix (parallel runs divide resistance by
  N), count-#size conductor groups, GND/N/IG members ignored for sizing;
  sizes #14 AWG through #4/0 and 250–600 kcmil. Resistance is a bundled
  plain-dict table (ohms per 1000 ft, Cu and Al, NEC Chapter 9 Table
  8-shaped values). Drop is `2·I·R·L/1000` for 1- and 2-pole circuits
  against their line voltage and `√3·I·R·L/1000` for 3-pole; percent
  against circuit voltage. Resistance-only — no reactance, no power
  factor — which is the stated reason this is a screening check. A row is
  flagged when its percent is strictly greater than its threshold (3.0%
  exactly passes; the boundary is pinned in tests).
- **The scan/report cycle (+ optional apply)** — Run produces one snapshot
  and one evaluation: per-circuit rows grouped by panel, each carrying the
  inputs it used, plus the named buckets. Refresh re-reads the live model,
  so the report always answers "what is over the line now". When "also
  write results" is on, `build_plan` stages the computed percent into the
  one picked parameter — one plan object for grid and executor — with
  changed-only writes (epsilon compare against the current value), rows red
  until Apply, and an acknowledgement tick that is the tool's conscience:
  "Calculated from Revit's estimated circuit length — a screening value,
  not a design calculation." The Report window re-reads written values from
  the committed model.
- **Edge cases & honest limits** — named buckets: "no wire size" (blank
  text), "wire size not understood" (raw string shown verbatim — the bucket
  the Dynamo graphs lack), "no length" (devices without locations leave the
  estimate empty or zero), "no voltage", "spare/space — not judged", "zero
  load — not judged" (the zero-amp temperament: no connected load is its
  own finding, not a 0% pass). One wire size per circuit is all Revit
  stores, so mixed-size or per-segment runs are invisible — stated in the
  header, not discovered by the user. The tool refuses to pick conductor
  sizes, refuses to judge code compliance, and refuses to write anything
  when the target parameter probe says read-only.
- **Risks** — wire size strings are shaped by each office's wire type
  naming; the parser needs a wide desktop corpus and must prefer refusing
  to guessing — a wrong parse writes a confident wrong number, which is the
  one unforgivable failure here. The liability framing must be everywhere
  (tooltip, header, acknowledgement, report footer) or a screening tool
  gets treated as an engineering one. The optional write inherits the full
  ForgeTypeId/DisplayUnitType split — percentage-spec vs plain-number
  parameters convert differently, and the hoisted unit path plus fakes for
  both API generations are the defense. `ApparentCurrent` availability
  varies by release; probe, fall back, and bucket.
- **Tests** — `test_voltage_drop_state.py` pins the parser corpus (good,
  bad, "2 sets of 3-#3/0", kcmil, GND-stripping), formula results against
  hand calculations, the strict-greater threshold boundary, and set
  division. `test_voltage_drop_command_names.py` pins bundle metadata, the
  pulldown layout entry, XAML↔handler wiring, icons, the IronPython AST
  scan, and a pin that no Transaction is constructed on the read path.
  `test_voltage_drop_revit.py` drives fakes shaped like both API
  generations — `ApparentCurrent` present/absent, missing BIP names,
  read-only target, one nested rollback zeroing its own counter — and
  asserts nothing but ints, floats, and unicode crosses back.

## UI description

**Main window** — resizable modal, header "Voltage Drop" over a DimGray
subtitle: "Lengths are Revit's most-remote-device estimate. Screening only."
Top row, two cards. **Settings card**: conductor material ComboBox (Copper /
Aluminum), two numeric TextBoxes "Branch limit (%)" = 3 and "Feeder limit
(%)" = 5, and a checkbox "Also write results to a circuit parameter" that
reveals a ComboBox of writable numeric circuit parameters (the Update
Circuit Rating discovery list; read-only entries greyed with the reason in a
tooltip). **Panels card**: checkbox list with Search and the count line
("14 panels — 14 checked, 0 unchecked."), Select All / Select None. Footer:
status left, **Run** (`IsDefault`), **Export** (disabled until a run,
tooltip "Run first."), **Close** (`IsCancel`).

After Run the body swaps to the **results table**, grouped by panel under
expanders (state preserved across Refresh): Ckt, Load Name, B/F, Length,
Wire, Amps, %VD — over-threshold rows tinted with the percent bold, and a
bottom expander "Not calculable (9)" showing each row's raw wire text and
reason. Run relabels to **Refresh**. When writing is enabled, a staged
column "→ Parameter" renders red until Apply, the acknowledgement checkbox
appears in the footer, and **Apply** sits disabled — never hidden — until it
is ticked.

> "188 calculated, 9 not calculable, 6 over 3%. Nothing was changed."

> "Staged: 143 values to write (changed only). One undo step."

**Report window** — after Apply only: read-only table of Panel, Ckt, %VD
(read back from the model), Result; skips and rollbacks in named buckets.

> "141 written, 2 rolled back, 45 unchanged — read back from the model."

### User operation flow

1. Ribbon: Misc Tools → Circuiting → Voltage Drop. The Main window opens;
   material Cu, limits 3/5, all panels checked.
2. Press **Run**. The status line ticks the pass; the results table fills,
   over-threshold rows first within each panel.
3. Read the "Not calculable (9)" expander — a skipped item looks like:
   "LP-2 / 14 — wire size 'AL XHHW special' not understood" or "RP-1 / 3 —
   no length (device has no location)". Fix wire types or locations in the
   model, press **Refresh**, watch the bucket drain.
4. **Export** writes the table plus the bucket to .xlsx for the calc file.
5. To stamp results: tick "Also write…", pick the parameter, and the staged
   column fills red with changed-only values.
6. Tick "Calculated from Revit's estimated circuit length…"; **Apply**
   enables and commits one TransactionGroup — one undo step; a locked
   circuit rolls back alone into the ledger.
7. The Report window reads the written values back from the committed
   model. Ctrl+Z reverts the batch.
8. Cancel path: **Close**/Esc before Apply leaves the model untouched — the
   read pass never opened a Transaction at all.

## See also

- Existing: **Update Circuit Rating** (target discovery, unit path, and the
  per-circuit rollback shape this reuses), **Circuit Schedule**
  (`collect_circuits` and the panel-grouped presentation).
- Rank 21 **Circuit Excel** — hoist partner for the discovery-and-units
  helpers; also the road out when the engineer wants the numbers in Excel.
- Rank 01 **Circuit Check** — the rule sweep next door; it judges numbers
  the model already has, this computes one the model lacks.
- Rank 34 **Spare Capacity** and rank 18 **Phase Balance** — the other
  panel-arithmetic siblings on the same circuit snapshot.
- Rank 43 **One Line Draft** — the feeder story downstream; a stamped %VD
  parameter is exactly what its riser annotations want to carry.
