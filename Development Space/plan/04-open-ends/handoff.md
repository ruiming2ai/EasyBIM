# 04 — Open Ends

Every unconnected MEP connector in the model, classified, in one tree — with the two ends that touch but do not join at the top.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 04 of 45 | Misc Tools ▸ Coordination (new pulldown) | yes | S | 9/10 | 7/10 |

## Main purpose

An unconnected connector is invisible until it isn't: the duct run that
analyses as two systems, the pipe Revit refuses to slope, the fitting that
looks joined on plan but is 3 mm short. Finding them today means the System
Browser plus squinting, and the worst case — two open ends touching but not
connected — looks exactly like a healthy model until an engineer asks why the
static pressure calc is nonsense. Native disconnect warnings fire only at edit
time and vanish; pyRevit's stock tools list a selection's connectors, not a
model-wide sweep.

Open Ends is the model-wide sweep, read-only, one bounded pass. It collects
everything that owns a ConnectorManager, walks physical connectors only, and
turns every `IsConnected == false` into a classified row in the tree engine
Circuit Schedule already ships: domain → system type → system → element,
counts at every node, token search. The classification is the point — the
state layer pairs up open connectors of the same domain that sit within
tolerance with compatible shape and size, and puts *"touching, not connected"*
in its own group at the top, because that is the silent killer the eye cannot
catch.

This is the S-effort half of a deliberate split: the plain disconnect sweep
ships now, on machinery that already exists, while the deeper rule engine —
mismatched mates, direction, size, family connector authoring against the
systems instances actually sit on — is scoped apart as **15 Connection
Check**. The two stay separate tools with separate temperaments; what they
share is the connector-graph walker, and whichever is built second hoists it.
Because Open Ends wants to live as a dockable pane, its modules land in
`lib/easybim/` from day one — which happens to put the walker exactly where
Connection Check (and Slope Check) will look for it. Circuit Schedule reads
the electrical *logical* graph; this reads the *physical* connector graph
across all MEP domains, which no existing tool or native view surfaces. That
adjacency, and the near-zero marginal machinery, is what puts it at rank 04.

## Basic implementation ideas

- **Bundle & module layout** — new pulldown
  `EasyBIM.tab/Misc Tools.panel/Coordination.pulldown/` with its own
  `bundle.yaml` (title "Coordi-\nnation", tooltip, `author: Ruiming Liu`,
  `layout:` starting with Open Ends) holding `Open Ends.pushbutton/` — a thin
  `script.py` that shows or focuses the pane, plus `bundle.yaml` and icons.
  Because a dockable pane registers from `startup.py` (init-only API) and
  anything startup imports must live in lib, the real code goes straight to
  `lib/easybim/`: `open_ends_state.py` (classification, tree assembly on the
  generic engine, ignore-set logic), `open_ends_revit.py` (the pass, the
  connector walker, show/select), `open_ends_panel.py` (pane provider,
  registration, modeless fallback — mirror `circuit_schedule_panel`), with
  `lib/easybim/ui/open_ends_panel.xaml` and `open_ends_panel_window.xaml`.
  The tree/search core is *not* rewritten: reuse
  `circuit_schedule_state`'s Node / build_index / token-search engine, which
  is already generic and already listed as reusable kit.
- **Revit API route** — one pass: curve elements by class
  (`Mechanical.Duct`/`FlexDuct`, `Plumbing.Pipe`/`FlexPipe`,
  `Electrical.CableTray`, `Electrical.Conduit`) plus `FamilyInstance` with a
  non-null `MEPModel.ConnectorManager` (fittings, accessories, equipment,
  fixtures, air terminals), every access wrapped the way
  `circuit_schedule_revit` wraps reads. Per element, iterate
  `ConnectorManager.Connectors`; keep physical connectors only —
  `ConnectorType` End/Curve, and `Domain` filtered to exclude
  `DomainElectrical` by default (electrical logical connectors are a
  different notion of "connected"; a checkbox brings the physical electrical
  ones in). Each `IsConnected == false` becomes a dict: element id, connector
  index, origin XYZ, direction (`CoordinateSystem.BasisZ`), domain, shape,
  size (radius or width/height), system name/type via `MEPSystem`. The
  touching-pair pass runs in state code over a spatial hash of origins:
  within tolerance (default 5 mm, conservative), same domain, compatible
  shape and size, and directions roughly anti-parallel — the direction test
  is what keeps stacked riser ends on adjacent levels from being invented
  into pairs. No writes, no Transaction (pinned). Scan, Refresh, Show, and
  Ignore all ride `ExternalEventBridge`; deliberately **no** Idling or
  DocumentChanged hook — this is a bounded scan on demand, not a second
  resident mode beside Clash Detection Mode.
- **The scan/report cycle** — read-only: scan → classify → tree.
  `open_ends_revit.scan(doc, options)` returns the flat open-connector list
  plus scan metadata (elements visited, wall-clock, whether the element cap
  or time budget truncated the pass) — ints, floats, unicode only.
  `open_ends_state.build(snapshot, ignores)` pairs the touchers, then builds
  the tree: "Touching, not connected" first and pre-expanded, then by domain
  → system type → system → element, per-branch display caps with the
  truncation stated on the branch. Refresh re-runs the pass against the live
  model; expander state, search text, and session ignores survive. Excel
  export writes the flat classified list.
