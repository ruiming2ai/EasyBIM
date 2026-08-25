# 24 — Space Sync

Diff-and-sync MEP Spaces against the architect's linked Rooms — explicit phases, a dry-run diff, and orphan safety before anything is written.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 24 of 45 | Links | yes | L | 8/10 | 9/10 |

## Main purpose

MEP analysis runs on Spaces that are supposed to mirror the architect's
Rooms in the link — and the architect renumbers, renames, adds, and deletes
rooms weekly. Spaces drift silently: load calcs reference a room name from
two issues ago, new rooms have no space at all, and deleted rooms leave
orphan spaces still feeding schedules. Reconciling by hand is an afternoon
of side-by-side windows, so it happens rarely and badly.

Creating spaces from linked rooms is not, by itself, new ground —
pyRevitMEP and MEPover both do a basic version, and that shapes what this
tool must be. The differentiation is everything around the creation, and it
leads the design: a three-way dry-run diff (Create / Update / Orphan) shown
staged and checkable before a single write; phases chosen explicitly on
both sides, never inferred, because a wrong phase pairing produces a
confidently wrong diff; rename and renumber carry-over as a first-class
Update lane, so week-two drift — the common case — is a two-minute re-run,
not a re-create; and orphan safety, where a space whose room has vanished
is listed, default-unchecked, and deletable only behind its own
acknowledgement, because spaces carry the engineering data and the tool
never assumes an orphan is garbage.

It earns an L effort and a 9 impact because nothing in the EasyBIM
inventory is spatial at all — the MEP muscle (Circuiting, connectors, clash)
has no rooms-to-spaces story — and because the free graphs break exactly
where this design spends its care: phase mapping, level mapping, and
shared-coordinate transforms. The paid MEP suites do this; between them and
a broken Dynamo graph sits the weekly reconciliation with the house
guarantees: fail closed, stage everything, name every skip, one undo step,
report read back from the model.

## Basic implementation ideas

- **Bundle & module layout** — `EasyBIM.tab/Links.panel/Space Sync.pushbutton/`
  with `script.py` running a STEP_* state machine over the window sequence
  (the wizard idiom), `bundle.yaml` (two-line title "Space\nSync",
  narrative tooltip, `author: Ruiming Liu`, `min_revit_version` unneeded —
  every surface used here is old), 96×96 icons. Four-layer split plus
  Excel: `space_sync_state.py` (the three-way diff, level mapping, number
  collision pre-check — pure and desktop-tested), `space_sync_revit.py`
  (link/room/space collectors, transforms, creator/updater),
  `space_sync_ui.py`, `space_sync_xlsx.py` (one-way diff export,
  xlsxwriter guarded, no metadata sheet — it is a log, not a round-trip),
  with `SpaceSyncSetupWindow.xaml`, `SpaceSyncDiffWindow.xaml`,
  `SpaceSyncReport.xaml`. Nothing hoists yet; the transformed-point
  helpers stay local until 27 Site Check or 13 Sleeve Place wants them.
