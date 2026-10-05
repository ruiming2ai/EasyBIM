# Auto Update cross-session repair

## Scope
Restore unattended refresh for open Revit processes sharing the EasyBIM checkout,
without reverting the IronPython 2024 status fix or updating unrelated extensions.
A process busy in a Revit command/modal must defer to Revit's next safe API cycle.
Separate checkouts are not shared storage; do not silently edit those repositories.

## Evidence
Full `git log --follow -p` inspection of the updater (May-September) and the
button/startup/Idling history. August 25 restricted the update to EasyBIM and
stopped unconditional reloads. September 25 added verified per-process markers
and explicit StatusOptions for IronPython. September 30 restored a local observer,
but it depends on natural Idling, refuses an unknown baseline, and suppresses
all future retries after one failed reload for a revision.

## Implementation and verification
1. Add regressions for missing natural Idling, worker-thread/API separation,
   event coalescing, disposal/reload lifecycle, unknown baseline recovery and
   transient failures. Observe failures before implementation.
2. Add one process-scoped, low-frequency Timer that only raises ExternalEvent.
   Run the existing update consumer on Revit's API thread. Stop/replace old
   timers on reload and stop on ApplicationClosing. Do not call model APIs,
   Git, reload or forms from the timer thread.
3. Recover unknown markers only by verifying this clean tracked checkout and
   successfully reloading it; retain the previous marker on failure. Retry
   failed receiving-session reloads after 60/120 seconds, maximum three attempts
   per revision. A new revision or explicit manual retry gets a new budget.
4. Keep native StatusOptions, own-repository discovery, mutex, local edits,
   ancestry/tree verification and no-network receiving-session behavior.
5. Correct existing test fixtures that still assume Coordination Review defaults
   on; do not change the requested off-by-default production behavior.
6. Run focused tests, full regression suite, real Windows IronPython/LibGit2Sharp
   tests, inspect full diff and publish the tested allowlisted files to main.

Live Revit UI execution cannot be proved by off-host tests. Document this limit.

## Review rulings
- Close every native Git handle after use, including rejected discoveries and
  failed fetches; preserve all verification checks.
- Reacquire and revalidate after manual confirmation instead of retaining a
  mutex across user dialogs; a changed checkout defers loading to the observer.
- Run the existing full suite, not only updater tests. Existing Coordination
  Review fixtures must opt in explicitly now that production defaults off.
