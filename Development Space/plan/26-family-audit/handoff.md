# 26 — Family Audit

The family-weight report Revit refuses to show: sizes, CAD stowaways, nesting
depth, and copy-name suspects — priced honestly as a quick pass and an opt-in
deep pass.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 26 of 45 | Family | no | M | 8/10 | 8/10 |

## Main purpose

A model bloats to 800 MB and nobody knows which of 400 loaded families is the
80 MB culprit with three CAD files buried inside it. Revit shows no family
size, no import content, no nesting depth anywhere in the UI. The usual
routine — save out every family by hand and sort a folder by size — takes an
afternoon, so it is done once per project at best, and always after the
damage.

Family Audit is that afternoon as a tool, split into two passes that are
priced honestly. The quick pass is one collector sweep into plain dicts —
category, type count, placed instances, in-place flag, shared-nested flag,
and a pure-state name-suspicion scan for the "Chair1 / Chair 2 / chair 2"
debris that batch copying leaves behind. It is instant and covers the whole
model. The deep pass is opt-in per ticked family: open it with `EditFamily`,
save it to a temp folder for the true file size, count the CAD imports
inside, walk its nested families behind a depth guard, close without saving.
Seconds per family, and the window says so before the user commits to it. A
cancelled deep pass keeps every row already scanned and marks the rest "not
scanned" — never a guess.

