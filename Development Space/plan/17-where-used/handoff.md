# 17 — Where Used

The reverse lookup Revit never built: pick a family, type, or parameter and
see everything that would break — before the rename, the unload, the delete.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 17 of 45 | Family | no | L | 8/10 | 8/10 |

## Main purpose

Before renaming a type, unloading a family, or unbinding a project parameter,
the question is always "what breaks?" — and Revit has no forward answer. The
only reverse lookup it offers is the warning dialog *after* you delete, so
people delete the thing, read the dialog, and Ctrl+Z; or they never delete
anything and the model grows. "Select All Instances" answers a tenth of the
question: it says nothing about the legend, the schedule filter, the view
filter rule, or the tag family quietly leaning on the thing.

Where Used answers it read-only, up front. Pick a subject — a family, a type,
or a project/shared parameter — and one read pass builds a dependency tree in
plain dicts: placed instances by level, legend components, view filters whose
rules test it, schedules whose fields or filters carry it, tags on its
instances, nested placement inside host families, parameter bindings — and,
as the honesty backstop, `Element.GetDependentElements` rendered as its own
branch labelled "other dependents — Revit's own list, unclassified". Every
classified row also carries a **by id / by name** tag, because the blast
radius differs by verb: a rename breaks only the by-name rows (filter string
rules, schedule filter values, formulas), while a delete breaks everything.
No other free tool makes that distinction visible.

