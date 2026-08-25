# 38 — Family Rename

Convention-driven batch renaming of families and types — staged red,
collision-checked before Apply, honest about the view filters a rename
breaks.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 38 of 45 | Family | no | S | 8/10 | 7/10 |

## Main purpose

Every inherited model carries "Family1", "Copy of Desk", mixed-case
near-duplicates, and names that flunk the office convention. Renaming them
one at a time through the Project Browser is slow enough that nobody does
it — and browser sorting, schedules, and view filters pay for it for the
rest of the job.

Family Rename collects every loadable family and type into one staged
grid: Current name / New name, edits red until Apply. A rules bar drives
the New column from pure state code — find/replace, add or strip
prefix/suffix, case normalize, collapse double spaces, strip copy
artifacts — and rules only stage; nothing writes until the grid has been
read. The dry run is the safety story: before Apply enables, it detects
two rows converging on one name, a type name already taken within its
family, a family name already used in its category, illegal characters,
and empty results — each a named skip while the clean rows stay
applicable. It also warns about the breakage nobody else checks: a
`ParameterFilterElement` whose rule string equals a name being changed,
because view filters matching on Family Name silently stop matching after
a rename. The Excel round trip rides `excel_workbook`: names out with
ElementId as a visible on-sheet key, edits back in as red staged cells,
matched by id first so a rename in Revit does not break an older
workbook.

