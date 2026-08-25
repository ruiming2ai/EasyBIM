# EasyBIM Auto Update Design

Date: 2026-05-18
Status: **Superseded 2026-08-25** - scoped to EasyBIM's own repository. The
original "wrap native pyRevit Update" decision is recorded below under
*Superseded decision* for history.

## Summary

EasyBIM Auto Update keeps **the EasyBIM extension itself** up to date. It does
not touch pyRevit core or any other extension.

On each new Revit session, EasyBIM should:

- run once during extension startup
- pull only the repository that holds this extension
- reload pyRevit **only when that repository actually moved**
- show a concise popup only when the pull installed changes
- own no git code of its own: the pull, the credentials and the repository
  discovery all stay pyRevit's

The `Auto Update` button under `EasyBIM.tab/Misc Tools.panel` does the same on
demand.

## Product Decision

EasyBIM updates one repository - its own - through pyRevit's own per-repository
entry point, `pyrevit.versionmgr.updater.update_repo(repo_info)`, using
pyRevit's own `RepoInfo` objects. It never calls the update-everything entry
point.

Two rules follow, and both are enforced by tests:

- **Fail closed.** If the per-repository entry point is missing, if the
  repository cannot be identified, or if the pull raises, the tool does
  nothing. It must never widen its scope to compensate - updating everything is
  exactly what it exists to avoid.
- **Reload follows a real change.** An unchanged repository leaves the session
  alone. A reload request coming from pyRevit during the pull is still honoured.

### What this drops

EasyBIM Auto Update no longer updates pyRevit core, nor any other extension -
**including extensions My Ribbon installed**. Those come from pyRevit ▸ Update,
which is unchanged. Pulling every repository on the machine and forcing a reload
on every session is more authority than one extension's button should hold.

### Identifying our own repository

The extension root is derived from this module's own path
(`<EXT_ROOT>/lib/easybim/auto_update.py`) and matched against
`repo_info.directory`:

- equal, or an ancestor - pyRevit discovers a repository by walking *up* from
  the extension folder, so in a checkout holding several `*.extension` folders
  the repository directory is a parent of ours
- deepest match wins, so a nested extension repository beats its container
- matched on directory only, never on name: a stranger's extension called
  EasyBIM must not match
- normalised for case, separators and the trailing separator LibGit2Sharp puts
  on a working directory; the prefix test is component-safe, so `.../Easy`
  never matches `.../EasyBIM`
- candidates come from `get_thirdparty_ext_repos()` when available, and the
  clone reported by `get_pyrevit_repo()` is excluded - otherwise an EasyBIM
  installed inside pyRevit's own clone would make us pull pyRevit itself

**Monorepo caveat, inherent to git:** if EasyBIM shares a repository with
sibling extensions, one pull necessarily advances them too. "EasyBIM only"
means "one repository only", which is as close as git allows.

## Superseded decision (2026-05-18 - 2026-08-25)

> EasyBIM does not implement its own extension updater.
>
> The implementation calls `pyrevit.versionmgr.updater.update_pyrevit()`,
> matching the native pyRevit `Update.smartbutton` script. EasyBIM keeps only a
> small session guard so startup auto update does not loop after pyRevit
> reloads.
>
> EasyBIM may observe pyRevit-managed repo metadata before and after native
> Update to decide whether to show a popup. It must not perform its own update
> operation.

Reversed because the button reads as "update EasyBIM" but pulled every enabled
extension on the machine and reloaded whether or not anything relevant changed.
The replacement still owns no git code: it calls pyRevit's own per-repository
pull.

## Components

### `startup.py`

- runs when pyRevit loads `EasyBIM.extension`
- checks the session guard
- queues the update for the first Idling tick (`queue_startup_auto_update()`),
  so git and network work never block Revit's startup thread

### `lib/easybim/auto_update.py`

- owns the once-per-session guard helpers and the cross-process startup mutex
- exposes `run_startup_auto_update()` and `run_manual_auto_update()`
- identifies the repository holding this extension (`_find_own_repo`)
- pulls that one repository through `updater.update_repo(repo_info)`
- re-reads the repository head afterwards and shows a popup only if it moved
- temporarily captures pyRevit reload requests, so the popup comes first and
  the reload decision stays ours

### `Auto Update.pushbutton`

- calls `run_manual_auto_update()`
- adds no update handling of its own
- a click always gets an answer: fail-closed outcomes are reported to the user,
  where the startup path stays silent about them

## Data Flow

Startup:

1. Revit starts; pyRevit loads EasyBIM.
2. `startup.py` checks the session guard and queues the update.
3. The first Idling tick detaches the delegate and runs the guarded update.
4. `check_for_updates()` gates the run; anything but `True` stops here.
5. EasyBIM finds its own repository, or stops (silently, at startup).
6. EasyBIM pulls that repository with `updater.update_repo()`.
7. It re-reads the head. Unchanged: nothing more happens - no popup, no reload.
8. Changed: popup, then reload.
9. After a reload `startup.py` runs again and exits because the guard is set.

Manual: the same from step 5, without the guard, the mutex and the pre-check,
and with fail-closed outcomes shown to the user.

Popup body:

```text
EasyBIM Auto Update installed changes:

- EasyBIM

pyRevit is reloading.
```

No popup when the repository head did not move.

## Testing Strategy

- Unit-test the startup guard, the queue/pending flag and the mutex behaviour.
- Unit-test that only our repository is passed to `update_repo`, with other
  extensions present in the list.
- Unit-test that another extension moving is neither updated nor reported.
- Unit-test no popup and **no reload** when our head is unchanged.
- Unit-test popup then reload when our head moves.
- Unit-test that a reload requested by pyRevit is still honoured.
- Unit-test fail-closed paths: no `update_repo` on the updater, our repository
  absent, enumeration raising, the pull raising - each does nothing, reloads
  nothing, and is reported on the manual path but silent at startup.
- Unit-test the matcher directly: exact, ancestor, deepest-wins, trailing
  separator, shared-prefix sibling, empty directory, pyRevit core clone
  excluded, third-party enumerator preferred.
- Source-guard that `update_pyrevit` appears nowhere in `auto_update.py`.
- Syntax-check `startup.py`, the button script, and `lib/easybim/auto_update.py`.

## Verification still owed in Revit

The pyRevit updater API cannot be exercised off Revit, so these were not
verified before shipping:

- `updater.update_repo(repo_info)` exists and accepts a `RepoInfo`
- click with EasyBIM current: no popup, **no reload**
- click one commit behind: popup naming EasyBIM, then one reload
- a second extension with pending updates is left untouched, and pyRevit ▸
  Update still picks it up
- a non-git (zip) install: the manual click explains itself once, startup stays
  silent

## References

- pyRevit native Update smartbutton source: `extensions/pyRevitCore.extension/pyRevit.tab/pyRevit.panel/Update.smartbutton/script.py`
- pyRevit updater reference: <https://docs.pyrevitlabs.io/reference/pyrevit/versionmgr/updater/>
