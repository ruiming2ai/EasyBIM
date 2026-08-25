# 35 — Power Sweep

Every device that should be on a circuit and is not, in one window — found
by the tool instead of by a schedule-filter safari the week before issue.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 35 of 45 | Misc Tools — Circuiting pulldown | yes | S | 8/10 | 7/10 |

## Main purpose

A receptacle placed on Tuesday and never circuited looks identical to a
finished one in every view. The uncircuited stragglers only surface when
someone filters a schedule by blank panel — one schedule per category, per
level, by hand — and even that says nothing about the family that was
never given a power connector at all. The week before issue, "find
everything without power" is the job, and the model gives you no single
place to stand and see it.

Power Sweep is that place, read-only, in the Tags Sweep mould. The scope
card offers the power-consuming categories; one pass walks their family
instances and sorts every device into honest buckets. The verdict comes
from two release-stable facts, not one fragile API surface: whether the
element appears in any power circuit's member list (the same
`circuit.Elements` walk Circuit Schedule's snapshot already does), and how
many power connectors the family actually carries (a connector census
behind capability probes). *Unpowered* means it has a power connector and
sits on no power circuit. *No power connector* is counted separately —
conflating the two is how a sweep loses trust. *Partially fed* catches the
dual-feed cases — an ATS with its emergency side still open — where some
but not all power connectors are served. Because equipment appears in the
member list of the circuit that feeds it, the unfed panel falls out of the
same rule for free, and it is the loudest catch of all.