- **Edge cases & honest limits** — categories or domains unticked in the
  filter chips are footer-listed as excluded, never silently absent.
  Equipment with legitimately unused connectors (spare taps) is handled by
  per-connector **Ignore**, which lasts the session only — the window says
  so in as many words, because an honest session limit beats a fake
  persistent one keyed on ElementIds; a persistent by-name ignore store
  (extensible storage vs shared-folder JSON) is a deferred design decision,
  named here, not smuggled in. Elements whose ConnectorManager throws land in
  a "could not read (n)" leaf with ids, not in a crash. A truncated scan
  says exactly what was not visited. The tool does not judge *why* an end is
  open, does not chase connectors into links (nested links are unreachable
  and even direct-link traversal is out of scope for the S version — stated
  in the tooltip), and does not attempt to join anything: fixing is
  modelling work, and the pane only carries you to it.
- **Risks** — connector iteration over fabrication-heavy models is the
  performance wall: the element cap and wall-clock budget go in on day one,
  and the spatial hash keeps pairing O(n) instead of O(n²). The touching
  tolerance must stay conservative — inventing pairs is worse than missing
  them, since a false pair sends someone hunting a defect that does not
  exist; tolerance is user-visible in the options row for that reason. The
  session-only ignore list will be the first user complaint — resist
  upgrading it ad hoc; it needs the real design decision. Pane registration
  keeps the Circuit Schedule restart-once rule: registered from startup, and
  until the next full Revit restart the tool opens as the modeless fallback
  window pinned right — same behaviour, same explanation.
- **Tests** — `test_open_ends_state.py` pins touching-pair classification at
  its edges (tolerance boundary, size mismatch, same-direction riser ends
  rejected, ignored connectors excluded from pairs), tree grouping, and
  branch caps. `test_open_ends_command_names.py` pins the pulldown and
  pushbutton bundles, pane XAML↔handler wiring, icon sizes, the IronPython
  AST scan, the no-Transaction pin, and that startup's registration path
  imports cleanly. `test_open_ends_revit.py` drives the walker over fakes
  shaped like each API generation — missing MEPModel, ConnectorManager that
  throws, logical vs physical connector types, domain enums — asserting
  every failure becomes a named bucket and only plain data crosses back.

## UI description

**Open Ends pane** — a dockable pane on the right (default DockPosition
Right), registered unconditionally from `startup.py`, with the modeless
right-edge window fallback and the restart-once note when registration has
not taken effect yet. Top strip: a Search box (token search — "12" finds
system 12, not 112; names by substring) and a row of filter chips as
checkboxes — Duct, Pipe, Cable Tray, Conduit, plus "Include electrical
physical connectors" off by default — with an options row holding the
touching tolerance. Body: the tree — **"Touching, not connected (7)"** as the
first expander, pre-expanded, then one branch per domain → system type →
system → element. Each leaf shows element name, level, and open-connector
count; leaf buttons are **Show** (select + zoom via ExternalEventBridge) and
**Ignore** (session only — the tooltip says "Ignored until Revit closes").
Expander state survives Refresh; truncated branches end in "… 214 more — 
narrow the filter or export." Footer: status line left, **Refresh** and
**Export** buttons right. Status lines:

> "Scanned 8,412 elements in 1.9 s — 63 open ends, 7 touching pairs. Electrical excluded."

> "Scan stopped at the 10 s budget — 3 of 4 domains complete; Conduit not visited."

> "No open ends in the scanned categories. Cable Tray excluded."

No confirmation window exists — nothing is ever written — and there is no
separate report window: the pane is the report, and the Excel export is its
portable copy.

### User operation flow

1. Ribbon: Misc Tools → Coordination → Open Ends. The pane docks (or the
   fallback window opens, with the restart-once note in its subtitle) and the
   first scan runs; the status line ticks while it does.
2. "Touching, not connected (7)" sits open at the top. **Show** on the first
   pair — Revit selects both elements and zooms; drag the connection closed
   in the model.
3. Work down the domains. A spare tap on an AHU is noise: press **Ignore** —
   the row greys out of the counts, and the tooltip has already said the
   ignore lasts only this session.
4. A skipped item looks like: "could not read (2)" under a domain, listing
   the two in-place elements whose ConnectorManager threw — counted apart
   from open ends — or a footer note "Cable Tray excluded" for an unticked
   chip.
5. **Refresh** after fixing: the pass re-runs, closed ends disappear, search
   text, expanders, and ignores survive.
6. **Export** writes the flat classified list (ids as visible keys) for the
   coordination meeting.
7. Closing the pane is the whole cancel path — no writes ever started. The
   next open re-scans fresh; ignores survive until Revit closes, and the
   window said so.

## See also

- Existing: **Circuit Schedule** (the dockable pane pattern, the generic
  tree/search engine, the restart-once rule — all reused here), **Clash
  Detection Mode** (the resident coordination mode this tool deliberately is
  not).
- Siblings: **15 Connection Check** (the deep-rules sibling — mismatched
  mates, direction, size, connector authoring — built on the shared
  connector-graph walker this tool positions in lib), **03 Slope Check**
  (its traversal stops at the open ends this tool finds; the two reports
  cross-reference), **13 Sleeve Place** and **33 Penetration Schedule**
  (the other residents of the Coordination pulldown).
