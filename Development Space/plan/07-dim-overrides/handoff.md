# 07 — Dim Overrides

Every overridden dimension in the set, and whether it tells the truth.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 07 of 45 | Annotation (new panel) | no | S | 8/10 | 9/10 |

## Main purpose

A dimension whose text was typed over lies on the print — the model says
11' 7-7/8" and the paper says 12'-0" — and Revit gives no way to see which
dimensions carry overrides short of clicking every one. Before every issue
somebody is supposed to check, and nobody can. The overrides that merely
retype the true value are noise; the one that contradicts it is a lawsuit.

Dim Overrides audits first and strips second. One pass collects every
dimension override in the chosen scope — active view, the sheets of a print
set, or the whole model — and the classifier does the part no one else does:
it parses the override text back into a number and compares it against the
measured value within the dimension type's own rounding, separating the
harmless retype from the contradiction. Text overrides ("VARIES", "HOLD")
and affix-only rows are their own classes, flagged for eyes rather than
judged. From the same table, checked rows can be stripped back to their
live measured value — with group and worksharing guards, staged red, one
undo step.

It earns rank 9-impact at S-effort because the deliverable risk is severe,
the scan is cheap, and the field is empty: nothing in the EasyBIM inventory
reads dimensions, native Revit cannot list overrides at all, and the prior-art
survey found annotation QA has near-zero free coverage — this is commercial
territory. Community scripts that merely select overridden dims exist, but
none parse the override against the measured value, and none carry
group/worksharing guards or a selective strip with named skips. It also
founds the new Annotation panel — the natural home a future move of Tag
Align and Tags Sweep would share, though that move is not part of this build.

## Basic implementation ideas

- **Bundle & module layout** — New panel:
  `EasyBIM.tab/Annotation.panel/Dim Overrides.pushbutton/` with thin
  `script.py`, `bundle.yaml` (two-line title, narrative tooltip, `author:
  Ruiming Liu`), 96×96 icons. The window is modeless (audit while you fix),
  so the button sets `__persistentengine__ = True`, drops stale modules on
  relaunch, and mirrors live state into pyRevit envvars. Four-layer split in
  the pushbutton: `dim_overrides_state.py` (the parser/classifier — the
  heart of the tool, desktop-tested hard), `dim_overrides_revit.py` (scan +
  strip through the bridge), `dim_overrides_ui.py` + `DimOverridesWindow.xaml`
  and `StripConfirm.xaml`. Composes from lib: `print_sets` for the print-set
  scope, `external_events.ExternalEventBridge` for all model work. Nothing
  hoists — first consumer of a dimension scanner.
- **Revit API route** — Scope resolves to a view list: the active view; each
  sheet of a chosen print set expanded via `sheet.GetAllPlacedViews()`; or
  every view (one documentwide collector grouped by `OwnerViewId`). Per view,
  `FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension)`.
  Single-segment dims read `ValueOverride`, `Prefix`, `Suffix`, `Above`,
  `Below` directly and the measured double from `Value`; multi-segment dims
  iterate `Segments` for per-`DimensionSegment` values. Display formatting
  uses the document's units via `UnitFormatUtils.Format`, with the length
  spec resolved by capability probe — `SpecTypeId.Length` where ForgeTypeId
  exists, the legacy `UnitType` otherwise. The comparison tolerance comes
  from the dimension type's own `GetUnitsFormatOptions()`; when
  `UseDefault` is set, fall back to `doc.GetUnits().GetFormatOptions()` for
  length. Strip is `ValueOverride = u""` per dimension. All model work from
  the modeless window rides the ExternalEventBridge; commit is one
  assimilated `TransactionGroup` with a nested `Transaction` per dimension
  so one refused edit rolls back one row. Group membership via `GroupId`;
  worksharing editability via a `WorksharingUtils.GetCheckoutStatus` probe
  before staging.
- **The plan/apply cycle** — Scan produces plain-dict rows (element id,
  view, sheet, override text, measured double, affixes, class, group id,
  checkout state) and the classifier assigns each row exactly one class:
  retype (parses equal within the type's rounding), contradiction (parses
  to a different number — the red list), text override, affix only. Strip
  is the write half: `build_plan` gathers the checked rows, drops the ones
  that changed underneath the modeless window ("no longer in model" —
  re-verified through the bridge), and the Strip confirmation window shows
  counts by class plus every named exclusion. Dimensions inside groups are
  excluded unless the acknowledgement checkbox is ticked — "Stripping
  inside a group edits every instance of that group" — and a second
  acknowledgement states the irreversible part: "The typed text is
  discarded; the dimension returns to its measured value." After commit the
  table re-reads the stripped rows from the model; rows whose
  `ValueOverride` reads back empty leave the staged-red state, and the
  status line reports from the committed model, not the plan.
