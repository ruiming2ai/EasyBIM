# 15 — Connection Check

Judges every live MEP joint and every authored connector against the system
it actually serves — supply mated to return, backwards valves, size steps,
208 V gear on a 480 V circuit.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 15 of 45 | Misc Tools / MEP Checks (new pulldown) | yes | L | 9/10 | 9/10 |

## Main purpose

The joints are where MEP models lie. A hydronic supply mated to a return, a
check valve installed backwards, a 4-inch connector jammed onto a 2-inch
pipe — each looks joined on plan and each quietly poisons a calculation. The
authoring side lies too: a family connector with the wrong system
classification, a 208 V connector on 480 V gear, one pole where the circuit
has three, or an architectural placeholder that survived into the MEP model
with no connectors at all and can never join a system. Native Show
Disconnects draws orange glyphs in one view for exactly one of these
defects; everything else surfaces as a system that refuses to compute, found
by view-by-view archaeology the week before a model exchange.

Connection Check is one auditor for both halves — this is a deliberate merge
of two brainstorms, physical-joint rules and connector-authoring rules, into
one tool with one findings tree and a rule chip per finding kind. One pass
snapshots every physical connector in scope into plain dicts; every judgment
then happens in desktop-tested state code. Each finding states exactly what
it compared, as two values on the row ("208 V connector — 480 V circuit"),
because a conservative report that explains itself is the only kind that
gets believed. It is read-only; the exportable report with a reviewer column
is the deliverable, the QA record a model exchange can attach.

One rule is deliberately absent: the plain open-end sweep is **04 Open
Ends**, already shipped on the same connector-graph walker, and this tool
links to it rather than re-implementing it. That split is the curator's:
Open Ends is the S-effort resident pane, Connection Check is the deep-rules
audit you run on demand. Nothing else occupies this ground — Show
Disconnects covers open ends only, Circuit Schedule draws the electrical
tree but judges nothing, Families Downgrade reads connectors inside family
documents for a different purpose, and no free tool known combines
classification, direction, and size rules with authoring-vs-circuit checks
and a reviewable export. The 9/9 scores reflect that; the L effort — seven
rules, two domains' worth of probing, hospital-scale performance — is what
holds it to rank 15.

## Basic implementation ideas

