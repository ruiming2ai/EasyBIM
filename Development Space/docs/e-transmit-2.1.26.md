# e-transmit 2.1.26 - restore host copy delivery

## Regression and correction

2.1.25 treated backend support for independent host finalization as a requirement
for every host. A worker error, unavailable linked RVT, or failed inventory could
therefore remove an already-acquired host from the delivery folder and retain it
only in an external recovery directory. Even turning Repath off did not avoid
independent finalization.

2.1.26 restores the requested copy-first workflow. Backend capability alone no
longer authorizes or requires independent Save As. Copy-first is enabled by
default and is visible in the dialog; the user's selection is persisted.

- Copy-first, Repath off: deliver saved local/cache bytes without opening or
  independently saving the host. Unsaved edits remain excluded.
- Copy-first, Repath on: deliver the host and use closed-file TransmissionData
  for supported native file references. An unavailable RVT does not prevent
  available CAD references from being repathed. Unsupported references remain
  explicit manual-repair items, not silent success.
- Metadata processing failure: restore the acquired host in the package and
  report the error instead of losing the deliverable.
- Full repair (Copy-first unchecked), Cleanup, or Upgrade: retain independent
  Revit processing and its existing strict verification/recovery behavior.

No source model saving, synchronization, publishing, cache identity guessing,
Auto Update changes, or new Revit API dependencies were introduced.

## Opening the result

A copied host is not an independently saved package central. For workshared
copies, use Detach from Central and never synchronize to the original central.
Native repathing keeps transmitted metadata active so Revit can apply desired
reference paths. Clearing that flag without materializing and saving the links
would discard the intended repath behavior. The report distinguishes metadata
readback from an actual Revit opening/load test.

Autodesk references:
- TransmissionData class: https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/d78d1e9c-1cee-1336-88d5-b605dacd077d.htm
- WriteTransmissionData: https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/96561c21-134c-9744-45de-8c3f772f0676.htm
- Open workshared model independently: https://help.autodesk.com/cloudhelp/2023/ENU/Revit-Collaborate/files/GUID-2B3E48F7-96A3-4379-BF6B-538D8C9ED5EF.htm

## Verification scope

The regression suite exercises real acquisition, filesystem delivery, rollback,
archives, report text, and routing. Autodesk serialization is substituted in the
portable tests; these tests are not a Revit integration certification. Existing
independent-finalization tests explicitly select full mode and retain their
strict worker, recovery, and source-protection assertions.

Desktop acceptance still required:
1. Confirm dialog version 2.1.26 and leave Copy-first checked, Cleanup/Upgrade off.
2. Transmit an open ACC host with Repath off; verify the model-named RVT exists.
3. Repeat with Repath on; verify linked CAD paths after opening the package copy.
4. Repeat with one unavailable linked RVT; the host and available CAD must remain,
   with the missing RVT prominently reported.
5. Relocate the whole package and verify supported native relative references.
6. Confirm original host/cache timestamps and contents are unchanged.