Circuit Schedule starts from circuits, so a device on no circuit is
precisely what it cannot show; schedules need one per category and are
mute about connectorless families; no EasyBIM tool sweeps devices, though
Tags Sweep proved the shape for annotation. Within the plan, 01 Circuit
Check judges the circuits that exist — this is its device-side converse.
Usefulness 8 because the sweep is the pre-issue ritual on every electrical
job; impact 7 because it finds the work rather than doing it, and S
because everything hard — the snapshot, the tree engine, the show/zoom —
already lives in `lib/easybim`.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Circuiting.pulldown/Power
  Sweep.pushbutton/`, added to the pulldown's `layout:`. `script.py` thin
  with `__persistentengine__ = True` — the window is modeless; Run,
  Refresh, and Show ride `ExternalEventBridge`. `bundle.yaml` (two-line
  title "Power\nSweep", narrative tooltip naming the three buckets and the
  closed-workset caveat, `author: Ruiming Liu`), 96×96 icons. Split:
  `power_sweep_state.py` (the verdict lattice, power-token matching,
  grouping, truncation accounting — zero Revit imports),
  `power_sweep_revit.py` (the circuit snapshot reuse plus the connector
  census), `power_sweep_ui.py`, `PowerSweepWindow.xaml`,
  `power_sweep_xlsx.py`. Reuse from `lib/easybim`:
  `circuit_schedule_revit.scan_model` (membership sets and system-type
  text), the generic tree/search half of `circuit_schedule_state`,
  `show_elements`, `ExternalEventBridge`, `compat`. Nothing hoists yet;
  the connector census is local until a second consumer appears (15
  Connection Check reads a far deeper connector surface and is not that
  consumer — different question, different walker).
- **Revit API route** — Two reads, both crossing back plain dicts. First,
  the lib snapshot: membership is *element id appears in a circuit's
  `element_ids` where that circuit's `system_type` text matches the power
  kind* — state owns the token match, and the count of distinct power
  circuits per element feeds the partially-fed verdict. This deliberately
  reuses the one API surface the lib already trusts (`BaseEquipment` +
  `Elements`) instead of `MEPModel.GetElectricalSystems`, whose accessors
  the lib docstring warns changed shape across releases. Second, the
  census: per checked category (`OST_ElectricalFixtures`,
  `OST_LightingFixtures`, `OST_ElectricalEquipment`,
  `OST_MechanicalEquipment`, and the device categories —
  `OST_DataDevices`, `OST_FireAlarmDevices`, `OST_CommunicationDevices`,
  `OST_NurseCallDevices`, `OST_SecurityDevices`, `OST_TelephoneDevices`,
  `OST_LightingDevices`), a `FilteredElementCollector`
  `OfClass(FamilyInstance)` `WhereElementIsNotElementType()`; per
  instance, `MEPModel.ConnectorManager.Connectors` behind `hasattr`
  probes, counting connectors whose `Domain` is electrical and whose
  `ElectricalSystemType` is a power type — the family-authored
  Power-Balanced/Unbalanced kinds that circuit creation turns into
  PowerCircuit systems. Switch, data, telephone, and control connectors
  never count, which is what keeps a switched-only lighting device out of
  the unpowered list. A census that raises lands the element in
  "connectors unreadable", never in a verdict. Level from `LevelId` with
  "no level" fallback; `DesignOption` probed so secondary-option elements
  separate out. No writes, so no Transaction of any kind — pinned by the
  command-names test.
- **The scan/report cycle** — read-only: scan → judge → report.
  `power_sweep_revit.scan_model(doc, category_ids)` returns the circuit
  membership snapshot plus device dicts (id, name, type, category, level,
  power connector count, option flag). `power_sweep_state.evaluate` sorts
  every device through the lattice: powered / unpowered / partially fed /
  no power connector / unreadable, then groups Level → Category inside
  each bucket. Refresh re-runs both passes against the live model; an
  all-clear run says so in words. Per-category element budgets cap the
  pass; a truncated category renders its own row — "stopped at 20,000
  Electrical Fixtures — narrow the scope" — so a guard bug costs a
  truncated branch, never a hung Revit.
- **Edge cases & honest limits** — the buckets above are the contract,
  and two caveats are stated rather than discovered: elements on closed
  worksets are not collected, so the subtitle says "Closed worksets are
  not read"; elements in non-primary design options list under their own
  header rather than polluting the punch list. The "no power connector"
  bucket renders collapsed by default — in a mechanical-heavy model it is
  large and it is information, not alarm. The tool refuses to judge
  switch, data, or comm connectivity (power only in v1, tooltip says so),
  refuses to decide whether equipment *should* be fed — a spare ATS is a
  human call — and never creates a circuit. Spares and spaces have no
  member elements and cannot vouch for anything, which is correct.
- **Risks** — Deciding "has a power connector" is the whole tool: the
  connector census must not count switch or low-voltage connectors, and
  families modelled with bare connectors but no MEPModel systems present
  differently per release — the probe order (MEPModel absent →
  ConnectorManager absent → Connectors raising) needs its own
  desktop-tested fake per API generation, and every failure mode must land
  in "connectors unreadable" visibly. Splitting the verdict across two
  surfaces (membership from the circuit snapshot, capability from the
  census) is the defense: the fragile surface can only ever misplace a
  device into the unreadable bucket, never silently mark it powered. The
  membership set must be built once as a plain int set — O(1) lookups —
  or a big model turns the judge quadratic. Large models get the
  bounded-everything treatment: per-category caps, truncation rows, and a
  status line that ticks per category.
- **Tests** — `test_power_sweep_state.py` pins the verdict lattice
  (0/1/2 connectors × 0/1/2 power-circuit appearances), the power-token
  match against system-type text (a "Data" circuit vouches for nothing),
  truncation accounting, level fallback and option grouping, and the
  token-vs-substring search rule ("12 does not find 112"). —
  `test_power_sweep_command_names.py` pins bundle metadata and the grown
  pulldown layout, XAML↔handler wiring, icon sizes, the IronPython AST
  scan, and the no-Transaction pin. — `test_power_sweep_revit.py` drives
  the adapter against fakes per API generation — MEPModel missing,
  ConnectorManager missing, Connectors raising mid-iteration, mixed
  connector types on one family, design option present — asserting
  nothing but ints and unicode crosses back. — `test_power_sweep_xlsx.py`
  pins the punch-list export rows and header order.

## UI description

**Main window** — one resizable modeless window (`ShowInTaskbar` off,
centered, grip-resizable), root `Grid Margin="14"`, rows Auto/*/Auto.
Header "Power Sweep" SemiBold ~30px over a DimGray subtitle: "Read-only.
Power connectors only — closed worksets are not read." Top card, **Scope**:
checkbox list of the categories with live instance counts ("Electrical
Fixtures — 1,204"), count line "4 of 11 categories selected.", Select All
/ Select None; zero-instance categories greyed with "none in this model"
tooltips; the device categories ship unchecked with the tooltip "usually
no power connector — counted separately when checked". Body after Run: a
summary strip — "1,204 devices scanned — 37 unpowered, 3 partially fed,
12 with no power connector." — over the results tree: expanders
**Unpowered (37)**, **Partially fed (3)**, **No power connector (12)**
(collapsed by default), **Not classified (0)**, each grouped Level →
Category, each leaf a device row (type name, id linkified, level) with a
**Show** button (select + zoom via ExternalEventBridge). A Search box
filters by type-name substring or element-id token — visibility flips,
expander state survives. Footer: status TextBlock left, then **Run**
(`IsDefault`, relabels to **Refresh** after the first pass), **Export**
(disabled until a run, tooltip "Run a sweep first."), **Close**
(`IsCancel`).

> "1,204 devices scanned — 37 unpowered, 3 partially fed, 12 with no power connector. Nothing was changed."

> "Sweeping Lighting Fixtures… 800 of 1,950."

> "All 1,182 devices with a power connector sit on a circuit."

> "Electrical Fixtures truncated at 20,000 — narrow the scope and re-run."

There is no confirmation window and no report window — the tool never
writes. Export writes the visible buckets to .xlsx through the standard
save dialog for the punch list.

### User operation flow

1. Ribbon: Misc Tools → Circuiting → Power Sweep. The window opens; the
   scope card fills with categories and live counts.
2. Keep the four power-consuming categories checked; check Fire Alarm too
   if that model powers its panels here. Press **Run**.
3. The status line ticks per category while the bridge does the two
   passes; the tree fills, Unpowered first.
4. Expand L3 → Electrical Fixtures, click **Show** on a receptacle —
   Revit selects and zooms. Circuit it (Revit's own tools or the
   Circuiting siblings), then press **Refresh** — the row is gone,
   expander and search state survive.
5. A separated item looks like: a row under "No power connector (12)"
   reading "Junction Box 100A — id 415220 — L2", or under "Not
   classified" reading "connectors unreadable — in-place family". Neither
   is counted as unpowered.
6. A partially fed item looks like: "ATS-1 — 2 power connectors, 1
   circuit — emergency side open."
7. **Export** writes the remaining buckets to .xlsx — the punch list the
   last week works from.
8. **Close** (or Esc) at any point — the cancel path and the happy path
   are the same door, because nothing was ever going to be written.

## See also

- Existing: **Tags Sweep** (the model-wide sweep shape this borrows for
  devices), **Circuit Schedule** (the membership snapshot and the
  show/zoom helper).
- Siblings: **01 Circuit Check** — judges the circuits that exist; this
  finds the devices that have none. **34 Spare Capacity** — where the
  newly found devices will ask for room. **06 Load Names** — the naming
  pass that follows the circuiting this tool provokes. **04 Open Ends** —
  the same sweep temperament applied to duct and pipe connectors, and the
  place the deeper connector-surface manners live.
