# 29 — Issue Register

The sheets-by-revisions issue matrix as a portable Excel round trip — the
register the document controller marks up, re-enacted in Revit cell by cell.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 29 of 45 | Sheet > Revision Manager pulldown (extends it) | no | M | 7/10 | 7/10 |

## Main purpose

The drawing issue register — the matrix with a sheet per row, a revision per
column, and a mark in every issued cell — is rebuilt by hand in Excel at
every single issue, from data the model already holds. The return trip is
worse: the document controller marks up the register deciding what goes out,
then someone re-enacts the markup in Revit sheet by sheet — exactly the
misclick Add Revisions on Sheets was built to prevent, except that tool works
one picked revision at a time, not from a register.

Issue Register makes the register a document the model writes and reads.
Export produces the live matrix: rows are sheets (the number is the
identity), columns are revisions (sequence number, date, description —
identity is sequence + date, never ElementId, because an ElementId means
nothing in the next document and nothing in a workbook e-mailed for markup).
Each cell is **C** where the revision arrives via a cloud
(`GetAllRevisionIds` minus additional — the lib's `get_locked_revision_ids`)
or **A** where it was added by hand (`GetAdditionalRevisionIds`), because the
two are not equally editable and a register that hides the difference lies.
Import reads a marked-up register back and builds the dry run: cells to add,
A-cells to clear, and C-cells the user tried to clear as named skips —
"comes from a cloud; remove the cloud" — since no API route removes a
cloud-driven revision from a sheet, and pretending otherwise is how registers
drift from models. Per the prior-art survey, this whole territory is
commercial (Xrev Transmit); SheetLink moves sheet data but neither audits the
matrix nor leaves a record. Each dated export *is* the transmittal record.

The honest lane inside EasyBIM: Sheet Manager's staged grid already toggles
revisions per sheet with checkbox columns, and its Excel export includes
them — but that workbook is a WYSIWYG dump of an editing surface, keyed by
`rev:{ElementId}` headers for this document and this session. Issue Register's
workbook is the deliverable: portable identity, C/A semantics, readable by a
document controller who has never opened Revit, archivable per issue,
re-importable weeks later after Excel has had its way with it. Revision
Manager's four buttons operate per run on picked revisions and cloud
visibility; none produces the register, and none accepts bulk intent from a
spreadsheet. Print Set From Excel reads sheet lists, not matrices. Rank 29 is
not a daily tool — it is an every-issue-day tool, which is why usefulness
sits at 7 while the workflow it replaces is universally hated.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Sheet.panel/Revision Manager.pulldown/Issue Register.pushbutton/`
  added to the pulldown layout as its fifth button. `script.py` thin; all
  windows modal and short-lived (no bridge, no persistent engine — the write
  is one command). `bundle.yaml` two-line title "Issue\nRegister", narrative
  tooltip naming the C/A rule, `author: Ruiming Liu`. Split:
  `issue_register_state.py` (matrix model, mark parsing, header matching by
  content, sequence-first identity resolution, cell diff, skip bucketing —
  pure Python), `issue_register_revit.py` (snapshot: sheets + revisions +
  per-sheet all/additional id sets; executor), `issue_register_ui.py` +
  `IssueRegisterWindow.xaml`, `IssueRegisterConfirm.xaml`,
  `IssueRegisterReport.xaml`, and `issue_register_xlsx.py` (write via
  xlsxwriter in the `sheet_manager_xlsx` mould; read via lib
  `excel_workbook.read_workbook_sheets`). Composes
  `lib/easybim/sheet_revisions` (`get_locked_revision_ids`,
  `ensure_revisions_on_sheet`, `remove_revisions_from_sheet`) and
  `lib/easybim/print_sets.collect_revision_rows` — making this the second
  proper consumer after Sheet Manager, and the natural occasion to do what
  the Decision Log (2026-08-02) deferred as "optional migration later":
  retire the local `sheet_revisions` copies still inside the four Revision
  Manager scripts.
- **Revit API route** — Sheets via `FilteredElementCollector`
  `OfClass(ViewSheet)`; `IsPlaceholder` sheets are listed but flagged (they
  hold revisions like any sheet — the register should show them, marked).
  Revisions via `collect_revision_rows` (`OfClass(Revision)`:
  `SequenceNumber`, `RevisionDate`, `Description`, `Issued`). Per sheet:
  `GetAllRevisionIds()` and `GetAdditionalRevisionIds()`; C = all minus
  additional. The printed revision number is deliberately never used for
  identity — `GetRevisionNumberOnSheet` can differ per sheet, which is
  exactly why the column key is sequence + date. Writes go through the lib:
  `ensure_revisions_on_sheet` (prefers `AddRevision`, falls back to
  `Get/SetAdditionalRevisionIds` — the lib owns that fallback chain and its
  `hasattr` probes) and `remove_revisions_from_sheet`. Transaction shape:
  one assimilated `TransactionGroup` per Apply, one nested `Transaction` per
  sheet; a refused sheet rolls back alone and counters zero on rollback. No
  ExternalEvent or Idling anywhere; modal throughout.
- **The plan/apply cycle** — Export is read-only: scan → workbook, the
  status line reporting the written matrix. Import: `build_plan(workbook,
  snapshot)` emits a cell-level diff — add-cells, clear-A-cells,
  C-clear-attempts (named skip), unknown sheet numbers (named skip, row
  quoted verbatim), unmatched revision columns (named skip, header quoted),
  cells already matching ("no change", never a write) — and the run is
  blocked while any sheet number in the model is ambiguous (two sheets, one
  number: fail closed, no guessing). One plan object feeds both the
  confirmation grid and the executor so preview and write cannot drift. The
  confirmation window carries an acknowledgement tick whenever the plan
  contains clears — "Revisions will be removed from N sheets." — because
  removing a revision from an issued sheet rewrites a record, even inside
  one undo step. The Report window re-reads every touched sheet's revision
  ids from the committed model, so the counts claim only what is actually
  there.
- **Edge cases & honest limits** — Named buckets: "cloud-driven — remove
  the cloud", "unknown sheet number — row quoted", "revision column
  unmatched — header quoted", "ambiguous sheet number — blocks the run",
  "revision marked Issued — flagged; a Revit refusal rolls back that
  sheet", "placeholder sheet", "already matches", "declined / unchecked".
  The tool never creates revisions or sheets — the register edits
  membership, not the revision table, and the window says so; new revisions
  are made in Revit's own dialog first, then exported. It never touches
  clouds (a C cell originates in a cloud; only deleting the cloud clears
  it) and never writes per-cloud visibility — Hide/Unhide Revision Clouds
  by Sequences already owns that. It does not try to be a full transmittal
  system: no recipients, no formats-issued columns — those live in the
  Excel file where the DC adds them, and import ignores columns it did not
  write, by design.
- **Risks** — Revision identity across the round trip is the trap:
  descriptions get edited between export and import, so matching is
  sequence-number-first with date as the tiebreak, and any mismatch fails
  closed to a named skip rather than guessing. Excel mangles dates —
  "01/08/26" silently becomes a serial date — so the export writes date
  cells explicitly as text and the import compares text-equal. Users
  reorder and insert columns, so the reader locates columns by header
  content, never by position (`excel_workbook`'s existing habit). Whether
  `SetAdditionalRevisionIds` accepts a revision marked Issued is not
  verifiable off Revit — the fakes cover both outcomes, a refusal rolls
  back per sheet with Revit's reason, and this lands on the "Still to
  verify in Revit" list explicitly. Sheet counts × revision counts stay
  small (hundreds × dozens); performance is not the risk here — identity is.
- **Tests** — `test_issue_register_state.py` pins C/A classification from
  id sets, header-content column location, sequence-first date-tiebreak
  matching and its fail-closed mismatch, the cell diff (add/clear/skip/no
  change), ambiguous-sheet blocking, quoted-row skips, and counters zeroing
  on a sheet rollback. `test_issue_register_command_names.py` pins the
  five-button pulldown layout, bundle metadata, XAML↔handler wiring for all
  three windows, 96×96 icons, the IronPython AST scan, and forbidden-API
  pins. `test_issue_register_revit.py` drives the adapter against fakes per
  API generation: `AddRevision` present/absent, the
  `Get/SetAdditionalRevisionIds` fallback, an issued-revision refusal
  rolling back its sheet, placeholder sheets, and the post-commit
  read-back. `test_issue_register_xlsx.py` pins the workbook shape, C/A
  cell marks, dates-as-text, and a full write-then-read round trip through
  `read_workbook_sheets`.

## UI description

**Main window** — resizable modal, header "Issue Register" over the DimGray
subtitle "Export the sheets-by-revisions matrix; import it back after
markup. C cells come from clouds and only a cloud can clear them." Two
cards: left, "Sheets" — checkbox list with search, Select All / Select
None, count line "143 sheets — 143 checked, 0 unchecked."; right,
"Revisions" — checkbox list of revisions as "Seq 12 — 2026-08-18 — Issued
for Construction", with an "Include issued revisions" checkbox on by
default (issued ones are usually the point). Footer: status left, then
**Export Register…** (`IsDefault`), **Import Marked-up Register…**, and
**Cancel** (`IsCancel`).

> "Wrote 143 sheets × 12 revisions to Issue Register 2026-08-25.xlsx."

**Confirmation window** (import) — the Linked Sheets Transfer idiom: a
staged grid of every cell change — Sheet, Revision, Was, Becomes — red
until Apply, with expander sections underneath for the named skips:
C-cell attempts ("comes from a cloud; remove the cloud"), unmatched rows
and columns quoted verbatim. While any sheet number is ambiguous the run is
blocked — Apply disabled, never hidden, reason in its tooltip. When the
plan clears anything, the acknowledgement tick "Revisions will be removed
from 9 sheets." sits left of the buttons and gates Apply.

> "61 cells to add, 9 to clear, 3 skipped (cloud-driven), 2 rows unmatched."

**Report window** — read-only WPF table after commit: Added / Cleared /
Skipped (named) / Failed (named, sheet rolled back whole), re-read from the
model. Footer:

> "61 added, 9 cleared — read back from the model. 3 cloud-driven cells
> unchanged. One undo step."

### User operation flow

1. Ribbon: Sheet → Revision Manager → Issue Register. The snapshot runs;
   both cards fill, everything checked.
2. **Export Register…** prompts for a path and writes the matrix; the
   status line reports the counts. Hand the file to the DC. (Steps 3+ may
   happen days later, in a fresh session.)
3. **Import Marked-up Register…** prompts for the workbook. The reader
   locates columns by header content and matches revisions
   sequence-first; the Confirmation window opens with the staged grid.
4. Review the red cells. Untick any row to decline it — declined rows are
   "skipped — declined", never failed. A skipped item reads: "A-101 /
   Seq 12 — skipped: comes from a cloud; remove the cloud."
5. If clears are planned, tick "Revisions will be removed from 9 sheets."
   Apply enables.
6. Apply commits per sheet inside one assimilated group; a sheet Revit
   refuses rolls back alone and is named. The Report window opens with
   counts read back from the model.
7. Close. One Ctrl+Z in Revit reverts the whole import.
8. Cancel path: Cancel (or Esc) in either window before Apply writes
   nothing — export never touches the model, and import plans without
   writing until Apply.

## See also

- Existing: **Revision Manager pulldown** (Add/Remove Revisions on Sheets,
  Hide/Unhide Revision Clouds by Sequences — the per-run tools this
  extends, and the scripts whose local `sheet_revisions` copies retire
  during this build), **Sheet Manager** (first consumer of
  `lib/easybim/sheet_revisions`; its grid is the in-session editing
  surface where this tool is the portable record), **Print Set** pulldown
  (From Excel reads sheet lists, not matrices), **Linked Sheets Transfer**
  (the confirmation-window idiom this import copies).
- Plan siblings: **21 Circuit Excel** — the same portable name-keyed
  round-trip discipline applied to circuits; **11 Reference Check** — the
  pre-issue QA neighbour run the same afternoon; **37 Batch Runner** — the
  issue-day workflow neighbour: print the set, then write the register.
