# Independent placement compatibility
## Release gate
The 2024-2027 API adapter supports standalone OneLevelBased point families.
Support is conditional on actual placement, physical-host, orientation, geometry,
and parameter verification. A required reference level is allowed; a physical
host or geometry-backed work plane is not.

WorkPlaneBased / face-based, wall/ceiling/floor/roof-hosted, adaptive, in-place,
curve-based and nested shared families are not certified for automatic conversion.
They must be reported as unsupported, never silently placed as hosted substitutes.
The source family and existing destination remain untouched on refusal.
Do not use native Copy/Monitor, temporary building hosts, or flattened geometry.

## Evidence
No Revit installation is available on the implementation host. None of Revit
2024, 2025, 2026 or 2027 has been runtime-certified by this change.
Desktop tests cover mathematics, parameter mapping, record lifecycle and adapter
rollback. They do not establish Revit conversion or placement compatibility.

## Revit acceptance matrix
Run in disposable projects on each target release with metric and imperial units:
- Standalone lighting fixture, mechanical equipment, electrical device, Generic Model.
- Face-based and ceiling-based fixtures: explicit unsupported result, no created copy.
- Different level names and elevations, 3D rotations, mirrored and repeated links.
- Variable instance length, material, shared parameters and electrical connectors.
- Source moved then orphaned: independent destination follows new coordinates.
- Source deleted, destination deleted, link unloaded/replaced: distinct review state.
- Pinned/owned/grouped elements, connected systems: failure retains previous state.
- Undo/redo and save/reopen preserve model/relationship consistency.
- 100, 1,000 and 10,000 pairs: initial copy, no-change check, 50 changed sources.
Record Revit build, family identity, operation, expected/actual frame, host,
parameter exceptions, elapsed time and result. Never label a version tested
without recording its actual run.

A future conversion certification must additionally prove retained formulas,
parameter-driven geometry, nested family behavior, plan symbols, photometrics,
connectors, source preservation and subsequent independent movement.
