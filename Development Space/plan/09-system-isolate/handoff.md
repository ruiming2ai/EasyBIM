# 09 — System Isolate

One pick → the whole run traced, isolated, and framed in a reusable named 3D view.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 09 of 45 | Misc Tools / Systems (new pulldown) | yes | S | 9/10 | 7/10 |

## Main purpose

The most common mechanical question — "show me this whole run, alone, in 3D" —
takes native Revit a System Browser hunt, a selection, a new 3D view, a section
box, and a temporary isolate, and the result evaporates when the view session
ends. People burn ten minutes per question, several times a day, and the
follow-up question ("now the return side") starts the whole dance again.

System Isolate answers it from one pick. Click an element — or start from the
current selection — and the tool traces the full connected network through the
connectors, across fittings and (by a checkbox) through equipment, with a
visited set and a hard element budget so a pathological loop costs a truncated
trace, never a hung Revit. When the picked element carries one clean MEPSystem
it also offers system membership as the faster, exact scope. It then creates
*or reuses* a 3D view named `EB System - {system name}` — identity by name, so
re-running updates the same view instead of littering `3D View 47` copies —
sets the section box to the traced extents plus padding, and isolates by one of
two modes whose lifetimes are stated on the label: **Temporary isolate** (quick,
this session only) or a **view filter on System Name** (survives save, portable,
visible in Visibility/Graphics like any filter).

It earns rank 9 on frequency times ceremony saved. pyRevit's
section-box-from-selection tools need the whole system already selected, which
is exactly the hard part; native System Browser "Show" merely highlights in
whatever view is open; nothing free traces from a single pick, crosses the
fittings and equipment a window-selection misses, reuses a named view on rerun,
or offers a persistent filter-based isolation. Inside EasyBIM it is the spatial
twin of Circuit Schedule's Show button — that tree answers "where does this
circuit go", this answers "show me this duct run" — and it is deliberately not
the Sheet panel's Isolate in All, which isolates *categories* across views; this
isolates *one traced network* in one view. Effort is S because there is no
modeless machinery at all: a pick wizard, a bounded read pass, and two view
transactions.

## Basic implementation ideas

- **Bundle & module layout** — New `Systems.pulldown` inside
  `EasyBIM.tab/Misc Tools.panel/`, sibling of `Circuiting.pulldown` and shaped
  exactly like it (pulldown `bundle.yaml` with `layout:`, own icons). This tool
  is `System Isolate.pushbutton/` with `script.py` (STEP_* wizard loop),
  `system_isolate_state.py` (traversal over plain dict graphs, bbox union and
  pad caps, view-name derivation — zero Revit imports),
  `system_isolate_revit.py` (connector reads into dicts, view/filter writes),
  `system_isolate_ui.py` + one XAML. The graph walker is shared territory with
  03 Slope Check, 04 Open Ends, and 15 Connection Check: if one of those has
  already landed, consume `lib/easybim/mep_network.py`; if this builds first,
  keep the walker local but shaped for that hoist (dict-in/dict-out, no UI
  strings inside), and the second consumer moves it per the house rule.
  Pulldown siblings per the plan: 05 System Schedule, 20 Batch Insulation,
  36 Air Balance.
