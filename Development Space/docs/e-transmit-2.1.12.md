# EasyBIM e-transmit 2.1.12 — transmitted primary host

## Purpose

Keep 2.1.11's fast collect-only path while making copied workshared/ACC-cache primary
hosts open as **transmitted** Revit models rather than ordinary copies that still behave
like the original central.

## Behavior

- After the final primary host reaches its package location, EasyBIM uses closed-file
  `TransmissionData`; it does not open/save the host for this operation.
- For a workshared primary host, every TransmissionData-backed file reference is copied
  into desired reference data with the same path, path type and saved load intent, then
  `IsTransmitted` is set and written.
- The result is verified using `TransmissionData.IsDocumentTransmitted`, with a read-back
  fallback for compatible API shapes.
- ACC/external-server references are outside TransmissionData and remain untouched by
  transmission marking.
- Directly collected linked RVTs are not marked or rewritten by this feature.
- Repath remains independent: OFF preserves reference locations; ON continues the
  existing supported repath workflow.
- If no usable TransmissionData exists or verification fails, the copy is retained and
  reports `TRANSMIT_UNAVAILABLE` with manual **Detach from Central → Preserve Worksets**
  guidance.
- Because transmission marking changes packaged RVT metadata, the final checksum records
  the pre-transmission hash and then verifies the new final signature/hash rather than
  misclassifying the expected change as corruption.

## Recipient opening

Autodesk documents that transmitted workshared files open detached from their central
model until finalized. `TRANSMITTED` therefore allows a recipient to open the packaged
host normally while Revit handles the transmitted state. Do not synchronize the package
back to the original central. Marking transmitted does not itself repath external
references.

## Verification

Portable/API-shaped suite: **446 tests passed with 4 platform skips** before release
packaging. Linux/Windows/IronPython GitHub CI is required before merge. Actual Revit 2024
opening behavior remains a workstation acceptance check.
