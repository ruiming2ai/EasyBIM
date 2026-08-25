# 33 — Penetration Schedule

The numbered opening schedule the structural engineer actually asked for —
every sleeve paired to its service, audited for drift, and exported with
nothing guessed.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 33 of 45 | Misc Tools ▸ Coordination (new pulldown) | yes | M | 7/10 | 8/10 |

## Main purpose

The structural engineer does not want your model; they want a numbered
opening schedule — mark, level, wall, size, fire rating, offset from grids.
Today that document is a manual Excel built by clicking every sleeve, and it
goes stale the day a pipe moves. Worse, the two questions that actually
matter for coordination — "which sleeves no longer have a pipe in them?"
and "which pipes have no sleeve?" — can only be answered by redoing the
whole visual sweep, so in practice nobody answers them.

Penetration Schedule reads the sleeves and answers all of it in one pass.
Scope is the sleeve and opening families picked by name through the family
wizard, so it works on sleeves placed by hand exactly as well as on Sleeve
Place's output — per the curator note, this tool is buildable standalone
and must say so. Pairing is geometric and lives in pure state code: a
sleeve is *paired* when a service axis passes through it within tolerance,
*drifted* when the nearest axis is off-center beyond tolerance (distance
reported), *orphan* when no service is within reach — and a service
crossing structure with no sleeve at all lands in a *missing* list that is
exactly the input Sleeve Place wants next. Host fire rating is transcribed
from the linked wall type's rating parameter, chosen by name per link
because offices disagree on which parameter carries it; links are never
written. Mark numbering follows an editable pattern, existing marks are
kept by default, and renumbering demands an acknowledgement tick. The
workbook and the on-screen schedule are read back from the committed model
after the Mark write, zero-amp-report style: the schedule answers what is
actually there, and the drifted/orphan/missing sections answer what is
left to do.

The Excel button in Misc Tools exports Revit schedules; this deliverable
cannot be a Revit schedule at all, because one row joins a host-model
sleeve, a linked wall type's rating, and a geometric pairing verdict that
no schedule field carries. Within the plan it is the audit-and-deliverable
half that 13 Sleeve Place deliberately does not do. Usefulness sits at 7
because the tool is reached for at issue milestones rather than daily;
impact is 8 because the sheet it replaces is the coordination document of
record, and a stale one costs core drills.

## Basic implementation ideas

- **Bundle & module layout** — joins the Coordination pulldown that 04 Open
  Ends creates (Sleeve Place is the second resident):
  `EasyBIM.tab/Misc Tools.panel/Coordination.pulldown/Penetration
  Schedule.pushbutton/`, added to the pulldown's `layout:`. `script.py` is
  thin but sets `__persistentengine__ = True` — the Main window is modeless
  so Show buttons and the Mark write can ride `ExternalEventBridge`.
  `bundle.yaml` (two-line title "Penetration\nSchedule", narrative tooltip
  naming what is transcribed vs computed, `author: Ruiming Liu`), 96×96
  icons. Split: `penetration_schedule_state.py` (pairing classification,
  drift math, mark allocator, grid-pair pick, workbook row shaping — pure
  Python), `penetration_schedule_revit.py` (collectors, link transforms,
  host resolve, Mark writer, read-back), `penetration_schedule_ui.py`,
  `PenetrationScheduleWindow.xaml` + `PenetrationReport.xaml`,
  `penetration_schedule_xlsx.py` over `lib/easybim/excel_workbook`. Reuse:
  the family wizard (`family_selection_state/revit/ui`) for the sleeve
  picker, `show_elements`-style select-and-zoom, `compat`. The crossing
  scan (bbox prefilter + `Solid.IntersectWithCurve` per link) is Sleeve
  Place's engine: if 13 exists first, hoist it to
  `lib/easybim/link_crossings.py` and both repoint — the second-consumer
  rule; standalone, this tool writes its own scoped copy and becomes the
  first consumer.
