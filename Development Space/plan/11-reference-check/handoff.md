# 11 — Reference Check

Find every section head and view reference that will print "?" — checked against the set actually being issued.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 11 of 45 | Annotation (new panel) | no | S | 8/10 | 8/10 |

## Main purpose

A section head printing "?" — because its target view was pulled off a sheet in
a reshuffle — is found by the client, on paper, every single time. Removing a
view from a sheet quietly orphans every marker and every view reference
pointing at it across the whole set, and Revit offers no list, only the
discovery. The subtler bug is worse: a marker whose target *is* on a sheet,
but a sheet outside the set being issued, prints a perfectly confident wrong
number — nothing looks broken until the recipient chases a detail that is not
in the package.

Reference Check is entirely read-only. One pass builds the view-to-sheet map
from viewports, walks every view's reference-type annotations to their
targets, and classifies every section, callout, and elevation by whether it
sits on a sheet. Three finding lists come out: markers and references whose
target is on no sheet (the "?" list), targets that are on a sheet outside the
selected print set (the confident-wrong-number list), and — informational,
never an error to burn down — referenced-kind views that are on sheets yet
referenced by nothing. Each row has a Show button that selects and zooms, the
window stays open while you fix and re-check, and the findings export to Excel
for the QA record.

It earns rank 11 on the asymmetry between cost and cover: the failure is
public and embarrassing, the check is minutes before every issue, and nobody
reads this API surface — native Revit's only nod is the "referenced views"
filter buried in a dialog, with no off-sheet detection and no print-set
awareness, and community scripts that find some orphans never check against
the set actually going out, which is where the wrong-number bug lives. Inside
EasyBIM it is a pre-issue companion to Print Sheets and the Print Set
pulldown, and the first QA resident of the new Annotation panel (07 Dim
Overrides is the other — whichever lands first creates the panel). S effort:
no writes, one window, and the print-set read already lives in `lib`.

## Basic implementation ideas

- **Bundle & module layout** — New `EasyBIM.tab/Annotation.panel/` (create it
  if 07 Dim Overrides has not already) with
  `Reference Check.pushbutton/`. `reference_check_state.py` computes every
  finding from plain dicts (marker records, view→sheet map, print-set sheet
  list) — pure Python, desktop-tested; `reference_check_revit.py` is the one
  read pass plus the Show/Refresh ExternalEvent handlers;
  `reference_check_ui.py` + one XAML for the modeless report window. The
  window is modeless with model interaction, so the button follows the house
  modeless rules: work rides `lib/easybim/external_events.py`
  (ExternalEventBridge), `__persistentengine__ = True` in bundle.yaml, stale
  modules dropped on relaunch, live state mirrored into pyRevit envvars.
  Print sets compose `lib/easybim/print_sets`; export composes
  `lib/easybim/excel_workbook`. The viewport map + reference-resolution pass
  is exactly what 32 View Sweep needs to decide "kept because referenced" —
  when that builds, this pass hoists to `lib/easybim/view_references.py`.
- **Revit API route** — Read pass, no transaction anywhere: (1)
  `FilteredElementCollector(doc).OfClass(Viewport)` → the view→sheet map;
  `ScheduleSheetInstance` deliberately ignored (schedules are out of scope
  here). (2) Every placeable view once: sections, callouts, and elevations
  classified on-sheet / off-sheet. (3) Per view, the reference annotations:
  `View.GetReferenceCallouts()`, `GetReferenceSections()`,
  `GetReferenceElevations()`, each returned id resolved through
  `ReferenceableViewUtils.GetReferencedViewId(doc, id)` — all behind
  `hasattr` capability probes, because this surface has generation gaps; the
  docs place it in the 2016 era, so `bundle.yaml` carries
  `min_revit_version` there and the probes decide the rest at runtime. A
  probe that fails removes its finding list *with a visible note in the
  window* — never a silently empty list claiming a clean model. (4) The
  scope ComboBox reads `ViewSheetSet` names via the `print_sets` helpers;
  "Whole model" is the default and skips list 2's outside-the-set test.
  Everything crosses back to the state module as ints and unicode. Show
  selects the marker's host element and zooms via the ExternalEventBridge;
  Refresh re-runs the read pass through the same bridge, because a modeless
  window has no API context of its own.
