# 37 — Batch Runner

Open, run, close — a curated task loop over many models, reusing the
office's own print sets and exports, naming every model it had to skip.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 37 of 45 | Misc Tools | no | L | 7/10 | 9/10 |

## Main purpose

Deliverable week: export the door schedules from fourteen models, print the
issue set from each, and produce a warnings count for the QA sheet. That is
fourteen opens, fourteen waits, fourteen chances to misclick — a full
afternoon of babysitting Revit for work no human decision is part of.
Everyone scripts this once in Dynamo, badly, and the script dies on the
first missing-link dialog.

Batch Runner is the open-run-close loop, zero-doc capable. The model list
comes from a folder scan, pasted paths, or an Excel column read through
`excel_workbook` — and before anything opens, every row is pre-flighted
from its file header: missing files, files saved in a newer Revit than
this session, and cloud rows are named skips while the list is still just
a list. Each surviving model opens detached with all worksets closed, with
Revit's open-time noise contained by a failure handler and a
dialog-answering handler, under a per-model time budget — a guard bug must
cost one abandoned model, never a hung Revit. The tasks are a curated
menu of engines EasyBIM already owns, read-only by default: schedule
export to Excel, a named print set to PDF, a health snapshot of counts.
The one write task, link reload, arms only behind an explicit save
acknowledgement, and saves land only as detached copies in a chosen
output folder — the runner fails closed on output and will never write
over a central path. The report is a per-model-by-per-task grid written
back beside the Excel input list, so the batch documents itself.

