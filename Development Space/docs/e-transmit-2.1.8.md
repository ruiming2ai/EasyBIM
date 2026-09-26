# e-transmit 2.1.8 — measured, host-only processing

## What changed

- Directly collected linked RVTs are preserved byte-for-byte. Repath, cleanup and
  upgrade processing apply to the selected host, not those linked dependencies.
- No preparatory Revit verification. With Repath on, the final delivered host is
  verified once. Its immediate reference paths and requested load states are
  checked; linked RVTs are not opened separately for verification.
- Native local/network RVT acquisition uses one verified copy instead of two
  when direct source access is supported. Very long Windows source paths retain
  the short-path acquisition fallback for IronPython compatibility.
- Dependency destinations are allocated before deferred copies, retaining the
  category/original/flat layouts, repeated-reference deduplication, same-name
  separation and RCP Support folder grouping.
- For metadata-only host workflows, unchanged local dependencies are copied
  directly to their final destination through the existing verified temporary
  file / publish operation. Host metadata uses the final host path as its
  reference base, not the temporary working path.
- Image/cloud-resource conversion, cleanup and upgrade hosts retain isolated
  short-path preparation. Moves of verified task-owned scratch files replace
  unnecessary copies on the same volume. Across volumes, a verified copy is
  used instead. Original user files and Revit caches are never moved.
- Checksums are reused only for unchanged verified outputs; changed output
  signatures trigger an integrity read. Mutated host bytes receive a new hash.
- Initial saved-state link registration records identity without recursively
  scanning linked documents or their private plugin resources.

## Performance reports

Each package has `timings.csv` and a `performance` object in `manifest.json`.
`REPORT.txt` / `START_HERE.txt` include a PERFORMANCE SUMMARY.

The timing clock is Python perf_counter or .NET Stopwatch where available.
Any wall-clock fallback is explicitly identified, not described as monotonic.
Initial reference discovery is reported separately and included in the measured
sum. User dialogs and the final telemetry serialization are excluded.

The CSV identifies scope, phase, operation, file, target, start offset, elapsed
seconds, exclusive seconds, completion status, bytes, and MiB/s. Start offsets
are relative to the named scope. Parent durations include their children;
exclusive phase totals do not double-count nested operations. Copy throughput
includes its checksum verification, not just disk transfer. Logical bytes are
not physical disk traffic. Failed operations can have unknown partial byte
counts. Operation rows are bounded; any omitted count is reported.

Recorded operations include saved/live reference discovery, source resolution,
cache acquisition, native validation, file copying, checksums, layout planning,
relocation, host processing, Revit open/save/close, verification, report writing,
and scratch cleanup. No extra payload reads are added solely for telemetry.

At batch level, `batch.json` and `batch_timings.csv` record ZIP and orchestration.
Package manifests are not rewritten after ZIP creation. Therefore the final
ZIP duration is in the outer batch report, not retrospectively inserted into
the ZIP's own embedded report. Batch and package timers are separate scopes and
must not be added together.

## Copy verification is not full BIM portability verification

`UNCHANGED_DEPENDENCY` means linked RVT content was not processed.
`FILE_INTEGRITY_ONLY` means the copied file's bytes were verified; it does not
claim that file was independently reopened or that its nested references work.
`OPENED_AND_REFERENCES_CHECKED` is reserved for the final host verification.
Repath off performs collection without an automatic final Revit opening check.
Missing files and failed host verification remain failures/review items, not
successful portability checks. Failed/cancelled output and recovery locations
remain explicitly reported.

ACC acquisition remains the existing identity-based saved-cache workflow. Real
ACC behavior still needs workstation acceptance; portable tests are not Revit.

## Verification

Local CPython: 400 tests, 4 platform skips, no failures. All four new test files
also passed as individual scripts. `git diff --check` passed.

A simulated host-plus-five-links fixture executed against both source versions:

| Boundary operation | 2.1.7 | 2.1.8 |
|---|---:|---:|
| Verified file-copy calls | 37 | 9 |
| Model-processing calls | 6 | 1 |
| Standalone verification calls | 12 | 1 |
| Direct links copied | 5 | 5 |

This counts real filesystem operations and substituted Revit-boundary calls.
It is not a measurement of Revit export speed or an assertion of a speed factor.
Actual workstation timing is the purpose of the new reports.

New tests cover direct/fallback layout, reference bases, unchanged links,
copy/hash reuse, cross-volume fallback, source changes, overwrite protection,
failures/cancellation, initial discovery, nested timing accounting, ZIP scope,
long-source fallback and safe publication of timing CSVs at long destinations.
Existing cancellation/failure tests now intercept relocation instead of a
redundant copy that no longer happens. Historical duplicate-verification and
linked-model-rewriting expectations were updated to the approved scope.

## Workstation acceptance

Update the complete EasyBIM extension and restart Revit. Confirm version 2.1.8.
Repeat the Snowdon Towers Electrical run with the same settings and destination.
Upload the package reports plus outer batch.json / batch_timings.csv. Compare
actual phase durations, not upload timestamps or example timing numbers.

Check immediate links, placement, annotations and model reopening. Repeat an
image/PDF host, ACC host, long destination, cancellation and a different-volume
output. Nested dependency collection remains deliberately out of scope.