- **Bundle & module layout** — joins the MEP Checks pulldown that **03 Slope
  Check** creates: `EasyBIM.tab/Misc Tools.panel/MEP Checks.pulldown/
  Connection Check.pushbutton/` (add to the pulldown's `layout:`), with
  `script.py` (`__persistentengine__ = True` — the results window is
  modeless), `bundle.yaml`, `ConnectionCheckSetupWindow.xaml`,
  `ConnectionCheckResultsWindow.xaml`, `connection_check_state.py` (the rule
  engine and tree assembly — pure Python), `connection_check_revit.py` (the
  pass), `connection_check_ui.py`, `connection_check_xlsx.py`. Reuse from
  lib: the connector-graph walker that 03/04 declared the shared hoist —
  by build order it should already sit in `lib/easybim/` (Open Ends put its
  walker there for the dockable pane); if this ships first instead, it
  writes the walker locally and becomes the hoist donor. The findings tree
  runs on `circuit_schedule_state`'s generic Node / build_index /
  token-search engine, and electrical circuits come from
  `circuit_schedule_revit.collect_circuits` in lib — the repo already
  records that collector's duplication as a deferred hoist; this tool
  consumes the lib copy and must not fork a third.
- **Revit API route** — one pass: curve classes (`Mechanical.Duct`/
  `FlexDuct`, `Plumbing.Pipe`/`FlexPipe`, `Electrical.CableTray`,
  `Electrical.Conduit`) plus `FamilyInstance` with a non-null
  `MEPModel.ConnectorManager`. Per connector, keep physical only
  (`ConnectorType` End/Curve; logical skipped by design) and snapshot:
  owner id, index, `IsConnected`, `Domain`, `Shape`, `Radius`/`Width`/
  `Height` (probed per domain), `Direction` (`FlowDirectionType`, behind
  `hasattr`), `DuctSystemType`/`PipeSystemType` (probed), `MEPSystem` name,
  primacy via `GetMEPConnectorInfo().IsPrimary` where present, and the
  `AllRefs` partner filtered to physical End/Curve — the filter that keeps
  logical partners and analytical noise from becoming false joints.
  Electrical values ride the two-channel rule from Circuit Check: judging
  numbers from `AsDouble()`/`AsInteger()` in internal units — device side
  from `RBS_ELEC_VOLTAGE` and `RBS_ELEC_NUMBER_OF_POLES` resolved with the
  `getattr(DB.BuiltInParameter, name, None)` guard, circuit side from
  `ElectricalSystem` via `collect_circuits` (`Voltage`, `PolesNumber`
  behind probes) — display text from `AsValueString()` for showing only,
  never parsed back. No writes, no Transaction (pinned). Scan, Refresh,
  and Show ride `ExternalEventBridge` because the results window is
  modeless.
- **The scan/report cycle** — read-only: scan → judge → tree.
  `connection_check_revit.scan(doc, scope)` returns connector dicts, joint
  pairs, circuit dicts, and scan metadata (visited counts, caps hit) — ints,
  floats, unicode only. `connection_check_state.evaluate(snapshot, rules,
  tolerances, exclusions)` runs the seven rules, each its own chip:
  **Mates** — the two sides of a live joint declare conflicting system
  classifications (supply to return, sanitary to vent); **Direction** — both
  mated connectors are directional and agree In-to-In or Out-to-Out (the
  backwards check valve and pump); **Size** — the two mated connectors of
  one joint differ beyond tolerance (judged per joint, so a reducer's own
  two ends are never a finding); **Undefined** — elements riding the
  default/undefined classification; **Voltage** and **Poles** — a device's
  primary electrical connector against the circuit it actually sits on;
  **No connectors** — MEP-category families whose symbols expose no
  connectors at all, the dumb content that can never join a system.
  Findings group kind → system → level → element on the tree engine, the
  compared values on each row's second line, per-branch display caps with
  the truncation stated on the branch. Refresh re-scans the live model;
  chips, search, and expansion survive. Export writes the flat findings
  plus skips through `connection_check_xlsx`, with an empty reviewer
  column for the QA record.
- **Edge cases & honest limits** — named-skip buckets: *"connectors
  unreadable (n)"* for elements whose `MEPModel` or manager throws — fail
  closed, never counted clean; *"secondary connector — not compared"* for
  multi-connector electrical devices, where per-connector circuit
  attribution is ambiguous and the tool compares the primary only;
  *"not on a circuit"* for the Voltage/Poles rules (that absence is Power
  Sweep's finding, not this tool's); *"branch capped at N — search to
  narrow"*. Rules grey per domain where they cannot apply (tray and conduit
  carry no classification), and any accessor absent on this Revit version
  greys its chip with the reason instead of guessing. The Direction chip's
  tooltip states up front that most content declares Bidirectional
  connectors, so the rule fires rarely — and each hit is a backwards valve;
  without that sentence the low count looks like a broken rule. The tool
  judges declared classifications and authored values, not hydraulics: a
  clean report is model QA, not engineering signoff, and the subtitle says
  nothing is ever changed.
- **Risks** — conservative heuristics or the report cries wolf: every rule
  compares only what both sides explicitly declare, and anything inferred is
  out (this is why there is no "probably backwards" rule for Bidirectional
  valves). `AllRefs` noise is the classic false-joint trap; the
  ConnectorType filter is the defense and the fakes must cover it. The
  performance cliff is a hospital model's connector count: strictly one
  collector pass, dict-keyed joint pairing in state code, never re-entering
  the API mid-judgment, bounded display — the caps are the feature.
  Exclusions ship conservative (exclude nothing; checkboxes widen), because
  a default exclusion is a silent drop wearing a nicer name. Coordination
  risk: the walker hoist with 03/04 must be settled at build time — consume
  lib if present, donate if first, never a second copy.