It earns rank 17 as the fullest composition of the existing kit — the
`circuit_schedule_state` tree engine, the family selection wizard pages,
`ExternalEventBridge` show-and-zoom, and the bounded-deep-pass discipline —
pointed at ground the prior-art survey calls a wedge: dependency analysis
before delete is Ideate StyleManager territory, with no credible free
equivalent. It is L-effort because completeness is a set of probe families
that each drift across API generations, and it sits below the top ten only
because 10 Families Purge already answers the *binary* form of the question
(zero use / some use / unknown) for the purge case; this tool is the
interactive, per-reference answer for everything else.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Family.panel/Where Used.pushbutton/` beside Family Types.
  `script.py` is a thin launcher with `__persistentengine__ = True` (the
  window is modeless, bridge-owning), `bundle.yaml` (two-line title
  "Where\nUsed", narrative tooltip naming what it classifies and what it
  shunts to the unclassified branch, `author: Ruiming Liu`), 96×96 icon
  pair. Split: `where_used_state.py` (classification merge, by-id/by-name
  tagging, branch caps, tree building on the generic engine — pure Python),
  `where_used_revit.py` (the probe passes), `where_used_ui.py` +
  `WhereUsedWindow.xaml`, `where_used_xlsx.py` (flat-row export through
  `lib/easybim/excel_workbook`). The tree composes the generic half of
  `lib/easybim/circuit_schedule_state` (`Node`, token search, `apply_filter`,
  `breadcrumb`, expansion state, `MAX_DEPTH`); subject picking composes the
  `family_selection_*` wizard pages for families/types and a parameter list
  card for bindings. The usage-probe layer (type histogram, legend, nested,
  filter probes) is the piece shared with 10 Families Purge — both keep it
  dict-in/dict-out, and whichever builds second hoists it to
  `lib/easybim/usage_probes.py` per the second-consumer rule. The planned
  dockable-pane future moves all three modules to `lib/` (anything a pane
  imports must live there); v1 stays in the pushbutton and says so in a
  module docstring.
- **Revit API route** — For a type subject: one
  `WhereElementIsNotElementType()` pass building a `GetTypeId()` histogram
  (instances, by id); legend components via `OST_LegendComponents` reading
  `BuiltInParameter.LEGEND_COMPONENT` `AsElementId()` (by id,
  capability-probed — resolution differs across generations); view filters
  via `ParameterFilterElement`, walking `GetElementFilter()` on newer APIs
  and `GetRules()` on older, matching string rules on Family Name / Type
  Name / Family and Type with each rule's own evaluator (by name); schedule
  filters via `ViewSchedule.Definition.GetFilters()` whose value is the
  type's id or its name string (tagged accordingly); tags on its instances
  via `IndependentTag.GetTaggedLocalElementIds()` with the older
  `TaggedLocalElementId` fallback behind `hasattr`; nested placement via
  `FamilyInstance.SuperComponent`, reported as "nested in {host family}".
  For a parameter subject: bindings from the `doc.ParameterBindings`
  iterator (categories, instance/type); schedules by field `ParameterId`
  and filter `FieldId`; filter rules by the rule's own parameter id; loaded
  families carrying the shared GUID, read off their symbols' and a sampled
  instance's parameters (`IsShared`, `GUID`). The catch-all branch calls
  `GetDependentElements(None)` on the subject, subtracts every id already
  classified, and shows the rest — behind a `hasattr` probe, since the
  method is 2018.1+; on older Revit that branch greys with the reason.
  Formula references inside families are visible only through
  `Document.EditFamily`, so they are an off-by-default deep pass: bounded
  budget, cancellable `forms.ProgressBar`, one family open at a time, the
  same discipline 26 Family Audit specifies — whichever builds second
  hoists the bounded family-opener. Everything crosses back as ints and
  unicode; Show and Refresh ride `ExternalEventBridge`. No Transaction
  anywhere — a forbidden-API pin the command-names test enforces.
- **The scan/report cycle** — Read-only, so scan → tree → probe again. The
  scan returns one snapshot dict per dependency kind plus a `not_available`
  list naming every probe the running Revit could not perform;
  `where_used_state.build_tree` merges them under the subject root, one
  branch per kind with counts on headers, every row tagged by id / by name,
  every branch display-capped with a visible "…and 240 more — search to
  narrow" tail row. The window *is* the report; Refresh re-scans the live
  model and preserves expansion state, so the loop is: look, fix the model,
  Refresh, watch the branch shrink. Export writes the visible tree flat to
  .xlsx for the record before a risky cleanup session.
- **Edge cases & honest limits** — The tool never says "safe to delete";
  its top rule is that a clean bill of health it cannot prove is the bug.
  What it cannot classify lands in the unclassified branch, and what it
  cannot probe at all greys its branch header with the reason ("Tags — not
  readable in this Revit") instead of vanishing. Named limits, stated in
  tooltip and subtitle: host document only (references *from* linked models
  are invisible by design of the platform); formula references only via the
  deep pass, off by default; keynote assignments, view-type defaults, and
  Family Type parameters in other families are exactly the residue the
  unclassified branch exists for; built-in parameters are not offered as
  subjects (they cannot be unbound anyway); global parameters out of scope
  v1. A subject with zero rows everywhere still shows the unclassified
  branch result before the footer will say "no dependents found in 7 of 7
  kinds checked".
- **Risks** — Completeness is the trap the brainstorm named: the tool must
  enumerate exactly which reference kinds it classifies and shunt the rest
  into the unclassified branch, or users will read absence as proof. Tag,
  legend, and schedule accessors change shape across versions — one
  hand-rolled fake per API generation, per house convention, and every
  probe individually guarded so one broken kind degrades one branch. The
  histogram pass is linear by design; anything per-subject-times-per-element
  is the performance trap on 5,000-family models. The deep pass inherits
  every `EditFamily` cost (slow, memory-hungry, dialog-prone) — budget,
  cancel, and never on by default. By-name matching must follow house
  search rules or a type named "12" will claim rows belonging to "112".
- **Tests** —
  - `test_where_used_state.py` pins classification merging from fixed dicts:
    branch construction per subject kind, by-id/by-name tagging, the
    unclassified subtraction, branch caps with the visible tail row,
    greyed-not-hidden unavailable kinds, token search, expansion round-trip.
  - `test_where_used_command_names.py` pins bundle metadata, XAML↔handler
    wiring, 96×96 icon pairs, `__persistentengine__`, the IronPython AST
    scan, and the no-Transaction forbidden-API pin.
  - `test_where_used_revit.py` drives the adapter against fakes per API
    generation: legend parameter shapes old and new, `GetElementFilter` vs
    `GetRules`, tag accessor old and new, `GetDependentElements` absent,
    a probe that throws degrading only its own branch, binding iteration.
  - `test_where_used_xlsx.py` pins the flat export rows and header order.

## UI description

**Main window** — modeless, shaped like Circuit Schedule (a dockable pane
later, with the modeless right-edge fallback the kit already has). Header
"Where Used" over a DimGray subtitle: "What the model does with this — by id
and by name. Host document only; formulas need the deep pass." Top row: the
subject picker — a **Pick family/type…** button opening the family selection
wizard pages, a **Pick parameter…** button opening a card listing project and
shared parameters with search, and the current subject rendered as a bold
line ("Type: Single-Flush : 36″ × 84″"). Below it a small "Search" label
with the live-filter TextBox and a "Deep pass (open families)" checkbox, off
by default with the cost named in its tooltip. Body card: the dependency
tree — subject at root, branch headers with counts ("Instances (212)",
"View filters (3)", "Other dependents — unclassified (9)"), rows with second
lines like "Schedule: Door Schedule — filter value, by name" and
"Filter: FR Doors — rule on Type Name, by name", a by-id/by-name tag on
every classified row, greyed branch headers for kinds this Revit cannot
read, and a capped branch ending in "…and 240 more — search to narrow". A
breadcrumb path line sits above the tree; **Show** on every row selects and
zooms through the bridge; **Refresh** re-reads with expansion preserved.
Footer: status left, then **Export to Excel** and **Close** (`IsCancel`).

> "6 dependency kinds checked, 1 not available in Revit 2021 — 312 rows (2 branches capped)."

> "Renaming? 14 by-name rows are the blast radius. Deleting? All 226 rows are."

> "Deep pass: 61 of 240 families opened, 3 formula references found — Cancel keeps what was read."

### User operation flow

1. Ribbon: Family → Where Used. The Main window opens empty with the two
   picker buttons enabled and the footer reading "Pick a subject to scan."
2. Pick a subject — the wizard pages for a family or type, the parameter
   card for a binding. The scan runs through the bridge; branch counts fill.
3. Read the by-name total in the footer before a rename; expand branches
   and search ("Door" narrows by substring, "112" matches by token) — the
   filter flips visibility, so selection and expansion survive.
4. Press **Show** on any row: Revit selects and zooms. Fix or migrate the
   dependent (repoint the filter, edit the schedule), press **Refresh**,
   watch the branch shrink.
5. Optionally tick **Deep pass** and Refresh: a cancellable progress bar
   opens families one at a time; cancelling keeps every formula reference
   already found and the footer says the pass was partial.
6. A "skipped" item here is a greyed branch: "Tags — not readable in this
   Revit", or the capped tail row — shown with its reason, never hidden.
7. **Export to Excel** writes the visible tree flat for the record; then do
   the rename/unload/delete in Revit with the tree still open, Refresh, and
   confirm the dependents went where you meant.
8. Cancel path: **Close** (or Esc) at any time — there is no write path, so
   closing the window is the whole exit and nothing was changed.

## See also

- Existing: **Family Types**, **Families Transfer**, **Families Downgrade**
  (the family selection wizard pages this composes), **Circuit Schedule**
  (the generic tree/search engine and the show/zoom bridge path).
- Plan siblings: **10 Families Purge** — the binary form of this question
  (zero use / some use / unknown); the shared usage-probe layer hoists to
  `lib/easybim/usage_probes.py` when the second of the two builds.
  **26 Family Audit** — the other bounded `EditFamily` deep pass; share the
  family-opener. **38 Family Rename** — the tool whose by-name blast radius
  this window measures before the batch runs. **42 Parameter Audit** — the
  parameter-side sibling that repairs what this tool only locates.
  **32 View Sweep** — the same reference-aware "kept because" instinct
  applied to views.