- **Revit API route** — fail closed from the first click:
  `OfClass(RevitLinkInstance)` fills the link ComboBox;
  `GetLinkDocument()` returning null is a plain refusal ("link is
  unloaded — reload it first"). Next probe: the link type's Room Bounding
  parameter (`WALL_ATTR_ROOM_BOUNDING` on the `RevitLinkType`,
  `AsInteger`) — off means `NewSpace` would land in unbounded air, so the
  Setup window shows a blocking banner telling the user to enable it; the
  tool does not silently edit another type's parameter. Rooms: one pass
  over `FilteredElementCollector(linkDoc).OfCategory(OST_Rooms)`, kept
  when the room's phase matches the chosen link phase, reading Number,
  Name, Level (name + elevation), and `Location` point into plain dicts —
  unplaced rooms (no Location) and zero-area rooms (not enclosed or
  redundant; the API cannot cheaply tell which, so the bucket says both)
  are named skips before the diff. Host side: `OST_MEPSpaces` on the
  chosen host phase, same shape. Levels map by name first, transformed
  elevation within tolerance second; unmatched levels surface in the Setup
  window with a manual override ComboBox per row — before the diff, never
  during it. Matching is by transformed containment: the room point pushed
  through `instance.GetTotalTransform().OfPoint()` (the surface
  `clash_detection_engine` already trusts in lib), tested with
  `Space.IsPointInSpace` against only the spaces bucketed on the mapped
  level, with room Number as the tiebreak when regions overlap —
  `Space.Room` is deliberately not used, because it is unreliable across
  links, and the window header says so. Creation is
  `doc.Create.NewSpace(level, UV)` inside the nested transaction, Name and
  Number set in the same breath; since that overload inherits the active
  view's phase, the adapter refuses to apply unless `doc.ActiveView`'s
  phase equals the chosen host phase — checked before the confirmation
  window, with the fix named ("open a view on phase New Construction") —
  and capability-probes a phase-taking overload at runtime to lift the
  restriction where the running Revit offers one. Updates write the
  space's own `ROOM_NAME` / `ROOM_NUMBER` parameters. Commit is one
  assimilated `TransactionGroup`, one nested `Transaction` per space, so
  one refused placement costs one row; counters zero on rollback. The
  room scan runs under a cancellable `forms.ProgressBar` — it is the long
  read on a 3,000-room hospital. Modal flow throughout: no ExternalEvent,
  no Idling, no persistent engine.
- **The plan/apply cycle** — `build_plan` computes the three-way diff in
  state: **Create** (room with no containing space) with the transformed
  placement point; **Update** (matched space whose Name or Number differs)
  with old → new per field, plus a pre-check that staged numbers collide
  with no existing or staged space number — a collision is "skipped —
  number already in use", not a Revit warning at commit; **Orphan** (space
  with no room on the chosen phases), listed and default-unchecked. One
  plan object feeds the Diff window, the Confirmation window, and the
  executor. Orphan deletion is the one destructive step and demands its
  own acknowledgement tick — "Delete 3 orphan spaces — engineering data on
  them (airflow, loads) is lost" — while creates and updates need none.
  After commit, a created space that reads back zero-area (its point
  landed in unbounded air despite the probes) is rolled back by its own
  nested transaction and reported as failed, never left as an unenclosed
  ghost. The Report window re-reads created and updated spaces from the
  committed model and exports the whole diff-with-outcomes via
  `space_sync_xlsx` for the coordination log.
- **Edge cases & honest limits** — named-skip buckets: "room unplaced",
  "room zero-area (not enclosed or redundant)", "room level unmapped",
  "duplicate room number in link" (containment still matches; the
  tiebreak result is flagged, not hidden), "space in group — edit it in
  the group" (parameter writes on group members are how groups get broken;
  refused), "number already in use", "owned by another user",
  "unchecked". Created spaces get Revit's default upper limit and offset —
  the tool does not compute ceiling heights, and the report says "review
  heights for load calcs". It refuses to infer a phase pairing (two
  explicit ComboBoxes, pre-paired only on exact name match), refuses to
  touch room or space geometry, refuses to run with Room Bounding off,
  and refuses to delete anything without the tick. Second run immediately
  after Apply must show an empty diff — idempotence is the correctness
  test, and the state suite pins it.
- **Risks** — phase mapping between two documents is the deepest
  ambiguity in the tool; explicitness is the design answer, and the Setup
  window must make the two choices impossible to miss. A room's location
  point can fall outside every bounded region after transform (odd
  shapes, point moved by the architect) — the zero-area read-back probe
  plus per-row rollback is the honest recovery, and the failure row names
  the room. Performance on large links: everything runs on the snapshot
  dicts, containment tests only against level-bucketed candidates, one
  pass per document, progress ticking — never an all-pairs sweep. The
  `NewSpace` active-view-phase wall is real: the guard must run before
  confirmation, or Apply dies mid-batch. Orphan deletion is
  irreversible-in-spirit even inside one undo step — default-unchecked
  plus the tick is the floor, and the report lists deleted ids.
- **Tests** — `test_space_sync_state.py` pins the diff on synthetic
  room/space sets (creates, renames, renumbers, orphans), name-then-
  elevation level mapping with overrides, duplicate-number tiebreaks, the
  number-collision pre-check, and second-run idempotence.
  `test_space_sync_command_names.py` pins bundle metadata, XAML↔handler
  wiring for all three windows, icon sizes, and the IronPython AST scan.
  `test_space_sync_revit.py` drives fakes for an unloaded link, Room
  Bounding off, transforms applied to room points, phase filtering on
  both documents, the zero-area post-create rollback zeroing its own
  counter, and the group-member refusal — asserting plain dicts only.