It earns rank 38 as the S-effort tool with the widest audience on the
back half of the list — every inherited model, every office with a naming
standard. The lane inside EasyBIM is clear: Family Types renames types
inside one family at a time; this renames families and types across the
whole document. Outside, EF-Tools Naming and DiRoots FamilyReviser
already do batch find-replace — which is exactly why find-replace is not
the pitch. The differentiators are the convention-rules engine, the
collision pre-check that turns refusals into named skips before the run,
the staged red grid, and the filter-breakage warning: the difference
between a rename tool and a rename tool you trust on Thursday night.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Family.panel/Family Rename.pushbutton/` beside Family
  Types. `script.py` thin launcher; `bundle.yaml` two-line title
  "Family\nRename", narrative tooltip naming the nested-family limit,
  `author: Ruiming Liu`. Split: `family_rename_state.py` (every rule as
  a pure function, collision detection, illegal-character and empty
  checks, staging model, Excel row matching, report bucketing),
  `family_rename_revit.py` (one collector pass into dicts, the filter
  rule reader, the rename executor, post-commit re-read),
  `family_rename_ui.py` + `FamilyRenameWindow.xaml` +
  `FamilyRenameReport.xaml`, `family_rename_xlsx.py` (export writer,
  `xlsxwriter` like the other `_xlsx` modules; import reads through
  `lib/easybim/excel_workbook.read_workbook_sheets`). The
  filter-rule decomposition reader is shared machinery with 39 Filter
  Manager — whichever builds second hoists it to
  `lib/easybim/filter_rules.py`; until then it is a standalone
  dict-in/dict-out function here.
- **Revit API route** — Inventory:
  `FilteredElementCollector(doc).OfClass(Family)` and
  `.OfClass(FamilySymbol)` into rows of kind (family/type), category,
  current name, owning-family name for types, and ElementId value via
  `compat.eid_to_int`. In-place families rename like any other and are
  included; system family types (wall, duct, text types) are not
  families and are out of scope — named in the tooltip, with 45 Text
  Types owning the text lane. Filter check:
  `OfClass(ParameterFilterElement)`, each decomposed via
  `GetElementFilter()` → `ElementLogicalAndFilter/OrFilter.GetFilters()`
  → `ElementParameterFilter.GetRules()` → `FilterStringRule` values,
  capability-probed per API generation; a rule whose string equals a
  current name being renamed, on a family-name-shaped parameter
  (`ALL_MODEL_FAMILY_NAME`, `ELEM_FAMILY_PARAM`,
  `ALL_MODEL_TYPE_NAME`), raises the warning; a filter that does not
  decompose lands in "filters not checked" rather than a clean bill.
  Write: one assimilated `TransactionGroup` "Rename families", one
  nested `Transaction` per row, `element.Name = new_name`; a Revit
  refusal rolls that row back alone and is reported Failed with Revit's
  message verbatim; in workshared documents the failure row carries the
  owner from `WorksharingUtils.GetCheckoutStatus`. The report re-reads
  final names from the committed model. No ExternalEvent, no Idling —
  modal window, one command.
- **The plan/apply cycle** — the staged grid *is* the plan: `build_plan`
  runs after every rule edit and every Excel import, computing per row
  the staged name, its collision status (case-insensitive convergence,
  type-name taken within its family, family-name taken within its
  category, illegal characters from Revit's forbidden set, empty
  result), and the intersecting filter names. Apply commits directly
  when no filters intersect — renames are one undo step, nothing here
  is irreversible, so no acknowledgement gate for the plain case. When
  staged renames intersect filter rules, a native-mimicry TaskDialog
  interposes with the affected filter names and a verification checkbox
  ("These filters will stop matching the renamed families.") before the
  commit proceeds. The Report window reads final names back from the
  model and repeats the filter list as follow-up work, since the tool
  does not rewrite filter rules (that is 39 Filter Manager's editing
  surface, not this one's).
- **Edge cases & honest limits** — Named buckets: "collision — two rows
  converge on '{name}'", "collision — type name taken in this family",
  "collision — family name taken in this category", "illegal characters",
  "empty name", "unchanged" (rule produced the same name — not staged,
  not an error), "owned by {user}", "failed — {Revit's message}", and on
  import "workbook row not in model — ignored (named)". Honest limits,
  stated plainly in the report: renaming a family does not touch nested
  copies inside other families — their names live in the host family's
  document, and a later reload of the host can resurrect the old name
  as a duplicate; the filter warning covers `ParameterFilterElement`
  rules only — schedule filters and key-schedule matches are not
  checked, and the window says so; where a filter's rules cannot be
  read it is "filters not checked", never silently clean. The tool
  refuses to guess at convention: rules stage, humans read the red
  column, and an unreviewed Apply is impossible because Apply is
  disabled while collisions are unresolved only in the colliding rows —
  clean rows never wait for dirty ones.
- **Risks** — The copy-artifact rule is the sharpest edge: it strips
  `^Copy of ` and ` Copy \d+$`, and bare trailing ` \d+$` only behind
  its own sub-checkbox, because "Duct Tap 45" matches and legitimate
  size digits will stage red — staged review is the safety, and the
  state tests pin the exact regexes and the non-matches ("MC Cable
  2x12" untouched). Case normalize must preserve fully-uppercase
  tokens ("VAV", "AHU") or Title Case vandalizes MEP names — pinned.
  Name-uniqueness scope in Revit is not perfectly documented across
  categories and versions, so the pre-check is deliberately stricter
  (case-insensitive, per category) and Revit's own per-row refusal is
  the final word — per-row rollback exists precisely so a wrong guess
  costs one row. Filter-rule reading across API generations is probed;
  38 ships before 39, so the reader stays local until Filter Manager
  hoists it. Renames change match identity for 25 Families Reload —
  run renames before a library sync, and the See-also on both sides
  says so.
- **Tests** — `test_family_rename_state.py` pins every rule as a pure
  function (find/replace, prefix/suffix, case with acronym
  preservation, space collapse, the copy-artifact regexes and their
  non-matches), collision detection cases (convergence, per-family type
  clash, per-category family clash, case-insensitive matching), the
  forbidden-character set, empty-name and unchanged classification,
  Excel row matching by id with name fallback, and report bucketing
  with counters zeroed on rollback.
  `test_family_rename_command_names.py` pins bundle metadata,
  XAML↔handler wiring for both windows, 96×96 icon pairs, the
  IronPython AST scan, and forbidden-API pins.
  `test_family_rename_revit.py` drives the adapter against fakes:
  the two collectors into plain dicts, filter decomposition present /
  partial / throwing (the throwing case landing in "filters not
  checked"), a rename refusal rolling back its row alone, checkout
  status on the failure row, and the post-commit name re-read — plus
  the assertion that nothing but ints and unicode crosses back.
  `test_family_rename_xlsx.py` pins the export columns (Kind /
  Category / Current / New / ElementId as visible key) and the import
  staging including the stale-workbook id-first match.

## UI description

**Main window** — resizable modal, root `Grid Margin="14"`, rows
Auto/*/Auto. Header: "Family Rename" over the DimGray subtitle "Renames
families and types across the document. Rules stage; nothing writes
until Apply." One card: Search (name by substring), Select All / Select
None, count line "412 names — 17 staged, 2 collisions.", then the grid
grouped in expanders by category — columns Kind / Current name / New
name / Note, staged New cells red, collision rows greyed in place with
the reason in the Note column and tooltip. Under the grid the **rules
bar**: Find / Replace boxes, Prefix / Suffix boxes with Add/Strip
toggles, Case ComboBox (unchanged / Title / UPPER / lower), "Collapse
double spaces" and "Strip copy artifacts" checkboxes (the latter with
its "also bare trailing digits" sub-checkbox), each edit re-staging the
New column live. Footer status left; buttons right: **Export to
Excel**, **Import from Excel…**, **Apply** (`IsDefault`, disabled with
a tooltip until at least one clean row is staged), **Cancel**
(`IsCancel`, asks before dropping staged work).

> "412 names loaded — 17 staged, 2 collisions skipped (see Note), 393
> unchanged."

> "Import staged 24 cells from RenameList.xlsx — 3 workbook rows not in
> model (ignored, named)."

**Filter warning dialog** — native-mimicry TaskDialog, shown only when
staged renames intersect filter rules: "2 view filters match names
being renamed" with the filter names in the message, command links
"Apply 17 renames (one undo step)" / "Back", and the verification
checkbox "These filters will stop matching the renamed families." The
apply link stays inert until ticked.

**Report window** — read-only WPF table: Renamed / Skipped (named) /
Failed (named, Revit's message verbatim), final names re-read from the
model; the affected-filters list repeated on top as follow-up work when
present, and the nested-family limit stated beneath it. Buttons:
**Export to Excel**, **Close**.

> "15 renamed, 2 skipped (collision), 0 failed — names re-read from the
> model. One undo step. 2 filters now need new rule strings."

### User operation flow

1. Ribbon: Family → Family Rename. The one-pass collector fills the
   grid; nothing is staged.
2. Search "Copy of", set rules — the New column stages red as each rule
   lands; collisions grey immediately with reasons. Hand-edit any New
   cell directly; hand edits stage red the same way.
3. Optionally **Export to Excel**, let the librarian fill New names,
   **Import from Excel…** — imported edits stage red into the same
   grid, matched by id first.
4. Press **Apply**. If filters intersect, the Filter warning dialog
   demands its tick; otherwise the commit runs directly.
5. Cancel path: **Cancel** (or Esc) asks before dropping staged work;
   nothing has written. Unticking a staged row makes it "skipped —
   unchecked", never failed.
6. The Report window opens with names re-read from the model. A skipped
   item reads: "Desk 2 → Desk — skipped: collision, two rows converge
   on 'Desk'." A failed item reads: "Failed — rolled back: owned by
   jsmith."
7. Close. One Ctrl+Z restores every renamed family and type; the
   filter follow-up list is the one thing undo does not fix, because
   the tool never touched the filters.

## See also

- Existing: **Family Types** (type renames inside one family, and the
  staged-grid + Excel precedent this tool follows), **Families
  Transfer** / **Families Downgrade** (panel neighbours), **Sheet
  Manager** (the original staged red grid with Excel round trip).
- Plan siblings: **39 Filter Manager** — shares the filter-rule
  decomposition reader; whichever builds second hoists it to
  `lib/easybim`, and its editing surface is where the broken rule
  strings get fixed. **25 Families Reload** — renames change its
  name-match identity; run renames before a library sync. **26 Family
  Audit** — finds the families worth renaming. **45 Text Types** — the
  system-type naming lane this tool deliberately does not enter.
