# e-transmit H Saved Copy Repath (test)

## Scope

One additional button: **EasyBIM > Test panel > Test > e-transmit H Saved Copy
Repath (test)**. Production e-transmit remains 2.1.28. Typeless, Auto Update,
startup hooks and the A-G probe implementations are unchanged. Only test-lab
routing, its ribbon layout and its button-count regression are extended.

H addresses the reproduced A/E/F opening problem: a newly opened working
copy can report `input_detached.rvt`, rather than a fully qualified PathName.
That API display name is never used as a filesystem target. H verifies the
absolute input/output paths and identifies the newly returned document against
the application's pre-open Documents collection. Existing or linked documents
are never adopted or closed. New task documents are registered for cleanup
before any further validation; rejected copies are closed without saving.
This correction is H-only; the older experiments remain unchanged controls.

## Run this button, not all the old tests again

Prefer **Use existing package (faster)** and select the **per-model manifest.json**
in an existing Collected/<model> folder alongside its RVT and actual Links
files. The same manifest from D's Collected folder is suitable if all listed
input files still match their checksums. A report-only OneDrive upload is not
a complete package and cannot be used as the model input.

Alternatively use **Collect a fresh package**. This still uses the private
collection dialog. Only saved input bytes are collected; no source Save,
SaveAs, Sync, reload, cleanup or upgrade is requested. Select one available,
loaded DWG type and choose an output location for the new experiment.

Each click creates **ET_TEST_H_<timestamp>**. In its one trial H:

1. Makes independent copies of the saved host and selected collected DWG.
2. Opens the working copy and checks the selected CAD's original reference.
3. Saves a separate package central with worksets retained (or a normal project
   for a non-workshared input). ClearTransmitted is used only when appropriate
   for an already-transmitted input. It does not rewrite TransmissionData.
4. Closes without saving pending post-save changes, then reopens normally.
5. Checks the same CAD UniqueId and type; calls LoadFrom with the absolute
   packaged DWG filename. Saves the requested CAD repair once, then closes.
6. Reopens normally and reads back the actual CAD path and loaded state. Checks
   that the saved host is not transmitted and that worksharing, when present,
   points to this candidate central rather than the original source central.

Unlike A-F, H opens all user worksets so the selected CAD and its instances are
available. Other installed link providers and add-ins may run during open.
H does not disable them, certify a clean environment, or intentionally repair
other references. It is NOT an offline/complete transmittal test. Existing
pending metadata in a transmitted input can be consumed by Revit on opening;
use an unchanged Collected input for the cleanest comparison.

## Read the outcome

- **CAD_REPATH_VERIFIED_TEST_ONLY**: selected path and loaded state matched after
  normal reopening, path is relative, and H did not observe a dirty state.
- **CAD_REPATH_VERIFIED_REVIEW_REQUIRED**: selected path and normal opening were
  verified, but an open/post-save dirty state or absolute path needs review.
  This is not the same as repathing failure. It is not production certification.
- **FAILED_SAVED_CAD_VERIFICATION**: reopened path/load did not match.
- **FAILED / FAILED_DOCUMENT_CLOSE**: see the precise stage and traceback;
  the intended test did not complete. It is not labeled EXPERIMENTS_COMPLETED.

`TEST_REPORT.txt` shows the candidate file and expected/actual CAD paths.
`TEST_REPORT.json`, `Trials/saved_copy_cad/RESULT.json`, and
`Diagnostics/events.jsonl` retain details. Worker handoff files remain under
Diagnostics/Worker_* even on failure. Source package checksum checks remain.

No test candidate is promoted to a real transmittal. Do not synchronize test
copies to an original central. A failure during normalization may leave an
unfinished candidate; only the recorded normal-reopen checks establish its
opening state. Do not infer that every file in the test folder is normal.

## Validation boundary

Tests include workshared simulated documents with empty, full, filename-only,
numbered and invalid PathName values, existing/linked/read-only object refusal,
cleanup on rejection, source checksums, post-save dirty flags, a rebased old
reference, duplicate target refusal, a saved path that reverts, unloaded final
CAD, and the actual runner/report dispatch. They serialize fixtures to disk;
they do NOT execute Autodesk Revit. Native SaveAs, ribbon display and real DWG
persistence remain workstation acceptance checks.

## Primary API references

- PathName: https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/8a92a6fd-ce25-cd86-2068-f9dcb24d72d6.htm
- CAD LoadFrom(String): https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/41db0b8b-4bd4-02b0-f06a-a7a169802e1b.htm
