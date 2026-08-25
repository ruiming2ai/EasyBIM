# 27 — Site Check

One table that compares shared coordinates across the host and every link —
and moves the drifted ones back, with the numbers shown first.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 27 of 45 | Links | no | M | 7/10 | 9/10 |

## Main purpose

Coordinate drift is the quietest expensive bug in linked work: someone
acquires from the wrong model, re-places a link Origin-to-Origin instead of
by Shared Site, or nudges an unpinned link 3 mm — and it surfaces weeks
later at clash review as a building that does not sit where the survey says.
Revit offers no report that compares coordinates across the host and all its
links in one place; the native workflow is per-link dialogs, a spot-the-
difference game between two Project Position readouts, and folklore.

Site Check reads the coordinate picture once — the host's project position,
base points, and geolocation, then the same set from every link whose
document is readable — and computes, per link instance, the transform that
*would* place it on the agreed shared system versus the transform it actually
has. The delta comes out as millimetres and degrees in the project's own
display units, next to plain-language flags: "placed origin-to-origin while
shared sites differ", "survey points disagree by 12 mm", "true north differs
0.5°". Everything below an explicit tolerance is a green row, so hairline
float noise never floods the report. Unloaded links are named skips —
"unloaded — coordinates unreadable" — because the tool never guesses.