- **Revit API route** — Start set from `uidoc.Selection.GetElementIds()` or
  `PickObject(ObjectType.Element)` with a selection filter that rejects linked
  elements. Connectors come from `MEPCurve.ConnectorManager` and
  `FamilyInstance.MEPModel.ConnectorManager`; traversal follows
  `Connector.AllRefs`, filtering to `ConnectorType.End`/`Curve` and skipping
  refs whose `Owner` is a `MEPSystem` or `InsulationLiningBase` — `AllRefs`
  hands back the system element itself as a logical owner, and treating it as a
  neighbour would swallow the whole system in one step. Equipment (an element
  whose `MEPModel` sits on more than one system, or category Mechanical
  Equipment) is a stop node unless "trace through equipment" is ticked. System
  scope reads `Connector.MEPSystem` → `MEPSystem.Elements`. Insulation and
  lining are separate elements with no connectors: one collector over
  `InsulationLiningBase` builds a `HostElementId` map so traced hosts bring
  their insulation into the isolate set — without this, temporary isolate hides
  the insulation of the very pipes it shows. View: find `View3D` by exact name,
  else `View3D.CreateIsometric` with a `ViewFamilyType` of
  `ViewFamily.ThreeDimensional` and no view template;
  `SetSectionBox` + `IsSectionBoxActive`. Isolation:
  `View.IsolateElementsTemporary(ids)`, or a `ParameterFilterElement` named
  like the view with a *not-contains* string rule on
  `RBS_SYSTEM_NAME_PARAM` (System Name is comma-joined on multi-system
  equipment, which is why not-equals would be wrong), categories limited to
  those `ParameterFilterUtilities` reports as accepting the parameter,
  visibility off in the view. The string-rule constructor arity changed across
  generations (the caseSensitive argument era) — a `compat`-style try/except
  probe picks the living overload; if `ParameterFilterElement.Create` with an
  `ElementFilter` is absent, the filter mode greys out with the reason rather
  than shipping a rot branch. Transaction shape: one `TransactionGroup`
  "Isolate system", assimilated — T1 create/reuse the view, T2 section box +
  isolation — then `uidoc.RequestViewChange(view)` after the group commits
  (active view cannot change inside a transaction). No ExternalEvent, no
  Idling: the wizard is sequential.
