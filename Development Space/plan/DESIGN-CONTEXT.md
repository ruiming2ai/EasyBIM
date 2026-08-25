# EasyBIM Design Context

This file is the shared context for every idea folder in this `plan/` directory.
It condenses what the repository itself says about how EasyBIM tools are
designed, built, and tested, so a handoff can assume it instead of repeating it.
Read this first; each idea's `handoff.md` builds on top of it.

## What EasyBIM is

A pyRevit extension ("Handy Tools for Revit", author Ruiming Liu) of ~30
production tools across 9 panels — General, Sheet, Views, Family, Parameters,
Links, Keynote, Message, Misc Tools — with a strong lean toward sheet/document
production, family and parameter management, Revit-link workflows, and
MEP/electrical work (the Circuiting pulldown, MEP connectors as first-class
citizens in Families Downgrade, live Clash Detection Mode). Development happens
off Revit: 68 desktop test files (~1674 tests, ~6 s, stdlib unittest only) run
on CPython 3 while production code stays IronPython 2.7 compatible.

## Design philosophy (the taste to match)

1. **Dry-run before write.** Nothing writes to the model until a complete plan
   has been shown. `build_plan` produces one plan object that both the
   confirmation dialog and the executor read, "so preview and write cannot
   drift". Destructive or surprising steps demand explicit acknowledgement
   checkboxes.
2. **One undo step.** Batches commit through one assimilated TransactionGroup;
   per-item nested groups roll back individually so one bad sheet never costs
   twenty good ones. Counters are zeroed on rollback "so the report never
   claims work that is gone".
