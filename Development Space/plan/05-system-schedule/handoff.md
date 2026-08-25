# 05 — System Schedule

The searchable, docked mechanical and plumbing distribution tree that Circuit Schedule already is for electricity.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 05 of 45 | Misc Tools / Systems (new pulldown, sibling of Circuiting) | yes | M | 9/10 | 7/10 |

## Main purpose

Revit's System Browser shows every duct and pipe system in the model at once
and cannot be searched. Finding which AHU serves a diffuser, or which systems
still sit on the auto-generated "Supply Air 14" with no equipment assigned,
means scrolling a flat unsearchable list or clicking elements one at a time.
Circuit Schedule fixed exactly this for electrical distribution; mechanical
and plumbing users doing the same daily lookups get nothing.

System Schedule is the explicitly-invited second life of the
`circuit_schedule_state` tree engine, hosted on a new mechanical/plumbing
snapshot. Equipment sits at the root (whatever no system feeds), its systems
below, and a downstream piece of equipment is not a leaf — it opens into its
own systems, so a VAV box under an AHU reads plant → system → box → system →
terminal. The pane is read-only by design: it never writes, it only searches,
walks, and cross-probes the model with Show buttons that select and zoom.
Systems with no base equipment land in the same "Unassigned" bucket pattern
the electrical tree uses — that bucket is where the "Supply Air 14" cleanup
list falls out for free.

It earns rank 5 on leverage as much as need: the loop guard, `MAX_DEPTH`,
token search, breadcrumb, depth accents, and expansion-state machinery are
reused verbatim from a module that already ships, so a medium effort buys a
tool electrical users already know how to read. Native System Browser has no
search, no breadcrumb, no unassigned bucket, and no cross-probe zoom; no
pyRevit built-in or common Dynamo package offers a searchable, docked
mechanical distribution tree. Being the second consumer also proves the lib
placement of the tree engine was right — see DESIGN-CONTEXT.md on hoisting.

## Basic implementation ideas

- **Bundle & module layout** — New `Systems.pulldown` under
  `EasyBIM.tab/Misc Tools.panel/`, sibling of `Circuiting.pulldown`, holding
  `System Schedule.pushbutton` (thin `script.py` that shows the registered
  pane or opens the fallback window, mirroring Circuit Schedule's launcher;
  `bundle.yaml` with two-line title, narrative tooltip, `author: Ruiming
  Liu`). Because a dockable pane's imports must survive outside the
  pushbutton's sys.path window, all three modules live in `lib/easybim/`:
  `system_schedule_panel.py` (registration + fallback), `system_schedule_revit.py`
  (the snapshot pass), `system_schedule_state.py` (the mechanical `build_tree`).
  The state module imports the generic half of `circuit_schedule_state` —
  `Node`, `tokens`, `node_matches`, `apply_filter`, `breadcrumb`,
  `set_expanded`/`expansion_state`/`restore_expansion`, `accent_for_depth`,
  `MAX_DEPTH` — and writes only the duct/pipe edge semantics itself. Do not
  generalize the two snapshot collectors into one MEP abstraction yet; that
  merge waits for a third system domain, per the second-consumer rule.
- **Revit API route** — One pass in `system_schedule_revit.scan_model(doc)`:
  `FilteredElementCollector(doc).OfClass(DB.Mechanical.MechanicalSystem)` and
  `.OfClass(DB.Plumbing.PipingSystem)`, `WhereElementIsNotElementType()`. Per
  system read `Name`, the system type name and classification — probe
  `system.SystemType` with `hasattr` first (it was deprecated mid-stream) and
  fall back to `doc.GetElement(system.GetTypeId())` for the `MEPSystemType`
  name — `BaseEquipment` id, and `Elements` membership ids. Flow and fixture
  units come from `RBS_DUCT_FLOW_PARAM`, `RBS_PIPE_FLOW_PARAM`, and
  `RBS_PIPE_FIXTURE_UNITS_PARAM`, read with `AsValueString` for display only.
  Everything crosses back as plain dicts — nothing but ints and unicode.
  Hierarchy edges mirror `circuit_schedule_revit.scan_model`'s single-source
  rule: `BaseEquipment` is the upstream end, `Elements` the downstream end,
  so one walk over the systems yields every edge and a parent and child can
  never disagree about who feeds whom. `ConnectorManager` is deliberately not
  consulted for the tree — same reasoning that kept the electrical snapshot
  off `MEPModel`. Show reuses the select-and-zoom path (`show_elements`)
  through the existing `ExternalEventBridge`; that helper gains its second
  consumer here and hoists to a shared lib home. `startup.py` registers the
  pane unconditionally (init-only API), default DockPosition Right, with the
  modeless right-edge fallback when registration is missed.
- **The plan/apply cycle** — Read-only, so scan/report: Refresh runs
  `scan_model` (through the bridge when triggered from the pane), the state
  module builds the tree, and the pane *is* the report. The footer status
  line is the summary — systems counted, unassigned counted, the document key
  the snapshot was read from (via the `document_key` pattern) so a stale pane
  can say so. No transactions anywhere; Show changes only selection and zoom.
