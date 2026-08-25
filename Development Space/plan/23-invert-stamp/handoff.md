# 23 — Invert Stamp

Writes datum-honest invert elevations onto pipes, fittings, and fixtures — survey point included, approximations flagged, one undo step.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 23 of 45 | Misc Tools (next to Slope) | yes | M | 8/10 | 8/10 |

## Main purpose

Civil coordination and underground plumbing sheets need invert elevations at
drains, cleanouts, and pipe ends — and they need them relative to the survey
point, because that is the datum the civil drawings speak. Revit's newer
end-elevation parameters exist on pipes only, report against the reference
level, and do not exist on fittings or fixtures at all. So people compute
inverts by hand and type them into tags, and the numbers rot the first time
the routing drops an inch for a duct crossing. Nobody re-checks; the wrong
invert is found by the site contractor.

Invert Stamp computes the invert per element from connector geometry and
writes it, formatted, into a shared parameter the user picks by name — so
tags and schedules read a real parameter instead of typed text, and a re-run
after re-routing refreshes every value in one undo step. The math is honest
about what the API exposes: pipes get true inverts (centerline minus inside
radius); fittings and fixtures expose only connector outside radius, so
their values are computed off OD/2 and every such row is flagged
"outside-diameter approximation" in the plan — never silently mixed with the
exact ones. The datum is an explicit choice — Internal Origin, Project Base
Point, Survey Point, or a Level — with a live sample value shown before
anything is written, because a flipped basis is the classic way this
calculation goes wrong.