It earns rank 37 as the highest-impact L on the list that most seats use
a few times a month, not daily. The prior art shapes the wedge exactly:
Revit Batch Processor exists free but is developer-grade — bring your own
scripts, no dry-run, no named-skip discipline — and batch exporters
(ProSheets and kin) are saturated territory this tool refuses to enter.
Batch Runner is not another exporter and not a script runner: it is a
friendly, curated UI over the print sets, schedule exports, and Excel
round-trip the office already maintains in EasyBIM, with the house
dry-run-and-named-skip discipline applied to the one thing no EasyBIM
tool has ever owned — the loop across documents. Every existing tool in
the inventory works inside one open model; this is the first to open and
close them.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Misc Tools.panel/Batch Runner.pushbutton/` with
  `context: zero-doc` in `bundle.yaml` — the loop starts with no document
  open. Two-line title "Batch\nRunner", narrative tooltip naming the
  central-safety rule ("never saves over a central; writes land only in
  detached copies"), `author: Ruiming Liu`. `script.py` thin launcher;
  the run is synchronous inside one command under the cancellable
  progress bar — no ExternalEventBridge, no persistent engine. Split:
  `batch_runner_state.py` (list resolution including the Excel column,
  pre-flight bucketing, the task registry as data, budget bookkeeping,
  report-grid shaping — pure dicts), `batch_runner_revit.py` (the
  open/close loop, both containment handlers, per-task adapters),
  `batch_runner_ui.py` + `BatchRunnerWindow.xaml`,
  `BatchRunnerConfirm.xaml`, `BatchRunnerReport.xaml`, and
  `batch_runner_xlsx.py` (report writer, `xlsxwriter` like the other
  `_xlsx` modules). This tool forces three second-consumer hoists, and
  the handoff says so plainly because a `.pushbutton` folder is only on
  sys.path while its own script runs: the schedule-export engine
  (`schedule_export_state/_revit/_xlsx`) hoists out of the Excel
  pushbutton into `lib/easybim/`; the print submit loop hoists out of
  Print Sheets' `script.py` into `lib/easybim/print_submit.py`,
  composing the existing `lib/easybim/print_sets` name resolution; and
  the `BasicFileInfo` header reader that 25 Families Reload specs as a
  standalone dict-in/dict-out function becomes shared. Link reload
  composes `lib/easybim/link_reload` as-is.
- **Revit API route** — Pre-flight, no document open:
  `os.path.exists` per row, then `DB.BasicFileInfo.Extract(path)` —
  `IsWorkshared` drives the detach plan, the saved-in version
  (capability-probed `Format` falling back to `SavedInVersion`) gates
  newer-than-session rows out and marks older rows "opens with upgrade",
  and an `Extract` that throws is "header unreadable — skipped". Open:
  `app.OpenDocumentFile(ModelPathUtils.ConvertUserVisiblePathToModelPath(
  path), open_options)` — Application-level, so the document never
  activates — with `OpenOptions.SetOpenWorksetsConfiguration(
  WorksetConfiguration(WorksetConfigurationOption.CloseAllWorksets))`
  and, for workshared rows, `DetachFromCentralOption.
  DetachAndPreserveWorksets`. Containment, subscribed for the run and
  detached in a finally: `UIApplication.DialogBoxShowing` answering the
  known dismissible set via `args.OverrideResult` (missing links,
  default-view prompts, updater nags — unknown dialog ids are logged
  into the report so the known set can grow), and
  `Application.FailuresProcessing` deleting warnings and resolving
  resolvable failures. Tasks: schedule export through the hoisted
  engine, schedules resolved per model by name; print set — on 2022+
  via `doc.Export` with `DB.PDFExportOptions` (driverless, no OS
  dialogs), the set resolved through `print_sets`; pre-2022 the
  `PrintManager` path is plan-flagged "attended"; health snapshot —
  `doc.GetWarnings()` count, in-place family count via
  `OfClass(Family)` + `IsInPlace`, link load states via the
  `link_reload` collectors — bounded counts, never element dumps.
  Write shape: read tasks run without a transaction; write tasks run in
  one assimilated `TransactionGroup` per model, so a failed task rolls
  its model back to as-opened. Saves are `doc.SaveAs` onto a path the
  tool built inside the output folder, behind a guard that refuses any
  target equal to the source path or the header's `CentralPath` —
  pinned by test. `doc.Close(False)` always runs in the finally. Cloud
  rows (cloud model path or header) grey the save option: no API writes
  files into ACC Docs, stated as the reason.
- **The plan/apply cycle** — `build_plan` pairs every resolved model row
  with the checked task set and the options (output folder, per-model
  minute budget, detach), carrying the pre-flight buckets already named.
  The Confirmation window shows the per-model plan grid and holds the
  acknowledgement ticks: the detach tick whenever any workshared row is
  in the list ("A detached copy is opened; the central model is never
  touched.") and the save-arming restatement whenever a write task is
  checked ("Writes land only in detached copies in the output folder.").
  Run stays inert until the applicable ticks are checked. The report
  grid is collected task-by-task while each document is open and frozen
  at its close — the report claims nothing about a closed model beyond
  what was read before closing, and says so in its footer. Export
  writes the grid as a new worksheet ("Batch Report 2026-08-25") beside
  the input list in the source workbook.
- **Edge cases & honest limits** — Named buckets: "file missing",
  "saved in Revit {year} — newer than this session", "header unreadable",
  "cloud — export only", "opened with upgrade" (a note, not a skip),
  "abandoned: over budget", "abandoned: dialog", "print set '{name}' not
  found in this model", "schedule '{name}' not found", "cancelled — not
  opened", "failed — {one-line exception}". The tool refuses to guess:
  a moved file is never re-found by name search; it never synchronizes
  with central — `SynchronizeWithCentral` is out of scope by design,
  because this tool produces deliverables and corrected detached
  copies, it does not maintain centrals; and it never runs arbitrary
  scripts — the registry is curated, and Revit Batch Processor already
  exists for people who want the other thing. Honest limits, stated in
  the window: OS-level dialogs (print drivers, third-party add-in
  popups) are invisible to `DialogBoxShowing`; pre-2022 printing is
  attended; memory grows across many opens, so the plan warns above a
  stated batch size instead of hiding a restart.
- **Risks** — The modal that never raises `DialogBoxShowing` is the
  honest wall: the budget is checked between tasks and between models
  and cannot interrupt a blocked call — a truly stuck open stalls the
  run until a human dismisses the dialog, after which the model is
  abandoned over budget and the row names it. Opening an older file
  upgrades it in memory — harmless while nothing saves, but every
  detached save from an upgraded open must carry the "opened with
  upgrade" note so nobody trusts it as a faithful copy of the original
  version. Print drivers are their own modal minefield — the
  `PDFExportOptions` route on 2022+ is the real fix, and the attended
  flag is the pre-2022 truth, not a promise. `OpenDocumentFile` itself
  can throw (corrupt file, exclusive lock): per-model failed with the
  exception text, loop continues. Memory: close-and-continue with the
  stated batch-size warning, never a hidden restart.
- **Tests** — `test_batch_runner_state.py` pins list resolution from
  all three sources (missing files, duplicate rows, the Excel column),
  pre-flight bucketing including the version gate boundaries, budget
  bookkeeping between tasks, per-model report bucketing with abandoned
  vs failed vs skipped kept distinct, and counters frozen at close.
  `test_batch_runner_command_names.py` pins `context: zero-doc`, bundle
  metadata, XAML↔handler wiring for the three windows, 96×96 icons, the
  IronPython AST scan, and that the schedule-export and print engines
  are imported from `easybim`, never from a sibling pushbutton.
  `test_batch_runner_revit.py` drives the loop against fakes per API
  generation: `BasicFileInfo` shapes, detach options only on workshared
  rows, `DialogBoxShowing` overrides and the unknown-id log,
  `FailuresProcessing` resolution, a task exception rolling the model's
  group back, the SaveAs guard refusing source and central paths, the
  `PDFExportOptions` probe vs the attended flag, cloud rows greying
  save, and `Close(False)` reached on every exit path.
  `test_batch_runner_xlsx.py` pins the report worksheet written beside
  the input list.

## UI description

**Main window** — resizable modal (zero-doc), header "Batch Runner" over
the DimGray subtitle "Runs curated tasks across many models. Centrals are
never written." Three cards. **Models card**: list rows carrying path,
resolve status, and saved-in version; **Add Folder…** / **Add from
Excel…** / **Paste Paths**, live-filter Search; pre-flight-skipped rows
greyed in place with the reason in a tooltip. **Tasks card**: checkbox
list of the curated tasks with a per-task one-line description; write
tasks greyed until the save checkbox below the list is ticked, reason in
the tooltip; attended print rows flagged on pre-2022 sessions. **Options
card**: output folder picker, per-model minute budget ComboBox, the save
checkbox ("Save detached copies to the output folder"). Footer status
left, **Run…** (`IsDefault`, disabled with a tooltip until a model and a
task are checked), **Cancel** (`IsCancel`).

> "14 models — 12 resolved, 1 missing (skipped), 1 saved in Revit 2026
> (skipped). 3 tasks checked, read-only."

**Confirmation window** — the per-model plan as a read-only grid, one
row per model × task, pre-flight buckets named in place, the batch-size
warning line when the list is long, and the acknowledgement ticks:
"A detached copy is opened; the central model is never touched." (shown
when any row is workshared) and "Writes land only in detached copies in
the output folder." (shown when a write task is armed). **Run 12
models** stays inert until the applicable ticks are checked; **Back**
returns.

> "12 models × 3 tasks — 2 rows pre-skipped (named). Budget 10 min per
> model."

**Run progress** — the one sanctioned `forms.ProgressBar`, cancellable:
cancelling finishes closing the current model without saving and stops
cleanly, with the remainder marked "cancelled — not opened".

> "Model 6 of 12: LP-Tower-E — exporting schedules (2 of 3)…"

**Report window** — read-only grid, models as rows, tasks as columns,
each cell done / skipped (reason) / failed (one-line exception) /
abandoned (budget or dialog), with the per-model notes ("opened with
upgrade", "detached copy saved: {path}") in a Note column. Buttons:
**Write Report to Excel** (into the source workbook, beside the input
list), **Close**.

> "11 models done, 1 abandoned (dialog) — 33 task results, 31 done.
> Collected before each close; nothing was read after closing."

### User operation flow

1. Ribbon: Misc Tools → Batch Runner. No document needs to be open. Add
   a folder or the Excel list; the pre-flight runs as rows land and the
   Models card fills, skips already named.
2. Check tasks — read-only ones are live immediately; ticking the save
   checkbox in Options arms the write tasks and un-greys them.
3. Press **Run…**; read the Confirmation grid; tick the detach
   acknowledgement (and the save restatement if armed).
4. **Run 12 models**. The progress bar narrates per model and per task.
   A dialog the handlers know is answered silently; one they do not
   stalls until dismissed by hand, and that model lands as "abandoned:
   dialog".
5. Cancel path A: **Cancel** / **Back** before step 4 — nothing has
   opened. Cancel path B: cancelling the progress bar finishes closing
   the current model without saving; done models keep their results and
   the rest read "cancelled — not opened".
6. The Report window opens. A skipped cell reads "Print set 'Issue 04'
   not found in this model — skipped"; an abandoned row reads
   "abandoned: over budget (10 min) — closed without saving."
7. **Write Report to Excel** appends the grid beside the input list so
   the batch documents itself; Close. Nothing in the session changed:
   every opened model was detached or closed unsaved.

## See also

- Existing: **Print Sheets** and the **Print Set pulldown** (the print
  engines this tool hoists and composes), **Excel** (the
  schedule-export engine, hoisted to `lib` as its second consumer),
  **Batch Link** / **DWG Open/Reload** (the `link_reload` lib this
  tool's one write task reuses).
- Plan siblings: **30 Open Routine** — the other tool that opens models
  with a planned workset configuration; whichever builds second hoists
  the shared `open_options` helper to `lib/easybim`. **25 Families
  Reload** — the `BasicFileInfo` header reader is specced there as a
  standalone function; this tool is the consumer that hoists it.
  **08 Warnings Watch** — the health snapshot task is its one-shot
  cousin; per-central history belongs there, counts-today belong here.
  **40 Smoke Test** — the other tool built on API-opened documents and
  the close-in-finally discipline.
