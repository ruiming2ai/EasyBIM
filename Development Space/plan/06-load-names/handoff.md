# 06 — Load Names

Pattern-driven batch writer for circuit load names — "RECEPT — LEVEL 2 OFFICE 214" instead of "Duplex Receptacle, Duplex Receptacle, Duplex Receptacle".

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 06 of 45 | Misc Tools — Circuiting pulldown | yes | M | 9/10 | 8/10 |

## Main purpose

Revit's automatic circuit Load Name is a comma dump of connected device types,
and the moment you hand-type something better it freezes forever. Renaming two
hundred circuits to a house standard by hand, per panel, is an afternoon of
the same twelve keystrokes — and it drifts the first time a device moves
rooms, because the frozen text never updates. Every electrical deliverable
carries these names onto panel schedules, so the pain lands on paper.

Load Names writes `RBS_ELEC_CIRCUIT_NAME` from a token pattern —
`{category} {level} {room} {panel} {number}` and friends — evaluated per
circuit from what the circuit actually feeds. The adapter walks each
circuit's elements once and gathers category, type, level, and location;
the state module dedupes repeated values and resolves multi-room circuits
with a deterministic majority-wins rule, ties listed so the result never
looks random. Every proposed rename is staged red before anything is
written, circuits with nothing to name from are named skips (never written
blank), and an acknowledgement checkbox states the honest cost up front:
written load names stop auto-updating.

