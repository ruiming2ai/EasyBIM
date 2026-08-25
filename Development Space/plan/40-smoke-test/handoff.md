# 40 — Smoke Test

The harness that runs EasyBIM's own in-Revit checks against a scratch
document — draining every spec's "Still to verify in Revit" backlog.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 40 of 45 | Misc Tools (next to Auto Update) | no | L | 5/10 | 9/10 |

## Main purpose

Sixty-eight desktop test files prove the pure logic in six seconds, and
not one of them can execute a `*_revit.py` adapter against a live
document. Every spec in this repository therefore ends with a "Still to
verify in Revit" backlog, and a regression in `sheet_geometry` or the
`ExternalEventBridge` surfaces in production, usually two Revit
versions after the line was written. The repo's own gap list calls this
the sorest spot and names the fix — "an in-Revit smoke-test harness
would relieve it."

Smoke Test is that harness as an ordinary EasyBIM button. It runs a
registry of small checks, each a module in `lib/easybim/smoketests/`
declaring what it needs: nothing, a scratch document, or session/UI
context. Scratch-needing checks get a document the runner creates itself
via `Application.NewProjectDocument` and discards with `Close(False)` in
a finally — an API-created document is never the active document, so
your models are structurally out of reach, and the window says so in its
subtitle. Checks drive the real adapters: seed a level, a plan, a sheet
with the bundled titleblock, then read them back through
`sheet_geometry`/`sheet_titleblocks`; round-trip a temp file through
`excel_workbook` under IronPython (the desktop suite proves it under
CPython — the engine difference is precisely the risk); post a no-op
through a real `ExternalEventBridge`; cycle a scratch panel through My
Ribbon's apply/remove and its envvar mirror. The async checks make the
harness the very pattern it is testing — a staged state machine riding
the bridge and the single Idling delegate, envvar-mirrored so an engine
recycle cannot orphan a pending row.

