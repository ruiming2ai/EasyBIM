# 03 — Slope Check

Walks every gravity system and says where the fall goes flat, thin, or backwards — before the field does.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 03 of 45 | Misc Tools / MEP Checks (new pulldown) | yes | M | 8/10 | 9/10 |

## Main purpose

A gravity drain that runs flat, or briefly uphill, looks fine in plan and only
fails in the field — where it costs a core drill and a change order instead of
a mouse drag. Checking fall today means tagging inverts along every run and
reading them by eye, and nobody re-checks after the routing moves for
coordination. The Slope tool can *set* a slope on picked elements; nothing
walks a whole sanitary or storm system and says where the fall breaks.

Slope Check is that walk, and it never writes. Setup is picking which piping
system types count as gravity (sanitary, storm, condensate pre-checked;
pumped mains stay out) and confirming a minimum-slope-by-diameter table. One
bounded pass builds a connector graph over those systems and computes four
finding kinds off geometry alone — flat segments, slope below the per-diameter
minimum, reversals along the walk toward the outfall, and invert steps at
fittings where downstream sits above upstream. Everything is judged from
`Location.Curve` endpoints and connector origins, never from flow settings or
the slope parameter, because the geometry is what gets built.

The rank reflects that this is the highest-consequence check in the plumbing
half of the list with no occupant anywhere: native Revit has no fall check at
all, and the Dynamo slope scripts in circulation check single elements against
one number — not per-diameter minimums, not reversals along a traversal, not
fitting invert steps — and none of them names why a branch could not be
judged. That last part is the EasyBIM half of the idea: a run the walker
cannot finish is reported as a named skip, fail closed, never guessed across.
It also opens the **MEP Checks** pulldown that ideas 15 and 44 later move
into, so its bundle layout is worth getting right the first time.

## Basic implementation ideas

- **Bundle & module layout** — new pulldown
  `EasyBIM.tab/Misc Tools.panel/MEP Checks.pulldown/` with its own
  `bundle.yaml` (title "MEP\nChecks", tooltip "Read-only audits of MEP
  systems.", `author: Ruiming Liu`, `layout:` starting with Slope Check),
  containing `Slope Check.pushbutton/`: `script.py`
  (`__persistentengine__ = True`), `bundle.yaml`,
  `SlopeCheckSetupWindow.xaml`, `SlopeCheckResultsWindow.xaml`,
  `slope_check_state.py` (graph walk, slope math, finding classification —
  pure Python over plain dicts), `slope_check_revit.py` (collectors,
  connector extraction, pick-outfall, show), `slope_check_ui.py`,
  `slope_check_xlsx.py`. The minimum-slope table saves by name in config the
  way `tag_align_presets.py` does. The connector-graph extractor is written
  locally now and is the declared hoist candidate: **15 Connection Check** and
  **04 Open Ends** walk the same surface, and whichever lands second lifts a
  shared walker into `lib/easybim/connector_graph.py`.
- **Revit API route** — setup list from `FilteredElementCollector`
  `OfClass(Plumbing.PipingSystemType)`, pre-checking by
  `SystemClassification` (Sanitary and friends). The pass collects
  `Plumbing.Pipe` (and `FlexPipe`, listed but excluded from slope judgment by
  default) filtered to the chosen system types via
  `RBS_PIPING_SYSTEM_TYPE_PARAM`, plus the fittings they touch through
  `FamilyInstance.MEPModel.ConnectorManager`. Graph edges come from
  `ConnectorManager.Connectors` / `Connector.AllRefs`; geometry from
  `Connector.Origin` and `LocationCurve` endpoint Z; diameter from
  `RBS_PIPE_DIAMETER_PARAM` (`AsDouble`, internal feet). Slope is rise/run in
  internal units — no display-string parsing anywhere; the minimums table is
  converted once at the edge. Near-vertical drops are excluded with the same
  cosine guard Slope already carries (`NEAR_VERTICAL_COS_EPS`). The walk keeps
  a visited set plus `MAX_DEPTH`, an element budget, and a wall-clock budget —
  a guard bug must cost a truncated branch, never a hung Revit. No writes, no
  Transaction (pinned). The results window is modeless: Refresh, Show, and
  the pick-outfall prompt all ride `ExternalEventBridge` — `PickObject` is
  fine from a bridge handler because the window is modeless, not modal.
- **The scan/report cycle** — read-only: the confirmation step *is* the
  report. `slope_check_revit.scan(doc, chosen_system_type_ids)` returns one
  snapshot: pipe dicts (id, system, diameter, endpoints), fitting dicts
  (connector origins per element), and adjacency — ints, floats, unicode
  only. `slope_check_state.evaluate(snapshot, minimums, outfalls)` walks each
  system from its fixtures toward the outfall — the lowest open connector
  when there is exactly one candidate, a user-picked point of connection
  otherwise — and emits the four finding kinds plus the named skips. Every
  Refresh re-scans the live model, so after re-routing the report shows what
  is still broken, and an all-clear run says so in words.
- **Edge cases & honest limits** — the named-skip buckets: *"traversal
  stopped at open end — downstream not judged"* (an open joint mid-network;
  the row points at Open Ends as the tool that finds them);
  *"outfall ambiguous — two open ends; pick the point of connection"* (the
  tool asks, it never picks the lowest of two); *"fitting has no readable
  connectors"* (in-place or degenerate families break the graph there and
  say so); *"flex pipe — not judged"* by default; *"branch truncated at
  depth/budget"* with the counts stated. Near-vertical stacks are excluded by
  design and listed once as such. The tool refuses to judge pumped or vacuum
  systems (whatever was left unchecked in setup is footer-listed as
  excluded), refuses to infer flow direction from Revit's flow settings, and
  states plainly that it checks geometry, not code compliance — a run that
  passes here can still fail a plumbing inspector.
