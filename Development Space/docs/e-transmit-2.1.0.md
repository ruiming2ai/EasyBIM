# e-transmit 2.1.0 — current-document discovery and version-bound ACC downloads

## Source modes

**LIVE_DOCUMENT** is the default for open project rows. EasyBIM keeps the actual
Document, reads current references, and traverses already-loaded linked documents
once per command. It does not reopen models just to discover these references.
Choose an output location and transmit; a detached model does not require a saved
path to collect its live dependencies.

Host serialization is separate. An unmodified local host may use its saved RVT
only when the saved DocumentVersion matches the open document. Otherwise a per-host
confirmation offers SaveAs to the persistent `<run>_WorkingSnapshots` recovery
folder outside the transmittal. **SaveAs may change the working document's file
location.** EasyBIM never automatically restores, closes, synchronizes or publishes
that document. Review the active file after the run. The recovery file is retained
even if a post-save event from another add-in fails. Refusing SaveAs, or a Revit
restriction preventing it, still permits discovered material collection, but the
host is explicitly incomplete. No older saved/published host is silently substituted.
Cloud SaveAs remains subject to the installed Revit API's restrictions.

A document returned by GetLinkDocument is read-only for this workflow: EasyBIM
never calls SaveAs or Close on it. Linked RVT bytes must come from an exact source
and match both parts of the loaded DocumentVersion (GUID and save count).

**SAVED_FILE** remains available through Browse Models/Folder. It transmits that
saved file, including the existing composite-archive extraction route. It does
not represent unsaved state in an open document.

**PUBLISHED_VERSION** is added through **Add ACC published model**. Browse the
account, project, folder and RVT, then explicitly select a version. The read-only
RCM linked-files endpoint returns the host and downloadable linked RVTs for that
published host version. Returned graph membership is used as provenance, not a
recursive filename search across the project. Expected Revit links missing from
the returned inventory remain errors; Autodesk can omit links without download
permission. This mode deliberately exports a published snapshot, not live state.

**Associate ACC download for open row** binds a graph to an open cloud host only
when the host model GUID matches. Downloaded linked RVTs must additionally match
the editions actually loaded in the open model. A different loaded/published
revision is an error, not an automatic update to latest.

## Optional Autodesk setup (one-time administrator setup)

This is a native PKCE client, not an embedded shared Autodesk account. A company
administrator/developer must register an APS Desktop/Mobile/Native application
with the Data Management and Forma/ACC APIs, register exactly
`http://127.0.0.1:8767/callback` (or the selected loopback callback), and provision
the integration in the relevant Autodesk account. The user must have host and
linked-file download permission.

Select **APS setup / Sign in** and enter the public client ID and registered
callback. **Never enter a client secret.** The browser uses OAuth v2 PKCE/S256 and
only `data:read`. Tokens/refresh tokens stay in memory for this command, never in
settings, reports, ZIPs or GitHub. Public client ID and callback preferences may
be remembered. Data API calls are GET-only. Signed S3 downloads never receive the
bearer token. Expiring download URLs are refreshed against the same pinned host
version; changed membership/identity is rejected.

The sign-in is optional for saved/local/composite sources. Desktop Connector
installation alone is not APS application registration. Cloud-only exact download
support is not usable until the app has been provisioned and the user signs in.
An unavailable/unsupported published version is reported; it is not replaced by
the newest version. The RCM API's documented support window applies.

## Batch and archives

Select multiple open, saved or published sources. Revit API operations execute
sequentially on the command thread. This is a batch queue, not concurrent Revit
threads or separate Revit processes. Each job has an independent numbered output
folder and report. A failed job does not prevent the next selected job.

**Create a separate ZIP for each model** forces separate packages and produces
`01.zip`, `02.zip`, etc. Each contains that job's Sources, _Refs and reports.
Uncompressed folders remain. ZIP creation is atomic and CRC-checked before it is
marked VERIFIED. This archive status is not a Revit-opening certification.
The optional whole-batch ZIP remains available separately. `batch.json` records
job status, archive results and models not started after cancellation.

## Spreadsheet sources from plugins

The spreadsheet category is enabled by default. The reader inspects named source
fields in readable external resources and vendor-associated/public Extensible
Storage, including bounded nested string, array, map, JSON and XML containers.
It records provider/schema/element/field provenance and copies original workbook
bytes. No Excel process, macros or private storage edits are executed.

This is a conservative generic discovery adapter, **not certified universal
RFtools/DraftXL/Ideate support**. Restricted schemas, encoded vendor data and
unsupported layouts have explicit coverage statuses. A zero-result scan does not
prove that no plugin spreadsheet exists. Relative associations use the host's
configured central/local base when it is exposed. A workbook copied into the
package is not automatically reconnected inside the third-party plugin; the
report states PLUGIN_RECONNECT_REQUIRED. Spreadsheet data imported once without
an exposed persistent association may not be discoverable.

## Safety and verification repairs

- Short _Refs aliases include source/document identity, preventing collisions
  between models sharing element IDs and file basenames. Original filenames and
  complete available filesystem mirrors remain unchanged.
- An image identified as embedded/imported stays excluded across all inventory
  passes; the tool does not convert it to an external link.
- Verification DEFERRED is distinct from a model that was opened and FAILED.
- SaveAs recovery paths are reported even after a post-save failure.
- Cloud names are resolved only inside a selected authoritative download graph.
  The old same-folder-existence fallback is removed.
- RCP external scan completeness, CAD-internal Xrefs, NWF dependencies, inaccessible
  network/user paths and vendor reconnection remain explicit review boundaries.
- Cloud snapshot folders use graph/item identity when Autodesk supplies no original
  filesystem hierarchy. They are not presented as fabricated Shared/Consumed paths.

## Validation and installation

Update the entire EasyBIM extension from main and restart Revit. Confirm 2.1.0.
No additional Python packages or Excel installation are required. Keep the existing
native file-I/O helper with the extension.

The portable suite and Windows IronPython runtime suite test file IO, hashing,
archive verification, source-state separation, mocked Revit boundaries, official
APS response contracts, PKCE security and failure cases. They do not run licensed
Autodesk Revit, authenticate to a company ACC workspace or certify private plugin
storage formats. A desktop acceptance run is still required before production use.

First test the previously failing model in explicit PUBLISHED_VERSION mode after
Autodesk app setup, then separately test LIVE_DOCUMENT state and a two-model batch
with individual ZIPs. Check expected RVT inventory, loaded paths, tag references,
source mode/state basis, working recovery path and every review warning.

## Primary contracts

- https://aps.autodesk.com/en/docs/acc/v1/tutorials/files/rcm-linked-files
- https://aps.autodesk.com/blog/new-api-retrieve-metadata-and-signed-download-urls-hostlinked-files-revit-cloud-model
- https://aps.autodesk.com/blog/changes-are-coming-revit-cloud-model-downloads-autodeskbim-360-docs-starting-february-15-2026
- https://aps.autodesk.com/blog/getting-token-pkce-desktop-app-0
- https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/df74c7e1-a98a-7751-676a-e9b074566f62.htm
- https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/3574fa56-016e-b146-1499-b3b1c9129705.htm
- https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/9817e7db-8367-ea4e-1769-0488f3faa37f.htm
- https://support.ideatesoftware.com/support/help/ideate-sticky/using-ideate-sticky/sticky-file-properties
