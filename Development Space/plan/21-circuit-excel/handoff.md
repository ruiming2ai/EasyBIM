# 21 — Circuit Excel

The panel-schedule markup loop — engineer edits in Excel, circuits update in Revit — run straight off ElectricalSystems, no schedule required.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 21 of 45 | Misc Tools — Circuiting pulldown | yes | M | 8/10 | 8/10 |

## Main purpose

The engineer who decides breaker ratings and load descriptions is usually not
the person driving Revit. Today that loop is a screenshot of a panel schedule,
a marked-up PDF back, and a modeler retyping — a day of latency per round and
a typo per panel. The circuits themselves should make the round trip: export
what is editable, let the engineer edit it in the tool they already live in,
and stage every changed cell back against the live model before anything is
written.

Circuit Excel exports one worksheet per panel, keyed by the portable identity
the Circuiting tools already trust — panel name plus circuit number, with
multi-wire numbers like "1,3,5" kept verbatim as text and ElementId along as
a visible key only. The editable columns are not a fixed list: the same
per-document discovery scan Update Circuit Rating runs finds every genuinely
writable circuit parameter — Load Name, Rating, Frame, and any project or
shared parameters bound to circuits ride along automatically. Import
re-collects circuits fresh, matches rows by the identity pair, builds a plan
from changed cells only, and shows it as a staged red diff. Unchanged cells
are never rewritten; rows whose identity no longer resolves are named skips,
never guesses.

