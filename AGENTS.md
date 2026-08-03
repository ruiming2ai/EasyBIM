# AGENTS Guide

## Project Snapshot

- Project name: EasyBIM.extension
- Status: Active pyRevit extension development on `main`
- Primary goal: Maintain and expand EasyBIM pyRevit commands/hooks with safe workflow governance
- Primary stack: Python (pyRevit/Revit API), XAML for command UI

## Current Workflow Summary

- Work inside existing pyRevit folder conventions (`*.panel`, `*.pushbutton`, `*.pulldown`).
- Keep command scripts focused and consistent with existing import/transaction patterns.
- Validate Python syntax for changed scripts before handoff (`python -m py_compile <script.py>`).
- Update command icons/bundles only when behavior or UX changes require it.
- Keep repo-level workflow decisions documented in `AGENTS.md` and mirrored in the repo parent skill.
- Use shorthand command phrase `sync worktrees` for the standard parallel-worktree sync workflow.
- Default meaning of that phrase:
  - Commit + push on the active feature worktree branch first.
  - Identify the creator branch associated with that feature worktree. `Temp-Phase-and-View-2` is only an example creator branch, not a fixed target.
  - In primary worktree, sync the creator branch with the feature worktree branch.
  - Stop after the creator branch is updated unless a separate merge to `main` is explicitly requested.

## Commands

- Python syntax check for one file:
  - `python -m py_compile "<path-to-script.py>"`
- Compile many command scripts:
  - `python -m compileall "EasyBIM.tab"`
- Primary worktree and branch checks:
  - `git worktree list`
  - `git rev-parse --abbrev-ref HEAD`
- Standard creator-branch sync commands:
  - `git add <files>`
  - `git commit -m "<message>"`
  - `git push origin <feature-worktree-branch>`
  - `git checkout <creator-branch>` (in primary worktree)
  - `git pull origin <creator-branch>`
  - `git merge --no-ff origin/<feature-worktree-branch>`
  - `git push origin <creator-branch>`
- Optional later promotion to `main`:
  - `git checkout main`
  - `git pull origin main`
  - `git merge --no-ff <creator-branch>`
  - `git push origin main`

## Conventions

- Keep edits narrow and feature-scoped.
- Follow existing command naming and folder structure.
- Prefer shared helpers under `lib/` for repeated logic.
- Keep UI labels and command behavior aligned.

## Git Worktree Manager Style

- Naming: create worktree folders as `C:\Users\RML\Documents\GitHub\EasyBIM.extension-<BranchName>`.
- Explicit-name override: if user provides a specific worktree name/path, create that exact folder and create/check out a branch with the exact same name as that worktree folder name.
- One branch per worktree: never keep the same branch checked out in more than one worktree.
- Branch conflict handling:
  - If a requested branch already has a worktree, reuse that worktree instead of creating a duplicate.
  - If that branch must move into primary, remove the secondary worktree and switch primary to that branch.
- Do not auto-generate suffix branch names (for example `*-Worktree-1`) when user requests an explicit worktree name.
- Primary branch movement:
  - No fixed fallback branch is enforced.
  - Primary branch switching is situational and user-directed.
- Bulk-create defaults:
  - Target all local non-`main` branches by default.
  - Skip branches already represented by same-branch worktrees.
  - If target path exists but is not a registered worktree, classify it as `path conflict`, skip it, and report it.
- Deletion defaults:
  - Prefer `git worktree remove <path>` first.
  - Use `git worktree remove --force <path>` only when complete deletion is explicitly requested and local edits may exist.
  - If folder deletion fails because it is locked/in use, report it and provide a retry command after handles are closed.
  - Run `git worktree prune` after removals.
- Orphan handling:
  - If a folder exists on disk but is absent from `git worktree list`, treat it as an orphan worktree folder.
  - Remove orphan folders only when explicitly requested.

## Decision Log

