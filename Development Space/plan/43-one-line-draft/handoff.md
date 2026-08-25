# 43 — One Line Draft

Commits the electrical distribution skeleton — boards, feeders, ratings —
to a timestamped drafting view the drawing set can carry.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 43 of 45 | Misc Tools — Circuiting pulldown | yes | L | 7/10 | 9/10 |

## Main purpose

The single-line diagram is redrawn by hand, in a drafting view, from facts
the model already knows: which board feeds which, at what rating, over what
wire. It goes stale the day after it is drawn, and every coordination move
that re-feeds a panel quietly makes the paper wrong. Nobody expects Revit
to produce a stamped one-line — the protective device story, the
annotations, the taste of a good riser are engineering work — but the
tiered skeleton with the right names and numbers on it is pure
transcription, and transcription is what tools are for.

One Line Draft is that transcription, run in the Clash Detection Mode
spirit: forward-only. Each run creates a fresh, timestamped drafting view
and never edits a previous one — regeneration is re-run and
delete-the-old-yourself, and the tooltip, the confirmation, and the report
all say so, because the alternative (diffing detail lines against a
hand-annotated view) is a promise no tool should make. The hierarchy comes
free: `circuit_schedule_state.build_tree` already walks service equipment
into boards into circuits, and its `drawn` rule already handles the
dual-fed board — expanded once under its first feeder, pointed at from the
second. What is new is a tiered layout computed in pure state code, symbol
placement through the office's own detail component families, and feeder
lines annotated from the same `CIRCUIT_FIELDS` the Circuit Schedule pane
reads.

