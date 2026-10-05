# Auto Update cross-session review and repair

## Finding and scope

Updating files on disk and refreshing code already loaded by each Revit process
are different operations. The early whole-pyRevit updater reloaded more broadly;
restoring it would also restore unwanted authority over unrelated extensions.
The August 25 single-repository change tied reload to changes made by that call.
September 25 introduced verified per-session revisions, explicit IronPython
StatusOptions and file-neutral merge-history support. September 30 added a
shared-checkout observer but only dispatched it through natural Idling.

Reproduced failures in the current code:

- No natural Idling callback means no receiving-session observer invocation.
- A missing loaded-revision marker after startup permanently disables polling.
- One failed reload suppresses that revision indefinitely, even if transient.
- Native repository wrappers opened by fetch/pull verification are not disposed
  consistently. Cleanup now covers success, failure and rejected repositories.
- A second writer can change the checkout while a manual confirmation is open.
  Reload now reacquires the writer mutex and revalidates before reading files.

## Repair contracts

One low-frequency timer per Revit process only raises ExternalEvent. The event
runs the existing updater consumer in a safe Revit API context, not on a worker
thread. It does not run unrelated startup windows. Requests coalesce; old timers
are replaced on reload; disposal of an executing event waits for its callback
to return; ApplicationClosing stops the timer. No busy-spin Idling mode is used.

Unknown revisions recover only through a clean, tracked, published checkout and
a confirmed reload. Failures retain the previous loaded marker. Receiving-session
reloads retry after 60/120 seconds, capped at three attempts per revision; manual
retry or a new revision resets eligibility. Current sessions remain no-ops.

Preserved: EasyBIM-only updates, native IronPython StatusOptions, file-neutral
merge history, local edit protection, no hard reset/clean, branch ownership,
writer mutex, offline receiving-session refresh and once-per-session startup
remote checks. Coordination Review remains off by default; old test fixtures
were corrected to explicitly opt in where they test that feature.

## Validation

Failing regressions were run before implementation for missing wakeups, unknown
markers, permanent suppression, native handle cleanup and the confirmation race.
Tests cover callback/thread separation, pending coalescing, event rejection,
shutdown, replacement during execution, stale runtime callbacks, transaction
safety, offline refresh, lock contention and local edit protection.

Commands:

```text
python -B -m unittest discover -s "Development Space/tests" -p "test_auto_update*.py" -v
python -B -m unittest discover -s "Development Space/tests" -p "test_update_wakeup.py" -v
python -B -m unittest discover -s "Development Space/tests" -v
powershell -File "Development Space/tests/run_auto_update_native.ps1"
```

The Windows probe uses real IronPython 2.7, .NET timers/mutexes and LibGit2Sharp
repositories. UpdateWakeupHost.cs is an explicitly fake Revit API-shaped event
pump for managed-interface and thread tests. It is not Autodesk Revit. Actual
ribbon reload, modal timing and Revit-version behavior still require a desktop
Revit session; no off-host test is claimed to prove them.

## Deployment boundary

Each receiving Revit must load the repaired code once (restart/reload may be
needed for already-open broken sessions). Afterwards, shared-checkout changes
are observed without another button click. Separate installation folders are
not shared storage and are deliberately not mutated by this observer. A busy
Revit cannot be forced to reload safely until it provides an API cycle.

Last updater result is retained in EASYBIM_AUTO_UPDATE_LAST_RESULT; the existing
loaded-revision and retry markers remain process-scoped. The scheduler exposes
`easybim.update_wakeup.diagnostics()` for pending/active/error/counter inspection.

## Main-line updater history (including followed rename)

Historical subjects use [former tool name] where the brand was renamed; commit IDs are retained.

```text
51339d8 | 2026-09-30 | Restore automatic EasyBIM refresh across open Revit sessions
0fca590 | 2026-09-25 | Allow verified update merge history in EasyBIM Auto Update
690b1ce | 2026-09-25 | Fix IronPython status inspection blocking EasyBIM updates
d8edde2 | 2026-09-25 | Fix EasyBIM update verification and stale-session reloads
b2b0245 | 2026-08-25 | Update only the EasyBIM repository from Auto Update
8f5ba3d | 2026-08-03 | "Auto Update" - Move the deferred update onto the Idling delegate
58d4df3 | 2026-07-31 | "Main" - Defer Auto-Update off Revit Startup
3f54d24 | 2026-07-27 | Check pending updates before startup auto update
c97cb1e | 2026-07-27 | Add startup auto update process lock
1d72feb | 2026-07-27 | Update native auto update popup behavior
b4628ca | 2026-07-27 | "Auto Update" - Update Message
38999b2 | 2026-07-27 | "Auto Update" - Remove Git
8af7a43 | 2026-07-27 | "Main" - Rename Repo & Tool
8f76b15 | 2026-05-20 | Fix IronPython git detection for auto update
deeb420 | 2026-05-18 | Squashed commit of the following:
dfe1340 | 2026-05-18 | Add [former tool name] auto update workflow
```

The remaining branch-specific updater changes, the prior validation candidate,
and button/startup/Idling histories were also compared. The repair builds on the
current main checkout rather than reverting to the early all-extension updater.
