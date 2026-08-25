# 16 — Panel Sheets

Creates every missing panel schedule view and flows them onto sheets in
titleblock-fitted columns — the addendum hour of dragging, gone.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 16 of 45 | Misc Tools — Circuiting pulldown | yes | M | 8/10 | 8/10 |

## Main purpose

Every distribution board needs a panel schedule, and every panel schedule
needs a home on a sheet. Late in a project someone creates the missing
schedule views one at a time, then drags them onto sheets three per column,
eyeballing the gaps against the titleblock. It is an hour of clicking — and
it gets redone in full every time an addendum adds six panels, because
nothing remembers which panels are already done.

Panel Sheets is a two-stage batch over exactly that. Stage one finds every
piece of electrical equipment that has circuits but no `PanelScheduleView`
and plans the view creations, template chosen per panel kind. Stage two
plans placement: each unplaced schedule flows into a column grid computed
inside the titleblock margins, onto existing sheets by number or onto new
sheets numbered from a seed. The hard truth of this API — a schedule
instance's footprint is only knowable after it exists — is met head-on:
commit is place-then-measure-then-move inside each item's own nested
transaction, and an item that overflows its column rolls back alone and is
reported "skipped — does not fit", never overlapped. Panels already done
are named skips, sheets are identified by number and panels by name, and
the whole run is one undo step.