The tool never deletes — that is Families Purge's job — so its dry-run is the
whole tool: it is the evidence the purge, rename, and reload decisions read
from. That division is also the rank's justification: family library
governance is commercial territory (CTC's suites), native Revit exposes none
of it, pyRevit's built-ins count families but cannot see size or CAD content,
and the Dynamo graphs that exist die half-way and keep nothing. The
differentiators are the house patterns — named skips, a cancelled run that
keeps its partial truth, and reuse of the family selection wizard and Excel
kit instead of a third family browser.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Family.panel/Family Audit.pushbutton/` beside Family Types.
  `script.py` thin launcher with `__persistentengine__ = True` — the window
  is modeless so **Show** can zoom the model while the grid stays open.
  `family_audit_state.py` (name-suspicion heuristics, row shaping, flag
  derivation, deep-pass budgeting and partial-result bookkeeping, sort keys —
  zero Revit imports), `family_audit_revit.py` (the quick-pass collectors,
  the EditFamily/SaveAs/close deep probe, temp-folder hygiene, the
  select-and-zoom handler), `family_audit_ui.py` + XAML (one window, two
  body states). Compose from `lib/easybim`: the `family_selection_state/ui`
  wizard pages for the category-grouped picker (third consumer),
  `external_events.ExternalEventBridge` for every model touch after the
  window opens, `excel_workbook` for export, `compat` for ElementId values.
  The `GetTypeId()` instance histogram is the same machinery 10 Families
  Purge builds — whichever ships second hoists it to `lib/easybim` as a
  dict-returning pass; keep this copy shaped for that move.
- **Revit API route** — Quick pass, one trip:
  `FilteredElementCollector(doc).OfClass(Family)` for the inventory
  (name, `FamilyCategory`, `IsInPlace`, `GetFamilySymbolIds()` count);
  one `OfClass(FamilyInstance)` pass building a `GetTypeId()` histogram for
  placed counts, with instances whose `SuperComponent` is not null counted
  in a separate shared-nested column — user-placed and nested-placed must
  never blur, because the purge decision this report feeds reads that flag.
  Deep pass, per ticked family, entirely read-only on the project (no
  transaction, nothing to undo): `doc.EditFamily(family)` → `famdoc`;
  `famdoc.SaveAs(temp_path, SaveAsOptions)` with overwrite allowed, size via
  `os.path.getsize` — reported as "size when saved today", the honest proxy
  the column footnote explains; CAD content via
  `FilteredElementCollector(famdoc).OfClass(ImportInstance)` count; nesting
  via `OfClass(Family)` inside `famdoc`, recursing through `EditFamily` on
  the nested family documents behind a depth guard (default 5) and a total
  open-document budget per family (default 32) — a truncated walk is marked
  "depth capped", never presented as complete; `famdoc.Close(False)` in a
  `finally` for every document opened, temp files deleted afterwards, and a
  leftover-temp sweep at startup for the crash that skipped cleanup.
  `EditFamily` throws for in-place families and for a family already open in
  the editor — both are named skips, as is any per-family exception or time
  budget overrun (default a few seconds per family, configurable). The deep
  pass rides the ExternalEventBridge one family per event so cancel always
  lands between families and the UI never freezes; up-front checks probe the
  temp folder for write permission and free space before the first
  EditFamily. Version gating: none needed — every surface here predates
  2020; the command-names test pins that no `Transaction` is constructed in
  this bundle.
- **The scan/report cycle** — read-only, so the cycle is scan → enrich →
  report. The quick pass returns one snapshot of family rows (ints and
  unicode only); `family_audit_state` derives flags — in-place, name-suspect
  (trailing copy-digits, case-only clashes reported on **both** rows of the
  clash), zero placed instances — and the grid renders it immediately: the
  whole model, no waiting. Deep Audit enriches ticked rows in place as each
  family finishes, so partial results are the natural state, not a recovery
  mode. The report is the grid itself plus the Excel export; every number in
  it was read, not inferred — a row the deep pass did not reach says "not
  scanned" in the Size and CAD columns, and a skip says why. Re-running Deep
  Audit on the same rows re-reads; nothing is cached across sessions.
- **Edge cases & honest limits** — Named buckets: "skipped — in-place
  (EditFamily unavailable)", "skipped — open in the family editor",
  "skipped — EditFamily failed: {message}", "skipped — over the {n}s
  budget", "not scanned" (deep pass never asked or cancelled first),
  "depth capped at {n}". The size column is the family's compact save size
  today, not the bytes it adds to this model — stated in the column tooltip.
  The name-suspicion flags are labelled *heuristic* and never feed any
  automatic action; a project whose real convention ends in digits will see
  false positives, and the flag is information, not accusation. The tool
  states what it cannot do: it never edits, never deletes, never purges
  inside a family (Families Downgrade territory), and cannot see the
  in-place families' contents at all.
- **Risks** — `EditFamily` is genuinely slow and can throw on corrupt
  families; the per-family try/except, the time budget, and the
  close-in-finally are load-bearing, not decoration — a leaked family
  document costs memory for the rest of the session. Temp-folder SaveAs
  needs the disk-space and permission checks up front, unique file names,
  and the startup sweep. Shared-nested detection via `SuperComponent` must
  be exact or the in-place/nested flags mislead the purge decision this
  report feeds — that classification carries the heaviest state-test load.
  Deep-passing hundreds of families is minutes of wall time by nature; the
  honest defense is the per-family cost stated on the button, live row
  fill-in, and a cancel that keeps everything scanned so far.
- **Tests** —
  - `test_family_audit_state.py` pins the name heuristics against known-good
    and known-bad strings ("Chair1"/"Chair 2" flag, "M_Bolt 10mm" does not;
    case-only clash flags both rows), flag derivation from fixed dicts, the
    partial-result bookkeeping (cancel after k rows keeps k, marks rest
    "not scanned"), budget and depth-cap accounting, and sort keys.
  - `test_family_audit_command_names.py` pins bundle.yaml metadata,
    XAML↔handler wiring, 96×96 icon pairs, the IronPython AST scan, the
    persistent-engine flag, and the forbidden-API pin that no `Transaction`
    is ever constructed in this bundle.
  - `test_family_audit_revit.py` drives the adapter against fakes:
    `EditFamily` throwing for in-place, `SaveAs` failing on a full disk,
    `SuperComponent` present/absent shapes, a nested walk that exceeds the
    depth guard, and the assertion that every opened fake document has
    `Close(False)` called — exceptions included — and that only ints and
    unicode cross back.
  - `test_family_audit_xlsx.py` pins the export rows, header order, and the
    "not scanned" cell text surviving into Excel.

## UI description

**Main window (pick state)** — resizable modeless window, `ShowInTaskbar`
off, root `Grid Margin="14"`, rows Auto/*/Auto. Header: "Family Audit" over
the DimGray subtitle "What every loaded family weighs, carries, and hides."
Body: the family card built from the shared wizard pages — category-grouped
checkbox list, Search, Select All / Select None, "38 selected, 362
unchecked.", star row with MinHeight. Footer status left: "400 loadable
families, 6 in-place (listed)." Buttons right: **Quick Audit** (primary),
**Deep Audit** (disabled until rows are ticked; tooltip: "opens each ticked
family behind this window — seconds per family"), **Cancel**.

**Main window (results state)** — the body swaps to a read-only sortable
grid: Family / Category / Types / Instances / Nested inst. / Size / CAD
Imports / Nested (depth) / Flags. Quick-pass columns fill instantly; deep
columns show "—" until scanned, then fill row by row while the footer reads
"Opening 12 of 38: Chair-Executive…" with a **Cancel Deep** button beside it.
Flags render as short chips ("in-place", "name-suspect", "not scanned",
"skipped — over budget") with the full reason in the tooltip. Footer status
after the run: "38 audited, 3 skipped (listed), 362 not scanned." Buttons:
**Show** (select + zoom the family's instances via ExternalEvent, disabled
for zero-instance rows with the reason in a tooltip), **Deep Audit** (re-runs
on the current tick set), **Export to Excel**, **Back** (returns to the pick
state, selection preserved), **Close**.

### User operation flow

1. Ribbon: Family → Family Audit. The quick pass runs on open (footer:
   "Scanning families…"), and the pick state appears with counts already
   live.
2. Press **Quick Audit** to jump straight to the results grid for the whole
   model — sizes and CAD columns read "—", flags and instance counts are
   complete. Sort by Instances or Flags to find the debris.
3. Tick the suspects (or everything in a category) and press **Deep Audit**.
   Rows fill in as each family is opened, measured, and closed; the model is
   never written.
4. Cancel path: **Cancel Deep** stops after the family in flight; every
   scanned row keeps its numbers, the rest read "not scanned", and the
   footer says "cancelled after 12 of 38". Closing the window mid-run does
   the same and still deletes its temp files.
5. A skipped item looks like: "skipped — in-place (EditFamily unavailable)"
   as a Flags chip, with Size and CAD blank.
6. **Show** zooms the placed instances of the selected row to confirm the
   suspect is what you think it is; **Export to Excel** writes the grid for
   the model-health meeting; **Close** ends it — there is nothing to undo.

## See also

- Existing EasyBIM: **Family Types** (the type-table neighbour), **Families
  Downgrade** (the tool that acts on what this report finds inside a
  family), **Families Transfer** — and the shared `family_selection_*`
  wizard pages and `excel_workbook` kit this composes.
- Plan siblings: **10 Families Purge** — audit finds the heavy families,
  purge removes the dead ones; a natural pair, and the shared `GetTypeId()`
  histogram is the hoist moment. **25 Families Reload** — the fix for a
  family the audit condemns is often a clean library copy. **38 Family
  Rename** — the name-suspect flag is its work list. **17 Where Used** — the
  deep answer when "0 instances" is not proof enough.