- **The plan/apply cycle** — Read-only, so this is the scan/report cycle:
  `build_report` (the state module's `build_plan` analogue) computes the
  three lists plus per-list counts, grouped by target view, each row carrying
  the annotation kind (view's own marker / reference callout / reference
  section / reference elevation), the host view, the target, and where the
  target sits. Per-category display caps (500 rows a list) with a stated
  truncation note — "list truncated at 500; export to Excel for all 612" —
  never an unbounded WPF tree. The window *is* the report; Refresh re-scans
  after fixes; Excel export writes the full untruncated lists. Nothing is
  written to the model at any point, and the tooltip says so.
- **Edge cases & honest limits** — The tool is honest about one simplification
  and states it in the window footer: it reports the dangling *relationship*
  and its target, not every view where each marker happens to be visible —
  per-view visibility testing is unbounded work, so the bound is stated
  instead of faked. The "unreferenced" list is scoped to referenced-kind
  views only (sections, callouts, elevations, and drafting views reached by
  reference); floor plans, schedules, legends, and title sheets are reached
  from the index, not from markers, so they are excluded rather than reported
  as noise — and the list presents as information ("nothing points here"),
  never as errors. Named skip buckets inside the scan: "reference list
  unavailable in this Revit (probe failed)" removes a list with its note;
  "target unresolvable" (the utils returned nothing readable) is its own
  small bucket rather than a guess; views in closed worksets scan fine (this
  is element data, not graphics). What it refuses to do: guess whether an
  off-sheet target *should* be placed, hide "?" markers, or touch the model
  in any way — fixing is the user's move, re-checking is the tool's.
- **Risks** — The reference-view API surface is the whole risk register: the
  probes must gate each list independently, and the failure mode to design
  against is a silently empty list reading as a pass — the visible removal
  note is load-bearing, test-pinned behaviour. "Unreferenced" is inherently
  advisory and will be wrong for some office workflows (match-line sheets,
  key plans); it must never share a colour or a counter with the real
  defects. Model-wide marker walks on a 3,000-view set are the performance
  trap — one pass, per-view loops bounded, display caps on the way out.
- **Tests** —
  - `test_reference_check_state.py` pins the classification from fixed dicts:
    on/off-sheet targets, outside-the-print-set detection, unreferenced-list
    scoping (plans and legends excluded), grouping by target, display-cap
    truncation notes, and the probe-removed-list bookkeeping.
  - `test_reference_check_command_names.py` pins bundle.yaml
    (`min_revit_version`, `__persistentengine__`), XAML↔handler wiring, 96×96
    icon pairs, the IronPython AST scan, and zero Revit imports in the state
    module.
  - `test_reference_check_revit.py` drives the adapter against fakes per API
    generation: one with the full reference surface, one missing
    `GetReferenceElevations` (list must vanish with a note), one whose
    resolver throws (row lands in "target unresolvable"), plus the
    view→sheet map shape.
  - `test_reference_check_xlsx.py` pins the untruncated Excel export through
    `excel_workbook`.

## UI description

**Report window** (the only window) — modeless, resizable, stays open beside
Revit while you fix. Header: "Reference Check" over "Every marker and
reference that will not print a real number." Scope row: a print set ComboBox
("Whole model" default, then each `ViewSheetSet` by name) and a **Refresh**
button. Body card: the findings tree — three expanders with counts in the
header, state preserved across rebuilds:

- *Will print "?" (3)* — grouped by target view; rows like
  "Section A-A — marker in FP-L2 Plan · target on no sheet", each with a
  **Show** button.
- *Points outside the print set (7)* — rows like "Callout 5/A-401 — target on
  sheet A-902, not in 'Print Set 90% CD'".
- *Unreferenced but on sheets (5)* — informational styling, no red anywhere:
  "Detail 7/A-503 — on sheet, referenced by nothing."

A live search box filters by view and sheet name (substring; identifiers by
token, so "12" does not find "112"). Any probe-removed list shows as a flat
note row in its place: "Reference elevations unavailable in this Revit —
list removed." Footer status left: "3 will print ?, 7 point outside Print Set
90% CD, 5 views on sheets are never referenced." Buttons right: **Export to
Excel**, **Close**. Example truncation status: "Will print ? truncated at 500
rows — export to Excel for all 612."

### User operation flow

1. Ribbon: Annotation → Reference Check. The read pass runs (footer:
   "Scanning 412 views…"), and the Report window opens modeless with "Whole
   model" scope.
2. Pick the print set being issued from the ComboBox; the outside-the-set
   list computes and the footer restates the counts.
3. Expand "Will print ?", press **Show** on a row — Revit selects and zooms
   to the marker's host via ExternalEvent; the window stays open.
4. Fix it in Revit (place the target, delete the dead marker — the user's
   call; the tool never guesses), then press **Refresh**. The pass re-runs
   and the row disappears; expander state and search text survive.
5. A "skipped" item in this read-only tool is a removed or bounded list, and
   it is always visible: the probe note row, or the truncation line in the
   footer — never a quietly shorter list.
6. **Export to Excel** for the issue record if wanted.
7. Cancel path: **Close** at any point — nothing was ever written, so
   closing is always safe; re-opening re-scans from scratch.

## See also

- Existing EasyBIM: **Print Set pulldown** and **Print Sheets** (the set
  this checks against, via the same `lib/easybim/print_sets`), **Sheet
  Manager**, **Revision Manager** (the issue-day neighbours), **Tag Align**
  and **Tags Sweep** (the annotation tools the new panel will eventually
  gather).
- Plan siblings: **07 Dim Overrides** — the other Annotation panel founder,
  hunting the same printed-lie class; **32 View Sweep** — consumes this
  tool's viewport map and reference resolution as its "kept because
  referenced" test (the `view_references` hoist); **45 Text Types**
  (Annotation panel sibling); **12 Legend Place** and **29 Issue Register**
  — the same pre-issue sheet-set hygiene shelf.
