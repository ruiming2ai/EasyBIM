# 13 — Sleeve Place

Finds every service crossing through linked structure and places a sized,
unhosted sleeve at each one — the pre-weekend opening drawing, automated.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 13 of 45 | Misc Tools ▸ Coordination (new pulldown) | yes | L | 8/10 | 9/10 |

## Main purpose

Every project that models pipe, duct, conduit, or cable tray against a linked
structural model ends with the same chore: someone spends days windowing along
walls, eyeballing where services cross, and placing sleeve and opening
families one at a time. Sizes get guessed, insulation gets forgotten, and the
sleeve placed for the 50 mm pipe is still 50 mm after the pipe becomes 80.
The structural engineer asks for the opening drawing on Thursday and the
answer is always a weekend.

Sleeve Place computes the crossings and places the sleeves. One geometric
pass intersects every host MEP curve with the walls and floors of the host
document and each loaded link; every hit becomes a staged row — level, host,
service, computed size, skew — sized in an editable table (outside diameter
plus insulation plus annular clearance, rounded up) that lives in pure state
code, so the sizing logic desktop-tests without Revit. The sleeve families
are the user's own, picked through the existing family wizard with parameters
mapped by name. Placement is deliberately unhosted, so a link reload can
never delete a sleeve, and the service's system name and size are stamped
into mapped text parameters so downstream tools can read the pairing —
identity stays geometric plus stamped text, never an ElementId into someone
else's model.

Nothing in the inventory writes coordination geometry: Clash Detection Mode
watches and reports, Batch Duplicate Host duplicates hosted elements between
documents, and native Interference Check produces a list, not sleeves. The
free ecosystem either hard-codes one sleeve family or keeps link hosts for
the paid tier. This tool composes what the repo already has — the family
wizard, the three-store preset pattern, the staged red grid — around the one
genuinely new piece, the intersection engine. The L effort (risers, skew,
stacked walls) is what keeps a 9-impact idea at rank 13. Its companion
deliverable is **33 Penetration Schedule**, which reads the parameters this
tool stamps and turns them into the structural engineer's document.

## Basic implementation ideas

- **Bundle & module layout** — joins the Coordination pulldown that **04 Open
  Ends** creates: `EasyBIM.tab/Misc Tools.panel/Coordination.pulldown/Sleeve
  Place.pushbutton/` (add "Sleeve Place" to the pulldown's `layout:`), with a
  thin `script.py`, `bundle.yaml` (two-line title "Sleeve\nPlace", narrative
  tooltip, `author: Ruiming Liu`), 96×96 icons. Four-layer split in the
  pushbutton: `sleeve_place_state.py` (size table, rounding, skew math,
  crossing merge/dedupe, plan builder — pure Python), `sleeve_place_revit.py`
  (collectors, link transforms, intersections, placement, read-back),
  `sleeve_place_ui.py`, `SleevePlaceWindow.xaml` + `ReportWindow.xaml`.
  Reuse from lib: the family selection wizard pages
  (`family_selection_state/revit/ui`) for the two family pickers, and the
  size-table store generalises `tag_align_presets`'s three-location pattern
  (this computer / this model / shared folder) — that store is the declared
  hoist: second consumer, so the local/model/shared classes lift to
  `lib/easybim/preset_store.py` and Tag Align repoints at them.
- **Revit API route** — services: one `FilteredElementCollector` with an
  `ElementMulticategoryFilter` over `OST_PipeCurves`, `OST_DuctCurves`,
  `OST_Conduit`, `OST_CableTray`, `LocationCurve` kept only when it is a
  `Line` (flex and arced runs are named skips). Sizes as doubles in internal
  units: `RBS_PIPE_OUTER_DIAMETER` (falling back to `RBS_PIPE_DIAMETER_PARAM`
  with the substitution noted), duct `RBS_CURVE_WIDTH/HEIGHT/DIAMETER_PARAM`,
  conduit `RBS_CONDUIT_OUTER_DIAM_PARAM`, tray width/height — every
  BuiltInParameter name resolved via the `getattr(DB.BuiltInParameter, name,
  None)` guard. Insulation joined once per domain by collecting
  `PipeInsulation`/`DuctInsulation` and dict-keying `HostElementId`.
  Structure: walls and floors from the host document and from each
  `RevitLinkInstance.GetLinkDocument()` (each instance of a copied link keeps
  its own `GetTotalTransform()`); rather than transforming solids, the
  service line is mapped into link space with the inverse transform,
  intersected there, and the hit points mapped back. Per candidate pair —
  prefiltered by a padded-`Outline` `BoundingBoxIntersectsFilter` per link
  document — `Solid.IntersectWithCurve(line, SolidCurveIntersectionOptions())`
  yields entry/exit segments; midpoint, axis, and the penetrated face normal
  at the entry point (face projection, not wall-wide orientation, so curved
  walls skew correctly) cross back as plain floats. Placement:
  `doc.Create.NewFamilyInstance(XYZ, symbol, level,
  StructuralType.NonStructural)` on the nearest host-document level at or
  below the crossing, then up to two `ElementTransformUtils.RotateElement`
  calls to align the family's +X axis to the service axis (the stated family
  contract: sleeve modelled along +X, origin at mid-length). Commit is one
  assimilated `TransactionGroup`, one nested `Transaction` per sleeve. No
  ExternalEvent or Idling — the flow is modal; the scan runs under a
  cancellable `forms.ProgressBar` because it is the long read.