The rank reflects the honest split between impact and reach. When it
lands, it lands hard — hours of drafting per issue, and a skeleton that is
never out of date costs one click — hence the 9. But the output is a
draft, offices guard their one-line look fiercely, and the layout
algorithm is real L-effort work. The differentiation holds up everywhere:
Circuit Schedule renders this tree as a dockable pane for the modeler;
this commits it to paper for the set. Revit's own electrical single-line
(2025+) requires the analytical distribution workflow most production jobs
skip, and gating on it would abandon 2021–2024; drafting-view generation
works on every release EasyBIM supports. No EasyBIM tool draws into views
today — this is the first, which is exactly why its transaction shape must
be exemplary.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Circuiting.pulldown/One Line Draft.pushbutton/`
  added to the pulldown's `bundle.yaml` `layout:` list, with `script.py`
  (thin launcher; the flow is modal and pick-free, so no persistent
  engine and no ExternalEvent), `bundle.yaml` (two-line title
  "One Line\nDraft", narrative tooltip stating the forward-only contract,
  `author: Ruiming Liu`), 96×96 icons, `OneLineDraftWindow.xaml`,
  `OneLineConfirmWindow.xaml`, `OneLineReportWindow.xaml`,
  `one_line_state.py` (the layout engine — tiers, columns, label
  composition, caps; all coordinates plain numbers, zero Revit imports),
  `one_line_revit.py` (tree snapshot via lib, symbol inventory, the
  writer, read-back), `one_line_ui.py`. Reuse from lib:
  `circuit_schedule_revit.collect_circuits` and `CIRCUIT_FIELDS` (rating,
  poles, voltage, wire size all already collected — never a third copy),
  `circuit_schedule_state.build_tree` with its `drawn` dual-feed rule,
  and the family selection wizard pages
  (`family_selection_state`/`_ui`) for picking detail components. Symbol
  mappings persist by name in config the way Tag Align presets do —
  family and type names, never ElementIds, so the mapping survives into
  the next project that uses the same office content.
- **Revit API route** — the drafting view:
  `FilteredElementCollector` `OfClass(ViewFamilyType)` filtered to
  `ViewFamily.Drafting`, then `ViewDrafting.Create(doc, vft_id)`, named
  "One Line Draft — MSB-1 — 2026-08-25 14:02" (timestamp makes collision
  handling trivial), `view.Scale` set from the window. Symbols: detail
  component `FamilySymbol`s, `Activate()` before first use, placed with
  the drafting-view overload `doc.Create.NewFamilyInstance(XYZ, symbol,
  view)`. Feeders: `doc.Create.NewDetailCurve(view, Line.CreateBound(p1,
  p2))` in orthogonal segments. Labels: `TextNote.Create(doc, view.Id,
  XYZ, text, type_id)` with
  `doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType)` — one
  probe-guarded call path, no per-version branches kept to rot. All
  numbers for labels come from the lib collector's `AsValueString` text —
  display strings for display, never parsed. Transaction shape: one
  assimilated `TransactionGroup` "One Line Draft"; the view is created in
  the first nested transaction; each board (symbol + its feeder + its
  labels) commits in its own nested group so one throwing placement rolls
  back that board alone; its subtree is then not attempted and is
  reported as skipped with the reason. If every board fails or is
  skipped, the outer group rolls back entirely — no empty timestamped
  view left behind, and counters zeroed so the report cannot claim work
  that is gone. Opening the result rides
  `uidoc.RequestViewChange(view)` — asynchronous and safe from the report
  window, no Idling or ExternalEvent needed.
- **The plan/apply cycle** — `build_plan` snapshots circuits and equipment
  through lib, builds the tree, prunes to the chosen root and depth, then
  runs the layout: column per depth, siblings stacked and spaced in view
  units scaled off `view.Scale`, every op emitted as plain data —
  `place(family_key, x, y, label)`, `feeder(x1, y1, x2, y2, label_text)`,
  `marker(x, y, text)` for dual-feed references and truncations. The plan
  resolves each equipment family name against the symbol mapping;
  unresolved nodes become named skips *in the plan*, and if no node
  resolves at all the tool refuses to run — fail closed, never an
  invented placeholder rectangle. The confirmation window shows the plan
  as counts and named lists — boards, feeders, labels, every skip with
  its reason — behind the acknowledgement checkbox "This creates a new
  drafting view; it will not update when the model changes." Apply runs
  under a cancellable `forms.ProgressBar`; the report reads back from the
  committed model — a collector over the new view's id counts what
  actually landed — lists every skip and rollback, and offers Open View.
- **Edge cases & honest limits** — named-skip buckets: *"no symbol mapped
  for family 'X' (n boards)"*; *"board rolled back — placement threw"*
  (its subtree listed under it as "not attempted"); *"branch truncated at
  depth/node cap"* — and the truncation is also drawn, a marker in the
  view reading "+ 14 more boards — re-run deeper", because a drawing that
  silently ends is a lie on paper. Dual-fed boards draw once with a
  reference marker under the second feeder — the tree's own rule, stated
  in the report. Unassigned circuits (the tree's bucket) are out of scope
  for a one-line and say so. Branch circuits are off by default; on, they
  render as capped text stubs per board ("+ 9 more circuits"), never
  hundreds of symbols. The tool does not draw protective-device internals,
  does not annotate voltage drop or AIC, and does not update old views —
  three sentences that live in the tooltip verbatim.
- **Risks** — the layout algorithm is the real work: wide shallow trees
  need column balancing or the view is unusable, and "looks like a
  one-line" is a taste bar, not a correctness bar — budget iteration time
  on spacing constants, and keep every constant in `one_line_state` where
  desktop tests can pin geometry without Revit. Users will immediately
  ask for in-place update; the forward-only contract must be loud in
  tooltip, acknowledgement, and view name, or the tool earns resentment.
  Text note width versus view scale is fiddly — labels sized off
  `view.Scale` with a fixed-width layout slot, and the first in-Revit
  session must verify overlap behavior (a "Still to verify in Revit"
  item by design). Symbol families vary wildly per office, which is
  exactly why mapping is by the user's own families, keyed by the
  *source* equipment family name — no invented board taxonomy to guess
  wrong — and absence fails closed.
- **Tests** — `test_one_line_state.py` pins the layout: column
  assignment by depth, sibling spacing, dual-feed markers, depth/node
  caps with marker emission, label composition from field text, and the
  fail-closed empty-mapping path. `test_one_line_command_names.py` pins
  the grown pulldown layout, bundle metadata, XAML↔handler wiring for
  all three windows, icon sizes, and the IronPython AST scan.
  `test_one_line_revit.py` drives the adapter over fakes shaped like
  each API generation — inactive symbols, a throwing `NewFamilyInstance`
  rolling back one board's group, the empty-view full rollback,
  read-back counting only committed elements.

## UI description

**Main window** — resizable modal, root `Grid Margin="14"`, rows
Auto/*/Auto. Header: "One Line Draft" SemiBold ~30px over the DimGray
subtitle "Draws a new drafting view. It will not update — re-run instead."
Two cards. **Scope card**: root board ComboBox (service equipment
pre-selected; single choice, so a ComboBox), a depth spinner, view scale
ComboBox, and the "Include branch circuits" checkbox, off by default with
the DimGray line "Feeders only. Branch circuits render as capped text
stubs." **Symbols card**: one row per distinct equipment family in scope —
"Panelboard - Surface : 250A → EB Detail Panel : Std" — each row's "…"
button opening the family-selection page over detail components; unmapped
rows render red until mapped, with the count line "5 of 6 board families
mapped." Saved mappings load by name on open. Footer: status left,
**Generate** (`IsDefault`, disabled while zero rows are mapped, the
reason in its tooltip), **Close** (`IsCancel`). The window is pick-free,
so it stays open after its action — a re-run is two clicks.

> "23 boards in scope under MSB-1, depth 3. 1 family unmapped — its 2 boards will be skipped by name."

**Confirmation window** — small modal over the main window: the plan as
counts and named lists — "23 boards, 22 feeders, 46 labels. Skipped: 2
boards (no symbol mapped for 'Transformer - Dry Type')." — above the
acknowledgement checkbox "This creates a new drafting view; it will not
update when the model changes." **Draw** stays disabled until ticked;
**Back** returns without writing.

**Report window** — read-only table after the batch: placed / skipped /
rolled back, every count re-read from the committed view, skips named,
plus **Open View** (`RequestViewChange`) and **Close**.

> "View 'One Line Draft — MSB-1 — 2026-08-25 14:02' created: 21 boards, 20 feeders drawn. 2 skipped (no symbol mapped — listed). One undo step."

> "Nothing drawn — no symbol mapping resolved. The view was rolled back; the model is untouched."

### User operation flow

1. Ribbon: Misc Tools → Circuiting → One Line Draft. The Main window
   opens; saved symbol mappings load by name, unmapped families sit red.
2. Pick the root board and depth; map the red rows through the family
   pages (or leave one unmapped, accepting its named skip).
3. Press **Generate**. The Confirmation window shows counts and every
   planned skip; tick the forward-only acknowledgement, press **Draw**.
   **Back** or Esc here writes nothing.
4. The cancellable ProgressBar ticks per board. Cancelling mid-batch
   keeps committed boards, reports the rest as "skipped — cancelled",
   and still ends in one undo step.
5. The Report window reads back from the model. A skipped item looks
   like: "T-1, T-2 — no symbol mapped for 'Transformer - Dry Type'" or
   "DP-4 — placement threw; board rolled back, 3 boards under it not
   attempted."
6. **Open View** jumps to the new drafting view; annotate it, or put it
   on a sheet.
7. After the next re-feed, re-run: two clicks make a fresh timestamped
   view; delete the old one yourself once its markups are carried over.
8. Ctrl+Z once removes the entire view and everything in it.

## See also

- Existing: **Circuit Schedule** (the same tree, rendered live for the
  modeler; donor of `build_tree`, the dual-feed rule, and
  `collect_circuits`), **Clash Detection Mode** (the forward-only
  precedent this tool's contract copies), **Tag Align** (name-keyed
  preset persistence pattern for the symbol mappings).
- Siblings: **22 Voltage Drop** (a stamped %VD parameter is exactly what
  these feeder labels want to carry next), **16 Panel Sheets** (the other
  commit-to-the-drawing-set electrical tool), **01 Circuit Check** (run
  it first — a one-line transcribes whatever the circuits claim), **05
  System Schedule** (the tree engine's mechanical cousin).
