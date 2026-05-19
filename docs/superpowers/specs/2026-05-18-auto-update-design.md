# RL Tools Auto Update Design

Date: 2026-05-18
Status: Proposed and approved in brainstorming

## Summary

Add an `Auto Update` command under `Misc Tools` and add extension startup automation for `RL_Tools.extension`.

On each Revit program launch, RL Tools should:

- run once per Revit session during extension startup
- target only the installed `RL_Tools.extension` repo
- force that repo to match `origin/main`
- reload pyRevit only when the repo changed or had to be repaired
- stay silent when nothing changed
- show a concise message only when changes were applied or when a real update failure occurred

The `Auto Update` button should run the same workflow immediately on demand, outside the startup guard.

## Goals

- Keep installed RL Tools aligned with `origin/main` automatically at Revit startup.
- Remove dependence on users remembering to run manual update steps.
- Recover from local branch drift, divergence, or dirty working trees in the installed extension.
- Reuse one shared update path for both startup automation and the ribbon button.

## Non-Goals

- Do not update pyRevit core.
- Do not update any other third-party extension.
- Do not preserve local edits in the installed RL Tools extension folder.
- Do not run on every document open.
- Do not introduce a persistent user toggle. Startup behavior is always on by policy.

## Product Decisions

### Trigger

The automatic behavior runs from extension startup, not from document hooks.

Rationale:

- `doc-opened` would run on each file open, which does not match the requirement.
- pyRevit supports extension startup scripts through `startup.py`.

### Update Policy

The updater always forces the installed repo to `origin/main`.

This policy intentionally differs from native pyRevit update behavior. Native pyRevit updates the repo's current tracked branch. RL Tools auto update must instead treat `origin/main` as the single deployment source.

### Dirty or Drifted Installations

If the installed repo is dirty, on the wrong branch, diverged, or otherwise drifted, the updater should hard-recover it to `origin/main`.

This is intentionally destructive and is part of the accepted product policy.

### UX

- Startup path: silent when no change is needed.
- Startup path: show a concise notice when changes were applied.
- Startup path: show a concise failure notice only for real update errors.
- Manual button: show a short result when no change is needed or when an error occurs.
- Manual button: if the repo changed or was repaired, prioritize immediate reload over showing a pre-reload result dialog.

## Architecture

### Components

### `startup.py`

Add an extension-root `startup.py` that pyRevit executes when the extension loads.

Responsibilities:

- check a once-per-session guard
- exit immediately if startup auto update already ran this Revit session
- mark the guard before running update logic
- call a shared helper in `lib/rltools/auto_update.py`

This file should stay small and avoid embedding git logic directly.

### `lib/rltools/auto_update.py`

Create a shared helper module that owns the full workflow.

Responsibilities:

- identify the installed RL Tools repo root
- confirm the repo is valid and has `origin`
- fetch remote state
- compare local installed state against `origin/main`
- detect drift conditions:
  - wrong branch
  - dirty working tree
  - divergence from `origin/main`
  - local HEAD behind `origin/main`
- force-align the repo to `origin/main` when needed
- decide whether pyRevit reload is required
- classify the outcome for startup and manual UX

Suggested public entry points:

- `run_startup_auto_update()`
- `run_manual_auto_update()`

Suggested internal helpers:

- `get_repo_context()`
- `check_update_state()`
- `force_sync_to_origin_main()`
- `maybe_reload_pyrevit()`
- `show_result_message()`

### `Auto Update.pushbutton`

Add a new pushbutton under `RL_Tools.tab/Misc Tools.panel`.

Responsibilities:

- call `run_manual_auto_update()`
- present a short result when the repo is already current or when the update fails

## Data Flow

### Startup Flow

1. Revit launches.
2. pyRevit loads `RL_Tools.extension`.
3. `startup.py` runs.
4. `startup.py` checks a session guard.
5. If already attempted this session, exit.
6. Otherwise mark the guard and call `run_startup_auto_update()`.
7. The helper fetches `origin` and evaluates local state against `origin/main`.
8. If the repo is already clean and aligned, exit silently.
9. If the repo is dirty, on the wrong branch, diverged, or behind, force-sync it to `origin/main`.
10. If files changed or repo state was repaired, reload pyRevit once.
11. After reload, `startup.py` runs again, sees the guard, and exits.

