# Independent placement compatibility and performance

## Release gate

The runtime adapter implements native OneLevelBased point-family placement for
MEP/device categories and Generic Models. It checks physical hosting, the actual
frame within **1 mm / 0.1 degrees**, numeric parameter read-back and transaction
commit. Reference levels may remain; intended elevation comes from coordinates.

There are **no certified hosted-family conversion paths**. Face/work-plane,
wall/ceiling/floor/roof, adaptive, in-place, curve and nested component/shared
family conversion is refused. No hosted substitute, flattened geometry or dummy
building host is created. Native Copy/Monitor is not invoked.

Native family loading is used for accepted originals, rather than geometry
reconstruction. This preserves the definition, but live parametric geometry,
connector, tag and reference fidelity still require acceptance. Do not label
them verified merely because the desktop tests passed.

## Evidence as of 2026-09-10 UTC

No Revit installation is available on the implementation host. No placement,
conversion, WPF, Extensible Storage round-trip or Revit performance test was run.

| Revit release | Placement/UI | Hosted conversion | Save/reopen, Undo/Redo, worksharing | Revit benchmark |
| --- | --- | --- | --- | --- |
| 2024 | Not run | Disabled; not certified | Not run | Not run |
| 2025 | Not run | Disabled; not certified | Not run | Not run |
| 2026 | Not run | Disabled; not certified | Not run | Not run |
| 2027 | Not run | Disabled; not certified | Not run | Not run |

Desktop verification exercises transformed/mirrored frames, local offsets,
relative rotation, identity mapping, missing states, source/local conflicts,
review actions, parameter decisions, stale-family rejection and simulated
transaction rollback. Adapter tests use substitutes for the Revit API. They do
not establish Revit compatibility.

The read-only `Development Space/revit/copy_monitor_diagnostics.py` script prints
selected native family hosting/placement diagnostics when run in pyRevit.
It is not an acceptance result.

## Final desktop verification

At runtime commit `1b26b5c`:

- Full repository unittest discovery: **2,232 tests passed** in 5.916 seconds.
- All 12 touched/new runtime Python files parsed successfully.
- All five XAML files in the two command bundles parsed successfully.
- `git diff --check` passed.
- Scoped independent review of button/review/storage integration found no
  concrete additional defects; it did not perform live Revit verification.

Command: `python3 -m unittest discover -s 'Development Space/tests'`.
Existing test-suite warnings concern an invalid escape sequence and an unclosed
Families Downgrade test log; neither produced a test failure.

## Measured desktop benchmark

Run:

```sh
python3 'Development Space/benchmarks/copy_monitor_decisions.py'
```

[Raw results and environment](copy-monitor-desktop-benchmark.json) record Python
3.10.6 on WSL2, 40 parameters per source/destination snapshot, and the median of
three runs. All figures below are seconds.

| Pairs | Construct records | Compare unchanged | Compare set with 50 changed sources |
| ---: | ---: | ---: | ---: |
| 100 | 0.012090 | 0.018504 | 0.015404 |
| 1,000 | 0.101175 | 0.169165 | 0.168474 |
| 10,000 | 1.199161 | 1.890333 | 1.958980 |

These are pure Python construction/comparison timings. They exclude Revit API
reads, registry serialization, family loading, placement, geometry, connectors,
regeneration and UI. They are not initial-placement or update benchmarks, and
do not establish a speed advantage over legacy Batch Duplicate Host or native
Copy/Monitor.

## Required live acceptance matrix

Run in disposable metric and imperial projects on each target release. Record
Revit build, family/source/destination identities, link transform, operation,
expected/actual frame, physical host, parameter exceptions, connector results,
elapsed time and outcome for each run.

| Scenario | Required result |
| --- | --- |
| Standalone lighting fixture, equipment, electrical device, Generic Model | Native definition, expected coordinates, no physical host |
| Face/ceiling/wall/work-plane family | Explicit unsupported outcome; no copy or partial loaded family |
| Translated, rotated, mirrored and repeated links | Correct absolute and local-offset placement; distinct relationships |
| Mismatched levels | Intended world elevation within tolerance |
| Align off; rotated relative offset | Fixed prototype orientation for original recipe; relative pose rotates with source |
| Surviving source moved then orphaned | Independent destination follows current pose without former host |
| Source/destination deletion; unloaded/replaced link | Distinct unavailable state; surviving destination preserved |
| Variable instance length, flips, nested geometry | Parameter-driven native geometry retains intended dimensions and orientation |
| Materials, shared/built-in parameters, same-name conflicts | Typed/GUID matches; explicit exceptions; unrelated content unchanged |
| Local/source/both edits; Accept/Relative/Postpone/Stop | Correct repeat-check behavior; unresolved data edits remain pending |
| Existing hosted destination | Relationship recorded; conversion required; original instance preserved |
| Pinned, groups, design options, ownership | Atomic refusal without state changes |
| Connected MEP/circuits, tags and references | Existing associations preserved; unsupported mutation refused |
| Save/reopen, Undo/Redo, worksharing synchronization | Model and registry remain consistent; no accidental pair duplication |
| 100/1,000/10,000 monitored pairs; 50 changed | Measure initial copy, unchanged check and update versus legacy BDH |

A future conversion gate must additionally prove retained formulas,
geometry-driving parameters, plan symbols, nested components, photometrics,
connector layout and orientation, circuit/tag/reference behavior, original
family preservation and subsequent independent movement. A hosted family is
not certified merely because Revit accepted a placement API call.

## API review references

- [Autodesk MirrorElements](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/bb533c52-171a-85f9-8896-c7bb661e129f.htm): use `mirrorCopies=False` to mirror the existing destination.
- [Autodesk DataStorage](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/015081b6-3a45-1b4c-991a-93419e9acd51.htm): separate elements store coordination records.
- [Autodesk GeneralFailures](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/d4679f32-45d9-bec2-e0ff-168c3aee6de9.htm): DuplicateValue identifies duplicate parameter labels; other failures are not suppressed.

API documentation review is not a substitute for the live acceptance matrix.