| Date | Decision | Why | Impact | Status |
|------|----------|-----|--------|--------|
| 2026-03-02 | Standardize repository workflow source on `AGENTS.md` | Align global hub + parent skill governance | Enables reliable drift checks and skill maintenance | Active |
| 2026-03-04 | Standardize Git worktree manager style rules | Preserve consistent branch/worktree behavior across sessions | Reduces duplicate checkouts, path conflict churn, and deletion errors | Active |
| 2026-03-04 | Adopt standard promotion pipeline (`branch -> primary worktree branch -> main`) | Ensure predictable delivery from worktrees to production branch | Reduces missed merges and branch drift | Active |
| 2026-03-04 | Standardize shorthand phrase `update feature & main worktree` | Reduce ambiguity and typing for routine promotion requests | Faster communication and fewer git wording mistakes | Superseded |
| 2026-03-04 | Enforce explicit-name worktree/branch parity | Match user-provided worktree names exactly and prevent unwanted auto-suffix branches | Eliminates naming drift for manually named worktrees | Active |
| 2026-03-04 | Standardize 3-stage promotion path (`feature branch -> Temp-Phase-and-View-2 -> main`) | Ensure primary worktree always stages feature deliveries through the creator branch before production merge | Improves promotion traceability and keeps primary integration flow consistent | Superseded |
| 2026-03-13 | Rename shorthand to `sync worktrees` | Match revised parallel worktree terminology | Clarifies shorthand request handling across repo docs and skill mirrors | Active |
| 2026-03-13 | Make creator-branch sync the default worktree handoff | Ensure the primary branch refreshed depends on the branch that created the feature worktree | Removes hardcoded staging-branch assumptions and separates optional `main` promotion | Active |
| 2026-08-02 | Rename ribbon panel `Print.panel` to `Sheet.panel` | Panel now hosts sheet management beyond printing (Sheet Manager) | Panel title follows folder name; only test path references needed updates | Active |
| 2026-08-02 | Extract shared revision helpers to `lib/easybim/sheet_revisions.py` | Add/Remove Revision fallback chains and cloud hide/unhide were duplicated at module level in Revision Manager scripts and un-importable | Sheet Manager reuses one tested implementation; Revision Manager scripts intentionally unchanged (optional migration later) | Active |
| 2026-08-02 | Sheet Manager stages all edits and commits only via Apply Changes | Single assimilated undo step, red-preview of every pending change, safe two-phase renumbering | All model writes flow through `sheet_manager_revit.apply_staged_changes`; cloud-driven revision unchecks route through consolidated hide/unhide confirmations | Active |
| 2026-08-02 | Sheet Manager Excel round-trip uses `easybim.excel_workbook` + hidden `_metadata` keys | Reuse the shared OOXML reader (no Excel install needed); `p:`/`tb:`/`rev:<id>` column keys keep round-trips unambiguous | Import matches by ElementId (number rescue only for stale ids); missing-id rows create sheets, duplicate numbers are red-flagged and skipped | Active |
| 2026-08-02 | Promote `View Template.pulldown` to a single `View Template.pushbutton` | Pulldown held one command; the reshaped tool covers view and view-template transfer in both directions | Ribbon icon-mirroring aliases retarget to the new title; old bundle paths removed | Active |
| 2026-08-02 | View Template transfer engine: keep the non-controlled sandwich for template targets, restrict-the-source (or a temp `CreateViewTemplate` template for view sources) for view targets | Views have no non-controlled parameter set, so the target-side sandwich cannot work on them; the sandwich is production-tested for templates | All writes flow through one assimilated `TransactionGroup`; temp templates never persist; per-target rollback isolation retained | Active |
| 2026-08-02 | Selective Transfer V/G rows are tri-state masters; fully-checked groups ride the native parameter mechanism, partial groups are written per item and forced-controlled on template targets | Per-item granularity was user-confirmed; group-level bits (e.g. non-overridden category visibility) only travel via the native mechanism; partial items on a template are inert unless the group is controlled | Nothing transfers unless explicitly checked (all selections default off) | Active |
| 2026-08-02 | Clash Detection Mode is forward-only: only elements changed after Start are ever tested | Gives the user "the clash you just made" without a whole-project pass; also why Start is instant and pre-existing clashes never appear | No baseline scan exists; a clash between a changed element and an untouched one still reports | Active |
| 2026-08-02 | Clash Detection subscribes `DocumentChanged` + `Idling` directly on Start and detaches on Stop, instead of adding pyRevit hook files | Hook files fire a script on every event for every session; direct delegates cost nothing while the mode is off and skip engine spin-up while it is on | `DocumentChanged` does set unions only; all geometry, popups and Show requests run from the budgeted Idling pass | Active |
| 2026-08-03 | All per-Idling work runs from one `Idling` delegate installed by `startup.py` (`lib/easybim/idling.py`); the `app-idling` hook file is deleted | Extends the Clash Detection decision below to the rest of the extension: pyRevit re-reads and recompiles a hook script on every Idling event (file read + compile + fresh scope + `ScriptRuntime` per tick, no compiled-code cache), which Revit raises continuously all session | Consumers get the delegate's own `sender` (`EXEC_PARAMS` returns stale data outside a script run); the delegate guards `SystemExit` as well as `Exception`, holds a re-entrancy flag for consumers that open modals, and detaches before the auto-update in case it reloads pyRevit; the envvar mirror is mandatory since pyRevit detaches its own hooks on reload but never a raw delegate | Active |
| 2026-08-02 | Clash Detection writes nothing to disk; all state is module globals mirrored to pyRevit envvars | User required no local file that grows, and the repo already deleted a debug log for the same reason | Stop has no cache to delete; `test_clash_detection_no_local_files.py` fails the build if a file API appears in any clash module | Active |
| 2026-08-02 | Clash Detection pushbutton sets `__persistentengine__ = True` (repo's first use) | The engine holds live Revit event delegates after the command returns; a recycled pyRevit engine would silently stop detection | Enforced by `test_clash_detection_command_names.py`; any future event-owning button needs the same flag | Active |
| 2026-08-02 | Dockable pane registered unconditionally from `startup.py`, with a modeless right-edge window as fallback | Revit only accepts `RegisterDockablePane` during app init, so it cannot be deferred to first use; pyRevit dockable-panel support could not be verified off-Revit | Registration is hidden and free; `clash_detection_panel` degrades instead of failing when the API is absent | Active |
| 2026-08-02 | Clash Detection queries return `(ids, completed)`; resolution runs only on completed queries and never on a pair recorded in the same pass | A bare `[]` meant "no clash", "no bounding box" and "the query threw" all at once, so one work item deleted the clash another had just found - which is why moved and copied elements went unreported | Fixes the create-vs-edit asymmetry; enforced by `test_clash_detection_state.SamePassProtectionTests` and a source check in `test_clash_detection_no_local_files` | Active |
| 2026-08-02 | Group/AssemblyInstance ids expand to their members instead of being dropped | Revit reports the container, whose category is on neither side, so a moved or pasted group vanished silently | Members flow through the normal queue; nested containers expand on their own turn | Active |
| 2026-08-02 | Pause does not queue edits; Resume re-validates the recorded pairs instead of replaying them | Paused should cost what off costs, and a long pause must not bank a backlog that floods Resume | One primitive (`_revalidate_pairs`) serves both Resume and Edit Categories, budgeted through the normal per-tick limits | Active |
| 2026-08-02 | Edit Categories rebuilds the side contexts in place rather than restarting the session | User asked to keep clashes that are still clashing; a restart would discard unreviewed work | `ElementInfo` carries `category_id` so scope is checked without re-reading elements | Active |
| 2026-08-02 | Ribbon state badge is drawn over the button's live image, not swapped for pre-baked icons | Keeps the dot correct in both Revit themes and survives pyRevit picking the icon variant at load time | No extra PNGs; `clash_detection_ribbon` restores the original image on stop | Active |
| 2026-08-02 | Clash setup UI moved from the pushbutton folder into `lib/easybim` | The dockable panel offers Edit Categories and cannot import upward out of `lib/` | `script.py` is a thin launcher; XAML lives with the other panels in `lib/easybim/ui/` | Active |
| 2026-08-03 | Clash Detection running flag mirrored into a pyRevit envvar | A recycled engine lost the module global while the .NET delegates stayed attached, so the mode kept detecting with every window reporting it off and no way left to stop it | `is_active()` survives reloads; `stop()` detaches through the envvar even when the session is gone; `has_live_session()` distinguishes the two | Active |
| 2026-08-03 | One main window: the ribbon button always opens the category window, which carries the live session controls | A closed panel was unrecoverable and Stop had no permanent home; a separate status window would have been unreachable once the ribbon opened the main window | `clash_detection_status.py` and its XAML removed as superseded; Open Panel / Pause / Resume / Stop live on the main window | Active |
| 2026-08-03 | Clash rows carry a checkbox per element, not per pair | A clash names two elements and the user often wants only one of them; Show now takes element keys | `request_show` takes element keys; `_show_elements` replaces the pair-based version | Active |
| 2026-08-03 | Clash Detection drops its own `easybim.clash_detection*` modules on launch when no session is running | `__persistentengine__` keeps `sys.modules` alive across a pyRevit reload (that is what keeps the event delegates alive), so the documented "pull and reload" update flow kept running old code until Revit restarted | Updates land on the next click; the live pane and its rows are mirrored to envvars so a re-imported module still drives the pane Revit already created | Active |

## Session Handoff Log

| Date | What Changed | Files Touched | Checks Run | Next Step |
|------|---------------|---------------|------------|-----------|
| 2026-03-02 | Created baseline AGENTS governance file | `AGENTS.md` | None | Add new decision/handoff rows after merged workflow changes |
| 2026-03-04 | Added Git Worktree Manager Style governance and mirrored rule intent for skills | `AGENTS.md`, `C:\Users\RML\.codex\skills\rml-repo-easybim-extension\SKILL.md` | `rg` keyword checks, focused `git diff`, `git status --short` scope check | Continue applying these defaults for all EasyBIM worktree operations |
| 2026-03-04 | Added documented standard git promotion flow and linked `skills.md` process | `AGENTS.md`, `skills.md` | Doc-only update | Follow this process for routine feature promotion |
| 2026-03-04 | Added shorthand workflow command definition and default semantics | `AGENTS.md`, `skills.md` | Doc-only update | Superseded by `sync worktrees` wording |
| 2026-03-04 | Added explicit-name worktree/branch parity rule and removed auto-suffix creation behavior | `AGENTS.md`, `C:\Users\RML\.codex\skills\rml-repo-easybim-extension\SKILL.md` | Worktree recreate + doc sync + status verification | Keep explicit worktree names and branch names identical when requested |
| 2026-03-04 | Standardized primary-worktree 3-stage promotion flow via `Temp-Phase-and-View-2` before `main` | `AGENTS.md`, `skills.md`, `C:\Users\RML\.codex\skills\rml-repo-easybim-extension\SKILL.md` | Doc update for workflow governance | Superseded by creator-branch sync default |
| 2026-03-13 | Replaced fixed staging-branch shorthand with creator-branch `sync worktrees` semantics | `AGENTS.md`, `skills.md`, `C:\Users\RML\.codex\skills\rml-repo-easybim-extension\SKILL.md` | Targeted doc sync across root repo, mirrored skill, and worktree copies | Keep `main` promotion as a separate explicit step |
| 2026-08-02 | Added Sheet Manager bundle (staged grid editor: per-revision checkbox columns, sheet/TB param editing, load sheet list/print set, revision+parameter filters, sort, copy sheet info, search & replace, save print set, Excel round-trip) and renamed `Print.panel` to `Sheet.panel` | `EasyBIM.tab/Sheet.panel/Sheet Manager.pushbutton/*`, `lib/easybim/sheet_revisions.py`, `Development Space/tests/test_sheet_manager_*.py`, `test_sheet_revisions.py`, `test_print_set_command_names.py`, `AGENTS.md` | `python -m pytest "Development Space/tests"` (277 passed), `python -m compileall EasyBIM.tab lib`, XAML parse tests | In-Revit verification of Phase-1 exit criteria (XamlReader templates + Reactive rows), then user feedback on pinned-element handling |
| 2026-08-02 | Reshaped Batch Transfer View Template Settings into the `View Template` pushbutton: view-or-template source radios (active view preselected), view-or-template target radios with template-locked views greyed, `Transfer All`, and a `Selective Transfer` dialog with per-item V/G Overrides edit windows (read-only source values, all selections off by default); post-review hardening: RVT Links V/G group gated to Revit 2024+ (2023 shows an unsupported hint), indeterminate Include click selects all instead of clearing, V/G rows detected via `VIS_GRAPHICS_*` BuiltInParameter ids as localization fallback, transfer summary zeroed on group rollback and item notes split from skipped-target counts, target count refreshes on direct checkbox clicks | `EasyBIM.tab/Views.panel/View Template.pushbutton/*`, `lib/easybim/view_template_ribbon.py`, `Development Space/tests/test_view_template_transfer_state.py`, `test_view_template_ribbon.py`, `AGENTS.md` | `python -m pytest "Development Space/tests"` (304 passed), `python -m compileall EasyBIM.tab lib`, XAML XML parse check, adversarial multi-agent review (9 findings confirmed and fixed or gated) | In-Revit smoke test: ribbon icon aliases, XAML load, tri-state Include cycle, `CreateViewTemplate` return/rollback behavior, selective apply to plain views, `GetLinkOverrides` id kind, import-category enumeration, partial-group writes propagating through template-controlled views |
| 2026-08-02 | Added Clash Detection Mode: live forward-only interference checking. Native-Interference-Check-shaped setup window with Extended multi-select category lists (shift/drag/spacebar bulk ticking), `Start Ongoing Detection Mode` replacing OK, host + linked-model `Categories from` sources, Silent Mode driving a right-side dockable pane, non-modal alert window otherwise, `Show` on every row, `Stop Detection` on both surfaces. Engine subscribes `DocumentChanged` (set unions only) + `Idling` (0.35s debounce, 25-element/40ms budget), quick-filters before exact filters, solid-transform path for links, bounded re-sweep on link-instance moves, memory-only state with hard caps | `EasyBIM.tab/Misc Tools.panel/Clash Detection Mode.pushbutton/*`, `lib/easybim/clash_detection_{state,engine,panel,alert}.py`, `lib/easybim/wpf_notify.py`, `lib/easybim/ui/clash_detection_*.xaml`, `startup.py`, `hooks/doc-closing.py`, `Development Space/tests/test_clash_detection_*.py`, `Development Space/docs/superpowers/specs/2026-08-02-clash-detection-mode-design.md`, `AGENTS.md` | `python -m pytest "Development Space/tests"` (409 passed), `python -m compileall "EasyBIM.tab" lib hooks startup.py`, XAML XML parse checks | In-Revit smoke test: pyRevit `register_dockable_panel` availability and right-side docking, `__persistentengine__` keeping the delegates alive, `INotifyPropertyChanged` bulk-tick refresh, link solid-transform accuracy, `Selection.SetReferences` on 2023+ vs the zoom fallback, and responsiveness while moving ~500 elements |
| 2026-08-02 | Clash Detection round 2. Fixed the bug that lost clashes created by a move or a copy (empty query result read as "resolved"; a pair could be deleted in the pass that found it) and the silent drop of grouped/pasted containers. Added Pause/Resume with pair re-validation, a status window on the ribbon button, a green/amber ribbon badge, Edit Categories from the panel, row checkboxes with one Show button acting on every ticked clash, and raised `MAX_QUEUE` to 20000 so a large paste is checked end to end | `lib/easybim/clash_detection_{state,engine,panel,alert,setup,status,ribbon,revit}.py`, `lib/easybim/ui/clash_detection_*.xaml`, `EasyBIM.tab/Misc Tools.panel/Clash Detection Mode.pushbutton/script.py`, `Development Space/tests/test_clash_detection_*.py`, `Development Space/docs/superpowers/specs/2026-08-02-clash-detection-mode-design.md`, `AGENTS.md` | `python -m pytest "Development Space/tests"` (448 passed), `python -m compileall "EasyBIM.tab" lib hooks startup.py`, XAML XML parse checks | In-Revit: confirm a move and a multi-element paste now report and keep their clashes (watch the `resolved` counter - it must not tick up on a move that created one), grouped/pasted groups detected, badge colours, Resume re-check, Edit Categories keeping in-scope clashes, and ticked-rows Show framing them together |
| 2026-08-03 | Clash Detection round 3. Fixed the reason the mode could not be stopped once the panel was closed: the running flag was a bare module global, so a recycled pyRevit engine reported the mode off while its handlers stayed attached. Merged the status window into the main window, which the ribbon button now always opens and which carries Open Panel / Pause / Resume / Stop plus live state. Gave every clash a checkbox per element instead of one per pair. Reworded the main description to "Watch for clashes created while you keep modeling." and dropped the "Tick at least one category" hint | `lib/easybim/clash_detection_{engine,state,panel,alert,setup}.py`, `lib/easybim/ui/clash_detection_{setup,panel,panel_window,alert}.xaml`, `EasyBIM.tab/Misc Tools.panel/Clash Detection Mode.pushbutton/{script.py,bundle.yaml}`, removed `lib/easybim/clash_detection_status.py` + xaml, `Development Space/tests/test_clash_detection_*.py`, spec, `AGENTS.md` | `python -m pytest "Development Space/tests"` (450 passed), `python -m compileall "EasyBIM.tab" lib hooks startup.py`, XAML parse + handler-resolution checks | In-Revit: close the panel and confirm the ribbon button reopens it and can Stop; tick individual elements and confirm Show frames exactly those; confirm the session strip appears whenever a session is running, including after a pyRevit reload |
| 2026-08-03 | Fixed why an extension update showed no UI change: `__persistentengine__` (this repo's only use, added for Clash Detection) keeps the engine's `sys.modules` across a pyRevit reload, so updated modules were never re-imported. The command now drops its own modules on launch when no session is live, and the panel mirrors its view/row collection through pyRevit envvars so a re-imported module keeps driving the pane Revit already built. Added a `Build <timestamp>` stamp on the main window, read from file mtimes, so "did my update land?" is answerable at a glance | `EasyBIM.tab/Misc Tools.panel/Clash Detection Mode.pushbutton/script.py`, `lib/easybim/clash_detection_{setup,panel}.py`, `lib/easybim/ui/clash_detection_setup.xaml`, `README.md`, `Development Space/tests/test_clash_detection_no_local_files.py`, `AGENTS.md` | `python -m pytest "Development Space/tests"` (452 passed), `python -m compileall "EasyBIM.tab" lib` | In-Revit: pull the update, reload pyRevit (no restart), open the tool and confirm the Build stamp matches the pull and the session strip/description are present |