The rank is honest about who presses the button: usefulness 5, because
an end user reaches for it only after an extension update or on a new
Revit build, to prove the toolset before trusting it on a deadline. The
strategic value to this repository is a different number entirely:
development here happens off Revit, in an AI loop whose specs each end
with a manual-verification backlog a human must walk. Smoke Test turns
that backlog from an afternoon of hand checks into a check module and a
button press — every future tool in this plan ships smoke checks beside
its desktop tests, so the backlog drains as a habit, not an event, and
the Copy Report block is written in exactly the shape a spec's
verification section wants pasted back. pyRevit's own unit-test runner
executes IronPython scripts but knows nothing of scratch-document
lifecycle, async ExternalEvent checks, or EasyBIM's adapter seams; no
existing EasyBIM tool tests EasyBIM.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Smoke Test.pushbutton/` beside Auto
  Update, the other zero-doc infrastructure button. `bundle.yaml`:
  two-line title "Smoke\nTest", `context: zero-doc`, narrative tooltip
  naming the promise ("runs EasyBIM's checks against a scratch
  document; your models are never touched"), `author: Ruiming Liu`.
  `script.py` thin launcher, `__persistentengine__ = True`, stale
  modules dropped on relaunch like Sheet Manager. The state/revit
  split hoists to lib on day one — the Idling delegate imports it:
  `lib/easybim/smoke_state.py` (registry model, needs-gating, row
  lifecycle pending→terminal, budgets, report shaping — pure dicts),
  `lib/easybim/smoke_revit.py` (scratch-document lifecycle, per-check
  transaction wrapper, the run context handed to checks), and
  `lib/easybim/smoketests/` — one module per check plus
  `assets/minimal_titleblock.rfa`, the registry an explicit list in
  `__init__.py`, no import scanning. UI stays in the pushbutton
  (`smoke_test_ui.py` + `SmokeTestWindow.xaml`): the persistent engine
  keeps it alive, and the Idling consumer calls only duck-typed
  methods on the window object it finds in the envvar mirror.
- **Revit API route** — Scratch document:
  `app.NewProjectDocument(app.DefaultProjectTemplate)` when the template
  path is set and exists, else `app.NewProjectDocument(DB.UnitSystem.
  Metric)`; both failing means every scratch check is "skipped — no
  scratch document", and the runner refuses to touch
  `uiapp.ActiveUIDocument` by construction — no smoke module may even
  reference it (pinned by test). Fixtures per check inside the scratch
  doc: `Level.Create`, a `ViewFamilyType` collected by `OfClass` and
  filtered to `ViewFamily.FloorPlan`, `ViewPlan.Create`,
  `doc.LoadFamily(assets path)` for the titleblock then
  `ViewSheet.Create(doc, tb_type_id)` — falling back to
  `ViewSheet.Create(doc, ElementId.InvalidElementId)` with the
  titleblock sub-checks skipped by name when the load fails — and
  `Viewport.Create` to give `sheet_geometry` a real projection to
  invert. Transaction shape: the runner owns one `TransactionGroup`
  per scratch check and rolls it back after the check's read-backs, so
  every check starts from the same pristine document and fixture cost
  never accumulates; a check that leaks an open `Transaction` makes the
  group rollback throw — that check fails "left a transaction open",
  the remaining scratch checks skip as "scratch document poisoned by
  {check}", and the close is still attempted. Session checks:
  `DockablePane.PaneExists` on the clash and circuit pane ids behind
  the panes' own pyRevit-build probe; `ExternalEvent` round-trip
  through `ExternalEventBridge` with a `Raise` result outside
  `OK_RAISE_RESULTS` failing the row immediately ("bridge raise denied
  — never pends"); a one-shot consumer registered with the `idling`
  dispatcher, finalizing on the next tick; envvar write/read;
  `compat.py` ElementId shims against a real element from the scratch
  doc. Budgets are wall-clock per check, checked between steps and
  enforced on pending rows by the Idling watchdog; version-dependent
  checks probe with `hasattr`/ForgeTypeId and mark themselves "skipped
  — needs 2022+" rather than failing. `doc.Close(False)` runs in the
  finally of the scratch phase and is re-attempted on window close.
- **The scan/report cycle** — read-only from the user model's point of
  view (it writes only into its own scratch document), so no
  confirmation window and no acknowledgement tick: `build_plan`
  resolves the registry against the session — which checks are
  selectable, which grey with reasons — and Run executes scratch
  checks first, then, the scratch doc closed, the async session
  checks. Terminal states are idempotent — a late `on_done` against a
  row already failed on budget is dropped. The report is the window
  itself: every row carries pass / fail / skipped(reason) / pending,
  the footer re-counting from row states, not run bookkeeping. Copy
  Report emits a plain-text block via `System.Windows.Clipboard`: one
  line per row under a fingerprint header — Revit version and build,
  pyRevit version, the extension commit read the way Auto Update reads
  its own repository, the scratch template and units — because a smoke
  report without its environment is unactionable.
- **Edge cases & honest limits** — Named buckets: "skipped — no
  scratch document (no template, Metric fallback failed)", "skipped —
  needs 2022+ ({probe} absent)", "skipped — needs dockable-pane support
  in this pyRevit build", "skipped — titleblock failed to load ({Revit's
  message})", "skipped — scratch document poisoned by {check}",
  "skipped — cancelled", "skipped — unchecked", "failed — budget
  exceeded ({n}s)", "failed — left a transaction open", "failed —
  bridge raise denied", "failed — {one-line exception}". What it
  refuses: it never runs any check against a user document — no
  template and no scratch doc shrinks the run, never widens it; it
  never auto-fixes what it finds — the fix is a code change, not a
  button; and it is not CI — it needs a live Revit session, and a
  journal-driven headless run is out of scope v1, stated. Honest
  limits, stated in the window: checks prove adapters construct and
  round-trip, not that windows look right; `family_load_options`'
  overwrite prompt is UI and cannot be exercised headless;
  worksharing, cloud paths, and linked documents are not modeled by a
  scratch doc, so their adapters stay on the manual backlog — the
  report names the unproven seams, so a green run never overclaims.
- **Risks** — `ViewSheet.Create`/titleblock loading differs across API
  generations, and the bundled `.rfa` must be saved in the oldest
  supported format (the `copy_paste` contract is verified 2021–2026, so
  2021), with a desktop test pinning the asset's presence and stored
  format marker. A check that hard-crashes the engine — not throws —
  strands the scratch document: the finally covers exceptions, not
  engine death, so the envvar mirror records the live document handle
  and run state, and the next launch detects the leftover and offers
  to close it before starting — a stranded doc is unsaved and
  invisible, costing memory until closed, and the Recovery dialog says
  exactly that. A modal dialog raised mid-check blocks the main thread
  as it blocks 37 Batch Runner — checks are written dialog-free,
  a `DialogBoxShowing` guard auto-dismisses and fails the raising check
  by dialog id, and the budget cannot preempt a truly blocked call;
  whichever of 37 and 40 builds second hoists the shared
  `lib/easybim/dialog_guard.py`. Offices that lock template paths must
  actually reach the `UnitSystem` fallback, and the fingerprint must
  show the scratch units — geometry epsilons are unit-honest or they
  are lies.
- **Tests** — `test_smoke_state.py` pins registry needs-gating and
  grey reasons, the two-phase row lifecycle with idempotent terminal
  states (late callback dropped, budget expiry on a pending row),
  poisoned-scratch skip fan-out, cancel semantics per phase, report
  re-counting from row states, and the Copy Report block shape
  including the fingerprint header. `test_smoke_command_names.py` pins
  bundle metadata (`context: zero-doc`, two-line title),
  `__persistentengine__` in script.py, XAML↔handler wiring, 96×96 icon
  pairs, the IronPython AST scan across the smoketests package, the
  explicit-registry-lists-every-module pin, and the forbidden-API pin
  that no smoke module references `ActiveUIDocument` or `ActiveView`.
  `test_smoke_revit.py` drives the runner against fakes per API
  generation: template present / absent / throwing landing in the
  right skip bucket, `Close(False)` reached on every exit path, the
  group-rollback-throw path, stranded-doc recovery from the envvar
  mirror, `Raise` denied failing fast, and the Idling watchdog
  finalizing an over-budget pending row — plus one fake-driven test
  per check module, so the harness cannot itself become untested code.

## UI description

**Main window** — modeless (it must survive async checks), its
`ExternalEventBridge` created while the command is still in API
context. Header "Smoke Test" over the DimGray subtitle "Runs EasyBIM's
in-Revit checks against a scratch document. Your models are never
touched." One card: a small "Search" label with live-filter TextBox,
Select All / Select None, the count line, then the checkbox list of
checks grouped by needs (No document / Scratch document / Session),
each row a checkbox, the check name, a one-line description, and after
Run a result chip — pass, fail, skipped (reason), pending. Rows that
cannot run in this session grey out in place with the reason in a
tooltip; a failed row's tooltip carries the one-line exception, and a
"Details" expander under the list (state preserved across rebuilds)
shows full text for failed rows. Footer: status TextBlock left;
right-aligned buttons **Run** (`IsDefault`, disabled while a run is
live, reason in tooltip), **Copy Report** (disabled until a run has
terminal rows), **Close** (`IsCancel`). The window stays open after
its action — it never needs pick UI.

> "38 checks — 31 selected, 4 unchecked, 3 grey (named)."

> "Running 12 of 31: sheet_geometry — scratch document open (metric,
> default template)."

> "29 of 31 done: 24 passed, 1 failed, 4 skipped (named), 2 pending —
> scratch document closed."

> "Copied 38-line report — paste into the spec's 'Still to verify in
> Revit' section."

**Recovery dialog** — native-mimicry TaskDialog, shown at launch only
when the envvar mirror records a scratch document stranded by a
previous run: "A previous Smoke Test run left a scratch document open."
Command links **Close it and start fresh** / **Leave it — I'll close
Revit later**, with the memory cost stated in the message. Fail closed:
declining does not block the window, but scratch checks grey with
"skipped — previous scratch document still open".

**Copy Report block** — not a window but a contract: a fingerprint
header ("Revit 2024.2 build … · pyRevit 4.8.x · EasyBIM {commit} ·
scratch: metric, default template") then one line per row — "PASS
sheet_geometry (0.8 s)", "FAIL excel_roundtrip — budget exceeded
(10 s)", "SKIP dockpane_probe — needs dockable-pane support" — ending
with the unproven-seams line ("not covered: worksharing, cloud,
linked documents").

### User operation flow

1. Ribbon: Misc Tools → Smoke Test. No document needs to be open. If a
   previous run stranded a scratch document, the Recovery dialog offers
   to close it first.
2. The Main window opens with the registry already resolved: grey rows
   carry their reasons; everything else is checked by default. Filter
   with Search, uncheck what is not wanted.
3. Press **Run**. Phase one: scratch checks run synchronously under
   their budgets, the status line narrating per check. Phase two: the
   scratch document closes, async session checks post their events,
   and pending chips resolve as callbacks and Idling ticks land.
4. Cancel path: **Close** during phase one finishes the current check,
   marks the rest "skipped — cancelled", and closes the scratch
   document before the window goes; during phase two it marks pending
   rows "skipped — cancelled" and drops their late callbacks. Either
   way nothing in the session changed — the scratch document was never
   saved.
5. A skipped item reads in place: "dockpane_probe — skipped: this
   pyRevit build has no dockable-pane support." A failed item reads:
   "smoke_bridge — failed: bridge raise denied."
6. Press **Copy Report**; paste the block into the spec's "Still to
   verify in Revit" section.
7. Close. If the scratch document could not be closed, the status line
   said so and named it — nothing else is left behind.

## See also

- Existing: **Auto Update** (ribbon neighbour; the other zero-doc
  infrastructure button, and the commit reader the fingerprint
  borrows), **My Ribbon** (the apply/remove cycle and envvar-mirror
  pattern the harness both uses and tests), **Clash Detection Mode**
  and **Circuit Schedule** (the dockable-pane, bridge, and
  persistent-engine machinery under test), **Excel**, **View Align**,
  **Linked Sheets Transfer** (the `excel_workbook` and
  `sheet_geometry`/`sheet_titleblocks` adapters the first check wave
  re-proves).
- Plan siblings: **37 Batch Runner** — the other tool built on
  API-opened documents and the close-in-finally discipline, and the
  co-owner of the `DialogBoxShowing` guard (whichever builds second
  hoists `lib/easybim/dialog_guard.py`). Beyond that, every idea in
  this plan is downstream: each new tool ships its smoke checks beside
  its desktop tests, and this registry is where its "Still to verify
  in Revit" list goes to die.