It earns rank 6 because the need is universal on electrical jobs and nothing
covers it: Update Circuit Rating moves numbers from devices to circuits —
this moves words, and no existing EasyBIM tool touches circuit naming.
Native Revit's auto-name is exactly the problem, and the pyRevit/Dynamo
equivalents do flat find-replace with no room lookup, no tie rule, and no
skip ledger. Presets stored by name make the office standard portable
between projects, per the identity-by-name rule in DESIGN-CONTEXT.md.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Circuiting.pulldown/Load Names.pushbutton/`
  with thin `script.py`, `bundle.yaml` (two-line title, narrative tooltip,
  `author: Ruiming Liu`), 96×96 icons. Four-layer split in the pushbutton:
  `load_names_state.py` (pattern engine, dedupe, tie rule, plan builder —
  pure Python), `load_names_revit.py` (collector + element walk + writer),
  `load_names_ui.py` + `LoadNamesWindow.xaml` and `LoadNamesReport.xaml`.
  This build is the natural moment to clear the recorded deferred hoist:
  `collect_circuits` already exists twice across the lib/pushbutton boundary
  (`circuit_schedule_revit` in lib, the rating tool's local copy) — hoist it
  to a shared lib home, import it here, and point `circuit_schedule_revit`
  at it. Presets persist by name under `%APPDATA%\EasyBIM\Load Names\`.
- **Revit API route** — Circuits via the hoisted `collect_circuits`
  (`OST_ElectricalCircuit`, not element types), each cast to
  `Electrical.ElectricalSystem`. Skip spares and spaces up front via
  `ElectricalSystem.CircuitType` — only real circuits are candidates. Per
  circuit read `PanelName`, `CircuitNumber` (identity is panel name +
  number, never ElementId), the current `RBS_ELEC_CIRCUIT_NAME`, and walk
  `Elements` once: per fed element, category name, type name, level, and
  location — `inst.get_Space(phase)` first, `inst.get_Room(phase)` as host
  fallback, with "no room/space" as an explicit token. The phase index is
  chosen explicitly in the Main window (ComboBox, defaulting to the
  document's last phase) because `get_Space`/`get_Room` are phase-indexed
  and guessing silently is how a tool looks random. All of it crosses back
  as plain dicts. Writing is `get_Parameter(RBS_ELEC_CIRCUIT_NAME).Set()`
  after an `IsReadOnly` check; in workshared models a
  `WorksharingUtils.GetCheckoutStatus` probe marks rows owned elsewhere
  before the write is attempted. Commit shape: one assimilated
  `TransactionGroup`, one nested `Transaction` per circuit, so a single
  locked parameter rolls back one row and never the batch; counters zero on
  rollback. No ExternalEvent or Idling — the window is modal and short-lived.
- **The plan/apply cycle** — `build_plan` evaluates the pattern per checked
  circuit and produces one plan object read by both the staged grid and the
  executor, so preview and write cannot drift: circuit identity (panel +
  number), current name, proposed name, and the skip bucket if any.
  Proposals equal to the current name are "skipped — already named";
  circuits with no located elements are "skipped — nothing to name from",
  never written blank. The staged grid renders every rename red until
  Apply; the Apply button stays disabled — never hidden — with the reason
  in a tooltip until the acknowledgement checkbox "Written load names stop
  auto-updating" is ticked. After commit the Report window reads the names
  back from the committed model, not from the plan, and lists every skip
  with its reason — skipped is distinguished from failed throughout.
- **Edge cases & honest limits** — Named-skip buckets: "already named",
  "nothing to name from", "spare/space circuit", "parameter read-only",
  "owned by another user", "unchecked". Multi-room circuits: majority wins;
  a tie resolves alphabetically and the row lists all contenders in its
  detail, so the choice is deterministic and visible. Rooms usually live in
  the architectural link, where a host-model Room lookup returns nothing —
  Spaces are the honest primary source and the window says so in its
  subtitle; reaching into link documents by point is explicitly out of
  scope for v1 (phase-sensitive, slow, and a scope decision that deserves
  its own tool). The `{room}` token therefore resolves Space name/number
  first. The tool refuses to guess a name from zero located elements and
  refuses to write through a tie silently.
- **Risks** — The link-room gap is the one users will ask about first; the
  UI must state it rather than let an empty `{room}` look like a bug.
  Walking `Elements` with phase-indexed Space lookups across hundreds of
  circuits is the slow path — one pass, results cached in the plan, and a
  cancellable `forms.ProgressBar` if the scan exceeds the house threshold.
  `CircuitType` and the space accessors are stable, but the fakes must
  still cover an element whose `Space` throws (families with no location
  point). The tie rule must ship exactly as tested or two runs on the same
  model produce different names.
- **Tests** — `test_load_names_state.py`: token evaluation, dedupe,
  majority/tie determinism, "already named" and "nothing to name from"
  classification, preset round-trip by name. `test_load_names_command_names.py`:
  bundle metadata, XAML↔handler wiring for both windows, icon sizes,
  IronPython AST scan, forbidden-API pins. `test_load_names_revit.py`:
  adapter against fakes — space-then-room fallback, phase indexing, spare
  `CircuitType` exclusion, read-only parameter skip, checkout-status skip,
  per-circuit rollback zeroing its counter.

## UI description

**Main window** — resizable modal, header "Load Names" over a DimGray
subtitle "Names are built from what each circuit feeds. Spaces are read
first; rooms in linked models are not visible here." Two cards side by
side. Left card, "Pattern": a ComboBox of saved presets (portable, stored
by name), a row of token insert buttons ({panel} {number} {category}
{type} {level} {room}), the phase ComboBox, and a live example line
rendered from the first checked circuit. Right card, "Panels & circuits":
checkbox tree of panels and their circuits with count line ("14 panels,
212 circuits — 96 checked, 116 unchecked."), Select All / Select None, and
a Search box (numbers match as whole tokens — 12 does not find 112). Below
both cards, the staged grid: Panel, Ckt, Current Name, New Name — every
proposed rename red until Apply, skip rows greyed with their bucket named.
Footer: status text left, acknowledgement checkbox "Written load names
stop auto-updating", then Apply (`IsDefault`, disabled until ticked, reason
in tooltip) and Cancel (`IsCancel`).

> "96 renames staged, 41 skipped — 22 already named, 11 nothing to name from, 8 spare."

**Report window** — read-only WPF table after commit, never stacked
message boxes: Panel, Ckt, Name (read back from the model), Result. Skips
listed under their named buckets, failures (a nested rollback) separate
from skips. Footer status:

> "94 written, 41 skipped, 2 rolled back — read back from the model."

### User operation flow

1. Click **Load Names** in the Circuiting pulldown. The Main window opens;
   the scan runs and the count line fills in.
2. Pick or build a pattern; the example line updates live from the first
   checked circuit. Pick the phase if the default is wrong.
3. Check panels/circuits; search filters visibility without losing checks.
4. The staged grid fills red with proposals; skip rows grey out with their
   reason. Review, uncheck any row you disagree with.
5. Tick "Written load names stop auto-updating"; Apply enables.
6. Apply commits one TransactionGroup (one undo step). A locked or
   checked-out circuit rolls back its own nested transaction and moves to
   the skip ledger — one bad circuit never costs the batch.
7. The Report window opens with names read back from the committed model.
   Close it; one Ctrl+Z in Revit reverts everything.
8. Cancel path: Cancel (or Esc) at any point before Apply closes the Main
   window with the model untouched — declined choices are skipped, never
   failed, and nothing was written.

## See also

- Existing: **Update Circuit Rating** (same pulldown — numbers where this is
  words, and the origin of the per-circuit rollback shape); **Circuit
  Schedule** (the panel + number identity and `collect_circuits` hoist
  partner).
- Rank 01 **Circuit Check** — the QA sweep that would flag stale or
  inconsistent load names this tool then fixes.
- Rank 19 **Circuit Renumber** and rank 21 **Circuit Excel** — Circuiting
  siblings sharing the panel+number portable identity.
- Rank 24 **Space Sync** — the workflow neighbor upstream: it creates and
  maintains the Spaces this tool reads its `{room}` token from.