Sheet Manager, Print Sheets, and Sheet Align manage sheets and viewports;
none of them can touch panel schedules, because `PanelScheduleView` is its
own class with its own sheet-instance type — a different API species from
viewports and `ScheduleSheetInstance`. Native Revit offers one-at-a-time
drag placement and nothing else; the free ecosystem has schedule placers
for regular schedules but not panel schedules. This tool composes
`sheet_geometry` and `sheet_titleblocks` instead of rebuilding layout math,
and shares the panel-by-name identity the whole Circuiting pulldown runs
on. Usefulness is electrical-team-shaped rather than office-wide, which is
what holds an 8/8 idea to rank 16.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Circuiting.pulldown/Panel Sheets.pushbutton/`
  (add to the pulldown's `layout:`), thin `script.py`, `bundle.yaml`
  (two-line title "Panel\nSheets", narrative tooltip, `author: Ruiming
  Liu`), 96×96 icons. Four-layer split in the pushbutton:
  `panel_sheets_state.py` (column-flow packing, occupied-zone avoidance,
  sheet-number sequencing with collision pre-check, bucket classification,
  plan builder — pure Python), `panel_sheets_revit.py` (collectors,
  create/place/measure/move, read-back), `panel_sheets_ui.py`,
  `PanelSheetsWindow.xaml` + `ReportWindow.xaml`. The brainstorm sketched a
  Print-Set-shaped wizard, but the house rule reserves the STEP_* wizard for
  flows that must interleave Revit pick UI; this flow never picks, so it is
  one Main window with cards and a staged grid, staying open after Apply.
  Reuse from lib: `sheet_titleblocks.first_title_block` and
  `location_point` for the margin box, `sheet_geometry`'s arithmetic
  helpers for the grid. The titleblock-relative placement math is the
  declared hoist candidate shared with **12 Legend Place** — whichever
  builds second lifts a common `sheet_layout` module.
- **Revit API route** — panels via `FilteredElementCollector`
  `OfCategory(OST_ElectricalEquipment)` instances; "has circuits" through
  `MEPModel.GetAssignedElectricalSystems()` behind a `hasattr` probe.
  Existing views via `OfClass(PanelScheduleView)`, mapped to their panel
  through `GetPanel()` behind the same probe — a view whose panel cannot be
  resolved is a named skip, not a guess. Templates via
  `OfClass(PanelScheduleTemplate)`, bucketed by `GetPanelScheduleType()`
  (branch panel / switchboard / data) so kinds are never cross-applied.
  Stage one: `PanelScheduleView.CreateInstanceView(doc, panel_id)` — the
  signature is version-sensitive and gets the capability-probe treatment;
  it creates against the panel's default template, and applying the chosen
  template afterwards is probed too, falling back to the default *with the
  substitution named on the row*, never silently. Stage two:
  `PanelScheduleSheetInstance.Create(doc, view_id, sheet_id)` at origin,
  `doc.Regenerate()`, measure via `get_BoundingBox(sheet_view)`, then move
  to the planned cell (the `Origin` setter where present,
  `ElementTransformUtils.MoveElement` otherwise). Occupied zones on target
  sheets — existing viewports via `Viewport.GetBoxOutline()` and existing
  schedule instances via their bounding boxes — are collected once so the
  flow packs around them. New sheets via `ViewSheet.Create(doc,
  titleblock_type_id)` with numbers from the state sequence. Commit shape:
  one assimilated `TransactionGroup`, one nested `Transaction` per action
  (view creation, sheet creation, placement including its measure-and-move)
  — a placement that does not fit rolls back alone. The batch runs under
  the cancellable `forms.ProgressBar` reserved for long writes; no
  ExternalEvent or Idling — the window is modal-shaped and self-contained.
- **The plan/apply cycle** — `build_plan` classifies every panel (needs
  view / needs placing / already done / no circuits), assigns flow order
  and target columns, sequences any new sheet numbers with a collision
  pre-check against existing numbers (a collision is a red plan error that
  disables Apply — refused up front, not discovered mid-commit), and emits
  one action list read by both the staged grid and the executor. Because
  exact footprints resolve only at commit, the plan promises order and flow
  rules, not coordinates — the preview draws column bands and the flow
  sequence, with a DimGray note "Exact fit is measured at Apply." Every
  action renders as a red row — "Create view (Branch, 'BP 42-row') —
  PP-1A", "Place PP-1A on E-601, column 2", "Create sheet E-603" — until
  Apply; skips grey with their reason. No acknowledgement tick is needed:
  nothing is deleted, and the whole run is one undo step. After commit the
  Report window reads back from the committed model — which views exist,
  which instances sit on which sheet — never from the plan, and rollbacks
  zero their counters so the report never claims work that is gone.
- **Edge cases & honest limits** — named-skip buckets: *"already on
  E-600"* (placed schedules are never re-placed or nudged); *"no circuits —
  nothing to schedule"* (listed, default-unchecked, checkable for panels
  awaiting circuits); *"does not fit — rolled back"* (the measured height
  overflowed every remaining cell); *"sheet has no titleblock — margins
  unknown"*; *"owned by another user"* via the worksharing checkout probe
  before writes; *"creation failed — rolled back"* for equipment kinds
  whose `CreateInstanceView` throws (transformers and disconnects vary by
  version — the rollback is the detector, the bucket is the answer). A
  template whose schedules are taller than a full column can legitimately
  fit nothing; the tool reports "template 'SWBD 34-row' fits zero per
  column at these margins" rather than shrinking, overlapping, or scaling
  anything. The tool refuses to guess which template a panel kind should
  get (one ComboBox per kind bucket, switchboards never receive branch
  templates), refuses to reflow schedules it did not place, and does not
  touch panel schedule *content* — templates own that.
- **Risks** — footprint-after-creation is the load-bearing constraint: the
  fit check must live inside each nested transaction with a `Regenerate()`
  before measuring, which makes regen-per-item the slow path — the
  ProgressBar and per-item rollback make it safe, and the handoff states
  plainly that a 40-panel run is minutes, not seconds. `CreateInstanceView`
  and the template-apply surface drift across releases and need
  capability probes with the default-template fallback named per row.
  Cross-applying switchboard and branch templates is the silent-corruption
  case the kind buckets exist to prevent; the state tests pin that a
  mismatched pairing cannot be expressed in a plan. Occupied-zone data
  (viewport outlines) can lie on sheets with hidden or empty viewports —
  err toward treating a zone as occupied; a wasted cell is recoverable, an
  overlap is a redline.
- **Tests** — `test_panel_sheets_state.py` pins the packing math (column
  flow, occupied-zone avoidance, overflow cascade to next column and next
  sheet), sheet-number sequencing at collision boundaries, kind-bucket
  classification, and that a cross-kind template assignment is
  unrepresentable in a plan. `test_panel_sheets_command_names.py` pins the
  grown pulldown layout, bundle metadata, XAML↔handler wiring for both
  windows, icon sizes, the IronPython AST scan, and the forbidden-API
  pins. `test_panel_sheets_revit.py` drives the adapter over fakes shaped
  like each API generation — `CreateInstanceView` signatures and throws,
  `GetPanel()` absent, template setter absent (substitution named),
  bounding box only after regenerate, `Origin` setter absent with the
  MoveElement fallback, checkout-status skip, per-item rollback zeroing
  its counter.

## UI description

**Main window** — resizable modal, `Grid Margin="14"`, rows Auto/*/Auto.
Header "Panel Sheets" SemiBold ~30px over a DimGray subtitle "Creates
missing panel schedule views and places them inside titleblock margins.
Placed schedules are never moved." Two cards side by side. Left, **Panels
card**: checkbox list — "PP-1A — needs view + placing", "LP-2 — needs
placing" — with done rows greyed ("already on E-600" in the tooltip),
count line "23 panels — 14 need a view, 9 need placing, 5 already done.",
Select All / Select None, live-filter Search (panel names by substring,
numbers by token). Right, **Placement card**: one template ComboBox per
kind bucket present (Branch / Switchboard — a bucket with no checked
panels greys its ComboBox), a target ComboBox "Existing sheets by number /
New sheets numbered from…" with the seed TextBox, a columns-per-sheet
spinner, margin fields, and the preview canvas drawing column bands, flow
order, and hatched occupied zones with the note "Exact fit is measured at
Apply." Below both cards: the staged action grid, every row red until
Apply, skips greyed and reasoned, collisions rendered as red errors that
name the clash. Footer: status TextBlock left, then **Apply** (`IsDefault`,
110×35, disabled — never hidden — with the tooltip naming the missing
template or the number collision) and **Cancel** (`IsCancel`).

> "14 views and 9 placements staged across 3 sheets (1 new) — 5 panels already done, skipped."

> "Sheet number E-603 already exists — placement onto new sheets is blocked until the seed changes."

During Apply the cancellable `forms.ProgressBar` shows "Placing LP-2 on
E-602 (7 of 9)…" — Cancel stops before the next item; finished items stand
and the report says exactly where the run stopped.

**Report window** — read-only WPF table after commit: one row per action —
Panel, View (created / existed), Sheet, Column, Result — read back from the
committed model, with skips under their named buckets and rollbacks listed
apart from skips. Footer:

> "14 views created, 8 placed, 1 did not fit (rolled back, named), 5 skipped — read back from the model. One undo step."

### User operation flow

1. Ribbon: Misc Tools → Circuiting → Panel Sheets. The Main window opens;
   the panel scan fills the list and count line.
2. Check the panels to process; pick a template per kind bucket; choose
   existing sheets or a new-sheet seed; set columns and margins. The
   preview redraws as settings change.
3. The staged grid fills red with every create/place action. Uncheck any
   row to decline it — "skipped — unchecked", never failed.
4. Press **Apply**. The ProgressBar ticks per item; each action commits in
   its own nested transaction inside one TransactionGroup.
5. A skipped item looks like: a grey grid row "MSB-1 — already on E-600"
   before Apply, or a report row "PP-4 — does not fit — rolled back" after
   — the schedule view it created in stage one still exists and is said
   so, ready for a hand placement.
6. The Report window opens, read back from the committed model. Close it;
   the window stays open for another pass, and one Ctrl+Z in Revit reverts
   the entire batch.
7. Cancel path: **Cancel**/Esc before Apply closes with the model
   untouched; Cancel during Apply stops at the next item boundary, the
   group assimilates what finished, and the report names where it stopped.

## See also

- Existing: **Circuit Schedule** and **Update Circuit Rating** (the
  pulldown's panel-by-name identity and per-item rollback shape), **Sheet
  Manager** (sheet creation and numbering manners, the staged-grid
  precedent), **Print Sheets** (the sheet-set instincts this feeds).
- Siblings: **12 Legend Place** (the same keep-it-inside-the-titleblock
  placement math for legends and regular schedules — the shared
  `sheet_layout` hoist partner), **01 Circuit Check** (run it before this
  puts schedules on paper), **18 Phase Balance** (reads the same panels'
  loads; workflow neighbor on the same pulldown).