3. **Never silently drop.** Every skip is named with its reason; skipped is
   distinguished from failed; declined user choices are "skipped — never
   failed". Reports are read back from the committed model when they claim to
   describe it (the zero-amp report answers "what is left to do", not "what did
   I just do").
4. **Fail closed, degrade visibly.** A tool that cannot identify its scope does
   nothing rather than widening it. A dockable pane that cannot register falls
   back to a modeless window; decorative chrome that cannot line up hides
   itself rather than taking the grid down.
5. **Identity by name, never ElementId,** for anything portable (presets,
   Excel round-trips carry ElementId only as a visible key with name fallback).
   "An ElementId means nothing in the next document."
6. **Capability probing over version numbers.** Runtime `hasattr` checks and
   ForgeTypeId strings decide behavior, backed by `min_revit_version` in
   bundle.yaml. No degraded fallback branch is kept "to rot".
7. **Bounded everything.** Queues, budgets, depth guards, display caps — "a
   guard bug must cost a truncated branch, never a hung Revit."
8. **Explain the why.** Docstrings, tooltips, and README prose state rationale
   and name the failure a rule prevents. Decisions land as dated rows in
   AGENTS.md's Decision Log; corrections are logged, not erased.

## Architecture conventions (every new tool follows this)

- Bundle: `EasyBIM.tab/<Panel>.panel/<Tool>.pushbutton/` with `script.py`
  (thin launcher), `bundle.yaml` (two-line title, narrative tooltip,
  `author: Ruiming Liu`, `min_revit_version` where needed, `context: zero-doc`
  where no document is needed), `icon.png` + `icon.dark.png` (96×96).
- Four-layer split: `<tool>_state.py` (pure Python, zero Revit/pyRevit imports,
  desktop-testable), `<tool>_revit.py` (every API call; one pass over the
  document into plain dicts — "nothing but ints and unicode crosses back"),
  `<tool>_ui.py` (WPF, no Revit API), plus one XAML file per window.
- Shared code hoists to `lib/easybim/` exactly when a second consumer appears.
  Anything imported by `startup.py` or a dockable pane must live in `lib/`
  (a `.pushbutton` folder is only on sys.path while its own script runs).
- Modeless windows do model work through `lib/easybim/external_events.py`
  (ExternalEventBridge — one ExternalEvent + FIFO). Deferred/session work rides
  the single Idling delegate in `lib/easybim/idling.py`. Event-owning buttons
  set `__persistentengine__ = True`, drop stale modules on relaunch, and mirror
  live state into pyRevit envvars so an engine recycle can find it.
- Dockable panes register unconditionally from `startup.py` (init-only API),
  default DockPosition Right, with a modeless right-edge window fallback.
- IronPython 2.7 habits: `# -*- coding: utf-8 -*-`, `from __future__ import
  print_function`, `u""` literals, `.format()` (no f-strings), guarded `clr`
  imports, `compat.py` ElementId shims, `AsValueString` for read-only display.
- Tests to ship with a tool: `test_<tool>_state.py` (pure logic),
  `test_<tool>_command_names.py` (bundle metadata, XAML↔handler wiring, icon
  sizes, IronPython AST scan, forbidden-API pins), `test_<tool>_revit.py`
  (adapter driven against hand-rolled fakes shaped like each API generation),
  `test_<tool>_xlsx.py` when Excel is involved.

## UI language (what a "typical EasyBIM dialog" looks like)

- Resizable modal `Window`, `ShowInTaskbar=False`, centered, grip-resizable,
  explicit Width/Height + MinWidth/MinHeight. Root `<Grid Margin="14">`, rows
  Auto/*/Auto: header (SemiBold title ~30px over a DimGray 13px subtitle),
  card body, footer bar.
- Cards: `Border BorderBrush="#D0D0D0"` with an inner `#E0E0E0` list border;
  header + "X selected, Y unchecked." count + Select All / Select None + a
  small "Search" label with live-filter TextBox + checkbox list. Only the list
  row is a star row, and **every star row carries a MinHeight**.
- Footer: status TextBlock left (the progress/feedback channel — status text,
  not ProgressBars, except cancellable `forms.ProgressBar` for long batch
  writes), right-aligned 100–120×34–36 buttons, primary `IsDefault`, Cancel
  `IsCancel`.
- Multi-select is always checkboxes (spacebar/shift-range works, label in its
  own TextBlock); single-choice is a ComboBox; buttons disable — never hide —
  with the reason in a tooltip; unavailable rows grey out rather than vanish.
- Staged edits render red until Apply; "Hide Un-checked" filters at rebuild
  time; searches flip `is_visible` instead of rebuilding (selection survives);
  identifiers match by token ("12 does not find 112"), names by substring.
- Wizards are small windows setting `self.result` verb strings driven by a
  STEP_* state machine in script.py (Revit's pick UI cannot run under a modal).
  Windows that never need pick UI stay open after their action.
- Reports: read-only WPF tables (never stacked message boxes), pyRevit output
  window with `linkify` for element links, expanders with state preserved
  across rebuilds, "Show" buttons that select + zoom via ExternalEvent.
- Native mimicry where Revit has a precedent: TaskDialog command links +
  verification checkbox for overwrite prompts, Interference-Check-shaped setup
  windows, System-Browser-shaped trees.

## Existing tools (do not re-propose these)

Sheet: Sheet Manager (staged grid + Excel round-trip), Sheet Align, Linked
Sheets Transfer, Print Sheets, Print Set pulldown (From Excel / From Schedule),
Revision Manager pulldown, Isolate. Views: View Align, View Settings Transfer.
Family: Family Types (type-table grid + Excel), Families Transfer, Families
Downgrade. Parameters: Load Parameters, Parameter Copy, Parameter Combine.
Links: Batch Link, DWG Open/Reload. Keynote: Keynotes (fork). Message: Start
Message (+ passive Coordination Review at file open). General: My Ribbon.
Misc Tools: Temp Phase, Tag Align, Circuiting (Update Circuit Rating, Circuit
Schedule dockable tree), Clash Detection Mode (live, forward-only), Batch
Duplicate Host, Excel (schedule export/import), Flip Multiple, 3D Rotate
Multiple, Slope, Grid Offset, Tags Sweep, Auto Update.

## Known gaps and wishlist the repo itself records

- Tag Align: room/area/space tags not supported yet. Linked Sheets Transfer:
  plan views only. My Ribbon: rename/re-icon of placed buttons deferred.
- Deferred hoists: `collect_circuits` duplicated across the lib/pushbutton
  boundary; Revision Manager scripts still carry local copies of
  `sheet_revisions` logic.
- The sorest spot: nothing can be verified off Revit — every spec ends with a
  "Still to verify in Revit" backlog; an in-Revit smoke-test harness would
  relieve it. No CI exists yet for the 6-second desktop suite.
- Hard API walls (do not propose through them): a link's per-category
  visibility is unreadable/unwritable through 2026; families cannot be
  downgraded by API (rebuild only); no API writes files into ACC Docs;
  matchlines are not families; nested links are unreachable.
- Reusable kit a new tool should compose instead of rebuilding: family
  selection wizard pages, ExternalEventBridge, Idling dispatcher,
  `family_load_options` prompt, `excel_workbook` round-trip,
  `sheet_geometry`/`sheet_content`/`sheet_titleblocks`, `copy_paste` factory,
  envvar-mirrored persistent-engine state, `circuit_schedule_state`'s generic
  tree/search engine.