- **The plan/apply cycle** — `build_plan` merges collinear crossing segments
  (stacked and embedded walls become one crossing with the combined
  penetration length), dedupes by center point within tolerance, computes
  sleeve size from the table (hole = OD + 2×insulation + 2×annular
  clearance, rounded up; length = penetration + projection each side), and
  buckets every non-candidate. One plan object feeds both the staged grid and
  the executor, so preview and write cannot drift. The grid renders every
  proposed sleeve red until Apply, skips greyed with their reason, each row
  individually uncheckable (declined rows are "skipped — unchecked", never
  failed). Apply stays disabled — never hidden — until both families are
  picked, their mapped parameters verified present, and the acknowledgement
  ticked: **"Sleeves are unhosted: they stay when a service moves or a link
  reloads. Re-run to re-check."** — the honest cost of reload-proof
  placement. After commit the Report window reads the placed instances back
  from the model, not from the plan, and lists skips and per-item rollbacks
  separately. One Ctrl+Z reverts the whole batch.
- **Edge cases & honest limits** — named-skip buckets: *"skewed 27° — place
  by hand"* (beyond the editable tolerance, default 10°); *"no table size
  fits (1250 mm duct)"*; *"already sleeved"* (an instance of a chosen family
  within tolerance of the crossing); *"flex/arced run — not sleeved"*;
  *"link unloaded"* and *"wall in a nested link — unreachable by API"*
  (each named, never silently absent); *"geometry unreadable (budget)"* for
  in-place structure that blows the per-element solid budget; *"sloped
  through floor beyond tolerance"* (v1 sizes shallow slopes by the projected
  major axis and refuses steeper, stated). Existing sleeves whose mapped size
  parameter is smaller than today's computed size are flagged **"existing
  sleeve undersized — review, not moved"** in their own report list: the tool
  refuses to move or resize placed sleeves, because a placed sleeve may
  already be agreed with the structural engineer. Walls and floors only —
  beam web penetrations are out of scope and the tooltip says so. The tool
  never cuts or hosts into the link (no API for it) and never invents a
  crossing it cannot measure.
- **Risks** — the intersection pass is the performance wall: build a per-link
  spatial index of structural bounding boxes once, prefilter every curve
  against it, and hard-cap candidates with the truncation stated in the
  report ("stopped at 5,000 crossings — narrow the source selection").
  Stacked/embedded walls double-report one crossing unless the merge runs on
  segment gaps, and the merge tolerance is user-visible for that reason.
  Vertical risers through floors are where the L lives: the floor posture
  (axis vertical, rectangular openings oriented to the service pair) is
  genuinely different rotation logic from wall sleeves and needs its own
  fakes. The family contract (axis along +X, origin mid-length, parameters
  by name) must be validated at plan time — a family missing "Diameter" is a
  red plan error with Apply disabled, not a runtime throw. The "already
  sleeved" tolerance can false-positive on dense racks; keep it tight and
  visible.
