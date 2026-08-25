# 12 — Legend Place

Place and re-align named legends and schedules at the same spot across the whole sheet set.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 12 of 45 | Sheet | no | M | 8/10 | 7/10 |

## Main purpose

The general-notes legend, the symbols legend, and the abbreviations schedule
have to sit on every sheet of the set, in the same spot on every sheet.
Legends and schedules are the two views Revit lets you place on many sheets —
but only by dragging them onto one sheet at a time, by eye. When the standard
position moves half an inch, sixty sheets each need the same drag, and the
three sheets somebody nudged are invisible until the set is printed. Keeping
this furniture aligned across the set is a ~900-vote Revit Ideas request that
Autodesk has not touched.

Legend Place works from a reference sheet where the furniture already sits
correctly. It reads that sheet's legend viewports and schedule instances,
records each one's offset from the title block — using the datum discipline
Sheet Align established, extended with a picked-corner option so offsets stay
meaningful across mixed sheet sizes — and then, for every ticked target sheet,
either places what is missing or also moves what drifted, per an explicit mode
choice. Identity is by view name throughout, never ElementId, so the same
saved "sheet furniture" preset replays on the next project and anything the
new model lacks is named in the load report rather than silently dropped.

The re-align mode is the headline, not the one-shot placement: the free
ecosystem's answer (Dynamo copy-legends graphs) places once and walks away,
which solves the easy day and not the maintenance. Here the tool is
re-runnable by design — change the standard, re-run, and the whole set snaps
back, with "already in place" as a named skip so the report tells you how
much of the set was actually straight. Inside EasyBIM it fills a real hole:
Sheet Align moves a sheet's *whole contents* as one block, Linked Sheets
Transfer rebuilds a *link's* sheets — nothing yet places and aligns individual
named items across your own set. Rank 12: every project needs it, weekly, and
the only competition is dragging.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Sheet.panel/Legend Place.pushbutton/` beside Sheet Align.
  `legend_place_state.py` (offset maths from plain floats, plan bucketing,
  tolerance test, preset serialization — names only), `legend_place_revit.py`
  (reference-sheet read, per-sheet create/move executor),
  `legend_place_ui.py` + XAML for the three windows. Composes
  `lib/easybim/sheet_geometry` (paper-space maths, mm conversion) and
  `lib/easybim/sheet_titleblocks` (finding the datum instance). Presets are a
  JSON file per computer (`%APPDATA%\pyRevit\pyRevit_EasyBIM_LegendPlace_presets.json`,
  the Tag Align precedent), holding datum choice, item names, and offsets —
  no ElementIds. 16 Panel Sheets will also create `ScheduleSheetInstance`s in
  planned positions; whichever builds second hoists the shared
  place-and-nudge helper to `lib/easybim/sheet_furniture.py`.
- **Revit API route** — Reference read:
  `ViewSheet.GetAllViewports()` → keep viewports whose viewed view has
  `ViewType.Legend`; `FilteredElementCollector(doc, sheet.Id)
  .OfClass(ScheduleSheetInstance)` for schedules, excluding rows whose
  `ViewSchedule.IsTitleblockRevisionSchedule` is true (those live inside the
  title block family and must not be counted as furniture). Datum: the title
  block instance via `sheet_titleblocks`; either its origin (Sheet Align's "By
  Title Block Origin") or a picked corner of its sheet-space bounding box —
  the corner option is what keeps "the same spot" meaningful when A1 and A0
  sheets mix, because furniture rides the frame edge, not the sheet centre.
  Anchor point per item is the **top-left of its outline**
  (`Viewport.GetBoxOutline()`; the schedule instance's
  `get_BoundingBox(sheet)`), because schedules grow downward as rows change —
  top-left is the point a drafter actually aligns. Creation is a two-step
  *create-then-nudge*, because outline size is unknowable before creation:
  `Viewport.Create(doc, sheetId, viewId, point)` (gated per pair by
  `Viewport.CanAddViewToSheet` plus a probe that the legend is not already on
  that sheet) or `ScheduleSheetInstance.Create(doc, sheetId, scheduleId,
  point)` at the approximate spot, then read the real outline and
  `ElementTransformUtils.MoveElement` by the residual delta. Moves of
  existing items use the same delta move; pinned items are unpinned, moved,
  and re-pinned, with a pin that will not go back reported, not silenced
  (Sheet Align's rule). Viewport type and `Viewport.Rotation` are matched
  from the reference where the property exists (capability probe). Writes run
  in one assimilated `TransactionGroup` "Place legends and schedules" with a
  nested `Transaction` per sheet, so one bad sheet rolls back alone. No
  ExternalEvent and no Idling — one modal window, one write command; the only
  pick UI (the datum corner) uses the STEP_* verb-string pattern so
  `PickPoint` never runs under the modal.
- **The plan/apply cycle** — `build_plan` computes, per target sheet and per
  item: **create** (missing; with the target point), **move** (present but
  off by more than the 0.25 mm tolerance; with the printed-mm delta),
  **already in place** (named skip), **no title block** (named skip — with no
  datum the position is unknowable, so the sheet is refused rather than
  guessed), plus the smaller named skips below. The confirmation window shows
  the complete dry run before anything writes — every sheet, every item,
  every skip with its reason. No acknowledgement tick: nothing here is
  destructive (no deletes, one undo step), so the plan itself is the gate.
  After commit the report re-reads the placed items' actual positions and
  states residual deltas, so "moved into place" is a measured claim, not an
  intention; counters zero for any sheet that rolled back.
- **Edge cases & honest limits** — Named skip buckets: "no title block",
  "two title blocks — datum ambiguous", "placeholder sheet — cannot take
  viewports" (`IsPlaceholder`), "already in place (within 0.25 mm)", "two
  copies of {name} on this sheet — cannot tell which is standard, left
  alone", "legend already on this sheet" (create mode when Revit refuses the
  duplicate), and on preset load "not in this model: {names}" — named, never
  silently dropped. Honest limits, stated in tooltip and README: the tool
  aligns, it does not detect overlaps — a schedule's placed size depends on
  its own formatting, so "same top-left" can still collide with a crowded
  sheet's viewports, and pretending to check that would be a lie; the
  reference sheet itself is never modified; revision schedules inside title
  blocks are out of scope by design; "the same spot" across mixed title
  block families is only as meaningful as the chosen datum, and anything the
  datum cannot anchor is a skip, not a guess.
- **Risks** — The create-then-nudge depends on the created outline being
  readable immediately inside the transaction (regenerate before reading —
  the classic stale-outline trap `sheet_geometry`'s docstring warns about:
  outlines move when content changes, so read *after* every write, never
  before). Mixed sheet sizes are genuinely ambiguous and the datum choice
  only covers the honest cases — resist adding per-size offset tables in v1.
  `Viewport.Rotation` and the exact `ScheduleSheetInstance` anchor semantics
  vary by generation — probe, and pin both shapes in fakes. Schedule height
  changes between plan and apply (another user edits filters mid-run) can
  turn "move 3 mm" into a different residual — the post-commit measured
  report is the mitigation, not a tighter promise.
- **Tests** —
  - `test_legend_place_state.py` pins the offset maths against
    `sheet_geometry` fixtures: datum-corner vs origin offsets, top-left
    anchoring, tolerance bucketing, mixed-size scenarios, plan bucket
    ordering, preset round-trip with missing-name reporting, and
    report-counter zeroing on rollback.
  - `test_legend_place_command_names.py` pins bundle.yaml metadata, XAML
    handler wiring for all three windows, 96×96 icon pairs, the IronPython
    AST scan, and zero Revit imports in the state module.
  - `test_legend_place_revit.py` drives the adapter against fakes: the
    titleblock-revision-schedule exclusion, `CanAddViewToSheet` refusal, the
    create-then-nudge residual, pin/unpin/re-pin including the pin that will
    not re-pin, placeholder sheets, the two-copies ambiguity, and per-sheet
    rollback.

## UI description

**Main window** — resizable modal. Header: "Legend Place" over "Put the same
legends and schedules on every sheet, in the same place." Top row: reference
sheet ComboBox (defaults to the active sheet) beside the datum ComboBox
("Title block origin" / "Picked corner of title block…" — picking uses the
verb-string wizard hop). Two cards side by side: left, the furniture found on
the reference sheet as a checkbox list with count line ("3 selected, 1
unchecked.") — rows like "Legend: General Notes — 12.5, 261.0 mm from datum";
right, target sheets with live search, Select All / Select None, and its own
count line. Below: the mode radio pair — "Place missing only" / "Place and
re-align existing". Footer status left: "58 sheets ticked; 4 items from
A-101." Buttons right: **Preview…** (primary), **Save preset**, **Load
preset**, **Cancel**. Example status after a preset load: "Preset 'Office A1
furniture' loaded — not in this model: Legend 'Symbols (Metric)'."

**Confirmation window** — the complete dry run as a read-only grouped table,
one expander per bucket with counts: Create (112), Move (40 — each row with
its mm delta: "A-104: General Notes — move 12.3 mm"), Already in place (6),
Skipped (2 — "A-902: no title block"). Footer status: "58 sheets — 112
placed, 40 moved, 6 already in place, 2 skipped (no title block)." Buttons:
**Apply** (primary), **Back**, **Cancel**.

**Report window** — same buckets, read back from the committed model, rows
now measured ("A-104: General Notes — in place, residual 0.0 mm"), failures
with Revit's message and their sheet rolled back. Footer status: "111 placed,
40 moved, 1 failed (rolled back), 8 skipped. One undo step." Button:
**Close**.

### User operation flow

1. Ribbon: Sheet → Legend Place. Main window opens; the active sheet is
   pre-selected as reference and its furniture is listed with offsets.
2. Optionally switch the datum to "Picked corner…" — the window hides via the
   STEP_* verb, `PickPoint` snaps to the title block corner, the window
   returns with offsets recomputed.
3. Tick target sheets (search "A-1" to narrow), pick the mode — after a
   standards change, "Place and re-align existing" is the point.
4. **Preview…** builds the plan and opens the Confirmation window. Nothing
   has been written.
5. Cancel path: **Cancel** or **Back** here costs nothing — the model is
   untouched, selections survive Back.
6. **Apply** commits sheet by sheet under a cancellable progress bar;
   cancelling stops the remaining sheets, keeps the committed ones as the
   single undo step, and the report says so.
7. The Report window reads back the result. A skipped item reads exactly as
   planned — "A-902: skipped — no title block" — and a sheet that failed
   mid-write appears once, under Failed, with its whole sheet rolled back.
8. **Close**. Re-run any time the standard moves; "already in place" counts
   tell you how straight the set already was. One Ctrl+Z undoes the run.

## See also

- Existing EasyBIM: **Sheet Align** — the datum precedent and the
  whole-sheet-block counterpart to this per-item tool; **Linked Sheets
  Transfer** — the other create-and-position machine, and the source of the
  `sheet_geometry` discipline; **Sheet Manager** and the **Print Set**
  pulldown (set-scope neighbours); **Tag Align** — the preset
  save/load-by-name precedent.
- Plan siblings: **16 Panel Sheets** — shares the schedule-instance
  placement core (the `sheet_furniture` hoist rule above); **31 Detail
  Renumber** — the same sheet-tidiness shelf; **11 Reference Check** and
  **29 Issue Register** — the pre-issue hygiene set this belongs to.