- **Revit API route** — Sleeves: `FilteredElementCollector`
  `OfClass(FamilyInstance)` filtered to the wizard-picked family names; per
  instance cross back id, level, `LocationPoint.Point`,
  `FacingOrientation`/`HandOrientation`, bounding-box extents, and the
  mapped size/stamp parameters as display text (`AsValueString`) plus
  doubles for the size where they read. Services: one collector with an
  `ElementMulticategoryFilter` over `OST_PipeCurves`, `OST_DuctCurves`,
  `OST_Conduit`, `OST_CableTray`, `LocationCurve` kept only when it is a
  `Line`. Structure: walls and floors of each checked
  `RevitLinkInstance.GetLinkDocument()`; points map into link space via the
  inverse `GetTotalTransform()` for host resolve (which wall type contains
  or nearly contains the sleeve center) and for the missing-list crossing
  scan — padded-`Outline` `BoundingBoxIntersectsFilter` prefilter, then
  `Solid.IntersectWithCurve`, per Sleeve Place's manners. Rating: the
  chosen type parameter read as text from the resolved wall/floor type —
  read-only into the link, always. Grids: `OfClass(DB.Grid)` in the host
  document, straight grids only (`Curve` is a `Line`); the offset pair uses
  the two nearest grids whose directions differ by more than 45°. Mark:
  `ALL_MODEL_MARK` via the house `getattr(DB.BuiltInParameter, name, None)`
  probe. Transactions: the scan phase opens none (pinned); Mark writes
  commit through one assimilated `TransactionGroup` with one nested
  `Transaction` per sleeve, dispatched through the bridge, counters zeroed
  on rollback.
- **The plan/apply cycle** — `build_plan` classifies every sleeve
  (paired / drifted with distance / orphan), builds the missing list from
  the crossing scan, and allocates marks: pattern tokens `{level}` and
  `{nnn}` ("P-{level}-{nnn}"), ordering deterministic (level, then grid
  offsets, then id — stable sorts pinned), existing marks kept by default
  with the allocator routing around them so a planned mark can never
  collide with a kept one. One plan object feeds both the staged grid and
  the executor, so preview and write cannot drift. **Write Marks** stays
  disabled — never hidden — until a scan exists; the "Renumber existing
  marks" checkbox is gated by its own red acknowledgement line ("Marks
  already issued to the structural engineer will change."). After commit
  the Report window re-reads Marks from the model and lists written / kept
  / rolled back; **Export Excel** always re-reads first too, so the
  workbook never claims a mark the model does not carry. The workbook has
  two sheets — *Schedule*, one row per mark (the sheet the engineer
  numbers off), and *Services*, one row per pairing keyed by mark — which
  is the decided answer to the two-pipes-one-opening question the
  brainstorm left open. ElementId travels only as a visible key with name
  fallback.
