# 31 — Detail Renumber

Renumber detail viewports by where they sit on the page — two-passing
Revit's duplicate-number constraint so the swap dance can never abort.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 31 of 45 | Sheet | no | S | 8/10 | 7/10 |

## Main purpose

After details get shuffled between sheets for a week, the numbers on a sheet
read 7, 2, 9, 4 in no order that matches where anything sits on the page.
Renumbering by hand is the swap dance — Revit blocks duplicate detail
numbers per sheet, so every exchange needs a temporary number in the middle
— and doing that across thirty sheets before an issue is an afternoon of the
same three clicks. The sheet somebody missed is the one found on the plot,
where the eye walks the page looking for detail 5 and the numbers lead it
nowhere.

Detail Renumber reads the order off the page. Tick sheets, pick a reading
convention, and every viewport is renumbered by its position: centres from
`Viewport.GetBoxCenter()`, normalised against the title block origin,
banded into rows, sorted by the chosen convention. The write is a strict
two-pass inside per-sheet groups: first probe-checked temporary numbers, so
Revit's per-sheet uniqueness constraint can never abort a swap halfway, then
the finals. A detail number is the identity callouts and section marks
print, so renumbering re-points every "5/A-501" reference automatically —
the tool changes what the page says, never what links to what. The pure
logic — banding, ordering, two-pass planning — lives in the state module and
is desktop-tested against synthetic layouts, which is what keeps this an
S-effort tool.

