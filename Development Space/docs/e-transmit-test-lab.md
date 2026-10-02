# e-transmit Test lab 1.0

## Scope and frozen production baseline

Seven scenario groups A-G, with eleven experiment arms in total. These are
hypotheses/diagnostic questions, NOT seven confirmed defects or seven fixes.
Only new files are added. The production e-transmit 2.1.28, its library,
Typeless/other tools, startup hooks, and Auto Update are not edited.
Baseline: `675b8067b51e29b84f150a99b4105592ed33daa9`.
The private `easybim_etransmit_tests.base` tree is copied from that baseline;
BASELINE.json records the source hashes and the two private adaptations.
Production does not import the test code. No runtime monkeypatch of production
classes, module globals, settings, or event handlers is performed.

## Ribbon

**EasyBIM > Test panel > Test drop-down**

| Button | Experiment and expected evidence |
|---|---|
| e-transmit A Save Guard (test) | Two matched saved copies: immediate dirty-state rejection versus close without saving pending edits and normal reopening of the on-disk file. Does not blindly resave unknown callback edits. |
| e-transmit B Save Events (test) | Save event/DocumentChanged tracing plus loaded application/assembly census. Environment label allows repeated runs to be compared. It does NOT disable add-ins or claim a clean profile. |
| e-transmit C Save Sequence (test) | Two matched copies: first save without versus with native CAD TransmissionData preparation. Activated reference IDs are recorded; zero IDs means no CAD metadata intervention was possible. |
| e-transmit D CAD Routing (test) | Select one collected DWG type. Compare LoadFrom(string) and LoadFrom(local ExternalResourceReference, Relative). Records result code, returned type ID, actual saved path, and normal-reopen path. |
| e-transmit E CAD Duplicates (test) | Select a duplicate exact-source CAD group. Compare shared target with identical-byte, per-type subfolder targets. No type merge, deletion or instance recreation. |
| e-transmit F Saved Inventory (test) | Read the saved copy BEFORE any SaveAs. Compare report/live IDs and paths, enumerate saved-only CAD type IDs, and report absent/mismatched/unverified references. Observation only; no new saved output host. |
| e-transmit G Recovery Evidence (test) | Run the frozen full baseline repair on a separate candidate. Preserve failed stage, candidate, original input, per-arm results and event history rather than hiding everything behind host restoration. |

A/B isolate first-save state with no repathing. A-F use closed user worksets
for initial save/read probes; D/E reopen with the selected CAD worksets.
G alone runs the full baseline with its original workset/link-loading lifecycle.
Therefore compare the matched arms within a button first; do not treat a
minimal probe as identical to the full G lifecycle. Opening a workset containing
other links can still invoke their installed resource providers.

## Running a test

Reload/update EasyBIM and reload pyRevit to create the new ribbon items.
Start with A, then B and C; use G when exact full-pipeline evidence is needed.
D and E are targeted CAD tests, not complete-package repathing.

Every button offers:

1. **Use existing package (faster):** choose its per-model `manifest.json`
   beside the actual RVT and `Links` files. A report-only folder is not enough.
   Manifest-relative paths support a relocated package; original absolute
   locations are never guessed from basenames. Input hashes must match.
2. **Collect a fresh package:** a private copy of the e-transmit dialog collects
   one saved host and dependencies. Collection itself has Repath, Cleanup,
   Upgrade and ZIP disabled, then the selected experiment operates separately.
   The original live document is not saved, synchronized, reloaded or relocated
   by this collection path. Unsaved edits are excluded. For a detached source
   with no provable saved location, add its known saved RVT instead of SaveAs.

Each click creates `ET_TEST_<A-G>_<timestamp>` under the chosen output folder.
Matched arms start from the SAME saved-host hash with independently copied
files (no hard links). Only dependencies relevant to A-F are duplicated; G
copies the complete collected set. Revit version must match the saved input;
there is no automatic upgrade.

Each experiment opens a separate installed Revit process. Its smart-button
initializer installs a lab-only Idling handler, deduplicated across engines.
The child uses its own job environment variables. An existing child-only
production worker-mode opt-out suppresses ordinary EasyBIM startup consumers;
the production worker receives no job. Existing startup code is not changed.
If startup is not acknowledged within 180 seconds, the parent stops that worker
and reports a startup problem rather than waiting for the two-hour job limit.
Other installed add-ins still load. B is observational, not a clean-room test.

## Reading results

- `EXPERIMENT_PLAN.json`: question, code baseline, input, selected IDs, arms.
- `TEST_REPORT.txt` / `TEST_REPORT.json`: overall outcomes and original-input hash.
- `Trials/<arm>/RESULT.json`: results, precise failure stage and traceback.
- `Trials/<arm>/INPUT_REFERENCES.json`: arm-specific collected target mapping.
- `Diagnostics/events.jsonl`: incremental phase, state and save/change events.
- `Diagnostics/Worker_*/job.json`, `result.json`, `status.json`: retained worker
  handoff and suppressed dialogs/failures; these are retained even on failure.
- `LAUNCH_ERROR.json`: launcher/bootstrap/timeout/cancellation error when available.

`VERIFIED_TEST_ONLY` means only the selected test paths and normal opening
matched in that arm. It is NOT complete building/model/geometry certification.
`PATHS_MATCH_REVIEW_DIRTY_STATE` explicitly retains post-save modification
uncertainty; it must not be interpreted as a production-safe fix.
`PATHS_MATCH_ABSOLUTE_REVIEW` means references match but portability is unproven.
A baseline failure or target collision can be a useful reproduced condition.
No candidate is automatically promoted to the user's production transmittal.
Failed/transmitted/non-normal candidates stay in the marked experiment tree;
do not synchronize any test copy to a source central.

F compares cloud project/model GUIDs from the saved external-resource data,
not opaque session URLs. `UNVERIFIED_RESOURCE_IDENTITY` means that identity
could not be read or matched. It does not infer same identity from filenames.
Model schema names and loaded assemblies alone do not identify a guilty add-in.
Event subscriptions observe only task-owned documents and are removed afterward.
The summary records subscriptions that actually succeeded. An unavailable event
subscription is not evidence that no event occurred. E verifies identical payload
bytes before a shared-target/per-type comparison and rejects incomparable inputs.

## Validation boundary

Portable/file-backed fake-Revit tests cover all scenario dispatch, source/file
isolation, real close/reopen of serialized fixtures, dirty-save behavior,
CAD API overloads, duplicate target detection, mismatched original path rejection,
read-only inventory, and outside-root refusal. These are NOT licensed native
Revit tests. Ribbon appearance, worker startup under the installed pyRevit,
real SaveAs behavior and actual CAD persistence require desktop execution.

No user reports, model bytes or project paths are committed as test fixtures.

## Primary API references

- CAD LoadFrom(string): https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/41db0b8b-4bd4-02b0-f06a-a7a169802e1b.htm
- CAD LoadFrom(resource): https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/de80a921-92a2-ad7e-5aa5-355eb850a992.htm
- Local resource: https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/457745f0-5346-77ed-444b-554295ebb14b.htm
- WorksetConfiguration.Open: https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/6fd8d399-0b42-784d-5863-cc6618499ad8.htm
- DocumentSavedAs: https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/7ace570d-870f-be20-e493-e80ffa27f454.htm
- pyRevit smart-button initialization: https://raw.githubusercontent.com/pyrevitlabs/pyRevit/master/pyrevitlib/pyrevit/loader/uimaker.py
