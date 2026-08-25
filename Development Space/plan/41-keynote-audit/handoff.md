# 41 — Keynote Audit

Reads the model against the keynote table and lists every key that will
print blank, every key the table never defined, and every entry nobody uses.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 41 of 45 | Keynote | no | S | 7/10 | 7/10 |

## Main purpose

The Keynotes fork manages the table document — categories, keys, text, the
DeffrelDB lock that keeps two people from stepping on each other — but
nothing in EasyBIM, or in Revit, reads the model back against that table. A
keynote tag whose key was pruned in a table cleanup prints blank. An element
carries a key the table never defined, and its tag prints nothing without
ever raising a warning. And before anyone dares clean a bloated table, there
is no way to know which entries the model actually uses. All three defects
are discovered the same way: one blank tag at a time, on paper, by the
person reviewing the print.

Keynote Audit is the read-only verification half of the workflow the
Keynote panel already owns. One pass reads the keynote table as Revit
resolved it — the loaded entries, not the file on disk, because the loaded
entries are what the tags will print — and one pass reads every keynote tag
in the model, resolving each to its key and recording *how* the key was
found, because element, material, and user keynotes store it in three
different places and the report must say which it read. Pure state code then
crosses the two: keys in the model that the table lacks, tags with no key at
all, and table entries used zero times. The tool never edits anything — not
the model, and pointedly not the table, because table editing is the
Keynotes tool's job and the report says so in as many words.

It sits at rank 41 not because the check is weak but because the audience is
narrow: offices that run keynote-driven annotation need this before every
issue, and offices that do not will never click it. Where it applies,
nothing else covers it — native Revit's keynote legend lists used keys per
sheet but cannot name a broken key or an unused entry, the Keynotes fork
never looks at the model, and the free ecosystem treats keynotes as a table
problem, not a model-versus-table problem. Same family of failure as 11
Reference Check: what prints broken is found by the client unless a tool
lists it first.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Keynote.panel/Keynote Audit.pushbutton/` beside the Keynotes
  fork, with `script.py` (thin launcher, `__persistentengine__ = True` —
  the window is modeless and Refresh/Show ride the bridge), `bundle.yaml`
  (two-line title "Keynote\nAudit", narrative tooltip naming the fork as
  the fix tool, `author: Ruiming Liu`), 96×96 icons,
  `KeynoteAuditWindow.xaml`, `keynote_audit_state.py` (key resolution
  logic, cross-check, tree assembly — zero Revit imports),
  `keynote_audit_revit.py` (both passes into plain dicts),
  `keynote_audit_ui.py`, `keynote_audit_xlsx.py` (xlsxwriter guarded,
  pyrevit-free, the `sheet_manager_xlsx` build pattern). The report tree
  runs on `circuit_schedule_state`'s generic Node / build_index /
  token-search engine from lib; Show and Refresh use
  `lib/easybim/external_events.py`. Nothing hoists — the fork's `keynotesdb`
  reads the file through DeffrelDB for editing, and this tool deliberately
  does not share that path (see the API route).
- **Revit API route** — table side:
  `DB.KeynoteTable.GetKeynoteTable(doc)`, then `GetKeyBasedTreeEntries()`
  flattened to a key → (text, parent_key) dict; the overload taking
  `KeyBasedTreeEntriesLoadResults` is used behind a capability probe so a
  partially-failed load is reported, not silently truncated. The table's
  path for the header card comes from `GetExternalFileReference()` →
  `ModelPathUtils.ConvertModelPathToUserVisiblePath`, guarded — a table
  that is missing, unresolvable, or empty is the audit's single finding and
  the tool stops there, fail closed, because checking tags against half a
  table produces confident nonsense. Model side: `FilteredElementCollector`
  `OfCategory(OST_KeynoteTags)` `WhereElementIsNotElementType()`; per tag
  read `KEY_VALUE` (`AsString`), the host via `GetTaggedLocalElementIds()`
  behind a probe (older single-reference accessors as fallback), and the
  host's keynote — instance `KEYNOTE_PARAM` first, then its type's, then
  the host's materials' keynote parameters, every BuiltInParameter resolved
  with the `getattr(DB.BuiltInParameter, name, None)` guard. The optional
  third pass reads `KEYNOTE_PARAM` from element *types* only (that is where
  the value lives), with placed-instance counts per type — bounded, one
  type collector, no per-instance walk. No writes, no Transaction — pinned
  by the command-names test.
- **The scan/report cycle** — read-only: scan → cross → tree. The scan runs
  on open (both passes are cheap) and on every Refresh, re-read live so the
  report always answers "what is still broken". `keynote_audit_state.
  resolve_tag(tag_dict)` classifies each tag's mode by comparison — key
  matches the host element's keynote → element; matches a host material's →
  material; matches neither → user — and where two paths match the same key
  the mode column says "ambiguous", never a guess. `cross(table, tags,
  typed_elements)` emits four groups: **Missing from table** (keys used in
  the model with no table entry — the blank-print list, red, grouped by key
  with usage counts), **Empty keys** (tags resolving to nothing at all),
  **Unused entries** (leaf table entries used zero times — informational,
  never red, capped, and parents whose children are all unused roll up to
  one row so a master table's dead division does not print as 400 rows),
  and **Keyed but untagged** (types carrying a key no tag shows —
  informational, off by default). Export writes every group plus the skips
  through `keynote_audit_xlsx` for the spec coordinator.
- **Edge cases & honest limits** — named-skip buckets: *"tag unreadable —
  host or parameter threw (n)"*; *"mode ambiguous — element and material
  carry the same key"* (listed with both readings, judged against the table
  once); *"tag in a linked model — out of scope"* (a link references a
  different table; half-checking it would invert findings, so the count is
  stated and nothing more). Orphaned tags whose host was deleted land under
  Empty keys with the reason "host gone", distinct from a genuinely blank
  key. The unused-entries list is framed as inventory, not defect — a
  master office table is never fully used, the header says so, and the
  display cap ("showing 200 of 1,340 — export carries all") keeps it from
  drowning the two red lists. The tool never edits the table, never edits a
  tag, and never claims the file on disk matches the loaded table — it
  audits what Revit will print, and says exactly that in the subtitle.
- **Risks** — the three keynote modes storing their key in three places is
  the whole risk: misclassifying a mode inverts a finding, so mode is
  inferred only by comparison against values actually read, every
  resolution path is recorded on the row, and the resolution logic is pure
  state code under hard desktop tests. `GetKeyBasedTreeEntries` behavior
  around unloadable files varies by release — probe the results overload,
  and treat any throw as the fail-closed "table unreadable" finding. Very
  large tables cost memory only in the unused list; the cap and the
  parent-rollup are the defence. The linked-model exclusion will
  disappoint multi-model teams — the count line keeps the exclusion loud
  instead of silent.
- **Tests** — `test_keynote_audit_state.py` pins the mode-inference matrix
  (element/material/user/ambiguous), the fail-closed empty-table path, the
  parent-rollup and cap arithmetic, and cross-check outputs on synthetic
  tables. `test_keynote_audit_command_names.py` pins bundle metadata,
  XAML↔handler wiring, icon sizes, the IronPython AST scan, and the
  no-Transaction pin. `test_keynote_audit_revit.py` drives the adapter
  over fakes shaped like each API generation — missing table, throwing
  `GetExternalFileReference`, single-host vs multi-host tag accessors,
  materials without keynote parameters — asserting nothing but ints and
  unicode crosses back and every failure lands in a named bucket.

## UI description

**Main window** — one resizable modeless window (`ShowInTaskbar` off,
centered, grip-resizable), root `Grid Margin="14"`, rows Auto/Auto/*/Auto.
Header: "Keynote Audit" SemiBold ~30px over the DimGray 13px subtitle
"Where the model and the keynote table disagree. Nothing is ever changed."
Below it a one-line **Table card** (`#D0D0D0` border): the resolved table
path, its entry count, and a load warning where the results overload
reported one — "Office Keynotes.txt — 1,340 entries." Body: the report tree
on the generic engine — **Missing from table (9)** / **Empty keys (3)** /
**Unused entries (61)** / **Keyed but untagged (12)** — grouped by key,
each row showing key, table text where one exists, usage count, the mode
column ("element / material / user / ambiguous"), and a **Show** button
that selects and zooms through `ExternalEventBridge`. A **Named skips**
expander sits last. Live Search matches keys by token ("12" does not find
112) and text by substring; expander state survives Refresh. Footer: status
TextBlock left, right-aligned 110×35 buttons — **Refresh** (`IsDefault`),
**Export**, **Close** (`IsCancel`). The Keyed-but-untagged group carries a
checkbox in its header ("include type keynotes") that is off by default and
re-runs only that pass when ticked. Status lines:

