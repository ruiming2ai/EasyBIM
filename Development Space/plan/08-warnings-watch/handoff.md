# 08 — Warnings Watch

A docked warnings tree that remembers — counts, creators, and the week-over-week trend native Review Warnings forgets on close.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 08 of 45 | Health (new panel) | no | M | 9/10 | 7/10 |

## Main purpose

Native Review Warnings is a flat list that forgets everything the moment you
close it: no counts over time, no idea whether the 400 "identical instances"
warnings are old news or arrived this week, no way to see who keeps creating
them, and no export for the model-health section of the BIM report. So
warnings get triaged once a quarter in a panic instead of watched weekly.

Warnings Watch is a dockable pane in the Circuit Schedule mould: warnings
grouped by description, then workset, then elements, searchable, bounded,
with Show buttons that select and zoom. The headline — per the prior-art
survey — is not the browsing, which several free tools do one-shot, but the
part nobody free does: persistence. Each refresh writes a dated snapshot of
group counts keyed to the central model, and the next run shows the delta
per group — "Identical instances — 412 (+38 since Aug 18)". That per-model
history across sessions is what turns a warning list into a health watch,
and it is what the weekly BIM report actually needs, exported to Excel in
one click.

The tool is read-only by design: zero transactions, with one loudly-named
exception — an optional "Isolate in 3D" action that creates exactly one view
in one transaction and says so before it does. In workshared models each
element row carries creator and last-changed-by attribution, so the recurring
source of a warning class stops being a mystery. Coordination Review in the
inventory is a passive file-open message, not a warnings browser; nothing
existing overlaps. It founds the new Health panel.

## Basic implementation ideas

- **Bundle & module layout** — New panel:
  `EasyBIM.tab/Health.panel/Warnings Watch.pushbutton/` — thin `script.py`
  that shows the registered pane or the fallback window, `bundle.yaml`
  (two-line title, narrative tooltip, `author: Ruiming Liu`), 96×96 icons.
  A dockable pane's imports must live in `lib/`, so the modules are
  `lib/easybim/warnings_panel.py` (registration + right-edge fallback,
  modeled on `circuit_schedule_panel`), `warnings_revit.py` (the scan),
  `warnings_state.py` (grouping + tree via the generic engine), and
  `warnings_history.py` (pure-Python snapshot store — file IO only, fully
  desktop-testable). `startup.py` registers the pane unconditionally
  (init-only API), DockPosition Right. The state module consumes
  `circuit_schedule_state`'s generic tree/search machinery (Node, tokens,
  filter, expansion state, breadcrumb, `MAX_DEPTH`) — alongside 05 System
  Schedule this is another consumer proving the lib placement; whichever
  builds second inherits the already-proven split. The select-and-zoom
  helper (`show_elements`) gains a second consumer here and hoists to a
  shared lib home. Excel export composes lib `excel_workbook`.
- **Revit API route** — `doc.GetWarnings()` returns the live
  `FailureMessage` list; per message read `GetDescriptionText()`,
  `GetSeverity()`, `GetFailingElements()` and `GetAdditionalElements()`,
  flattened to plain dicts. Element ids resolve — lazily, on branch expand
  — to category name, workset name via `WorksetId` + the workset table,
  and creator / last-changed-by via
  `WorksharingUtils.GetWorksharingTooltipInfo(doc, id)`, workshared
  documents only; every one of those lookups is guarded because an id can
  be resolved-to-deleted between refreshes. The history key prefers the
  central model: `doc.GetWorksharingCentralModelPath()` through
  `ModelPathUtils.ConvertModelPathToUserVisiblePath`, falling back to
  `PathName`, then `Title` (extending the `document_key` precedent) — and
  whichever key is in use is named in the pane, so Save As or detach never
  silently compares the wrong history. Zero transactions on the main path;
  "Isolate in 3D" creates one `View3D` and isolates the group's elements in
  a single named transaction, executed like Show through the existing
  `ExternalEventBridge`.