- **Edge cases & honest limits** — named buckets: *"orphan — no service
  within reach"*; *"drifted 35 mm — review"*; *"missing sleeve — send to
  Sleeve Place"*; *"curved or flex service — not paired"*; *"sleeve has no
  location point — not paired"* (line-based opening families);
  *"axis unknown — paired by distance only"* (hand-placed families that do
  not follow the along-+X contract lose the angular test, stated per row);
  *"no rating source chosen for link X"* and *"rating parameter empty"*
  (an em-dash with the reason, never a blank that reads as unrated);
  *"link unloaded — host and rating unknown"*; *"radial or arc grid —
  offset not computed"*; *"no grids in host model"*; *"skipped —
  unchecked"* for declined rows. The tool never moves, resizes, or deletes
  a sleeve (a placed sleeve may already be agreed with the engineer —
  Sleeve Place's rule, kept), never writes into a link, cannot reach
  nested links, and states that the rating column is a transcription of a
  named link parameter — it does not judge fire-stopping or code
  compliance.
- **Risks** — Two services through one oversized opening: resolved by the
  two-sheet workbook shape (one mark per opening on *Schedule*, every
  pairing on *Services*), so the row shape is a decision, not an
  ambiguity. Rating parameter names vary per office and per link — the
  by-name ComboBox per link handles it, and a link with no choice made
  must render "no rating source chosen", never blanks. The missing-list
  crossing scan is the performance wall: it is Sleeve Place's expensive
  pass re-run, so the bbox prefilter, per-link spatial index, and a
  hard candidate cap with a truncation notice come along with it — and if
  13 is built, the code comes along too rather than being forked.
  Duplicate-Mark warnings against non-sleeve elements are possible and
  benign (the allocator only guarantees uniqueness within its own scope);
  the report names the warning when Revit raises it. Reach for hand-placed
  families falls back from the mapped size parameter to bbox extents with
  the substitution noted — a false "paired" from a generous bbox is the
  trap, so the fallback reach is the *smaller* horizontal extent.
- **Tests** — `test_penetration_schedule_state.py` pins pairing
  classification at the tolerance boundaries, drift distance math, the
  mark allocator (kept marks, collision rerouting, renumber mode,
  deterministic ordering), the perpendicular grid-pair pick and its radial
  refusal, and the two-sheet row shaping. —
  `test_penetration_schedule_command_names.py` pins the grown pulldown
  layout, bundle metadata, XAML↔handler wiring for both windows, icon
  sizes, the IronPython AST scan, and the no-Transaction-during-scan pin.
  — `test_penetration_schedule_revit.py` drives the adapter over fakes
  shaped like each API generation: link transform round-trip, host
  containment resolve, rating parameter absent, `LocationPoint` missing,
  Mark write refusal rolling back one sleeve and zeroing its counter, and
  the read-back pass returning plain dicts only. —
  `test_penetration_schedule_xlsx.py` pins both sheets' headers and rows.

## UI description

**Main window** — resizable modeless window (`ShowInTaskbar` off, grip
resize), root `Grid Margin="14"`, rows Auto/*/Auto. Header "Penetration
Schedule" SemiBold ~30px over a DimGray subtitle: "Links are read, never
written. Nested links cannot be reached." Two cards side by side before a
scan. Left, **Sleeves & structure card**: the sleeve family picker (opens
the family wizard, then shows the chosen families), a checkbox list of
structural links with count line "2 of 3 links selected.", Select All /
Select None, Search; unloaded links greyed with the reason in a tooltip.
Right, **Numbering & rating card**: the pattern TextBox with a live example
line ("P-L2-001"), the "Renumber existing marks" checkbox over its red
acknowledgement line, and one rating-parameter ComboBox per checked link
("EB-Structure.rvt — Fire_Rating"). After **Scan** the lower star row
(MinHeight set) fills with the staged grid: Mark (old → new in red until
Write Marks), Level, Host, Rating, Service, Size, Offset, Status — Paired /
"Drifted 35 mm" / Orphan / "Missing sleeve" — drifted, orphan, and missing
rows greyed with reasons, every row with a **Show** button (select + zoom
via ExternalEventBridge; missing rows zoom to the service), every planned
row uncheckable. Footer: status TextBlock left, then **Scan**, **Write
Marks** (`IsDefault`, disabled with tooltip until a scan exists), **Export
Excel** (disabled until a scan exists), **Close** (`IsCancel`).

> "214 penetrations: 195 paired, 9 drifted, 6 orphans — 4 services missing sleeves."

> "Marks staged: 187 new, 27 kept. Nothing is written until Write Marks."

> "EB-Structure.rvt has no rating source chosen — its Rating column shows the reason, not a blank."

**Report window** — read-only WPF table after Write Marks, never stacked
message boxes: one row per sleeve — Written (mark read back from the
model), Kept, Skipped (bucket named), or Rolled back — with the
drifted/orphan/missing counts restated. Buttons: **Export Excel**,
**Close**. Footer:

> "187 marks written, 27 kept, 2 rolled back — read back from the model. One undo step."

Closing the Report window re-scans the Main window, so the grid now shows
the committed marks in black.

### User operation flow

1. Ribbon: Misc Tools → Coordination → Penetration Schedule. The Main
   window opens; links list immediately.
2. Pick the sleeve families through the wizard, check the structural
   links, choose each link's rating parameter, set the mark pattern.
3. Press **Scan**. The status line ticks per link during the pairing and
   crossing pass; nothing is written — the scan phase holds no
   transaction.
4. The grid fills. A skipped item looks like: a grey row "Sleeve 401557 —
   L3 — orphan: no service within 160 mm" or "SAN 100 — L2 corewall —
   missing sleeve (send to Sleeve Place)". Uncheck any planned row to move
   it to "skipped — unchecked"; use **Show** to look before deciding.
5. To renumber, tick the red acknowledgement under the checkbox; otherwise
   existing marks stay and only unmarked sleeves get numbers.
6. **Write Marks** commits one TransactionGroup, one nested transaction
   per sleeve; a refused write rolls back that sleeve alone. The Report
   window opens read back from the committed model. One Ctrl+Z in Revit
   reverts every mark the run wrote.
7. **Export Excel** (from either window) re-reads marks, then writes the
   two-sheet workbook through the standard save dialog.
8. Cancel path: **Close** (or Esc) at any point before Write Marks leaves
   the model untouched — scanning never writes.

## See also

- Existing: **Excel** (exports Revit schedules — the contrast this tool's
  purpose states), **Clash Detection Mode** (the bbox-prefilter manners
  for the crossing scan), **Families Transfer** (the family wizard reused
  for the sleeve picker).
- Siblings: **13 Sleeve Place** — the companion tool: it stamps the
  parameters this reads, consumes this tool's missing list, and owns the
  crossing engine that hoists to lib when both exist. **04 Open Ends** —
  creates the Coordination pulldown this joins. **14 Clash Sweep** — the
  neighbouring batch pass for everything that crosses where it should not.