It offers exactly one write: "Move to shared position" per flagged link,
closing the measured delta, one undo step, per-link rollback, pinned
instances skipped unless the unpin is explicitly acknowledged. What it will
not do is stated in the window: it never edits a link's internal shared
coordinates, never acquires or publishes (changing which document owns the
truth is a human decision that belongs in Revit's own UI), and never reaches
nested links. The impact score is the point: nothing native or free does the
all-links comparison, Batch Link places links but never audits where they
landed, and the failure this catches is one of the most expensive a
coordination team can ship.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Links.panel/Site Check.pushbutton/` beside Batch Link.
  `script.py` thin launcher; `bundle.yaml` two-line title "Site\nCheck",
  narrative tooltip naming the walls (no acquire/publish, no nested links),
  `author: Ruiming Liu`. `site_check_state.py` — the whole comparison
  engine: pure matrix algebra over dicts (position, angle, elevation in raw
  feet and radians), expected-transform construction, delta decomposition,
  tolerance classification, flag wording, fix-step emission — zero Revit
  imports, because this math is exactly what must be desktop-tested to
  death. `site_check_revit.py` (the one read pass, unit formatting through
  the document's display units, the move executor, post-fix re-read),
  `site_check_ui.py` + XAML per window. Reuse: `excel_workbook` for the
  export, `compat` for ElementId values. Nothing hoists yet; if 28 Link
  Health ships too, the RevitLinkInstance row collector is the shared piece
  to hoist.
- **Revit API route** — Host reads:
  `doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)` for
  EastWest / NorthSouth / Elevation / Angle; base points via
  `FilteredElementCollector(doc).OfClass(BasePoint)` split by `IsShared`
  (survey vs project base point), with the newer
  `BasePoint.GetProjectBasePoint/GetSurveyPoint` statics used behind a
  capability probe when present; `doc.SiteLocation` for latitude/longitude.
  Link reads: every `RevitLinkInstance` (not type — a link placed twice has
  two positions), `GetLinkDocument()` — null for unloaded and closed-workset
  links, a named skip — then the same read set against the link document,
  plus the link's active site name (`ActiveProjectLocation.Name`) for the
  Shared Site column. Comparison: `site_check_state` builds the expected
  instance transform from the two project positions and compares it to
  `GetTotalTransform()`, decomposing the mismatch into a horizontal
  translation, an elevation delta, and a rotation about Z. The fix: per
  link, `ElementTransformUtils.RotateElement` about the vertical axis
  through the instance origin by the state-computed angle, then
  `MoveElement` by the state-computed vector — the state module emits the
  exact (axis point, angle, vector) triple so the executor is dumb.
  Transaction shape: `TransactionGroup` "Move links to shared position",
  assimilated; one nested `Transaction` per link; a pinned instance inside
  the acknowledged unpin path is unpinned, moved, re-pinned inside its own
  nested transaction so rollback restores the pin too. No ExternalEvent, no
  Idling — modal window, one command. Version gating: every surface here is
  ancient API; only the BasePoint statics are probed.
- **The plan/apply cycle** — `build_plan` computes, per flagged fixable
  link, the absolute before and after: current E/N/elevation/rotation and
  target E/N/elevation/rotation in display units, plus the delta — because
  this is precisely the tool where a wrong fix causes the disaster it
  exists to prevent, the dry-run must show numbers a human can check
  against the survey, not just "will be fixed". The confirmation window
  lists each move with those numbers, carries the acknowledgement tick
  "Unpin pinned links, move them, and re-pin" (unchecked, pinned links are
  named skips), and notes "Revit may raise Coordination Monitor warnings
  after the move." Apply runs the group; the report re-reads
  `GetTotalTransform()` from the committed model and shows the residual
  delta per moved link — the proof line is a residual of 0.0 mm / 0.00°,
  and any nonzero residual is printed, not rounded away.
- **Edge cases & honest limits** — Named skips and refusals: "unloaded —
  coordinates unreadable"; "closed workset — link document unavailable";
  "pinned — acknowledge unpin to include"; "multiple instances — position
  by design cannot be told from drift" (a link placed more than once gets
  every instance compared and reported, but auto-fix is greyed with that
  reason: only a human knows which instance is the building and which is
  the copy). Geolocation differences (lat/long) are reported as an
  informational flag with no fix offered. Tolerances are explicit and
  configurable in the window (defaults 1 mm, 0.01°); rows under tolerance
  are green and never enter the fix card. The tool states its walls in the
  header tooltip: no acquire, no publish, no edits inside any link, no
  nested links, and no opinion about *which* side is right — it reports
  disagreement and offers to move instances to the host's shared frame,
  nothing else.
- **Risks** — The transform algebra must be right in every combination of
  rotated true north, clipped/unclipped survey point, mirrored links, and
  elevation offset; this is the tool's central hazard and the reason the
  math lives in pure state code with a property-shaped test (apply the
  emitted fix to the fake's transform; the recomputed delta must be zero).
  `GetLinkDocument()` nullability is routine, not exceptional — every read
  is guarded and lands in a named skip. Angle wrap (−180°/+180°) and
  near-antipodal rotations need explicit normalisation or the report shows
  a 359.9° "drift". Tolerance defaults matter: too tight floods the report
  with float noise and teaches users to ignore red; the configurable
  threshold with honest defaults is the defense.
- **Tests** —
  - `test_site_check_state.py` pins the algebra at its boundaries: identity
    (all-zero delta), pure translation, rotation about an off-origin point,
    elevation-only offsets, combined cases, angle-wrap normalisation, the
    apply-fix-then-recompute-zero round trip, tolerance edge values, and
    flag wording from fixed dicts.
  - `test_site_check_command_names.py` pins bundle.yaml metadata, XAML
    handler wiring across the three windows, 96×96 icon pairs, the
    IronPython AST scan, and a pin that the executor never constructs a
    transform itself — it only consumes state-emitted triples.
  - `test_site_check_revit.py` drives the adapter against fakes:
    `GetLinkDocument` returning null, BasePoint statics present and absent,
    a pinned instance path proving unpin-move-repin order inside one nested
    transaction, a move that throws rolling back its link alone, and the
    ints-and-unicode boundary.
  - `test_site_check_xlsx.py` pins the Excel export of the comparison
    table, including green rows and named skips.

## UI description

**Main window** — resizable modal, root `Grid Margin="14"`, rows Auto/*/Auto.
Header: "Site Check" over the DimGray subtitle "Compares shared coordinates
across the host and every link." Body, two stacked cards. **Comparison
card**: a read-only table, one row per link instance — columns Link / Shared
Site / Δ Position / Δ Rotation / Δ Elevation / Flags — a green check for
in-tolerance rows, a red flag otherwise, deltas in the project's display
units, skips greyed in place with the reason in a tooltip. Above the table,
the tolerance controls ("Flag beyond 1 mm / 0.01°") that re-classify on
change without re-reading. **Fix card**: only flagged, fixable links, as
checkboxes, each row spelling out the move in absolute terms — "ARCH-Central:
E 1032.500 → 1032.500, N 220.100 → 208.100 m, rotation +0.00° — Δ 12.0 mm";
pinned rows greyed until the confirmation's unpin acknowledgement, multi-
instance rows greyed with their reason. Footer status left: "2 links off
shared position, 1 unloaded — skipped. Nothing moved." Buttons right: **Move
to Shared Position…** (primary, disabled with tooltip until a fix is
checked), **Export to Excel**, **Close**.

**Confirmation window** — the complete plan as a read-only table of
before/after coordinates per link, the Coordination Monitor note, and the
acknowledgement checkbox "Unpin pinned links, move them, and re-pin" gating
any pinned rows. Footer status: "2 links will move; 1 pinned link included."
Buttons: **Move 2 Links** (primary, disabled while a checked pinned row
lacks the tick), **Back**.

**Report window** — Moved / Skipped / Failed expanders; each moved row shows
the residual delta re-read from the committed model ("residual 0.0 mm /
0.00°"); a skipped row reads "Skipped — pinned, unpin not acknowledged"; a
failed row carries Revit's message. Footer status: "2 moved (residual 0.0
mm), 0 failed. One undo step." Buttons: **Export to Excel**, **Close**.

### User operation flow

1. Ribbon: Links → Site Check. The read pass runs ("Reading 6 links…") and
   the Main window opens with the comparison table — most rows green on a
   healthy model, and that is the point of running it weekly.
2. Widen or tighten the tolerance if the survey allows; rows re-classify in
   place. Export to Excel here for the coordination record even when
   touching nothing.
3. Tick the drifted links in the fix card. A multi-instance or unloaded row
   cannot be ticked; its tooltip says why.
4. Press **Move to Shared Position…**, read the absolute before/after
   numbers against the survey, tick the unpin acknowledgement if a pinned
   link is included, press **Move 2 Links**.
5. Cancel path: **Back** or **Close** at any point before step 4's commit —
   nothing has been written; the report table alone is a complete deliverable.
6. The Report window opens with residuals re-read from the model. A skipped
   item looks like "Skipped — pinned, unpin not acknowledged"; it is listed,
   never silently dropped.
7. Close. One Ctrl+Z returns every moved link — and its pin state — to
   where it was.

## See also

- Existing EasyBIM: **Batch Link** (places the links this tool audits),
  **DWG Open/Reload**, and the passive **Coordination Review** at file open
  — the natural place a future version could surface "site drift detected"
  as a start-of-day line.
- Plan siblings: **28 Link Health** — the same panel's bookkeeping audit
  (pins, worksets, duplicates, imports); Site Check is the geodesy half of
  the same weekly ritual, and the link-row collector is their shared hoist.
  **24 Space Sync** (Links-panel workflow neighbour), **14 Clash Sweep** —
  where uncaught drift is otherwise discovered, expensively.
