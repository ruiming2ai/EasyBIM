# 36 — Air Balance

The load calc's CFM into the model, the model's terminals summed back
against spaces and equipment — one reconcile instead of three ad-hoc
schedules every QA round.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 36 of 45 | Misc Tools / Systems (new pulldown) | yes | L | 8/10 | 9/10 |

## Main purpose

Design CFM lives in the load-calc spreadsheet; someone types it into
hundreds of diffusers by hand; spaces drift between Specified and Actual
airflow; and the sum of terminals on a system quietly stops matching the
AHU it hangs from. Every mechanical QA round rediscovers the same three
mismatches with three ad-hoc schedules, fixes the loudest ones, and leaves
the drift to reopen before the next issue.

Air Balance reconciles the three columns of truth in one pass, and only
then offers to write. One collector pass reads Spaces with their specified
airflows, air terminals with their flow, owning space at an explicit
phase, and duct classification, and the scoped systems with their
equipment. An optional workbook — room number and name against design CFM,
matched by exact number with exact-name fallback, never fuzzy — supplies
the external column. Pure state code computes per-space terminal sums and
deltas under an absolute-plus-percent tolerance, judged per classification
(a return grille never sums against Specified Supply), and rolls each
system's terminals up against its equipment's capacity parameter. Report
first, always. The explicit second stage distributes a space's design CFM
across its writable terminals under a stated deterministic rule and can
update the space's Specified Airflow; every terminal it cannot touch is a
named skip. Writes commit per space, nested inside one assimilated group,
and the closing view is re-read from the committed model — it answers
"what is still out of balance", not "what did I just do".

