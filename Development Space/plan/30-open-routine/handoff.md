# 30 — Open Routine

Replay a per-model morning ritual — worksets, active workset, stale links,
working views — from one confirmed plan instead of ten minutes of clicks.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 30 of 45 | General | no | M | 9/10 | 7/10 |

## Main purpose

Every morning, the same ritual: open the model, wait, close the worksets you
never touch, set your active workset, reload the two links that went stale
overnight, open your two working views, close the default 3D that opened
with the file. Ten minutes of clicks before the first real minute of work,
repeated by everyone on the team, every day, in every model. Revit's
"Specify" workset dialog forgets between sessions, and view and
active-workset setup has no native memory at all.

Open Routine records that ritual once and replays it on demand. A routine
belongs to a model, matched by central path or by cloud project + model name
— by name, so the routine follows the model to any local copy. "Record from
Current Session" snapshots the current state: which user worksets are open,
the active workset, the open views by name, which links are loaded. Running
a routine starts with a dry-run plan: worksets are pre-read *without opening
the model* via `WorksharingUtils.GetUserWorksetInfo`, so every step shows
resolved or "workset AR-Interior not found — skipped" before anything
happens, and a routine whose model cannot be identified does nothing rather
than opening the wrong file. Confirm, the model opens with exactly the
planned workset configuration, and the post-open steps run one Idling tick
at a time until the report says what was done, skipped, and why.