- **The plan/apply cycle** — Read-only, so scan/report: Refresh runs the
  scan through the bridge, `warnings_state` groups by description and
  builds the tree, and `warnings_history` appends a dated snapshot
  ({date, group → count}) atomically (write-temp-then-replace) under
  `%APPDATA%\EasyBIM\Warnings Watch\`, keyed as above and pruned to a
  bounded count of stored snapshots. The pane *is* the report: count and
  delta badges per group, "new" badges for groups absent from the previous
  snapshot, the footer naming what was compared against. Excel export
  writes two sheets via `excel_workbook` — the current census with
  per-element attribution, and the trend table (dates × groups) the BIM
  report pastes straight in. The isolate action is the only write, fronted
  by a TaskDialog with command links that names its one transaction before
  creating the view.
- **Edge cases & honest limits** — Deleted elements are a named row state
  ("element gone — refresh"), never a crash. Non-workshared documents hide
  the creator and workset columns entirely rather than showing empty chrome.
  Branches respect the house display cap: a 10,000-warning model truncates
  a branch with an "… N more — capped" row instead of hanging Revit, and
  tooltip-info resolution runs lazily per expanded branch, never up front.
  A corrupt or unreadable history file starts a fresh history and says so
  in the status line — it never guesses or crashes. Two sessions writing
  the same model's history on the same day are last-writer-wins; the tool
  states this rather than pretending to merge. Detached models fall back to
  the title key and the pane names the fallback. The tool cannot fix
  warnings and does not pretend to — it watches, attributes, and exports.
- **Risks** — `GetWarnings` plus per-element resolution is the performance
  trap on unhealthy models; the cap plus lazy resolution is load-bearing,
  not decorative, and needs a pinned test. `FailureMessage` accessor shapes
  and `GetWarnings` availability want fakes per API generation and a
  capability probe with a visible "warnings API unavailable" empty state
  rather than a crash on old versions (`min_revit_version` in bundle.yaml
  as the backstop). Snapshot keying is the subtle one — Save As, detach,
  and file moves must degrade to an honestly-named key, because comparing
  against the wrong history is worse than having none. Description text is
  the group identity and is localized: same-language teams are fine, but a
  model opened under another Revit language forks the history — name the
  limit in the README.
- **Tests** — `test_warnings_state.py`: grouping, delta math against a
  prior snapshot, "new group" detection, branch capping, tree search, and
  `warnings_history` pruning, atomic-replace behavior, and key-fallback
  naming on dict/tempdir fixtures. `test_warnings_command_names.py`:
  Health-panel bundle metadata, `startup.py` registration wiring, icon
  sizes, XAML↔handler wiring, IronPython AST scan, forbidden-API pins
  (no transactions imported outside the isolate path).
  `test_warnings_revit.py`: adapter against fakes per API generation —
  deleted-id guards, non-workshared hiding, lazy tooltip-info resolution,
  central-path/PathName/Title key ladder.

## UI description

**Warnings pane** (dockable, right edge). Search box on top; below it the
tree of warning groups, each row "description — count (delta)" with a
severity glyph and a "new" badge where the group has no prior snapshot:
"Identical instances — 412 (+38)". Expanding a group shows worksets, then
element rows — category, id, creator, last changed by — each with a Show
button that selects and zooms via ExternalEvent. Expanders preserve state
across rebuilds; capped branches end in an "… 180 more — capped" row.
Toolbar above the tree: Refresh, Export to Excel, Isolate in 3D (enabled
only with a group selected; disabled buttons keep their reason in a
tooltip). No write buttons anywhere else. Footer status line:

> "412 warnings in 9 groups. Snapshot saved — compared against Aug 18."

> "History keyed by title — detached model. Trend restarts if the file is renamed."

**Fallback window** — when dockable registration was missed, the same
content opens as a modeless window pinned to the right edge:

> "Dockable registration missed — floating window pinned right. Restart Revit to dock."

**Isolate confirmation dialog** — native-mimicry TaskDialog with command
links: "Create one 3D view isolating these 412 elements (one transaction,
one undo step)" / "Cancel". Nothing happens without the click.

**Excel export** — a save-path prompt, then the status line reports the
written file:

> "Exported 412 warnings and 6 snapshots to Model-Health.xlsx."

### User operation flow

1. Click **Warnings Watch** on the Health panel. The pane opens (or the
   fallback window pins right) and the first scan runs; the footer names
   the history key and what this run was compared against.
2. Read the group census: counts, deltas, "new" badges. Sort by delta to
   see what arrived this week rather than what is merely old.
3. Search to a group; expand it — worksets resolve, then element rows with
   creator attribution fill in lazily. A giant group truncates at the cap
   and says so.
4. Press Show on an element row to select and zoom; fix it in the model.
   A row whose element has since been deleted reads "element gone —
   refresh" instead of erroring.
5. Optionally select a group and press **Isolate in 3D**; the confirmation
   dialog names its one transaction; accepting creates the isolation view
   (one undo step). Declining is a skip, not a failure.
6. Press **Export to Excel** before the BIM meeting; paste the trend table
   into the health report.
7. Press Refresh after a fixing session — the new snapshot is saved and the
   deltas go negative, which is the whole payoff.
8. Cancel path: there is nothing to cancel on the main path because there
   is nothing written; closing the pane ends the session with the history
   file already saved.

## See also

- Existing: **Circuit Schedule** — the dockable-tree pattern, generic
  tree/search engine, and select-and-zoom donor; **Coordination Review**
  (Message panel) — passive file-open health message this pane supersedes
  for warnings; **Excel** (Misc Tools) — the `excel_workbook` export kit.
- Rank 05 **System Schedule** — the sibling consumer of the generic tree
  engine; build order decides who does the hoist proof.
- Rank 28 **Link Health** — the natural second resident of the Health
  panel; same watch-and-report posture applied to links.
- Rank 14 **Clash Sweep** — the other persist-across-sessions tool; its
  status history and this tool's snapshot store should share the
  %APPDATA% keying discipline (central path first, named fallbacks).