Revit shows Specified vs Actual per space but cannot compare against an
external design table, cannot push values down onto terminals, and never
rolls terminals up against equipment capacity; EasyBIM's Excel tool
round-trips schedules generically, while this understands airflow
semantics — distribution rules, tolerance, read-only flow configurations.
Nothing in the free ecosystem balances. The honest wall is stated up
front rather than half-worked-around: rooms living in an architectural
link are not spaces, so the tool requires Spaces in the mechanical model
and says so in the subtitle — 24 Space Sync is the tool that gets a model
there. Impact 9 because airflow totals are contract numbers on every
mechanical set; the L (three data sources, a write stage, and the
space-assignment swamp) is what holds an 8·9 idea at rank 36.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Systems.pulldown/Air Balance.pushbutton/`
  — the pulldown is created by 05 System Schedule, 09 System Isolate, or
  20 Batch Insulation if any builds first; otherwise this tool creates it
  and the others slot in. `script.py` thin with
  `__persistentengine__ = True` — the Setup window is modal, but the
  Balance window is modeless with Show/Refresh/Apply riding
  `ExternalEventBridge`. `bundle.yaml` (two-line title "Air\nBalance",
  narrative tooltip naming the spaces-not-rooms wall,
  `author: Ruiming Liu`), 96×96 icons. Split: `air_balance_state.py`
  (workbook matching, tolerance, classification split, the distribution
  rule, plan and bucket shaping — pure Python), `air_balance_revit.py`
  (the collector pass, writability probes, the per-space writer,
  read-back), `air_balance_ui.py`, `AirBalanceSetup.xaml` +
  `AirBalanceWindow.xaml`, `air_balance_xlsx.py` (design-column read and
  report export over `lib/easybim/excel_workbook`). Nothing hoists yet;
  the unit shim rides `compat` alongside the ForgeTypeId/DisplayUnitType
  split `circuit_rating_revit` already carries.
- **Revit API route** — One pass in `air_balance_revit.scan_model`:
  Spaces via `FilteredElementCollector` `OfCategory(OST_MEPSpaces)` —
  number and name from `ROOM_NUMBER`/`ROOM_NAME`, the specified
  supply/return/exhaust triple resolved through the house
  `getattr(DB.BuiltInParameter, name, None)` probe
  (`ROOM_DESIGN_SUPPLY_AIRFLOW_PARAM` and its return/exhaust siblings).
  Terminals via `OfCategory(OST_DuctTerminal)` `OfClass(FamilyInstance)`:
  flow from `RBS_DUCT_FLOW_PARAM` in two channels (`AsDouble` internal
  for math, `AsValueString` for display), owning space via
  `inst.get_Space(phase)` with an explicit phase ComboBox — the Load
  Names precedent — and deliberately *no* `get_Room` fallback, because
  rooms are not spaces and this tool refuses the substitution;
  classification from the terminal's duct connector's `DuctSystemType`
  probe with the owning system's classification as fallback; system
  membership from the connector's `MEPSystem` id. Systems via
  `OfClass(Mechanical.MechanicalSystem)` — 05's collector, including the
  `hasattr` probe on `SystemType` with the `GetTypeId()` fallback —
  plus `BaseEquipment`. Equipment capacity is a parameter chosen by name
  from a ComboBox of numeric parameters found on the scoped systems'
  equipment, because family libraries disagree; absence is a per-system
  named reason, never a zero. Writability is probed per terminal with
  `param.IsReadOnly` — the honest test; the skip reason explains the flow
  configuration (Calculated/System) when it can be read. All user-facing
  numbers are CFM (or L/s, m³/h — a workbook-unit ComboBox), converted
  once at the edge through the compat unit shim; internals stay doubles
  in internal units. Transactions: the scan opens none (pinned); Apply
  commits one assimilated `TransactionGroup` with one nested
  `Transaction` per space — a half-balanced space is worse than an
  untouched one, so any refused terminal rolls its whole space back
  alone, counters zeroed. The write runs in one bridge dispatch under a
  cancellable `forms.ProgressBar`, per space.
- **The plan/apply cycle** — `build_plan` takes the reconcile plus the
  chosen source of truth and emits, per matched space: the target flow
  per writable terminal — design CFM split equally across that
  classification's writable terminals, rounded to a stated step (default
  5 CFM), remainder onto the terminal with the largest current flow, ties
  broken by lowest element id, all deterministic and pinned — plus the
  optional Specified Airflow update, plus a bucket for everything it
  will not touch. One plan object feeds both the staged grid and the
  executor, so preview and write cannot drift. Staging renders old → new
  in red per terminal, grouped by space; unchecking a space moves it to
  "skipped — unchecked", never failed. **Apply** is disabled — never
  hidden — until the acknowledgement tick "Terminal airflows will be
  overwritten." is set. After commit the Balance window re-reads the
  model and returns to its review grid with a commit line; the numbers
  shown are the committed ones, and the spaces still out of tolerance
  are the honest remainder.
- **Edge cases & honest limits** — named buckets: *"no space at phase
  <name>"* — grouped loudly with per-level counts ("L3: 28, L4: 13"),
  because ceiling-hosted terminals above the space upper limit flood
  this bucket in badly set-up models and the report must say so as one
  line, not 41; *"space has zero terminals — nowhere to put 450 CFM"*;
  *"workbook row matches no space"* and *"duplicate number in workbook —
  not matched"* (refused, red, never guessed); *"flow is read-only —
  system-computed"*; *"classification unknown — not summed"*; *"terminal
  on no duct system — summed into its space, absent from rollups"*;
  *"equipment capacity parameter absent"*; *"owned by another user —
  space skipped"*. Per-space sums are whole-model truth regardless of
  scope — scope governs the write and the system rollup, never the sum,
  or the deltas would lie. The tool applies no diversity, sizes no duct,
  and checks no code; it reconciles against the design table it was
  given, and the tooltip says exactly that.
- **Risks** — Space assignment is the swamp the L pays for: `get_Space`
  is phase-indexed and volume-dependent, terminals hosted above the
  space upper limit legitimately report no space, and the per-terminal
  lookup is the slow path — one pass, cached into the snapshot, status
  ticking per level. Writability varies by family flow configuration
  and release: the `IsReadOnly` probe per terminal, never a version
  branch; and because setting flow can cascade a system recompute, the
  writer batches per space inside its nested transaction rather than
  regenerating per terminal. Workbook matching must survive "101" vs
  "101A": exact-number-then-exact-name, stated in the dialog, duplicates
  refused — fuzziness is how a design value lands in the wrong room.
  The distribution rule must be deterministic or two runs disagree —
  stable sorts and the tie rule are pinned. Capacity comparison is only
  as good as the picked parameter; keeping it by-name and per-model
  (never a preset ElementId) is what keeps it portable and honest.
- **Tests** — `test_air_balance_state.py` pins workbook matching
  (number-then-name, duplicates refused, "101" ≠ "101A"), the tolerance
  boundary (delta exactly at absolute-plus-percent passes), distribution
  determinism (rounding step, remainder placement, id tie), the
  classification split, bucket routing, and counters zeroing on a space
  rollback. — `test_air_balance_command_names.py` pins pulldown-or-
  pushbutton bundle metadata, XAML↔handler wiring for both windows, icon
  sizes, the IronPython AST scan, and the no-Transaction-during-scan
  pin. — `test_air_balance_revit.py` drives the adapter over fakes per
  API generation — `get_Space` absent or raising, connector
  `DuctSystemType` missing, `IsReadOnly` true, the deprecated
  `SystemType` shape, both unit-shim generations, a mid-space write
  refusal rolling the space back whole — asserting plain data only. —
  `test_air_balance_xlsx.py` pins the design-column parse, the declared
  workbook unit, and the report export rows.

## UI description

**Setup window** — resizable modal, `Grid Margin="14"`, header "Air
Balance" SemiBold ~30px over the DimGray subtitle: "Requires Spaces in
this model — rooms in a linked model are not read." Two cards. Left,
**Scope card**: checkbox list of mechanical system types with Search and
the count line "4 of 9 system types selected.", Select All / Select
None, the phase ComboBox, and the equipment-capacity parameter ComboBox.
Right, **Source of truth card**: radio "Model only (Specified Airflow)" /
"Workbook…" with a file picker, the workbook-unit ComboBox, and a live
match line once picked — "63 rows — 58 matched, 3 unmatched, 2
duplicates (refused)." with the failures in red — over the tolerance
boxes ("± 10 CFM + 5 %") and the rounding-step box. Footer: status left,
**Reconcile** (`IsDefault`), **Cancel** (`IsCancel`).

**Balance window** — resizable modeless, two modes. *Review mode*: a
read-only grid grouped by space — Space, Design (source named),
Specified, Terminal sum, Δ — deltas beyond tolerance in red, terminal
rows expandable under each space, a **Systems** section with one
expander per system (equipment, capacity from the named parameter,
terminal sum, Δ), a **Named skips** expander grouped loudly ("No space
at phase New Construction — 41 terminals (L3: 28, L4: 13)"), and a
**Show** button on every row (select + zoom via ExternalEventBridge).
Footer: status left, then **Stage Fixes** (disabled with tooltip until a
source yields writable targets), **Export**, **Refresh**, **Close**
(`IsCancel`). *Staged mode* — the confirmation stage: the matched spaces
flip to an old → new grid, red until Apply, each space uncheckable, the
acknowledgement CheckBox "Terminal airflows will be overwritten." in the
footer beside **Apply** (`IsDefault`, disabled until ticked) and
**Back**. Status lines:

> "112 spaces read — 84 within tolerance, 19 out, 9 skipped (named). 3 of 7 systems exceed equipment capacity."

> "61 spaces staged — 312 terminal writes planned, 9 spaces skipped (named)."

> "Balanced 61 spaces — read back: 58 within tolerance, 3 still out (listed). One undo step."

### User operation flow

1. Ribbon: Misc Tools → Systems → Air Balance. The Setup window opens;
   system types and phases list immediately.
2. Check the supply systems, pick the phase, pick the capacity
   parameter, point at the load-calc workbook — the match line updates
   live, unmatched and duplicate rows in red. Press **Reconcile**.
   Cancel here closes with nothing read beyond the lists.
3. The Balance window opens in Review mode as the pass runs, status
   ticking per level; deltas fill, red where out of tolerance. Nothing
   has been written.
4. Click **Show** on Space 214's red row, look at the ceiling, fix the
   space upper limit, **Refresh** — the skip bucket shrinks; expander
   state and the workbook match survive.
5. Press **Stage Fixes**. The staged grid renders old → new in red.
   Uncheck the conference room the engineer wants left alone — it moves
   to "skipped — unchecked". Tick the acknowledgement; **Apply** enables.
6. Apply writes per space under the cancellable progress bar, one nested
   transaction each inside one assimilated group. Cancelling stops the
   remaining spaces; committed spaces stand as one undo step and the
   status says "cancelled after 40 of 61 spaces".
7. Review mode returns, re-read from the committed model. A skipped item
   looks like: "Space 214 — 0 terminals — nowhere to put 450 CFM" or
   "VAV 3-2 terminal — flow is read-only (system-computed)". One Ctrl+Z
   in Revit reverts every space the run touched.
8. **Export** writes the reconcile — deltas, rollups, and skips — to
   .xlsx for the QA record.
9. Cancel path: **Cancel** in Setup, or **Close**/Esc in Review mode,
   leaves the model untouched; **Back** in Staged mode returns to Review
   with nothing written.

## See also

- Existing: **Excel** (the generic schedule round-trip this deliberately
  is not), **Update Circuit Rating** (the write-then-read-back pattern
  and the unit-shim precedent), **Circuit Schedule** (the modeless
  window + ExternalEvent manners).
- Siblings: **05 System Schedule** — creates the Systems pulldown and
  owns the mechanical-system collector this reuses. **20 Batch
  Insulation** and **09 System Isolate** — Systems pulldown neighbours.
  **24 Space Sync** — creates and maintains the Spaces this tool
  requires; run it first on a model that fails the subtitle. **06 Load
  Names** — donor of the explicit-phase `get_Space` walk. **44 Fixture
  Units** — the plumbing cousin: the same sum-along-the-tree-against-
  capacity temperament, water instead of air.