Nothing in the inventory touches detail numbers; Sheet Manager edits
sheet-level fields, not viewport identity. Native Revit has no
renumber-by-position at all. Per the prior-art note, pyChilizer and EF-Tools
renumber viewports along a spline or a pick order — the user tells them the
sequence, one sheet at a time — and the well-known Dynamo graphs die on the
duplicate-number constraint instead of two-passing it. The by-position
two-pass with a staged preview, per-sheet rollback, and named skips is the
differentiator, and it is why this earns a slot despite being small: it is
the last hour before every issue, automated.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Sheet.panel/Detail Renumber.pushbutton/`. `script.py` thin;
  one modal main window plus a report window, no bridge, no persistent
  engine. `bundle.yaml` two-line title "Detail\nRenumber", narrative
  tooltip naming the two-pass and the box-centre rule,
  `author: Ruiming Liu`. Split: `detail_renumber_state.py` (row banding,
  the reading conventions, two-pass move planning, temp-number generation
  against a reserved set, skip bucketing — pure Python over plain dicts),
  `detail_renumber_revit.py` (snapshot: sheets → viewports → centres,
  detail numbers, title block origin; executor),
  `detail_renumber_ui.py` + `DetailRenumberWindow.xaml` +
  `DetailRenumberReport.xaml`. Title-block origin comes from
  `lib/easybim/sheet_titleblocks` (`first_title_block`, `location_point`)
  — a third consumer of that module, no new hoist needed. The banding
  tolerance is a named module constant, not a knob, until a real project
  proves it needs one.
- **Revit API route** — Sheets via `FilteredElementCollector`
  `OfClass(ViewSheet)` (placeholder sheets have no viewports and are
  skipped by name). Per sheet: `ViewSheet.GetAllViewports()` → `Viewport`,
  `GetBoxCenter()` for position, the detail number read and written
  through `BuiltInParameter.VIEWPORT_DETAIL_NUMBER` (a string parameter —
  read `AsString`, write `Set`). `ScheduleSheetInstance`s are not
  Viewports and carry no detail number, so schedules are naturally out of
  scope; legend viewports are Viewports, hold detail numbers, and are in.
  Centres are normalised by subtracting the title block's
  `location_point`; a sheet with no title block falls back to raw sheet
  coordinates and is named in the report, never dropped; a sheet with two
  title blocks uses the first and says so (the `first_title_block`
  precedent). Transaction shape: one assimilated `TransactionGroup` per
  Apply, one nested `Transaction` per sheet holding both passes — pass 1
  writes temporary numbers probe-checked against *every* detail number on
  that sheet (including viewports outside the plan), pass 2 writes the
  finals — so a refused write rolls back its whole sheet alone and
  counters zero on rollback. No ExternalEvent, no Idling, no version
  gating: every API surface here predates 2015.
- **The plan/apply cycle** — `build_plan` computes per sheet: the
  normalised centres, the row bands (centres within one tolerance band
  count as one row), the ordered sequence under the chosen convention
  (left-to-right/top-down, right-to-left/top-down, top-down/left-to-right,
  and their bottom-up mirrors), and the old→new pair per viewport,
  numbering 1..N as plain integers. A viewport already holding its target
  is "already correct" — a named skip, never a write. A viewport whose
  detail number cannot be written (read-only parameter) keeps its number,
  which becomes *reserved*: a plan whose targets collide with a reserved
  number fails that sheet closed — "detail 3 is held by an unwritable
  viewport" — rather than renumbering around it into a lying sequence.
  The whole sheet is the unit: all its viewports renumber together, so
  the choice surface is which sheets, not which viewports. One plan
  object feeds the staged grid and the executor. Nothing here is
  irreversible — one Ctrl+Z reverts the lot — so there is no
  acknowledgement tick; the footer says "one undo step" instead. The
  report re-reads detail numbers from the committed model per sheet.
- **Edge cases & honest limits** — Named buckets: "already correct", "no
  title block — positions taken from sheet origin", "two title blocks —
  first used", "no viewports", "unwritable detail number — sheet not
  renumbered", "placeholder sheet", "declined / unchecked". Alphanumeric
  existing numbers ("A1") are replaced by integers and the staged grid
  shows exactly that before anything runs. Rotated viewports report a
  rotated box; the box centre is used regardless and the tooltip says so.
  The tool never moves viewports, never renames views, never renumbers
  sheets, and never invents an ordering it cannot show: the grid is the
  plan.
- **Risks** — Row banding tolerance is the judgement call: a tall section
  viewport's box centre sits far from its visual anchor, so a fixed band
  can misorder mixed-height rows. The mitigation is structural — the
  staged grid shows every old→new before a single write, and the
  tolerance stays a named constant until a real project argues for a
  knob. A viewport whose crop is far larger than its drawn content has an
  unrepresentative centre; same mitigation, same answer. The temp pass
  must generate numbers that collide with nothing on the sheet — the
  reserved-set probe covers viewports outside the plan, and the state
  tests pin it, because a temp collision is precisely the mid-swap abort
  this tool exists to prevent.
- **Tests** — `test_detail_renumber_state.py` pins banding fixtures
  (single row, mixed heights, near-band-boundary centres), every reading
  convention including the mirrors, two-pass planning over swap cycles,
  temp-number generation avoiding the reserved set, the
  reserved-collision sheet failure, skip bucketing, determinism (stable
  sorts — two runs, one answer), and counters zeroing on a sheet
  rollback. `test_detail_renumber_command_names.py` pins bundle metadata,
  XAML↔handler wiring for both windows, 96×96 icons, the IronPython AST
  scan, and forbidden-API pins. `test_detail_renumber_revit.py` drives
  the adapter against fakes: `GetAllViewports`/`GetBoxCenter` shapes, the
  no-title-block and two-title-block paths, a read-only detail-number
  parameter, a mid-pass write refusal rolling back its sheet, and the
  post-commit read-back.

## UI description

**Main window** — resizable modal, `Grid Margin="14"`, header "Detail
Renumber" over the DimGray subtitle "Renumber viewports by their position
on the sheet. The whole sheet renumbers together." Left card, "Sheets":
checkbox list with search, Select All / Select None, count line
"38 sheets — 12 checked, 26 unchecked." Right side: a "Reading order"
ComboBox (the six conventions, left-to-right/top-down default), then the
read-only staged grid — Sheet, View, Old, New — where every changed row
renders red until Apply and skip rows sit greyed with their bucket named.
Footer: status left, **Renumber** (`IsDefault`, disabled with a tooltip
until at least one sheet is checked), **Cancel** (`IsCancel`).

> "12 sheets — 87 to renumber, 5 already correct, 1 sheet without a title
> block (positions taken from sheet origin). One undo step."

> "E-501 not staged: detail 3 is held by an unwritable viewport."

**Report window** — read-only WPF table after commit, grouped per sheet:
View, Final # (read back from the model), Result. A rolled-back sheet is
listed whole under its reason. **Close** only.

> "11 sheets renumbered — 87 viewports read back. E-501 rolled back:
> Revit refused the write at detail 4. One undo step."

### User operation flow

1. Ribbon: Sheet → Detail Renumber. The snapshot runs; the sheet card
   fills.
2. Check sheets — search filters visibility without losing checks. Pick
   the reading order; the staged grid recomputes as either changes.
3. Review the red rows. A sheet that looks wrong (a tall section banded
   into the wrong row) is simply unchecked — "skipped — declined", never
   failed — and renumbered by hand later.
4. **Renumber**. Each sheet commits both passes in its own nested group
   inside one assimilated TransactionGroup.
5. The Report window opens with final numbers read back from the model. A
   skipped item reads: "M-402 — skipped: no title block; positions taken
   from sheet origin" (still renumbered, on raw coordinates, and named);
   a failed sheet reads whole: "E-501 — rolled back: Revit refused the
   write."
6. Close. Callouts and section marks already print the new numbers —
   nothing else to fix. One Ctrl+Z reverts every sheet the run touched.
7. Cancel path: **Cancel** (or Esc) before Renumber closes the window
   with the model untouched — planning never writes.

## See also

- Existing: **Sheet Align** (the `sheet_titleblocks`/`sheet_geometry`
  donors and the sheet-with-no-title-block posture), **Sheet Manager**
  (the staged red grid idiom; owns sheet-level fields where this owns
  viewport identity), **Linked Sheets Transfer** (the per-sheet nested
  rollback precedent).
- Plan siblings: **19 Circuit Renumber** — the same
  two-pass-around-an-occupancy-constraint pattern applied to panel slots;
  its handoff cross-references this one. **12 Legend Place** — the
  neighbouring same-spot-on-the-page machinery for legends and schedules.