## UI description

**Setup window** — resizable modal, header "Space Sync" over a DimGray
subtitle: "Matching is by location; the link's Room parameter is not
trusted." Top: link ComboBox, then two phase ComboBoxes side by side —
"Rooms in link, phase" and "Spaces here, phase" — pre-paired only when
names match exactly. When the link type's Room Bounding is off, a red
blocking banner replaces the footer's Next: "Room Bounding is off for this
link — enable it in the link type before spaces can form." Below, the
**Level mapping card**: link level → host level rows, auto-matches in
plain text, unmatched rows red with a manual override ComboBox, count line
"8 of 9 link levels mapped." Footer: status left, **Scan** (`IsDefault`),
**Cancel** (`IsCancel`).

> "L-B1 has no host level match — map it, or its 14 rooms will be
> skipped."

**Diff window** — after the progress-barred scan: three expanders, each a
checkbox list with live search — **Create (64)** rows "Room 214 —
OFFICE — Level 2", **Update (12)** rows "Space 214: 'OFFICE' → 'OPEN
OFFICE', 214 → 214A", **Orphans (3)** rows default-unchecked and greyed
until the acknowledgement tick in the footer is set. Staged rows render
red until Apply; "Hide Un-checked" filters at rebuild time; searches flip
visibility so checks survive. A **Skipped** expander names every
pre-diff reason. Footer: status left, the orphan acknowledgement checkbox,
**Apply** (`IsDefault`), **Cancel** (`IsCancel`).

> "64 creates, 12 updates, 3 orphans, 9 skipped. Nothing written."

**Confirmation window** — small, counts-only restatement of the checked
work ("Create 64 spaces, update 12, delete 2 orphans — one undo step."),
the active-view-phase guard result, **Apply** / **Back**.

**Report window** — read-only table, never stacked message boxes:
Created / Updated / Deleted / Skipped (every reason named) / Failed (the
zero-area rollbacks, room named), values read back from the committed
model; **Export** writes the diff-with-outcomes to .xlsx.

> "64 created, 12 updated, 2 deleted, 9 skipped, 1 failed — read back
> from the model."

### User operation flow

1. Ribbon: Links → Space Sync. The Setup window opens; pick the
   architectural link. An unloaded link refuses with the reason.
2. Pick both phases explicitly. Fix the one red level mapping, or accept
   that its rooms will be named skips.
3. **Scan** runs under a cancellable progress bar — cancel here abandons
   cleanly, nothing read but the setup lists.
4. The Diff window fills. Review Creates and Updates; open Orphans and
   decide — the rows stay greyed until the tick.
5. A skipped item looks like: "Room 117 — skipped: zero-area (not
   enclosed or redundant)" or "Space 302 — skipped: in group 'Typical
   Patient Room'".
6. **Apply** → the Confirmation window restates the counts and checks the
   active view's phase; a mismatch blocks with "open a view on phase X".
7. Confirm. One TransactionGroup commits; a room whose point lands in
   unbounded air rolls back its own row into Failed.
8. The Report window reads the result back from the model; Export drops
   the .xlsx into the coordination log. Ctrl+Z reverts the whole batch.
9. Next week, same click: the diff shows only the drift — renames and
   renumbers carry over as Updates, and an unchanged model shows an empty
   diff that says so.
10. Cancel path: Cancel/Esc on any window before the final Apply leaves
    the model untouched — declined rows are skipped, never failed.

## See also

- Existing: **Batch Link** (same panel; gets the link loaded before this
  tool refuses on it), **Clash Detection Mode** (the lib precedent for
  `GetTotalTransform` handling), **Temp Phase** (the house's other
  phase-explicit tool).
- Rank 06 **Load Names** — downstream consumer: its `{room}` token reads
  the Spaces this tool keeps truthful; its handoff already points here.
- Rank 36 **Air Balance** — further downstream: reconciles design airflow
  on the spaces this tool creates and maintains.
- Rank 27 **Site Check** — the upstream sanity check that the link's
  shared coordinates (and so every transformed point here) are agreed.
- Rank 28 **Link Health** — the same panel's housekeeping sibling; run it
  when Space Sync's refusals suggest the link itself is the problem.
