# Flip Multiple Geometric Modes Design

Date: 2026-05-17
Tool: `RL_Tools.tab/Misc Tools.panel/Flip Multiple.pushbutton`

## Summary

Extend `Flip Multiple` from a strict native-flip tool into a mixed native + geometric flip tool.

- Keep `Flip Work-plane` as a separate native Revit family flip mode.
- Change `Flip Front/Back` and `Flip Left/Right` to origin-centered geometric flips.
- Add `Flip Up/Down` as a new origin-centered geometric flip mode.

The geometric modes must preserve the element instance origin while reflecting the instance across a plane derived from the instance's local orientation. The command remains all-or-nothing.

## User-Facing Behavior

The mode list becomes:

- `Flip Work-plane`
- `Flip Front/Back`
- `Flip Left/Right`
- `Flip Up/Down`

Mode meanings:

- `Flip Work-plane`
  Uses the family instance's native Revit work-plane flip behavior only.
- `Flip Front/Back`
  Reflects the instance across a vertical plane through the instance origin, derived from the instance facing direction.
- `Flip Left/Right`
  Reflects the instance across the orthogonal vertical plane through the instance origin, derived from the instance hand direction.
- `Flip Up/Down`
  Reflects the instance across a horizontal plane through the instance origin, derived from the instance local vertical direction.

The origin-centered behavior is implementation detail and must not be included in the mode label text.

On success, the command should report success only. No extra summary details are needed in the success dialog.

## Execution Model

The tool keeps the current `Slope`-style WPF dialog and selection flow.

Execution splits into two paths:

1. Native path
   - Used only by `Flip Work-plane`
   - Validates native work-plane flip support
   - Runs the native work-plane flip operation

2. Geometric path
   - Used by `Flip Front/Back`, `Flip Left/Right`, and `Flip Up/Down`
   - Derives a mirror plane through the element origin from the instance's local transform/orientation
   - Applies an in-place Revit mirror/transform across that plane

Local plane derivation rules:

- `Flip Front/Back`
  Plane passes through instance origin and is derived from the instance facing direction.
- `Flip Left/Right`
  Plane passes through instance origin and is derived from the instance hand direction.
- `Flip Up/Down`
  Plane passes through instance origin and is derived from the instance local vertical direction.

The implementation should use the instance's local transform/orientation where available. If the required local plane for the selected geometric mode cannot be derived for any selected element, the command must not run.

## Validation and Failure Handling

Validation remains all-or-nothing.

- `Flip Work-plane`
  Reject only if native work-plane flip is unsupported.
- Geometric modes
  Reject only if the required local plane cannot be derived.

For geometric modes, do not proactively reject categories or families just because they may be risky to mirror. After plane derivation succeeds, the transaction is the proof point.

Runtime rules:

- Run the selected flip mode on the full selection in one transaction.
- If any runtime geometric flip fails, roll back the entire transaction.
- Runtime rollback errors should state that nothing was kept and should include short troubleshooting detail, such as sample failing family types or element ids.

Pre-validation rejection should continue to report incompatible family types.

## UI and Messaging

Keep the current `Flip Multiple` window layout and icon.

Update helper text so it clearly distinguishes:

- native `Flip Work-plane`
- geometric `Flip Front/Back`
- geometric `Flip Left/Right`
- geometric `Flip Up/Down`

Result messaging:

- Success
  Report success only.
- Pre-validation rejection
  Report the mode and incompatible family types.
- Runtime rollback
  Report that the transaction was rolled back and include short troubleshooting detail.

## Testing Notes

Verify:

- `Flip Work-plane` still behaves as a native mode.
- `Flip Front/Back` preserves instance origin and flips around the derived front/back plane.
- `Flip Left/Right` preserves instance origin and flips around the derived left/right plane.
- `Flip Up/Down` preserves instance origin and flips around the derived horizontal plane.
- Pre-validation rejects only when native work-plane support is missing or required geometric plane derivation fails.
- Runtime transform failures roll back the whole transaction.
- Success messaging is reduced to success only.