- **Edge cases & honest limits** — Root detection is "base equipment no
  system feeds"; equipment whose family has logical-only or missing
  connectors never appears in any `Elements` set, so it reads as a leaf with
  a "no connectors readable" subtitle — shown, never guessed. Systems whose
  `BaseEquipment` is deleted or None root under "Unassigned". Circular
  feeds are cut by the drawn-set guard plus `MAX_DEPTH` (64) — a truncated
  branch, never a hung Revit. A document with no mechanical or plumbing
  systems shows an empty-state line instead of a blank tree. Systems living
  in linked models are out of scope and the pane says so; host document only.
  Search follows house rules: identifiers by token ("12 does not find 112"),
  names by substring, filtering flips visibility so selection survives.
- **Risks** — `MechanicalSystem`/`PipingSystem` API shapes drift across
  versions (the `SystemType` deprecation is the known one); hand-rolled fakes
  per API generation are mandatory, and every property read is guarded.
  `Elements` on a large hospital model is the slow path — one pass, no
  re-walks, display caps per branch. Dockable registration is init-only, so
  the first install needs a Revit restart — same caveat Circuit Schedule
  already documents, repeated in the tooltip. Multi-equipment chains that
  loop through shared plant will lean on the loop guard; verify the guard
  fires on real ring mains, not just synthetic fixtures.
- **Tests** — `test_system_schedule_state.py`: mechanical `build_tree` on
  dict fixtures — plant → system → box → system → terminal chaining, the
  Unassigned bucket, loop-guard truncation, token search, expansion-state
  round-trip. `test_system_schedule_command_names.py`: new pulldown +
  pushbutton bundle.yaml fields, 96×96 icons both themes, IronPython AST
  scan, forbidden-API pins, `startup.py` registration wiring.
  `test_system_schedule_revit.py`: adapter against fakes shaped like each API
  generation — `SystemType` present vs `GetTypeId`-only, missing flow params,
  deleted `BaseEquipment`, empty documents.

## UI description

**System Schedule pane** (dockable, right edge — the only window). Same
System-Browser-shaped layout as Circuit Schedule so electrical and mechanical
read alike: search box on top with a small "Search" label, tree below with
one glyph per row kind and depth accents dark-at-the-plant, a breadcrumb line
drawn from the current selection, Expand All / Collapse All buttons, and a
Show button per row that selects and zooms through the ExternalEvent. Row
labels carry the system name; row detail shows flow (`AsValueString`,
read-only) and, for piping systems, fixture units. Equipment rows that open
into their own systems get the branch glyph; leaves with unreadable
connectors carry the "no connectors readable" subtitle in DimGray. The
"Unassigned" bucket sits last at root. Footer status line:

> "Scanned 62 systems, 4 unassigned. Search filters in place — selection survives."

> "Snapshot read from HOSP-M-Central.rvt. Refresh to re-read."

**Fallback window** — when pane registration was missed, the identical
content opens as a modeless window pinned to the right edge, and the status
line says so:

> "Dockable registration missed — floating window pinned right. Restart Revit to dock."

### User operation flow

1. Click **System Schedule** in the new Systems pulldown. The pane opens (or
   the fallback window pins itself to the right edge) and the first snapshot
   is read.
2. Read the footer: system count, unassigned count, source document.
3. Type in the search box — "AHU-3" filters by token, expansion and selection
   survive the filter. The breadcrumb tracks whatever row is selected.
4. Expand a plant item: its systems, then the equipment they feed, then those
   boxes' own systems, down to terminals — or to the depth cap, which shows a
   truncated-branch row rather than recursing forever.
5. Press **Show** on any row: the elements are selected and zoomed in the
   active view via the ExternalEvent. The pane stays open and docked.
6. Open the **Unassigned** bucket to work the "Supply Air 14, no equipment"
   cleanup list; Show each one, fix it in the model, press Refresh, watch the
   bucket shrink.
7. There is no cancel path because there is no write path — closing the pane
   (or the fallback window) is the whole exit. Nothing was changed.

## See also

- Existing: **Circuit Schedule** (Misc Tools → Circuiting) — the engine donor
  and UX template; **Clash Detection Mode** — the other dockable-pane
  precedent and fallback pattern.
- Rank 09 **System Isolate** — same new Systems pulldown; the write-side
  companion (isolate what this pane finds).
- Rank 20 **Batch Insulation** and rank 36 **Air Balance** — Systems pulldown
  siblings that consume the same mechanical-system snapshot shape.
- Rank 08 **Warnings Watch** — the next consumer of the generic tree/search
  engine; whichever builds second inherits the split this tool proves.
- Rank 04 **Open Ends** / rank 15 **Connection Check** — the connector-graph
  tools; deliberately different machinery (they walk connectors, this tool
  walks system membership).