> "412 keynote tags read — 9 keys missing from the table, 3 empty. 61 of 1,340 table entries unused. Nothing was changed."

> "Keynote table could not be read — audit stopped. Fix the table path in Revit or the Keynotes tool first."

> "All 412 tags resolve against the table. 61 unused entries listed as inventory."

There is no confirmation window and no report window — the tool never
writes, so the Main window's tree is the whole story.

### User operation flow

1. Ribbon: Keynote → Keynote Audit. The Main window opens and both passes
   run immediately; the status line ticks while the tree fills.
2. If the table cannot be read, the tree holds exactly one red row and the
   status says the audit stopped — the fail-closed path. Close, fix the
   table, reopen.
3. Read the red groups first: click **Show** on "K-0912 — used 7 times —
   missing from table"; Revit selects and zooms the tagged elements. Fix in
   the model, or add the key back through the Keynotes tool.
4. A skipped item looks like: "Tag id 512907 — in linked model 'ARCH' —
   out of scope", or "Tag id 400123 — mode ambiguous — element and
   material both read K-0455" under Named skips. Neither is counted as a
   finding.
5. Press **Refresh** after fixing — both passes re-read the live model and
   table; fixed rows disappear, search text and expander state survive.
6. **Export** writes all four groups plus the skips to .xlsx for the spec
   coordinator's cleanup session in the Keynotes tool.
7. **Close** or Esc at any time — the cancel path and the happy path are
   the same door, because nothing was ever going to be written.

## See also

- Existing: **Keynotes** (the fork — edits the table this tool audits
  against; the two are the write and read halves of one workflow), **Tags
  Sweep** (the general tag sweep next door in temperament).
- Siblings: **11 Reference Check** (the other "what prints broken" audit —
  same client-finds-it failure, different annotation), **07 Dim Overrides**
  and **45 Text Types** (the annotation-QA family), **02 Parameter Check**
  (the same scan-fix-Refresh loop generalised to any parameter).
