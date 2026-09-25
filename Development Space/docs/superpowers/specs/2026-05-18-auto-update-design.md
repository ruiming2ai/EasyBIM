# EasyBIM Auto Update Design

Original date: 2026-05-18. Revised: 2026-09-25.

## Contract

Auto Update verifies and updates the repository containing this EasyBIM
installation. It never updates pyRevit core or enumerates unrelated extension
repositories. A shared repository necessarily advances all its files, including
any sibling extensions in that repository.

Two independent facts govern the result:

- **Files are current:** a successful fetch of the configured upstream, followed
  by matching local and upstream commits in a clean checkout.
- **This session is current:** that verified commit matches the commit recorded
  when this Revit process loaded EasyBIM.

No network failure, unknown metadata, incomplete pull, or unchanged local commit
is treated as proof of freshness. Every manual click receives a result. Startup
failures are logged without dialogs; a startup pull that installs changes shows
a notification before reloading.

## Repository and update flow

1. Guard against process-local reentry, including modal-dialog callbacks. Acquire
   the named cross-process mutex for both manual and startup updates. A busy or
   unavailable lock prevents the update; a manual click explains why.
2. Discover the nearest Git repository from the actual extension directory using
   `pyrevit.coreutils.git.libgit.Repository.Discover()` and `git.get_repo()`.
   Normalize directory comparisons and validate that the repository contains the
   extension. Exclude the core repository using
   `pyrevit.versionmgr.get_pyrevit_repo()` and the pyRevit home directory.
3. Reject dirty/conflicted, detached, or untracked-branch checkouts. Do not reset,
   stash, switch branches, or overwrite local work.
4. Call `updater.get_updates(repo_info)` and require explicit success. This uses
   pyRevit's saved credentials and fetches the tracked remote. Do not call the
   global connectivity/pending-update check or enumerate other extensions.
5. Reopen only EasyBIM's repository and compare its commits. A changed local
   branch, upstream, or commit during the fetch invalidates the operation.
   If commits differ, require a readable divergence with zero local commits and
   positive upstream commits. Local-only or divergent commits require resolution.
6. Pull only when behind using `updater.update_repo(repo_info)`. Consume the new
   `RepoInfo` returned by that call; the input wrapper retains its old hash.
   Reopen EasyBIM alone and verify its clean checkout, unchanged branch/upstream,
   returned commit, and fetched upstream commit. A nonthrowing incomplete or
   conflicted pull is still a failed update.
7. Release the mutex before dialogs and reload. Keep the process-local reentry
   guard active until the operation finishes.

Git operations and authentication remain pyRevit's responsibility. There is no
shell Git dependency, custom merge implementation, or update-everything fallback.
The shared implementation uses APIs inspected in pyRevit 4.8.16, 5.3.1, and 6.5.5,
with IronPython-compatible Python syntax and no Revit-year-specific branch.

## Loaded revision and startup lifecycle

`startup.py` calls `record_loaded_revision()` on **every** extension load before
`queue_startup_auto_update()`. The baseline stores normalized repository identity
and commit in `EASYBIM_AUTO_UPDATE_LOADED`, using pyRevit's process-local envvars.
This is a local read with no network work. Unknown or dirty files invalidate the
baseline instead of preserving an old claim about what was loaded.

The once-per-process attempted guard remains independent of that baseline. The
first Idling tick consumes the pending flag and runs the update. The Idling
dispatcher detaches around the operation because reloading can replace its engine.
A delegate registered by the new startup is preserved; the old runtime restores
its subscription only when no replacement was installed.
A manual click also consumes pending startup work so it cannot trigger a second
update. The attempt guard must be persisted before any reload.

A verified checkout reloads when its commit differs from this process's baseline,
or when the current pull installed changes. The latter can establish a baseline
in a session started before revision tracking existed. Otherwise, a missing or
mismatched repository baseline reports that the files are verified but session
freshness is unknown; the user reloads pyRevit or restarts Revit once.

A successful reload must confirm the expected baseline through extension startup.
A throwing reload, unavailable reload API, or missing confirmation is reported as
incomplete application of verified files. Restore the previous baseline on a
failed reload so later checks do not silently treat the old session as current.

## Interfaces and messages

Existing public entry points remain `run_startup_auto_update()`,
`run_manual_auto_update()`, and the startup queue/guard helpers. Results preserve
`status`, `trigger`, and `updated_repos`, and add `verified`, `reload_status`,
`repo_key`, `branch`, `upstream`, `before_head`, `after_head`, `upstream_head`, and
`message` for explicit verification and reload outcomes.

Manual results distinguish:

- installed changes and reloading;
- verified files already installed, but this session requires reloading;
- verified files and session already current, without reloading;
- verified files but unknown loaded version;
- busy, invalid checkout, verification failure, pull failure, or reload failure.

Messages include the branch and short local/upstream commit identifiers when
available. Raw native exceptions are not echoed because they may contain
credentials in authenticated URLs. Startup failures use pyRevit debug logging.

## Validation

The focused suite exercises immutable native-style `RepoInfo` snapshots, returned
pull metadata, remote fetch failure, incomplete/nonthrowing conflicted pulls,
local changes and divergence, branch races, direct discovery and core exclusion,
Windows path normalization, mutex cleanup and reentry, independent loaded-version
state, repeated clicks, reload restoration, and the startup queue lifecycle.
The adjacent Idling and startup-reentrancy suites must also pass.

Live acceptance remains a separate check in Revit 2024 and an available newer
version: one commit behind, already current, two sessions sharing a checkout,
network failure, and a second extension with pending updates left untouched.
Passing off-host tests does not establish live Revit compatibility.

## History and references

The original 2026-05-18 implementation wrapped pyRevit's global update command.
The 2026-08-25 revision scoped pulls to EasyBIM but depended on global repository
snapshots and only compared the local commit before/after pulling. That could
silently omit a reload and could not recognize files updated by another process.
This revision replaces those assumptions with remote and session verification.

- [pyRevit updater API](https://docs.pyrevitlabs.io/reference/pyrevit/versionmgr/updater/)
- [pyRevit Git wrapper](https://docs.pyrevitlabs.io/reference/pyrevit/coreutils/git/)