- **Edge cases & honest limits** — Named-skip buckets: "in a group (not
  acknowledged)", "owned by another user", "no longer in model",
  "unchecked". Only length-bearing dimension shapes get the
  retype-vs-contradiction verdict; angular, radial, arc-length, spot and
  ordinate rows are listed with their override but classed "text — not
  parsed", never guessed. The parser must fail toward "text override"
  (flagged for eyes) rather than "retype" (dismissed) — a string it cannot
  confidently parse is never declared harmless. Dual-unit overrides parse
  the primary half and note the rest. Dimensions in linked documents are
  out of scope (unwritable anyway) and the window says so. The tool refuses
  to judge intent: "VARIES" may be perfectly correct — it is surfaced, not
  scored.
- **Risks** — Parsing override text is the whole game and it is genuinely
  messy: unit symbols, typed fractions, "±", "~", dual units, thin spaces.
  The classifier ships with a large desktop test table and the
  fail-toward-text rule is a pinned test, not a convention. Equality
  tolerance must come from the dimension type's own rounding, not a global
  epsilon, or half the retypes read as contradictions and the tool loses
  trust on day one. Whole-model scans on large sets are the performance
  trap — per-view collectors keep it bounded and the status line streams
  progress. Modeless staleness (a dim deleted after scan) must be a named
  skip, never an exception out of the bridge handler.
- **Tests** — `test_dim_overrides_state.py`: the parser table
  (feet-inch-fraction, metric, affixes, ±, dual units, garbage), classifier
  classes, tolerance-from-accuracy math, fail-toward-text pin, plan
  building with group/ownership exclusions. `test_dim_overrides_command_names.py`:
  new-panel bundle metadata, persistent-engine flag, XAML↔handler wiring for
  both windows, icon sizes, IronPython AST scan, forbidden-API pins.
  `test_dim_overrides_revit.py`: adapter against fakes — single vs
  multi-segment dims, FormatOptions `UseDefault` fallback, ForgeTypeId vs
  legacy units generations, checkout-status and deleted-element guards,
  per-row rollback zeroing counters.

## UI description

**Main window** ("Dim Overrides") — modeless, resizable, persistent engine.
Header: "Dim Overrides" over DimGray subtitle "Every overridden dimension,
and whether it tells the truth." A slim scope card on top: scope ComboBox
(Active view / Print set / Whole model), a print-set ComboBox that enables
only for that scope, and a Scan button. Body: read-only WPF table grouped by
sheet then view — Override, Measured, Class, and a Show button per row that
selects and zooms via ExternalEvent. Above the table, filter checkboxes —
Contradictions / Text / Retypes / Affixes — plus a live search box; filters
flip visibility, so checks survive. Per-row checkboxes stage rows for
stripping; staged rows render red until Apply. Rows the tool will not touch
grey out with the reason in a tooltip rather than vanish. Footer: status
text left; right, "Strip Checked…" (primary, disabled with tooltip when
nothing stageable is checked) and Close.

> "214 overrides — 3 contradictions, 41 text, 158 retypes, 12 affixes. 6 skipped: 4 in groups, 2 owned by another user."

**Strip confirmation window** — small modal over the main window. Counts by
class of what will be stripped, the named-exclusion list, and two
acknowledgement checkboxes: "Stripping inside a group edits every instance
of that group" (enables the group rows) and "The typed text is discarded;
the dimension returns to its measured value" (gates Strip itself). Strip
(`IsDefault`, disabled until acknowledged, reason in tooltip) and Cancel
(`IsCancel`). Footer status:

> "12 dimensions will be stripped — 2 contradictions, 10 retypes. 4 group rows excluded."

After commit the Main window doubles as the report: stripped rows re-read
from the model, red cleared, status updated —

> "12 stripped, 0 failed — read back from the model. One undo step."

### User operation flow

1. Click **Dim Overrides** on the Annotation panel. The Main window opens
   modeless; pick a scope and press Scan.
2. The table fills, grouped by sheet then view; the footer gives the class
   census. Filter to Contradictions first — that is the red list.
3. Press Show on a row: the dimension is selected and zoomed via the
   ExternalEvent while the window stays open. Fix the model, or decide the
   override goes.
4. Check rows to strip; they stage red. A greyed row explains itself in its
   tooltip ("in a group — acknowledge in the confirmation to include").
5. Press **Strip Checked…**; the Strip confirmation window opens with counts
   and exclusions. Tick the acknowledgements that apply.
6. Strip commits one assimilated TransactionGroup; a refused row (checked
   out elsewhere since the probe) rolls back alone and lands in the skip
   ledger as a named skip, never a failure of the batch.
7. The Main window re-reads the affected rows from the committed model and
   reports. One Ctrl+Z in Revit restores every override just stripped.
8. Cancel path: Cancel in the confirmation returns to the table with stages
   intact and the model untouched; closing the Main window ends the session
   — declined choices are skipped, never failed.

## See also

- Existing: **Tags Sweep** and **Tag Align** — annotation QA neighbors and
  the panel's future co-residents; **Print Sheets / Print Set pulldown** —
  source of the print-set scope via lib `print_sets`.
- Rank 11 **Reference Check** and rank 45 **Text Types** — the other
  Annotation-panel founders; together the three make the panel worth its
  ribbon space.
- Rank 02 **Parameter Check** — the same audit-then-fix, named-skip QA shape
  applied to parameters instead of dimensions.