- **The plan/apply cycle** — `build_plan` runs after the pick: it computes the
  traced element set, per-domain counts, systems touched, levels spanned, the
  open-end count where the trace stopped, the section box (union of
  `get_BoundingBox(None)` plus padding capped at min(10% of the dimension,
  2 m)), the view name and whether it will be created or reused, and which
  isolation modes are available. Step two of the wizard *is* the confirmation:
  the trace summary card shows exactly those numbers before "Show in 3D"
  writes anything. No acknowledgement tick — nothing here is irreversible; the
  whole write is one undo step and view-only. The status line after commit is
  the report, read from what was actually done: which view, created or
  updated, how many elements isolated, and any warning ("section box spans 12
  levels"). Rerun with the same system re-enters the same view idempotently:
  stale EB filters on that view are removed before the new mode applies.
- **Edge cases & honest limits** — Named refusals, never widened scope: a pick
  with no readable connectors and no system does nothing and says why ("Picked
  element has no MEP connectors and belongs to no system — nothing to trace");
  it never falls back to isolating the category. Linked elements are rejected
  at pick time ("linked elements cannot be traced — open the link"). Cable
  tray and conduit trace fine but carry no MEPSystem, so system scope and the
  filter mode disable with the reason in a tooltip. A view name that exists
  but is not a 3D view gets a ` (2)` suffix and a note. A reused view that has
  since been given a view template locking V/G or the section box is not
  fought: the blocked write is named in the status line and the rest proceeds.
  Temporary isolate is labelled "(this session only)" — Revit's design, said
  plainly, never oversold. A trace that hits the element budget is truncated
  and says so; the truncated result is still isolated, with the count marked
  "at least".
- **Risks** — The double-build trap: 03/04/15 share this walker, and building
  it twice guarantees the two traversals eventually disagree; the hoist rule
  above is the mitigation, and the handoff of whichever builds second must
  honour it. Far-flung systems (a riser spanning 40 levels) produce a section
  box so tall it is useless — the pad caps do not fix that, so the summary
  must warn and the user decides. `AllRefs` noise (system owners, own-element
  refs) silently corrupts counts if unfiltered — pin it in tests. Five-figure
  element networks make the read pass the slow step; the budget bounds it and
  the summary shows the truncation rather than a spinner. Filter mode on a
  view whose template controls filters simply does not stick — detect via
  `ViewTemplateId` and report, do not detach the template.
- **Tests** —
  - `test_system_isolate_state.py` pins traversal on dict graphs: visited-set
    loop truncation at the budget, through-equipment stop/continue, open-end
    counting, insulation-host inclusion, bbox union with pad caps and the
    tall-box warning threshold, view-name derivation and the ` (2)` collision
    rule.
  - `test_system_isolate_command_names.py` pins the new pulldown and button
    bundle.yaml metadata, XAML↔handler wiring, 96×96 icon pairs for both
    themes, the IronPython AST scan, and the forbidden-import rule on the
    state module.
  - `test_system_isolate_revit.py` drives the adapter against fakes shaped per
    API generation: an `AllRefs` set that includes a MEPSystem owner (must be
    filtered), a fitting reachable only via `MEPModel.ConnectorManager`, both
    string-rule constructor arities, the reuse-by-name path including the
    non-3D name collision, and the template-locked view refusal.

## UI description

**Main window (wizard)** — a small resizable modal in the house STEP_* pattern:
the window sets `self.result` verb strings and closes so `script.py` can run
Revit's pick UI, then re-shows with state preserved. Header: "System Isolate"
over a DimGray subtitle "Trace a connected run from one pick and frame it in a
named 3D view."

- *Step 1 card* — two buttons as command links: "Pick an element" and "Use
  current selection (3 elements)", the latter disabled with a tooltip when the
  selection is empty. Footer status: "Pick a duct, pipe, fitting, or a piece
  of equipment."
- *Step 2 card (trace summary)* — the plan, as counts: "214 elements · 2
  systems (SA-AHU-1, RA-AHU-1) · levels L1–L4 · stopped at 3 open ends."
  Below it a radio pair when system scope is available — "Connected network
  (214)" / "System membership: SA-AHU-1 (89)" — a "Trace through equipment"
  checkbox (re-traces on toggle), and the mode ComboBox: "Temporary isolate
  (this session only)" / "View filter on System Name (survives save)", the
  filter entry greyed with its reason when unavailable. Primary button
  **Show in 3D** (`IsDefault`), then **Pick again**, then **Close**
  (`IsCancel`).

The window stays in the loop after each apply, ready for the next pick. Example
status lines, which are the report channel: "Traced 214 elements across 2
systems; view EB System - SA-AHU-1 updated.", "Section box spans 12 levels —
expect a tall view.", "Trace truncated at 5000 elements — counts read 'at
least'.", "Picked element has no MEP connectors and belongs to no system —
nothing to trace."

### User operation flow

1. Ribbon: Misc Tools → Systems → System Isolate. Main window opens at
   Step 1; if a selection exists, "Use current selection" is live.
2. Click "Pick an element". The window closes, Revit's pick cursor runs; Esc
   returns to Step 1 with "Pick cancelled — nothing changed." (the cancel
   path; nothing was read or written).
3. On a valid pick the read pass runs; the window re-shows at Step 2 with the
   trace summary.
4. Adjust scope radio, equipment checkbox (summary re-computes live), and the
   isolation mode.
5. Press **Show in 3D**. One TransactionGroup commits (create/reuse view,
   section box, isolate), the active view switches to `EB System - SA-AHU-1`,
   and the window re-shows at Step 2 with the committed status line.
6. A skipped write looks like this, in the status line, never silent: "View
   template 'MEP 3D' controls V/G — filter not applied; section box set."
7. Pick again for the next run (the same window loop), or **Close**. One
   Ctrl+Z undoes the entire last apply.

## See also

- Existing EasyBIM: **Circuit Schedule** (Misc Tools — Circuiting) — the
  electrical answer to the same "where does it go" question, and the Show
  select+zoom precedent; **Isolate in All** (Sheet) — category isolation
  across views, the opposite scope, do not merge them; **Slope** (Misc Tools)
  — the write tool this read pass will sit beside.
- Plan siblings: **03 Slope Check** and **04 Open Ends** and **15 Connection
  Check** — the shared connector-graph walker (hoist rule above); **05 System
  Schedule** — the Systems pulldown's tree view, whose Show button this makes
  spatial; **20 Batch Insulation** and **36 Air Balance** — the other Systems
  pulldown residents.
