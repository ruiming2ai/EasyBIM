# EasyBIM Auto Update Design

Original date: 2026-05-18. Revised: 2026-09-30.

## Contract

Auto Update verifies and updates the repository containing this EasyBIM
installation. It never updates pyRevit core or enumerates unrelated extension
repositories. A shared repository necessarily advances all its files, including
any sibling extensions in that repository.

Two independent facts govern the result:

- **Files are current:** a successful fetch of the configured upstream, followed
  by a clean checkout containing all upstream commits and matching its file tree.
  Identical commit IDs establish both; extra local history requires separate
  ancestry and tree checks.
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
   Inspect status with `repo.RetrieveStatus(git.libgit.StatusOptions())`:
   the zero-argument form is a C# extension method unavailable as an instance
   method in IronPython. Inspection failures report the operation and exception
   type without exposing the native exception body.
4. Call `updater.get_updates(repo_info)` and require explicit success. This uses
   pyRevit's saved credentials and fetches the tracked remote. Do not call the
   global connectivity/pending-update check or enumerate other extensions.
5. Reopen only EasyBIM's repository and compare its commits. A changed local
   branch, upstream, or commit during the fetch invalidates the operation.
   If commits differ, require readable ahead/behind counts. Extra local history
   is safe when its HEAD tree matches the upstream tree (nothing behind), or
   matches their common ancestor's tree (behind upstream). Read the common
   ancestor with the instance API `repo.ObjectDatabase.FindMergeBase(head, upstream)`.
   This admits update-generated merge history without admitting committed file
   edits. Missing common history or unreadable trees cannot authorize a pull.
6. Pull only when behind using `updater.update_repo(repo_info)`. Consume the new
   `RepoInfo` returned by that call; the input wrapper retains its old hash.
   Reopen EasyBIM alone and verify its clean checkout, unchanged branch/upstream,
   returned commit, and fetched upstream. A native merge can return a different
   commit ID from upstream: accept it only with zero upstream commits missing
   and a matching tree. Matching files alone do not prove upstream history was
   incorporated. A nonthrowing incomplete or conflicted pull is still a failure.
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

The once-per-process attempted guard remains independent of that baseline.
A busy mutex does not consume the startup attempt: the pending job is requeued
with a ten-second backoff, rather than requiring a manual click. The
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
freshness is unknown on a manual request. A fresh startup with a missing baseline
instead performs one guarded reload after successful remote verification: another
process may have been updating the checkout while startup tried to record it.

A successful reload must confirm the expected baseline through extension startup.
A throwing reload, unavailable reload API, or missing confirmation is reported as
incomplete application of verified files. Restore the previous baseline on a
failed reload so later checks do not silently treat the old session as current.

## Automatic refresh of already-open sessions

The existing single Idling dispatcher checks the installed EasyBIM HEAD at most
once per ten seconds, even after its startup check has completed. Unchanged
revisions require no network, working-tree scan, reload or delegate replacement.
All native repository handles opened by this polling path are disposed.

When another session (or the built-in updater) advances the shared checkout, the
receiving process acquires the same named mutex, checks the working tree, and
verifies the installed HEAD against the **cached** upstream history/tree. This
is local-installation verification, not a claim that GitHub was just checked.
It never fetches, pulls, resets or modifies files. Dirty, detached, untracked,
unreadable, incomplete or privately modified checkouts are left alone. Harmless
merge history remains supported. Busy receivers retry on a later idle pass.

A verified change triggers a quiet reload on Revit's Idling thread, without a
confirmation dialog. The writer mutex remains held through this local reload;
the startup/manual paths still release it before their existing dialogs. The
process-local reentry guard remains active. Detaching before reload and keeping
the delegate installed by the new engine avoids duplicate or dead callbacks.
Disposable e-transmit worker processes remain excluded by the existing dispatcher.

A reload failure preserves the old baseline and suppresses automatic retries for
that same target revision, avoiding a reload loop. A different revision, explicit
manual update, or successful reload can retry. Successful baseline recording
clears the failed-revision marker.

This applies to Revit versions/sessions sharing the **same installation on disk**.
It does not broadcast to independent clones or other computers. A minimized/busy
Revit session refreshes when it next receives Idling; ten seconds is a throttle,
not a real-time delivery guarantee. Sessions still executing the pre-fix code
need one reload or restart to install the observer before future updates propagate.

## Interfaces and messages

Existing public entry points remain `run_startup_auto_update()`,
`run_manual_auto_update()`, and the startup queue/guard helpers. Results preserve
`status`, `trigger`, and `updated_repos`, and add `verified`, `reload_status`,
`repo_key`, `branch`, `upstream`, `before_head`, `after_head`, `upstream_head`,
`history_ahead`, `verification_source`, and `message` for explicit verification and reload outcomes.
`history_ahead` counts extra commits retained in a verified checkout; the manual
current-version message explains that matching published files are installed
and the extra history was preserved.

`verification_source` distinguishes a remote fetch from a local checkout check.
The dispatcher also uses `has_pending_session_refresh()` and
`run_pending_session_refresh()` after the startup job has finished.

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
local changes and divergence, extra history with matching published files,
subsequent updates and repeated clicks after a native-style merge, missing
ancestry/tree metadata, branch races, direct discovery and core exclusion,
Windows path normalization, mutex cleanup and reentry, independent loaded-version
state, repeated clicks, reload restoration, and the startup queue lifecycle.
The adjacent Idling and startup-reentrancy suites must also pass.
The status test double requires a `StatusOptions` argument, matching the .NET
instance overload. Native smoke checks use actual IronPython 2.7.12 and
LibGit2Sharp 0.31.0 assemblies to check clean/dirty worktrees and the startup
record/queue/fetch path against a disposable local upstream.
History smoke checks also exercise actual native merge commits, consecutive
updates, and protection of committed file edits with those assemblies.

`test_auto_update_sessions.py` drives the real dispatcher with API-shaped host
fixtures: cross-session refresh, offline operation, contention/backoff, startup
baseline recovery, repeated revisions, safe reload lifecycle, cleanup, and local
edit protection. `run_auto_update_native.ps1` runs the current source in Windows
IronPython 2.7.12 with LibGit2Sharp 0.31.0, exercising real local remotes, merges,
status calls, a competing-thread named mutex, and independent session state.
Only Revit/pyRevit session services are substituted in that native test.

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