### Manual Button Flow

1. User clicks `Auto Update`.
2. The button calls `run_manual_auto_update()`.
3. The helper runs the same repo evaluation and sync workflow.
4. If no change is needed, show a short "already current" result.
5. If the repo changed or was repaired, reload pyRevit immediately.
6. If the update fails, show a concise error result.

## State Model

Use a session-scoped pyRevit env var as a guard so startup auto update runs once per Revit session.

Properties:

- set before update work starts
- survives the immediate pyRevit reload inside the same Revit session
- reset naturally when Revit closes and a new session starts

This guard prevents update-reload-startup loops.

## Repo Handling

The helper should treat the installed extension directory as the repo working tree and operate only on that repo.

Expected sequence:

1. Discover repo.
2. Fetch `origin`.
3. Resolve `origin/main`.
4. Inspect current branch, cleanliness, and local HEAD.
5. Decide whether the repo is already compliant.
6. If not compliant, force checkout/reset to `origin/main`.

Target compliance means:

- current branch is `main`
- local HEAD matches `origin/main`
- working tree is clean

If the repo cannot resolve `origin/main`, the update should fail with a user-visible error.

## Reload Rules

Reload pyRevit only when one of these is true:

- new commits were applied
- local dirty state was cleared by reset
- the branch was corrected to `main`
- divergence from `origin/main` was repaired

Do not reload when the repo was already clean and current.

## Error Handling

### Silent Skip Cases

These should not interrupt the user with a modal failure:

- no internet connection
- remote temporarily unavailable
- fetch reports no reachable update path but local install is still usable

Startup behavior for these cases:

- log diagnostic detail
- skip reload
- keep user on current installed version

### User-Visible Failure Cases

These should show a concise failure notice:

- repo is not valid git
- `origin` is missing
- `origin/main` can not be resolved
- hard reset or branch correction fails
- pyRevit reload call fails after a repo change

The visible message should stay short. Detailed diagnostics should go to logs/output.

## Testing Strategy

### Startup Cases

- Repo already on clean `main` and matches `origin/main`: no UI, no reload.
- Repo behind `origin/main`: update and reload once.
- Repo on wrong branch: switch/reset to `origin/main`, then reload once.
- Repo has uncommitted local edits: discard them, sync to `origin/main`, then reload once.
- Repo diverged from `origin/main`: hard reset to `origin/main`, then reload once.
- No internet: no modal failure, no reload.
- Reload after update: startup script runs again but exits because the session guard is set.

### Manual Cases

- Manual run when current: short success/status message, no reload.
- Manual run when behind or drifted: update and reload.
- Manual run when repo is invalid: concise error message.

## Implementation Constraints

- Keep startup bootstrap code minimal.
- Put update logic in shared library code, not inline in `startup.py`.
- Prefer pyRevit-supported reload behavior for the final refresh step.
- Keep the feature scoped to RL Tools only.

## Open Questions Resolved

- Should this use native pyRevit update directly? No. Native behavior follows the current tracked branch, while RL Tools must always force `origin/main`.
- Should it run on file open? No. It must run only when the Revit program starts and pyRevit loads the extension.
- Should startup behavior be user-toggleable? No. It is always on by policy.
- Should dirty local edits be preserved? No. They should be discarded by force sync.

## References

- pyRevit extension startup naming and discovery: `EXT_STARTUP_NAME = "startup"` and `PYTHON_EXT_STARTUP_FILE`  
  <https://docs.pyrevitlabs.io/reference/pyrevit/extensions/>
- pyRevit startup script execution entry point: `execute_extension_startup_script(...)`  
  <https://docs.pyrevitlabs.io/reference/pyrevit/loader/sessionmgr/>
- pyRevit updater behavior and reload flow  
  <https://docs.pyrevitlabs.io/reference/pyrevit/versionmgr/updater/>
- pyRevit git wrapper behavior for current-branch pull/fetch  
  <https://docs.pyrevitlabs.io/reference/pyrevit/coreutils/git/>