- **Risks** — reversal detection is only as believable as its outfall;
  auto-picking among multiple open ends is wrong often enough that asking is
  the design, which costs a click per ambiguous system and must not nag on
  Refresh (remember the pick per system for the session, keyed by system
  name). Fittings without readable connectors are common in old content and
  must degrade to a named skip, not a walk abort. Performance is the real
  wall: the graph walk must run on the snapshot dicts with id-keyed lookups —
  never re-entering the API mid-walk — or a hospital model freezes the
  window; bounded budgets from day one, and the status line ticks per system.
  Float comparisons need a stated epsilon so a numerically-flat 0.0001 ft/ft
  segment lands in "flat", not "passing".
- **Tests** — `test_slope_check_state.py` pins slope math and epsilons,
  per-diameter band lookups at the boundaries (exactly 3 in), reversal
  detection on synthetic graphs (loops, twin open ends, mid-run opens), and
  budget truncation. `test_slope_check_command_names.py` pins both bundles
  (pulldown and pushbutton), XAML↔handler wiring, icons, the IronPython AST
  scan, and the no-Transaction pin. `test_slope_check_revit.py` drives the
  adapter over connector fakes shaped like each API generation — missing
  ConnectorManager, AllRefs cycles, FlexPipe — asserting the snapshot is
  plain data and every failure lands in a named bucket.

## UI description

**Setup window** — Interference-Check-shaped modal, `Grid Margin="14"`,
header "Slope Check" over a DimGray subtitle naming the document. Two cards.
Left, **Gravity systems card**: checkbox list of the model's piping system
types with live-filter Search, count line "3 of 11 system types selected.",
Select All / Select None; sanitary, storm, condensate pre-checked. Right,
**Minimum slope card**: an editable two-column grid (diameter up to /
minimum slope) seeded 1/4 in/ft under 3 in and 1/8 in/ft for 3–6 in; edited
rows render red until saved; Save / Load named tables. Footer: status left
("Pumped mains left unchecked are excluded, and the report will say so."),
**Check** (`IsDefault`), **Cancel** (`IsCancel`).

**Results window** — resizable modeless read-only table grouped by system,
with one expander per finding kind carrying its count — "Reversals — 3",
"Below minimum — 17", "Flat — 5", "Invert steps — 2" — and a **Named skips**
expander at the bottom. Each row: system, element ids (linkified), level, the
measured value against the required one ("1/16 in/ft, needs 1/4"), and a
**Show** button (select + zoom via ExternalEvent). Ambiguous-outfall systems
carry a **Pick outfall** button inline that prompts a `PickObject` through
the bridge and re-judges that system. Footer: **Refresh**, **Export**,
**Close**. Status lines:

> "Walked 41 runs; 2 stopped at open ends (listed). Nothing was changed."

> "System 'SAN 3' — outfall ambiguous: pick the point of connection to judge reversals."

> "All 41 runs hold their fall. Nothing was changed."

### User operation flow

1. Ribbon: Misc Tools → MEP Checks → Slope Check. The Setup window opens with
   gravity classifications pre-checked and the default minimums table loaded.
2. Adjust the systems and the table (edited rows sit red until saved); press
   **Check**. Cancel here closes with nothing read beyond the setup lists.
3. The Results window opens as the walk runs, status ticking per system;
   findings fill the expanders, reversals first.
4. A skipped item looks like: a row under Named skips reading "SAN 2 —
   traversal stopped at open end at id 400123 — downstream not judged (see
   Open Ends)", or "Fitting id 512907 — no readable connectors". Skips are
   never counted as findings.
5. A system flagged "outfall ambiguous" gets **Pick outfall**; pick the point
   of connection in the model and that system re-judges in place.
6. **Show** on a reversal, drag the pipe in the model, **Refresh** — the
   fixed row disappears, expander state and the session's outfall picks
   survive.
7. **Export** writes findings and skips to .xlsx for the coordination log.
8. **Close** or Esc at any time; both windows write nothing, ever.

## See also

- Existing: **Slope** (writes slope on picked elements; shares the
  near-vertical guard — the fix tool to this tool's audit), **Circuit
  Schedule** (the modeless-window + ExternalEvent read-only pattern).
- Siblings: **04 Open Ends** (finds the open joints this walker stops at),
  **15 Connection Check** (the deep connector-rules sibling; second consumer
  hoists the shared graph walker to lib), **23 Invert Stamp** (writes the
  invert elevations this tool audits), **44 Fixture Units** (same MEP Checks
  pulldown, same read-only temperament).
