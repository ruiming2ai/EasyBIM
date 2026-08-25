# 20 — Batch Insulation

The spec's insulation table — system type × size range → type and thickness —
applied model-wide as a first-match plan instead of piece by piece.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 20 of 45 | Misc Tools / Systems (new pulldown) | yes | M | 8/10 | 8/10 |

## Main purpose

Insulation rules live in the spec — this system type, this size range, this
thickness — but Revit applies insulation per selection with one thickness, so
every routing revision leaves new segments bare and old ones carrying
yesterday's thickness. Nobody re-selects a whole system after every change,
so QA finds the gap at the clash pass, when the missing two inches finally
collides with structure — or worse, on site.

Batch Insulation makes the spec table the input. An ordered rules grid —
system type name, size range, segment/fitting inclusion, insulation type
name, thickness — drives one collector pass over ducts, pipes, and their
fittings; pure logic matches each element to its first rule top-down and
emits keep / add / replace / remove actions. Elements matching no rule are
untouched and *counted*: the closing report's headline is what is still bare
because no rule claimed it — what is left to do, not what was done. Rules
round-trip through Excel with everything identified by name, so the same
workbook is the office standard, carried project to project.

It earns rank 20 because the pain is universal on mechanical and plumbing
jobs and the ecosystem's answer is thin: native Revit is Add Insulation per
selection with one thickness; MEPover-style Dynamo nodes can create
insulation but carry no rules table, no first-match plan, no group-member
skip handling, and no single-undo batch with per-element rollback. Nothing
in EasyBIM touches insulation today — this is the Systems pulldown's first
writer, next to the read-only 05 System Schedule and 09 System Isolate —
and it feeds a direct downstream consumer: 13 Sleeve Place sizes sleeves
from the service *plus its insulation*, so a model this tool has trued up
produces sleeves that are actually big enough.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Systems.pulldown/Batch Insulation.pushbutton/`
  — the pulldown is created by 05 System Schedule or 09 System Isolate if
  either builds first; otherwise this tool creates it (pulldown
  `bundle.yaml` + icons) and the others slot in. `script.py` thin, with
  `__persistentengine__ = True` — the Main and Confirmation windows are
  modal, but the Report window stays open modeless with Show buttons, so
  the script owns an `ExternalEventBridge` for the post-commit
  select-and-zoom. `bundle.yaml` two-line title "Batch\nInsulation",
  narrative tooltip naming linings as out of scope, `author: Ruiming Liu`.
  Split: `batch_insulation_state.py` (rule model, half-open ranges, shadow
  lint, first-match classification, plan/report shaping — pure Python),
  `batch_insulation_revit.py` (the collector pass, insulation reads, the
  create/delete executor), `batch_insulation_ui.py` + three XAML files,
  `batch_insulation_xlsx.py` (rules round-trip and report export through
  `lib/easybim/excel_workbook`). Rules also persist as a named local
  library via `script.get_universal_data_file`, so the tool works without
  a workbook in hand; Excel remains the interchange format.
- **Revit API route** — One pass per domain:
  `FilteredElementCollector` over `OST_DuctCurves`, `OST_FlexDuctCurves`,
  `OST_DuctFitting`, `OST_DuctAccessory`, `OST_PipeCurves`,
  `OST_FlexPipeCurves`, `OST_PipeFitting`, `OST_PipeAccessory`,
  `WhereElementIsNotElementType()`. Two value channels, never mixed:
  matching reads `AsDouble()` in internal units — duct width/height or
  diameter (`RBS_CURVE_WIDTH_PARAM` / `RBS_CURVE_HEIGHT_PARAM` /
  `RBS_CURVE_DIAMETER_PARAM`, max side for rectangular), pipe nominal
  diameter (`RBS_PIPE_DIAMETER_PARAM`) — while the grids show
  `AsValueString()`. Fittings and accessories size from their largest end
  connector via `ConnectorManager` (radius doubled, or width/height max),
  and the plan labels that source. System type name as display text from
  `RBS_DUCT_SYSTEM_TYPE_PARAM` / `RBS_PIPING_SYSTEM_TYPE_PARAM`, resolved
  with the house `getattr(DB.BuiltInParameter, name, None)` probe.
  Existing insulation via the static
  `InsulationLiningBase.GetInsulationIds(doc, id)`; per id, type name and
  `Thickness`. Insulation types collected `OfClass(DuctInsulationType)` /
  `OfClass(PipeInsulationType)` and matched by name — a rule naming a type
  the document lacks is disabled at plan time with the reason shown; the
  tool never invents types. Writes: `DuctInsulation.Create` /
  `PipeInsulation.Create(doc, hostId, insulationTypeId, thickness)`;
  remove via `doc.Delete(insulationId)`. Replace is delete + create inside
  the *same* nested `Transaction`, so a refused create rolls the delete
  back too — an element is never left stripped by a half-replace. Commit
  shape: one assimilated `TransactionGroup`, one nested transaction per
  element, counters zeroed on rollback, the whole write under a
  cancellable `forms.ProgressBar` in bounded batches — replace regenerates
  briefly, and 10k elements must stay cancellable throughout.
- **The plan/apply cycle** — `build_plan` classifies every in-scope
  element: keep (first rule matched and the existing insulation already
  has that type name and a thickness within the stated tolerance), add,
  replace, remove (a rule row whose action is Remove), or no-rule
  (untouched, counted). Size ranges are half-open `[min, max)` and printed
  that way in the grid — "≥ 3″ and < 6″" — so two rows can never both
  claim a size; a state-layer lint still warns when ordering makes a row
  unreachable ("rule 4 is shadowed by rule 2 for pipes 2″–3″"). One plan
  object feeds the Confirmation window and the executor, so preview and
  write cannot drift. Irreversible-feeling steps gate on acknowledgement:
  Remove rows demand a verification checkbox in a TaskDialog-shaped prompt
  (native mimicry, per house rules), and elements carrying stacked
  insulations get "replace all with one" only behind their own tick.
  After commit the Report window re-reads `GetInsulationIds` from the
  model: written, removed, refused (rolled back, named), and the "still
  bare — no rule matched" expander with Show buttons through the bridge.
- **Edge cases & honest limits** — Named buckets: "in a group — skipped"
  (insulation edits on group members throw; probed at plan time via
  `GroupId`, so it is a plan-time skip, never a runtime failure),
  "placeholder — skipped", "owned by another user — skipped" (worksharing
  checkout probe before the write), "stacked insulations — needs
  acknowledgement", "category refused insulation" (runtime refusal turned
  into a named skip by its nested rollback), "rule disabled — insulation
  type not in document", "no rule matched — untouched". Out of scope and
  stated: duct/pipe *linings* (v1, named in the tooltip), elements in
  linked models (unwritable by definition), cable tray and conduit (no
  insulation classes), and inventing insulation types. The tool never
  widens scope to "everything else" — fail closed is the first rule.
- **Risks** — Fitting insulation follows different behavior than segment
  insulation and some categories refuse it; that cannot be probed at plan
  time without writing, so plan rows for fittings carry a "may refuse"
  note and the per-element nested group converts a refusal into a named
  skip, not a batch failure. Replace = delete + create regenerates as it
  goes; bounded batches, the progress bar, and a cancel that keeps
  committed work are load-bearing on large models. The half-open range
  convention must be printed, tested at the exact boundary (a 3″ pipe
  belongs to the row that owns 3″), and identical between the grid, the
  lint, and the executor. Thickness comparison needs an explicit tolerance
  or every run re-replaces the same insulation forever. Connector-derived
  fitting sizes are an approximation and the plan must label them as such
  rather than presenting them as the fitting's nominal size.
- **Tests** —
  - `test_batch_insulation_state.py` pins first-match-wins ordering, the
    half-open boundary (exactly 3″), shadow-lint detection, keep vs
    replace under the thickness tolerance, remove classification, stacked
    and group buckets, plan/report shaping, counters zeroing on rollback.
  - `test_batch_insulation_command_names.py` pins pulldown + pushbutton
    bundle metadata, XAML↔handler wiring across all three windows, 96×96
    icon pairs, `__persistentengine__`, the IronPython AST scan, and
    forbidden-API pins.
  - `test_batch_insulation_revit.py` drives the adapter against fakes per
    API generation: round vs rectangular size params, connector-derived
    fitting sizes, `GetInsulationIds` returning zero/one/many, a missing
    insulation type disabling its rule, a `Create` refusal rolling back
    its delete partner, group-member and checkout probes, cancel mid-run.
  - `test_batch_insulation_xlsx.py` pins the rules round-trip by name
    (import rejects a malformed row with its reason, never half-reads)
    and the report export rows.

## UI description

**Main window** — resizable modal, header "Batch Insulation" over the
DimGray subtitle "First rule that matches wins. Elements no rule claims are
counted, never touched." Two cards. Left card, "Rules": an editable grid —
#, System Type (ComboBox from the model's list), Size (half-open, printed
"≥ 3″ and < 6″"), Segments/Fittings ticks, Insulation Type, Thickness,
Action (Apply / Remove) — rows staged red until saved, Up/Down ordering
buttons, **Load Excel** / **Save Excel**, a lint line under the grid
("rule 4 is shadowed by rule 2 for pipes 2″–3″"), and disabled rules greyed
with their reason. Right card, "Scope": the system types checkbox list with
search and "14 system types — 8 checked, 6 unchecked.", Select All / Select
None. Footer: status left, **Build Plan…** (`IsDefault`, disabled with
tooltip until at least one rule and one system type), **Cancel**
(`IsCancel`).

> "Scope: 8 of 14 system types. Rules: 6 rows, 1 disabled (insulation type not in document)."

**Confirmation window** — the complete dry run grouped by action with
counts in the header — "Add 212 · Replace 34 · Remove 8 · Keep 1,090 ·
No rule 77" — each group an expander of rows (element, system type, size,
rule #, "may refuse" notes on fittings), skips listed with their buckets.
If the plan removes anything, a TaskDialog-shaped prompt with a
verification checkbox ("I understand 8 insulations will be deleted.") must
be ticked before **Apply** enables; stacked-insulation replacements carry
their own tick. The write runs under the cancellable `forms.ProgressBar`.

> "254 writes planned, 8 removes await the verification tick. 12 skipped — 9 in groups, 3 placeholders."

**Report window** — modeless after commit, read-only expanders: Written /
Removed / Refused (rolled back, named) / Skipped / **Still bare — no rule
matched (77)**, the last with a **Show** button per row that selects and
zooms through the bridge. Buttons: **Export to Excel**, **Close**. Footer:

> "246 written, 8 removed, 3 refused (rolled back), 77 still bare — re-read from the model. One undo step."

### User operation flow

1. Ribbon: Misc Tools → Systems → Batch Insulation. The scan fills the
   system-type list; load the office workbook or edit rules in place.
2. Order the rules (first match wins — the row numbers are the contract);
   fix anything the lint line flags. Check the system types in scope.
3. **Build Plan…** runs the one classification pass and opens the
   Confirmation window grouped by action.
4. Review; tick the Remove verification (and the stacked-insulation tick
   if offered). **Apply** enables only then.
5. Apply writes per element under the progress bar, nested transactions
   inside one assimilated group. Cancelling keeps what committed as one
   undo step and the report says "cancelled after 180 of 254".
6. The Report window opens re-read from the model. A skipped item looks
   like: "Duct 4412 — skipped: in a group"; a refusal like: "Pipe fitting
   887 — rolled back: category refused insulation."
7. Open "Still bare — no rule matched", **Show** the stragglers, extend
   the rules or fix the model, and run again — the report answers what is
   left to do. **Export to Excel** for the QA record; Close. One Ctrl+Z
   reverts the whole run.
8. Cancel path: **Cancel** on either window before Apply — nothing has
   been written; declined removes are skipped, never failed.

## See also

- Existing: **Slope** (the nearest pipe-side writer today), **Excel**
  (schedule round-trips; this tool's workbook goes through the same
  `excel_workbook` lib), **Clash Detection Mode** (where missing
  insulation is currently discovered — after the fact).
- Plan siblings: **05 System Schedule** and **09 System Isolate** — the
  Systems pulldown neighbours; the schedule finds the system, isolate
  frames it, this tool dresses it. **13 Sleeve Place** — the direct
  consumer: sleeves are sized from service + insulation, so run this
  first. **03 Slope Check** — the same read-the-whole-system discipline
  on the drainage side. **36 Air Balance** — Systems pulldown sibling on
  the same mechanical snapshot shape.