- **Tests** — `test_connection_check_state.py` pins each rule at its
  boundaries: Bidirectional pairs excluded from Direction, joint-local size
  judgment (reducer not flagged, mismatched joint flagged at exactly the
  tolerance), primary-connector attribution, undefined-classification
  detection, chip/domain applicability, exclusion widening, branch caps.
  `test_connection_check_command_names.py` pins the grown pulldown layout,
  bundle metadata, XAML↔handler wiring for both windows, icon sizes, the
  IronPython AST scan, and the no-Transaction pin.
  `test_connection_check_revit.py` drives the adapter over fakes shaped like
  each API generation — missing `MEPModel`, throwing ConnectorManager,
  logical partners in `AllRefs`, absent `FlowDirectionType`, absent
  electrical BuiltInParameter names — asserting every failure lands in a
  named bucket and only plain data crosses back.
  `test_connection_check_xlsx.py` pins export columns, the reviewer column,
  and the skip rows.

## UI description

**Setup window** — Interference-Check-shaped modal, `Grid Margin="14"`,
header "Connection Check" over a DimGray subtitle: "Read-only. Open ends are
Open Ends' job — this judges the connections that exist." Three cards.
**Rules card**: seven checkboxes, each label in its own TextBlock with a
one-line DimGray why beneath ("Direction fires rarely — each hit is a
backwards valve."), count line "7 of 7 rules selected.", Select All / Select
None. **Scope card**: a ComboBox — Whole model / Active view / Picked
systems — with a checkbox system list and Search appearing for the third
choice. **Tolerances & exclusions card**: size tolerance, voltage tolerance,
and the equipment-classification exclusion checkboxes, all unticked by
default. Footer: status left ("Rules that cannot run on this Revit version
will appear greyed with the reason."), **Check** (`IsDefault`), **Cancel**
(`IsCancel`).

**Results window** — resizable modeless, staying open beside Revit. Top
strip: Search box (identifiers by token — "12" does not find 112; names by
substring) and the rule chips with live counts — "Mates 12 · Direction 2 ·
Size 9 · Undefined 21 · Voltage 3 · Poles 1 · No connectors 8" — that filter
the tree without rebuilding it. Body: the findings tree, kind → system →
level → element, each leaf carrying the two compared values on its second
line and a **Show** button (select + zoom via ExternalEventBridge). A
**Named skips** expander sits last. Footer: **Refresh**, **Export**,
**Close**, status line left:

> "18,240 connectors read in one pass — 56 findings, 9 elements skipped (unreadable — listed). Nothing was changed."

> "Voltage rule greyed: electrical parameters unreadable on this Revit version."

> "All checked rules pass on 18,240 connectors. Nothing was changed."

### User operation flow

1. Ribbon: Misc Tools → MEP Checks → Connection Check. The Setup window
   opens with all seven rules ticked and nothing excluded.
2. Narrow scope if wanted, set tolerances, press **Check**. Cancel here
   closes with nothing read beyond the setup lists.
3. The Results window opens as the pass runs, status ticking; chips fill
   with counts, worst kinds first in the tree.
4. Click a chip to isolate a kind; **Show** on "CHW Supply ↔ CHW Return —
   L3 — mated at fitting 512907", fix the joint in the model.
5. A skipped item looks like: "AHU-7 — connectors unreadable — not judged"
   under Named skips, or a greyed Voltage chip with its reason in the
   tooltip. Skips are never counted as findings.
6. **Refresh** after fixing — the pass re-runs against the live model;
   fixed rows disappear, chips, search, and expansion survive.
7. **Export** writes findings, skips, and the empty reviewer column to
   .xlsx — the QA record for the exchange.
8. **Close** or Esc at any time; both windows write nothing, ever, so the
   cancel path and the happy path are the same door.

## See also

- Existing: **Circuit Schedule** (tree/search engine, show/zoom helper, and
  the `collect_circuits` this consumes from lib), **Families Downgrade**
  (reads connectors inside family documents — the authoring-side cousin
  with a different purpose), **Clash Detection Mode** (the geometric
  counterpart: where things touch that should not, versus joints that
  should be right and are not).
- Siblings: **04 Open Ends** (the S-effort disconnect sweep and walker
  donor — deliberately not re-implemented here), **03 Slope Check**
  (creates the MEP Checks pulldown; same walker, same read-only
  temperament), **01 Circuit Check** (judges circuits by their numbers the
  way this judges joints by their connectors), **35 Power Sweep** (owns the
  "not on a circuit" finding this tool deliberately skips), **44 Fixture
  Units** (the next resident of the pulldown).