Generic Excel round-trips are saturated territory — SheetLink, pyRevit's own
importer, and this repository's Excel pushbutton all exist — but every one of
them speaks in schedule views: build a schedule first, get its columns, and
hope the row order survives re-export. Circuit Excel is different on exactly
two counts, and they are the whole point. It works straight off
`ElectricalSystem` collectors with no schedule anywhere, offering only
parameters the current document will actually accept a write to; and its
rows carry the panel+number portable identity, so the same workbook survives
a re-export, a re-sort, and a week of parallel model edits — which a schedule
export's positional rows never guarantee. That, plus the named-skip ledger
when identities go stale, earns it the rank among the Circuiting siblings.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Circuiting.pulldown/Circuit Excel.pushbutton/`
  with thin `script.py`, `bundle.yaml` (two-line title "Circuit\nExcel",
  narrative tooltip, `author: Ruiming Liu`), 96×96 icons, and the four-layer
  split plus the Excel file: `circuit_excel_state.py` (matching, changed-cell
  detection, plan builder — pure), `circuit_excel_revit.py` (collect,
  discovery scan, writer), `circuit_excel_ui.py`, `circuit_excel_xlsx.py`
  (xlsxwriter guarded, pyrevit-free so the desktop suite runs it, exactly as
  `sheet_manager_xlsx` is built), with `CircuitExcelWindow.xaml`,
  `ImportDiffWindow.xaml`, `ReportWindow.xaml`. `script.py` branches on the
  Main window's `self.result` verb strings ("export" / "import" /
  "cancel") — the same front door the Excel pushbutton drives its
  `ScheduleSelectionWindow` with. Add `Circuit Excel` to the
  Circuiting pulldown's `bundle.yaml` `layout:` list. Two hoists intersect
  here: `collect_circuits` (the recorded deferred hoist, claimed by 06 Load
  Names — whichever of the two builds first performs it) and the
  discovery-and-units helpers in `circuit_rating_revit`
  (`_collect_target_options`, `_get_param_unit_id`, `_to_display`,
  `_to_internal`) — this tool is their second consumer, so the first of
  21/22 to build lifts them into `lib/easybim/circuit_params.py` and Update
  Circuit Rating imports them back.
- **Revit API route** — circuits via the hoisted `collect_circuits`
  (`OST_ElectricalCircuit`, not element types), cast to
  `Electrical.ElectricalSystem`; per circuit `PanelName`, `CircuitNumber`
  (verbatim unicode — identity, never parsed), `CircuitType` (spares and
  spaces exported too, marked in a read-only Type column). Editable-column
  discovery runs per document, at export *and again at import*: instance
  parameters with storage Double/Integer/String, `IsReadOnly` False on every
  sampled circuit, BuiltInParameter names (`RBS_ELEC_CIRCUIT_NAME`,
  `RBS_ELEC_CIRCUIT_RATING_PARAM`, `RBS_ELEC_CIRCUIT_FRAME_PARAM`) resolved
  through the `getattr(DB.BuiltInParameter, name, None)` guard. Two value
  channels, never mixed: editable numeric cells carry display-unit floats
  converted through `_to_display`, and come back through `_to_internal`;
  read-only and identity cells carry `AsValueString` text. Writes are
  `param.Set()` after a fresh `IsReadOnly` check and, in workshared models, a
  `WorksharingUtils.GetCheckoutStatus` probe. Commit is one assimilated
  `TransactionGroup`, one nested `Transaction` per circuit — one locked
  parameter rolls back one circuit's cells, never the batch; counters zero on
  rollback. The flow is modal and short-lived: no ExternalEvent, no Idling,
  no persistent engine. The workbook side mirrors the Sheet Manager dialect
  so users see one house format: locked grey identity columns (Panel, Ckt,
  ElementId, Type), white editable columns, sheet protection on, and a
  hidden `_metadata` sheet carrying a format signature ("EasyBIM Circuit
  Excel"), version, and the worksheet→panel-name and column→parameter-name
  maps — worksheet tabs are sanitized to Excel's 31-char/forbidden-char
  rules with collision suffixes, and `_metadata` stays the authority on
  which panel a sheet means. Circuit numbers are written with
  `write_string` under an explicit text format so Excel never coerces
  "1,3,5" into a number — the same guard the schedule export applies to
  its identity column. Import reads through `easybim.excel_workbook`,
  which reads hidden sheets by name — that is its charter.
- **The plan/apply cycle** — `build_plan` re-collects circuits fresh and
  matches each workbook row by (panel name, circuit number): a pair that no
  longer resolves is "skipped — circuit not found", a pair resolving to two
  circuits is "skipped — ambiguous identity", and a resolved circuit whose
  ElementId disagrees with the row's is "skipped — identity mismatch, needs
  review" — flagged, never trusted. Surviving rows diff cell by cell:
  strings compare normalized, numerics compare in display units within a
  stated epsilon so a float round-trip never stages the whole workbook as
  changed. The plan is one object read by both the staged grid and the
  executor. Nothing here is un-undoable, so there is normally no
  acknowledgement tick — except when the plan writes Load Name, which
  surfaces the same tick Load Names owns: "Written load names stop
  auto-updating." The Report window re-reads every written parameter from
  the committed model, with skipped distinguished from rolled-back.
- **Edge cases & honest limits** — named-skip buckets: "circuit not found",
  "ambiguous identity", "identity mismatch", "column read-only in this
  document" (users do unprotect sheets and edit grey cells — import
  re-checks and refuses), "owned by another user", "value unparseable"
  (with the raw cell text shown), "unchecked". A worksheet whose panel no
  longer exists is skipped whole and named once. When more than a fifth of
  rows fail identity, the diff window shows a banner — "this workbook looks
  stale; circuits were likely renumbered — re-export" — because circuit
  numbers change when slots move and the ledger must be loud, not just
  itemized. The tool moves data only: it refuses to create circuits, move
  slots, re-panel, or renumber — those are model topology and belong to 19
  Circuit Renumber — and says so in the tooltip.
- **Risks** — Rating and Frame writability varies with how the document's
  electrical settings define them; discovery must probe `IsReadOnly` per
  document and never assume, and the import-side re-probe is what protects a
  workbook exported from a differently-configured model. Stale identities
  after slot moves are the systemic failure; the skip ledger plus the stale
  banner are the safety net. Excel's number coercion is defended at export
  (`write_string` + text format) but a hand-retyped circuit number can still
  come back as "1,3,5.0"-shaped junk — import normalizes token whitespace
  and otherwise skips with the raw text shown. Display-unit float
  round-trips need the epsilon compare or every re-import stages everything.
- **Tests** — `test_circuit_excel_state.py` pins matching (found, ambiguous,
  mismatch), changed-cell detection at the epsilon boundary, the stale-
  workbook threshold, and skip classification. `test_circuit_excel_command_names.py`
  pins bundle metadata, the pulldown layout entry, XAML↔handler wiring for
  all three windows, icon sizes, and the IronPython AST scan.
  `test_circuit_excel_revit.py` drives the adapter against fakes where
  `IsReadOnly` flips between export and import, BIP names are missing,
  checkout status refuses, and one nested rollback zeroes only its own
  counters. `test_circuit_excel_xlsx.py` round-trips writer→reader, pinning
  the metadata sheet, tab sanitization collisions, and the "1,3,5" text
  guard.

## UI description

**Main window** — resizable modal, header "Circuit Excel" over a DimGray
subtitle naming the document. Two cards: **Panels** (checkbox list with
live-filter Search, count line "12 panels — 9 checked, 3 unchecked.", Select
All / Select None) and **Columns** (checkbox list of discovered writable
parameters, Load Name / Rating / Frame pre-checked; parameters read-only in
this document greyed — never hidden — with the reason in a tooltip; the
column set applies to export only, and the card says so). Footer: status
left, then **Export** (`IsDefault`), **Import…**, **Cancel** (`IsCancel`) —
the two verbs set `self.result` and close, Excel-pushbutton style, and
`script.py` runs the chosen branch.

> "34 columns discovered — 3 read-only in this document (greyed). 9 panels
> will export."

> "Wrote LP-Circuits.xlsx — 9 worksheets, 214 rows."

**Import diff window** — after the standard open dialog: a staged diff grid
— Panel, Ckt, Column, Model Value, Excel Value — every changed cell red
until Apply, checkbox per row, "Hide Un-checked" filtering at rebuild time,
Search matching circuit numbers as whole tokens (12 does not find 112).
Below, an expander "Skipped rows (5)" grouped by named reason, and the stale
banner when it trips. Footer: status left, the Load Name acknowledgement
checkbox (only when Load Name cells are staged), **Apply** (`IsDefault`,
disabled with tooltip until any required tick), **Cancel** (`IsCancel`).

> "61 cells to write across 41 circuits, 5 rows skipped. One undo step."

**Report window** — read-only WPF table, never stacked message boxes:
Panel, Ckt, Column, Value (read back from the model), Result; skips under
their named buckets, rollbacks separate from skips.

> "58 cells written, 5 rows skipped, 1 circuit rolled back — read back from
> the model."

### User operation flow

1. Ribbon: Misc Tools → Circuiting → Circuit Excel. The Main window opens;
   the discovery scan fills both cards and the count lines.
2. Export: check panels and columns, press **Export**, hand the named file
   to the engineer. Cancel closes with nothing read beyond the scan.
3. The engineer edits white cells in Excel; grey identity cells are locked.
4. Days later — Main window → **Import…**, pick the workbook. The fresh
   re-collect runs; the diff grid fills with only genuinely changed cells,
   red.
5. Review; uncheck any cell you disagree with. A skipped item looks like:
   "LP-2 / 7 — skipped: circuit not found (renumbered?)" or "EM-1 / 1,3,5 —
   Rating — skipped: value 'abc' unparseable."
6. If Load Name cells are staged, tick "Written load names stop
   auto-updating"; Apply enables.
7. **Apply** commits one TransactionGroup — one undo step. A locked circuit
   rolls back its own nested transaction into the ledger.
8. The Report window opens with values read back from the committed model.
   One Ctrl+Z in Revit reverts the batch.
9. Cancel path: **Cancel**/Esc anywhere before Apply closes with the model
   untouched — declined rows are skipped, never failed.

## See also

- Existing: **Excel** (the schedule-view round-trip this deliberately is
  not; donor of the verb-string front door and the text-guard manners),
  **Sheet Manager** (the xlsx dialect: locked grey cells, `_metadata`,
  `excel_workbook` reader), **Update Circuit Rating** (discovery + unit
  machinery donor), **Circuit Schedule** (`collect_circuits` and the
  panel+number identity).
- Rank 06 **Load Names** — the in-model mass-writer for the same Load Name
  cells; shares the `collect_circuits` hoist and the auto-update
  acknowledgement.
- Rank 22 **Voltage Drop** — the other consumer of the hoisted
  discovery-and-units helpers; first to build lifts them to lib.
- Rank 19 **Circuit Renumber** — the tool that makes workbooks stale by
  design; its runs are why the identity ledger must be loud.
- Rank 01 **Circuit Check** — run it after an import lands to QA what the
  engineer sent back.