The tool is honest that most of this is session state, not model state. The
one step that changes the document is the link reload — not undoable — so it
defaults to unchecked and declining it is a skip, never a failure; the
active workset is a per-user setting, but it decides where new elements
land, so it is named in the plan like everything else. Nothing in EasyBIM,
pyRevit, or native Revit replays an open ritual: Batch Link manages link
placement inside an already-open document, DWG Open/Reload handles CAD. The
usefulness score of 9 is the frequency argument — this is the rare tool
whose audience is literally every seat, every morning — and routines export
and import as JSON in the My Ribbon manner, so a team lead hands the
standard morning setup to the whole team. It sits on the General panel
beside My Ribbon: the personalise-your-session corner of the tab.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/General.panel/Open Routine.pushbutton/` with
  `context: zero-doc` in `bundle.yaml` — the whole point is running before
  a document is open. Two-line title "Open\nRoutine", narrative tooltip
  naming the link-reload exception, `author: Ruiming Liu`. `script.py` is a
  STEP_* wizard state machine (the Tag Align pattern): the plan window must
  close before `OpenAndActivateDocument` can run, so windows set
  `self.result` verbs and the script acts between them. Split:
  `open_routine_state.py` (routine schema, match-by-name resolution, plan
  building, import diff, cancellation bucketing — pure Python),
  `open_routine_revit.py` (workset pre-read, open call, recorders),
  `open_routine_ui.py` + `OpenRoutineWindow.xaml`, `OpenRoutinePlan.xaml`,
  `OpenRoutineRun.xaml`. The post-open step runner lives in
  `lib/easybim/open_routine_steps.py` from day one — it rides the single
  Idling delegate in `lib/easybim/idling.py` as a registered job, and a
  `.pushbutton` folder is only on sys.path while its own script runs, so
  anything the Idling tick executes must be importable from `lib/`. Link
  reloads compose `lib/easybim/link_reload` (`reload_link_elements` and
  its collectors). Routines persist as JSON through
  `script.get_universal_data_file` (roaming, Revit-version-independent —
  the Tag Align presets precedent), identity by name throughout: workset
  names, view names, link names, cloud project + model name. No ElementId
  is ever stored.
- **Revit API route** — Record: `FilteredWorksetCollector` with
  `WorksetKind.UserWorkset`, `IsOpen` per workset;
  `doc.GetWorksetTable().GetActiveWorksetId()`;
  `uidoc.GetOpenUIViews()` → view names; link load state via the
  `link_reload` collectors. Pre-read at plan time:
  `ModelPathUtils.ConvertUserVisiblePathToModelPath` (file-based) or the
  cloud-path route, then `WorksharingUtils.GetUserWorksetInfo(modelPath)`
  → `WorksetPreview` names — wrapped in a capability probe, because cloud
  paths vary by version; when the pre-read fails, the workset step is
  skipped by name and the open falls back to Revit's default
  configuration, stated in the plan — no rot branch. Open:
  `UIApplication.OpenAndActivateDocument(modelPath, openOptions, False)`
  with `OpenOptions.SetOpenWorksetsConfiguration(WorksetConfiguration(`
  `WorksetConfigurationOption.CloseAllWorksets))` plus `.Open(ids)` matched
  by name against the pre-read. Post-open, one Idling tick each: reload
  named links (`link_reload`); `SetActiveWorksetId` (workshared documents
  only, no transaction — a per-user setting); open views by name via
  `uidoc.RequestViewChange` — strictly one request per tick, Revit drops
  stacked ones; close other views via `UIView.Close()`, where the last
  open view refuses and becomes a named skip. If the target model is
  already open, the open phase is skipped whole and only the post-open
  steps run against it.
- **The plan/apply cycle** — `build_plan` resolves every recorded step
  against reality before anything runs: model identity resolved or the
  routine refuses to run; workset names against the pre-read (found /
  "not found — skipped"); link names against the loaded link types; view
  names against non-template views, where a duplicated view name is a
  named skip — "two views named 'Working Plan' — not guessing". The Plan
  window shows the steps as a checkbox list in two groups, Opening and
  After open, red-staged until confirmed, with the link-reload step
  unchecked by default beside the explicit note "Link reloads cannot be
  undone." Confirm closes the window, the open runs, and the Run window
  takes over as the steps drain. The closing report lists every step as
  done / skipped (reason) / failed (reason), and reads back what can be
  read: the active workset re-queried, the open views re-listed — the
  report describes the session as it actually is.
- **Edge cases & honest limits** — Named buckets: "workset not found —
  renamed since recording", "two views share this name", "last view
  cannot close", "link already current", "link not found", "model not
  workshared — workset steps not applicable" (hidden, not errored),
  "pre-read unavailable — opened with Revit's default worksets",
  "cancelled — not run". A renamed workset strands a step; the honest
  recovery is one click — "Re-record from Current Session" overwrites the
  routine from reality. Import of a teammate's JSON shows a diff first and
  names every step this machine's model cannot resolve. The tool never
  creates worksets or views, never synchronises, never closes other
  documents, and does not restore zoom — views open where Revit left them,
  and the tooltip says so.
- **Risks** — `OpenAndActivateDocument` cannot run while any modal window
  is up: the wizard-close-then-act shape is load-bearing, not styling.
  `GetUserWorksetInfo` against cloud paths varies by version — probe,
  never assume, and let the fallback be visible. Sequential
  `RequestViewChange` calls need one Idling tick each or Revit silently
  drops them — the queue must be paced, not looped. A link reload can take
  minutes and cannot be interrupted mid-flight, so cancellation is checked
  *between* steps: Stop finishes the current step, marks the rest
  "cancelled", and the report says so — a half-run ritual must end in a
  readable report, never a mystery session. Engine lifetime: the step
  queue survives in the Idling job with its state mirrored to pyRevit
  envvars, the house pattern for work that outlives the click.
- **Tests** — `test_open_routine_state.py` pins the routine schema round
  trip, match-by-name resolution (renames, missing, duplicates,
  non-workshared), plan bucketing, the import diff, and
  cancellation-between-steps bucketing.
  `test_open_routine_command_names.py` pins `context: zero-doc`, bundle
  metadata, the lib placement of the step runner (the pushbutton imports
  it from `lib`, pinned by the AST scan), XAML↔handler wiring for the
  three windows, 96×96 icons, and forbidden-API pins.
  `test_open_routine_revit.py` drives the adapter against fakes:
  `GetUserWorksetInfo` present / absent / throwing, already-open
  detection, the one-request-per-tick discipline, last-view close
  refusal, `SetActiveWorksetId` guarded on non-workshared docs, and the
  read-back of active workset and open views.

## UI description

**Main window** — resizable modal, header "Open Routine" over the DimGray
subtitle "Record a model's opening ritual once; replay it on demand." One
card, "Routines": search box, list rows carrying routine name, model name,
step count, and last run date. Right of the card: **Record from Current
Session** (disabled with a tooltip when no document is open), **Run**
(`IsDefault`), **Edit**, **Delete**, and below a separator **Export…** /
**Import…** for the JSON hand-off. Footer status left, **Close**
(`IsCancel`).

> "4 routines. 'L2 Electrical — morning' last ran Aug 22."

**Plan window** — Interference-Check shape: two grouped checkbox lists,
"Opening" (open with 6 of 14 worksets; fall-back note if the pre-read
failed) and "After open" (active workset, links, views), every row staged
red until confirmed, unresolvable rows greyed with the reason in a
tooltip, and the note line "Link reloads cannot be undone." beside that
step's unchecked checkbox. Footer status left, **Run** (`IsDefault`) and
**Cancel** (`IsCancel`).

> "14 steps — 11 resolved, 2 skipped (workset renamed), 1 unchecked."

**Run window** — a small modeless window that opens once the model is up
(immediately, for an already-open model) and stays while the Idling queue
drains: a status line, a step list ticking done/skipped as it goes, and a
**Stop** button. When the queue empties it becomes the report — a
read-only table of done / skipped (reason) / failed (reason) — and the
button becomes **Close**.

> "Reloading link ST-Central (2 of 3)…"

> "12 done, 2 skipped (workset renamed; last view kept open), 0 failed.
> Active workset read back: E-Power."

### User operation flow

1. Ribbon: General → Open Routine. The Main window lists this machine's
   routines; no document needs to be open.
2. First time: open and arrange a model by hand, relaunch, press **Record
   from Current Session**, name the routine. Every later morning starts at
   step 3.
3. Select the routine, press **Run**. The pre-read resolves the plan; the
   Plan window opens with every step staged red and the unresolvable ones
   greyed with reasons.
4. Tick the link reload if today needs it; untick anything unwanted —
   unticked steps are "skipped — declined", never failed. A skipped item
   reads: "Open workset AR-Interior — skipped: workset not found (renamed
   since recording)."
5. **Run**. The Plan window closes, the model opens with the planned
   worksets, and the Run window appears, draining one step per Idling
   tick with the status line narrating.
6. **Stop** at any point finishes the current step and marks the rest
   cancelled; the report still opens, honest about both halves.
7. The Run window becomes the report: done / skipped / failed, with the
   active workset and open views read back from the session. Close it.
8. Cancel path: **Cancel** in the Plan window (or Esc) ends the run with
   nothing opened and nothing touched — planning never writes. If a
   workset rename stranded half the routine, press **Re-record from
   Current Session** after fixing the session once by hand.

## See also

- Existing: **My Ribbon** (General-panel sibling; the JSON export/import
  discipline and the Idling-applied session setup this tool mirrors),
  **Batch Link** and **DWG Open/Reload** (the `lib/easybim/link_reload`
  donors), **Temp Phase** (the single-Idling-delegate lifecycle and the
  "session state until saved" honesty this tool inherits).
- Plan siblings: **37 Batch Runner** — the other tool that opens models
  with a planned workset configuration; whichever builds second hoists a
  shared `open_options` helper to `lib/easybim`. **28 Link Health** — the
  audit sibling for the links this tool reloads.