Nothing else covers this. Native end elevations stop at pipes and cannot be
re-based to the survey point; no existing EasyBIM tool writes elevations at
all — Slope, its ribbon neighbor, is about gradient, not datum; and the
Dynamo invert graphs in circulation carry no dry-run grid, no named skips,
and no portable preset, so each office rebuilds one badly. The stamped
parameter also gives 03 Slope Check's audit findings a place to become
drawing content, which is why the two sit side by side in the plan.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Invert Stamp.pushbutton/` with thin
  `script.py`, `bundle.yaml` (two-line title "Invert\nStamp", narrative
  tooltip naming the OD approximation up front, `author: Ruiming Liu`),
  96×96 icons. Misc Tools has no panel-level `bundle.yaml`, so ordering is
  alphabetical — "Invert Stamp" lands near Slope on its own; do not add a
  panel layout file just for adjacency. Four-layer split:
  `invert_stamp_state.py` (invert math, end rules, epsilon handling, plan
  builder — pure), `invert_stamp_revit.py` (collectors, connector reads,
  base points, writer), `invert_stamp_ui.py`, with `InvertStampWindow.xaml`
  and `InvertStampReport.xaml`. Presets (datum + parameter name + scope
  rules + end rule) persist by name under `%APPDATA%\EasyBIM\Invert Stamp\`
  — identity by name, never ElementId, so the same preset works in the next
  document. Nothing hoists yet; the base-point reader is the declared
  candidate if 27 Site Check builds later and wants the same collectors.
- **Revit API route** — scope is selection, active view, or picked piping
  systems (a checkbox list of `PipingSystem` names — no PickObject, the
  flow stays modal). Collection is one `FilteredElementCollector` (view-
  scoped when applicable) with an `ElementMulticategoryFilter` over
  `OST_PipeCurves`, `OST_PipeFitting`, `OST_PlumbingFixtures`,
  `OST_PipeAccessory`. Connectors: `MEPCurve.ConnectorManager` for pipes,
  `FamilyInstance.MEPModel.ConnectorManager` for the rest — both behind
  guards, a missing manager is a named skip — keeping only
  `Domain.DomainPiping` connectors. Per connector: `Origin` (XYZ) and, for
  round shapes, `Radius`; non-round connectors are a named skip. Pipe
  inside radius from `RBS_PIPE_INNER_DIAM_PARAM` (`AsDouble`, internal
  feet), the BuiltInParameter resolved by name the way Slope resolves its
  slope parameters. Datums: the two `BasePoint` elements via
  `OfClass(BasePoint)` — `IsShared` True is the survey point — read once
  into plain floats; a Level datum uses `Level.ProjectElevation`. All math
  runs in internal feet inside state; conversion happens at the write/
  display edge only. Writing: the picked parameter looked up by
  `Definition.Name` per element, `IsReadOnly` checked, and in workshared
  models a `WorksharingUtils.GetCheckoutStatus` probe first. A Double/
  Length parameter gets `Set(internal_feet)`; a String parameter gets
  `UnitFormatUtils.Format` output per project units (format frozen at
  write time — the choice is shown, not assumed). Commit is one
  assimilated `TransactionGroup`, one nested `Transaction` per element,
  counters zeroed on rollback. Modal and short-lived: no ExternalEvent, no
  Idling, no version gating beyond the BIP-name and `MEPModel` guards.
- **The plan/apply cycle** — `build_plan` computes, per element: the
  governing connector (a fitting's invert is its lowest piping connector —
  stated, and the row names which), the invert under the chosen datum, the
  exactness flag (true ID vs OD approximation), and the current parameter
  value read back for the old → new column. One plan object feeds both the
  staged grid and the executor. Values equal to the current one within the
  format epsilon are "skipped — already current". Sloped pipes whose two
  end inverts differ beyond epsilon are skipped — "sloped, ends differ,
  both listed" — unless the user picks an end rule: lower end, higher end,
  or both ends into two parameters (a second parameter ComboBox appears).
  The one acknowledgement tick states the honest cost: "Stamped inverts are
  snapshots — they do not update when the routing moves." The Report
  window re-reads every written parameter from the committed model.
- **Edge cases & honest limits** — named-skip buckets: "parameter not
  bound to this category" (the ComboBox greys such targets per category up
  front, and the plan re-checks per element), "no readable piping
  connector", "non-round connector", "sloped — ends differ (no end rule
  chosen)", "read-only / owned by another user", "already current",
  "unchecked". The OD approximation is a flag, not a skip — the value is
  useful, but it must stay visible through plan, grid, and report, because
  wall thickness is simply not in the API for fittings. The tool refuses
  to guess wall thickness, refuses to write a length value into a text
  parameter or vice versa without showing which formatting rule applies,
  and refuses to stamp elements in linked documents — links are read-only
  and the tooltip says the stamping belongs in the link's own model.
- **Risks** — the survey-point sign flip is the classic bug: clipped vs
  unclipped base-point states change what `Position` means, and the
  defense is designed in, not tested in — the Datum card shows the chosen
  datum's offset from Internal Origin in DimGray plus a live sample ("at
  FD-1: 97.42 ft") so a flipped basis is caught by eye before anything is
  written, and the fakes cover clipped/unclipped variants. The OD
  approximation must survive every UI surface or the tool ships wrong
  numbers with confidence. Text-parameter stamping freezes number
  formatting at write time — acceptable, but only because the choice is
  displayed. Connector `Radius` throws on some content; every read is
  guarded into the skip bucket, never a crash.
- **Tests** — `test_invert_stamp_state.py` pins the invert math (ID path
  vs OD path), end rules on synthetic sloped pipes, the already-current
  epsilon, lowest-connector selection, and preset round-trip by name.
  `test_invert_stamp_command_names.py` pins bundle metadata, XAML↔handler
  wiring for both windows, icon sizes, and the IronPython AST scan.
  `test_invert_stamp_revit.py` drives fakes shaped like each API
  generation — missing `MEPModel`, non-round connectors, `Radius` that
  throws, clipped and unclipped base points, a read-only parameter rolling
  back one nested transaction and zeroing only its own counter — and
  asserts the snapshot is nothing but ints, floats, and unicode.

## UI description

**Main window** — resizable modal, header "Invert Stamp" over a DimGray
subtitle: "Pipes use inside diameter; fittings and fixtures are
outside-diameter approximations, flagged." Three cards across the top.
**Scope card**: source ComboBox (Selection / Active view / Piping systems)
over a checkbox list of collected elements with live-filter Search and the
count line ("124 elements — 118 checked, 6 unchecked."), Select All /
Select None. **Datum card**: ComboBox (Internal Origin / Project Base
Point / Survey Point / Level…), the implied offset in DimGray ("Survey
Point is 312.50 ft below Internal Origin"), and the live sample line ("at
FD-1: 97.42 ft"). **Target card**: parameter ComboBox (instance-bound
Length or Text parameters; entries not bound to every scoped category
greyed with tooltip "not bound to Pipe Fittings"), the formatting rule
line ("Length parameter — displays per project units"), the end-rule
ComboBox (Skip sloped / Lower end / Higher end / Both ends), and a preset
Save/Load row. Below, the **staged grid**: Element, Connector, Old, New —
every write red until Apply, OD rows carrying a small "OD" note, skip rows
greyed with their bucket named. Footer: status left, the acknowledgement
checkbox, **Apply** (`IsDefault`, disabled with tooltip until ticked),
**Cancel** (`IsCancel`).

> "Will write 118 values (31 OD-approximate), skip 6 — reasons listed. One
> undo step."

> "Sloped pipes: 12 skipped — ends differ. Pick an end rule to include
> them."

**Report window** — read-only table after commit: Element, Parameter,
Value (read back from the model), Result; skips under named buckets,
rollbacks separate.

> "116 written, 6 skipped, 2 rolled back — read back from the model."

### User operation flow

1. Ribbon: Misc Tools → Invert Stamp (Slope's neighbor). The Main window
   opens on the current selection, or empty with Active view offered.
2. Pick scope; the element list and count line fill. Pick the datum —
   check the DimGray offset and the live sample against a known invert.
3. Pick the target parameter; if it is greyed for a scoped category, the
   tooltip names the missing binding — bind it via Load Parameters first.
4. The staged grid fills red. Sloped pipes sit skipped until an end rule
   is chosen; OD rows show their note. Uncheck anything you disagree with.
5. A skipped item looks like: "Elbow id 401227 — non-round connector" or
   "P-12 — sloped, ends 96.10 / 95.85 — no end rule chosen".
6. Tick "Stamped inverts are snapshots…"; **Apply** enables and commits
   one TransactionGroup — one undo step; a checked-out element rolls back
   alone.
7. The Report window reads values back from the committed model. Tags
   pointing at the parameter now show real inverts; Ctrl+Z reverts all.
8. Routing moved next week? Re-open, Load the preset, re-run — "already
   current" rows skip, moved rows stage red again.
9. Cancel path: **Cancel**/Esc before Apply closes with nothing written —
   declined rows are skipped, never failed.

## See also

- Existing: **Slope** (ribbon neighbor; gradient where this is datum, and
  the donor of by-name BIP resolution and parameter-writability manners),
  **Parameter Copy** (origin of the shared unit-conversion split),
  **Load Parameters** (how the target shared parameter gets bound first).
- Rank 03 **Slope Check** — audits the fall this tool then documents; its
  handoff already points back here.
- Rank 27 **Site Check** — the upstream sanity check that the survey point
  this tool re-bases to is actually agreed across links.
- Rank 13 **Sleeve Place** — the other elevation-critical coordination
  writer, one panel over in the planned Coordination pulldown.