- **Tests** — `test_sleeve_place_state.py` pins sizing at table boundaries
  (exact table size does not round up), skew classification at the tolerance,
  segment merging for stacked walls, dedupe, undersized-existing detection,
  and plan bucket classification. `test_sleeve_place_command_names.py` pins
  both bundles (pulldown layout grown, pushbutton metadata), XAML↔handler
  wiring for both windows, icon sizes, the IronPython AST scan, and the
  forbidden-API pin that no Transaction opens during the scan phase.
  `test_sleeve_place_revit.py` drives the adapter over fakes shaped like each
  API generation — link transform round-trip, `SolidCurveIntersection`
  segments, insulation join by host id, missing OD parameter fallback,
  placement rollback zeroing its counter — asserting nothing but ints,
  floats, and unicode crosses back.

## UI description

**Main window** — resizable modal, `Grid Margin="14"`, rows Auto/*/Auto.
Header "Sleeve Place" SemiBold ~30px over a DimGray subtitle: "Sleeves are
placed unhosted in this model. Nested links cannot be reached." Two cards
side by side. Left, **Structural sources card**: checkbox list of the host
document and every Revit link with live-filter Search, count line "3 of 5
sources selected.", Select All / Select None; unloaded links greyed with the
reason in a tooltip. Right, **Services & sizing card**: the four MEP category
checkboxes; two family pickers ("Round sleeve…", "Rectangular opening…")
that open the family wizard and then show the chosen family : type; the
parameter-mapping row (Diameter / Width / Height / Length / two stamp
targets, defaulting to Comments); and the size table — an editable grid
(clearance, rounding steps, projection) whose edited rows render red until
saved, with Save / Load by name to this computer / this model / shared
folder. Below both cards after **Scan**: the staged plan grid — Level, Host
(link name), Service, Size, Skew, Status — every planned sleeve red until
Apply, skips greyed with their named reason, "Hide Un-checked" filter.
Footer: status TextBlock left, the acknowledgement checkbox, then **Scan** /
**Apply** (`IsDefault`, 110×35, disabled with tooltip until families map
clean and the tick is set) and **Cancel** (`IsCancel`).

> "Scanned 1,240 service curves against 3 links — 96 sleeves staged, 12 skipped (named in the grid)."

> "Scan stopped at the 5,000-crossing cap — Conduit not finished; narrow the selection."

**Report window** — read-only WPF table after commit, never stacked message
boxes: one row per planned sleeve — Placed (id linkified, level, size, read
back from the model), Skipped (bucket named), or Rolled back — plus the
"Existing sleeves undersized (3)" review list. Footer:

> "94 placed, 12 skipped, 2 rolled back — read back from the model. One undo step."

### User operation flow

1. Ribbon: Misc Tools → Coordination → Sleeve Place. The Main window opens;
   sources and categories list immediately.
2. Check the structural link(s), pick the two sleeve families through the
   wizard, adjust the size table (edited rows sit red until saved).
3. Press **Scan**. The cancellable progress bar ticks per source; cancelling
   here abandons the scan with nothing staged and nothing written.
4. The plan grid fills red. A skipped item looks like: a grey row "Duct
   600×400 — L2 corewall — skewed 27°, place by hand" or "PIPE SAN 3 — wall
   in nested link — unreachable". Uncheck any row you disagree with; it
   moves to "skipped — unchecked".
5. Tick "Sleeves are unhosted…"; Apply enables. Apply commits one
   TransactionGroup with a nested transaction per sleeve — one failed
   placement rolls back alone and lands in the report, never costing the
   other ninety-five.
6. The Report window opens, read back from the committed model, undersized
   existing sleeves listed for review. Close it; one Ctrl+Z in Revit reverts
   the whole batch.
7. Cancel path: **Cancel** or Esc any time before Apply closes the window
   with the model untouched.

## See also

- Existing: **Clash Detection Mode** (watches the collisions this tool
  pre-empts at structure; its outline/bbox prefilter manners are the model
  for the candidate pass), **Batch Duplicate Host** (the other
  cross-document geometry tool), **Tag Align** (donor of the three-store
  preset pattern this hoists to lib), **Families Transfer** (the family
  wizard reused here).
- Siblings: **33 Penetration Schedule** (the companion deliverable — reads
  the stamped service/size parameters and writes the structural engineer's
  schedule; buildable standalone against hand-placed sleeves), **04 Open
  Ends** (creates the Coordination pulldown this joins), **14 Clash Sweep**
  (the backlog finder for everything that crosses where it should not).
